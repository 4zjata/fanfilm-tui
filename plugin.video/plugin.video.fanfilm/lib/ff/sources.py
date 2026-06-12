# -*- coding: utf-8 -*-

"""
    FanFilm Add-on

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from __future__ import annotations
import datetime
import json
import random
import re
import sys
from time import monotonic
from contextlib import suppress, contextmanager
from threading import Lock, Event
from ast import literal_eval
from enum import Enum, IntEnum
from html import unescape
from pathlib import Path
from sqlite3 import dbapi2 as database, Cursor as SqlCursor, OperationalError
from typing import Union, Any, Dict, List, Set, Sequence, Iterable, Iterator, Tuple, ClassVar, TYPE_CHECKING
from typing_extensions import TypedDict, NotRequired, Unpack, Protocol, Literal, TypeAlias
from urllib.parse import parse_qsl, quote_plus, unquote
from attrs import evolve
import xbmc
import PTN
from ..ff import (
    apis,
    cache,
    cleantitle,
    client,
    control,
    debrid,
    source_utils,
)
from ..windows.sources import SourceDialog, RescanSources, EditDialog
from .info import ffinfo
from .item import FFItem
from .types import EllipsisType
from ..defs import MediaRef, MediaType
from . import player
from .db import state
from .db.search import sources_edit_db
from .threads import ThreadCanceled
from ..sources import SourceItem, SourceMeta, Source, scan_source_modules, clear_source_modules
from .log_utils import fflog, fflog_exc, log
from .workers import Thread
from .settings import settings
from .kotools import xsleep
from .. import FAKE
from ..kolang import L
from cdefs import SourceSearchProgressStyle
from const import const
if TYPE_CHECKING:
    from typing_extensions import LiteralString
    from .item import FFEpisodeGroup
    from ..sources import SourceModule, SourceResolveKwargs, ProviderProtocol

try:
    import resolveurl
except Exception as e:
    print(e)
    resolveurl = None


class DeadLink(ValueError):
    """Exception raised when a source link is dead or invalid."""


Item = SourceMeta


#: Type of resolver object.
class ResolverClass(Protocol):
    name: str
    domains: Sequence[str]
    pattern: str | None = None


# 1 GiB
GB = 1024**3


SourceSearchQueryKey: TypeAlias = Literal['localtitle', 'title', 'originalname', 'year', 'premiered', 'imdb', 'tmdb', 'tvshowtitle', 'season', 'episode', 'episode_group', 'ffitem']


class SourceSearchQuery(TypedDict):
    localtitle: str      # Local title (in current api language)
    title: str           # English title
    originalname: str    # Original title
    year: int
    premiered: str
    imdb: str
    tmdb: str
    tvshowtitle: str     # DEPRECATED, use `title`
    season: int | None
    episode: int | None
    episode_group: str | None
    episode_offset: dict[str | None, dict[int, int]]  # mapping [group name][group season] = episode number offset
    ffitem: FFItem


#: Link to default color.
DEFAULT_COLOR_LINK: str = "00000000"


class SortElem(Enum):  # NOTE: MUST be in the "hosts.sort.elemN" setting option order
    SERVICE = 0
    LANGUAGE = 1
    QUALITY = 2
    SIZE = 3
    NONE = 4


class Quality(IntEnum):  # NOTE: values in setting "hosts.quality.max" / "hosts.quality.min" values
    """Quality (video resolution)."""
    SD = 0
    HD = 1
    FHD = 2
    UHD = 3

    H_480 = SD
    H_720 = HD
    H_1080 = FHD
    H_2160 = UHD


class ProgressDialogProtocol(Protocol):
    """Protocol for dialogs that can show search progress."""
    def update(self, percent: int, message: str = '', *, providers: Sequence[str] | None = None) -> None: ...
    def iscanceled(self) -> bool: ...


class SourceDialogProtocol(Protocol):
    """Protocol for dialogs that can search sources."""
    # def __init__(self,
    query: SourceSearchQuery
    def __new__(cls,
                *args,
                sources: sources,
                item: FFItem,
                items: Sequence[Source] = (),
                query: SourceSearchQuery,
                default_query: SourceSearchQuery | None = None,
                edit_search: bool = False,
                ) -> SourceDialogProtocol: ...
    def start_search(self) -> None: ...
    def finish_search(self, sources: Sequence[Source]) -> None: ...
    def do_modal(self) -> Source | None: ...
    def destroy(self) -> None: ...
    def update(self, percent: int, message: str = '', *, providers: Sequence[str] | None = None) -> None: ...
    def iscanceled(self) -> bool: ...


class GetSourcesKwargs(TypedDict):
    title: str
    localtitle: str
    year: int
    imdb: str
    tmdb: str
    season: int | None
    episode: int | None
    tvshowtitle: str
    premiered: str
    originalname: NotRequired[str]
    episode_group: NotRequired[str | None]
    quality: NotRequired[str]
    timeout: NotRequired[int | None]
    ffitem: FFItem
    progress_dialog: NotRequired[ProgressDialogProtocol | None]


class ProvidersSetting(dict):
    """
    Simple setting cache, for mass providers query.

    >>> so = ProvidersSetting('sort.order', default=0, source_mods=mods)
    >>> so['cda']  # value of 'cda.sort.order'
    """

    def __init__(self, name: str, *, default: Any, source_mods: list[SourceModule]) -> None:
        var_name: str = f'has_{name.replace(".", "_")}'
        super().__init__()
        #: Settings name (suffix).
        self.name: str = name
        #: Default valur if settings is not found.
        self.default: Any = default
        #: Providers with settings enabled.
        self._enabled: Set[str] = {srcmod.name for srcmod in source_mods if getattr(srcmod.provider, var_name, False)}

    def _get_settings(self, name: str) -> Any:
        return settings.getString(name)

    def __missing__(self, key: str) -> Any:
        key = key.lower()
        if key in self._enabled:
            value = self._get_settings(f'{key}.{self.name}')
        else:
            value = self.default  # default no-order
        self[key] = value
        return value


class SortOrderSettings(ProvidersSetting):
    """Simple setting cache, for mass query (sorting in filters)."""

    def __init__(self, *, source_mods: list[SourceModule]) -> None:
        super().__init__(name='sort.order', default=None, source_mods=source_mods)

    def _get_settings(self, name: str) -> int:
        return settings.getInt(name)


class ColorSettings(ProvidersSetting):
    """Simple setting cache, for mass query (sorting in filters)."""

    def __init__(self, *, source_mods: list[SourceModule]) -> None:
        super().__init__(name='color.identify2', default=None, source_mods=source_mods)


class LibraryColorSettings(ProvidersSetting):
    """Simple setting cache, for mass query (sorting in filters)."""

    def __init__(self, *, source_mods: list[SourceModule]) -> None:
        super().__init__(name='library.color.identify2', default=None, source_mods=source_mods)


class sources:
    """Main class for searching and playing sources."""

    QUALITIES = {
        Quality.UHD: {'4K', '8K'},
        Quality.FHD: {'1080', '1080p', '1080i', '1440p', '2K', 'FHD'},
        Quality.HD: {'720', '720p', 'HD'},
        Quality.SD: {'480', '480p', 'SD'},
    }
    QUALITY_NAMES = {0: 'SD', 1: '720', 2: '1080', 3: '4K'}
    TOTAL_FORMAT = "[COLOR {color}][B]{count}[/B][/COLOR]"

    # Priorytet typu języka
    language_type_priority = const.sources_dialog.language_type_priority

    # Debug. Run single source provider without threads.
    DEBUG_SINGLE_PROVIDER: ClassVar[str | None] = None

    # Dialog window factory (class).
    SOURCE_DIALOG: ClassVar[type[SourceDialogProtocol]] = SourceDialog

    def __init__(self):
        self.lock = Lock()
        self.sourceFile = control.providercacheFile
        self.selectedSource = None
        self.itemProperty = None
        self.metaProperty = None
        self.source_mods: list[SourceModule] = None
        self.host_list: list[str] = []
        self.pr_host_list: list[str] = []
        self.hq_host_list: list[str] = []
        self.sources: list[Source] = []
        self.test = {}
        self.lang = control.apiLanguage()["tmdb"] or "en"
        self.exts = ("avi", "mkv", "mp4", ".ts", "mpg")  # dozwolone rozszerzenia filmów

    def play(self,
             media_type: MediaType,
             ffid: int,
             season: int | None = None,
             episode: int | None = None,
             *,
             #: If true, show edit search dialog instead of source selection.
             edit_search: bool = False,
             #: Override season and episode numbers (play as episode group).
             override_episode: tuple[int, int] | None = None,
             #: Episode group to use (used if season/episode if not set).
             episode_group: str | None | EllipsisType = ...,
             ) -> None:
        try:
            # state.delete_all(module='player')
            with state.with_var('ff.play', module='player'):
                return self._play(media_type=media_type, ffid=ffid, season=season, episode=episode,
                                  edit_search=edit_search, override_episode=override_episode,
                                  episode_group=episode_group)
        except Exception:
            fflog_exc()
            raise
        finally:
            clear_source_modules()

    def _play(self,
              media_type: MediaType,
              ffid: int,
              season: int | None = None,
              episode: int | None = None,
              *,
              edit_search: bool = False,
              override_episode: tuple[int, int] | None = None,
              episode_group: str | None | EllipsisType = ...,
              ) -> None:
        # mark playing state
        state.multi_set(module='player', values=(
            ('playing.run', False),
        ))

        # TODO:  -----------------------------------------------------------------
        # TODO:  ---  refaktor, bez sensu odzyskuje pewne dane, zanim użyć ffinfo
        # TODO:  -----------------------------------------------------------------

        self.sources = []  # create new list to avoid add sources after cancel
        ref: MediaRef = MediaRef(media_type, ffid, season, episode)
        ffitem: FFItem | None = ffinfo.find_item(ref, details=const.sources.info_details)
        if ffitem is None:
            return

        # override season/episode if requested (play as episode group)
        if ref.is_episode and override_episode:
            ffitem.season, ffitem.episode = override_episode
        # elif ref.is_episode and (episode_group is None or isinstance(episode_group, str)):
        #     ffinfo.switch_episode_group(ffitem, episode_group)

        if ref.type == 'show' and (sh_item := ffitem.show_item):
            vtag = sh_item.getVideoInfoTag()
            premiered = sh_item.date
            # show_title = vtag.getEnglishTvShowTitle() or sh_item.title
        else:
            vtag = ffitem.getVideoInfoTag()
            premiered = ffitem.date
            # show_title = ''
        # `title` and `show_title` should be in English.
        title = vtag.getTitle()
        en_title = vtag.getEnglishTitle() or vtag.getOriginalTitle() or title
        query: SourceSearchQuery = {
            'title': en_title,    # title is in English
            'localtitle': title,  # title is in api locale
            'originalname': vtag.getOriginalTitle(),
            'year': vtag.getYear(),
            'imdb': vtag.getUniqueID('imdb'),
            'tmdb': vtag.getUniqueID('tmdb'),
            'season': ffitem.season,
            'episode': ffitem.episode,
            'tvshowtitle': en_title if ref.type == 'show' else '',
            'premiered': str(premiered or ''),
            'episode_group': None,
            'episode_offset': {},
            'ffitem': ffitem,
        }
        default_query = query.copy()
        if const.sources_dialog.edit_search.cache and (se := sources_edit_db.get(ffitem)):
            query.update(se.data)  # type: ignore[reportArgumentType]

        # Switch episode group if needed
        eg_id: str | None = None
        if ref.is_episode:
            if episode_group is not ...:
                if TYPE_CHECKING:
                    assert not isinstance(episode_group, EllipsisType)
                query['episode_group'] = episode_group
            ffinfo.switch_episode_group(ffitem, query.get('episode_group'))
            if ffitem.episode_group.current is not None:
                default_query['season'] = ffitem.season
                default_query['episode'] = ffitem.episode
                if const.sources_dialog.edit_search.show_granularity == 'show':
                    query['season'] = ffitem.season
                    query['episode'] = ffitem.episode
                elif const.sources_dialog.edit_search.show_granularity == 'season':
                    query['episode'] = ffitem.episode
            if const.sources_dialog.edit_search.cache is not None and 'episode_offset' in const.sources_dialog.edit_search.cache and ffitem.season:
                eg_id = ffitem.episode_group.tmdb_id or None
                if offset := query['episode_offset'].get(eg_id, {}).get(ffitem.season):
                    query['episode'] = (ffitem.episode or 0) + offset
        initial_query = query.copy()  # for detect changes after rescan

        # Create window ASAP (before getConstants and other slow operations)
        # Show empty window as placebo - progress bar will be enabled later in background thread
        win: SourceDialogProtocol | None = None
        if not edit_search:
            win = self.SOURCE_DIALOG(sources=self, item=ffitem, query=query,
                                     default_query=default_query, edit_search=edit_search)

        # Initialize aliases and providers in background thread
        init_complete = Event()

        def init_background():
            try:
                ffitem.copy_from(ffitem.season_item, ffitem.show_item)
            except Exception:
                fflog_exc()
            try:
                if show_ffitem := ffitem.show_item:
                    ffinfo.get_title_aliases(show_ffitem)
                else:
                    ffinfo.get_title_aliases(ffitem)
            except Exception:
                fflog_exc()
            try:
                self.getConstants(ffitem=ffitem)
            except Exception:
                fflog_exc()
            init_complete.set()

        # Start background initialization thread (aliases + provider loading)
        init_thread = Thread(target=init_background, name="SourcesInit")
        init_thread.start()

        log_s = f', S{ffitem.season or 0:0>2}E{ffitem.episode or 0:0>2}/group={ffitem.episode_group or ""}' if ref.is_episode else ''
        fflog(f'play(ffid={ffid!r}) ref={ref!a}{log_s}')

        with state.with_var('sources', module='player'):
            # Source searching  int loop (allows rescanning)
            while True:
                # Ensure window exists if needed (e.g., after rescan from EditDialog when edit_search changes to False)
                if not edit_search and win is None:
                    win = self.SOURCE_DIALOG(sources=self, item=ffitem, items=[], query=query,
                                             default_query=default_query, edit_search=edit_search)

                source_to_play: Source | None = None

                try:
                    if win:
                        with state.with_var('sources.window', module='player'):
                            # Search in background thread while window is shown
                            def search_thread(win: SourceDialogProtocol) -> None:
                                # Wait for initialization to complete
                                init_complete.wait()
                                # NOW enable search mode (progress bar appears)
                                win.start_search()
                                self.initial_progress_update(win)
                                # Search for sources
                                sources = self.get_sources(**query, progress_dialog=win) or []
                                # Set found sources to window
                                win.finish_search(sources)

                            thread = Thread(target=search_thread, args=(win,), name="SourcesSearch")
                            thread.start()

                            # Show window (blocks until user closes or selects source)
                            # Search thread updates window in background
                            try:
                                source_to_play = win.do_modal()
                                thread.join(timeout=1)  # wait for thread to finish (should be done already)
                            finally:
                                query = win.query  # get possibly updated query from window before closing (event on cancel, to keep user edit changes)
                                with suppress(Exception):
                                    win.destroy()
                                win = None  # remove reference to closed window
                                # make sure to finish changing sources object before allowing next search or exiting
                                init_complete.wait()
                            break  # source selected or canceled, exit while loop
                    else:
                        # make sure to finish changing sources object if there was no window
                        init_complete.wait()
                    if edit_search:
                        # edit search without source-dialog in background
                        edit_win = EditDialog(query=query, default_query=default_query)
                        edit_win.do_modal()
                        del edit_win
                        break
                except RescanSources as rescan:
                    # Rescan requested (as exception), continue loop with new query
                    if rescan.query:
                        query_changed = query != rescan.query
                        fflog(f'[SOURCES] rescan request (edit={query_changed})')
                        # Close current window before rescanning
                        if win is not None:
                            with suppress(Exception):
                                win.destroy()
                            win = None
                        # Always clear cache on rescan to get fresh results
                        # (whether query changed or user wants to refresh results)
                        from .cache import cache_clear_sources
                        self.sources.clear()
                        cache_clear_sources()
                        query = rescan.query
                        if initial_query['episode_group'] != query['episode_group']:
                            # reset season and episode to current if episode group changed, to avoid wrong offsets
                            # fflog(f'>>>>>>>>>> EG {default_query["episode_group"]!r} -> {query.get("episode_group")!r}: {default_query["season"]}x{default_query["episode"]} -> {ffitem.season}x{ffitem.episode}, eg={ffitem.episode_group}')
                            default_query['season'] = ffitem.season or 0
                            default_query['episode'] = ffitem.episode or 0
                        query['ffitem'] = ffitem
                        # Window will be recreated at the start of next loop iteration
                except Exception:
                    fflog_exc()
                    break
                finally:
                    # make sure to finish changing sources object before allowing next search or exiting
                    init_complete.wait()
                edit_search = False  # skip only first sources scan
        fflog(f'source to play {source_to_play!r}')
        if allowed := const.sources_dialog.edit_search.cache:
            if 'episode_offset' in const.sources_dialog.edit_search.cache and ffitem.season:
                def es(q: SourceSearchQuery) -> str:
                    if q['season'] is None or q['episode'] is None:
                        return 'movie'
                    if q['episode_group']:
                        return f'S{q["season"]}E{q["episode"]}({q["episode_group"]})'
                    return f'S{q["season"]}E{q["episode"]}'
                d_offset = default_query['episode'] or 0
                q_offset = query['episode'] or 0
                i_season = initial_query['season'] or 0
                i_episode_group = initial_query['episode_group']
                fflog(f'{ffitem.ref!a}, S{ffitem.season or 0:02d}E{ffitem.episode or 0:02d}:'
                      f' save episode offset: d={es(default_query)}, i={es(initial_query)}, q={es(query)}, {i_episode_group=}')
                if d_offset and q_offset and d_offset != q_offset:
                    query['episode_offset'].setdefault(query['episode_group'], {})[ffitem.season] = q_offset - d_offset
                elif episode_offset := query['episode_offset'].get(i_episode_group):
                    episode_offset.pop(i_season, 0)
                    if not episode_offset:
                        del query['episode_offset'][eg_id]
            if query == default_query and not query.get('episode_group'):
                fflog(f'{ffitem!a}: remove saved edit')
                sources_edit_db.delete(ffitem)
            else:
                save_query = {k: v for k, v in query.items() if k in allowed}
                fflog(f'{ffitem!a}: save edit: {save_query}')
                sources_edit_db.set(ffitem, save_query)
        with state.with_var('playing.prepare', module='player'):
            if source_to_play:
                player.play(source=source_to_play)
            else:
                player.cancel(ffitem)

    def get_sources(self, **kwargs: Unpack[GetSourcesKwargs]) -> List[Source]:
        with state.with_var('sources.scan', module='player'):
            return self._get_sources(**kwargs)

    def src_format(self, fmt: 'list[list[LiteralString]]', *, qrange: range | None) -> Iterator[str]:
        if qrange is None:
            qmax = settings.getInt("hosts.quality.max")
            qmin = settings.getInt("hosts.quality.min")
            qrange = range(qmin, qmax + 1)
        for f in fmt:
            if len(f) > 2:  # sources
                inc = f[::-1]
                for i in reversed(qrange):  # no reversed(), because pdiag_*_format has already reverted order
                    yield inc[i]
            else:
                yield from f

    def initial_progress_update(self, progress_dialog: ProgressDialogProtocol | None) -> None:
        """Initial progress dialog update."""
        if progress_dialog is None:
            return
        line = L(30536, "Preparing sources...")
        progress_dialog.update(0, line)

    def _get_sources(self, *,
                     title: str,
                     localtitle: str,
                     year: int,
                     imdb: str,
                     tmdb: str,
                     season: int | None,
                     episode: int | None,
                     tvshowtitle: str,
                     premiered: str,
                     originalname: str = "",
                     episode_group: str | None = None,              # used in query, not used directly
                     episode_offset: dict[int, int] | None = None,  # used in query, not used directly
                     quality: str = "HD",
                     timeout: int | None = None,
                     ffitem: FFItem,
                     progress_dialog: ProgressDialogProtocol | None = None,
                     ) -> List[Source]:
        # fflog("get_sources")
        fflog(f'\033[91mget_sources\033[0m({title=}, {localtitle=}, {year=}, {imdb=}, {tmdb=}, '
              f'{season=}, {episode=}, {tvshowtitle=}, {premiered=}, {originalname=} ---------')
        # --- DEBUG ---
        # if 0:
        #     self.sources = [
        #         Source(url='/a/b/c', provider='test', hosting='host', ffitem=ffitem, meta={'size': '1 KB', 'quality': '720p'}),
        #     ]
        #     self.sourcesFilter(ffitem=ffitem)
        #     return self.sources
        # return []

        if timeout is None:
            timeout = settings.getInt("scrapers.timeout.1")

        # progress_dialog is now always created in _play() before calling get_sources()
        # If None, progress updates will be skipped (used for testing or external calls)

        self.prepareSources()

        # -------------------------------------------------------------------------------------------------------- XXX XXX XXX
        def log_source_mods(title='source_mods'):  # TODO: Rremove it  (DEBUG)
            # from pprint import pformat
            nonlocal source_mods
            source_mods = list(source_mods)  # support for generators
            fflog.debug(f'{title}: {", ".join(smod.name for smod in source_mods)}', stack_depth=2)
            # fflog.debug(f'{title}\n{pformat(source_mods, indent=2, width=240, compact=False)}', stack_depth=2)

        source_mods: Iterable[SourceModule] = self.source_mods
        srcmod: SourceModule
        genres: Set[str]
        log_source_mods('start')
        for srcmod in source_mods:
            srcmod.provider.canceled = False

        # filter-out provider modules with provider-level pattern (provider, platform, kodi version).
        # no source-level conditions means the provider could be skipped entirely here
        old_srcmods = source_mods
        source_mods = [srcmod for srcmod in source_mods if srcmod.is_enabled_by_rules()]
        if len(source_mods) < len(old_srcmods):
            fflog(f'Providers disabled by rules: {", ".join(srcmod.name for srcmod in old_srcmods if srcmod not in source_mods)}')

        # filtrowanie po dozowlonych providerach, tzn. takich, które włączył użytkownik w ustawieniach
        if const.dev.sources.force_all_sources:
            fflog('[DEV] force_all_sources: skipping provider user-settings filter')
        else:
            try:
                source_mods = filter(lambda srcmod: settings.getBool(f"provider.{srcmod.name}"), source_mods)
            except (TypeError, ValueError):
                fflog_exc()
        log_source_mods('enabled')

        content = "movie" if not season else "episode"
        if content == "movie":
            # filtrowanie po providerach, które posiadają metodę `movie()`
            source_mods = (srcmod for srcmod in source_mods if hasattr(srcmod.provider, "movie"))
            # TODO: TRAKT  genres = set(trakt.getGenre("movie", "imdb", imdb))
        else:
            # filtrowanie po providerach, które posiadają metodę `tvshow()`
            source_mods = (srcmod for srcmod in source_mods if hasattr(srcmod.provider, "tvshow"))
            # TODO: TRAKT  genres = set(trakt.getGenre("show", "tmdb", tmdb))
        log_source_mods('method')

        # filtrowanie po obsługiwanych gatunkach – ŻADEN provider tego nie dostarcza
        source_mods = filter(lambda srcmod: (not getattr(srcmod.provider, "genre_filter", ())
                                             or set(srcmod.provider.genre_filter) & genres),
                             source_mods)
        log_source_mods('genres')

        # filtrowanie po języku, czy provider dostarcza treści w jakimkowiek akceptowalnym dla nas języku
        langs: Set[str] = set(self.getLanguage())
        if not const.dev.sources.force_all_sources:
            source_mods = filter(lambda srcmod: set(srcmod.provider.language) & langs, source_mods)
        log_source_mods('langs')

        # sortowanie po priorytecie
        if False:
            source_mods = list(source_mods)  # generator → list, for random.shuffle()
            random.shuffle(source_mods)
        # od tej pory `source_mods` jest listą nie generatorem, można wielokrotnie przeglądać
        source_mods = sorted(source_mods, key=lambda srcmod: srcmod.provider.priority)
        log_source_mods('sorted')

        # pozyskiwanie źródeł (linków) od providerów (w wątkach)
        threads: dict[Thread, SourceModule] = {}
        fake = str

        if content == "movie":
            # title = self.getTitle(title)  # niszczy polskie znaki diakrytyczne
            # localtitle = self.getTitle(localtitle)  # niszczy polskie znaki diakrytyczne
            # originalname = self.getTitle(originalname)  # niszczy polskie znaki diakrytyczne
            # aliases = self.getTMDBAliasTitles(tmdb, localtitle, content)
            aliases = self.getAliasTitles(imdb, localtitle, content, ffitem=ffitem)
            if originalname:
                aliases = [
                    {"originalname": originalname, "country": "original"},
                    *aliases,
                ]
            if self.DEBUG_SINGLE_PROVIDER:
                if debug_srcmod := next((s for s in source_mods if s.name == self.DEBUG_SINGLE_PROVIDER), None):
                    fflog(f'DEBUG SINGLE PROVIDER: {debug_srcmod.name}')
                    self.getMovieSource(title, localtitle, aliases, fake(year), imdb,
                                        debug_srcmod.name, debug_srcmod.provider, ffitem=ffitem)
                else:
                    fflog.warning(f'DEBUG SINGLE PROVIDER: {self.DEBUG_SINGLE_PROVIDER} not found')
            else:
                for srcmod in source_mods:
                    th = Thread(
                        target=self.getMovieSource,
                        args=(title, localtitle, aliases, fake(year), imdb, srcmod.name, srcmod.provider),
                        kwargs={'ffitem': ffitem},
                        name=f'{srcmod.name} movie sources',
                    )
                    threads[th] = srcmod
            localtvshowtitle = ''
        else:
            # tvshowtitle = self.getTitle(tvshowtitle)  # niszczy polskie znaki diakrytyczne
            localtvshowtitle = self.getLocalTitle(tvshowtitle, imdb, tmdb, content)
            # aliases = self.getTMDBAliasTitles(tmdb, localtvshowtitle, content)
            # aliases = self.getAliasTitles(imdb, localtvshowtitle, content, ffitem=ffitem)
            aliases = self.getAliasTitles(imdb, localtvshowtitle, content, ffitem=ffitem.show_item or ffitem)
            if originalname:
                aliases = [
                    {"originalname": originalname, "country": "original"},
                    *aliases,
                ]

            # Disabled on 11/11/17 due to hang. Should be checked in the future and possible enabled again.
            # season, episode = thexem.get_scene_episode_number(tvdb, season, episode)

            if self.DEBUG_SINGLE_PROVIDER:
                if debug_srcmod := next((s for s in source_mods if s.name == self.DEBUG_SINGLE_PROVIDER), None):
                    fflog(f'DEBUG SINGLE PROVIDER: {debug_srcmod.name}')
                    self.getEpisodeSource(title, localtitle, fake(year), imdb, fake(tmdb), fake(season), fake(episode),
                                          tvshowtitle or originalname, localtvshowtitle, aliases,
                                          premiered, debug_srcmod.name, debug_srcmod.provider, ffitem=ffitem)
                else:
                    fflog.warning(f'DEBUG SINGLE PROVIDER: {self.DEBUG_SINGLE_PROVIDER} not found')
            else:
                for srcmod in source_mods:
                    th = Thread(
                        target=self.getEpisodeSource,
                        args=(
                            title,
                            localtitle,
                            fake(year),
                            imdb,
                            fake(tmdb),
                            fake(season),
                            fake(episode),
                            tvshowtitle or originalname,
                            localtvshowtitle,
                            aliases,
                            premiered,
                            srcmod.name,
                            srcmod.provider,
                        ),
                        kwargs={
                            'ffitem': ffitem,
                        },
                        name=f'{srcmod.name} episode sources',
                    )
                    threads[th] = srcmod

        # Start threads z timeout tracking
        start_time: float = monotonic()
        end_time: float = start_time + timeout
        fflog(f'[SOURCES] Starting {len(threads)} providers (max {timeout:.1f}s): {", ".join(sorted(srcmod.name for srcmod in threads.values()))}')
        for th in threads:
            th.start()

        string_total = L(32601, 'Total')
        string_premium = L(32606, 'Prem')
        string_normal = L(32607, 'Normal')
        string_languages = L(32014, 'Languages')

        qmax = settings.getInt("hosts.quality.max")
        qmin = settings.getInt("hosts.quality.min")
        qrange = range(qmin, qmax + 1)

        line1 = line2 = line3 = ""
        # debrid_only = settings.getBool("debrid.only")  -- NOT USED

        TOTAL = len(self.QUALITIES)
        source_qq = [0] * (TOTAL + 1)    # +1 for [TOTAL] - normal sources
        prem_source_qq = [0] * (TOTAL + 1)  # +1 for [TOTAL] - premium sources (premium=True OR debrid-compatible)

        debrid_list = []
        debrid_status = False

        total_format = self.TOTAL_FORMAT

        # Event-driven monitoring zamiast busy wait
        update_interval = .5  # Update co sekundę
        next_update_time = monotonic() + update_interval
        total_threads = len(threads)

        # Helper functions for progress dialog formatting
        def format_quality_count(count, color=None, width=2):
            """Format single quality count with color and fixed width"""
            if color is None:
                color = "lime" if count > 0 else "red"
            return f"[COLOR {color}][B]{count:>{width}}[/B][/COLOR]"

        def build_progress_line(*lines):
            """Combine lines with optional remaining line"""
            return '\n'.join(str(line) for line in lines if line)

        def build_quality_line(labels_list, quality_names, qrange, prefix=''):
            """Build quality line with proper alignment"""
            parts = [f"{quality_names[i]}: {labels_list[i]}" for i in reversed(qrange)]
            line = " | ".join(parts)
            return f"{prefix}{line}" if prefix else line

        def build_inline_quality_parts(source_qq, prem_source_qq, quality_names, qrange, has_premium):
            """Build quality parts for inline mode with premium in parentheses"""
            parts = []
            for i in reversed(qrange):
                normal_count = source_qq[i]
                premium_count = prem_source_qq[i]
                total_count = normal_count + premium_count

                if has_premium and premium_count > 0:
                    # Show as: "4K: 12 (1)" where 12 is total, (1) is premium in gold
                    part = f"{quality_names[i]}: {format_quality_count(total_count)} [COLOR gold]({premium_count})[/COLOR]"
                else:
                    # Show as: "4K: 12" (no premium sources, or total is 0)
                    part = f"{quality_names[i]}: {format_quality_count(total_count)}"

                parts.append(part)
            return parts

        # Main loop, wait for provider (source-module) threads to complete or timeout
        while (threads := {th: srcmod for th, srcmod in threads.items() if th.is_alive()}) and (now := monotonic()) < end_time:
            if xbmc.Monitor().abortRequested():
                return sys.exit()

            with suppress(AttributeError):
                if progress_dialog.iscanceled():
                    break

            try:
                # Update progress only periodically to reduce CPU usage
                if next_update_time <= now:
                    next_update_time = now + update_interval

                    if progress_dialog is not None:
                        with suppress():
                            progress_dialog.update_time((now - start_time), (end_time - start_time))

                    for q in qrange:
                        qq = self.QUALITIES[Quality(q)]
                        # Normal sources: not premium and not debridonly
                        source_qq[q] = sum(1 for e in self.sources if e["quality"] in qq and not e.get("premium") and not e.get("debridonly"))
                    source_qq[TOTAL] = sum(source_qq[0:TOTAL])

                    # Premium sources: premium=True OR debrid-compatible
                    for q in qrange:
                        qq = self.QUALITIES[Quality(q)]
                        prem_source_qq[q] = sum(1 for e in self.sources
                                                if e["quality"] in qq and
                                                (e.get("premium") or (debrid_status and any(d.valid_url("", e["hosting"]) for d in debrid_list))))
                    prem_source_qq[TOTAL] = sum(prem_source_qq[0:TOTAL])

                    try:
                        info = [srcmod.name.upper() for srcmod in threads.values()]

                        # Common calculations
                        total_all = source_qq[TOTAL] + prem_source_qq[TOTAL]
                        quality_names = self.QUALITY_NAMES

                        # Count sources by language (using source.sound.lang integer setting)
                        allowed_source_lang = settings.getInt("source.sound.lang")

                        # Map setting value to allowed languages (same as in prepareSources)
                        langs_map: Dict[int, Set[str]] = {
                            0: set(),  # all languages
                            1: {'pl'},
                            2: {'pl', 'multi'},
                            3: {'en', ''},
                            4: {'en', 'multi', ''},
                        }
                        allowed_langs = langs_map.get(allowed_source_lang, set())

                        lang_counts = {}
                        for e in self.sources:
                            lang = e.get("language", "").lower()

                            # Only count if language is allowed (or if all languages allowed)
                            if allowed_source_lang == 0 or any(l in lang.split() for l in allowed_langs):
                                lang_upper = lang.upper() if lang else 'EN'  # empty is EN
                                lang_counts[lang_upper] = lang_counts.get(lang_upper, 0) + 1

                        # Format language display: "EN: 10 | PL: 20 | MULTI: 100"
                        if lang_counts:
                            lang_parts = []
                            for lang, count in sorted(lang_counts.items(), key=lambda x: x[1], reverse=True):
                                color = "lime" if count > 0 else "red"
                                lang_parts.append(f"{lang}: [COLOR {color}][B]{count}[/B][/COLOR]")
                            language_display = " | ".join(lang_parts)
                        else:
                            language_display = ""

                        has_premium = prem_source_qq[TOTAL] > 0

                        # Progress based on completed threads, not time
                        percent = min(99, 100 * (total_threads - len(threads)) // total_threads)

                        # Display mode from const
                        display_mode = SourceSearchProgressStyle(const.sources_dialog.searching.progress_style)

                        if display_mode == SourceSearchProgressStyle.FULL:
                            # Full mode: premium/normal separation with headers
                            prem_source_labels = [format_quality_count(sq, color="gold" if sq > 0 else "red") for sq in prem_source_qq]
                            source_labels = [format_quality_count(sq) for sq in source_qq]

                            if has_premium:
                                # With premium: show headers and totals
                                # Line 0: "Premium" header with total count
                                line0 = f"[B]{string_premium}:[/B] {format_quality_count(prem_source_qq[TOTAL], color='gold')}"
                                # Line 1: Premium qualities
                                line1 = build_quality_line(prem_source_labels, quality_names, qrange)
                                # Line 2: "Normal/Free" header with total count
                                line2 = f"[B]{string_normal}:[/B] {format_quality_count(source_qq[TOTAL])}"
                                # Line 3: Normal qualities
                                line3 = build_quality_line(source_labels, quality_names, qrange)
                                # Line 4: Total (all sources)
                                line4 = f"[B]{string_total}:[/B] {format_quality_count(total_all)}"
                                # Line 5: Languages
                                line5 = f"[B]{string_languages}:[/B] {language_display}" if language_display else None
                                progress_line = build_progress_line(line0, line1, line2, line3, line4, line5)
                            else:
                                # Without premium: no headers, just totals
                                # Line 0: Normal qualities
                                line0 = build_quality_line(source_labels, quality_names, qrange)
                                # Line 1: Total
                                line1 = f"[B]{string_total}:[/B] {format_quality_count(total_all)}"
                                # Line 2: Languages
                                line2 = f"[B]{string_languages}:[/B] {language_display}" if language_display else None
                                progress_line = build_progress_line(line0, line1, line2)

                        elif display_mode == SourceSearchProgressStyle.EXTENDED:
                            # Extended mode: premium in gold parentheses on same line
                            # Line 1: Combined qualities with premium in parentheses
                            combined_parts = build_inline_quality_parts(source_qq, prem_source_qq, quality_names, qrange, has_premium)
                            line1 = ' | '.join(combined_parts)
                            # Line 2: Total with premium shown separately
                            # Format: "Total: 38 (Premium: 4)" or "Total: 38" if no premium
                            if has_premium:
                                line2 = f"{string_total}: {format_quality_count(total_all)} ([COLOR gold]{string_premium}: {prem_source_qq[TOTAL]}[/COLOR])"
                            else:
                                line2 = f"{string_total}: {format_quality_count(total_all)}"
                            progress_line = build_progress_line(line1, line2)

                        else:  # display_mode == SourceSearchProgressStyle.SIMPLE
                            # Normal mode: all sources in one line, no distinction
                            all_sources_qq = [source_qq[i] + prem_source_qq[i] for i in range(len(source_qq))]
                            all_source_labels = [format_quality_count(sq) for sq in all_sources_qq]
                            # Line 1: All sources (normal + premium combined)
                            line1 = build_quality_line(all_source_labels, quality_names, qrange)
                            # Line 2: Total
                            line2 = f"{string_total}: {format_quality_count(total_all)}"
                            progress_line = build_progress_line(line1, line2)

                        if progress_dialog is not None:
                            progress_dialog.update(max(1, percent), progress_line, providers=info)
                    except Exception:
                        fflog_exc()

                # Sleep less when we have sources, more when waiting
                # sleep_time = 0.2 if len(self.sources) > 0 else 0.5
                # xsleep(sleep_time)
                xsleep(0.25)

            except Exception:
                fflog_exc()
                break

        # Final progress update
        try:
            final_message = L(30534, 'Finalizing sources...')
            if progress_dialog is not None:
                progress_dialog.update(100, final_message)
        except Exception:
            pass

        # Cleanup: stop remaining threads
        if threads:
            # fflog(f'[SOURCES] Stopping {len(alive_threads)} remaining threads: {[th.name for th in alive_threads]}')
            fflog(f'[SOURCES] Stopping {len(threads)} remaining providers: {", ".join(sorted(srcmod.name for srcmod in threads.values()))}')
            for th in threads:
                if th.is_alive():
                    th.stop()

        elapsed = timeout - (end_time - monotonic())
        fflog(f'[SOURCES] Completed: found {len(self.sources)} sources ({total_threads - len(threads)} / {total_threads} providers) in {elapsed:.1f}s')

        self.sourcesFilter(ffitem=ffitem)
        return self.sources

    def prepareSources(self):
        fflog("prepareSources")
        if settings.getBool("enableSourceCache"):
            if not control.existsPath(control.dataPath):
                control.make_dir(control.dataPath)
            self.sourceFile = control.sourcescacheFile
        else:
            self.sourceFile = ':memory:'

    def _init_sources_db(self, dbcur: SqlCursor) -> None:
        try:
            dbcur.execute(
                "CREATE TABLE IF NOT EXISTS rel_url ("
                "source TEXT, "
                "imdb_id TEXT, "
                "season TEXT, "
                "episode TEXT, "
                "rel_url TEXT, "
                "UNIQUE(source, imdb_id, season, episode)"
                ");"
            )
            dbcur.execute(
                "CREATE TABLE IF NOT EXISTS rel_src ("
                "source TEXT, "
                "imdb_id TEXT, "
                "season TEXT, "
                "episode TEXT, "
                "hosts TEXT, "
                "added TEXT, "
                "UNIQUE(source, imdb_id, season, episode)"
                ");"
            )
        except OperationalError:
            fflog_exc()

    @contextmanager
    def _with_calling_provider(self, call: ProviderProtocol):
        """Context manager to log exceptions in provider calls."""
        try:
            yield
        except Exception as exc:
            from .log_utils import CallerInfo, traceback_string
            x = CallerInfo.info(n=2)
            z = traceback_string(exc.__traceback__, stack_limit=1, ffonly=True)
            fflog(f'Exception in provider {call.__class__.__module__}.{call.__class__.__name__}: {exc!r}', stack_depth=3)
            fflog(f' --- {x}, {x.module_name} --- \n{z}')

    def getMovieSource(self, title, localtitle, aliases, year, imdb, source, call: ProviderProtocol, from_cache=False, *, ffitem: FFItem):
        fflog.debug(f'getMovieSource {source=}')

        sources = []
        try:
            call.ffitem = ffitem  # XXX, tylko roboczo
            with database.connect(self.sourceFile) as dbcon:
                dbcur = dbcon.cursor()
                self._init_sources_db(dbcur)

                cache_imdb = imdb
                if not cache_imdb or cache_imdb == "0":
                    tmdb = ffitem.getVideoInfoTag().getUniqueID('tmdb')
                    if tmdb:
                        cache_imdb = f"tmdb:{tmdb}"

                """ Fix to stop items passed with a 0 IMDB id pulling old unrelated sources from the database. """
                if imdb == "0":
                    try:
                        dbcur.execute(
                            "DELETE FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcur.execute(
                            "DELETE FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcon.commit()
                    except Exception:
                        pass
                """ END """
                if source in const.sources_dialog.library_cache:
                    # fix pokazania już pobranych przy włączonym cache
                    try:
                        dbcur.execute(
                            "DELETE FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcur.execute(
                            "DELETE FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcon.commit()
                    except Exception:
                        pass
                if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                    try:
                        dbcur.execute(
                            "SELECT * FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        match = dbcur.fetchone()
                        t1 = int(re.sub("[^0-9]", "", str(match[5])))
                        t2 = int(datetime.datetime.now().strftime("%Y%m%d%H%M"))
                        update = abs(t2 - t1) > 60
                        if not update:
                            with fflog_exc():
                                sources = [Source.from_json(it, ffitem=ffitem) for it in literal_eval(match[4])]
                            with fflog_exc():
                                if check_and_add_on_account_sources := getattr(call, 'check_and_add_on_account_sources', None):
                                    check_and_add_on_account_sources(sources, ffitem, source)
                            with self.lock:
                                return self.sources.extend(sources)
                    except Exception:
                        pass

                url = None
                if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                    try:
                        dbcur.execute(
                            "SELECT * FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        url = dbcur.fetchone()
                        url = literal_eval(url[4])
                    except Exception:
                        pass

                try:
                    if url is None and not from_cache:
                        # fflog(f'call({call}).movie({imdb=}, {title=}, {localtitle=}, {aliases=}, {year=})')
                        with self._with_calling_provider(call):
                            url = call.movie(imdb, title, localtitle, aliases, year)
                    if url is None and from_cache:
                        results_cache = cache.cache_get(
                            f"{source}_results", control.sourcescacheFile
                        )
                        if results_cache:  # może w ogóle nie być
                            results_cache = literal_eval(results_cache["value"])
                            if results_cache:  # bo może być pusty
                                url = [results_cache[k] for k in results_cache][0]
                                fflog(f"dla {source} odczytano z cache rekordów: {len(url)}")
                    if url is not None and not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                        dbcur.execute(
                            "DELETE FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcur.execute(
                            "INSERT INTO rel_url Values (?, ?, ?, ?, ?)",
                            (source, cache_imdb, "", "", repr(url)),
                        )
                        dbcon.commit()
                except Exception:
                    pass

                try:
                    with fflog_exc():
                        try:
                            if from_cache:
                                # fflog(f'call({call}).sources({url=}, {self.host_list=}, {self.pr_host_list=}, {from_cache=})')
                                with self._with_calling_provider(call):
                                    sources = call.sources(url, self.host_list, self.pr_host_list, from_cache=from_cache)
                            else:
                                # fflog(f'call({call}).sources({url=}, {self.host_list=}, {self.pr_host_list=})')
                                with self._with_calling_provider(call):
                                    sources = call.sources(url, self.host_list, self.pr_host_list)
                        except ThreadCanceled:
                            pass
                    if sources is None:
                        raise Exception()
                    # remove duplicates while preserving order
                    # it also should work:
                    sources = list({json.dumps(src, sort_keys=True): src for src in sources}.values())

                    # Check if any items has unexpected keys and log them for debugging
                    for src in sources:
                        if unexpected := src.keys() - SourceItem.__annotations__.keys():
                            fflog.warning(f'Source from provider {call.__class__.__module__}.{call.__class__.__name__}'
                                          f' has unexpected source item keys: {", ".join(unexpected)}')

                    with fflog_exc():
                        sources = [Source.from_provider_dict(provider=source, ffitem=ffitem, item=it) for it in sources]
                    with self.lock:
                        self.sources.extend(sources)
                    if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                        dbcur.execute(
                            "DELETE FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcur.execute(
                            "INSERT INTO rel_src Values (?, ?, ?, ?, ?, ?)",
                            (
                                source,
                                cache_imdb,
                                "",
                                "",
                                repr([src.as_json() for src in sources]),
                                datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                            ),
                        )
                        dbcon.commit()
                except Exception:
                    pass
        except OperationalError:
            pass

    def getEpisodeSource(
        self,
        title,
        localtitle,
        year,
        imdb,
        tmdb,
        season,
        episode,
        tvshowtitle,
        localtvshowtitle,
        aliases,
        premiered,
        source,
        call: ProviderProtocol,
        from_cache=False,
        *,
        ffitem: FFItem,
    ):
        # fflog(f'getEpisodeSource {source=}')

        try:
            call.ffitem = ffitem  # XXX, tylko roboczo
            tvdb = ffitem.ids.get('tvdb')
            with database.connect(self.sourceFile) as dbcon:
                dbcur = dbcon.cursor()
                self._init_sources_db(dbcur)

                cache_imdb = imdb
                if not cache_imdb or cache_imdb == "0":
                    if tmdb:
                        cache_imdb = f"tmdb:{tmdb}"

                if source in const.sources_dialog.library_cache:
                    # fix pokazania już pobranych przy włączonym cache
                    try:
                        dbcur.execute(
                            "DELETE FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, season, episode)
                        )
                        dbcur.execute(
                            "DELETE FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, season, episode)
                        )
                        dbcon.commit()
                    except Exception:
                        pass
                if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                    try:
                        sources = []
                        dbcur.execute(
                            "SELECT * FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, season, episode)
                        )
                        match = dbcur.fetchone()
                        t1 = int(re.sub("[^0-9]", "", str(match[5])))
                        t2 = int(datetime.datetime.now().strftime("%Y%m%d%H%M"))
                        update = abs(t2 - t1) > 60
                        if not update:
                            with fflog_exc():
                                sources = [Source.from_json(it, ffitem=ffitem) for it in literal_eval(match[4])]
                            with fflog_exc():
                                if check_and_add_on_account_sources := getattr(call, 'check_and_add_on_account_sources', None):
                                    check_and_add_on_account_sources(sources, ffitem, source)
                            with self.lock:
                                return self.sources.extend(sources)
                    except Exception:
                        pass

                url = None
                if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                    try:
                        dbcur.execute(
                            "SELECT * FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        url = dbcur.fetchone()
                        url = literal_eval(url[4])
                    except Exception:
                        pass

                try:
                    if url is None and not from_cache:
                        tvshowtitle, localtvshowtitle = title, localtitle
                        # fflog(f'call({call}).tvshow({imdb=}, {tvdb=}, {tvshowtitle=}, {localtvshowtitle=}, {aliases=}, {year=})')
                        with self._with_calling_provider(call):
                            url = call.tvshow(imdb, tvdb, tvshowtitle, localtvshowtitle, aliases, year)
                    if url is None and from_cache:
                        results_cache = cache.cache_get(
                            f"{source}_results", control.sourcescacheFile
                        )
                        if results_cache:  # może w ogóle nie być
                            results_cache = literal_eval(results_cache["value"])
                            if results_cache:  # może być pusty
                                url = [results_cache[k] for k in results_cache][0]
                                fflog(f"dla {source} odczytano z cache rekordów: {len(url)}")
                    if url is None:
                        raise Exception()
                    if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                        dbcur.execute(
                            "DELETE FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, "", "")
                        )
                        dbcur.execute(
                            "INSERT INTO rel_url Values (?, ?, ?, ?, ?)",
                            (source, cache_imdb, "", "", repr(url)),
                        )
                        dbcon.commit()
                except Exception:
                    pass

                ep_url = None
                if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                    try:
                        dbcur.execute(
                            "SELECT * FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, season, episode)
                        )
                        ep_url = dbcur.fetchone()
                        ep_url = literal_eval(ep_url[4])
                    except Exception:
                        pass

                try:
                    if url is None:
                        raise Exception()
                    if ep_url is None and not from_cache:
                        # fflog(f'call({call}).episode({url=}, {imdb=}, {tvdb=}, {title=}, {premiered=}, {season=}, {episode=})')
                        with self._with_calling_provider(call):
                            ep_url = call.episode(url, imdb, tvdb, title, premiered, season, episode)
                    if url is None and from_cache:
                        results_cache = cache.cache_get(
                            f"{source}_results", control.sourcescacheFile
                        )
                        if results_cache:  # może w ogóle nie być
                            results_cache = literal_eval(results_cache["value"])
                            if results_cache:  # może być pusty
                                url = [results_cache[k] for k in results_cache][0]
                                fflog(f"dla {source} odczytano z cache rekordów: {len(url)}")
                    if ep_url is None:
                        raise Exception()
                    if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                        dbcur.execute(
                            "DELETE FROM rel_url WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, season, episode)
                        )
                        dbcur.execute(
                            "INSERT INTO rel_url Values (?, ?, ?, ?, ?)",
                            (source, cache_imdb, season, episode, repr(ep_url)),
                        )
                        dbcon.commit()
                except Exception:
                    pass

                try:
                    sources = []
                    try:
                        if from_cache:
                            # fflog(f'call({call}).sources({ep_url=}, {self.host_list=}, {self.pr_host_list=}, {from_cache=})')
                            with self._with_calling_provider(call):
                                sources = call.sources(ep_url, self.host_list, self.pr_host_list, from_cache=from_cache)
                        else:
                            # fflog(f'call({call}).sources({ep_url=}, {self.host_list=}, {self.pr_host_list=})')
                            with self._with_calling_provider(call):
                                sources = call.sources(ep_url, self.host_list, self.pr_host_list)
                    except ThreadCanceled:
                        pass
                    if sources is None:
                        raise Exception()
                    # remove duplicates while preserving order
                    sources = list({json.dumps(src, sort_keys=True): src for src in sources}.values())

                    # Check if any items has unexpected keys and log them for debugging
                    for src in sources:
                        if unexpected := src.keys() - SourceItem.__annotations__.keys():
                            fflog.warning(f'Source from provider {call.__class__.__module__}.{call.__class__.__name__}'
                                          f' has unexpected source item keys: {", ".join(unexpected)}')

                    sources = [Source.from_provider_dict(provider=source, ffitem=ffitem, item=it) for it in sources]
                    with self.lock:
                        self.sources.extend(sources)
                    if not self.DEBUG_SINGLE_PROVIDER:  # use DB if not debugging single provider
                        dbcur.execute(
                            "DELETE FROM rel_src WHERE source = '%s' AND imdb_id = '%s' AND season = '%s' AND episode = '%s'"
                            % (source, cache_imdb, season, episode)
                        )
                        dbcur.execute(
                            "INSERT INTO rel_src Values (?, ?, ?, ?, ?, ?)",
                            (
                                source,
                                cache_imdb,
                                season,
                                episode,
                                repr([src.as_json() for src in sources]),
                                datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                            ),
                        )
                        dbcon.commit()
                except Exception:
                    pass
        except OperationalError:
            pass

    def alterSources(self, url):
        fflog("alterSources")

        try:
            if settings.getInt("hosts.mode") == 2:
                url += "&select=1"
            else:
                url += "&select=2"
            control.run_plugin(url)
        except Exception:
            pass

    def sourcesFilter(self, *, ffitem: FFItem):
        fflog("sourcesFilter")

        # Check if any items has unexpected keys and log them for debugging
        for src in self.sources:
            if unexpected := src.meta.keys() - SourceMeta.__annotations__.keys():
                fflog.warning(f'Source from provider {src.provider or "UNKNOWN"} has unexpected source keys: {", ".join(unexpected)}')

        # Sort by provider+url to get deterministic order after multithreaded collection
        self.sources.sort(key=lambda src: (src.provider, src.url))

        # cache na odczyt ustawień PROVIDER.sort.order
        so = SortOrderSettings(source_mods=self.source_mods)
        # cache na odczyt ustawień PROVIDER.library.color.identify2
        libcolors = LibraryColorSettings(source_mods=self.source_mods)
        # cache na odczyt ustawień PROVIDER.color.identify2
        colors = ColorSettings(source_mods=self.source_mods)
        # Set of src mods with premium color items.
        premium_src_mods: Set[str] = {srcmod.name for srcmod in self.source_mods
                                      if getattr(srcmod.provider, "use_premium_color", False)}

        debrid_only = settings.getBool("debrid.only")
        qmax = Quality(settings.getInt("hosts.quality.max"))
        qmin = Quality(settings.getInt("hosts.quality.min"))
        qrange = range(qmin, qmax + 1)
        # HEVC = settings.getBool("HEVC")
        allowed_source_lang = settings.getInt("source.sound.lang")

        # random.shuffle(self.sources)

        # filtrowanie po zabronionym host (source)
        self.sources = [src for src in self.sources if src.hosting.lower() not in const.sources_dialog.disabled_hosts]

        # filtrowanie po regułach (False wyłącza)
        def is_enabled_by_rule(src: Source) -> bool:
            src.attr_update()
            enabled = True
            for pat, attr in const.sources.rules.items():
                if src.match(pat):
                    enabled = attr is not False
                    if attr and attr.meta:
                        src.meta.update(attr.meta)  # type: ignore[union-attr]  -- attr.meta has not required keys, but if they exist they match SourceMeta
                    if attr and attr.final:
                        break
            return enabled
        self.sources = [src for src in self.sources if is_enabled_by_rule(src)]

        # Filtrowanie po słowach kluczowych w opisie lub linku
        if const.sources.exclude_keywords:
            fflog(f"Filtering sources by keywords: {const.sources.exclude_keywords}")
            keywords_lower = [k.lower() for k in const.sources.exclude_keywords]
            self.sources = [
                src for src in self.sources
                if not any(
                    keyword in src.get("info", "").lower() or
                    keyword in src.get("url", "").lower() or
                    keyword in src.get("filename", "").lower() or
                    keyword in src.get("info2", "").lower()
                    for keyword in keywords_lower
                )
            ]

        # wywalanie zepsutych url (jesli nie są to pliki lokalne)
        # self.sources = sources_with_links(self.sources)

        for src in self.sources:
            if src.get("checkquality") and src["hosting"].lower() not in self.hq_host_list and src["quality"] not in ["SD", "SCR", "CAM"]:
                src["quality"] = "SD"

        local = [src for src in self.sources if src.get('local')]
        for src in local:
            src.update({"language": self._getPrimaryLang() or "en"})
        self.sources = [src for src in self.sources if src not in local]

        # `direct` na początek listy
        self.sources.sort(key=lambda src: not src.get('direct'))

        filter = []
        # for d in debrid.debrid_resolvers:
        #     valid_hoster = {src.hosting for src in self.sources}
        #     valid_hoster = {hosting.lower() for hosting in valid_hoster if d.valid_url('', hosting)}
        #     filter = [src for src in self.sources if src.hosting.lower() in valid_hoster]
        #     for src in filter:
        #         src.meta['debrid'] = d.name
        # if not debrid_only or not debrid.status():
        filter.extend(src for src in self.sources
                      if src.hosting.lower() not in self.pr_host_list and not src.meta.get("debridonly"))
        self.sources = filter

        for i in range(len(self.sources)):
            q = self.sources[i]["quality"]
            if q == "HD":
                self.sources[i].update({"quality": "720p"})

        filter = []
        filter += local

        CAM_disallowed = settings.getBool("CAM.disallowed")
        if const.dev.sources.force_all_sources:
            filter += list(self.sources)
        else:
            for i in reversed(qrange):
                qq = self.QUALITIES[Quality(i)]
                filter.extend(s for s in self.sources if s["quality"] in qq)

            if CAM_disallowed:
                filter += [i for i in self.sources if i["quality"] in ["SCR"]]
            else:
                filter += [i for i in self.sources if i["quality"] in ["SCR", "CAM"]]

            # Filtrowanie po dostępności transferu
            if settings.getBool("source.premium.no_transfer"):
                filter = [
                    i for i in filter
                    if not i.get("no_transfer", False) or i.get("on_account", False)
                ]

        self.sources = filter

        # filter_out = {
        #     id(i)
        #     for i in self.sources
        #     if i["source"].lower() in self.hostblockDict and "debrid" not in i
        # }
        # self.sources = [i for i in self.sources if id(i) not in filter_out]

        # filtrowanie po wielkości pliku
        if not const.dev.sources.force_all_sources:
            size_min = settings.getInt('source.size.min') * GB
            size_max = (settings.getInt('source.size.max') or 10**6) * GB
            size_range = range(size_min, size_max + 1)
            self.sources = [
                s for s in self.sources
                if s.hosting in ("download", "library")
                or not (size := source_utils.convert_size_to_bytes(s.get("size", "")))
                or size in size_range
            ]

        multi = [i["language"] for i in self.sources]
        multi = [x for y, x in enumerate(multi) if x not in multi[:y]]
        multi = True if len(multi) > 1 else False

        if multi:
            self.sources = [i for i in self.sources if not i["language"] == "en"] + [
                i for i in self.sources if i["language"] == "en"
            ]

        # ograniczenie maksymalnej ilości źródeł
        self.sources = self.sources[:2000]

        # muszą być wszystkie pozycje, które zwraca source_utils.check_sd_url()
        # i które zostały "zassane" przez zmienną filter
        my_quality_order = ["4K", "1440p", "1080p", "1080i", "720p", "SD", "SCR", "CAM"]
        quality_order = {key: i for i, key in enumerate(my_quality_order)}

        # muszą być wszystkie możliwości wypisane jakie chcemy obsługiwać
        language_order = {key: i for i, key in enumerate(const.sources.language_order)}

        priority = self.language_type_priority

        def normalize_language_type(text: str) -> Tuple[Tuple[int, ...], bool]:
            parts = text.lower().replace("|", " ").replace(",", " ").split()
            is_kino = "kino" in parts
            prioritized = tuple(
                priority[p]
                for p in parts
                if p in priority
            )
            return (prioritized, is_kino)

        # ustalenie kolejności dla nazw serwisów
        # z ustawień sort.order
        my_provider_order: List[Union[str, int]] = list(range(1, 11))

        premium_providers = sorted(
            {i["provider"] for i in self.sources if i.get("premium", False)}  # Domyślnie False, jeśli "premium" brak
        )
        my_provider_order.extend(premium_providers)
        provider_order = {key: i for i, key in enumerate(my_provider_order)}

        # --- wpólne funkcje pomocne przy sortowaniu ---
        def order_provider(src: Source) -> int:
            provider: str = src['provider']
            # Sprawdź czy to premium źródło
            if provider.lower() in premium_src_mods and src.get("premium"):
                # Premium - użyj ustawionego sort.order
                return provider_order.get(so[provider] or provider, len(my_provider_order))
            else:
                # Darmowe - standardowa pozycja (bez preferencji)
                return len(my_provider_order)

        def order_size(src: Source) -> int:
            size = src.get("size")
            if not size:
                size = (src.get("info") or "").rpartition("|")[2]
            return source_utils.convert_size_to_bytes(size) * -1

        def order(src: Source, *, by_provider: bool = True) -> int:
            if (order := src.attr.order) is None:
                order = 0
                for i, name in enumerate(reversed(('download', 'library', 'plex', 'jellyfin')), 1):
                    if src.provider.startswith(name):  # TODO, why not provider == name?
                        order += 2000 + i
                if src.get('on_account', 0):
                    order += 1000
                if by_provider:
                    order += 999 - min(999, order_provider(src))
            # d['_order'] = order  # DEBUG order
            return -order
        # wybór wariantu
        sort_source = settings.getInt("hosts.sort")
        if sort_source == 0:
            fflog("Sortuję wg dostawców")  # by providers
            self.sources = sorted(
                self.sources,
                key=lambda d: (
                    order(d),
                    language_order[d["language"]],
                    quality_order[d["quality"]],
                    d["provider"],  # provider (serwis internetowy www)
                    normalize_language_type(d.get("info", "")),
                    order_size(d),
                ),
            )
        if sort_source == 1:
            fflog("Sortuję wg źródeł (hostingów)")  # by hosting (hosting, server)
            self.sources = sorted(
                self.sources,
                key=lambda d: (
                    order(d),
                    language_order[d["language"]],
                    quality_order[d["quality"]],
                    d["hosting"],  # hosting (serwer, hosting)
                    normalize_language_type(d.get("info", "")),
                    order_size(d),
                ),
            )
        if sort_source == 2:
            fflog("Sortuję wg rozmiaru")  # by size
            self.sources = sorted(
                self.sources,
                key=lambda d: (
                    order(d),
                    order_size(d),
                    normalize_language_type(d.get("info", "")),
                ),
            )
        if sort_source == 3:
            custom_criterion = ' -> '.join(SortElem(settings.getInt(f"hosts.sort.elem{n+1}")).name
                                           for n in range(4))
            fflog(f'Sortuję wg ustawień użytkownika: {custom_criterion}')  # custom

            # funkcja pomocnicza
            def choose_criterium(d: Source, n: int) -> int:
                crit = SortElem(settings.getInt(f"hosts.sort.elem{n+1}"))
                # fflog(f"choose_criterium {n}: {crit.name}")
                try:
                    if crit == SortElem.SERVICE:
                        return order_provider(d)
                    if crit == SortElem.LANGUAGE:
                        return language_order[d["language"]]
                    if crit == SortElem.QUALITY:
                        return quality_order[d["quality"]]
                    if crit == SortElem.SIZE:
                        return order_size(d)
                except Exception as e:
                    fflog(f"Error in choose_criterium for {d}: {e!r}")
                return 0

            # sortowanie
            self.sources = sorted(
                self.sources,
                key=lambda d: (
                    # zawsze na początku (pobrane,local ...) oraz na koncie online
                    order(d, by_provider=False),
                    # kryteria użytkownika
                    choose_criterium(d, 0),  # 1-wsze kryterium
                    choose_criterium(d, 1),  # 2-gie kryterium
                    choose_criterium(d, 2),  # 3-cie kryterium
                    choose_criterium(d, 3),  # 4-te kryterium
                    normalize_language_type(d.get("info", "")),
                ),
            )

        exts = self.exts
        extra_info = settings.getBool("sources.extrainfo")

        # Kompilacja wyrażeń regularnych dla pola 'audio'
        audio_re1 = re.compile(r"(?<!\d)([57]\.[124](?:\.[24])?)\.(ATMOS)\b", re.I)
        audio_re2 = re.compile(r"(?<=[DSPXAC3M])[.-]?([57261]\.[102])\b", re.I)
        audio_re3 = re.compile(r"\b(DTS)[.-]?(HD|ES|EX|X(?!26))[. ]?(MA)?", re.I)
        audio_re4 = re.compile(r"(TRUEHD|DDP)\.(ATMOS)\b", re.I)
        audio_re5 = re.compile(r"(custom|dual)\.(audio)", re.I)
        audio_re6 = re.compile(r"ddp(?!l)", re.I)

        # Kompilacja wyrażeń regularnych dla pola 'codec'
        codec_re1 = re.compile(r"(\d{2,3})(fps)", re.I)
        codec_re2 = re.compile(r"plus", re.I)
        codec_re3 = re.compile(r"\bDoVi\b", re.I)
        codec_re4 = re.compile(r"\s*/\s*DolbyVision", re.I)
        codec_re5 = re.compile(r"DolbyVision", re.I)

        # Kompilacja wyrażeń regularnych dla pola 'quality'
        quality_re1 = re.compile(r"\b(\w+)\.(\w+)\b", re.I)

        for i, src in enumerate(self.sources):
            url2: str = ''
            if extra_info:
                try:
                    if url2 := src.meta.get("filename"):
                        pass
                    else:
                        url2 = (
                            src["url"]
                            .replace(" / ", " ")
                            .replace("_/_", "_")
                            .rstrip("/")
                            .split("/")[-1]
                        )
                        url2 = url2.rstrip("\\").split("\\")[
                            -1
                        ]  # dla plików z własnej biblioteki na dysku lokalnym
                        # fflog(f' {[i]} {url2=!r}')
                        url2 = re.sub(
                            r"(\.(html?|php))+$", "", url2, flags=re.I
                        )  # na przypadki typu "filmik.mkv.htm"
                        if url2.lower()[-3:] not in exts:
                            # próba pozyskanie nazwy z 2-giej linijki lub opisu
                            if (
                                "info2" in src
                                and src["info2"]
                                and src["info2"].lower()[-3:] in exts
                            ):
                                url2 = src["info2"]
                            else:
                                """
                                # to raczej nie będzie już wykorzystywane, bo okazało się, że info może mieć juz swoje oznaczenia, więc mogą się dublować
                                url2 = src["info"] if src["info"] else ''
                                # próba odfiltrowania nazwy
                                url2 = url2.split("|")[-1].strip().lstrip("(").rstrip(")")
                                """
                                url2 = ""

                    url2 = unquote(
                        url2
                    )  # zamiana takich tworów jak %nn (np. %21 to nawias)
                    url2 = unescape(url2)  # pozbycie się encji html-owych

                    t = PTN.parse(url2)  # proces rozpoznawania
                    t3d = (
                        t["3d"] if "3d" in t else ""
                    )  # zapamiętanie informacji pod inną zmienną czy wersja 3D
                    textended = (
                        t["extended"] if "extended" in t else ""
                    )  # informacja o wersji rozszerzonej
                    tremastered = (
                        t["remastered"] if "remastered" in t else ""
                    )  # informacja o wersji zremasterowanej

                    # poniżej korekty wizualne
                    if "audio" in t:
                        t["audio"] = audio_re1.sub(r"\1 \2", t["audio"])
                        t["audio"] = audio_re2.sub(r" \1", t["audio"])
                        t["audio"] = audio_re3.sub(r"\1-\2 \3", t["audio"]).rstrip()
                        t["audio"] = audio_re4.sub(r"\1 \2", t["audio"])
                        t["audio"] = audio_re5.sub(r"\1 \2", t["audio"])
                        t["audio"] = audio_re6.sub("DD+", t["audio"])
                    if "codec" in t:
                        t["codec"] = codec_re1.sub(r"\1 \2", t["codec"])
                        t["codec"] = codec_re2.sub("+", t["codec"])
                        t["codec"] = codec_re3.sub("DV", t["codec"])
                        if "DV".lower() in t["codec"].lower():
                            t["codec"] = codec_re4.sub("", t["codec"])
                        else:
                            t["codec"] = codec_re5.sub("DV", t["codec"])
                    if "quality" in t:
                        t["quality"] = quality_re1.sub(r"\1-\2", t["quality"])

                    t = [
                        t[j]
                        for j in t
                        if "quality" in j or "codec" in j or "audio" in j
                    ]
                    t = " | ".join(t)

                    if not t:
                        t = source_utils.getFileType(
                            url2
                        )  # taki fallback dla PTN.parse()
                        t = t.strip()

                    """
                    # pozbycie się tych samych oznaczeń ze zmiennej info
                    if t:
                        src["info"] = re.sub(fr'(\b|[ ._|/]+)({"|".join(t.split(" / "))})\b', '', src["info"], flags=re.I)
                    """

                    # dodanie dodatkowych informacji (moim zdaniem ważnych)
                    if t3d:
                        if "3d" in url2.lower() and "3d" not in t.lower():
                            t = f"[3D] | {t}"
                        else:
                            t = t.replace("3D", "[3D]")
                    # dodatkowe oznaczenie pliku z wieloma sciezkami audio
                    if (
                        re.search(
                            r"\bMULTI\b", url2, re.I
                        )  # szukam w adresie, który powinien zawierać nazwę pliku
                        and "mul" not in src["language"].lower()
                        # and "PL" not in src["language"].upper()  # założenie, że jak wykryto język PL, to nie ma potrzeby o dodatkowym ozaczeniu
                        and "multi"
                        not in src[
                            "info"
                        ].lower()  # sprawdzenie, czy przypadkiem już nie zostało przekazane przez plik źródła
                        and "multi"
                        not in t.lower()  # sprawdzenie, czy nie ma tej frazy już w opisie
                    ):
                        t += " | MULTI"
                    if (
                        "multi" in t.lower()
                        or "multi" in src["info"].lower()
                    ) and src["language"] != "pl":
                        src["language"] = "multi"  # wymiana języka
                        t = re.sub(
                            r"[/| ]*multi\b", "", t, flags=re.I
                        )  # wywalenie z opisu, aby nie było dubli
                        src["info"] = re.sub(
                            r"[/| ]*multi\b", "", src["info"], flags=re.I
                        )  # wywalenie z opisu, aby nie było dubli

                    if textended:
                        if textended is True:
                            t += " | EXTENDED"
                        else:
                            textended = re.sub(
                                "(directors|alternat(?:iv)?e).(cut)",
                                r"\1 \2",
                                textended,
                                flags=re.I,
                            )
                            t += f" | {textended}"

                    # długi napis i czy aż tak istotny?
                    if tremastered:
                        if tremastered is True:
                            t += " | REMASTERED"
                        else:
                            if "rekonstrukcja" not in t.lower():
                                tremastered = re.sub(
                                    "(Rekonstrukcja).(cyfrowa)",
                                    r"\1 \2",
                                    tremastered,
                                    flags=re.I,
                                )
                                t += f" | {tremastered}"

                    if (
                        "imax" in url2.lower() and "imax" not in t.lower()
                    ):  # sprawdzenie czy dodać info IMAX
                        t += " | [IMAX]"

                    if (
                        "avi" in url2.lower()[-3:] and "avi" not in t.lower()
                    ):  # aby nie bylo zdublowań
                        t += " | AVI"  # oznaczenie tego typu pliku, bo nie zawsze dobrze odtwarza sie "w locie"

                    t = t.lstrip(
                        " | "
                    )  # przydaje się, jak ani PTN.parse() ani getFileType() nic nie znalazły
                    t += " "

                    src.meta['info2'] = ' | '.join(x for x in [src.meta.get('info2'), t] if x.strip())
                except Exception:
                    t = None
            else:
                t = None

            # u = src["url"]  -- NOT USED

            p = src.provider  # serwis, strona www
            lng = src["language"]
            s = src.hosting  # serwer / hosting / dawne "source"
            q = src["quality"]  # rozdzielczość

            s = s.rsplit(".", 1)[
                0
            ]  # wyrzucenie ostatniego człona domeny (np. ".pl", ".com")

            try:  # f to info (tu może być też rozmiar pliku na końcu)
                f = " | ".join(
                    [
                        "[I]%s [/I]" % info.strip()
                        for info in src["info"].split("|")
                    ]
                )
            except Exception:
                f = ""

            d = src.meta.setdefault('debrid', '')
            if d.lower() == "real-debrid":
                d = "RD"

            if d:
                label = "%02d | [B]%s | %s[/B] | " % (i, d, p)
            else:
                label = "%02d | [LIGHT][B]%s[/B][/LIGHT] | " % (i, p)

            # oznaczenie, czy źródło jest w tzw. bibliotece danego serwisu
            if src.get('on_account'):
                if expires := src.get('on_account_expires'):
                    label += f'[I]konto ({expires})[/I]  | '
                else:
                    label += "[I]konto[/I]  | "

            # oznaczenie języka
            if lng:
                if (
                    multi
                    and lng != "en"  # nie rozumiem, kiedy ten warunek zachodzi
                    or not multi
                    and lng != "en"  # dałem ten warunek
                ):
                    label += "[B]%s[/B] | " % lng

            if t:  # extra_info
                if q in ["4K", "1440p", "1080p", "1080i", "720p"]:
                    label += "%s | [B][I]%s [/I][/B] | [I]%s[/I] | %s" % (s, q, t, f)
                elif q == "SD":
                    # label += "%s | %s | [I]%s[/I]" % (s, f, t)
                    # moja propozycja (wielkość pliku na końcu - dla spójności)
                    label += "%s | [I]%s[/I] | %s" % (s, t, f)
                else:
                    # label += "%s | %s | [I]%s [/I] | [I]%s[/I]" % (s, f, q, t)
                    # moja propozycja (wielkość pliku na końcu - dla spójności)
                    # label += "[LIGHT]%s | [B][I]%s [/I][/B] | [I]%s[/I] | %s[/LIGHT]" % (s, q, t, f)
                    label += "[LIGHT]%s | [I]%s[/I] | %s[/LIGHT]" % (s, t, f)
            else:
                if q in ["4K", "1440p", "1080p", "1080i", "720p"]:
                    label += "%s | [B][I]%s [/I][/B] | %s" % (s, q, f)
                elif q == "SD":
                    label += "%s | %s" % (s, f)
                else:
                    # label += "%s | %s | [I]%s [/I]" % (s, f, q)
                    # moja propozycja (wielkość pliku na końcu - dla spójności)
                    # label += "[LIGHT]%s | [B][I]%s [/I][/B] | %s[/LIGHT]" % (s, q, f)
                    label += "[LIGHT]%s | %s[/LIGHT]" % (s, f)

            # korekty wizualne
            label = label.replace("| 0 |", "|").replace(" | [I]0 [/I]", "")
            label = re.sub(r"\[I\]\s+\[/I\]", " ", label)
            label = re.sub(r"\|\s+\|", "|", label)
            label = re.sub(
                r"\|\s+\|", "|", label
            )  # w pewnych okolicznościach ponowne wykonanie takiej samej linijki kodu jak wyżej pomaga
            label = re.sub(r"\|(?:\s+|)$", "", label)
            label = re.sub(
                r"(\d+(?:[.,]\d+)?\s*[GMK]B)", r"[B]\1[/B]", label, flags=re.I
            )  # wyróżnienie rozmiaru pliku
            label = re.sub(
                r"(?<=\d)\s+(?=[GMK]B\b)", "\u00A0", label, flags=re.I
            )  # aby nie rodzielal cyfr od jednostek
            # aby np. 1080i było bardziej widoczne
            # label = re.sub(r"\s?((\[\w\])*(?:1080|720|1440)[pi])\s?", r"[LOWERCASE]\1[/LOWERCASE]", label,flags=re.I)
            label = re.sub(
                "((?:1080|720|1440)[pi])",
                r"[LOWERCASE]\1[/LOWERCASE]",
                label,
                flags=re.I,
            )

            # To źródło jest w biliotece, używamy specjalnego koloru dla biblioteki tego providera
            color: str = libcolors[p]
            if not color or not src.get("on_account"):
                # Sprawdź czy to premium źródło od premium providera
                if p.lower() in premium_src_mods and src.get("premium"):
                    # Premium content - użyj dedykowanego koloru providera
                    color = colors[p]
                    if not color or color == DEFAULT_COLOR_LINK:
                        # Fallback na globalny kolor premium
                        color = settings.getString("prem.color.identify2")
                else:
                    # Darmowy content - zawsze domyślny kolor
                    color = settings.getString("default.color.identify2")
            # Jeśli jest kolor, ale jest to kolor domyślny, to jawnie bierzemy jego wartość.
            if color == DEFAULT_COLOR_LINK:
                color = settings.getString("default.color.identify2")
            # Ręczne nadpisywanie kolorów
            color = src.attr.color or color
            # Jeśli mamy kolor, to kolorujemy
            if color:
                src["color_identify"] = color
                src["label"] = f"[COLOR {color}]{label.upper()}[/COLOR]"
            else:
                # custom-lista musi mieć jakiś kolor, bierzemy domyślny (niejawnie)
                src["color_identify"] = settings.getString("default.color.identify2")
                src["label"] = label.upper()

            if (
                settings.getBool("sources.filename_in_2nd_line")
                and "info2" not in src
            ):
                if url2 and url2.lower()[-3:] in exts:  # zmienna 'exts' jest definiowana po 'url2'
                    src["info2"] = url2
                elif extra_info and (filename := src.get("filename")):
                    src["info2"] = filename

            if text := src.get("info2"):
                src["info2"] = unescape(unquote(text))  # mam nadzieję, kolejność odkodowywania nie ma znaczenia
                src["label"] += f"[LIGHT][CR]  {text}[/LIGHT]"

        # czy mogą być pozycje bez "label" ?
        self.sources = [src for src in self.sources if "label" in src]

        """to już w dzisiejszych czasach chyba nie ma znaczenia
        if not HEVC:
            self.sources = [
                src
                for src in self.sources
                if "HEVC" not in src["label"] or "265" not in src["label"]
            ]
        """

        if not const.dev.sources.force_all_sources:
            if not settings.getBool("HDR.allowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if "HDR" not in src["label"]
                ]

            if not settings.getBool("DV.allowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if "DV" not in src["label"]
                ]

            if not settings.getBool("AV1.allowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if "AV1" not in src["label"]
                ]

            if not settings.getBool("HEVC.allowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if "HEVC" not in src["label"] or "265" not in src["label"]
                ]

            if not settings.getBool("F3D.allowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if "[3D]" not in src["label"]
                ]

            if not settings.getBool("AVI.allowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if not (
                        src.get("url", "").lower().endswith(".avi")
                        or src.get("filename", "").lower().endswith(".avi")
                    )
                ]

            if CAM_disallowed:
                CAM_format = ["camrip", "tsrip", "hdcam", "hqcam", "dvdcam", "dvdts", "cam", "telesync", " ts"]
                if settings.getBool("HDTS.disallowed"):
                    CAM_format += ["hdts", "hd-ts"]
                self.sources = [
                    src
                    for src in self.sources
                    # if "CAM" not in src["label"]
                    if not any(x in src["label"].lower() for x in CAM_format)
                ]

            if settings.getBool("SUBTITLES.disallowed"):
                def _has_pl_audio(label):
                    return any(x in label for x in ["lektor", "dubbing"])

                def _has_subs(label):
                    return any(x in label for x in ["napisy", "subbed", "subtitles", " sub ", "]sub"])

                self.sources = [src for src in self.sources
                                if not _has_subs(src["label"].lower()) or _has_pl_audio(src["label"].lower())]

            if settings.getBool("LEKTORAI.disallowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if not source_utils.is_ai_lector(src.get("label", ""))
                ]

            if settings.getBool("MD.sound.disallowed"):
                self.sources = [
                    src
                    for src in self.sources
                    if not re.search(r"\b(md|dubbing[ _.-]kino)\b", src["label"], re.I)
                ]

            if allowed_source_lang:
                langs: dict[int, set[str]] = {
                    1: {'pl'},
                    2: {'pl', 'multi'},
                    3: {'en', ''},  # puste to podobno EN
                    4: {'en', 'multi', ''},  # puste to podobno EN
                }
                allowed = langs[allowed_source_lang]
                if const.sources.include_languages:
                    allowed |= {lang.lower() for lang in const.sources.include_languages}
                self.sources = [src for src in self.sources
                                if set(src.get('language', '').lower().split()) & allowed]

        # return all sources
        return self.sources

    def resolve_source(self, item: Source, /, info: bool = False, for_resolve: SourceResolveKwargs | None = None, **kwargs) -> str | None:
        fflog("sourcesResolve")
        if for_resolve is None:
            for_resolve = {}
        try:
            if item is None:
                raise ValueError('No source item')
            if item.get('fake'):
                if resolve_to := item.get('resolve_to'):
                    return str(resolve_to)
                raise ValueError('Fake source item')

            u = url = item["url"]

            d = item["debrid"]
            direct = item.get("direct", False)
            local = item.get("local", False)

            provider = item["provider"]
            src_mod: SourceModule = next(iter(sm for sm in self.source_mods if sm.name == provider))
            fflog(f'Go to {item.provider!r} to resolve {url!r}')
            u = url = src_mod.provider.resolve(url, **for_resolve)

            if url is None:
                error_msg = L(30337, 'Selected link is not working')
                fflog.warning(f'Dead link in provider {provider!r} ({item.get("hosting", "unknown")}): {u!r}')
                raise DeadLink(error_msg)

            if "://" not in str(url) and not local:
                # if provider in ('netflix', 'external'):
                #    return url
                raise ValueError(f'Invlaid URL {url!r} in provider {provider!r}')

            if not local:
                url = url[8:] if url.startswith("stack:") else url

                urls = []
                for part in url.split(" , "):
                    u = part
                    # Plugin URLs are internal Kodi paths and should not be passed to external resolvers.
                    if part.startswith("plugin://"):
                        pass  # Keep the plugin URL as is.
                    elif not d == "":
                        part = debrid.resolver(part, d)

                    elif not direct:
                        if resolveurl is None:
                            fflog.warning('resolveurl module not available')
                        else:
                            hmf = resolveurl.HostedMediaFile(url=u, include_disabled=True, include_universal=False)
                            if hmf.valid_url():
                                part = hmf.resolve()
                    urls.append(part)

                url = "stack://" + " , ".join(urls) if len(urls) > 1 else urls[0]

            if not url:
                raise ValueError(f'Empty URL in provider {provider!r}')

            ext = (
                url.split("?")[0]
                .split("&")[0]
                .split("|")[0]
                .rsplit(".")[-1]
                .replace("/", "")
                .lower()
            )
            if ext == "rar":
                raise ValueError(f'RAR in URL in provider {provider!r}')

            drm_url, _, headers = url.partition('|')
            if " " in headers:
                headers = quote_plus(headers).replace("%3D", "=")
            headers = dict(parse_qsl(headers))

            if url.startswith("http") and ".m3u8" in url and not headers:
                # Only ping the m3u8 URL when there are no special headers.
                # When headers are present (e.g. from resolveurl), the URL is already
                # valid and verified — the CDN may reject requests without exact headers.
                result = client.request(drm_url, headers=headers, output="geturl", timeout=20)
                if result is None:
                    raise DeadLink(f'Resolve m3u8 failed in provider {provider!r}')
            # elif url.startswith("http"):
            #     return url

            fflog(f'[SOURCES] resolve to {url!r} in provider {provider!r}')
            return url

        except DeadLink as e:
            # Przewidywane błędy (martwe źródło, invalid URL, itd.) - logujemy jako warning
            error_msg = str(e)
            fflog.warning(f'Failed to resolve source: {error_msg}')
            if info:
                control.infoDialog(f'{error_msg}', sound=False, icon='INFO')
            return

        except Exception as e:
            # Nieprzewidziane błędy - logujemy jako exception
            fflog_exc()
            error_info = L(32401, 'No stream available')
            if str(e):
                import xbmcgui
                xbmcgui.Dialog().notification('FanFilm', f'{error_info}: {e}')
            if info:
                # player.cancel()
                control.infoDialog(f'{error_info}: {e}', sound=False, icon='INFO')
            return

    # def sourcesDirect(self, items):
    #     fflog("sourcesDirect")

    #     filter = [
    #         i
    #         for i in items
    #         if i["source"].lower() in self.hostblockDict and i["debrid"] == ""
    #     ]
    #     items = [i for i in items if i not in filter]

    #     items = [
    #         i
    #         for i in items
    #         if ("autoplay" in i and i["autoplay"]) or "autoplay" not in i
    #     ]

    #     if settings.getBool("autoplay.sd"):
    #         items = [
    #             i
    #             for i in items
    #             if i["quality"] not in ["4K", "1440p", "1080p", "1080i", "HD"]
    #         ]

    #     u = None

    #     header = control.addonInfo("name")
    #     header2 = header.upper()

    #     try:
    #         control.sleep(1000)

    #         progress_dialog_mode = ProgressDialogMode(settings.getInt("progress.dialog"))
    #         if progress_dialog_mode is ProgressDialogMode.BACKGROUND:
    #             progressDialog = control.progressDialogBG()
    #         else:
    #             progressDialog = control.progressDialog()
    #         progressDialog.create(header, "")
    #         progressDialog.update(0)
    #     except Exception:
    #         pass

    #     for i in range(len(items)):
    #         try:
    #             if progressDialog.iscanceled():
    #                 break
    #             progressDialog.update(
    #                 int((100 / float(len(items))) * i),
    #                 str(items[i]["label"]) + "\n" + str(" "),
    #             )
    #         except Exception:
    #             progressDialog.update(
    #                 int((100 / float(len(items))) * i),
    #                 str(header2) + "\n" + str(items[i]["label"]),
    #             )

    #         try:
    #             if xbmc.Monitor().abortRequested():
    #                 return sys.exit()

    #             url = self.sourcesResolve(items[i])
    #             if u is None:
    #                 u = url
    #             else:
    #                 break
    #         except Exception:
    #             pass

    #     try:
    #         progressDialog.close()
    #     except Exception:
    #         pass

    #     return u

    def getLanguage(self):
        fflog.debug("getLanguage")

        langDict = {
            "English": ["en"],
            "German": ["de"],
            "German+English": ["de", "en"],
            "French": ["fr"],
            "French+English": ["fr", "en"],
            "Portuguese": ["pt"],
            "Portuguese+English": ["pt", "en"],
            "Polish": ["pl"],
            "Polish+English": ["pl", "en"],
            "Korean": ["ko"],
            "Korean+English": ["ko", "en"],
            "Russian": ["ru"],
            "Russian+English": ["ru", "en"],
            "Spanish": ["es"],
            "Spanish+English": ["es", "en"],
            "Greek": ["gr"],
            "Italian": ["it"],
            "Italian+English": ["it", "en"],
            "Greek+English": ["gr", "en"],
        }
        name = settings.getString("providers.lang")
        return langDict.get(name, ["pl"])

    def getLocalTitle(self, title, imdb, tmdb, content):
        fflog("getLocalTitle")

        lang = self._getPrimaryLang()
        if not lang:
            return title

        # TODO: TRAKT
        t = ''
        # if content == "movie":
        #     t = trakt.getMovieTranslation(imdb, lang)
        # else:
        #     t = trakt.getTVShowTranslation(imdb, lang)

        return t or title

    def getAliasTitles(self, imdb, localtitle, content, *, ffitem: FFItem) -> List[Dict[Literal['title', 'country', 'originalname'], str]]:
        return [{'title': a.title, 'country': a.country} for a in ffitem.aliases_info]
        # fflog("getAliasTitles")
        # lang = self._getPrimaryLang()

        # try:
        #     t = (ffinfo.trakt.aliases('movie', imdb) if content == "movie" else ffinfo.trakt.aliases('show', imdb))
        #     return [i for i in t if (i.get("country", "").lower() in [lang, "", "us"]
        #                              and i.get("title", "").lower() != localtitle.lower())]
        # except:
        #     return []

    def getTMDBAliasTitles(self, tmdb, localtitle, content):
        fflog("getTMDBAliasTitles")

        try:
            api_key = settings.getString("tmdb.api_key") or apis.tmdb_API
            base_url = "https://api.themoviedb.org/3/"

            if content == "movie":
                url = f"{base_url}movie/{tmdb}/alternative_titles?api_key={api_key}"
            else:
                url = f"{base_url}tv/{tmdb}/alternative_titles?api_key={api_key}"

            response = client.request(url, output="json")

            if content == "movie":
                titles = response.get("titles", [])
            else:
                titles = response.get("results", [])

            aliases = []
            added_titles = set()

            for title in titles:
                key = title["title"]
                if key not in added_titles:
                    aliases.append(
                        {
                            "title": title["title"],
                            "country": title["iso_3166_1"].lower(),
                        }
                    )
                    added_titles.add(key)

            # Usuwanie duplikatów
            reduce_dupes = {i["title"]: i for i in aliases}
            aliases = [i for i in reduce_dupes.values()]

            return aliases
        except Exception as e:
            print(f"Wystąpił błąd: {e}")
            return []

    def _getPrimaryLang(self):
        fflog("_getPrimaryLang")

        langDict = {
            "English": "en",
            "German": "de",
            "German+English": "de",
            "French": "fr",
            "French+English": "fr",
            "Portuguese": "pt",
            "Portuguese+English": "pt",
            "Polish": "pl",
            "Polish+English": "pl",
            "Korean": "ko",
            "Korean+English": "ko",
            "Russian": "ru",
            "Russian+English": "ru",
            "Spanish": "es",
            "Spanish+English": "es",
            "Italian": "it",
            "Italian+English": "it",
            "Greek": "gr",
            "Greek+English": "gr",
        }
        name = settings.getString("providers.lang")
        lang = langDict.get(name)
        return lang

    def getTitle(self, title):
        fflog("getTitle")

        title = cleantitle.normalize(title)
        return title

    def getConstants(self, *, ffitem: FFItem) -> None:
        fflog("getConstants")

        self.itemProperty = f"{control.plugin_id}.container.items"
        self.metaProperty = f"{control.plugin_id}.container.meta"
        self.source_mods = scan_source_modules(ffitem=ffitem)
        if not const.dev.sources.force_all_sources:
            self.source_mods = [sm for sm in self.source_mods if settings.getBool(f"provider.{sm.name}")]
        fflog(f"getConstants scan_sources ({len(self.source_mods)})")

        self.host_list = []
        if resolveurl is not None:
            try:
                # wszyskie demeny (hosty) z wszystkich klas resolvera (z pominięciem generycznego '*'), bez duplikatów
                host_list: List[ResolverClass] = resolveurl.relevant_resolvers(order_matters=True)
                self.host_list = list(dict.fromkeys(domain.lower()
                                                    for cls in host_list
                                                    for domain in cls.domains if domain != '*'))
            except Exception:
                fflog_exc()

        self.pr_host_list = [
            "1fichier.com",
            "rapidgator.net",
            "rg.to",
            "filefactory.com",
            "nitroflare.com",
            "turbobit.net",
        ]
        self.hq_host_list = [
            "gvideo",
            "google.com",
        ]
