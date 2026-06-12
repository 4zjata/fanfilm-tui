# -*- coding: utf-8 -*-
"""
FanFilm – źródło: wrzucaj.pl (video stream + file catalog)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Providers:
  wrzucaj    – Video search: wrzucaj.pl/videos, direct CDN URLs
  wrzucaj.2  – File search: /ajax/search, resolve via file_details


And if you absolutely must borrow the scraper, remember that it cost us over a month of work...
It would be a good idea to mention this in your version where you are borrowing from,
in accordance with the license.
Scraper was created in cooperation with wrzutaj.pl owner, who helped us with reverse engineering and testing.
"""

from __future__ import annotations
import re
import json
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional, Tuple, TypedDict, TYPE_CHECKING
from urllib.parse import quote_plus, unquote_plus, urlparse, parse_qs, quote
from html import unescape

from lib.ff import requests

from lib.sources import single_call, SourceModule
from lib.ff import cache, control
from lib.ff import source_utils
from lib.ff.source_utils import FF_UA, ShowData, show_data_asdict, ShowDataDict
if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
from lib.ff.settings import settings
from lib.ff.log_utils import fflog, fflog_exc
from const import const
from lib.ff.item import FFItem


class _SearchResult(TypedDict):
    file_id: str
    dl_page_url: str
    filename: str
    file_size_mb: int


class _FileDetails(TypedDict):
    cdn_url: Optional[str]
    filesize_mb: int


class _VideoCard(TypedDict):
    """Search result card from WrzucajVideo._search()."""
    file_id: str
    watch_url: str
    filename: str


COOKIE_EXPIRATION_HOURS = 3
_CACHE_KEY_PREFIX = 'wrzucaj_session'

_RE_EXT_STRIP = re.compile(r'\.\w{2,4}$')


# ─── WrzucajApiClient ────────────────────────────────────────────────────────────

