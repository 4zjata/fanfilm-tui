# -*- coding: utf-8 -*-
"""
FanFilm - źródło: M3U
Copyright (C) 2026 :)

Scraper czytający filmy/seriale z pliku m3u
Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import os
import re
import time
import xbmcvfs
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
from typing_extensions import TypedDict
from urllib.request import urlopen, Request
from urllib.error import URLError
from lib.ff import cleantitle, source_utils
from lib.sources import single_call
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc


class _M3uShowDict(TypedDict):
    tvshowtitle: str
    localtitle: str
    year: str


class _M3uEntryMeta(TypedDict, total=False):
    """Metadata dict for one M3U entry passed in Tuple (title, url, meta)."""
    title: str
    url: str
    tvg_name: str
    group_title: str

from lib.ff.settings import settings

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# Maximum m3u file size (25MB)
MAX_FILE_SIZE_MB = 25

# m3u source type (settings: m3u.source_type)
SOURCE_LOCAL = 0
SOURCE_REMOTE = 1
SOURCE_IPTVSIMPLE = 2


_RX_M3U_ATTR = re.compile(r'(?P<key>\S+)="(?P<value>[^"]*)"')

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl', 'en']

    has_sort_order: bool = True
    has_color_identify2: bool = True
    use_premium_color: bool = True

    def __init__(self):
        self.domains = ['m3u']
        self.available = True

    @single_call
    def init(self):
        """Lazy initialization - called on first use (movie/episode).
        Avoids heavy work (HEAD requests, XML parsing) when scraper is disabled."""

        source_type = settings.getInt('m3u.source_type') or SOURCE_LOCAL
        if source_type == SOURCE_IPTVSIMPLE:
            self.m3u_path = self._get_m3u_from_iptvsimple()
            if not self.m3u_path:
                fflog('could not read m3u path from pvr.iptvsimple, falling back to manual path')
                self.m3u_path = settings.getString('m3u.file_path')
        elif source_type == SOURCE_REMOTE:
            self.m3u_path = settings.getString('m3u.remote_url')
        else:
            self.m3u_path = settings.getString('m3u.file_path')

        if not self.m3u_path:
            self.available = False
            fflog('no file path in settings')
            return False

        self.is_remote = self.m3u_path.startswith(('http://', 'https://'))

        if self.is_remote:
            # Check if cache is fresh — if so, skip HEAD request
            if self._is_cache_fresh():
                fflog(f'initialized with cached file (url: {self.m3u_path})')
                return True

            # Cache stale — check remote file size via HEAD request
            try:
                req = Request(self.m3u_path, method='HEAD')
                with urlopen(req, timeout=10) as resp:
                    content_length = resp.headers.get('Content-Length')
                    if content_length:
                        file_size_mb = int(content_length) / (1024 * 1024)
                        if file_size_mb > MAX_FILE_SIZE_MB:
                            self.available = False
                            fflog(f'remote file too large: {file_size_mb:.1f}MB (max {MAX_FILE_SIZE_MB}MB)')
                            return False
                        fflog(f'remote file size: {file_size_mb:.1f}MB')
            except Exception:
                fflog_exc()
                self.available = False
                return False
        else:
            # Local file — check if exists and readable
            local_path = xbmcvfs.translatePath(self.m3u_path)
            if not xbmcvfs.exists(local_path):
                self.available = False
                fflog(f'local file does not exist: {local_path}')
                return False
            file_stat = xbmcvfs.Stat(local_path)
            file_size_mb = file_stat.st_size() / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                self.available = False
                fflog(f'local file too large: {file_size_mb:.1f}MB (max {MAX_FILE_SIZE_MB}MB)')
                return False
            fflog(f'initialized: local file {local_path} ({file_size_mb:.1f}MB)')

        return True

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> Optional[List[Tuple[str, str, _M3uEntryMeta]]]:
        if not self.init():
            return None

        try:
            fflog(f'searching for movie "{title}" ({year})')

            # Parse m3u file
            entries = self._parse_m3u()
            if not entries:
                fflog('no entries in file')
                return None

            fflog(f'parsed {len(entries)} entries from m3u')

            # Prepare search titles
            search_titles = []
            if localtitle:
                search_titles.append(cleantitle.get_simple(localtitle))
            if title and title != localtitle:
                search_titles.append(cleantitle.get_simple(title))
            if not search_titles:
                search_titles = [cleantitle.get_simple(title)]

            # Add aliases (max 4, no non-latin scripts)
            for alias in self._prepare_aliases(aliases, year):
                alias_clean = cleantitle.get_simple(alias)
                if alias_clean not in search_titles:
                    search_titles.append(alias_clean)

            fflog(f'search titles: {search_titles}')

            # Search for matching entries
            results = []
            for entry in entries:
                # Skip TV channels
                if self._is_tv_channel(entry):
                    continue

                # Get tvg-name (preferred) or title
                entry_title = entry.get('tvg-name', entry.get('title', ''))
                if not entry_title:
                    continue

                # Parse title and year
                entry_title_clean_text, entry_year = self._parse_title_year(entry_title)
                entry_title_clean = cleantitle.get_simple(entry_title_clean_text)
                if not entry_title_clean:
                    continue

                # Check if title matches (substring - m3u titles may have suffixes like "(PL)")
                title_match = any(
                    search_title in entry_title_clean or entry_title_clean in search_title
                    for search_title in search_titles
                )

                if not title_match:
                    continue

                # Check year (tolerance ±1)
                if year and entry_year:
                    if abs(int(year) - entry_year) > 1:
                        continue

                # Found a match
                url = entry.get('url', '')
                if url:
                    fflog(f'match: {entry_title}')
                    results.append((entry_title, url, entry))

            if results:
                fflog(f'search: {len(results)} results')
                return results
            else:
                fflog('search: 0 results')
                return None

        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtitle: str, aliases: list[SourceTitleAlias], year: str, **kwargs) -> _M3uShowDict:
        # Return data for episode()
        return {
            'tvshowtitle': tvshowtitle,
            'localtitle': localtitle,
            'year': year
        }

    def episode(self, url: _M3uShowDict | None, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str, **kwargs) -> Optional[List[Tuple[str, str, _M3uEntryMeta]]]:
        if not self.init():
            return None

        try:
            # Get show data from url (dict from tvshow)
            if not url or not isinstance(url, dict):
                return None

            tvshowtitle = url.get('tvshowtitle')
            localtvshowtitle = url.get('localtitle')

            fflog(f'searching for episode "{tvshowtitle}" S{int(season):02d}E{int(episode):02d}')

            # Parse m3u file
            entries = self._parse_m3u()
            if not entries:
                return None

            # Prepare search titles
            search_titles = []
            if localtvshowtitle:
                search_titles.append(cleantitle.get_simple(localtvshowtitle))
            if tvshowtitle and tvshowtitle != localtvshowtitle:
                search_titles.append(cleantitle.get_simple(tvshowtitle))
            if not search_titles:
                search_titles = [cleantitle.get_simple(tvshowtitle)]

            # Search for matching episodes
            results = []
            for entry in entries:
                # Skip TV channels
                if self._is_tv_channel(entry):
                    continue

                # Get tvg-name
                entry_name = entry.get('tvg-name', entry.get('title', ''))
                if not entry_name:
                    continue

                # Try to parse episode info
                show_title, ep_season, ep_episode = self._parse_episode_info(entry_name)
                if not show_title:
                    continue

                # Check if season and episode match
                if ep_season != int(season) or ep_episode != int(episode):
                    continue

                # Check if show title matches
                show_title_clean = cleantitle.get_simple(show_title)
                title_match = any(search_title == show_title_clean for search_title in search_titles)

                if not title_match:
                    continue

                # Found a match
                url_str = entry.get('url', '')
                if url_str:
                    fflog(f'found episode: {entry_name}')
                    results.append((entry_name, url_str, entry))

            if results:
                fflog(f'found {len(results)} episode(s)')
                return results
            else:
                fflog('no episodes found')
                return None

        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[List[Tuple[str, str, _M3uEntryMeta]]], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        if not self.available:
            return sources

        try:
            if not url:
                return sources

            items_to_process = []
            if isinstance(url, list):
                items_to_process = url
            elif isinstance(url, tuple):
                items_to_process = [url]
            else:
                return sources

            for item in items_to_process:
                try:
                    # item is a tuple (title, url, entry) from movie()/episode()
                    if not isinstance(item, tuple) or len(item) != 3:
                        continue

                    filename, stream_url, entry = item

                    if not stream_url:
                        continue

                    # Detect quality and language from m3u tags
                    quality, language, info = self._detect_quality_language(entry, filename)

                    # Detect file type from URL
                    codec = source_utils.get_codec(stream_url.lower())
                    source_name = codec.replace(' | ', '').strip() if codec and codec != '0' else 'M3U'

                    sources.append({
                        'source': source_name,
                        'quality': quality,
                        'language': language,
                        'url': stream_url,
                        'info': info,
                        'filename': filename,
                        'direct': True,
                        'debridonly': False,
                        'premium': True,
                    })

                except Exception:
                    fflog_exc()
                    continue

        except Exception:
            fflog_exc()

        fflog(f'sources: {len(sources)}')
        return sources

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_m3u_from_iptvsimple():
        """
        Read m3u file path from pvr.iptvsimple addon settings.
        Returns path or None if not found.
        """
        try:
            # Get instance number from settings (default 1)
            instance_num = settings.getInt('m3u.iptvsimple_instance') or 1

            # Path to pvr.iptvsimple instance settings
            userdata_path = xbmcvfs.translatePath('special://userdata')
            settings_file = os.path.join(
                userdata_path,
                'addon_data',
                'pvr.iptvsimple',
                f'instance-settings-{instance_num}.xml'
            )

            if not os.path.exists(settings_file):
                fflog(f'pvr.iptvsimple settings not found: {settings_file}')
                return None

            # Parse XML and find m3uPath
            tree = ET.parse(settings_file)
            root = tree.getroot()

            for setting in root.findall('setting'):
                if setting.get('id') == 'm3uPath':
                    path = setting.text
                    if path:
                        fflog(f'found m3u path in pvr.iptvsimple: {path}')
                        return path

            fflog('no m3uPath in pvr.iptvsimple settings')
            return None

        except Exception as e:
            fflog(f'error reading pvr.iptvsimple settings: {e}')
            return None

    @staticmethod
    def _get_cache_path():
        """Get path for cached m3u file."""
        addon_data = xbmcvfs.translatePath('special://userdata/addon_data/plugin.video.fanfilm/')
        return os.path.join(addon_data, 'm3u_cache.m3u')

    def _is_cache_fresh(self):
        """Check if cached file exists and is within cache duration."""
        cache_path = self._get_cache_path()
        if not os.path.exists(cache_path):
            return False
        cache_hours = settings.getInt('m3u.cache_hours') or 4
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        return age_hours < cache_hours

    def _download_and_cache(self):
        """Download remote m3u and save to cache. Returns raw bytes or None."""
        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        try:
            fflog(f'downloading remote {self.m3u_path}')
            req = Request(self.m3u_path)
            req.add_header('User-Agent', source_utils.DEFAULT_UA)
            with urlopen(req, timeout=30) as resp:
                raw = resp.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    fflog(f'remote file exceeds {MAX_FILE_SIZE_MB}MB limit, truncated')
                    raw = raw[:max_bytes]

            cache_path = self._get_cache_path()
            with open(cache_path, 'wb') as cf:
                cf.write(raw)
            fflog(f'cached to {cache_path} ({len(raw) / (1024*1024):.1f}MB)')
            return raw
        except Exception as e:
            fflog(f'error downloading remote: {e}')
            return None

    def _parse_m3u(self):
        """
        Parse m3u from local file, cache, or remote URL.
        For remote: uses cache if fresh, otherwise downloads and caches.
        """
        entries = []

        try:
            if self.is_remote:
                if self._is_cache_fresh():
                    cache_path = self._get_cache_path()
                    fflog(f'reading from cache: {cache_path}')
                    with open(cache_path, 'r', encoding='utf-8', errors='ignore') as f:
                        text = f.read()
                else:
                    raw = self._download_and_cache()
                    if not raw:
                        return []
                    text = raw.decode('utf-8', errors='ignore')
            else:
                with open(self.m3u_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()

            item = {}
            for line in text.splitlines():
                line = line.strip()
                if line.startswith('#EXTINF:'):
                    # Parse metadata: #EXTINF:-1 key="value" key="value",Title
                    line, _, title = line[8:].rpartition(',')
                    item['title'] = title.strip()
                    # Parse key-value pairs
                    for mch in _RX_M3U_ATTR.finditer(line):
                        item[mch.group('key')] = mch.group('value')
                elif line and not line.startswith('#'):
                    # URL line
                    item['url'] = line
                    entries.append(item)
                    item = {}
        except Exception as e:
            fflog(f'error parsing: {e}')
            return []

        return entries

    @staticmethod
    def _is_tv_channel(entry):
        """Check if entry is a TV channel (not a movie/show)"""
        group_title = entry.get('group-title', '').lower()
        # Skip TV channels
        tv_keywords = ['telewizja', ' tv', 'tv ', 'sport', 'news', 'kids', 'music', 'radio', 'live']
        return any(kw in group_title for kw in tv_keywords)

    @staticmethod
    def _parse_title_year(title_str):
        """
        Parse title and year from string like "Movie Title (2020)"
        Returns (title, year) or (title, None)
        """
        match = re.match(r'^(.+?)\s*\((\d{4})\)\s*$', title_str)
        if match:
            return match.group(1).strip(), int(match.group(2))
        return title_str.strip(), None

    @staticmethod
    def _parse_episode_info(title_str):
        """
        Parse TV show episode info from string like "Show Title S05 E03"
        or "Show Title (2020) S05 E03"
        Returns (show_title, season, episode) or (None, None, None)
        """
        # Pattern: "Show Title (year) S05 E03" or "Show Title S05E03"
        match = re.match(r'^(.+?)(?:\s*\(\d{4}\))?\s+S(\d+)\s*E(\d+)\s*$', title_str, re.I)
        if match:
            show_title = match.group(1).strip()
            season = int(match.group(2))
            episode_num = int(match.group(3))
            return show_title, season, episode_num
        return None, None, None

    @staticmethod
    def _prepare_aliases(aliases, year):
        """Prepare cleaned aliases list (max 4), filtered from non-latin scripts."""
        if not aliases:
            return []
        cleaned = []
        for a in aliases:
            t = a.get('title') or a.get('originalname') or ''
            t = t.replace(str(year), '').replace('()', '').strip()
            if t and not source_utils.detect_script(t):
                cleaned.append(t)
        return source_utils.deduplicate_list_ci(cleaned)[:4]

    def _detect_quality_language(self, entry, filename):
        """
        Detect quality and language from m3u entry tags and filename.
        Returns (quality, language, info)
        """
        # Combine all text sources for detection
        group_title = entry.get('group-title', '')
        tvg_name = entry.get('tvg-name', entry.get('title', ''))
        combined_text = f"{group_title} {tvg_name} {filename}"
        combined_lower = combined_text.lower()

        # Detect quality - check manually first for common patterns
        quality = '1080p'  # default
        if '4k' in combined_lower or 'uhd' in combined_lower or '2160p' in combined_lower or '2160' in combined_lower:
            quality = '4K'
        elif '1080p' in combined_lower or 'fhd' in combined_lower or '1080' in combined_lower:
            quality = '1080p'
        elif '720p' in combined_lower or 'hd ' in combined_lower or ' hd' in combined_lower or '720' in combined_lower:
            quality = '720p'
        else:
            # Fallback to source_utils
            detected = source_utils.check_sd_url(combined_text)
            if detected and detected != 'SD':
                quality = detected

        # Detect language and audio info (lektor/dubbing/napisy)
        language, info_lang = source_utils.get_lang_by_type(combined_text)

        # Detect audio channels (5.1, 7.1, etc.)
        audio_info = source_utils.get_audio(combined_text)
        audio_info = audio_info.replace(' | ', '').strip() if audio_info and audio_info != '0' else ''

        # Detect file type/tech info
        info_tech = source_utils.getFileType(combined_text)

        # Build info string - include audio info (lektor/dubbing/napisy) but remove redundant language code
        info_parts = []
        if info_lang:
            # Remove language code prefix if it's redundant (e.g., "PL Lektor" -> "Lektor" when language='pl')
            if language:
                info_lang_clean = info_lang.replace(language.upper(), '').strip()
                if info_lang_clean:  # Only add if there's something left after removing language code
                    info_parts.append(info_lang_clean)
            else:
                info_parts.append(info_lang)
        if audio_info:
            info_parts.append(audio_info)
        if info_tech:
            info_parts.append(info_tech)
        info = ' '.join(info_parts) if info_parts else ''

        # Default to PL if no language detected
        if not language:
            language = 'pl'

        return quality, language, info
