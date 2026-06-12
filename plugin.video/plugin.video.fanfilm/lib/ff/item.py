"""FanFilm ListItem wrapper taken from libka."""

from __future__ import annotations
import re
from sys import version_info as PY
from enum import Enum, IntFlag
from copy import deepcopy
from datetime import datetime, date as dt_date, time as dt_time, timezone
from typing import Any, Sequence, Mapping, Iterable, Iterator, Callable, ClassVar, Generic, TYPE_CHECKING
from typing import TypeVar, Type, NamedTuple, overload
from typing_extensions import Literal, TypeAlias, Protocol, Self
from wrapt.wrappers import ObjectProxy
from attrs import define, frozen, has as has_attrs, asdict, field
from .types import JsonData, is_datetime_instance

from xbmcgui import ListItem as XbmcListItem
from xbmc import Actor, InfoTagVideo

from .. import MOCK
from ..defs import RefType, MediaRef, VideoIds, MediaProgress, IdsDict
from .log_utils import fflog
from .debug.profiler import profiler
from .tricks import dump_obj_gets, str_removeprefix, super_get_attr, super_set_attr, MISSING, MissingType, InvalidString
from .calendar import fromisoformat, timestamp as dt_timestmap, timezone_str, astimezone
from ..kolang import L
from const import const
from cdefs import ItemDetailLevel

if TYPE_CHECKING:
    from .kodidb import KodiVideoInfo
    from .menu import Target, ContextMenuItem
    InfoTagVideoProxyType = InfoTagVideo
else:
    InfoTagVideoProxyType = ObjectProxy


# FFItem dict.
FFItemDict: TypeAlias = 'dict[MediaRef, FFItem]'
#: Kodi listitem's infoLabels type.
LabelInfoType: TypeAlias = Literal['video', 'music', 'pictures', 'game']
#: Info type value dict.
LabelInfoValues: TypeAlias = 'dict[str, Any]'
#: Popreries dict.
LabelPoperties: TypeAlias = 'dict[str, str]'
#: Popreries dict.
ArtValues: TypeAlias = 'dict[str, str]'
#: Kodi video media type.
LabelMediaType: TypeAlias = Literal['video', 'set', 'musicvideo', 'movie', 'tvshow', 'season', 'episode']
#: Special sort position.
SortPosition: TypeAlias = Literal['top', 'normal', 'bottom']
#: Episode type (or empty in not  applicable.
EpisodeType: TypeAlias = Literal['', 'standard',
                                 'series_premiere', 'season_premiere',
                                 'mid_season_finale', 'mid_season_premiere',
                                 'season_finale', 'series_finale']


T = TypeVar('T')

_EMPTY_VTAG: InfoTagVideo = XbmcListItem().getVideoInfoTag()
NO_DATE: str = _EMPTY_VTAG.getPremieredAsW3C()
NO_DATETIME: str = _EMPTY_VTAG.getLastPlayedAsW3C()


class VarApi(NamedTuple, Generic[T]):
    """Object variable (atribute) getter and setter."""
    name: str | None = None
    getter: str | Callable[[], T] | None = None
    setter: str | Callable[[T], None] | Callable[[str], Callable[[T], None]] | None = None
    default: T | MissingType = MISSING
    property: bool = False
    type: Callable[[Any], Any] | None = None


#: Type of ...
ellipsis = type(...)
#: Class auto-vvariables description.
Vars: TypeAlias = 'dict[str, VarApi | Any]'


class JsonApiProto(Protocol):
    VARS: Vars
    def __to_json__(self) -> JsonData: ...
    def __set_json__(self, data: JsonData) -> None: ...
    @classmethod
    def __from_json__(cls: Type[Self], data: JsonData) -> Self: ...  # XXX HACK
    # def __from_json__(cls: Type[Self], data: JsonData) -> FFItem: ...  # XXX HACK


# def json_vars_deco(cls: Type[T]) -> Type[JsonApiProto[T] | T]:
def json_vars_deco(cls: Type[T]) -> Type[T]:
    """Decorator for handle JSON methods and VARS definition."""
    def proc(k: str, v: VarApi | Any) -> VarApi:
        name = f'{k[:1].upper()}{k[1:]}'
        if v is ...:
            return VarApi(k, f'get{name}', f'set{name}')
        if not isinstance(v, VarApi):
            return VarApi(k, f'get{name}', f'set{name}', default=v)
        if not v.name:
            v = v._replace(name=k)
        if v.getter is True:
            v = v._replace(getter=f'get{name}')
        if v.setter is True:
            v = v._replace(setter=f'set{name}')
        return v

    def __to_json__(self) -> JsonData:
        """Dump object to JSON data."""
        def serialize(var):
            getter: Callable[[], Any] = var.getter
            if isinstance(getter, str):
                getter = super_get_attr(self, getter)
            if callable(getter):
                val = getter()
            elif var.property:
                val = super_get_attr(self, var.name)
            else:
                val = getter
            if val == var.default:
                return MISSING
            if val_to_json := getattr(val, '__to_json__', None):
                val = val_to_json()
                return MISSING if val == var.default else val
            if has_attrs(type(val)):
                return asdict(val, recurse=True)
            if isinstance(val, Sequence) and val and has_attrs(type(val[0])):
                return [asdict(v, recurse=True) for v in val]
            if isinstance(val, (dt_date, dt_time)):
                return str(val)
            return val

        return {var.name: val
                for var in self.VARS.values() if var.getter or var.property
                for val in (serialize(var),) if val is not MISSING}

    def __set_json__(self, data: JsonData) -> None:
        """Set JSON in existing object."""
        for key, val in data.items():
            var: VarApi = self.VARS.get(key)
            if var is None:
                fflog(f'ERROR, missing variable {key!r} in {self.__class__.__name__}')
                return
            if callable(var.type):
                val = var.type(val)
            if var.setter is not None:
                setter = var.setter
                if isinstance(setter, str):
                    setter = super_get_attr(self, setter)
                if callable(setter):
                    setter(val)
                else:
                    super_set_attr(self, var.setter, val)
            elif var.property and var.name:
                super_set_attr(self, var.name, val)
            else:
                fflog(f'ERROR, variable {key!r} is not settable {self.__class__.__name__}')

    @classmethod
    def __from_json__(cls, data: JsonData) -> T:
        """Load (create) object from JSON data."""
        obj = cls()
        obj.__set_json__(data)
        return obj

    cls.VARS = {k: proc(k, v) for k, v in getattr(cls, 'VARS', {}).items()}
    if not hasattr(cls, '__to_json__'):
        cls.__to_json__ = __to_json__
    if not hasattr(cls, '__from_json__'):
        cls.__from_json__ = __from_json__
    if not hasattr(cls, '__set_json__'):
        cls.__set_json__ = __set_json__
    return cls


class Temp:
    """Temporary custom attributes. Values are not stored."""


# @define(kw_only=True)
# class KodiItemData:
#     """Raw Kodi item data (from VideoDB)."""

#     # File ID ('idFile' from 'files')
#     file_id: int | None = None
#     # Bookmark ID ('idBookmark' from 'bookmark')
#     bookmark_id: int | None = None
#     # File/link name('strFilename' from 'files').
#     filename: str = ''
#     # Play count ('playCount' from 'files').
#     play_count: int | None = None
#     # Played time (sec).
#     duration: float | None = None
#     # Video duration (sec).
#     total_time: float | None = None

#     @property
#     def prostgress(self) -> float | None:
#         """Percent progress."""
#         if self.duration is None or self.total_time is None:
#             return None
#         if self.total_time > 0:
#             return 100 * self.duration / self.total_time
#         return 0.0


class FMode(Enum):
    """FFItem mode."""

    # Folder item (isFolder=True, isPalayble=False).
    Folder = 'folder'
    # Video item (isFolder=False, isPalayble=True).
    Playable = 'playable'
    # Command / passive item (isFolder=False, isPalayble=False).
    Command = 'command'
    # Kind od command.
    Separator = 'separator'


# class Role(Enum):
#     """Person main role."""
#     #: Actor
#     Actor = 'actor'
#     Director = 'director'
#     Writer = 'writer'
#     Crew = 'crew'


@frozen
class FFTitleAlias:
    title: str
    country: str = ''


class FFEpisodeGroupType(IntFlag):
    """Episode group type flags (taken from TMDB)."""
    # see https://developer.themoviedb.org/reference/tv-episode-group-details

    NONE = 0
    AIR_DATE = 1
    ABSOLUTE = 2
    DVD = 3
    DIGITAL = 4
    STORY_ARC = 5
    PRODUCTION = 6
    TV = 7

    @classmethod
    def from_tmdb(cls, type: int) -> Self:
        """1:1 enum is the same as in tmdb."""
        return cls(type)


