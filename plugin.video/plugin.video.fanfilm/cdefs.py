"""
FanFilm const (low-level developper) settings.
Do NOT change if you don't know what you doing.

Avoid imports here, please.
Supports `local.py` from plugin userdata folder. File is created if missing.

You can override settings in `local.py`.
Just in the same form as in section "CONST SETTINGS",
>>> const.name = value


Define new setting
------------------

To create new const settings (ex. `const.a.b.c` as `int`) you have to:
 - define the settings
 - assing the value

Define new settings in `class const` structure in "CONST DEFINITION" section.
Every section (ex. `a.b.`) must be a class with `@constdef` decorator.
The name after the last dot (ex. `c`) have to be defined as a class variable with a type annotations.
DO NOT assign default value here.
>>> @constdef
>>> class const:
>>>    @constdef
>>>    class a:
>>>        @constdef
>>>        class b:
>>>            c: int

And just assign values in following "CONST SETTINGS" section.
>>> const.a.b.c = value

Every value `const.x.y = value` must be defined in `class const` structure.
Every value defined settings in `class const` structure must be assigned.


Use const settings
------------------

And use in source code.
>>> from const import const
>>> if const.a.b.c == value: ...
"""

# Only safe imports here, double check BEFORE add something new.
from __future__ import annotations
import runpy
import re
from pathlib import Path
from enum import Enum, IntEnum, Flag, IntFlag, auto as auto_enum
from typing import Optional, Union, Any, Collection, Iterable, Sequence, Mapping, TypeVar, overload, TYPE_CHECKING
from typing_extensions import Literal, Self, TypeIs, Generic, get_args as get_typing_args
from attrs import frozen, field
from frozendict import frozendict
from lib.indexers.defs import DirItemSource, CodeId
from lib.kolang import L  # noqa: 401
if TYPE_CHECKING:
    from typing_extensions import TypeAlias, ClassVar
    from datetime import timedelta
    from simpleeval import SimpleEval
    from xbmcaddon import Settings as XbmcAddonSettings
    from lib.defs import MediaRef, RefType
    from lib.ff.item import FFItem
    from lib.ff.menu import ContentView
    from lib.ff.lists import AddToServiceDefs  # noqa: F401
    from lib.sources import SourceXMeta, SourceItemPattern
    from lib.api.trakt import SortType as TraktSortType, SortBy as TraktSortBy
    from lib.windows.add_to import AddToDialogMediaBarMode

T = TypeVar('T')

#: ID of source color match: PROVIDER or (PROVIDER, SOURCE).
ExSourceId: TypeAlias = 'str | tuple[str, str]'
#: Supported prgramt Tv services.
ProgramTvService: TypeAlias = Literal['filmweb', 'onet', 'teleman']
#: External source ID (name).
ExtSourceId: TypeAlias = str

#: Minute in seconds.
MINUTE = 60
#: Hour in seconds.
HOUR = 60 * MINUTE
#: Day in seconds.
DAY = 24 * HOUR
#: Week in seconds.
WEEK = 7 * DAY

#: KiB in bytes.
KiB = 1024
#: MiB in bytes.
MiB = 1024 * KiB
#: GiB in bytes.
GiB = 1024 * MiB
#: TiB in bytes.
TiB = 1024 * GiB

#: Name of media cache, used in `netcache` settings.
NetCacheName: TypeAlias = Literal['', 'other', 'media', 'art', 'lists', 'discover', 'search']
#: request_cache.DO_NOT_CACHE - Per RFC 4824 - "NOCACHE" in Semaphore Flag Signaling System :-)
DO_NOT_CACHE = 0x0D0E0200020704


def constdef(cls: type[T]) -> T:
    """Const section definition decorator."""

    def __init__(self) -> None:
        cls = type(self)
        ann = getattr(cls, '__annotations__', {})
        for k, v in vars(cls).items():
            if not k.startswith('_'):
                if getattr(v, '_const_def', None):
                    object.__setattr__(self, k, v)
                elif k in ann:
                    name = f'{cls.__qualname__}.{k}'
                    raise TypeError(f'Value for {name!r} is FORBIDEN, assign it in "CONST SETTINGS"')
                else:
                    raise TypeError(f'Pure class {k!r} in {cls.__qualname__!r} is FORBIDDEN, use @constdef')

    def __setattr__(self, key: str, value: Any) -> None:
        if not key.startswith('_'):
            cls = self.__class__
            if key not in getattr(cls, '__annotations__', {}):
                raise AttributeError(f'No const attribute {cls.__qualname__}.{key}')
            if getattr(constdef, '_locked', False) and (old := getattr(self, key, ...)) is not ... and old != value:
                print(f'WARNING: redefine {cls.__qualname__}: {old!r} -> {value!r}')
                raise RuntimeError(f'WARNING: redefine {cls.__qualname__}: {old!r} -> {value!r}')
            if isinstance(value, SettingsDescriptor):
                setattr(cls, key, value)
                return
        object.__setattr__(self, key, value)

    def __repr__(self) -> str:
        return f'<{self.__class__.__qualname__}>'

    cls.__init__ = __init__
    cls.__setattr__ = __setattr__
    cls.__repr__ = __repr__
    cls._const_def = True  # type: ignore
    return cls()


def SETTING(expr: str | bool | None) -> Any:  # Must be `Any` to allow assignment in `const` class.
    """Mark setting to be evaluated."""
    return SettingsDescriptor(expr)  # type: ignore[return-value]


all_languages = (
    'aa', 'ab', 'ae', 'af', 'ak', 'am', 'an', 'ar', 'as', 'av', 'ay', 'az', 'ba', 'be', 'bg', 'bi', 'bm', 'bn', 'bo',
    'br', 'bs', 'ca', 'ce', 'ch', 'cn', 'co', 'cr', 'cs', 'cu', 'cv', 'cy', 'da', 'de', 'dv', 'dz', 'ee', 'el', 'en',
    'eo', 'es', 'et', 'eu', 'fa', 'ff', 'fi', 'fj', 'fo', 'fr', 'fy', 'ga', 'gd', 'gl', 'gn', 'gu', 'gv', 'ha', 'he',
    'hi', 'ho', 'hr', 'ht', 'hu', 'hy', 'hz', 'ia', 'id', 'ie', 'ig', 'ii', 'ik', 'io', 'is', 'it', 'iu', 'ja', 'jv',
    'ka', 'kg', 'ki', 'kj', 'kk', 'kl', 'km', 'kn', 'ko', 'kr', 'ks', 'ku', 'kv', 'kw', 'ky', 'la', 'lb', 'lg', 'li',
    'ln', 'lo', 'lt', 'lu', 'lv', 'mg', 'mh', 'mi', 'mk', 'ml', 'mn', 'mo', 'mr', 'ms', 'mt', 'my', 'na', 'nb', 'nd',
    'ne', 'ng', 'nl', 'nn', 'no', 'nr', 'nv', 'ny', 'oc', 'oj', 'om', 'or', 'os', 'pa', 'pi', 'pl', 'ps', 'pt', 'qu',
    'rm', 'rn', 'ro', 'ru', 'rw', 'sa', 'sc', 'sd', 'se', 'sg', 'sh', 'si', 'sk', 'sl', 'sm', 'sn', 'so', 'sq', 'sr',
    'ss', 'st', 'su', 'sv', 'sw', 'ta', 'te', 'tg', 'th', 'ti', 'tk', 'tl', 'tn', 'to', 'tr', 'ts', 'tt', 'tw', 'ty',
    'ug', 'uk', 'ur', 'uz', 've', 'vi', 'vo', 'wa', 'wo', 'xh', 'xx', 'yi', 'yo', 'za', 'zh', 'zu',
)


if TYPE_CHECKING:
    _LikeStringCompare = str  # to hit type check with str
    _AnyTypeBase = Any
