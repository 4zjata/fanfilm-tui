# -*- coding: utf-8 -*-
"""
FanFilm - źródło: rapideo/nopremium/twojlimit
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, ClassVar, List, Tuple
from typing_extensions import TypedDict, NotRequired
import re

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
import hashlib
import time
from datetime import datetime
from threading import Thread
import xbmcgui
from lib.ff import requests
from lib.sources import SourceModule, Source
from lib.ff import cache, cleantitle, control, source_utils
from lib.ff.source_utils import ShowData, show_data_asdict, ShowDataDict
from lib.ff.settings import settings
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem
from const import const

# Constants
SEARCH_RESULTS_LIMIT = 100
AUTHTOKEN_EXPIRATION_HOURS = 12


# ─── TypedDicts ──────────────────────────────────────────────────────────────────

class _Translation(TypedDict, total=False):
    description: str
    language: str


class _FileEntry(TypedDict):
    """File entry from rapideo/nopremium/twojlimit API (search result or account file)."""
    url: str
    hosting: str
    filesize: str
    filename: str
    filename_full: NotRequired[str]
    filename_long: NotRequired[str]
    hash: NotRequired[str]
    translation: NotRequired[_Translation]
    on_account: NotRequired[bool]
    on_account_expire_date: NotRequired[str]
    expire_date: NotRequired[str]
    download_url: NotRequired[str]
    chargeuser: NotRequired[str]


class _SearchResponse(TypedDict, total=False):
    """Top-level response from search API."""
    search_result: list[_FileEntry]
    error: bool
    message: str


class _AccountInfo(TypedDict, total=False):
    """Account info from API."""
    transfer_left_gb: str
    premium_left_gb: str
    bonus_left_gb: str


# ─── APIClient ───────────────────────────────────────────────────────────────────

class APIClient:
    """Handles all API communication with rapideo/nopremium/twojlimit services"""

    def __init__(self, provider_name: str, base_url: str, req_session: Any) -> None:
        self.provider = provider_name
        self.base_url = base_url
        self.req = req_session
        self.authtoken = None

        # API endpoints
        self.login_url = f"{base_url}/api/rest/login"
        self.search_url = f"{base_url}/api/rest/search"
        self.check_url = f"{base_url}/api/rest/files/check"
        self.files_url = f"{base_url}/api/rest/files/get"
        self.download_url = f"{base_url}/api/rest/files/download"
        self.account_url = f"{base_url}/api/rest/account"

    def login(self, username: str, password: str) -> Tuple[bool, Optional[str]]:
        """Login to service and get auth token"""
        response = self.req.post(
            self.login_url,
            data={'login': username, 'password': password},
        ).json()

        if authtoken := response.get('authtoken'):
            self.authtoken = authtoken
            return True, None

        if response.get('error') and (msg := response.get('message')):
            return False, msg

        return False, 'Unknown login error'

    def get_account_info(self) -> Optional[_AccountInfo]:
        """Get account information including transfer limits"""
        if not self.authtoken:
            return None

        try:
            data = {'authtoken': self.authtoken, 'mode': 'ff'}
            account_info = self.req.post(self.account_url, data=data).json()

            if account := account_info.get('account'):
                return account
        except Exception:
            fflog_exc()
            raise

        return None

    def search_files(self, keyword: str) -> Tuple[Optional[_SearchResponse], Optional[str]]:
        """Search for files"""
        data = {
            'authtoken': self.authtoken,
            'keyword': keyword,
            'display': SEARCH_RESULTS_LIMIT,
            'video': True,
            'mode': 'ff',
        }

        search_response = self.req.post(self.search_url, data=data).json()

        if search_response.get('error') and (msg := search_response.get('message')):
            return None, msg

        if search := search_response.get('search'):
            return search, None

        return None, None

    def get_user_files(self) -> List[_FileEntry]:
        """Get files from user account"""
        data = {'authtoken': self.authtoken, 'video': True, 'mode': 'ff'}
        files_response = self.req.post(self.files_url, data=data).json()

        if files := files_response.get('files'):
            return files

        return []

    def get_file_by_url(self, url: str) -> Optional[_FileEntry]:
        """Get single file by URL"""
        data = {'authtoken': self.authtoken, 'url': url, 'mode': 'ff'}
        get_files = self.req.post(self.files_url, data=data).json()

        if files := get_files.get('files'):
            for file in files:
                if file.get('url') == url and file.get('download_url'):
                    return file

        return None

    def check_file(self, url: str) -> Tuple[Optional[_FileEntry], Optional[str]]:
        """Check if file can be downloaded"""
        data = {'authtoken': self.authtoken, 'url': url, 'mode': 'ff'}
        check_response = self.req.post(self.check_url, data=data).json()

        if check_response.get('error') and (msg := check_response.get('message')):
            return None, msg

        if file := check_response.get('file'):
            if file.get('error') and (msg := file.get('message')):
                return None, msg
            return file, None

        return None, 'Unknown error'

    def download_file(self, file_hash: str) -> Tuple[Optional[str], Optional[str]]:
        """Add file to account for download"""
        data = {'authtoken': self.authtoken, 'hash': file_hash, 'mode': 'ff'}
        response = self.req.post(self.download_url, data=data).json()

        if response.get('error') and (msg := response.get('message')):
            return None, msg

        if file := response.get('file'):
            if url := file.get('url'):
                return url, None
            if file.get('error') and (msg := file.get('message')):
                return None, msg

        return None, 'Unknown error'


# ─── _source ─────────────────────────────────────────────────────────────────────

class _source:
    """Base scraper for sites like rapideo, nopremium, twojlimit"""

    PROVIDER: ClassVar[str]
    URL: ClassVar[str]
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ['pl']

    has_sort_order: bool = True
    has_color_identify2: bool = True
    has_library_color_identify2: bool = True
    use_premium_color: bool = True

    ffitem: FFItem  # Assigned dynamically by FanFilm

    def __init__(self):
        self.domains = [f"{self.PROVIDER}.pl"]
        session = requests.Session()
        session.headers.update({
            'Connection': 'close',
            'Keep-Alive': 'timeout=0',
        })
        self.req = session
        self.user_name = settings.getString(f"{self.PROVIDER}.username")
        self.user_pass = settings.getString(f"{self.PROVIDER}.password")
        pass_hash = hashlib.md5(self.user_pass.encode()).hexdigest()[:8]
        self._auth_cache_key = f"{self.PROVIDER}_authtoken_{self.user_name}_{pass_hash}"
        self.titles = []
        self.remaining_limit_bytes = -1
        self.debug = const.sources.rapideo.debug
        self._relevant_franchises = {}
        self._franchise_cache_key = None

        # Initialize API client
        self.api = APIClient(self.PROVIDER, self.URL, self.req)

    def login(self) -> bool:
        """Login to service"""
        get_auth = cache.cache_get(self._auth_cache_key, control.providercacheFile)
        if get_auth is not None and get_auth.get('value'):
            age = int(time.time()) - int(get_auth.get('date', 0))
            if age < AUTHTOKEN_EXPIRATION_HOURS * 3600:
                self.api.authtoken = get_auth['value']
                return True
            fflog(f'{self.PROVIDER}: authtoken expired ({age//3600}h), re-login')

        if self.user_name and self.user_pass:
            success, error_msg = self.api.login(self.user_name, self.user_pass)
            if success:
                cache.cache_insert(self._auth_cache_key, self.api.authtoken, control.providercacheFile)
                return True
            else:
                xbmcgui.Dialog().notification(f"{self.PROVIDER} - Error",
                               error_msg or 'Login failed', xbmcgui.NOTIFICATION_ERROR)
        else:
            xbmcgui.Dialog().notification(f"{self.PROVIDER} - Error",
                           'No login credentials - check settings', xbmcgui.NOTIFICATION_ERROR)

        return False

    def get_account_info(self) -> None:
        """Get account info and calculate transfer limits"""
        try:
            account = self.api.get_account_info()
            if account:
                def _to_float(key):
                    try:
                        return float(account.get(key, 0))
                    except ValueError:
                        return 0.0
                total_gb = _to_float('transfer_left_gb') + _to_float('premium_left_gb') + _to_float('bonus_left_gb')
                self.remaining_limit_bytes = total_gb * 1024 * 1024 * 1024
        except Exception:
            self.remaining_limit_bytes = float('inf')

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> list[_FileEntry] | None:
        """Search for movie"""
        return self._search(title, localtitle, year, aliases=aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict | None:
        """Helper method before searching for episode"""
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, year))

    def episode(self, url: ShowDataDict | None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> list[_FileEntry] | None:
        """Search for episode"""
        data = ShowData(**url)
        return self._search(
            data.title,
            data.local_title,
            aliases=data.aliases,
            year=data.year,
            season=season,
            episode=episode,
            premiered=premiered
        )

    def sources(self, rows: list[_FileEntry] | None, hostDict: List[str], hostprDict: List[str]) -> 'Optional[List[SourceItem]]':
        """Pass sources to sources.py"""
        if not rows:
            fflog('no sources')
            return None

        rows.sort(key=lambda k: bool(k.get('on_account')), reverse=True)
        sources = []
        for row in rows:
            try:
                size = row['filesize']
                hosting = row['hosting']
                filename = row.get('filename_long') or row.get('filename_full')
                api_lang = row.get('translation', {}).get('language', '')
                api_info = row.get('translation', {}).get('description', '').rstrip(' |')
                api_quality = (row.get('more_info') or {}).get('q', '')
                fn_quality, fn_lang, fn_info = source_utils.parse_source_quality_lang(filename or '')
                # Prefer API-provided quality (catches 4K reliably; filenames often lack the marker)
                quality = source_utils.get_quality(api_quality) if api_quality else fn_quality
                language = fn_lang or api_lang  # filename-based detection takes priority; API as fallback
                info = fn_info if fn_info else api_info

                # Check transfer availability — prefer exact byte count from API
                try:
                    source_size_bytes = int(row.get('filesize_bytes') or 0)
                except (ValueError, TypeError):
                    source_size_bytes = 0
                if not source_size_bytes:
                    source_size_bytes = source_utils.convert_size_to_bytes(size)
                no_transfer = (
                    const.sources.premium.no_transfer
                    and self.remaining_limit_bytes >= 0
                    and source_size_bytes > 0
                    and source_size_bytes > self.remaining_limit_bytes
                )

                # Format expiration date
                expire_date_str = row.get('on_account_expire_date') or row.get('expire_date')
                if expire_date_str:
                    try:
                        expire_date_formatted = f"{expire_date_str[:5]} {expire_date_str[11:16]}"
                    except Exception:
                        expire_date_formatted = expire_date_str
                else:
                    expire_date_formatted = ''

                sources.append({
                    'source': hosting,
                    'quality': quality,
                    'language': language,
                    'url': row['url'],
                    'info': info,
                    'size': size,
                    'filesize_bytes': source_size_bytes,
                    'direct': True,
                    'debridonly': False,
                    'filename': filename,
                    'on_account': row['on_account'],
                    'on_account_expires': expire_date_formatted,
                    'premium': True,
                    'no_transfer': no_transfer,
                })
            except (KeyError, NameError) as err:
                fflog(repr(err) + f' {row=}')
                continue

        duplicates = len(rows) - len(sources)
        if duplicates > 0:
            fflog(f'duplicates skipped: {duplicates}')
        fflog(f'sources: {len(sources)}')
        return sources

    def resolve(self, url: str) -> Optional[str]:
        """Resolve download link"""
        try:
            self.login()

            # Check if file is already on account
            existing_file = self.api.get_file_by_url(url)
            if existing_file and (dl_url := existing_file.get('download_url')):
                return str(dl_url)

            auto = settings.getBool(f"auto{self.PROVIDER}")

            # Check if file can be "purchased"
            file_info, error_msg = self.api.check_file(url)

            if error_msg:
                cache.cache_insert(self._auth_cache_key, '', control.providercacheFile)
                raise Exception('Error', error_msg)

            if not file_info:
                raise Exception('Error', 'Could not retrieve file information')

            if 'filesize' in file_info and 'filename' in file_info:
                if not auto:
                    self.get_account_info()
                    confirmed = source_utils.confirm_transfer_dialog(
                        filename=file_info['filename_full'],
                        charge=source_utils.convert_size(file_info['chargeuser']),
                        remaining=source_utils.convert_size(self.remaining_limit_bytes),
                    )
                    if not confirmed:
                        raise Exception('Information', 'Download cancelled')

            # Attempt to add file to account
            download_url, error_msg = self.api.download_file(file_info['hash'])

            if error_msg:
                cache.cache_insert(self._auth_cache_key, '', control.providercacheFile)
                raise Exception('Error', error_msg)

            if not download_url:
                raise Exception('Error', 'Could not retrieve download URL')

            return str(download_url)

        except Exception as err:
            if len(err.args) >= 2:
                error, message = err.args[0], err.args[1]
            elif len(err.args) == 1:
                error, message = 'Error', str(err.args[0])
            else:
                error, message = 'Error', str(err)
            xbmcgui.Dialog().notification(f'{self.PROVIDER} - {error}', message, xbmcgui.NOTIFICATION_ERROR)
            return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _prepare_aliases(self, aliases: list[SourceTitleAlias], year: str) -> List[str]:
        return source_utils.prepare_alias_search_list(aliases, year)

    def _prepare_search_titles(self, title: str, localtitle: str, aliases: list[SourceTitleAlias] | None, year: str) -> List[str]:
        """Prepare all titles for searching and filtering"""
        year_str = str(year)

        # Prepare aliases
        prepared_aliases = self._prepare_aliases(aliases, year_str) if aliases else []

        # Base titles
        titles = [title, localtitle]
        self.titles = titles + prepared_aliases
        self.titles = list(filter(None, self.titles))
        self.titles = list(dict.fromkeys(self.titles))

        # Normalize for search; drop apostrophes so "Clancy's" → "Clancys", not "Clancy s"
        search_titles = [cleantitle.normalize(cleantitle.getsearch(t.replace("'", "").replace("’", ""), preserve=(':')))
                         for t in titles]
        search_titles = [re.sub(r' ?(\d)/', r' \1 ', t) for t in search_titles]  # Handle fractions
        search_titles = [t.replace('&', 'and') for t in search_titles]  # "Dungeons & Dragons"
        search_titles = list(filter(None, search_titles))
        search_titles = list(dict.fromkeys(search_titles))

        # Add at most one English alias as an extra query — catches files indexed
        # under a shorter title (e.g. "Jack Ryan: Ghost War" without "Tom Clancy's").
        existing = {t.lower() for t in search_titles}
        for alias in (aliases or []):
            if alias.get('country') != 'us':
                continue
            alias_val = alias.get('title') or alias.get('originalname') or ''
            if not alias_val:
                continue
            cleaned = cleantitle.normalize(cleantitle.getsearch(
                alias_val.replace("'", "").replace("’", ""), preserve=(':')))
            cleaned = cleaned.replace('&', 'and').strip()
            if cleaned and cleaned.lower() not in existing:
                search_titles.append(cleaned)
                break

        # Add to filtering titles
        self.titles = search_titles + self.titles
        self.titles = list(dict.fromkeys(self.titles))

        self.titles = source_utils.add_colon_reversed_titles(self.titles)

        return search_titles

    def _build_episode_code(self, season: str, episode: str, with_wildcard: bool = False) -> str:
        if not season or not episode:
            return ''
        return source_utils.format_episode_key(season, episode, with_wildcard=with_wildcard)

    def _fetch_search_results(self, query: str) -> Optional[_SearchResponse]:
        """Fetch search results from API"""
        if query in getattr(self, '_tried_queries', set()):
            return None
        if hasattr(self, '_tried_queries'):
            self._tried_queries.add(query)

        fflog(f'query: {query!r}')

        search_results, error_msg = self.api.search_files(query)

        if error_msg:
            cache.cache_insert(self._auth_cache_key, '', control.providercacheFile)
            fflog('search error: ' + error_msg)
            return None

        if search_results:
            fflog(f"search: {len(search_results.get('search_result', []))} results")
            return search_results

        return None

    def _fetch_account_files(self, force: bool = False) -> dict[str, _FileEntry]:
        """Fetch files from user account"""
        account_files = self.api.get_user_files()
        account_files_map = {}
        for f in account_files:
            if url := f.get('url'):
                f['on_account'] = True
                account_files_map[url] = f

        return account_files_map

    def _combine_search_and_account_results(self, search_results: _SearchResponse, account_files_map: dict[str, _FileEntry]) -> list[_FileEntry]:
        """Combine search results with account files.

        Primary match: URL. Fallback: cleaned filename + filesize_bytes
        (account API injects '<br />' tags into filenames, so we strip HTML
        before comparing — catches files added with a different URL hash).
        """
        combined_results = []

        # Build (clean_filename, filesize_bytes) index for fuzzy fallback
        fn_size_index: dict[tuple, str] = {}
        for url, account_file in account_files_map.items():
            raw_fn = account_file.get('filename_long') or account_file.get('filename') or ''
            clean_fn = re.sub(r'<[^>]+>', '', raw_fn).lower()
            size = str(account_file.get('filesize_bytes') or '')
            if clean_fn and size:
                fn_size_index[(clean_fn, size)] = url

        if search_result := search_results.get('search_result'):
            for s_res in search_result:
                matched_url = None
                if s_res['url'] in account_files_map:
                    matched_url = s_res['url']
                else:
                    raw_fn = s_res.get('filename_long') or s_res.get('filename') or ''
                    clean_fn = re.sub(r'<[^>]+>', '', raw_fn).lower()
                    size = str(s_res.get('filesize_bytes') or '')
                    if clean_fn and size:
                        matched_url = fn_size_index.pop((clean_fn, size), None)

                if matched_url:
                    combined_results.append(account_files_map.pop(matched_url))
                else:
                    combined_results.append(s_res)

        # Add remaining account files not matched to any search result
        for url in account_files_map:
            combined_results.append(account_files_map[url])

        return combined_results

    def _filter_results(self, combined_results: list[_FileEntry], season: Optional[str], episode: Optional[str], year: str, premiered: str) -> list[_FileEntry]:
        """Filter results by title and episode/year matching"""
        _fkey = id(self.titles)
        if self._franchise_cache_key != _fkey:
            self._relevant_franchises = source_utils.build_relevant_franchises(
                self.titles, const.sources.franchise_names, const.sources.franchise_names_sep
            )
            self._franchise_cache_key = _fkey
        relevant_franchises = self._relevant_franchises

        # Episode-specific setup
        if season and episode:
            ep_en_title = self.ffitem.vtag.getEnglishTitle() or ''
            ep_local_title = self.ffitem.vtag.getTitle() or ''

            # Build combined episode titles if available
            if ep_local_title or ep_en_title:
                base_titles = list(dict.fromkeys([t for t in self.titles if t]))
                for base in base_titles[:2]:  # Only first couple base titles
                    if ep_local_title:
                        self.titles.append(f"{base} {ep_local_title}")
                    if ep_en_title:
                        self.titles.append(f"{base} {ep_en_title}")
                self.titles = list(dict.fromkeys(self.titles))

            year_in_title = re.search(r'\b\d{4}\b', self.titles[0] if self.titles else '')
            year_in_title = year_in_title[0] if year_in_title else ''
            current_year = datetime.now().year

        alias_cleans = source_utils.build_alias_cleans(self.titles)

        results = []
        for s in combined_results:
            filename = re.sub(r'<[^>]+>', '', s['filename'])

            if season and episode:
                abs_ep = getattr(self, '_absolute_episode', None)
                # Fast path: API often pre-parses season/episode in more_info
                api_info_meta = s.get('more_info') or {}
                api_season_str = str(api_info_meta.get('s') or '').strip()
                api_episode_str = str(api_info_meta.get('e') or '').strip()
                used_api_se = False
                if api_season_str and api_episode_str:
                    try:
                        file_season = int(api_season_str)
                        file_episode = int(api_episode_str)
                        used_api_se = True
                        if file_season != int(season) or file_episode != int(episode):
                            if abs_ep and file_episode == abs_ep:
                                pass
                            else:
                                if self.debug:
                                    fflog(f'skip (year/episode mismatch): {filename}')
                                continue
                    except ValueError:
                        used_api_se = False

            if season and episode and not used_api_se:
                clean_filename = filename.replace('.', ' ').replace('-', ' ')
                ep_match = re.search(r's(\d{1,2})\s?e(\d{1,3})', clean_filename, re.I)
                if ep_match:
                    file_season = int(ep_match.group(1))
                    file_episode = int(ep_match.group(2))
                    if file_season != int(season) or file_episode != int(episode):
                        # For anime, also accept absolute episode number match
                        if abs_ep and file_episode == abs_ep:
                            pass
                        else:
                            if self.debug:
                                fflog(f'skip (year/episode mismatch): {filename}')
                            continue
                elif abs_ep:
                    # No sXXeYY found - check standalone episode number (e.g. E081)
                    standalone_ep = re.search(r'(?<![a-zA-Z])e(\d{2,4})(?!\d)', clean_filename, re.I)
                    if standalone_ep:
                        file_ep = int(standalone_ep.group(1))
                        if file_ep != abs_ep and file_ep != int(episode):
                            if self.debug:
                                fflog(f'skip (year/episode mismatch): {filename}')
                            continue

            # Check year for TV shows
            if season and episode:
                if premiered and re.search(r'\b\d{4}\b', filename):
                    if source_utils.check_year_in_filename(filename, year, premiered, year_in_title, current_year) is False:
                        if self.debug:
                            fflog(f'skip (year/episode mismatch): {filename}')
                        continue

            # Check title match
            match_found = source_utils.match_filename_title_parsed(filename, alias_cleans)
            reason = 'parsed title match' if match_found else ''

            # Check franchise match
            franchise_match = False
            if not match_found:
                try:
                    for pat_list in relevant_franchises.values():
                        for pat in pat_list:
                            if re.search(pat, filename, re.I):
                                franchise_match = True
                                reason = 'franchise match'
                                break
                        if franchise_match:
                            break
                except Exception:
                    franchise_match = False

            if match_found or franchise_match:
                # For movies: if filename carries a year, it must match expected year.
                # Prevents an account file like "Zwierzogród 2016" from matching when
                # searching "Zwierzogród 2" (2025) via franchise/loose title match.
                if not (season and episode) and year:
                    file_years = re.findall(r'\b(19\d{2}|20\d{2})\b', filename)
                    if file_years and str(year) not in file_years:
                        if self.debug:
                            fflog(f'skip (movie year mismatch): {filename}')
                        continue

                if not (season and episode) and source_utils.is_episode_filename(filename):
                    if year and re.search(rf'\b{re.escape(str(year))}\b', filename):
                        pass  # correct year present — episode marker is part of title, not episode designator
                    else:
                        if self.debug:
                            fflog(f'skip (episode marker in movie): {filename}')
                        continue
                results.append(s)
                if self.debug:
                    fflog(f'match: {filename}')
            else:
                if self.debug:
                    fflog(f'skip (title mismatch): {filename}')

        return results

    def _try_fallback_searches(self, title: str, localtitle: str, year: str, season: Optional[str], episode: Optional[str], premiered: str, aliases: list[SourceTitleAlias] | None) -> list[_FileEntry]:
        """Try various fallback search strategies"""
        results = []

        # Strategy 1: Remove certain phrases
        ex_list = [re.escape(e) for e in source_utils.antifalse_filter_exceptions]
        ex_pat = f"(?:{'|'.join(ex_list)})"
        if re.search(ex_pat, ' '.join([title, localtitle])):
            title2 = re.sub(rf"([_\\W]+{ex_pat})", '', title,
                            flags=re.I) if re.search(rf"([_\\W]+{ex_pat})", title, flags=re.I) else ''
            localtitle2 = re.sub(rf"([_\\W]+{ex_pat})", '', localtitle,
                                 flags=re.I) if re.search(rf"([_\\W]+{ex_pat})", localtitle, flags=re.I) else ''
            if title2 or localtitle2:
                fflog('retry: removing common phrases')
                results = self._search(title2 or title, localtitle2 or localtitle, year,
                                       season, episode, premiered, recurrency=True)
                if results:
                    return results

        if not season or not episode:
            return results

        # Strategy 3: 3-digit episode number
        if len(episode) == 1 or (len(episode) == 2 and not episode.startswith('0')):
            if self.debug:
                fflog('retry: 3-digit episode number')
            episode_3digit = episode.zfill(3)
            results = self._search(title, localtitle, year, season, episode_3digit, premiered, recurrency=True)
            if results:
                return results

            if (self._is_anime or getattr(self, '_is_long_season', False)) and season != '01':
                results = self._search(title, localtitle, year, '01', episode_3digit, premiered, recurrency=True)
                if results:
                    return results

        # Strategy 4: Combined episodes
        results = self._search(title, localtitle, year, season, episode, premiered, recurrency=True)
        if results:
            return results

        # Strategy 6: Non-English original name
        if aliases:
            originalname = [a for a in aliases if 'originalname' in a]
            originalname = originalname[0]['originalname'] if originalname else ''
            if originalname and originalname != title and originalname != localtitle and \
               not source_utils.detect_script(originalname):
                fflog('retry: non-English original title')
                results = self._search(originalname, '', year, season, episode, premiered, recurrency=True)
                if results:
                    return results

        # Strategy 7: Year - 1 (for movies only)
        if year and not season and not episode:
            fflog(f"retry: year {int(year)-1} (results may be inaccurate)")
            results = self._search(title, localtitle, str(int(year) - 1), season, episode, premiered, recurrency=True)

        return results

    def _search(self, title: str, localtitle: str, year: str = '', season: Optional[str] = None, episode: Optional[str] = None, premiered: str = '', aliases: list[SourceTitleAlias] | None = None, recurrency: Optional[bool] = None) -> list[_FileEntry]:
        """Fetch results from service API"""
        try:
            year = str(year) if year else ''

            if not recurrency:
                self.login()
                self._tried_queries = set()

                # Fetch account_info and account_files in parallel (both need only authtoken)
                _account_files = {}
                def _fetch_files():
                    _account_files.update(self._fetch_account_files())
                account_files_thread = Thread(target=_fetch_files, daemon=True)
                account_files_thread.start()
                self.get_account_info()
                account_files_thread.join()
                account_files_map_base = _account_files

                search_titles = self._prepare_search_titles(title, localtitle, aliases, year)

                # Anime detection for absolute episode support
                self._is_anime = source_utils.is_anime(self.ffitem) if season and episode else False

                # Long season detection
                self._is_long_season = False
                if season and episode:
                    try:
                        if self.ffitem.season_episode_count(int(season)) > 99:
                            self._is_long_season = True
                            if self.debug:
                                fflog(f'long season detected (>99 episodes), will try 2 and 3-digit padding')
                    except Exception:
                        pass

                self._absolute_episode = None
                if self._is_anime:
                    self._absolute_episode = self.ffitem.absolute_episode_number()
                    if self._absolute_episode:
                        if self.debug:
                            fflog(f'anime detected, absolute episode: {self._absolute_episode}')
            else:
                search_titles = [cleantitle.normalize(cleantitle.getsearch(t, preserve=(':')))
                                                      for t in [title, localtitle] if t]
                search_titles = [t.replace('&', 'and') for t in search_titles]
                search_titles = list(dict.fromkeys(search_titles))
                account_files_map_base = self._fetch_account_files()  # cache hit

            # Build query components
            year_r = year if not season and not episode else ''
            episode_r = ''
            if season and episode:
                episode_code = self._build_episode_code(season, episode)
                episode_r = re.sub(r'(?<=\*).*', '', episode_code)  # Remove after wildcard
                episode_code = episode_code.replace('*', '')

            results = []

            for search_title in search_titles:
                search_title = search_title.replace(':', '')
                search_title = cleantitle.normalize(search_title)

                # For long seasons, we try both formats in primary search
                queries_to_try = [episode_r]
                if getattr(self, '_is_long_season', False):
                    queries_to_try.append(episode.zfill(3))

                for ep_query in queries_to_try:
                    query = f"{search_title} {year_r} {ep_query}".replace('  ', ' ').strip()

                    search_results = self._fetch_search_results(query)
                    if not search_results:
                        continue

                    combined_results = self._combine_search_and_account_results(
                        search_results, dict(account_files_map_base))

                    if combined_results:
                        filtered = self._filter_results(combined_results, season, episode, year, premiered)
                        results.extend(filtered)

            # If search returned nothing for all queries, still check account files directly
            # (covers files uploaded manually by the user that the search engine doesn't index)
            if not results and not recurrency:
                if self.debug:
                    fflog('no search results, checking account files directly')
                if account_files_map_base:
                    filtered = self._filter_results(list(account_files_map_base.values()),
                                                    season, episode, year, premiered)
                    if filtered:
                        if self.debug:
                            fflog(f'found {len(filtered)} matching file(s) on account')
                        results.extend(filtered)

            # For anime TV shows: additional search strategies
            if not results and not recurrency and season and episode and getattr(self, '_absolute_episode', None):
                abs_ep = self._absolute_episode
                abs_ep_r = f"e{abs_ep:03d}"

                # Step 2: Original titles + absolute episode
                for search_title in search_titles:
                    st = search_title.replace(':', '')
                    st = cleantitle.normalize(st)
                    query = f"{st} {abs_ep_r}".strip()

                    search_results = self._fetch_search_results(query)
                    if search_results:
                        combined_results = self._combine_search_and_account_results(
                            search_results, dict(account_files_map_base))
                        if combined_results:
                            filtered = self._filter_results(combined_results, season, episode, year, premiered)
                            results.extend(filtered)
                    if results:
                        break

                # Build EN alias titles (US country, deduplicated vs search_titles)
                en_alias_titles = []
                if not results and aliases:
                    existing = {cleantitle.normalize(s.replace(':', '')).lower() for s in search_titles}
                    for alias in aliases:
                        if alias.get('country') != 'us':
                            continue
                        for key in ('title', 'originalname'):
                            alias_val = alias.get(key, '')
                            if not alias_val:
                                continue
                            cleaned = cleantitle.normalize(cleantitle.getsearch(alias_val))
                            cleaned = cleaned.replace(':', '').strip()
                            if cleaned and cleaned.lower() not in existing:
                                en_alias_titles.append(cleaned)
                                existing.add(cleaned.lower())
                    en_alias_titles = list(dict.fromkeys(en_alias_titles))

                # Step 3: EN alias titles + sXXeYY
                if not results and en_alias_titles:
                    for alias_title in en_alias_titles:
                        at = cleantitle.normalize(alias_title)
                        query = f"{at} {episode_r}".strip()

                        search_results = self._fetch_search_results(query)
                        if search_results:
                            combined_results = self._combine_search_and_account_results(
                                search_results, dict(account_files_map_base))
                            if combined_results:
                                filtered = self._filter_results(combined_results, season, episode, year, premiered)
                                results.extend(filtered)
                        if results:
                            break

                # Step 4: EN alias titles + absolute episode
                if not results and en_alias_titles:
                    for alias_title in en_alias_titles:
                        at = cleantitle.normalize(alias_title)
                        query = f"{at} {abs_ep_r}".strip()

                        search_results = self._fetch_search_results(query)
                        if search_results:
                            combined_results = self._combine_search_and_account_results(
                                search_results, dict(account_files_map_base))
                            if combined_results:
                                filtered = self._filter_results(combined_results, season, episode, year, premiered)
                                results.extend(filtered)
                        if results:
                            break

            # Try fallback searches if no results and not in recursion
            if not results and not recurrency:
                results = self._try_fallback_searches(title, localtitle, year, season, episode, premiered, aliases)

            if not results:
                fflog('no sources')
            else:
                fflog(f'approved: {len(results)}')

            return results
        except Exception:
            fflog_exc()
            return []

    def check_and_add_on_account_sources(self, sources: List[Source], ffitem: 'FFItem', source_name: str):
        """Check and mark sources that are already in user account"""
        try:
            self.login()
            if not self.api.authtoken:
                fflog('not logged in — skip account check')
                return

            # Update account info to get current transfer limits
            self.get_account_info()

            account_files = self.api.get_user_files()
            if not account_files:
                fflog('no files on account')
                return

            # Filter account files by year if available
            filtered_account_files = []
            expected_year = str(ffitem.year) if ffitem.year else None

            for f in account_files:
                filename_full = f.get('filename_full', '')
                file_year_match = re.search(r'\b(19\d{2}|20\d{2})\b', filename_full)

                if expected_year:
                    if file_year_match:
                        file_year = file_year_match.group(1)
                        if file_year == expected_year:
                            filtered_account_files.append(f)
                else:
                    filtered_account_files.append(f)

            account_file_urls = {f['url'] for f in filtered_account_files}
            account_fn_size_index = {}
            for f in filtered_account_files:
                raw_fn = f.get('filename_long') or f.get('filename') or ''
                clean_fn = re.sub(r'<[^>]+>', '', raw_fn).lower()
                size = str(f.get('filesize_bytes') or '')
                if clean_fn and size:
                    account_fn_size_index[(clean_fn, size)] = True

            for source_item in sources:
                if not hasattr(source_item, 'meta'):
                    continue
                if source_item.url in account_file_urls:
                    source_item.meta['on_account'] = True
                    continue
                meta = source_item.meta or {}
                clean_fn = re.sub(r'<[^>]+>', '', (meta.get('filename') or '')).lower()
                size = str(meta.get('filesize_bytes') or '')
                if clean_fn and size and (clean_fn, size) in account_fn_size_index:
                    source_item.meta['on_account'] = True

        except Exception:
            fflog_exc()
# ─── Rapideo ─────────────────────────────────────────────────────────────────────


class Rapideo(_source):
    """Scraper for rapideo."""
    PROVIDER: ClassVar[str] = 'rapideo'
    URL: ClassVar[str] = 'https://www.rapideo.net'


# ─── TwojLimit ───────────────────────────────────────────────────────────────────

class TwojLimit(_source):
    """Scraper for twojlimit."""
    PROVIDER: ClassVar[str] = 'twojlimit'
    URL: ClassVar[str] = 'https://www.twojlimit.pl'


# ─── NoPremium ───────────────────────────────────────────────────────────────────

class NoPremium(_source):
    """Scraper for nopremium."""
    PROVIDER: ClassVar[str] = 'nopremium'
    URL: ClassVar[str] = 'https://www.nopremium.pl'


def register(sources: List[SourceModule], group: str) -> None:
    """Register all scrapers."""
    for src in (Rapideo, TwojLimit, NoPremium):
        sources.append(SourceModule(name=src.PROVIDER, provider=src(), group=group))
