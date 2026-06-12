# -*- coding: utf-8 -*-
"""
FanFilm - źródło: Videasy (10 backendowych serwerów przez player.videasy.net)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Port z JS All-in-One-Nuvio. Każdy z 10 serwerów Videasy (Neon/Yoru/Cypher/…)
zwraca zaszyfrowany payload, który deszyfruje zewnętrzne enc-dec.app.
"""

from __future__ import annotations

import threading
from typing import ClassVar, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlencode, parse_qs

from lib.ff import requests
from lib.ff.source_utils import get_quality, append_headers, probe_m3u8_quality
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


_DECRYPT_API = 'https://enc-dec.app/api/dec-videasy'
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36')
_HEADERS: Dict[str, str] = {
    'User-Agent': _UA,
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://player.videasy.net',
    'Referer': 'https://player.videasy.net/',
}
_STREAM_HEADERS: Dict[str, str] = {
    'Referer': 'https://player.videasy.net/',
    'Origin': 'https://player.videasy.net',
    'User-Agent': _UA,
}
_SERVERS: Dict[str, str] = {
    'Neon':   'https://api.videasy.net/myflixerzupcloud/sources-with-title',
    'Yoru':   'https://api.videasy.net/cdn/sources-with-title',
    'Cypher': 'https://api.videasy.net/moviebox/sources-with-title',
    'Reyna':  'https://api.videasy.net/primewire/sources-with-title',
    'Omen':   'https://api.videasy.net/onionplay/sources-with-title',
    'Breach': 'https://api.videasy.net/m4uhd/sources-with-title',
    'Ghost':  'https://api.videasy.net/primesrcme/sources-with-title',
    'Sage':   'https://api.videasy.net/1movies/sources-with-title',
    'Vyse':   'https://api.videasy.net/hdmovie/sources-with-title',
    'Raze':   'https://api.videasy.net/superflix/sources-with-title',
}
_MOVIES_ONLY = {'Yoru'}
_SCHEME = 'videasy://'


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    ffitem: 'FFItem'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        tmdb = str(self.ffitem.tmdb_id or '')
        if not tmdb or not title:
            return None
        return self._encode(type='movie', tmdb=tmdb, imdb=imdb or '',
                       title=title, year=str(year or ''))

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        # for episodes ffitem.tmdb_id is the episode id; show-level id lives on show_item
        show_item = getattr(self.ffitem, 'show_item', None)
        tmdb = str((show_item.tmdb_id if show_item else None) or self.ffitem.tmdb_id or '')
        if not tmdb or not tvshowtitle:
            return None
        return self._encode(type='tv', tmdb=tmdb, imdb=imdb or '',
                       title=tvshowtitle, year=str(year or ''))

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        if not url:
            return None
        return f'{url}&s={int(season)}&e={int(episode)}'

    def sources(self, url: Optional[str], hostDict: List[str],
                hostprDict: List[str]) -> 'List[SourceItem]':
        if not url:
            return []
        params = self._decode(url)
        if not params or not params.get('title'):
            return []

        results: List[dict] = []
        lock = threading.Lock()

        threads = [threading.Thread(target=self._fetch_server,
                                    args=(n, u, params, results, lock),
                                    daemon=True)
                   for n, u in _SERVERS.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        unique = self._dedupe(results)
        fflog(f'found {len(unique)} streams across {len(_SERVERS)} servers')
        return unique

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _encode(**kwargs: str) -> str:
        return _SCHEME + urlencode({k: v for k, v in kwargs.items() if v is not None})

    @staticmethod
    def _decode(url: str) -> Dict[str, str]:
        if not url.startswith(_SCHEME):
            return {}
        qs = url[len(_SCHEME):]
        parsed = parse_qs(qs, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def _fetch_server(self, name: str, server_url: str, params: Dict[str, str],
                      out: List[dict], lock: threading.Lock) -> None:
        """Hit one Videasy backend, decrypt, append SourceItems to `out`."""
        try:
            if params.get('type') == 'tv' and name in _MOVIES_ONLY:
                return
            q: Dict[str, str] = {
                'title': params['title'],
                'mediaType': params.get('type', 'movie'),
                'year': params.get('year', ''),
                'tmdbId': params.get('tmdb', ''),
                'imdbId': params.get('imdb', ''),
            }
            if params.get('type') == 'tv':
                q['seasonId'] = params.get('s', '')
                q['episodeId'] = params.get('e', '')
            r = self.session.get(server_url, params=q, timeout=10)
            if not r.ok:
                return
            text = r.text or ''
            if len(text) < 20 or text.lstrip().startswith('<!'):
                return
            dec = self.session.post(
                _DECRYPT_API,
                json={'text': text, 'id': params.get('tmdb', '')},
                headers={'Content-Type': 'application/json'},
                timeout=10,
            )
            if not dec.ok:
                return
            payload = dec.json()
            data = payload.get('result') if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                data = payload if isinstance(payload, dict) else {}
            for s in data.get('sources') or []:
                su = s.get('url')
                if not su:
                    continue
                quality = get_quality(str(s.get('quality') or ''))
                if '.m3u8' in su:
                    probed = probe_m3u8_quality(su, _STREAM_HEADERS)
                    if probed:
                        quality = probed
                final = f'{su}{append_headers(_STREAM_HEADERS)}'
                if '.m3u8' in su or '.mpd' in su:
                    final = f'isa+{final}'
                with lock:
                    out.append({
                        'source': f'VIDEASY {name}',
                        'quality': quality,
                        'language': 'en',
                        'url': final,
                        'direct': True,
                        'debridonly': False,
                    })
        except Exception:
            fflog_exc()

    @staticmethod
    def _dedupe(items: List[dict]) -> List[dict]:
        """Drop entries with the same base URL (header tail ignored)."""
        seen: set = set()
        out: List[dict] = []
        for it in items:
            base = it['url'].split('|', 1)[0]
            if base in seen:
                continue
            seen.add(base)
            out.append(it)
        return out


if __name__ == '__main__':
    try:
        from lib.ff.cmdline import DebugArgumentParser as ArgumentParser
    except ImportError:
        from argparse import ArgumentParser
    parser = ArgumentParser(description='Test Videasy source provider')
    parser.add_argument('tmdb_id', help='TMDB ID')
    parser.add_argument('--title', required=True)
    parser.add_argument('--year', default='')
    parser.add_argument('--imdb', default='')
    parser.add_argument('--type', default='movie', choices=['movie', 'tv'])
    parser.add_argument('--season', type=int, default=None)
    parser.add_argument('--episode', type=int, default=None)
    args = parser.parse_args()
    src = source()
    url = source._encode(type=args.type, tmdb=args.tmdb_id, imdb=args.imdb,
                  title=args.title, year=args.year)
    if args.type == 'tv':
        url = f'{url}&s={args.season}&e={args.episode}'
    try:
        from pprint import pprint
        pprint(src.sources(url, [], []))
    except Exception as e:
        print(f'Error: {e}')
