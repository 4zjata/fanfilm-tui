# -*- coding: utf-8 -*-
"""
FanFilm - źródło: vod.tvp.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
import json, random, re
from datetime import date as dt_date, timedelta
from urllib.parse import urlencode

from lib.ff import requests, cleantitle, source_utils
from lib.ff.item import FFItem
from lib.ff.source_utils import DEFAULT_UA, ShowData, show_data_asdict, ShowDataDict
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem  # assigned dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.show_payable = False
        self.base_url = 'https://vod.tvp.pl'
        self.api_url = self.base_url + '/api/'
        self.search_url = 'products/vods/search/VOD'
        self.tvshow_search_url = 'products/vods/search/SERIAL'
        self.platform = 'BROWSER'
        self.base_headers = {
            'User-Agent': DEFAULT_UA,
            'Referer': self.base_url,
            'X-Redge-VOD': 'true',
            'API-DeviceInfo': 'HbbTV;2.0.1 (ETSI 1.4.1);Chrome +DRM Samsung;Chrome +DRM Samsung;HbbTV;2.0.3',
            'Accept': 'application/json, text/plain, */*'
        }
        self.session = requests.Session()
        self.UA = DEFAULT_UA

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> list[SourceItem]:
        try:
            # fflog(f"searching for movie '{localtitle}' ({year})")
            results = []
            search_results = self._search(cleantitle.geturl(localtitle), 'movie')
            for item in search_results:
                if item.get('type') != 'VOD' or not item.get('id'):
                    continue
                normalized_item_title = cleantitle.get_simple(item.get('title', ''))
                search_clean = cleantitle.get_simple(localtitle)

                if search_clean == normalized_item_title and abs(item.get('year', 0) - int(year)) <= 1:
                    tenant_uid = item.get('tenant', {}).get('uid', '') if isinstance(item.get('tenant'), dict) else ''
                    is_audiodescription = 'audiodeskrypcja' in item.get('title', '').lower()
                    info = 'Lektor' + (' - Audiodescription' if is_audiodescription else '')
                    results.append({
                        'source': '',
                        'quality': '1080p',
                        'language': 'pl',
                        'url': f"DRMFF|{item['id']}|{tenant_uid}|MOVIE",
                        'info': info,
                        'info2': '',
                        'filename': item.get('slug') or item.get('title') or '',
                        'direct': False,
                        'debridonly': False
                    })
            fflog(f'sources: {len(results)}')
            return results
        except Exception:
            fflog_exc()
            return []

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, int(year)))

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> list[SourceItem]:
        try:
            data = ShowData(**url)
            localtvshowtitle = data.local_title
            tvshowtitle = data.title
            year = str(data.year)
            aliases = data.aliases
            sources = []

            # fflog(f"searching for show '{localtvshowtitle}' ({year})")
            search_results = self._search(cleantitle.geturl(localtvshowtitle), 'tvshow')
            if not search_results:
                # fflog("no search results found for tvshow")
                return []

            matched_show = None
            for item in search_results:
                if item.get('type') != 'SERIAL':
                    continue

                # Exact title matching
                normalized_item_title = cleantitle.normalize(item.get('title', ''))
                search_clean = cleantitle.normalize(localtvshowtitle)

                # For serials compare titles only
                title_match = normalized_item_title == search_clean

                if title_match:
                    matched_show = item
                    # fflog(f"matched show '{item.get('title')}' (ID: {item.get('id')})")
                    break

            if not matched_show:
                # fflog(f"no match for '{localtvshowtitle}'")
                return []

            show_id = matched_show['id']
            seasons_data = self.session.get(f'{self.api_url}products/vods/serials/{show_id}/seasons',
                                          headers=self._make_headers(), params=self._make_params()).json()
            found_episode = None
            found_tvp_season = None
            episode_int = int(episode)
            season_int = int(season)

            # Detect TVP season structure: range-titled seasons (e.g. "2201–2300") mean TVP uses
            # global episode numbering and TMDB counts are likely incomplete → skip absolute matching.
            has_range_seasons = any(self._parse_season_range(s.get('title', '')) for s in seasons_data)
            absolute_episode_number = self.ffitem.absolute_episode_number()
            fflog(f"looking for s{season_int}e{episode_int}, show_id={show_id}, seasons={len(seasons_data)}, range_seasons={has_range_seasons}, absolute={absolute_episode_number}")

            # Strategy 1: extract TVP episode number from TMDB episode title (e.g. "Odcinek 2276").
            # Highest confidence, no API calls — runs first for range_seasons shows.
            if not found_episode and has_range_seasons:
                ep_title = (self.ffitem.title or '') if self.ffitem else ''
                fflog(f"episode title: '{ep_title}'")
                m = re.search(r'\b(\d{3,5})\b', ep_title)
                if m:
                    title_ep_number = int(m.group(1))
                    fflog(f"extracted ep number {title_ep_number} from title '{ep_title}'")
                    for s in sorted(seasons_data, key=lambda s: s.get('number', 0)):
                        season_range = self._parse_season_range(s.get('title', ''))
                        if season_range:
                            ep_min, ep_max = season_range
                            if title_ep_number < ep_min:
                                break
                            if title_ep_number > ep_max:
                                continue
                        eps = self.session.get(f"{self.api_url}products/vods/serials/{show_id}/seasons/{s['id']}/episodes",
                                               headers=self._make_headers(), params=self._make_params()).json()
                        matched = next((e for e in eps if e.get('number') == title_ep_number), None)
                        if matched:
                            found_episode = matched
                            found_tvp_season = s.get('number')
                            fflog(f"matched by title number {title_ep_number}: '{found_episode.get('title')}' id={found_episode.get('id')}")
                            break

            # Strategy 2: match by absolute/TVP episode number across all seasons.
            # Uses season range titles to skip unnecessary API calls.
            # If absolute_episode_number is unavailable but TVP uses global numbering (range_seasons),
            # episode_int itself may be the TVP number (e.g. "Na dobre i na złe" s20e712).
            tvp_number = absolute_episode_number or (episode_int if has_range_seasons else None)
            if not found_episode and tvp_number:
                for s in sorted(seasons_data, key=lambda s: s.get('number', 0)):
                    season_range = self._parse_season_range(s.get('title', ''))
                    if season_range:
                        ep_min, ep_max = season_range
                        if tvp_number < ep_min:
                            break
                        if tvp_number > ep_max:
                            continue
                    eps = self.session.get(f"{self.api_url}products/vods/serials/{show_id}/seasons/{s['id']}/episodes",
                                           headers=self._make_headers(), params=self._make_params()).json()
                    eps.sort(key=lambda e: e.get('number', 0))
                    if not eps:
                        continue
                    if not season_range:
                        if eps[-1].get('number', 0) < tvp_number:
                            continue
                        if eps[0].get('number', 0) > tvp_number:
                            break
                    matched = next((e for e in eps if e.get('number') == tvp_number), None)
                    if matched:
                        found_episode = matched
                        found_tvp_season = s.get('number')
                        fflog(f"matched by absolute #{tvp_number} in TVP season {found_tvp_season}: '{found_episode.get('title')}' number={found_episode.get('number')} id={found_episode.get('id')}")
                        break

            # Strategy 3: anchor search — find TVP episode nearest to TMDB season premiere,
            # then offset by episode_int-1. For range_seasons shows with incomplete TMDB counts.
            # TVP 'since' ≠ TV air date, so ±14-day tolerance is applied.
            if not found_episode and has_range_seasons:
                show_item = self.ffitem.show_item
                season_start = None
                if show_item:
                    sz = show_item.get_season_item(season_int)
                    season_start = sz.date if sz else None
                fflog(f"anchor search: TMDB s{season_int} premiere={season_start}")
                if season_start:
                    anchor_ep = None
                    anchor_delta = None
                    anchor_tvp_season = None
                    search_margin = timedelta(days=14)
                    for s in sorted(seasons_data, key=lambda s: s.get('number', 0)):
                        eps = self.session.get(f"{self.api_url}products/vods/serials/{show_id}/seasons/{s['id']}/episodes",
                                               headers=self._make_headers(), params=self._make_params()).json()
                        eps.sort(key=lambda e: e.get('since', '') or '')
                        if not eps:
                            continue
                        try:
                            last_since = dt_date.fromisoformat((eps[-1].get('since') or '')[:10])
                            first_since = dt_date.fromisoformat((eps[0].get('since') or '')[:10])
                        except ValueError:
                            continue
                        if last_since < season_start - search_margin:
                            continue
                        if first_since > season_start + search_margin:
                            break
                        for e in eps:
                            since_str = (e.get('since') or '')[:10]
                            if not since_str:
                                continue
                            try:
                                ep_date = dt_date.fromisoformat(since_str)
                            except ValueError:
                                continue
                            delta = abs((ep_date - season_start).days)
                            if anchor_delta is None or delta < anchor_delta:
                                anchor_ep = e
                                anchor_delta = delta
                                anchor_tvp_season = s.get('number')
                    if anchor_ep:
                        anchor_number = anchor_ep.get('number', 0)
                        target_number = anchor_number + (episode_int - 1)
                        fflog(f"anchor: TVP ep {anchor_number} (delta {anchor_delta}d, TVP season {anchor_tvp_season}), target ep: {target_number}")
                        for s in sorted(seasons_data, key=lambda s: s.get('number', 0)):
                            season_range = self._parse_season_range(s.get('title', ''))
                            if season_range:
                                ep_min, ep_max = season_range
                                if target_number < ep_min:
                                    break
                                if target_number > ep_max:
                                    continue
                            eps = self.session.get(f"{self.api_url}products/vods/serials/{show_id}/seasons/{s['id']}/episodes",
                                                   headers=self._make_headers(), params=self._make_params()).json()
                            matched = next((e for e in eps if e.get('number') == target_number), None)
                            if matched:
                                found_episode = matched
                                found_tvp_season = s.get('number')
                                fflog(f"matched by anchor+offset: '{found_episode.get('title')}' number={found_episode.get('number')} id={found_episode.get('id')}")
                                break
                        if not found_episode:
                            fflog(f"anchor+offset: ep {target_number} not found in TVP")
                    else:
                        fflog(f"no TVP episode found near {season_start} (±{search_margin.days}d)")
                else:
                    fflog("no TMDB season start date, skipping anchor search")

            # Strategy 4: index within matching TVP season number (last resort)
            if not found_episode:
                fflog(f"trying index {episode_int - 1} in TVP season {season_int}")
                target_season = next((s for s in seasons_data if s.get('number') == season_int), None)
                if target_season:
                    eps = self.session.get(f'{self.api_url}products/vods/serials/{show_id}/seasons/{target_season["id"]}/episodes',
                                           headers=self._make_headers(), params=self._make_params()).json()
                    eps.sort(key=lambda e: e.get('number', 0))
                    ep_first = eps[0].get('number') if eps else 'none'
                    ep_last = eps[-1].get('number') if eps else 'none'
                    fflog(f"TVP season {season_int}: {len(eps)} episodes, numbers {ep_first}..{ep_last}")
                    target_index = episode_int - 1
                    if 0 <= target_index < len(eps):
                        found_episode = eps[target_index]
                        found_tvp_season = season_int
                        fflog(f"matched by index {target_index}: '{found_episode.get('title')}' number={found_episode.get('number')} id={found_episode.get('id')}")
                    else:
                        fflog(f"index {target_index} out of range (season has {len(eps)} episodes)")
                else:
                    fflog(f"TVP season {season_int} not found (available: {sorted(s.get('number') for s in seasons_data)})")

            if not found_episode:
                # fflog("no match")
                return []

            if found_episode.get('payable') and not self.show_payable:
                return []

            tenant_uid = found_episode.get('tenant', {}).get(
                'uid', '') if isinstance(found_episode.get('tenant'), dict) else ''
            is_audiodescription = 'audiodeskrypcja' in found_episode.get('title', '').lower()
            info = 'Lektor' + (' - Audiodescription' if is_audiodescription else '')
            web_url = found_episode.get('webUrl', '')
            tvp_slug = found_episode.get('slug') or web_url.rsplit('/', 1)[-1]
            ep_title = found_episode.get('title') or ''
            sources.append({
                'source': '',
                'quality': '1080p',
                'language': 'pl',
                'url': f"DRMFF|{found_episode['id']}|{tenant_uid}|EPISODE",
                'info': info,
                'info2': '',
                'filename': ep_title or tvp_slug,
                'direct': False,
                'debridonly': False
            })
            # fflog(f"returning {len(sources)} episode sources")
            return sources
        except Exception:
            fflog_exc()
            return []

    def sources(self, url: list[SourceItem], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        return url

    def resolve(self, url: str) -> Optional[str]:
        try:
            if not url.startswith('DRMFF|'):
                return url
            parts = url.split('|')
            _, product_id, tenant_uid, video_type = parts
            api_video_type = 'MOVIE' if video_type == 'EPISODE' else video_type
            playlist = self._get_video_playlist(product_id, api_video_type, tenant_uid)
            if not playlist or not isinstance(playlist, dict):
                return None

            if playlist.get('code') == 'ITEM_NOT_PAID':
                return None

            protocol = None
            stream_url = None
            if sources := playlist.get('sources'):
                has_drm = (drm := playlist.get('drm')) and (widevine := drm.get('WIDEVINE')) and widevine.get('src')
                if has_drm and (dash := sources.get('DASH')):
                    stream_url = dash[0]['src']
                    protocol = 'mpd'
                elif hls := sources.get('HLS'):
                    stream_url = hls[0]['src']
                    protocol = 'hls'
                elif dash := sources.get('DASH'):
                    stream_url = dash[0]['src']
                    protocol = 'mpd'

            if not stream_url:
                return None
            if stream_url.startswith('//'):
                stream_url = 'https:' + stream_url

            headers_str = urlencode({'User-Agent': self.UA, 'Referer': self.base_url})
            adaptive_data = {
                'protocol': protocol or 'mpd',
                'mimetype': 'application/dash+xml' if protocol == 'mpd' else 'application/x-mpegURL',
                'manifest': stream_url,
                'licence_type': playlist.get('drm', {}).get('WIDEVINE', {}).get('type', '') if 'drm' in playlist else '',
                'licence_url': playlist.get('drm', {}).get('WIDEVINE', {}).get('src', '') if 'drm' in playlist else '',
                'licence_header': '',
                'post_data': '',
                'response_data': '',
                'content_lookup': False,
                'is_playable': True,
                'stream_headers': headers_str,
                'manifest_headers': headers_str
            }
            return f"DRMFF|{repr(adaptive_data)}"
        except Exception:
            fflog_exc()
            return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _make_headers(self):
        try:
            headers = self.base_headers.copy()
            headers['API-CorrelationID'] = 'smarttv_' + self._code_gen(32)
            return headers
        except Exception:
            fflog_exc()
            return self.base_headers

    def _make_params(self):
        return {'lang': 'PL', 'platform': self.platform}

    def _code_gen(self, length):
        return ''.join(random.choice('0123456789abcdef') for _ in range(length))

    def _search(self, query, item_type):
        try:
            params = self._make_params()
            params['keyword'] = cleantitle.geturl(query)
            endpoint = self.search_url if item_type == 'movie' else self.tvshow_search_url if item_type == 'tvshow' else None
            if not endpoint:
                # fflog(f"invalid item_type: {item_type}")
                return []

            full_url = f'{self.api_url}{endpoint}'
            fflog(f'query: {params["keyword"]!r}')

            response = self.session.get(full_url, headers=self._make_headers(), params=params)

            if response.status_code != 200:
                return []
            data = response.json()
            items = data.get('items', [])
            fflog(f'search: {len(items)} results')
            if not self.show_payable:
                items = [i for i in items if not i.get('payable')]
            # fflog(f"found {len(items)} items")
            return items
        except Exception:
            fflog_exc()
            return []

    def _get_video_playlist(self, product_id, video_type, tenant_uid=None):
        try:
            params = {'videoType': video_type, 'platform': self.platform}
            if tenant_uid:
                params['tenant'] = tenant_uid

            url = f'{self.api_url}products/{product_id}/videos/playlist'
            response = self.session.get(url, headers=self._make_headers(), params=params)
            response.raise_for_status()
            return response.json()
        except Exception:
            fflog_exc()
            return None

    def _parse_season_range(self, title: str) -> Optional[Tuple[int, int]]:
        """Parse TVP season title like '2801–2900' into (min, max) episode number range."""
        m = re.search(r'(\d+)\s*[–-]\s*(\d+)', title or '')
        if m:
            return int(m.group(1)), int(m.group(2))
        return None