else:
    _LikeStringCompare = object
    _AnyTypeBase = object


class RegEx(_LikeStringCompare):
    """
    Regex comparator.

    Example:
    >>> R('pattern') == 'string'  # True if fullmatch
    """

    rx: re.Pattern[str]

    def __new__(cls, pattern: str, flags: int = 0) -> Self:
        obj = super().__new__(cls)
        obj.rx = re.compile(pattern, flags)
        return obj

    def __eq__(self, other: object) -> bool:
        if isinstance(other, type(self.rx)):
            other = other.pattern
        elif isinstance(other, R):
            other = other.rx.pattern
        return self.rx.fullmatch(str(other)) is not None

    def __hash__(self) -> int:
        return hash(self.rx)

    def __repr__(self) -> str:
        return f'cdefs.{self.__class__.__name__}({self.rx.pattern!r})'


class Glob(RegEx):
    """
    Glob comparator. Uses fnmatch (glob wildcards).
    Flags supported:
    - re.IGNORECASE (case-insensitive matching)

    Example:
    >>> G('pattern*') == 'string'  # True if fullmatch
    """

    def __new__(cls, pattern: str, flags: int = 0) -> Self:
        from fnmatch import translate
        pattern = translate(pattern)
        return super().__new__(cls, pattern, flags)


class Expr(_AnyTypeBase):
    """
    Expression comparator. Evaluates expression with simple-eval and compares result.
    Use '{}' as placeholder for value.

    Example:
    >>> X('1 + 1') == 2  # True
    >>> X('{} > 1)' == 1)  # False
    >>> X('{} > 1)' == 2)  # True
    >>> X('> 1)' == 2)     # True (shortcat for '{} > 1')
    """

    expr: str
    _evaluator: ClassVar[SimpleEval | None] = None

    def __new__(cls, expr: str) -> Self:
        obj = super().__new__(cls)
        expr = expr.strip()
        if expr[:1] in '<>=!':  # shortcat for simple comparisons: "<= 1" means "{} <= 1"
            expr = f'{{}} {expr}'
        obj.expr = expr
        return obj

    def __eq__(self, other: object) -> bool:
        ev = self._get_evaluator()
        expr = self.expr.replace('{}', repr(other))
        try:
            return bool(ev.eval(expr))
        except Exception:
            from lib.ff.log_utils import fflog_exc
            fflog_exc(f'Error evaluating expression {expr!r} for value {other!r}:')
            return False

    def __hash__(self) -> int:
        return hash(self.expr)

    def __repr__(self) -> str:
        return f'cdefs.Expr({self.expr!r})'

    @classmethod
    def _get_evaluator(cls) -> SimpleEval:

        class Settings:

            def __init__(self):
                self._settings: XbmcAddonSettings | None = None

            def __getattr__(self, name: str) -> Any:
                if self._settings is None:
                    from xbmcaddon import Addon
                    self._settings = Addon().getSettings()
                return getattr(self._settings, name)

        if cls._evaluator is None:
            import math
            from simpleeval import SimpleEval
            cls._evaluator = SimpleEval(names={
                'math': math,
                'const': const,
                'settings': Settings(),
                'L': L,
                'KiB': KiB,
                'MiB': MiB,
                'GiB': GiB,
                'TiB': TiB,
                'KB': KiB,
                'MB': MiB,
                'GB': GiB,
                'TB': TiB,
            }, functions={
                'len': len,
                'abs': abs,
                'min': min,
                'max': max,
                'sum': sum,
            })
        return cls._evaluator


#: Short alias for RegEx, to use in settings.
R = RegEx
#: Short alias for Expr, to use in settings.
X = Expr


# ----------------------------------------------------------------------------- #
# -----                          ENUM DEFINITIONS                         ----- #
# ----------------------------------------------------------------------------- #


class ItemDetailLevel(IntEnum):
    """Level of details for media item."""

    #: No details (ref only).
    NONE = 0
    #: Minimal (degraded) details.
    DEGRADED = 1
    #: Basic details.
    BASIC = 2
    #: Full details.
    FULL = 3


