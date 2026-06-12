"""Tiny TheMovieDB.org API wrapper."""

from __future__ import annotations
from sys import version_info as python_version_info
import re
from datetime import datetime, date as dt_date, timedelta
from threading import Lock, Semaphore
from collections import UserDict
from itertools import chain
from queue import Queue
from urllib.parse import urljoin
from enum import Enum, Flag, auto as auto_enum
from time import monotonic
from typing import Optional, Union, Any, Tuple, List, Dict, Sequence, Iterable, Iterator, Mapping, Callable, ClassVar
from typing import Generic, Generator, ClassVar, Type, TypeVar, overload, TYPE_CHECKING
from typing_extensions import Literal, get_args as get_typing_args, TypedDict, Unpack, NotRequired, TypeAlias, Self, Pattern, cast
# from typing_extensions import Annotated
from attrs import define, frozen, field, evolve

from ..defs import VideoIds, MediaRef, MediaType
from ..defs import RefType, MainMediaType, MainMediaTypeList, ItemList, SearchType, FFRef
from ..ff.db.playback import MediaPlayInfoDict
from ..ff.item import FFItem, EpisodeType, FFEpisodeGroup, FFEpisodeGroupType
from ..ff.types import JsonData, JsonResult, KwArgs, Params, Headers
from ..ff.calendar import fromisoformat
from ..ff.tricks import join_items, batched, jwt_decode
from ..ff.kotools import xsleep
from ..ff.log_utils import fflog
from ..ff.debug import logtime
from ..ff.control import max_thread_workers
from ..ff.requests import RequestsPoolExecutor, clear_netcache
from ..ff import requests
from cdefs import InfoDetails
from const import const

if TYPE_CHECKING:
    from ..ff.requests import CacheArg, Method
    from concurrent.futures import Future


if not TYPE_CHECKING:
    if python_version_info < (3, 9):
        class UserDict(UserDict, Generic[TypeVar('K'), TypeVar('V')]):
            """Backport of typing.Generic for UserDict."""
            pass


T = TypeVar('T')

DialogId: TypeAlias = Any
ApiVer: TypeAlias = Literal[3, 4, None]

TmdbId: TypeAlias = int
MovieId: TypeAlias = TmdbId
ShowId: TypeAlias = TmdbId
SeasonId: TypeAlias = TmdbId
EpisodeId: TypeAlias = TmdbId
SeasonNumber: TypeAlias = int
EpisodeNumber: TypeAlias = int
# TmdbTvIdKey = Tuple[ShowId, Optional[SeasonId], Optional[EpisodeId]]
# TmdbTvIds = Dict[TmdbTvIdKey, TmdbId]

#: TMDB get info content type.
TmdbContentType: TypeAlias = Literal['movie', 'tv']
TmdbContentPluralType: TypeAlias = Literal['movies', 'tv']
TmdbContentRatingType: TypeAlias = Literal['movies', 'tv', 'episodes']
#: TMDB get info content type.
TmdbListType: TypeAlias = Literal[
    'top_rated',    # ?include_adult=false&include_video=false&language=en-US&page=1&sort_by=vote_average.desc&without_genres=99,10755&vote_count.gte=200
    'popular',      # ?include_adult=false&include_video=false&language=en-US&page=1&sort_by=popularity.desc
    'now_playing',  # ?include_adult=false&include_video=false&language=en-US&page=1&sort_by=popularity.desc&with_release_type=2|3&release_date.gte={min_date}&release_date.lte={max_date}
    'upcoming',     # ?include_adult=false&include_video=false&language=en-US&page=1&sort_by=popularity.desc&with_release_type=2|3&release_date.gte={min_date}&release_date.lte={max_date}
]

TimeWindow: TypeAlias = Literal['week', 'day']

TmdbConfName: TypeAlias = Literal['countries', 'jobs', 'languages', 'primary_translations', 'timezones']

ExternalIdType: TypeAlias = Literal['imdb']

TmdbSearchType: TypeAlias = Literal['collection', 'company', 'keyword', 'movie', 'multi', 'person', 'tv']

PersonDataType: TypeAlias = Literal['combined_credits', 'movie_credits', 'tv_credits']

DetailsAllowed: TypeAlias = Literal['movie', 'show', 'person', 'collection', 'company', 'keyword', 'network']

MediaResource: TypeAlias = Literal['recommendations', 'similar']

AccountId: TypeAlias = Union[int, Literal['me']]

#: Users general list.
UserGeneralListType: TypeAlias = Literal['favorite', 'watchlist']
#: TMDB JSON item data.
TmdbItemJson: TypeAlias = Dict[str, Any]
#: Media JSON data.
TmdbMediaDataDict: TypeAlias = Dict[MediaRef, TmdbItemJson]
#: TMDB sort_by values (movie).
TmdbMovieSortBy: TypeAlias = Literal['original_title.asc', 'original_title.desc',
                                     'popularity.asc', 'popularity.desc',
                                     'revenue.asc', 'revenue.desc',
                                     'primary_release_date.asc', 'primary_release_date.desc',
                                     'title.asc', 'title.desc',
                                     'vote_average.asc', 'vote_average.desc',
                                     'vote_count.asc', 'vote_count.desc',
                                     ]
#: TMDB sort_by values (tv).
TmdbTvSortBy: TypeAlias = Literal['first_air_date.asc', 'first_air_date.desc',
                                  'name.asc', 'name.desc',
                                  'original_name.asc', 'original_name.desc',
                                  'popularity.asc', 'popularity.desc',
                                  'vote_average.asc', 'vote_average.desc',
                                  'vote_count.asc', 'vote_count.desc',
                                  ]
#: TMDB sort_by values.
TmdbSortBy: TypeAlias = Literal['original_title.asc', 'original_title.desc',              # movie only
                                'revenue.asc', 'revenue.desc',                            # movie only
                                'primary_release_date.asc', 'primary_release_date.desc',  # movie only
                                'title.asc', 'title.desc',                                # movie only
                                'first_air_date.asc', 'first_air_date.desc',              # tv-show only
                                'name.asc', 'name.desc',                                  # tv-show only
                                'original_name.asc', 'original_name.desc',                # tv-show only
                                'popularity.asc', 'popularity.desc',
                                'vote_average.asc', 'vote_average.desc',
                                'vote_count.asc', 'vote_count.desc',
                                ]


@frozen
class TmdbCredentials:
    """TMDB credentials."""

    #: Optional TMDB api-key for get more tv-show info.
    api_key: Optional[str] = None
    #: Optional TMDB v4 api bearer JWT token.
    bearer: Optional[str] = None
    #: User name.
    user: Optional[str] = None
    #: User password.
    password: Optional[str] = None
    #: User session.
    session_id: Optional[str] = None
    #: Access token (v4).
    access_token: Optional[str] = None

    def __bool__(self) -> bool:
        """Return True if credentials are defined (user is logged)."""
        return bool(self.session_id) or bool(self.access_token)

    @property
    def v3(self) -> bool:
        """Return True if credentials are defined (user is logged) in v3 API."""
        return bool(self.session_id)

    @property
    def v4(self) -> bool:
        """Return True if credentials are defined (user is logged) in v4 API."""
        return bool(self.access_token)

    @property
    def account_id(self) -> str:
        """Return account_id from v4 access_token."""
        if self.access_token:
            return jwt_decode(self.access_token).get('sub', '')
        return ''


class GetImageMode(Enum):
    """Mode of image getting."""
    #: append  - use `append_to_response=images` with fixed `include_image_language`, no poster at all often
    APPEND = 'append'
    #: append  - use `append_to_response=images` with fixed `include_image_language=en.
    APPEND_EN = 'append_en'
    #: append  - use `append_to_response=images` with fixed `include_image_language={lang}.
    APPEND_LANG = 'append_lang'
    #: pull    - like `append` but gen images in next request if fails (no images)
    PULL = 'pull'
    #: full    - always make two requests, support all services (it is forced for non-tmdb services, e.g. fanart.tv)
    FULL = 'full'
    #: all     - use /images to get all images in concurrent request
    ALL = 'all'


@frozen
class PersonCredits:
    """Person credits result."""

    cast: tuple[FFItem, ...] = ()
    crew: tuple[FFItem, ...] = ()


class Condition(Generic[T]):
    """TMDB discovery conditions."""

    RX_EXPR: ClassVar[Pattern[str]] = re.compile(r'^\s*(\w+)\s*([<>=]=?)\s*(.*\S)\s*$')

    def __init__(self, type: Type[T], *, cond: Optional[List[Tuple[str, str]]] = None) -> None:
        self.type: Type[T] = type
        self.cond: List[Tuple[str, str]] = [] if cond is None else cond

    def range(self, min: T, max: T) -> 'Condition':
        return Condition(self.type, cond=[('gte', str(min)), ('lte', str(max))])

    def _add(self, val: T, a: int) -> T:
        if isinstance(val, dt_date):
            return val + timedelta(days=a)
        if isinstance(val, int):
            return val + a
        if isinstance(float, int):
            return val + .001 * a
        return val

    def __repr__(self) -> str:
        return f'Conditional(type={self.type}, cond={self.cond})'

    def __str__(self) -> str:
        s = ' & '.join(f'{k}={v}' for k, v in self.cond)
        return f'Conditional({s})'

    def __le__(self, other: T) -> 'Condition':
        if type(other) is not self.type:
            try:
                other = self.type(other)
            except Exception:
                return Condition(self.type)
        return Condition(self.type, cond=[('lte', str(other))])

    def __ge__(self, other: T) -> 'Condition':
        if type(other) is not self.type:
            try:
                other = self.type(other)
            except Exception:
                return Condition(self.type)
        return Condition(self.type, cond=[('gte', str(other))])

    def __lt__(self, other: T) -> 'Condition':
        if type(other) is not self.type:
            try:
                other = self.type(other)
            except Exception:
                return Condition(self.type)
        return Condition(self.type, cond=[('lte', str(self._add(other, -1)))])

    def __gt__(self, other: T) -> 'Condition':
        if type(other) is not self.type:
            try:
                other = self.type(other)
            except Exception:
                return Condition(self.type)
        return Condition(self.type, cond=[('gte', str(self._add(other, 1)))])

    def __and__(self, other: 'Condition[T]') -> 'Condition':
        if isinstance(other, Condition):
            return Condition(self.type, cond=[*self.cond, *other.cond])
        return NotImplemented

    @classmethod
    def filter_from_str_expr(cls, expr: str) -> Tuple[str, Union[str, Self, None]]:
        """Return condition from expr. Try use numbers."""
        if mch := cls.RX_EXPR.fullmatch(expr):
            a, o, b = mch.groups()
            if o in ('=', '=='):
                return a, b
            if o == '<':
                return a, cls(str) < b  # type: ignore [reportOperatorIssue]
            if o == '<=':
                return a, cls(str) <= b  # type: ignore [reportOperatorIssue]
            if o == '>':
                return a, cls(str) > b  # type: ignore [reportOperatorIssue]
            if o == '>=':
                return a, cls(str) >= b  # type: ignore [reportOperatorIssue]
        return expr, None

    @classmethod
    def filters_from_str_expr_list(cls, expresions: Iterable[str]) -> 'DiscoveryFilters':
        allowed = TmdbApi.DISCOVER_FILTERS
        filters = {k: v for val in expresions
                   for k, v in (cls.filter_from_str_expr(val),) if k in allowed and v is not None}
        return cast('DiscoveryFilters', filters)


# RangeCondition: TypeAlias = Annotated[T, Condition]


class DiscoveryFilters(TypedDict):
    """Filter arguments for TMDB discovery."""

    air_date: NotRequired[Condition[dt_date]]
    first_air_date: NotRequired[Condition[dt_date]]
    first_air_year: NotRequired[int]
    include_null_first_air_dates: NotRequired[bool]
    screened_theatrically: NotRequired[bool]
    timezone: NotRequired[str]
    with_networks: NotRequired[int]
    with_status: NotRequired[Union[str, Literal[0, 1, 2, 3, 4, 5]]]  # Returning Series: 0 Planned: 1 In Production: 2 Ended: 3 Cancelled: 4 Pilot: 5
    with_type: NotRequired[Union[str, Literal[0, 1, 2, 3, 4, 5, 6]]]

    certification: NotRequired[Union[str, Condition[str]]]
    certification_country: NotRequired[str]
    include_adult: NotRequired[bool]
    include_video: NotRequired[bool]
    language: NotRequired[str]
    primary_release_year: NotRequired[int]
    primary_release_date: NotRequired[Condition[dt_date]]
    region: NotRequired[str]
    release_date: NotRequired[Condition[dt_date]]
    sort_by: NotRequired[TmdbSortBy]
    vote_average: NotRequired[Condition[float]]
    vote_count: NotRequired[Condition[int]]
    watch_region: NotRequired[str]
    with_cast: NotRequired[str]
    with_companies: NotRequired[str]
    with_crew: NotRequired[str]
    with_genres: NotRequired[str]
    with_keywords: NotRequired[str]
    with_origin_country: NotRequired[str]
    with_original_language: NotRequired[str]
    with_people: NotRequired[str]
    with_release_type: NotRequired[Union[str, Literal[1, 2, 3, 4, 5, 6]]]
    with_runtime: NotRequired[Condition[int]]
    with_text_query: NotRequired[str]
    with_watch_monetization_types: NotRequired[Union[str, Literal['flatrate', 'free', 'ads', 'rent', 'buy']]]
    with_watch_providers: NotRequired[str]
    without_companies: NotRequired[str]
    without_genres: NotRequired[str]
    without_keywords: NotRequired[Union[str, int, Sequence[int]]]
    without_watch_providers: NotRequired[str]
    year: NotRequired[int]


class SearchFilters(TypedDict):
    """Filter arguments for TMDB search."""

    include_adult: NotRequired[bool]
    primary_release_year: NotRequired[int]
    year: NotRequired[int]
    first_air_date_year: NotRequired[int]
    region: NotRequired[str]


class TmdbRequestKwargs(TypedDict):
    credentials: NotRequired[Optional[TmdbCredentials]]
    api_version: NotRequired[Literal[3, 4]]
    params: NotRequired[Optional[KwArgs]]
    append_to_response: NotRequired[Tuple[str, ...]]
    lang: NotRequired[Optional[str]]
    expected_errors: NotRequired[Sequence[int]]
    cache: NotRequired[Optional[str]]


@define
class TmdbApiStats:
    request_count: int = 0
    extra_seasons_count: int = 0
    multi_seasons_count: int = 0


