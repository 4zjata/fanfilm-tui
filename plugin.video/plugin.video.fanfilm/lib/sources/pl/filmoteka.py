# -*- coding: UTF-8 -*-
"""
FanFilm ‑ źródło: repozytorium.fn.org.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING, ClassVar
import re
from urllib.parse import quote_plus
from lib.ff import requests
from lib.ff.client import replaceHTMLCodes
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem
from lib.ff.source_utils import year_matches

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem  # ↓ assigned dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_url = 'https://repozytorium.fn.org.pl'
        self.search_url = self.base_url + '/?q=pl/search/site/'
        self.headers = {'User-Agent': 'Mozilla/5.0'}
        self.session = requests.Session()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        # Check if Poland is in the countries list
        if 'PL' not in self.ffitem.vtag.getCountryCodes():
            return None

        # Limit to films before 1960
        if int(year) >= 1960:
            return None

        try:
            search_term = localtitle or title
            search_year = int(year)
            url = self.search_url + quote_plus(search_term)

            fflog(f'query: {search_term!r}')
            req = self.session.get(url, headers=self.headers, timeout=30)
            if req.status_code != 200:
                return None

            # Find films in results
            pattern = r'<div class="fntile film-tile">.*?<a href="(/\?q=pl/node/\d+)">.*?<div class="fntile_title">\s*(.*?)\s*</div>'
            matches = re.findall(pattern, req.text, re.DOTALL | re.IGNORECASE)
            fflog(f'search: {len(matches)} results')

            # Normalize title
            search_norm = search_term.lower()
            search_norm = re.sub(r'[.,!?;:\']', '', search_norm)

            for node_url, movie_title in matches:
                movie_title = replaceHTMLCodes(movie_title.strip())
                title_norm = movie_title.lower()
                title_norm = re.sub(r'[.,!?;:\']', '', title_norm)

                if search_norm in title_norm or title_norm in search_norm:
                    # Fetch the film page to check the year
                    film_url = self.base_url + node_url
                    film_req = self.session.get(film_url, headers=self.headers, timeout=30)
                    if film_req.status_code == 200:
                        year_match = re.search(
                            r'<div class="fncustom_field[^>]*field_year[^>]*>.*?<span class="fncustom_field_value">(\d{4})</span>', film_req.text, re.DOTALL)
                        if year_match and not year_matches(year_match.group(1), search_year):
                            continue

                    return self.base_url + node_url

            return None

        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> None:
        return None

    def episode(self, url: None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> None:
        return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        if not url:
            return sources

        try:
            sources.append({
                'source': 'Filmoteka Narodowa',
                'quality': 'SD',
                'language': 'pl',
                'url': url,
                'info': '',
                'direct': False,
                'debridonly': False,
                'filename': '',
                'premium': False,
            })
            fflog(f'sources: {len(sources)}')
            return sources

        except Exception:
            fflog_exc()
            return sources

    def resolve(self, url: str) -> Optional[str]:
        try:
            response = self.session.get(url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                return None

            # Search for file: in jwplayer setup
            match = re.search(r'file:\s*encodeURI\(["\']([^"\']+)["\']\)', response.text)
            if match:
                file_url = match.group(1)
                # If relative URL
                if not file_url.startswith('http'):
                    file_url = self.base_url + '/' + file_url.lstrip('/')
                return file_url

            return None
        except Exception:
            fflog_exc()
            return None
