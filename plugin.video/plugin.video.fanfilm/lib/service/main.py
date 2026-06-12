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
import sty  # XXX XXX XXX

# from pathlib import Path
# import re
import json
# from enum import Enum
from time import monotonic
from weakref import WeakSet
from itertools import chain
from urllib.parse import urlsplit
# from pprint import pformat  # DEV LOGS
from typing import Optional, Union, Any, Tuple, List, Dict, Set, Callable, Sequence, Iterable, overload, TYPE_CHECKING
from typing_extensions import cast
from attrs import define, frozen, Factory, field

import xbmc
from xbmc import getInfoLabel, getCondVisibility, Player as XbmcPlayer, PlayList as XbmcPlayList
from xbmcgui import ListItem
from random import randint

# Some consts.
from const import const, MediaWatchedMode
from ..indexers.core import Indexer
from ..main import reset

from ..defs import MediaRef, RefType, MediaType, MainMediaType, FFRef
from ..ff import cache, control
from ..ff.threads import Thread, Event, Queue
from ..ff.trakt import trakt, ScrobbleAction
from ..ff.settings import settings, advanced_settings
from ..ff.db import state
from ..ff.db.playback import MediaPlayInfo, get_playback_item, update_track_watched_item
from ..ff.item import FFItem, FFItemDict
from ..ff.info import ffinfo
from ..ff.calendar import utc_timestamp
from ..ff.tricks import suppress, str_removeprefix
from ..ff.kotools import xsleep, get_player_item, stop_playing, KodiLibraryType, KodiRpc
from ..ff.kodidb import KodiVideoDB, KodiVideoInfo, video_db, load_strm_file
from ..ff.log_utils import fflog, fflog_exc
from ..ff.types import Params
from ..indexers.defs import VideoIds
from .exc import ExitBaseException, KodiExit
from .misc import VolatileFfid, PluginRequestInfo, TreeFolderInfo, FFState, update_playback_db_by_kodi_info
from ..indexers.lists import ListsInfo
from ..ff.debug.timing import logtime  # XXX DEBUG
from .http_server import HttpProxy
from .web_server import WebServer
from .tracking import TrackingService, tracking_service
from .tracking.trakt import TraktSync, TraktSender
from ..kolang import L
if TYPE_CHECKING:
    from .misc import LibraryAutoAddSource
    from .reload import ReloadMonitor
    from .library import Library, Batch
    from ..ff.types import JsonData
    from ..api.trakt import PlayContentType, HistoryContentType, Ids as TraktIds

# start sync on addon start (default: True), False for dev/testing (CLI)
START_SYNC_ON_START = True
# hack, for state in service mode
state.SERVICE = True
# reset some stuff on script relaod
reset()
#: skip blank
OMIT_FILE = 'tt430.mp4'


# FF plugin path
plugin_id: str = control.plugin_id
plugin_url: str = control.plugin_url


@define
class MediaNotifInfo:
    #: Real media type (movie or episode).
    type: RefType
    #: Video FFID.
    ffid: int
    #: Movie or episode IDs for query old FF links in kodi db.
    item: FFItem
    #: Movie or episode Kodi DB video info.
    kodi_info: KodiVideoInfo
    #: Trakt playback (event if not trakt credentials, will be ignored in this case)
    playback: MediaPlayInfo
    #: Denormalized ref.
    de_ref: MediaRef

    @property
    def ref(self) -> MediaRef:
        """Movie or episode ref (normalized)."""
        return self.item.ref

    @property
    def denormalized_ref(self) -> MediaRef:
        """Movie or episode ref (denormalized)."""
        return MediaRef(self.type, self.ffid)

    @property
    def history_type(self) -> HistoryContentType:
        """Movie or episode ref (normalized)."""
        # hack: movie -> movies, episode -> episodes
        return cast(HistoryContentType, f'{self.type}s')

    @property
    def ids(self) -> TraktIds:
        """Returns item ids. Convert type only, data already are OK."""
        if TYPE_CHECKING:
            return cast(TraktIds, self.item.ids)
        return self.item.ids


@frozen(kw_only=True)
class SeekInfo:
    """Seek offset and time from xbmc.Player.OnSeek notification."""
    # Seek offset – time from begining.
    offset: float
    # Total time.
    time: float
    # Playing speed factor.
    speed: float = 1

    @property
    def progress(self) -> float:  # 0 .. 100
        return 100 * self.offset / self.time if self.time > 0 else 0

    def progress_for_time(self, time: float) -> float:  # 0 .. 100
        return 100 * self.offset / time if time > 0 else 0


@frozen(kw_only=True)
class AddToLibraryRequest:
    """Info for adding to library request."""
    items: Sequence[FFRef]
    name: str
    sync: bool | None = None
    quiet: bool = False
    reload_after: bool = True   # False for intermediate chunks — skip UpdateLibrary


@define
class Work:
    name: str
    func: Callable[..., None]
    interval: int = 0
    thread: Optional[Thread] = None
    break_event: Event = Factory(Event)

    def force_call(self):
        """Force re-call cyclic function now."""
        self.break_event.set()


@define
class Works:
    #: Jobs (DB requests).
    jobs: Work | None = None
    #: Trakt sync thread object.
    trakt_sync: TraktSync | None = None
    #: All events (shared with HTTP server).
    events: Dict[str, Event] = Factory(dict)
    #: Volatile FFID.
    volatile_ffid: VolatileFfid = Factory(VolatileFfid)
    #: Folder info and history.
    folder: PluginRequestInfo = Factory(PluginRequestInfo)
    #: HTTP proxy (service) server.
    http: HttpProxy | None = None
    #: WEB server.
    web: WebServer | None = None
    #: General state virtual DB.
    state: FFState = Factory(FFState)
    #: Tracking services (trakt, ...).
    tracking_service: TrackingService = tracking_service
    #: Library service (adding items in the background).
    library: Library | None = None
    #: Library service (adding items in the background).
    library_jobs: Queue[AddToLibraryRequest] = field(factory=Queue)
    #: Methods to library auto add.
    library_auto_add: dict[LibraryAutoAddSource, Callable[[], None]] = field(factory=dict)


# All cyclic workers.
works = Works()

# Base events.
works.events['folder'] = Event()
works.events['folder'].set()  # ready by default


def hash_bar(text: Optional[str] = None, *, stack_depth: int = 1) -> None:
    """Log ##### bar with text in the center."""
    width = 63
    if text is None:
        fflog("#" * width, stack_depth=stack_depth+1)
    else:
        text = f" {text} "
        fflog(f"{text:#^{width}}", stack_depth=stack_depth+1)


