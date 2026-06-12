# -*- coding: utf-8 -*-
"""
FanFilm - źródło: ogladaj.cc (movies only)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  1. POST /engine/ajax/controller.php?mod=search  (IMDB ID → page URL)
  2. GET movie page                                (iframe csst.online/embed/{id})
  3. GET secvideo1.online/embed/{id}/              (Playerjs file=[360p]url,[720p]url,[1080p]url)
  4. GET get_file URL → 302 → CDN URL (MP4 360p/720p/1080p)

dle_login_hash is stable for guests (server-side hash, not session-based).
"""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING, Dict, List, Optional
from urllib.parse import urlencode, parse_qs, quote_plus

from lib.ff import requests
from lib.ff.item import FFItem
from lib.ff.source_utils import FF_UA
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

_BASE = 'https://ogladaj.cc'
_SEARCH_URL = f"{_BASE}/engine/ajax/controller.php?mod=search"
_SECVIDEO = 'https://secvideo1.online'

# Stable guest hash (SHA-1 based on server key, not session)
_GUEST_HASH_CACHE: list[str] = []

_HEADERS = {
    'User-Agent': FF_UA,
    'Referer': _BASE + '/',
    'X-Requested-With': 'XMLHttpRequest',
}

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority = 1
    language = ['pl']
    domains = ['ogladaj.cc']

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            return urlencode({'imdb': imdb})
        except Exception:
            fflog_exc()

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> None:
        return None  # movies only

    def episode(self, url: None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> None:
        return None  # movies only

    def sources(self, url: str, hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            if not url:
                return result
            imdb = (parse_qs(url).get('imdb') or [''])[0]
            if not imdb:
                return result

            sess = requests.Session()
            sess.headers.update({'User-Agent': FF_UA})

            # 1. Guest hash (may require GET homepage once per session)
            user_hash = self._get_guest_hash(sess)
            if not user_hash:
                fflog('ogladaj: dle_login_hash missing')
                return result

            # 2. Search by IMDB ID
            fflog(f'query: {imdb!r}')
            resp = sess.post(
                _SEARCH_URL,
                data={'query': imdb, 'user_hash': user_hash},
                headers=_HEADERS,
                timeout=10,
            )
            m = re.search(r'href="((?:https://ogladaj\.cc)?/film/[^"]+)"', resp.text)
            if not m:
                fflog('search: 0 results')
                return result
            film_url = m.group(1)
            if film_url.startswith('/'):
                film_url = _BASE + film_url
            fflog(f'search: 1 results')

            # 3. Fetch movie page → all csst/fsst iframes
            film_html = sess.get(film_url, headers={'User-Agent': FF_UA}, timeout=10).text

            seen_ids: set[str] = set()
            for embed_match in re.finditer(r'(?:csst|fsst)\.online/embed/([0-9]+)/', film_html):
                csst_id = embed_match.group(1)
                if csst_id in seen_ids:
                    continue
                seen_ids.add(csst_id)

                # Detect language from ~500 chars of context before the iframe
                ctx = film_html[max(0, embed_match.start() - 500):embed_match.start()].lower()
                if 'dubbing' in ctx:
                    lang, info = 'pl', 'dubbing'
                elif 'lektor' in ctx:
                    lang, info = 'pl', 'lektor'
                elif 'napisy' in ctx or 'subtitle' in ctx:
                    lang, info = 'en', 'napisy'
                else:
                    lang, info = 'pl', ''

                fflog(f"ogladaj: csst_id={csst_id} info={info or '?'}")

                # 4. Fetch embed secvideo1.online → Playerjs file param
                embed_url = f"{_SECVIDEO}/embed/{csst_id}/"
                try:
                    embed_html = sess.get(
                        embed_url,
                        headers={'User-Agent': FF_UA, 'Referer': film_url},
                        timeout=10,
                    ).text
                except Exception:
                    fflog_exc()
                    continue

                # Format 1: [360p]url,[720p]url,...
                fm = re.search(r'file\s*:\s*"(\[\d+p\]https?://[^"]+)"', embed_html, re.S)
                if fm:
                    files = self._parse_playerjs_files(fm.group(1))
                    for quality in ('1080p', '720p', 'SD'):
                        if quality in files:
                            result.append({
                                'source': 'ogladaj',
                                'url': files[quality],
                                'quality': quality,
                                'language': lang,
                                'info': info,
                                'direct': False,
                                'debridonly': False,
                            })
                            break
                    continue

                # Format 2: file:"https://.../_NNNp.mp4/" (single URL)
                for single_m in re.finditer(r'file\s*:\s*"(https?://[^"]+)"', embed_html):
                    file_url = single_m.group(1).rstrip('/')
                    q_m = re.search(r'[_-](\d+p)\.', file_url)
                    raw_q = q_m.group(1) if q_m else 'SD'
                    quality = raw_q if raw_q in ('1080p', '720p') else 'SD'
                    result.append({
                        'source': 'ogladaj',
                        'url': file_url,
                        'quality': quality,
                        'language': lang,
                        'info': info,
                        'direct': False,
                        'debridonly': False,
                    })
                    fflog(f"ogladaj: single-url quality={quality} csst_id={csst_id}")
                    break  # first/best only

                if not result:
                    fflog(f"ogladaj: no Playerjs file for csst_id={csst_id}")

            if not result:
                fflog(f"ogladaj: no csst/fsst iframe on {film_url}")

        except Exception:
            fflog_exc()
        fflog(f'sources: {len(result)}')
        return result

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_guest_hash(sess: requests.Session) -> str:
        if _GUEST_HASH_CACHE:
            return _GUEST_HASH_CACHE[0]
        html = sess.get(_BASE + '/', headers={'User-Agent': FF_UA}, timeout=10).text
        m = re.search(r"dle_login_hash\s*=\s*'([a-f0-9]+)'", html)
        if m:
            _GUEST_HASH_CACHE.append(m.group(1))
            return m.group(1)
        return ''

    @staticmethod
    def _parse_playerjs_files(file_str: str) -> dict[str, str]:
        """Parse '[360p]url,[720p]url,...' → {'360p': url, '720p': url, ...}"""
        result: dict[str, str] = {}
        for m in re.finditer(r'\[(\d+p)\](https?://[^,\[]+)', file_str):
            result[m.group(1)] = m.group(2).rstrip('/')
        return result

    def resolve(self, url: str) -> Optional[str]:
        """Follow get_file → redirect → CDN MP4 URL."""
        try:
            sess = requests.Session()
            sess.headers.update({
                'User-Agent': FF_UA,
                'Referer': _SECVIDEO + '/',
            })
            resp = sess.get(url, allow_redirects=False, timeout=10)
            if resp.status_code in (301, 302):
                cdn_url = resp.headers.get('Location', '')
                if cdn_url:
                    fflog(f"ogladaj resolve: CDN {cdn_url[:60]}...")
                    return (
                        f"{cdn_url}"
                        f"|Referer={_SECVIDEO}/"
                        f"&User-Agent={quote_plus(FF_UA)}"
                    )
        except Exception:
            fflog_exc()
        return url