@define(kw_only=True)
class FFEpisodeGroup:
    """Episode group definition."""

    #: TMDB Episode Group ID.
    tmdb_id: str = ''
    #: Episode group name.
    name: str = ''
    #: Episode group description.
    description: str = ''
    #: Number of episodes in the group.
    episode_count: int = 0
    #: Number of seasons in the group.
    group_count: int = 0
    #: The network the group belongs to.
    network: str | None = None
    #: The type of the episode group.
    type: FFEpisodeGroupType = FFEpisodeGroupType.NONE
    #: Parsed children items (quasi-seasons and episodes) if not None.
    items: list[FFItem] | None = None

    def __str__(self) -> str:
        return self.name

    def __len__(self) -> int:
        return 0 if self.items is None else len(self.items)

    @overload
    def __getitem__(self, index: int) -> FFItem: ...

    @overload
    def __getitem__(self, index: slice) -> list[FFItem]: ...

    def __getitem__(self, index: int | slice) -> FFItem | list[FFItem]:
        if self.items is None:
            raise IndexError(f'[{index}] failed, no items')
        return self.items[index]

    def __iter__(self) -> Iterator[FFItem]:
        if self.items is None:
            raise StopIteration('no items')
        return iter(self.items)

    def mapping(self) -> dict[MediaRef, MediaRef]:
        """Return mapping from general order to episode group order."""
        return {ep.ref: ep.ref.with_season_episode(ep.season or 0, ep.episode or 0)
                for sz in self.items or () for ep in sz.episode_iter(special=True)}

    def rev_mapping(self) -> dict[MediaRef, MediaRef]:
        """Return reverse mapping: from episode group order. to general order."""
        return {ep.ref.with_season_episode(ep.season or 0, ep.episode or 0): ep.ref
                for sz in self.items or () for ep in sz.episode_iter(special=True)}


@define(kw_only=True)
class FFItemEpisodeGroup:
    """Support FFItem episode group."""

    #: Current episode group.
    current: FFEpisodeGroup | None = None
    # #: Seasons (groups) and episodes in current episode groups.
    # children: list[FFItem] = field(factory=list)
    # #: Mapping from general order to episode group order.
    # mapping: dict[MediaRef, MediaRef] = field(factory=dict)
    #: All episode groups (in the order).
    groups: list[FFEpisodeGroup] | None = None

    @property
    def tmdb_id(self) -> str:
        """TMDB Episode Group ID of current episode group."""
        return '' if self.current is None else self.current.tmdb_id


@define
class Person:
    id: int
    name: str
    department: str | None = None
    job: str | None = None
    character: str | None = None
    thumbnail: str | None = None
    gender: int = 0
    order: int = -1

    @property
    def is_actor(self) -> bool:
        """True if actor"""
        return self.job == 'Actor'

    def actor(self) -> Actor | None:
        """Return actor object."""
        if self.job == 'Actor':
            return Actor(self.name, self.character or '', self.order, self.thumbnail or '')
        return None


class _FFMetaClass(type):
    """FFItem meta-class to modify ListItem__init__ arguments."""

    def __call__(self, *args, **kwargs):
        return self.__new__(self, *args, **kwargs)
        # return super().__call__(*args, **kwargs).__wrapped__


class FFActor(Actor, metaclass=_FFMetaClass):
    """Extended actor/person."""

    def __new__(cls,
                name: str = '',
                role: str = '',
                order: int = -1,
                thumbnail: str = '',
                *,
                ffid: int = 0,
                ) -> 'FFActor':
        obj = super().__new__(cls, name, role, order, thumbnail)
        obj.__init__(name, role, order, thumbnail, ffid=ffid)
        return obj

    def __init__(self,
                 name: str = '',
                 role: str = '',
                 order: int = -1,
                 thumbnail: str = '',
                 *,
                 ffid: int = 0,
                 ) -> None:
        super().__init__(name, role, order, thumbnail)
        self.ffid = ffid

    def __hash__(self) -> int:
        """Return hash of item."""
        return hash(id(self))


