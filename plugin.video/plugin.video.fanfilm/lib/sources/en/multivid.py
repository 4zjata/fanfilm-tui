# -*- coding: utf-8 -*-
"""
FanFilm - źródło: MultiVid (vidlink.pro)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Port z JS All-in-One-Nuvio (multivid.js). Pominięte:
  - VidEasy   → już mamy w xx/videasy.py
  - VidSrc    → już mamy w en/playimdb.py i en/vsembed_vidsrc.py
  - Vidmody   → fałszywy source: master playlist zawsze prowadzi do
                segmentów typu thumbnail-N.jpg (statyczne miniatury, nie wideo).
                Sprawdzone na wielu tytułach z różnymi UA — zawsze fake content.
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlencode, parse_qs

from lib.ff import requests
from lib.ff.source_utils import append_headers, probe_m3u8_quality
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


_SCHEME = 'multivid://'
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36')

_VIDLINK_ENC = 'https://enc-dec.app/api/enc-vidlink'
_VIDLINK_API = 'https://vidlink.pro/api/b'
_VIDLINK_HEADERS: Dict[str, str] = {
    'Referer': 'https://vidlink.pro/',
    'Origin': 'https://vidlink.pro',
    'User-Agent': _UA,
}


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    ffitem: 'FFItem'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': _UA})

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        tmdb = str(self.ffitem.tmdb_id or '')
        if not tmdb and not imdb:
            return None
        return self._encode(type='movie', tmdb=tmdb, imdb=imdb or '')

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        show_item = getattr(self.ffitem, 'show_item', None)
        tmdb = str((show_item.tmdb_id if show_item else None) or self.ffitem.tmdb_id or '')
        if not tmdb and not imdb:
            return None
        return self._encode(type='tv', tmdb=tmdb, imdb=imdb or '')

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
        if not params:
            return []

        results: List[dict] = []
        self._resolve_vidlink(params, results)
        unique = self._dedupe(results)
        fflog(f'found {len(unique)} streams')
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

    def _resolve_vidlink(self, params: Dict[str, str], out: List[dict]) -> None:
        """VidLink: enc-dec.app encodes TMDB → vidlink.pro/api/b/... → m3u8 playlist."""
        tmdb = params.get('tmdb')
        if not tmdb:
            return
        try:
            r = self.session.get(f'{_VIDLINK_ENC}?text={tmdb}', timeout=10)
            if not r.ok:
                return
            encoded = (r.json() or {}).get('result')
            if not encoded:
                return

            if params.get('type') == 'tv':
                s = params.get('s', '1')
                e = params.get('e', '1')
                api_url = f'{_VIDLINK_API}/tv/{encoded}/{s}/{e}?multiLang=0'
            else:
                api_url = f'{_VIDLINK_API}/movie/{encoded}?multiLang=0'

            r = self.session.get(api_url, headers=_VIDLINK_HEADERS, timeout=10)
            if not r.ok:
                return
            if not r.text.strip():
                return
            payload = r.json() or {}
            playlist = ((payload.get('stream') or {}).get('playlist') or '').strip()
            if not playlist:
                return

            quality = probe_m3u8_quality(playlist, _VIDLINK_HEADERS) or 'SD'
            
            headers = dict(_VIDLINK_HEADERS)
            captions = (payload.get('stream') or {}).get('captions') or []
            if captions:
                import json
                headers['subtitles'] = json.dumps(captions)

            final = f'{playlist}{append_headers(headers)}'
            if '.m3u8' in playlist or '.mpd' in playlist:
                final = f'isa+{final}'

            out.append({
                'source': 'VidLink',
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
    parser = ArgumentParser(description='Test MultiVid source provider')
    parser.add_argument('tmdb_id', help='TMDB ID')
    parser.add_argument('--imdb', default='', help='IMDB ID (required for Vidmody)')
    parser.add_argument('--type', default='movie', choices=['movie', 'tv'])
    parser.add_argument('--season', type=int, default=None)
    parser.add_argument('--episode', type=int, default=None)
    args = parser.parse_args()
    src = source()
    url = source._encode(type=args.type, tmdb=args.tmdb_id, imdb=args.imdb)
    if args.type == 'tv':
        url = f'{url}&s={args.season}&e={args.episode}'
    try:
        from pprint import pprint
        pprint(src.sources(url, [], []))
    except Exception as e:
        print(f'Error: {e}')
