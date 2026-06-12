# -*- coding: utf-8 -*-
"""
FanFilm - moduł: yt_utils
Copyright (C) 2026 :)

Shared helpers for YouTube-based scrapers (no API key required), used by:
  * lib/sources/pl/youtube_channels.py — full movies from distributor channels
  * lib/sources/en/african.py          — African cinema (Nollywood, Wakaliwood…)

Techniques:
  * a search-results / channel-search page embeds ``ytInitialData`` with the
    matching videos,
  * the ``SOCS=CAI`` cookie skips the EU cookie-consent interstitial
    (without it the page contains no ytInitialData).

NOTE: when YouTube changes its JSON structure (``videoRenderer`` /
``lockupViewModel``), parsing breaks — look here first.

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Dict, List, Optional, TYPE_CHECKING

from lib.ff import requests
from lib.ff.log_utils import fflog

if TYPE_CHECKING:
    from requests import Session


# ─── constants ──────────────────────────────────────────────────────────────────────

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36')

#: Letters that are not diacritics (NFKD does not decompose them).
_LETTER_MAP = str.maketrans({
    'ł': 'l', 'Ł': 'l', 'ø': 'o', 'Ø': 'o', 'đ': 'd', 'Đ': 'd',
    'ß': 'ss', 'æ': 'ae', 'Æ': 'ae', 'œ': 'oe', 'Œ': 'oe', 'ı': 'i',
})

_DURATION_RE = re.compile(r'^\d{1,2}(?::\d{2}){1,2}$')


# ─── session ────────────────────────────────────────────────────────────────────────

def make_session() -> 'Session':
    """A requests session with the cookie that skips the consent page."""
    session = requests.Session()
    session.cookies.set('SOCS', 'CAI', domain='.youtube.com')
    return session


# ─── pure helpers ───────────────────────────────────────────────────────────────────

def norm_title(text: Optional[str]) -> str:
    """Normalize a title for comparison: no diacritics, no non-alphanumerics."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.translate(_LETTER_MAP).lower()
    return re.sub(r'[^a-z0-9]+', '', text)


def title_tokens(text: Optional[str]) -> List[str]:
    """Split a title into tokens (words) — no diacritics, lower-case."""
    if not text:
        return []
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.translate(_LETTER_MAP).lower()
    return [t for t in re.split(r'[^a-z0-9]+', text) if t]