class InfoDetails(Flag):
    """
    How to obtain details for media item. For info (tmdb) request.
    For direct media (movie, show, season, episode, etc.) uses INFO_* flags.
    For sub-requests (get non-direct show, season) uses SHOW_*, SEASON_* and EPISODE_* flags.
    """

    NONE = 0

    # --- Request media details ---

    #: Get only media details in API language (info request).
    INFO_LANG = 0x001
    #: Request media details for English (info request). For episode could be ignored with SKIP_EPISODES, use EPISODE_EN.
    INFO_EN = 0x002
    #: Request media details for original language (info request). For episode could be ignored with SKIP_EPISODES, use EPISODE_ORIG.
    INFO_ORIG = 0x004
    #: Request media main details like 'external_ids', etc. (info request).
    COMMON_DETAILS = 0x008
    #: Request media main details like 'external_ids', 'release_dates', 'keywords', 'credits', 'alternative_titles', etc. (info request).
    MEDIA_DETAILS = 0x010
    #: Request media translations (info request).
    IMAGES = 0x020
    #: Request media images (info request).
    VIDEOS = 0x040
    #: Request media videos (info request).
    TRANSLATIONS = 0x080
    #: Request media aggregate credits (info request). Only for TV shows and with DETAILS flag.
    AGGREGATE_CREDITS = 0x100
    #: Request media episode groups (info request). Only for TV shows.
    EPISODE_GROUPS = 0x200
    #: Base details: COMMON_DETAILS + MEDIA_DETAILS.
    BASE_DETAILS = COMMON_DETAILS | MEDIA_DETAILS
    #: Typical details: INFO_LANG + BASE_DETAILS + IMAGES + VIDEOS + TRANSLATIONS.
    DETAILS = INFO_LANG | BASE_DETAILS | IMAGES | VIDEOS | TRANSLATIONS
    #: Mask for locale/lang flags.
    ALL_LOCALES = INFO_LANG | INFO_EN | INFO_ORIG

    # --- Request show details (sub-request) ---

    #: Do not get show details for season/episode (sub-request).
    NO_SHOW = 0
    #: Request show (with simple seasons) for API language (show sub-request). Simple seasons have no translations, will be in API language.
    SHOW_LANG = 0x001_000
    #: Request show (with simple seasons) for English (show sub-request). Simple seasons have no translations, will be in English.
    SHOW_EN = 0x002_000
    #: Request show (with simple seasons) for original language (show sub-request). Simple seasons have no translations, will be in original language.
    SHOW_ORIG = 0x004_000
    #: Request media main details like 'external_ids', etc. (show sub-request).
    SHOW_COMMON_DETAILS = 0x008_000
    #: Request media main details like 'release_dates', etc. (show sub-request).
    SHOW_MEDIA_DETAILS = 0x010_000
    #: Request media translations (show sub-request).
    SHOW_IMAGES = 0x020_000
    #: Request media images (show sub-request).
    SHOW_VIDEOS = 0x040_000
    #: Request media videos (show sub-request).
    SHOW_TRANSLATIONS = 0x080_000
    #: Request media episode groups (show sub-request).
    SHOW_EPISODE_GROUPS = 0x200_000
    #: Request media aggregate credits (show sub-request). Only for TV shows and with DETAILS flag.
    SHOW_AGGREGATE_CREDITS = 0x100_000
    #: Request media main details like 'external_ids', 'release_dates', etc. (show sub-request).
    SHOW_BASE_DETAILS = SHOW_COMMON_DETAILS | SHOW_MEDIA_DETAILS
    #: Typical details for show sub-request.
    SHOW_DETAILS = SHOW_LANG | SHOW_BASE_DETAILS | SHOW_IMAGES | SHOW_VIDEOS | SHOW_TRANSLATIONS
    #: Get all season details for show request (with all seasons translations), got simple episodes too.
    SHOW_SEASONS = 0x800_000

    SHOW = SHOW_LANG
    _GET_SHOW = SHOW_LANG | SHOW_EN | SHOW_ORIG

    # --- Request season details (sub-request) ---

    #: Do not get show details for episode (sub-request).
    NO_SEASON = 0
    #: Request season (with simple episodes) for API language (season sub-request). Simple episodes have no translations, will be in API language.
    SEASON_LANG = 0x001_000_000
    #: Request season (with simple episodes) for English (season sub-request). Simple episodes have no translations, will be in English.
    SEASON_EN = 0x002_000_000
    #: Request season (with simple episodes) for original language (season sub-request). Simple episodes have no translations, will be in original language.
    SEASON_ORIG = 0x004_000_000
    #: Request media main details like 'external_ids', etc. (season sub-request).
    SEASON_COMMON_DETAILS = 0x008_000_000
    #: Request media main details like 'release_dates', etc. (season sub-request).
    SEASON_MEDIA_DETAILS = 0x010_000_000
    #: Request media translations (season sub-request).
    SEASON_IMAGES = 0x020_000_000
    #: Request media images (season sub-request).
    SEASON_VIDEOS = 0x040_000_000
    #: Request media videos (season sub-request).
    SEASON_TRANSLATIONS = 0x080_000_000
    #: Request media aggregate credits (season sub-request). Only for TV shows and with DETAILS flag.
    SEASON_AGGREGATE_CREDITS = 0x100_000_000
    #: Request media main details like 'external_ids', 'release_dates', etc. (season sub-request).
    SEASON_BASE_DETAILS = SEASON_COMMON_DETAILS | SEASON_MEDIA_DETAILS
    #: Typical details for season sub-request.
    SEASON_DETAILS = SEASON_LANG | SEASON_BASE_DETAILS | SEASON_IMAGES | SEASON_VIDEOS | SEASON_TRANSLATIONS
    # #: Froce to get season details even if optimized request (season request). The season use SEASON_* flags.
    # SEASON_FORCE = 0x400_000_000
    #: Get all episodes details for season request (with all episodes translations). IGNORED, there is no more data than in simple episodes.
    SEASON_EPISODES = 0x800_000_000  # IGNORED

    SEASON = SEASON_LANG
    _GET_SEASON = SEASON_LANG | SEASON_EN | SEASON_ORIG

    # --- Request episode details (sub-request) ---

    EPISODE_LANG = 0x001_000_000_000
    EPISODE_EN = 0x002_000_000_000
    EPISODE_ORIG = 0x004_000_000_000

    #: Get only season details even for given episode (sic!) (episode request). The episode use SEASON_* flags.
    #: Used in `ffinfo` only, `tmdb` ignores it.
    EPISODE_SKIP = 0x800_000_000_000
    SKIP_EPISODES = EPISODE_SKIP


    # --- Global settings ---

    #: Do NOT optimize requests to get minimal data (seasons with show).
    NO_OPTIMIZE = 0x1_000_000_000_000
    SEPARATE_SEASONS = NO_OPTIMIZE
    #: Get degraded episodes details (with no translations, etc.) when getting show without seasons.
    DEGRADED_EPISODES = 0x2_000_000_000_000

    #: Flag is not defined. Do NOT use it on production code. Used in CLI tests.
    NOT_DEFINED = 0xfff_fff_fff_fff_fff

    #: Get minimal details by default.
    DEFAULT = DETAILS | SHOW_DETAILS | SEASON | SEASON_EPISODES | EPISODE_EN | SKIP_EPISODES

    #: Shift mask for locale/lang flags for show/season/episode sub-requests.
    __SHIFT: int
    #: Mask for locale/lang flags for show/season/episode sub-requests. 0x800 and 0x400 is not shifted.
    __MASK: int
    #: Regex to split string flags in new().
    __RX_SPLIT: re.Pattern[str]

    ITEM_MASK = 0x7ff
    SHOW_MASK = 0x7ff_000
    SEASON_MASK = 0x7ff_000_000
    EPISODE_MASK = 0x7ff_000_000_000
    # ITEM_MASK: InfoDetails
    # SHOW_MASK: InfoDetails
    # SEASON_MASK: InfoDetails
    # EPISODE_MASK: InfoDetails

    @classmethod
    def new(cls, val: InfoDetails | str | int | None, /, *, default: InfoDetails | int = DEFAULT) -> Self:
        """Return InfoDetails from diff string."""
        if not val:
            return cls(default)
        if isinstance(val, str):
            if val.startswith('InfoDetails.'):
                val = val[12:]  # remove 'InfoDetails.' prefix
            flag = cls.NONE
            for i, part in enumerate(cls.__RX_SPLIT.split(val.upper())):
                part = part.strip()
                if not part:
                    continue
                neg = False
                if part.startswith('+'):
                    if not i:  # first part, add to default
                        flag = cls(default)
                    part = part[1:].strip()
                elif part.startswith('-'):
                    part = part[1:].strip()
                    neg = True
                part = part.replace('-', '_')
                try:
                    f = cls[part.strip()]
                except KeyError:
                    try:
                        from lib.ff.log_utils import fflog
                        fflog.error(f'Unknown InfoDetails flag {part!r}')
                    except Exception:
                        pass
                    raise
                if neg:
                    flag &= ~f
                else:
                    flag |= f
            return flag
        return cls(val)

    def show(self, *, combine: bool = False, seasons: bool = False) -> InfoDetails:
        """Convert SHOW_* flags to INFO_* flags."""
        shift = self.__SHIFT
        flag = self.__class__((self.value >> shift) & self.__MASK)
        if combine:
            flag |= self
        if seasons and (self & self.SHOW_SEASONS):
            flag |= InfoDetails.SHOW_SEASONS
        return flag

    def season(self, *, combine: bool = False) -> InfoDetails:
        """Convert SEASON_* flags to INFO_* flags."""
        shift = self.__SHIFT * 2
        flag = self.__class__((self.value >> shift) & self.__MASK)
        if combine:
            flag |= self
        return flag

    def __to_json__(self) -> Any:
        return str(self)


# --- For Python < 3.10 no private attributes can be defined ---

#: Shift mask for locale/lang flags for show/season/episode sub-requests.
InfoDetails._InfoDetails__SHIFT = 12                          # type: ignore[reportAttributeAccessIssue]
#: Mask for locale/lang flags for show/season/episode sub-requests. 0x800 and 0x400 is not shifted.
InfoDetails._InfoDetails__MASK = 0x7ff                        # type: ignore[reportAttributeAccessIssue]
#: Regex to split string flags in new().
InfoDetails._InfoDetails__RX_SPLIT = re.compile(r'[,|]')      # type: ignore[reportAttributeAccessIssue]

# InfoDetails.ITEM_MASK = InfoDetails(InfoDetails._InfoDetails__MASK << (InfoDetails._InfoDetails__SHIFT * 0))     # type: ignore[reportAttributeAccessIssue]
# InfoDetails.SHOW_MASK = InfoDetails(InfoDetails._InfoDetails__MASK << (InfoDetails._InfoDetails__SHIFT * 1))     # type: ignore[reportAttributeAccessIssue]
# InfoDetails.SEASON_MASK = InfoDetails(InfoDetails._InfoDetails__MASK << (InfoDetails._InfoDetails__SHIFT * 2))   # type: ignore[reportAttributeAccessIssue]
# InfoDetails.EPISODE_MASK = InfoDetails(InfoDetails._InfoDetails__MASK << (InfoDetails._InfoDetails__SHIFT * 3))  # type: ignore[reportAttributeAccessIssue]


