# -*- coding: utf-8 -*-
"""
FanFilm - źródło: VidZee (dl.vidzee.wtf) + 111477.xyz directory
Copyright (C) 2026 :)

Łączy: v8 API, vcd (cinedown) API, xc-vod index + directory listing
Deduplikacja po URL.

Flow:
   1. movie()/tvshow() encode tmdb_id + type into vidzee:// scheme
   2. sources() queries all three sources in parallel threads
   3. resolve() follows 307 redirect chain → working CDN URL
"""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING, ClassVar, Dict, List, Optional
from urllib.parse import urlencode, parse_qs, quote

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

from lib.ff import requests
from lib.ff.item import FFItem
from lib.ff.source_utils import FF_UA, convert_size, get_quality, get_quality_from_filename
from lib.ff.log_utils import fflog, fflog_exc

_BASE = 'https://dl.vidzee.wtf'
_CINEDOWN = 'https://api.izaak-aadi2012.workers.dev'

_HEADERS: Dict[str, str] = {
    'User-Agent': FF_UA,
    'Referer': 'https://player.vidzee.wtf/',
    'Origin': 'https://player.vidzee.wtf',
}

# xc-vod index
_INDEX_BASE = 'https://xc-vod-files.pages.dev'
_CDN_BASE = 'https://a.111477.xyz'
_XTREAM_API = 'https://xtream-vod.data-search.workers.dev'

_XHEADERS = {'User-Agent': FF_UA}

_SCHEME = 'vidzee://'

# API versions
_MOVIE_VERSIONS = ('v8', 'vcd', 'xvod')
_TV_VERSIONS = ('v8', 'vcd', 'xvod')

