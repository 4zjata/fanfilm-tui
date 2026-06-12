from __future__ import annotations
import sys
from pathlib import Path
import time
from typing import Union, List, Dict, Tuple, Optional, Iterable, Mapping, Callable, TYPE_CHECKING
from typing_extensions import Self, ClassVar
from attrs import define
from lib.fake.fake_tools import DeprecatedError, getsetmethods, field, Factory
from threading import Thread as _Thread
import json as _json
from enum import Enum as _Enum
from attrs import define as _define
if TYPE_CHECKING:
    from typing import Any
    import socket
    from json import JSONDecoder
    import xbmcgui
    from lib.ff.types import JsonData
from lib.fake import fake_api

__fake_kodi__: bool = True
_DEFAULT_JSONRPC_PORT: int = 9090

DRIVE_NOT_READY = 1
ENGLISH_NAME = 2
ISO_639_1 = 0
ISO_639_2 = 1
LOGDEBUG = 0
LOGERROR = 3
LOGFATAL = 4
LOGINFO = 1
LOGNONE = 5
LOGWARNING = 2
PLAYLIST_MUSIC = 0
PLAYLIST_VIDEO = 1
SERVER_AIRPLAYSERVER = 2
SERVER_EVENTSERVER = 6
SERVER_JSONRPCSERVER = 3
SERVER_UPNPRENDERER = 4
SERVER_UPNPSERVER = 5
SERVER_WEBSERVER = 1
SERVER_ZEROCONF = 7
TRAY_CLOSED_MEDIA_PRESENT = 3
TRAY_CLOSED_NO_MEDIA = 2
TRAY_OPEN = 1

_print_log_cb: Optional[Callable[[str, int], None]] = None


def _exit_kodi() -> None:
    if not Monitor._exit:
        Monitor._exit = True
        _Service.stop()


def _broadcast(sender: str, method: str, data: str) -> None:
    for monitor in tuple(Monitor._instances):
        monitor.onNotification(sender, method, data)


@_define(kw_only=True)
class _JsonRpcStream:
    """CLI xbmc JSON RPC TCP stream."""

    sock: socket.socket
    data: str = ''

    @property
    def fileno(self) -> int:
        return self.sock.fileno()

    def recv_data(self, bufsize: int = 1024 * 1024) -> None:
        self.append_data(self.sock.recv(bufsize))

    def append_data(self, data: str | bytes) -> None:
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        self.data += data

    def json_iter(self, decoder: JSONDecoder) -> Iterable[Any]:
        while True:
            self.data = self.data.lstrip()
            if not self.data:
                break
            try:
                obj, idx = decoder.raw_decode(self.data)
                yield obj
                self.data = self.data[idx:]
            except _json.JSONDecodeError:
                break


class _ServiceMode(_Enum):
    # Internal only (no network)
    INTERNAL = 0
    # Server mode, listening for incoming connections
    SERVER = 1
    # Client mode, connecting to master server (_ServiceMode or another xbmc instance)
    CLIENT = 2