class PlayCancel(Enum):
    """How to cancel start video playing."""

    #: setResolvedUrl(success=False).
    FALSE = 'false'
    #: Use empty.m3u8.
    EMPTY = 'empty'
    #: Use long black video.
    TT430 = 'tt430'


class StateMode(Enum):
    """Where keep state varibables."""

    #: Use sqlite state.db.
    DB = 'db'
    #: Use local HTTP service.
    SERVICE = 'service'


class MediaWatchedMode(Enum):
    """(Un)watched detection mode."""

    #: Set FFID as fake DBID (Kodi database ID). NOT RECOMENDED.
    FAKE_DBID = 'dbid'
    #: Watch ListItem changes on folder refresh.
    WATCH_LISTITEM = 'listitem'


class StrmFilename(Enum):
    """STRM file name format."""

    # FF2 old file format. Example: My.Brilliant.Friend.S01E07.strm
    DOT = 'dot'
    # Another format. Example: My_Brilliant_Friend.S01E07.strm
    LOW_LINE = 'line'
    # A normal file name without the year. Example: My Brilliant Friend S01E07.strm
    TITLE = 'title'
    # A full file name with the year (FF3 default). Example: My Brilliant Friend (2018) S01E07.strm
    TITLE_YEAR = 'title_year'


#: Supported scrobble services.
ScrobbleService = Literal['trakt']

#: Supported list services.
ListService: TypeAlias = Literal['local', 'library', 'own', 'trakt', 'tmdb', 'mdblist', 'logs']
#: All possible list targets as single  pointer string.
ListPointer: TypeAlias = Literal['local',  # dummy target, no items, do NOT use it as pointer
                                 'library',
                                 'own:favorites', 'own:watchlist', 'own:collection', 'own:user',
                                 'trakt:favorites', 'trakt:watchlist', 'trakt:collection', 'trakt:user',
                                 'tmdb:favorites', 'tmdb:watchlist', 'tmdb:user',
                                 'mdblist:watchlist', 'mdblist:user',
                                 'logs']

#: Show or not items in context-menu.
#: False – shows never
#: True  – shows always
#: str   - depends on this settgins.eval()
ShowCxtMenu: TypeAlias = 'bool | str'
#: Supported services in CM "Add to...": `local` contains `library` and `own`.
AddToService: TypeAlias = ListService


@frozen
class ListPointerInfo:
    """Info about pointer to list (service and subset like favorites)."""
    #: The pointer string, like 'trakt:favorites'.
    pointer: ListPointer
    #: Group names for all list pointers. Diffrent groups are separated (horizontal line) in Add-to dialog.
    group: str = field(default='', kw_only=True)
    #: When the pointer (service & section) is enabled in Add-to dialog.
    enabled: ShowCxtMenu = field(default=True, kw_only=True)


@frozen
class AddToMenu:
    """Add-to context menu item definition."""

    name: str
    enabled: ShowCxtMenu = True
    service: AddToService | None = None
    list: str | None = None

    def target(self) -> ListTarget | None:
        """Return target list for this menu item."""
        if not self.service:
            return None
        return ListTarget.from_url(service=self.service, list=self.list)


class ListType(IntFlag):
    """Media list types (own and other lists)."""

    LIST = auto_enum()
    MOVIE = auto_enum()
    SHOW = auto_enum()
    SEASON = auto_enum()
    EPISODE = auto_enum()
    COLLECTION = auto_enum()
    PERSON = auto_enum()
    VIDEO = MOVIE | EPISODE
    MAIN = MOVIE | SHOW
    MEDIA = MOVIE | SHOW | SEASON | EPISODE  # | COLLECTION ?
    ALL_MEDIA = MEDIA | COLLECTION
    MIXED = MEDIA | COLLECTION | PERSON
    MOVIE_LIKE = MOVIE | COLLECTION
    SHOW_LIKE = SHOW | SEASON | EPISODE
    ALL = LIST | MOVIE | SHOW | SEASON | EPISODE | COLLECTION | PERSON
    NONE = 0

    def __str__(self) -> str:
        """Return string representation of this type."""
        return str(self.value)
        # if self == ListType.NONE:
        #     return 'none'
        # if self == ListType.ALL:
        #     return 'all'
        # return ', '.join(sorted(f.attr for f in self.iter_single_flags()))

    @property
    def attr(self) -> str:
        """Return attribute name for this type."""
        return (self.name or '').lower()

    def iter_single(self) -> Iterable[ListType]:
        """Return single-bit flags."""
        n = 1
        while n < self.ALL:
            if n & self:
                yield ListType(n)
            n *= 2

    @classmethod
    def iter_single_flags(cls) -> Iterable[ListType]:
        """Return single flags. Old Python iter(IntFlag) return all flags. New Python return single flags."""
        return cls.ALL.iter_single()

    @classmethod
    def new(cls, val: ListType | str | int | None, /, *, default: ListType | int = 0) -> Self:
        """Return OwnListType from diff string."""
        if not val:
            return cls(default)
        if isinstance(val, str):
            if val.isdecimal():
                return cls(int(val))
            from functools import reduce
            from operator import or_
            return reduce(or_, (cls[f.strip()] for f in val.upper().split(',')))
        return cls(val)

    @classmethod
    def from_media_ref(cls, val: MediaRef | RefType, /) -> Self:
        if not isinstance(val, str):
            val = val.type
        try:
            return cls[val.upper()]
        except KeyError:
            return cls(0)


@frozen(kw_only=True)
class ListTarget:
    """Target list for items."""

    service: ListService
    section: str = ''
    list: str = ''
    types: ListType = ListType.ALL

    @classmethod
    def from_pointer(cls, pointer: ListPointer, list: str | None = None, *, types: ListType = ListType.ALL) -> Self:
        """Create ListTarget from pointer and optional list name/id."""
        service: ListService
        service, _, section = pointer.partition(':')  # type: ignore[assignment]
        if list is None:
            section, _, list = section.partition(':')
        return cls(service=service, section=section, list=list or '', types=types)

    @classmethod
    def from_url(cls, service: ListService, list: str | None = None, *, types: ListType = ListType.ALL, default_section: str = 'user') -> Self:
        """Create ListTarget from URL variables."""
        from lib.ff.types import get_args
        from lib.ff.log_utils import fflog
        service, sep, section = service.partition(':')  # type: ignore[assignment]
        if not sep and list and list[0] == ':':
            section = list[1:]
            list = ''
        elif list is None:
            default_section = ''  # no section
        if service not in get_args(ListService):
            fflog.warning(f'Unknown service {service!r} in ListTarget.from_url()')
        return cls(service=service, section=section or default_section, list=list or '', types=types)

    @classmethod
    def from_ffitem(cls, item: 'FFItem') -> Self:
        """Create ListTarget from FFItem folder item."""
        service: ListService = item.getProperty('service')  # type: ignore
        section: str = item.getProperty('section')
        list_id: str = item.getProperty('list_id')
        types = ListType.new(item.getProperty('types'), default=ListType.ALL)
        name = item.getLabel()
        return cls(service=service, section=section, list=list_id or str(item.ffid or name), types=types)

    def pointer(self) -> ListPointer:
        """Return pointer string for this target."""
        if self.section:
            return f'{self.service}:{self.section}'  # type: ignore[return-value]
        return self.service  # type: ignore[return-value]


