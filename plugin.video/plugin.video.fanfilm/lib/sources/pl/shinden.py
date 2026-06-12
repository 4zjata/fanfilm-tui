# -*- coding: utf-8 -*-
"""
FanFilm - źródło: shinden
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
import re

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
from lib.ff import requests, cleantitle, client, control, source_utils
from lib.ff.source_utils import DEFAULT_UA, ShowData, show_data_asdict, ShowDataDict
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.settings import settings
from lib.ff.item import FFItem
from lib.api import kitsu as kitsu_api


HEADERS = {
    'User-Agent': DEFAULT_UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
    'TE': 'Trailers',
}


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.domains = ['shinden.pl']
        self.base_link = 'https://shinden.pl'
        self.search_link = self.base_link + '/series?search=%s'
        self.user_name = settings.getString('shinden.username')
        self.user_pass = settings.getString('shinden.password')
        self.session = requests.Session()
        self.cookies = ''
        self.logged_in = False

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[Tuple[str, str]]:
        """Search for anime movie"""
        if not source_utils.is_anime(self.ffitem):
            return None

        tmdb = self.ffitem.tmdb_id
        movie_info = kitsu_api.get_movie_info(tmdb) if tmdb else None
        if movie_info:
            titles = (movie_info['title'], title, localtitle)
            fflog(f"searching (kitsu): {movie_info['title']!r}")
        else:
            titles = (title, localtitle)

        premiered = self.ffitem.vtag.getPremieredAsW3C()
        return self._search(titles, None, None, None, year, premiered, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        """Returns show data for later use in episode()"""
        tmdb = self.ffitem.show_item.tmdb_id
        # Store tmdb — needed in episode() for Kitsu lookup
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, int(year), str(tmdb) if tmdb else None))

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[Tuple[str, str]]:
        """Search for anime series episode"""
        if not self.ffitem.show_item or not source_utils.is_anime(self.ffitem):
            return None

        tmdb = self.ffitem.show_item.tmdb_id
        premiered = self.ffitem.vtag.getFirstAiredAsW3C()
        data = ShowData(**url)
        # tmdb from ShowData (from tvshow) or fetched directly from ffitem
        tmdb_id = data.tmdb or (str(tmdb) if tmdb else None)
        return self._search((data.title, data.local_title), season, episode, tmdb_id, data.year, premiered, data.aliases)

    def sources(self, url: Optional[Tuple[str, str]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        """Fetch playback sources for a given URL"""
        if not url:
            return []

        episode_title = ''
        if isinstance(url, tuple):
            url, episode_title = url

        headers = HEADERS.copy()
        headers['Cookie'] = self.cookies

        try:
            content = requests.get(url, headers=headers)
            if not content:
                return []

            # Try to extract Polish episode title from page
            pl_title = self._get_polish_episode_title(content.text)
            if pl_title:
                fflog(f'episode title: {pl_title!r}')
                episode_title = pl_title

            results = client.parseDOM(content.text, 'section', attrs={'class': 'box episode-player-list'})
            rows = client.parseDOM(results, 'tr')

            sources = []
            for row in rows:
                try:
                    src = self._parse_source_row(row, content.text, episode_title)
                    if src:
                        sources.append(src)
                except Exception:
                    fflog_exc()

            fflog(f'sources: {len(sources)}')
            return sources
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        """Resolve API URL to final video link"""
        headers = HEADERS.copy()

        if url.startswith('//'):
            url = 'http://' + url

        cookies = client.request(url, headers=headers, output='cookie')
        headers['Cookie'] = cookies

        control.sleep(5000)

        video_url = url.replace('player_load', 'player_show') + '&width=508'
        video = client.request(video_url, headers=headers)

        try:
            video = client.parseDOM(video, 'iframe', ret='src')[0]
        except Exception:
            video = client.parseDOM(video, 'a', ret='href')[0]

        if video.startswith('//'):
            video = 'https:' + video

        return str(video)

    # ── helpers ────────────────────────────────────────────────────────────

    def login(self):
        """Login to shinden.pl"""
        if self.logged_in:
            return True

        if not self.user_name or not self.user_pass:
            return False

        try:
            cookies = client.request(self.base_link + '/', output='cookie')
            if not cookies:
                cookies = ''

            headers = {
                'authority': 'shinden.pl',
                'cache-control': 'max-age=0',
                'origin': self.base_link,
                'upgrade-insecure-requests': '1',
                'dnt': '1',
                'content-type': 'application/x-www-form-urlencoded',
                'user-agent': DEFAULT_UA,
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'referer': self.base_link + '/',
                'accept-encoding': 'gzip, deflate, br',
                'accept-language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
                'Cookie': cookies
            }

            data = {
                'username': self.user_name,
                'password': self.user_pass,
                'login': '',
            }

            response = requests.post(self.base_link + '/main/0/login', headers=headers, data=data)
            if not response:
                return False

            kuki = list(response.cookies.items())
            if kuki:
                self.cookies = '; '.join([str(x) + '=' + str(y) for x, y in kuki])
                self.logged_in = True
                fflog('login: ok')
                return True

            fflog('login: failed — no cookies in response')
            return False

        except Exception:
            fflog_exc()
            return False

    def _search(self, titles, season: Optional[str], episode: Optional[str], tmdb: Optional[str], year, premiered, aliases: list[SourceTitleAlias] | None) -> Optional[Tuple[str, str]]:
        """Unified search for movies and episodes"""
        if not titles:
            return None

        headers = HEADERS.copy()
        headers['Cookie'] = self.cookies

        is_episode = season is not None and episode is not None

        # ── KITSU PATH (series) ──────────────────────────────────────────────
        if is_episode and tmdb:
            return self._search_with_kitsu(titles, season, episode, tmdb, premiered, aliases, headers, year)

        # ── MOVIES ───────────────────────────────────────────────────────────
        if not is_episode:
            try:
                results = self._get_search_results(titles, headers, aliases, is_episode)
            except Exception:
                fflog_exc()
                return None

            if not results:
                return None

            alias_cleans = self._build_alias_cleans(titles, aliases)

            self.login()
            headers['Cookie'] = self.cookies

            air_date = None
            if premiered:
                try:
                    air_date = datetime.strptime(str(premiered), '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    pass

            for link, title, raw_html in results:
                normalized = cleantitle.normalize(title)
                if any(all(word in normalized for word in mt.split()) for mt in alias_cleans):
                    title_clean = re.sub(r'<[^>]+>', '', title).strip()
                    fflog(f'movie: found {title_clean!r}, looking for ep 1')
                    full_url = self.base_link + link + '/all-episodes'
                    response = client.request(full_url, headers=headers)
                    if response:
                        return self._find_episode_in_series(response, link, 1, air_date)

        return None

    def _search_with_kitsu(self, titles, season: str, episode: str, tmdb: str, premiered, aliases: list[SourceTitleAlias] | None, headers: dict[str, str], year) -> Optional[Tuple[str, str]]:
        """
        Fetches cour structure from Kitsu, maps abs_ep to local number,
        then searches shinden by en_jp title from Kitsu.
        """
        cours = kitsu_api.get_cour_structure(tmdb)
        if not cours:
            fflog(f'shinden/kitsu: no data for tmdb={tmdb}')
            return None

        cour, local_ep = kitsu_api.resolve_cour(cours, self.ffitem)
        if not cour:
            fflog(f'shinden/kitsu: cour not found for tmdb={tmdb} S{season}E{episode}')
            return None

        fflog(f'shinden/kitsu: cour="{cour["title"]}" local_ep={local_ep}')

        # Search shinden ONLY by Kitsu title (en_jp)
        kitsu_title = cour['title']
        kitsu_norm = self._normalize_title(kitsu_title)
        fflog(f'kitsu: searching series {kitsu_title!r} (norm: {kitsu_norm!r})')

        try:
            results = self._get_search_results([kitsu_title], headers, aliases, is_episode=True)
        except Exception:
            fflog_exc()
            return None

        if not results:
            return None

        # Build list of titles to compare (Kitsu + TMDB + Aliases)
        all_alias_cleans = {kitsu_norm}
        for t in list(titles) + [a.get('title') for a in aliases or [] if a.get('title')]:
            if t:
                all_alias_cleans.add(self._normalize_title(t))

        # Find best match
        candidates = []
        kitsu_words = set(kitsu_norm.split())

        for link, title, raw_html in results:
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            title_norm = self._normalize_title(title_clean)

            # Extract year
            year_match = re.search(r'\((\d{4})\)', title_clean)
            res_year = year_match.group(1) if year_match else None

            # Skip if year does not match (string comparison)
            if res_year and year and res_year != str(year):
                continue

            # 1. PRIORITY: Exact match against Kitsu title (not aliases — too broad)
            if title_norm == kitsu_norm:
                fflog(f'kitsu: series matched (exact): {title_clean!r}')
                return self._find_local_episode_in_series([(link, title_clean)], local_ep, premiered, headers)

            title_words = set(title_norm.split())
            if res_year and res_year in title_words:
                title_words.remove(res_year)

            for mt in all_alias_cleans:
                mt_words = set(mt.split())
                if not mt_words:
                    continue
                if mt_words.issubset(title_words) or title_words.issubset(mt_words):
                    # How many kitsu_norm words overlap with Shinden — more = better match
                    overlap = len(kitsu_words & title_words)
                    # Bonus when Shinden title year matches series year (e.g. "JoJo (2012)" vs "(TV)")
                    year_bonus = 1 if (res_year and year and res_year == str(year)) else 0
                    candidates.append((link, title_clean, len(title_words), overlap, year_bonus))
                    break

        if candidates:
            # Sort: year match > overlap > shorter title
            candidates.sort(key=lambda x: (-x[4], -x[3], x[2]))
            best_link, best_title, _, _, _ = candidates[0]
            fflog(f'kitsu: series matched (subset): {best_title!r}')
            return self._find_local_episode_in_series([(best_link, best_title)], local_ep, premiered, headers)

        fflog(f'kitsu: no match for {kitsu_title!r}')
        # Log what was in results
        for link, title, raw_html in results:
            fflog(f'  result: "{self._normalize_title(title)}" (orig: {re.sub(r"<[^>]+>", "", title)})')
        return None

    def _find_local_episode_in_series(self, matching_series: List[Tuple[str, str]], local_ep: int, premiered, headers: dict[str, str]) -> Optional[Tuple[str, str]]:
        """
        Searches for a specific local episode number within a series.
        Does not do cumulative mapping — local_ep is already resolved by Kitsu.
        """
        air_date = None
        if premiered:
            try:
                air_date = datetime.strptime(premiered, '%Y-%m-%d').date()
            except Exception:
                pass

        self.login()
        # Update cookies in headers after login
        headers['Cookie'] = self.cookies

        skip_keywords = ['official pv', 'trailer', 'zwiastun', 'preview', 'promo']

        for link, title in matching_series:
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            title_lower = title_clean.lower()
            if any(kw in title_lower for kw in skip_keywords):
                fflog(f'kitsu: skip (keyword): {title_clean!r}')
                continue

            full_url = self.base_link + link + '/all-episodes'
            fflog(f'kitsu: searching ep {local_ep} in {title_clean!r} -> {full_url}')
            try:
                response = client.request(full_url, headers=headers)
                if not response:
                    fflog(f'kitsu: no response for {title_clean!r}')
                    continue

                result = self._find_episode_in_series(response, link, local_ep, air_date)
                if result:
                    fflog(f'shinden/kitsu: found ep {local_ep} in "{title_clean}" → {result[0]}')
                    return result
                else:
                    fflog(f'shinden/kitsu: ep {local_ep} not found in "{title_clean}"')
            except Exception:
                fflog_exc()
                continue

        return None

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize title: strip HTML, macrons→double vowels, punctuation, whitespace, lowercase."""
        title = re.sub(r'<[^>]+>', '', title)       # strip HTML tags
        return kitsu_api.normalize_romaji(title)

    def _build_alias_cleans(self, titles, aliases: list[SourceTitleAlias] | None) -> set:
        """Builds a set of normalized titles for matching against search results."""
        alias_cleans = set()
        if aliases:
            for alias in aliases:
                alias_title = alias.get('title', '')
                if alias_title:
                    alias_cleans.add(cleantitle.normalize(alias_title))
                orig_name = alias.get('originalname', '')
                if orig_name:
                    alias_cleans.add(cleantitle.normalize(orig_name))
        for title in titles:
            if title:
                alias_cleans.add(cleantitle.normalize(title))
        return alias_cleans

    def _get_search_results(self, titles, headers: dict[str, str], aliases, is_episode: bool) -> List[Tuple[str, str, str]]:
        """Fetch search results"""
        results = []
        search_titles = self._prepare_search_titles(titles, aliases)

        for title in search_titles:
            if isinstance(title, str):
                normalized = cleantitle.normalize(title).replace(' ', '+').replace('shippuden', 'shippuuden')
                filters = '&series_type[0]=TV&series_type[1]=ONA&series_type[2]=OVA' if is_episode else '&series_type[0]=Movie'
                filters += '&series_status[0]=Currently+Airing&series_status[1]=Finished+Airing'
                url = self.search_link % normalized + quote_plus(filters, '&=')
            else:
                url = title[0]

            if isinstance(title, str):
                fflog(f'query: {normalized!r}')
            response = self.session.get(url, headers=headers)
            if not response:
                continue

            items = client.parseDOM(response.text, 'li', attrs={'class': 'desc-col'})
            found_names = []
            for item in items:
                links = client.parseDOM(item, 'a', ret='href')
                titles_found = client.parseDOM(item, 'a')
                for i, t in enumerate(titles_found):
                    # Filter only series links (skip genres, tags, etc.)
                    if i < len(links) and ('/series/' in links[i] or '/titles/' in links[i]):
                        results.append((links[i], t, item))
                        found_names.append(t)
            if isinstance(title, str):
                fflog(f'search: {len(found_names)} results')

            # Pagination (first extra page)
            next_page = client.parseDOM(response.text, 'li', attrs={'class': 'paging-next'})
            if next_page and len(search_titles) == 1:
                next_url = client.parseDOM(next_page, 'a', ret='href')[0]
                results += self._get_search_results([(next_url,)], headers, '', is_episode)

        return results

    def _prepare_search_titles(self, titles, aliases: list[SourceTitleAlias] | None) -> List[str]:
        """Prepares list of titles for searching"""
        if not aliases:
            return [titles[0]] if titles else []

        jp_titles = [a['title'] for a in aliases if a.get('country') == 'jp']
        jp_title = jp_titles[0] if jp_titles else None
        en_title = titles[0] if titles else None
        original = next((a.get('originalname') for a in aliases if 'originalname' in a), None)

        combined = [jp_title, en_title, original]
        combined = [t.lower() for t in combined if t]
        combined = list(dict.fromkeys(combined))

        return combined

    def _find_episode_in_series(self, response: str, series_url: str, local_episode_num: int, air_date) -> Optional[Tuple[str, str]]:
        """Find a specific local episode within a given series"""
        try:
            episodes_tbody = client.parseDOM(response, 'tbody', attrs={'class': 'list-episode-checkboxes'})
            if not episodes_tbody:
                fflog(f'shinden/_find_ep: no tbody in {series_url} (len={len(response) if response else 0})')
                return None

            episode_rows = client.parseDOM(episodes_tbody, 'tr')
            if not episode_rows:
                fflog(f'shinden/_find_ep: no tr in tbody for {series_url}')
                return None

            fflog(f'shinden/_find_ep: {len(episode_rows)} episodes, searching ep {local_episode_num}')

            # Direct match by episode number (with ±1 tolerance)
            date_mismatch = False
            for offset in [0, 1, -1]:
                if date_mismatch:
                    break
                target_num = local_episode_num + offset
                for row in episode_rows:
                    cols = client.parseDOM(row, 'td')
                    if not cols:
                        continue

                    try:
                        ep_num = int(re.sub(r'<[^>]+>', '', cols[0]).strip())
                    except (ValueError, TypeError):
                        continue

                    if ep_num == target_num:
                        links = client.parseDOM(row, 'a', ret='href')
                        if links:
                            ep_title = cols[1].strip() if len(cols) > 1 else f"Odcinek {ep_num}"
                            ep_title = re.sub(r'<[^>]+>', '', ep_title).strip()
                            # Verify date only for exact match (not ±1)
                            if offset == 0 and air_date and len(cols) >= 5:
                                try:
                                    ep_date_str = re.sub(r'<[^>]+>', '', cols[4]).strip()
                                    if '.' in ep_date_str:
                                        ep_date = datetime.strptime(ep_date_str, '%d.%m.%Y').date()
                                    elif '-' in ep_date_str:
                                        ep_date = datetime.strptime(ep_date_str, '%Y-%m-%d').date()
                                    else:
                                        ep_date = None
                                    if ep_date and ep_date != air_date:
                                        fflog(
                                            f'shinden/_find_ep: ep {ep_num} found by number but date {ep_date} != {air_date} — trying by date')
                                        date_mismatch = True
                                        break
                                except Exception:
                                    pass
                            if not date_mismatch:
                                return (self.base_link + links[0], ep_title)

            # Fallback: match by air date
            if air_date:
                for row in episode_rows:
                    cols = client.parseDOM(row, 'td')
                    if len(cols) < 5:
                        continue

                    try:
                        ep_num = int(re.sub(r'<[^>]+>', '', cols[0]).strip())
                        ep_date_str = re.sub(r'<[^>]+>', '', cols[4]).strip()

                        if '.' in ep_date_str:
                            ep_date = datetime.strptime(ep_date_str, '%d.%m.%Y').date()
                        elif '-' in ep_date_str:
                            ep_date = datetime.strptime(ep_date_str, '%Y-%m-%d').date()
                        else:
                            continue

                        if ep_date == air_date:
                            links = client.parseDOM(row, 'a', ret='href')
                            if links:
                                ep_title = cols[1].strip() if len(cols) > 1 else f"Odcinek {ep_num}"
                                ep_title = re.sub(r'<[^>]+>', '', ep_title).strip()
                                return (self.base_link + links[0], ep_title)
                    except Exception:
                        continue

            return None

        except Exception:
            fflog_exc()
            return None

    @staticmethod
    def _get_polish_episode_title(html: str) -> Optional[str]:
        """Extracts the Polish episode title from the 'Titles in other languages' section."""
        try:
            section = client.parseDOM(html, 'section', attrs={'class': 'episode-other-titles box'})
            if not section:
                return None
            rows = client.parseDOM(section, 'tr')
            for row in rows:
                cols = client.parseDOM(row, 'td')
                if len(cols) < 4:
                    continue
                lang = re.sub(r'<[^>]+>', '', cols[2]).strip()
                if lang == 'Polski':
                    title = re.sub(r'<[^>]+>', '', cols[1]).strip()
                    if title:
                        return title
        except Exception:
            fflog_exc()
        return None

    def _parse_source_row(self, row: str, page_content: str, episode_title: str = '') -> SourceItem | None:
        """Parse a playback source row"""
        cols = client.parseDOM(row, 'td')
        if not cols or len(cols) < 6:
            return None

        host = 'VIDOZA' if 'vidoza' in cols[0].lower() else cols[0]
        quality = source_utils.check_sd_url(cols[1])

        audio = client.parseDOM(cols[2], 'span', attrs={'class': 'mobile-hidden'})
        audio = audio[0] if audio else ''

        is_polish = 'Polski' in audio
        language = 'pl' if is_polish else ''

        if is_polish:
            info = 'Polskie Audio'
        else:
            subs = client.parseDOM(cols[3], 'span', attrs={'class': 'mobile-hidden'})
            subs = subs[0] if subs else ''
            info = f"{subs}e Napisy" if subs and '--' not in subs else ''

        vid_id = re.findall(r'''data_(.*?)\"''', str(cols[5]))
        code = re.findall(r"""_Storage\.basic.*=.*'(.*?)'""", page_content)

        if not vid_id or not code:
            return None

        video_link = f"https://api4.shinden.pl/xhr/{vid_id[0]}/player_load?auth={code[0]}"
        filename = episode_title if episode_title else ''

        return {
            'source': host,
            'quality': quality,
            'language': language,
            'url': video_link,
            'info': info,
            'filename': filename,
            'direct': False,
            'debridonly': False,
            'premium': False,
        }
