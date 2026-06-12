# -*- coding: utf-8 -*-
"""
FanFilm - źródło: vestroiakr
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

from urllib.parse import quote_plus
import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar, ClassVar, ClassVar

from lib.ff import requests, cleantitle, client, source_utils
from lib.ff.source_utils import DEFAULT_UA, ShowData, show_data_asdict, ShowDataDict
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias


HEADERS = {
    'User-Agent': DEFAULT_UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
    'Connection': 'keep-alive',
}


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_link = 'https://vestroiakr.blogspot.com'
        self.search_link = self.base_link + '/search?q=%s'
        self.session = requests.Session()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        """Search for a movie"""
        search_title = localtitle or title
        return self._search(search_title, year, is_movie=True)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        """Returns show data"""
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, int(year)))

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[Tuple[str, int, str]]:
        """Search for a series episode"""
        if not url:
            return None

        data = ShowData(**url)
        search_title = data.local_title or data.title

        # Find show page — first by PL title, then EN
        show_url = self._search(search_title, data.year, is_movie=False)
        if not show_url and data.local_title and data.title and data.local_title != data.title:
            fflog(f'no match for PL title, searching by EN: {data.title}')
            show_url = self._search(data.title, data.year, is_movie=False)
        if not show_url:
            return None

        # Find the specific episode
        return self._find_episode(show_url, season, episode)

    def sources(self, url: Optional[Tuple[str, int, str]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        """Fetch sources from post"""
        if not url:
            fflog('no url')
            return []

        # Check if it is a tuple (tvshow) or string (movie)
        episode_num = None
        episode_filename = ''
        if isinstance(url, tuple):
            if len(url) == 3:
                url, episode_num, episode_filename = url
            else:
                url, episode_num = url
            fflog(f'fetching sources for episode {episode_num} from: {url}')
        else:
            fflog(f'fetching sources from: {url}')

        try:
            response = self.session.get(url, headers=HEADERS)
            if not response:
                fflog('no response')
                return []

            sources = []
            page = response.text

            if episode_num is not None:
                # Find episode block using the shared parser
                episodes_on_page = self._parse_episodes(page)
                block_html = None
                for ep_num, titles, block in episodes_on_page:
                    if ep_num == episode_num:
                        block_html = block
                        if not episode_filename:
                            episode_filename = ' / '.join(titles) if titles else ''
                        break

                if not block_html:
                    fflog(f'no block found for episode {episode_num}')
                    return []

                video_links = re.findall(r'<a\s+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', block_html, re.I)
            else:
                # For movies — extract all links with target="_blank" from page
                video_links = re.findall(
                    r'<a\s+href=["\']([^"\']+)["\'][^>]*target=["\']_blank["\'][^>]*>([^<]+)</a>', page, re.I)

            for link_url, host_name in video_links:
                # Skip obvious non-video links (blogger, blogspot)
                if 'blogger.com' in link_url.lower() or 'blogspot.com' in link_url.lower():
                    continue

                sources.append({
                    'source': host_name.strip().strip('/'),
                    'quality': 'HD',
                    'language': 'pl',
                    'url': link_url,
                    'info': '',
                    'filename': episode_filename,
                    'direct': False,
                    'debridonly': False,
                    'premium': False,
                })

            fflog(f'sources: {len(sources)}')
            return sources

        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        """Resolves URL to the final link"""
        if not url:
            return None
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _search(self, title, year, is_movie):
        """Searches for a movie/series on the blog"""
        try:
            # Use original title with Polish characters, but strip punctuation
            search_query = re.sub(r'[^\w\s]', '', title)
            # Append year only for movies if provided
            if year and is_movie:
                search_query = f"{search_query} {year}"

            fflog(f'query: {search_query!r}')
            search_url = self.search_link % quote_plus(search_query)

            response = self.session.get(search_url, headers=HEADERS)

            if not response:
                return None

            page = response.text

            # Format: <h3 class='post-title entry-title' itemprop='name'><a href='URL'>TITLE</a></h3>
            results = re.findall(
                r"<h3[^>]*class=['\"][^'\"]*post-title[^'\"]*['\"][^>]*>.*?<a\s+href=['\"]([^'\"]+)['\"][^>]*>([^<]+)</a>", page, re.DOTALL | re.I)
            fflog(f'search: {len(results)} results')

            search_normalized = re.sub(r'[^\w\s]', '', cleantitle.normalize(title)).lower()

            for url, result_title in results:
                clean_title = re.sub(r'<[^>]+>', '', result_title).strip()
                normalized = re.sub(r'[^\w\s]', '', cleantitle.normalize(clean_title)).lower()

                # Check if title matches
                if search_normalized in normalized:
                    # Check year (for movies and tvshows)
                    if year:
                        if str(year) in clean_title or str(year) in url:
                            fflog(f"found {'movie' if is_movie else 'series'}: {url}")
                            return url
                        else:
                            continue
                    else:
                        fflog(f"found {'movie' if is_movie else 'series'}: {url}")
                        return url

            return None

        except Exception:
            fflog_exc()
            return None

    def _parse_episodes(self, page):
        """Parses episodes from the page.
        Returns a list of (ep_num, [titles], block_html).

        Handles the table format: <td> cells arranged in triples:
        [ODCINEK X][titles][links] - multiple episodes may be in one <tr>.
        Titles may be nested in <span>/<b> and separated by <br>.
        """
        episodes = []

        # Extract all <td> cells from the page
        cells = re.findall(r'<td[^>]*>(.*?)</td>', page, re.DOTALL | re.I)

        for i, cell in enumerate(cells):
            # Find cell with "ODCINEK X" (episode header)
            ep_match = re.search(r'ODCINEK\s*(\d+)', cell, re.I)
            if not ep_match:
                continue
            ep_num = int(ep_match.group(1))

            # Next cell = titles, following cell = links
            titles = []
            link_cell = ''
            if i + 1 < len(cells):
                title_html = cells[i + 1]
                # Strip HTML tags, keep text and <br> as separator
                parts = re.split(r'<br\s*/?>', title_html, flags=re.I)
                for p in parts:
                    clean = re.sub(r'<[^>]+>', '', p).strip()
                    if clean:
                        titles.append(clean)

            if i + 2 < len(cells):
                link_cell = cells[i + 2]

            # block_html = title cell + link cell (used for extracting links in sources())
            block_html = (cells[i + 1] if i + 1 < len(cells) else '') + ' ' + link_cell
            episodes.append((ep_num, titles, block_html))

        return episodes

    def _find_episode(self, show_url, season, episode):
        """Finds a specific episode - returns tuple (url, episode_num, filename)"""
        try:
            season = int(season)
            episode = int(episode)

            response = self.session.get(show_url, headers=HEADERS)
            if not response:
                return None

            page = response.text

            absolute_number = self.ffitem.absolute_episode_number() or episode

            fflog(f'searching for episode: S{season:02d}E{episode:02d} (absolute: {absolute_number})')

            episodes_on_page = self._parse_episodes(page)
            fflog(f'found {len(episodes_on_page)} episodes on page')

            # METHOD 1: by absolute episode number
            for ep_num, titles, _ in episodes_on_page:
                if ep_num == absolute_number:
                    filename = ' / '.join(titles) if titles else ''
                    if len(titles) > 1:
                        filename += ' [łączone]'
                    fflog(f'found by absolute number: {absolute_number} ("{filename}")')
                    return (show_url, ep_num, filename)

            # METHOD 2: By episode number within season
            if episode != absolute_number:
                for ep_num, titles, _ in episodes_on_page:
                    if ep_num == episode:
                        filename = ' / '.join(titles) if titles else ''
                        if len(titles) > 1:
                            filename += ' [łączone]'
                        fflog(f'found by season episode number: E{episode:02d} (episode {ep_num}: "{filename}")')
                        return (show_url, ep_num, filename)

            fflog('no match')
            return None

        except Exception:
            fflog_exc()
            return None