@frozen
class OwnListDef:
    """User own list (custom or built-in like favorites) definition."""

    # List name, should be unique.
    name: str
    #: List content type (type of items), "media" means mixed (movie & show).
    type: ListType = field(converter=ListType.new)
    #: List of item URL / path.
    url: str
    #: List icon (thumb).
    icon: str | None = field(default=None, kw_only=True)
    #: List icon (thumb).
    mixed: bool = field(default=True, kw_only=True)
    #: List label, if not set then name is used.
    label: str = field(default='', kw_only=True)
    #: Translations, name in locale (ex. "pl-PL") / language (ex. "pl").
    translations: Mapping[str, str] = field(factory=dict, kw_only=True)

    @property
    def title(self) -> str:
        """Return list title."""
        return self.label or self.name

    def own_name(self) -> str | None:
        """Return own name for this list."""
        if self.url.startswith('db://own/'):
            return self.url[9:]  # cut 'db://own/' prefix
        return None

    @staticmethod
    def type_match(type: ListType, media: ListType | str | None) -> bool:
        """Return True if list should be visible for given media type."""
        media = ListType.new(media or ListType.MEDIA)
        # if type == 'list':
        #     # list of lists are always avaliable
        #     return True
        # if media == 'list':
        #     # Only list of lists are allowed but this is not.
        #     return False
        # true if any common type
        return bool(type & media)

    def match(self, media: ListType | str | None) -> bool:
        """Return True if list should be visible for given media type."""
        return self.type_match(self.type, media)


class WhenShowListName(Enum):
    """When to show list name as role. Used in "my lists" top level."""

    #: Never show list name.
    NEVER = 'never'
    #: Show name if many lists (more then one).
    IF_MANY = 'if_many'
    #: Always show list name.
    ALWAYS = 'always'


class SourceSearchProgressStyle(Enum):
    """How to show progress of source search."""

    #: Simple progress dialog. Mixed sources (no premium/normal distinction).
    SIMPLE = 0
    #: Extended progress dialog with more info. Premium sources in parentheses.
    EXTENDED = 1
    #: Full progress dialog with all info. Separate lines for premium and normal sources.
    FULL = 2


#: How to play the video.
PlayMode = Literal['auto', 'direct', 'isa']
#: Actions for context-menu in sources window. Must include PlayMode.
#: Action 'play' means auto-action (direct or isa).
SourceAction = Literal['direct', 'isa', 'play', 'buy']


def is_play_mode(obj: object) -> TypeIs[PlayMode]:
    """Type guard for PlayMode."""
    return obj in get_typing_args(PlayMode)


def _make_meta(meta: SourceItemPattern | None) -> SourceItemPattern | None:
    """Make meta pattern from dict."""
    if meta is None:
        return None
    return frozendict(meta)  # type: ignore[return-value]


@frozen(kw_only=True)
class ProviderPattern:
    """Match the provider. Empty means doesn't matter."""

    #: Video provider name (scraper module).
    provider: str = ''
    #: Kodi / system platform name, like "windows", "linux", "android", etc.
    platform: str | Collection[str] | None = None
    #: Kodi minimal version (if int) or Kodi version range/set.
    #: Example (21,22) or the same as range(21,23).
    #: Remember: range() uses start, stop not min, max.
    kodi: int | Collection[int] | RegEx | Expr | None = None

    def has_provider_condition(self) -> bool:
        """Return True if any provider condition is set."""
        return bool(self.provider or self.platform or self.kodi)


@frozen(kw_only=True)
class SourcePattern(ProviderPattern):
    """Match the source (and its provider etc.) in the rule. Empty means doesn't matter."""

    #: Video hosting, "source" from sources item list.
    hosting: str = ''  # "source" from sources item list
    #: Setting name (bool) or setting condition.
    setting: str | None = None
    #: Is source m3u8.
    m3u8: bool | None = None
    #: Only premium sources (taken from meta['premium']).
    premium: bool | None = None
    #: Meta (provider source data). For keys see SourceItemPattern (lib/sources/__init__.py).
    meta: SourceItemPattern | None = field(default=None, converter=_make_meta)
    #: Source size (in bytes) parsed from meta['size']. Approximated if meta['size'] is a string like "1 GB".
    size: int | RegEx | Expr | None = None
    #: Real media type (movie, show, season, episode) for this source.
    media: RefType | None = None

    def has_source_condition(self) -> bool:
        """Return True if any source condition is set."""
        return bool(self.hosting or self.m3u8 is not None or self.setting or self.premium is not None or self.meta or self.size is not None)


@frozen(kw_only=True)
class SourceAttribute:
    """Source attribute (what to set in this source) for matched rule."""

    #: Override source color in sources dialog.
    #: Color is in AARRGGBB format (alpha could not be omitted).
    color: str | None = None
    #: Override source order in sources dialog.
    #: By default is from settings or zero if there is no setting.
    #: Bigger value are more important:
    #: +2001..+2006:  download, local, plex, jellyfin, external
    #: +1000:         account (already bought, in library, etc.)
    #: 0..999:        providers
    order: int | None = None
    #: Play mode.
    play: PlayMode | None = None
    #: Context menu.
    menu: Sequence[SourceAction] | None = None
    #: Override source item meta dict.
    meta: SourceItemPattern | None = None
    #: Stop processing next rules if this rule is applied. Use it to set final attributes for source.
    final: bool = False


#: Value of const.sources.rules: attribute or False if source item should be ignored (not shown in sources dialog).
SourceRuleValue: TypeAlias = 'SourceAttribute | Literal[False]'


@frozen
class NetCache:
    """NetCache settings."""

    #: Expiration time of cache in seconds. settings.eval() will be used if str.
    expire: int | str
    #: Cache size limit in bytes (`filesystem` backend only). settings.eval() will be used if str.
    size_limit: int | str = 0
    #: Net-cache busy timeout in seconds ('sqlite' backend only).
    busy_timeout = 1.0
    #: Net-cache sqlite3 WAL mode ('sqlite' backend only).
    wal = True
    #: Delete expired items on cleanup.
    cleanup: bool = True

    def size_limit_in_bytes(self) -> int:
        if isinstance(self.size_limit, str):
            from lib.ff.settings import settings
            return settings.eval(self.size_limit)
        return self.size_limit


#: Names of supported ratings services.
RatingServiceName = Literal['tmdb', 'trakt', 'mdblist']
#: Names of rate actions.
RatingAction = Literal['context_menu', 'watched_movie', 'watched_episode', 'get']


# ----------------------------------------------------------------------------- #
# -----                          CONST DEFINITION                         ----- #
# ----------------------------------------------------------------------------- #


