# -*- coding: utf-8 -*-
"""
FanFilm ‑ źródło: kodi library
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
from typing import ClassVar, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING
import xbmcaddon
from urllib.parse import urlencode, parse_qsl, urlsplit
from lib.ff.kodidb import load_strm_file
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff import control, source_utils
if TYPE_CHECKING:
    from .. import SourceItem, SourceTitleAlias
    from ...ff.item import FFItem


def ensure_str(s, encoding='utf-8', errors='strict'):
    if type(s) is str:
        return s
    if isinstance(s, bytes):
        return s.decode(encoding, errors)
    return s


ensure_text = ensure_str
plugin_id: str = control.plugin_id

# Codec mappings
VIDEO_CODEC_MAP = {
    'avc1': 'h264',
    'h265': 'hevc',
}

AUDIO_CODEC_MAP = {
    'eac3': 'dd+',
    'dca': 'dts',
    'dtshd_ma': 'dts-hd ma',
}

AUDIO_CHANNELS_MAP = {
    1: 'mono',
    2: '2.0',
    6: '5.1',
    7: '6.1',
    8: '7.1',
}

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    has_sort_order: ClassVar[bool] = False
    has_color_identify2: ClassVar[bool] = True
    use_premium_color: ClassVar[bool] = True
    # TODO: use const?
    language: ClassVar[Sequence[str]] = ('en', 'de', 'fr', 'gr', 'ko', 'pl', 'pt', 'ru')
    priority: ClassVar[int] = 1
    domains: ClassVar[Sequence[str]] = ()

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        tmdb = self.ffitem.tmdb_id or ''
        try:
            return urlencode({'tmdb': tmdb, 'imdb': imdb, 'title': title, 'localtitle': localtitle, 'year': year})
        except Exception:
            return

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            tmdb = self.ffitem.show_item.tmdb_id
            return urlencode({'imdb': imdb, 'tmdb': tmdb, 'tvshowtitle': tvshowtitle, 'localtvshowtitle': localtvshowtitle, 'year': year})
        except Exception:
            return

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        if url is None:
            return
        try:
            url = dict(parse_qsl(url))
            url.update({'premiered': premiered, 'season': season, 'episode': episode})
            return urlencode(url)
        except Exception:
            return

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> list[SourceItem]:
        from lib.ff.libtools import LibTools
        if url is None:
            return []

        library = LibTools()
        data = dict(parse_qsl(url))
        content_type = 'episode' if 'tvshowtitle' in data else 'movie'
        lib = {}
        tmdb = str(data.get('tmdb', ''))
        imdb = data.get('imdb', '')
        fflog(f'query: tmdb={tmdb!r} imdb={imdb!r}')
        if content_type == 'movie':
            lib = library.check_in_library(tmdb=tmdb, imdb=imdb, year=data['year'], include_streamdetails=True)
        elif content_type == 'episode':
            lib = library.check_in_library(tmdb=tmdb, imdb=imdb, year=data['year'], season=data['season'], episode=data['episode'],
                                           include_streamdetails=True)
        else:
            return []
        fflog(f"check_in_library: {lib}")

        if not isinstance(lib, dict) or 'file' not in lib:
            return []

        url = lib['file']
        qual = -1  # Initialize qual to a default value
        try:
            fflog(f"streamdetails: {lib.get('streamdetails')}")
            if (streamdetails := lib.get('streamdetails')) and (video := streamdetails.get('video')):
                qual = int(video[0]['width'])
        except Exception:
            fflog_exc()  # Log the exception for debugging

        quality = source_utils.quality_from_resolution(width=qual)

        info = []
        name = ''
        icon = ''
        size = ''
        try:
            f = control.openFile(url)
            try:
                if '://' not in url and url.lower().endswith('.strm'):
                    strm = load_strm_file(url)
                    if strm.startswith('plugin://'):
                        u = urlsplit(strm)
                        if u.hostname == plugin_id:
                            return []
                        try:
                            addon = xbmcaddon.Addon(u.hostname)
                            icon, name = addon.getAddonInfo('icon'), addon.getAddonInfo('name')
                        except Exception:
                            fflog_exc()
                            name = u.hostname or ''
                        url = strm
                    else:
                        strm = load_strm_file(url)
                        name = strm
                        url = strm
                else:
                    size = source_utils.convert_size(f.size())
            finally:
                f.close()
        except Exception:
            fflog_exc()

        # Extract streamdetails once to avoid repetition
        streamdetails = lib.get('streamdetails', {})

        # Extract and map video codec
        try:
            video = streamdetails.get('video', [{}])[0]
            c = video.get('codec', '')
            c = VIDEO_CODEC_MAP.get(c, c)
            if c:
                info.append(c)
        except Exception:
            fflog_exc()

        # Extract and map audio codec
        try:
            audio = streamdetails.get('audio', [{}])[0]
            ac = audio.get('codec', '')
            ac = AUDIO_CODEC_MAP.get(ac, ac)
            if ac:
                info.append(ac)
        except Exception:
            fflog_exc()

        # Extract and map audio channels
        try:
            audio = streamdetails.get('audio', [{}])[0]
            ach = audio.get('channels')
            if ach is not None:
                ach = AUDIO_CHANNELS_MAP.get(ach, ach)
                info.append(str(ach))
        except Exception:
            fflog_exc()

        info = ' '.join(info)
        lang = source_utils.get_lang_by_type(url)[0]  # added language detection

        result = [{
            'source': name,
            'quality': quality,
            'language': lang,
            'url': url,
            'info': info,
            'size': size,
            'local': True,
            'direct': True,
            'debridonly': False,
            'icon': icon,
        }]
        fflog(f'sources: {len(result)}')
        return result

    def resolve(self, url: str) -> Optional[str]:
        return url
