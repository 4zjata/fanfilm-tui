# -*- coding: utf-8 -*-
"""
FanFilm - źródło: animerealms
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING, ClassVar

from lib.ff import requests, control, source_utils
from lib.ff.source_utils import DEFAULT_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem
from lib.api import kitsu as kitsu_api
if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# Non-proxy providers that return directly playable streams.
# zencloud wykluczone: JWT binduje client_ip=127.0.0.1 (serwer animerealms),
# co blokuje bezpośrednie odtwarzanie poza ich playerem.
_PROVIDERS = ['allmanga', 'allmanga-dub']

_QUALITY_MAP = {
    '1080': '1080p',
    '720': '720p',
    'hd': '720p',
    'hls': '720p',
    'auto': '720p',
}


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.base_url = 'https://www.animerealms.org'
        self.api_url = self.base_url + '/api/watch'
        self.session = requests.Session()

    # ── public api ─────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[List[str]]:
        if not source_utils.is_anime(self.ffitem):
            return None
        tmdb = self.ffitem.tmdb_id
        if not tmdb:
            return None
        movie_info = kitsu_api.get_movie_info(tmdb)
        if not movie_info:
            return None
        anilist_id = movie_info.get('anilist_id')
        if not anilist_id:
            fflog(f'animerealms/movie: brak anilist_id dla tmdb={tmdb}')
            return None
        fflog(f'animerealms/movie: "{movie_info["title"]}" anilist_id={anilist_id}')
        return [self._watch_url(anilist_id, 1)]

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        return tvshowtitle

    @fflog_exc
    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        if not self.ffitem.show_item or not source_utils.is_anime(self.ffitem):
            return None
        tmdb = self.ffitem.show_item.tmdb_id
        if not tmdb:
            return None

        cours = kitsu_api.get_cour_structure(tmdb)
        if not cours:
            fflog(f'animerealms: brak danych kitsu dla tmdb={tmdb}')
            return None

        cour, local_ep = kitsu_api.resolve_cour(cours, self.ffitem)
        if not cour or local_ep is None:
            fflog(f'animerealms: nie znaleziono coura dla tmdb={tmdb} S{season}E{episode}')
            return None

        anilist_id = cour.get('anilist_id')
        if not anilist_id:
            fflog(f'animerealms: brak anilist_id dla coura "{cour["title"]}"')
            return None

        fflog(f'animerealms: cour="{cour["title"]}" anilist_id={anilist_id} local_ep={local_ep}')
        return self._watch_url(anilist_id, local_ep)

    @fflog_exc
    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        if not url:
            return []

        if isinstance(url, list):
            url = url[0]

        parsed = self._parse_watch_url(url)
        if not parsed:
            return []
        anilist_id, ep_num = parsed
        filename = f'animerealms/{anilist_id}/{ep_num}'

        sources = []
        for provider in _PROVIDERS:
            try:
                data = self._call_api(provider, anilist_id, ep_num)
                if not data:
                    continue
                streams = data.get('streams') or []
                for idx, stream in enumerate(streams):
                    stream_url = stream.get('url', '')
                    if not stream_url:
                        continue
                    referer = (stream.get('headers') or {}).get('Referer', '')
                    quality = self._map_quality(stream.get('quality', ''), stream_url)
                    info = 'Dubbing' if 'dub' in provider else 'Napisy'
                    placeholder = f'{stream_url}#{referer}'
                    sources.append({
                        'source': provider,
                        'quality': quality,
                        'language': 'en',
                        'url': placeholder,
                        'info': info,
                        'filename': filename,
                        'direct': False,
                        'debridonly': False,
                        'premium': False,
                    })
            except Exception:
                fflog_exc()

        fflog(f'animerealms: {len(sources)} sources')
        return sources

    @fflog_exc
    def resolve(self, url: str) -> Optional[str]:
        # Placeholder: {stream_url}#{referer}
        if '#' not in url:
            return str(url)

        stream_url, referer = url.split('#', 1)
        if not stream_url:
            return None

        if referer:
            return f'{stream_url}|Referer={referer}&User-Agent={DEFAULT_UA}'
        return stream_url

    # ── helpers ────────────────────────────────────────────────────────────

    def _watch_url(self, anilist_id: int, ep_num: int) -> str:
        return f'{self.base_url}/en/watch/{anilist_id}/{ep_num}'

    def _parse_watch_url(self, url: str) -> Optional[tuple]:
        match = re.search(r'/en/watch/(\d+)/(\d+)', url)
        if not match:
            fflog(f'animerealms: nieprawidłowy URL: {url}')
            return None
        return int(match.group(1)), int(match.group(2))

    def _call_api(self, provider: str, anilist_id: int, ep_num: int) -> dict[str, Any] | None:
        headers = {
            'User-Agent': DEFAULT_UA,
            'Content-Type': 'application/json',
            'Origin': self.base_url,
            'Referer': self._watch_url(anilist_id, ep_num),
        }
        body = {'provider': provider, 'anilistId': anilist_id, 'episodeNumber': ep_num}
        response = self.session.post(self.api_url, headers=headers, json=body)
        if not response:
            fflog(f'animerealms/{provider}: brak odpowiedzi (status={getattr(response, "status_code", "?")})')
            return None
        data = response.json()
        if data.get('streams'):
            fflog(f'animerealms/{provider}: {len(data["streams"])} streams')
        else:
            fflog(f'animerealms/{provider}: brak streams ({data.get("message", "")})')
        return data

    @staticmethod
    def _map_quality(quality_str: str, url: str = '') -> str:
        if '1080' in url:
            return '1080p'
        if '720' in url:
            return '720p'
        quality_lower = quality_str.lower()
        for key, value in _QUALITY_MAP.items():
            if key in quality_lower:
                return value
        return 'SD'