@constdef
class const:
    """Const (low-level developper) settings."""

    @constdef
    class debug:
        enabled: bool
        tty: bool
        crash_menu: bool
        dev_menu: bool
        autoreload: bool
        service_notifications: bool
        log_xsleep_jitter: float
        log_folders: bool
        add_to_logs: bool
        log_exception: bool
        log_gui: bool

    @constdef
    class debugger:
        enabled: bool

    @constdef
    class dev:

        @constdef
        class imdb:
            api_page_size: int

        @constdef
        class tmdb:
            api_key: str | None
            session_id: str | None

            @constdef
            class v4:
                bearer: str | None
                access_token: str | None

        @constdef
        class trakt:
            client: str | None
            secret: str | None

        @constdef
        class mdblist:
            api_key: str | None

        @constdef
        class db:
            echo: bool
            backup: bool

        @constdef
        class sources:
            prepend_fake_items: Sequence[SourceXMeta]
            append_fake_items: Sequence[SourceXMeta]
            log_exception: bool
            force_all_sources: bool

        @constdef
        class kodidb:
            ver: str | None

    @constdef
    class global_defs:
        country_language: dict[str, str]

    @constdef
    class core:
        gc_every_nth: int
        exit_every_nth: int
        widgets_exit_every_nth: int
        volatile_ffid: bool
        volatile_seasons: bool
        media_watched_mode: MediaWatchedMode

        @constdef
        class bypass:
            info_details: InfoDetails | str | None

        @constdef
        class netcache:
            backend: Literal[False, 'memory', 'sqlite', 'filesystem', 'redis']
            serializer: tuple[Literal['json', 'pickle'], Literal['', 'zlib', 'gzip', 'bzip2', 'lzma']]
            cache: dict[NetCacheName, NetCache]
            widgets: bool

            @constdef
            class cleanup:
                interval: int
                expire_factor: float
                expire_offset: int | timedelta

            @constdef
            class redis:
                host: str
                port: int
                ttl: bool
                ttl_offset: int

        @constdef
        class kodidb:
            advanced_settings: bool

        @constdef
        class info:
            save_cache: bool
            copy_year: bool

    @constdef
    class media:
        aliases_service: Literal['tmdb', 'trakt']
        info_details: InfoDetails | str | None

        @constdef
        class progress:
            as_watched: int
            info_details: InfoDetails | str | None

            @constdef
            class show:
                episodes_watched: bool

    @constdef
    class sources_dialog:
        index_color: str
        show_empty: bool
        rescan_edit: bool
        time_progress: bool
        language_type_priority: dict[str, int]
        disabled_hosts: set[str]
        cda_drm: bool
        library_cache: set[str]
        movie_title_format: str
        episode_title_format: str
        episode_group_title_format: str
        episode_poster: Literal['episode', 'season', 'show']

        @constdef
        class searching:
            progress_style: SourceSearchProgressStyle

        @constdef
        class edit_search:
            in_menu: bool
            in_dialog: bool
            in_filters: bool
            cache: set[str] | None
            show_granularity: Literal['show', 'season', 'episode']

    # ----- Folders -----
    @constdef
    class folder:
        cache_to_disc: bool
        lock_wait_timeout: float
        refresh_delay: float
        max_scan_step_interval: float
        db_save: bool
        script_autorefresh: bool
        previous_page: Literal['never', 'always', 'on_last_page']
        max_page_jump: int
        fanart_fallback: Literal['landscape', 'poster', 'thumb', 'banner', 'clearlogo'] | None
        category: int
        category_by_skin: dict[str, int]
        separator_label: str

        @constdef
        class style:
            future: str | None
            role: str | None
            broken: str | None
            section_label: str | None
            top: str | None
            bottom: str | None
            separator: str | None

    # ----- Indexes -----
    @constdef
    class indexer:
        region: str
        lists_view: ContentView
        empty_folder_message: bool | str
        page_size: int
        add_to_limit: int
        trending_scan_limit: int
        default_alone_view: dict[RefType | Literal['MIXED'], ContentView]
        default_mixed_view: ContentView

        @constdef
        class art:
            fake_thumb: bool

        @constdef
        class progressbar:
            style: str
            mode: Literal['none', 'watching', 'watched', 'percent', 'percent_and_watched']
            width: int

            @constdef
            class fill:
                color: str
                char: str

            @constdef
            class partial:
                color: str
                char: str

            @constdef
            class empty:
                color: str
                char: str

            @constdef
            class watched:
                color: str
                char: str

        @constdef
        class no_content:
            show_item: bool
            notification: bool

        @constdef
        class search:
            view: ContentView
            clear_if_sure: bool
            year_dialog: Literal['never', 'context-menu', 'entry', 'always']
            year_pattern: str
            query_option_format: str
            multi_search: bool
            limit: int

        @constdef
        class context_menu:
            add_to: Sequence[AddToMenu]

        # -- Directories: main menu
        @constdef
        class navigator:
            lists_folder: bool

        # -- Directories: movies
        @constdef
        class movies:
            region: str
            discover_sort_by: Sequence[Literal['original_title.asc', 'original_title.desc',  # list in setting `movies.sort` order
                                               'popularity.asc', 'popularity.desc',
                                               'revenue.asc', 'revenue.desc',
                                               'primary_release_date.asc', 'primary_release_date.desc',
                                               'title.asc', 'title.desc',
                                               'vote_average.asc', 'vote_average.desc',
                                               'vote_count.asc', 'vote_count.desc']]
            missing_duration: int
            future_if_no_date: bool
            date_from_year: bool
            future_playable: bool
            discovery_scan_limit: int

            @constdef
            class resume:
                watched_date_format: str | None

            @constdef
            class top_rated:
                votes: int | None

            @constdef
            class genre:
                votes: int | None
                # menu: set[int]

            @constdef
            class year:
                votes: int | None

            @constdef
            class joke:
                production_company: bool
                keyword: bool

            @constdef
            class trending:
                service: Literal['tmdb', 'trakt']

            @constdef
            class cinema:
                last_days: int
                next_days: int
                use_region: bool

            @constdef
            class tv:
                media_page_size: int
                stub_page_size: int
                service: ProgramTvService | Sequence[ProgramTvService]
                list_mode: Literal['media', 'mixed', 'folders', 'direct', 'direct_or_folder']
                service_view: ContentView
                sort_by: Literal['', 'aired_date', 'title']
                day_range: range
                channels: Sequence[str] | None
                day_first_hour: int

                @constdef
                class filmweb:
                    show_non_id: bool
                    skip_items: Collection[str] | None

                @constdef
                class onet:
                    channel_page_count: int
                    channel_worker_count: int | None
                    more_details: bool

                @constdef
                class teleman:
                    pages_to_scan: int

            @constdef
            class new:
                votes: int | None

            @constdef
            class new_vod:
                days: int
                services: Collection[str]

            @constdef
            class progressbar:
                style: str
                mode: Literal['none', 'watching', 'watched', 'percent', 'percent_and_watched']
                width: int

            @constdef
            class my_lists:
                enabled: bool

                @constdef
                class root:
                    flat: bool

            @constdef
            class search:
                year_pattern: str

        # -- Directories: tv-shows
        @constdef
        class tvshows:
            region: str
            discover_sort_by: Sequence[Literal['first_air_date.asc', 'first_air_date.desc',  # list in setting `tvshows.sort` order
                                               'name.asc', 'name.desc',
                                               'original_name.asc', 'original_name.desc',
                                               'popularity.asc', 'popularity.desc',
                                               'vote_average.asc', 'vote_average.desc',
                                               'vote_count.asc', 'vote_count.desc']]
            future_if_no_date: bool
            calendar_range: tuple[int, int]
            discovery_scan_limit: int
            season_details: InfoDetails | str | None
            last_show_progress_folder: int

            @constdef
            class top_rated:
                votes: int | None

            @constdef
            class genre:
                votes: int | None
                # menu: set[int]

            @constdef
            class year:
                votes: int | None

            @constdef
            class joke:
                production_company: bool

            @constdef
            class trending:
                service: Literal['tmdb', 'trakt']

            @constdef
            class premiere:
                votes: int | None

            @constdef
            class new_vod:
                days: int
                services: Collection[str]

            @constdef
            class progress:
                show: Literal['show', 'season', 'episode']
                next_policy: Literal['last', 'continued', 'first', 'newest']
                episode_folder: bool
                episode_focus: bool
                episode_select: bool
                episode_label_style: str | None
                show_full_watched: bool

            @constdef
            class my_lists:
                enabled: bool

                @constdef
                class root:
                    flat: bool

            @constdef
            class search:
                year_pattern: str

        # -- Directories: seasons
        @constdef
        class seasons:
            no_title_labels: Sequence[str]
            with_title_labels: Sequence[str]
            alone_label: str
            override_title_by_label: bool
            future_if_no_date: bool

        # -- Directories: episodes
        @constdef
        class episodes:
            missing_duration: int
            progress_if_aired: bool
            future_if_no_date: bool
            date_from_year: bool
            future_playable: bool
            continuing_numbers: bool
            label: str
            alone_label: str

            @constdef
            class resume:
                watched_date_format: str | None

            @constdef
            class progressbar:
                style: str
                mode: Literal['none', 'watching', 'watched', 'percent', 'percent_and_watched']
                width: int

        # -- Directories: anime
        @constdef
        class anime:
            region: str | None

            @constdef
            class aired:
                last_days: int

        # -- Directories: persons
        @constdef
        class persons:
            enabled: bool
            discovery_scan_limit: int

            @constdef
            class show:
                include_self: bool

            @constdef
            class my_lists:
                enabled: bool
                flat: bool

                @constdef
                class root:
                    flat: bool

        # -- Directories: details
        @constdef
        class details:
            info_details: InfoDetails | str | None
            show_episode_groups: bool

            @constdef
            class videos:
                view: ContentView

            @constdef
            class credits:
                info_details: InfoDetails | str | None

        # -- Directories: stump indexers (keywords, companies)
        @constdef
        class stump:
            votes: int | None

            @constdef
            class keywords:
                search_n_keywords: Sequence[int]
                select: bool

        # -- Directories: trakt lists
        @constdef
        class trakt:
            show_sync_entry: bool

            @constdef
            class collection:
                mixed: bool

            @constdef
            class recommendation:
                mixed: bool

            @constdef
            class mine:
                view: ContentView

            @constdef
            class lists:
                watched_date_format: str | None
                info_details: InfoDetails | str | None

            @constdef
            class progress:
                bar: bool
                page_size: int
                movies_page_size: int
                episodes_page_size: int
                shows_page_size: int
                shows_page_size_exact_match: bool

            @constdef
            class sort:
                default: TraktSortType  # obserwowane
                watchlist: TraktSortType  # obserwowane
                collections: TraktSortType  # kolekcje
                reverse_order: dict[TraktSortBy, bool]

        # -- Directories: tmdb lists
        @constdef
        class tmdb:

            @constdef
            class root:
                flat: bool

            @constdef
            class favorites:
                mixed: bool

            @constdef
            class watchlist:
                mixed: bool

            @constdef
            class mine:
                view: ContentView
                align_list_pages: bool

        # -- Directories: imdb lists
        @constdef
        class imdb:
            page_size: int

            @constdef
            class watchlist:
                mixed: bool

            @constdef
            class mine:
                view: ContentView

        # -- Directories: MDBList lists
        @constdef
        class mdblist:
            enabled: bool
            page_size: int

            @constdef
            class root:
                flat: bool

            @constdef
            class mine:
                view: ContentView

            @constdef
            class top:
                view: ContentView

        # -- Directories: justwatch (vod) lists
        @constdef
        class justwatch:
            enabled: bool

            @constdef
            class new_vod:
                days: int
                services: Collection[str]

        # -- Directories: url lists
        @constdef
        class own:
            enabled: bool
            page_size: int
            flat: bool
            generic: Sequence[OwnListDef]
            lists: Sequence[OwnListDef]

            @constdef
            class root:
                flat: bool

            # @constdef
            # class favorites:
            #     enabled: bool
            #     mixed: bool
            #     list: OwnListDef

            # @constdef
            # class watchlist:
            #     enabled: bool
            #     mixed: bool
            #     list: OwnListDef

            @constdef
            class mine:
                view: ContentView
                show_list_name: WhenShowListName
                flat_default: bool
                root_direct_role: str

        # -- Directories: history
        @constdef
        class history:
            show_watching: bool
            role: str
            limit: int

        # -- Directories: tools
        @constdef
        class tools:
            view: ContentView

    @constdef
    class indexer_group:

        # -- Genre menu
        @constdef
        class genres:
            translations: dict[str, dict[CodeId, str]]
            icons_set: Literal['fanfilm', 'kodi']
            icons: dict[Literal['fanfilm', 'kodi'], dict[CodeId, str]]

        # -- Language menu
        @constdef
        class languages:
            default: set[CodeId]
            top: set[CodeId]
            groups: tuple[DirItemSource, ...]
            votes: int | None

        # -- Country menu
        @constdef
        class countries:
            default: set[CodeId]
            top: set[CodeId]
            groups: tuple[DirItemSource, ...]
            votes: int | None

    # ----- tmdb.org settings -----
    @constdef
    class tmdb:
        append_to_response_limit: int
        # image_languages: Sequence[str]
        get_image_mode: Literal['append', 'append_en', 'append_lang', 'pull', 'full', 'all']
        language_aliases: Sequence[Sequence[str]]
        skel_max_threads: int
        avoid_keywords: Sequence[int]

        @constdef
        class auth:
            api: Literal[3, 4, None]
            dialog_interval: int
            dialog_expire: int
            qrcode_size: int

        @constdef
        class connection:
            try_count: int
            try_delay: float
            timeout: float
            common_session: bool
            max: int

    # ----- trakt.tv settings -----
    @constdef
    class trakt:
        recommendations_limit: int

        @constdef
        class page:
            limit: int

        @constdef
        class scan:
            @constdef
            class page:
                limit: int

        @constdef
        class sync:
            startup_interval: int
            interval: int
            cooldown: int
            notification_delay: int
            wait_for_service_timeout: float

            @constdef
            class alien:
                default: bool
                scheme: dict[str, bool]
                plugins: dict[str, bool]

        @constdef
        class auth:
            auto: bool

            @constdef
            class qrcode:
                size: int

        @constdef
        class connection:
            try_count: int
            try_delay: float
            timeout: float

    # ----- mdblist.com settings -----
    @constdef
    class mdblist:

        @constdef
        class connection:
            try_count: int
            try_delay: float
            timeout: float

    # ----- justwatch.com settings -----
    @constdef
    class justwatch:
        episode_max_limit: int

    # ----- sources settings / tune -----
    @constdef
    class sources:
        duration_epsilon: float
        check_anime: str
        exclude_keywords: Sequence[str]
        include_languages: set[str] | None
        rules: dict[SourcePattern, SourceRuleValue]
        franchise_names: dict[str, Sequence[str]]
        franchise_names_sep: str
        language_order: Sequence[str]
        info_details: InfoDetails | str | None

        @constdef
        class translations:
            providers: dict[str, dict[str, str]]
            hostings: dict[str, dict[str, str]]



        @constdef
        class cda:
            duration_epsilon: float
            episode_duration_epsilon: float
            show_premium_free: str
            debug: bool
            min_size_mb: int

        @constdef
        class ekinotv:
            domain: str

        @constdef
        class giptv:
            index_max_age_h: int

        @constdef
        class xtb7:
            dot_before_season: bool
            space_before_season: bool
            max_show_search_queries: int
            similarity_check: bool
            similarity_threshold: float
            title_replacements: dict[str, str]
            debug: bool
            parallel_sessions: bool

        @constdef
        class premium:
            no_transfer: bool

        @constdef
        class rapideo:  # rapideo/nopremium/twojlimit
            debug: bool

        @constdef
        class wrzucaj:
            debug: bool

        @constdef
        class netmirror:
            debug: bool

    # ----- Player -----
    @constdef
    class player:
        set_dbid: bool
        cancel_start: PlayCancel
        cancel_resume: PlayCancel
        refresh_on_cancel_resume: bool
        head_lookup: bool

    # ----- Library -----
    @constdef
    class library:
        flat_show_folder: bool
        strm_filename: StrmFilename
        title_language: str
        info_details: InfoDetails | str | None
        safe_filename: bool

        @constdef
        class service:
            sync_every_batch: bool
            sync_wait: bool
            chunk_size: int
            chunk_sleep: float

    # ----- Dialogues -----
    @constdef
    class dialog:

        @constdef
        class auth:
            link_color: str
            code_color: str

        @constdef
        class add_to:
            allowed_conversions: dict[RefType, ListType]
            quiet: bool

            @constdef
            class media_type:
                absent_visible: ListType
                absent_color: str
                exist_color: str
                converting_color: str
                disallowed_color: str
                bar_mode: AddToDialogMediaBarMode

            # @constdef
            # class list_ui:
            #     color: str
            #     border: str
            #     selected_color: str
            #     selected_border: str

            @constdef
            class lists:
                default: dict[str, Sequence[ListPointer]]
                services: dict[ListService, dict[str, Sequence[ListPointer]]]

        @constdef
        class ratings:
            style: Literal['triple', 'lines', 'stars']
            show_raw_rating: bool
            notification: bool
            colors: Sequence[str]
            autoclose_timeout: int

    @constdef
    class ratings:
        enabled: bool | Sequence[RatingServiceName]
        every_watched: bool
        watched_show_mode: Sequence[Literal['show', 'season', 'episode', 'show_finished', 'season_finished']]
        filter: dict[tuple[RatingServiceName | Literal['*'], RatingAction | Literal['*']], bool]
        all_folders: Literal[False, 'min', 'max', 'avg', 'first'] | RatingServiceName

    # ----- Low level settings (don't change, you are warnend) -----
    @constdef
    class tune:
        sleep_step: float
        event_step: float
        depagine_max_workers: int | None

        @constdef
        class db:
            connection_timeout: float
            state_wait_read_interval: float
            state_mode: StateMode
            module_state_mode: dict[str, StateMode]

        @constdef
        class settings:
            vacuum_files: int

        # @constdef
        # class indexer:

        @constdef
        class service:
            check_interval: float
            job_list_sleep: int
            group_update_timeout: float
            group_update_busy_dialog: bool
            focus_item_delay: float
            startup_delay: float
            startup_timeout: float
            rpc_call_timeout: float

            @constdef
            class http_server:
                port: int
                try_count: int
                wait_for_url: float
                verbose: int

            @constdef
            class web_server:
                port: int
                verbose: int
                cookies: dict[str | re.Pattern[str], dict[str, str]]
                update_notification: bool

        @constdef
        class misc:

            @constdef
            class qrcode:
                size: int

        @constdef
        class gui:
            xml_output_filename: str
            set_xml_value_lag: float


