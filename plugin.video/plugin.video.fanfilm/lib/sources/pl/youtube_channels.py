# -*- coding: utf-8 -*-
"""
FanFilm - źródło: full movies from YouTube channels
Copyright (C) 2026 :)

Distributor channels that publish whole movies legally and for free:
  * KinoSwiatVOD  (https://www.youtube.com/@KinoSwiatVOD)
  * AleFilmy      (https://www.youtube.com/@alefilmy)

How it works:
  * the channel is searched for each known title variant of the wanted movie
    (one lightweight request per variant — no catalogue download, no cache),
  * titles on these channels are structured, so the movie title (and, for
    KinoSwiatVOD, also the year and the original title) can be extracted and
    matched against a TMDB movie,
  * playback goes through ``plugin.video.youtube``.

Shared, fragile YouTube scraping code lives in ``lib/ff/yt_utils.py``.

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from typing import ClassVar, Dict, List, Optional, Sequence, TYPE_CHECKING

from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.yt_utils import (gather_titles, make_session, norm_title,
                             search_channel, youtube_play_url, youtube_source)
from lib.sources import Provider

if TYPE_CHECKING:
    from lib.ff.sources import SourceItem
    from lib.sources import SourceModule, SourceTitleAlias


# ─── title-parsing constants & helpers ──────────────────────────────────────────────

_YEAR_RE = re.compile(r'^(?:19|20)\d{2}$')
_QUALITY_SEG_RE = re.compile(r'(?:ᴴᴰ|hd|fhd|uhd|4k|sd)', re.IGNORECASE)
#: KinoSwiatVOD: "Polish title (genre, cast, YEAR, Original title, ᴴᴰ) ..."
#: The parenthesis body is matched greedily up to the last ")", so titles
#: containing parentheses survive, e.g. "Tension(s)".
_KSV_RE = re.compile(r'^(.+?)\s*\((.+)\)', re.DOTALL)


def _audio_info(lower_title: str) -> str:
    """Detect the audio-track type from a video title."""
    return ', '.join(tag for tag in ('dubbing', 'lektor', 'napisy') if tag in lower_title)


# ─── _source ────────────────────────────────────────────────────────────────────────

class _source(Provider):
    """Base scraper for a YouTube channel that hosts full movies."""

    # --- scraper api ---
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ['pl']

    # --- subclass configuration ---
    PROVIDER: ClassVar[str]
    HANDLE: ClassVar[str]
    #: At most this many distinct title variants are searched on the channel.
    MAX_QUERIES: ClassVar[int] = 3
    #: Minimum duration accepted as a full movie (seconds).
    MIN_DURATION: ClassVar[int] = 40 * 60

    def __init__(self) -> None:
        self._found: List[Dict] = []

    # ── public api ──────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if self.ffitem.ref.type != 'movie':
            return None

        title_variants = gather_titles(self.ffitem, title, localtitle, aliases)
        # title variants → normalized set to match against
        wanted = {norm_title(t) for t in title_variants}
        wanted.discard('')
        if not wanted:
            return None

        try:
            want_year = int(year) if year else 0
        except (TypeError, ValueError):
            want_year = 0

        matched: List[Dict] = []
        for video in self._search_videos(title_variants):
            duration = video.get('duration', 0)
            if duration and duration < self.MIN_DURATION:
                continue  # trailer / clip, not a full movie
            parsed = self.parse_video(video['title'])
            if not parsed:
                continue
            candidates = {norm_title(t) for t in parsed['titles']}
            candidates.discard('')
            if not candidates & wanted:
                continue
            # the year is checked only when known on both sides
            vid_year = parsed.get('year')
            if want_year and vid_year and abs(vid_year - want_year) > 1:
                continue
            matched.append({
                'id': video['id'],
                'title': video['title'],
                'duration': duration,
                'info': parsed.get('info', ''),
                'quality': '1080p',  # YouTube serves the best stream itself
            })

        if not matched:
            fflog(f'[{self.PROVIDER.upper()}] {self.ffitem} not found')
            return None
        self._found = matched
        fflog(f'[{self.PROVIDER.upper()}] {self.ffitem}: {len(matched)} match(es)')
        return self._movie_id(imdb)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> None:
        return None  # these channels host movies only

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> None:
        return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str],
                from_cache: bool = False) -> 'List[SourceItem]':
        if not url:
            return []
        result: List[SourceItem] = [
            youtube_source(video, language='pl', info=video.get('info', ''))
            for video in self._found]
        fflog(f'[{self.PROVIDER.upper()}] sources: {len(result)}')
        return result

    def resolve(self, url: str, *, buy_anyway: bool = False) -> str:
        """Convert a YouTube URL into a plugin.video.youtube address."""
        return youtube_play_url(url)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _movie_id(self, imdb: str) -> str:
        """Non-empty id returned from movie() (passed through to sources())."""
        return str(self.ffitem.ids.get('tmdb') or imdb or self.PROVIDER)

    def _search_videos(self, title_variants: List[str]) -> List[Dict]:
        """Search the channel for each distinct title variant (one request each).

        Per-movie results are cached by FanFilm itself (``{source}_results``),
        so no catalogue download and no provider-side cache are needed.
        """
        unique: Dict[str, Dict] = {}
        queried: set = set()
        session = None
        for variant in title_variants:
            key = norm_title(variant)
            if not key or key in queried:
                continue
            queried.add(key)
            if session is None:
                session = make_session()
            for video in search_channel(self.HANDLE, variant, session=session):
                vid = video.get('id')
                if vid:
                    unique.setdefault(vid, video)
            if len(queried) >= self.MAX_QUERIES:
                break
        if session is not None:
            session.close()
        return list(unique.values())

    def parse_video(self, raw_title: str) -> Optional[Dict]:
        """From a video title return {'titles', 'year', 'info'} or None.

        Overridden in the subclass — each channel has a different title format.
        """
        raise NotImplementedError


# ─── KinoSwiatVOD ───────────────────────────────────────────────────────────────────

class KinoSwiatVOD(_source):
    """Scraper for the @KinoSwiatVOD channel.

    Some movies have a structured title:
        ``Polish title (genre, cast, YEAR, Original title, ᴴᴰ) cały film lektor PL``
    The rest (clickbait, no parenthesis) are skipped — they cannot be matched.
    """

    PROVIDER: ClassVar[str] = 'kinoswiatvod'
    HANDLE: ClassVar[str] = 'KinoSwiatVOD'

    def parse_video(self, raw_title: str) -> Optional[Dict]:
        match = _KSV_RE.match(raw_title or '')
        if not match:
            return None  # clickbait title without a parenthesis — unidentifiable
        local = match.group(1).strip()
        if not local or len(local) < 2:
            return None

        titles = {local}
        segments = [s.strip() for s in match.group(2).split(',') if s.strip()]
        year: Optional[int] = None
        quality_index: Optional[int] = None
        for index, seg in enumerate(segments):
            if _YEAR_RE.match(seg):
                year = int(seg)
            if quality_index is None and _QUALITY_SEG_RE.fullmatch(seg):
                quality_index = index

        # original title: the segment right before the quality tag (unless it's a year)
        if quality_index is not None and quality_index > 0:
            prev = segments[quality_index - 1]
            if not _YEAR_RE.match(prev):
                titles.add(prev)

        return {
            'titles': titles,
            'year': year,
            'info': _audio_info(raw_title.lower()),
        }


# ─── AleFilmy ───────────────────────────────────────────────────────────────────────

class AleFilmy(_source):
    """Scraper for the @alefilmy channel.

    Titles are consistent:
        ``TITLE | cast | genre | cały film | lektor po polsku``
    The movie title is the first segment before ``|``.
    """

    PROVIDER: ClassVar[str] = 'alefilmy'
    HANDLE: ClassVar[str] = 'alefilmy'

    def parse_video(self, raw_title: str) -> Optional[Dict]:
        raw_title = raw_title or ''
        if '|' not in raw_title:
            return None
        lower = raw_title.lower()
        if 'cały film' not in lower and 'caly film' not in lower:
            return None  # does not look like a full-movie entry
        name = raw_title.split('|', 1)[0].strip()
        if not name or len(name) < 2:
            return None
        return {
            'titles': {name},
            'year': None,  # the channel does not put the year in the title
            'info': _audio_info(lower),
        }


# ─── registration ───────────────────────────────────────────────────────────────────

def register(sources: 'List[SourceModule]', group: str) -> None:
    """Register the YouTube-channel scrapers."""
    from lib.sources import SourceModule
    for src in (KinoSwiatVOD, AleFilmy):
        sources.append(SourceModule(name=src.PROVIDER, provider=src(), group=group))
