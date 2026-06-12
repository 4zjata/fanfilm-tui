# -*- coding: utf-8 -*-
"""
FanFilm - źródło: filmlinks4u.guru (Bollywood / Hindi / South Indian)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  search:  GET /wp-json/wp/v2/posts?search=TITLE&per_page=10  → JSON [{title, link, date}]
  movie:   GET /slug/  → <iframe src="https://speedostreamN.com/embed-HASH.html">
  resolve: GET speedostream embed → file: "https://.../master.m3u8?t=TOKEN..."
           → isa+m3u8|headers
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, TYPE_CHECKING, ClassVar
from urllib.parse import urlencode, parse_qs, quote_plus

from lib.ff import client, cleantitle
from lib.ff.item import FFItem
from lib.ff.source_utils import DEFAULT_UA, is_indian_content, quality_from_resolution, year_matches
from lib.ff.log_utils import fflog, fflog_exc
if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

_BASE = 'https://filmlinks4u.guru'
_API  = _BASE + '/wp-json/wp/v2/posts'
_HEADERS = {'User-Agent': DEFAULT_UA, 'Referer': _BASE + '/'}

_LANG_NAMES = {
    'hi': 'Hindi', 'ta': 'Tamil', 'te': 'Telugu', 'ml': 'Malayalam',
    'kn': 'Kannada', 'bn': 'Bengali', 'mr': 'Marathi', 'pa': 'Punjabi', 'gu': 'Gujarati',
}

_RE_YEAR_IN_TITLE = re.compile(r'\s*\((\d{4})\).*$')
_RE_SPEEDOSTREAM  = re.compile(r'<iframe[^>]+src=["\']([^"\']*speedostream[^"\']+)["\']', re.I)
_RE_M3U8          = re.compile(r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']')


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.domains = ['filmlinks4u.guru']

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not is_indian_content(self.ffitem):
                return None
            return urlencode({'title': title, 'year': year, 'imdb': imdb})
        except Exception:
            fflog_exc()

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not is_indian_content(self.ffitem):
                return None
            return urlencode({'tvshowtitle': tvshowtitle, 'year': year, 'imdb': imdb})
        except Exception:
            fflog_exc()

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if url is None:
                return None
            data = {k: v[0] for k, v in parse_qs(url).items()}
            data.update({'title': title, 'premiered': premiered,
                         'season': season, 'episode': episode})
            return urlencode(data)
        except Exception:
            fflog_exc()

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            if not url:
                return result
            data = {k: v[0] for k, v in parse_qs(url).items()}
            is_tv = 'tvshowtitle' in data
            title  = data.get('tvshowtitle' if is_tv else 'title', '')
            year   = data.get('year', '')

            post_url = self._search(title, year)
            if not post_url:
                return result

            embed_url, quality = self._get_embed_url(post_url)
            if not embed_url:
                return result

            result.append({
                'source': 'filmlinks4u',
                'quality': quality,
                'language': 'en',
                'url': embed_url,
                'info': self._lang_info(),
                'direct': False,
                'debridonly': False,
            })
        except Exception:
            fflog_exc()
        fflog(f'sources: {len(result)}')
        return result

    def resolve(self, url: str) -> str:
        try:
            r = client.request(url, headers={**_HEADERS, 'Referer': _BASE + '/'})
            if not r:
                return url
            m = _RE_M3U8.search(r)
            if not m:
                fflog(f'no m3u8 in {url[:60]}')
                return url
            m3u8_url = m.group(1)
            embed_origin = re.match(r'(https?://[^/]+)', url)
            referer = (embed_origin.group(1) + '/') if embed_origin else _BASE + '/'
            fflog(f'resolve → {m3u8_url[:80]}')
            # AES-128 key requires Referer — use plain URL (ffmpeg passes headers to key requests)
            return f"{m3u8_url}|Referer={referer}&User-Agent={DEFAULT_UA}"
        except Exception:
            fflog_exc()
            return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _lang_info(self) -> str:
        try:
            langs = self.ffitem.vtag.getLanguageCodes()
            names = [_LANG_NAMES[l] for l in langs if l in _LANG_NAMES]
            if names:
                return ' | '.join(names)
            if 'IN' in (self.ffitem.vtag.getCountryCodes() or []):
                return 'Hindi'
        except Exception:
            pass
        return ''

    def _search(self, title: str, year: str) -> Optional[str]:
        """WP REST API search → return best-matching post URL or None."""
        fflog(f'query: {title!r}')
        api_url = f"{_API}?search={quote_plus(title)}&per_page=10&_fields=title,link,date"
        raw = client.request(api_url, headers=_HEADERS)
        if not raw:
            return None
        try:
            posts = json.loads(raw)
        except Exception:
            return None

        fflog(f'search: {len(posts)} results')
        clean = cleantitle.get(title)
        for post in posts:
            raw_title = post.get('title', {}).get('rendered', '')
            year_match = _RE_YEAR_IN_TITLE.search(raw_title)
            post_title = raw_title[:year_match.start()] if year_match else raw_title
            post_year  = year_match.group(1) if year_match else ''
            if cleantitle.get(post_title) != clean:
                continue
            if year and post_year and not year_matches(post_year, int(year)):
                continue
            link = post.get('link', '')
            fflog(f'matched {raw_title!r} → {link}')
            return link

        fflog(f'no match for {title!r} ({year})')
        return None

    def _get_embed_url(self, post_url: str) -> tuple[Optional[str], str]:
        """Fetch movie page → return (speedostream embed URL, quality)."""
        html = client.request(post_url, headers=_HEADERS)
        if not html:
            return None, 'SD'
        m = _RE_SPEEDOSTREAM.search(html)
        if not m:
            fflog(f'no speedostream embed in {post_url}')
            return None, 'SD'
        embed = m.group(1)
        if embed.startswith('//'):
            embed = 'https:' + embed
        return embed, self._detect_quality(embed)

    def _detect_quality(self, embed_url: str) -> str:
        """Fetch speedostream page + master m3u8 → detect max resolution."""
        try:
            r = client.request(embed_url, headers={**_HEADERS, 'Referer': _BASE + '/'})
            if not r:
                return 'SD'
            m3u8_match = _RE_M3U8.search(r)
            if not m3u8_match:
                return 'SD'
            master = client.request(m3u8_match.group(1), headers=_HEADERS)
            if not master:
                return 'SD'
            widths = [int(w) for w in re.findall(r'RESOLUTION=(\d+)x\d+', master)]
            if not widths:
                fflog('no RESOLUTION in m3u8 → 720p (fallback)')
                return '720p'
            max_w = max(widths)
            quality = quality_from_resolution(width=max_w)
            fflog(f'max_width={max_w} → {quality}')
            return quality
        except Exception:
            return 'SD'
