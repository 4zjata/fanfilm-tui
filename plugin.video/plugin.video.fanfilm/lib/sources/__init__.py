"""
    FanFilm Add-on
    Copyright (C) 2017 FanFilm

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
import re
from pathlib import Path
from pkgutil import walk_packages
from typing import Union, Any, Sequence, Iterator, Iterable, Callable, ClassVar, overload, TYPE_CHECKING
from typing_extensions import Literal, Protocol, TypedDict, NotRequired, Unpack, ParamSpec, TypeVar, Self
from functools import wraps
from attrs import frozen, define, field, evolve, asdict, fields, filters
from ..ff.log_utils import log, fflog_exc, log_submodule
from ..ff.debug.timing import logtime  # TODO: remove, only for testing
from cdefs import SourceAttribute, RegEx, Expr
from const import const
if TYPE_CHECKING:
    from typing_extensions import KeysView, Pattern, Mapping, Collection
    from types import ModuleType
    from attrs import Attribute
    from cdefs import ProviderPattern, SourcePattern, SourceRuleValue, PlayMode, SourceAction
    from ..ff.item import FFItem
    from ..ff.types import JsonData


T = TypeVar('T')
R = TypeVar('R')
P = ParamSpec('P')


# Shared optional data fields.
# Optional in both the provider result and the source item.
class SourceOptionalData(TypedDict):
    size: NotRequired[str]
    filename: NotRequired[str]
    color_identify: NotRequired[str]
    icon: NotRequired[str]
    debridonly: NotRequired[bool]
    direct: NotRequired[bool]
    # True, if file is on local (or locally mounted) storage. Set by provider.
    local: NotRequired[bool]
    premium: NotRequired[bool]
    on_account: NotRequired[bool]
    on_account_link: NotRequired[str]
    on_account_expires: NotRequired[str]
    no_transfer: NotRequired[bool]
    external: NotRequired[bool]
    # --- DEBUG ---
    fake: NotRequired[bool]
    resolve_to: NotRequired[Union[None, str, Path]]


# Type descriptor for a position element reported by the provider (scraper) — effectively a dict.
# This is the dict returned by providers.
class SourceItem(SourceOptionalData):
    # source is de fact hosting name
    source: NotRequired[str]  # TODO: rename it to "hosting"
    info: NotRequired[str]
    info2: NotRequired[str]
    url: str
    language: NotRequired[str]
    language_list: NotRequired[Sequence[str]]
    quality: NotRequired[str]
    debrid: NotRequired[str]


if TYPE_CHECKING:
    class SourceItemPattern(SourceOptionalData):
        """SourceItem but all not required, for matching source meta with pattern in SourcePattern.meta."""
        info: NotRequired[str]
        info2: NotRequired[str]
        url: NotRequired[str]
        language: NotRequired[str]
        quality: NotRequired[str]
        debrid: NotRequired[str]


# Type descriptor for a position element — effectively a dict.
# To są metadane źródła już po obróbce i teki lecą do okna źródeł. Dokładnie to co w source.meta.
class SourceMeta(SourceOptionalData):
    label: str
    info: str
    info2: str
    language: str
    quality: str
    debrid: str


# Opis typu do SZTUCZNEGO tworzenia z meta.
class SourceXMeta(SourceMeta):
    provider: str
    hosting: str  # old "source" from providers' source items
    url: str


# Extra arguments that can come from the sources window as `for_resolve`.
class SourceResolveKwargs(TypedDict):
    buy_anyway: NotRequired[bool]


# Because Python is still missing Introsection or KeyType we haveto copy all SourceMeta keys explicite.
SourceMetaRequiredKey = Literal['provider', 'hosting', 'label', 'info', 'url', 'language', 'quality', 'debrid']
SourceMetaOptionalStrKey = Literal['size', 'filename', 'color_identify', 'icon', 'info2', 'on_account_link', 'on_account_expires', 'resolve_to', 'no_transfer']
SourceMetaOptionalBoolKey = Literal['debridonly', 'direct', 'local', 'premium', 'on_account', 'fake', 'external']
SourceMetaOptionalKey = Union[SourceMetaOptionalStrKey, SourceMetaOptionalBoolKey]
SourceMetaKey = Union[SourceMetaRequiredKey, SourceMetaOptionalKey]


def _make_source_meta(meta: SourceMeta | None = None) -> SourceMeta:
    """Make SourceMeta with default values for missing keys."""
    required = SourceMeta.__annotations__.keys() - SourceOptionalData.__annotations__.keys()
    if meta is None:
        return {k: '' for k in required}  # type: ignore
    return {**{k: '' for k in required}, **meta}  # type: ignore


@define(kw_only=True)
class Source:
    url: str
    provider: str
    hosting: str
    ffitem: FFItem
    attr: SourceAttribute = field(factory=SourceAttribute)
    meta: SourceMeta = field(factory=_make_source_meta, converter=_make_source_meta)
    resolved: bool = False
    _size: int | None = field(init=False, repr=False, default=None)

    RX_M38U: ClassVar[Pattern[str]] = re.compile(r'^https?://.*\.m3u8\b')
    RX_NOT_M38U: ClassVar[Pattern[str]] = re.compile(r'\.(?:avi|mkv|mp4|ts|mpg)\b')
    RX_UNIT: ClassVar[Pattern[str]] = re.compile(r'([\d,.]+)([KMGT])B')

    def match(self, pat: SourcePattern) -> bool:
        """Check if source match given pattern."""
        def matched(val: int | str, pat: int | str | RegEx | Expr | Collection[int] | Collection[str] | None) -> bool:
            if pat is None:
                return True
            if isinstance(pat, (str, int, float, RegEx, Expr)):
                return val == pat
            if hasattr(pat, '__contains__'):
                return val in pat
            return False

        if pat.provider and not matched(self.provider, pat.provider):
            return False
        if pat.hosting and not matched(self.hosting, pat.hosting):
            return False
        if pat.platform is not None:
            from ..ff.kotools import get_platform
            platform = get_platform()
            if not matched(platform, pat.platform):
                return False
        if pat.m3u8 is not None and ((m3u8 := self.is_m3u8()) is None or not matched(m3u8, pat.m3u8)):  # XXX
            return False
        if pat.setting:
            from ..ff.settings import settings
            if not settings.eval(pat.setting):
                return False
        if pat.kodi is not None:
            from ..kodi import KODI
            if isinstance(pat.kodi, int):
                if KODI >= pat.kodi:
                    return False
            elif not matched(KODI, pat.kodi):
                return False
        if pat.premium is not None and not matched(self.meta.get('premium', False), pat.premium):
            return False
        if pat.meta is not None:
            for key, val in pat.meta.items():
                if not matched(self.meta.get(key), val):  # type: ignore[reportGeneralTypeIssues]
                    return False
        if pat.size is not None and pat.size != self.size:
            return False
        if pat.media is not None and not matched(self.ffitem.ref.real_type, pat.media):
            return False
        return True

    def attr_update(self) -> bool:
        """Find mathing rule, return True if changed."""
        # old attrs
        attr = asdict(self.attr)
        # lookup
        changed = False
        ff: Sequence[Attribute] = fields(SourceAttribute)
        for pat, new in const.sources.rules.items():
            if new is not False and self.match(pat):
                for f in ff:
                    val = getattr(new, f.name)
                    if val is not None and getattr(self.attr, f.name) is None and val != attr[f.name]:
                        attr[f.name] = val
                        changed = True
        if changed:
            self.attr = SourceAttribute(**attr)
        return changed

    @property
    def resolved_url(self) -> str:
        """Return URL after resolve or empty string."""
        return self.url if self.resolved else ''

    def is_m3u8(self) -> bool | None:
        """Determine if is m3u8 file. Return None if unknown."""
        # stream URL is known
        if self.resolved:
            return bool(self.RX_M38U.search(self.url))
        # link is not resolved, the name could be ok or not, then...
        # ... it's m3u8
        if self.RX_M38U.search(self.url):
            return True
        # ... it's another file
        if self.RX_NOT_M38U.search(self.url):
            return False
        # ... we ca not determine
        return None

    def set_play_mode(self, play: PlayMode) -> None:
        self.attr = evolve(self.attr, play=play)

    def menu_actions(self) -> Iterator[SourceAction]:
        """Iterate over avaliable contex-menu actions (like play-modes). Action `play` is replaced with `direct or `isa`."""
        actions = self.attr.menu
        if actions is None:
            actions = ('play', )
        for act in actions:
            if act == 'play':
                if self.attr.play != 'direct':
                    yield 'direct'
                # ISA is not a default and m3u8 is not false (means is true or none/unknown)
                if self.attr.play != 'isa' and self.is_m3u8() is not False:
                    yield 'isa'
            else:
                yield act

    @property
    def size(self) -> int:
        """Return size (like "12.3 GB") in bytes or 0 if unknown or not a number."""
        if self._size is None:
            self._size = 0
            size = self.meta.get('size', '')
            if mch := self.RX_UNIT.fullmatch(size.upper().replace(' ', '')):
                num, unit = mch.groups()
                try:
                    num = float(num.replace(',', '.'))
                except ValueError:
                    pass
                else:
                    if unit:
                        num *= 1024 ** ('KMGT'.index(unit) + 1)
                    self._size = int(num)
        return self._size

    def as_json(self) -> JsonData:
        """Return JSON data without FFItem."""
        return asdict(self, recurse=True, filter=filters.exclude('ffitem', '_size'))

    @classmethod
    def from_json(cls, data: JsonData, *, ffitem: FFItem) -> Self:
        """Return source from JSON data with FFItem."""
        data = dict(data)
        attr = SourceAttribute(**data.pop('attr'))
        return cls(ffitem=ffitem, attr=attr, **data)

    @classmethod
    def from_provider_dict(cls, *, provider: str, ffitem: FFItem, item: SourceItem) -> Self:
        """Create instance from provider's meta-item-dict."""
        meta: SourceMeta = dict(item)  # type: ignore[reportGeneralTypeIssues]
        url: str = meta.pop('url', '')  # type: ignore[reportGeneralTypeIssues]
        hosting: str = meta.pop('hosting', '') or meta.pop('source', '').lower()  # type: ignore[reportGeneralTypeIssues]
        attr = SourceAttribute()
        return cls(url=url, provider=provider, hosting=hosting, ffitem=ffitem, attr=attr, meta=meta)

    @classmethod
    def from_meta(cls, *, ffitem: FFItem, meta: SourceXMeta) -> Self:
        """Create instance from already updated meta-dict. Useful for const prepend and append."""
        attr = SourceAttribute()
        meta = SourceXMeta(meta)
        url = meta.pop('url', '')
        provider = meta.pop('provider', '')
        hosting = meta.pop('hosting', '') or meta.pop('source', '').lower()
        src = cls(url=url, provider=provider, hosting=hosting, ffitem=ffitem, attr=attr, meta=meta)
        src.attr_update()
        return src

    @classmethod
    def action_label(cls, action: SourceAction) -> str:
        from ..kolang import L
        if action == 'direct':
            return L(30311, 'Play direct')
        elif action == 'isa':
            return L(30310, 'Play via InputStream')
        elif action == 'buy':
            return L(30116, 'Buy it again')
        return ''

    # --- dict-like infterface (readonly) ---

    def __contains__(self, key: Any) -> bool:
        return key in self.meta

    def __getitem__(self, key: SourceMetaRequiredKey) -> str:
        if key == 'provider':
            return self.provider
        if key == 'hosting':
            return self.hosting
        if key == 'url':
            return self.url
        return self.meta[key]

    @overload
    def get(self, key: SourceMetaRequiredKey, default: Any = None) -> str: ...

    @overload
    def get(self, key: SourceMetaOptionalStrKey, default: T = None) -> Union[str, T]: ...

    @overload
    def get(self, key: SourceMetaOptionalBoolKey, default: T = None) -> Union[bool, T]: ...

    @overload
    def get(self, key: Any, default: T = None) -> T: ...

    def get(self, key: Any, default: T = None) -> Union[str, bool, T]:
        if key == 'provider':
            return self.provider
        if key == 'hosting':
            return self.hosting
        if key == 'url':
            return self.url
        return self.meta.get(key, default)

    def keys(self) -> KeysView[str]:
        return self.meta.keys()

    def values(self):  # -> ValuesView[Union[int, bool]]:
        return self.meta.values()

    def items(self):  # -> ItemsView[str, Union[int, bool]]:
        return self.meta.items()

    # --- dict-like infterface (write) ---  TODO: think it's really necesery

    @overload
    def __setitem__(self, key: Union[SourceMetaRequiredKey, SourceMetaOptionalStrKey], value: str) -> None: ...

    @overload
    def __setitem__(self, key: SourceMetaOptionalBoolKey, value: bool) -> None: ...

    def __setitem__(self, key: str, value: Union[str, bool]) -> None:
        if key in ('provider', 'hosting', 'url'):
            log.error(f'Set Source[{key:r}] is not allowed')
        self.meta[key] = value

    def update(self, data: dict[SourceMetaKey, Any], /):
        if wrong := ({'provider', 'hosting', 'url'} & data.keys()):
            log.error(f'Source.update() for {", ".join(map(repr, wrong))} is not allowed')
        self.meta.update(data)  # type: ignore[reportCallIssue]  # there is no Partial[SourceMeta] yet


class SourceTitleAlias(TypedDict):
    """Alias for title, used in provider protocol."""
    title: str
    country: str
    originalname: str


#: "class source" type.
class ProviderProtocol(Protocol):
    """Source provider class protocol (api)."""

    #: True, if __init__ accepts ffitem.
    INIT_WITH_FFITEM: ClassVar[bool] = False

    # -- old "sources" api (still used) ---
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ()
    # "domains" is not used

    # --- those settings are parsed but not requiered, see Provider class ---
    # has_sort_order: ClassVar[bool] = False
    # has_color_identify2: ClassVar[bool] = False
    # has_library_color_identify2: bool = False
    # use_premium_color: ClassVar[bool] = False

    # --- provider instance data, set in sources.py ---
    canceled: bool
    ffitem: FFItem   # set in ff/sources.py at that moment

    # def __init__(self, *, ffitem: FFItem) -> None: ...

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> str | None: ...

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Any: ...

    def episode(self, url: str, imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> str | None: ...

    def sources(self, url: str, host_list: Sequence[str], pr_host_list: Sequence[str], /, from_cache: bool = False) -> Sequence[SourceItem]: ...

    def resolve(self, url: str, **kwargs: Unpack[SourceResolveKwargs]) -> str: ...


class Provider:
    """Base provider class."""

    #: True, if __init__ accepts ffitem.
    INIT_WITH_FFITEM: ClassVar[bool] = True

    has_sort_order: ClassVar[bool] = False
    has_color_identify2: ClassVar[bool] = False
    has_library_color_identify2: bool = False
    use_premium_color: ClassVar[bool] = False

    # -- old "sources" api (still used) ---
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ()
    # domains: ClassVar[Sequence[str]] = ()  # ???  (used only a few times)

    # --- provider instance data, set in sources.py ---
    ffitem: FFItem   # set in ff/sources.py at that moment
    canceled: bool

    def __init__(self, *, ffitem: FFItem) -> None:
        self.ffitem = ffitem
        self.canceled = False


@frozen
class SourceModule:
    """Tuple from load source/*/* modules, provider proxy."""
    #: Module name.
    name: str
    #: Module source object.
    provider: ProviderProtocol
    #: Group of the sources (eg. language like "en", "pl").
    group: str = ''

    def match(self, pat: ProviderPattern) -> bool:
        """Check if source match given provder pattern (only provder level: provider, platform, kodi) for prefiltering."""
        if pat.provider and pat.provider != self.name:
            return False
        if pat.platform is not None:
            from ..ff.kotools import get_platform
            platform = get_platform()
            if isinstance(pat.platform, str) and pat.platform != platform:
                return False
            elif platform not in pat.platform:
                return False
        if pat.kodi is not None:
            from ..kodi import KODI
            if isinstance(pat.kodi, int):
                if KODI >= pat.kodi:
                    return False
            elif isinstance(pat.kodi, (str, RegEx, Expr)):
                if pat.kodi != KODI:
                    return False
            elif KODI not in pat.kodi:
                return False
        return True

    def is_enabled_by_rules(self, rules: Mapping[SourcePattern, SourceRuleValue] | None = None) -> bool:
        """Check if provider is enabled by given rules. False means the provider is disabled at all (any source will not match)."""
        if rules is None:
            rules = const.sources.rules
        enabled: bool = True
        for pat, attr in const.sources.rules.items():
            if attr is False:  # disabled
                # no source condition means disable provider
                if self.match(pat) and not pat.has_source_condition():
                    enabled = False
            elif enabled is False:
                # currently disabled, check for re-enable
                enabled = self.match(pat)
        return enabled


@frozen
class SourcePythonModule:
    """Python module from load source/*/*."""
    #: Module name.
    name: str
    #: Module python object.
    module: ModuleType
    #: Group of the sources (eg. language like "en", "pl").
    group: str = ''

    def load_providers(self, *, ffitem: FFItem) -> Iterable[SourceModule]:
        """Load providers form python module."""
        with logtime(name=f'Create providers from {self.name}', fflog=log.DEBUG):
            try:
                if register := getattr(self.module, 'register', None):
                    sources: list[SourceModule] = []
                    register(sources, group=self.group)
                    yield from sources
                else:
                    provider_class = self.module.source
                    if getattr(provider_class, 'INIT_WITH_FFITEM', False):
                        provider = provider_class(ffitem=ffitem)
                    else:
                        provider = provider_class()
                        provider.ffitem = ffitem
                    yield SourceModule(name=self.name, provider=provider, group=self.group)
            except Exception as e:
                log.warning(f'Provider creating Error - {self.name!r}: {e}')
                fflog_exc()


def scan_source_modules(*, ffitem: FFItem) -> list[SourceModule]:
    """Scan source/*/*.py and load modules."""
    global _source_modules
    if _source_modules is None:
        _source_modules = []
        try:
            # real absolute path
            path = Path(__file__).parent.resolve()
        except PermissionError:
            # fallback for restricted systems (like XBox)
            path = Path(__file__).parent
        # NOTE, map(str,…) for workaround py3.10 bug
        for info in walk_packages(map(str, path.glob("[!_.]*"))):
            if not info.ispkg:
                with log_submodule(f'loading:{info.name}'):
                    with logtime(name=f'Load module {info.name}', fflog=log.DEBUG):
                        try:
                            spec = info.module_finder.find_spec(info.name, None)
                            if spec is not None and spec.loader is not None:
                                group = str(Path(info.module_finder.path).relative_to(path))
                                module = spec.loader.load_module(info.name)
                                _source_modules.append(SourcePythonModule(name=info.name, module=module, group=group))
                        except Exception as e:
                            log.warning(f'Provider loading Error - {info.name!r}: {e}')
                            fflog_exc()
    return [src for mod in _source_modules for src in mod.load_providers(ffitem=ffitem)]


def clear_source_modules() -> None:
    """Clean source modules, destroy provider objects."""


def class_single_call(method: Callable[P, R], /) -> Callable[P, R]:
    """Decorator for single call method. Once per class not per instance!"""
    @wraps(method)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        nonlocal result
        if result is ...:
            result = method(*args, **kwargs)
        return result
    result = ...
    return wrapped


def single_call(method: Callable[P, R], /) -> Callable[P, R]:
    """Decorator for single call a method, ex. init(), per instance."""
    @wraps(method)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        if not args:
            return method(*args, **kwargs)
        self = args[0]
        key = f'_single_call_result_{method.__name__}'
        result = getattr(self, key, ...)
        if result is ...:
            result = method(*args, **kwargs)
            setattr(self, key, result)
        return result
    return wrapped


_source_modules: list[SourcePythonModule] | None = None
