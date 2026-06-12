# -*- coding: utf-8 -*-
"""
FanFilm ‑ źródło: vod.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re, json, random
from urllib.parse import urlencode, parse_qs
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
from typing_extensions import TypedDict

from lib.ff import requests, cleantitle
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc


class _VodSerial(TypedDict, total=False):
    title: str


class _VodSeason(TypedDict, total=False):
    number: int
    serial: _VodSerial


class _VodPlItem(TypedDict, total=False):
    """VoD API response item (movie VOD or EPISODE)."""
    id: str | int
    type: str
    title: str
    year: str
    episode: int
    season: _VodSeason
    images: dict[str, str]

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem  # assigned dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_url = 'https://player.pl/'
        self.api_url = self.base_url + 'playerapi/'
        self.platform = 'ANDROID_TV'
        self.UA = 'playerTV/2.2.2 (455) (Linux; Android 8.0.0; Build/sdk_google_atv_x86) net/sdk_google_atv_x86userdebug 8.0.0 OSR1.180418.025 6695156 testkeys'
        self.device_uid = self._code_gen(16)
        self.uid = self._code_gen(32)

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> list[_VodPlItem]:
        return self._search(title, localtitle, year, aliases=aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> tuple[tuple[str, str, list[SourceTitleAlias]], str]:
        return (tvshowtitle, localtvshowtitle, aliases), year

    def episode(self, url: tuple[tuple[str, str, list[SourceTitleAlias]], str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str, episode_title: Optional[str] = None) -> list[_VodPlItem]:
        titles = self._extract_titles(url, title)
        if not titles:
            # fflog("no titles to search")
            return []

        series_id = self._find_series(titles)
        if not series_id:
            # fflog("no match")
            return []
        episode_title = self.ffitem.title
        # Pass episode title if known
        return self._get_episodes(series_id, int(season), int(episode), episode_title=episode_title)

    def sources(self, rows: list[_VodPlItem] | None, hostDict: Optional[List[str]] = None, hostprDict: Optional[List[str]] = None) -> 'List[SourceItem]':
        # fflog(f"sources called with {len(rows) if rows else 0} items")
        if not rows:
            return []
        sources = []
        for el in rows:
            try:
                item_type = el.get('type', 'VOD')
                info = ''
                if item_type == 'EPISODE':
                    season = el.get('season', {}).get('number', 0)
                    episode = el.get('episode', 0)
                    serial_title = el.get('season', {}).get('serial', {}).get('title', '')
                    title = el.get('title', '')
                    if title:
                        info += f" - {title}"
                else:
                    serial_title = ''
                sources.append({
                    'source': '',
                    'quality': self._get_quality(el),
                    'language': 'pl',
                    'url': f"DRMFF|{el.get('id')}|{item_type}",
                    'info': info,
                    'info2': serial_title if item_type == 'EPISODE' else '',
                    'direct': False,
                    'debridonly': False,
                    'image': self._get_image(el.get('images', {}))
                })
            except Exception:
                fflog_exc()
        fflog(f'sources: {len(sources)}')
        return sources

    def resolve(self, url: str) -> Optional[str]:
        # fflog(f"resolving url: {url}")
        if not url.startswith('DRMFF|'):
            return url
        try:
            _, item_id, item_type = url.split('|')
            item_data = requests.get(f"{self.api_url}product/vod/{item_id}",
                                     headers=self._headers(), cookies=self._cookies(),
                                     params={'4K': 'true', 'platform': self.platform}).json()
            tid = item_data['shareUrl'].replace(self.base_url, '').replace(',', '_').replace('-', '_').replace('/', '_')
            playlist_data = requests.get(f"{self.api_url}item/{item_id}/playlist",
                                         headers=self._headers(), cookies=self._cookies(),
                                         params={'type': 'MOVIE', 'page': tid, '4K': 'true', 'platform': self.platform, 'version': '3.1'}).json()
            if playlist_data.get('code'):
                return None
            video = playlist_data.get('movie', {}).get('video', {})
            if (sources := video.get('sources')) and (dash := sources.get('dash')):
                stream = dash['url']
                if (protections := video.get('protections')) and (widevine := protections.get('widevine')):
                    lic = widevine['src']
                    hea = {'User-Agent': self.UA, 'Referer': self.base_url, 'Content-Type': ''}
                    adaptive = {'protocol': 'mpd',
                                'mimetype': 'application/dash+xml',
                                'manifest': stream,
                                'licence_type': 'com.widevine.alpha',
                                'licence_url': lic,
                                'licence_header': urlencode(hea),
                                'post_data': 'R{SSM}',
                                'response_data': ''}
                    return f"DRMFF|{repr(adaptive)}"
                return stream
            elif (sources := video.get('sources')) and (hls := sources.get('hls')):
                return hls['url']
        except Exception:
            fflog_exc()
        return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _code_gen(self, length):
        base = '0123456789abcdef'
        return ''.join(random.choice(base) for _ in range(length))

    def _headers(self):
        correlation_id = f"androidTV_{self._code_gen(8)}-{self._code_gen(4)}-{self._code_gen(4)}-{self._code_gen(4)}-{self._code_gen(12)}"
        return {
            'User-Agent': self.UA,
            'accept-encoding': 'gzip',
            'api-correlationid': correlation_id,
            'api-deviceuid': self.device_uid,
            'api-deviceinfo': 'sdk_google_atv_x86;unknown;Android;8.0.0;Unknown;2.2.2 (455);',
        }

    def _cookies(self):
        return {'uid': self.uid}

    def _is_available(self, item):
        if item.get('payable', False):
            return False
        try:
            schedules = [s for s in item.get('displaySchedules', []) if s.get('active')]
            if schedules and schedules[0].get('type') == 'SOON':
                return False
        except Exception:
            fflog_exc()
        return True

    def _get_quality(self, item):
        if item.get('uhd'):
            return '4K'
        quality = 'SD'
        for typ in ['android_tv', 'pc', 'smart_tv', 'playstation', 'mobile', 'apple_tv']:
            imgs = item.get('images', {}).get(typ, [])
            if imgs and 'mainUrl' in imgs[0]:
                match = re.search(r'dstw=(\d+)&dsth=(\d+)', imgs[0]['mainUrl'])
                if match:
                    h = int(match.group(2))
                    if h >= 1080:
                        quality = '1080p'
                    elif h >= 720:
                        quality = '720p'
                    elif h >= 480:
                        quality = '480p'
                    elif h >= 360:
                        quality = '360p'
                    if quality != 'SD':
                        break
        return quality

    def _get_image(self, images):
        try:
            img = images['pc'][0]['mainUrl']
            if img.startswith('//'):
                img = 'https:' + img
            return img
        except (KeyError, IndexError, TypeError):
            return ''

    def _extract_titles(self, url, default_title):
        titles = []
        try:
            if isinstance(url, tuple) and len(url) == 2 and isinstance(url[0], tuple):
                t0, t1, aliases = (url[0]+(None,)*3)[:3]
                for t in [t0, t1]:
                    if t and t not in titles:
                        titles.append(t)
                if aliases:
                    for a in aliases:
                        if isinstance(a, dict) and 'title' in a and a['title'] not in titles:
                            titles.append(a['title'])
            elif isinstance(url, str):
                params = parse_qs(url)
                for k in ('titles', 'tvshowtitle', 'localtvshowtitle'):
                    val = params.get(k, [None])[0]
                    if val and val not in titles:
                        titles.append(val)
            elif isinstance(url, dict) and 'titles' in url:
                for t in url['titles']:
                    if t and t not in titles:
                        titles.append(t)
            if not titles and default_title:
                titles.append(default_title)
        except Exception as e:
            fflog_exc()
            # fflog(f"error extracting titles: {e}")

        # Deduplicate titles
        unique_titles = list({t.lower(): t for t in titles}.values())
        # fflog(f"extracted and deduplicated titles: {unique_titles}")
        return unique_titles

    def _find_series(self, titles, year=None):
        # fflog(f"_find_series called with titles: {titles}")
        cleaned_titles = [cleantitle.get(t) for t in titles]
        for t in titles:
            # fflog(f"searching series for title '{t}'")
            try:
                resp = requests.get(
                    self.api_url+'item/search',
                    headers=self._headers(),
                    cookies=self._cookies(),
                    params={'4K': 'true', 'platform': self.platform, 'keyword': t, 'episodes': 'false'}
                )
                # fflog(f"search response status {resp.status_code} for '{t}'")
                if resp.status_code != 200:
                    continue

                for item in resp.json():
                    el = item.get('element')
                    if not el:
                        continue
                    if el.get('type') != 'SERIAL' or not self._is_available(el):
                        continue

                    match_title = el.get('title')
                    match_year = el.get('year')
                    # fflog(f"candidate series: '{match_title}' ({match_year}), id {el.get('id')}")

                    clean_match_title = cleantitle.get(match_title)

                    if clean_match_title in cleaned_titles:
                        # Year check
                        if year and match_year:
                            try:
                                if abs(int(year)-int(match_year)) > 1:
                                    # fflog(f"skip (year mismatch): {year} vs {match_year}")
                                    continue
                            except (ValueError, TypeError):
                                pass

                        # fflog(f"matched series ID {el.get('id')} for title '{match_title}'")
                        return el.get('id')

            except Exception as e:
                fflog_exc()
                # fflog(f"exception during series search for '{t}': {e}")
        # fflog("no match")
        return None

    def _get_episodes(self, series_id, season, episode, episode_title=None):
        # fflog(f"_get_episodes called for series {series_id}, season {season}, episode {episode}, episode_title={episode_title!r}")
        try:
            resp = requests.get(
                f"{self.api_url}product/vod/serial/{series_id}/season/list",
                headers=self._headers(),
                cookies=self._cookies(),
                params={'4K': 'true', 'platform': self.platform}
            )
            if resp.status_code != 200:
                # fflog(f"failed to fetch season list, status {resp.status_code}")
                return []

            seasons = resp.json()
            # fflog(f"found seasons: {[s.get('number') for s in seasons]}")

            target = next((s for s in seasons if s.get('number') == season), None)
            if not target:
                # fflog(f"season {season} not found")
                return []

            season_id = target.get('id')
            resp = requests.get(
                f"{self.api_url}product/vod/serial/{series_id}/season/{season_id}/episode/list",
                headers=self._headers(),
                cookies=self._cookies(),
                params={'4K': 'true', 'platform': self.platform}
            )
            if resp.status_code != 200:
                # fflog(f"failed to fetch episodes list, status {resp.status_code}")
                return []

            episodes = resp.json()
            # fflog(f"episodes in season {season}: {[f'{ep.get("episode")}->{ep.get("title")}' for ep in episodes]}")

            if episode_title:
                # Try matching by episode title first
                results = [ep for ep in episodes if ep.get('title', '').lower(
                ) == episode_title.lower() and self._is_available(ep)]
                # fflog(f"matched episodes by title: {[ep.get('title') for ep in results]}")
            # If not found (or titles empty), fall back to episode number
            if not results:
                results = [ep for ep in episodes if ep.get('episode') == episode and self._is_available(ep)]
                # fflog(f"matched episodes by number: {[ep.get('title') for ep in results]}")

            return results
        except Exception:
            fflog_exc()
            # fflog(f"exception fetching episodes: {e}")
            return []

    def _search(self, title, localtitle, year='', episode='', premiered='', aliases=None):
        # fflog(f"search called with title='{title}', localtitle='{localtitle}'")

        search_titles = []
        if localtitle:
            search_titles.append(localtitle)
        if title and title not in search_titles:
            search_titles.append(title)

        # fflog(f"searching for titles: {search_titles}")

        cleaned_titles = [cleantitle.get(t) for t in search_titles]
        results = []

        for q in search_titles:
            try:
                fflog(f'query: {q!r}')
                resp = requests.get(self.api_url+'item/search', headers=self._headers(), cookies=self._cookies(),
                                    params={'4K': 'true', 'platform': self.platform, 'keyword': q, 'episodes': 'true'})
                if resp.status_code != 200:
                    continue

                raw_items = resp.json()
                fflog(f'search: {len(raw_items)} results')
                for item in raw_items:
                    el = item.get('element')
                    if not el or episode or el.get('type') != 'VOD' or not self._is_available(el):
                        continue

                    match_title = el.get('title')
                    if not match_title:
                        continue

                    clean_match_title = cleantitle.get(match_title)

                    if clean_match_title in cleaned_titles:
                        if year:
                            y = el.get('year')
                            if y:
                                try:
                                    if abs(int(year)-int(y)) > 1:
                                        continue
                                except (ValueError, TypeError):
                                    pass

                        if el not in results:
                            # fflog(f"match: '{match_title}'")
                            results.append(el)

            except Exception:
                fflog_exc()

        # fflog(f"search finished, found {len(results)} results")
        return results