class _Service(_Thread):
    """CLI xbmc JSON RPC service implementation"""

    instance: ClassVar[_Service | None] = None
    INTERNAL: ClassVar[_ServiceMode] = _ServiceMode.INTERNAL
    SERVER: ClassVar[_ServiceMode] = _ServiceMode.SERVER
    CLIENT: ClassVar[_ServiceMode] = _ServiceMode.CLIENT

    def __new__(cls, *args, mode: _ServiceMode = _ServiceMode.INTERNAL, address: tuple[str, int] | None = None, **kwargs) -> _Service:
        if cls.instance is None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self,
                 group: None = None,
                 target: Callable[..., object] | None = None,
                 name: str | None = None,
                 args: Iterable[Any] = (),
                 kwargs: Mapping[str, Any] | None = None,
                 *,
                 daemon: bool | None = None,
                 mode: _ServiceMode = _ServiceMode.INTERNAL,
                 address: tuple[str, int] | None = None,
                 ) -> None:
        if name is None:
            name = 'FakeXbmcServie'
        super().__init__(target=target, name=name, args=args, kwargs=kwargs, daemon=daemon)
        self.mode: _ServiceMode = mode
        self.address: tuple[str, int] = address or ('127.0.0.1', _DEFAULT_JSONRPC_PORT)
        self.sock: socket.socket | None = None
        self.remotes: dict[int, _JsonRpcStream] = {}
        self.json_decoder = _json.JSONDecoder()
        self.addon_id: str = ''
        self.running: bool = False

    @classmethod
    def start_service(cls, *, mode: _ServiceMode = _ServiceMode.INTERNAL, address: tuple[str, int] | None = None) -> None:
        srv = cls(mode=mode, address=address, daemon=True)
        if mode is not _ServiceMode.INTERNAL:
            srv.start()

    @classmethod
    def stop_service(cls) -> None:
        import socket
        if (self := cls.instance) and self.sock:
            self.running = False
            if self.mode is _ServiceMode.SERVER:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                        sock.send(b'null')
                except ConnectionError:
                    pass
            self.sock.shutdown(socket.SHUT_RDWR)
            self.sock.close()
            self.sock = None

    stop = stop_service

    def run(self) -> None:
        from xbmcaddon import Addon
        if not _DEFAULT_JSONRPC_PORT:
            log('Start fake xbmc service with no port, disabled', LOGERROR)
            return
        self.addon_id = Addon()._id
        if self.mode is _ServiceMode.SERVER:
            import platform
            if platform.system().lower() == 'windows':
                self._run_windows_server()
            else:
                self._run_server()
        elif self.mode is _ServiceMode.CLIENT:
            self._run_client()
        else:
            pass

    def _run_server(self):
        import socket
        import select
        self.running = True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            self.sock = sock
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            # sock.bind(('127.0.0.1', _DEFAULT_JSONRPC_PORT))
            sock.bind(self.address)
            sock.listen(1)
            sock.setblocking(False)

            epoll = select.epoll()
            epoll.register(sock.fileno(), select.EPOLLIN)
            print(f' ** Fake XMBC Service: server {self.address[0]}:{self.address[1]} ready')
            try:
                while self.running:
                    for fileno, event in epoll.poll(1):
                        # new connection
                        if fileno == sock.fileno():
                            conn, addr = sock.accept()
                            conn.setblocking(False)
                            epoll.register(conn.fileno(), select.EPOLLIN)
                            self.remotes[conn.fileno()] = _JsonRpcStream(sock=conn)
                        # new client data
                        elif event & select.EPOLLIN:
                            client = self.remotes[fileno]
                            client.recv_data()
                            for msg in client.json_iter(self.json_decoder):
                                if msg is None:
                                    return
                                try:
                                    self.jsonrpc(msg)
                                except Exception:
                                    from lib.ff.log_utils import fflog_exc
                                    fflog_exc()
                             # if EOL1 in requests[fileno] or EOL2 in requests[fileno]:
                             #       epoll.modify(fileno, select.EPOLLOUT)
                             #       print('Client sent: ' + requests[fileno].decode()[:-2])
                        # clien can send
                        # elif event & select.EPOLLOUT:
                        #     byteswritten = connections[fileno].send(responses[fileno])
                        #     responses[fileno] = responses[fileno][byteswritten:]
                        #     if len(responses[fileno]) == 0:
                        #         epoll.modify(fileno, 0)
                        #         connections[fileno].shutdown(socket.SHUT_RDWR)
                        # connection close
                        elif event & select.EPOLLHUP:
                            epoll.unregister(fileno)
                            client = self.remotes.pop(fileno)
                            client.sock.close()
            finally:
                epoll.unregister(sock.fileno())
                epoll.close()

    def _run_windows_server(self):
        import os
        print(' ** Fake XMBC Service: NOT IMPLEMENTED')
        os._exit(1)

    def _run_client(self):
        import socket
        self.running = True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            self.sock = sock
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            # sock.settimeout(5)
            while True:
                try:
                    sock.connect(self.address)
                except ConnectionError:
                    time.sleep(1)
                    continue
                print(f' ** Fake XMBC Service: client connected to {self.address[0]}:{self.address[1]}')
                # sock.sendall(''.encode('utf-8'))
                stream = _JsonRpcStream(sock=sock)
                try:
                    while chunk := sock.recv(1024 * 1024):
                        stream.append_data(chunk)
                        for msg in stream.json_iter(self.json_decoder):
                            try:
                                self.jsonrpc(msg)
                            except Exception:
                                from lib.ff.log_utils import fflog_exc
                                fflog_exc()
                except TimeoutError:
                    time.sleep(1)
                    continue
                break

    def send_to_master(self, data: JsonData | str) -> JsonData:
        import socket
        if not isinstance(data, str):
            data = _json.dumps(data)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            self.sock = sock
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.settimeout(5)
            sock.connect(self.address)
            sock.sendall(data.encode('utf-8'))
            stream = _JsonRpcStream(sock=sock)
            while chunk := sock.recv(1024 * 1024):
                stream.append_data(chunk)
                for msg in stream.json_iter(self.json_decoder):
                    return msg
            return {}

    def execute_jsonrpc(self, jsonrpccommand: str) -> str:
        if self.mode is _ServiceMode.CLIENT:
            result = self.send_to_master(jsonrpccommand)
        else:
            result = self.jsonrpc(jsonrpccommand)
        return '' if result is None else _json.dumps(result)

    def jsonrpc(self, data: JsonData | str, *, addon_id: str | None = None) -> JsonData | None:
        addon_id = self.addon_id if addon_id is None else addon_id
        if isinstance(data, str):
            try:
                data = _json.loads(data)
                if not isinstance(data, dict):
                    return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid Request'}}
            except _json.JSONDecodeError:
                return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        method: str = data['method']
        params: JsonData = data.get('params', {})
        mid = data.get('id')
        if handler := getattr(self, f'rpc_{method.replace(".", "_")}', None):
            try:
                result = handler(**params)
            except Exception as e:
                from lib.ff.log_utils import fflog_exc
                fflog_exc()
                return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -500, 'message': str(e)}}
            return {'jsonrpc': '2.0', 'id': mid, 'result': result}
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'Method not found'}}

    def rpc_JSONRPC_NotifyAll(self, *, sender: str = '???', message: str, data: JsonData) -> Any:
        method = f'Other.{message}'
        mdata = '' if data is None else _json.dumps(data)
        if self.mode is _ServiceMode.SERVER:
            response = _json.dumps({
                'jsonrpc': '2.0',
                'method': 'JSONRPC.OnNotification',
                'params': {
                    'sender': sender,
                    'method': method,
                    'data': mdata
                }
            }).encode('utf-8')
            for remote in self.remotes.values():
                remote.sock.sendall(response)
        _broadcast(sender=sender, method=method, data=mdata)
        return {}