@json_vars_deco
class FFVTag(InfoTagVideoProxyType):
    """Wrapper for InfoTagVideo, implements sam extra Famfilm stuff."""

    _rx_mpaa_split: ClassVar[re.Pattern] = re.compile(r'\s*,\s*')

    VARS: Vars = {
        'actors':        VarApi(None, '_get_actors_json', '_set_actors_json', []),
        'countries':     [],
        'dbId':          -1,
        'directors':     [],
        'duration':      0,
        'episode':       -1,
        'firstAired':    VarApi(None, 'getFirstAiredAsW3C', 'setFirstAired', NO_DATE),
        'genres':        [],
        'IMDBNumber':    '',
        'mediaType':     '',
        'mpaaList':      [],
        'originalTitle': '',
        'plot':          '',
        'plotBase':      '',  # external plot without modification and styling (e.g. w/o progress)
        'plotOutline':   '',
        'premiered':     VarApi(None, 'getPremieredAsW3C', 'setPremiered', NO_DATE),
        'ratings':       {},
        'season':        -1,
        'studios':       [],
        'TvShowTitle':   '',
        'tagLine':       '',
        'title':         '',
        'trailer':       '',
        'uniqueIDs':     {},
        'userRating':    0,
        'writers':       [],
        'year':          0,
        'lastPlayed':    VarApi(None, 'getLastPlayedAsW3C', 'setLastPlayed', NO_DATETIME),
        # 'playcount'
        # 'resumeTime'
        # 'resumeTimeTotal'
        # 'file'
        # 'filenameAndPath'
        # 'path'
        # -- music --
        # 'album':         ...,
        # 'artists':       ...,
        # 'track':         ...,
        # -- FF specific --
        'englishTitle':       '',
        'englishTvShowTitle': '',
        'episodeType':        '',
        'seriesStatus':       '',
        'countryCodes':       [],

    }

    class Rating(NamedTuple):
        rating: float = 0
        votes: int = 0

    def __init__(self, vtag: InfoTagVideo) -> None:
        super().__init__(vtag)
        #: Unique Ids.
        self._self_unique_ids: dict[str, str] = {}
        #: Unique Ids.
        self._self_unique_ids_default: str = ''
        #: MPAA ratings.
        self._self_mpaa: list[str] = []
        #: List of countries.
        self._self_countries: list[str] = []
        #: List of of ISO 3166-1 country codes.
        self._self_country_codes: list[str] = []
        #: List of of ISO 639-2 language codes.
        self._self_language_codes: list[str] = []
        #: List of studios.
        self._self_studios: list[str] = []
        #: Ratings (keep copy to implement getRatings).
        self._self_ratings: dict[str, FFVTag.Rating] = {}
        #: Default Rating name (keep copy to implement getRatingDefault).
        self._self_rating_default: str = 'default'
        #: Actor list (keep copy to implement getImDb).
        self._self_actors: list[FFActor] = []
        #: Crew - person list.
        self._self_crew: list[FFActor] = []
        #: Original / external plot without modification and styling (e.g. w/o progress).
        self._self_plot_base: str = ''
        #: The title in English.
        self._self_english_title: str = ''
        #: The tv-show title in English.
        self._self_english_tvshow_title: str = ''
        #: First aired date.
        self._self_first_aired: dt_date | None = None
        #: Premiered date.
        self._self_premiered: dt_date | None = None
        # #: Last played date-time.
        self._self_last_played: datetime | None = None
        #: Episode type.
        self._self_episode_type: EpisodeType = ''
        #: Tv-show status.
        self._self_series_status: str = ''


    def __repr__(self) -> str:
        return f'FFVTag({self.__wrapped__!r})'

    def dumps(self, *, indent: int = 2, margin: int = 0) -> str:
        def dump(vv: Any, margin: int) -> str:
            if isinstance(vv, tuple):
                br = '()'
            elif isinstance(vv, list):
                br = '[]'
            elif isinstance(vv, set):
                br = '{}'
            else:
                return repr(vv)
            if len(vv) <= 1:
                return repr(vv)
            out = '\n'.join(f'{"":{margin+indent}}{v!r}' for v in vv)
            return f'{br[0]}\n{out}\n{"":{margin}}{br[1]}'

        if not indent:
            return repr(self)
        vals = {str_removeprefix(k, 'InfoTagVideo.get'): v for k, v in dump_obj_gets(self)}
        if hasattr(self.__wrapped__, '__attributes__'):
            vals.update({a: v for a in self.__wrapped__.__attributes__ if (v := getattr(self, f'_{a}')) or True})
        out = '\n'.join(f'{"":{margin+indent}}{n:28} = {dump(v, margin=margin+indent)}' for n, v in vals.items())
        return f'FFVTag(\n{out}\n{"":{margin}})'

    def getUniqueIDs(self) -> dict[str, str]:
        """Get all unique IDs."""
        return dict(self._self_unique_ids)

    def setUniqueIDs(self, values: dict[str, str], defaultuniqueid: str | None = None) -> None:
        """Set all unique IDs."""
        self._self_unique_ids = dict(values)
        if defaultuniqueid:
            self._self_unique_ids_default = defaultuniqueid
        self.__wrapped__.setUniqueIDs(values, defaultuniqueid or '')

    def setUniqueID(self, uniqueid: str, type: str = '', isdefault: bool = False) -> None:
        """Set one unique ID."""
        if not type:
            type = self._self_unique_ids_default
        if isdefault:
            self._self_unique_ids_default = type
        self._self_unique_ids[type] = uniqueid
        self.__wrapped__.setUniqueID(uniqueid, type, isdefault)

    def getAdult(self) -> bool:
        """Return True if it's adult video (X rated)."""
        return 'X' in self._self_mpaa

    def setAdult(self, adult: bool) -> None:
        """Set adult video flag (X rated)."""
        adult = bool(adult)
        was = 'X' in self._self_mpaa
        if was != adult:
            if adult:
                self._self_mpaa.append('X')
            else:
                self._self_mpaa.remove('X')
            self._set_mpaa()

    def getMpaaList(self) -> list[str]:
        """Get the MPAA rating of the video."""
        return list(self._self_mpaa)

    def setMpaaList(self, mpaa: list[str]) -> None:
        """Set the MPAA rating of the video."""
        self._self_mpaa = list(v for v in dict.fromkeys(mpaa) if v)
        self._set_mpaa()

    def getMpaa(self) -> str:
        """Get the MPAA rating of the video."""
        return ', '.join(self._self_mpaa)

    def setMpaa(self, mpaa: str) -> None:
        """Set the MPAA rating of the video."""
        self.setMpaaList(self._rx_mpaa_split.split(mpaa))

    def addMpaa(self, mpaa: str) -> None:
        """Add the MPAA rating of the video."""
        if mpaa and mpaa not in self._self_mpaa:
            self._self_mpaa.append(mpaa)
            self._set_mpaa()

    def _set_mpaa(self) -> None:
        """Set MPAA in kodi InfoTagVideo."""
        self.__wrapped__.setMpaa(', '.join(self._self_mpaa))

    def setCountries(self, countries: list[str]) -> None:
        """Set list of countries."""
        self._self_countries = list(countries)
        self.__wrapped__.setCountries(countries)

    def getCountries(self) -> list[str]:
        """Get list of countries (missing in kodi)."""
        return list(self._self_countries)

    def setCountryCodes(self, codes: Sequence[str]) -> None:
        """Set list of ISO 3166-1 country codes (ex. PL, US)."""
        self._self_country_codes = list(codes)

    def getCountryCodes(self) -> list[str]:
        """Get list of ISO 3166-1 country codes (ex. PL, US)."""
        return list(self._self_country_codes)

    def setLanguageCodes(self, codes: Sequence[str]) -> None:
        """Set list of ISO 639-2 language codes (ex. pl, en)."""
        self._self_language_codes = list(codes)

    def getLanguageCodes(self) -> list[str]:
        """Get list of ISO 639-2 language codes (ex. pl, en)."""
        return list(self._self_language_codes)

    def setStudios(self, studios: list[str]) -> None:
        """Set list of studios."""
        self._self_studios = list(studios)
        self.__wrapped__.setStudios(studios)

    def getStudios(self) -> list[str]:
        """Get list of studios (missing in kodi)."""
        return list(self._self_studios)

    def setRating(self, rating: float, votes: int = 0, type: str = "", isdefault: bool = False) -> None:
        """Set rating."""
        if not type:
            type = self._self_rating_default
        if isdefault:
            self._self_rating_default = type
        self._self_ratings[type] = FFVTag.Rating(rating=rating, votes=votes)
        self.__wrapped__.setRating(rating, votes, type, isdefault)

    def setRatings(self, ratings: dict[str, tuple[float, int]], defaultrating: str = "") -> None:
        """Set ratings."""
        if defaultrating:
            self._self_rating_default = defaultrating
        else:
            defaultrating = self._self_rating_default
        self._self_ratings = {k: FFVTag.Rating(*v) for k, v in ratings.items()}
        self.__wrapped__.setRatings(ratings, defaultrating)

    def getRatingDefault(self) -> str:
        """Get rating default name."""
        return self._self_rating_default

    def getRatings(self) -> dict[str, tuple[float, int]]:
        """Get ratings."""
        return dict(self._self_ratings)

    def _get_actors_json(self) -> list[JsonData]:
        """Get list of actors as JSON."""
        return [{'name': a.getName(), 'role': a.getRole(), 'order': a.getOrder(), 'thumbnail': a.getThumbnail(),
                 'ffid': a.ffid}
                for a in self._self_actors]

    def _set_actors_json(self, data: list[JsonData]) -> None:
        """Set list of actors from JSON data."""
        self._self_actors = [FFActor(**act) for act in data]
        self.__wrapped__.setCast(self._self_actors)

    def getTvShowTitle(self) -> str:
        """Get the video TV show title."""
        return self.getTVShowTitle()

    def getActors(self) -> list[FFActor]:
        """Get cast (actor list)."""
        return list(self._self_actors)

    def setActors(self, actors: Iterable[FFActor]) -> None:
        """Set cast (actor list)."""
        def act(a: FFActor | Actor | tuple[str, str]) -> FFActor:
            if isinstance(a, FFActor):
                return FFActor(a.getName(), a.getRole(), a.getOrder(), a.getThumbnail(), ffid=a.ffid)
            if isinstance(a, Actor):
                return FFActor(a.getName(), a.getRole(), a.getOrder(), a.getThumbnail())
            return FFActor(*a)

        self._self_actors = [act(a) for a in actors]
        self.__wrapped__.setCast(self._self_actors)

    def getCrew(self) -> list[FFActor]:
        """Get crew (director, producer, writer, ... list)."""
        return list(self._self_crew)

    def setCrew(self, persons: Iterable[FFActor]) -> None:
        """Set crew (director, producer, writer, ... list)."""
        def act(a: FFActor | Actor | tuple[str, str]) -> FFActor:
            if isinstance(a, FFActor):
                return FFActor(a.getName(), a.getRole(), a.getOrder(), a.getThumbnail(), ffid=a.ffid)
            if isinstance(a, Actor):
                return FFActor(a.getName(), a.getRole(), a.getOrder(), a.getThumbnail())
            return FFActor(*a)

        self._self_crew = [act(a) for a in persons]

    setCast = setActors

    def getPlotBase(self) -> str:
        """Returtns original / external plot without modification and styling (e.g. w/o progress)."""
        return self._self_plot_base

    def setPlotBase(self, plot: str) -> None:
        """Sets original / external plot without modification and styling (e.g. w/o progress). Overrides plot."""
        self._self_plot_base = str(plot or '')
        self.setPlot(self._self_plot_base)

    def getEnglishTitle(self) -> str:
        """Return the title in English."""
        return self._self_english_title

    def setEnglishTitle(self, title: str) -> None:
        """Set the title in English."""
        self._self_english_title = title

    def getEnglishTvShowTitle(self) -> str:
        """Return the tv-show title in English."""
        return self._self_english_tvshow_title

    def setEnglishTvShowTitle(self, title: str) -> None:
        """Set the tv-show title in English."""
        self._self_english_tvshow_title = title

    def getEpisodeType(self) -> EpisodeType:
        """Return the episode type."""
        return self._self_episode_type

    def setEpisodeType(self, value: EpisodeType) -> None:
        """Set the episode type."""
        self._self_episode_type = value

    def getSeriesStatus(self) -> str:
        """Return the episode type."""
        return self._self_series_status

    def setSeriesStatus(self, value: str) -> None:
        """Set the episode type."""
        self._self_series_status = value

    def getFirstAiredDate(self) -> dt_date | None:
        """Get first aired date."""
        return self._self_first_aired

    def getFirstAiredAsW3C(self) -> str:
        """Get first aired date."""
        if self._self_first_aired is None:
            return InvalidString(NO_DATE)
        return self._self_first_aired.isoformat()

    def setFirstAired(self, firstaired: str | dt_date | datetime | None) -> None:
        """Set first aired date."""
        date = firstaired
        if isinstance(date, str):
            date = fromisoformat(date)
        if is_datetime_instance(date):
            date = date.date()
        self._self_first_aired = date
        self.__wrapped__.setFirstAired(NO_DATE if date is None else str(date))

    def getPremieredDate(self) -> dt_date | None:
        """Get premiered date."""
        return self._self_premiered

    def getPremieredAsW3C(self) -> str:
        """Get premiered date."""
        if self._self_premiered is None:
            return InvalidString(NO_DATE)
        return self._self_premiered.isoformat()

    def setPremiered(self, premiered: str | dt_date | datetime | None) -> None:
        """Set premiered date."""
        date = premiered
        if isinstance(date, str):
            date = fromisoformat(date)
        if is_datetime_instance(date):
            date = date.date()
        self._self_premiered = date
        self.__wrapped__.setPremiered(NO_DATE if date is None else str(date))

    def getLastPlayedDateTime(self) -> datetime | None:
        """Get last played date."""
        return self._self_last_played

    def getLastPlayedAsW3C(self) -> str:
        """Get last played date."""
        if self._self_last_played is None:
            return InvalidString(NO_DATETIME)
        if PY >= (3, 12):
            return f'{self._self_last_played:%Y-%m-%dT%H:%M:%S%:z}'
        return f'{self._self_last_played:%Y-%m-%dT%H:%M:%S}{timezone_str(self._self_last_played)}'

    def setLastPlayed(self, lastplayed: str | dt_date | datetime | None) -> None:
        """Set last played date."""
        date = lastplayed
        if isinstance(date, str):
            date = fromisoformat(date)
        if isinstance(date, dt_date) and not is_datetime_instance(date):
            date = datetime.combine(date, dt_time(12))
        if date is not None and date.tzinfo is None:
            date = astimezone(date)  # default is local timezone
        self._self_last_played = date
        if date is None:
            date_str = NO_DATETIME
        else:
            date_str = f'{self._self_last_played:%Y-%m-%dT%H:%M:%S}'  # ignore timezone, kodi does not support it in setLastPlayedAsW3C
        self.__wrapped__.setLastPlayed(date_str)

    if TYPE_CHECKING:
        @classmethod
        def __from_json__(cls, data: JsonData) -> Self: ...
        def __to_json__(self) -> JsonData: ...
        def __set_json__(self, data: JsonData) -> None: ...


