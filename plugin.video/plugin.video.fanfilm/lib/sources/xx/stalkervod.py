# -*- coding: utf-8 -*-
"""
FanFilm - źródło: StalkerVOD
Copyright (C) 2026 :)

Scraper extracting links from the plugin.video.stalkervod plugin.
Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import sys
import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
from typing_extensions import TypedDict
from lib.ff import cleantitle, control, source_utils
from lib.sources import single_call
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc


class _StalkerShowDict(TypedDict):
    tvshowtitle: str
    localtitle: str
    aliases: list[SourceTitleAlias]
    year: str

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

# (video_id, cmd, title)
_MovieItem = Tuple[str, str, str]
# (season_id, cmd, title, season_num, episode_num)
_EpisodeItem = Tuple[str, str, str, int, int]


class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl', 'en']

    has_sort_order: bool = True
    has_color_identify2: bool = True
    use_premium_color: bool = True

    def __init__(self):
        self.domains = ['stalkervod']
        self.available = True

    @single_call
    def init(self):
        """Lazy initialization - called on first use (movie/episode)."""

        if not control.condVisibility('System.AddonIsEnabled(plugin.video.stalkervod)'):
            self.available = False
            return False

        try:
            import xbmcaddon
            import xbmcvfs
            import os
            import importlib.util
            from urllib.parse import urlsplit

            stalker_addon = xbmcaddon.Addon('plugin.video.stalkervod')
            self.icon = stalker_addon.getAddonInfo('icon')

            stalker_path = stalker_addon.getAddonInfo('path')
            stalker_lib = os.path.join(stalker_path, 'lib')

            init_file = os.path.join(stalker_lib, '__init__.py')
            spec = importlib.util.spec_from_file_location('stalkervod_lib', init_file)
            stalkervod_package = importlib.util.module_from_spec(spec)
            sys.modules['stalkervod_lib'] = stalkervod_package

            spec_api = importlib.util.spec_from_file_location('stalkervod_lib.api', os.path.join(stalker_lib, 'api.py'))
            api_module = importlib.util.module_from_spec(spec_api)
            sys.modules['stalkervod_lib.api'] = api_module

            spec_globals = importlib.util.spec_from_file_location(
                'stalkervod_lib.globals', os.path.join(stalker_lib, 'globals.py'))
            globals_module = importlib.util.module_from_spec(spec_globals)
            sys.modules['stalkervod_lib.globals'] = globals_module

            spec_auth = importlib.util.spec_from_file_location(
                'stalkervod_lib.auth', os.path.join(stalker_lib, 'auth.py'))
            auth_module = importlib.util.module_from_spec(spec_auth)
            sys.modules['stalkervod_lib.auth'] = auth_module

            spec_loggers = importlib.util.spec_from_file_location(
                'stalkervod_lib.loggers', os.path.join(stalker_lib, 'loggers.py'))
            loggers_module = importlib.util.module_from_spec(spec_loggers)
            sys.modules['stalkervod_lib.loggers'] = loggers_module

            spec_utils = importlib.util.spec_from_file_location(
                'stalkervod_lib.utils', os.path.join(stalker_lib, 'utils.py'))
            utils_module = importlib.util.module_from_spec(spec_utils)
            sys.modules['stalkervod_lib.utils'] = utils_module

            spec.loader.exec_module(stalkervod_package)
            spec_loggers.loader.exec_module(loggers_module)
            spec_utils.loader.exec_module(utils_module)
            spec_globals.loader.exec_module(globals_module)
            spec_auth.loader.exec_module(auth_module)
            spec_api.loader.exec_module(api_module)

            self.Api = api_module.Api
            G = globals_module.G

            G.portal_config.mac_cookie = 'mac=' + stalker_addon.getSetting('mac_address')
            G.portal_config.device_id = stalker_addon.getSetting('device_id')
            G.portal_config.device_id_2 = stalker_addon.getSetting('device_id_2')
            G.portal_config.signature = stalker_addon.getSetting('signature')
            G.portal_config.serial_number = stalker_addon.getSetting('serial_number')
            G.portal_config.server_address = stalker_addon.getSetting('server_address')

            split_url = urlsplit(G.portal_config.server_address)
            G.portal_config.portal_base_url = split_url.scheme + '://' + split_url.netloc

            alternative_context = stalker_addon.getSetting('alternative_context_path') == 'true'
            if alternative_context:
                G.portal_config.portal_url = G.portal_config.portal_base_url + '/stalker_portal/server/load.php'
            else:
                G.portal_config.portal_url = G.portal_config.portal_base_url + '/portal.php'

            G.addon_config.max_retries = 3
            G.addon_config.max_page_limit = 10  # increased from 5 to 10 for maximum result coverage

            token_path = xbmcvfs.translatePath(stalker_addon.getAddonInfo('profile'))
            if not xbmcvfs.exists(token_path):
                xbmcvfs.mkdirs(token_path)
            G.addon_config.token_path = token_path
            G.addon_config.name = stalker_addon.getAddonInfo('name')

            fflog('login: ok')
            return True
        except Exception as e:
            self.available = False
            fflog(f'login: failed — {e}')
            fflog_exc()
            return False

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> list[_MovieItem] | None:
        if not self.init():
            return None
        try:
            return self._search(self.Api.get_videos, title, localtitle, year, content_type='film')
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> _StalkerShowDict:
        return {
            'tvshowtitle': tvshowtitle,
            'localtitle': localtvshowtitle,
            'aliases': aliases,
            'year': year,
        }

    def episode(self, url: _StalkerShowDict | None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> list[_EpisodeItem] | None:
        if not self.init():
            return None
        try:
            if not url or not isinstance(url, dict):
                return None

            tvshowtitle = url.get('tvshowtitle')
            localtvshowtitle = url.get('localtitle')
            year = url.get('year')

            fflog(f'episode: S{int(season):02d}E{int(episode):02d} for "{tvshowtitle}"')

            search_title = localtvshowtitle if localtvshowtitle else tvshowtitle
            tvshowtitle_clean = cleantitle.get_simple(re.sub(r'^(the|a|an)\s+', '', tvshowtitle, flags=re.I))
            localtitle_clean = cleantitle.get_simple(re.sub(r'^(the|a|an)\s+', '', localtvshowtitle, flags=re.I)) if localtvshowtitle else None

            fflog(f'query: {search_title!r}')
            data = self.Api.get_series('', 1, search_title, '0')
            items = data.get('data', [])
            fflog(f'search: {len(items)} results')

            found_series = []
            for item in items:
                item_title = item.get('name', '')
                item_year = item.get('year', '')
                item_id = item.get('id', '')
                cmd = item.get('cmd', '')
                item_year_int = self._parse_year_from_date(item_year)
                main_title_clean = cleantitle.get_simple(self._clean_item_title(item_title))
                title_match = (main_title_clean == tvshowtitle_clean) or (localtitle_clean and main_title_clean == localtitle_clean)
                if title_match and self._check_year_match(item_year_int, year):
                    fflog(f'match: {item_title} ({item_year})')
                    found_series.append((item_id, cmd, item_title))

            if not found_series:
                fflog('no match')
                return None

            results = []
            for serie_id, cmd, serie_title in found_series:
                try:
                    seasons_data = self.Api.get_seasons(serie_id)
                    for idx, season_item in enumerate(seasons_data.get('data', [])):
                        season_name = season_item.get('name', '')
                        season_id = season_item.get('id', '')
                        episode_range = season_item.get('series', [])
                        if not isinstance(episode_range, list) or not episode_range:
                            continue
                        season_num_match = re.match(r'^Season (\d+)$', season_name)
                        season_num = int(season_num_match.group(1)) if season_num_match else idx + 1
                        if season_num == int(season) and int(episode) in episode_range:
                            fflog(f'found S{int(season):02d}E{int(episode):02d} in "{serie_title}"')
                            results.append((season_id, cmd, serie_title, season_num, int(episode)))
                            break
                except Exception:
                    fflog_exc()
                    continue

            if results:
                fflog(f'approved: {len(results)}')
                return results
            fflog('no sources')
            return None

        except Exception:
            fflog_exc()
            return None

    def sources(self, url: list[_MovieItem] | list[_EpisodeItem] | None, hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        if not self.init():
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
                        video_id, cmd, video_title, season_no, episode_no = item
                        series = str(episode_no)
                        filename = f"{video_title} S{int(season_no):02d}E{int(episode_no):02d}"
                    elif len(item) == 3:
                        video_id, cmd, video_title = item
                        series = '0'
                        filename = video_title
                    else:
                        continue

                    fflog(f'fetching URL for id={video_id}, series={series}')
                    stream_url = self.Api.get_vod_stream_url(video_id, series=series, cmd=cmd, use_cmd='0')

                    if stream_url:
                        fflog(f'stream url: {stream_url[:50]}...')
                        quality = source_utils.check_sd_url(filename)

                        if quality in ('SD', '1080p'):
                            filename_upper = filename.upper()
                            if any(x in filename_upper for x in [' 4K', '(4K)', '[4K]', '4K ', '-4K']):
                                quality = '4K'
                            elif any(x in filename_upper for x in [' UHD', '(UHD)', '[UHD]', 'UHD ']):
                                quality = '4K'
                            elif ' 2160' in filename_upper or '2160P' in filename_upper:
                                quality = '4K'
                            elif ' 1440' in filename_upper or '1440P' in filename_upper:
                                quality = '1440p'
                            elif quality == 'SD':
                                quality = '1080p'

                        lang_match = re.match(r'^([A-Z]{2,3})\s*[-:]', filename, re.I)
                        if lang_match:
                            lang_prefix = lang_match.group(1).upper()
                            lang_map = {
                                'PL': 'pl', 'EN': 'en', 'UK': 'en',
                                'ES': 'es', 'LAT': 'es',
                                'DE': 'de', 'FR': 'fr', 'IT': 'it',
                                'PT': 'pt', 'RU': 'ru', 'KO': 'ko',
                                'IN': '', 'GR': '', 'AL': '',
                            }
                            language = lang_map.get(lang_prefix, '')
                            _, info_lang = source_utils.get_lang_by_type(filename)
                        else:
                            language, info_lang = source_utils.get_lang_by_type(filename)

                        info_tech = source_utils.getFileType(filename)
                        info = f"{info_lang} {info_tech}".strip()

                        sources.append({
                            'source': 'stalkervod',
                            'quality': quality,
                            'language': language,
                            'url': stream_url,
                            'info': info,
                            'filename': filename,
                            'direct': True,
                            'debridonly': False,
                            'icon': self.icon,
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

    def _parse_year_from_date(self, year_string):
        """Parse year from YYYY-MM-DD or YYYY format."""
        if not year_string or year_string == 'N/A':
            return None
        try:
            return int(year_string[:4])
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _clean_item_title(item_title):
        """
        Strip language prefixes (PL, EN, etc.) and quality suffixes (4K, HD, etc.) from title.
        Returns a cleaned title ready for comparison.
        """
        # Extract main title (without brackets)
        main_title = re.split(r'[\(\[]', item_title)[0].strip()
        # Remove language prefix with optional quality: "PL -", "PL 4K -", "EN -", etc.
        main_title = re.sub(r'^[A-Z]{2,3}(?:\s+(?:4K|UHD|HD|1080p|720p))?\s*[-:]\s*', '', main_title, flags=re.I)
        # Remove quality suffix: "4K", "1080p", "HD", etc.
        main_title = re.sub(r'\s+(4K|1080p|720p|HD|UHD)\s*$', '', main_title, flags=re.I).strip()
        # Remove leading articles
        main_title = re.sub(r'^(the|a|an)\s+', '', main_title, flags=re.I)
        return main_title

    @staticmethod
    def _check_year_match(item_year_int, search_year):
        """
        Check if the item year matches the search year (tolerance ±1).
        Returns True if it matches or if there is no year to check.
        """
        if not search_year:
            return True
        if not item_year_int:
            return False
        try:
            return abs(item_year_int - int(search_year)) <= 1
        except (ValueError, TypeError):
            return False

    def _search(self, search_method, title, localtitle, year, content_type='film'):
        """Search movies/series via ALL categories - returns all versions"""
        # Build list of titles to search
        search_titles = []
        if localtitle:
            search_titles.append(localtitle)
        if title and title != localtitle:
            search_titles.append(title)
        if not search_titles:
            search_titles = [title]

        fflog(f'searching {content_type}: {search_titles!r}')

        found_items = []
        found_ids = set()  # deduplication by ID

        # Search across ALL categories
        for search_title in search_titles:
            try:
                fflog(f'query: {search_title!r}')
                data = search_method('', 1, search_title, '0')
                items = data.get('data', [])
                fflog(f'search: {len(items)} results')

                for item in items:
                    item_title = item.get('name', '')
                    item_year = item.get('year', '')
                    item_id = item.get('id', '')
                    cmd = item.get('cmd', '')

                    # deduplication
                    if item_id in found_ids:
                        continue

                    # compare titles and year
                    item_year_int = self._parse_year_from_date(item_year)
                    main_title = self._clean_item_title(item_title)
                    main_title_clean = cleantitle.get_simple(main_title)

                    search_title_no_articles = re.sub(r'^(the|a|an)\s+', '', search_title, flags=re.I)
                    search_clean = cleantitle.get_simple(search_title_no_articles)

                    if search_clean == main_title_clean and self._check_year_match(item_year_int, year):
                        fflog(f'match: {item_title} ({item_year})')
                        found_items.append((item_id, cmd, item_title))
                        found_ids.add(item_id)

            except Exception as e:
                fflog(f'search error for {search_title!r}: {e}')
                continue

        if found_items:
            fflog(f'results: {len(found_items)}')
            return found_items
        else:
            fflog('no match')
            return None