def parse_duration(text: Optional[str]) -> int:
    """Convert 'H:MM:SS' / 'M:SS' into seconds (0 if not recognised)."""
    text = (text or '').strip()
    if not _DURATION_RE.match(text):
        return 0
    parts = [int(p) for p in text.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


# ─── ytInitialData parsing ──────────────────────────────────────────────────────────

def _lockup_duration(lvm: Dict) -> int:
    """Extract the duration from the thumbnail badge (lockupViewModel format)."""
    try:
        overlays = lvm['contentImage']['thumbnailViewModel']['overlays']
    except (KeyError, TypeError):
        return 0
    for overlay in overlays:
        for badge in overlay.get('thumbnailBottomOverlayViewModel', {}).get('badges', []):
            text = badge.get('thumbnailBadgeViewModel', {}).get('text', '')
            if _DURATION_RE.match(text):
                return parse_duration(text)
    return 0


def extract_videos(node: object) -> List[Dict]:
    """Collect video entries from any ytInitialData structure.

    Handles both the ``videoRenderer`` and the newer ``lockupViewModel``
    video entry shapes. Returns a list of dicts
    ``{'id', 'title', 'duration', 'channel'}``.
    """
    out: List[Dict] = []

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            lvm = obj.get('lockupViewModel')
            if isinstance(lvm, dict) and lvm.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO':
                vid = lvm.get('contentId')
                try:
                    title = lvm['metadata']['lockupMetadataViewModel']['title']['content']
                except (KeyError, TypeError):
                    title = ''
                if vid and title:
                    out.append({'id': vid, 'title': title,
                                'duration': _lockup_duration(lvm), 'channel': ''})
            vr = obj.get('videoRenderer')
            if isinstance(vr, dict):
                vid = vr.get('videoId')
                title = ''.join(r.get('text', '') for r in vr.get('title', {}).get('runs', []))
                if vid and title:
                    channel = ''
                    runs = (vr.get('longBylineText') or vr.get('ownerText') or {}).get('runs', [])
                    if runs:
                        channel = runs[0].get('text', '')
                    out.append({
                        'id': vid, 'title': title, 'channel': channel,
                        'duration': parse_duration(vr.get('lengthText', {}).get('simpleText', '')),
                    })
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(node)
    return out


# ─── YouTube fetching ───────────────────────────────────────────────────────────────

def search_channel(handle: str, query: str, *, hl: str = 'pl',
                   session: Optional['Session'] = None) -> List[Dict]:
    """Search a single YouTube channel for ``query``.

    Fetches ``/@handle/search?query=…`` — one request, results scoped to that
    channel. Returns a list of ``{'id','title','duration','channel'}``.
    """
    own_session = session is None
    session = session or make_session()
    from urllib.parse import quote_plus
    try:
        resp = session.get(
            f'https://www.youtube.com/@{handle}/search?query={quote_plus(query)}',
            headers={'User-Agent': UA, 'Accept-Language': f'{hl},{hl};q=0.9'},
            timeout=30)
    except Exception as exc:
        fflog(f'@{handle} search {query!r}: failed: {exc!r}')
        return []
    finally:
        if own_session:
            session.close()
    if resp.status_code != 200 or 'consent.youtube.com' in resp.url:
        fflog(f'@{handle} search {query!r}: HTTP {resp.status_code} / consent')
        return []
    m_data = re.search(r'var ytInitialData\s*=\s*({.*?});</script>', resp.text, re.DOTALL)
    if not m_data:
        m_data = re.search(r'ytInitialData\s*=\s*({.*?});', resp.text, re.DOTALL)
    if not m_data:
        fflog(f'@{handle} search {query!r}: no ytInitialData')
        return []
    try:
        data = json.loads(m_data.group(1))
    except json.JSONDecodeError:
        return []
    videos = extract_videos(data)
    fflog(f'@{handle} search {query!r}: {len(videos)} result(s)')
    return videos


def youtube_search(query: str, *, hl: str = 'en', gl: str = 'US',
                   session: Optional['Session'] = None) -> List[Dict]:
    """Search YouTube. Returns a list of ``{'id','title','duration','channel'}``."""
    own_session = session is None
    session = session or make_session()
    from urllib.parse import quote_plus
    try:
        resp = session.get(
            f'https://www.youtube.com/results?search_query={quote_plus(query)}',
            headers={'User-Agent': UA, 'Accept-Language': f'{hl}-{gl},{hl};q=0.9'},
            timeout=30)
    except Exception as exc:
        fflog(f'search {query!r}: failed: {exc!r}')
        return []
    if resp.status_code != 200 or 'consent.youtube.com' in resp.url:
        fflog(f'search {query!r}: HTTP {resp.status_code} / consent')
        return []
    m_data = re.search(r'var ytInitialData\s*=\s*({.*?});</script>', resp.text, re.DOTALL)
    if not m_data:
        m_data = re.search(r'ytInitialData\s*=\s*({.*?});', resp.text, re.DOTALL)
    if not m_data:
        fflog(f'search {query!r}: no ytInitialData')
        return []
    try:
        data = json.loads(m_data.group(1))
    except json.JSONDecodeError:
        return []
    if own_session:
        session.close()
    return extract_videos(data)


# ─── scraper helpers ────────────────────────────────────────────────────────────────

def gather_titles(ffitem: object, title: str, localtitle: str,
                  aliases: object) -> List[str]:
    """Collect every known title variant (from args, ffitem.vtag, aliases)."""
    titles: List[str] = [t for t in (title, localtitle) if t]
    vtag = getattr(ffitem, 'vtag', None)
    if vtag is not None:
        for getter in ('getOriginalTitle', 'getEnglishTitle', 'getTitle'):
            try:
                value = getattr(vtag, getter)()
            except Exception:
                value = ''
            if value:
                titles.append(value)
    for alias in aliases or ():
        for key in ('title', 'originalname'):
            value = alias.get(key)
            if value:
                titles.append(value)
    return titles


def youtube_play_url(url: str) -> str:
    """Convert a YouTube video URL into a plugin.video.youtube address."""
    match = re.search(r'(?:v=|youtu\.be/|/watch\?.*v=)([A-Za-z0-9_-]{11})', url or '')
    if match:
        return f'plugin://plugin.video.youtube/play/?video_id={match.group(1)}'
    return url


def youtube_source(video: Dict, *, language: str, info: str = '') -> Dict:
    """Build a source entry (SourceItem) for a YouTube movie."""
    return {
        'source': 'youtube',
        'quality': video.get('quality', 'HD'),
        'language': language,
        'url': f'https://www.youtube.com/watch?v={video["id"]}',
        'info': info,
        'filename': video.get('title', ''),
        'direct': False,
        'debridonly': False,
        'premium': False,
    }
