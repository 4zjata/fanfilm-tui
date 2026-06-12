# -*- coding: utf-8 -*-
"""
FanFilm - źródło: streamimdb.me / cloudnestra (PlayIMDB)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  movie/tvshow  → IMDB ID (str)
  episode()     → playimdb://{imdb}/{season}/{episode}
  sources()     → wewnętrzny URL, bez HTTP, jakość zakładana 1080p
  resolve()     → embed page → data-hash → resolve_rcp() (wspólny łańcuch cloudnestra)
"""

from __future__ import annotations

import re
from typing import ClassVar, List, TYPE_CHECKING

from lib.ff import requests
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.resolve_utils import resolve_rcp
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    priority: ClassVar[int] = 100
    language: ClassVar[List[str]] = ['en']

    ffitem: FFItem

    _MOVIE_URL = 'https://streamimdb.me/embed/{imdb}'
    _TV_URL    = 'https://streamimdb.me/embed/tv?imdb={imdb}&season={season}&episode={episode}&color=e600e6'

    _BASE_HEADERS: dict[str, str] = {
        'User-Agent': DEFAULT_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    def __init__(self):
        self._session: requests.Session | None = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self._BASE_HEADERS)
        return self._session

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> str | None:
        return imdb or None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: list[SourceTitleAlias], year: str) -> str | None:
        return imdb or None

    def episode(self, url: str | None, imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> str | None:
        if not url:
            return None
        return f'playimdb://{url}/{season}/{episode}'

    def sources(self, url: str | None, hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        if not url:
            return []
        return [{
            'source': 'cloudnestra',
            'quality': '1080p',
            'language': 'en',
            'url': url,
            'info': '',
            'direct': False,
            'debridonly': False,
        }]

    def resolve(self, url: str) -> str | None:
        try:
            imdb, season, episode = self._parse_url(url)
            data_hash = self._fetch_data_hash(imdb, season, episode)
            # rcp → prorcp → CDN m3u8 (shared chain, with per-IP proxy fallback)
            return resolve_rcp(data_hash, referer='https://streamimdb.me/')
        except Exception:
            fflog_exc()
            return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _parse_url(self, url: str) -> tuple[str, int | None, int | None]:
        """Parse playimdb://{imdb}/{season}/{episode} or bare IMDB ID."""
        if url.startswith('playimdb://'):
            url = url[len('playimdb://'):]
        parts = url.split('/')
        if len(parts) == 3:
            return parts[0], int(parts[1]), int(parts[2])
        return parts[0], None, None

    def _fetch_data_hash(self, imdb: str, season: int | None, episode: int | None) -> str:
        """GET embed page → return data-hash attribute (a cloudnestra rcp hash)."""
        if season is not None:
            url = self._TV_URL.format(imdb=imdb, season=season, episode=episode)
            extra = {
                'Referer': f'https://streamimdb.me/embed/{imdb}',
                'Sec-Fetch-Dest': 'iframe',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
            }
        else:
            url = self._MOVIE_URL.format(imdb=imdb)
            extra = {
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            }
        r = self.session.get(url, headers=extra)
        r.raise_for_status()
        m = re.search(r'data-hash="([^"]+)"', r.text)
        if not m:
            raise ValueError(f'No data-hash on embed page for {imdb!r}')
        return m.group(1)


if __name__ == '__main__':
    try:
        from lib.ff.cmdline import DebugArgumentParser as ArgumentParser
    except ImportError:
        from argparse import ArgumentParser
    parser = ArgumentParser(description='Test PlayIMDB source provider')
    parser.add_argument('imdb_id', help='IMDB ID, e.g. tt1392214 or tt1392214/1/3')
    args = parser.parse_args()
    src = source()
    try:
        from pprint import pprint
        sources = src.sources(args.imdb_id, [], [])
        result = src.resolve(sources[0]['url']) if sources else None
        pprint({'sources': sources, 'resolved': result})
    except Exception as e:
        print(f'Error: {e}')