class WrzucajApiClient:
    """HTTP layer: session login + file catalog search + file_details."""

    BASE_LINK: ClassVar[str] = 'https://wrzucaj.pl'
    LOGIN_URL: ClassVar[str] = BASE_LINK + '/account/login'
    SEARCH_URL: ClassVar[str] = BASE_LINK + '/ajax/search'
    ACCOUNT_URL: ClassVar[str] = BASE_LINK + '/account'
    ACCOUNT_EDIT_URL: ClassVar[str] = BASE_LINK + '/account/edit'

    RE_BADGE = re.compile(r'badge-roundless">\s*([^<]+)<')

    RE_TRANSFER_GB = re.compile(
        r'tile-stats[^>]*tile-green[^>]*>.*?data-end="([\d.]+)".*?(?:Available transfer|Dostępny transfer)',
        re.DOTALL | re.IGNORECASE,
    )

    RE_FILE_URL = re.compile(
        r'href="(https://wrzucaj\.pl/([a-f0-9]{12,32})/([^"]+))"',
    )

    RE_HISTORY_ROW = re.compile(
        r'<td>(\d{2}\.\d{2}\.\d{4} - \d{2}:\d{2}:\d{2})</td>'
        r'.*?href="(https://wrzucaj\.pl/([a-f0-9]{12,32})/[^"]+)"',
        re.DOTALL,
    )

    RE_FILESIZE = re.compile(
        r'(?:Filesize|Rozmiar pliku):\s*([\d.]+)\s*([TGMK]B)',
        re.IGNORECASE,
    )

    RE_NUMERIC_ID = re.compile(r'showFileInformation\((\d+)')

    RE_CDN_PLAYER = re.compile(
        r'"(https://s\d+\.wrzucaj\.pl/[a-f0-9]{12,32}/[^"]+\?download_token=[a-f0-9]+)"',
    )

    RE_CDN_DOWNLOAD = re.compile(
        r"openUrl\('(https://(?:s\d+\.)?wrzucaj\.pl/[a-f0-9]{12,32}/[^']+\?download_token=[a-f0-9]+)'",
    )

    RE_FILESIZE_DETAIL = re.compile(
        r'(?:Filesize|Rozmiar[^<]{0,20}):(?:&nbsp;|\s)+([\d.]+)(?:&nbsp;|\s)+([TGMK]B)',
        re.IGNORECASE,
    )

    # Files in download history are available without transfer cost for 6h
    # (confirmed by service owner) — 5 min safety margin
    _HISTORY_MAX_AGE = timedelta(hours=5, minutes=55)

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': FF_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
            'Referer': self.BASE_LINK + '/',
        })

    @staticmethod
    def _size_to_mb(val: float, unit: str) -> int:
        unit = unit.upper()
        return int(
            val * 1024 * 1024 if unit == 'TB' else
            val * 1024 if unit == 'GB' else
            val if unit == 'MB' else
            val / 1024
        )

    def set_cookies(self, cookie_str: str):
        self.session.cookies.clear()
        for part in cookie_str.split('; '):
            if '=' in part:
                name, value = part.split('=', 1)
                self.session.cookies.set(name, value)

    def get_cookies(self) -> str:
        return '; '.join(f'{name}={value}' for name, value in self.session.cookies.items())

    def login(self, username: str, password: str) -> bool:
        self.session.get(self.LOGIN_URL, verify=False)
        resp = self.session.post(
            self.LOGIN_URL,
            data={'username': username, 'password': password, 'rememberme': '1', 'submitme': '1'},
            headers={'Referer': self.LOGIN_URL},
            verify=False,
            allow_redirects=True,
        )
        return 'LOGGED_IN = true' in resp.text or bool(self.session.cookies.get('wB'))

    def check_premium(self) -> str:
        try:
            page = self.session.get(self.ACCOUNT_URL, verify=False, timeout=10).text
            badge_match = self.RE_BADGE.search(page)
            if badge_match and 'PREMIUM' in badge_match.group(1).upper():
                return 'premium'
        except Exception:
            fflog_exc()
        return 'free'

    def get_account_edit(self) -> str:
        return self.session.get(self.ACCOUNT_EDIT_URL, verify=False, timeout=15).text

    def parse_transfer_gb(self, html: str) -> float:
        transfer_match = self.RE_TRANSFER_GB.search(html)
        return float(transfer_match.group(1)) if transfer_match else 0.0

    def parse_download_history(self, html: str) -> Dict[str, str]:
        history: Dict[str, str] = {}
        idx = html.find('id="downloadHistory"')
        if idx < 0:
            return history
        cutoff = datetime.now() - self._HISTORY_MAX_AGE
        for row_match in self.RE_HISTORY_ROW.finditer(html[idx:idx + 30000]):
            date_str, url, file_id = row_match.group(1), row_match.group(2), row_match.group(3)
            try:
                dt = datetime.strptime(date_str, '%d.%m.%Y - %H:%M:%S')
            except ValueError:
                continue
            if dt >= cutoff and file_id not in history:
                history[file_id] = url
        return history

    def search(self, query: str, max_results: int = 100) -> List[_SearchResult]:
        params = {
            'sEcho': '1', 'iColumns': '2', 'sColumns': '',
            'iDisplayStart': '0', 'iDisplayLength': str(max_results),
            'sSearch': '', 'bRegex': 'false',
            'bSearchable_0': 'true', 'bSearchable_1': 'true',
            'iSortingCols': '0', 'filterText': query, 'filterType': 'videos',
        }
        try:
            fflog(f'query: {query!r}')
            resp = self.session.get(
                self.SEARCH_URL, params=params,
                headers={
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'Referer': self.BASE_LINK + '/search',
                },
                verify=False, timeout=15,
            )
            if const.sources.wrzucaj.debug:
                fflog(f'wrzucaj.2: search HTTP {resp.status_code} len={len(resp.text)}')
            if not resp.text.strip():
                return []
            results = []
            for row in resp.json().get('aaData', []):
                if not isinstance(row, (list, tuple)) or not row:
                    continue
                cell = row[0]
                url_match = self.RE_FILE_URL.search(cell)
                if not url_match:
                    continue
                file_size_mb = 0
                size_match = self.RE_FILESIZE.search(cell)
                if size_match:
                    file_size_mb = self._size_to_mb(float(size_match.group(1)), size_match.group(2))
                results.append({
                    'file_id': url_match.group(2),
                    'dl_page_url': url_match.group(1),
                    'filename': unescape(url_match.group(3)).strip(),
                    'file_size_mb': file_size_mb,
                })
            fflog(f'search: {len(results)} results')
            return results
        except Exception:
            fflog_exc()
            return []

    def get_file_details(self, dl_page_url: str) -> _FileDetails:
        result = {'cdn_url': None, 'filesize_mb': 0}
        try:
            resp = self.session.get(dl_page_url, verify=False, timeout=20)
            if const.sources.wrzucaj.debug:
                fflog(f'wrzucaj.2: dl_page HTTP {resp.status_code}')
            numeric_id_match = self.RE_NUMERIC_ID.search(resp.text)
            if not numeric_id_match:
                if const.sources.wrzucaj.debug:
                    fflog(f'wrzucaj.2: no numericId at {dl_page_url!r}')
                return result
            numeric_id = numeric_id_match.group(1)
            if const.sources.wrzucaj.debug:
                fflog(f'wrzucaj.2: numericId={numeric_id}')

            safe_referer = quote(dl_page_url, safe=':/?#[]@!$&\'()*+,;=%')
            resp2 = self.session.post(
                f'{self.ACCOUNT_URL}/ajax/file_details',
                data={'u': numeric_id, 'p': 'true'},
                headers={
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'Referer': safe_referer,
                },
                verify=False, timeout=20,
            )
            data = resp2.json()
            html = data.get('html', '')
            if const.sources.wrzucaj.debug:
                fflog(f"wrzucaj.2: file_details success={data.get('success')} html_len={len(html)}")

            cdn_match = self.RE_CDN_PLAYER.search(html)
            if cdn_match:
                result['cdn_url'] = cdn_match.group(1)
                if const.sources.wrzucaj.debug:
                    fflog(f"wrzucaj.2: CDN URL (player) = {result['cdn_url']!r}")
            else:
                dl_match = self.RE_CDN_DOWNLOAD.search(html)
                if dl_match:
                    result['cdn_url'] = dl_match.group(1)
                    if const.sources.wrzucaj.debug:
                        fflog(f"wrzucaj.2: CDN URL (download) = {result['cdn_url']!r}")
                else:
                    idx = html.find('openUrl')
                    ctx = html[max(0, idx - 20):idx + 200] if idx >= 0 else '(brak openUrl)'
                    if const.sources.wrzucaj.debug:
                        fflog(f'wrzucaj.2: no CDN URL in file_details, ctx={ctx!r}')

            filesize_match = self.RE_FILESIZE_DETAIL.search(html)
            if filesize_match:
                result['filesize_mb'] = self._size_to_mb(
                    float(filesize_match.group(1)), filesize_match.group(2)
                )
                if const.sources.wrzucaj.debug:
                    fflog(f"wrzucaj.2: filesize = {result['filesize_mb']} MB")

        except Exception:
            fflog_exc()
        return result