def hash_box(lines: List[str], *, ff: bool = False, stack_depth: int = 1) -> None:
    """Log text in ##### box. If `ff` is True, log "FANFILM" in header. """
    hash_bar("FANFILM" if ff else None, stack_depth=stack_depth+1)
    for line in lines:
        hash_bar(line, stack_depth=stack_depth+1)
    hash_bar(None, stack_depth=stack_depth+1)


def hash_bar_next(settting_name: str, title: str = '', stack_depth: int = 1) -> None:
    """Log update info, plus next in H hours."""
    hours = settings.getInt(settting_name)
    hash_bar(f"{title or settting_name} UPDATE - NEXT ON {hours} HOURS", stack_depth=stack_depth+1)


def cyclic_call(interval: Union[int, Tuple[int, int]], func: Callable[..., None], *args: Any, **kwargs) -> Optional[Work]:
    def calling():
        interval = first_interval
        try:
            while not monitor.abortRequested():
                if const.debug.autoreload:
                    from .reload import ReloadMonitor
                    if ReloadMonitor.reloading:
                        break
                xsleep(interval, cancel_event=work.break_event)
                settings.delete()
                func(*args, **kwargs)
                interval = next_interval
                if not interval:
                    return
        except ExitBaseException:
            return
        except Exception:
            fflog_exc()
            raise

    if not interval:
        return None
    if isinstance(interval, Sequence):
        first_interval, next_interval = interval
    else:
        first_interval = next_interval = interval
    thread = Thread(target=calling)
    thread.name = f'{thread.name}: {func.__qualname__}'
    work = Work(thread=thread, func=func, name=func.__qualname__, interval=next_interval)
    thread.start()
    threads.add(thread)
    return work


@define
class SkipUpdate:
    """Info what to skip."""
    #: skip is valid until `timestamp'
    timestamp: float = 0
    #: Parent ref of skipped media.
    parent: Optional[MediaRef] = None
    #: Parent ref of skipped media.
    refs: Set[MediaRef] = Factory(set)
    #: Is playcount incremented?
    playcount: bool = False

    def __bool__(self) -> bool:
        return bool(self.refs)