@define
class TmdbProvider:
    id: int
    name: str
    logo: Optional[str] = None
    display_priority: int = 0


@frozen(kw_only=True)
class MediaVideo:
    lang: str
    name: str
    key: str
    site: str
    type: str
    official: bool
    published_at: datetime

    # @property
    # def locale(self) -> str:
    #     return f'{self.lang}-{self.country}'


class SkelOptions(Flag):
    """TMDB skeleton options."""

    NONE = 0
    #: Get translate info.
    #: Not need to set, ffinfo.get_en_skel_items() sets is if `locale` is used.
    TRANSLATIONS = auto_enum()
    #: Get info for first few seasons.
    #: It is useful only if show has up to 18 seasons but it do not any extra request.
    SHOW_FIRST_SEASONS = auto_enum()
    #: Get info for last few seasons.
    #: Seasons details depend on number of seasons. Skel try to get a few first seasons (more then 18) with main show request.
    #: If there is more seasons skel request a few last seasons (more then 18), because now skel knows number of seasons.
    SHOW_LAST_SEASONS = auto_enum()
    #: Get info for all seasons.
    #: Skel try to get a few first seasons (more then 18) with main show request then requests next season group one by one.
    SHOW_ALL_SEASONS = auto_enum()
    #: Approximate the episode air date as season air date.
    #: It is used only if there is no the season's details.
    SHOW_EPISODE_DATE_FIRST = auto_enum()
    #: Approximate the episode air date as last emitted episode or day before next season air date.
    #: It is used only if there is no the season's details.
    SHOW_EPISODE_DATE_LAST = auto_enum()
    #: Approximate the episode air date as linear date between SHOW_EPISODE_DATE_FIRST and SHOW_EPISODE_DATE_LAST.
    #: It is used only if there is no the season's details.
    SHOW_EPISODE_DATE_APPROXIMATE = auto_enum()


# MediaRequestKey: TypeAlias = Tuple[MediaRef, InfoDetails, str, Tuple[int, ...], Tuple[str, ...]]  # (ref, details, locale, seasons, append_to_response)
MediaRequestKey: TypeAlias = Tuple[MediaRef, str]  # (ref, locale)
LOCALE_EN = 'en-US'
LOCALE_ORIG = '_ORIG_'


@define(kw_only=True)
class MediaRequest:
    """Media request."""

    # --- input ---

    #: Media reference (or ffitem).
    item: FFRef = field(kw_only=False)
    #: Info details flags.
    details: InfoDetails = field(default=InfoDetails.DEFAULT, kw_only=False)
    #: Locale (language) for get info, e.g. 'pl-PL'. If empty, use default (API locale).
    locale: str = ''  # e.g. 'pl-PL'

    @property
    def ref(self) -> MediaRef:
        """Media item reference."""
        return self.item.ref

    @property
    def ffitem(self) -> FFItem | None:
        """Media item reference."""
        return self.item if isinstance(self.item, FFItem) else None

    # --- internal ---

    seasons: Iterable[int] | None = None
    # key: str = ''
    path: str = ''
    _append_to_response: list[str] | None = None
    params: Params = field(factory=dict)
    data: JsonData = field(factory=dict)
    _key: MediaRequestKey | None = field(default=None, init=False, repr=False, eq=False, hash=False)

    @classmethod
    def new(cls, item: FFRef | MediaRequest, *,
            details: InfoDetails | None = None, locale: str | None = None,
            default_details: InfoDetails = InfoDetails.DEFAULT, default_locale: str = '',
            ) -> MediaRequest:
        if isinstance(item, MediaRequest):
            if details is None:
                details = item.details
            if locale is None:
                locale = item.locale or ''
            return item.copy(details=details, locale=locale)
        if details is None:
            details = default_details
        if locale is None:
            locale = default_locale or ''
        return cls(item=item, details=details, locale=locale or '')

    def copy(self, *, item: FFRef | None = None, details: InfoDetails | None = None, locale: str | None = None) -> MediaRequest:
        """Duplicate object."""
        if item is None:
            item = self.item
        if details is None:
            details = self.details
        if locale is None:
            locale = self.locale or ''
        return evolve(self, item=item, details=details, locale=locale)

    @property
    def key(self) -> MediaRequestKey:
        """Unique request key."""
        if self._key is None:
            # self._key = (self.ref, self.details, self.locale or '', tuple(sorted(self.seasons)) if self.seasons else (),
            #              tuple(sorted(self.append_to_response)))
            self._key = (self.ref, self.locale or '')
        return self._key

    @property
    def append_to_response(self) -> list[str]:
        """Append to response values."""
        if self._append_to_response is None:
            self._append_to_response = self.create_append_to_response()
        return self._append_to_response

    @property
    def need_create_append_to_response(self) -> bool:
        """Return True if need to initialize append_to_response."""
        return self._append_to_response is None

    def create_append_to_response(self) -> list[str]:
        """Append items to `append_to_response` list with respect of limit."""
        details = self.details
        seasons = self.seasons
        tv_seasons = tuple(seasons or ())
        ref = self.ref
        rtype = ref.real_type
        imode = GetImageMode(const.tmdb.get_image_mode)

        respapp = []
        if details & InfoDetails.COMMON_DETAILS:
            respapp.extend(('external_ids',))
        if details & InfoDetails.MEDIA_DETAILS:
            respapp.extend(('release_dates', 'keywords'))
            if details & InfoDetails.AGGREGATE_CREDITS and ref.real_type in ('show', 'season'):
                respapp.append('aggregate_credits')
            elif ref.real_type in ('movie', 'show', 'season', 'episode'):
                respapp.append('credits')
            if const.media.aliases_service == 'tmdb':
                respapp.append('alternative_titles')
        elif details & InfoDetails.AGGREGATE_CREDITS and ref.real_type in ('show', 'season') and 'aggregate_credits' not in respapp:
            respapp.append('aggregate_credits')
        if details & InfoDetails.IMAGES:
            if imode in (GetImageMode.APPEND, GetImageMode.APPEND_EN, GetImageMode.APPEND_LANG, GetImageMode.PULL):
                respapp.append('images')
        if details & InfoDetails.VIDEOS:
            respapp.append('videos')
        if details & InfoDetails.TRANSLATIONS:
            respapp.append('translations')
        if details & InfoDetails.EPISODE_GROUPS and rtype == 'show':
            respapp.append('episode_groups')
        # append a few seasons with episodes to single request (sic!)
        if details & InfoDetails.SHOW_SEASONS and rtype == 'show':  # append a few existing seasons with episodes to single request (sic!)
            # see: https://www.themoviedb.org/talk/63ee22b4699fb7009e3e5102
            scount = const.tmdb.append_to_response_limit - len(respapp)
            respapp.extend(f'season/{i+1}' for i in range(scount))
        if tv_seasons and rtype == 'show':  # append a few requestet seasons to single request (sic!)
            scount = const.tmdb.append_to_response_limit - len(respapp)
            respapp.extend(f'season/{i}' for i in tv_seasons[:scount])
            self.tv_seasons = tv_seasons[scount:]
        return respapp


class MediaRequestDict(UserDict[MediaRequestKey, MediaRequest]):
    """Dictionary of media requests."""

    data: Dict[MediaRef, Dict[str, MediaRequest]]  # type: ignore [reportIncompatibleVariableOverride]

    def __init__(self, _init: Iterable[tuple[MediaRequestKey, MediaRequest]] | Mapping[MediaRequestKey, MediaRequest] | None = None, /) -> None:
        """Initialize dictionary."""
        super().__init__()
        if _init is None:
            pass
        elif isinstance(_init, Mapping):
            for (ref, loc), val in _init.items():
                self.data.setdefault(ref, {})[loc] = val  # type: ignore [reportOptionalSubscript]
        else:
            for (ref, loc), val in _init:
                self.data.setdefault(ref, {})[loc] = val

    @overload
    def __getitem__(self, key: MediaRequestKey) -> MediaRequest: ...

    @overload
    def __getitem__(self, key: MediaRef) -> Dict[str, MediaRequest]: ...

    def __getitem__(self, key: MediaRequestKey | MediaRef) -> MediaRequest | Dict[str, MediaRequest]:
        """Get item by key or partial get (all locales for ref)."""
        if isinstance(key, MediaRef):
            return self.data[key]
        ref, loc = key
        reqs = self.data[ref]
        return reqs[loc]

    def __setitem__(self, key: MediaRequestKey, value: MediaRequest) -> None:
        """Set item by key."""
        ref, loc = key
        reqs = self.data.setdefault(ref, {})
        reqs[loc] = value

    def __delitem__(self, key: MediaRequestKey) -> None:
        """Delete item by key."""
        ref, loc = key
        reqs = self.data.get(ref)
        if reqs is None:
            raise KeyError(key)
        del reqs[loc]
        if not reqs:
            del self.data[ref]

    def keys(self) -> Iterator[MediaRequestKey]:
        """Generate all keys."""
        for ref, reqs in self.data.items():
            for loc in reqs.keys():
                yield (ref, loc)

    @overload
    def get(self, key: MediaRequestKey, default: T = None) -> MediaRequest | T: ...

    @overload
    def get(self, key: MediaRef, default: T = None) -> Dict[str, MediaRequest] | T: ...

    def get(self, key: MediaRequestKey | MediaRef, default: Any = None) -> Any:
        """Get item by key or partial get (all locales for ref)."""
        if isinstance(key, MediaRef):
            return self.data.get(key, default)
        ref, loc = key
        reqs = self.data.get(ref)
        if reqs is None:
            return default
        return reqs.get(loc, default)

    def all_values(self) -> Iterator[MediaRequest]:
        """Generate all requests."""
        for reqs in self.data.values():
            for req in reqs.values():
                yield req

    def setdefault(self, key: MediaRequestKey, req: MediaRequest) -> MediaRequest:
        """Set default request."""
        ref, loc = key
        reqs = self.data.setdefault(ref, {})
        return reqs.setdefault(loc, req)


