# -*- coding: utf-8 -*-
"""
FanFilm - źródło: frixysubs
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

        self.base_link = 'https://frixysubs.pl'
        self.api_link = self.base_link + '/api'
        self.search_link = f"{self.api_link}/anime?offset=0&limit=50&search=%s"
        self.episode_link = f"{self.api_link}/anime/%s"
        self.session = requests.Session()
        self.headers = {
            'User-Agent': DEFAULT_UA,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
            'Accept-Encoding': 'identity',
            'Connection': 'keep-alive',
            'Referer': self.base_link + '/',
        }

    # ── public api ─────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        if not source_utils.is_anime(self.ffitem):
            return
        tmdb = self.ffitem.tmdb_id
        movie_info = kitsu_api.get_movie_info(tmdb) if tmdb else None
        if movie_info:
            titles = [movie_info['title'], title]
            fflog(f'frixysubs/movie: kitsu="{movie_info["title"]}"')
        else:
            jp_aliases = [alias['title'] for alias in aliases if alias['country'] == 'jp']
            titles = (jp_aliases[:1] if jp_aliases else []) + [title]
        return self._search(titles, None, None, None, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> tuple[list[str], str, list[SourceTitleAlias]]:
        jp_aliases = [alias['title'] for alias in aliases if alias['country'] == 'jp']
        jp_titles_from_aliases = jp_aliases[:1] if jp_aliases else []
        titles = jp_titles_from_aliases + [tvshowtitle]
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
        if not url:
            return sources

        try:
            if isinstance(url, list):
                url = url[0]

            anime_link, episode_number = url.split('|')
            episode_number = int(episode_number)

            resp = self.session.get(self.episode_link % anime_link, headers=self.headers)
            if not resp or resp.status_code != 200:
                return sources

            try:
                data = resp.json()
            except Exception:
                fflog_exc()
                return sources

            episodes = data.get('episodes', [])
            if not episodes:
                return sources

            if isinstance(episodes, dict):
                episodes = episodes.get('episodes', [])

            target_episode = None
            for ep in episodes:
                if isinstance(ep, dict):
                    if ep.get('number') == episode_number:
                        target_episode = ep
                        break

            if not target_episode:
                return sources

            players = target_episode.get('players', [])
            for player in players:
                try:
                    player_name = player.get('name', 'Unknown')
                    player_link = player.get('link', '')

                    if not player_link:
                        continue

                    sources.append({
                        'source': player_name.upper(),
                        'quality': '720p',
                        'language': 'pl',
                        'url': player_link,
                        'info': '',
                        'filename': anime_link,
                        'direct': False,
                        'debridonly': False,
                        'premium': False,
                    })

                except Exception:
                    fflog_exc()
                    continue

            fflog(f'sources: {len(sources)}')
            return sources

        except Exception:
            fflog_exc()
            return sources

    @fflog_exc
    def resolve(self, url: str) -> Optional[str]:
        try:
            return url
        except Exception:
            fflog_exc()
            return url

    # ── helpers ────────────────────────────────────────────────────────────

    @fflog_exc
    def _search_with_kitsu(self, titles: List[str], season: str, episode: str, tmdb: Optional[str], aliases: list[SourceTitleAlias]) -> Optional[str]:
        cours = kitsu_api.get_cour_structure(tmdb)
        if not cours:
            fflog(f'frixysubs/kitsu: no data for tmdb={tmdb}')
            return None

        cour, local_ep = kitsu_api.resolve_cour(cours, self.ffitem)
        if not cour:
            fflog(f'frixysubs/kitsu: cour not found for tmdb={tmdb}')
            return None

        kitsu_title = cour['title']
        kitsu_norm = kitsu_api.normalize_romaji(kitsu_title)
        fflog(f'frixysubs/kitsu: cour="{kitsu_title}" local_ep={local_ep}')

        control.sleep(200)
        title_normalized = unicodedata.normalize('NFD', kitsu_title)
        title_normalized = ''.join(c for c in title_normalized if unicodedata.category(c) != 'Mn')
        title_encoded = quote_plus(title_normalized)
        search_url = self.search_link % title_encoded
        fflog(f'frixysubs/kitsu: searching "{kitsu_title}"')

        resp = self.session.get(search_url, headers=self.headers)
        if not resp or resp.status_code != 200:
            return None

        try:
            data = resp.json()
        except Exception:
            fflog_exc()
            return None

        if not data.get('series'):
            fflog('frixysubs/kitsu: no results')
            return None

        fflog(f"frixysubs/kitsu: results: {[a.get('title') for a in data['series']]}")

        candidates = []
        tmdb_year = str(self.ffitem.vtag.getYear()) if self.ffitem.vtag.getYear() else None
        kitsu_words = set(kitsu_norm.split())

        for anime in data['series']:
            if anime.get('movieseries'):
                continue
            anime_title = anime.get('title', '').strip()
            anime_link = anime.get('link', '')

            # Extract year from title
            year_match = re.search(r'\((\d{4})\)', anime_title)
            res_year = year_match.group(1) if year_match else None

            if res_year and tmdb_year and res_year != tmdb_year:
                continue

            anime_norm = kitsu_api.normalize_romaji(anime_title)

            # 1. PRIORITY: Exact match
            if anime_norm == kitsu_norm:
                fflog(f'frixysubs/kitsu: matched "{anime_title}" (exact) → ep {local_ep}')
                return self._get_episode_link(anime_link, local_ep)

            anime_words = set(anime_norm.split())
            if res_year and res_year in anime_words:
                anime_words.remove(res_year)

            if kitsu_words.issubset(anime_words) or anime_words.issubset(kitsu_words):
                # 2. PRIORITY: Matching year
                if res_year and res_year == tmdb_year:
                    fflog(f'frixysubs/kitsu: matched "{anime_title}" (year) → ep {local_ep}')
                    return self._get_episode_link(anime_link, local_ep)

                candidates.append((anime_link, anime_title, len(anime_words)))

        if candidates:
            candidates.sort(key=lambda x: x[2])
            anime_link, anime_title, _ = candidates[0]
            fflog(f'frixysubs/kitsu: matched "{anime_title}" (subset) → ep {local_ep}')
            return self._get_episode_link(anime_link, local_ep)

        fflog(f'frixysubs/kitsu: no match for "{kitsu_title}"')
        return None

    @fflog_exc
    def _search(self, titles: List[str], season: Optional[str], episode: Optional[str], tmdb: Optional[str], aliases: list[SourceTitleAlias] | None = None) -> Optional[str]:
        try:
            if season is not None and episode is not None:
                absolute_ep = self.ffitem.absolute_episode_number()
                if absolute_ep is None:
                    return
                target_episode = absolute_ep
            else:
                target_episode = 1

            titles = list(filter(None, titles))
            titles = [t.lower() for t in titles]
            titles = list(dict.fromkeys(titles))

            # Build set of all titles for comparison
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

            for title in titles:
                if not title:
                    continue

                control.sleep(200)

                fflog(f'query: {title!r}')

                title_normalized = unicodedata.normalize('NFD', title)
                title_normalized = ''.join(c for c in title_normalized if unicodedata.category(c) != 'Mn')
                title_encoded = quote_plus(title_normalized)

                search_url = self.search_link % title_encoded

                resp = self.session.get(search_url, headers=self.headers)
                if not resp or resp.status_code != 200:
                    control.sleep(500)
                    continue

                try:
                    data = resp.json()
                except Exception:
                    fflog_exc()
                    continue

                fflog(f'search: {len(data.get("series", []))} results')
                if not data.get('series'):
                    # fflog('no series in response')
                    continue

                for anime in data['series']:
                    anime_title = anime.get('title', '').strip()
                    anime_link = anime.get('link', '')
                    is_movie = anime.get('movieseries', False)

                    fflog(f'found anime: "{anime_title}", link: {anime_link}, movie: {is_movie}')

                    result_normalized = cleantitle.normalize(anime_title).lower()
                    matched = any(
                        all(word in result_normalized for word in mt.split())
                        for mt in alias_cleans
                    )
                    if matched:
                        # For movies, return directly for episode 1
                        if is_movie and season is None:
                            return f"{anime_link}|1"
                        # For series, get episode link
                        elif not is_movie and season is not None:
                            episode_url = self._get_episode_link(anime_link, target_episode)
                            if episode_url:
                                return episode_url
                            else:
                                return None  # Stop searching if episode not found

        except Exception:
            fflog_exc()
            return

    def _get_episode_link(self, anime_link: str, episode_number: int) -> Optional[str]:
        """Get specific episode link from anime API"""
        try:
            r = self.session.get(self.episode_link % anime_link, headers=self.headers)
            if not r or r.status_code != 200:
                return None

            try:
                data = r.json()
            except Exception:
                fflog_exc()
                return None

            episodes = data.get('episodes', [])
            if not episodes:
                return None

            # Check if episodes is a list or dict
            if isinstance(episodes, dict):
                episodes = episodes.get('episodes', [])

            for ep in episodes:
                if isinstance(ep, dict):
                    ep_number = ep.get('number', 0)
                    if ep_number == episode_number:
                        return f"{anime_link}|{episode_number}"

            return None

        except Exception:
            fflog_exc()
            return None
