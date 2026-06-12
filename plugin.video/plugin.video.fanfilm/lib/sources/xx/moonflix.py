# -*- coding: utf-8 -*-
"""
FanFilm - źródło: moonflix.com (Restored Classic Films)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
import base64
from typing import Dict, List, Optional, TYPE_CHECKING, ClassVar
from urllib.parse import urlencode, urlparse

from lib.ff import requests, cleantitle
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.base_url = 'https://moonflix.com'
        self.session = requests.Session()
        self.headers = {
            'User-Agent': DEFAULT_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
            'Referer': self.base_url + '/',
        }

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not year or not year.isdigit() or int(year) > 1940:
                return None
            return self._find_page(title, year)
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        try:
            if not url:
                return []
            r = self.session.get(url, headers=self.headers, timeout=20)
            if r.status_code != 200:
                return []
            result = self._extract_sources(r.text)
            fflog(f'sources: {len(result)}')
            return result
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        if 'vimeo.com' in url and '/external/' in url:
            m = re.search(r'/external/(\d+)', url)
            if m:
                # Use $$ trick to pass referer to Vimeo resolver
                # and use a format that doesn't trigger the 404 behavior in VimeoResolver
                return f"https://vimeo.com/{m.group(1)}$${self.base_url}/"

        headers_str = urlencode({'User-Agent': DEFAULT_UA, 'Referer': self.base_url + '/'})
        return f"{url}|{headers_str}"

    # ── helpers ────────────────────────────────────────────────────────────

    def _find_page(self, title: str, year: str) -> Optional[str]:
        """Search moonflix and return the best matching page URL."""
        fflog(f'query: {title!r}')
        r = self.session.get(f"{self.base_url}/", params={'s': title},
                             headers=self.headers, timeout=20)
        if r.status_code != 200:
            return None

        search_clean = cleantitle.get(title)
        search_year = int(year)

        candidates = re.findall(
            r'href="(https://moonflix\.com/[a-z0-9/-]+)"[^>]*>([^<]{5,120})</a>',
            r.text
        )
        fflog(f'search: {len(candidates)} results')

        best_url = None
        best_exact = False

        for url, raw_title in candidates:
            if any(x in url for x in ['category', 'tag', 'page', '#', 'wp-', 'register', 'profile']):
                continue

            # extract year from moonflix title, e.g. "Metropolis (1927) The New Pollutants Score"
            year_m = re.search(r'\((\d{4})\)', raw_title)
            if not year_m or abs(int(year_m.group(1)) - search_year) > 1:
                continue

            # extract base title (everything before the year parenthesis)
            base_title = raw_title[:year_m.start()].strip()
            # strip rescores/subtitles after " | " or " – "
            base_title = re.split(r'\s*[\|–-]\s*', base_title)[0].strip()

            item_clean = cleantitle.get(base_title)
            exact = item_clean == search_clean
            partial = search_clean in item_clean or item_clean in search_clean

            if not (exact or partial):
                continue

            if exact and not best_exact:
                best_url = url
                best_exact = True
            elif not best_exact and best_url is None:
                best_url = url

        fflog(f"moonflix: {'found' if best_url else 'not found'} for '{title}' ({year})")
        return best_url

    def _extract_sources(self, html: str) -> List[SourceItem]:
        """Extract all video versions from a movie page."""
        results = []
        seen_urls = set()

        for raw_attr in re.findall(r'data-video-source="([^"]+)"', html):
            for src, label in re.findall(r"\{source:'([^']+)',\s*label:'([^']+)'\}", raw_attr):
                m3u8_url = self._decode_source(src)
                if not m3u8_url or m3u8_url in seen_urls:
                    continue
                seen_urls.add(m3u8_url)

                host = urlparse(m3u8_url).netloc
                source_name = host.rsplit('.', 1)[0].rsplit('.', 1)[-1] if host else 'moonflix.com'
                if not source_name: source_name = 'moonflix.com'

                is_color = 'color' in label.lower() or 'coloriz' in label.lower()
                info = 'Colorized AI' if is_color else 'Original B&W'
                quality = '4K' if '4k' in label.lower() else '1080p'

                results.append({
                    'source': source_name,
                    'quality': quality,
                    'language': 'en',
                    'url': m3u8_url,
                    'info': info,
                    'filename': '',
                    'direct': False,
                    'debridonly': False,
                    'premium': False,
                })

        return results

    def _decode_source(self, src: str) -> Optional[str]:
        if src.startswith('encrypt:'):
            try:
                return base64.b64decode(src[8:]).decode('utf-8')
            except Exception:
                return None
        return src if src.startswith('http') else None