class FFMonitor(xbmc.Monitor):
    """FF monitor."""

    VTYPES: Dict[str, RefType] = {
        'tvshow': 'show',
    }

    def __init__(self, *, selfcheck: bool = True):
        super().__init__()
        self.selfcheck: bool = bool(selfcheck)
        self._indexer: Optional[Indexer] = None
        self.reload_monitor: Optional['ReloadMonitor'] = None
        self.kodidb: KodiVideoDB = video_db
        #: Plugin needs refresh (run update)
        self.need_refresh: bool = False
        #: Plugin needs refresh (run update) - for sure.
        self.force_refresh: bool = False
        #: True if working, else exit from monitor loop.
        self.working: bool = True
        #: Player state dict.
        self.player_info: Dict[str, Any] = {}
        #: Playing now media denormalized reference (FFID).
        self.playing_media_de_ref: Optional[MediaRef] = None
        #: Full ff-item of playing media
        self.playing_ffitem: Optional[FFItem] = None
        #: All  itms needed to support playing media progress.
        self.playing_items: Optional[FFItemDict] = None
        #: What to skip and when.
        self.skip_updates: SkipUpdate = SkipUpdate()
        #: Current playing Kodi ListItem. Set on OnPlay, remove after OnStop.
        self.playing_item: Optional[ListItem] = None
        #: True, if library is scanning.
        self.lib_scanning: bool = False
        #: JsonRPC client for Kodi RPC calls.
        self.rpc = KodiRpc()

        # XXX
        self._cnt = 0

    def run(self) -> None:
        while self.working and not self.abortRequested():
            # fflog(f'FFMonitor.run... ans={service.ans}')
            # state.add_job('plugin', 'dupa', ('blada', 'ciemna'), sender='service')
            interval = const.tune.service.check_interval
            if self.waitForAbort(interval):
                break
            if self.selfcheck and self.reload_monitor:
                self.reload_monitor.check()  # raises ReloadExit on any change
        raise KodiExit()

    @property
    def indexer(self) -> Indexer:
        if self._indexer is None:
            self._indexer = Indexer()
        return self._indexer

        # --- start from beginning (old code) ---
        # Info.OnChanged          None
        # Player.OnPlay           {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1}}
        # Player.OnAVChange       {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1}}
        # Player.OnSeek           {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1,
        #                             'seekoffset': {'hours': 0, 'milliseconds': 0, 'minutes': 10, 'seconds': 0},
        #                             'time': {'hours': 0, 'milliseconds': 470, 'minutes': 10, 'seconds': 5}}}
        # <FF enter>
        # VideoLibrary.OnUpdate  {'item': {'id': 170244501, 'type': 'movie'}}
        # VideoLibrary.OnUpdate  {'id': 170244501, 'type': 'movie'}
        # Player.OnStop          {'end': False, 'item': {'id': 170244501, 'type': 'movie'}}

        # --- resume (kodi) ---
        # <ff enter>             action=play&imdb=tt17024450&tmdb=926393
        # Playlist.OnAdd         {'item': {'id': 170244501, 'type': 'movie'}, 'playlistid': 1, 'position': 0}
        # <ff exit>
        # Info.OnChanged         None
        # Player.OnPlay          {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1}}
        # Player.OnAVChange      {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1}}
        # Player.OnSeek          {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1,
        #                            'seekoffset': {'hours': 0, 'milliseconds': 0, 'minutes': 10, 'seconds': 0},
        #                            'time': {'hours': 0, 'milliseconds': 149, 'minutes': 32, 'seconds': 18}}}
        # VideoLibrary.OnUpdate  {'item': {'id': 170244501, 'type': 'movie'}}
        # VideoLibrary.OnUpdate  {'id': 170244501, 'type': 'movie'}
        # Player.OnStop          {'end': False, 'item': {'id': 170244501, 'type': 'movie'}}
        # <ff enter>             action=movies&url=tmdb_popular

        # --- to the end (no user action at all)
        # ...
        # <ff enter>             action=movies&url=tmdb_popular
        # VideoLibrary.OnUpdate  {'item': {'id': 170244501, 'type': 'movie'}, 'playcount': 2}
        # VideoLibrary.OnUpdate  {'id': 170244501, 'type': 'movie'}
        # Player.OnStop         {'end': True, 'item': {'id': 170244501, 'type': 'movie'}}

        # --- cancel sources ---
        # Player.OnStop          {'end': True, 'item': {'id': 170244501, 'type': 'movie'}}
        # Info.OnChanged         None
        # Player.OnPlay          {'item': {'id': 170244501, 'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1}}

        # --- pure video file ---
        # Playlist.OnAdd'        {'item': {'title': 'Watch True Lies (1994) Full Movie Online Free  Movie  TV Online HD Quality.mp4',
        #                         'type': 'movie'}, 'playlistid': 1, 'position': 0}
        # Player.OnPlay          {'item': {'title': 'Watch True Lies (1994) Full Movie Online Free  Movie  TV Online HD Quality.mp4',
        #                         'type': 'movie'}, 'player': {'playerid': 1, 'speed': 1}}
        # Player.OnAVChange x2, Player.OnAVStart ...
        # VideoLibrary.OnUpdate  {'id': -1, 'type': ''}
        # Player.OnStop          {'end': True, 'item': {'title': 'smediaPL--Most nad Sundem S02E07 Lektor PL.[CDA:12827975cd].mp4', 'type': 'movie'}}

        # --- set as watched ---
        # VideoLibrary.OnUpdate  {'item': {'id': 182713481, 'type': 'episode'}, 'playcount': 1}

        # --- set as unwatched ---
        # VideoLibrary.OnUpdate  {'item': {'id': 182713481, 'type': 'episode'}, 'playcount': 0}

        # --- reset progress  ---
        # VideoLibrary.OnUpdate  ...

        # --- others ---
        # System_OnSleep         None
        # System_OnWake          None

        # --- widget ---
        # Player.OnPlay          {'item': {'type': 'unknown'}, 'player': {'playerid': -1, 'speed': 1}}

        # ---
        # Info.OnChanged    - called on CGUIInfoManager::SetCurrentItem, when ListItem info changed?

        # Logika
        # ------
        # przed  Trakt | 0 ½ | 0 e | 0 ½ | 0 e | 1 ½ | 1 e | 1 ½ | 1 e |
        #         Kodi | 0 ½ | 0 e | 1 ½ | 1 e | 0 ½ | 0 e | 1 ½ | 1 e |
        #       folder | 0         | 1         | 1         | 1         |
        # -------------+-----+-----+-----+-----+-----+-----+-----+-----+
        # po   db Kodi | 0 D | 1 _ | 0 D | 1 _ | 0 D | 1 _ | 2 D | 2 _ |
        #        Trakt | 0 D | 1 _ | 1 D | 1 _ | 1 _ | 1 _ | 1 D | 1 _ |

    def onScanStarted(self, library: str) -> None:
        """The video or music library scan has been started, called by Kodi."""
        lib = cast(KodiLibraryType, library)
        if lib == 'video':
            self.lib_scanning = True

    def onScanFinished(self, library: str) -> None:
        """The video or music library has been scanned, called by Kodi."""
        lib = cast(KodiLibraryType, library)
        if lib == 'video':
            self.lib_scanning = False

    # @suppress(Exception, log_traceback=True)
    def onNotification(self, sender: str, method: str, data: str) -> None:
        """Notification, called by Kodi."""
        self._cnt += 1
        cnt = self._cnt
        if sender == 'xbmc' and method == 'VideoLibrary.OnUpdate' and data and json.loads(data).get('transaction') is True:
            fflog(f'--- [{cnt:2d} / enter / exit : mass library update')
            return
        fflog(f'--- [{cnt:2d} / enter]')
        self.player_info = state.get_all(module='player')
        if const.debug.service_notifications:
            from .dev import spy
            spy.onNotification(sender, method, data)

        # works.events['folder'].clear()
        # fflog('[FOLDER] hold')
        _PLR = '\n'.join(f'  {k!r}: {v!r}' for k, v in self.player_info.items())
        fflog.debug(f' >> player_info = {{\n{_PLR}\n  }}')

        # skip updates no longer valid
        if self.skip_updates.parent and self.skip_updates.timestamp < monotonic():
            self.skip_updates.parent = None
            self.skip_updates.refs.clear()
            self.skip_updates.timestamp = 0

        # XXX DEBUG
        fflog(f'[SERVICE][PLAYING] {xbmc.Player().isPlaying()=}')
        if xbmc.Player().isPlaying():
            fflog(f'[SERVICE][PLAYING] {xbmc.Player().getPlayingFile()=}')

        try:
            if sender == plugin_id:
                self.handle_fanfilm(method, data)
            else:
                self.handle_notification(sender, method, data)
            fflog(f'--- [{cnt:2d} / exit]')
        except Exception:
            fflog_exc()
        except BaseException:
            fflog_exc()
            raise
        finally:
            # self.folder_ready()
            try:
                fflog(f'--- [{cnt:2d} / fin] folder_ready={works.events["folder"].is_set()}')
            except KeyError:
                fflog('works.events["folder"] MISSING !!!')

    def handle_fanfilm(self, method: str, data_str: str) -> None:
        """Handle FanFilm specific notifications."""
        method = str_removeprefix(method, 'Other.')
        data: JsonData = json.loads(data_str or '{}') or {}
        mid = data.get(KodiRpc.MESSAGE_ID_NAME, 0)

        if method == 'ServicePing':
            self.rpc.notify('ServicePong', data, id=mid, action='response')

    def handle_notification(self, sender: str, method: str, data_str: str) -> None:
        data: JsonData = json.loads(data_str or '{}') or {}
        # rx = re.compile(r'\W+')
        # notif: str = f'{rx.sub("_", sender)}__{rx.sub("_", method)}'
        notif = f'{sender}.{method}'

        def playing_empty() -> bool:
            """True if empty file is really playing."""
            player = XbmcPlayer()
            return player.isPlaying() and bool(path := player.getPlayingFile()) and OMIT_FILE in path

        # kodi playing ListItem.
        def dump_item(it: Optional[ListItem]) -> None:
            if it is None:
                fflog(f'[SERVICE] {notif} playing_item: None')
                return
            v = it.getVideoInfoTag()
            imdb = v.getIMDBNumber() or v.getUniqueID('imdb')
            tmdb = v.getUniqueID('tmdb')
            resume = f'{v.getResumeTime()} / {v.getResumeTimeTotal()}'
            fflog(f'[SERVICE] {notif} playing_item: {v.getIMDBNumber()=}, {imdb=}, {tmdb=}, {v.getDbId()=}, {v.getEpisode()=}, {it.getLabel()=}, {it.getPath()=}, {resume=}')
        #
        if notif in ('xbmc.Player.OnPlay', 'xbmc.Player.OnResume', 'xbmc.Playlist.OnAdd'):
            self.playing_item = get_player_item()
        dump_item(get_player_item())

        # guess, what happened
        should_playing: bool = self.player_info.get('playing.video', False)
        is_playing_now: bool = getCondVisibility('Player.Playing')
        try:
            if self.player_info.get('playing.empty') or playing_empty():
                self.handle_skip_playing(notif, data)
            elif self.player_info.get('playing.run'):
                self.handle_ff_play(notif, data)
            elif notif == 'xbmc.VideoLibrary.OnUpdate' and not data.get('added') and not is_playing_now and not should_playing:
                self.handle_library_watched(notif, data)
            elif (notif == 'xbmc.VideoLibrary.OnUpdate' and not self.player_info.get('state')
                  and isinstance((dbid := data.get('id')), int) and dbid > 0 and dbid not in VideoIds.KODI_DBID):
                self.handle_watched(notif, data)
            else:
                self.handle_other(notif, data)
        finally:
            # clear current playing item
            if notif == 'xbmc.Player.OnStop' or (notif == 'xbmc.Playlist.OnClear' and not is_playing_now):
                self.playing_item = None

    def handle_skip_playing(self, notif: str, data: JsonData) -> None:
        """Handle skip FF play (close sources window)."""
        fflog(f'[SERVICE][FFSKIP] {notif}')
        if notif == 'xbmc.Player.OnPlay':
            control.close_busy_dialog()
            stop_playing()
            self.set_state('state', 'canceling')
        elif notif == 'xbmc.Player.OnStop':
            self.clear_player_state('canceling')
            self.set_state('state', '')
        elif notif == 'xbmc.VideoLibrary.OnUpdate':
            # If video was playing... we need to finish previous video state.
            if self.player_info.get('playing.media.ref'):
                self.next_play(data=data)

    def handle_ff_play(self, notif: str, data: JsonData) -> None:
        """Handle FF3 playing (library too), all with plugin://plugin.videofanfilm[3]/..."""
        fflog(f'[SERVICE][FFPLAY] {notif}')
        if notif == 'xbmc.Player.OnPlay':
            # control.close_busy_dialog()  -- to early, the video could start a few seconds
            if info := self.item_info(data):
                self.set_state((
                    ('playing.progress', info.kodi_info.percent),
                    ('playing.play_count', info.kodi_info.play_count),
                ))
            else:
                self.set_state((
                    ('playing.progress', 0),
                    ('playing.play_count', 0),
                ))
        elif notif == 'xbmc.Player.OnAVStart':
            control.close_busy_dialog()
            info = self.item_info(data)
            self.send_playing_state(info=info)
            self.set_state((
                ('playing.video', True),
                ('playing.path', getInfoLabel('Player.FilenameAndPath')),
                ('playing.last', None),
                ('playing.next', None),
                *((f'playing.{k}', v) for k, v in self.player_info.items() if k.startswith('media.')),  # copy media.* to playing.media.*
                ('state', 'playing'),
            ))
            if info and info.type == 'episode' and not getInfoLabel('Player.FilenameAndPath').lower().endswith('.strm') and (media_ref := self.player_info.get('media.ref')):
                from ..ff.upnext import signal_upnext
                signal_upnext(current_item=info.item, ref=media_ref)
        elif notif == 'xbmc.VideoLibrary.OnUpdate':
            # If video should be playing...
            if self.player_info.get('playing.video'):
                if XbmcPlayer().isPlaying():
                    # Still playing buf video is changed (more items on playlist, upnext etc...).
                    if (old_path := self.player_info.get('playing.path')) and old_path != (new_path := getInfoLabel('Player.FilenameAndPath')):
                        self.next_play(data=data)
                else:
                    # FF video should be playing but player has stop. It's playing finish labeled as "state = played".
                    self.set_state('state', 'played')
        elif notif == 'xbmc.Player.OnStop':
            is_playing: bool = self.player_info.get('playing.video', False)
            if not is_playing:
                # if the video didn't start at all (there was no Player.OnAVStart)
                control.close_busy_dialog()
            self.clear_player_state()
            if is_playing:
                # real successful playing (was on Player.OnAVStart)
                info = self.item_info(data)
                self.send_played_state(info=info)
            else:
                fflog('[SERVICE] playing failed (no Player.OnAVStart)')
            self.set_state('state', '')
            self.folder_ready()
        elif notif == 'xbmc.Player.OnSeek':
            info = self.item_info(data)
            seek = self.seek_info(data)
            fflog(f'[SERVICE] {info=}')
            self.send_playing_state(info=info, seek=seek)
        else:
            fflog(f'[SERVICE] unsupported play event, {notif=}, {data=}')

    def next_play(self, *, data: JsonData) -> bool:
        """Handle next play. Still playing buf video is changed (more items on playlist, upnext etc...)."""
        item: Dict[str, Any] = data.get('item', data)
        vtype: str = item.get('type', '')
        dbid: int = item.get('id', 0)
        old_path = self.player_info.get('playing.path')
        new_path = getInfoLabel('Player.FilenameAndPath')
        if vtype in ('movie', 'episode') and old_path and old_path != new_path:
            playing_dbid: int = int(getInfoLabel('VideoPlayer.DBID') or 0)
            new_url: str = ''
            if new_path.lower().endswith('.strm'):
                try:
                    new_url = load_strm_file(new_path)
                except (OSError, RuntimeError):
                    pass
            fflog(f'[SERVICE][NEXT] Playing {vtype} video changed from {old_path!r} to {new_path!r} ({new_url!r}), dbid {dbid} → {playing_dbid}')
            self.set_state((
                ('playing.path', new_path),
                ('playing.last', {
                    'dbid': dbid,
                    'ref': self.player_info.get('playing.media.ref'),
                    'tmdb': self.player_info.get('playing.media.tmdb'),
                    'imdb': self.player_info.get('playing.media.imdb'),
                }),
                ('playing.next', {
                    'dbid': playing_dbid,
                    'ref': None,
                    'tmdb': getInfoLabel('VideoPlayer.UniqueID(tmdb)'),
                    'imdb': getInfoLabel('VideoPlayer.UniqueID(imdb)'),
                }),
                ('state', 'play next'),
            ))
            info = self.item_info(data, player_info_prefix='playing.media')
            fflog(f'[SERVICE][NEXT] {info=}')
            self.send_played_state(info=info)
        return False

    def handle_watched(self, notif: str, data: JsonData) -> None:
        fflog(f'[SERVICE][WATCHED] {notif}')
        if notif == 'xbmc.VideoLibrary.OnUpdate':
            old_state = self.player_info.get('state')
            try:
                self.set_state('state', 'watching')
                # direct FF item set (un)watched
                if (li := get_player_item()) and (ref := MediaRef.from_slash_string(li.getVideoInfoTag().getUniqueID('ffref'))):
                    pass
                # FFID is set as DBID (and it is not internal kodi DBID), we can recover what ref is used.
                elif (vtype := data.get('type')) and isinstance((dbid := data.get('id')), int) and dbid > 0 and dbid not in VideoIds.KODI_DBID:
                    # TODO: implement it?
                    # should not hit here, shound call handle_library_watched()
                    fflog(f'[SERVICE] set (un)watch from OnUpdate DBID {vtype=}, {dbid=}  [NOT IMPLEMENTED]')
                    ...
                    return
                else:
                    fflog('[SERVICE] unsupported xbmc.VideoLibrary.OnUpdate')
                    return
                if info := self.item_info(data, ref=ref):
                    if self.skip_updates and info.ref in self.skip_updates.refs and bool(info.playback.play_count) == self.skip_updates.playcount:
                        fflog(f'[SERVICE] skip update {info.ref} because {self.skip_updates.parent}')
                        return

                    kodi_playcount = data.get('playcount')
                    fflog(f'{sty.fg.green}[SERVICE]{sty.rs.all}: {ref=}, {kodi_playcount=}, {info.kodi_info.play_count=}  ~~~')
                    if kodi_playcount is None:
                        kodi_playcount = info.kodi_info.play_count
            finally:
                self.set_state('state', old_state)

    def handle_library_watched(self, notif: str, data: JsonData) -> None:
        """On set (un)watched on library item. Support ff-links and alien links. Also called for FF3 if FAKE_DBID."""
        fflog(f'[SERVICE][LIBRARY] {notif}')
        if notif == 'xbmc.VideoLibrary.OnUpdate':
            item: Dict[str, Any] = data.get('item', data)
            vtype: str = item.get('type', '')
            dbid: int = item.get('id', 0)
            fflog(f'[SERVICE] Look watched for {vtype=}, {dbid=}')
            if dbid in VideoIds.KODI_DBID and vtype in ('movie', 'episode'):  # this is the library
                if kodi_vid_info := video_db.get_play_by_kodi_id(vtype, dbid):
                    if self.is_ff_link(kodi_vid_info.fname):
                        fflog(f'[SERVICE] Found watched {kodi_vid_info=}')
                        update_playback_db_by_kodi_info(kodi_vid_info)
                    elif self.is_alien_enabled(kodi_vid_info.fname):
                        if info := self.item_info(data, kodi_vid_info=kodi_vid_info):
                            # TODO: handle alien library videos
                            fflog(f'[SERVICE] library set (un)watch from OnUpdate DBID {vtype=}, {dbid=}  [NOT IMPLEMENTED]')
                            ...
            elif dbid > 0 and const.core.media_watched_mode is MediaWatchedMode.FAKE_DBID:
                # TODO: handle old (dbid) watch detector (is this necessary?)
                ...

    def handle_other(self, notif: str, data: JsonData) -> None:
        """Handle all other events (non-FF3)."""
        fflog(f'[SERVICE][OTHER] event {notif}: {data=}')
        if notif in ('xbmc.Player.OnAVStart', 'xbmc.Player.OnSeek', 'xbmc.Player.OnStop'):
            self.handle_alien(notif, data)

    def handle_alien(self, notif: str, data: JsonData) -> None:
        """Handle alien events (kodi and other plugins video events)."""
        path = getInfoLabel('Player.FilenameAndPath') or getInfoLabel('ListItem.FilenameAndPath')
        if self.is_alien_enabled(path):
            imdb = getInfoLabel('VideoPlayer.IMDBNumber') or getInfoLabel('ListItem.IMDBNumber')
            dbid = getInfoLabel('VideoPlayer.DBID') or getInfoLabel('ListItem.DBID')
            progress = getInfoLabel('Player.Progress') or getInfoLabel('ListItem.PercentPlayed')
            info = self.item_info(data)
            url = urlsplit(path)
            if url.scheme == 'plugin':
                fflog(f'[SERVICE][ALIEN] found {notif} event for alien plugin {url.hostname!r}, {imdb=}, {dbid=}, {progress=}, {info=}')
            else:
                fflog(f'[SERVICE][ALIEN] found {notif} event for alien scheme {url.scheme!r}, {imdb=}, {dbid=}, {progress=}, {info=}')
            # --- handle alien notifications
            return  # TODO:  implement it

    def is_ff_link(self, path: str) -> bool:
        """Return True if FF link."""
        if '://' not in path and path.lower().endswith('.strm'):
            path = load_strm_file(path)
        return path.startswith(plugin_url)

    def is_alien_enabled(self, path: str) -> bool:
        """Return True if support path is enabled (local or alien plugins)."""
        url = urlsplit(path)
        sync = const.trakt.sync.alien.scheme.get(url.scheme or 'file', const.trakt.sync.alien.default)
        if url.scheme == 'plugin':
            if url.hostname == plugin_id:  # this is not an alient, this is the FF3
                return True
            sync = const.trakt.sync.alien.plugins.get(url.hostname or '', sync)
        return sync

    def item_info(self,
                  data: JsonData,
                  *,
                  player_info_prefix: str = 'media',
                  ref: Optional[MediaRef] = None,
                  kodi_vid_info: Optional[KodiVideoInfo] = None,
                  ) -> Optional[MediaNotifInfo]:
        """Returns full video info, based on state[player], kodi db, trakt db, notification json."""
        ffitem: Optional[FFItem] = None
        pb: Optional[MediaPlayInfo] = None

        item: Dict[str, Any] = data.get('item', data)
        de_ref = MediaRef(item.get('type', ''), item.get('id', 0))
        log_msg = ''
        # if (itype := item.get('type')) and (iid := item.get('id')):
        #     de_ref = MediaRef(itype, iid)
        if ref is not None:
            log_msg = 'with given ref'
        elif ref := self.player_info.get(f'{player_info_prefix}.ref'):
            log_msg = 'from "media.ref"'
        elif (li := get_player_item()) and (ref := MediaRef.from_slash_string(li.getVideoInfoTag().getUniqueID('ffref'))):
            log_msg = 'from "ffref"'
        else:
            fflog(f'[SERVICE] the is no REF at all, {item=}')
            return None
        # find more info
        ffitem = ffinfo.find_item(ref, progress=ffinfo.Progress.NO)
        fflog(f'[SERVICE] item info {log_msg}: {ref=}, {ffitem=}')
        # info from kodi MyVideos DB for VideoLibrary (DBID)
        if kodi_vid_info is None and de_ref.type in ('movie', 'episode') and de_ref.ffid in VideoIds.KODI_DBID:
            kodi_vid_info = video_db.get_play_by_kodi_id(de_ref.type, de_ref.ffid)
        # info from kodi MyVideos DB
        if kodi_vid_info is None and (media_url := self.player_info.get(f'{player_info_prefix}.url')):
            kodi_vid_info = video_db.get_play_by_url(media_url)
            if ffitem is None and kodi_vid_info is not None and kodi_vid_info.ref:
                ffitem = ffinfo.find_item(kodi_vid_info.ref, progress=ffinfo.Progress.NO)
                if ffitem:
                    fflog(f'[SERVICE] recovered from kodi db: {ffitem=}')
        if kodi_vid_info is None:
            kodi_vid_info = KodiVideoInfo(ref=ref)  # empty video info (no progress)
        if ffitem is None:
            fflog(f'[SERVICE] no ffitem, {ref=}, {item=}')
            return None
        # direct trakt playback
        if (pb := get_playback_item(ref)) is None:
            pb = MediaPlayInfo.from_ffitem(ffitem)
            if not kodi_vid_info.fake:
                pb.play_count = kodi_vid_info.play_count
                pb.progress = kodi_vid_info.percent
        # super info object
        return MediaNotifInfo(type=item.get('type', ref.real_type), ffid=ref.ffid,
                              item=ffitem, kodi_info=kodi_vid_info, playback=pb, de_ref=de_ref)

    def seek_info(self, data: JsonData) -> Optional[SeekInfo]:
        """Returns seek info (percent progress)."""
        def get_time(d: JsonData) -> Optional[float]:
            try:
                h, m, s, x = d['hours'], d['minutes'], d['seconds'], d.get('milliseconds', 0)
            except KeyError:
                return None
            return h * 3600 + m * 60 + s + x / 1000

        if player_data := data.get('player', {}):
            offset = get_time(player_data.get('seekoffset', {}))
            time = get_time(player_data.get('time', {}))
            speed = player_data.get('speed', 1)
            if offset is not None and time is not None:
                return SeekInfo(offset=offset, time=time, speed=speed)
        return None

    def clear_player_state(self, state: str = 'stopping'):
        self.set_state((
            ('playing.empty', False),
            ('playing.run', False),
            ('playing.video', False),
            ('playing.path', ''),
            ('playing.last', None),
            ('playing.next', None),
            *((k, None) for k in self.player_info if k.startswith('playing.media.')),  # clear playing.media.*
            ('cancel_mode', None),
            ('state', state),
        ))

    def send_playing_state(self, *, info: Optional[MediaNotifInfo], seek: Optional[SeekInfo] = None) -> None:
        """Send current playing state (scrobble: start)."""
        fflog(f'[SERVICE] playing state {info=}, {seek=}')
        if info is None:
            fflog.warning('[SERVICE] can NOT set playing state, there is no info')
            return
        # Seek playing video, just update "play srobble" progress (from seek notification).
        if seek:
            # Kodi SeekInfo video time could be corrupted, get offsent without total time
            time = max(info.item.duration or 0, info.playback.duration or 0, info.kodi_info.total_s, seek.time)
            self.scrobble(info, 'start', progress=seek.progress_for_time(time))
        # Just playing (start or resume), update "play srobble" video progress (from trakt, db or kodi).
        else:
            self.scrobble(info, 'start')

    def send_played_state(self, *, info: Optional[MediaNotifInfo]) -> None:
        """Send played state, current playing just stops (sroblle: pause, stop)."""
        fflog(f'[SERVICE] played state {info=}')
        if info is None:
            fflog.warning('[SERVICE] can NOT set played state, there is no info')
            return
        old_play_count = self.player_info.get('playing.play_count') or 0
        # Paused in the middle (has progress and is not at the end).
        if (info.kodi_info.has_progress and info.kodi_info.percent
                and info.kodi_info.percent < advanced_settings.get('video', 'playcountminimumpercent', default=const.media.progress.as_watched)):
            info.playback.progress = info.kodi_info.percent
            self.scrobble(info, 'pause')
        # Finish watching (no progress and play_count is incremented).
        elif info.kodi_info.play_count == old_play_count + 1:
            info.playback.play_count = info.kodi_info.play_count
            info.playback.clear_progress()
            self.scrobble(info, 'stop', 100)
            self.just_watched(info, first_time=not old_play_count)
        # WTF? I don't know what happens. Maybe stop at video beginning?
        else:
            self.scrobble(info, 'pause')

    @overload
    def set_state(self, key: str, value: Any) -> None: ...

    @overload
    def set_state(self, values: Iterable[Tuple[str, Any]], /) -> None: ...

    def set_state(self, key: Any, value: Any = None) -> None:
        """Set player state."""
        if isinstance(key, str):
            fflog(f'[SERVICE] set state {(key, value)}')
            self.player_info[key] = value
            state.set(key, value, module='player')
        else:
            values = tuple(key)  # multiset
            fflog(f'[SERVICE] set state {values}')
            self.player_info.update(values)
            state.multi_set(values, module='player')

    def folder_ready(self):
        """Mark folder as ready to show."""
        # if works.events['folder'].is_set():
        #     fflog('[FOLDER] already ready')
        works.events['folder'].set()

    @logtime
    def update_db(self, video: MediaNotifInfo) -> bool:
        """Set playback in DB and mark folder ready."""
        if result := update_track_watched_item(video.playback, info=self.playing_items):
            if trakt.credentials():
                trakt.updated()
            # stored = get_playback_item(video.ref)  # DEBUG only
            # fflog(f'{video.playback.progress}%, x{video.playback.play_count}, {result=}, {video.playback=}, {video.playback._db_values=}, {stored=}')
            fflog(f'{video.playback.progress}%, x{video.playback.play_count}, {result=}, {video.playback=}, {video.playback._db_values=}')
            # self.need_refresh = True  # only for make sure (trakt should have the same data as DB have)
        else:
            result = False
        self.folder_ready()
        return result

    def scrobble(self, info: MediaNotifInfo, action: ScrobbleAction, progress: Optional[float] = None) -> None:
        """Set trakt scrobble if progress changed. video.playback.progress must be already set."""
        if progress is None:
            progress = info.kodi_info.percent
        progress = progress or 0
        # first, set in DB
        self.update_db(info)
        # next, send to trakt
        if info and trakt.credentials():
            trakt.scrobble_ref(action, info.ref, progress, db_save=False)  # DB is updated already
        if const.debug.tty:
            fflog(f'[SERVICE] scrobble: {sty.fg.cyan} {action} {progress:.1f}% {sty.rs.all}')
        else:
            fflog(f'[SERVICE] scrobble: {action} {progress:.1f}%')
        self.folder_ready()

    def set_watched(self, video: MediaNotifInfo, play_count: int) -> None:
        """Set trakt watched plays. NOT USED ANYMORE."""
        fflog.error('[SERVICE] set_watched is DEPRECATED !!!')
        if video:
            if play_count:
                # first, set in DB
                video.playback.clear_progress()
                video.playback.play_count = play_count
                self.update_db(video)
                # next, send to trakt
                if trakt.credentials():
                    if video.playback.progress:
                        trakt.scrobble_ref('stop', video.ref, 100, db_save=False)  # DB is updated already
                    else:
                        trakt.add_history_ref(video.ref, db_save=False)  # DB is updated already
            else:
                # first, set in DB
                video.playback.clear_progress()
                video.playback.play_count = 0
                self.update_db(video)
                # next, send to trakt
                if trakt.credentials():
                    if video.playback.progress:
                        trakt.scrobble_ref('pause', video.ref, 0, db_save=False)  # DB is updated already
                    trakt.remove_history_ref(video.ref, db_save=False)  # DB is updated already
            if const.debug.tty:
                fflog(f'[SERVICE] plays: {sty.fg.cyan} set {play_count or 0} {sty.rs.all}')
            else:
                fflog(f'[SERVICE] plays:  set {play_count or 0}')
            # self.need_refresh = True
        # else:
        #     self.folder_ready()

    def onSettingsChanged(self):
        fflog('[SERVICE]')
        L.reset()
        settings.reset()  # force recreate settings proxy
        ffinfo.reset()
        # detect library settings change and start thread if needed
        library_enabled = settings.getBool('enable_library')
        if library_enabled and works.library is None:
            from .library import Library  # noqa: F811
            works.library = Library()
            works.library.start()
        elif not library_enabled and works.library is not None:
            library, works.library = works.library, None
            library.stop()

    def just_watched(self, video: MediaNotifInfo, *, first_time: bool) -> None:
        """Handle watch video finished."""
        fflog(f'[SERVICE] just watched: {video.ref}, first time: {first_time}')
        if const.ratings.every_watched or first_time:
            ffitem = video.item
            main_type = video.ref.type
            if main_type in ('movie', 'show') and settings.getBool(f'rate_after_watch.{main_type}'):
                from ..ff.ratings import rate_media
                if main_type == 'movie':
                    rate_media(video.item, action='watched_movie')
                else:
                    for mode in const.ratings.watched_show_mode:
                        if mode == 'show':
                            ref = ffitem.show_item or video.ref.show_ref
                        elif mode == 'season':
                            ref = ffitem.season_item or video.ref.season_ref
                        elif mode == 'episode':
                            ref = ffitem
                        elif mode == 'show_finished':
                            ref = ffitem.show_item or video.ref.show_ref
                            if ref is None or (it := ffinfo.find_item(ref, progress=ffinfo.Progress.BASIC)) is None:
                                continue
                            if it.progress is None or it.progress.play_count < 1:
                                continue
                            ref = it
                        elif mode == 'season_finished':
                            ref = ffitem.season_item or video.ref.season_ref
                            if ref is None or (it := ffinfo.find_item(ref, progress=ffinfo.Progress.BASIC)) is None:
                                continue
                            if it.progress is None or it.progress.play_count < 1:
                                continue
                            ref = it
                        else:
                            fflog(f'[SERVICE] unsupported watched show rating mode: {mode!r}')
                            continue
                        assert ref is not None
                        rate_media(ref, action='watched_episode', if_not_rated=not const.ratings.every_watched)