class InfoTagGame:

    def __init__(self, offscreen: bool = False) -> None:
        pass

    def getTitle(self) -> str:
        return ""

    def getPlatform(self) -> str:
        return ""

    def getGenres(self) -> List[str]:
        return [""]

    def getPublisher(self) -> str:
        return ""

    def getDeveloper(self) -> str:
        return ""

    def getOverview(self) -> str:
        return ""

    def getYear(self) -> int:
        return 0

    def getGameClient(self) -> str:
        return ""

    def setTitle(self, title: str) -> None:
        pass

    def setPlatform(self, platform: str) -> None:
        pass

    def setGenres(self, genres: List[str]) -> None:
        pass

    def setPublisher(self, publisher: str) -> None:
        pass

    def setDeveloper(self, developer: str) -> None:
        pass

    def setOverview(self, overview: str) -> None:
        pass

    def setYear(self, year: int) -> None:
        pass

    def setGameClient(self, gameClient: str) -> None:
        pass


class InfoTagMusic:

    def __init__(self, offscreen: bool = False) -> None:
        pass

    def getDbId(self) -> int:
        return 0

    def getURL(self) -> str:
        return ""

    def getTitle(self) -> str:
        return ""

    def getMediaType(self) -> str:
        return ""

    def getArtist(self) -> str:
        return ""

    def getAlbum(self) -> str:
        return ""

    def getAlbumArtist(self) -> str:
        return ""

    def getGenre(self) -> str:
        return ""

    def getGenres(self) -> List[str]:
        return [""]

    def getDuration(self) -> int:
        return 0

    def getYear(self) -> int:
        return 0

    def getRating(self) -> int:
        return 0

    def getUserRating(self) -> int:
        return 0

    def getTrack(self) -> int:
        return 0

    def getDisc(self) -> int:
        return 0

    def getReleaseDate(self) -> str:
        return ""

    def getListeners(self) -> int:
        return 0

    def getPlayCount(self) -> int:
        return 0

    def getLastPlayed(self) -> str:
        return ""

    def getLastPlayedAsW3C(self) -> str:
        return ""

    def getComment(self) -> str:
        return ""

    def getLyrics(self) -> str:
        return ""

    def getMusicBrainzTrackID(self) -> str:
        return ""

    def getMusicBrainzArtistID(self) -> List[str]:
        return [""]

    def getMusicBrainzAlbumID(self) -> str:
        return ""

    def getMusicBrainzReleaseGroupID(self) -> str:
        return ""

    def getMusicBrainzAlbumArtistID(self) -> List[str]:
        return [""]

    def setDbId(self, dbId: int, type: str) -> None:
        pass

    def setURL(self, url: str) -> None:
        pass

    def setMediaType(self, mediaType: str) -> None:
        pass

    def setTrack(self, track: int) -> None:
        pass

    def setDisc(self, disc: int) -> None:
        pass

    def setDuration(self, duration: int) -> None:
        pass

    def setYear(self, year: int) -> None:
        pass

    def setReleaseDate(self, releaseDate: str) -> None:
        pass

    def setListeners(self, listeners: int) -> None:
        pass

    def setPlayCount(self, playcount: int) -> None:
        pass

    def setGenres(self, genres: List[str]) -> None:
        pass

    def setAlbum(self, album: str) -> None:
        pass

    def setArtist(self, artist: str) -> None:
        pass

    def setAlbumArtist(self, albumArtist: str) -> None:
        pass

    def setTitle(self, title: str) -> None:
        pass

    def setRating(self, rating: float) -> None:
        pass

    def setUserRating(self, userrating: int) -> None:
        pass

    def setLyrics(self, lyrics: str) -> None:
        pass

    def setLastPlayed(self, lastPlayed: str) -> None:
        pass

    def setMusicBrainzTrackID(self, musicBrainzTrackID: str) -> None:
        pass

    def setMusicBrainzArtistID(self, musicBrainzArtistID: List[str]) -> None:
        pass

    def setMusicBrainzAlbumID(self, musicBrainzAlbumID: str) -> None:
        pass

    def setMusicBrainzReleaseGroupID(self, musicBrainzReleaseGroupID: str) -> None:
        pass

    def setMusicBrainzAlbumArtistID(self, musicBrainzAlbumArtistID: List[str]) -> None:
        pass

    def setComment(self, comment: str) -> None:
        pass