# ─── _WrzucajSource ──────────────────────────────────────────────────────────────

class _WrzucajSource:
    """Shared base for WrzucajVideo and WrzucajFiles.

    Session login is shared — a single cached login is reused by both providers.
    Credentials: wrzucaj.username / wrzucaj.password (shared by both providers).
    """

    PROVIDER: ClassVar[str] = ''

    has_sort_order: ClassVar[bool] = False
    has_color_identify2: ClassVar[bool] = False
    has_library_color_identify2: ClassVar[bool] = False
    use_premium_color: ClassVar[bool] = False

    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self._logged_in = False
        self._tier = 'free'
        self.debug = const.sources.wrzucaj.debug

    @single_call
    def init(self):
        self.username = settings.getString('wrzucaj.username')
        self.password = settings.getString('wrzucaj.password')
        self._setup_provider()

    # ── helpers ────────────────────────────────────────────────────────────

    def _setup_provider(self):
        """Override in subclass to initialize provider-specific HTTP session."""
        pass

    def _apply_cookies(self, cookie_str: str) -> None:
        """Apply cached cookies to the HTTP session. Override in subclass."""
        pass

    def _do_login(self) -> Tuple[bool, str, str]:
        """Perform fresh login. Return (ok, tier, cookie_str). Override in subclass."""
        return False, 'free', ''

    def _session_login(self) -> bool:
        """Shared session login with unified cache key.

        Both providers use the same cache entry, so a single login attempt
        benefits whichever provider runs first.
        """
        if self._logged_in:
            return True
        if not self.username or not self.password:
            fflog(f'{self.PROVIDER}: no credentials')
            return False
        try:
            pass_hash = hashlib.md5(self.password.encode()).hexdigest()[:8]
            cache_key = f'{_CACHE_KEY_PREFIX}_{self.username}_{pass_hash}'

            cached = cache.cache_get(cache_key, control.providercacheFile)
            if cached is not None:
                age = int(time.time()) - int(cached.get('date', 0))
                if age < COOKIE_EXPIRATION_HOURS * 3600:
                    try:
                        session_data = json.loads(cached.get('value', '{}'))
                        cookie_str = session_data.get('cookies', '')
                        tier = session_data.get('tier', 'free')
                    except Exception:
                        cookie_str, tier = '', 'free'
                    if cookie_str:
                        self._apply_cookies(cookie_str)
                        self._logged_in = True
                        self._tier = tier
                        if self.debug:
                            fflog(f'{self.PROVIDER}: session from cache, tier={tier}')
                        return True

            ok, tier, cookie_str = self._do_login()
            if ok:
                cache.cache_insert(
                    cache_key,
                    json.dumps({'cookies': cookie_str, 'tier': tier}),
                    control.providercacheFile,
                )
                self._logged_in = True
                self._tier = tier
                fflog(f'{self.PROVIDER}: logged in, tier={tier}')
                return True
            fflog(f'{self.PROVIDER}: login failed')
        except Exception:
            fflog_exc()
        return False

    @staticmethod
    def _local_if_different(title: str, local: str) -> str:
        return local if (local and local.lower() != title.lower()) else ''

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: list[SourceTitleAlias], year: str) -> dict[str, Any]:
        self.init()
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases or [], year))

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_qs_single(url_str: str) -> Dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(url_str).query).items()}

    def _url_with_headers(self, cdn_url: str) -> str:
        return f'{cdn_url}|User-Agent={FF_UA}&Referer={WrzucajApiClient.BASE_LINK}/'