class TmdbApi:
    """Base TBDB API."""

    # 2015:
    #   Movies - alternative_titles, changes, credits, images, keywords, lists, releases, reviews, similar, translations, videos
    #   TV - alternative_titles, changes, content_ratings, credits, external_ids, images, keywords, similar, translations, videos
    #   People - changes, combined_credits, external_ids, images, movie_credits, tagged_images, tv_credits
    # + (2015):
    #   genre_ids, original_language and overview

    DISCOVER_FILTERS = {
        'air_date.gte': dt_date,
        'air_date.lte': dt_date,
        'first_air_year': int,
        'first_air_date.gte': dt_date,
        'first_air_date.lte': dt_date,
        'include_null_first_air_dates': bool,
        'screened_theatrically': bool,
        'timezone': str,
        'with_networks': int,
        'with_status': Union[str, Literal[0, 1, 2, 3, 4, 5]],  # can be a comma (AND) or pipe (OR) separated query, can be used in conjunction with region
        'with_type': Union[str, Literal[0, 1, 2, 3, 4, 5, 6]],  # can be a comma (AND) or pipe (OR) separated query, can be used in conjunction with region

        'certification': str,
        'certification.gte': str,
        'certification.lte': str,
        'certification_country': str,
        'include_adult': bool,
        'include_video': bool,
        'language': str,
        'primary_release_year': int,
        'primary_release_date.gte': dt_date,
        'primary_release_date.lte': dt_date,
        'region': str,
        'release_date.gte': dt_date,
        'release_date.lte': dt_date,
        'sort_by': TmdbSortBy,
        'vote_average.gte': float,
        'vote_average.lte': float,
        'vote_count.gte': float,
        'vote_count.lte': float,
        'watch_region': str,
        'with_cast': str,  # can be a comma (AND) or pipe (OR) separated query
        'with_companies': str,  # can be a comma (AND) or pipe (OR) separated query
        'with_crew': str,  # can be a comma (AND) or pipe (OR) separated query
        'with_genres': str,  # can be a comma (AND) or pipe (OR) separated query
        'with_keywords': str,   # can be a comma (AND) or pipe (OR) separated query
        'with_origin_country': str,
        'with_original_language': str,
        'with_people': str,  # can be a comma (AND) or pipe (OR) separated query
        'with_release_type': Union[str, Literal[1, 2, 3, 4, 5, 6]],  # can be a comma (AND) or pipe (OR) separated query, can be used in conjunction with region
        'with_runtime.gte': int,
        'with_runtime.lte': int,
        'with_text_query': str,
        'with_watch_monetization_types': Union[str, Literal['flatrate', 'free', 'ads', 'rent', 'buy']],  # use in conjunction with watch_region, can be a comma (AND) or pipe (OR) separated query
        'with_watch_providers': str,  # use in conjunction with watch_region, can be a comma (AND) or pipe (OR) separated query
        'without_companies': str,
        'without_genres': str,
        'without_keywords': Union[str, int, Sequence[int]],
        'without_watch_providers': str,
        'year': int,
        # --- conditional keywords ---
        'air_date': dt_date,
        'first_air_date': dt_date,
        'primary_release_date': Condition[str],
        'release_date': Condition[dt_date],
        'vote_average': Condition[float],
        'vote_count': Condition[float],
        'with_runtime': Condition[int],
    }

    # --- some dicovery conditions ---

    Date = Condition(dt_date)
    Int = Condition(int)
    Float = Condition(float)
    VoteCount = Int
    VoteAverage = Float
    Vote = VoteAverage
    Today = dt_date.today()
    Now = Today
    WeekAgo = Today - timedelta(days=7)

    # --- others ---

    EPISODE_TYPE_TO_FF: ClassVar[dict[Optional[str], EpisodeType]] = {
        'finale': 'season_finale',  # of 'series_finale' for some tvshow statuses, see get_episode_type()
        'mid_season': 'mid_season_finale',
    }

    # --- internal ---

    _main2tmdb_type: ClassVar[dict[MainMediaType, TmdbContentType]] = {
        'movie': 'movie',
        'show': 'tv',
    }

    _main2tmdb_type2: ClassVar[dict[MainMediaType, TmdbContentPluralType]] = {
        'movie': 'movies',
        'show': 'tv',
    }

    _ref2rating: ClassVar[dict[MediaType, TmdbContentRatingType]] = {
        'movie': 'movies',
        'show': 'tv',
        'episode': 'tv/episodes',
    }

    _ext_id_results: ClassVar[dict[RefType, str]] = {
        'movie': 'movie_results',
        'show': 'tv_results',
        'season': 'tv_season_results',
        'episode': 'tv_episode_results',
        'person': 'person_results',
    }

    _search2tmdb: ClassVar[dict[SearchType, Optional[TmdbSearchType]]] = {
        'all': 'multi',
        'multi': 'multi',
        'movie': 'movie',
        'show': 'tv',
        'person': 'person',
        'collection': 'collection',
        'company': 'company',
        'keyword': 'keyword',
    }

    _person2ref: ClassVar[dict[PersonDataType, RefType]] = {
        'combined_credits': '',
        'movie_credits': 'movie',
        'tv_credits': 'show',
    }

    # NOT USED yet, got from https://developer.themoviedb.org/reference/movie-now-playing-list
    predef_lists = {
        'now_playing': 'include_adult=false&include_video=false&sort_by=popularity.desc&with_release_type=2|3&release_date.gte={min_date}&release_date.lte={max_date}',
        'popular': 'include_adult=false&include_video=false&sort_by=popularity.desc',
        'top_rated': 'include_adult=false&include_video=false&sort_by=vote_average.desc&without_genres=99,10755&vote_count.gte=200',
        'upcoming': 'include_adult=false&include_video=false&sort_by=popularity.desc&with_release_type=2|3&release_date.gte={min_date}&release_date.lte={max_date}',
    }

    Skel = SkelOptions

    # Media (like images) EN locales
    EN_LOCALES: ClassVar[Sequence[str]] = ('en-US', 'en-GB', 'en')
    # Media (like image) non-locales
    NON_LOCALES: ClassVar[Sequence[str]] = ('xx-XX', 'xx', 'null')

    #: TMDB API page, it's not possibl to change it, but it is used in some calculations.
    PAGE_SIZE: ClassVar[int] = 20

    _DEBUG_STATS: ClassVar[TmdbApiStats] = TmdbApiStats()
    _FAKE: ClassVar[bool] = False

    def __init__(self, api_key: Optional[str] = None, lang: Optional[str] = None, *,
                 auth_api_version: ApiVer = const.tmdb.auth.api, bearer: Optional[str] = None) -> None:
        #: TMDB base URL.
        self.base: str = 'https://api.themoviedb.org/'
        #: Base API version.
        self.auth_api_version: ApiVer = auth_api_version
        #: TMDB base URL for v3.
        self.base3: str = f'{self.base}3'
        #: TMDB base URL for v4.
        self.base4: str = f'{self.base}4'
        #: URL to image profile image.
        self.art_image_url: str = 'https://image.tmdb.org/t/p/w780'
        #: URL to image profile image.
        self.art_landscape_url: str = 'https://image.tmdb.org/t/p/w1280'
        #: URL to art image with formating.
        self.art_image_url_fmt: str = 'https://image.tmdb.org/t/p/{width}{path}'
        #: URL to person profile image.
        self.person_image_url: str = 'https://image.tmdb.org/t/p/w300_and_h450_bestv2'
        #: TMDB API key for direct use. If None, credentials() must be overloaded.
        self.api_key: Optional[str] = api_key
        #: TMDB API v4 auth bearer for direct use. If None, credentials() must be overloaded.
        self.bearer: Optional[str] = bearer
        #: TMDB response locale (e.g. pl-PL).
        self.lang: Optional[str] = lang  # or 'pl-PL'
        #: Timestamp to hold requests until rate is enabled.
        self.hold_until: float = 0
        #: Internal lock to protect common data (like `hold_until`).
        self.lock = Lock()
        #: Semaphore to limit connections number.
        self.conn_semaphore = Semaphore(const.tmdb.connection.max or 1_000_000)
        #: HTTP sessions.
        self._sessions: dict[str, requests.Session] = {}

    def _update_locale(self, lang: str | None = None) -> str:
        """Update locale (from settings), e.g. 'pl-PL'."""
        if lang is None:
            if self.lang is None:
                from ..ff.control import apiLanguage
                self.lang = apiLanguage().get('tmdb', 'pl-PL')
            lang = self.lang
        return lang

    @property
    def locale(self) -> str:
        """Return locale (language) for API requests, e.g. 'pl-PL'."""
        return self._update_locale()

    def _prepare_credentials(self, *, params: Optional[Params] = None, credentials: Optional[TmdbCredentials] = None) -> Tuple[Params, Headers]:
        """Helper. Prepare creadentials, params and headers."""
        if credentials is None:
            credentials = self.credentials()
        if params is None:
            params = {}
        headers = {
            'Accept': 'application/json',
        }
        if v4_bearer := credentials.bearer or self.bearer or const.dev.tmdb.v4.bearer or apis.tmdb_bearer:
            headers['Authorization'] = f'Bearer {v4_bearer}'
            if credentials.v4:
                headers['Authorization'] = f'Bearer {credentials.access_token}'
        if api_key := credentials.api_key or self.api_key or apis.tmdb_API:
            params.setdefault('api_key', api_key)
            if credentials.v3:
                params.setdefault('session_id', credentials.session_id)
        return params, headers

    def _honorate_rate_limit(self) -> None:
        """Honorate TMDB max rate limit. Sleep if limit is exceeded."""
        with self.lock:
            now = monotonic()
            delay = max(0, self.hold_until - now)
        if delay:
            xsleep(delay)

    def _process_status_code(self, status_code: int) -> bool:
        """Process REST status code and return True if got response, False if should repeat."""
        if status_code == 0:
            xsleep(const.trakt.connection.try_delay)
            return False
        # Connection Error | Rate Limit Exceeded
        elif status_code == 429:
            # See: https://developer.themoviedb.org/docs/rate-limiting
            fflog(f'[TMDB] Rate limit exceeded {status_code}')
            with self.lock:
                if not self.hold_until:
                    self.hold_until = monotonic() + const.trakt.connection.try_delay
            return False
        # Gateway Error - site is not avaliable
        elif status_code == 502:
            xsleep(5 * const.trakt.connection.try_delay)
            return False
        with self.lock:
            self.hold_until = 0
        return True

    def session(self, cache: CacheArg = None) -> requests.Session:
        if not const.tmdb.connection.common_session:
            return requests.Session(cache)
        if cache is False:
            cache_name = ''
        else:
            _, cache_name, _, _ = requests.cache_params(cache, None)
        try:
            return self._sessions[cache_name or '']
        except KeyError:
            pass
        self._sessions[cache_name or ''] = sess = requests.Session(cache)
        sess.mount(self.base, requests._requests.adapters.HTTPAdapter(pool_maxsize=100))
        return sess

    # @logtime
    def request(self,
                method: Method,
                url: str,
                *,
                credentials: Optional[TmdbCredentials] = None,
                api_version: Literal[3, 4] = 3,
                data: Optional[JsonData] = None,
                params: Optional[KwArgs] = None,
                append_to_response: Sequence[str] = (),
                lang: Optional[str] = None,
                errors: Literal['strict', 'ignore'] = 'ignore',
                expected_errors: Sequence[int] = (),
                cache: CacheArg = None,
                ) -> Optional[requests.Response]:
        """Request to API."""

        def cond_params(params: KwArgs) -> Generator[Tuple[str, Any], None, None]:
            for k, v in params.items():
                if isinstance(v, Condition):
                    for cmp, val in v.cond:
                        # if val is True:
                        #     val = 'true'
                        # elif val is False:
                        #     val = 'false'
                        yield f'{k}.{cmp}', val
                else:
                    # if v is True:
                    #     v = 'true'
                    # elif v is False:
                    #     v = 'false'
                    yield k, v

        if self._FAKE:
            return None
        url = urljoin(f'{self.base}/{api_version}/', url)
        # headers = {
        #     'Accept': 'application/json',
        # }
        # if credentials is None:
        #     credentials = self.credentials()

        # append_to_response = ['external_ids', 'release_dates', 'translations']  # 'images', 'original_language'
        params = dict(cond_params(params or {}))
        # if v4_bearer := credentials.bearer or self.bearer or const.dev.tmdb.v4.bearer or apis.tmdb_bearer:
        #     headers['Authorization'] = f'Bearer {v4_bearer}'
        #     if credentials.v4:
        #         headers['Authorization'] = f'Bearer {credentials.access_token}'
        # if api_key := credentials.api_key or self.api_key or apis.tmdb_API:
        #     params.setdefault('api_key', api_key)
        #     if credentials.v3:
        #         params.setdefault('session_id', credentials.session_id)
        params, headers = self._prepare_credentials(credentials=credentials, params=params)
        if params.get('page') == 0:
            params['page'] = 1
        if append_to_response:
            params.setdefault('append_to_response', ','.join(append_to_response))
        lang = self._update_locale(lang)
        if lang:
            params['language'] = lang
        # fflog(f'[FF][TMDB] {url=}, {params=}')

        resp = None
        status_code: int = 0
        T = monotonic()
        try:
            for _ in range(const.trakt.connection.try_count or 1):
                self._DEBUG_STATS.request_count += 1
                self._honorate_rate_limit()
                try:
                    # resp = requests.request(method, url, json=data, params=params, headers=headers,
                    #                         timeout=const.tmdb.connection.timeout, cache=cache)
                    with self.conn_semaphore:
                        resp = self.session(cache).request(method, url, json=data, params=params, headers=headers, timeout=const.tmdb.connection.timeout)
                    status_code = resp.status_code
                except requests.ConnectionError:
                    status_code = 0
                except requests.RequestException:
                    if errors != 'ignore':
                        raise
                    status_code = 0
                if self._process_status_code(status_code):
                    break
            T = monotonic() - T
        except Exception as exc:
            fflog(f'TMDB get failed: {exc}, url={url}, params={params}')
            raise
        finally:
            from ..ff.tricks import str_removeprefix
            params_str = repr(params)
            if session_id := params.get('session_id'):
                params_str = params_str.replace(session_id, '...')
            S = len(resp.content) / 1_048_576 if resp else 0
            if const.debug.tty:
                import sty
                log_label = f'[FF][TMDB]{sty.bg.da_green}[API]{sty.rs.bg}'
            else:
                log_label = '[FF][TMDB][API]'
            fflog(f'{log_label} {status_code:03d} ({T:.3f}s, {S:.2f}MB) /{str_removeprefix(url, self.base)} params={params_str}')

        if resp is None:
            return None

        # Success
        if 100 <= status_code <= 299:
            return resp

        if status_code >= 500:  # temporary
            if status_code not in expected_errors:
                fflog(f'[TMDB] Temporary error {status_code}')
            return None
        if status_code >= 400:  # permanent
            if status_code not in expected_errors:
                fflog(f'[TMDB] Error {status_code}\n{resp.text}')
            if status_code == 401:  # Authentication failed
                self.auth_failed()
            return None

    def get(self, url: str, **kwargs: Unpack[TmdbRequestKwargs]) -> Optional[JsonResult]:
        """Send GET request to tbdb.org and return JSON."""
        resp: Optional[requests.Response] = self.request('GET', url, data=None, **kwargs)
        if resp is None:
            return None
        return resp.json()

    def post(self, url: str, data: JsonData | None, **kwargs: Unpack[TmdbRequestKwargs]) -> Optional[JsonData]:
        """Send POST request to tbdb.org and return JSON."""
        resp: Optional[requests.Response] = self.request('POST', url, data=data, **kwargs)
        if resp is None:
            return None
        return resp.json()

    def delete(self, url: str, data: JsonData | None = None, **kwargs: Unpack[TmdbRequestKwargs]) -> Optional[JsonData]:
        """Send POST request to tbdb.org and return JSON."""
        resp: Optional[requests.Response] = self.request('DELETE', url, data=data, **kwargs)
        if resp is None:
            return None
        return resp.json()

    def auth(self, *, credentials: Optional[TmdbCredentials] = None, force: bool = False) -> bool:
        """Authorize user and create new session / token."""
        if self.auth_api_version == 3:
            return self.auth_v3(credentials=credentials, force=force)
        return self.auth_v4(credentials=credentials, force=force)

    def auth_v3(self, *, credentials: Optional[TmdbCredentials] = None, force: bool = False) -> bool:
        """Authorize user and create new v3 session."""
        if credentials is None:
            credentials = self.credentials()
        if not force and credentials:
            return True  # already authorized
        credentials = evolve(credentials, session_id=None)
        if not credentials.user or not credentials.password:
            return False
        data = self.get('authentication/token/new', credentials=credentials)
        if not isinstance(data, Mapping):
            return False
        token = data.get('request_token')
        if not token:
            return False
        data = self.post('authentication/token/validate_with_login', credentials=credentials, data={
            'username': credentials.user,
            'password': credentials.password,
            'request_token': token,
        })
        if not data:
            return False
        data = self.post('authentication/session/new', credentials=credentials, data={'request_token': token})
        if not isinstance(data, Mapping) or not data.get('success'):
            return False
        self.save_session(session_id=data['session_id'])
        return True

    def auth_v4(self, *, credentials: Optional[TmdbCredentials] = None, force: bool = False) -> bool:
        """Authorize user and create new v4 access token and v3 session."""
        if credentials is None:
            credentials = self.credentials()
        if not force and credentials.v3 and credentials.v4:
            return True  # already authorized
        if force:
            credentials = evolve(credentials, access_token=None, session_id=None)

        if not credentials.v4:
            start = monotonic()
            data = self.post('auth/request_token', data={}, api_version=4, credentials=credentials)
            if not isinstance(data, Mapping) or not data.get('success') or not (request_token := data.get('request_token')):
                fflog.info(f'TMDB auth (v4) FAILED: no request token')
                return False
            # Create (progress-bar) dialog.
            # print(f'https://www.themoviedb.org/auth/access?request_token={request_token}')
            expires_in = const.tmdb.auth.dialog_expire  # max 15 min
            interval = const.tmdb.auth.dialog_interval
            dialog = self.dialog_create(request_token=request_token, verification_url=f'https://www.themoviedb.org/auth/access?request_token={request_token}')
            # Continue as long as not expire or user authorized or user canceled.
            access_token: str = ''
            try:
                while True:
                    #: Current timestamp.
                    now: float = monotonic()
                    # check in expires...
                    if start + expires_in < now:
                        fflog.info(f'TMDB auth (v4) FAILED: no access token in {expires_in} seconds')
                        return False
                    # check if dialog is canceled
                    if self.dialog_is_canceled(dialog):
                        return False
                    if access_token := self._get_access_token(request_token, credentials=credentials):
                        break
                    # update progress-bar
                    self.dialog_update_progress(dialog, 100 * (now - start) / expires_in)
                    # sleep given interval
                    xsleep(interval)
            except KeyboardInterrupt:
                print('''\nCancelled. Enter '0'.\n''')
            finally:
                # finish - close dialog
                self.dialog_close(dialog)
            if not access_token:
                return False
            credentials = evolve(credentials, access_token=access_token)

        if not credentials.v3:
            data = self.post('authentication/session/convert/4', data={'access_token': credentials.access_token}, credentials=credentials)
            if not isinstance(data, Mapping) or not data.get('success') or not (session_id := data.get('session_id')):
                fflog.info(f'TMDB auth (v4) FAILED: no v3 session id')
                return False
            credentials = evolve(credentials, session_id=session_id)

        self.save_session(access_token=credentials.access_token, session_id=credentials.session_id)
        return True

    def _get_access_token(self, request_token: str, *, credentials: Optional[TmdbCredentials] = None) -> str:
        """Retrive access_token if request_token is accepted by user (on web site)."""
        data = self.post('auth/access_token', data={}, params={'request_token': request_token}, api_version=4,
                         credentials=credentials, expected_errors=[422])
        # access_token, account_id (can be obtain from access_token)
        if isinstance(data, Mapping) and data.get('success') and (access_token := data.get('access_token')):
            return access_token
        return ''

    def revoke_session(self, *, credentials: Optional[TmdbCredentials] = None) -> bool:
        """Revoke the session (logout user)."""
        if credentials is None:
            credentials = self.credentials()
        if not credentials:
            return True  # no session, already done
        success = True
        if credentials.v4:
            data = self.delete('auth/access_token', data={'access_token': credentials.access_token}, api_version=4)
            if not isinstance(data, Mapping) or not data.get('success'):
                success = False
        if credentials.v3:
            data = self.delete('authentication/session', credentials=credentials, data={'session_id': credentials.session_id})
            if not isinstance(data, Mapping) or not data.get('success'):
                success = False
        self.save_session(session_id=None, access_token=None)  # forgot session_id (v3) and access_token (v4)
        return success

    unauth = revoke_session

    def _ref_id_to_tmdb(self, ref_id: T, /) -> T:
        if isinstance(ref_id, int) and ref_id in VideoIds.TMDB:
            ref_id = VideoIds.TMDB.index(ref_id)
        return ref_id

    def _parse_art(self, *, item: JsonData, translate_order: Sequence[str], skip_fanart: bool) -> Dict[str, str]:
        """Pre-parse art images. Much simpler verison of Art.parse_art()."""
        from ..ff.art import TmbdArtService
        images = item.get('images') or {}
        art_langs = (*dict.fromkeys(loc.partition('-')[0] for loc in translate_order), *(None,))
        no_langs = ('00', 'xx', 'null', None)
        art: Dict[str, str] = {}
        for aname, artdef in TmbdArtService.art_names.items():
            for src in artdef.sources:
                ilist = images.get(src.key)
                if ilist:
                    for lng in artdef.langs(yes=art_langs, no=no_langs, skip_fanart=skip_fanart):
                        for ielem in ilist:
                            if ielem.get('iso_639_1') == lng and (path := ielem['file_path']):
                                art[aname] = f'{self.art_image_url}{path}'
                                break
                        else:
                            continue
                        break
                    else:
                        continue
                    break
        return art

    @requests.netcache('media')
    def get_media_by_ref(self,
                         req: MediaRequest,
                         *,
                         credentials: Optional[TmdbCredentials] = None,
                         # --- for internal use only ---
                         queue: Queue | None = None,
                         ) -> TmdbItemJson:
        """Get single media by reference."""
        # @requests.netcache('media')
        def get(path: str, params: dict[str, Any]):
            # fflog.debug(f'[FF][TMDB] {path=}, params={params!r}')
            self._DEBUG_STATS.request_count += 1
            self._honorate_rate_limit()
            if TYPE_CHECKING:
                status_code, resp = 0, requests.Response()
            for _ in range(const.trakt.connection.try_count or 1):
                status_code = T = 0
                resp = None
                try:
                    T = monotonic()
                    resp = requests.get(f'{self.base3}/{path}', params=params, headers=headers, timeout=const.tmdb.connection.timeout)
                    status_code = resp.status_code
                    T = monotonic() - T
                except (requests.ConnectionError, requests.RequestException):
                    pass
                except Exception as exc:
                    fflog(f'TMDB get failed: {exc}, url={self.base3}/{path}, params={params}')
                    raise
                finally:
                    params_str = repr(params)
                    if session_id := params.get('session_id'):
                        params_str = params_str.replace(session_id, '...')
                    S = len(resp.content) / 1_048_576 if resp else 0
                    if const.debug.tty:
                        import sty
                        log_label = f'[FF][TMDB]{sty.bg.da_green}[API]{sty.rs.bg}'
                    else:
                        log_label = '[FF][TMDB][API]'
                    fflog(f'{log_label} {status_code:03d} ({T:.3f}s, {S:.2f}MB) /3/{path} params={params_str}')
                if self._process_status_code(status_code):
                    break
            if status_code >= 400:
                fflog(f'[FF][TMDB] ERROR {status_code} for {path}')
            if resp is None:
                fflog(f'[FF][TMDB] connection FAILED for {path}')
                return {}
            return resp.json()

        details = req.details
        seasons = req.seasons
        ref = req.ref
        rtype = ref.real_type
        cred_params, headers = self._prepare_credentials(credentials=credentials)
        params = dict(cred_params)
        imode = GetImageMode(const.tmdb.get_image_mode)
        respapp = req.append_to_response
        tv_seasons = req.seasons

        data: JsonData = {}
        if details & (InfoDetails.INFO_LANG | InfoDetails.INFO_ORIG):
            locale = req.locale or self._update_locale()
        elif details & InfoDetails.INFO_EN:
            locale = 'en-US'
        else:
            fflog.error(f'No InfoDetails.INFO_* locale set for {ref}, skipped')
            return {}
        lang = locale.partition('-')[0] if locale else 'en'

        # prepare request
        params['append_to_response'] = ','.join(respapp)
        if locale:
            params['language'] = locale
        if 'images' in respapp:
            lang = locale.partition('-')[0] if locale else 'en'
            if imode is GetImageMode.APPEND_EN:
                params['include_image_language'] = 'en'
            elif imode is GetImageMode.APPEND_LANG:
                params['include_image_language'] = lang
            else:
                params['include_image_language'] = ','.join(dict.fromkeys((lang, 'en', 'xx', 'null')))
        if 'videos' in respapp:
            lang = locale.partition('-')[0] if locale else 'en'
            params['include_video_language'] = ','.join(dict.fromkeys((lang, 'en', 'xx', 'null')))
        if ref.type == 'movie':
            path = f'movie/{ref.tmdb_id}'
        elif ref.type == 'show':
            tmdb_tv_tuple = ref.tmdb_tv_tuple
            if (details & InfoDetails.EPISODE_SKIP) and ref.episode is not None:
                fflog.warning(f'Ignoring episode number for show ref {ref}  ({details=}:{details.value:03x})')
            path = '/'.join(f'{k}{v}' for k, v in zip(['tv/', 'season/', 'episode/'], tmdb_tv_tuple))
        elif ref.type in get_typing_args(DetailsAllowed):
            path = f'{ref.type}/{ref.tmdb_id}'
        else:
            # non DetailsAllowed type
            raise ValueError(f'Unsupported TMDB media type {ref.type}, can NOT use season or episode ID or else')

        # receive tmdb data with images
        if imode is GetImageMode.ALL:
            with RequestsPoolExecutor(max_thread_workers()) as ex:
                obj = ex.submit(get, path, params)
                if details & InfoDetails.IMAGES:
                    img = ex.submit(get, f'{path}/images', cred_params)
            data = obj.result()
            if details & InfoDetails.IMAGES:
                data['images'] = img.result()  # type: ignore[reportPossiblyUnboundVariable]
        else:
            data = get(path, params)
            if details & InfoDetails.IMAGES and imode is GetImageMode.PULL:
                orig_lang: str = data.get('original_language', 'en')
                trans_order = tuple(dict.fromkeys((locale, lang, *self.EN_LOCALES, *self.NON_LOCALES,
                                                   *(f'{orig_lang}-{c}' for c in data.get('origin_country', ())), orig_lang)))
                art = self._parse_art(item=data, translate_order=trans_order, skip_fanart=ref.is_episode)
                if not all(art.get(a) for a in ('fanart', 'landscape', 'poster', 'clearlogo')):
                    data['images'] = get(f'{path}/images', cred_params)

        # fix up extra seasons and theirs episodes
        if (details & InfoDetails.SHOW_SEASONS or tv_seasons) and rtype == 'show':
            # get missing seasons (if more then const.tmdb.append_to_response_limit)
            if details & InfoDetails.SHOW_SEASONS:
                seasons_need = {sz['season_number'] for sz in data.get('seasons', ())}
            else:
                seasons_need = set(tv_seasons)
            seasons_got = {int(sznum) for asz in data if asz.startswith('season/') if (sznum := asz.partition('/')[2]).isdigit()}
            missing_seasons = seasons_need - seasons_got
            if missing_seasons:
                data2: JsonData
                self._DEBUG_STATS.extra_seasons_count += 1
                scount = const.tmdb.append_to_response_limit
                if len(missing_seasons) > scount:
                    def get_seasons(nums):
                        sz_params = {**params, 'append_to_response': ','.join(f'season/{sz}' for sz in nums)}
                        return get(path, sz_params)

                    self._DEBUG_STATS.multi_seasons_count += 1
                    with RequestsPoolExecutor(max_thread_workers()) as ex:
                        for data2 in ex.map(get_seasons, batched(missing_seasons, scount)):
                            if isinstance(data2, Mapping) and data2.get('success', True):
                                data.update(data2)
                else:
                    # get extra info about missing show seasons
                    params['append_to_response'] = ','.join(f'season/{sz}' for sz in missing_seasons)
                    data2 = get(path, params)
                    if isinstance(data2, Mapping) and data2.get('success', True):
                        data.update(data2)

        # udpate main season list
        if rtype == 'show':
            for sz in data.get('seasons') or ():
                num: int = sz['season_number']
                sz['_ref'] = ref.with_season(num)
                sz['_type'] = 'season'
                if ex := data.get(f'season/{num}'):
                    sz.update(ex)

        # merge received data
        # for req in media_requests:
        #     if req.key:
        #         data[req.key] = req.data
        #     else:
        #         data.update(req.data)

        data['_ref'] = ref
        data['_type'] = rtype
        data['_request'] = req
        if rtype == 'episode':
            if tv_vid := VideoIds.from_ffid(ref.ffid):
                data.setdefault('show_id', tv_vid.tmdb)
        return data

    # @logtime
    def get_media_list_by_ref(self, refs: Sequence[MediaRef], *, details: InfoDetails = InfoDetails.DEFAULT,
                              credentials: Optional[TmdbCredentials] = None) -> List[TmdbItemJson]:
        """Get list of media by theris references."""
        raise AssertionError('DEPRECATED')

    @logtime(name='[FF][TMDB] get_media_dict_by_ref')
    def get_media_dict_by_ref(self,
                              refs: Iterable[FFRef | MediaRequest],
                              *,
                              details: InfoDetails = InfoDetails.DEFAULT,
                              credentials: Optional[TmdbCredentials] = None,
                              ) -> Dict[MediaRef, TmdbItemJson]:
        """Get dict of media by theirs references."""
        def get(req: MediaRequest) -> TmdbItemJson:
            data: TmdbItemJson = {}
            try:
                data = self.get_media_by_ref(req, credentials=credentials, queue=queue)
                return data
            finally:
                ref = req.ref
                signal = (ref, None)  # signal end of movie, show to cancel original request
                # if ref.season is None and req.details & InfoDetails.INFO_ORIG:
                if ref.season is None:
                    orign_lang = data.get('original_language')
                    origin_country = next(iter(data.get('origin_country') or ()), None)
                    if orign_lang and origin_country:
                        orig_locale = f'{orign_lang.lower()}-{origin_country.upper()}'
                        if orig_locale not in (LOCALE_EN, locale):
                            signal = (ref, orig_locale)  # signal end of movie, show to submit original request
                fflog.debug(f'[FF][TMDB] Signal request {signal[0]:a}: {signal[1]!r}')
                queue.put(signal)  # signal end of movie, show ...

        def create_requests(ref: FFRef | MediaRequest) -> Iterator[MediaRequest]:
            """Split request by locales if needed."""
            if isinstance(ref, MediaRequest):
                req_details = details if ref.details == InfoDetails.NOT_DEFINED else ref.details
                req_locale = ref.locale
                item = ref.item
            else:
                req_details = details
                req_locale = ''
                item = ref
            det = req_details
            if det & InfoDetails.INFO_LANG:
                det &= ~InfoDetails.INFO_EN
                yield MediaRequest(item, details=det, locale=req_locale or locale)
            det = req_details
            if det & InfoDetails.INFO_EN:
                if det & InfoDetails.INFO_LANG:
                    det &= ~(InfoDetails.DETAILS | InfoDetails.AGGREGATE_CREDITS)  # all details are in INFO_EN request
                det &= ~InfoDetails.INFO_LANG
                yield MediaRequest(item, details=det, locale=req_locale or LOCALE_EN)

        optimize = not details & InfoDetails.NO_OPTIMIZE
        locale = self._update_locale()

        # convert to MediaRequest
        requesting = MediaRequestDict((req.key, req) for ref in refs for req in create_requests(ref))

        # collapse requests (same ref and locale)
        for req in tuple(requesting.all_values()):
            ref, loc = req.ref, req.locale
            if req.details & (InfoDetails.INFO_EN | InfoDetails.INFO_ORIG):
                for req2 in requesting[ref].values():
                    if req2 is not req and req2.ref == ref and req2.details & InfoDetails.TRANSLATIONS:
                        if ref.is_show and req.details & (InfoDetails.SEASON_EN | InfoDetails.SEASON_ORIG):
                            break  # keep both requests, need more languages for seasons
                        if ref.is_season and req.details & InfoDetails.SKIP_EPISODES:
                            if req.details & InfoDetails.INFO_LANG:
                                req2.details &= ~(InfoDetails.DETAILS | InfoDetails.AGGREGATE_CREDITS | InfoDetails.SHOW_DETAILS)
                            else:
                                req.details &= ~(InfoDetails.DETAILS | InfoDetails.AGGREGATE_CREDITS | InfoDetails.SHOW_DETAILS)
                            break  # keep both requests, need more languages for episodes
                        # remove en or original lang request, they are in translations
                        if req.details & InfoDetails.INFO_LANG:
                            del requesting[req2.key]
                        else:
                            del requesting[req.key]
                        break

        # support seasons languages (from shows)
        for req in tuple(requesting.all_values()):
            ref = req.ref
            if ref.is_show and req.details & (InfoDetails.SEASON_EN | InfoDetails.SEASON_ORIG):
                if req.details & InfoDetails.SEASON_EN:
                    xreq = requesting.setdefault((ref, LOCALE_EN), MediaRequest(ref, details=InfoDetails.NONE, locale=LOCALE_EN))
                    xreq.details |= InfoDetails.INFO_EN
                if req.details & InfoDetails.SEASON_ORIG:
                    xreq = requesting.setdefault((ref, LOCALE_ORIG), MediaRequest(ref, details=InfoDetails.NONE, locale=LOCALE_ORIG))
                    xreq.details |= InfoDetails.INFO_ORIG

        # expand seasons (from episodes)
        for req in tuple(requesting.all_values()):
            ref, loc = req.ref, req.locale
            # season with all episodes
            if ref.is_season and req.details & InfoDetails.SEASON_EPISODES:
                if req.details & InfoDetails.SKIP_EPISODES:
                    if req.details & InfoDetails.EPISODE_EN:
                        xreq = requesting.setdefault((ref, LOCALE_EN), MediaRequest(ref, details=InfoDetails.NONE, locale=LOCALE_EN))
                        xreq.details |= InfoDetails.INFO_EN | InfoDetails.SEASON_EN
                else:
                    ...  # add all episodes ???

        # append seasons (from episodes)
        for req in tuple(requesting.all_values()):
            ref, loc = req.ref, req.locale
            # episode from season
            if ref.is_episode and req.details & (InfoDetails._GET_SEASON | InfoDetails.SKIP_EPISODES):
                if xref := req.ref.season_ref:
                    xreq = requesting.setdefault((xref, loc), MediaRequest(xref, details=InfoDetails.NONE, locale=loc))
                    xreq.details |= req.details.season()
                    if req.details & InfoDetails.EPISODE_EN:
                        xreq = requesting.setdefault((xref, LOCALE_EN), MediaRequest(xref, details=InfoDetails.NONE, locale=LOCALE_EN))
                        xreq.details |= InfoDetails.INFO_EN

        # append shows (from seasons and episodes)
        for req in tuple(requesting.all_values()):
            ref, loc = req.ref, req.locale
            if ref.season is not None and req.details & InfoDetails._GET_SHOW:
                if xref := req.ref.show_ref:
                    xreq = requesting.setdefault((xref, loc), MediaRequest(xref, details=InfoDetails.NONE, locale=loc))
                    xreq.details |= req.details.show(seasons=True)

        # remove episodes and force more season details (more locales)
        for req in tuple(requesting.all_values()):
            ref, loc = req.ref, req.locale
            if ref.is_episode and req.details & InfoDetails.SKIP_EPISODES:
                if xref := req.ref.season_ref:
                    if req.details & InfoDetails.EPISODE_EN:
                        xreq = MediaRequest(xref, details=InfoDetails.INFO_EN, locale=LOCALE_EN)
                        requesting.setdefault(xreq.key, xreq)
                    if (req.details & InfoDetails.EPISODE_ORIG) and (xreq := requesting.get((xref, loc))):
                        xreq.details |= InfoDetails.INFO_ORIG
                del requesting[ref, loc]  # remove episode

        # add sessions (from shows)
        for req in tuple(requesting.all_values()):
            ref = req.ref
            if ref.is_show and req.details & InfoDetails.SHOW_SEASONS:
                if req.details & InfoDetails.SEASON_EN:
                    xreq = MediaRequest(ref, details=InfoDetails.NONE, locale=LOCALE_EN)
                    xreq = requesting.setdefault(xreq.key, xreq)
                    xreq.details |= InfoDetails.INFO_EN | InfoDetails.SHOW_SEASONS
                if req.details & InfoDetails.SEASON_ORIG:
                    xreq = MediaRequest(ref, details=InfoDetails.NONE, locale=LOCALE_ORIG)
                    xreq = requesting.setdefault(xreq.key, xreq)
                    xreq.details |= InfoDetails.INFO_ORIG | InfoDetails.SHOW_SEASONS

        # update append_to_response for all shows
        for req in requesting.all_values():
            if req.ref.is_show:
                req.append_to_response  # update append_to_response

        # optimize seasons (with show request)
        if optimize:
            def append_to_show(xref: MediaRef, loc: str, key: str, details: InfoDetails) -> bool:
                xreq = requesting.setdefault((xref, loc), MediaRequest(xref, details=details, locale=loc))
                if key in xreq.append_to_response:
                    return True
                if len(xreq.append_to_response) < limit:
                    xreq.append_to_response.append(key)
                    return True
                return False

            limit = const.tmdb.append_to_response_limit
            for req in tuple(requesting.all_values()):
                ref, loc = req.ref, req.locale
                if ref.is_season and not req.details & InfoDetails.NO_OPTIMIZE and (xref := ref.show_ref):
                    key = f'season/{ref.season}'
                    delete = True
                    if not append_to_show(xref, loc, key, InfoDetails.INFO_LANG):
                        delete = False
                    if req.details & (InfoDetails.INFO_EN | InfoDetails.SEASON_EN):
                        if not append_to_show(xref, LOCALE_EN, key, InfoDetails.INFO_EN):
                            delete = False
                    if req.details & (InfoDetails.INFO_ORIG | InfoDetails.SEASON_ORIG):
                        if not append_to_show(xref, LOCALE_ORIG, key, InfoDetails.INFO_ORIG):
                            delete = False
                    if delete:
                        del requesting[req.key]  # remove season request, it is just append in show request

        requests = [req for req in requesting.all_values() if req.details & (InfoDetails.INFO_LANG | InfoDetails.INFO_EN)]
        postponed = [req for req in requesting.all_values() if req.details & InfoDetails.INFO_ORIG]

        # debug...
        if 1:
            fflog.debug('[FF][TMDB] --- Requests:')
            for req in requests:
                fflog.debug(f' - {req}')
            fflog.debug('[FF][TMDB] --- Postponed:')
            for req in postponed:
                fflog.debug(f' - {req}')
            # raise SystemExit(0)
            fflog.debug('[FF][TMDB] ---')

        # postponed requests by original language
        orig_requests: dict[MediaRef, list[MediaRequest]] = {}
        for req in postponed:
            ref = req.ref
            if ref.type == 'show':
                ref = ref.show_ref or ref
            orig_requests.setdefault(ref, []).append(req)

        # get items
        if credentials is None:
            credentials = self.credentials()
        queue: Queue[tuple[MediaRef, str | None]] = Queue()
        postponed_jobs: list[tuple[MediaRequest, TmdbItemJson]] = []
        if len(requests) == 1 and not postponed:
            jobs = [get(next(iter(requests)))]
        else:
            with RequestsPoolExecutor(max_thread_workers()) as ex:
                jobs = ex.map(get, requests)
                if orig_requests:
                    if unsupported := {req.ref.main_ref for req in requests} - orig_requests.keys():
                        fflog.error(f'No original language info for {", ".join(f"{ref!a}" for ref in unsupported)}')
                        raise AssertionError(f'No original language info for {", ".join(f"{ref!a}" for ref in unsupported)}')
                postponed_futures: list[tuple[MediaRequest, Future[TmdbItemJson]]] = []
                while orig_requests:
                    ref, loc = queue.get()
                    for req in orig_requests.pop(ref, None) or ():  # remove all postponed requests for ref
                        if loc:  # locale is given - get original language info
                            if requesting.get((req.ref, loc)):
                                fflog(f'[FF][TMDB] Postponed request for {ref} locale={loc} canceled, already requested')
                            else:
                                fflog(f'[FF][TMDB] Postponed request for {ref} locale={loc}')
                                req.locale = loc
                                fut = ex.submit(get, req)
                                postponed_futures.append((req, fut))
                        else:
                            fflog(f'[FF][TMDB] No postponed request for {ref}')
                    postponed_jobs = [(req, fut.result()) for req, fut in postponed_futures]
        # raise SystemExit(0)

        # split merged sub-requests (e.g. season/N in show), ref could be many times
        items: list[tuple[MediaRequest, TmdbItemJson]] = []
        rx_subitem = re.compile(r'(season|episode)/(\d+)')
        for req, item in chain(zip(requests, jobs), postponed_jobs):
            items.append((req, item))
            for key, mch in [(key, mch) for key, val in item.items() if (mch := rx_subitem.fullmatch(key))]:
                name, num = mch[1], int(mch[2])
                if name == 'season':
                    xref = req.ref.with_season(num)
                    items.append((evolve(req, item=xref), item.pop(key)))
                elif name == 'episode':
                    xref = req.ref.with_episode(num)
                    items.append((evolve(req, item=xref), item.pop(key)))

        # generate item by ref (api lang)
        result: dict[MediaRef, TmdbItemJson] = {}
        for lang_flag_hit in (True, False):
            for req, item in items:
                item['_ref'] = req.ref
                item['_request'] = req
                item.setdefault('_type', req.ref.real_type)
                item.setdefault('id', 0)
                if bool(req.details & InfoDetails.INFO_LANG) ^ lang_flag_hit == 0:
                    data = result.setdefault(req.ref, item)
                    if data is not item:
                        locales = data.setdefault('_locales', {})
                        if locales.setdefault(req.locale, item) is not item:
                            print(f'[FF][TMDB] WARNING: Duplicate item for {req.ref:a} ({req.locale}), merged')
                        if req.details & InfoDetails.INFO_ORIG:
                            locales[LOCALE_ORIG] = item
        # raise SystemExit(0)
        return result

    def list_refs(self, type: RefType, items: Sequence[Union[JsonData, FFItem]]) -> List[MediaRef]:
        """Get refs from media item list."""
        if items:
            elem = items[0]
            if isinstance(elem, (FFItem, MediaRef)):
                return [it.ref for it in cast(Sequence[FFItem], items)]
            if isinstance(elem, Mapping):
                return [MediaRef.from_tmdb(type, it.get('id', 0), season=None, episode=None) for it in items]
        return []

    def get_skel_en_media(self,
                          refs: Sequence[MediaRef],
                          *,
                          options: SkelOptions = SkelOptions.SHOW_LAST_SEASONS,
                          credentials: Optional[TmdbCredentials] = None,
                          ) -> Dict[MediaRef, TmdbItemJson]:
        """Load skeleton media info."""

        def req(path: str, *, params: Params, append: Sequence[str] = ()):
            params = {**params, 'append_to_response': ','.join(append)}
            fflog(f'[FF][TMDB] {path=}, params={params!r}')
            self._DEBUG_STATS.request_count += 1
            if TYPE_CHECKING:
                status_code, resp = 0, requests.Response()
            for _ in range(const.trakt.connection.try_count or 1):
                self._honorate_rate_limit()
                try:
                    resp = requests.get(f'{self.base3}/{path}', params=params, headers=headers, timeout=const.tmdb.connection.timeout)
                    status_code = resp.status_code
                except (requests.ConnectionError, requests.RequestException):
                    status_code = 0
                if self._process_status_code(status_code):
                    break
            if resp.status_code >= 400:
                fflog(f'[FF][TMDB] ERROR {resp.status_code} for {path}')
            return resp.json()

        def get(ref: MediaRef, par: Optional[Dict[str, Any]] = None) -> TmdbItemJson:
            if par is None:
                par = params
            rtype = ref.real_type
            r_append = list(append_to_response)

            append_season_number = 0
            if ref.type == 'movie':
                path = f'movie/{ref.tmdb_id}'
            elif ref.type == 'show':
                # path = '/'.join(f'{k}{v}' for k, v in zip(['tv/', 'season/', 'episode/'], ref.tmdb_tv_tuple))
                path = f'tv/{ref.tmdb_id}'
                if ref.season:  # season and episode
                    r_append.append(f'season/{ref.season}')
                elif options & APPEND_SEASONS:
                    # try to match first 10 seasons, hope that it will be all seasons (then the last too)
                    if len(r_append) < const.tmdb.append_to_response_limit:
                        append_season_number = const.tmdb.append_to_response_limit - len(r_append)
                        r_append.extend(f'season/{i}' for i in range(1, append_season_number + 1))
            elif ref.type in ('person', 'collection'):
                path = f'{ref.type}/{ref.tmdb_id}'
            else:
                # non DetailsAllowed type
                raise ValueError(f'Unsupported TMDB media type {ref.type}, can NOT use season or episode ID or else')
            data = req(path, params=par, append=r_append)
            data['_ref'] = ref
            data['_type'] = rtype
            last_season_number = max(z['season_number'] for z in data.get('seasons', ())) if ref.is_show else 0
            # obtain all seasons
            if last_season_number and options & SkelOptions.SHOW_ALL_SEASONS:
                for s_nums in batched(range(append_season_number, last_season_number), const.tmdb.append_to_response_limit):
                    r_append = [f'season/{i+1}' for i in s_nums]
                    s_data = req(path, params=par, append=r_append)
                    for key in r_append:
                        data[key] = s_data[key]
            # obtain a last few seasons (if not received already)
            elif last_season_number and options & SkelOptions.SHOW_LAST_SEASONS and last_season_number > append_season_number:
                r_append = [f'season/{i+1}'
                            for i in range(max(append_season_number, last_season_number - const.tmdb.append_to_response_limit), last_season_number)]
                s_data = req(path, params=par, append=r_append)
                for key in r_append:
                    data[key] = s_data[key]
            if rtype == 'episode':
                if tv_vid := VideoIds.from_ffid(ref.ffid):
                    data.setdefault('show_id', tv_vid.tmdb)
            return data

        APPEND_SEASONS = SkelOptions.SHOW_FIRST_SEASONS | SkelOptions.SHOW_LAST_SEASONS | SkelOptions.SHOW_ALL_SEASONS;
        if credentials is None:
            credentials = self.credentials()
        cred_params, headers = self._prepare_credentials(credentials=credentials)
        append_to_response = ['external_ids']
        params = {
            **cred_params,
            # 'language': 'en-US',
        }
        if options & SkelOptions.TRANSLATIONS:
            append_to_response.append('translations')

        if not refs:
            return {}
        if len(refs) == 1:
            ref = refs[0]
            return {ref: get(ref)}
        with RequestsPoolExecutor(min(max_thread_workers(), const.tmdb.skel_max_threads)) as ex:
            jobs = ex.map(get, refs)
        return {ref: item for ref, item in zip(refs, jobs)}

    def parse_episode_type(self, item: JsonData, *, tvshow_status: str = '') -> EpisodeType:
        """Determine episode type."""
        if etype := item.get('episode_type'):
            if etype == 'finale':  # season or tvshow
                if tvshow_status.lower() in ('ended', 'canceled', 'canceled'):
                    return 'series_finale'
        return self.EPISODE_TYPE_TO_FF.get(etype, '')

    def _item_list(self, type: RefType | SearchType, data: JsonResult | None, *,
                   key: str = 'results', out_type: RefType | None = None, locale: str | None = None) -> ItemList[FFItem]:
        """Return item list with pagination."""

        def parse(it: JsonData) -> FFItem:
            def set_if(setter: Callable, key: Union[str, Sequence[str]]) -> None:
                if isinstance(key, str):
                    key = (key,)
                for k in key:
                    val = it.get(k)
                    if val is not None and val != '':
                        setter(val)
                        return

            mtype: RefType = it.get('media_type', out_type)
            if mtype == 'tv':
                mtype = 'show'
            if out_type == 'movie' and 'media_type' not in it and 'genre_ids' not in it and 'popularity' not in it:
                mtype = 'collection'  # hack, searching movies returns collections
            ref = MediaRef.from_tmdb(mtype, it.get('id') or 0)
            ffid = ref.ffid
            if out_type == 'show' and type in ('season', 'episode') and (show_id := it.get('show_id')) and (sz_num := it.get('season_number')):
                ref = MediaRef.from_tmdb('show', show_id, season=sz_num, episode=it.get('episode_number'))
            it['_ref'] = ref
            ff = FFItem(ref)
            if ffid:
                ff.ffid = ffid
            ff.source_data = it
            vtag = ff.vtag
            ff.label = ff.title = it.get('title', it.get('name')) or ''
            if locale and locale.partition('-')[0] == 'en':
                vtag.setEnglishTitle(ff.label)
            set_if(vtag.setOriginalTitle, 'original_name')
            set_if(vtag.setPlot, 'description')
            set_if(vtag.setPlot, 'overview')
            set_if(vtag.setPremiered, ('release_date', 'created_at'))
            set_if(vtag.setFirstAired, 'air_date')
            set_if(vtag.setFirstAired, 'first_air_date')
            if date := ff.date:
                vtag.setYear(date.year)
            # set_if(vtag.set..., 'popularity')
            vtag.setRatings({'tmdb': (it.get('vote_average', 0.0), it.get('vote_count', 0))}, 'tmdb')
            if ref.is_show:
                set_if(vtag.setSeriesStatus, 'status')
            if episode_type := self.parse_episode_type(it):
                vtag.setEpisodeType(episode_type)
            ff.role = it.get('character', it.get('job', ''))
            if rating := it.get('rating'):
                ff.setProperty('user_rating', str(rating))
                ff.setProperty('tmdb.user_rating', str(rating))
                # vtag.setUserRating(rating)
            art = {}
            if logo := it.get('logo_path'):
                art['clearlogo'] = f'{self.art_image_url}{logo}'
                if ref.type == 'person':
                    art['poster'] = art['clearlogo']
            if poster := it.get('poster_path'):
                art['poster'] = f'{self.person_image_url}{poster}'
            if landscape := it.get('still_path'):
                art['landscape'] = f'{self.art_landscape_url}{landscape}'
            if fanart := it.get('backdrop_path'):
                fanart = f'{self.art_landscape_url}{fanart}'
                art['fanart'] = fanart
                art.setdefault('landscape', fanart)
            if not art.get('thumb') and (thumb := art.get('landscape' if ref.is_episode else 'poster', art.get('clearlogo'))):
                art['thumb'] = thumb
            if art:
                ff.setArt(art)
            if count := it.get('number_of_items'):
                ff._children_count = count
            if (val := it.get('public')) is not None:
                ff.temp.public = val
            return ff

        if out_type is None:
            out_type = type

        if isinstance(data, Mapping):
            total_pages = data.get('total_pages', 0)
            total_results = data.get('total_results', 0)
            page_size = 0
            # make sure that page size is correct
            if total_pages and total_results and (total_results + self.PAGE_SIZE - 1) // self.PAGE_SIZE == total_pages:
                page_size = self.PAGE_SIZE
            return ItemList([parse(it) for it in data.get(key, ())], page=data.get('page', 0),
                            total_pages=total_pages, total_results=total_results, page_size=page_size)

        if isinstance(data, Sequence) and not isinstance(data, str):
            return ItemList([parse(it) for it in data], page=1, total_pages=1, total_results=len(data), page_size=len(data))

        return ItemList.empty()

    def get_videos(self, ref: MediaRef) -> ...:
        if ref.type == 'movie':
            path = f'movie/{ref.tmdb_id}'
        elif ref.type == 'show':
            path = '/'.join(f'{k}{v}' for k, v in zip(['tv/', 'season/', 'episode/'], ref.tmdb_tv_tuple))
        else:
            raise ValueError(f'Unsupported TMDB media type {ref.type}, can NOT use season or episode ID or else')
        lang = self._update_locale()
        trailers_lang_priority = {lang.partition('-')[0]: 0, 'en': 1}
        result = self.get(f'{path}', append_to_response=('videos',), params={'include_video_language': f'{lang},en-US,en-GB'})
        if not isinstance(result, Mapping):
            return []
        items = sorted(
            result['videos']['results'],
            key=lambda t: (not t.get('official', False), t['type'] != 'Trailer', trailers_lang_priority.get(t['iso_639_1']), t.get('published_at', ''))
        )
        return [MediaVideo(lang=it['iso_639_1'], name=it['name'], key=it['key'], site=it['site'], type=it['type'], official=it['official'], published_at=fromisoformat(it['published_at'])) for it in items]

    def get_alternative_titles(self, ref: MediaRef) -> Sequence[JsonData]:
        """Get alternative titles for movie or show."""
        if (rtype := ref.real_type) not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.get_alternative_titles() got incorrect type {rtype!r} only movie or show allowed')
        assert rtype in ('movie', 'show')
        mtype = self._main2tmdb_type.get(rtype)
        result = self.get(f'{mtype}/{ref.tmdb_id}/alternative_titles')
        if isinstance(result, Mapping):
            return result.get('results', ())
        return ()

    # --- General API ---

    @requests.netcache('discover')
    def discover(self,
                 type: MainMediaType,
                 *,
                 page: int = 1,
                 # all filter paramaters from https://developer.themoviedb.org/reference/discover-movie
                 **kwargs: Unpack[DiscoveryFilters],
                 ) -> ItemList[FFItem]:
        """Discover media."""
        if type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.discover() got incorrect type {type!r}')
        try:
            ctype = self._main2tmdb_type[type]
        except KeyError:
            return ItemList.empty()

        allowed = set(self.DISCOVER_FILTERS)
        if kwargs.keys() - allowed:
            wrong = ', '.join(kwargs.keys() - allowed)
            raise TypeError(f'TmdbApi.discover() unknown filter params: {wrong}')

        params = {}
        if const.tmdb.avoid_keywords:
            kwargs.setdefault('without_keywords', const.tmdb.avoid_keywords)
        for k, v in kwargs.items():
            if v is not None:
                if not isinstance(v, str) and isinstance(v, Sequence):
                    v = '|'.join(map(str, v))
                params[k] = v
        params['page'] = page
        # data: Optional[JsonData] = cast(Optional[JsonData], self.get(type, params=params))
        return self._item_list(type, self.get(f'discover/{ctype}', params=params))

    @requests.netcache('discover')
    def discover_list(self,
                      type: MainMediaType,
                      list: TmdbListType,
                      *,
                      page: int = 1,
                      region: Optional[str] = None,
                      ) -> ItemList:
        """Discover media list (tweaked discover)."""
        if type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.discover() got incorrect type {type!r}')
        if list not in get_typing_args(TmdbListType):
            raise ValueError(f'TmdbApi.discover() got incorrect list {list!r}')
        try:
            ctype = self._main2tmdb_type[type]
        except KeyError:
            return ItemList.empty()

        params: Dict[str, Any] = {'page': page}
        if region:
            params['region'] = region
        return self._item_list(type, self.get(f'{ctype}/{list}', params=params))

    @requests.netcache('discover')
    def trending(self,
                 type: MainMediaType,
                 time: TimeWindow = 'week',
                 *,
                 page: int = 1,
                 ) -> ItemList:
        if type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.trending() got incorrect type {type!r}')
        if time not in get_typing_args(TimeWindow):
            raise ValueError(f'TmdbApi.trending() got incorrect time window {time!r}')
        ctype = self._main2tmdb_type[type]

        params: Dict[str, Any] = {'page': page}
        return self._item_list(type, self.get(f'trending/{ctype}/{time}', params=params))
        # Daily Trending
        # https://api.themoviedb.org/3/trending/movie/day?api_key=###
        # https://api.themoviedb.org/3/trending/tv/day?api_key=###
        # https://api.themoviedb.org/3/trending/person/day?api_key=###
        # https://api.themoviedb.org/3/trending/all/day?api_key=###
        # Weekly Trending
        # https://api.themoviedb.org/3/trending/movie/week?api_key=###
        # https://api.themoviedb.org/3/trending/tv/week?api_key=###
        # https://api.themoviedb.org/3/trending/person/week?api_key=###
        # https://api.themoviedb.org/3/trending/all/weekly?api_key=###

    @requests.netcache('art')
    def configuration(self, name: TmdbConfName) -> Sequence[JsonData]:
        """Get configuration lists like countries, languages etc."""
        data = self.get(f'configuration/{name}')
        if isinstance(data, Sequence):
            return data
        return []

    @requests.netcache('art')
    def genres(self, type: MainMediaType) -> List[JsonData]:
        """Get genres lists."""
        if type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.genres() got incorrect type {type!r}')
        ctype = self._main2tmdb_type[type]

        data = self.get(f'genre/{ctype}/list')
        if isinstance(data, Mapping):
            return data['genres']
        return []

    def find_id(self, source: ExternalIdType, id: int | str) -> MediaRef | None:
        """Find external id."""
        data = self.get(f'find/{id}', params={'external_source': f'{source}_id'})
        # with open(f'/tmp/find_{source}_{id}.json', 'w') as f:
        #     import json
        #     json.dump(data, f, indent=2)
        if not isinstance(data, Mapping):
            return None
        for mtype, key in self._ext_id_results.items():
            if data.get(key):
                data = data[key][0]
                return MediaRef.from_tmdb(mtype, data['id'], data.get('season_number'), data.get('episode_number'))
        return None

    def find_ids(self, source: ExternalIdType, ids: Iterable[int | str]) -> Sequence[MediaRef | None]:
        """Find list of external id."""
        def get(id: int | str) -> MediaRef | None:
            return self.find_id(source, id)

        with RequestsPoolExecutor(max_thread_workers()) as ex:
            jobs = ex.map(get, ids)
        return list(jobs)

    def find_mixed_ids(self, ids: Iterable[tuple[ExternalIdType, int | str]]) -> Sequence[MediaRef | None]:
        """Find list of external id as list of (source, id)."""
        def get(args: tuple[ExternalIdType, int | str]) -> MediaRef | None:
            src, id = args
            return self.find_id(src, id)

        with RequestsPoolExecutor(max_thread_workers()) as ex:
            jobs = ex.map(get, ids)
        return list(jobs)

    @requests.netcache('search')
    def search(self,
               type: SearchType,
               query: str,
               *,
               page: int = 1,
               **kwargs: Unpack[SearchFilters],
               ) -> ItemList[FFItem]:
        """Search items with /search endpoint."""
        stype = self._search2tmdb.get(type)
        if not stype:
            return ItemList.empty()
        params = {'page': page, 'query': query, **kwargs}
        return self._item_list(type, self.get(f'search/{stype}', params=params))

    def person_credits(self, person_id: int, type: PersonDataType) -> PersonCredits:
        """Get person credits."""
        mtype = self._person2ref[type]
        person_id = self._ref_id_to_tmdb(person_id)
        data = self.get(f'person/{person_id}/{type}')
        if isinstance(data, Mapping):
            return PersonCredits(cast=tuple(self._item_list(mtype, data.get('cast', ()))),
                                 crew=tuple(self._item_list(mtype, data.get('crew', ()))))
        return PersonCredits()

    def person_images(self, person_id: int) -> Sequence[JsonData]:
        """Get person images."""
        person_id = self._ref_id_to_tmdb(person_id)
        data = self.get(f'person/{person_id}/images')
        if isinstance(data, Mapping):
            return data.get('profiles', [])
        return []

    def collection_items(self, collection_id: int) -> Sequence[FFItem]:
        """Get collection's items."""

        def parse(it: JsonData) -> FFItem:
            mtype = it.get('media_type', 'movie')
            if mtype == 'tv':
                mtype = 'show'
            ref = MediaRef.from_tmdb(mtype, it['id'])
            ff = FFItem(ref)
            ff.title = it.get('title') or ''
            ff.vtag.setOriginalTitle(it.get('original_title') or '')
            art = {}
            if poster := it.get('poster_path'):
                art['poster'] = f'{self.art_image_url}{poster}'
            if landscape := it.get('still_path'):
                art['landscape'] = f'{self.art_landscape_url}{landscape}'
            if art:
                ff.setArt(art)
            return ff

        collection_id = self._ref_id_to_tmdb(collection_id)
        data = self.get(f'collection/{collection_id}')
        if isinstance(data, Mapping):
            return [parse(it) for it in data.get('parts', ())]
        return []

    def media_resource(self, ref: MediaRef, resource: MediaResource, *, page: int = 1) -> List[FFItem]:
        """Get media (movie, show) resource."""
        if ref.type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.media_resource() got incorrect type {ref.type!r}')
        if resource not in get_typing_args(MediaResource):
            raise ValueError(f'TmdbApi.media_resource() got incorrect resource {resource!r}')
        try:
            mtype = cast(MainMediaType, ref.type)
            ctype = self._main2tmdb_type[mtype]
        except KeyError:
            return ItemList.empty()

        params = {'page': page}
        return self._item_list(mtype, self.get(f'{ctype}/{ref.tmdb_id}/{resource}', params=params))

    def media_keywords(self, ref: MediaRef) -> List[FFItem]:
        """Get media (movie, show) resource."""
        if ref.type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.media_resource() got incorrect type {ref.type!r}')
        try:
            mtype = cast(MainMediaType, ref.type)
            ctype = self._main2tmdb_type[mtype]
        except KeyError:
            return ItemList.empty()

        return self._item_list(mtype, self.get(f'{ctype}/{ref.tmdb_id}/keywords'), key='keywords', out_type='keyword')

    @requests.netcache('lists')
    def user_lists(self, account_id: AccountId = 'me', *, page: int = 1, api: ApiVer = None) -> List[FFItem]:
        """Get user lists."""
        account_id = self._ref_id_to_tmdb(account_id)
        if (credentials := self.credentials()).v4 and api != 3 and account_id == 'me':
            data = self.get(f'account/{credentials.account_id}/lists', params={'page': page}, api_version=4)
        elif credentials.v3 and api != 4:
            data = self.get(f'account/{account_id}/lists', params={'page': page})
        else:
            return ItemList.empty()
        return self._item_list('list', data)

    @requests.netcache('lists')
    def user_list_items(self, list_id: int, *, page: int = 1, api: ApiVer = None) -> ItemList[FFItem]:
        """Get user lists."""
        list_id = self._ref_id_to_tmdb(list_id)
        if (credentials := self.credentials()).v4 and api != 3:
            data = self.get(f'list/{list_id}', params={'page': page}, api_version=4)
            return self._item_list('', data)
        if credentials.v3 and api != 4:
            data = self.get(f'list/{list_id}', params={'page': page})
            return self._item_list('', data, key='items')
        return ItemList.empty()

    def _user_list_items_param(self, items: Iterable[FFRef]) -> list[dict[str, str | int]]:
        """Prepare items for user list API."""
        m2t = {
            **self._main2tmdb_type,
            # 'season': 'tv',   # add season's show
            # 'episode': 'tv',  # add epsiode's show
        }
        param = [{
            'media_type': media,
            'media_id': tmdb_id,
        } for it in items if (tmdb_id := (ref := it.ref).tmdb_id) and (media := m2t.get(ref.real_type))]
        return param

    def add_to_user_list(self, list_id: int, items: Iterable[FFRef]) -> int:
        """Add items to the list and return number of added items. Support only v4."""
        list_id = self._ref_id_to_tmdb(list_id)
        if not isinstance(items, Sequence):
            items = tuple(items)
        if not items:
            return True
        to_add = self._user_list_items_param(items)
        if not to_add:
            return False
        data = self.post(f'list/{list_id}/items', data={'items': to_add}, api_version=4)
        if not isinstance(data, Mapping) or not data.get('success'):
            return False
        clear_netcache('lists')
        return sum(res['success'] for res in data['results'])

    def remove_from_user_list(self, list_id: int, items: Iterable[FFRef]) -> int:
        """Remove items tfrom the list and return number of removed items. Support only v4."""
        list_id = self._ref_id_to_tmdb(list_id)
        if not isinstance(items, Sequence):
            items = tuple(items)
        if not items:
            return True
        to_remove = self._user_list_items_param(items)
        if not to_remove:
            return False
        data = self.delete(f'list/{list_id}/items', data={'items': to_remove}, api_version=4)
        if not isinstance(data, Mapping) or not data.get('success'):
            return False
        clear_netcache('lists')
        return sum(res['success'] for res in data['results'])

    def create_user_list(self, name: str, *, descr: str = '', public: bool = True, locale: Optional[str] = None, api: ApiVer = None) -> Optional[int]:
        """Create user list and return its ID."""
        # XXX XXX XXX   ----   TMDB (v4) ignores "public": false !!!!  ----   XXX XXX XXX
        if not name:
            raise ValueError('TmdbApi.create_user_list() requires name')
        if locale is None:
            locale = self.lang or 'pl-PL'
        country, _, lang = locale.partition('-')
        if (credentials := self.credentials()).v4 and api != 3:
            d = {'name': name, 'description': descr, 'public': public, 'iso_3166_1': country, 'iso_639_1': lang}
            fflog(f'new list data: {d}')
            data = self.post('list', data={'name': name, 'description': descr, 'public': public,
                                           'iso_3166_1': country, 'iso_639_1': lang}, api_version=4)
        elif credentials.v3 and api != 4:
            data = self.post('list', data={'name': name, 'description': descr, 'language': lang}, api_version=3)
        else:
            return None
        if isinstance(data, Mapping) and data.get('success'):
            clear_netcache('lists')
            return data.get('id')
        fflog(f'[FF][TMDB] Failed to create list {name!r}, data={data!r}')
        return None

    def delete_user_list(self, list_id: int) -> bool:
        """Delete the list (and its content). Return true on success. Support only v4."""
        list_id = self._ref_id_to_tmdb(list_id)
        data = self.delete(f'list/{list_id}', data=None, api_version=4)
        if not isinstance(data, Mapping) or not data.get('success'):
            return False
        clear_netcache('lists')
        return True

    @requests.netcache('lists')
    def user_general_lists(self,
                           list_type: UserGeneralListType,
                           type: Union[MainMediaType, MainMediaTypeList],
                           account_id: AccountId = 'me',
                           *,
                           page: int = 1,
                           chunk: int = 0,
                           sort: str = 'created_at.desc',
                           ) -> ItemList[FFItem]:
        """Get user lists."""
        def get(mtype: MainMediaType) -> ItemList[FFItem]:
            if mtype not in get_typing_args(MainMediaType):
                raise ValueError(f'TmdbApi got incorrect type {mtype!r}')
            ctype = self._main2tmdb_type2[mtype]
            data = self.get(f'account/{account_id}/{list_type}/{ctype}', params={'page': page, 'sort_by': sort})
            return self._item_list(mtype, data)

        if isinstance(type, str):
            type = cast(MainMediaType, type.split(','))
        if not type:
            return ItemList.empty()
        if len(type) == 1:
            return get(type[0])
        with RequestsPoolExecutor(max_thread_workers()) as pool:
            datas: Iterable[ItemList[FFItem]] = pool.map(get, type)
        return ItemList(join_items(*datas, zip_chunk=chunk), page=page, total_pages=max((d.total_pages for d in datas), default=1))

    def _set_general_list_items(self,
                                list_type: UserGeneralListType,
                                items: Iterable[FFRef],
                                *,
                                add: bool,
                                account: AccountId = 'me',
                                ) -> int:
        """Add or remove items to/from the general list (favorites, watchlist) and return number of proceeded items. Support only v3."""
        def update(it: JsonData) -> bool:
            data = self.post(f'account/{account}/{list_type}', data=it, api_version=3)
            return bool(isinstance(data, Mapping) and data.get('success'))

        if not isinstance(items, Sequence):
            items = tuple(items)
        if not items:
            return 0
        m2t = {**self._main2tmdb_type}
        to_update = [{
            'media_type': media,
            'media_id': tmdb_id,
            list_type: add,  # add to the list type
        } for it in items if (tmdb_id := (ref := it.ref).tmdb_id) and (media := m2t.get(ref.real_type))]
        if not to_update:
            return 0
        with RequestsPoolExecutor(max_thread_workers()) as ex:
            results = ex.map(update, to_update)
        clear_netcache('lists')
        return sum(results)

    def add_items_to_general_list(self,
                                  list_type: UserGeneralListType,
                                  items: Iterable[FFRef],
                                  *,
                                  account: AccountId = 'me',
                                  ) -> int:
        return self._set_general_list_items(list_type, items, add=True, account=account)

    def remove_items_from_general_list(self,
                                       list_type: UserGeneralListType,
                                       items: Iterable[FFRef],
                                       *,
                                       account: AccountId = 'me',
                                       ) -> int:
        return self._set_general_list_items(list_type, items, add=False, account=account)

    def web_url(self, ref: MediaRef) -> str:
        """Return link to media for humans."""
        if not ref.tmdb_id:
            return ''
        if ref.type == 'show':
            path = '/'.join(f'{k}{v}' for k, v in zip(['tv/', 'season/', 'episode/'], ref.tmdb_tv_tuple))
        elif ref.type in ('movie', 'person', 'collection'):
            path = f'{ref.type}/{ref.tmdb_id}'
        else:
            return ''  # unspotted media type
        return f'https://www.themoviedb.org/{path}'

    @requests.netcache('art')
    def providers(self, type: MainMediaType, region: str) -> Sequence[TmdbProvider]:
        """Return list of providers."""

        def parse(it: JsonData) -> TmdbProvider:
            if logo_path := it['logo_path']:
                logo = f'{self.art_image_url}{logo_path}'
            else:
                logo = None
            return TmdbProvider(id=it['provider_id'], name=it['provider_name'], logo=logo, display_priority=it['display_priority'])

        ctype = self._main2tmdb_type[type]  # konersja typu FF na TMDB
        result = self.get(f'watch/providers/{ctype}', params={'watch_region': region})
        if isinstance(result, Mapping):
            return [parse(it) for it in result.get('results', ())]
        return ()

    @requests.netcache('discover')
    def popular_people(self, *, page: int = 1) -> Sequence[FFItem]:
        """Return popular people."""
        return self._item_list('person', self.get('person/popular', params={'page': page}))

    def aliases(self, type: MainMediaType, id: int) -> Sequence[JsonData]:
        """Get media (movie, show) aliases."""
        if type not in get_typing_args(MainMediaType):
            raise ValueError(f'TmdbApi.aliases() got incorrect type {type!r}')
        try:
            ctype = self._main2tmdb_type[type]
        except KeyError:
            return []

        data = self.get(f'{ctype}/{id}/alternative_titles')
        if isinstance(data, Mapping):
            return data.get('results', ())
        return []

    def episode_groups(self, show: FFRef | int) -> list[FFEpisodeGroup]:
        """Get episode groups for the show."""
        if isinstance(show, int):
            show_id = show
        else:
            show_id = show.ref.tmdb_id
        data = self.get(f'tv/{show_id}/episode_groups')
        if isinstance(data, Mapping):
            return [FFEpisodeGroup(tmdb_id=it['id'], name=it['name'], description=it.get('description', ''),
                    episode_count=it.get('episode_count', ()), group_count=it.get('group_count', ()),
                    type=FFEpisodeGroupType(it.get('type', 0)))
                    for it in data.get('results', ())]
        return []

    def episode_group(self, group: str, *, show: FFRef | int | None = None) -> FFEpisodeGroup | None:
        """Get given episode group items by group hex ID. Return "seasons" (groups) with episodes."""
        data = self.get(f'tv/episode_group/{group}', lang='')  # "language" is not allowed here (in tmdb api)
        if isinstance(data, Mapping) and data.get('id'):
            # import json
            # with open('/tmp/e.json', 'w') as f:  json.dump(data, f, indent=2)  # XXX, TODO: remove it
            result = FFEpisodeGroup(tmdb_id=data['id'], name=data['name'], type=FFEpisodeGroupType.from_tmdb(data['type']),
                                    description=data['description'], network=data['network'],
                                    episode_count=data['episode_count'], group_count=data['group_count'])
            if show is None:
                any_episode = next(iter(ep for gr in data.get('groups', ()) for ep in gr.get('episodes', ())), None)
                if any_episode is None:
                    return result
                show = MediaRef.tvshow(any_episode['show_id'])
            elif isinstance(show, int):
                show = MediaRef.from_tmdb('show', show)
            else:
                show = show.ref
            seasons = []
            for group_item in sorted(data.get('groups', ()), key=lambda it: it.get('order', 0)):
                sz = FFItem(ref=show.with_season(group_item['order']), type='season')
                # sz.season = sz.ref.season
                sz.title = group_item['name']
                sz.vtag.setUniqueID(group_item['id'], 'tmdb_episode_group_id')
                episodes: list[TmdbItemJson] = sorted(group_item.get('episodes', ()), key=lambda it: it.get('order', 0))
                sz.children_items = self._item_list('episode', episodes, out_type='show', locale=LOCALE_EN)
                for i, ep in enumerate(sz.children_items, 1):
                    ep.season = sz.season
                    ep.episode = i
                if sz.children_items:
                    sz.vtag.setFirstAired(sz.children_items[0].date)
                seasons.append(sz)
            result.items = seasons
            return result
        return None

    def episode_group_list(self, group: str, *, show: FFRef | int | None = None) -> ItemList[FFItem]:
        gr = self.episode_group(group=group, show=show)
        return ItemList.empty() if gr is None or gr.items is None else ItemList.single(gr.items)

    def episode_group_mapping(self, *, show: FFRef | int | None = None, group: str) -> dict[MediaRef, MediaRef]:
        """Get given episode group items by group hex ID, Return dict [main_order] = group_order."""
        data = self.get(f'tv/episode_group/{group}', lang='')  # "language" is not allowed here (in tmdb api)
        if isinstance(data, Mapping):
            if not show:
                show = MediaRef.tvshow(0)
            elif isinstance(show, int):
                show = MediaRef.tvshow(show)
            else:
                show = show.ref
            group_item: JsonData
            refs: dict[MediaRef, MediaRef] = {}
            for group_item in sorted(data.get('groups', ()), key=lambda it: it['order']):
                sn = group_item['order']  # XXX  Czy to na pewno jest numer "sezonu" ???
                episodes: list[TmdbItemJson] = group_item.get('episodes', [])
                episodes.sort(key=lambda it: it.get('order', 0))
                refs.update((show.with_season_episode(ep['season_number'], ep['episode_number']), show.with_season_episode(sn, en))
                            for en, ep in enumerate(episodes, 1))
            return refs
        return {}

    def user_ref_rating(self, ref: FFRef) -> float | None:
        """"Get user media rating."""
        # movie/{movie_id}/account_states
        ref = ref.ref
        if (rtype := ref.real_type) not in ('movie', 'show', 'episode'):
            return None
        assert ref.type in ('movie', 'show')
        ctype = self._main2tmdb_type[ref.type]
        path = '/'.join(f'{k}{v}' for k, v in zip([f'{ctype}/', 'season/', 'episode/'], ref.tmdb_id_tuple))
        data = self.get(f'{path}/account_states', api_version=3)
        if isinstance(data, Mapping):
            rated_data = data.get('rated')
            if isinstance(rated_data, dict):
                return rated_data.get('value') or None
            return None

        return None

    def user_ratings(self, type: Literal['movie', 'show', 'episode'] | None, page: int = 1) -> ItemList[FFItem]:
        """Get user media ratings."""
        if type is None:
            with RequestsPoolExecutor(max_thread_workers()) as ex:
                movie = ex.submit(self.user_ratings, 'movie', page)
                show = ex.submit(self.user_ratings, 'show', page)
                episode = ex.submit(self.user_ratings, 'episode', page)
                return ItemList.single((*movie.result(), *show.result(), *episode.result()))

        ctype = self._ref2rating[type]
        account_id = 'me'
        data = self.get(f'account/{account_id}/rated/{ctype}', params={'page': page or 1})
        if not page and isinstance(data, Mapping):
            total_pages = data.get('total_pages') or 1
            if total_pages > 1:
                with RequestsPoolExecutor(max_thread_workers()) as ex:
                    jobs = [ex.submit(self.get, f'account/{account_id}/rated/{ctype}', params={'page': pg}) for pg in range(2, total_pages + 1)]
                    datas = [data, *(job.result() for job in jobs)]
                data = {'results': [it for d in datas if isinstance(d, Mapping) for it in d.get('results', ())]}
        out_type = 'show' if type == 'episode' else type
        return self._item_list(type, data, out_type=out_type)

    def add_user_rating(self, item: FFRef, *, rating: float | None) -> bool:
        """Add (set) single media user rating. If `rating` is None, get ratings from FFItem."""
        if rating is None:
            if not isinstance(item, FFItem):
                raise ValueError('TmdbApi.add_user_rating() got ref with missing ratings')
            rating = item.vtag.getUserRating()
        ref = item.ref
        if (rtype := ref.real_type) not in ('movie', 'show', 'episode'):
            raise ValueError(f'TmdbApi.add_user_rating() got incorrect type {rtype!r} only movie, show or episode allowed')
        assert ref.type in ('movie', 'show')
        ctype = self._main2tmdb_type[ref.type]
        path = '/'.join(f'{k}{v}' for k, v in zip([f'{ctype}/', 'season/', 'episode/'], ref.tmdb_id_tuple))
        data = self.post(f'{path}/rating', data={'value': rating}, api_version=3)
        return bool(isinstance(data, Mapping) and data.get('success'))

    def remove_user_rating(self, item: FFRef) -> bool:
        ref = item.ref
        if (rtype := ref.real_type) not in ('movie', 'show', 'episode'):
            raise ValueError(f'TmdbApi.add_user_rating() got incorrect type {rtype!r} only movie, show or episode allowed')
        assert ref.type in ('movie', 'show')
        ctype = self._main2tmdb_type[ref.type]
        path = '/'.join(f'{k}{v}' for k, v in zip([f'{ctype}/', 'season/', 'episode/'], ref.tmdb_id_tuple))
        data = self.delete(f'{path}/rating', api_version=3)
        return bool(isinstance(data, Mapping) and data.get('success'))

    # --- Following methods MUST be overridden ---

    def credentials(self) -> TmdbCredentials:
        """Return current credentials."""
        if self.api_key is not None:
            return TmdbCredentials(api_key=self.api_key)
        raise NotImplementedError('api.tmdb.TmdbApi.credentials() is not implemented')

    def save_session(self, *, session_id: Optional[str], access_token: Optional[str] = None) -> None:
        """Set session ID. Override this method and remember session ID or remove if None."""
        print(session_id, access_token)

    # --- Following methods COULD be overridden ---

    def dialog_create(self, request_token: str, verification_url: str) -> DialogId:
        """Create GUI dialog."""
        print(f'Confirm {request_token!r}, visit site {verification_url}')

    def dialog_close(self, dialog: DialogId) -> None:
        """Close GUI dialog."""
        print()

    def dialog_is_canceled(self, dialog: DialogId) -> bool:
        """Return True if GUI dialog is canceled."""
        return False

    def dialog_update_progress(self, dialog: DialogId, progress: float) -> None:
        """Update GUI dialog progress-bar."""
        print(f'\r {progress:5.1f}           ', end='')

    def auth_failed(self) -> None:
        """401 - auth error, should inform user or force auth again."""
        pass