class InfoTagPicture:

    def __init__(self, offscreen: bool = False) -> None:
        pass

    def getResolution(self) -> str:
        return ""

    def setResolution(self, width: int, height: int) -> None:
        pass

    def setDateTimeTaken(self, datetimetaken: str) -> None:
        pass


class InfoTagRadioRDS:

    def __init__(self) -> None:
        pass

    def getTitle(self) -> str:
        return ""

    def getBand(self) -> str:
        return ""

    def getArtist(self) -> str:
        return ""

    def getComposer(self) -> str:
        return ""

    def getConductor(self) -> str:
        return ""

    def getAlbum(self) -> str:
        return ""

    def getComment(self) -> str:
        return ""

    def getAlbumTrackNumber(self) -> int:
        return 0

    def getInfoNews(self) -> str:
        return ""

    def getInfoNewsLocal(self) -> str:
        return ""

    def getInfoSport(self) -> str:
        return ""

    def getInfoStock(self) -> str:
        return ""

    def getInfoWeather(self) -> str:
        return ""

    def getInfoHoroscope(self) -> str:
        return ""

    def getInfoCinema(self) -> str:
        return ""

    def getInfoLottery(self) -> str:
        return ""

    def getInfoOther(self) -> str:
        return ""

    def getEditorialStaff(self) -> str:
        return ""

    def getProgStation(self) -> str:
        return ""

    def getProgStyle(self) -> str:
        return ""

    def getProgHost(self) -> str:
        return ""

    def getProgWebsite(self) -> str:
        return ""

    def getProgNow(self) -> str:
        return ""

    def getProgNext(self) -> str:
        return ""

    def getPhoneHotline(self) -> str:
        return ""

    def getEMailHotline(self) -> str:
        return ""

    def getPhoneStudio(self) -> str:
        return ""

    def getEMailStudio(self) -> str:
        return ""

    def getSMSStudio(self) -> str:
        return ""


class Actor:

    def __new__(cls, name: str = "",
                 role: str = "",
                 order: int = -1,
                 thumbnail: str = "") -> 'Actor':
        return super().__new__(cls)

    def __init__(self, name: str = "",
                 role: str = "",
                 order: int = -1,
                 thumbnail: str = "") -> None:
        self._name: str = name
        self._role: str = role
        self._order: int = order
        self._thumbnail: str = thumbnail

    def __repr__(self) -> str:
        return f'Actor({self._name!r}, {self._role!r}, {self._order!r}, {self._thumbnail!r})'

    def getName(self) -> str:
        return self._name or ""

    def getRole(self) -> str:
        return self._role or ""

    def getOrder(self) -> int:
        return self._order or 0

    def getThumbnail(self) -> str:
        return self._thumbnail or ""

    def setName(self, name: str) -> None:
        self._name = name

    def setRole(self, role: str) -> None:
        self._role = role

    def setOrder(self, order: int) -> None:
        self._order = order

    def setThumbnail(self, thumbnail: str) -> None:
        self._thumbnail = thumbnail


