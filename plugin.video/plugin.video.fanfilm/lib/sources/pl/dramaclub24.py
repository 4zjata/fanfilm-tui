# -*- coding: utf-8 -*-
"""
FanFilm – źródło: dramaclub24.org
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
import re
from typing import Any, TYPE_CHECKING, Dict, Generator, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from lib.sources import SourceItem, ClassVar, SourceTitleAlias
from urllib.parse import urljoin


from lib.ff import requests

from lib.sources import single_call
from lib.ff import source_utils, cleantitle
from lib.ff.source_utils import DEFAULT_UA, ShowData, show_data_asdict, is_asian_content, ShowDataDict
from lib.ff.resolve_utils import build_isa_url
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.item import FFItem

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    # set in ff/sources.py
    has_sort_order: bool = True
    has_color_identify2: bool = True
    use_premium_color: bool = False
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    # Regex patterns (compiled once at class level)
    RE_LANG_INFO = re.compile(r'\b((?:Lektor|Napisy|Dubbing)\s+[A-Za-z]{2})\b', re.IGNORECASE)
    RE_PLAYLIST_URL = re.compile(r'PLAYLIST_URL\s*=\s*["\']([^"\']+\.m3u8)["\']')

    RE_AJAX_RESULT = re.compile(
        r'<a\s+href="(https://dramaclub24\.org/[^"]+)">'
        r'<span\s+class="searchheading">([^<]+)</span>'
    )

    @single_call
    def init(self):
        self.base_link = 'https://dramaclub24.org'
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': DEFAULT_UA})
        # establish session + grab dle_login_hash for AJAX search
        resp = self.session.get(self.base_link + '/', timeout=15, verify=False)
        hash_match = re.search(r"dle_login_hash\s*=\s*['\"]([^'\"]+)['\"]", resp.text)
        self._dle_hash = hash_match.group(1) if hash_match else ''

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> Optional[Tuple[str, str]]:
        self.init()
        if not is_asian_content(self.ffitem):
            fflog('not an Asian title, skip')
            return None
        alias_titles = source_utils.build_alias_list(title, localtitle, aliases, year)
        for search_title in source_utils.search_queries(title, localtitle):
            fflog(f'query: {search_title!r}')
            result = self._ajax_match(search_title, alias_titles, 'movie')
            if result:
                return result
        return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        self.init()
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, year))

    def episode(self, url: ShowDataDict, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[list[SourceItem]]:
        self.init()
        if not is_asian_content(self.ffitem.show_item):
            fflog(f'not an Asian title, skip ({title!r})')
            return None
        episode_num = int(episode)
        fflog(f'episode() called: season={season}, episode={episode_num}, title={title!r}')
        data = ShowData(**url)
        alias_titles = source_utils.build_alias_list(data.title, data.local_title, data.aliases, data.year)

        for search_title in source_utils.search_queries(data.title, data.local_title):
            fflog(f'query: {search_title!r}')
            result = self._ajax_match(search_title, alias_titles, 'tvshow')
            if result:
                show_url = result[0]
                show_page = self.session.get(show_url, verify=False).text
                info, language = self._detect_lang_info(show_page)
                sources = self._extract_episode_sources(
                    show_page, episode_num, info=info, language=language, filename=self._url_to_filename(show_url))
                if sources:
                    fflog(f'episode {episode_num}: {len(sources)} source(s)')
                    return sources
                fflog(f'show found but episode {episode_num} not in playlist')
                return None

        fflog('no match')
        return None

    def sources(self, url: Optional[list[SourceItem]], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        self.init()
        try:
            # episode() returns a pre-built source list – pass it through directly
            if isinstance(url, list):
                fflog(f'sources: {len(url)}')
                return url

            if isinstance(url, tuple):
                url = url[0]
            if not url:
                return []

            fflog(f'sources() fetching: {url}')
            page_content = self.session.get(url, verify=False).text
            info, language = self._detect_lang_info(page_content)
            fflog(f'detected: info={info!r}, language={language!r}')
            result = self._extract_sources_from_page(page_content, info=info, language=language, filename=self._url_to_filename(url))
            fflog(f'sources: {len(result)}')
            return result

        except Exception:
            fflog_exc()
            return []

    def resolve(self, url: str) -> Optional[str]:
        """Resolve direct dramaclub24 sources (HLS m3u8, dramaclub24.org mp4)."""
        if not url:
            return url
        if '.m3u8' in url:
            return build_isa_url(url, f"{self.base_link}/", self.base_link)
        if '.mp4' in url and url.startswith(self.base_link):
            return f"{url}|Referer={self.base_link}/&Origin={self.base_link}"
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _ajax_search(self, query: str) -> str:
        """POST to DLE AJAX search, return raw HTML fragment."""
        try:
            r = self.session.post(
                self.base_link + '/engine/ajax/controller.php?mod=search',
                data={'query': query, 'skin': 'VidStream', 'user_hash': self._dle_hash},
                headers={'Referer': self.base_link + '/', 'X-Requested-With': 'XMLHttpRequest'},
                timeout=10, verify=False,
            )
            return r.text
        except Exception:
            fflog_exc()
            return ''

    def _ajax_match(self, search_title: str, alias_titles: List[str], media_type: str) -> Optional[Tuple[str, str]]:
        """Search via AJAX and return (url, title) for the best match or None."""
        html = self._ajax_search(search_title)
        if not html:
            return None

        path_segment = 'filmy' if media_type == 'movie' else 'dramy'
        search_clean = cleantitle.get_simple(search_title)
        alias_cleans = {cleantitle.get_simple(a) for a in alias_titles if a and cleantitle.get_simple(a)}

        ajax_results = self.RE_AJAX_RESULT.findall(html)
        fflog(f'search: {len(ajax_results)} results')
        best = None
        for href, raw_title in ajax_results:
            if f'/{path_segment}/' not in href:
                continue
            title_clean = cleantitle.get_simple(raw_title)
            if title_clean == search_clean or title_clean in alias_cleans:
                fflog(f'ajax match: {raw_title!r} → {href}')
                return href, raw_title
            if not best and any(title_clean.startswith(c) or c.startswith(title_clean)
                                for c in [search_clean] + list(alias_cleans) if c):
                best = (href, raw_title)

        if best:
            fflog(f'ajax partial match: {best[1]!r} → {best[0]}')
        else:
            fflog(f'no ajax match for {search_title!r}')
        return best

    def _detect_lang_info(self, page_content: str) -> Tuple[str, str]:
        """Return (info, language) detected from page.
        info:     'Lektor' | 'Napisy' | 'Dubbing' | ''
        language: 'pl' | 'en' | ...
        """
        lang_match = self.RE_LANG_INFO.search(page_content)
        if lang_match:
            parts = lang_match.group(1).split()
            return parts[0].capitalize(), parts[1].lower()
        return '', 'pl'

    def _url_to_filename(self, url: str) -> str:
        """Extract 'dramy-japonskie/81-shogun' from show page URL."""
        path = url.replace(self.base_link, '').strip('/')
        path = path.rsplit('.', 1)[0]  # strip .html
        parts = path.split('/', 1)
        return parts[1] if len(parts) > 1 else path

    def _make_source(self, url: str, *, info: str, language: str, direct: bool, filename: str = '') -> SourceItem:
        """Build a source dict."""
        return {
            'source': self._extract_host_from_url(url),
            'url': url,
            'quality': '720p',
            'language': language,
            'info': info,
            'filename': filename,
            'direct': direct,
            'debridonly': False,
            'premium': False,
        }

    def _parse_m3u8_entries(self, content: str) -> Generator[Tuple[str, str], None, None]:
        """Yield (title, url) pairs from a custom M3U8 source list."""
        lines = [line.strip() for line in content.strip().splitlines()]
        for line, nxt in zip(lines, lines[1:]):
            if line.startswith('#EXTINF:'):
                title = line.split(',', 1)[1] if ',' in line else ''
                if nxt and not nxt.startswith('#'):
                    yield title, nxt

    def _fetch_source_playlist(self, url: str, *, info: str, language: str, filename: str = '') -> list[SourceItem]:
        """Fetch m3u8 and parse as a multi-source playlist (not HLS).
        Returns sources list or empty list if it's a real HLS stream."""
        try:
            content = self.session.get(url, verify=False).text
            if '#EXT-X-STREAM-INF' in content or '#EXT-X-TARGETDURATION' in content:
                return []  # real HLS – leave to caller
            sources = []
            for _, src_url in self._parse_m3u8_entries(content):
                src = self._make_source(src_url, info=info, language=language,
                                        direct=src_url.startswith(self.base_link), filename=filename)
                sources.append(src)
                fflog(f"playlist entry: {src['source']!r} -> {src_url}")
            return sources
        except Exception:
            fflog_exc()
            return []

    def _extract_episode_sources(self, show_page: str, episode_num: int, *, info: str, language: str, filename: str = '') -> list[SourceItem]:
        """Find all sources for a specific episode from the show's M3U8 playlist."""
        try:
            playlist_match = self.RE_PLAYLIST_URL.search(show_page)
            if not playlist_match:
                fflog('no PLAYLIST_URL in show page')
                return []
            playlist_url = playlist_match.group(1)
            fflog(f'playlist URL: {playlist_url}')

            content = self.session.get(playlist_url, verify=False).text
            ep_re = re.compile(rf'\bOdcinek\s+{episode_num}\b', re.IGNORECASE)
            sources = []
            for title, src_url in self._parse_m3u8_entries(content):
                if ep_re.search(title):
                    ep_filename = f"{filename} | {title.strip()}" if filename else title.strip()
                    sources.append(self._make_source(src_url, info=info, language=language,
                                   direct=src_url.startswith(self.base_link), filename=ep_filename))
                    fflog(f'  episode {episode_num}: {self._extract_host_from_url(src_url)} -> {src_url}')
            return sources
        except Exception:
            fflog_exc()
            return []

    def _extract_sources_from_page(self, page_content: str, *, info: str, language: str, filename: str = '') -> list[SourceItem]:
        """Extract all video sources from a movie/show detail page."""
        sources: list[SourceItem] = []
        seen_urls: Set[str] = set()

        def add(url: str, *, direct: bool = True) -> None:
            if url not in seen_urls:
                seen_urls.add(url)
                sources.append(self._make_source(url, info=info, language=language, direct=direct, filename=filename))

        # m3u8 – try as multi-source playlist first, fall back to direct HLS
        for match in re.finditer(r'https?://[^"\s<>]+\.m3u8', page_content):
            url = match.group(0)
            playlist = self._fetch_source_playlist(url, info=info, language=language, filename=filename)
            if playlist:
                for s in playlist:
                    add(s['url'], direct=s['direct'])
                fflog(f"source playlist: {len(playlist)} entries from {url}")
            else:
                add(url)
                fflog(f"HLS stream: {url}")

        # mp4 hosted on dramaclub24.org directly
        for match in re.finditer(r'https?://dramaclub24\.org/[^"\s<>]+\.mp4', page_content):
            add(match.group(0))

        # iframes from external domains
        for match in re.finditer(r'<iframe[^>]*(?:src|data-src)="([^"]+)"', page_content):
            url = match.group(1)
            if not url.startswith(self.base_link):
                add(url, direct=False)

        # <video><source src=...>
        for match in re.finditer(r'<video[^>]*>.*?<source[^>]*src="([^"]+)"', page_content, re.DOTALL):
            add(match.group(1))

        fflog(f"extract_sources: {len(sources)} sources, info={info!r}, lang={language!r}")
        return sources

    def _extract_host_from_url(self, url: str) -> str:
        """Extract 'domain.tld' from a URL."""
        if not url:
            return ''
        bare = url.replace('https://', '').replace('http://', '')
        parts = bare.split('/')[0].split('.')
        return f"{parts[-2]}.{parts[-1]}" if len(parts) >= 2 else bare.split('/')[0]
