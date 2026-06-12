# -*- coding: utf-8 -*-
"""
FanFilm - źródło: streamimdb.ru
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  streamimdb.ru/embed/movie/{imdb} → iframe → brightpathsignals.com
  brightpathsignals.com embed page contains CONFIG with streamDataApiUrl.
  Call streamdata.vaplayer.ru/api.php?imdb=X&type=movie → returns stream_urls (HLS m3u8).
  Resolution done entirely in sources() → returned as direct ISA URLs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, ClassVar
from urllib.parse import parse_qs, urlencode

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

from lib.ff import requests
from lib.ff.item import FFItem
from lib.ff.source_utils import FF_UA, probe_m3u8_quality
from lib.ff.resolve_utils import build_isa_url
from lib.ff.log_utils import fflog, fflog_exc


# ─── constants ────────────────────────────────────────────────────────────────────

_BASE = 'https://streamimdb.ru'
_BP_BASE = 'https://brightpathsignals.com'
_STREAMDATA_API = 'https://streamdata.vaplayer.ru/api.php'


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']
    domains: ClassVar[List[str]] = ['streamimdb.ru']

    ffitem: FFItem

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not imdb:
            return None
        return urlencode({'imdb': imdb})

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not imdb:
            return None
        return urlencode({'imdb': imdb})

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        if not url:
            return None
        data = {k: v[0] for k, v in parse_qs(url).items()}
        data.update({'season': season, 'episode': episode})
        return urlencode(data)

    def sources(self, url: Optional[str], hostDict: List[str],
                hostprDict: List[str]) -> 'List[SourceItem]':
        result = []
        try:
            if not url:
                return result
            data = {k: v[0] for k, v in parse_qs(url).items()}
            imdb = data.get('imdb', '')
            if not imdb:
                return result

            is_tv = 'season' in data and 'episode' in data
            season = data.get('season', '')
            episode = data.get('episode', '')

            if is_tv:
                bp_url = f'{_BP_BASE}/embed/tv?imdb={imdb}&season={season}&episode={episode}'
                stream_ref = f'{_BASE}/embed/tv?imdb={imdb}&season={season}&episode={episode}'
            else:
                bp_url = f'{_BP_BASE}/embed/movie/{imdb}'
                stream_ref = f'{_BASE}/embed/movie/{imdb}'

            sess = requests.Session()
            sess.headers.update({'User-Agent': FF_UA, 'Referer': stream_ref})
            bp_resp = sess.get(bp_url, timeout=15)
            if not bp_resp.ok:
                return result

            api_url = f'{_STREAMDATA_API}?imdb={imdb}&type={"tv" if is_tv else "movie"}'
            if is_tv:
                api_url += f'&season={season}&episode={episode}'

            api_resp = sess.get(api_url, timeout=15,
                                headers={'Referer': bp_url, 'Accept': 'application/json'})
            if not api_resp.ok:
                return result
            api_data = api_resp.json()
            if api_data.get('status_code') != '200':
                return result

            stream_urls = api_data.get('data', {}).get('stream_urls', [])
            if not stream_urls:
                return result

            probe_headers = {'User-Agent': FF_UA, 'Referer': bp_url}
            for i, stream_url in enumerate(stream_urls):
                quality = probe_m3u8_quality(stream_url, probe_headers, on_unknown='1080p')
                if not quality:
                    fflog(f'streamimdb: skip unreachable stream {i + 1}')
                    continue
                isa_url = build_isa_url(stream_url, bp_url, ua=FF_UA)
                result.append({
                    'source': f'streamimdb-{i + 1}',
                    'quality': quality,
                    'language': 'en',
                    'url': isa_url,
                    'direct': True,
                    'debridonly': False,
                })

            fflog(f'streamimdb: {len(result)}/{len(stream_urls)} streams')
        except Exception:
            fflog_exc()
        return result

    def resolve(self, url: str) -> Optional[str]:
        return url


if __name__ == '__main__':
    try:
        from lib.ff.cmdline import DebugArgumentParser as ArgumentParser
    except ImportError:
        from argparse import ArgumentParser
    parser = ArgumentParser(description='Test streamimdb.ru source provider')
    parser.add_argument('imdb', help='IMDB ID (e.g. tt0167260)')
    parser.add_argument('--season', type=int, default=None)
    parser.add_argument('--episode', type=int, default=None)
    args = parser.parse_args()
    src = source()
    if args.season and args.episode:
        url = urlencode({'imdb': args.imdb, 'season': str(args.season), 'episode': str(args.episode)})
    else:
        url = urlencode({'imdb': args.imdb})
    try:
        from pprint import pprint
        pprint(src.sources(url, [], []))
    except Exception as e:
        print(f'Error: {e}')