class VideoStreamDetail:

    def __init__(self, width: int = 0,
                 height: int = 0,
                 aspect: float = 0.0,
                 duration: int = 0,
                 codec: str = "",
                 stereoMode: str = "",
                 language: str = "",
                 hdrType: str = "") -> None:
        pass

    def getWidth(self) -> int:
        return 0

    def getHeight(self) -> int:
        return 0

    def getAspect(self) -> float:
        return 0.0

    def getDuration(self) -> int:
        return 0

    def getCodec(self) -> str:
        return ""

    def getStereoMode(self) -> str:
        return ""

    def getLanguage(self) -> str:
        return ""

    def getHDRType(self) -> str:
        return ""

    def setWidth(self, width: int) -> None:
        pass

    def setHeight(self, height: int) -> None:
        pass

    def setAspect(self, aspect: float) -> None:
        pass

    def setDuration(self, duration: int) -> None:
        pass

    def setCodec(self, codec: str) -> None:
        pass

    def setStereoMode(self, stereoMode: str) -> None:
        pass

    def setLanguage(self, language: str) -> None:
        pass

    def setHDRType(self, hdrType: str) -> None:
        pass


class AudioStreamDetail:

    def __init__(self, channels: int = -1,
                 codec: str = "",
                 language: str = "") -> None:
        pass

    def getChannels(self) -> int:
        return 0

    def getCodec(self) -> str:
        return ""

    def getLanguage(self) -> str:
        return ""

    def setChannels(self, channels: int) -> None:
        pass

    def setCodec(self, codec: str) -> None:
        pass

    def setLanguage(self, language: str) -> None:
        pass


class SubtitleStreamDetail:

    def __init__(self, language: str = "") -> None:
        pass

    def getLanguage(self) -> str:
        return ""

    def setLanguage(self, language: str) -> None:
        pass