# ─── WrzucajVideo ────────────────────────────────────────────────────────────────

class WrzucajVideo(_WrzucajSource):
    """Searches wrzucaj.pl/videos and returns direct CDN URLs from the watch page."""

    PROVIDER: ClassVar[str] = 'wrzucaj'

    has_sort_order: ClassVar[bool] = True
    has_color_identify2: ClassVar[bool] = True
    use_premium_color: ClassVar[bool] = True

    RE_VIDEO_CARD = re.compile(r'href="(/watch/(\d+)/([^"]+))"')
    RE_SOURCE_TAG = re.compile(
        r'<source\s[^>]*src="([^"]+)"[^>]*size="(\d+)"[^>]*label="([^"]+)"',
        re.DOTALL,
    )
    _RE_TRACK_TAG: ClassVar = re.compile(r'<track\b[^>]*/?>(?:</track>)?', re.IGNORECASE)
    _RE_TRACK_KIND: ClassVar = re.compile(r'kind=["\'](subtitles|captions)["\']', re.IGNORECASE)
    _RE_TRACK_SRC: ClassVar = re.compile(r'\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
    _RE_SUB_HASH: ClassVar = re.compile(r'/(?:subs|play)/([a-f0-9]{40,})/')

    def __init__(self):
        super().__init__()
        self._subtitle_map: Dict[str, List[str]] = {}

    _SEARCH_URL: ClassVar[str] = WrzucajApiClient.BASE_LINK + '/videos?search={}'

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> str:
        self.init()
        orig = self._local_if_different(title, localtitle)
        cc = ','.join(source_utils.extract_alias_country_codes(aliases, title))
        return '{}/videos?search={}&_title={}&_year={}&_local={}&_cc={}'.format(
            WrzucajApiClient.BASE_LINK, quote_plus(title), quote_plus(title), year or '', quote_plus(orig), cc)

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> str:
        self.init()
        show = ShowData(**url)
        orig = self._local_if_different(show.title, show.local_title)
        cc = ','.join(source_utils.extract_alias_country_codes(show.aliases, show.title))
        return '{}/videos?search={}&_title={}&_year={}&_season={}&_episode={}&_local={}&_cc={}'.format(
            WrzucajApiClient.BASE_LINK, quote_plus(show.title), quote_plus(show.title), show.year or '',
            int(season), int(episode), quote_plus(orig), cc)

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        self.init()
        try:
            if isinstance(url, tuple):
                url = url[0]
            if not url:
                return []

            self._session_login()
            if not self._logged_in:
                fflog('wrzucaj: not logged in')
                return []
            tier = self._tier

            params = self._parse_qs_single(str(url))
            query = params.get('search', '')
            item_title = unquote_plus(params.get('_title', ''))
            item_orig = unquote_plus(params.get('_local', ''))
            item_year = params.get('_year') or None
            want_season = int(params['_season']) if '_season' in params else None
            want_episode = int(params['_episode']) if '_episode' in params else None
            expected_cc = set(filter(None, params.get('_cc', '').split(','))) & source_utils.FRANCHISE_CC

            if not query:
                return []

            alias_cleans = source_utils.build_alias_cleans(filter(None, (item_title, item_orig)))

            queries_to_try = [query]
            if item_orig and item_orig.lower() != query.lower():
                queries_to_try.append(item_orig)
            if self.debug:
                fflog(f'wrzucaj: queries={queries_to_try}')

            # Pobierz wyniki wszystkich zapytań równolegle
            search_results: Dict[str, List] = {}
            with ThreadPoolExecutor(max_workers=len(queries_to_try)) as executor:
                futures = {executor.submit(self._search, q): q for q in queries_to_try}
                for future in as_completed(futures):
                    search_results[futures[future]] = future.result()

            results = []
            seen_ids: set = set()
            for search_query in queries_to_try:
                for card in search_results.get(search_query, []):
                    if card['file_id'] in seen_ids:
                        continue
                    parsed = source_utils.parse_filename_title(card['filename'])

                    if alias_cleans and not source_utils.match_parsed_title(parsed['title'], alias_cleans):
                        if self.debug:
                            fflog(f'skip (title mismatch): {card["filename"]}')
                        continue

                    if want_season is not None and expected_cc:
                        conflict_cc = source_utils.check_parsed_title_cc(parsed['title'], item_title, expected_cc)
                        if conflict_cc:
                            if self.debug:
                                fflog(f'skip (country variant {conflict_cc}): {card["filename"]}')
                            continue

                    if item_year and parsed['year'] and abs(int(item_year) - parsed['year']) > 1:
                        if self.debug:
                            fflog(f'skip (year/episode mismatch): {card["filename"]}')
                        continue

                    if want_season is None and source_utils.is_episode_filename(card['filename']):
                        if item_year and re.search(rf'\b{re.escape(str(item_year))}\b', card['filename']):
                            pass  # correct year present — episode marker is part of title, not episode designator
                        else:
                            if self.debug:
                                fflog(f'skip (episode marker in movie): {card["filename"]}')
                            continue

                    if want_season is not None and (parsed['season'] != want_season or parsed['episode'] != want_episode):
                        if self.debug:
                            fflog(f'skip (year/episode mismatch): {card["filename"]}')
                        continue

                    if self.debug:
                        fflog(f'match: {card["filename"]}')
                    seen_ids.add(card['file_id'])
                    results.extend(self._expand(card['watch_url'], card['file_id'], tier, card['filename']))

            fflog(f'sources: {len(results)}')
            return results
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> str:
        self.init()
        try:
            cdn_match = re.search(r'/play/([a-f0-9]{40,})/', str(url))
            if cdn_match:
                subs = self._subtitle_map.get(cdn_match.group(1))
                if subs:
                    control.window().setProperty('source.subtitles', json.dumps(subs))
            return url
        except Exception:
            fflog_exc()
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _setup_provider(self):
        self.client = WrzucajApiClient()

    def _apply_cookies(self, cookie_str: str) -> None:
        self.client.set_cookies(cookie_str)

    def _do_login(self) -> Tuple[bool, str, str]:
        if self.client.login(self.username, self.password):
            tier = self.client.check_premium()
            return True, tier, self.client.get_cookies()
        return False, 'free', ''

    def _search(self, query: str) -> list[_VideoCard]:
        try:
            fflog(f'query: {query!r}')
            clean = re.sub(r"[:'\"]+", '', query).strip()
            page = self.client.session.get(
                self._SEARCH_URL.format(quote_plus(clean)),
                verify=False, timeout=15,
            ).text
            results = [
                {
                    'file_id': file_id,
                    'watch_url': WrzucajApiClient.BASE_LINK + path,
                    'filename': unescape(filename).strip(),
                }
                for path, file_id, filename in self.RE_VIDEO_CARD.findall(page)
            ]
            fflog(f'search: {len(results)} results')
            return results
        except Exception:
            fflog_exc()
            return []

    def _expand(self, watch_url: str, file_id: str, tier: str, filename: str = '') -> list[SourceItem]:
        try:
            page = self.client.session.get(watch_url, verify=False, timeout=20).text
        except Exception:
            fflog_exc()
            return []

        tag_sources = self.RE_SOURCE_TAG.findall(page)
        if not tag_sources:
            fflog(f'wrzucaj: no <source> tags for file_id={file_id}')
            return []

        sub_urls = []
        for track_tag in self._RE_TRACK_TAG.findall(page):
            if not self._RE_TRACK_KIND.search(track_tag):
                continue
            src_match = self._RE_TRACK_SRC.search(track_tag)
            if src_match:
                sub_urls.append(src_match.group(1))
        if sub_urls:
            hash_match = self._RE_SUB_HASH.search(sub_urls[0])
            if hash_match:
                self._subtitle_map[hash_match.group(1)] = sub_urls

        results = []
        for cdn_url, size_str, label in tag_sources:
            size = int(size_str) if size_str.isdigit() else 0
            quality = source_utils.quality_from_resolution(height=size)

            if quality == '4K':
                if tier != 'premium':
                    continue
                src_premium = True
            else:
                src_premium = False

            language, audio = source_utils.parse_label_language(label, filename, has_subs=bool(sub_urls))

            audio_info = (audio + ' napisy').strip() if sub_urls and 'napisy' not in audio.lower() else audio
            info = ' | '.join(part for part in (audio_info, 'Premium' if src_premium else '') if part)
            if self.debug:
                fflog(f'wrzucaj: [{label}] {size}p → {quality} tier={tier} info={info!r}')
            results.append({
                'source': 'Oglądaj',
                'url': self._url_with_headers(cdn_url),
                'quality': quality,
                'language': language,
                'info': info,
                'filename': '',
                'direct': True,
                'debridonly': False,
                'premium': src_premium,
            })
        return results

# ─── WrzucajFiles ────────────────────────────────────────────────────────────────


class WrzucajFiles(_WrzucajSource):
    """Searches file catalog via /ajax/search and resolves via account/ajax/file_details."""

    PROVIDER: ClassVar[str] = 'wrzucaj.2'

    has_sort_order: ClassVar[bool] = True
    has_color_identify2: ClassVar[bool] = True
    has_library_color_identify2: ClassVar[bool] = True
    use_premium_color: ClassVar[bool] = True

    def __init__(self):
        super().__init__()
        self._size_cache: Dict[str, int] = {}
        self.remaining_limit_mb: int = -1
        self._history_cache: Dict[str, str] = {}

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> str:
        self.init()
        local = self._local_if_different(title, localtitle)
        cc = ','.join(source_utils.extract_alias_country_codes(aliases, title))
        return 'wrzucaj.2:movie?_title={}&_year={}&_local={}&_cc={}'.format(
            quote_plus(title), year or '', quote_plus(local), cc)

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> str:
        self.init()
        show = ShowData(**url)
        local = self._local_if_different(show.title, show.local_title)
        cc = ','.join(source_utils.extract_alias_country_codes(show.aliases, show.title))
        return 'wrzucaj.2:episode?_title={}&_year={}&_season={}&_episode={}&_local={}&_cc={}'.format(
            quote_plus(show.title), show.year or '',
            int(season), int(episode), quote_plus(local), cc)

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        self.init()
        try:
            if isinstance(url, tuple):
                url = url[0]
            if not url:
                return []

            self._session_login()
            if not self._logged_in:
                fflog('wrzucaj.2: not logged in')
                return []
            tier = self._tier
            self._fetch_account_info()

            params = self._parse_qs_single(str(url))
            item_title = unquote_plus(params.get('_title', ''))
            item_local = unquote_plus(params.get('_local', ''))
            item_year = params.get('_year') or None
            want_season = int(params['_season']) if '_season' in params else None
            want_episode = int(params['_episode']) if '_episode' in params else None
            expected_cc = set(filter(None, params.get('_cc', '').split(','))) & source_utils.FRANCHISE_CC

            if not item_title:
                return []
            if self.debug:
                fflog(
                    f'wrzucaj.2: title={item_title!r} local={item_local!r} year={item_year} s={want_season} e={want_episode} tier={tier}')

            alias_cleans = source_utils.build_alias_cleans(filter(None, (item_title, item_local)))

            queries = [self._to_search_query(item_title)]
            if item_local and item_local.lower() != item_title.lower():
                queries.append(self._to_search_query(item_local))
            if self.debug:
                fflog(f'wrzucaj.2: queries={queries}')

            # Pobierz wyniki wszystkich zapytań równolegle
            search_results: Dict[str, List] = {}
            with ThreadPoolExecutor(max_workers=len(queries)) as executor:
                futures = {executor.submit(self.client.search, q): q for q in queries}
                for future in as_completed(futures):
                    search_results[futures[future]] = future.result()

            results: list[SourceItem] = []
            seen_ids: set = set()
            for query in queries:
                for card in search_results.get(query, []):
                    if card['file_id'] in seen_ids:
                        continue
                    parsed = source_utils.parse_filename_title(card['filename'])

                    if alias_cleans and not source_utils.match_parsed_title(parsed['title'], alias_cleans):
                        if self.debug:
                            fflog(f'skip (title mismatch): {card["filename"]}')
                        continue

                    if want_season is not None and expected_cc:
                        conflict_cc = source_utils.check_parsed_title_cc(parsed['title'], item_title, expected_cc)
                        if conflict_cc:
                            if self.debug:
                                fflog(f'skip (country variant {conflict_cc}): {card["filename"]}')
                            continue

                    if item_year and parsed['year'] and abs(int(item_year) - parsed['year']) > 1:
                        if self.debug:
                            fflog(f'skip (year/episode mismatch): {card["filename"]}')
                        continue

                    if want_season is None and source_utils.is_episode_filename(card['filename']):
                        if item_year and re.search(rf'\b{re.escape(str(item_year))}\b', card['filename']):
                            pass  # correct year present — episode marker is part of title, not episode designator
                        else:
                            if self.debug:
                                fflog(f'skip (episode marker in movie): {card["filename"]}')
                            continue

                    if want_season is not None and (
                        parsed['season'] != want_season or parsed['episode'] != want_episode
                    ):
                        if self.debug:
                            fflog(f'skip (year/episode mismatch): {card["filename"]}')
                        continue

                    if self.debug:
                        fflog(f'match: {card["filename"]}')
                    seen_ids.add(card['file_id'])
                    self._size_cache[card['file_id']] = card.get('file_size_mb', 0)
                    results.extend(self._build_sources(
                        card['dl_page_url'], card['file_id'], tier,
                        card['filename'], card.get('file_size_mb', 0),
                    ))

            fflog(f'sources: {len(results)}')
            return results
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        self.init()
        try:
            url_str = str(url)
            if not url_str.startswith('wrzucaj.2:dl?'):
                return url_str

            self._session_login()
            if not self._logged_in:
                fflog('wrzucaj.2 resolve: no session')
                return None

            params = self._parse_qs_single(url_str)
            dl_page_url = unquote_plus(params.get('url', ''))
            file_id = params.get('fid', '')
            quality = params.get('q', '')
            is_on_account = params.get('oa', '0') == '1'
            fflog(f'wrzucaj.2 resolve: {dl_page_url!r} q={quality} on_account={is_on_account}')

            if not dl_page_url:
                return None

            if not settings.getBool('wrzucaj.2.auto') and not is_on_account:
                self._fetch_account_info()
                filename_raw = unquote_plus(dl_page_url).split('/')[-1]
                fn_display = (
                    _RE_EXT_STRIP.sub('', filename_raw)
                    .replace('.', ' ').replace('_', ' ').strip()
                )
                if not source_utils.confirm_transfer_dialog(
                    filename=fn_display,
                    charge=self._format_size(self._size_cache.get(file_id, 0)),
                    remaining=self._format_size(self.remaining_limit_mb),
                ):
                    return None

            details = self.client.get_file_details(dl_page_url)
            cdn_url = details.get('cdn_url')
            if not cdn_url:
                fflog(f'wrzucaj.2 resolve: no CDN URL for {dl_page_url!r}')
                return None

            if details.get('filesize_mb'):
                self._size_cache[file_id] = details['filesize_mb']

            fflog(f'wrzucaj.2 resolve: CDN URL = {cdn_url!r}')
            return self._url_with_headers(cdn_url)
        except Exception:
            fflog_exc()
        return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _setup_provider(self):
        self.client = WrzucajApiClient()

    def _apply_cookies(self, cookie_str: str) -> None:
        self.client.set_cookies(cookie_str)

    def _do_login(self) -> Tuple[bool, str, str]:
        if self.client.login(self.username, self.password):
            tier = self.client.check_premium()
            return True, tier, self.client.get_cookies()
        return False, 'free', ''

    def _fetch_account_info(self) -> None:
        try:
            html = self.client.get_account_edit()
            transfer_gb = self.client.parse_transfer_gb(html)
            self.remaining_limit_mb = int(transfer_gb * 1024)
            self._history_cache = self.client.parse_download_history(html)
            if self.debug:
                fflog(f'wrzucaj.2: transfer={transfer_gb:.2f} GB history={len(self._history_cache)} files')
        except Exception:
            fflog_exc()

    def check_and_add_on_account_sources(self, sources, ffitem, source_name: str):
        """Checks which cached sources are on account and marks them accordingly."""
        try:
            self.init()
            self._session_login()
            if not self._logged_in:
                return
            self._fetch_account_info()

            for src in sources:
                url_str = str(getattr(src, 'url', '') or src.get('url', ''))
                if 'wrzucaj.2:dl?' not in url_str:
                    continue
                file_id = self._parse_qs_single(url_str).get('fid', '')
                if not file_id or file_id not in self._history_cache:
                    continue
                src.update({
                    'on_account': True,
                    'on_account_link': self._history_cache[file_id],
                    'on_account_expires': '',
                })
                if '&oa=1' not in url_str:
                    src.update({'url': url_str + '&oa=1'})
                fflog(f'wrzucaj.2: on_account (cache) fid={file_id}')
        except Exception:
            fflog_exc('wrzucaj.2: check_and_add_on_account error')

    @staticmethod
    def _format_size(size_mb: int) -> str:
        if size_mb <= 0:
            return ''
        return f'{size_mb / 1024:.2f} GB' if size_mb >= 1024 else f'{size_mb} MB'

    def _build_sources(self, dl_page_url: str, file_id: str, tier: str,
                       filename: str, file_size_mb: int = 0) -> list[dict[str, Any]]:
        quality, language, audio = source_utils.parse_source_quality_lang(filename)

        if quality == '720p':
            if tier == 'none':
                return []
        elif quality in ('1080p', '4K'):
            if tier != 'premium':
                return []

        on_account = file_id in self._history_cache
        on_account_link = self._history_cache.get(file_id, '')
        no_transfer = (
            self.remaining_limit_mb >= 0
            and file_size_mb > 0
            and file_size_mb > self.remaining_limit_mb
            and not on_account
        )

        info = ' | '.join(filter(None, (audio,)))
        internal_url = 'wrzucaj.2:dl?url={}&fid={}&q={}{}'.format(
            quote_plus(dl_page_url), file_id, quality,
            '&oa=1' if on_account else '',
        )
        return [{
            'source': 'Pobierz',
            'url': internal_url,
            'quality': quality,
            'language': language,
            'info': info,
            'size': self._format_size(file_size_mb),
            'filename': filename,
            'direct': False,
            'debridonly': False,
            'premium': True,  # all files marked as premium
            'no_transfer': no_transfer,
            'on_account': on_account,
            'on_account_link': on_account_link,
            'on_account_expires': '',
        }]

    @staticmethod
    def _to_search_query(text: str) -> str:
        clean = re.sub(r"[:'\"]+", '', text).strip()
        return re.sub(r'\s+', '.', clean.lower())


def register(sources: List[SourceModule], group: str) -> None:
    sources.append(SourceModule(name=WrzucajVideo.PROVIDER, provider=WrzucajVideo(), group=group))
    if settings.getBool(f'provider.{WrzucajVideo.PROVIDER}') or const.dev.sources.force_all_sources:
        sources.append(SourceModule(name=WrzucajFiles.PROVIDER, provider=WrzucajFiles(), group=group))

