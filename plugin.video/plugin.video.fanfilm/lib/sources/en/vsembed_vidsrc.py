# -*- coding: utf-8 -*-
"""
FanFilm - źródło: vsembed.ru, vidsrc.cc
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Both services provide IMDB-based embeds — no title search needed.

vsembed flow:
  movie:   GET https://vsembed.ru/embed/movie?imdb=IMDB_ID
  episode: GET https://vsembed.ru/embed/tv?imdb=IMDB_ID&season=S&episode=E
  resolve: → cloudnestra chain → HLS m3u8

vidsrc flow:
  movie:   GET https://vidsrc.cc/v2/embed/movie/IMDB_ID
  episode: GET https://vidsrc.cc/v2/embed/tv/IMDB_ID/SEASON/EPISODE
  resolve: → extract iframe src → follow redirect → cloudnestra / direct m3u8
"""

from __future__ import annotations

import re
from urllib.parse import urlencode, parse_qs
from typing import TYPE_CHECKING, ClassVar, Dict, List, Optional

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceModule, SourceTitleAlias

from lib.ff import requests
from lib.ff.item import FFItem
from lib.ff.source_utils import FF_UA
from lib.ff.resolve_utils import resolve_vsembed, resolve_rcp
from lib.ff.log_utils import fflog, fflog_exc


# ─── _ImdbEmbed ──────────────────────────────────────────────────────────────────

class _ImdbEmbed:
    """Base for IMDB-id-based embed providers."""

    ffitem: FFItem

    NAME: ClassVar[str]
    MOVIE_URL: ClassVar[str]   # format string with {imdb}
    TV_URL: ClassVar[str]      # format string with {imdb}, {season}, {episode}
    priority: ClassVar[int] = 1
    language: ClassVar[list] = ['en']

    def __init__(self):
        pass

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            return urlencode({'imdb': imdb, 'type': 'movie'})
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            return urlencode({'imdb': imdb, 'type': 'tv'})
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if url is None:
                return None
            data = {k: v[0] for k, v in parse_qs(url).items()}
            data.update({'season': season, 'episode': episode})
            return urlencode(data)
        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            if url is None:
                return result

            data = {k: v[0] for k, v in parse_qs(url).items()}
            imdb = data.get('imdb', '')
            content_type = data.get('type', 'movie')

            if content_type == 'tv':
                embed_url = self.TV_URL.format(
                    imdb=imdb,
                    season=data.get('season', '1'),
                    episode=data.get('episode', '1'),
                )
            else:
                embed_url = self.MOVIE_URL.format(imdb=imdb)

            fflog(f'query: {imdb!r}')
            sess = requests.Session()
            resp = sess.get(embed_url, timeout=10,
                            headers={'User-Agent': FF_UA},
                            allow_redirects=True)
            if resp.status_code == 404:
                fflog(f'search: 0 results')
                return result

            fflog(f'search: 1 results')
            result.append({
                'source': self.NAME,
                'url': embed_url,
                'quality': '720p',
                'language': 'en',
                'info': '',
                'filename': embed_url,
                'direct': False,
                'debridonly': False,
            })
        except Exception:
            fflog_exc()
        fflog(f'sources: {len(result)}')
        return result


# ─── source_vsembed ──────────────────────────────────────────────────────────────

class source_vsembed(_ImdbEmbed):
    NAME = 'vsembed'
    domains = ['vsembed.ru']
    BASE_LINK: ClassVar[str] = 'https://vsembed.ru'
    MOVIE_URL: ClassVar[str] = BASE_LINK + '/embed/movie?imdb={imdb}'
    TV_URL: ClassVar[str]    = BASE_LINK + '/embed/tv?imdb={imdb}&season={season}&episode={episode}'

    # ── public api ─────────────────────────────────────────────────────────

    def resolve(self, url: str) -> Optional[str]:
        return resolve_vsembed(url)


# ─── source_vidsrc ───────────────────────────────────────────────────────────────

class source_vidsrc(_ImdbEmbed):
    NAME = 'vidsrc'
    domains = ['vidsrc.me']
    BASE_LINK: ClassVar[str] = 'https://vidsrc.me'
    MOVIE_URL: ClassVar[str] = BASE_LINK + '/embed/movie/{imdb}'
    TV_URL: ClassVar[str]    = BASE_LINK + '/embed/tv/{imdb}/{season}/{episode}'

    # ── public api ─────────────────────────────────────────────────────────

    def resolve(self, url: str) -> Optional[str]:
        try:
            sess = requests.Session()
            sess.headers.update({'User-Agent': FF_UA, 'Referer': self.BASE_LINK + '/'})

            # Step 1: fetch embed page → data-hash divs (each is a cloudnestra rcp hash)
            resp = sess.get(url, timeout=15, allow_redirects=True)
            final_url = resp.url
            html = resp.text
            hashes = re.findall(r'class="server"[^>]*data-hash="([^"]+)"', html)
            if not hashes:
                fflog(f"vidsrc: no data-hash on {url[:60]}")
                return url

            for h in hashes:
                try:
                    fflog(f"vidsrc: trying rcp hash {h[:20]}...")
                    result = resolve_rcp(h, referer=final_url)
                    if result:
                        return result
                except Exception:
                    fflog_exc()

            fflog(f"vidsrc: all hashes failed for {url[:60]}")
            return url
        except Exception:
            fflog_exc()
            return url


def register(sources: List[SourceModule], group: str) -> None:
    from lib.sources import SourceModule
    for cls in (source_vsembed, source_vidsrc):
        sources.append(SourceModule(name=cls.NAME, provider=cls(), group=group))