def seasons_iter(*data: JsonData | Sequence[JsonData], common: bool = False) -> Iterator[tuple[JsonData, ...]]:
    """Iterate over season lists (TMDB JSON)."""
    if not data:
        return
    seasons = [{it['season_number']: it for it in (dt if isinstance(dt, Sequence) else dt.get('seasons', ()))}
               for dt in data]
    common_keys = seasons[0].keys()
    for i in range(1, len(seasons)):
        common_keys &= seasons[i].keys()
    if not common:
        common_keys |= seasons[0].keys()
    common_keys = sorted(common_keys)
    for key in common_keys:
        yield tuple(it.get(key, {}) for it in seasons)


def episodes_iter(*data: JsonData | Sequence[JsonData], common: bool = False) -> Iterator[tuple[JsonData, ...]]:
    """Iterate over season lists (TMDB JSON)."""
    if not data:
        return
    episodes = [{(it['season_number'], it['episode_number']): it for it in (dt if isinstance(dt, Sequence) else dt.get('episodes', ()))}
                for dt in data]
    common_keys = episodes[0].keys()
    for i in range(1, len(episodes)):
        common_keys &= episodes[i].keys()
    if not common:
        common_keys |= episodes[0].keys()
    common_keys = sorted(common_keys)
    for key in common_keys:
        yield tuple(it.get(key, {}) for it in episodes)


