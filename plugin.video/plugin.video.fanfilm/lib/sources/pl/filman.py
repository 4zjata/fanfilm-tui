# -*- coding: utf-8 -*-
"""
FanFilm – source: filman.cc
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
import base64
import codecs
import re
import time
import json
from typing import Any, ClassVar, TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
from urllib.parse import parse_qsl, quote_plus, urlparse
from html import unescape

from lib.ff import requests

from lib.sources import single_call
from lib.ff import source_utils, cleantitle, utils, cache, control
from lib.ff.source_utils import ShowData, show_data_asdict, ShowDataDict
from lib.ff.client import parseDOM
from lib.ff.settings import settings
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    """Unified scraper for filman.cc supporting both free and premium paths."""

    has_sort_order: ClassVar[bool] = True
    has_color_identify2: ClassVar[bool] = True
    use_premium_color: ClassVar[bool] = True
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']
    domains: ClassVar[List[str]] = ['filman.cc']

    # Sleep before retrying when filman returns 200+empty (origin transient block — gives session time to recover)
    _EMPTY_RETRY_S: ClassVar[float] = 2.0

    EMBED_DOMAIN: ClassVar[str] = 'embed.tmp-url.pro'

    HOSTING_MAP: ClassVar[Dict[str, str]] = {
        'bigdisk.stream': 'bigdisk',
    }

    _VODI_BASE: ClassVar[str] = 'https://cloud.vodi.cc'
    _VODI_STRATEGIES: ClassVar[Tuple[str, ...]] = ('meganta_md5_v1', 'hmac_sha256_v1')

    RE_HREF = re.compile(r'href="(.*?)"')
    RE_TITLE_ALT = re.compile(r' (?:title|alt)="(.*?)"')
    RE_IFRAME = re.compile(r'data-iframe="([^"]+)"')
    RE_LINK_ID = re.compile(r'data-(?:link-)?id="(\d+)"')
    RE_TD = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
    RE_SEASON_EPISODE = re.compile(r'<span>Sezon (\d+)</span>.*?<ul>(.*?)</ul>', re.DOTALL)
    RE_EPISODE_LINK = re.compile(r'<a href="(.*?)">\s*\[s(\d+)e(\d+)\]\s*(.*?)\s*</a>', re.DOTALL)
    RE_YEAR = re.compile(r'<div class="film_year">(\d{4})</div>')
    RE_CSRF = re.compile(r'(?:name="_csrf" value="|csrf-token" content=")([^"]+)"')

    TIME_UNITS: ClassVar[Dict[str, int]] = {
        'bez': 1,
        'minutę': 60,
        'minuty': 60,
        'minut': 60,
        'godzinę': 3600,
        'godziny': 3600,
        'godzin': 3600,
        'dzień': 86400,
        'dni': 86400,
        'tydzień': 604800,
        'tygodnie': 604800,
        'tygodni': 604800,
        'miesiąc': 2592000,
        'miesiące': 2592000,
        'miesięcy': 2592000,
        'rok': 31536000,
        'lata': 31536000,
        'lat': 31536000,
    }

    @single_call
    def init(self):
        self.base_link = 'https://filman.cc'
        self.login_link = self.base_link + '/logowanie'
        self.search_link = self.base_link + '/search?phrase={title}'
        self.username = settings.getString('filman.username')
        self.password = settings.getString('filman.password')
        self.USER_AGENT = 'FilmanKodi/1.0 (compatible; Firefox/146.0)'
        # browser-like fingerprint — Referer dropped, DNT/UIR added (matches _tele which avoids CF blocks more reliably)
        self.HEADERS = {
            'Host': 'filman.cc',
            'User-Agent': self.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1',
        }
        # cache=False — bypass FF requests-cache wrapper; otherwise transient empty-body responses
        # from CF rate-limit get cached for minutes, blocking subsequent retries (see project_filman_cf_fingerprint)
        self.session = requests.Session(cache=False)
        self.session.headers.update(self.HEADERS)
        self._apply_cookie_settings()
        self._purge_old_episode_cache()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> Optional[List[Dict[str, Any]]]:
        self.init()
        results = []
        imdb_str = str(imdb) if imdb else ''
        alias_titles = source_utils.build_alias_list(title, localtitle, aliases, year)
        queries = list(source_utils.search_queries(title, localtitle))

        if settings.getBool('filman.premium'):
            try:
                if self._vodi_token():
                    item = self._vodi_search('movie', queries, alias_titles, year, imdb=imdb)
                    if item and item.get('available', True):
                        raw_quality = (item.get('quality') or (item.get('availableQualities') or [''])[0] or '').strip()
                        quality = self._normalize_vodi_quality(raw_quality)
                        results.append({
                            'type': 'premium',
                            'url': f"cloudvodi:movie:{item['id']}:{quality}:{item.get('version', '')}",
                            'title': item.get('title', title),
                            'imdb': imdb_str,
                        })
            except Exception:
                fflog_exc()

        if self.is_logged_in():
            for search_title in queries:
                fflog(f"filman: query {search_title!r}")
                resp = self._filman_get(self.search_link.format(title=quote_plus(search_title)))
                if resp is None:
                    continue
                page = resp.text
                if len(page) == 0:
                    # _filman_get already retried once — origin is blocking us, no point trying other queries
                    fflog("filman: empty body after retry — server is blocking, abort search")
                    break
                posters = page.count('<div class="poster">')
                fflog(f"filman: search len={len(page)} posters={posters}")
                # snippet only when we got content but no posters — sentinel for HTML layout change
                if posters == 0:
                    fflog(f"filman: no posters in response, snippet={page[:500]!r}")
                match = self._match_results(page, search_title, alias_titles, year, 'movie')
                fflog(f"filman: match {match}")
                if match:
                    results.append({
                        'type': 'free',
                        'url': match[0],
                        'title': match[1],
                        'imdb': imdb_str,
                    })
                    break

        return results if results else None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        self.init()
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, year))

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[List[Dict[str, Any]]]:
        self.init()
        results = []
        imdb_str = str(imdb) if imdb else ''
        tmdb = self.ffitem.show_item.tmdb_id
        data = ShowData(**url)
        episode_number_str = source_utils.format_episode_key(season, episode)
        absolute_episode_number = self.ffitem.absolute_episode_number() or 0
        alias_titles = source_utils.build_alias_list(data.title, data.local_title, data.aliases, data.year)
        queries = list(source_utils.search_queries(data.title, data.local_title))

        if settings.getBool('filman.premium'):
            try:
                token = self._vodi_token()
                if token:
                    item = self._vodi_search('series', queries, alias_titles, data.year, imdb=imdb)
                    if item:
                        ep = self._vodi_find_episode(item['id'], season, episode, absolute_episode_number, token)
                        if ep and ep.get('available', True):
                            quality = self._normalize_vodi_quality((ep.get('availableQualities') or [''])[0])
                            results.append({
                                'type': 'premium',
                                'url': f"cloudvodi:series:{ep['id']}:{quality}:{item.get('version', '')}",
                                'title': f"{item.get('title', data.title)} {episode_number_str}",
                                'imdb': imdb_str,
                                'season': int(season),
                                'episode': int(episode),
                            })
            except Exception:
                fflog_exc()

        if self.is_logged_in():
            filman_match = self._filman_episode_url(
                tmdb, data.title, episode_number_str, absolute_episode_number,
                queries, alias_titles, data.year)
            if filman_match:
                results.append({
                    'type': 'free',
                    'url': filman_match[0],
                    'title': filman_match[1],
                    'imdb': imdb_str,
                    'season': int(season),
                    'episode': int(episode),
                })

        return results if results else None

    def sources(self, candidates: Optional[List[Dict[str, Any]]], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        self.init()
        if not candidates:
            return []
        
        sources_list = []
        for candidate in candidates:
            try:
                ctype = candidate.get('type')
                url = candidate.get('url')
                
                if ctype == 'premium':
                    parsed = self._parse_cloudvodi_url(url)
                    if not parsed:
                        continue
                    source_target, source_id, quality, version = parsed
                    direct_url = self._vodi_signed_link(source_target, source_id)
                    if direct_url:
                        sources_list.append({
                            'source': 'vodi.cc',
                            'url': direct_url,
                            'quality': quality,
                            'language': 'pl',
                            'info': version,
                            'filename': '',
                            'direct': True,
                            'debridonly': False,
                            'premium': True,
                        })
                elif ctype == 'free':
                    fflog(f"filman: GET {url}")
                    resp = self._filman_get(url)
                    if resp is None:
                        continue
                    page_html = resp.text
                    route_token_match = re.search(r"var (?:rt|routeToken)\s*=\s*'([^']+)'", page_html)
                    self._rt_token = route_token_match.group(1) if route_token_match else ''

                    table = parseDOM(page_html, 'table', attrs={'id': 'links'})
                    if not table:
                        fflog(f"filman: no #links table at {url}")
                        continue

                    rows = table[0].split('<tr')[1:]
                    video_info_list = []
                    for row_html in rows:
                        video_info = self._extract_video_info(row_html)
                        if video_info:
                            video_info_list.append(video_info)

                    fflog(f"filman: sources: {len(video_info_list)}")
                    video_info_list.sort(key=lambda x: self._parse_time_string(x.get('ago', '')))
                    url_path = urlparse(url).path

                    for video in video_info_list:
                        sources_list.append({
                            'source': video['host'],
                            'url': video['url'],
                            'quality': video['quality'],
                            'language': video['lang'],
                            'info': video['sound'].strip(),
                            'filename': f"{video['ago']} | {url_path}",
                            'direct': False,
                            'debridonly': False,
                            'premium': False,
                        })
            except Exception:
                fflog_exc()
        
        return sources_list

    def resolve(self, url: str) -> Optional[str]:
        self.init()
        fflog(f"filman resolve: {url}")
        if not url:
            return None

        if url.startswith('cloudvodi:'):
            parsed = self._parse_cloudvodi_url(url)
            if not parsed:
                return None
            source_target, source_id, *_ = parsed
            return self._vodi_signed_link(source_target, source_id)

        rt = ''
        referer = self.base_link + '/'
        if '|' in url:
            url, params = url.split('|', 1)
            params_dict = dict(parse_qsl(params))
            rt = params_dict.get('rt', '')
            referer = params_dict.get('referer', referer)

        if '/link/go/' in url:
            try:
                link_id = url.split('/')[-1]
                if not rt:
                    resp = self.session.get(self.base_link, verify=False)
                    rt_m = re.search(r"var (?:rt|routeToken)\s*=\s*'([^']+)'", resp.text)
                    rt = rt_m.group(1) if rt_m else ''

                if rt:
                    self.session.get(url, verify=False, allow_redirects=False, headers={'Referer': referer})
                    token_url = f"{self.base_link}/link/token?link_id={link_id}&rt={rt}"
                    token_resp = self.session.get(token_url, verify=False, headers={
                        'X-Requested-With': 'XMLHttpRequest',
                        'Referer': referer
                    })
                    data = token_resp.json()
                    if data.get('ok') and data.get('url'):
                        url = base64.b64decode(data['url']).decode('utf-8')
            except Exception:
                fflog_exc()

        if self.EMBED_DOMAIN not in url:
            return url
        
        try:
            resp = requests.get(url, verify=False, headers={
                'User-Agent': self.USER_AGENT,
                'Referer': self.base_link + '/',
            })
            html = resp.text

            e_m = re.search(r"var _e\s*=\s*'([^']+)'", html)
            a_m = re.search(r"var _a\s*=\s*'([^']+)'", html)
            b_m = re.search(r"var _b\s*=\s*'([^']+)'", html)
            c_m = re.search(r"var _c\s*=\s*'([^']+)'", html)
            if e_m and a_m and b_m and c_m:
                key = a_m.group(1) + b_m.group(1) + c_m.group(1)
                raw = base64.b64decode(e_m.group(1))
                hoster_url = ''.join(chr(raw[i] ^ ord(key[i % len(key)])) for i in range(len(raw)))
                return hoster_url

            # fallback: pre-2026 base64+rot13 scheme — kept for older embed pages
            e_old = re.search(r"var _e = '([^']+)'", html)
            if e_old:
                return codecs.encode(base64.b64decode(e_old.group(1)).decode(), 'rot_13')
        except Exception:
            fflog_exc()
        return None

    # ── helpers ────────────────────────────────────────────────────────────

    def login(self) -> bool:
        if not self.username or not self.password:
            fflog('login: no credentials')
            return False
        try:
            self.session.cookies.clear()
            resp = self.session.get(self.login_link, verify=False)
            csrf_match = self.RE_CSRF.search(resp.text)
            csrf_token = csrf_match.group(1) if csrf_match else ''

            login_data = {
                'login': self.username,
                'password': self.password,
                'remember': 'on',
                'submit': '',
            }
            if csrf_token:
                login_data['_csrf'] = csrf_token

            headers = dict(self.HEADERS)
            headers['Referer'] = self.login_link

            response = self.session.post(self.login_link, data=login_data, headers=headers, verify=False)
            is_ok = 'guest: false' in response.text or response.status_code == 302
            
            if is_ok:
                cookie_dict = self.session.cookies.get_dict()
                cache.cache_insert('filman_session_cookies', json.dumps(cookie_dict), control.providercacheFile)
                fflog('login: ok, session cached')
                return True

            if "nieprawidłowy kod Cap" in response.text:
                fflog('login: failed — Captcha detected')
            fflog(f"login: failed — status={response.status_code} snippet: {response.text[:200]!r}")
        except Exception:
            fflog_exc()
        return False

    def is_logged_in(self) -> bool:
        # No HTTP probe — trust cached BKD_REMEMBER cookie. Stale cookies are handled by _filman_get
        # (302→/logowanie triggers self.login() + retry on the actual search request).
        self.init()
        if not (self.username and self.password):
            fflog('login: no credentials')
            return False
        if any(cookie.name == 'BKD_REMEMBER' for cookie in self.session.cookies):
            return True
        return self.login()

    def _filman_get(self, url: str) -> Optional['requests.Response']:
        """GET filman URL. One retry on 302→/logowanie (auth) or 200+empty body (origin transient block)."""
        retried_auth = retried_empty = False
        while True:
            resp = self.session.get(url, verify=False, allow_redirects=False)
            if 200 <= resp.status_code < 300:
                if len(resp.text) == 0 and not retried_empty:
                    fflog(f"filman: {url} → 200+empty, sleeping {self._EMPTY_RETRY_S}s and retrying")
                    control.sleep(int(self._EMPTY_RETRY_S * 1000))
                    retried_empty = True
                    continue
                return resp
            if resp.status_code in (301, 302):
                location = resp.headers.get('Location', '')
                if '/logowanie' in location and not retried_auth:
                    fflog(f"filman: {url} → /logowanie, re-authenticating")
                    if not self.login():
                        return None
                    retried_auth = True
                    continue
                fflog(f"filman: {url} redirect status={resp.status_code} location={location!r}")
            return None

    def _apply_cookie_settings(self) -> None:
        cached = cache.cache_get('filman_session_cookies', control.providercacheFile)
        if cached:
            try:
                cookie_dict = json.loads(cached['value'])
                self.session.cookies.update(cookie_dict)
                fflog('cookies: loaded from cache')
            except Exception:
                fflog_exc()

    def _purge_old_episode_cache(self) -> None:
        try:
            cutoff = int(time.time()) - 86400
            cursor = cache._get_connection_cursor_providers()
            cursor.execute(
                "DELETE FROM cache WHERE (key LIKE 'filman_eps_%' OR key LIKE 'filman_vodi_id_%' OR key LIKE 'filman_vodi_series_%') AND date < ?",
                (cutoff,))
            cursor.connection.commit()
        except Exception:
            fflog_exc()

    @staticmethod
    def _cache_get(key: str) -> Optional[Tuple[Any, int]]:
        cached = cache.cache_get(key, control.providercacheFile)
        if not cached or 'date' not in cached:
            return None
        try:
            return json.loads(cached['value']), int(cached['date'])
        except Exception:
            fflog_exc()
            return None

    @classmethod
    def _cache_get_fresh(cls, key: str, hours: int) -> Optional[Any]:
        result = cls._cache_get(key)
        if result is None:
            return None
        value, date = result
        return value if cache._is_cache_valid(date, hours) else None

    def _parse_search_results(self, page_content: str, year: int, media_type: str) -> list[dict[str, Any]]:
        candidates = []
        blocks = page_content.split('<div class="poster">')[1:]
        for block in blocks:
            try:
                year_match = self.RE_YEAR.search(block)
                if not year_match or abs(int(year_match.group(1)) - int(year)) > 1:
                    continue
                href_match = self.RE_HREF.search(block)
                if not href_match:
                    continue
                href = href_match.group(1)
                if href.startswith('/'):
                    href = self.base_link + href
                if (media_type == 'movie' and '/m/' not in href) or (media_type == 'tvshow' and '/s/' not in href):
                    continue
                title_match = self.RE_TITLE_ALT.search(block)
                if not title_match:
                    continue
                full_title = unescape(utils.decode_title_from_latin1(title_match.group(1)))
                parts = [cleantitle.get_simple(p.strip()) for p in full_title.split('/') if p.strip()]
                candidates.append({
                    'href': href,
                    'full_title': full_title,
                    'title_parts_clean': parts
                })
            except Exception:
                continue
        return candidates

    def _match_results(self, page_content: str, primary_title: str, aliases: List[str], year: int, media_type: str) -> Optional[Tuple[str, str]]:
        candidates = self._parse_search_results(page_content, year, media_type)
        search_clean = cleantitle.get_simple(primary_title)
        alias_cleans = {cleantitle.get_simple(a) for a in aliases if a}
        for candidate in candidates:
            if any(p == search_clean for p in candidate['title_parts_clean']):
                return (candidate['href'], candidate['full_title'])
        for candidate in candidates:
            if any(p in alias_cleans for p in candidate['title_parts_clean']):
                return (candidate['href'], candidate['full_title'])
        for candidate in candidates:
            if any(source_utils._levenshtein(p, search_clean) <= 3 for p in candidate['title_parts_clean']):
                return (candidate['href'], candidate['full_title'])
        return None

    def _filman_episode_url(self, tmdb, show_title: str, episode_number_str: str,
                             absolute_episode_number: int, queries, alias_titles, year) -> Optional[Tuple[str, str]]:
        if tmdb:
            episodes = self._cache_get_fresh(f"filman_eps_{tmdb}", 24)
            if episodes:
                result = self._find_in_episodes(episodes, show_title, episode_number_str, absolute_episode_number)
                if result:
                    return result
        for search_title in queries:
            fflog(f"filman: query {search_title!r} ({year})")
            resp = self._filman_get(self.search_link.format(title=quote_plus(search_title)))
            if resp is None:
                continue
            if len(resp.text) == 0:
                # _filman_get already retried — origin blocking, abort
                fflog("filman: empty body after retry — server is blocking, abort search")
                break
            result = self._match_results(resp.text, search_title, alias_titles, year, 'tvshow')
            if result:
                # first show match wins — if episode page parse fails here, don't refetch via other queries (likely same show)
                return self._extract_episode_url(result, episode_number_str, absolute_episode_number, tmdb)
        return None

    def _extract_episode_url(self, show_match: Tuple[str, str], episode_number_str: str, absolute_episode_number: int = 0, tmdb=None) -> Optional[Tuple[str, str]]:
        show_url, show_title = show_match
        try:
            resp = self._filman_get(show_url)
            if resp is None:
                return None
            page_html = resp.text
            season_blocks = self.RE_SEASON_EPISODE.findall(page_html)
            if not season_blocks:
                # sentinel for filman HTML layout changes — capture context around 'Sezon' for regex update
                if 'Sezon' in page_html:
                    sezon_at = page_html.find('Sezon')
                    snippet = page_html[max(0, sezon_at - 50):sezon_at + 200]
                else:
                    snippet = page_html[:300]
                fflog(f"filman: no season blocks at {show_url}, snippet={snippet!r}")
            episodes = []
            for _, episodes_html in season_blocks:
                for ep_url, season_num, episode_num, _ in self.RE_EPISODE_LINK.findall(episodes_html):
                    if ep_url.startswith('/'):
                        ep_url = self.base_link + ep_url
                    episodes.append((source_utils.format_episode_key(season_num, episode_num), ep_url))
            if not episodes:
                return None
            if tmdb:
                cache.cache_insert(f"filman_eps_{tmdb}", json.dumps(episodes), control.providercacheFile)
            return self._find_in_episodes(episodes, show_title, episode_number_str, absolute_episode_number)
        except Exception:
            fflog_exc()
        return None

    def _find_in_episodes(self, episodes: list[tuple[str, str]], show_title: str, episode_number_str: str, absolute_episode_number: int) -> Optional[Tuple[str, str]]:
        episode_map = dict(episodes)

        if episode_number_str in episode_map:
            return (episode_map[episode_number_str], f"{show_title} {episode_number_str}")

        if source_utils.is_anime(self.ffitem):
            # Filman stores anime as a single season (s01eNNN). TMDB assigns
            # arbitrary season numbers. absolute_episode_number() is reliable
            # when continuous_episode_number=True, but position-based indexing
            # on a lexicographically-sorted key list is broken for episode
            # numbers >99 (e.g. s01e999 sorts after s01e1155). Use dict lookup.
            if absolute_episode_number > 0:
                s01_key = source_utils.format_episode_key(1, absolute_episode_number)
                if s01_key in episode_map:
                    return (episode_map[s01_key], f"{show_title} {s01_key}")
            return None

        abs_key = source_utils.format_episode_key(1, absolute_episode_number)
        if absolute_episode_number > 0 and abs_key in episode_map:
            return (episode_map[abs_key], f"{show_title} {abs_key}")

        return None

    def _process_sound(self, sound_string: str) -> Tuple[str, str]:
        sound_string = sound_string.replace('Napisy_Tansl', 'napisy').replace('_', ' ')
        language_code = 'en' if 'ENG' in sound_string else 'pl'
        return sound_string.replace('PL', '').replace('ENG', '').strip() or ' ', language_code

    def _parse_time_string(self, time_string: str) -> float:
        time_match = re.match(r'(?:\b(\d+)\b\s+)?(\w+)', time_string)
        if not time_match:
            return float('inf')
        numeric_value, time_unit = time_match.groups()
        multiplier = self.TIME_UNITS.get(time_unit)
        return (int(numeric_value) if numeric_value else 1) * multiplier if multiplier else float('inf')

    def _extract_video_info(self, row_html: str) -> dict[str, Any]:
        td_cells = self.RE_TD.findall(row_html)
        if len(td_cells) < 3:
            return {}

        video_url = ''
        if iframe_match := self.RE_IFRAME.findall(row_html):
            decoded_iframe = base64.b64decode(iframe_match[0]).decode('utf-8').replace('\\/', '/')
            src_match = re.findall(r"src['\"]:['\"](.+?)['\"]", decoded_iframe)
            video_url = src_match[0] if src_match else decoded_iframe
        elif link_id_m := self.RE_LINK_ID.search(row_html):
            video_url = f"{self.base_link}/link/go/{link_id_m.group(1)}"

        if not video_url:
            return {}

        rt_token = getattr(self, '_rt_token', '')
        if '|' in video_url:
            video_url += f'&rt={rt_token}'
        else:
            video_url += f'|rt={rt_token}&referer={self.base_link}'

        sound_info, language_code = self._process_sound(td_cells[1].strip())
        # alt attribute on filman.cc rows = actual hosting name (VOE, Doodstream, etc.);
        # link/go/X URLs all point to filman.cc, so URL hostname is useless here
        host_match = re.search(r'alt="(.*?)"', td_cells[0])
        host_name = self._normalize_hosting_name(host_match.group(1) if host_match else '')

        ago_match = re.search(r'dodane\s+(.*?temu)', td_cells[0], re.S)
        return {
            'host': host_name,
            'url': video_url,
            'quality': source_utils.check_sd_url(td_cells[2].strip()),
            'sound': sound_info,
            'lang': language_code,
            'ago': ago_match.group(1).strip() if ago_match else ''
        }

    @classmethod
    def _normalize_hosting_name(cls, domain: str) -> str:
        for suffix, name in cls.HOSTING_MAP.items():
            if suffix in domain:
                return name
        parts = domain.rsplit('.', 2)
        if len(parts) >= 3:
            return f"{parts[-2]}.{parts[-1]}"
        return domain

    # ── Vodi API ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_cloudvodi_url(url: str) -> Optional[Tuple[str, int, str, str]]:
        try:
            head = url.partition('|')[0]
            parts = head.split(':', 4)
            if len(parts) < 3 or parts[0] != 'cloudvodi':
                return None
            target = parts[1]
            source_id = int(parts[2])
            quality = parts[3] if len(parts) > 3 and parts[3] else '1080p'
            version = parts[4] if len(parts) > 4 else ''
            return target, source_id, quality, version
        except Exception:
            fflog_exc()
            return None

    def _vodi_token(self) -> Optional[str]:
        if hasattr(self, '_vodi_access_token'):
            return self._vodi_access_token
        if not (self.username and self.password):
            fflog('vodi: no credentials, skipping premium auth')
            self._vodi_access_token = None
            return None
        token = self._vodi_resolve_token()
        self._vodi_access_token = token
        return token

    def _vodi_resolve_token(self) -> Optional[str]:
        result = self._cache_get('filman_vodi_auth')
        if result is not None:
            auth, date = result
            if auth.get('is_free'):
                if cache._is_cache_valid(date, 2):
                    fflog('vodi: skip (cached as free user)')
                    return None
            else:
                access_token = auth.get('access_token', '')
                refresh_token = auth.get('refresh_token', '')
                if access_token and cache._is_cache_valid(date, 168):
                    fflog('vodi: using cached token')
                    return access_token
                if refresh_token:
                    return self._vodi_do_refresh(refresh_token)
        return self._vodi_do_login()

    def _vodi_session_get(self) -> 'requests.Session':
        if getattr(self, '_vodi_session', None) is None:
            session = requests.Session(cache=False)
            session.headers.update({
                'Content-Type': 'application/json',
                'User-Agent': self.USER_AGENT,
                'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://filman.cc',
                'Referer': 'https://filman.cc/',
            })
            self._vodi_session = session
        return self._vodi_session

    def _vodi_post(self, path: str, payload: dict, token: Optional[str] = None) -> 'requests.Response':
        session = self._vodi_session_get()
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return session.post(f'{self._VODI_BASE}{path}', data=json.dumps(payload), headers=headers, verify=False, timeout=10)

    def _vodi_get(self, path: str, token: Optional[str] = None) -> 'requests.Response':
        session = self._vodi_session_get()
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        return session.get(f'{self._VODI_BASE}{path}', headers=headers, verify=False, timeout=10)

    @staticmethod
    def _save_vodi_auth(auth: dict) -> None:
        cache.cache_insert('filman_vodi_auth', json.dumps(auth), control.providercacheFile)

    def _vodi_do_login(self) -> Optional[str]:
        try:
            fflog(f'vodi: attempting login for {self.username}')
            resp = self._vodi_post('/auth/login', {
                'username': self.username, 'password': self.password,
                'deviceName': 'Filman Desktop', 'devicePlatform': 'desktop',
                'deviceType': 'pc', 'appVersion': '2.0.2',
            })
            fflog(f'vodi: login status={resp.status_code}')
            if resp.status_code == 429:
                self._handle_vodi_429(resp, "login")
                return None
            if resp.status_code in (200, 201):
                data = resp.json()
                auth = {'access_token': data['token'], 'refresh_token': data.get('refreshToken', ''), 'is_free': False}
                self._save_vodi_auth(auth)
                fflog('vodi: login ok (premium)')
                return auth['access_token']
            if resp.status_code in (401, 403):
                fflog('vodi: login failed (free/unauthorized)')
                self._save_vodi_auth({'is_free': True})
        except Exception:
            fflog_exc()
        return None

    def _vodi_do_refresh(self, refresh_token: str) -> Optional[str]:
        try:
            resp = self._vodi_post('/auth/token/refresh', {'refreshToken': refresh_token})
            fflog(f'vodi: refresh status={resp.status_code}')
            if resp.status_code == 200:
                data = resp.json()
                auth = {'access_token': data['token'], 'refresh_token': data.get('refreshToken', refresh_token), 'is_free': False}
                self._save_vodi_auth(auth)
                fflog('vodi: token refreshed')
                return auth['access_token']
        except Exception:
            fflog_exc()
        return self._vodi_do_login()

    def _vodi_relogin(self) -> Optional[str]:
        fflog('vodi: 401 — clearing cached token, re-login')
        if hasattr(self, '_vodi_access_token'):
            del self._vodi_access_token
        self._save_vodi_auth({'is_free': False})
        new_token = self._vodi_do_login()
        if new_token:
            self._vodi_access_token = new_token
        return new_token

    def _vodi_search(self, media_type: str, queries: List[str], alias_titles: List[str], year: str, imdb: str = '') -> Optional[dict]:
        cache_key = f'filman_vodi_id_{media_type}_{imdb}' if imdb else None
        if cache_key:
            entry = self._cache_get_fresh(cache_key, 24)
            if entry:
                if entry.get('not_found'):
                    fflog(f'vodi: cache hit {cache_key} (not_found)')
                    return None
                fflog(f'vodi: cache hit {cache_key}')
                return entry

        token = self._vodi_token()
        if not token:
            return None
        path = f'/{"movies" if media_type == "movie" else "series"}/search'
        year_int = int(year) if year else 0
        alias_cleans = [cleantitle.get_simple(a) for a in alias_titles if a]
        token_refreshed = False

        for query in queries:
            try:
                payload = {
                    'filters': [{'type': 'title', 'condition': 'contains', 'payload': query}],
                    'sorts': [], 'page': 1, 'itemsPerPage': 20,
                }
                resp = self._vodi_post(path, payload, token=token)
                fflog(f'vodi: search {media_type} {query!r} status={resp.status_code}')
                if resp.status_code == 401 and not token_refreshed:
                    token_refreshed = True
                    new_token = self._vodi_relogin()
                    if not new_token:
                        return None
                    token = new_token
                    resp = self._vodi_post(path, payload, token=token)
                    fflog(f'vodi: search retry status={resp.status_code}')
                if resp.status_code not in (200, 201):
                    continue
                all_items = resp.json().get('items', [])
                for item in all_items:
                    if year_int and abs(int(item.get('year') or 0) - year_int) > 1:
                        continue
                    item_parts = [cleantitle.get_simple(p.strip()) for p in item.get('title', '').split('/') if p.strip()]
                    for ac in alias_cleans:
                        for part in item_parts:
                            if ac == part or source_utils._levenshtein(ac, part) <= 2:
                                fflog(f'vodi: {media_type} match id={item["id"]} title={item.get("title")!r}')
                                entry = {
                                    'id': item['id'],
                                    'title': item.get('title', ''),
                                    'quality': item.get('quality', ''),
                                    'availableQualities': item.get('availableQualities', []),
                                    'version': item.get('version', ''),
                                    'available': item.get('available', True),
                                }
                                if cache_key:
                                    cache.cache_insert(cache_key, json.dumps(entry), control.providercacheFile)
                                return entry
            except Exception:
                fflog_exc()

        if cache_key:
            cache.cache_insert(cache_key, json.dumps({'not_found': True}), control.providercacheFile)
            fflog(f'vodi: cached negative {cache_key}')
        return None

    def _vodi_find_episode(self, series_id: int, season: str, episode: str, absolute: int, token: str) -> Optional[Dict[str, Any]]:
        cache_key = f'filman_vodi_series_{series_id}'
        seasons = self._cache_get_fresh(cache_key, 24)
        if seasons is not None:
            fflog(f'vodi: cache hit {cache_key}')
        else:
            try:
                path = f'/series/{series_id}'
                resp = self._vodi_get(path, token=token)
                if resp.status_code == 401:
                    new_token = self._vodi_relogin()
                    if not new_token:
                        return None
                    token = new_token
                    resp = self._vodi_get(path, token=token)
                    fflog(f'vodi: /series/{series_id} retry status={resp.status_code}')
                if resp.status_code in (200, 201):
                    seasons = resp.json().get('seasons', {})
                    cache.cache_insert(cache_key, json.dumps(seasons), control.providercacheFile)
                    fflog(f'vodi: cached {cache_key}')
            except Exception:
                fflog_exc()
        if seasons:
            try:
                season_eps = seasons.get(str(int(season)), [])
                for ep in season_eps:
                    if str(ep.get('episode', '')) == str(int(episode)):
                        return ep
                if absolute > 0:
                    for ep in seasons.get('1', []):
                        if ep.get('episode') == absolute:
                            return ep
            except Exception:
                fflog_exc()
        return None

    def _vodi_signed_link(self, source_target: str, source_id: int) -> Optional[str]:
        token = self._vodi_token()
        if not token:
            fflog('vodi resolve: no token')
            return None
        try:
            payload = {
                'sourceTarget': source_target, 'sourceId': int(source_id),
                'data': {'quality': 'auto'},
            }
            token_refreshed = False
            for code in self._VODI_STRATEGIES:
                payload['code'] = code
                resp = self._vodi_post('/signed-links', payload, token=token)
                fflog(f'vodi signed-link {code} status={resp.status_code}')

                if resp.status_code == 401 and not token_refreshed:
                    token_refreshed = True
                    new_token = self._vodi_relogin()
                    if not new_token:
                        return None
                    token = new_token
                    resp = self._vodi_post('/signed-links', payload, token=token)
                    fflog(f'vodi signed-link {code} retry status={resp.status_code}')

                if resp.status_code == 429:
                    self._handle_vodi_429(resp, "signed-link")
                    return None

                if resp.status_code in (200, 201):
                    url = resp.json().get('url')
                    if url:
                        fflog(f'vodi: signed-link ok code={code} url={url[:80]}...')
                        return url
                fflog(f'vodi: signed-link {code} fail status={resp.status_code}')
        except Exception:
            fflog_exc()
        return None

    def _handle_vodi_429(self, resp: 'requests.Response', context: str) -> None:
        try:
            seconds = int(resp.headers.get('Retry-After') or 0)
        except ValueError:
            seconds = 0
        when = self._format_retry(seconds) if seconds > 0 else 'later'
        msg = f"Vodi: daily limit reached — try again in {when}"
        fflog(f"vodi rate limit ({context}): retry_after={seconds}s | response: {resp.text!r}")
        control.infoDialog(msg, heading="Filman Premium", icon="WARNING")

    @staticmethod
    def _format_retry(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        if hours:
            return f"{hours}h {minutes}min" if minutes else f"{hours}h"
        if minutes:
            return f"{minutes} min"
        return f"{seconds} s"

    @staticmethod
    def _normalize_vodi_quality(raw_quality: str) -> str:
        cleaned = (raw_quality or '').strip()
        if cleaned.upper() == 'HD':
            return '1080p'
        return source_utils.get_quality(cleaned)
