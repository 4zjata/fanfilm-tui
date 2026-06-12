# -*- coding: UTF-8 -*-
"""
FanFilm ‑ źródło: stare-kino.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
import re
from lib.ff.resolve_utils import build_drmff
from lib.ff import requests
from lib.ff.client import replaceHTMLCodes
from lib.ff.source_utils import DEFAULT_UA, year_matches
from lib.ff.log_utils import fflog_exc
from lib.ff.item import FFItem

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

_NINATEKA_BASE = 'https://ninateka.pl'

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem  # ↓ assigned dynamically by FanFilm

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.list_url = 'https://stare-kino.pl/filmy-przedwojenne-online/'
        self.headers = {'User-Agent': 'Mozilla/5.0'}
        self.session = requests.Session()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[Tuple[str, int]]:
        # Check if Poland is in the countries list
        if 'PL' not in self.ffitem.vtag.getCountryCodes():
            return None

        # Limit to films before 1960
        if int(year) >= 1960:
            return None

        return (localtitle or title, int(year))

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> None:
        return None

    def episode(self, url: None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> None:
        return None

    def sources(self, url: Optional[Tuple[str, int]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        if not url:
            return sources

        try:
            search_title = url[0]
            search_year = url[1]

            fflog(f'query: {search_title!r} ({search_year})')
            req = self.session.get(self.list_url, headers=self.headers, timeout=30)
            if req.status_code != 200:
                return sources

            # Normalize title for search
            search_title = search_title.lower().replace('\xa0', ' ')
            search_title = re.sub(r'[.,!?;:\']', '', search_title)

            # Regex for two HTML structures:
            # 1. <a href="..."><em>Title</em></a>
            pattern1 = r'<a\s+href=["\']([^"\']+)["\'][^>]*>.*?<em>(.*?)</em>.*?</a>([^<]*)'
            # 2. <em><a href="...">Title</a></em>
            pattern2 = r'<em><a\s+href=["\']([^"\']+)["\'][^>]*>(.*?)</a></em>([^<]*)'

            matches = re.findall(pattern1, req.text, re.DOTALL | re.IGNORECASE)
            matches += re.findall(pattern2, req.text, re.DOTALL | re.IGNORECASE)

            for link_url, movie_title, description in matches:
                if not link_url.startswith('http'):
                    continue

                # Clean title from HTML entities
                movie_title = replaceHTMLCodes(movie_title.strip())
                if not movie_title:
                    continue

                # Normalize and match
                title_norm = movie_title.lower().replace('\xa0', ' ')
                title_norm = re.sub(r'[.,!?;:\']', '', title_norm)

                if search_title not in title_norm and title_norm not in search_title:
                    continue

                year_match = re.search(r'\((\d{4})\)', description)
                if year_match and not year_matches(year_match.group(1), search_year):
                    continue

                # Detect additional info tags
                desc_lower = description.lower()
                info_parts = []
                if 'restauracja' in desc_lower:
                    info_parts.append('restauracja')
                if 'fragment' in desc_lower:
                    info_parts.append('fragment')
                if 'dubbing' in desc_lower or 'lektor' in desc_lower:
                    info_parts.append('dubbing')
                if 'napisy' in desc_lower:
                    info_parts.append('napisy')
                if 'wersja' in desc_lower:
                    info_parts.append('wersja alt')

                info = ', '.join(info_parts) if info_parts else ''

                # YouTube
                if 'youtu.be' in link_url or 'youtube.com' in link_url:
                    video_id = None
                    if 'youtu.be' in link_url:
                        m = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', link_url)
                        video_id = m.group(1) if m else None
                    elif 'youtube.com' in link_url:
                        m = re.search(r'[?&]v=([a-zA-Z0-9_-]+)', link_url)
                        video_id = m.group(1) if m else None

                    if video_id:
                        video_id = video_id.split('?')[0].split('&')[0]
                        sources.append({
                            'source': 'youtube',
                            'quality': 'SD',
                            'language': 'pl',
                            'url': f'https://www.youtube.com/watch?v={video_id}',
                            'info': info,
                            'direct': False,
                            'debridonly': False,
                            'filename': '',
                            'premium': False,
                        })

                # Ninateka
                elif 'ninateka.pl' in link_url:
                    sources.append({
                        'source': 'ninateka',
                        'quality': 'HD',
                        'language': 'pl',
                        'url': link_url,
                        'info': info,
                        'direct': False,
                        'debridonly': False,
                        'filename': '',
                        'premium': False,
                    })

                # Other sources (repozytorium.fn.org.pl, cda.pl, etc.)
                else:
                    # Skip cyfrowa.tvp.pl (requires a special resolver)
                    if 'cyfrowa.tvp.pl' in link_url:
                        continue

                    # Detect hostname
                    host_match = re.search(r'https?://(?:www\.)?([^/]+)', link_url)
                    host_name = host_match.group(1).split('.')[0] if host_match else 'unknown'

                    # Special display name for national film repository
                    if 'repozytorium.fn.org.pl' in link_url:
                        host_name = 'Filmoteka Narodowa'

                    sources.append({
                        'source': host_name,
                        'quality': 'SD',
                        'language': 'pl',
                        'url': link_url,
                        'info': info,
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
        # Resolve links from repozytorium.fn.org.pl
        if 'repozytorium.fn.org.pl' in url:
            try:
                response = requests.get(url, headers=self.headers, timeout=30)
                if response.status_code != 200:
                    return None

                # Find file: in jwplayer setup
                match = re.search(r'file:\s*encodeURI\(["\']([^"\']+)["\']\)', response.text)
                if match:
                    file_url = match.group(1)
                    # If relative URL
                    if not file_url.startswith('http'):
                        file_url = 'https://repozytorium.fn.org.pl/' + file_url.lstrip('/')
                    return file_url

                return None
            except Exception:
                fflog_exc()
                return None

        # Resolve links from Ninateka
        if 'ninateka.pl' in url:
            try:
                # Extract slug from URL (e.g. "zew-morza-henryk-szaro")
                slug = url.rstrip('/').split('/')[-1]

                # API Ninateki
                api_url = _NINATEKA_BASE + '/api/products/vods/search/VOD'
                headers = {
                    'User-Agent': DEFAULT_UA,
                    'Referer': _NINATEKA_BASE,
                    'Accept': 'application/json, text/plain, */*'
                }
                params = {
                    'lang': 'POL',
                    'platform': 'BROWSER',
                    'keyword': slug.replace('-', ' ')
                }

                response = requests.get(api_url, headers=headers, params=params)
                if response.status_code != 200:
                    return None

                results = response.json().get('items', [])
                if not results:
                    return None

                # Take the first result
                item = results[0]
                eid = item['id']
                tenant = item['tenant']['uid']

                # Fetch playlist
                play_url = f'{_NINATEKA_BASE}/api/products/{eid}/videos/playlist'
                playlist_params = {
                    'videoType': 'MOVIE',
                    'platform': 'BROWSER',
                    'tenant': tenant
                }

                playlist_response = requests.get(play_url, headers=headers, params=playlist_params)
                if playlist_response.status_code != 200:
                    return None

                data = playlist_response.json()

                if 'sources' in data and 'DASH' in data['sources'] and data['sources']['DASH']:
                    stream_url = data['sources']['DASH'][0]['src']
                    if stream_url.startswith('//'):
                        stream_url = 'https:' + stream_url

                    widevine = (data.get('drm') or {}).get('WIDEVINE')
                    return build_drmff(
                        stream_url,
                        widevine_url=widevine['src'] if widevine else None,
                        lic_referer=_NINATEKA_BASE,
                    )

                return None
            except Exception:
                fflog_exc()
                return None

        return url
