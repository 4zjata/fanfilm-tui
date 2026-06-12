# -*- coding: utf-8 -*-
"""
FanFilm - źródło: archive.org (Public Domain / Creative Commons)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING, ClassVar
from urllib.parse import quote
from lib.ff import requests, cleantitle
from lib.ff.source_utils import DEFAULT_UA, check_sd_url
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.base_link = 'https://archive.org'
        self.search_api = f"{self.base_link}/advancedsearch.php"
        self.session = requests.Session()
        self.headers = {
            'User-Agent': DEFAULT_UA,
            'Accept': 'application/json,text/html',
        }

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        if year and str(year).isdigit():
            if datetime.now().year - int(year) < 70:
                return None  # film too recent for public domain
        return self._search_movie(title, localtitle, year)

    def tvshow(self, *args, **kwargs) -> None:
        return None

    def episode(self, *args, **kwargs) -> None:
        return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        try:
            if not url:
                return []

            m = re.search(r'/details/([^/]+)', url)
            if not m:
                return []

            identifier = m.group(1)
            video_files = self._get_video_files(identifier)
            if not video_files:
                fflog('no video files found')
                return []

            best_file = max(video_files, key=lambda f: f['size'])
            encoded_filename = quote(best_file['name'], safe='')
            mp4_url = f"{self.base_link}/download/{identifier}/{encoded_filename}"

            result = [{
                'source': '',
                'quality': check_sd_url(best_file['name']),
                'language': 'en',
                'url': mp4_url,
                'info': best_file['format'] or 'MP4',
                'filename': best_file['name'],
                'direct': True,
                'debridonly': False,
                'premium': False,
            }]
            fflog(f'sources: {len(result)}')
            return result
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _get_video_files(self, identifier: str) -> list[dict[str, Any]]:
        try:
            r = self.session.get(f"{self.base_link}/metadata/{identifier}",
                                 headers=self.headers, timeout=30)
            if r.status_code != 200:
                return []
            video_files = []
            for file in r.json().get('files', []):
                name = file.get('name', '')
                fmt = file.get('format', '')
                try:
                    size = int(file.get('size', 0))
                except (ValueError, TypeError):
                    size = 0
                if (name.endswith('.mp4') or 'MPEG4' in fmt or
                        name.endswith('.webm') or 'WebM' in fmt or
                        name.endswith('.mkv') or 'Matroska' in fmt):
                    video_files.append({'name': name, 'format': fmt, 'size': size})
            return video_files
        except Exception:
            return []

    def _search_movie(self, title: str, localtitle: str, year: str) -> Optional[str]:
        try:
            url = self._search(title, year)
            if url:
                return url
            if localtitle and localtitle.lower() != title.lower():
                return self._search(localtitle, year)
            return None
        except Exception:
            fflog_exc()
            return None

    def _search(self, title: str, year: str = None) -> Optional[str]:
        try:
            if not title:
                return None

            search_title_clean = cleantitle.get(title)
            oldest_year = datetime.now().year - 70
            search_year = int(year) if year and str(year).isdigit() else None

            fflog(f'query: {title!r} ({year})')

            params = {
                'q': f'mediatype:movies AND title:("{title}")',
                'fl[]': ['identifier', 'title', 'year'],
                'rows': 50,
                'output': 'json'
            }
            r = self.session.get(self.search_api, params=params, headers=self.headers, timeout=30)
            if r.status_code != 200:
                return None

            docs = r.json().get('response', {}).get('docs', [])
            fflog(f'search: {len(docs)} results')

            best = None
            best_score = -1

            for d in docs:
                identifier = d.get('identifier')
                item_title = d.get('title', '')
                item_year = d.get('year')

                if not identifier or not item_title:
                    continue

                if item_year and str(item_year).isdigit() and int(item_year) > oldest_year:
                    continue

                item_title_clean = cleantitle.get(item_title)
                exact = search_title_clean == item_title_clean
                partial = ((search_title_clean in item_title_clean or
                            item_title_clean in search_title_clean)
                           and len(search_title_clean) >= 3)

                if not (exact or partial):
                    continue

                score = 10 if exact else 1
                if search_year and item_year and str(item_year).isdigit():
                    diff = abs(int(item_year) - search_year)
                    if diff == 0:
                        score += 5
                    elif diff == 1:
                        score += 2

                if score > best_score:
                    best_score = score
                    best = identifier

            if not best:
                fflog(f'no match: {title!r}')
                return None

            return f"{self.base_link}/details/{best}"

        except Exception:
            fflog_exc()
            return None
