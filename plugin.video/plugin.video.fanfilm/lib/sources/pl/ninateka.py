# -*- coding: utf-8 -*-
"""
FanFilm ‑ źródło: ninateka.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji GPL‑3.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING, ClassVar
from lib.ff import requests
from lib.ff import cleantitle
from lib.ff.item import FFItem
from lib.ff.source_utils import DEFAULT_UA, get_quality_from_filename
from lib.ff.log_utils import fflog
from lib.ff.resolve_utils import build_drmff

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem  # assigned dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_url = 'https://ninateka.pl'
        self.api_url = self.base_url + '/api/'
        self.platform = 'BROWSER'

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> list[SourceItem]:
        # fflog(f"searching for {localtitle} ({year})")
        # Skip if Poland is not in the country list
        if 'PL' not in self.ffitem.vtag.getCountryCodes():
            fflog('not a Polish film, skip')
            return []
        search_url = self.api_url + 'products/vods/search/VOD'
        params = self._make_params()
        params['keyword'] = cleantitle.geturl(localtitle)

        try:
            fflog(f'query: {params["keyword"]!r}')
            response = requests.get(search_url, headers=self._make_headers(), params=params)
            if response.status_code != 200:
                return []

            results = response.json().get('items', [])
            fflog(f'search: {len(results)} results')

            sources = []
            for item in results:
                if item.get('type') != 'VOD':
                    continue

                normalized_item_title = cleantitle.normalize(item['title'])
                item_year = int(item.get('year', 0))
                search_year = int(year)

                if cleantitle.normalize(localtitle) in normalized_item_title and abs(item_year - search_year) <= 1:
                    # fflog(f"match: {item['title']} ({item.get('year', '')})")

                    is_audiodescription = 'audiodeskrypcja' in item['title'].lower()

                    eid = item['id']
                    tenant = item['tenant']['uid']
                    play_url = self.api_url + f'products/{eid}/videos/playlist'
                    playlist_params = {
                        'videoType': 'MOVIE',
                        'platform': self.platform,
                        'tenant': tenant
                    }
                    playlist_response = requests.get(play_url, headers=self._make_headers(), params=playlist_params)
                    if playlist_response.status_code == 200:
                        playlist_data = playlist_response.json()
                        if 'sources' in playlist_data and 'DASH' in playlist_data['sources'] and playlist_data['sources']['DASH']:
                            for stream in playlist_data['sources']['DASH']:
                                stream_url = stream['src']
                                if stream_url.startswith('//'):
                                    stream_url = 'https:' + stream_url

                                quality = get_quality_from_filename(stream_url) or 'SD'

                                info = 'Lektor'
                                if is_audiodescription:
                                    info += ' - Audiodeskrypcja'

                                source_info = {
                                    'source': 'Ninateka',
                                    'quality': quality,
                                    'language': 'pl',
                                    'url': f"DRMFF|{item['id']}|{item['tenant']['uid']}",
                                    'info': info,
                                    'info2': '',
                                    'direct': False,  # needs resolving
                                    'debridonly': False
                                }
                                sources.append(source_info)
            fflog(f'sources: {len(sources)}')
            return sources
        except Exception as e:
            # fflog(f"exception during search: {e}")
            return []

    def sources(self, url: Optional[list[SourceItem]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        return url

    def resolve(self, url: str) -> Optional[str]:
        # fflog(f"resolving url: {url}")
        if url.startswith('DRMFF|'):
            try:
                _, eid, tenant = url.split('|')

                play_url = self.api_url + f'products/{eid}/videos/playlist'
                params = {
                    'videoType': 'MOVIE',
                    'platform': self.platform,
                    'tenant': tenant
                }

                response = requests.get(play_url, headers=self._make_headers(), params=params)
                if response.status_code != 200:
                    fflog(f"resolve: request failed, status {response.status_code}")
                    return None

                data = response.json()

                if 'sources' in data and 'DASH' in data['sources'] and data['sources']['DASH']:
                    stream_url = data['sources']['DASH'][0]['src']
                    if stream_url.startswith('//'):
                        stream_url = 'https:' + stream_url

                    widevine = (data.get('drm') or {}).get('WIDEVINE')
                    return build_drmff(
                        stream_url,
                        widevine_url=widevine['src'] if widevine else None,
                        lic_referer=self.base_url,
                    )
                else:
                    # fflog("no DASH source found in response")
                    return None
            except Exception as e:
                # fflog(f"exception during resolve: {e}")
                return None
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _make_headers(self):
        h = {
            'User-Agent': DEFAULT_UA,
            'Referer': self.base_url,
            'Accept': 'application/json, text/plain, */*'
        }
        return h

    def _make_params(self):
        p = {
            'lang': 'POL',
            'platform': self.platform
        }
        return p
