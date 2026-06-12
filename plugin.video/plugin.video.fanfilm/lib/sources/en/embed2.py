# -*- coding: utf-8 -*-
"""
FanFilm - źródło: 2embed.cc

Aggregator front-end with 5 movie / 3 TV backends. We discover the active
backend URLs from the 2embed page (one HTTP call) and resolve those we can
handle in pure Python in parallel.

Resolvable backends:
  swish  → lookmovie2.skin/e/{id}        → JW packer → hls3 m3u8        (see resolve_swish)
  vsrc   → vidsrc-embed.ru/embed/{...}   → cloudnestra rcpvip → m3u8    (see resolve_rcp entry='rcpvip')
  xps    → play.xpass.top/e/{...}        → inline playlist.json + backups → m3u8 (×N)

Skipped:
  vsrcc  → vidsrc.cc/v2/...   – served by Videasy player (duplicates videasy.py)
  vkng   → vidking.net/...    – SPA, no plaintext source URL accessible without a browser

Copyright (C) 2026 :)
Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import json
import re
import threading
from typing import TYPE_CHECKING, ClassVar, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlencode

from lib.ff import requests
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.resolve_utils import resolve_rcp, resolve_swish, build_isa_url
from lib.ff.source_utils import FF_UA, append_headers, get_quality, probe_m3u8_quality

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


_BASE = 'https://www.2embed.cc'
_MOVIE_URL = _BASE + '/embed/{imdb}'
_TV_URL = _BASE + '/embedtv/{imdb}&s={season}&e={episode}'
_XPASS_MOVIE = 'https://play.xpass.top/e/movie/{imdb}'
_XPASS_TV = 'https://play.xpass.top/e/tv/{imdb}/{season}/{episode}'
_VSRC_MOVIE = 'https://vidsrc-embed.ru/embed/movie/{imdb}'
_VSRC_TV = 'https://vidsrc-embed.ru/embed/tv/{imdb}?season={season}&episode={episode}'

_DOMAINS: List[str] = ['2embed.cc', 'streamsrcs.2embed.cc']

_SCHEME = 'embed2://'

_HEADERS: Dict[str, str] = {
    'User-Agent': FF_UA,
    'Referer': 'https://watch.cinewave.qzz.io/',
}

# extract <a onclick="go('https://streamsrcs.2embed.cc/{server}...')"> from 2embed page
_RE_BACKEND = re.compile(
    r"""go\(['"](https://streamsrcs\.2embed\.cc/([a-z]+(?:-tv)?)\?[^'"]+)['"]\)""",
    re.I,
)
_RE_SWISH_ID = re.compile(r'[?&]id=([A-Za-z0-9_-]+)')


# ─── source ──────────────────────────────────────────────────────────────────────

class source:  # noqa: N801

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']
    domains: ClassVar[List[str]] = _DOMAINS

    ffitem: 'FFItem'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not imdb:
            return None
        return self._encode(type='movie', imdb=imdb)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not imdb:
            return None
        return self._encode(type='tv', imdb=imdb)

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        if not url:
            return None
        return f'{url}&s={int(season)}&e={int(episode)}'

    def sources(self, url: Optional[str], hostDict: List[str],
                hostprDict: List[str]) -> 'List[SourceItem]':
        out: List[dict] = []
        if not url:
            return out
        params = self._decode(url)
        imdb = params.get('imdb', '')
        if not imdb:
            return out

        is_tv = params.get('type') == 'tv'
        season = params.get('s', '')
        episode = params.get('e', '')

        swish_id = self._fetch_swish_id(imdb, is_tv, season, episode)

        lock = threading.Lock()
        threads: List[threading.Thread] = []

        if swish_id and not is_tv:
            # 2embed exposes swish only for movies
            threads.append(threading.Thread(
                target=self._do_swish, args=(swish_id, out, lock), daemon=True))

        threads.append(threading.Thread(
            target=self._do_xps,
            args=(imdb, is_tv, season, episode, out, lock),
            daemon=True))
        threads.append(threading.Thread(
            target=self._do_vsrc,
            args=(imdb, is_tv, season, episode, out, lock),
            daemon=True))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        fflog(f'sources: {len(out)}')
        return out

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _encode(**kwargs: str) -> str:
        return _SCHEME + urlencode({k: v for k, v in kwargs.items() if v})

    @staticmethod
    def _decode(url: str) -> Dict[str, str]:
        if not url or not url.startswith(_SCHEME):
            return {}
        parsed = parse_qs(url[len(_SCHEME):], keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def _fetch_swish_id(self, imdb: str, is_tv: bool,
                        season: str, episode: str) -> Optional[str]:
        try:
            if is_tv:
                page_url = _TV_URL.format(imdb=imdb, season=season, episode=episode)
            else:
                page_url = _MOVIE_URL.format(imdb=imdb)
            r = self.session.get(page_url, timeout=15, allow_redirects=True)
            if not r.ok:
                fflog(f'2embed.cc: status {r.status_code} for {imdb}')
                return None
            for m in _RE_BACKEND.finditer(r.text):
                full_url, name = m.group(1), m.group(2).lower()
                if name in ('swish', 'swish-tv'):
                    id_m = _RE_SWISH_ID.search(full_url)
                    if id_m:
                        return id_m.group(1)
            return None
        except Exception:
            fflog_exc()
            return None

    def _do_swish(self, swish_id: str, out: List[dict], lock: threading.Lock) -> None:
        try:
            resolved = resolve_swish(swish_id, referer='https://www.2embed.cc/')
            if not resolved:
                return
            with lock:
                out.append({
                    'source': 'swish',
                    'quality': '720p',
                    'language': 'en',
                    'url': resolved,
                    'direct': True,
                    'debridonly': False,
                })
        except Exception:
            fflog_exc()

    def _do_xps(self, imdb: str, is_tv: bool, season: str, episode: str,
                out: List[dict], lock: threading.Lock) -> None:
        try:
            if is_tv:
                page_url = _XPASS_TV.format(imdb=imdb, season=season, episode=episode)
            else:
                page_url = _XPASS_MOVIE.format(imdb=imdb)
            r = self.session.get(page_url, timeout=15,
                                 headers={'Referer': 'https://streamsrcs.2embed.cc/'})
            if not r.ok:
                return

            html = r.text
            backups = self._parse_xps_backups(html)
            if not backups:
                # fall back to primary playlist if backups parse fails
                primary = re.search(r'"playlist"\s*:\s*"([^"]+)"', html)
                if primary:
                    backups = [{'name': 'Default', 'url': primary.group(1)}]
            if not backups:
                return

            referer = page_url
            probe_headers = {'User-Agent': FF_UA, 'Referer': referer}
            for b in backups:
                pl_url = b['url']
                if not pl_url.startswith('http'):
                    pl_url = 'https://play.xpass.top' + pl_url
                m3u8 = self._fetch_xps_playlist(pl_url, referer)
                if not m3u8:
                    continue
                quality = probe_m3u8_quality(m3u8, probe_headers, on_unknown='1080p')
                if not quality:
                    # dead/blocked m3u8 — proxies often hand out expired tokens
                    fflog(f"xps: skip unreachable backup {b['name']}")
                    continue
                stream_url = build_isa_url(m3u8, referer, ua=FF_UA)
                with lock:
                    out.append({
                        'source': f"xps/{b['name']}",
                        'quality': quality,
                        'language': 'en',
                        'url': stream_url,
                        'direct': True,
                        'debridonly': False,
                    })
        except Exception:
            fflog_exc()

    @staticmethod
    def _parse_xps_backups(html: str) -> List[Dict[str, str]]:
        """Extract backups=[{id,name,url,dl},...] inline array from xpass page."""
        m = re.search(r'var\s+backups\s*=\s*(\[.*?\])\s*</script>', html, re.S)
        if not m:
            return []
        try:
            arr = json.loads(m.group(1))
        except Exception:
            return []
        return [
            {'name': b.get('name', '?'), 'url': b.get('url', ''),
             'label': b.get('label', '')}
            for b in arr if isinstance(b, dict) and b.get('url')
        ]

    def _fetch_xps_playlist(self, pl_url: str, referer: str) -> Optional[str]:
        try:
            r = self.session.get(pl_url, timeout=10,
                                 headers={'Referer': referer})
            if not r.ok:
                return None
            data = r.json()
            playlist = data.get('playlist') or []
            for item in playlist:
                for src in item.get('sources') or []:
                    file_url = src.get('file')
                    if file_url and '.m3u8' in file_url:
                        return file_url
        except Exception:
            pass
        return None

    def _do_vsrc(self, imdb: str, is_tv: bool, season: str, episode: str,
                 out: List[dict], lock: threading.Lock) -> None:
        try:
            if is_tv:
                page_url = _VSRC_TV.format(imdb=imdb, season=season, episode=episode)
            else:
                page_url = _VSRC_MOVIE.format(imdb=imdb)
            r = self.session.get(page_url, timeout=15, allow_redirects=True,
                                 headers={'Referer': 'https://streamsrcs.2embed.cc/'})
            if not r.ok:
                return
            iframe_m = re.search(
                r'id="player_iframe"\s+src="//cloudnestra\.com/(rcpvip|rcp)/([^"]+)"',
                r.text,
            )
            if not iframe_m:
                fflog('2embed vsrc: no rcpvip hash in embed page')
                return
            entry, rcp_hash = iframe_m.group(1), iframe_m.group(2)
            resolved = resolve_rcp(rcp_hash, referer=r.url, entry=entry)
            if not resolved:
                return
            quality = self._probe_isa_quality(resolved)
            if not quality:
                fflog('vsrc: skip unreachable resolved URL')
                return
            with lock:
                out.append({
                    'source': 'vsrc',
                    'quality': quality,
                    'language': 'en',
                    'url': resolved,
                    'direct': True,
                    'debridonly': False,
                })
        except Exception:
            fflog_exc()

    @staticmethod
    def _probe_isa_quality(isa_url: str) -> Optional[str]:
        """Strip the `isa+` prefix and `|headers` suffix to probe the raw m3u8."""
        try:
            url = isa_url[4:] if isa_url.startswith('isa+') else isa_url
            base, _, hdrs_qs = url.partition('|')
            headers = {}
            if hdrs_qs:
                for kv in hdrs_qs.split('&'):
                    if '=' in kv:
                        k, v = kv.split('=', 1)
                        headers[k] = unquote(v)
            return probe_m3u8_quality(base, headers, on_unknown='1080p')
        except Exception:
            fflog_exc()


if __name__ == '__main__':
    try:
        from lib.ff.cmdline import DebugArgumentParser as ArgumentParser
    except ImportError:
        from argparse import ArgumentParser
    parser = ArgumentParser(description='Test 2embed.cc source provider')
    parser.add_argument('imdb', help='IMDB ID (e.g. tt12042730)')
    parser.add_argument('--type', default='movie', choices=['movie', 'tv'])
    parser.add_argument('--season', type=int, default=None)
    parser.add_argument('--episode', type=int, default=None)
    args = parser.parse_args()
    src = source()
    url = source._encode(type=args.type, imdb=args.imdb)
    if args.type == 'tv':
        url = f'{url}&s={args.season}&e={args.episode}'
    try:
        from pprint import pprint
        pprint(src.sources(url, [], []))
    except Exception as e:
        print(f'Error: {e}')
