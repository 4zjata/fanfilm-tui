# -*- coding: utf-8 -*-
"""
FanFilm - źródło: tb7.pl / xt7.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
from typing import Sequence, ClassVar, List, Tuple, Dict, Optional, TYPE_CHECKING
import re
import time
import sys
import hashlib
import json
from html import unescape
from urllib.parse import unquote, quote
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from threading import current_thread, Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.sources import SourceModule, Source
from lib.ff import requests
from lib.ff import (
    cache,
    cleantitle,
    client,
    control,
    source_utils,
)
from lib.ff.source_utils import CHROME_UA, ShowData, ShowDataDict, show_data_asdict, strip_diacritics
from lib.ff.settings import settings
from lib.ff.log_utils import fflog, fflog_exc
from const import const
if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.ff.sources import SourceItem


# Constants
COOKIE_EXPIRATION_HOURS = 3
NOTES_MAX_SIZE = 4800


# ─── WebClient ───────────────────────────────────────────────────────────────────

class WebClient:
    """Handles all HTTP communication with tb7/xt7 services"""

    def __init__(self, provider_name, base_url):
        self.provider = provider_name
        self.base_url = base_url
        self.session = requests.Session()

        self.session.headers.update({
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': CHROME_UA,
            'DNT': '1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
        })

    def set_cookies(self, cookies):
        self.session.cookies.clear()
        for cookie_str in cookies.split('; '):
            if '=' in cookie_str:
                name, value = cookie_str.split('=', 1)
                self.session.cookies.set(name, value)

    def get_cookies(self):
        """Get cookies as string from session"""
        return '; '.join([f"{name}={value}" for name, value in self.session.cookies.items()])

    def login(self, username, password):
        """Login to service"""
        self.session.post(
            self.base_url + 'login',
            verify=False,
            allow_redirects=False,
            data={'login': username, 'password': password},
        )
        result = self.session.get(self.base_url).text
        return username in result

    def search(self, query):
        """Search for files"""
        return self.session.post(
            self.base_url + 'mojekonto/szukaj',
            data={'search': query, 'type': '1'},
            timeout=45,
        ).text

    def get_search_page(self, page_num):
        """Get specific page of search results"""
        return self.session.get(
            self.base_url + f"mojekonto/szukaj/{page_num}"
        ).text

    def get_library(self):
        """Get user's library/account files"""
        return self.session.get(
            self.base_url + 'mojekonto/pliki'
        ).text

    def get_notepad(self):
        """Get notepad content"""
        return self.session.get(
            self.base_url + 'mojekonto/notes'
        ).text

    def save_notepad(self, content):
        """Save notepad content"""
        return self.session.post(
            self.base_url + 'mojekonto/notes',
            data={'content': content}
        ).status_code == 200

    def get_account_info(self):
        """Get account info page (includes transfer limit)"""
        return self.session.get(self.base_url).text

    def post_download_step(self, step, content=None):
        """Post to download endpoint for file purchase"""
        data = {'step': str(step)}
        if content:
            data['content'] = content
        elif step == 2:
            data['0'] = 'on'

        return self.session.post(
            self.base_url + 'mojekonto/sciagaj',
            data=data
        ).text

    def safe_head(self, url, allow_redirects=False):
        """HEAD request; returns (status, location, content_type)."""
        try:
            response = self.session.head(url, verify=False, allow_redirects=allow_redirects)
            status = response.status_code
            location = response.headers.get('Location')
            content_type = response.headers.get('Content-Type')
            return status, location, content_type
        except Exception:
            return None, None, None
        finally:
            try:
                response.close()
            except Exception:
                pass

    def safe_get(self, url, allow_redirects=False):
        """GET request; returns (status, location, text)."""
        try:
            response = self.session.get(url, verify=False, allow_redirects=allow_redirects)
            status = response.status_code
            location = response.headers.get('Location')
            text = response.text if response.status_code == 200 else None
            return status, location, text
        except Exception:
            return None, None, None
        finally:
            try:
                response.close()
            except Exception:
                pass


# ─── _source ─────────────────────────────────────────────────────────────────────

