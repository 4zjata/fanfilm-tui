# -*- coding: utf-8 -*-
"""
FanFilm - źródło: download
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar
from urllib.parse import parse_qs, urlencode

import xbmcvfs

from lib.ff import cleantitle, control, source_utils
from lib.ff.item import FFItem
from lib.ff.settings import settings
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias


def _fix_surrogate(text):
    """Fix UTF-8 surrogates (Kodi may use a locale other than UTF-8)."""
    try:
        return os.fsencode(text).decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _get_originalname(aliases):
    for alias in aliases:
        if 'originalname' in alias:
            return alias['originalname']
    return ''


def _parse_url(url):
    """Parse a URL-encoded string into a flat dict."""
    parsed = parse_qs(url)
    return {key: (vals[0] if vals else '') for key, vals in parsed.items()}


def _normalize_name(name):
    """Normalize name for comparison (lowercase, no diacritics, no punctuation)."""
    result = cleantitle.normalize(cleantitle.getsearch(name))
    result = result.replace('.', ' ').replace('_', ' ')
    result = re.sub(' {2,}', ' ', result).strip()
    return result

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl', 'en']

    use_premium_color: bool = True

    def __init__(self):
        self.ext = ['mp4', 'mkv', 'flv', 'avi', 'mpg', 'ts', 'm4v', 'wmv', 'mov']
        self.movie_path = settings.getString('movie.download.path')
        self.tv_path = settings.getString('tv.download.path')
        self.other_folder = settings.getBool('download.other_folder')
        self.other_path = settings.getString('download.other_folder_path')

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            originalname = _get_originalname(aliases)
            dest = control.transPath(self.movie_path)

            return urlencode({
                'imdb': imdb, 'title': title, 'localtitle': localtitle,
                'originalname': originalname, 'year': year, 'path': dest,
                'aliases': aliases,
            })
        except Exception:
            fflog_exc()

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            tmdb = self.ffitem.show_item.tmdb_id
            originalname = _get_originalname(aliases)
            return urlencode({
                'imdb': imdb, 'tmdb': tmdb, 'tvshowtitle': tvshowtitle,
                'localtvshowtitle': localtvshowtitle,
                'originalname': originalname, 'year': year,
            })
        except Exception:
            fflog_exc()

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if url is None:
                return

            data = _parse_url(url)
            dest = control.transPath(self.tv_path)
            data.update({
                'title': data['tvshowtitle'],
                'localtitle': data['localtvshowtitle'],
                'premiered': premiered,
                'season': season,
                'episode': episode,
                'path': dest,
            })
            return urlencode(data)
        except Exception:
            fflog_exc()

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        sources = []
        # fflog(f'{url=}')
        try:
            if url is None:
                return sources

            data = _parse_url(url)
            # fflog(f'{data=}')

            paths = []
            base_path = data.get('path')
            if base_path:
                paths.append(base_path)

            if self.other_folder and self.other_path:
                dest = control.transPath(self.other_path)
                if dest not in paths:
                    paths.append(dest)

            if not paths:
                return sources

            title_en = data['title']
            title_local = data['localtitle']
            title_orig = data.get('originalname', '')
            filenames = [title_en, title_local, title_orig]
            filenames = list(filter(None, filenames))  # remove empty entries
            filenames = list(dict.fromkeys(filenames))  # remove duplicates

            year = data['year'] or ''

            match_year = re.search(r'\b\d{4}\b', title_en)
            year_in_title = match_year[0] if match_year else ''

            match_ep = re.search(r'\b\d{1,2}\b', title_en)
            ep_in_title = match_ep[0] if match_ep else ''

            episodes_notations = []
            if 'episode' in data:
                season_num = int(data['season'])
                episode_num = int(data['episode'])
                episodes_notations.append('S%02dE%02d' % (season_num, episode_num))
                if season_num == 1:
                    episodes_notations.append('E%02d' % episode_num)
                    episodes_notations.append('ep%02d' % episode_num)
                    episodes_notations.append('%02d' % episode_num)
                    if episode_num < 10:
                        episodes_notations.append('%d' % episode_num)

            episodes_pat = '|'.join(episodes_notations)
            episodes_pat = rf"\b({episodes_pat})\b" if episodes_pat else ''

            # Add variants without Windows-illegal characters
            for name in filenames[:]:
                cleaned = re.sub(r'[\\/:*?"<>|]', '', name)
                filenames.append(cleaned)

            # Normalize all names for comparison
            filenames = [_normalize_name(name) for name in filenames]
            filenames = list(dict.fromkeys(filenames))  # remove duplicates

            # fflog(f'{filenames=}')

            found_urls = []

            for path in paths:
                if not os.path.exists(path):
                    fflog(f'folder does not exist: {path=}')
                    continue

                for root, _dirs, files in os.walk(path):
                    root = _fix_surrogate(root)
                    for filename in files:
                        filename = _fix_surrogate(filename)
                        normalized = _normalize_name(filename)
                        # fflog(f"comparing: {filenames=}, {normalized=}")

                        matched = [fn for fn in filenames if fn.lower() in normalized.lower()]

                        if matched:
                            best_match = max(matched, key=len)
                            remainder = normalized.lower().partition(best_match.lower())[2].strip()
                            # strip file extension from remainder
                            remainder = re.sub(r'\b(?:' + '|'.join(self.ext) + r')$', '', remainder).strip()
                            if not episodes_notations:
                                # for movies: reject if a sequel number follows the title (e.g. "2", "3") but not a year (e.g. "2012")
                                if re.match(r'\d{1,3}(?!\d)', remainder):
                                    matched = []
                            if matched and remainder:
                                # reject if additional title words follow (metadata starts with year, brackets, quality, codec etc. — not a plain word)
                                if re.match(r'[a-z]', remainder) \
                                        and not re.match(r'(?:s\d{1,2}e\d{1,2}|ep?\d|pl|en|dubbed|lektor|napisy|sub|dual|hdtv|hdrip|bdrip|brrip|dvdrip|webrip|web dl|bluray|remux|repack|proper)\b', remainder, flags=re.I):
                                    matched = []

                        if not matched:
                            continue

                        # year verification (if the file contains any year, it must match)
                        if year:
                            year_found = re.search(r'\b\d{4}\b', filename.replace(year_in_title, '', 1))
                            if year_found and not re.search(rf'\b{year}\b', filename):
                                # fflog(f'searched year {year=} does not match year found in filename ({year_found[0]})')
                                continue

                        # episode number verification
                        if episodes_notations:
                            if not re.search(episodes_pat, filename.replace(ep_in_title, '', 1), flags=re.I):
                                # fflog(f'episode number not found in filename: {filename=}')
                                continue

                        # check file extension
                        if not any(filename.endswith('.%s' % ext) for ext in self.ext):
                            file_ext = filename.rpartition('.')[-1]
                            fflog(f'unsupported file extension ({file_ext=})')
                            continue

                        full_path = os.path.join(root, filename)
                        found_urls.append(full_path)

            for file_url in found_urls:
                file_name = os.path.basename(file_url)

                size = None
                try:
                    stat = xbmcvfs.Stat(file_url)
                    size = stat.st_size()
                except Exception:
                    try:
                        with xbmcvfs.File(file_url) as vfs_file:
                            size = vfs_file.size()
                    except Exception:
                        pass
                size = source_utils.convert_size(size)

                quality, lang, lang_info = source_utils.parse_source_quality_lang(file_name)
                info = lang_info if lang_info else ''

                sources.append({
                    'source': '',
                    'quality': quality,
                    'language': 'pl',
                    'url': file_url,
                    'info': info,
                    'size': size,
                    'direct': True,
                    'debridonly': False,
                    'filename': file_name,
                    'local': True,
                })

            fflog(f'sources: {len(sources)}')
            return sources
        except Exception:
            fflog_exc()
            return sources

    def resolve(self, url: str) -> Optional[str]:
        return url
