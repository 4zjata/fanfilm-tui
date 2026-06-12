# -*- coding: utf-8 -*-
"""
FanFilm - źródło: desu-online
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
import base64
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
from urllib.parse import quote_plus

from lib.ff import requests
from lib.ff import cleantitle, client, control, source_utils
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem
from lib.api import kitsu as kitsu_api
if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:

    # set in ff/sources.py
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):

        self.base_link = 'https://desu-online.pl'
        self.search_link = self.base_link + '/?s=%s'
        self.session = requests.Session()
        self.headers = {
            'Host': 'desu-online.pl',
            'User-Agent': DEFAULT_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
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
        if movie_info:
            titles = [movie_info['title'], title]
            fflog(f'desuonline/movie: kitsu="{movie_info["title"]}"')
        else:
            jp_aliases = [alias['title'] for alias in aliases if alias['country'] == 'jp']
            titles = (jp_aliases[:1] if jp_aliases else []) + [title]
        return self._search(titles, None, None, None, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: list[SourceTitleAlias], year: str) -> tuple[list[str], str, list[SourceTitleAlias]]:
        jp_aliases = [alias['title'] for alias in aliases if alias['country'] == 'jp']
        jp_titles_from_aliases = jp_aliases[:1] if jp_aliases else []
        titles = jp_titles_from_aliases + [tvshowtitle]
        return titles, year, aliases

    @fflog_exc
    def episode(self, url: tuple[list[str], str, list[SourceTitleAlias]], imdb: str, tvdb: str,
                title: str, premiered: str, season: str, episode: str) -> Optional[str]:
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

            ep_path = url.replace(self.base_link, '').strip('/')
            # fflog(f'fetching URL: {url}')
            resp = self.session.get(url, headers=self.headers)
            if not resp or resp.status_code != 200:
                return sources

            content = resp.text
            # fflog(f'URL response: {resp.url}')

            # Fix newlines in content first like original parseDOM
            content = re.sub(r'(<[^>]*?)\n([^>]*?>)', r'\1 \2', content)

            sourcebox = client.parseDOM(content, 'select', attrs={'class': 'mirror'})
            if not sourcebox:
                return sources

            all_options = client.parseDOM(sourcebox[0], 'option')
            all_values = client.parseDOM(sourcebox[0], 'option', ret='value')

            # fflog(f'all options: {all_options}')
            # fflog(f'all values: {all_values}')

            players = all_options[1:]  # Skip first
            values = [i for i in all_values if i != '']

            # fflog(f'players after skip: {players}')
            # fflog(f'values after filter: {values}')

            player_value_pairs = list(zip(players, values))

            for i, (player, value) in enumerate(player_value_pairs):
                try:
                    decoded = self.decrypt(value)
                    if not decoded:
                        continue

                    iframe_srcs = client.parseDOM(decoded, 'iframe', ret='src')
                    if not iframe_srcs:
                        continue

                    iframe_url = iframe_srcs[0]

                    if iframe_url.startswith('//'):
                        iframe_url = 'https:' + iframe_url
                    elif iframe_url.startswith('/'):
                        iframe_url = self.base_link + iframe_url

                    host_match = re.search(r'https?://(?:www\.)?([^/.]+)', iframe_url)
                    host_name = host_match.group(1) if host_match else player
                    host_name = host_name.upper()

                    sources.append({
                        'source': host_name,
                        'quality': '720p',
                        'language': 'pl',
                        'url': iframe_url,
                        'info': '',
                        'filename': ep_path,
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
    def resolve(self, url: str) -> str:
        try:
            return url
        except Exception:
            fflog_exc()
            return url

    # ── helpers ────────────────────────────────────────────────────────────

    def decrypt(self, hashed: str) -> str:
        """Decode base64 data"""
        try:
            return base64.b64decode(hashed.encode('ascii')).decode('ascii')
        except Exception:
            fflog_exc()
            return ''

    @fflog_exc
    def _search_with_kitsu(self, titles: List[str], season: str, episode: str,
                            tmdb: Optional[str], aliases: list[SourceTitleAlias]) -> Optional[str]:
        cours = kitsu_api.get_cour_structure(tmdb)
        if not cours:
            fflog(f'desuonline/kitsu: no data for tmdb={tmdb}')
            return None

        cour, local_ep = kitsu_api.resolve_cour(cours, self.ffitem)
        if not cour:
            fflog(f'desuonline/kitsu: cour not found for tmdb={tmdb}')
            return None

        kitsu_title = cour['title']
        fflog(f'desuonline/kitsu: cour="{kitsu_title}" local_ep={local_ep}')

        base_title, _ = kitsu_api.strip_season_suffix(kitsu_title)
        base_norm = kitsu_api.normalize_romaji(base_title)
        kitsu_norm = kitsu_api.normalize_romaji(kitsu_title)
        cour_year = str(cour['start_date'].year) if cour.get('start_date') else None

        control.sleep(200)
        query = unicodedata.normalize('NFD', base_title)
        query = ''.join(c for c in query if unicodedata.category(c) != 'Mn')
        fflog(f'query: {base_title!r}')
        r = self.session.get(self.search_link % quote_plus(query), headers=self.headers)
        if not r or r.status_code != 200:
            return None

        listupd = client.parseDOM(r.text, 'div', attrs={'class': 'listupd'})
        if not listupd:
            fflog('search: 0 results')
            return None

        anime_entries = client.parseDOM(listupd[0], 'div', attrs={'class': 'bsx'})
        fflog(f'search: {len(anime_entries)} results')

        fallback_url = fallback_title = None

        for entry in anime_entries:
            try:
                links = client.parseDOM(entry, 'a', ret='href')
                titles_found = client.parseDOM(entry, 'h2')
                if not links or not titles_found:
                    continue
                anime_url = links[0]
                anime_title = titles_found[0].strip()

                result_norm = kitsu_api.normalize_romaji(anime_title)
                if not result_norm.startswith(base_norm):
                    continue

                # 1. Exact match (kitsu title == site title, with season suffix normalized)
                if result_norm == kitsu_norm:
                    fflog(f'desuonline/kitsu: match (exact): "{anime_title}" → ep {local_ep}')
                    return self._get_episode_link(anime_url, local_ep)

                res_year_m = re.search(r'\((\d{4})\)', anime_title)
                res_year = res_year_m.group(1) if res_year_m else None

                # 2. Year match
                if cour_year and res_year == cour_year:
                    fflog(f'desuonline/kitsu: match (year {cour_year}): "{anime_title}" → ep {local_ep}')
                    return self._get_episode_link(anime_url, local_ep)

                if fallback_url is None:
                    fallback_url, fallback_title = anime_url, anime_title
            except Exception:
                continue

        if fallback_url:
            fflog(f'desuonline/kitsu: match (fallback): "{fallback_title}" → ep {local_ep}')
            return self._get_episode_link(fallback_url, local_ep)

        fflog(f'desuonline/kitsu: no match for "{kitsu_title}"')
        return None

    @fflog_exc
    def _search(self, titles: List[str], season: Optional[str], episode: Optional[str],
                tmdb: Optional[str], aliases: list[SourceTitleAlias] | None = None) -> Optional[str]:
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

                title_normalized = unicodedata.normalize('NFD', title)
                title_normalized = ''.join(c for c in title_normalized if unicodedata.category(c) != 'Mn')
                title_encoded = quote_plus(title_normalized)

                search_url = self.search_link % title_encoded

                r = self.session.get(search_url, headers=self.headers)
                if not r or r.status_code != 200:
                    control.sleep(500)
                    continue

                content = r.text

                listupd = client.parseDOM(content, 'div', attrs={'class': 'listupd'})
                if not listupd:
                    continue

                anime_entries = client.parseDOM(listupd[0], 'div', attrs={'class': 'bsx'})

                for entry in anime_entries:
                    try:
                        links = client.parseDOM(entry, 'a', ret='href')
                        titles_found = client.parseDOM(entry, 'h2')

                        if not links or not titles_found:
                            continue

                        anime_url = links[0]
                        anime_title = titles_found[0].strip()

                        # Simple contains check like original
                        result_normalized = cleantitle.normalize(anime_title).lower()
                        matched = any(
                            all(word in result_normalized for word in mt.split())
                            for mt in alias_cleans
                        )
                        if matched:
                            # fflog(f'found anime: "{anime_title}" for "{title}"')
                            episode_url = self._get_episode_link(anime_url, target_episode)
                            if episode_url:
                                return episode_url
                            else:
                                # fflog(f'episode {target_episode} not found in "{anime_title}"')
                                return None  # Stop searching other anime if episode not found

                    except Exception:
                        fflog_exc()
                        continue

        except Exception:
            fflog_exc()
            return

    def _get_episode_link(self, anime_url: str, episode_number: int) -> Optional[str]:
        """Get specific episode link from anime page using animeotaku approach"""
        try:
            r = self.session.get(anime_url, headers=self.headers)
            if not r or r.status_code != 200:
                return None

            content = r.text

            epbox = client.parseDOM(content, 'div', attrs={'class': 'eplister'})
            if not epbox:
                return None

            ep_numbers = client.parseDOM(epbox[0], 'div', attrs={'class': 'epl-num'})
            ep_titles = client.parseDOM(epbox[0], 'div', attrs={'class': 'epl-title'})
            ep_links = client.parseDOM(epbox[0], 'a', ret='href')

            for i, (ep_num, ep_title, ep_link) in enumerate(zip(ep_numbers, ep_titles, ep_links)):
                try:
                    num_match = re.search(r'(\d+)', ep_num)
                    if num_match:
                        current_ep_num = int(num_match.group(1))
                        # fflog(f'episode compare: wanted={episode_number}, found={current_ep_num}, link={ep_link}')
                        if current_ep_num == episode_number:
                            return ep_link
                except (ValueError, AttributeError):
                    continue

            return None

        except Exception:
            fflog_exc()
            return None