def cacheCleanSourcesList():
    cache.cache_clear_sources()
    hash_bar("Sources list has been cleaned")


def service_jobs():
    for job in state.jobs('service'):
        if job.command == 'trakt.sync':
            if works.trakt_sync:
                works.trakt_sync.sync_start()
            else:
                fflog('[WARNING] cyclic trakt_playback_sync() is NOT running, force sync directly')
        elif job.command in ('refresh', 'update'):
            # push job to plugin
            state.add_job('plugin', job.command, args=job.args, sender=job.sender)


def process_library_batches():
    """Process library batches for adding to library."""
    fflog(f'Add to library worker ({"up" if works.library else "passive"})')
    while not monitor.abortRequested():
        try:
            req = works.library_jobs.get()
        except ExitBaseException:
            break
        fflog(f'Process add to library chunk: {req.name!r} ({len(req.items)})')
        if works.library is None:
            fflog(f'[LIBRARY] library is disabled, skip processing batch {req.name!r}')
        else:
            works.library.add(req.items, name=req.name, quiet=req.quiet, sync=req.sync)
        works.library_jobs.task_done()
        xsleep(const.library.service.chunk_sleep)


def add_to_library(name: str, items: Iterable[FFRef], *, quiet: bool = False) -> None:
    """Add library batch for processing. Split into chunks."""
    if works.library is None:
        return
    items = tuple(items)
    chunk_size = const.library.service.chunk_size
    if not chunk_size:
        chunk_size = len(items)
    fflog(f'Add to library request: {name!r} {len(items)} items in {(len(items) + chunk_size - 1) // chunk_size} chunks')
    for offset in range(0, len(items), chunk_size):
        last = offset + chunk_size >= len(items)
        if last:
            req = AddToLibraryRequest(items=items[offset:], name=name, quiet=quiet)
        else:
            req = AddToLibraryRequest(items=items[offset : offset + chunk_size], name=f'{name} ({offset // chunk_size + 1})', quiet=True, sync=False)
        works.library_jobs.put_nowait(req)


