# -*- coding: utf-8 -*-
"""
FanFilm - źródło: animezone
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING, ClassVar
from urllib.parse import quote_plus

from lib.ff import requests, cleantitle, client, control, source_utils
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem
from lib.api import kitsu as kitsu_api
if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
    from lib.api.kitsu import CourInfo

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_link = 'https://www.animezone.pl'
        self.search_link = self.base_link + '/szukaj?q=%s'
        self.session = requests.Session()
        self.cookies = None

    # ── public api ─────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        if not source_utils.is_anime(self.ffitem):
            return None
        tmdb = self.ffitem.tmdb_id
        movie_info = kitsu_api.get_movie_info(tmdb) if tmdb else None
        if movie_info:
            titles = [movie_info['title'].lower()] + self._prepare_titles(title, aliases)
            titles = list(dict.fromkeys(titles))
            fflog(f'searching (kitsu): {titles}')
        else:
            titles = self._prepare_titles(title, aliases)
            fflog(f'searching: {titles}')
        return self._search(titles, None, None, None, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: list[SourceTitleAlias], year: str) -> tuple[list[str], str, list[SourceTitleAlias]]:
        titles = self._prepare_titles(tvshowtitle, aliases)
        fflog(f'searching tvshow: {titles}')
        return titles, year, aliases

    @fflog_exc
    def episode(self, url: tuple[list[str], str, list[SourceTitleAlias]], imdb: str, tvdb: str,
                title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        if not self.ffitem.show_item or not source_utils.is_anime(self.ffitem):
            return None
        tmdb = self.ffitem.show_item.tmdb_id
        aliases = url[2] if len(url) > 2 else []
        return self._search(url[0], season, episode, tmdb, aliases)

    @fflog_exc
    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        if not url:
            return []

        # fflog(f'{url=}')
        is_movie = isinstance(url, list)
        if is_movie:
            url = url[0]

        content = self.session.get(url, headers=self._get_headers())
        if not content:
            # fflog(f'error {content=}')
            return []

        # Save cookies for resolve method
        self.cookies = content.cookies

        ep_path = re.sub(r'^odcinek/', '', url.replace(self.base_link, '').strip('/'))

        # Extract episode title from <h2>
        ep_title = ''
        h2 = client.parseDOM(content.text, 'h2')
        if h2:
            ep_title = unescape(re.sub(r'<[^>]+>', '', h2[0]).strip())
            # Remove decorative quotes (animezone: 'Odcinek X: „title"')
            ep_title = re.sub(r'[„"""\u201C\u201D\u201E\u201F]', '', ep_title).strip()
            # Remove trailing colon when no title follows (e.g. 'Odcinek X:')
            ep_title = re.sub(r':\s*$', '', ep_title).strip()

        # Parse host table: name, language, data-value
        pattern = (r'<tr[^>]*>.*?<td>([^<]+)</td>.*?<td[^>]*>.*?</td>.*?<td[^>]*>.*?'
                   r'<span class="sprites ([A-Z]{2}) lang">.*?</td>.*?<td[^>]*>.*?'
                   r'data-[a-zA-Z]+="([0-9][^"]*)".*?</td>.*?</tr>')

        matches = re.findall(pattern, content.text, re.DOTALL)

        sources = []
        for idx, (host, lang, data) in enumerate(matches):
            host = host.strip().lstrip('.')
            if not host or not data:
                continue

            placeholder = f"{url}#{data}#{host}#{lang.lower()}#{idx}"

            sources.append({
                'source': host.upper(),
                'quality': '720p',
                'language': lang.lower(),
                'url': placeholder,
                'info': '',
                'filename': ep_path,
                'direct': False,
                'debridonly': False,
                'premium': False,
            })

        fflog(f'sources: {len(sources)}')
        return sources

    @fflog_exc
    def resolve(self, url: str) -> Optional[str]:
        # fflog(f'{url=}')

        # Placeholder URL: {base}#{data}#{host}#{lang}#{index}
        if '#' not in url:
            if 'sibnet' not in url:
                url = url.replace('//', '/').replace(':/', '://').split('?')[0]
            return str(url)

        parts = url.split('#')
        if len(parts) != 5:
            return None

        base_url, old_data, host, lang, index = parts
        index = int(index)
        # fflog(f'resolving: {base_url} with data: {old_data} for host: {host}, language: {lang}, index: {index}')

        # Get fresh page content using the same session and cookies as sources()
        headers = self._get_headers(referer=base_url)
        content = self.session.get(base_url, headers=headers)
        if not content:
            # fflog(f'failed to get fresh page content')
            return None

        # Extract fresh host data pairs from the page
        pattern = (r'<tr[^>]*>.*?<td>([^<]+)</td>.*?<td[^>]*>.*?</td>.*?<td[^>]*>.*?'
                   r'<span class="sprites ([A-Z]{2}) lang">.*?</td>.*?<td[^>]*>.*?'
                   r'data-[a-zA-Z]+="([0-9][^"]*)".*?</td>.*?</tr>')

        matches = re.findall(pattern, content.text, re.DOTALL)

        # Match by position/host/language combination
        fresh_data = None

        # First try exact index match
        if index < len(matches):
            page_host, page_lang, data_val = matches[index]
            page_host = page_host.strip().lstrip('.')

            if page_host.lower() == host.lower() and page_lang.lower() == lang.lower():
                fresh_data = data_val
                # fflog(f'found exact index match {index}: {page_host} ({page_lang}) -> {fresh_data}')
            # else:
                # fflog(f'index {index} mismatch: expected {host}({lang}), got {page_host}({page_lang})')

        # If index doesn't match, search by host+language
        if not fresh_data:
            for i, (page_host, page_lang, data_val) in enumerate(matches):
                page_host = page_host.strip().lstrip('.')

                if page_host.lower() == host.lower() and page_lang.lower() == lang.lower():
                    fresh_data = data_val
                    # fflog(f'found host+lang match at index {i}: {page_host} ({page_lang}) -> {fresh_data}')
                    break

        # Use fresh data if found, otherwise keep original
        data_to_use = fresh_data or old_data

        # Use the same POST logic as original working version with maintained session
        verify_headers = self._get_headers(referer=base_url, xhr=False)
        verify = self.session.get(self.base_link + '/images/statistics.gif', headers=verify_headers)
        if not verify:
            # fflog(f'error {verify=}')
            pass

        # POST request with cookies
        post_headers = self._get_headers(referer=base_url, xhr=True)
        response = self.session.post(base_url, headers=post_headers, data={'data': data_to_use})

        if not response:
            # fflog(f'error {response=}')
            if hasattr(response, 'status_code') and response.status_code == 429:
                # fflog("rate limited (429) - adding delay")
                control.sleep(2000)
            return None

        # Parse video link
        video = client.parseDOM(response.text, 'a', ret='href')
        if video:
            # fflog(f'found video link: {video[0]}')
            return video[0]

        video = client.parseDOM(response.text, 'iframe', ret='src')
        if video:
            # fflog(f'found iframe link: {video[0]}')
            return video[0]

        # fflog("no video link found in response")
        return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _prepare_titles(self, main_title: str, aliases: list[SourceTitleAlias]) -> List[str]:
        jp_aliases = [a['title'] for a in aliases if a.get('country') == 'jp']
        jp_title = jp_aliases[0] if jp_aliases else None
        titles = [jp_title, main_title]
        titles = [t for t in titles if t]
        return list(dict.fromkeys([t.lower() for t in titles]))

    def _search(self, titles: List[str], season: Optional[str], episode: Optional[str],
                tmdb: Optional[int] = None, aliases: list[SourceTitleAlias] | None = None) -> Optional[str]:
        is_episode = season is not None and episode is not None

        # ── KITSU PATH (tv shows) ─────────────────────────────────────────
        if is_episode and tmdb:
            return self._search_with_kitsu(titles, season, episode, tmdb, aliases)

        # ── MOVIES ────────────────────────────────────────────────────────────
        if not is_episode:
            titles = [t for t in titles if t]
            titles = list(dict.fromkeys([t.lower() for t in titles]))
            alias_cleans = self._build_alias_cleans(titles, aliases)
            return self._do_search(titles, alias_cleans, season, 1)

    def _search_with_kitsu(self, titles: List[str], season: str, episode: str,
                            tmdb: int, aliases: list[SourceTitleAlias]) -> Optional[str]:
        """
        Fetches cour structure from Kitsu, maps abs_ep to local number,
        then searches animezone by Kitsu en_jp title.
        """
        cours = kitsu_api.get_cour_structure(tmdb)
        if not cours:
            fflog(f'animezone/kitsu: no data for tmdb={tmdb}')
            return None

        cour, local_ep = kitsu_api.resolve_cour(cours, self.ffitem)
        if not cour:
            fflog(f'animezone/kitsu: cour not found for tmdb={tmdb} S{season}E{episode}')
            return None

        kitsu_title = cour['title']
        fflog(f'animezone/kitsu: cour="{kitsu_title}" local_ep={local_ep}')

        base_title, _ = kitsu_api.strip_season_suffix(kitsu_title)
        cour_year = str(cour['start_date'].year) if cour.get('start_date') else None

        # Search by base title first (catches "Part 2", "Season 2" variants on site),
        # then full kitsu title, then standard titles as fallback
        search_titles = [base_title.lower()]
        if base_title.lower() != kitsu_title.lower():
            search_titles.append(kitsu_title.lower())
        # Strip parenthetical qualifier "(TV)", "(OVA)" — animezone search handles it poorly
        base_stripped = re.sub(r'\s*\([^)]+\)\s*$', '', base_title).strip().lower()
        if base_stripped and base_stripped != search_titles[0]:
            search_titles.insert(1, base_stripped)
        search_titles += [t for t in titles if t]

        alias_cleans = self._build_alias_cleans([kitsu_title.lower()], aliases)
        for t in titles:
            if t:
                alias_cleans.add(cleantitle.normalize(t).replace('  ', ' ').lower())

        return self._do_search(search_titles, alias_cleans, season, local_ep,
                               kitsu_title=kitsu_title, cour_year=cour_year)

    def _build_alias_cleans(self, titles: List[str], aliases: list[SourceTitleAlias] | None) -> Set[str]:
        """Build a set of normalized titles for comparing against search results."""
        alias_cleans = set()
        if aliases:
            for alias in aliases:
                alias_title = alias.get('title', '')
                if alias_title:
                    alias_cleans.add(cleantitle.normalize(alias_title).replace('  ', ' ').lower())
                orig_name = alias.get('originalname', '')
                if orig_name:
                    alias_cleans.add(cleantitle.normalize(orig_name).replace('  ', ' ').lower())
        for title in titles:
            if title:
                alias_cleans.add(cleantitle.normalize(title).replace('  ', ' ').lower())
        return alias_cleans

    def _do_search(self, titles: List[str], alias_cleans: Set[str], season: Optional[str],
                   target_ep: int, kitsu_title: Optional[str] = None,
                   cour_year: Optional[str] = None) -> Optional[str]:
        """
        Runs queries against animezone search and returns episode URL.
        titles       — list of titles to search (order = priority)
        alias_cleans — set of normalized titles for matching results
        season       — None for movies
        target_ep    — local episode number (already resolved)
        kitsu_title  — Kitsu title for base-norm matching
        cour_year    — year from cour start_date for disambiguation
        """
        titles = [t for t in titles if t]
        titles = list(dict.fromkeys([t.lower() for t in titles]))

        base_title, _ = kitsu_api.strip_season_suffix(kitsu_title) if kitsu_title else (None, None)
        base_norm = kitsu_api.normalize_romaji(base_title) if base_title else None

        fflog(f'_do_search: titles={titles} target_ep={target_ep} cour_year={cour_year}')

        for title in titles:
            control.sleep(200)

            normalized = unicodedata.normalize('NFD', title)
            normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
            normalized = re.sub(r'[^\w\s:\-.]', ' ', normalized)  # keep ":", "." and "-" (NieR:Automata, Tamon-kun)
            normalized = re.sub(r'\s+', ' ', normalized).strip()
            normalized = normalized.replace('shippuden', 'shippuuden')

            search_url = self.search_link % quote_plus(normalized)
            fflog(f'query: {normalized!r}')
            response = self.session.get(search_url, headers=self._get_headers())

            if not response:
                fflog(f"no response for {normalized!r} (status={getattr(response, 'status_code', '?')})")
                control.sleep(200)
                continue

            fflog(f'response: {response.status_code}, len={len(response.text)}')

            results = client.parseDOM(response.text, 'div', attrs={'class': 'description pull-right'})
            links = client.parseDOM(results, 'a', ret='href')
            names = client.parseDOM(results, 'a')

            fflog(f'search: {len(links)} results')
            for idx, (link, name) in enumerate(zip(links, names)):
                clean_name = re.sub(r'<[^>]+>', '', name).strip()
                fflog(f'  [{idx}] "{clean_name}" -> {link}')

            # Kitsu path: exact → year → fallback
            if base_norm:
                kitsu_norm = kitsu_api.normalize_romaji(kitsu_title)
                fallback_url = fallback_title = None
                for link, name in zip(links, names):
                    full_title = re.sub(r'<[^>]+>', '', name).strip()
                    result_norm = kitsu_api.normalize_romaji(full_title)
                    if not result_norm.startswith(base_norm):
                        continue
                    ep_url = (self.base_link
                              + link.replace('odcinki', 'odcinek').replace('/anime/', '/odcinek/')
                              + '/' + str(target_ep))
                    # 1. Exact match (site uses same English title as kitsu)
                    if result_norm == kitsu_norm:
                        fflog(f'kitsu match (exact): "{full_title}" -> {ep_url}')
                        return [ep_url] if season is None else ep_url
                    res_year_m = re.search(r'\((\d{4})\)', full_title)
                    res_year = res_year_m.group(1) if res_year_m else None
                    # 2. Year match
                    if cour_year and res_year == cour_year:
                        fflog(f'kitsu match (year {cour_year}): "{full_title}" -> {ep_url}')
                        return [ep_url] if season is None else ep_url
                    if fallback_url is None:
                        fallback_url, fallback_title = ep_url, full_title
                if fallback_url:
                    fflog(f'kitsu match (fallback): "{fallback_title}" -> {fallback_url}')
                    return [fallback_url] if season is None else fallback_url

            for link, name in zip(links, names):
                try:
                    full_title = re.sub(r'<[^>]+>', '', name).strip()

                    # For TV shows — skip movies
                    if season is not None:
                        if 'movie' in full_title.lower() or 'movie' in link.lower():
                            fflog(f'skip (movie): "{full_title}"')
                            continue
                        if 'film' in full_title.lower() or 'film' in link.lower():
                            fflog(f'skip (film): "{full_title}"')
                            continue

                    result_title = cleantitle.normalize(full_title).replace('  ', ' ').lower()

                    matched = False
                    for mt in alias_cleans:
                        mt_words = mt.split()
                        if all(word in result_title for word in mt_words):
                            matched = True
                            fflog(f'match: "{result_title}" matches "{mt}"')
                            break

                    if not matched:
                        fflog(f'no match: "{result_title}"')
                        continue

                    url = (self.base_link
                           + link.replace('odcinki', 'odcinek').replace('/anime/', '/odcinek/')
                           + '/' + str(target_ep))
                    fflog(f'match: "{full_title}" -> {url}')
                    return [url] if season is None else url

                except Exception as ex:
                    fflog(f'exception on "{name}": {ex}')
                    continue

        fflog('no match')
        return None

    def _get_headers(self, referer: Optional[str] = None, xhr: bool = False) -> Dict[str, str]:
        headers = {
            'Host': 'www.animezone.pl',
            'User-Agent': DEFAULT_UA,
            'Accept': '*/*' if xhr else 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

        if xhr:
            headers.update({
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Pragma': 'no-cache',
                'Cache-Control': 'no-cache'
            })

        if referer:
            headers['Referer'] = referer.replace('http://', 'https://www.')

        if self.cookies:
            headers['Cookie'] = '; '.join([f"{k}={v}" for k, v in self.cookies.items()])

        return headers
