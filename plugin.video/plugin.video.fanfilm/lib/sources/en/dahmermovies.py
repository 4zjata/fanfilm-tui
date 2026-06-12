# -*- coding: utf-8 -*-
"""
FanFilm - źródło: DahmerMovies (a.111477.xyz)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Port z JS All-in-One-Nuvio (dahmermovies-4k.js, czystsza wersja z HEAD-follow).
HTTP directory listing pod a.111477.xyz; ścieżki:
  movies: /movies/{Title (Year)}/
  tv:     /tvs/{Title}/Season {SS}/   (z zero-paddingiem i bez)
Pliki bezpośrednie .mkv/.mp4/.avi/.webm, sortowane 4K-first.
"""

from __future__ import annotations

import re
from typing import ClassVar, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlencode, parse_qs, quote, urljoin

from lib.ff import requests
from lib.ff.source_utils import DEFAULT_UA, append_headers, parse_source_quality_lang
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


_API = 'https://a.111477.xyz'
_STREAM_HEADERS: Dict[str, str] = {
    'User-Agent': DEFAULT_UA,
    'Referer': _API + '/',
}
_FILE_EXT_RE = re.compile(r'\.(mkv|mp4|avi|webm)$', re.I)
_ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.I | re.S)
_LINK_RE = re.compile(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>([^<]*)</a>', re.I)
_SIZE_RE = re.compile(r'<td[^>]*>\s*(\d+(?:\.\d+)?\s?[KMGT]B)\s*</td>', re.I)
_4K_RE = re.compile(r'2160p|4k|UHD', re.I)
_SCHEME = 'dahmer://'
_MAX_RESULTS = 5


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    ffitem: 'FFItem'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': DEFAULT_UA})

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not title or not year:
            return None
        return self._encode(type='movie', title=title, year=str(year))

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not tvshowtitle:
            return None
        return self._encode(type='tv', title=tvshowtitle, year=str(year or ''))

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

        try:
            html, dir_url = self._fetch_directory(params)
            if not html:
                return []

            links = self._parse_links(html)

            # episode filter
            if params.get('type') == 'tv' and params.get('e'):
                e_num = int(params['e'])
                pat = re.compile(rf'E0*{e_num}\b', re.I)
                links = [l for l in links if pat.search(l['text'])]

            # 4K first
            links.sort(key=lambda l: 0 if _4K_RE.search(l['text']) else 1)

            out: List[dict] = []
            for entry in links[:_MAX_RESULTS]:
                # Keep bare a.111477.xyz URL; resolve() handles the CF Workers
                # redirect lazily on play to avoid rate-limiting the redirect API.
                file_url = self._build_url(entry['href'], dir_url)
                quality, language, info = parse_source_quality_lang(entry['text'])
                if _4K_RE.search(entry['text']):
                    quality = '4K'

                out.append({
                    'source': 'DahmerMovies',
                    'quality': quality,
                    'language': language or 'en',
                    'url': file_url,
                    'info': info,
                    'size': entry.get('size', ''),
                    'filename': entry['text'],
                    'direct': True,
                    'debridonly': False,
                })

            fflog(f'found {len(out)} streams')
            return out
        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        # Follow a.111477.xyz redirect once → final CF Workers CDN URL.
        # Streaming/seek then talks directly to *.workers.dev which honors Range
        # without rate-limit. Headers are appended for the Kodi player.
        final = self._resolve_final_url(url) or url
        return f'{final}{append_headers(_STREAM_HEADERS)}'

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

    @staticmethod
    def _parse_links(html: str) -> List[Dict[str, str]]:
        links: List[Dict[str, str]] = []
        for row in _ROW_RE.finditer(html):
            row_html = row.group(1)
            m = _LINK_RE.search(row_html)
            if not m:
                continue
            href = m.group(1)
            text = m.group(2).strip()
            if not text or href == '../':
                continue
            if not _FILE_EXT_RE.search(text):
                continue
            size_m = _SIZE_RE.search(row_html)
            links.append({
                'text': text,
                'href': href,
                'size': size_m.group(1).strip() if size_m else '',
            })
        return links

    def _fetch_directory(self, params: Dict[str, str]) -> tuple:
        """Return (html, dir_url) of the first folder variant that responds 200."""
        clean = (params['title'] or '').replace(':', '')
        if params.get('type') == 'tv':
            s = int(params.get('s') or 1)
            variants = [
                f'/tvs/{quote(clean)}/Season%20{s:02d}/',
                f'/tvs/{quote(clean)}/Season%20{s}/',
            ]
        else:
            year = params.get('year', '')
            variants = [f'/movies/{quote(f"{clean} ({year})")}/']

        for path in variants:
            dir_url = _API + path
            try:
                r = self.session.get(dir_url, timeout=10)
                if r.ok:
                    return r.text, dir_url
            except Exception:
                continue
        return '', ''

    @staticmethod
    def _build_url(href: str, dir_url: str) -> str:
        if href.startswith('http'):
            return href
        if '/movies/' in href or '/tvs/' in href:
            return _API + (href if href.startswith('/') else '/' + href)
        return urljoin(dir_url, href)

    def _resolve_final_url(self, url: str) -> Optional[str]:
        """HEAD-follow redirect chain to canonical CF Workers CDN URL."""
        try:
            r = self.session.head(url, headers=_STREAM_HEADERS, timeout=8,
                                  allow_redirects=True)
            return r.url if r.ok else None
        except Exception:
            return None


if __name__ == '__main__':
    try:
        from lib.ff.cmdline import DebugArgumentParser as ArgumentParser
    except ImportError:
        from argparse import ArgumentParser
    parser = ArgumentParser(description='Test DahmerMovies source provider')
    parser.add_argument('--title', required=True)
    parser.add_argument('--year', default='')
    parser.add_argument('--type', default='movie', choices=['movie', 'tv'])
    parser.add_argument('--season', type=int, default=None)
    parser.add_argument('--episode', type=int, default=None)
    args = parser.parse_args()
    src = source()
    url = source._encode(type=args.type, title=args.title, year=args.year)
    if args.type == 'tv':
        url = f'{url}&s={args.season}&e={args.episode}'
    try:
        from pprint import pprint
        pprint(src.sources(url, [], []))
    except Exception as e:
        print(f'Error: {e}')
