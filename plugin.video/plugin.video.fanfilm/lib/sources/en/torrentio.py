# -*- coding: utf-8 -*-
"""
FanFilm - źródło: Torrentio (Stremio Addon API)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING, ClassVar, List, Optional

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
    from lib.ff.item import FFItem

from lib.ff import requests, source_utils
from lib.ff.source_utils import FF_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.settings import settings


class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en', 'pl']

    def __init__(self):
        pass

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not imdb:
                return None
            return urllib.parse.urlencode({'imdb': imdb, 'type': 'movie'})
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not imdb:
                return None
            return urllib.parse.urlencode({'imdb': imdb, 'type': 'tv'})
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if not url:
                return None
            data = {k: v[0] for k, v in urllib.parse.parse_qs(url).items()}
            data.update({'season': season, 'episode': episode})
            return urllib.parse.urlencode(data)
        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            if not url:
                return result

            data = {k: v[0] for k, v in urllib.parse.parse_qs(url).items()}
            imdb = data.get('imdb', '')
            content_type = data.get('type', 'movie')

            if not imdb:
                return result

            base_url = settings.getString("torrentio.base_url")
            if not base_url:
                base_url = "https://torrentio.strem.fun"
            base_url = base_url.rstrip('/')

            config = settings.getString("torrentio.config")
            if config:
                base_url += f"/{config.strip('/')}"

            if content_type == 'tv':
                season = data.get('season', '1')
                episode = data.get('episode', '1')
                target_url = f"{base_url}/stream/series/{imdb}:{season}:{episode}.json"
            else:
                target_url = f"{base_url}/stream/movie/{imdb}.json"

            fflog(f'[Torrentio] Querying: {target_url}')
            
            sess = requests.Session()
            resp = sess.get(target_url, headers={'User-Agent': FF_UA}, timeout=15)
            if resp.status_code != 200:
                fflog(f'[Torrentio] Query returned status: {resp.status_code}')
                return result

            streams_data = resp.json()
            streams = streams_data.get('streams', [])
            fflog(f'[Torrentio] Found {len(streams)} streams')

            for s in streams:
                name = s.get('name', '')
                title = s.get('title', '')
                info_hash = s.get('infoHash', '')
                file_idx = s.get('fileIdx', 0)
                stream_url = s.get('url')

                lines = title.split('\n')
                filename = lines[0].strip() if lines else ''

                seeds = 0
                size_str = ''
                if len(lines) > 1:
                    second_line = lines[1]
                    seeds_match = re.search(r'👤\s*(\d+)', second_line)
                    if seeds_match:
                        seeds = int(seeds_match.group(1))
                    size_match = re.search(r'💾\s*([\d\.]+\s*(?:GB|MB|KB|TB))', second_line, re.IGNORECASE)
                    if size_match:
                        size_str = size_match.group(1)

                quality = 'SD'
                for q in ['2160p', '4k', '1080p', '720p', '480p']:
                    if q in name.lower() or q in filename.lower():
                        if q == '4k' or q == '2160p':
                            quality = '4K'
                        elif q == '1080p':
                            quality = '1080p'
                        elif q == '720p':
                            quality = '720p'
                        break

                # Detect language and audio type using the new detect_torrent_language helper
                lang_code, audio_type = source_utils.detect_torrent_language(filename, title)
                is_pl = (lang_code == 'pl')

                info_label = f"👤 {seeds} | 💾 {size_str}"
                if audio_type:
                    info_label += f" | {audio_type}"
                
                # Check if it has direct resolved URL (e.g. Debrid configured via Torrentio website)
                if stream_url:
                    info_label += " (Direct)"
                    resolved_url = stream_url
                    direct = True
                    local = False
                else:
                    info_label += " (P2P)"
                    # Construct magnet link for client downloading
                    # Pass file index so engine knows what file to play
                    magnet = f"magnet:?xt=urn:btih:{info_hash}"
                    if filename:
                        magnet += f"&dn={urllib.parse.quote_plus(filename)}"
                    magnet += f"&file_idx={file_idx}"
                    resolved_url = magnet
                    direct = False
                    local = True

                result.append({
                    'source': 'Torrentio',
                    'quality': quality,
                    'language': 'pl' if is_pl else 'en',
                    'url': resolved_url,
                    'info': info_label,
                    'filename': filename,
                    'direct': direct,
                    'debridonly': False,
                    'local': local,
                    'seeds': seeds,
                })

        except Exception:
            fflog_exc()

        return result

    def resolve(self, url: str) -> Optional[str]:
        return url