# --- DEBUG & TESTS ---


if __name__ == '__main__':
    import json
    import sty
    # from pprint import pprint
    from ..ff.cmdline import DebugArgumentParser, parse_ref, parse_movie_ref, parse_show_ref
    from ..ff import apis
    from ..ff.settings import settings
    # from ..ff.settings import settings
    code_color: str = f'{sty.fg.red}{sty.ef.bold}'

    class TerminalTmdb(TmdbApi):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.canceled = False

        def credentials(self):
            user: str = settings.getString('tmdb.username')
            password: str = settings.getString('tmdb.password')
            session_id: str = settings.getString('tmdb.sessionid')
            access_token = settings.getString('tmdb.access_token')
            return TmdbCredentials(user=user, password=password, session_id=session_id, access_token=access_token)

        def save_session(self, *, session_id: Optional[str], access_token: Optional[str] = None) -> None:
            """Set session ID. Save session ID or remove if None."""
            settings.setString('tmdb.sessionid', session_id or '')
            settings.setString('tmdb.access_token', access_token or '')

        def dialog_create(self, request_token: str, verification_url: str) -> DialogId:
            """Create GUI dialog."""
            import segno
            import sys
            print(f'[TMDB] Auth: visit {sty.ef.bold}{verification_url}{sty.rs.all}')
            qrcode = segno.make(verification_url)
            qrcode.terminal(out=sys.stderr, compact=True)
            self.canceled = False
            return 1

        def dialog_close(self, dialog: DialogId) -> None:
            """Close GUI dialog."""

        def dialog_cancel(self, dialog: DialogId = None) -> None:
            """Cancel GUI dialog. Debug only."""
            self.canceled = True

        def dialog_is_canceled(self, dialog: DialogId) -> bool:
            """Return True if GUI dialog is canceled."""
            return self.canceled

        def dialog_update_progress(self, dialog: DialogId, progress: float) -> None:
            """Update GUI dialog progress-bar."""
            print(f'[TMDB] auth : {progress:5.1f}')

    def parse_request(s: str) -> MediaRequest:
        """Parse request (ref, details, lang) string."""
        vv = s.split(':', 2)
        ref = parse_ref(vv[0])
        try:
            details = InfoDetails.new(vv[1]) if len(vv) > 1 else InfoDetails.NOT_DEFINED
        except KeyError as e:
            fflog.error(f'{code_color}Unknown InfoDetails flag {e.args[0]!r}{sty.rs.all}')
            raise ValueError(s)
        lang = vv[2] if len(vv) > 2 else ''
        return MediaRequest(ref, details=details, locale=lang)

    def jprint(data: JsonData) -> None:
        print(json.dumps(data, indent=2))

    def mprint(info: MediaPlayInfoDict) -> None:
        print('{')
        for k, v in info.items():
            # x = str(v).replace(', item_count', ',\n                  item_count')
            x = str(v).replace(', duration', ',\n                  duration')
            print(f'  {k}:\n    {x},')
        print('}')

    def add_common(pp: DebugArgumentParser) -> None:
        pp.add_argument('-d', '--details', metavar='FLAG[,FLAG]…', type=InfoDetails.new, default=InfoDetails.DEFAULT,
                        help='set media detail flags (see InfoDetails)')
        # pp.add_argument('-c', '--aggregate-credits', action='store_true', help='get aggregate credits')
        pp.add_argument('--req-locale', default='', help='language (e.g. pl-PL) for InfoDetails.INFO_ORIG')
        pp.add_argument('--append-seasons', default=[], type=lambda s: [int(v) for v in s.split(',')], help='extra seasons for show')

    p = DebugArgumentParser(dest='cmd')
    p.add_argument('--tmdb-api-key', help='TMDB API key, for get extra info')
    with p.with_subparser('get', help='get any media') as pp:
        pp.add_argument('id', type=parse_ref, help='tmdb movie/show/person id')
        add_common(pp)
    with p.with_subparser('movie') as pp:
        pp.add_argument('id', type=parse_movie_ref, help='tmdb movie id')
        add_common(pp)
    with p.with_subparser('tv') as pp:
        pp.add_argument('id', type=parse_show_ref, help='tmdb tv-show id')
        pp.add_argument('season', type=int, nargs='?', help='optional season number')
        pp.add_argument('episode', type=int, nargs='?', help='optional episode number')
        add_common(pp)
    with p.with_subparser('info') as pp:
        pp.add_argument('ids', type=parse_request, nargs='+', help='tmdb refs (m123, s123, s123/4, s123/4/5)')
        add_common(pp)
    with p.with_subparser('videos') as pp:
        pp.add_argument('ids', type=parse_ref, nargs='+', help='tmdb refs (m123, s123, s123/4, s123/4/5)')
    with p.with_subparser('discover') as pp:
        pp.add_argument('type', choices=('movie', 'tv', 'show'), help='media type')
        pp.add_argument('filter', nargs='*', help='filter (from DiscoveryFilters, ex: with_runtime<=90)')
        pp.add_argument('-p', '--page', type=int, default=1, help='page number (1..500)')
    with p.with_subparser('list') as pp:
        pp.add_argument('list', nargs='?', help='list ID')
        pp.add_argument('-A', '--api', choices=(3, 4), type=int, help='API version')
        pp.add_argument('-p', '--page', type=int, help='page number')
        pp.add_argument('-a', '--add', action='append', type=parse_ref, help='add media to the list')
    with p.with_subparser('auth') as pp:
        pp.add_argument('-A', '--api', choices=(3, 4), type=int, default=3, help='API version')
        pp.add_argument('-U', '--user', help='username (v3)')
        pp.add_argument('-P', '--pass', dest='password', help='password (v3)')
        pp.add_argument('-R', '--revoke', '--deauth', action='store_true', help='revoke authorization')
        pp.add_argument('-f', '--force', action='store_true', help='force auth')
    with p.with_subparser('skel') as pp:
        pp.add_argument('ids', type=parse_ref, nargs='+', help='tmdb refs (m123, s123, s123/4, s123/4/5)')
        pp.add_argument('-s', '--show', choices=('last', 'all', 'none'), default='last', help='show seasons options')
        pp.add_argument('-t', '--translations', action='store_true', help='append translations')
    with p.with_subparser('xxx') as pp:
        pass
    args = p.parse_args()
    # print(args); raise SystemExit(0)  # DEBUG

    tmdb = TerminalTmdb(api_key=const.dev.tmdb.api_key or apis.tmdb_API)

    if ids := getattr(args, 'ids', None):
        for req in ids:
            if not req.locale:
                req.locale = getattr(args, 'req_locale', '')
            if req.details == InfoDetails.NOT_DEFINED:
                req.details = getattr(args, 'details', InfoDetails.DEFAULT)

    if args.cmd == 'xxx':
        def get(ref: FFRef):
            tmdb.get_media_by_ref(ref.ref)
        # tmdb.get_media_by_ref(MediaRef('movie', 100950387))
        # exit()
        from . import depaginate
        with depaginate(tmdb, limit=1000) as api:
            items = api.trending('movie')
        print(len(items))
        print(items[0])
        with RequestsPoolExecutor(max_workers=1000) as pool:
            full = list(pool.map(get, items))
        print(len(items), len(full))

    if args.cmd == 'get':
        req = MediaRequest(args.id, details=args.details, locale=args.req_locale, seasons=args.append_seasons)
        jprint(tmdb.get_media_by_ref(req))
    elif args.cmd == 'movie':
        req = MediaRequest(args.id, details=args.details, locale=args.req_locale, seasons=args.append_seasons)
        jprint(tmdb.get_media_by_ref(req))
    elif args.cmd == 'tv':
        ref = args.id._replace(season=args.season, episode=args.episode)
        req = MediaRequest(ref, details=args.details, locale=args.req_locale, seasons=args.append_seasons)
        jprint(tmdb.get_media_by_ref(req))
    elif args.cmd == 'info':
        # pprint(list(tmdb.x_media_play_dict(args.ids, details=args.details, aggregate_credits=args.aggregate_credits).keys()))
        res = tmdb.get_media_dict_by_ref(args.ids, details=args.details)
        for data in res.values():
            jprint(data)
        # mprint(res)
    elif args.cmd == 'videos':
        # pprint(list(tmdb.x_media_play_dict(args.ids, details=args.details, aggregate_credits=args.aggregate_credits).keys()))
        for ref in args.ids:
            # TODO: merge with master
            jprint(tmdb.get_videos(ref))
    elif args.cmd == 'discover':
        if args.type == 'tv':
            args.type = 'show'
        for it in tmdb.discover(args.type, page=args.page, **Condition.filters_from_str_expr_list(args.filter)):
            print(f'{it.ref:a} :  {it}')
    elif args.cmd == 'list':
        if args.list:
            if args.add:
                tmdb.add_to_user_list(args.list, args.add)
                items = args.add
            else:
                items = tmdb.user_list_items(args.list, page=args.page, api=args.api)
        else:
            items = tmdb.user_lists(page=args.page, api=args.api)
        print(f'{len(items)} item(s)')
        print(items)
    elif args.cmd == 'auth':
        if args.revoke:
            tmdb.revoke_session()
        elif args.user and args.password:
            tmdb.auth_v3(force=args.force)
        else:
            tmdb.auth_v4(force=args.force)
    elif args.cmd == 'skel':
        opts = {
            'none': SkelOptions.NONE,
            'first': SkelOptions.SHOW_LAST_SEASONS,
            'last': SkelOptions.SHOW_LAST_SEASONS,
            'all': SkelOptions.SHOW_ALL_SEASONS,
        }
        opt = opts[args.show]
        if args.translations:
            opt |= SkelOptions.TRANSLATIONS
        tmdb.get_skel_en_media(args.ids, options=opt)
