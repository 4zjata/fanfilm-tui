# -*- coding: utf-8 -*-
"""
FanFilm - źródło: goojara.to
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  1. GET homepage → prime aGooz session cookie + extract JS anti-bot cookie (_3chk)
  2. Search via POST /xmre.php (z=data-rand, x=hash from JS) → find content page URL
  3. GET content page → extract JS anti-bot cookie, parse go.php links from #drl div
  4. sources() returns raw go.php URLs immediately (fast listing)
  5. resolve() follows go.php with proper session+referer → embed URL, then:
     - luluvdo: normalize /d/ID → /e/ID for URLResolver
     - vsembed.ru: resolve via cloudnestra chain → direct HLS m3u8
     - others: return embed URL for URLResolver

Working hosters: wootly, doodstream (myvidplay.com), streamplay, vidsrc (vsembed.ru)
vidsrc flow: go.php → vsembed.ru/embed/... → cloudnestra.com/rcp → /prorcp → HLS m3u8
  CDN domains tried in order: neonhorizonworkshops.com, wanderlynest.com, orchidpixelgardens.com, cloudnestra.com
Note: luluvdo URLs resolve correctly but files often dead on server side
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, ClassVar
from urllib.parse import urlencode, parse_qs

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

from lib.ff import requests
from lib.sources import single_call
from lib.ff import cleantitle
from lib.ff.item import FFItem
from lib.ff.source_utils import FF_UA
from lib.ff.resolve_utils import resolve_vsembed
from lib.ff.log_utils import fflog, fflog_exc


# Known hoster name fragments (label substring → canonical name)
_HOSTER_MAP: Dict[str, str] = {
    'wootly': 'wootly',
    'luluvdo': 'luluvdo',
    'dood': 'doodstream',
    'streamplay': 'streamplay',
    'vidsrc': 'vidsrc',
    'filemoon': 'filemoon',
    'mixdrop': 'mixdrop',
    'upstream': 'upstream',
    'streamtape': 'streamtape',
}

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.domains = ['goojara.to']

    @single_call
    def init(self):
        self.base_link = 'https://ww1.goojara.to'
        self._domain = 'ww1.goojara.to'
        self._home_html = ''
        self.session = requests.Session()
        self.session.headers.update(
            {
                'User-Agent': FF_UA,
                'Referer': self.base_link + '/',
            }
        )

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            return urlencode({'title': title, 'year': year, 'imdb': imdb})
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            return urlencode({'tvshowtitle': tvshowtitle, 'year': year, 'imdb': imdb})
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if url is None:
                return None
            data = {k: v[0] for k, v in parse_qs(url).items()}
            data.update({'season': season, 'episode': episode, 'ep_title': title, 'premiered': premiered})
            return urlencode(data)
        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            if url is None:
                return result

            self.init()
            data = {k: v[0] for k, v in parse_qs(url).items()}

            if 'season' in data:
                title = data.get('tvshowtitle', '')
                year = data.get('year', '')
                show_url = self._find_content_url(title, year, is_tvshow=True)
                if not show_url:
                    return result
                ep_url = self._find_episode(show_url, data.get('season', '1'), data.get('episode', '1'))
                if not ep_url:
                    return result
                result = self._get_sources_from_page(ep_url)
            else:
                movie_url = self._find_content_url(data.get('title', ''), data.get('year', ''), is_tvshow=False)
                if not movie_url:
                    return result
                result = self._get_sources_from_page(movie_url)

        except Exception:
            fflog_exc()

        return result

    def resolve(self, url: str) -> Optional[str]:
        # Follow goojara go.php → embed URL, then resolve further if needed
        if '/go.php?url=' in url:
            self.init()
            self._fetch_home()
            referer = self.base_link + '/'
            embed_url = self._follow_go_php(url, referer=referer)
            if not embed_url:
                fflog(f"goojara resolve: go.php failed for {url}")
                return None
            fflog(f"goojara resolve: go.php → {embed_url}")
            url = embed_url

        # Normalize luluvdo /d/ID or /ID → /e/ID for URLResolver
        m = re.match(r'(https?://(?:luluvdo\.com|lulustream\.com)/)(?:[a-z]/)?([a-zA-Z0-9]+)$', url)
        if m:
            return m.group(1) + 'e/' + m.group(2)
        # Resolve vsembed.ru → direct HLS m3u8 via cloudnestra
        if 'vsembed.ru/embed/' in url:
            return resolve_vsembed(url)
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _set_js_cookie(self, html: str) -> None:
        """Extract _3chk('name','value') calls from page HTML and set cookies."""
        for name, value in re.findall(r"_3chk\('([^']+)'\s*,\s*'([^']+)'\)", html):
            self.session.cookies.set(name, value, domain=self._domain)
            fflog(f"goojara: JS cookie set: {name}={value}")

    def _fetch_home(self) -> str:
        """Lazily fetch homepage, prime session cookies, cache HTML."""
        if not self._home_html:
            try:
                resp = self.session.get(self.base_link + '/', timeout=15)
                self._home_html = resp.text
                self._set_js_cookie(self._home_html)
            except Exception:
                fflog_exc()
        return self._home_html

    def _search(self, query: str) -> str:
        """POST to xmre.php using hash and data-rand extracted from cached homepage HTML."""
        try:
            html = self._fetch_home()

            x_match = re.search(r'"z="\+g\+"&x=([a-f0-9]+)&q=', html)
            if not x_match:
                x_match = re.search(r'&x=([a-f0-9]{10})&q=', html)
            if not x_match:
                fflog('goojara: cannot find search hash (x=)')
                return ''

            rand_match = re.search(r'id="res"[^>]*data-(?:rand|ins)="([^"]+)"', html)
            if not rand_match:
                fflog('goojara: cannot find data-rand/ins on homepage #res')
                return ''

            fflog(f'query: {query!r}')
            resp = self.session.post(
                self.base_link + '/xmre.php',
                data={'z': rand_match.group(1), 'x': x_match.group(1), 'q': query},
                headers={'X-Requested-With': 'XMLHttpRequest'},
                timeout=15,
            )
            return resp.text
        except Exception:
            fflog_exc()
            return ''

    def _find_content_url(self, title: str, year: str, is_tvshow: bool) -> Optional[str]:
        """Search and return the relative content URL (e.g. /mB4Q0R or /tXXXXXX)."""
        try:
            clean = cleantitle.get(title)
            html = self._search(title)
            if not html:
                return None

            # Results: <li><a href="/XXXXX"><div class="im|it"><strong>Title</strong> (year)</div></a></li>
            items = re.findall(
                r'<a href="(/[a-zA-Z0-9]+)"><div class="(im|it)"><[^>]*>([^<]*)<[^>]*>([^<]*)</div>',
                html,
            )
            if not items:
                items = [
                    (href, cls, '', text)
                    for href, cls, text in re.findall(
                        r'<a href="(/[a-zA-Z0-9]+)"><div class="(im|it)">(.*?)</div>', html
                    )
                ]
            fflog(f'search: {len(items)} results')

            want_cls = 'it' if is_tvshow else 'im'
            for href, cls, strong_text, rest_text in items:
                if cls != want_cls:
                    continue
                combined = (strong_text + rest_text).strip()
                yr_match = re.search(r'\((\d{4})\)', combined)
                result_year = yr_match.group(1) if yr_match else ''
                result_title = re.sub(r'\s*\(\d{4}\).*$', '', combined).strip()
                if cleantitle.get(result_title) != clean:
                    continue
                if year and result_year and year != result_year:
                    continue
                return href

            return None
        except Exception:
            fflog_exc()
            return None

    def _find_episode(self, show_url: str, season: str, episode: str) -> Optional[str]:
        """Fetch season page and return episode relative URL. Episodes are listed newest-first."""
        try:
            resp = self.session.get(f"{self.base_link}{show_url}?s={season}", timeout=15)
            all_eps = list(reversed(re.findall(r'<a href="(/e[a-zA-Z0-9]+)"', resp.text)))
            fflog(f"goojara: season {season} has {len(all_eps)} episodes, want ep {episode}")
            ep_idx = int(episode) - 1
            return all_eps[ep_idx] if 0 <= ep_idx < len(all_eps) else None
        except Exception:
            fflog_exc()
            return None

    def _parse_label(self, label: str) -> Tuple[str, str]:
        ll = label.lower()
        if '1080' in ll or 'fhd' in ll:
            quality = '1080p'
        elif '720' in ll or 'hd' in ll or 'dvd' in ll:
            quality = '720p'
        else:
            quality = 'SD'
        for frag, name in _HOSTER_MAP.items():
            if frag in ll:
                return name, quality
        return ll.replace(' ', ''), quality

    def _follow_go_php(self, go_url: str, referer: str) -> Optional[str]:
        """Follow go.php redirect with proper session+referer → embed URL."""
        try:
            resp = self.session.get(
                go_url, allow_redirects=True, timeout=15, verify=False,
                headers={'Referer': referer},
            )
            fflog(f"goojara go.php: status={resp.status_code} final={resp.url}")
            final = resp.url
            if final and final != go_url and 'go.php' not in final:
                return final
            m = re.search(
                r'(?:location\.href|window\.location(?:\.href)?)\s*=\s*["\']([^"\']+)',
                resp.text,
            )
            if m:
                return m.group(1).strip()
            fflog(f"goojara go.php: no redirect, body[:200]={resp.text[:200]}")
            return None
        except Exception:
            fflog_exc()
            return None

    def _get_sources_from_page(self, page_url: str) -> List[dict]:
        """Parse go.php links from content page HTML – all resolution deferred to resolve()."""
        try:
            full_url = self.base_link + page_url
            resp = self.session.get(full_url, timeout=15)
            html = resp.text

            links = re.findall(
                r'<a[^>]+href=["\']([^"\']*go\.php\?url=[^"\']+)["\'][^>]*>(.*?)</a>',
                html, re.IGNORECASE | re.DOTALL,
            )
            if not links:
                fflog(f'search: 0 results')
                return []

            fflog(f'search: {len(links)} results')
            self._set_js_cookie(html)

            sources = []
            seen = set()
            for go_url, raw_label in links:
                go_url = go_url.strip()
                if go_url in seen:
                    continue
                seen.add(go_url)

                label = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw_label)).strip()
                host_name, quality = self._parse_label(label)

                span_m = re.search(r'<span[^>]*>([^<]+)</span>', raw_label, re.IGNORECASE)
                info = span_m.group(1).strip() if span_m else label

                sources.append({
                    'source': host_name,
                    'url': go_url,
                    'quality': quality,
                    'language': 'en',
                    'info': info,
                    'filename': full_url,
                    'direct': False,
                    'debridonly': False,
                    'premium': False,
                })

            fflog(f'sources: {len(sources)}')
            return sources
        except Exception:
            fflog_exc()
            return []