class _source:
    """Base scraper for sites like tb7, xt7"""

    ffitem: FFItem

    PROVIDER: ClassVar[str]
    URL: ClassVar[str]
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ['pl']

    has_sort_order: bool = True
    has_color_identify2: bool = True
    has_library_color_identify2: bool = True
    use_premium_color: bool = True

    def __init__(self):
        self.VIDEO_EXTENSIONS = ('avi', 'mkv', 'mp4', 'mpg', 'mov', '.ts', 'mts', '2ts')
        self.results = []
        self._results_set = set()
        self.titles = []
        self.pages = []
        self.additional_filter = None
        self.additional_filter2 = None
        self._filter_cache_key = None
        self._relevant_franchises = {}
        self._alias_cleans: set = set()
        self.remaining_limit_mb = -1
        self.domains = [f"{self.PROVIDER}.pl"]
        self.base_link = f"{self.URL}/"

        self.user_name = settings.getString(f"{self.PROVIDER}.username")
        self.user_pass = settings.getString(f"{self.PROVIDER}.password")
        self.debug = const.sources.xtb7.debug
        self.last_sources = []
        self._library_data: tuple = ()

        # Initialize web clients
        # web_primary  – main session (login, downloads, library, etc.)
        # web_parallel – separate session used only for parallel PL/EN search
        self.web = WebClient(self.PROVIDER, self.base_link)
        self.web_parallel = WebClient(self.PROVIDER, self.base_link)

        # Compiled regex patterns
        self.RE_CRC32 = re.compile(r'\b[0-9a-f]{8}\b', re.I)

        # Pre-compute extension pattern for filename matching
        self.ext_pattern = f"({'|'.join(self.VIDEO_EXTENSIONS)})".replace('.', '')

        # Twojplik specific regex for filename normalization
        self.RE_TWOJPLIK_CRC = re.compile(r'\.[0-9A-F]{3}(\.(?:avi|mkv|mp4|mpg|mov|ts|mts|2ts))$', re.I)

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> list[str] | None:
        fflog('searching: movie', 0)
        self.results = []
        self._results_set = set()
        return self._search(title, localtitle, year, aliases=aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        """Helper method before searching for episode"""
        self.results = []
        self._results_set = set()
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, year))

    def episode(self, url: ShowDataDict | None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> list[str] | None:
        self.results = []
        self._results_set = set()
        data = ShowData(**url)
        return self._search(
            data.title,
            data.local_title,
            year=data.year,
            season=season,
            episode=episode,
            aliases=data.aliases,
            premiered=premiered
        )

    def sources(self, rows: list[str] | None, hostDict: List[str], hostprDict: List[str], from_cache: Optional[bool] = None) -> 'List[SourceItem]':
        if not rows:
            fflog('no sources')
            if self.results:
                rows = self.results
            else:
                return []

        self.login()

        if from_cache or self.remaining_limit_mb < 0:
            self._fetch_remaining_limit()

        if self._library_data:
            library_cache, library_links = self._library_data[0], list(self._library_data[1])
        else:
            library_cache, library_links = self.files_on_user_account()[0:2]

        sources = []
        try:
            for row in rows:
                try:
                    filename = client.parseDOM(row, 'label')[0]
                    if '<a ' in filename.lower():
                        filename = client.parseDOM(filename, 'a')[0]

                    link = client.parseDOM(row, 'input', ret='value')[0]

                    # Check if selected item is already in user account
                    on_account, on_account_link, case, on_account_expires = self.check_if_file_is_on_user_account(
                        library_links, link, filename, library_cache
                    )

                    # Avoid duplicates for items added from library
                    if 'added_from_library' in row and on_account_link and any(on_account_link == s['on_account_link'] for s in sources):
                        continue

                    hosting = client.parseDOM(row, 'td')[1]
                    size = client.parseDOM(row, 'td')[3]

                    source_size_mb = int(source_utils.convert_size_to_bytes(size) / (1024 * 1024))
                    no_transfer = (
                        const.sources.premium.no_transfer
                        and self.remaining_limit_mb >= 0
                        and source_size_mb > 0
                        and source_size_mb > self.remaining_limit_mb
                    )

                    quality, language, info = source_utils.parse_source_quality_lang(filename)

                    reject = False

                    if const.sources.xtb7.similarity_check:
                        # Check if it's a duplicate source (similar to existing)
                        for i in reversed(range(len(sources))):
                            s = sources[i]

                            if hosting in s['source'] and info == s['info'] and quality == s['quality']:
                                similarity = self._filename_similarity(
                                    self._decode_url_text(filename),
                                    self._decode_url_text(s['filename']),
                                )
                                if similarity > const.sources.xtb7.similarity_threshold:
                                    reject = True
                                    break

                    if reject:
                        continue

                    hosting += case
                    sources.append(
                        {
                            'source': hosting,
                            'quality': quality,
                            'language': language,
                            'url': link,
                            'info': info,
                            'size': size,
                            'direct': True,
                            'debridonly': False,
                            'filename': filename,
                            'on_account': on_account,
                            'on_account_expires': on_account_expires,
                            'on_account_link': on_account_link,
                            'premium': True,
                            'no_transfer': no_transfer,
                        }
                    )
                except Exception:
                    fflog_exc()
                    continue

            fflog(f'sources: {len(sources)}')
            self.last_sources = sources
            return sources

        except Exception:
            fflog_exc()
            return sources

    def resolve(self, url: str, buy_anyway: bool = False) -> Optional[str]:
        """Returns link to player"""
        original_url = url
        specific_source_data = {}
        try:
            if hasattr(self, 'last_sources') and self.last_sources:
                fflog('resolve: looking up source in last_sources')
                source_data = next((i for i in self.last_sources if i.get('url') == url), None)
                if source_data:
                    specific_source_data = source_data
                    fflog(f'resolve: source found in last_sources: {specific_source_data!r}')
                else:
                    fflog('resolve: source not found in last_sources')
            else:
                fflog('resolve: last_sources is empty')
        except Exception as e:
            fflog(f'resolve: error looking up last_sources: {e!r}')

        player_link_after_redirection = True

        def _check_on_account_link_before_play(on_account_link):
            """Test and possibly repair inactive link"""
            link = on_account_link

            # Only sciagaj links
            if '/sciagaj/' in link:
                status, location, text = self.web.safe_get(link, allow_redirects=False)

                # 200 — link inactive
                if status == 200:
                    control.execute('Dialog.Close(notification,true)')
                    if text and 'dla podanego linka Premium' in text:
                        control.dialog.ok(f'{self.PROVIDER}', 'Wykorzystano limit połączeń dla tego źródła.')
                    else:
                        control.dialog.ok(f'{self.PROVIDER}', 'Link wygasł.')
                    return None

                # 302 — follow redirect
                if status == 302 and location:
                    link = location
                    status2, loc2, ctype2 = self.web.safe_head(link, allow_redirects=False)

                    # wrzucaj.pl + token — fix link
                    if ctype2 and 'text' in ctype2:
                        if 'download_token=' in link and '.wrzucaj.pl/' in link:
                            link = re.sub(r'(?<=//)\w+?\.(wrzucaj\.pl/)', r'\1file/', link)
                            link = re.sub(r'\&?download_token=[^&]*', '', link).rstrip('?')

                            try:
                                link = link.encode('latin1').decode('utf8')
                            except Exception:
                                fflog_exc()

                            # HEAD after fix
                            status3, loc3, _ = self.web.safe_head(link, allow_redirects=False)
                            if status3 == 302 and loc3:
                                link = loc3

                        # 302 → follow redirect further
                        if status2 == 302 and loc2:
                            link = loc2
                            # final HEAD — check only
                            self.web.safe_head(link, allow_redirects=False)

                # 403 — forbidden
                if status == 403:
                    control.execute('Dialog.Close(notification,true)')
                    control.dialog.ok('Dostęp zabroniony', ' [CR] - sprawdź powód na stronie')
                    return None

                # other errors
                if status and status >= 400:
                    control.infoDialog(f'Server zwrócił błąd {status}', f'{self.PROVIDER}', 'ERROR', 4000)
                    control.sleep(4000)
                    return None

                fflog(f'status code: {status}', 0)

            if player_link_after_redirection:
                on_account_link = link

            fflog(f'resolved: {on_account_link=}', 0)
            return self._format_player_url(on_account_link)

        if not buy_anyway and specific_source_data:
            on_account = specific_source_data.get('on_account', False)
            if on_account:
                on_account_link = specific_source_data.get('on_account_link', '')
                if on_account_link:
                    on_account_link = on_account_link.replace('%2F', '-')
                    return _check_on_account_link_before_play(on_account_link)

        self.login()

        filename = specific_source_data.get('filename', '')
        fflog(f'resolve: {filename=}')

        auto_purchase = settings.getBool(f"{self.PROVIDER}.auto")
        if not auto_purchase:
            limit_info = self.web.get_account_info()
            limit_info = client.parseDOM(limit_info, 'div', attrs={'class': 'textPremium'})
            remaining_limit = str(client.parseDOM(limit_info, 'b')[-1])
            remaining_limit = re.sub(r"\s*\w+\s*=\s*([\"']?).*?\1(?=[\s>]|$)\s*", '', remaining_limit)
            remaining_limit = re.sub('<[^>]+>', '', remaining_limit)

            if not filename:
                filename = url
            filename = unquote(filename)
            filename = unescape(filename)
            filename = self.prepare_filename_to_display(filename)
            filename = f"[LIGHT]{filename}[/LIGHT]"

            size_info = specific_source_data.get('meta', {}).get('size', '') or specific_source_data.get('size', '')
            if not source_utils.confirm_transfer_dialog(
                filename=filename,
                charge=size_info,
                remaining=remaining_limit,
            ):
                return False

        links = [original_url]
        links = list(dict.fromkeys(links))

        for link in links:
            # Step 1 - send address to check if active
            response = self.web.post_download_step(1, link)

            # Check if active
            if ' value="Wgraj linki"' not in response:
                fflog(f'inactive link: {link=}')
                time.sleep(0.1)
                continue
            else:
                break

        if 'ymagane dodatkowe' in response:
            control.dialog.ok(
                'Brak środków', f'Brak wystarczającego transferu. \n[COLOR gray](aktualnie posiadasz [B]{remaining_limit}[/B])[/COLOR]')
            fflog('insufficient transfer')
            return None

        if ' value="Wgraj linki"' not in response:
            plural = len(links) > 1
            control.infoDialog(
                (f"Wystąpił błąd. \nTa pozycja ma nieaktywn{'e' if plural else 'y'} link{'i' if plural else ''}."), f'{self.PROVIDER}', 'ERROR')
            fflog('no active links for this item')
            return None

        if buy_anyway:
            if '/wrzucaj.pl/' in link:

                if '/file/' not in link:
                    link = link.replace('/wrzucaj.pl/' , '/wrzucaj.pl/file/')
                    if '/' in filename:
                        parts = list(link.partition('/file/'))
                        parts[-1] = list(parts[-1].partition('/'))
                        parts[-1][-1] = parts[-1][-1].replace('/', '%2F')
                        parts[-1] = ''.join(parts[-1])
                        link = ''.join(parts)

                link = link.replace('%2F', '-')

                if player_link_after_redirection:
                    status, location, _ = self.web.safe_head(link, allow_redirects=False)
                    if status == 302 and location:
                        link = location

                return self._format_player_url(link)

        # Step 2 - attempt to add source to library
        response = self.web.post_download_step(2)

        div = client.parseDOM(response, 'div', attrs={'class': 'download'})
        try:
            link = client.parseDOM(div, 'a', ret='href')[1]
            size = div[1].split('|')[-1].strip()
        except Exception:
            fflog_exc()
            if 'Nieaktywne linki' in response:
                control.dialog.notification(f"{self.PROVIDER}", 'Link okazał się nieaktywny')
                fflog(f'link is inactive after all: {link=}')
            else:
                control.infoDialog('Wystąpił błąd. \nMoże brak wystarczającego transferu?', f"{self.PROVIDER}", 'ERROR')
            fflog_exc()
            return None

        # Save information in notepad
        if settings.getBool(f"{self.PROVIDER}.use_web_notebook_for_history"):
            notes_list = []
            notepad_html = self.web.get_notepad()
            notes_value = client.parseDOM(notepad_html, 'textarea', attrs={'class': 'notepad'})
            if notes_value:
                notes_value = notes_value[0].strip()
                if notes_value and notes_value.startswith('[') and notes_value.endswith(']'):
                    try:
                        notes_list = json.loads(notes_value)
                    except (json.JSONDecodeError, ValueError):
                        notes_list = []

            filename_for_notes = specific_source_data.get('filename')
            if not filename_for_notes:
                filename_for_notes = original_url.split('/')[-1]
                if '.' not in filename_for_notes or len(filename_for_notes.split('.')[-1]) > 5:
                    filename_for_notes = ''
            filename_for_notes = self._decode_url_text(filename_for_notes or '')

            link1 = link.rpartition('/')[0] if '/sciagaj/' in link else link

            generation_date = datetime.now()
            generation_date_str = generation_date.strftime('%H:%M %d.%m.%Y')

            # NEW STRUCTURE: [original_url, download_link, filename, size, generation_date]
            new_entry = [original_url, link1, filename_for_notes, size, generation_date_str]
            notes_list = [new_entry] + notes_list

            # Control size via JSON
            while len(json.dumps(notes_list)) >= NOTES_MAX_SIZE:
                notes_list.pop()

            self.web.save_notepad(json.dumps(notes_list))

        # Return link to player
        link = link.replace('%2F', '-')
        if player_link_after_redirection:
            return _check_on_account_link_before_play(link)
        else:
            return self._format_player_url(link)

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _decode_url_text(text):
        """Decode URL-encoded and HTML-escaped text"""
        return unescape(unquote(text))

    @staticmethod
    def _extract_filename_from_url(url):
        """Extract filename from URL path"""
        return url.rstrip('/').split('/')[-1]

    def _normalize_twojplik_filename(self, text, link):
        """Normalize TwojPlik filenames by replacing CRC with placeholder"""
        if '/twojplik.pl/' in link.lower():
            return self.RE_TWOJPLIK_CRC.sub(r'.ZZZ\1', text)
        return text

    def _format_player_url(self, url):
        """Format URL with User-Agent for player"""
        user_agent = quote(self.web.session.headers.get('User-Agent', ''))
        return f"{url}|User-Agent={user_agent}&Connection=close&verifypeer=false"

    @staticmethod
    def _format_episode(season, episode):
        """Format season and episode numbers (s01e05)"""
        season_num = str(season).zfill(2)
        episode_num = str(episode).zfill(2)
        return season_num, episode_num, f"s{season_num}", f"s{season_num}e{episode_num}"

    def _search(self, title: str, localtitle: str, year: str = '', season: str | None = None, episode: str | None = None, aliases: list[SourceTitleAlias] | None = None, premiered: str = '') -> list[str]:
        if not title:
            fflog(f'search error: title is empty: {title=}')
            return
        if const.sources.premium.no_transfer:
            self._fetch_remaining_limit()
        _search_start = time.time()
        results = []

        if aliases is None:
            aliases = []

        year = str(year)

        try:
            aliases2 = source_utils.prepare_alias_search_list(aliases, year)

            all_titles_for_filtering = []
            search_titles = []
            titles_to_use = [title.lower()]
            if localtitle.lower() != title.lower():
                titles_to_use.append(localtitle.lower())

            # Anime detection for special search handling
            is_anime = hasattr(self, 'ffitem') and source_utils.is_anime(self.ffitem)
            self._absolute_episode = None
            if is_anime and season and episode:
                try:
                    self._absolute_episode = self.ffitem.absolute_episode_number()
                except Exception:
                    pass
                if self._absolute_episode:
                    fflog(f'anime detected, absolute episode: {self._absolute_episode}')

            if season and episode:
                season_num, episode_num, season_str, episode_str = self._format_episode(season, episode)

                search_stages = []

                # [Anime] original title + absolute episode number
                if is_anime and self._absolute_episode:
                    abs_ep_str = str(self._absolute_episode).zfill(2)
                    anime_stage = []
                    if const.sources.xtb7.space_before_season:
                        anime_stage.append(f"{title.lower()} e{abs_ep_str}")
                    if const.sources.xtb7.dot_before_season:
                        anime_stage.append(f"{title.lower()}.e{abs_ep_str}")
                    search_stages.append(anime_stage)

                # Stage 1: original title + episode
                stage1 = []
                if const.sources.xtb7.space_before_season:
                    stage1.append(f"{title.lower()} {episode_str}")
                if const.sources.xtb7.dot_before_season:
                    stage1.append(f"{title.lower()}.{episode_str}")
                search_stages.append(stage1)

                # Stage 2: local title + episode (only when different from original, skip for anime)
                if not is_anime and localtitle.lower() != title.lower():
                    stage2 = []
                    if const.sources.xtb7.space_before_season:
                        stage2.append(f"{localtitle.lower()} {episode_str}")
                    if const.sources.xtb7.dot_before_season:
                        stage2.append(f"{localtitle.lower()}.{episode_str}")
                    search_stages.append(stage2)
                else:
                    search_stages.append([])

                # Stage 3 (fallback): original title + season (skip for anime)
                if not is_anime:
                    stage3 = []
                    if const.sources.xtb7.space_before_season:
                        stage3.append(f"{title.lower()} {season_str}")
                    if const.sources.xtb7.dot_before_season:
                        stage3.append(f"{title.lower()}.{season_str}")
                    search_stages.append(stage3)
                else:
                    search_stages.append([])

                # Stage 4 (fallback): local title + season (skip for anime)
                if not is_anime and localtitle.lower() != title.lower():
                    stage4 = []
                    if const.sources.xtb7.space_before_season:
                        stage4.append(f"{localtitle.lower()} {season_str}")
                    if const.sources.xtb7.dot_before_season:
                        stage4.append(f"{localtitle.lower()}.{season_str}")
                    search_stages.append(stage4)
                else:
                    search_stages.append([])

                # Stage 5 (fallback): original title only
                search_stages.append([title.lower()])

                # Stage 6 (fallback): local title only (skip for anime)
                if not is_anime and localtitle.lower() != title.lower():
                    search_stages.append([localtitle.lower()])
                else:
                    search_stages.append([])

                max_stages = const.sources.xtb7.max_show_search_queries
                for i in range(max_stages):
                    if i < len(search_stages):
                        search_titles.extend(search_stages[i])

            else:
                search_titles.extend(titles_to_use)

            # Remove duplicates from search titles
            unique_search_titles = source_utils.deduplicate_list_ci(search_titles)

            # Prepare ALL titles for filtering (main + aliases)
            aliases_lower = [a.lower() for a in aliases2]
            all_titles_for_filtering = search_titles + aliases_lower
            self.titles = source_utils.deduplicate_list_ci(all_titles_for_filtering)

            # For movies: add the first English alias as an extra query
            # (+1 sequential HTTP request) — catches files indexed under a
            # shorter title, e.g. "Jack Ryan: Ghost War" without "Tom Clancy's".
            if not (season and episode):
                existing = {st.lower() for st in unique_search_titles}
                for alias in aliases:
                    if alias.get('country') != 'us':
                        continue
                    alias_val = alias.get('title') or alias.get('originalname') or ''
                    if alias_val and alias_val.lower() not in existing:
                        unique_search_titles.append(alias_val)
                        if self.debug:
                            fflog(f'extra query from alias: {alias_val!r}')
                        break

            # Prepare only main titles for searching
            final_search_titles = []
            for t in unique_search_titles:
                # drop apostrophes so "Clancy's" → "Clancys", not "Clancy s"
                cleaned = cleantitle.normalize(cleantitle.getsearch(t.replace("'", "").replace("’", "")))
                final_search_titles.append(cleaned)

            final_search_titles = source_utils.deduplicate_list_ci(final_search_titles)
            final_search_titles = [
                const.sources.xtb7.title_replacements.get(t.lower(), t)
                for t in final_search_titles
            ]

            def prepare_search_query(title):
                """Prepare title for search engine"""
                title = strip_diacritics(title)
                title = title.replace(' - ', ' ')
                title = title.replace('-', '_')
                title = title.replace('. ', '.')
                title = title.replace(' ', '_')
                title = title.replace(',', '')
                title = title.replace('#', '')
                title = re.sub(r'_?(\d)/', r'_\1_', title)  # e.g., 1½ -> _1_
                title = title.replace('&', '_and_')
                return title

            def finalize_query(title_r):
                """Add padding and wildcards to query"""
                if not customTitles:
                    title_r = re.sub(r'^(the|an?)[ _]', '', title_r, 1, flags=re.IGNORECASE)
                if len(title_r) < 3:
                    if season and episode:
                        title_r += f' s{str(season).zfill(2)}e{str(episode).zfill(2)}'
                    else:
                        title_r += f' {year}'
                if not re.search(r'\W', title_r.rstrip('%')) and not title_r.endswith('%'):
                    title_r += '%'
                return title_r

            if self.debug:
                fflog(f'search titles: {final_search_titles}')
                fflog(f'all titles for filtering: {self.titles}')

            self.login()

            # Start library fetch in background so it overlaps with search queries.
            # Uses a temp session — self.web stays free for EN search.
            _lib_web = self._make_temp_webclient()
            _lib_result: list = [None]
            def _bg_fetch_library():
                try:
                    _lib_result[0] = self._fetch_files_on_user_account(web=_lib_web)
                except Exception:
                    fflog_exc('background library fetch failed')
            _lib_thread = Thread(target=_bg_fetch_library, daemon=True)
            _lib_thread.start()

            customTitles = 'customTitles' in sys.argv[2]

            # Build finalized queries upfront
            finalized_queries: List[Tuple[str, str]] = []
            for search_title in unique_search_titles:
                title_to_search = prepare_search_query(search_title) if not customTitles else search_title
                title_r = finalize_query(title_to_search)
                finalized_queries.append((search_title, title_r))

            if self.debug:
                fflog(f'search queries: {[tr for _, tr in finalized_queries]}')

            # Phase 1: Fetch all queries (EN priority, PL within time budget)
            all_raw_rows = self._fetch_all_rows(finalized_queries, start_time=_search_start)

            # Phase 2: Single filter pass over all collected rows
            if all_raw_rows:
                self.get_pages_content(
                    page=None, rows=all_raw_rows, year=year, title=title,
                    season=season, episode=episode, premiered=premiered,
                )
            elif self.debug:
                fflog('no match')

            # Phase 3: Last-chance fallback (sequential, only when Phase 1+2 yielded nothing)
            if not self.results and season and episode:
                season_num, episode_num, _, _ = self._format_episode(season, episode)
                extra_titles: List[str] = []
                # 3b. Episode-only variant for s01
                if season_num == '01':
                    for page in self.pages:
                        page_title = list(page)[0]
                        if re.search(r's[0o]1(e\d+)?', page_title, flags=re.I):
                            base_title = re.sub(r'[\W_]s[0o]1(e\d+)?', '', page_title, flags=re.I)
                            ep_only = f"e{episode_num}"
                            sep = ' ' if ' ' in base_title else '.'
                            new_variant = f"{base_title}{sep}{ep_only}"
                            if new_variant not in final_search_titles:
                                extra_titles.append(new_variant)
                                fflog(f'last-chance variant: {new_variant!r}')
                            break

                for lc_title in extra_titles:
                    if getattr(current_thread(), 'stop_event', None) and current_thread().stop_event.is_set():
                        break
                    title_r = finalize_query(prepare_search_query(lc_title) if not customTitles else lc_title)
                    if self.debug:
                        fflog(f'last-chance query: {title_r!r}')
                    _, _, lc_rows = self._fetch_raw_rows(title_r)
                    if lc_rows:
                        self.get_pages_content(
                            page=None, rows=lc_rows, year=year, title=title,
                            season=season, episode=episode, premiered=premiered,
                        )
                        if self.results:
                            break

            # Wait for background library fetch (should already be done by now)
            _lib_timeout = max(settings.getInt('scrapers.timeout.1') - (time.time() - _search_start) - 1, 1.0)
            _lib_thread.join(timeout=_lib_timeout)
            if _lib_result[0]:
                library_cache, library_links = _lib_result[0][0], list(_lib_result[0][1])
                self._library_data = (library_cache, list(library_links))
            else:
                library_cache, library_links = {}, []
                fflog('library fetch timed out or failed', 0)

            # Phase 4: Library sweep – catch files on account missed by search
            try:
                lib_rows = [
                    f'<td></td><td></td><td></td><td></td>'
                    f'<label>{self._decode_url_text(self._extract_filename_from_url(url))}</label>'
                    f'<input value="{url}"/>added_from_library'
                    for url, _expiry in library_links
                ] + [
                    f"<td></td><td>{data['source']}</td><td></td><td></td>"
                    f"<label>{data['filename']}</label>"
                    f'<input value="{data["on_account_link"]}"/>added_from_library'
                    for data in library_cache.values()
                ]
                if lib_rows:
                    fflog(f'phase 4: checking {len(lib_rows)} library files', 0)
                    self.get_pages_content(
                        page=None, rows=lib_rows, year=year, title=title,
                        season=season, episode=episode, premiered=premiered,
                    )
            except Exception:
                fflog_exc('Library phase 4 failed')

            results = self.results
            if results:
                fflog(f'approved: {len(results)}')
            else:
                fflog('no sources')
            return results

        except Exception as e:
            fflog_exc()
            fflog(f'search error: {e}')
            return []

    def _fetch_raw_rows(self, title_r: str, web=None) -> Tuple[str, int, list[str]]:
        """Fetch all raw table rows for a search query without any filtering."""
        web = web or self.web
        fflog(f'query: {title_r!r}')
        pre_res = web.search(title_r)
        pre_res = pre_res.replace('\r', '').replace('\n', '')

        if self.debug:
            fflog(f'response length for {title_r!r}: {len(pre_res)} chars')

        page_block = re.search('class="page-list"(.+?)</div>', pre_res, re.IGNORECASE)
        if page_block is None:
            fflog(f'search: 0 results (response length: {len(pre_res)})')
            return title_r, 0, []

        pages = len(re.findall('href=', page_block.group()))
        self.pages.append({title_r: pages})

        # Page 1 is already in the POST response — no extra GET needed
        rows = client.parseDOM(pre_res, 'tr')[1:]
        fflog(f'search: {len(rows)} results')

        for page_num in range(2, pages + 1):
            if hasattr(current_thread(), 'stop_event') and current_thread().stop_event.is_set():
                break
            res = web.get_search_page(page_num)
            page_rows = client.parseDOM(res, 'tr')[1:]
            fflog(f'search: {len(page_rows)} results (page {page_num})')
            rows.extend(page_rows)

        return title_r, pages, rows

    def _fetch_all_rows(self, queries: List[Tuple[str, str]], start_time: float = None) -> list[str]:
        """Fetch raw rows for all queries.

        queries[0] = EN (priority) — always fetched.
        queries[1] = PL (secondary) — fetched in parallel with EN, but abandoned
                     gracefully if the time budget is exhausted before it completes.

        Time budget = scrapers.timeout.1 - elapsed_since_start - SAFETY_MARGIN.
        If PL does not finish within the budget, we return EN results only.
        """
        if start_time is None:
            start_time = time.time()

        SAFETY_MARGIN = 3  # seconds to reserve for title-matching after all fetches
        MIN_FETCH_BUDGET = 5  # don't even try PL if budget < this
        global_timeout = settings.getInt('scrapers.timeout.1')

        all_rows: list[str] = []

        def _is_cancelled():
            return getattr(current_thread(), 'stop_event', None) and current_thread().stop_event.is_set()

        if _is_cancelled() or not queries:
            return all_rows

        # Deduplicate by final query string — avoids duplicate HTTP requests when
        # EN and PL titles normalise to the same search term (e.g. "Batman").
        seen_query_strs: set = set()
        deduped: List[Tuple[str, str]] = []
        for orig_title, title_r in queries:
            if title_r not in seen_query_strs:
                seen_query_strs.add(title_r)
                deduped.append((orig_title, title_r))
        if len(deduped) < len(queries):
            fflog(f'deduplicated {len(queries)} queries → {len(deduped)} (identical strings removed)', 0)
        queries = deduped

        # ── Sequential path: single query or parallel sessions disabled ─────────
        if len(queries) == 1 or not const.sources.xtb7.parallel_sessions:
            for _, title_r in queries:
                if _is_cancelled():
                    break
                try:
                    _, _, rows = self._fetch_raw_rows(title_r, self.web)
                    all_rows.extend(rows)
                except Exception:
                    fflog_exc(f'Sequential fetch failed for {title_r!r}')
            return list(dict.fromkeys(all_rows))

        # ── Parallel path: queries[0]=EN (priority), queries[1]=PL (secondary) ─
        title_r_en = queries[0][1]
        title_r_pl = queries[1][1]
        elapsed_so_far = time.time() - start_time
        fetch_budget = global_timeout - elapsed_so_far - SAFETY_MARGIN
        fflog(f'parallel fetch: EN={title_r_en!r} PL={title_r_pl!r} budget={fetch_budget:.1f}s', 0)

        if fetch_budget < MIN_FETCH_BUDGET:
            # No time left — at least try EN synchronously
            fflog(f'budget too low ({fetch_budget:.1f}s) — EN-only sequential fallback', 0)
            try:
                _, _, rows = self._fetch_raw_rows(title_r_en, self.web)
                all_rows.extend(rows)
            except Exception:
                fflog_exc('EN-only fallback fetch failed')
        else:
            executor = ThreadPoolExecutor(max_workers=2)
            try:
                f_en = executor.submit(self._fetch_raw_rows, title_r_en, self.web)
                f_pl = executor.submit(self._fetch_raw_rows, title_r_pl, self.web_parallel)
                try:
                    for f in as_completed([f_en, f_pl], timeout=fetch_budget):
                        label = 'EN' if f is f_en else 'PL'
                        try:
                            _, _, rows = f.result()
                            all_rows.extend(rows)
                            fflog(f'{label} done: {len(rows)} rows (t+{time.time() - start_time:.1f}s)', 0)
                        except Exception:
                            fflog_exc(f'{label} fetch failed, continuing with partial results')
                except TimeoutError:
                    elapsed = time.time() - start_time
                    fflog(f'PL search budget exhausted at t+{elapsed:.1f}s, returning EN-only results', 0)
            finally:
                # Do NOT block — let any still-running futures complete in background.
                executor.shutdown(wait=False)

        # ── Additional queries beyond the first pair (sequential, if time permits) ─
        for _, title_r in queries[2:]:
            if _is_cancelled():
                break
            elapsed = time.time() - start_time
            if global_timeout - elapsed - SAFETY_MARGIN < MIN_FETCH_BUDGET:
                fflog(f'skip (extra query): {title_r!r} — insufficient time remaining', 0)
                break
            try:
                _, _, rows = self._fetch_raw_rows(title_r, self.web)
                all_rows.extend(rows)
            except Exception:
                fflog_exc(f'Extra fetch failed for {title_r!r}')

        return list(dict.fromkeys(all_rows))  # deduplicate, preserve order

    def get_pages_content(self, page, year, title='', season=None, episode=None, premiered='', aliases=None, rows: list[str] | None = None):
        """Filter search result rows by title, year, and episode number."""
        if self.debug:
            if season and episode:
                _, _, _, episode_str = self._format_episode(season, episode)
            else:
                episode_str = ''
            fflog(f'{title=} {year=} {episode_str=} {page=} {premiered=}')

        if not rows:
            if page:
                if isinstance(page, range):
                    pages = page
                else:
                    pages = [page-1]
                rows = []
                for page in pages:
                    if hasattr(current_thread(), 'stop_event') and current_thread().stop_event.is_set():
                        fflog('scraper thread cancelled, exiting page loop')
                        break
                    page += 1
                    res = self.web.get_search_page(page)
                    row = client.parseDOM(res, 'tr')[1:]
                    fflog(f'[page {page}]: {len(row)} results')
                    rows += row
                fflog(f'to analyze: {len(rows)}') if isinstance(pages, range) and page > 1 else ''
                res = row = None

        if season and episode:
            # Remove potential episode number from query title
            title = re.sub(r'(s[0o]?\d{1,2})?[ _.]?(e[\do]{2,4})?$', '', title).rstrip('._ ')
        if year:
            # Remove potential year from query title
            title = ''.join(title.rsplit(year, 1)).rstrip('._ ')
        title = title.rstrip('%')
        title = title.replace('.', ' ')

        # Phrase boundaries (alternative to \b)
        boundary_start = r'(?:^|(?<=[([ _.-]))'
        boundary_end = r'(?=[)\] _.-]|$)'

        # Year pattern (1900 - 2099)
        year_universal_pat = r'\b(19\d[\dOo]|2[Oo0][\dOo]{2})\b'

        if year:
            year_pat = f"{boundary_start}{year}{boundary_end}".replace('0', '[0Oo]')

        # Pattern for checking if sequence in filename suggests it's a TV show episode
        # Matches: S01E05, .e05, cz., odc., ep., episode, odcinek, (05), - 05 [-, 1x05
        episode_universal_pat = r'((S\d{1,2})?[.,-]?E\d{2,4}|\bcz\.|\bodc\.|\bep\.|episode|odcinek|[\(\[]\d{2,3}[\)\]]|\- \d{2,3} [([-]|\b\dx\d{2}\b)'
        episode_universal_pat = episode_universal_pat.replace(r'\d', r'[\dO]').replace('0', '[0O]')

        episode_universal_pat2 = r'(S\d{2})?[.,-]?(E(\d{2,4}))'
        episode_universal_pat2 = episode_universal_pat2.replace(r'\d', r'[\dO]')

        # Variables to remember created filter
        additional_filter = self.additional_filter
        additional_filter2 = self.additional_filter2

        _filter_key = (id(self.titles), bool(season and episode))
        if self._filter_cache_key != _filter_key:
            # Franchise filter (static; depends only on self.titles)
            self._relevant_franchises = source_utils.build_relevant_franchises(
                self.titles, const.sources.franchise_names, const.sources.franchise_names_sep
            )

            # Build titles list for pattern matching
            titles = [t.lower() for t in self.titles]
            titles = list(dict.fromkeys(titles))

            # Add simplified versions of titles (only for matching, not for queries)
            extra_titles = []
            for t in titles:
                # Remove prefixes: the, a, an
                if t.startswith(('the ', 'a ', 'an ')):
                    stripped = re.sub(r'^(the|a|an) ', '', t, flags=re.IGNORECASE)
                    if stripped not in titles and stripped not in extra_titles:
                        extra_titles.append(stripped)

                # Remove .sXX from end
                if re.search(r'\.s\d{1,2}$', t):
                    base = re.sub(r'\.s\d{1,2}$', '', t)
                    if base not in titles and base not in extra_titles:
                        extra_titles.append(base)

                # Add version without separators, e.g. "f1 film" -> "f1film"
                alnum_t = re.sub(r'[^a-zA-Z0-9]', '', t)
                if len(alnum_t) > 2 and alnum_t != t and alnum_t not in titles and alnum_t not in extra_titles:
                    extra_titles.append(alnum_t)

            if extra_titles:
                titles += extra_titles

            if title and title not in titles:
                titles = [title] + titles

            titles = source_utils.add_colon_reversed_titles(titles)

            titles_pat_list = [source_utils.build_title_pattern(t) for t in titles]
            titles_pat_list = list(dict.fromkeys(titles_pat_list))

            title_pat = f"({'|'.join(titles_pat_list)})"

            # Resolution pattern
            res_pat = r'[ ._]*[(\[]?(720|1080)[pi]?[)\]]?'

            res_pat = r'\b(SD|HD|UHD|2k|4k|480p?|540p?|576p?|720p?|1080[pi]?|1440p?|2160p?)\b'
            # Pattern for commonly occurring phrases
            custom_pat = r'\b(lektor|subbed|napisy|dubbing|polish|po?l(dub|sub)?|us|fr|de|dual|multi|p2p|web[.-]?(dl)?|remux|3d|imax)\b'

            # Group name pattern (should be at the very beginning)
            group_pat = r'^[.[][^.[\]]{3,}[.\]]'

            # File extensions
            ext_pat = self.ext_pattern

            if not season and not episode:  # For movies
                after_pat = fr"(\[\w*?\]|{res_pat}|{custom_pat}|{ext_pat}$)"

                additional_filter = re.compile(
                    rf"^(\d{{1,2}}|{year_universal_pat}|{group_pat})?[ .-]*(\W?{title_pat}((?<!\d)\d|[ .-]1)?[ ./()-]{{1,4}})+((?<=[(])\d[)])?[ .-]?[(\[]?({year_universal_pat}|{after_pat})",
                    flags=re.I)

                self.additional_filter = additional_filter

            if season and episode:  # For TV shows
                episode_universal_pat = episode_universal_pat[:-1] + r'|\b\d{2,3}\b)'

                additional_filter = re.compile(
                    rf"(^({group_pat})?|[/-]|\d{{1,2}})[ .]?(\W?{title_pat}((?<!\d)\d|[ .-]1)?[ ./()-]{{1,4}})+((?<=[(])\d[)])?[ .-]?[([]?([ .-]*({res_pat}|{year_universal_pat}|{custom_pat}))*[)\]]?[ .-]*[([]?{episode_universal_pat}",
                    flags=re.I,
                )

                additional_filter2 = re.compile(
                    rf"(^\d{{1,2}}\.?\W?|[([]?{episode_universal_pat2}\W*){title_pat}([ .]*[/-]|[ .]{{2,}})",
                    flags=re.I
                )

                self.additional_filter = additional_filter
                self.additional_filter2 = additional_filter2

            self._alias_cleans = source_utils.build_alias_cleans(self.titles)
            self._filter_cache_key = _filter_key

        relevant_franchises = self._relevant_franchises

        # Check if TV show title contains a year
        year_in_title = re.search(r'\b\d{4}\b', title)
        year_in_title = year_in_title[0] if year_in_title else ''

        # Get current year
        current_year = int(time.strftime('%Y'))

        year_universal_re = source_utils.RE_YEAR_FILENAME
        year_re = re.compile(year_pat) if year else None
        episode_universal_re = source_utils.RE_EPISODE_FILENAME

        abs_ep_val = getattr(self, '_absolute_episode', None)

        # Pre-compute per-search constants (avoid repeated work inside row loop)
        year_minus_1_re = re.compile(str(int(year) - 1)) if year else None
        premiered_years = ''.join(re.findall(r'\d{4}', premiered)) if premiered else ''
        # Pre-select year/episode check as a closure — avoids evaluating constants per row
        if season and episode:
            def _passes_year_episode(fn):
                return source_utils.filename_passes_episode(fn, season, episode, abs_ep_val)
        elif not year and not season and not episode:
            def _passes_year_episode(fn):
                return True
        else:
            def _passes_year_episode(fn):
                return (
                    (year_re and year_re.search(fn))
                    or (year_minus_1_re and year_minus_1_re.search(fn))
                    or (premiered_years and re.search(premiered_years, fn))
                    or (year and not year_universal_re.search(fn) and not episode_universal_re.search(fn))
                )

        for row in rows:
            if row in self._results_set:
                continue

            filename0 = ''.join(client.parseDOM(row, 'a') or client.parseDOM(row, 'label'))

            # Reject non-video file extensions
            if filename0[-3:] not in self.VIDEO_EXTENSIONS:
                continue

            filename = filename0
            filename = unquote(filename)
            filename = unescape(filename)
            filename = filename.replace('_', ' ')
            filename = self.RE_CRC32.sub('', filename)  # Remove CRC32 from filename

            if _passes_year_episode(filename):
                # Check if filename matches searched title
                if season and episode:
                    if premiered and year_universal_re.search(filename):
                        if source_utils.check_year_in_filename(filename, year, premiered, year_in_title, current_year) is False:
                            continue

                if (
                    not additional_filter
                    or additional_filter.search(filename)
                    or (additional_filter2 and additional_filter2.search(filename))
                    or source_utils.match_filename_title_parsed(filename, self._alias_cleans)
                    or any(
                        re.search(pat, filename, re.I)
                        for pat_list in relevant_franchises.values()
                        for pat in pat_list
                    )
                ):
                    if season and episode:
                        normalized_words = filename.replace('.', ' ').replace('_', ' ').lower().split()
                        conflict_cc = source_utils.detect_country_variant_conflict(
                            normalized_words, list(self.titles)
                        )
                    else:
                        conflict_cc = None
                    if conflict_cc:
                        if self.debug:
                            fflog(f'skip (country variant {conflict_cc}): {self._decode_url_text(filename0)}')
                    else:
                        if self.debug:
                            fflog(f'match: {self._decode_url_text(filename0)}')
                        self.results.append(row)
                        self._results_set.add(row)
                else:
                    if self.debug:
                        fflog(f'skip (title mismatch): {self._decode_url_text(filename0)}')
            else:
                if self.debug:
                    fflog(f'skip (year/episode mismatch): {self._decode_url_text(filename0)}')

    @staticmethod
    def _filename_similarity(name1, name2):
        """Compare filename similarity on scale 0.0 - 1.0"""
        return SequenceMatcher(None, name1.lower(), name2.lower()).ratio()

    def prepare_filename_to_display(self, filename):
        """Prepare filename for display (allow text wrapping)"""
        filename = filename[:-4].replace('.', ' ').replace('_', ' ') + filename[-4:]
        # Remove last dash - usually followed by file "author" name
        filename = re.sub(r'-(?=\w+( \(\d\))?( [0-9A-F]{3})?\.\w{2,4}$)', ' ', filename, flags=re.I)
        filename = self.replace_audio_format_in_filename(filename)
        return filename

    def replace_audio_format_in_filename(self, filename):
        """Restore necessary dots and dashes for certain phrases"""
        replacements = [
            (r'(?<!\d)([57261]) ([10])\b', r'\1.\2'),  # Channel count: 5.1, 2.0
            (r'\b([hx]) (26[45])\b', r'\1.\2', re.I),  # h264, x264, x265, h265
            (r'\b(DDP?) (EX)\b', r'\1-\2', re.I),  # DD-EX
            (r'\b(DTS) (HD(?!-?(?:TS|cam|TV))|ES|EX|X(?![ .]26))\b', r'\1-\2', re.I),  # DTS
            (r'\b(AAC) (LC)\b', r'\1-\2', re.I),  # AAC-LC
            (r'\b(AC) (3)\b', r'\1-\2', re.I),  # AC-3
            (r'\b(HE) (AAC)\b', r'\1-\2', re.I),  # HE-AAC
            (r'\b(WEB|Blu|DVD|DCP|B[DR]|HD) (DL|Ray|RIP|Rip|TS)\b', r'\1-\2', re.I),
        ]
        for pattern in replacements:
            if len(pattern) == 3:
                old, new, flags = pattern
                filename = re.sub(old, new, filename, flags=flags)
            else:
                old, new = pattern
                filename = re.sub(old, new, filename)
        return filename

    def _make_temp_webclient(self) -> 'WebClient':
        """Temporary WebClient with cookies copied from the main session."""
        wc = WebClient(self.PROVIDER, self.base_link)
        wc.set_cookies(self.web.get_cookies())
        return wc

    def _sync_notepad_with_library(self, notes_list, library_links2, library_links, web=None):
        """Remove notepad entries that no longer exist in library history"""
        history_filenames = set()
        for library_link, _ in library_links2:
            filename = self._extract_filename_from_url(library_link)
            history_filenames.add(self._decode_url_text(filename))
        for library_link in library_links:
            filename = self._extract_filename_from_url(library_link)
            history_filenames.add(self._decode_url_text(filename))

        notes_list_cleaned = []
        removed_count = 0

        for note_item in notes_list:
            should_keep = False
            try:
                note_link = note_item[1]
                note_filename = note_item[2]
                link_filename = self._decode_url_text(self._extract_filename_from_url(note_link))

                if (note_filename and any(note_filename in hf for hf in history_filenames)) or \
                        (link_filename and link_filename in history_filenames):
                    should_keep = True
            except (IndexError, KeyError):
                should_keep = True

            if should_keep:
                notes_list_cleaned.append(note_item)
            else:
                removed_count += 1

        if removed_count > 0:
            try:
                (web or self.web).save_notepad(json.dumps(notes_list_cleaned))
            except Exception:
                fflog_exc('Error during automatic notepad cleaning')
            return notes_list_cleaned
        return notes_list

    def files_on_user_account(self, force: bool = False):
        library_cache, library_links2, notes_list = self._fetch_files_on_user_account()
        return library_cache, list(library_links2), notes_list

    def _fetch_files_on_user_account(self, web=None):
        web = web or self.web
        notes_list = []
        library_cache = {}

        # Get user notes
        if settings.getBool(f"{self.PROVIDER}.use_web_notebook_for_history"):
            notes_page_content = web.get_notepad()
            notes_value = client.parseDOM(notes_page_content, 'textarea', attrs={'class': 'notepad'})

            if notes_value:
                notes_value = notes_value[0].strip()
                if notes_value and notes_value.startswith('[') and notes_value.endswith(']'):
                    try:
                        notes_list = json.loads(notes_value)
                        for entry in notes_list:
                            if isinstance(entry, list) and len(entry) >= 5:
                                original_url, download_link, filename, size, expires = entry[:5]
                                library_cache[original_url] = {
                                    'filename': filename,
                                    'size': size,
                                    'url': original_url,
                                    'on_account_link': download_link,
                                    'on_account_expires': expires,
                                    'source': re.sub(r'https?://(?:www\.)?([^.]+)\..+', r'\1', original_url, flags=re.I).upper(),
                                }
                    except (json.JSONDecodeError, ValueError):
                        fflog('notepad parse error: unsupported format, data ignored')
                        notes_list = []
                elif notes_value:
                    fflog('notepad parse error: not a JSON list')

        # Get library history
        html = web.get_library()
        table = client.parseDOM(html, 'table', attrs={'class': 'list'})
        library_links = client.parseDOM(table, 'input', ret='value') or []
        rows = client.parseDOM(table, 'tr')[1:] if library_links else []
        library_links_exp = [client.parseDOM(row, 'td')[3] for row in rows]
        library_links2 = list(zip(library_links, library_links_exp))

        # Sync notepad with library
        if settings.getBool(f"{self.PROVIDER}.use_web_notebook_for_history") and notes_list:
            notes_list = self._sync_notepad_with_library(notes_list, library_links2, library_links, web=web)
            if notes_list:
                notepad_links = {n[1] for n in notes_list if n and len(n) > 1}
                library_links2 = [link for link in library_links2 if link[0].rpartition('/')[0] not in notepad_links]

        return library_cache, library_links2, notes_list

    def check_if_file_is_on_user_account(self, library_links, links, filenames, library_cache=None):
        match_quality = ''
        on_account = False
        on_account_expires = ''
        on_account_link = ''
        last_checked_link = None  # Track last link for Method 3

        filename = filenames
        if isinstance(links, str):
            links = [links]

        for link in links:
            last_checked_link = link  # Remember for Method 3

            # Method 1: Check via cache (notepad data) - based on original_url
            if library_cache:
                # Compare link with original_url (the KEY in library_cache)
                if link in library_cache:
                    cached_data = library_cache[link]
                    on_account = True
                    on_account_link = cached_data['on_account_link']
                    on_account_expires = cached_data['on_account_expires']
                    match_quality = ' ***'  # Perfect match from notepad
                    break

            # Method 2: Check via link comparison (older method - library history)
            if not on_account:
                for i in range(len(library_links) - 1, -1, -1):
                    item_org = library_links[i]
                    item_filename = self._extract_filename_from_url(item_org[0])
                    item = self._decode_url_text(item_filename).replace('_', ' ').replace('.', ' ')
                    url = self._decode_url_text(link).replace('_', ' ').replace('.', ' ')

                    item = self._normalize_twojplik_filename(item, link)
                    url = self._normalize_twojplik_filename(url, link)

                    if item in url:
                        on_account_link = item_org[0]
                        on_account_expires = item_org[1]
                        on_account = True
                        if on_account_link != link:
                            match_quality = ' **'  # Two stars - good match
                        else:
                            match_quality = ' ***'  # Three stars - perfect match
                        del library_links[i]
                        break

            if on_account:
                break

        # Method 3: Check via filename (uses last checked link)
        if not on_account and filenames and last_checked_link:
            filenames = [filenames] if isinstance(filenames, str) else filenames

            for i in range(len(library_links) - 1, -1, -1):
                item_org = library_links[i]
                item = item_org[0]
                item = self._normalize_twojplik_filename(item, last_checked_link)

                for filename in filenames:
                    if not filename:
                        continue
                    fn = self._normalize_twojplik_filename(filename, last_checked_link)

                    if self._decode_url_text(fn) in self._decode_url_text(item):
                        on_account_link = item_org[0]
                        on_account_expires = item_org[1]
                        on_account = True
                        match_quality = ' *'  # Worst match (by filename)
                        del library_links[i]
                        break
                if on_account:
                    break

        # Format expiration date
        if on_account_expires:
            try:
                expires_dt = datetime.strptime(on_account_expires, '%H:%M %d.%m.%Y') + timedelta(hours=24)
                on_account_expires = expires_dt.strftime('%d.%m %H:%M')
            except ValueError:  # Date is already formatted in notes
                pass
            except Exception:
                fflog_exc()
                pass

        return on_account, on_account_link, match_quality, on_account_expires

    def check_and_add_on_account_sources(self, sources: List[Source], ffitem: 'FFItem', source_name: str):
        fflog(f'checking on-account status for cached sources from {source_name}')
        try:
            self.login()
            # Update account info to get current transfer limits
            self._fetch_remaining_limit()
            library_cache, library_links = self.files_on_user_account()[:2]
            cached_links = set()
            for s in sources:
                urls = [s.url]
                filenames = [s.get('filename')]
                on_account, link, match_quality, expires = self.check_if_file_is_on_user_account(
                    library_links, urls, filenames, library_cache
                )
                if on_account:
                    s.update({
                        'on_account': True,
                        'on_account_link': link,
                        'on_account_expires': expires,
                    })
                    if match_quality and match_quality not in s.hosting:
                        s.hosting += match_quality
                    cached_links.add(link)
        except Exception:
            fflog_exc(f'Failed to check/add on-account status for cached {source_name} sources')

    def login(self):
        fflog('login: checking session', 0)

        pass_hash = hashlib.md5(self.user_pass.encode()).hexdigest()[:8]
        cache_key = f"{self.PROVIDER}_cookie_{self.user_name}_{pass_hash}"
        parallel_cache_key = f"{self.PROVIDER}_cookie_parallel_{self.user_name}_{pass_hash}"

        try:
            var = cache.cache_get(cache_key, control.providercacheFile)
            cookies = '' if var is None else var['value']
            cookie_time = 0 if var is None else int(var.get('date', 0))
        except Exception:
            fflog_exc()
            cookies = ''
            cookie_time = 0

        def _setup_parallel_session():
            if not (const.sources.xtb7.parallel_sessions and self.user_name and self.user_pass):
                return
            try:
                pvar = cache.cache_get(parallel_cache_key, control.providercacheFile)
                p_cookies = '' if pvar is None else pvar['value']
                p_time = 0 if pvar is None else int(pvar.get('date', 0))
            except Exception:
                p_cookies = ''
                p_time = 0
            if p_cookies and (int(time.time()) - p_time) < (COOKIE_EXPIRATION_HOURS * 60 * 60):
                self.web_parallel.set_cookies(p_cookies)
                fflog('login: web_parallel ok (cached)', 0)
            elif self.web_parallel.login(self.user_name, self.user_pass):
                cache.cache_insert(parallel_cache_key, self.web_parallel.get_cookies(), control.providercacheFile)
                fflog('login: web_parallel ok', 0)
            else:
                fflog('login: web_parallel failed', 0)

        # Check if cookies are current (less than 3 hours)
        if cookies and (int(time.time()) - cookie_time) < (COOKIE_EXPIRATION_HOURS * 60 * 60):
            self.web.set_cookies(cookies)
            if self.user_name and self.user_name in self.web.get_account_info():
                fflog('login: ok (cached session)', 0)
                _setup_parallel_session()
                return
            fflog('login: cached session rejected by server, re-logging in', 0)

        # Login user
        if self.user_name and self.user_pass:
            fflog('login: logging in', 0)
            if self.web.login(self.user_name, self.user_pass):
                fflog('login: ok', 0)
                cache.cache_insert(cache_key, self.web.get_cookies(), control.providercacheFile)
                _setup_parallel_session()
            else:
                fflog('login: failed')
                control.infoDialog('Błąd logowania', f'{self.PROVIDER}', 'ERROR')
        else:
            fflog('login: no credentials')
            control.infoDialog('Brak danych logowania! - sprawdź ustawienia', f'{self.PROVIDER}', 'ERROR')

    def _fetch_remaining_limit(self, force: bool = False) -> None:
        try:
            limit_info_html = self.web.get_account_info()
            limit_info_div = client.parseDOM(limit_info_html, 'div', attrs={'class': 'textPremium'})
            limit_info_b = client.parseDOM(limit_info_div, 'b')
            if limit_info_b:
                remaining_limit_str = str(limit_info_b[-1])
                remaining_limit_str = re.sub(r"\s*\w+\s*=\s*([\"']?).*?\1(?=[\s>]|$)\s*", '', remaining_limit_str)
                remaining_limit_str = re.sub('<[^>]+>', '', remaining_limit_str)
                remaining_limit_str = remaining_limit_str.replace(',', '.')
                self.remaining_limit_mb = int(source_utils.convert_size_to_bytes(remaining_limit_str) / (1024 * 1024))
                fflog(f"available limit: {self.remaining_limit_mb} MB")
        except Exception:
            fflog_exc('Failed to fetch limit information')
# ─── Tb7 ─────────────────────────────────────────────────────────────────────────


class Tb7(_source):
    """Scraper for tb7."""
    PROVIDER: ClassVar[str] = 'tb7'
    URL: ClassVar[str] = 'https://www.tb7.pl'


# ─── Xt7 ─────────────────────────────────────────────────────────────────────────

class Xt7(_source):
    """Scraper for xt7."""
    PROVIDER: ClassVar[str] = 'xt7'
    URL: ClassVar[str] = 'https://www.xt7.pl'


def register(sources: List[SourceModule], group: str) -> None:
    """Register all scrapers."""
    from lib.sources import SourceModule, SourceTitleAlias
    for src in (Tb7, Xt7):
        sources.append(SourceModule(name=src.PROVIDER, provider=src(), group=group))