def startup():
    """Initialize all startup and cyclic stuff."""
    from ..ff.control import pretty_log_info
    from ..ff.requests import cleanup_netcache

    def start_cyclic_task(func: Callable, *, setting_interval: str, setting_on_start: Optional[str] = None, title: str = '') -> None:
        hours = settings.getInt(setting_interval)
        startup = setting_on_start and settings.getBool(setting_on_start)

        if hours > 0 and startup:
            hash_box([
                f"STARTING {title} SCHEDULING",
                f"SCHEDULED TIME FRAME {hours} HOURS",
            ])

        timeout = 3600 * hours

        if startup:
            # timeout = (randint(60, 120), timeout)
            timeout = (10, timeout)  # XXX
        cyclic_call(timeout, func)

    try:
        AddonVersion = control.addon(control.plugin_id).getAddonInfo("version")
        AddonOldVersion = settings.getString("addon.version")
        if AddonOldVersion != AddonVersion:
            cache.cache_clear_all()
            hash_bar("FANFILM NOWA INSTALACJA")
        settings.setString("addon.version", AddonVersion)
        pretty_log_info()
    except Exception:
        hash_box([
            "CURRENT FANFILM VERSIONS REPORT",
            "ERROR GETTING FANFILM VERSIONS - NO HELP WILL BE GIVEN AS THIS IS NOT AN OFFICIAL FANFILM INSTALL"
        ], ff=True)

    def sync_trakt_lib():
        if ListsInfo().trakt_enabled() and works.library:
            add_to_library('auto trakt', chain(trakt.user_collection('movie'), trakt.user_collection('show')))

    def sync_tmdb_lib():
        from ..api import depaginate
        from ..ff.tmdb import tmdb
        if ListsInfo().tmdb_enabled() and works.library:
            with depaginate(tmdb) as api:
                add_to_library('auto tmdb', chain(api.user_general_lists(list_type='favorite', type='movie'),
                                                  api.user_general_lists(list_type='favorite', type='show')))

    def sync_imdb_lib():
        from ..api import depaginate
        from ..api.imdb import ImdbScraper
        if ListsInfo().imdb_enabled() and works.library:
            with depaginate(ImdbScraper()) as api:
                add_to_library('auto imdb', api.watch_list(settings.getString('imdb.user')))

    def sync_mdblist_lib():
        from ..api.mdblist import mdblist
        if ListsInfo().mdblist_enabled() and works.library:
            # TODO: add pagination support for mdblist. Now limit is 1000 items.
            add_to_library('auto mdblist', mdblist.watchlist_items())

    def sync_own_lib():
        from ..ff.ownlists import own_db
        if works.library:
            add_to_library('auto own', own_db.list(own_db.FAVORITES))

    works.library_auto_add = {
        'own': sync_own_lib,
        'trakt': sync_trakt_lib,
        'tmdb': sync_tmdb_lib,
        'imdb': sync_imdb_lib,
        'mdblist': sync_mdblist_lib,
    }

    if START_SYNC_ON_START:
        start_cyclic_task(sync_trakt_lib, setting_interval='schedTraktTime', setting_on_start='autoTraktOnStart', title='auto trakt')
        start_cyclic_task(sync_tmdb_lib, setting_interval='schedTmdbTime', setting_on_start='autoTmdbOnStart', title='auto tmdb')
        start_cyclic_task(sync_imdb_lib, setting_interval='schedIMdbTime', setting_on_start='autoIMdbOnStart', title='auto imdb')
        start_cyclic_task(sync_mdblist_lib, setting_interval='schedMdbListTime', setting_on_start='autoMdbListOnStart', title='auto mdblist')
        start_cyclic_task(sync_own_lib, setting_interval='schedOwnTime', setting_on_start='autoOwnOnStart', title='auto own')

        # Clean cached sources list
        cyclic_call(900, cacheCleanSourcesList)

        # netcache cleanup expired entries
        if const.core.netcache.cleanup.interval:
            cyclic_call(const.core.netcache.cleanup.interval, cleanup_netcache)

    if START_SYNC_ON_START:
        assert works.trakt_sync
        works.events['trakt'] = works.trakt_sync.synced
        works.trakt_sync.start()
        threads.add(works.trakt_sync)
        threads.add(th := Thread(target=process_library_batches, name='library-batches', daemon=True))
        th.start()

    works.jobs = cyclic_call(const.tune.service.job_list_sleep, service_jobs)

    # monitor started, notify all (FF plugin)
    monitor.rpc.notify('ServiceUp', id=KodiRpc.BROADCAST)