@getsetmethods
class InfoTagVideo:

    @define
    class Rating:
        rating: float = 0
        votes: int = 0

    DbId: int = -1
    TagLine: str = ""
    PlotOutline: str = ""
    Plot: str = ""
    Title: str = ""
    TvShowTitle: str = field(default='', getter='getTVShowTitle')
    OriginalTitle: str = ""
    MediaType: str = ""
    Duration: int = 0
    Year: int = 0
    FilenameAndPath: str = ''
    UserRating: int = 0
    Playcount: int = 0
    Album: str = ''
    Season: int = -1
    Episode: int = -1
    ResumeTime: float = field(default=0.0, setter=False)
    ResumeTimeTotal: float = field(default=0.0, setter=False)
    Premiered: str = field(default='1970-01-01', getter='getPremieredAsW3C')
    FirstAired: str = field(default='1970-01-01', getter='getFirstAiredAsW3C')
    LastPlayed: str = field(default='1970-01-01T01:00:00+01:00', getter='getLastPlayedAsW3C')
    Directors: List[str] = field(default='', default_factory=list, first_getter='getDirector')
    Writers: List[str] = Factory(list)
    Actors: List[Actor] = field(default_factory=list, setter='setCast')
    Artists: List[str] = Factory(list)
    Genres: List[str] = field(default='', default_factory=list, first_getter='getGenre')
    Countries: List[str] = field(default_factory=list, getter=False)

    def __init__(self, offscreen: bool = False) -> None:
        self._offscreen: bool = offscreen
        self._cast: List['Actor'] = []
        self._dirs: List[str] = [""]
        self._ratings: dict[str, InfoTagVideo.Rating] = {}
        self._rating_default: str = 'default'
        self._ids: dict[str, str] = {}
        self._ids_default: dict[str, str] = {}

    def getWritingCredits(self) -> str:
        raise DeprecatedError()

    def getPictureURL(self) -> str:
        raise DeprecatedError()
        return ""

    def getVotes(self) -> str:
        raise DeprecatedError()

    def getVotesAsInt(self, type: str = "") -> int:
        if not type:
            type = self._rating_default
        return self._ratings.get(type, InfoTagVideo.Rating()).votes

    def getCast(self) -> str:
        raise DeprecatedError()

    def getFile(self) -> str:
        return Path(self._FilenameAndPath).name if self._FilenameAndPath else ""

    def getPath(self) -> str:
        return str(Path(self._FilenameAndPath).parent) if self._FilenameAndPath else ""

    def getIMDBNumber(self) -> str:
        return self._ids.get('imdb', '')

    def getRating(self, type: str = "") -> float:
        if not type:
            type = self._rating_default
        return self._ratings.get(type, InfoTagVideo.Rating()).rating

    def getLastPlayed(self) -> str:
        raise DeprecatedError()

    def getPremiered(self) -> str:
        raise DeprecatedError()

    def getFirstAired(self) -> str:
        raise DeprecatedError()

    def getTrailer(self) -> str:
        return ""

    def getTrack(self) -> int:
        return 0

    def getUniqueID(self, key: str) -> str:
        if not key:
            key = self._ids_default
        return self._ids.get(key, '')

    def setUniqueID(self, uniqueid: str, type: str = "", isdefault: bool = False) -> None:
        if not type:
            type = self._ids_default
        if isdefault:
            self._ids_default = type
        self._ids[type] = uniqueid

    def setUniqueIDs(self, uniqueIDs: Dict[str, str], defaultuniqueid: str = "") -> None:
        if defaultuniqueid:
            self._ids_default = defaultuniqueid
        self._ids = dict(uniqueIDs)

    def setSortEpisode(self, sortepisode: int) -> None:
        pass

    def setSortSeason(self, sortseason: int) -> None:
        pass

    def setEpisodeGuide(self, episodeguide: str) -> None:
        pass

    def setTop250(self, top250: int) -> None:
        pass

    def setSetId(self, setid: int) -> None:
        pass

    def setTrackNumber(self, tracknumber: int) -> None:
        pass

    def setRating(self, rating: float, votes: int = 0, type: str = "", isdefault: bool = False) -> None:
        if not type:
            type = self._rating_default
        if isdefault:
            self._rating_default = type
        self._ratings[type] = InfoTagVideo.Rating(rating=rating, votes=votes)

    def setRatings(self, ratings: Dict[str, Tuple[float, int]], defaultrating: str = "") -> None:
        if defaultrating:
            self._rating_default = defaultrating
        else:
            defaultrating = self._rating_default
        self._ratings = {k: InfoTagVideo.Rating(*v) for k, v in ratings.items()}

    def setMpaa(self, mpaa: str) -> None:
        pass

    def setSortTitle(self, sorttitle: str) -> None:
        pass

    def setTvShowStatus(self, status: str) -> None:
        pass

    def setStudios(self, studios: List[str]) -> None:
        pass

    def setSet(self, set: str) -> None:
        pass

    def setSetOverview(self, setoverview: str) -> None:
        pass

    def setTags(self, tags: List[str]) -> None:
        pass

    def setProductionCode(self, productioncode: str) -> None:
        pass

    def setVotes(self, votes: int) -> None:
        raise DeprecatedError()

    def setTrailer(self, trailer: str) -> None:
        pass

    def setPath(self, path: str) -> None:
        if self._FilenameAndPath:
            self._FilenameAndPath = str(Path(path) / Path(self._FilenameAndPath).name)
        else:
            self._FilenameAndPath = path

    def setIMDBNumber(self, imdbnumber: str) -> None:
        self._ids['imdb'] = imdbnumber

    def setDateAdded(self, dateadded: str) -> None:
        pass

    def setShowLinks(self, showlinks: List[str]) -> None:
        pass

    def setResumePoint(self, time: float, totaltime: float = 0.0) -> None:
        self._ResumeTime, self._ResumeTimeTotal = time, totaltime

    def addSeason(self, number: int, name: str = "") -> None:
        pass

    def addSeasons(self, namedseasons: List[Tuple[int, str]]) -> None:
        pass

    def addVideoStream(self, stream: VideoStreamDetail) -> None:
        pass

    def addAudioStream(self, stream: AudioStreamDetail) -> None:
        pass

    def addSubtitleStream(self, stream: SubtitleStreamDetail) -> None:
        pass

    def addAvailableArtwork(self, url: str,
                            arttype: str = "",
                            preview: str = "",
                            referrer: str = "",
                            cache: str = "",
                            post: bool = False,
                            isgz: bool = False,
                            season: int = -1) -> None:
        pass


