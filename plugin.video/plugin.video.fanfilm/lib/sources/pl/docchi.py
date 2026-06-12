# -*- coding: utf-8 -*-
"""
FanFilm - źródło: docchi
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote_plus
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar

from lib.ff import requests

if TYPE_CHECKING:
    from lib.sources import SourceItem, ClassVar, SourceTitleAlias
from lib.ff import cleantitle, control, source_utils
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem
from lib.api import kitsu as kitsu_api

# ─── source ──────────────────────────────────────────────────────────────────────

class source:

    # set in ff/sources.py
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):

        self.base_link = 'https://docchi.pl'
        self.api_base = 'https://api.docchi.pl/v1'
        self.search_link = f"{self.api_base}/series/related/%s"
        self.list_link = f"{self.api_base}/series/list"
        self.session = requests.Session()
        self.headers = {
            'Host': 'docchi.pl',
            'User-Agent': DEFAULT_UA,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
            'Accept-Encoding': 'identity',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

    # ── public api ─────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        if not source_utils.is_anime(self.ffitem):
            return
        tmdb = self.ffitem.tmdb_id
        movie_info = kitsu_api.get_movie_info(tmdb) if tmdb else None
        if movie_info and movie_info.get('mal_id'):
            mal_id = movie_info['mal_id']
            fflog(f'docchi/movie: kitsu="{movie_info["title"]}" mal_id={mal_id}')
            api_headers = self.headers.copy()
            api_headers['Host'] = 'api.docchi.pl'
            api_headers['Accept'] = 'application/json'
            resp = self.session.get(f'{self.api_base}/series/related/{mal_id}', headers=api_headers)
            if resp and resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        series_slug = data[0].get('slug', '')
                        series_title = data[0].get('title', '')
                        if series_slug:
                            fflog(f'docchi/movie: found "{series_title}" slug="{series_slug}"')
                            self._current_episode = 1
                            return series_slug
                except Exception:
                    fflog_exc()
            fflog(f'docchi/movie: not found in database (mal_id={mal_id})')
            return None
        # no MAL ID — title fallback
        jp_aliases = [alias['title'] for alias in aliases if alias['country'] == 'jp']
        titles = (jp_aliases[:1] if jp_aliases else []) + [title]
        fflog(f'docchi/movie: title fallback {titles}')
        return self._search(titles, None, None, None, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> tuple[list[str], str, list[SourceTitleAlias]]:
        jp_aliases = [alias['title'] for alias in aliases if alias['country'] == 'jp']
        jp_titles_from_aliases = jp_aliases[:1] if jp_aliases else []
        titles = jp_titles_from_aliases + [tvshowtitle]
        fflog(f'tvshow search titles: {titles}')
        return titles, year, aliases

    @fflog_exc
    def episode(self, url: tuple[list[str], str, list[SourceTitleAlias]], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        if self.ffitem.show_item is None:
            return None
        if source_utils.is_anime(self.ffitem):
            tmdb = self.ffitem.show_item.tmdb_id
            aliases = url[2] if len(url) > 2 else []
            return self._search_with_kitsu(url[0], season, episode, str(tmdb) if tmdb else None, aliases)
        return None

    @fflog_exc
    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        # fflog(f'sources called with URL: {url}')
        if not url:
            # fflog('no URL provided')
            return sources

        try:
            # URL should be a series slug from search_ep_or_movie
            series_slug = url
            if isinstance(url, list):
                series_slug = url[0]
                episode_num = 1  # Movie
                # fflog(f'movie mode, using slug: {series_slug}')
            else:
                # Extract episode number from context or use absolute number from earlier
                episode_num = getattr(self, '_current_episode', 1)
                # fflog(f'series mode, slug: {series_slug}, episode: {episode_num}')

            # Get episode sources using API
            api_headers = self.headers.copy()
            api_headers['Host'] = 'api.docchi.pl'

            api_url = f"{self.api_base}/episodes/find/{series_slug}/{episode_num}"
            fflog(f'query: {series_slug!r} ep={episode_num}')

            api_response = self.session.get(api_url, headers=api_headers)

            if api_response and api_response.status_code == 200:
                try:
                    episode_data = api_response.json()
                    # fflog(f'API response keys: {list(episode_data.keys()) if isinstance(episode_data, dict) else "not dict"}')
                    # fflog(f'episodes API full JSON: {episode_data}')

                    # Handle different response formats
                    players_list = []
                    if isinstance(episode_data, list):
                        players_list = episode_data
                    elif 'players' in episode_data:
                        players_list = episode_data['players']
                    elif 'data' in episode_data:
                        players_list = episode_data['data']

                    fflog(f'search: {len(players_list)} results')

                    for i, player in enumerate(players_list):
                        # fflog(f'player {i} full object: {player}')

                        # Use correct keys from actual API response
                        player_url = player.get('player', '')
                        hosting = player.get('player_hosting', 'Unknown')
                        translator_name = player.get('translator_title', '')

                        # fflog(f'player {i}: hosting={hosting}, translator_name={translator_name}, url={player_url[:50] if player_url else "NO_URL"}...')

                        if player_url:
                            # Determine quality based on URL or default
                            quality = '720p'
                            if 'forcedQuality=hd720' in player_url:
                                quality = '720p'
                            elif 'forcedQuality=hd1080' in player_url:
                                quality = '1080p'
                            elif 'forcedQuality=sd' in player_url:
                                quality = 'SD'

                            # Determine language
                            language = 'pl'
                            if translator_name:
                                if any(word in translator_name.lower() for word in ['eng', 'english']):
                                    language = 'en'

                            info = ''

                            slug_display = re.sub(r'-\d+$', '', series_slug)
                            filename = f'{translator_name} | {slug_display}' if translator_name else slug_display
                            sources.append({
                                'source': hosting.upper(),
                                'quality': quality,
                                'language': language,
                                'url': player_url,
                                'info': info,
                                'filename': filename,
                                'direct': False,
                                'debridonly': False,
                                'premium': False,
                            })

                            fflog(f'added source: {hosting.upper()} - {quality} - {translator_name}')
                        # else:
                            # fflog(f'player {i} has no URL, skip')

                except Exception as e:
                    fflog_exc()
                    fflog(f'API JSON parse error: {e}')
                    # fflog(f'API raw response: {api_response.text[:500]}')
            # else:
                # fflog(f'API failed with status: {api_response.status_code if api_response else "No response"}')

            fflog(f'sources: {len(sources)}')
            return sources

        except Exception:
            fflog_exc()
            return sources

    @fflog_exc
    def resolve(self, url: str) -> Optional[str]:
        # fflog(f'resolve called with URL: {url}')
        # fflog(f'returning URL as-is: {url}')
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _contains_word(self, str_to_check: str, word: str) -> bool:
        if str(word).lower() in str(str_to_check).lower():
            return True
        return False

    def _contains_all_words(self, str_to_check: str, words: List[str]) -> bool:
        for word in words:
            if not self._contains_word(str_to_check, word):
                return False
        return True

    @fflog_exc
    def _search_with_kitsu(self, titles: List[str], season: str, episode: str, tmdb: Optional[str], aliases: list[SourceTitleAlias]) -> Optional[str]:
        cours = kitsu_api.get_cour_structure(tmdb)
        if not cours:
            fflog(f'docchi/kitsu: no data for tmdb={tmdb}')
            return None

        cour, local_ep = kitsu_api.resolve_cour(cours, self.ffitem)
        if not cour:
            fflog(f'docchi/kitsu: cour not found for tmdb={tmdb}')
            return None

        kitsu_title = cour['title']
        mal_id = cour.get('mal_id')
        fflog(f'docchi/kitsu: cour="{kitsu_title}" mal_id={mal_id} local_ep={local_ep}')

        if not mal_id:
            fflog(f'docchi/kitsu: no mal_id for "{kitsu_title}"')
            return None

        control.sleep(200)
        api_headers = self.headers.copy()
        api_headers['Host'] = 'api.docchi.pl'
        api_headers['Accept'] = 'application/json'

        related_url = f'{self.api_base}/series/related/{mal_id}'
        fflog(f'docchi/kitsu: searching mal_id={mal_id}')

        resp = self.session.get(related_url, headers=api_headers)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                if isinstance(data, list) and data:
                    series = data[0]
                    series_slug = series.get('slug', '')
                    series_title = series.get('title', '')
                    if series_slug:
                        fflog(f'docchi/kitsu: found "{series_title}" slug="{series_slug}" → ep {local_ep}')
                        self._current_episode = local_ep
                        return series_slug
            except Exception:
                fflog_exc()

        fflog(f'docchi/kitsu: no results for mal_id={mal_id}')
        return None

    @fflog_exc
    def _search(self, titles: List[str], season: Optional[str], episode: Optional[str], tmdb: Optional[str], aliases: list[SourceTitleAlias] | None = None) -> Optional[str]:
        # fflog(f'search_ep_or_movie: {titles=} {season=} {episode=} {tmdb=}')
        try:
            if season is not None and episode is not None:
                odcinek = self.ffitem.absolute_episode_number()
                # fflog(f'absolute episode number: {odcinek}')
                if odcinek is None:
                    # fflog('could not determine absolute episode number')
                    return
            else:
                odcinek = 1
                # fflog('movie mode, using episode 1')

            titles = list(filter(None, titles))
            titles = [t.lower() for t in titles]
            titles = list(dict.fromkeys(titles))
            fflog(f'search titles: {titles}')

            # Build set of all titles for comparison (aliases + search titles)
            alias_cleans = set()
            if aliases:
                for alias in aliases:
                    alias_title = alias.get('title', '')
                    if alias_title:
                        alias_cleans.add(cleantitle.normalize(alias_title).lower())
                    orig_name = alias.get('originalname', '')
                    if orig_name:
                        alias_cleans.add(cleantitle.normalize(orig_name).lower())
            for search_title in titles:
                if search_title:
                    alias_cleans.add(cleantitle.normalize(search_title).lower())

            fflog(f'alias_cleans: {alias_cleans}')

            for title in titles:
                if not title:
                    continue
                control.sleep(200)

                # Normalize unicode characters (remove diacritics like ū, û)
                title_normalized = unicodedata.normalize('NFD', title)
                title_normalized = ''.join(c for c in title_normalized if unicodedata.category(c) != 'Mn')
                title_normalized = title_normalized.replace('shippuden', 'shippuuden')

                # Try multiple API approaches with full JSON logging
                api_headers = self.headers.copy()
                api_headers['Host'] = 'api.docchi.pl'
                api_headers['Accept'] = 'application/json'

                # Approach 1: Try series/related endpoint
                search_query = quote_plus(title_normalized)
                related_url = f"{self.api_base}/series/related/{search_query}"
                # fflog(f'trying related API: {related_url}')

                resp = self.session.get(related_url, headers=api_headers)
                if resp:
                    # fflog(f'related API status: {resp.status_code}')
                    # fflog(f'related API headers: {dict(resp.headers)}')
                    try:
                        related_data = resp.json()
                        # fflog(f'related API full JSON: {related_data}')

                        if related_data and isinstance(related_data, dict):
                            # Check if this is a series object itself
                            if 'slug' in related_data and 'title' in related_data:
                                series_title = related_data.get('title', '').lower()
                                series_slug = related_data.get('slug', '')
                                clean_title = cleantitle.normalize(
                                    cleantitle.getsearch(series_title)).replace('  ', ' ')
                                words = clean_title.split(' ')

                                if any(self._contains_all_words(mt, words) for mt in alias_cleans) and series_slug:
                                    # fflog(f'match in related API: {series_title} -> {series_slug}')
                                    self._current_episode = odcinek
                                    return series_slug
                    except Exception:
                        fflog_exc()

                # Approach 2: Try series/list endpoint with search
                list_url = f"{self.api_base}/series/list?limit=100"
                # fflog(f'trying list API: {list_url}')

                resp = self.session.get(list_url, headers=api_headers)
                if resp and resp.status_code == 200:
                    # fflog(f'list API status: {resp.status_code}')
                    try:
                        list_data = resp.json()
                        # fflog(f'list API response type: {type(list_data)}')

                        if isinstance(list_data, dict):
                            # fflog(f'list API keys: {list(list_data.keys())}')
                            # Common response formats
                            series_list = list_data.get('data', list_data.get('series', list_data.get('results', [])))
                        elif isinstance(list_data, list):
                            series_list = list_data
                        else:
                            series_list = []

                        # fflog(f'list API found {len(series_list)} series')

                        # Log first few series for structure analysis
                        # for i, series in enumerate(series_list[:3]):
                        #     fflog(f'list API sample series {i}: {series}')

                        # Search through the list
                        for series in series_list:
                            if not isinstance(series, dict):
                                continue

                            series_title = series.get('title', '').lower()
                            series_slug = series.get('slug', '')
                            alt_titles = series.get('alternative_titles', [])

                            # Check main title and alternatives
                            all_titles = [series_title] + [alt.lower() for alt in alt_titles if alt]

                            for check_title in all_titles:
                                clean_title = cleantitle.normalize(cleantitle.getsearch(check_title)).replace('  ', ' ')
                                words = clean_title.split(' ')

                                if any(self._contains_all_words(mt, words) for mt in alias_cleans) and series_slug:
                                    # fflog(f'match in list API: {check_title} -> {series_slug}')
                                    self._current_episode = odcinek
                                    return series_slug

                    except Exception:
                        fflog_exc()

                # Approach 3: Try direct series/find with guessed slugs
                # fflog('trying direct find API with slug guesses')
                base_slug = title_normalized.lower().replace('"', '').replace("'", '').replace(':', '')
                slug_guesses = [
                    base_slug.replace(' ', '-'),
                    base_slug.replace(' no ', '-').replace(' ', '-'),
                    base_slug.replace(' ', '-').replace('--', '-'),
                ]
                # Add slugs from aliases
                for mt in alias_cleans:
                    alias_slug = re.sub(r'["\':]+', '', mt).replace(' ', '-')
                    alias_slug = re.sub(r'-+', '-', alias_slug).strip('-')
                    if alias_slug and alias_slug not in slug_guesses:
                        slug_guesses.append(alias_slug)

                for slug_guess in slug_guesses:
                    slug_guess = re.sub(r'-+', '-', slug_guess).strip('-')
                    if not slug_guess:
                        continue

                    find_url = f"{self.api_base}/series/find/{slug_guess}"
                    fflog(f'docchi find API: {slug_guess}')

                    resp = self.session.get(find_url, headers=api_headers)
                    if resp:
                        # fflog(f'find API status: {resp.status_code}')
                        if resp.status_code == 200:
                            try:
                                find_data = resp.json()
                                # fflog(f'find API full JSON: {find_data}')

                                if isinstance(find_data, dict) and 'slug' in find_data:
                                    series_slug = find_data.get('slug', slug_guess)
                                    # fflog(f'found via find API: {series_slug}')
                                    self._current_episode = odcinek
                                    return series_slug
                            except Exception:
                                fflog_exc()
                    control.sleep(100)

            fflog('no match')

        except Exception:
            fflog_exc()
            return