# dir listing regexes
_RE_TR = re.compile(r'<tr\s+data-entry[^>]*>(.*?)</tr>', re.DOTALL)
_RE_NAME = re.compile(r'data-name="([^"]*)"')
_RE_URL = re.compile(r'data-url="([^"]*)"')
_RE_SIZE = re.compile(r'data-sort="(\d+)"')


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.domains = ['vidzee.wtf', 'dl.vidzee.wtf', '111477.xyz']

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        tmdb = str(self.ffitem.tmdb_id or '')
        if not tmdb:
            return None
        return self._encode(type='movie', tmdb=tmdb, imdb=imdb or '',
                       title=title, year=str(year or ''))

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        show_item = getattr(self.ffitem, 'show_item', None)
        tmdb = str((show_item.tmdb_id if show_item else None) or self.ffitem.tmdb_id or '')
        if not tmdb:
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
        if not params or not params.get('tmdb'):
            return []

        results: List[dict] = []
        lock = threading.Lock()
        tmdb = params['tmdb']
        is_tv = params.get('type') == 'tv'
        season = params.get('s', '1')
        episode = params.get('e', '1')

        threads = []

        versions = _TV_VERSIONS if is_tv else _MOVIE_VERSIONS
        for v in versions:
            if v == 'xvod':
                title = params.get('title', '')
                t = threading.Thread(
                    target=self._fetch_xvod,
                    args=(tmdb, is_tv, season, episode, results, lock, title),
                    daemon=True,
                )
            elif is_tv:
                api_url = f'{_CINEDOWN}/tv/{tmdb}/{season}/{episode}' if v == 'vcd' else \
                          f'{_BASE}/download/tv/{v}/{tmdb}/{season}/{episode}'
                t = threading.Thread(
                    target=self._fetch_version,
                    args=(v, api_url, results, lock),
                    daemon=True,
                )
            else:
                api_url = f'{_CINEDOWN}/movie/{tmdb}' if v == 'vcd' else \
                          f'{_BASE}/download/movie/{v}/{tmdb}'
                t = threading.Thread(
                    target=self._fetch_version,
                    args=(v, api_url, results, lock),
                    daemon=True,
                )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25)

        unique = self._dedupe(results)
        fflog(f'vidzee: {len(unique)} streams ({len(results)} raw)')
        return unique

    def resolve(self, url: str) -> Optional[str]:
        try:
            if 'a.111477.xyz' in url:
                resp = requests.get(url, headers=_HEADERS, timeout=30, stream=True)
                resp.close()
                if resp.ok:
                    final = resp.url
                    fflog(f'vidzee resolved: {final}')
                    return final
                fflog(f'vidzee resolve failed: HTTP {resp.status_code}, final={resp.url}')
            return None
        except Exception:
            fflog_exc()
            return None

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

    # version → source label
    _VERSIONS: ClassVar[dict] = {
        'v8': 'VIDZEE 111477',
        'vcd': 'VIDZEE CD',
        'xvod': 'VIDZEE DIR',
    }

    def _fetch_version(self, version: str, api_url: str,
                       out: List[dict], lock: threading.Lock) -> None:
        try:
            resp = requests.get(api_url, headers=_HEADERS, timeout=15)
            if not resp.ok:
                return
            data = resp.json()
            label = self._VERSIONS.get(version)
            if label:
                if version == 'vcd':
                    self._parse_cinedown(data, out, lock, label)
                else:
                    self._parse_links(data, out, lock, label, 'links')
        except Exception:
            pass

    # ── xvod: xc-vod index + directory listing ────────────────────────────

    def _fetch_xvod(self, tmdb: str, is_tv: bool, season: str, episode: str,
                    out: List[dict], lock: threading.Lock, title: str = '') -> None:
        try:
            if is_tv:
                entries = self._xvod_tv(tmdb, season, episode, title)
            else:
                entries = self._xvod_movie(tmdb)
            if entries:
                with lock:
                    out.extend(entries)
        except Exception:
            fflog_exc()

    def _xvod_movie(self, tmdb: str) -> List[Dict]:
        api = f'{_XTREAM_API}/player_api.php?action=get_vod_info&vod_id={tmdb}'
        resp = requests.get(api, headers=_XHEADERS, timeout=15)
        if not resp.ok:
            return []
        data = resp.json()
        direct = data.get('movie_data', {}).get('direct_source', '')
        if not direct:
            return []
        folder = direct.rsplit('/', 1)[0] + '/'
        name = data.get('movie_data', {}).get('name', str(tmdb))

        resp2 = requests.get(folder, headers=_XHEADERS, timeout=30)
        if not resp2.ok:
            return []

        files = self._parse_dir_listing(resp2.text, folder)
        label = f'VIDZEE DIR {name[:30]}'
        return [self._build_entry(f, label) for f in files]

    def _xvod_tv(self, tmdb: str, season: str, episode: str,
                 title: str = '') -> List[Dict]:
        ep_code = f'S{int(season):02d}E{int(episode):02d}'

        # Try 1: construct folder URL from TMDB title (no cache)
        folder_base = self._xvod_tv_from_title(title)
        # Try 2: fallback to series.json cache
        if folder_base is None:
            folder_base = self._xvod_tv_from_cache(tmdb)

        if folder_base is None:
            return []

        folder_base = folder_base.rstrip('/')
        season_url = f'{folder_base}/Season {int(season)}/'
        resp = requests.get(season_url, headers=_XHEADERS, timeout=30)
        if not resp.ok:
            season_url = f'{folder_base}/Season {int(season):02d}/'
            resp = requests.get(season_url, headers=_XHEADERS, timeout=30)
            if not resp.ok:
                return []

        all_files = self._parse_dir_listing(resp.text, season_url)

        matched = [f for f in all_files if ep_code in f['name']]
        if not matched:
            ep_ci = ep_code.lower()
            matched = [f for f in all_files if ep_ci in f['name'].lower()]

        label = f'VIDZEE DIR'
        return [self._build_entry(f, label) for f in matched]

    def _xvod_tv_from_title(self, title: str) -> Optional[str]:
        if not title:
            return None
        encoded = quote(title, safe='')
        folder = f'{_CDN_BASE}/tvs/{encoded}/'
        resp = requests.get(folder, headers=_XHEADERS, timeout=15)
        if resp.ok:
            return folder
        return None

    def _xvod_tv_from_cache(self, tmdb: str) -> Optional[str]:
        resp = requests.get(f'{_INDEX_BASE}/series.json', headers=_XHEADERS, timeout=30)
        if not resp.ok:
            return None
        for s in resp.json():
            if str(s.get('tmdb_id', '')) == str(tmdb):
                return s['folder_url']
        return None

    # ── directory listing parser ───────────────────────────────────────────

    @staticmethod
    def _parse_dir_listing(html: str, base_url: str) -> List[Dict]:
        files = []
        for tr in _RE_TR.finditer(html):
            full = tr.group(0)
            name_m = _RE_NAME.search(full)
            url_m = _RE_URL.search(full)
            size_m = _RE_SIZE.search(full)
            if name_m and url_m and size_m:
                size = int(size_m.group(1))
                if size > 0:
                    path = url_m.group(1)
                    full_url = path if path.startswith('http') else f'{_CDN_BASE}{path}'
                    files.append({
                        'name': name_m.group(1),
                        'url': full_url,
                        'size': size,
                    })
        files.sort(key=lambda f: f['size'])
        return files

    @staticmethod
    def _build_entry(f: Dict, label: str) -> Dict:
        quality = get_quality_from_filename(f['name']) or '1080p'
        size = convert_size(f['size'])
        url = f['url']
        scheme_rest = url.split('://', 1)
        if len(scheme_rest) == 2:
            url = f'{scheme_rest[0]}://{quote(scheme_rest[1], safe="%/:=&?~#+!$,;@")}'
        entry = {
            'source': label,
            'quality': quality,
            'language': 'en',
            'url': url,
            'direct': False,
            'debridonly': False,
        }
        if f['name']:
            entry['filename'] = f['name']
        if size:
            entry['size'] = size
        return entry

    # ── JSON API parsers ───────────────────────────────────────────────────

    @staticmethod
    def _parse_links(data: dict, out: List[dict], lock: threading.Lock,
                     label: str, data_key: str) -> None:
        items = data.get(data_key) or []
        seen = set()
        for item in items:
            url = item.get('url', '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            name = item.get('name') or ''
            raw_q = item.get('quality') or ''
            if raw_q.strip():
                quality = get_quality(raw_q) or '1080p'
            elif name:
                quality = get_quality_from_filename(name) or get_quality(name) or '1080p'
            else:
                quality = '1080p'
            size_val = item.get('size')
            if size_val is not None:
                size = convert_size(int(size_val))
            else:
                size = ''
            scheme_rest = url.split('://', 1)
            if len(scheme_rest) == 2:
                url = f'{scheme_rest[0]}://{quote(scheme_rest[1], safe="%/:=&?~#+!$,;@")}'
            entry = {
                'source': label,
                'quality': quality,
                'language': 'en',
                'url': url,
                'direct': False,
                'debridonly': False,
            }
            if name:
                entry['filename'] = name
            if size:
                entry['size'] = size
            with lock:
                out.append(entry)

    @staticmethod
    def _parse_cinedown(data: dict, out: List[dict], lock: threading.Lock,
                        label: str) -> None:
        items = data.get('downloads') or []
        seen = set()
        for item in items:
            url = item.get('url', '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            quality = item.get('resolution') or '1080p'
            if quality == '480p':
                quality = 'SD'
            size = item.get('size') or ''
            name = item.get('filename') or ''
            scheme_rest = url.split('://', 1)
            if len(scheme_rest) == 2:
                url = f'{scheme_rest[0]}://{quote(scheme_rest[1], safe="%/:=&?~#+!$,;@")}'
            entry = {
                'source': label,
                'quality': quality,
                'language': 'en',
                'url': url,
                'direct': False,
                'debridonly': False,
            }
            if name:
                entry['filename'] = name
            if size:
                entry['size'] = size
            with lock:
                out.append(entry)

    @staticmethod
    def _dedupe(items: List[dict]) -> List[dict]:
        seen: set = set()
        out: List[dict] = []
        for it in items:
            base = it['url'].split('|', 1)[0]
            if base in seen:
                continue
            seen.add(base)
            out.append(it)
        return out



