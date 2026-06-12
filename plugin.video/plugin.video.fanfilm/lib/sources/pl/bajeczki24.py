# -*- coding: utf-8 -*-
"""
FanFilm - źródło: bajeczki24.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import time
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, ClassVar

from lib.ff import requests
from lib.ff import control, cache, cleantitle
from lib.ff.item import FFItem
from lib.ff.source_utils import DEFAULT_UA, ShowData, ShowDataDict, show_data_asdict
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_link = 'https://bajeczki24.pl'
        self.headers = {
            'Referer': self.base_link,
            'User-Agent': DEFAULT_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3'
        }
        self.session = requests.Session()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        return self._search_movie(title, localtitle, year, 'movie', aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, int(year)))

    def episode(self, url: ShowDataDict | None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        if not url:
            return None
        data = ShowData(**url)
        return self._find_episode(data.title, data.local_title, data.year, season, episode)

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        try:
            if not url:
                return []

            resp = self.session.get(url, headers=self.headers, timeout=30, verify=False)
            if resp.status_code != 200:
                return []

            match_frame = re.search(r'<iframe.*?src="(.*?)"', resp.text)
            if not match_frame:
                return []

            frame = match_frame.group(1)
            # Extract short host name
            host_match = re.search(r'https?://(?:www\.)?([^./]+)', frame)
            host_name = host_match.group(1) if host_match else frame

            source = {
                'source': host_name,
                'quality': '1080p',
                'language': 'pl',
                'url': frame,
                'info': '',
                'filename': url,  # DEBUG
                'direct': False,
                'debridonly': False,
                'premium': False
            }
            fflog(f'sources: 1')
            return [source]
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _match_item(self, item: str, search_title_clean: str, year: Optional[str] = None, episode: Optional[str] = None) -> Optional[str]:
        item_match = re.search(r'<title>(.*?)</title>.*?<link>(.*?)</link>', item, flags=re.DOTALL)
        if not item_match:
            return None

        full_title_str = item_match.group(1).strip()
        item_link = item_match.group(2).strip()

        title_parts_match = re.match(r'(.+?)(?:\s+\((\d{4})\))?(?:\s*-\s*(S\d+E\d+))?$', full_title_str)
        if not title_parts_match:
            rss_title = full_title_str
            item_year = None
            item_episode = None
        else:
            rss_title = title_parts_match.group(1).strip()
            item_year_str = title_parts_match.group(2)
            item_year = int(item_year_str) if item_year_str else None
            item_episode = title_parts_match.group(3)

        item_title_clean = cleantitle.get(rss_title)

        if year and item_year and item_year != int(year):
            return None
        if search_title_clean != item_title_clean:
            return None
        if episode and item_episode != episode:
            return None

        return item_link

    def _search_items(self, title: str, year: Optional[str] = None, episode: Optional[str] = None, type_: str = 'movie') -> Optional[str]:
        html = self._get_cached_rss()
        if not html:
            return None

        items = re.findall(r'<item>(.*?)</item>', html, flags=re.DOTALL)
        fflog(f'search: {len(items)} results')
        search_title_clean = cleantitle.get(title)

        for item in items:
            link = self._match_item(item, search_title_clean, year, episode)
            if not link:
                continue

            if type_ == 'movie' and '/filmy/' not in link:
                continue

            return link

        return None

    def _search_movie(self, title: str, localtitle: str, year: str, type_: str, aliases: list[SourceTitleAlias] | None = None) -> Optional[str]:
        try:
            if not title:
                return None
            # Priority: local (Polish) title
            if localtitle:
                url = self._search_items(localtitle, year, type_='movie')
                if url:
                    return url
            # Fallback: English title
            if title.lower() != (localtitle or '').lower():
                return self._search_items(title, year, type_='movie')
            return None
        except Exception:
            fflog_exc()
            return None

    def _find_episode(self, en_title: str, localtitle: str, year: str, season: str, episode: str) -> Optional[str]:
        fflog(f'searching episode: localtitle={localtitle!r} en={en_title!r} year={year} S{int(season):02d}E{int(episode):02d}')
        try:
            se_pattern = f'S{int(season):02d}E{int(episode):02d}'
            # Priority: local (Polish) title
            if localtitle:
                url = self._search_items(localtitle, year, episode=se_pattern, type_='episode')
                if url:
                    return url
            # Fallback: English title
            if en_title.lower() != (localtitle or '').lower():
                return self._search_items(en_title, year, episode=se_pattern, type_='episode')
            fflog(f'no match: localtitle={localtitle!r} en={en_title!r} {se_pattern}')
            return None
        except Exception:
            fflog_exc()
            return None

    def _get_cached_rss(self) -> Optional[str]:
        """
        Fetch RSS using ETag (If-None-Match).
        No hard time-based expiry — freshness is managed server-side (Cloudflare).
        """
        try:
            cache_key_rss = 'bajeczki24_rss'
            cache_key_etag = 'bajeczki24_rss_etag'

            row = cache.cache_get(cache_key_rss, control.providercacheFile)
            cached_rss = row['value'] if row else None

            row_etag = cache.cache_get(cache_key_etag, control.providercacheFile)
            cached_etag = row_etag['value'] if row_etag else None

            headers = dict(self.headers)
            if cached_etag:
                headers['If-None-Match'] = cached_etag

            rss_url = f'{self.base_link}/rss.xml'
            r = self.session.get(rss_url, headers=headers, timeout=30, verify=False)

            if r.status_code == 304:
                return cached_rss

            if r.status_code != 200:
                return cached_rss if cached_rss else None

            html = r.text
            etag = r.headers.get('ETag')

            cache.cache_insert(cache_key_rss, html, control.providercacheFile)
            if etag:
                cache.cache_insert(cache_key_etag, etag, control.providercacheFile)

            return html

        except Exception:
            fflog_exc()
            cached_rss = cache.cache_value('bajeczki24_rss', control.providercacheFile)
            return cached_rss if cached_rss else None