class Keyboard:

    def __init__(self, line: str = "",
                 heading: str = "",
                 hidden: bool = False) -> None:
        pass

    def doModal(self, autoclose: int = 0) -> None:
        pass

    def setDefault(self, line: str = "") -> None:
        pass

    def setHiddenInput(self, hidden: bool = False) -> None:
        pass

    def setHeading(self, heading: str) -> None:
        pass

    def getText(self) -> str:
        return ""

    def isConfirmed(self) -> bool:
        return True


class Monitor:

    _exit: ClassVar[bool] = False
    _instances: ClassVar[set[Monitor]] = set()

    def __new__(cls) -> Self:
        obj = super().__new__(cls)
        Monitor._instances.add(obj)
        return obj

    def __del__(self) -> None:
        Monitor._instances.discard(self)

    def __init__(self) -> None:
        pass

    def onSettingsChanged(self) -> None:
        pass

    def onScreensaverActivated(self) -> None:
        pass

    def onScreensaverDeactivated(self) -> None:
        pass

    def onDPMSActivated(self) -> None:
        pass

    def onDPMSDeactivated(self) -> None:
        pass

    def onScanStarted(self, library: str) -> None:
        pass

    def onScanFinished(self, library: str) -> None:
        pass

    def onCleanStarted(self, library: str) -> None:
        pass

    def onCleanFinished(self, library: str) -> None:
        pass

    def onNotification(self, sender: str, method: str, data: str) -> None:
        pass

    def waitForAbort(self, timeout: float = -1) -> bool:
        step = 0.25
        try:
            if timeout < 0:
                timeout = 9999999
            while timeout > 0 and not self._exit:
                time.sleep(min(step, timeout))
                timeout -= step
        except (KeyboardInterrupt, SystemExit):
            _exit_kodi()
        return self._exit

    def abortRequested(self) -> bool:
        return self._exit


class Player:

    def __init__(self) -> None:
        pass

    def play(self, item: Union[str,  'PlayList'] = "",
             listitem: Optional['xbmcgui.ListItem'] = None,
             windowed: bool = False,
             startpos: int = -1) -> None:
        pass

    def stop(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def playnext(self) -> None:
        pass

    def playprevious(self) -> None:
        pass

    def playselected(self, selected: int) -> None:
        pass

    def isPlaying(self) -> bool:
        return False

    def isPlayingAudio(self) -> bool:
        return False

    def isPlayingVideo(self) -> bool:
        return False

    def isPlayingRDS(self) -> bool:
        return False

    def isExternalPlayer(self) -> bool:
        return False

    def getPlayingFile(self) -> str:
        return ""

    def getPlayingItem(self) -> 'xbmcgui.ListItem':
        from xbmcgui import ListItem
        return ListItem()

    def getTime(self) -> float:
        return 0.0

    def seekTime(self, seekTime: float) -> None:
        pass

    def setSubtitles(self, subtitleFile: str) -> None:
        pass

    def showSubtitles(self, bVisible: bool) -> None:
        pass

    def getSubtitles(self) -> str:
        return ""

    def getAvailableSubtitleStreams(self) -> List[str]:
        return [""]

    def setSubtitleStream(self, iStream: int) -> None:
        pass

    def updateInfoTag(self, item: 'xbmcgui.ListItem') -> None:
        pass

    def getVideoInfoTag(self) -> InfoTagVideo:
        return InfoTagVideo()

    def getMusicInfoTag(self) -> InfoTagMusic:
        return InfoTagMusic()

    def getRadioRDSInfoTag(self) -> InfoTagRadioRDS:
        return InfoTagRadioRDS()

    def getTotalTime(self) -> float:
        return 0.0

    def getAvailableAudioStreams(self) -> List[str]:
        return [""]

    def setAudioStream(self, iStream: int) -> None:
        pass

    def getAvailableVideoStreams(self) -> List[str]:
        return [""]

    def setVideoStream(self, iStream: int) -> None:
        pass

    def onPlayBackStarted(self) -> None:
        pass

    def onAVStarted(self) -> None:
        pass

    def onAVChange(self) -> None:
        pass

    def onPlayBackEnded(self) -> None:
        pass

    def onPlayBackStopped(self) -> None:
        pass

    def onPlayBackError(self) -> None:
        pass

    def onPlayBackPaused(self) -> None:
        pass

    def onPlayBackResumed(self) -> None:
        pass

    def onQueueNextItem(self) -> None:
        pass

    def onPlayBackSpeedChanged(self, speed: int) -> None:
        pass

    def onPlayBackSeek(self, time: int, seekOffset: int) -> None:
        pass

    def onPlayBackSeekChapter(self, chapter: int) -> None:
        pass


class PlayList:

    def __init__(self, playList: int) -> None:
        pass

    def getPlayListId(self) -> int:
        return 0

    def add(self, url: str,
            listitem: Optional['xbmcgui.ListItem'] = None,
            index: int = -1) -> None:
        pass

    def load(self, filename: str) -> bool:
        return True

    def remove(self, filename: str) -> None:
        pass

    def clear(self) -> None:
        pass

    def size(self) -> int:
        return 0

    def shuffle(self) -> None:
        pass

    def unshuffle(self) -> None:
        pass

    def getposition(self) -> int:
        return 0

    def __getitem__(self, index: int) -> 'xbmcgui.ListItem':
        from xbmcgui import ListItem
        return ListItem()


class RenderCapture:

    def __init__(self) -> None:
        pass

    def getWidth(self) -> int:
        return 0

    def getHeight(self) -> int:
        return 0

    def getAspectRatio(self) -> float:
        return 0.0

    def getImageFormat(self) -> str:
        return ""

    def getImage(self, msecs: int = 0) -> bytearray:
        return bytearray()

    def capture(self, width: int, height: int) -> None:
        pass


def log(msg: str, level: int = LOGDEBUG) -> None:
    if _print_log_cb:
        try:
            _print_log_cb(msg, level)
        except Exception:
            pass
    try:
        import time
        with open('/home/voidy/rzeczy/repo/fanfilm/fanfilm_tui.log', 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}][KODI-LOG] {msg}\n")
    except Exception:
        pass
    print(msg, file=sys.stderr)