# ----------------------------------------------------------------------------- #
# -----                              THE END                              ----- #
# ----------------------------------------------------------------------------- #


if TYPE_CHECKING:
    ConstRefBase = type(const)
else:
    ConstRefBase = object


class ConstRef(ConstRefBase):
    """Dynamic const reference."""

    def __init__(self, obj: object, *keys: str) -> None:
        self._obj = obj
        self._keys = keys

    if not TYPE_CHECKING:
        def __getattr__(self, key: str) -> Any:
            if key.startswith('_'):
                raise AttributeError(key)
            return ConstRef(self._obj, *self._keys, key)

    def __call__(self) -> Any:
        obj = self._obj
        for key in self._keys:
            obj = getattr(obj, key)
        return obj

    def __repr__(self) -> str:
        return f'CONST_REF({".".join(self._keys)})'


CONST_REF = ConstRef(const)
_loading: bool = False


class SettingsDescriptor(Generic[T]):
    """Helper to mark setting to be evaluated."""

    def __init__(self, expr: str | bool | None) -> None:
        self.expr = expr

    def __repr__(self) -> str:
        return f'SETTING({self.expr!r})'

    @overload
    def __get__(self: Self, instance: None, objtype: type[object] | None = None) -> Self: ...

    @overload
    def __get__(self: Self, instance: object, objtype: type[object] | None = None) -> T: ...

    def __get__(self: Self, instance: object | None, objtype: type[object] | None = None) -> Self | T:
        """Get descriptor value or instance."""
        # return descriptor
        if instance is None:
            return self
        if self.expr is None or self.expr is False or self.expr is True:
            return self.expr  # type: ignore[return-value]
        from lib.ff.settings import settings
        return settings.eval(self.expr)


