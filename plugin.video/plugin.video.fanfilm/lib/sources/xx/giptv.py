# -*- coding: utf-8 -*-
"""
FanFilm - źródło: GIPTV (Xtream Code)
Copyright (C) 2026 :)

Scraper wyciągający linki z wtyczki plugin.video.giptv
Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
from typing_extensions import TypedDict
from urllib.request import urlopen, Request
from urllib.parse import urlparse, urlunparse
from lib.ff import cleantitle, control, source_utils
from lib.sources import single_call
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.settings import settings
from lib.ff.item import FFItem


class _GiptvShowDict(TypedDict):
    tvshowtitle: str
    localtitle: str
    year: str
    tmdb: int | None
from const import const

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    has_sort_order: bool = True
    has_color_identify2: bool = True
    use_premium_color: bool = True
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl', 'en']

    def __init__(self):
        self.domains = ['giptv']
        self.available = True

    @single_call
    def init(self):
        """Lazy initialization - called on first use (movie/episode).
        Avoids heavy work (xbmcaddon, credential reading) when scraper is disabled."""

        if not control.condVisibility('System.AddonIsEnabled(plugin.video.giptv)'):
            self.available = False
            return False

        try:
            import xbmcaddon
            import xbmcvfs

            giptv_addon = xbmcaddon.Addon('plugin.video.giptv')
            self.icon = giptv_addon.getAddonInfo('icon')

            # Read credentials based on selected account
            account = giptv_addon.getSetting('account') or '0'
            if account == '1':
                server_key, user_key, pass_key = 'server1', 'username1', 'password1'
            elif account == '2':
                server_key, user_key, pass_key = 'server2', 'username2', 'password2'
            else:
                server_key, user_key, pass_key = 'server', 'username', 'password'

            server = giptv_addon.getSetting(server_key).strip()
            self.username = giptv_addon.getSetting(user_key).strip()
            self.password = giptv_addon.getSetting(pass_key).strip()

            if not all([server, self.username, self.password]):
                self.available = False
                fflog('login: no credentials')
                return False

            # Clean server URL (remove embedded credentials if any)
            parsed = urlparse(server)
            netloc = parsed.hostname or ''
            if parsed.port:
                netloc += f":{parsed.port}"
            self.server = urlunparse((parsed.scheme, netloc, '', '', '', '')).rstrip('/')

            # Index paths (built by giptv service)
            index_dir = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.giptv/index')
            self._vod_index_path = os.path.join(index_dir, f'{self.username}_vod_index.json')
            self._series_index_path = os.path.join(index_dir, f'{self.username}_series_index.json')

            fflog(f'initialized ({self.server})')
            return True

        except Exception as e:
            self.available = False
            fflog(f'init failed: {e}')
            fflog_exc()
            return False

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[List[Tuple]]:
        if not self.init():
            return None
        tmdb = self.ffitem.tmdb_id or ''
        try:
            fflog(f'searching for movie "{title}" ({year}) (TMDB ID: {tmdb})')

            vod_entries = self._get_vod_entries()
            if not vod_entries:
                fflog('no VOD entries available')
                return None

            fflog(f'searching through {len(vod_entries)} VOD entries')

            tmdb_id = str(tmdb) if tmdb else None
            tmdb_results = []
            if tmdb_id:
                fflog(f'attempting TMDB ID search for "{tmdb_id}"')
                for entry in vod_entries:
                    entry_tmdb = str(entry.get('tmdb')) if entry.get('tmdb') else None
                    if entry_tmdb == tmdb_id:
                        stream_id = entry.get('id', '')
                        ext = entry.get('ext', 'mp4')
                        fflog(f"VOD match by TMDB ID: {entry.get('title')} (id={stream_id})")
                        tmdb_results.append((stream_id, entry.get('title'), ext))
                if tmdb_results:
                    fflog(f'found {len(tmdb_results)} VOD matches by TMDB ID')
                    return tmdb_results
                else:
                    fflog(f'no VOD match by TMDB ID: {tmdb_id}, falling back to title search')

            search_titles = []
            if localtitle:
                search_titles.append(cleantitle.get_simple(re.sub(r'^(the|a|an)\s+', '', localtitle, flags=re.I)))
            if title and title != localtitle:
                search_titles.append(cleantitle.get_simple(re.sub(r'^(the|a|an)\s+', '', title, flags=re.I)))
            if not search_titles:
                search_titles = [cleantitle.get_simple(re.sub(r'^(the|a|an)\s+', '', title, flags=re.I))]

            results = []
            for entry in vod_entries:
                entry_title = entry.get('title', '')
                if not entry_title:
                    continue

                clean_title = self._clean_item_title(entry_title)
                entry_clean = cleantitle.get_simple(clean_title)
                if not entry_clean:
                    continue

                title_match = any(st == entry_clean for st in search_titles)

                if title_match and self._check_year_match(self._extract_year(entry_title), year):
                    stream_id = entry.get('id', '')
                    ext = entry.get('ext', 'mp4')
                    fflog(f'VOD match by title: {entry_title} (id={stream_id})')
                    results.append((stream_id, entry_title, ext))

            if results:
                fflog(f'search: {len(results)} results')
                return results
            else:
                fflog('search: 0 results')
                return None

        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> _GiptvShowDict:
        tmdb = self.ffitem.show_item.tmdb_id
        return {
            'tvshowtitle': tvshowtitle,
            'localtitle': localtitle,
            'year': year,
            'tmdb': tmdb,
        }

    def episode(self, url: _GiptvShowDict | None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[List[Tuple]]:
        if not self.init():
            return None
        tmdb = self.ffitem.show_item.tmdb_id

        try:
            if not url or not isinstance(url, dict):
                return None

            tvshowtitle = url.get('tvshowtitle')
            localtvshowtitle = url.get('localtitle')

            fflog(f'searching for S{int(season):02d}E{int(episode):02d} "{tvshowtitle}" (TMDB ID: {tmdb})')

            series_entries = self._get_series_entries()
            if not series_entries:
                fflog('no series entries available')
                return None

            target_series = []
            tmdb_id = str(tmdb) if tmdb else None
            if tmdb_id:
                fflog(f'attempting TMDB ID search for series "{tmdb_id}"')
                for entry in series_entries:
                    entry_tmdb = str(entry.get('tmdb')) if entry.get('tmdb') else None
                    if entry_tmdb == tmdb_id:
                        target_series.append((entry.get('id'), entry.get('title')))
                        fflog(f"series match by TMDB ID: {entry.get('title')} (id={entry.get('id')})")
                if not target_series:
                    fflog(f'no series match by TMDB ID: {tmdb_id}, falling back to title search')

            if not target_series:  # If no TMDB match or TMDB not provided, do title search
                search_titles = []
                if localtvshowtitle:
                    search_titles.append(cleantitle.get_simple(
                        re.sub(r'^(the|a|an)\s+', '', localtvshowtitle, flags=re.I)))
                if tvshowtitle and tvshowtitle != localtvshowtitle:
                    search_titles.append(cleantitle.get_simple(re.sub(r'^(the|a|an)\s+', '', tvshowtitle, flags=re.I)))
                if not search_titles:
                    search_titles = [cleantitle.get_simple(
                        re.sub(r'^(the|a|an)\s+', '', tvshowtitle or '', flags=re.I))]

                for entry in series_entries:
                    entry_title = entry.get('title', '')
                    if not entry_title:
                        continue

                    clean_title = self._clean_item_title(entry_title)
                    entry_clean = cleantitle.get_simple(clean_title)
                    if not entry_clean:
                        continue

                    if any(st == entry_clean for st in search_titles):
                        series_id = entry.get('id', '')
                        fflog(f'series match by title: {entry_title} (id={series_id})')
                        target_series.append((series_id, entry_title))

            if not target_series:
                fflog('no series found')
                return None

            fflog(f'found {len(target_series)} series, checking episodes')

            # Check each series for the requested episode
            results = []
            for series_id, serie_title in target_series:
                try:
                    info = self._api_call('get_series_info', series_id=series_id)
                    if not info:
                        continue

                    episodes_data = info.get('episodes', {})
                    season_key = str(int(season))
                    if season_key not in episodes_data:
                        continue

                    for ep in episodes_data[season_key]:
                        if int(ep.get('episode_num', 0)) == int(episode):
                            ep_id = str(ep.get('id', ''))
                            ext = ep.get('container_extension', 'mp4')
                            fflog(f'found S{int(season):02d}E{int(episode):02d} in "{serie_title}" (ep_id={ep_id})')
                            results.append((ep_id, serie_title, int(season), int(episode), ext))

                except Exception as e:
                    fflog(f'error checking series "{serie_title}": {e}')
                    continue

            if results:
                fflog(f'found {len(results)} episode(s)')
                return results
            else:
                fflog('no episodes found')
                return None

        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[List[Tuple]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        if not self.available:
            return sources

        try:
            if not url:
                return sources

            items_to_process = []
            if isinstance(url, list):
                items_to_process = url
            elif isinstance(url, tuple):
                items_to_process = [url]
            else:
                return sources

            for item in items_to_process:
                try:
                    if len(item) == 5:
                        # Episode: (ep_id, serie_title, season, episode, ext)
                        stream_id, video_title, season_no, episode_no, ext = item
                        stream_type = 'series'
                        filename = f"{video_title} S{int(season_no):02d}E{int(episode_no):02d}"
                    elif len(item) == 3:
                        # Movie: (stream_id, title, ext)
                        stream_id, video_title, ext = item
                        stream_type = 'vod'
                        filename = video_title
                    else:
                        continue

                    stream_url = self._build_stream_url(stream_id, stream_type, ext)

                    quality = '1080p'

                    detected_quality = source_utils.get_quality(video_title, filename)

                    if detected_quality == 'SD':
                        quality = '1080p'
                    else:
                        quality = detected_quality

                    language, info_lang = source_utils.get_lang_by_type(filename)

                    if not language:
                        # If source_utils.get_lang_by_type didn't identify a primary language,
                        # try to extract from explicit prefixes (e.g., "EN -", "DE -")
                        lang_prefix_match = re.match(r'^(?:[A-Z0-9]{1,4}[-_\s]*)*?([A-Z]{2,3})\s*[-:]', filename, re.I)
                        if lang_prefix_match:
                            lang_prefix = lang_prefix_match.group(1).upper()
                            lang_map = {
                                'PL': 'pl', 'EN': 'en', 'UK': 'en',
                            }
                            language = lang_map.get(lang_prefix, '')

                    if not language:
                        # Third attempt: check for (XX) country code after year in filename
                        country_code_match = re.search(r'\(\d{4}\)\s+\(([A-Z]{2})\)', filename, re.I)
                        if country_code_match:
                            country_code = country_code_match.group(1).upper()
                            if country_code in ['US', 'EN', 'UK']:
                                language = 'en'
                            elif country_code == 'PL':
                                language = 'pl'

                    if not language:
                        language = 'en'

                    info_tech = source_utils.getFileType(filename)
                    info = f"{info_lang} {info_tech}".strip()

                    sources.append({
                        'source': ext.upper() if ext else 'giptv',
                        'quality': quality,
                        'language': language,
                        'url': stream_url,
                        'info': info,
                        'filename': filename,
                        'direct': True,
                        'debridonly': False,
                        'icon': self.icon,
                        'premium': True,
                    })
                except Exception:
                    fflog_exc()
                    continue

        except Exception:
            fflog_exc()

        fflog(f'sources: {len(sources)}')
        return sources

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _api_call(self, action, **params):
        """Call Xtream Code API and return JSON."""
        url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action={action}"
        for k, v in params.items():
            url += f"&{k}={v}"
        try:
            req = Request(url)
            req.add_header('User-Agent', source_utils.DEFAULT_UA)
            with urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            fflog(f'API error ({action}): {e}')
            return None

    @staticmethod
    def _load_index(index_path, max_age_hours=0):
        """Load search index JSON file. Returns None if missing, empty or stale."""
        try:
            if not os.path.exists(index_path) or os.path.getsize(index_path) <= 16:
                return None
            if max_age_hours > 0:
                age_hours = (time.time() - os.path.getmtime(index_path)) / 3600
                if age_hours > max_age_hours:
                    fflog(f'index stale ({age_hours:.0f}h > {max_age_hours}h): {os.path.basename(index_path)}')
                    return None
            with open(index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            fflog(f'error loading index: {e}')
        return None

    def _build_stream_url(self, stream_id, stream_type, ext='mp4'):
        """Construct Xtream stream URL."""
        type_map = {'vod': 'movie', 'series': 'series'}
        api_path = type_map.get(stream_type, stream_type)
        return f"{self.server}/{api_path}/{self.username}/{self.password}/{stream_id}.{ext}"

    @staticmethod
    def _clean_item_title(title):
        """Clean title from language prefixes (PL -, EN 4K -, etc.), quality suffixes and articles."""
        main = re.split(r'[\(\[]', title)[0].strip()
        main = re.sub(r'^[A-Z]{2,3}(?:\s+(?:4K|UHD|HD|1080p|720p))?\s*[-:]\s*', '', main, flags=re.I)
        main = re.sub(r'\s+(4K|1080p|720p|HD|UHD)\s*$', '', main, flags=re.I).strip()
        main = re.sub(r'^(the|a|an)\s+', '', main, flags=re.I)
        return main

    @staticmethod
    def _extract_year(title):
        """Extract year from title like 'Batman (2022)' or 'Batman 2022'."""
        m = re.search(r'\((\d{4})\)', title)
        if m:
            return int(m.group(1))
        m = re.search(r'\b((?:19|20)\d{2})\b', title)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _check_year_match(entry_year, search_year):
        """Check year match with ±1 tolerance. False if entry has no year."""
        if not search_year:
            return True
        if not entry_year:
            return False
        try:
            return abs(int(entry_year) - int(search_year)) <= 1
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _save_index(index_path, data):
        """Save index to JSON file for future use."""
        try:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            fflog(f'saved index ({len(data)} entries) to {index_path}')
        except Exception as e:
            fflog(f'error saving index: {e}')

    def _get_vod_entries(self):
        """Get VOD entries from index (if fresh) or API fallback."""
        vod_index = self._load_index(self._vod_index_path, max_age_hours=const.sources.giptv.index_max_age_h)
        if vod_index:
            fflog(f'VOD index loaded ({len(vod_index)} entries)')
            return vod_index

        fflog('VOD index missing or stale, fetching from API')
        vod_data = self._api_call('get_vod_streams')
        if not vod_data or not isinstance(vod_data, list):
            return []

        entries = []
        for m in vod_data:
            if not m.get('stream_id'):
                continue
            tmdb_val = m.get('tmdb') or m.get('tmdb_id')
            entries.append({
                'id': str(m['stream_id']),
                'title': m.get('name') or m.get('title') or '',
                'tmdb': str(tmdb_val) if tmdb_val else None,
                'ext': m.get('container_extension', 'mp4'),
            })
        if entries:
            self._save_index(self._vod_index_path, entries)
            fflog(f'VOD fetched: {len(entries)} entries')
        return entries

    def _get_series_entries(self):
        """Get series entries from index (if fresh) or API fallback."""
        series_index = self._load_index(self._series_index_path, max_age_hours=const.sources.giptv.index_max_age_h)
        if series_index:
            fflog(f'series index loaded ({len(series_index)} entries)')
            return series_index

        fflog('series index missing or stale, fetching from API')
        series_data = self._api_call('get_series')
        if not series_data or not isinstance(series_data, list):
            return []

        entries = []
        for s in series_data:
            if not s.get('series_id'):
                continue
            tmdb_val = s.get('tmdb') or s.get('tmdb_id')
            entries.append({
                'id': str(s['series_id']),
                'title': s.get('name') or '',
                'tmdb': str(tmdb_val) if tmdb_val else None,
            })
        if entries:
            self._save_index(self._series_index_path, entries)
            fflog(f'series fetched: {len(entries)} entries')
        return entries