def shutdown() -> None:
    pass


def restart() -> None:
    pass


def executescript(script: str) -> None:
    pass


def executebuiltin(function: str, wait: bool = False) -> None:
    pass


def executeJSONRPC(jsonrpccommand: str) -> str:
    return _Service().execute_jsonrpc(jsonrpccommand)


def sleep(timemillis: int) -> None:
    while timemillis > 0 and not Monitor._exit:
        time.sleep(min(timemillis, 1000) / 1000.0)
        timemillis -= 1000


def getLocalizedString(id: int) -> str:
    try:
        if TYPE_CHECKING:  # import for type checker only
            from .xbmcaddon import Addon
        else:  # real import
            from xbmcaddon import Addon
        import re
        lang = Addon.LANG.lower().replace('-', '_')
        with open(f'/usr/share/kodi/addons/resource.language.{lang}/resources/strings.po') as f:
            if mch := re.search(fr'\nmsgctxt "#{id}"\s*\nmsgid\s+"(?P<en>\\.|[^"]*)"\s*\nmsgstr\s+"(?P<loc>\\.|[^"]*)"', f.read()):
                return mch['loc'] or mch['en']
    except IOError:
        pass
    return ""


def getSkinDir() -> str:
    return ""


def getLanguage(format: int = ENGLISH_NAME, region: bool = False) -> str:
    if region:
        return fake_api.LOCALE
    return fake_api.LOCALE.partition('-')[0]


def getIPAddress() -> str:
    return ""


def getDVDState() -> int:
    return 0


def getFreeMem() -> int:
    return 0


def getInfoLabel(cLine: str) -> str:
    label = fake_api.INFO_LABEL.get(cLine)
    if label is not None:
        return label
    if cLine == 'Container.PluginName':
        if TYPE_CHECKING:  # import for type checker only
            from .xbmcaddon import Addon
        else:  # real import
            from xbmcaddon import Addon
        return Addon()._id
    return ''


def getInfoImage(infotag: str) -> str:
    return ""


def playSFX(filename: str, useCached: bool = True) -> None:
    pass


def stopSFX() -> None:
    pass


def enableNavSounds(yesNo: bool) -> None:
    pass


def getCondVisibility(condition: str) -> bool:
    return True


def getGlobalIdleTime() -> int:
    return 0


def getCacheThumbName(path: str) -> str:
    return ""


def getCleanMovieTitle(path: str,
                       usefoldername: bool = False) -> Tuple[str, str]:
    return "", ""


def getRegion(id: str) -> str:
    return ""


def getSupportedMedia(mediaType: str) -> str:
    return ""


def skinHasImage(image: str) -> bool:
    return True


def startServer(iTyp: int, bStart: bool) -> bool:
    return True


def audioSuspend() -> None:
    pass


def audioResume() -> None:
    pass


def getUserAgent() -> str:
    return ""


def convertLanguage(language: str, format: int) -> str:
    return ""