def __getattr__(key: str) -> Any:
    """Return const settings directly as module value."""
    if key and key[0].islower():
        return getattr(const, key)
    raise AttributeError(f'Module {__name__} has o attribute {key!r}')


# dummy function
def setup_debugger():
    """Configure remote debugger. Override this in local.py."""
    from lib.ff.log_utils import fflog
    fflog('setup_debugger() is missing in local.py. Your debugger is not set.')


def run_debugger():
    """Run remote debugger connection."""
    if const.debugger.enabled:
        from lib.ff.log_utils import fflog_exc
        try:
            setup_debugger()
        except Exception:
            fflog_exc()


def _data_path() -> Path:
    """Returns plugin data folder path."""
    from xbmcaddon import Addon
    from xbmcvfs import translatePath
    return Path(translatePath(Addon().getAddonInfo('profile')))


def _check_const_assignment(const_obj: Any, *, obj: Any = None, keys: Sequence[str] = ()):
    """Check if all defined `class const` settings are assigned."""
    if obj is None:
        const_obj._refs = {}
        obj = const_obj
    cls = type(obj)
    # attributrs
    elements = {k: v for k, v in vars(obj).items() if not k.startswith('_')}
    # desctriptors
    elements.update((k, v) for k, v in vars(cls).items() if not k.startswith('_') and hasattr(v, '__get__'))
    # check annotations
    if ann := getattr(cls, '__annotations__', None):
        if missing := ann.keys() - elements.keys():
            sets = ', '.join(sorted(missing))
            raise TypeError(f'{cls.__qualname__}: missing settings: {sets}')
    # for sub in elements.values():
    for key, sub in elements.items():
        if isinstance(sub, ConstRef):
            const_obj._refs[(*keys, key)] = sub
        if isinstance(sub, (tuple, list, set)) and any(isinstance(e, ConstRef) for e in sub):
            const_obj._refs[(*keys, key)] = sub
        if getattr(sub, '_const_def', None):
            _check_const_assignment(const_obj=const_obj, obj=sub, keys=(*keys, key))


def _resolve_const_assignment(obj):
    """Check if all defined `class const` settings are assigned."""
    def xset(x: Any, keys: Sequence[str], value: Any) -> None:
        for key in keys[:-1]:
            x = getattr(x, key)
        setattr(x, keys[-1], value)

    def get(ref: ConstRef) -> ConstRef:
        visited: set[str] = {keys, ref._keys}
        while ref2 := refs.get(ref._keys):
            ref = ref2
            if ref._keys in visited:
                cycle = ', '.join('.'.join(('const', *keys)) for keys in visited)
                raise ValueError(f'Cycle const refernece: {cycle}')
            visited.add(ref._keys)
        return ref

    refs = getattr(obj, '_refs', {})
    for keys, ref in refs.items():
        if type(ref) is ConstRef:
            # ref
            xset(obj, keys, get(ref)())
        else:
            # iterable of ref
            xset(obj, keys, type(ref)(get(e)() if type(e) is ConstRef else e for e in ref))


def const_done():

    global _loading
    if _loading or getattr(constdef, '_locked', False):
        return

    _check_const_assignment(const)  # pure const (without local) to check if all settings are set

    # --- must be on the bottom of the file

    _local_path: Path = _data_path() / 'local.py'
    if not _local_path.exists():
        try:
            _local_path.write_text(('"""Local const settings."""\n\nfrom cdefs import const, CONST_REF\n\n\n'
                                    '# --- Your custom settings\n\n# const.NAME = VALUE\n'), encoding='utf-8')
        except IOError as exc:
            from lib.ff.log_utils import fflog
            fflog(f'Creating local file {_local_path} failed: {exc}')
    if _local_path.is_file():
        _loading = True
        try:
            context = {k: v for k, v in globals().items() if not k.startswith('__')}
            globals().update((k, v)
                             for k, v in runpy.run_path(str(_local_path), init_globals=context).items()
                             if not k.startswith('__'))
        except IOError as exc:
            from lib.ff.log_utils import fflog
            fflog(f'Reading local file {_local_path} failed: {exc}')
        finally:
            _loading = False

    # resolve references
    _check_const_assignment(const)  # re-check after loading local settings, to handle CONT_REF removing
    _resolve_const_assignment(const)
    # end of const definitions
    constdef._locked = True  # type: ignore