def run(reload_monitor: Optional['ReloadMonitor'] = None) -> None:
    """Run service module."""
    monitor.reload_monitor = reload_monitor

    try:
        # Trakt sync thread.
        works.trakt_sync = TraktSync()

        if START_SYNC_ON_START:
            # Tracking services (trakt sender).
            works.tracking_service.append(TraktSender())
            works.tracking_service.start()

            # Library service (adding items in the background). Start only if enabled. Settings are monitored.
            if settings.getBool('enable_library') and works.library is None:
                from .library import Library  # noqa: F811
                works.library = Library()
                works.library.start()

        proxy.start()
        if works.web:
            works.web.start()
    except Exception:
        fflog_exc()
    try:
        fflog('[FF] ### Service start-up')
        startup()
        fflog('[FF] ### Service monitor run')
        monitor.run()
    finally:
        fflog('[FF] ### Service going to stop')
        try:
            stop(join=False)
        except Exception:
            fflog_exc()


def join_threds() -> None:
    """Join all threads."""
    for th in threads:
        th.join()
    threads.clear()


def stop(*, join: bool = True) -> None:
    """Stop all services."""
    from ..ff.kotools import destroy_xmonitor
    monitor.working = False
    # fflog('[FF] stopping::1 (trakt_sync)')
    if works.trakt_sync:
        works.trakt_sync.stop()
    # fflog('[FF] stopping::2 (tracking_service)')
    if works.tracking_service is not None:
        works.tracking_service.stop()
    # fflog('[FF] stopping::3 (library)')
    if works.library is not None:
        works.library.stop()
    # fflog('[FF] stopping::4 (web)')
    if works.web:
        works.web.stop()
    # fflog('[FF] stopping::5 (http)')
    proxy.stop()
    # fflog('[FF] stopping::6 (join threads)')
    if join:
        join_threds()
    # fflog('[FF] stopping::7 (monitor)')
    destroy_xmonitor()
    fflog('[FF] ### Service stopped')
    from threading import enumerate as thread_enumerate
    fflog(f'Active service threads: {thread_enumerate()}')  # debug


if const.tune.service.startup_delay:
    fflog(f'[SERVICE] ----- start delay ({const.tune.service.startup_delay:.1f}s) -----')
    _start_monitor = xbmc.Monitor()
    _start_monitor.waitForAbort(const.tune.service.startup_delay)
    del _start_monitor

fflog('[SERVICE] ----- start service -----')


# All thread
threads = WeakSet()

# Uruchamianie serwera HTTP
proxy = works.http = HttpProxy(works=works)
# Uruchamianie serwera web
works.web = WebServer(works=works)

# Główny monitor
monitor = FFMonitor()
works.state.monitor = monitor  # istniejący monitor, aby nie tworzyć go za każdym razem