# if MOCK:
#     _ffitem_metaclass_kwargs = {}
# else:
#     _ffitem_metaclass_kwargs = {'metaclass': _FFMetaClass}


@json_vars_deco
# class FFItem(XbmcListItem, **_ffitem_metaclass_kwargs):
class FFItem(XbmcListItem, metaclass=_FFMetaClass):
    """
    FanFilm xbmcgui.ListItem wrapper to keep URL and is_folder flag.

    >>> FFItem('label', 'label 2', '/path', offscreen=True)
    >>> FFItem('movie', 12345)
    >>> FFItem('movie', VideoIds(...))
    >>> FFItem(MediaRef(...))
    """

    DEFAULT_INFO_TYPE: ClassVar[LabelInfoType] = 'video'

    VARS: ClassVar[Vars] = {
        'type':                  VarApi(default=None, property=True),
        'ffid':                  VarApi(default=None, property=True),
        'tv_ffid':               VarApi(default=None, property=True),
        'url':                   VarApi(default=None, property=True),
        '_ref':                  VarApi(default=MediaRef('', 0), property=True, type=lambda v: MediaRef(**v)),
        #
        'art':                   {},
        'dateTime':              '',
        'label':                 '',
        'label2':                '',
        'path':                  '',
        'properties':            {},
        'availableFanart':       [],
        'folder':                VarApi('folder', 'isFolder', 'setIsFolder', False),
        'vtag':                  VarApi('vtag', 'getVideoInfoTag', 'vtag.__set_json__', {}),
        '_children_count':       VarApi(default=None, property=True),
        'episodes_count':        VarApi(default=None, property=True),
        'last_episode_to_air':   VarApi(default=None, property=True),
        'next_episode_to_air':   VarApi(default=None, property=True),
        'aliases_info':          VarApi(default=(), property=True, type=lambda vv: tuple(FFTitleAlias(*v) for v in vv)),
        'progress':              VarApi(default=None, property=True, type=lambda v: MediaProgress.__from_json__(v)),
        'keywords':              VarApi(default={}, property=True),
        'broken':                VarApi(default=False, property=True),
        '_continuous_episode_number': VarApi(default=False, property=True),
        '_first_episode_number':      VarApi(default=1, property=True),
        '_guessed_date':              VarApi(default=None, property=True, type=lambda v: fromisoformat(v) if isinstance(v, str) else v),
        # 'episode_groups':        VarApi(default=None, property=True),
        'mimeType':              '',
    }

    Mode: ClassVar[type[FMode]] = FMode
    ArtLabels: ClassVar[tuple[str, ...]] = ('thumb', 'poster', 'banner', 'fanart', 'clearart', 'clearlogo', 'landscape', 'icon')
    AIRED_DATE: ClassVar[dt_date] = dt_date(2000, 1, 1)  # arbitrary old date

    def __new__(cls,
                #: Kodi LiteItem label.
                label: str | MediaRef | None = None,
                #: Kodi LiteItem label2.
                label2: str | None = None,
                #: Kodi ListItem path.
                path: str | None = None,
                # Kodi LiteItem offscreen flag (faster render).
                offscreen: bool = True,
                **kwargs):
        # Translate label.
        if isinstance(label, int):
            label = L(label)
        # determine arguments (see doc above)
        if isinstance(label, MediaRef):
            ref: MediaRef = label
            kwargs.setdefault('ref', ref)
            kwargs.setdefault('type', ref.real_type)
            if ref.season is None:
                kwargs.setdefault('ffid', ref.ffid)  # only for "movie" and "show"
            if ref.type == 'show':
                kwargs.setdefault('tv_ffid', ref.ffid)
                kwargs.setdefault('season', ref.season)
                kwargs.setdefault('episode', ref.episode)
            if kwargs['type'] in ('movie', 'episode'):
                kwargs.setdefault('mode', FMode.Playable)
            else:
                kwargs.setdefault('mode', FMode.Folder)
            label = label2 = ''
        elif isinstance(label, str) and isinstance(label2, int):
            kwargs.setdefault('type', label)
            kwargs.setdefault('ffid', label2)  # only for "movie" and "show"
            label = label2 = None
        elif isinstance(label, str) and isinstance(label2, VideoIds):
            kwargs.setdefault('type', label)
            kwargs.setdefault('ffid', label2.ffid)  # only for "movie" and "show"
            label = label2 = ''
        else:
            ...
        # if path:
        #     kwargs.setdefault('url', path)

        # xbmcgui.ListItem.__new__ positional arguments only
        if label is not None and not isinstance(label, str):
            fflog(f'ERROR: incorrect type: FFItem({label!r})')
        obj = super().__new__(cls, label, label2, path, offscreen)
        # obj.__init__(label, label2, path, offscreen)
        # # ffitem keyword argument only
        # obj.__ff_init__(**kwargs)
        obj.__init__(label, label2, path, offscreen, **kwargs)
        return obj

    # @profiler(sort_by=['cumtime', 'tottime'], cumulative=True)
    def __init__(self,
                 # --- Kodi ListItem arguments. ---
                 # Kodi LiteItem label.
                 label: str | MediaRef | None = None,
                 # Kodi LiteItem label2.
                 label2: str = '',
                 # Kodi ListItem path.
                 path: str = '',
                 # Kodi LiteItem offscreen flag (faster render).
                 offscreen: bool = True,
                 # --- Real FFItem keyword arguments. ---
                 *,
                 # Media reference (explicit).
                 ref: MediaRef | None = None,
                 # Real media type (video type), one of: movie, show, season, episode.
                 type: RefType | None = None,
                 # Media DB ID.
                 ffid: int | None = None,
                 # Item mode (folder, playable, etc.).
                 mode: FMode = FMode.Command,
                 # Item URL, passed to kodi directory.
                 url: str | None = None,
                 # Kodi list item info type (video, music, ...).
                 info_type: LabelInfoType | None = 'video',
                 # Item target (URL or callback), passed to kodi directory. Will override url on kdir.add().
                 target: Target | None = None,
                 # TV show DB ID..
                 tv_ffid: int | None = None,
                 # TV show season.
                 season: int | None = None,
                 # TV show episode.
                 episode: int | None = None,
                 # Tv-show FFItem (for seasons and episodes).
                 tvshow_item: FFItem | None = None,
                 # Season FFItem (for episodes).
                 season_item: FFItem | None = None,
                 # sort_key=None,
                 # custom=None,
                 # Set special sort position (top, bottom).
                 position: SortPosition | None = None,
                 # UTC timestamp, When item is created / updated.
                 meta_updated_at: int = 0,
                 # Properties to set.
                 properties: LabelPoperties | None = None,
                 # Art values to set.
                 art: ArtValues | None = None,
                 ) -> None:
        """FanFilm specific initialize."""
        xbmc_label = label if isinstance(label, str) else ''
        super().__init__(xbmc_label, label2, path, offscreen)
        if ref is None:
            if type in ('season', 'episode') and tv_ffid and season is not None:
                ref = MediaRef(type='show', ffid=tv_ffid, season=season, episode=episode)
            elif type and ffid:
                ref = MediaRef(type=type, ffid=ffid)
            else:
                ref = MediaRef('', 0)
        #: Media reference.
        self._ref: MediaRef = ref
        if getattr(FFItem, '_no_init_', False):  # XXX XXX XXX
            self._mode = mode
            self._vtag = None
            return
        #: Media type.
        self.type: RefType | None = type
        #: Media DB ID.
        self.tv_ffid: int | None = tv_ffid
        #: Media DB ID.
        self._ffid: int | None = ffid
        # True if folder. Passed to kodi directory.
        self._item_folder: bool = mode == FMode.Folder
        #: Item mode.
        self._mode: FMode = mode
        #: Kodi listitem's infoLabels type.
        self.info_type: LabelInfoType | None = info_type
        #: Info labels copy.
        self._info: LabelInfoValues = {}
        #: Properties copy.
        self._props: LabelPoperties = {}
        #: Art values copy.
        self._art: ArtValues = {}
        #: Art values copy.
        self._available_fanart: list[ArtValues] = []
        # self.sort_key = sort_key  # TODO: get more from libka
        # self.custom = custom  # TODO: get more from libka
        #: Custom context-menu items. Item are NOT added to ListItem. Use `addContextMenuItem` or `KodiDirectory.` to add items to ListItem.
        self.cm_menu: list[ContextMenuItem] = []
        #: Wrapper of Kodi InfoTagVideo.
        self._vtag: FFVTag | None = None
        #: Tv-show ff-item, for show (itself), season, episode objects.
        self.show_item: FFItem | None = tvshow_item
        #: Season ff-item, for episode object.
        self.season_item: FFItem | None = season_item
        #: List of children: tv-show seasons or season episodes.
        self.children_items: list[FFItem] | None = None
        #: Declaration of children count. Used in degraded items (eg. seasons got form show details).
        self._children_count: int | None = None
        #: The number of all episodes count. Used in deep degraded show (without full seasons info).
        self.episodes_count: int | None = None
        #: Declaration of aired episodes count. Used in deep degraded items (number of episodes in the show).
        self._aired_episodes_count: int | None = None
        #: Last episode to air.
        self.last_episode_to_air: FFItem | None = None
        #: Next episode to air.
        self.next_episode_to_air: FFItem | None = None
        #: UI Language (eg. to obtain title language).
        self.ui_lang: str | None = None
        #: Item URL (for kodi directory).
        self.target: Target | None = target
        #: Item URL (for kodi directory).
        self.url: str | None = url
        #: Cutsom source data, eg. JSON object.
        self.source_data: Any | None = None
        #: Cutsom role / label (eg. playing character, person job, etc.).
        self.role: str = ''
        #: Something is not OK, eg. not TMDB info found.
        self.broken: bool = False
        #: Optional style for fromat or modify description.
        self.descr_style: str | None = None
        #: Temporary custom attributes.
        self.temp = Temp()
        #: Progress info.
        self.progress: MediaProgress | None = None
        #: UTC timestamp, When item is created / updated.
        self.meta_updated_at: int = int(meta_updated_at.timestamp() if is_datetime_instance(meta_updated_at) else meta_updated_at or 0)
        #: Title aliases.
        self.aliases_info: Sequence[FFTitleAlias] = ()
        #: Raw Kodi progress and play-count.
        self.kodi_data: KodiVideoInfo | None = None  # XXX ???
        #: Item keywords (from TMDB).
        self.keywords: Mapping[str, int] = {}
        #: Episode groups support (current and all group definitions).
        self.episode_group: FFItemEpisodeGroup = FFItemEpisodeGroup()
        #: Detail level.
        self.detail_level: ItemDetailLevel = ItemDetailLevel.BASIC
        #: First episode number in season (for proper episode numbering). The season may start from different number than 1.
        self._first_episode_number: int = 1
        #: Continuous episode numbering in show (for proper episode numbering). Episodes are numbered continuously in show, not per season.
        self._continuous_episode_number: bool = False
        #: Fake date for aired / premiered (guess from another data).
        self._guessed_date: dt_date | None = None
        #: ListItem mime-type needed for getMimeType().
        self._mime_type: str = ''

        if self.info_type is not None:
            self.setInfo(self.info_type, self._info)
        if self.url is None and self._mode == FMode.Separator:
            self.url = ''  # no operation
        # use property setters
        self.mode = mode
        if season is None and ref.season is not None:
            season = ref.season
        if episode is None and ref.episode is not None:
            episode = ref.episode
        if ffid is not None:
            self.ffid = ffid
        if season is not None:
            self.season = season
        if episode is not None:
            self.episode = episode
        if self.type:
            mtype = 'tvshow' if self.type == 'show' else self.type
            self.vtag.setMediaType(mtype)
        if position is not None:
            self.position = position
        if self._ffid:
            self.vtag.setUniqueID(str(self._ffid), 'ffid')
            self.vtag.setUniqueID(f'{self.ref:a}', 'ffref')
        if properties is not None:
            self.setProperties(properties)
        if art is not None:
            self.setArt(art)

    def __repr__(self):
        extra = ''
        if self.season:
            extra = f', season={self.season!r}'
            if self.episode:
                extra = f'{extra}, episode={self.episode!r}'
        # return (f'FFItem({super().__repr__()}, type={self.type!r}, ffid={self.ffid!r}{extra},'
        #         f' title={self.title!r}, year={self.year})')
        return (f'FFItem({self.ref:a}, type={self.type!r}, ffid={self.ffid!r}{extra},'
                f' title={self.title!r}, year={self.year})')

    def __hash__(self) -> int:
        """Return hash of item."""
        return hash(id(self))

    def dumps(self, *, indent: int = 2, margin: int = 0) -> str:
        if not indent:
            out = ', '.join(f'{n}={v!r}' for n, v in dump_obj_gets(self))
            return f'FFItem({out})'
            indent = 2
        # return '\n'.join(f'{indent}{n:28} = {v!r}' for n, v in dump_obj_gets(self))
        out = '\n'.join(f'{"":{indent}}{n:28} = {x}' for n, v in dump_obj_gets(self)
                        for x in (v.dumps(indent=indent, margin=margin+indent) if hasattr(v, 'dumps') else repr(v),))
        return f'FFItem(\n{out}\n{"":{margin}})'

    @property
    def vtag(self) -> FFVTag:
        if self._vtag is not None:
            return self._vtag
        return self.getVideoInfoTag()

    # def __getattr__(self, key):
    #     return getattr(self._xbmc_item, key)

    def __iter__(self) -> Iterator[FFItem]:
        """Iterate over children (seasons or episodes)."""
        return iter(self.children_items if self.children_items else ())

    def __call__(self) -> XbmcListItem:
        """Execute item, apply all virtual settings into Kodi ListItem."""
        # if self._menu is not None:
        #     self.addContextMenuItems(self._menu)
        return self

    # def __reduce_ex__(self, proto) -> tuple[type, tuple[Any, ...], Any]:
    #     """Return pickle dump (type, args, state)."""

    # def __cache_dump__(self) -> JsonData:
    #     """Dump object to JSON data."""

    # @classmethod
    # def __cache_load__(self, data: JsonData) -> FFItem:
    #     """Load (create) object from JSON data."""

    # def __to_json__(self) -> JsonData:
    #     """Dump object to JSON data."""

    # @classmethod
    # def __from_json__(self, data: JsonData) -> FFItem:
    #     """Load (create) object from JSON data."""

    @classmethod
    def from_actor(cls, actor: FFActor) -> FFItem:
        """Create FFItem from FFActor."""
        it = FFItem(MediaRef.person(actor.ffid), mode=FFItem.Mode.Folder)
        it.label = it.title = actor.getName()
        img = actor.getThumbnail()
        if img:
            it.setArt({'thumb': img})
        it.role = actor.getRole()
        return it

    def degraded_episodes(self) -> None:
        """Create degraded episode items for the season."""
        if not self.ref.is_season or self.children_items is not None or self._children_count is None:
            return
        snum = self.ref.season or 0
        enum = self._first_episode_number
        border: MediaRef | None = None
        nxt: FFItem | None = None
        lst: FFItem | None = None
        if show := self.show_item:
            lst = show.last_episode_to_air
            nxt = show.next_episode_to_air
            if ep := lst or nxt:
                border = ep.ref
        self.children_items = []
        # if self.date:
        #     date = self.date
        # elif date is None and border and self.ref < border:
        #     date = self.AIRED_DATE  # arbitrary old date
        # if date and self.date is None:
        #     self.vtag.setFirstAired(date)
        date = self.date
        for _ in range(self._children_count):
            ep_ref = self.ref.with_episode(enum)
            if lst and ep_ref == lst.ref:
                ep = lst
            elif nxt and ep_ref == nxt.ref:
                ep = nxt
            else:
                if border and ep_ref > border:
                    date = None
                ep = FFItem(ep_ref, season=snum, episode=ep_ref.episode)
                ep.show_item = show
                ep.season_item = self
                vtag = ep.vtag
                vtag.setFirstAired(date)
                vtag.setTvShowTitle(self.vtag.getTvShowTitle())
                vtag.setEnglishTvShowTitle(self.vtag.getEnglishTvShowTitle())
            self.children_items.append(ep)
            enum += 1
        # if 1:  # --- XXX --- DEBUG --- XXX ---
        #     def ff2s(ff: FFItem | None) -> str:
        #         if ff is None:
        #             return 'None'
        #         return f'<{ff.ref:a}, date={ff.date}, title={ff.title!r}>'
        #     print(f'season {snum}:  date={self.date}, lst={ff2s(lst)}, nxt={ff2s(nxt)}, border={f"{border:a}" if border else "None"}')
        #     for ep in self.children_items:
        #         print(f' - ep {ep.ref.episode}: {ep.ref:a}, date={ep.date}, title={ep.title!r}')

    def get_season_item(self, season: int) -> FFItem | None:
        """Get child season item, if exists."""
        for sz in self.season_iter():
            if sz.season == season:
                return sz
        return None

    # def get_episode_item(self) -> FFItem | None:
    #     """Iterate over season episodes."""
    #     return iter(self.children_items if self.type == 'season' and self.children_items else ())

    def season_iter(self) -> Iterator[FFItem]:
        """Iterate over tv-show seasons."""
        return iter(self.children_items if self.type == 'show' and self.children_items else ())

    def episode_iter(self, *, special: bool = False, create_degraded: bool = False) -> Iterator[FFItem]:
        """Iterate over season episodes (for season) and over all non-special (or all if special=True) episodes (for show)."""
        if self.type == 'show' and self.season is None and self.episode is None:
            return iter((ep for sz in self.season_iter() if sz.season or special for ep in sz.episode_iter()))
        if self.type == 'season':
            if self.children_items:
                return iter(self.children_items)
            if create_degraded and self._children_count:
                self.degraded_episodes()
                if self.children_items:
                    return iter(self.children_items)
        return iter(())

    def episode_ref_iter(self, *, special: bool = False) -> Iterator[MediaRef]:
        """Iterate ref over season episodes (for season) and over all non-special (or all if special=True) episodes (for show). Works for degraded episodes."""
        if self.type == 'show' and self.season is None and self.episode is None:
            return iter((ep for sz in self.season_iter() if sz.season or special for ep in sz.episode_ref_iter()))
        if self.children_items is None:
            if self._children_count is not None:
                iter(self.ref.with_episode(enum) for enum in range(1, self._children_count + 1))
        elif self.type == 'season':
            return iter(ep.ref for ep in self.children_items)
        return iter(())

    def linear_episode_ref(self, number: int) -> MediaRef | None:
        """Get episode ref by linear episode number in the show (1..N)."""
        ref = self.ref
        if ref.is_show:
            if not self.children_items:
                return None
            for sz in self.season_iter():
                if sz.season:  # skip specials
                    if 0 < number <= sz.children_count:
                        return ref.with_season(sz.season or 0).with_episode(number)
                    number -= sz.children_count
        elif ref.type == 'show':
            if show := self.show_item:
                return show.linear_episode_ref(number)
        return None

    def linear_episode_number(self) -> int | None:
        """Get the episode linear number in the show (1..N)."""
        ref = self.ref
        if not ref.is_episode:
            return None
        if show := self.show_item:
            number = 0
            for sz in show.season_iter():
                if sz.season and sz.season == ref.season:  # skip specials
                    for ep in sz.episode_iter():
                        number += 1
                        if ep.episode == ref.episode:
                            return number
                    return None
                number += sz.children_count
        return None

    def item_tree_iter(self, *, itself: bool = False) -> Iterator[FFItem]:
        """Iterate over all sub-items."""
        if itself:
            return self
        for it in self.children_items or ():
            yield it
            yield from it.item_tree_iter()

    def item_dict(self) -> FFItemDict:
        """Return it self and all sub-items dict."""
        return {it.ref: it for it in self.item_tree_iter(itself=True)}

    def getVideoInfoTag(self):
        """Returns the VideoInfoTag for this item."""
        if self._vtag is None:
            if self.mode in (FMode.Command, FMode.Separator):
                self._vtag = FFVTag(InfoTagVideo())
            else:
                self._vtag = FFVTag(super().getVideoInfoTag())
        return self._vtag

    @property
    def mode(self) -> FMode:
        """Item mode (folder, playable...)."""
        return self._mode

    @mode.setter
    # @profiler('set ffitem mode', sort_by=['cumtime', 'tottime'], cumulative=True, builtins=True)
    def mode(self, mode: FMode) -> None:
        playable = mode in (mode.Playable, 'play', 'playable')
        folder = mode in (mode.Folder, 'folder', 'menu')
        if playable or self.getProperty('IsPlayable'):
            self.setProperty('IsPlayable', 'true' if playable else 'false')
        self.setIsFolder(folder)
        self._item_folder = folder

    @property
    def ffid(self) -> int | None:
        """Media DB ID."""
        return self._ffid

    @ffid.setter
    def ffid(self, value: int) -> None:
        """Media DB ID."""
        self._ffid = value
        vtag = self.getVideoInfoTag()
        vtag.setUniqueID(str(self._ffid), 'ffid')
        vtag.setUniqueID(f'{self.ref:a}', 'ffref')
        vid = VideoIds.from_ffid(self._ffid)
        if vid is not None:
            if (default := vid.service()) and not vtag.getUniqueID(default):
                vtag.setUniqueID(vid.kodi_ids()[default], default, True)
            # vtag.setUniqueIDs(vid.kodi_ids(), vid.service())
            if self._ffid in VideoIds.KODI:
                try:
                    vtag.setDbId(self._ffid)  # Kodi DBID
                except OverflowError:
                    from .log_utils import fflog_exc
                    fflog_exc()
                    fflog(f'FFID overflow: ffid={self._ffid}, {vid=}')

    @property
    def label(self) -> str:
        """Current item label."""
        return self.getLabel()

    @label.setter
    def label(self, label: str) -> None:
        self.setLabel(label)

    @property
    def info(self) -> LabelInfoValues:
        return self._info

    def get(self, info: str) -> Any:
        """Get single info value or another value or None if not exists."""
        if info == 'label':
            return self.getLabel()
        return self._info.get(info)

    def get_info(self, info: str) -> Any:
        """Get single info value or None if not exists."""
        return self._info.get(info)

    def set_info(self, info: str | LabelInfoValues, value: Any | None = None):
        """
        Set info value or info dict.

        set_info(name, value)
        set_info({'name': 'value', ...})
        """
        if isinstance(info, Mapping):
            if value is not None:
                raise TypeError('Usage: set_info(name, value) or set_info(dict)')
            self._info.update(info)
        else:
            self._info[info] = value
        info_type = self.info_type
        if info_type is None:
            info_type = 'video'
        self.setInfo(info_type, self._info)

    def setInfo(self, info_type: str, infoLabels: LabelInfoValues) -> None:
        """See Kodi ListItem.setInfo()."""
        if self.info_type is None:
            self.info_type = info_type
        if info_type != self.info_type:
            raise ValueError(f'Info label type mismatch {self.info_type!r} != {info_type!r}')
        if self.info_type is None:
            raise TypeError('setInfo: type is None')
        self._info.update(infoLabels)
        super().setInfo(self.info_type, self._info)

    @property
    def title(self) -> str:
        """Item media title."""
        return self.vtag.getTitle()

    @title.setter
    def title(self, title: str) -> None:
        return self.vtag.setTitle(title)

    @property
    def year(self) -> int | None:
        """Returns media year."""
        return self.vtag.getYear()

    @property
    def duration(self) -> int | None:
        """Returns video duration or None."""
        return self.vtag.getDuration() or None

    @property
    def date(self) -> dt_date | None:
        vtag = self.vtag
        ds = vtag.getFirstAiredDate()
        if not ds:
            ds = vtag.getPremieredDate()
        if not ds and self._guessed_date:
            ds = self._guessed_date
        if not ds and (year := vtag.getYear()):
            rtype = self.ref.real_type
            if rtype == 'movie' and const.indexer.movies.date_from_year:
                return dt_date(year, 1, 1)
            elif rtype == 'episode' and const.indexer.episodes.date_from_year:
                return dt_date(year, 1, 1)
        return ds

    @property
    def date_timestamp(self) -> int | None:
        d = self.date
        if d:
            try:
                return int(dt_timestmap(d))
            except OSError:
                # On Windows platform, C time range can sometimes be restricted.
                # See ff.callendar.utc_timestamp().
                return 0
        return None

    def aired_before(self, date: dt_date | datetime | None = None) -> bool:
        """True, if episode is aired/premiered before date (or now) inclusive."""
        aired = self.date
        if aired is None:
            rtype = self.ref.real_type
            if rtype == 'movie':
                return not const.indexer.movies.future_if_no_date
            elif rtype == 'episode':
                return not const.indexer.episodes.future_if_no_date
            elif rtype == 'season':
                return not const.indexer.seasons.future_if_no_date
            elif rtype == 'show':
                return not const.indexer.tvshows.future_if_no_date
            return True  # we don't know
        if date is None:
            date = dt_date.today()
        elif is_datetime_instance(date):
            date = date.date()
        return aired <= date

    @property
    def unaired(self) -> bool:
        """True, if episode is not aired."""
        return not self.aired_before()

    def setProperties(self, values: LabelPoperties) -> None:
        """See Kodi ListItem.setProperties()."""
        values = {k.lower(): v for k, v in values.items()}
        self._props.update(values)
        super().setProperties(values)

    def getProperties(self) -> LabelPoperties:
        """Get all properties."""
        return dict(self._props)

    def setProperty(self, key: str, value: str) -> None:
        """See Kodi ListItem.setProperty()."""
        key = key.lower()
        self._props[key] = value
        super().setProperty(key, '' if value is None else str(value))

    @overload
    def getArt(self, key: str) -> str: ...

    @overload
    def getArt(self) -> ArtValues: ...

    def getArt(self, key: str | None = None) -> str | ArtValues:
        """Get the listitem's art by key or all art values if key is None."""
        if key is None:
            return dict(self._art)
        return super().getArt(key)

    def setArt(self, values: ArtValues) -> None:
        """Set the listitem's art."""
        self._art = dict(values)
        super().setArt(values)

    def getAvailableFanart(self) -> list[ArtValues]:
        """Get available images (needed for video scrapers)."""
        return deepcopy(self._available_fanart)

    def setAvailableFanart(self, images: List[ArtValues]) -> None:
        """Set available images (needed for video scrapers)."""
        self._available_fanart = [dict(im) for im in images]
        super().setAvailableFanart(images)

    @property
    def season(self) -> int | None:
        """Season number or None."""
        value = self.getVideoInfoTag().getSeason()
        return None if value < 0 else value

    @season.setter
    def season(self, season: int | None) -> None:
        vtag = self.getVideoInfoTag()
        if season is None:
            vtag.setSeason(-1)
        else:
            vtag.setSeason(int(season))
        vtag.setUniqueID(f'{self.ref:a}', 'ffref')

    @property
    def episode(self) -> int | None:
        """Season number or None."""
        value = self.getVideoInfoTag().getEpisode()
        return None if value < 0 else value

    @episode.setter
    def episode(self, episode: int | None) -> None:
        vtag = self.getVideoInfoTag()
        if episode is None:
            vtag.setEpisode(-1)
        else:
            vtag.setEpisode(int(episode))
        vtag.setUniqueID(f'{self.ref:a}', 'ffref')

    @property
    def show_ref(self) -> MediaRef | None:
        """Return show ref for show, season and episode."""
        return self.ref.show_ref

    @property
    def season_ref(self) -> MediaRef | None:
        """Return season ref for season and episode."""
        return self.ref.season_ref

    # def season_item(self) -> FFItem | None:
    #     """Return season item (for episodes)."""
    #     return self._season_item

    # def show_item(self) -> FFItem | None:
    #     """Return tv-show item (for seasons and episodes)."""
    #     return self._show_item

    @property
    def children_count(self) -> int:
        """Declaration of children count. Used in degraded items (e.g. seasons got form show details)."""
        if self.children_items is not None:
            return len(self.children_items)
        return self._children_count or 0

    @property
    def aired_episodes_count(self) -> int:
        """Declaration of episodes count. Used in deep degraded items (number of episodes in the show)."""
        if self._aired_episodes_count is None:
            ref = self.ref
            next_episode_to_air = self.next_episode_to_air
            if next_episode_to_air is None and self.show_item:
                next_episode_to_air = self.show_item.next_episode_to_air
            if ref.type != 'show':
                self._aired_episodes_count = 0
            elif ref.is_episode:
                self._aired_episodes_count = 1
            elif next_episode_to_air is None:  # no info about next episode...
                # we try use all episode count (it's good enough)
                if self.episodes_count is not None:
                    self._aired_episodes_count = self.episodes_count
                else:
                    # we count all episodes
                    seasons = (self,) if ref.is_season else self.children_items or ()
                    self._aired_episodes_count = sum(sz.children_count for sz in seasons)
            else:
                nxt = next_episode_to_air.ref
                assert nxt.season is not None
                assert nxt.episode is not None
                if ref.is_season:
                    # one season
                    assert ref.season is not None
                    if ref.season < nxt.season:
                        self._aired_episodes_count = self.children_count  # this season is before airing season, then is aired
                    elif ref.season > nxt.season:
                        self._aired_episodes_count = 0                    # this season is after airing season, then is not aired
                    else:
                        # the same season, check episodes (continuous episode counting, starting from one)
                        self._aired_episodes_count = nxt.episode - 1
                else:
                    # the show
                    self._aired_episodes_count = (sum(sz.children_count                          # episodes in all old seasons
                                                      for sz in self.season_iter()
                                                      if 0 < (sz.ref.season or 0) < nxt.season)
                                                  + nxt.episode - 1)                             # + old episodes in airing season
        return self._aired_episodes_count

    @aired_episodes_count.deleter
    def aired_episodes_count(self) -> None:
        self._aired_episodes_count = None

    def surrogate_episodes(self, *, aired: bool = False) -> Iterator[FFItem]:
        """Iterate over surrogate episodes (episodes could be degenerated FFItem, no title only number and guessed air date)."""
        ref: MediaRef = self.ref
        if ref.type != 'show':
            return
        if ref.is_episode:
            yield self
            return
        # if ref.is_season:
        #     fflog.warning(f'Season {ref:a} is not suppoerted in FFItem.surrogate_episodes()')
        #     return
        if ref.is_season:
            seasons = (self,)
            show_item = self.show_item
        else:
            seasons = tuple(sz for sz in self.children_items or () if sz.ref.is_season)
            show_item = self
        if not seasons:
            return
        next_episode_to_air = self.next_episode_to_air
        if next_episode_to_air is None and self.show_item:
            next_episode_to_air = self.show_item.next_episode_to_air
        # no info about next episode...
        if next_episode_to_air is None:
            # next episode is after all seasons, it means all episodes are aired
            nxt = ref.with_season((seasons[-1].ref.season or 0) + 1).with_episode(1)
        else:
            nxt = next_episode_to_air.ref
        # iterate over seasons and their episodes
        for sz in seasons:
            sz_num = sz.ref.season or 0
            if sz.children_items:
                for ep in sz.children_items:
                    ep_num = ep.ref.episode or 0
                    if not aired or (0 < sz_num < (nxt.season or 0)) or (sz_num == nxt.season and (0 < ep_num < (nxt.episode or 0))):
                        yield ep
            else:
                # we count all episodes
                for ep_num in range(1, sz.children_count + 1):
                    if not aired or (0 < sz_num < (nxt.season or 0)) or (sz_num == nxt.season and (0 < ep_num < (nxt.episode or 0))):
                        ep = FFItem(sz.ref.with_episode(ep_num))
                        ep.show_item = show_item
                        ep.season_item = sz
                        if date := sz.date:
                            ep.vtag.setFirstAired(date)
                        yield ep

    def get_episode_type(self) -> EpisodeType:
        """Get (guess) episode type. See #180."""
        ref = self.ref
        if not ref.is_episode:
            return ''
        assert ref.season is not None
        assert ref.episode is not None
        # Case 1. Known episode type.
        if episode_type := self.vtag.getEpisodeType():
            return episode_type
        if show := self.show_item:
            # Case 2. Last aired.
            if (last := show.last_episode_to_air) and last.ref == ref:
                return last.vtag.getEpisodeType() or 'standard'
            # Case 3. Next to air.
            if (ep := show.next_episode_to_air) and ep.ref == ref:
                return ep.vtag.getEpisodeType() or 'standard'
        if ref.episode == 1:
            # Case 4. First episode of first season.
            if ref.season == 1:
                return 'series_premiere'
            # Case 5. First episode of non-first season.
            return 'season_premiere'
        if show := self.show_item:
            # last episode ...
            if (sz := show.get_season_item(ref.season)) and sz.children_count == ref.episode:
                # Case 6. Last episode of non-last season.
                if show.get_season_item(ref.season + 1):
                    return 'season_finale'
        # Case 7. All others.
        return 'standard'

    def copy_art_from(self, *items: FFItem | None, all: bool = False) -> None:
        """Copy main (or all) art images from given items."""
        if all:
            ...
        is_episode = self.ref.is_episode
        art = self.getArt()  # no key, get all art images (FFItem extension)
        for key in self.ArtLabels:
            if not art.get(key):
                for it in items:
                    if it and (img := it.getArt(key)):
                        if key == 'thumb' and is_episode != it.ref.is_episode:
                            continue
                        art[key] = img
                        break
        # support for tvshow.poster and tvshow.fanart
        tvshow_art_labels = ('poster', 'landscape', 'fanart')
        for key in tvshow_art_labels:
            tvkey = f'tvshow.{key}'
            if not art.get(tvkey):
                for it in items:
                    if it and it.ref.is_show and (img := it.getArt(key)):
                        art[tvkey] = img
                        break
        if not art.get('thumb') and (thumb := art.get('landscape' if is_episode else 'poster')):
            art['thumb'] = thumb
        if art:
            self.setArt(art)

    def copy_from(self, *items: FFItem | None) -> None:
        """Copy data (art, description etc.) from given items."""
        vtag = self.vtag
        what_to_copy = [
            ('getPlotBase', vtag.setPlotBase),
            ('getGenres', vtag.setGenres),
            ('getMpaa', vtag.setMpaa),
            ('getActors', vtag.setActors),
            ('getStudios', vtag.setStudios),
            ('getCountries', vtag.setCountries),
            ('getDirectors', vtag.setDirectors),
            ('getTrailer', vtag.setTrailer),
        ]
        if const.core.info.copy_year:
            what_to_copy.append(('getYear', vtag.setYear))
        if not self.ref.is_episode:
            what_to_copy.append(('getTagLine', vtag.setTagLine))
        for getter_name, setter in what_to_copy:
            if not getattr(vtag, getter_name)():
                for it in items:
                    if it:
                        val = getattr(it.vtag, getter_name)()
                        if val:
                            setter(val)
                            break

        self.copy_art_from(*items)

    @property
    def position(self) -> SortPosition:
        """Get special sort position."""
        pos = self.getProperty('SpecialSort')
        return pos if pos in ('top', 'bottom') else 'normal'

    @position.setter
    def position(self, pos: SortPosition) -> None:
        """Get special sort position."""
        self.setProperty('SpecialSort', pos if pos in ('top', 'bottom') else '')

    @property
    def ref(self) -> MediaRef:
        """Return media reference. Real ref, `season` and `episode` could be different."""
        return self._ref

    @property
    def video_ids(self) -> VideoIds:
        """Return VideoIds()."""
        return self.ref.video_ids

    @property
    def ids(self) -> IdsDict:
        """Returns trakt.tv know IDs."""
        ids = self.vtag.getUniqueIDs()
        return {k: int(v) if v.isdecimal() else v
                for k in ('tmdb', 'imdb', 'trakt', 'tvdb', 'slug')
                if (v := ids.get(k))}  # type: ignore  -- number are converted to int

    @overload
    def get_id(self, key: Literal['tmdb', 'trakt', 'tvdb', 'dbid', 'ff/volatile']) -> int | None: ...

    @overload
    def get_id(self, key: Literal['imdb', 'slug']) -> str | None: ...

    @overload
    def get_id(self, key: str) -> int | str | None: ...  # mdblist (str for madia and int for list) and all unknown

    def get_id(self, key: str) -> int | str | None:
        """Get ID by key. More services than FFItem.ids Convert to int if ID is decimal."""
        ids = self.vtag.getUniqueIDs()
        val = ids.get(key)
        if val is not None and val.isdecimal():
            return int(val)
        return val

    @property
    def tmdb_id(self) -> int | None:
        """Get TMDB ID."""
        tmdb = self.vtag.getUniqueID('tmdb')
        return int(tmdb) if tmdb and tmdb.isdecimal() else None

    @property
    def aliases(self) -> Sequence[str]:
        """Returns all title aliases (from all countries)."""
        return tuple(a.title for a in self.aliases_info)

    @property
    def first_episode_number(self) -> int:
        """Get first episode number in season (for proper episode numbering). The season may start from different number than 1."""
        return self._first_episode_number

    @property
    def continuous_episode_number(self) -> bool:
        """Continuous episode numbering in show (for proper episode numbering). Episodes are numbered continuously in show, not per season."""
        return self._continuous_episode_number

    def absolute_episode_number(self) -> int | None:
        """Get absolute episode number (if exists)."""
        ref = self.ref
        if not ref.is_episode or not self.show_item:
            return None
        show = self.show_item
        snum = ref.season or 0
        enum = ref.episode or 0
        # If continuous numbering, episode number is already absolute
        if show.continuous_episode_number:
            return enum
        # count all previous seasons episodes
        index = sum(sz.children_count for sz in show.season_iter()
                    if sz.ref.season and sz.ref.season < snum)
        # check if previous seasons exists
        if snum > 1 and not index:
            return None
        # add current season episodes
        return index + enum

    def total_episode_count(self, *, only_aired: bool = False, date: dt_date | datetime | None = None) -> int:
        """Get total episode count (if exists)."""
        ref = self.ref
        if ref.type != 'show':
            return 0
        show = self.show_item or self
        if only_aired:
            # get date once
            if date is None:
                date = dt_date.today()
            elif is_datetime_instance(date):
                date = date.date()
            raise NotImplementedError('total_episode_count(only_aired=True) is not implemented yet')
        else:
            # count all seasons episodes
            return sum(sz.children_count for sz in show.season_iter() if sz.ref.season)

    def season_episode_count(self, season: int | None, *, only_aired: bool = False, date: dt_date | datetime | None = None) -> int:
        """Get total episode count for season (if exists)."""
        ref = self.ref
        if ref.type != 'show' or season is None:
            return 0
        show = self.show_item or self
        for sz in show.season_iter():
            if sz.ref.season == season:
                break
        else:
            return 0
        if only_aired:
            # get date once
            if date is None:
                date = dt_date.today()
            elif is_datetime_instance(date):
                date = date.date()
            raise NotImplementedError('season_episode_count(only_aired=True) is not implemented yet')
        else:
            # the season episodes
            return sz.children_count

    def getMimeType(self) -> str:
        """Get the listitem's mimetype if known."""
        return self._mime_type

    def setMimeType(self, mimetype: str) -> None:
        """Set the listitem's mimetype if known."""
        self._mime_type = mimetype
        super().setMimeType(mimetype)

    def clone(self) -> FFItem:
        """Clone FFItem."""
        data = self.__to_json__()
        item = FFItem(self.ref)
        item.__set_json__(data)
        item.progress = self.progress
        return item

    def __eq__(self, other: Any) -> bool:
        try:
            to_json = other.__to_json__
        except AttributeError:
            pass
        else:
            return self.__to_json__() == to_json()
        return False

    if TYPE_CHECKING:
        @classmethod
        def __from_json__(cls, data: JsonData) -> Self: ...
        def __to_json__(self) -> JsonData: ...
        def __set_json__(self, data: JsonData) -> None: ...


class FFFolder(FFItem):
    """Folder list item."""

    def __init__(self, name: str, *, mode: FMode | None = None, **kwargs) -> None:
        assert mode is None or mode == FMode.Folder
        super().__init__(name, mode=FMode.Folder)


class FFPlayable(FFItem):
    """Playable list item."""

    def __init__(self, name: str, *, mode: FMode | None = None, **kwargs) -> None:
        assert mode is None or mode == FMode.Playable
        super().__init__(name, mode=FMode.Playable)
