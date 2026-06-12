# -*- coding: utf-8 -*-
"""
FanFilm - źródło: African cinema (YouTube)
Copyright (C) 2026 :)

African film industries publish full movies on YouTube as their main
distribution channel — Nollywood (Nigeria), Wakaliwood (Uganda), plus
Ghanaian and Kenyan cinema. This scraper — only for movies whose TMDB
production country is NG/UG/GH/KE — searches YouTube for a full version
and returns it as a ``youtube`` source.

Matching is conservative (video titles on these channels tend to be noisy):
  * the movie title tokens must appear as a contiguous run in the video title,
  * sequel guard (e.g. "Oloture 2" must not match "Oloture"),
  * duration must fall within the feature-length window (drops trailers/compilations).

Playback goes through ``plugin.video.youtube``.
Shared, fragile YouTube scraping code lives in ``lib/ff/yt_utils.py``.

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from typing import ClassVar, Dict, List, Optional, Sequence, TYPE_CHECKING

from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.yt_utils import (gather_titles, title_tokens, youtube_play_url,
                             youtube_search, youtube_source)
from lib.sources import Provider

if TYPE_CHECKING:
    from lib.ff.sources import SourceItem
    from lib.sources import SourceModule, SourceTitleAlias


# ─── matching constants & helpers ───────────────────────────────────────────────────

#: Supported African-cinema countries → keyword that refines the YouTube query.
_COUNTRIES = {
    'NG': 'nigerian',    # Nollywood
    'UG': 'ugandan',     # Wakaliwood
    'GH': 'ghanaian',
    'KE': 'kenyan',
}

_SEQUEL_RE = re.compile(r'^(?:[2-9]|ii+|part)$')
_MARKER_RE = re.compile(
    r'\b(?:full movie|nollywood|wakaliwood|nigerian|ugandan|ghanaian|kenyan'
    r'|african|yoruba)\b', re.IGNORECASE)
#: Words marking material that is NOT the movie (commentary, livestream,
#: reaction etc.) — such a video can be as long as the film, so the duration
#: filter does not weed it out.
_BLOCK_RE = re.compile(
    r'\b(?:commentary|live ?stream|reaction|review|trailer|teaser|festival'
    r'|behind[ -]the[ -]scenes|making of|deleted scene|bloopers'
    r'|interview|podcast|recap|explained|breakdown)\b', re.IGNORECASE)
#: Separators splitting the movie name from the rest of a video title (cast, blurb…).
_SEP_RE = re.compile(r'[-–—|;:(\[]')

#: "Filler" words — when one follows a short title (or a year / quality tag),
#: it means the movie title has already ended.
_FILLER = frozenset((
    'full', 'movie', 'movies', 'latest', 'new', 'official', 'nollywood',
    'wakaliwood', 'nigeria', 'nigerian', 'uganda', 'ugandan', 'ghana',
    'ghanaian', 'kenya', 'kenyan', 'african', 'yoruba', 'igbo', 'hausa',
    'swahili', 'drama', 'classic', 'cinema', 'trending', 'blockbuster',
    'exclusive', 'premiere', 'released', 'newly', 'hd', 'fhd', 'uhd',
))
_FILLER_RE = re.compile(r'^(?:(?:19|20)\d{2}|\d{3,4}p|4k)$')


def _is_filler(token: str) -> bool:
    """Whether a token is a filler word / a year / a quality tag."""
    return token in _FILLER or bool(_FILLER_RE.match(token))


def _name_segment(title: Optional[str]) -> str:
    """The part of a video title before the first separator — usually the movie name."""
    return _SEP_RE.split(title or '', maxsplit=1)[0]


# ─── source ─────────────────────────────────────────────────────────────────────────

class source(Provider):
    """African-cinema scraper (Nollywood, Wakaliwood…) backed by YouTube."""

    # __init__ takes no ffitem — the loader assigns .ffitem after construction
    INIT_WITH_FFITEM: ClassVar[bool] = False

    # --- scraper api ---
    PROVIDER: ClassVar[str] = 'african'
    priority: ClassVar[int] = 2
    language: ClassVar[Sequence[str]] = ['en']

    # --- configuration ---
    #: Duration window accepted as a full movie (seconds).
    MIN_DURATION: ClassVar[int] = 40 * 60
    MAX_DURATION: ClassVar[int] = 240 * 60
    #: At most this many sources are returned per movie.
    MAX_RESULTS: ClassVar[int] = 3

    def __init__(self) -> None:
        self._found: List[Dict] = []

    # ── public api ──────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if self.ffitem.ref.type != 'movie':
            return None

        # the scraper runs only for cinema from the supported African countries
        try:
            countries = self.ffitem.vtag.getCountryCodes()
        except Exception:
            countries = []
        country = next((c for c in (countries or ()) if c in _COUNTRIES), None)
        if not country:
            return None

        # title variants → unique token sequences to match against
        token_seqs: List[List[str]] = []
        for name in gather_titles(self.ffitem, title, localtitle, aliases):
            toks = title_tokens(name)
            if toks and toks not in token_seqs:
                token_seqs.append(toks)
        if not token_seqs:
            return None

        # a fresh search on every open — no result cache
        query = f'{title} {year} {_COUNTRIES[country]} full movie'.strip()
        results = youtube_search(query)
        fflog(f'search {query!r}: {len(results)} results')

        matched = self._match(token_seqs, results)
        if not matched:
            fflog(f'{self.ffitem} not found')
            return None
        self._found = matched
        fflog(f'{self.ffitem}: {len(matched)} match(es)')
        return self._movie_id(imdb)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> None:
        return None  # YouTube — movies only

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> None:
        return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str],
                from_cache: bool = False) -> 'List[SourceItem]':
        if not url:
            return []
        result: List[SourceItem] = [youtube_source(video, language='en')
                                    for video in self._found]
        fflog(f'sources: {len(result)}')
        return result

    def resolve(self, url: str, *, buy_anyway: bool = False) -> str:
        """Convert a YouTube URL into a plugin.video.youtube address."""
        return youtube_play_url(url)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _movie_id(self, imdb: str) -> str:
        """Non-empty id returned from movie() (passed through to sources())."""
        return str(self.ffitem.ids.get('tmdb') or imdb or self.PROVIDER)

    def _match(self, token_seqs: List[List[str]], results: List[Dict]) -> List[Dict]:
        """From the search results pick feature-length movies matching by title."""
        scored: List[tuple] = []
        seen: set = set()
        for video in results:
            vid = video.get('id')
            duration = video.get('duration', 0)
            if not vid or vid in seen:
                continue
            if not (self.MIN_DURATION <= duration <= self.MAX_DURATION):
                continue  # trailer / clip / channel compilation
            if _BLOCK_RE.search(video.get('title') or ''):
                continue  # director's commentary / livestream / reaction — not the film
            full_tokens = title_tokens(video.get('title'))
            name_tokens = title_tokens(_name_segment(video.get('title')))
            if not any(self._title_match(seq, full_tokens, name_tokens)
                       for seq in token_seqs):
                continue
            seen.add(vid)
            scored.append((self._score(video), duration, video))

        scored.sort(key=lambda x: (-x[0], abs(x[1] - 110 * 60)))
        return [{'id': v['id'], 'title': v['title'], 'duration': v['duration'],
                 'quality': '1080p'}  # YouTube serves the best stream itself
                for _, _, v in scored[:self.MAX_RESULTS]]

    @staticmethod
    def _title_match(needle: List[str], full_tokens: List[str],
                     name_tokens: List[str]) -> bool:
        """Whether the movie title (``needle``) matches a video title.

        Short titles (≤2 words) carry a high false-positive risk, so they must
        either equal the video's leading name, or the video title must start
        with the movie name followed by a filler token (year / marketing word)
        — otherwise "Crocodile" would catch "Crocodile Smile". Longer titles
        only need to appear as a contiguous run of words (with a sequel guard,
        e.g. "Oloture 2").
        """
        n = len(needle)
        if n == 0:
            return False
        if n <= 2:
            if name_tokens == needle:
                return True  # a separator right after the title
            if full_tokens[:n] == needle:
                after = full_tokens[n] if len(full_tokens) > n else ''
                if not after or _is_filler(after):
                    return True
            return False
        if len(full_tokens) < n:
            return False
        for i in range(len(full_tokens) - n + 1):
            if full_tokens[i:i + n] == needle:
                after = full_tokens[i + n] if i + n < len(full_tokens) else ''
                if _SEQUEL_RE.match(after):
                    continue  # a sequel, not this movie
                return True
        return False

    @staticmethod
    def _score(video: Dict) -> int:
        """Heuristic match confidence (higher = better)."""
        score = 0
        duration = video.get('duration', 0)
        if 70 * 60 <= duration <= 180 * 60:
            score += 3
        low = (video.get('title') or '').lower()
        if 'full movie' in low:
            score += 2
        if _MARKER_RE.search(low):
            score += 1
        return score
