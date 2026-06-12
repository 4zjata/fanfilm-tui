# -*- coding: utf-8 -*-
"""
FanFilm - źródło: DramaQueen (plugin.video.dramaqueen)
Copyright (C) 2026 :)

Scraper wyciągający linki z wtyczki DramaQueen.
Dane pobierane z cache.db wtyczki DQ (sqlite3)
Odtwarzanie przez plugin.video.dramaqueen w trybie ListLinks.

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from sqlite3 import dbapi2 as sqlite
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, ClassVar
from urllib.parse import urlencode

import xbmcaddon
import xbmcvfs

from lib.ff import cleantitle, control
from lib.sources import single_call
from lib.ff.item import FFItem
from lib.ff.source_utils import get_asian_country_codes, ASIAN_COUNTRIES
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

DQ_PLUGIN = 'plugin.video.dramaqueen'

# Cache key md5("get_json_()_{}") — result of _hash_function(get_json, (), {})
_DQ_CACHE_KEY = hashlib.md5('get_json_()_{}'.encode()).hexdigest()

# Suffixes suggesting a sequel/continuation (after title prefix).
# Digit covers: "Title 2", "Title 3", "Title 1938" etc.
# "part" covers: "Island part 2" etc.
_CONTINUATION_SUFFIXES = ('part', 'season')


# ISO 3166-1 → DQ cache category key mapping.
# Countries unknown to DQ (e.g. US) yield empty set → no results (correct).
# No country info → None → no filtering (safe fallback).
_DQ_COUNTRY_KEYS = {
    'KR': {'Drama Koreańska', 'Film Korea'},
    'JP': {'Drama Japońska', 'Film Japonia'},
    'CN': {'Dramy Inne', 'Filmy Pozostałe'},
    'TW': {'Dramy Inne', 'Filmy Pozostałe'},
    'HK': {'Dramy Inne', 'Filmy Pozostałe'},
}


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    has_sort_order: bool = True
    has_color_identify2: bool = True
    use_premium_color: bool = True
    ffitem: FFItem  # set dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.domains = ['dramaqueen']
        self.available = True
        self._db_path = None
        self.icon = ''

    @single_call
    def init(self):
        if not control.condVisibility(f'System.AddonIsEnabled({DQ_PLUGIN})'):
            self.available = False
            return False

        try:
            dq_addon = xbmcaddon.Addon(DQ_PLUGIN)
            self.icon = dq_addon.getAddonInfo('icon')
            data_dir = xbmcvfs.translatePath(f'special://profile/addon_data/{DQ_PLUGIN}')
            self._db_path = os.path.join(data_dir, 'cache.db')
            return True
        except Exception as e:
            fflog(f'DQ init failed: {e}')
            self.available = False
            return False

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[List[Dict[str, Any]]]:
        if not self.init():
            return None
        try:
            fflog(f'searching movie "{title}" ({year})')
            results = self._search(title, localtitle, year, 'movie')
            if not results:
                fflog('no movie match')
                return None
            play_data_list = [
                self._make_play_data(show, ep)
                for show in results
                for ep in show.get('episodes', [])
                if ep.get('links')
            ]
            fflog(f'found {len(play_data_list)} movie source(s)')
            return play_data_list or None
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[List[Dict[str, Any]]]:
        if not self.init():
            return None
        try:
            fflog(f'searching drama "{tvshowtitle}" ({year})')
            results = self._search(tvshowtitle, localtitle, year, 'drama')
            if not results:
                fflog('no drama match')
                return None
            titles = ', '.join(f'"{r.get("title")}"' for r in results)
            fflog(f'found {len(results)} part(s): {titles}')
            return results
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[List[Dict[str, Any]]], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[List[Dict[str, Any]]]:
        if not self.init():
            return None
        try:
            if not url:
                return None
            shows = url if isinstance(url, list) else [url]
            ep_target = int(episode)
            offset = 0
            for show in shows:
                eps = show.get('episodes', [])
                local_ep = ep_target - offset
                matched_ep = next(
                    (ep for ep in eps
                     if str(ep.get('episode', '')) == str(local_ep) and ep.get('links')),
                    None
                )
                if matched_ep:
                    fflog(f'found episode {ep_target} (local={local_ep}) in "{show.get("title")}"')
                    return [self._make_play_data(show, matched_ep)]
                offset += len(eps)
            fflog(f"episode {ep_target} not found in {[s.get('title') for s in shows]}")
            return None
        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[List[Dict[str, Any]]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        src_list = []
        if not self.available:
            return src_list
        try:
            if not url:
                return src_list
            items = url if isinstance(url, list) else [url]
            for item in items:
                if not isinstance(item, dict) or not item.get('ep_links'):
                    continue
                plugin_url = f'plugin://{DQ_PLUGIN}/?' + urlencode({
                    'mode': 'ListLinks',
                    'name': item.get('ep_title', ''),
                    'data': repr(item),
                })
                src_list.append({
                    'source': 'DramaQueen',
                    'quality': '1080p',
                    'language': 'pl',
                    'url': plugin_url,
                    'info': '',
                    'direct': True,
                    'debridonly': False,
                    'icon': self.icon,
                    'premium': True,
                    'external': True,
                })
        except Exception:
            fflog_exc()
        fflog(f'sources: {len(src_list)}')
        return src_list

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_continuation_suffix(suffix: str) -> bool:
        """Whether the suffix after a common prefix suggests a continuation, not a different show."""
        return bool(suffix) and (suffix[0].isdigit() or suffix.startswith(_CONTINUATION_SUFFIXES))

    def _get_dq_data(self) -> Optional[Dict[str, Any]]:
        """Read data from DQ cache.db (read-only). Returns dict or None."""
        if not self._db_path or not os.path.exists(self._db_path):
            fflog('cache.db not found')
            return None

        try:
            uri = f'file:{self._db_path}?mode=ro'
            conn = sqlite.connect(uri, uri=True, timeout=5)
            try:
                cur = conn.cursor()
                cur.execute('SELECT value, date FROM cache WHERE key=?', (_DQ_CACHE_KEY,))
                row = cur.fetchone()
            finally:
                conn.close()
        except Exception as e:
            fflog(f'DQ cache.db read failed: {e}')
            return None

        if not row or not row[0]:
            fflog('no data in cache')
            return None

        age_h = (time.time() - row[1]) / 3600
        if age_h > 24:
            fflog(f'cache stale ({age_h:.1f}h) — will refresh on next run')
        else:
            fflog(f'cache age {age_h:.1f}h')

        try:
            return json.loads(row[0])
        except Exception as e:
            fflog(f'JSON parse error: {e}')
            return None

    def _get_country_keys(self) -> Optional[Set[str]]:
        """Return allowed DQ category keys based on production country and TMDB keywords.
        Returns None if country info is missing (no filtering)."""
        try:
            country_codes = self.ffitem.vtag.getCountryCodes()
        except Exception:
            return None  # ffitem not set or vtag unavailable → skip filtering
        if not country_codes:
            return None  # no country data → no filtering

        codes = get_asian_country_codes(self.ffitem)
        allowed: Set[str] = set()
        for code in codes:
            if code in _DQ_COUNTRY_KEYS:
                allowed |= _DQ_COUNTRY_KEYS[code]
        return allowed  # empty set → no results (non-Asian content)

    def _get_all_shows(self, allowed_keys: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        data = self._get_dq_data()
        if not data:
            return []
        shows = []
        for key, items in data.items():
            if key == 'genres' or not isinstance(items, list):
                continue
            if allowed_keys is not None and key not in allowed_keys:
                continue
            for item in items:
                if item and isinstance(item, dict):
                    shows.append(item)
        return shows

    def _search(self, title: str, localtitle: str, year: str, content_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search the DQ catalogue by title/localtitle and year (±1).
        Also matches continuation titles (e.g. 'Island part 2' for 'Island').
        Filters by DQ category based on production country from ffitem."""
        search_set = set()
        for t in (localtitle, title):
            if t:
                clean = cleantitle.get_simple(t)
                if clean:
                    search_set.add(clean)
        if not search_set:
            return []

        allowed_keys = self._get_country_keys()
        if allowed_keys is not None:
            fflog(f"DQ: country filter → {allowed_keys or 'empty (country outside DQ)'}")

        results = []
        for show in self._get_all_shows(allowed_keys):
            if content_type and show.get('type') != content_type:
                continue
            show_clean = cleantitle.get_simple(show.get('title', ''))
            exact = show_clean in search_set
            prefix = not exact and any(
                len(s) >= 4 and show_clean.startswith(s)
                and self._is_continuation_suffix(show_clean[len(s):])
                for s in search_set
            )
            if not exact and not prefix:
                continue
            try:
                if year and show.get('year') and abs(int(show['year']) - int(year)) > 1:
                    continue
            except (ValueError, TypeError):
                pass
            results.append(show)

        # Exact-title duplicates with different years (e.g. 'Trap' 2019 vs 'TRAP' 2020):
        # if a result with diff=0 exists, discard diff=1 results for the same title.
        if year:
            try:
                target_year = int(year)
                title_groups = {}
                for r in results:
                    r_clean = cleantitle.get_simple(r.get('title', ''))
                    if r_clean in search_set:
                        title_groups.setdefault(r_clean, []).append(r)
                to_remove = set()
                for group in title_groups.values():
                    if len(group) > 1:
                        has_exact = any(r.get('year') and int(r['year']) == target_year for r in group)
                        if has_exact:
                            # diff=0 exists — discard diff=1
                            for r in group:
                                if r.get('year') and int(r['year']) != target_year:
                                    to_remove.add(id(r))
                        else:
                            # no diff=0 — take only the oldest, do not merge different shows
                            oldest = min(group, key=lambda r: int(r.get('year') or 0))
                            for r in group:
                                if r is not oldest:
                                    to_remove.add(id(r))
                if to_remove:
                    results = [r for r in results if id(r) not in to_remove]
            except (ValueError, TypeError):
                pass

        results.sort(key=lambda s: s.get('year') or 0)
        return results

    def _make_play_data(self, show: Dict[str, Any], ep: Dict[str, Any]) -> Dict[str, Any]:
        """Minimal dict required by DQ ListLinks mode."""
        return {
            'type': show.get('type', 'drama'),
            'ep_title': ep.get('ep_title', ''),
            'ep_players': ep.get('players', []),
            'ep_links': ep.get('links', []),
        }
