# -*- coding: utf-8 -*-
"""
FanFilm - źródło: NoTorrent (addon-osvh.onrender.com)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Port wrapper-a JS scrapera z All-in-One-Nuvio.
Stremio-style addon API: GET /stream/{movie|series}/{imdb[:S:E]}.json
"""

from __future__ import annotations

import re
import threading
from typing import ClassVar, Dict, List, Optional, TYPE_CHECKING

from lib.ff import requests
from lib.ff.source_utils import DEFAULT_UA, get_quality, append_headers, probe_m3u8_quality
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


_API = 'https://addon-osvh.onrender.com'
_BLOCKED = ('github.com', 'googleusercontent.com')

# tag-in-parens → ISO code (Latin Spanish dub → 'es')
_LANG_MAP = {
    'english': 'en', 'eng': 'en',
    'polish': 'pl', 'pol': 'pl', 'pl': 'pl',
    'latino': 'es', 'spanish': 'es', 'castellano': 'es', 'esp': 'es',
    'french': 'fr', 'fr': 'fr',
    'german': 'de', 'deu': 'de', 'ger': 'de',
    'portuguese': 'pt', 'portugues': 'pt', 'pt': 'pt', 'brasil': 'pt',
    'italian': 'it', 'ita': 'it',
    'russian': 'ru', 'rus': 'ru',
    'japanese': 'ja', 'jpn': 'ja',
    'korean': 'ko', 'kor': 'ko',
    'chinese': 'zh', 'mandarin': 'zh',
    'hindi': 'hi',
    'turkish': 'tr',
    'arabic': 'ar',
    'dutch': 'nl',
}


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    ffitem: 'FFItem'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': DEFAULT_UA})

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        return imdb or None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        return imdb or None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        if not url:
            return None
        return f'{url}:{int(season)}:{int(episode)}'

    def sources(self, url: Optional[str], hostDict: List[str],
                hostprDict: List[str]) -> 'List[SourceItem]':
        if not url:
            return []
        try:
            is_series = ':' in url
            kind = 'series' if is_series else 'movie'
            api_url = f'{_API}/stream/{kind}/{url}.json'
            # First request may hit a cold Render dyno → generous timeout
            r = self.session.get(api_url, timeout=30)
            if not r.ok:
                fflog(f'API HTTP {r.status_code}')
                return []
            data = r.json()
        except Exception:
            fflog_exc()
            return []

        out: List[dict] = []
        probe_targets: List[tuple] = []  # (index, url, headers)

        for item in data.get('streams') or []:
            stream_url = item.get('url')
            if not stream_url or item.get('externalUrl'):
                continue
            if any(h in stream_url for h in _BLOCKED):
                continue

            raw = (item.get('title') or '').strip()
            quality = get_quality(raw)

            lang = 'en'
            langm = re.search(r'\(([^)]+)\)', raw)
            if langm:
                lang = _LANG_MAP.get(langm.group(1).strip().lower(), 'en')

            bh = item.get('behaviorHints') or {}
            headers = {**(bh.get('headers') or {}),
                       **((bh.get('proxyHeaders') or {}).get('request') or {})}
            final = stream_url
            if headers:
                final = stream_url + append_headers(headers)
            if '.m3u8' in stream_url or '.mpd' in stream_url:
                final = f'isa+{final}'

            idx = len(out)
            out.append({
                'source': 'NoTorrent',
                'quality': quality,
                'language': lang,
                'url': final,
                'direct': True,
                'debridonly': False,
            })
            if '.m3u8' in stream_url:
                probe_targets.append((idx, stream_url, headers))

        self._refine_qualities(out, probe_targets)
        fflog(f'found {len(out)} streams')
        return out

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _refine_qualities(out: List[dict], targets: List[tuple]) -> None:
        """Probe HLS master playlists in parallel and overwrite quality with real RESOLUTION."""
        if not targets:
            return

        def _probe(i: int, u: str, h: dict) -> None:
            q = probe_m3u8_quality(u, h)
            if q:
                out[i]['quality'] = q

        threads = [threading.Thread(target=_probe, args=t, daemon=True) for t in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)


if __name__ == '__main__':
    try:
        from lib.ff.cmdline import DebugArgumentParser as ArgumentParser
    except ImportError:
        from argparse import ArgumentParser
    parser = ArgumentParser(description='Test NoTorrent source provider')
    parser.add_argument('imdb_id', help='IMDB ID (movie) or "ttXXXXXXX:S:E" for series')
    args = parser.parse_args()
    src = source()
    try:
        from pprint import pprint
        pprint(src.sources(args.imdb_id, [], []))
    except Exception as e:
        print(f'Error: {e}')
