# -*- coding: utf-8 -*-
"""
FanFilm - źródło: adapter.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, ClassVar
from urllib.parse import urlencode, quote_plus

from lib.ff import requests, cleantitle
from lib.ff.item import FFItem
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem  # ↓ assigned dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_url = 'https://adapter.pl/'
        self.search_url = '?s='
        self.session = requests.Session()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> 'List[SourceItem]':
        # fflog(f"searching for movie '{localtitle}' ({year})")
        # Check if Poland is in the countries list
        if 'PL' not in self.ffitem.vtag.getCountryCodes():
            fflog('adapterpl: not a Polish film, skip')
            return []
        try:
            # Search for the movie
            search_results = self.search_movies(localtitle)

            if not search_results:
                # fflog("no search results found")
                return []

            sources = []
            normalized_search_title = self.normalize_title_for_comparison(localtitle)
            # fflog(f"normalized search title: '{normalized_search_title}'")

            for movie in search_results:
                movie_title = movie.get('title', '')
                movie_link = movie.get('link', '')

                if not movie_title or not movie_link:
                    continue

                normalized_movie_title = self.normalize_title_for_comparison(movie_title)
                # fflog(f"comparing '{normalized_search_title}' with '{normalized_movie_title}'")

                # --- Matching logic ---
                is_valid_match = False
                title_lower = movie_title.lower()

                # Case 1: Exact match after normalization
                if normalized_search_title == normalized_movie_title:
                    is_valid_match = True
                    # fflog(f"exact title match: {movie_title}")

                # Case 2: Check if movie title starts with search title (for suffixes)
                elif normalized_movie_title.startswith(normalized_search_title):
                    remaining_part = normalized_movie_title[len(normalized_search_title):].strip()

                    # Allow empty remaining part or specific allowed suffixes
                    if (not remaining_part or
                        remaining_part.lower() in ('pjm', 'z nowa audiodeskrypcja', 'z nową audiodeskrypcją')):
                        is_valid_match = True
                        # fflog(f"allowed suffix match: {movie_title}")
                    # else:
                        # fflog(f"rejecting match due to extra words: '{remaining_part}' in {movie_title}")

                # Case 3: Also check if search title starts with movie title (reverse)
                elif normalized_search_title.startswith(normalized_movie_title):
                    remaining_part = normalized_search_title[len(normalized_movie_title):].strip()

                    # Allow if remaining part is small or empty
                    if not remaining_part or len(remaining_part) <= 3:
                        is_valid_match = True
                        # fflog(f"reverse match: {movie_title}")

                if is_valid_match:
                    # fflog(f"found potential match: {movie_title}")

                    # Try to get video URL to verify it's playable
                    video_url = self.get_video_url(movie_link)

                    if video_url:
                        # Determine protocol and quality
                        protocol = 'hls'
                        quality = 'HD'  # adapter.pl usually provides good quality

                        if '.m3u8' in video_url or '.m3u' in video_url:
                            protocol = 'hls'
                        elif '.mpd' in video_url:
                            protocol = 'mpd'

                        # --- Build info field ---
                        info = []

                        if 'pjm' in title_lower:
                            info = 'PJM'
                        elif 'nową audiodeskrypcją' in title_lower:
                            info = 'NOWA AUDIODESKRYPCJA'
                        else:
                            info = 'AUDIODESKRYPCJA'

                        source_info = {
                            'source': '',
                            'quality': quality,
                            'language': 'pl',
                            'url': f"DRMFF|{movie_link}|{protocol}",
                            'info': info,
                            'info2': '',  # Not used
                            'direct': False,
                            'debridonly': False,
                        }

                        sources.append(source_info)

            fflog(f'sources: {len(sources)}')
            return sources

        except Exception:
            fflog_exc()
            return []

    def sources(self, url: 'List[SourceItem]', hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        return url

    def resolve(self, url: str) -> Optional[str]:
        """Resolve video URL"""
        # fflog(f"resolving URL: {url}")

        if not url.startswith('DRMFF|'):
            return url

        try:
            parts = url.split('|')
            if len(parts) != 3:
                # fflog(f"invalid URL format: {url}")
                return None

            _, movie_link, expected_protocol = parts

            # Get the actual video URL
            video_url = self.get_video_url(movie_link)

            if not video_url:
                # fflog("could not extract video URL")
                return None

            # Prepare headers for streaming
            stream_headers = self.get_headers()
            stream_headers.update({
                'Origin': self.base_url.rstrip('/'),
                'Accept': '*/*',
                'Connection': 'keep-alive'
            })

            # Determine actual protocol
            if '.m3u8' in video_url or '.m3u' in video_url:
                protocol = 'hls'
                mimetype = 'application/x-mpegURL'
            elif '.mpd' in video_url:
                protocol = 'mpd'
                mimetype = 'application/dash+xml'
            else:
                # fflog(f"unknown protocol for URL: {video_url}")
                return None

            # Create adaptive data structure
            adaptive_data = {
                'protocol': protocol,
                'mimetype': mimetype,
                'manifest': video_url,
                # Empty DRM fields (required by FanFilm)
                'licence_type': '',
                'licence_url': '',
                'licence_header': '',
                'post_data': '',
                'response_data': '',
                # VOD properties for seeking support
                'content_lookup': False,
                'is_playable': True,
                'stream_headers': urlencode(stream_headers),
                'manifest_headers': urlencode(stream_headers)
            }

            # fflog(f"returning adaptive stream: {video_url}")
            return f"DRMFF|{repr(adaptive_data)}"

        except Exception:
            fflog_exc()
            return None

    # ── helpers ────────────────────────────────────────────────────────────

    def get_headers(self) -> Dict[str, str]:
        return {
            'User-Agent': DEFAULT_UA,
            'Referer': self.base_url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }

    def clean_text(self, text: str) -> str:
        """Clean HTML entities and tags from text"""
        # Handle HTML entities like &#123;
        entities = re.findall(r'(&#[0-9]+?;)', text)
        for entity in entities:
            try:
                text = text.replace(entity, chr(int(entity[2:-1])))
            except Exception:
                pass

        # Remove HTML tags
        text = re.sub(r'<.*?>', '', text)
        return text.strip()

    def normalize_title_for_comparison(self, title: str) -> str:
        """Normalize title for comparison - handle different punctuation"""
        normalized = cleantitle.normalize(title)
        # Replace different types of colons, dashes, and punctuation
        normalized = normalized.replace(':', ' ').replace('–', ' ').replace('—', ' ').replace('-', ' ')
        # Remove multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def search_movies(self, query: str) -> List[Dict[str, str]]:
        """Search for movies on adapter.pl"""
        try:
            fflog(f'query: {query!r}')
            search_url = f'{self.base_url}{self.search_url}{quote_plus(query)}'

            response = self.session.get(search_url, headers=self.get_headers())

            if response.status_code != 200:
                return []

            html = response.text

            if 'Brak wyników wyszukiwania' in html:
                fflog('search: 0 results')
                return []

            results = self.parse_movie_list(html)
            fflog(f'search: {len(results)} results')
            return results

        except Exception:
            fflog_exc()
            return []

    def parse_movie_list(self, html: str) -> List[Dict[str, str]]:
        """Parse movie list from HTML"""
        movies = []
        try:
            # Find movie grid section
            if '"movie-grid"' not in html:
                return movies

            grid_section = html.split('"movie-grid"')[1]
            if '"main-subcategory__recommended"' in grid_section:
                grid_section = grid_section.split('"main-subcategory__recommended"')[0]

            # Split by movie items
            movie_items = grid_section.split('"media-thumb-full"')

            for item in movie_items:
                if 'media-thumb-full__info' not in item:
                    continue

                try:
                    # Extract image and title
                    img_title_match = re.search(r'<img src="([^"]+?)" alt="([^"]+?)"', item)
                    if not img_title_match:
                        continue

                    img_url, title = img_title_match.groups()
                    title = self.clean_text(title)

                    # Extract link
                    link_match = re.search(r'href="([^"]+?)"', item)
                    if not link_match:
                        continue

                    link = link_match.group(1)

                    # Extract description
                    desc_match = re.search(r'"media-thumb-full__excerpt">([^<]+?)<', item)
                    description = desc_match.group(1) if desc_match else ''

                    movie_info = {
                        'title': title,
                        'link': link,
                        'image': img_url,
                        'description': description
                    }

                    movies.append(movie_info)
                    # fflog(f"found movie: {title}")

                except Exception:
                    # fflog_exc()
                    continue

            return movies

        except Exception:
            fflog_exc()
            return []

    def get_video_url(self, movie_link: str) -> Optional[str]:
        """Extract video URL from movie page"""
        try:
            # fflog(f"getting video URL from: {movie_link}")

            response = self.session.get(movie_link, headers=self.get_headers())

            if response.status_code != 200:
                # fflog(f"failed to get movie page, status: {response.status_code}")
                return None

            # Check if login is required
            if 'logowanie' in response.url:
                # fflog("login required for this content")
                return None

            html = response.text

            # Try to find video ID
            video_id_match = re.search(r'id-video="([^"]+?)"', html)
            if video_id_match:
                video_id = video_id_match.group(1)
                stream_url = f'https://media.adapter.pl/{video_id}/hls/playlist.m3u8'
                # fflog(f"found video ID: {video_id}")
                return stream_url

            # Try to find direct video URL
            video_url_match = re.search(r'data-video-url="([^"]+?)"', html)
            if video_url_match:
                stream_url = video_url_match.group(1)
                # fflog(f"found direct video URL")
                return stream_url

            # fflog("no video URL found")
            return None

        except Exception:
            fflog_exc()
            return None
