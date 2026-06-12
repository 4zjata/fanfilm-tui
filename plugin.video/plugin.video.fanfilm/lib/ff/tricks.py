"""Some useful tricks."""

from __future__ import annotations
from sys import version_info
from functools import wraps, partial
from itertools import chain, zip_longest
from collections import namedtuple
from inspect import signature, Signature, Parameter
from inspect import ismethod, isfunction, isbuiltin, getmro, getmodule, currentframe
from dataclasses import Field, is_dataclass
import dataclasses as dc
from functools import reduce
from itertools import tee
from operator import or_
import threading
from enum import Flag
from datetime import datetime, date as dt_date, time as dt_time
from pathlib import Path
from string import Formatter as StringFormatter
from decimal import Decimal
import json
import math  # for DEFAULT_NAMES
import itertools  # for DEFAULT_NAMES
from typing import Any, Mapping, Sequence, Iterator, Iterable, TypeVar, Callable, NoReturn, NamedTuple, TYPE_CHECKING
from types import ModuleType, TracebackType
from typing_extensions import Concatenate, ParamSpec, TypeIs, cast, get_type_hints, Protocol, Generic, overload
import attrs
from attrs import Attribute
from simpleeval import InvalidExpression, EvalWithCompoundTypes, SimpleEval
import simpleeval

from .log_utils import fflog_exc, fflog
from ..exc import DeprecatedError
from ..service.exc import KodiExit

if TYPE_CHECKING:
    import ast
    from typing_extensions import Self, Buffer, TypeAlias
    ReadableBuffer: TypeAlias = Buffer


if version_info >= (3, 9):
    str_removeprefix = str.removeprefix
else:
    def str_removeprefix(s: str, __prefix: str, /):
        """
        If the string starts with the prefix string, return string[len(prefix):].
        Otherwise, return a copy of the original string.
        """
        if s.startswith(__prefix):
            return s[len(__prefix):]
        return s

if version_info >= (3, 10):
    from itertools import pairwise
else:
    def pairwise(iterable):
        """
        Return successive overlapping pairs taken from the input iterable.
        pairwise('ABCDE') --> AB BC CD DE.
        """
        a, b = tee(iterable)
        next(b, None)
        return zip(a, b)

if version_info >= (3, 12):
    from itertools import batched
else:
    from itertools import islice

    def batched(iterable, n):
        """
        Batch data from the iterable into tuples of length n. The last batch may be shorter than n.
        batched('ABCDEFG', 3) --> ABC DEF G
        """
        if n < 1:
            raise ValueError('n must be at least one')
        it = iter(iterable)
        while batch := tuple(islice(it, n)):
            yield batch


if version_info >= (3, 9):
    path_is_relative_to = Path.is_relative_to  # type: ignore[reportAssignmentType]
else:
    def path_is_relative_to(self: Path, other: Path) -> bool:
        other = Path(other)
        return other == self or other in self.parents


if TYPE_CHECKING:
    from dataclasses import _DataclassParams


P = ParamSpec('P')
K = ParamSpec('K')
T = TypeVar('T')
D = TypeVar('D')
C = TypeVar('C')
F = TypeVar('F', bound=Flag)
RET = TypeVar('RET')
N = TypeVar('N')
# NC = Callable[Concatenate[N, P], T]
NC = Concatenate[N, P]
FLOAT = TypeVar('FLOAT', float, Decimal)


class DataClass(Protocol):
    __dict__: dict[str, Any]
    __doc__: str | None
    # if using `@dataclass(slots=True)`
    __slots__: str | Iterable[str]
    __annotations__: dict[str, str | type]
    __dataclass_fields__: dict[str, Field]
    # the actual class definition is marked as private, and here I define
    # it as a forward reference, as I don't want to encourage
    # importing private or "unexported" members.
    # __dataclass_params__: '_DataclassParams'
    # __post_init__: Callable | None


def singleton(orig_cls: type[T]) -> type[T]:
    """Singleton class decorator. Only one instance of the class is created."""
    orig_new: Callable = orig_cls.__new__
    instance: Any = None

    @wraps(orig_cls.__new__)
    def __new__(cls, *args, **kwargs):
        nonlocal instance
        if instance is None:
            # object.__new__ takes only one argument
            if orig_new is object.__new__:
                instance = object.__new__(cls)
            else:
                instance = orig_new(cls, *args, **kwargs)
            if cls.__singleton_init__:
                cls.__singleton_init__(instance, *args, **kwargs)
        return instance

    # override __new__
    orig_cls.__new__ = __new__
    # remove __init__ to keep call it once
    orig_cls.__singleton_init__ = getattr(orig_cls, '__init__', None)
    if orig_cls.__singleton_init__ is not None and orig_cls.__singleton_init__ is not object.__init__:
        if not any(orig_cls.__init__ is c.__init__ for c in orig_cls.mro()[1:]):
            del orig_cls.__init__
    return orig_cls


@singleton
class MissingType:
    """General type of singleton MISSING."""

    def __repr__(self) -> str:
        return 'MISSING'

    def __bool__(self) -> bool:
        return False


#: General missing singleton object.
MISSING = MissingType()


class suppress:
    """
    Decorator and context manager that suppresses any of the specified exceptions.

    Examples.
    >>> with suppress(FileNotFoundError):
    >>>     os.remove('somefile.tmp')
    >>>
    >>> @suppress(FileNotFoundError)
    >>> def foo():
    >>>     os.remove('somefile.tmp')
    """

    def __init__(self, *exceptions, return_on_exception: Any = None,
                 errors_arg: bool = False, log_traceback: bool = False) -> None:
        #: List of suppressed exceptions.
        self._exceptions: tuple[BaseException, ...] = exceptions
        #: Value to return by decorated function if suppressed exception occurs.
        self._return_on_exception: Any = return_on_exception
        #: Look into `errors` function argument in decorator.
        self._errors_arg: bool = errors_arg
        #: Log traceback on exception.
        self._log_traceback: bool = log_traceback
        # # is it @suppress without arguments?
        # if len(exceptions) == 1 and callable(exceptions[0]) and not issubclass(exceptions[0], BaseException):
        #     self._exceptions = (Exception,)

    def __call__(self, func: Callable[P, RET]) -> Callable[P, RET]:
        if self._errors_arg:
            @wraps(func)
            def wrapped(*args, errors: str = 'ignore', **kwargs):
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    if errors == 'ignore' and self._check_if_suppressed(exc.__class__, exc):
                        return self._return_on_exception
                    if self._log_traceback:
                        fflog_exc(internal=True)
                    raise
        else:
            @wraps(func)
            def wrapped(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except BaseException as exc:
                    if self._check_if_suppressed(exc.__class__, exc):
                        return self._return_on_exception
                    if self._log_traceback:
                        fflog_exc(internal=True)
                    raise

        return wrapped

    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_inst, exc_tb):
        # return exc_type is not None and issubclass(exc_type, self._exceptions)
        return self._check_if_suppressed(exc_type, exc_inst)
        if exc_type is None:
            return
        if issubclass(exc_type, self._exceptions):
            return True
        # Python 3.12 adds support for Py3.11 ExceptionGroup
        if version_info >= (3, 11) and issubclass(exc_type, ExceptionGroup):
            match, rest = exc_inst.split(self._exceptions)
            if rest is None:
                return True
            raise rest
        if self._log_traceback:
            fflog_exc(internal=True)
        return False

    def _check_if_suppressed(self, exc_type, exc_inst) -> bool:
        if exc_type is None:
            return False
        if issubclass(exc_type, self._exceptions):
            return True
        # Python 3.12 adds support for Py3.11 ExceptionGroup
        if version_info >= (3, 11) and issubclass(exc_type, ExceptionGroup):
            match, rest = exc_inst.split(self._exceptions)
            if rest is None:
                return True
            raise rest
        return False


def is_namedtuple(obj: object) -> TypeIs[tuple]:
    """Retrun true if object is a namedtuple (NamedTuple)."""
    return isinstance(obj, tuple) and hasattr(type(obj), '_fields')


def is_namedtuple_class(cls: Any) -> TypeIs[type[tuple]]:
    """Retrun true if class inheris from namedtuple (NamedTuple)."""
    return isinstance(cls, type) and issubclass(cls, tuple) and hasattr(cls, '_fields')


def namedtuple_attrs(cls: type[tuple]) -> tuple[Attribute, ...]:
    """Return attrs.fields for namedtuple."""
    def make_field(name: str) -> Attribute:
        if name in defaults:
            default = defaults[name]
        else:
            default = attrs.NOTHING
        return Attribute(name=name, default=default, validator=None, repr=True, cmp=False, hash=None, init=True,
                         inherited=False, type=ann[name])  # type: ignore

    if not is_namedtuple(cls) and not is_namedtuple_class(cls):
        raise TypeError('must be called with a namedtuple type or instance') from None

    defaults: dict[str, Any] = cls._field_defaults
    ann = get_type_hints(cls)
    return tuple(make_field(f) for f in cls._fields)


def namedtuple_fields(cls: type[tuple]) -> tuple[Field, ...]:
    """Return dataclasses.fields from namedtuple."""
    def make_field(name: str) -> Field:
        f = dc.field()
        f.name = name
        f.type = ann[name]
        if name in defaults:
            f.default = defaults[name]
        return f

    if not is_namedtuple(cls) and not is_namedtuple_class(cls):
        raise TypeError('must be called with a namedtuple type or instance') from None

    defaults: dict[str, Any] = cls._field_defaults
    ann = get_type_hints(cls)
    return tuple(make_field(f) for f in cls._fields)


def namedtuple_base(base: type[NamedTuple]) -> Callable[[type[NamedTuple]], type[NamedTuple]]:
    """
    Decorator for NamedTuple derivation.

    The class is not derived from base class (isinstance fails), only fields are taken.
    If base class uses defaults all fields must use defaults.

    >>> class Point2d(NamedTuple):
    >>>     x: int
    >>>     y: int
    >>>
    >>> @namedtuple_base(Point2d)
    >>> class Point3d(NamedTuple):
    >>>     z: int
    >>>
    >>> Point3d(1, 2, 3)
    """

    def create_namedtuple(kls: type) -> type[NamedTuple]:
        def rev_defaults():
            defaults = {**base._field_defaults, **kls._field_defaults}
            for f in reversed(fields):
                if f not in defaults:
                    break
                yield defaults[f]

        if base._field_defaults and len(kls._field_defaults) < len(kls._fields):
            raise ValueError(f'Subclass {kls.__name__} have to set defaults for all fields')
        fields = base._fields + kls._fields
        nm_tpl = namedtuple(kls.__name__, fields, defaults=list(reversed(list(rev_defaults()))), module=kls.__module__)
        nm_tpl.__annotations__ = nm_tpl.__new__.__annotations__ = {**base.__annotations__, **kls.__annotations__}
        return nm_tpl

    return create_namedtuple


def is_attrs_class(obj) -> TypeIs[type[attrs.AttrsInstance] | attrs.AttrsInstance]:
    cls = obj if isinstance(obj, type) else type(obj)
    return attrs.has(cls)


def dataclass_attrs(cls: type[DataClass]) -> tuple[Attribute, ...]:
    """Return attrs.fields from dataclass."""

    def make_field(f: Field) -> Attribute:
        if f.default is dc.MISSING:
            default = attrs.NOTHING
        elif f.default_factory is not dc.MISSING:
            default = attrs.Factory(f.default_factory)
        else:
            default = f.default
        return Attribute(name=f.name, default=default, validator=None, repr=f.repr, cmp=f.compare, hash=f.hash, init=f.init,
                         inherited=False, type=f.type)  # type: ignore

    if not is_dataclass(cls):
        raise TypeError('must be called with a dataclass type or instance') from None

    # ann = get_type_hints(cls)
    return tuple(make_field(f) for f in dc.fields(cls))


def dict_diff_new(a: Mapping, b: Mapping, *, recursion: bool = True) -> dict:
    """Return dict new and differented values (b over a)."""
    # same = a.keys() & b.keys()
    # new = b.keys() - a.keys()
    if recursion:
        return {k: dict_diff_new(a[k], v) if k in a and isinstance(v, Mapping) else v
                for k, v in b.items() if k not in a or a[k] != v}
    else:
        return {k: v for k, v in b.items() if k not in a or a[k] != v}


def dump_obj_gets(obj: Any, pattern: str = 'get', def_str_key: str = 'tmdb') -> Iterator[tuple[str, Any]]:
    """Dump obj.get*()."""
    attrs: list[str] = dir(obj)
    if hasattr(obj, '__wrapped__') and hasattr(type(obj), '__dict__'):
        attrs.extend(type(obj).__dict__)  # access to wrapper methods
    for attr in attrs:
        if attr.startswith(pattern):
            getter = getattr(obj, attr, None)
            # ismethod(getter) - it works ONLY with class wirtent in Python
            if callable(getter) and getattr(getter, '__self__', None) is not None:
                sig: Signature = signature(getter)
                try:
                    name = getter.__qualname__
                except AttributeError:
                    name = f'{obj.__class__.__name__}.{attr}'
                try:
                    val = getter()
                except (TypeError, DeprecatedError):
                    def def_val(p: Parameter) -> Any:
                        if p.annotation is str and def_str:  # noqa: B023  (`def_str` is shared list)
                            return def_str.pop(0)            # noqa: B023
                        if p.annotation in (str, bool, int, float):
                            return p.annotation()
                        return ''

                    def_str: list[str] = [def_str_key]
                    try:
                        params = sig.parameters.values()
                        args = [def_val(p) for p in params if p.default is p.empty and p.kind is p.POSITIONAL_ONLY]
                        kwargs = {p.name: def_val(p)
                                  for p in params if p.default is p.empty and p.kind is not p.POSITIONAL_ONLY}
                        val = getter(*args, **kwargs)
                    except DeprecatedError as exc:
                        yield name, exc
                        continue
                    except Exception:  # as exc:
                        # yield f'{obj.__class__.__name__}.{attr}() = ???  # {exc}'
                        yield name, NoReturn
                        continue
                # yield f'{obj.__class__.__name__}.{attr}() = {val!r}'
                yield name, val


@overload
def super_get_attr(obj: Any, key: str, /) -> Any: ...


@overload
def super_get_attr(obj: Any, key: str, default: T, /) -> Any | T: ...


def super_get_attr(obj: Any, key: str, /, *default) -> Any:
    """Like getattr() with sub.obj.support."""
    if '.' in key:
        keys = key.split('.')
        for key in keys[:-1]:
            obj = getattr(obj, key)
        key = keys[-1]
    return getattr(obj, key, *default)


def super_set_attr(obj: Any, key: str, value: Any) -> None:
    """Like setattr() with sub.obj.support."""
    keys = key.split('.')
    for k in keys[:-1]:
        obj = getattr(obj, k)
    setattr(obj, keys[-1], value)


def super_has_attr(obj: Any, key: str) -> Any:
    """Like hasattr() with sub.obj.support."""
    try:
        super_get_attr(obj, key)
    except AttributeError:
        return False
    return True


# See: https://stackoverflow.com/a/74252449/9935708
def is_unbound_method(func) -> bool:
    """Return True if `func` is an unbound method."""
    if not isfunction(func):
        return False
    qualname = func.__qualname__
    name = func.__name__
    if qualname == name:  # it's a top-level function
        return False
    elif qualname.endswith(f'.{name}'):  # it's either a nested function or a method
        prefix = qualname[:-len(name) - 1]
        return not prefix.endswith('<locals>')
    else:  # what is it, even?
        raise ValueError(f"Can't tell if {func!r} is a method")


# See: https://stackoverflow.com/a/74252449/9935708
def is_pure_function(func) -> bool:
    """Return True if `func` is a pure function (not a method, bound or unbound)."""
    if not isfunction(func):
        return False
    qualname = func.__qualname__
    name = func.__name__
    if qualname == name:  # it's a top-level function
        return True
    elif qualname.endswith(f'.{name}'):  # it's either a nested function or a method
        prefix = qualname[:-len(name) - 1]
        return prefix.endswith('<locals>')
    elif name not in qualname:  # what is it, maybe hacked @folder internal function
        return False
    else:  # what is it, even?
        fflog.warning(f"Can't tell if {func!r} is a method, {name=}, {qualname=}")
        return False
        # raise ValueError(f"Can't tell if {func!r} is a method")


# See: https://stackoverflow.com/a/71968448/9935708
def with_function_typing(_: Callable[P, Any]) -> Callable[[Callable[..., T]], Callable[P, T]]:
    """Decorator does nothing but returning the casted original function."""

    def return_func(func: Callable[..., T]) -> Callable[P, T]:
        return cast(Callable[P, T], func)

    return return_func


def with_method_typing(_: Callable[Concatenate[C, P], Any]) -> Callable[[Callable[..., T]], Callable[P, T]]:
    """Decorator does nothing but returning the casted original method (ignore self/cls)."""

    def return_func(func: Callable[..., T]) -> Callable[P, T]:
        return cast(Callable[P, T], func)

    return return_func


# See: https://stackoverflow.com/a/25959545/9935708
def get_method_class(meth):
    """Get class of method `meth`, bound or not."""

    if isinstance(meth, partial):
        return get_method_class(meth.func)
    if (ismethod(meth) or (isbuiltin(meth) and getattr(meth, '__self__', None) is not None
                           and getattr(meth.__self__, '__class__', None))):
        for cls in getmro(meth.__self__.__class__):
            if meth.__name__ in cls.__dict__:
                return cls
        meth = getattr(meth, '__func__', meth)  # fallback to __qualname__ parsing
    if isfunction(meth):
        cls = getattr(getmodule(meth),
                      meth.__qualname__.split('.<locals>', 1)[0].rsplit('.', 1)[0],
                      None)
        if isinstance(cls, type):
            return cls
    return getattr(meth, '__objclass__', None)  # handle special descriptor objects


def join_items(*items: Iterable[T], zip_chunk: int = 0) -> Iterable[T]:
    """
    Join list it items by `zip_chunk`.


    >>> join_items('abc', '123', zip_chunk=0)  # abc123
    >>> join_items('abc', '123', zip_chunk=1)  # a1b2c3
    >>> join_items('abc', '123', zip_chunk=2)  # ab12c3
    """
    if not items:
        pass

    elif len(items) == 1:
        yield from iter(items[0])

    elif zip_chunk <= 0:
        yield from chain(*items)

    elif zip_chunk == 1:
        for row in zip_longest(*items, fillvalue=MISSING):
            for it in row:
                if it is not MISSING:
                    yield it

    else:
        iters: list[Iterator[T] | None] = [iter(ii) for ii in items]
        running = True
        while running:
            running = False
            for i, it in enumerate(iters):
                if it is not None:
                    for j in range(zip_chunk):
                        try:
                            yield next(it)
                            running = True
                        except StopIteration:
                            iters[i] = None
                            break


def pairprev(iterable: Iterable[T], fillvalue: D = None) -> Iterable[tuple[T | D, T]]:
    """s -> (None,s0), (s0,s1), (s1,s2), ..."""
    a, b = tee(iterable)
    try:
        yield fillvalue, next(b)
    except StopIteration:
        return
    yield from zip(a, b)


def pairnext(iterable: Iterable[T], fillvalue: D = None) -> Iterable[tuple[T, T | D]]:
    """s -> (s0,s1), (s1,s2), ... (sn, None)"""
    a, b = tee(iterable)
    next(b, None)
    return zip_longest(a, b, fillvalue=fillvalue)  # type: ignore – a always is longer


# Modiled Gareth Rees method.
# See: https://codereview.stackexchange.com/a/86067
def cyclic(*graphs: dict[T, Iterable[T]] | dict[T, dict[T, Any]]) -> bool:
    """
    Return True if the directed graph has a cycle.
    The graph must be represented as a dictionary mapping vertices to
    iterables of neighbouring vertices. For example:

    >>> cyclic({1: (2,), 2: (3,), 3: (1,)})
    True
    >>> cyclic({1: (2,), 2: (3,), 3: (4,)})
    False
    """
    visited = set()
    path = [object()]
    path_set = set(path)
    stack = [set(chain(*graphs))]
    while stack:
        for v in stack[-1]:
            if v in path_set:
                return True
            elif v not in visited:
                visited.add(v)
                path.append(v)
                path_set.add(v)
                stack.append({x for g in graphs for x in g.get(v, ())})
                break
        else:
            path_set.remove(path.pop())
            stack.pop()
    return False


if TYPE_CHECKING:

    class adict(dict[str, T], Generic[T]):
        """Just dict() with attribute access."""

        def __getattr__(self, key: str) -> T:
            try:
                return self[key]
            except KeyError:
                raise AttributeError(key) from None

else:

    class adict(dict):
        """Just dict() with attribute access."""

        def __getattr__(self, key: str) -> T:
            try:
                return self[key]
            except KeyError:
                raise AttributeError(key) from None


class FormatObjectGetter:
    """Wrapper for object to auto call attributes. Useful for str format."""

    TYPES = {int, float, str, bytes, datetime, dt_date, dt_time}

    def __init__(self, obj: Any) -> None:
        self.__wrapped__ = obj

    def __getattr__(self, key: str):
        try:
            value = getattr(self.__wrapped__, key)
        except AttributeError as exc:
            try:
                value = getattr(self.__wrapped__, f'get{key}')
            except AttributeError:
                raise exc
            pass
        if callable(value):
            value = value()
        if type(value) in self.TYPES:
            return value
        return FormatObjectGetter(value)


class AlwaysFalse:
    """Fake false object."""

    def __bool__(self) -> bool:
        return False


class ThreadExceptHookArgsType(Protocol):
    exc_type: type[BaseException]
    exc_value: BaseException | None
    exc_traceback: TracebackType
    thread: threading.Thread | None


def thread_excepthook(args: ThreadExceptHookArgsType) -> None:
    """Hook exception in thread."""
    if args.exc_value and args.exc_type not in (KodiExit, SystemExit, KeyboardInterrupt):
        fflog(f'EXCEPTION in thread: {args.thread}', internal=True)
        fflog_exc(args.exc_value, internal=True)
    else:
        fflog(f'Force exiting ({args.exc_type}) thread: {args.thread}', internal=True)


# Override exception hook.
threading.excepthook = thread_excepthook


def current_line_number(depth: int = 1) -> int:
    """Return caller file line number."""
    cf = currentframe()
    for _ in range(depth):
        if not cf:
            break
        cf = cf.f_back
    return cf.f_lineno if cf else 0


def jwt_decode(token: str) -> dict[str, Any]:
    """Simple JWT (JSON-Web-Token) decode."""
    from base64 import b64decode
    import json
    _, payload, *_ = token.split('.')
    if pad := len(payload) % 4:
        payload += '=' * (4 - pad)
    return json.loads(b64decode(payload).decode())


def decimal_range(start: float | Decimal, stop: float | Decimal, step: float | Decimal) -> Iterator[Decimal]:
    """Range for float values."""
    if not isinstance(start, Decimal):
        start = Decimal(str(start))
    if not isinstance(stop, Decimal):
        stop = Decimal(str(stop))
    if not isinstance(step, Decimal):
        step = Decimal(str(step))
    if step == 0:
        raise ValueError('step must not be zero')
    value = start
    if step < 0:
        while value > stop:
            yield value
            value += step
    else:
        while value < stop:
            yield value
            value += step


def frange(start: float, stop: float, step: float, *, ε: float | None = None) -> Iterator[float]:
    """Range for float values."""
    i = 0  # use integer arithmetics to avoid float precision issues
    if ε is None:
        ε = abs(step) / 1_000_000_000_000
    if step == 0:
        raise ValueError('step must not be zero')
    if step < 0:
        step -= ε
        while (value := start + step * i) > stop:
            yield value
            i += 1
    else:
        step += ε
        while (value := start + step * i) < stop:
            yield value
            i += 1


def round_to_step(value: FLOAT, /, step: FLOAT) -> FLOAT:
    """Round `value` to nearest `step`."""
    if TYPE_CHECKING:
        assert not isinstance(step, Decimal)
    if step == 0:
        fflog.error('step must not be zero')
        return value
    half  = .5 if step > 0 else -.5
    try:
        return int(value / step + half) * step
    except TypeError:
        return int(value / step + Decimal(half)) * step  # type: ignore[returnValue]


if version_info >= (3, 11):
    def iter_flags(cls_or_flag: F | type[F], /) -> Iterator[F]:  # type: ignore[reportRedeclaration]
        """Iterate over separate flags (default since py 3.11)."""
        yield from iter(cls_or_flag)
else:
    def iter_flags(cls_or_flag: F | type[F], /) -> Iterator[F]:
        """Iterate over separate flags (default since py 3.11)."""
        if isinstance(cls_or_flag, type):
            flag, cls = None, cls_or_flag
        else:
            flag, cls = cls_or_flag, type(cls_or_flag)
        flags2: tuple[F, ...] | None
        if (flags2 := getattr(cls, '_flags2', None)) is None:
            flags2 = tuple(sorted((f for f in cls if (v := f.value) and not (v & (v-1))), key=lambda f: f.value))
            setattr(cls, '_flags2', flags2)
        if flag is None:
            yield from flags2
        else:
            for f in flags2:
                if f & flag:
                    yield from f


def or_reduce(seq: Iterable[T]) -> T:
    """Reduce sequence with `or` operator."""
    return reduce(or_, seq)


DEFAULT_NAMES: 'dict[str, Any]' = {
    **simpleeval.DEFAULT_NAMES,
    'PY': version_info,
    'python_version': version_info,
    # 'KODI': KODI,
    # 'kodi_version': kodi_version_info,
    'math': math,
    'itertools': itertools,
}

DEFAULT_FUNCTIONS: 'dict[str, Callable[..., Any]]' = {
    **simpleeval.DEFAULT_FUNCTIONS,
    'range': range,
    'enumerate': enumerate,
    'zip': zip,
    'zip_longest': zip_longest,
    'min': min,
    'max': max,
    'any': any,
    'all': all,
    'len': len,
    # 'fflog': fflog,
}

DEFAULT_OPERATORS: 'dict[ast.AST, Callable[[Any, Any], Any] | Callable[[Any], Any]]' = {
    **simpleeval.DEFAULT_OPERATORS,
}


class Formatter(StringFormatter):
    """String formatter with safe missing fields."""

    DEFAULT_NAMES = DEFAULT_NAMES
    DEFAULT_FUNCTIONS = DEFAULT_FUNCTIONS
    DEFAULT_OPERATORS = DEFAULT_OPERATORS

    def __init__(self, *,
                 names: 'dict[str, Any] | None' = None,
                 functions: 'dict[str, Callable] | None' = None,
                 log_missing: bool = False,
                 ) -> None:
        super().__init__()
        #: Evaluator instance.
        self.evaluator: SimpleEval | None = None
        #: Evaluator built-in names (kwargs). If no evaluator `names` are merged with `kwargs`.
        self.evaluator_names: dict[str, Any] = {**DEFAULT_NAMES} if names is None else names
        #: Evaluator built-in functions.
        self.evaluator_functions: dict[str, Callable[..., Any]] = {**DEFAULT_FUNCTIONS} if functions is None else functions
        #: Formated string cache.
        self._formated_string: str = ''
        #: Log missing fields.
        self.log_missing = log_missing

    def vformat(self, format_string: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
        """Do the actual work of formatting."""
        # if self.evaluator_names:
        #     names = {**self.evaluator_names, **kwargs}
        # else:
        #     names = kwargs
        self._formated_string = format_string
        if self.evaluator is None:
            self.evaluator = EvalWithCompoundTypes(names=self.evaluator_names, functions=self.evaluator_functions)
            return super().vformat(format_string, args, kwargs)
        # return super().vformat(format_string, args, names)
        return super().vformat(format_string, args, kwargs)

    def get_field(self, field_name: str, args, kwargs) -> 'tuple[Any, str]':
        """Convert field_name as returned by parse() to an object to be formatted."""
        try:
            return super().get_field(field_name, args, kwargs)
        except (KeyError, AttributeError, IndexError):
            if field_name.isdigit():
                # missing positional argument
                return self.missing_field(field_name, args, kwargs)
        except Exception:
            pass
        if self.evaluator:
            eval_names, self.evaluator.names = self.evaluator.names, {**self.evaluator_names, **kwargs}
            try:
                return self.evaluator.eval(field_name), ()
            except (InvalidExpression, SyntaxError):
                pass
            finally:
                self.evaluator.names = eval_names
        return self.missing_field(field_name, args, kwargs)

    def missing_field(self, field_name: str, args, kwargs) -> 'tuple[Any, str]':
        """Return missing files, by default keep format string."""
        if self.log_missing:
            fflog(f'Missing field: {field_name!r} in format string {self._formated_string!r}')
        # return '{%s}' % field_name, ()
        return '{%s}' % field_name, field_name

    def format_field(self, value: Any, format_spec: str) -> str:
        """Call the global format() built-in, support for default values."""
        try:
            return super().format_field(value, format_spec)
        except ValueError:
            fflog.error(f'ERROR value={value!r}, spec={format_spec!r}')
            if format_spec:
                if value[:1] == '{' and value[-1:] == '}':
                    return f'{value[:-1]}:{format_spec}}}'
                return f'{value}:{format_spec}'
            return f'{value}'

    def add_name(self, name: str, value: Any) -> None:
        """Register `name` in easy-eval names."""
        if self.evaluator_names is None:
            self.evaluator_names = {}
        self.evaluator_names[name] = value


class JsonEncoder(json.JSONEncoder):
    def default(self, o):
        from .url import URL
        from .types import is_datetime_instance
        if serialize := getattr(o, '__to_json__', None):
            return serialize()
        if getattr(type(o), '__attrs_attrs__', None) is not None:  # define / frozen
            return attrs.asdict(o)
        if isinstance(o, (dt_date, dt_time)) or is_datetime_instance(o):
            return str(o)
        return super().default(o)


@attrs.frozen
class IterableItemInfo:
    index: int
    last: bool

    @property
    def first(self) -> bool:
        return self.index == 0

    @property
    def odd(self) -> bool:
        return bool(self.index % 2)

    @property
    def even(self) -> bool:
        return not self.index % 2


def info_iter(iterable):
    """s -> (info0, s0), (info1, s1), ..."""
    i, it = 0, iter(iterable)
    try:
        prev = next(it)
    except StopIteration:
        return
    while True:
        try:
            item = next(it)
        except StopIteration:
            yield IterableItemInfo(i, True), prev
            return
        yield IterableItemInfo(i, False), prev
        prev = item
        i += 1


# see: https://stackoverflow.com/a/31746873/9935708
def base36_encode(num: int) -> str:
    """Converts a positive integer into a base36 string."""
    if num < 0:
        return ''
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    res = ''
    while not res or num > 0:
        num, i = divmod(num, 36)
        res = digits[i] + res
    return res


class ValidString(str):
    """String with valid flag (false if string is invalid)."""

    _valid: bool

    @overload
    def __new__(cls, object: object = '', *, valid: bool = True) -> Self: ...

    @overload
    def __new__(cls, object: ReadableBuffer, encoding: str = 'utf-8', errors: str = 'strict', *, valid: bool = True) -> Self: ...

    def __new__(cls, object: object = '', encoding: str = 'utf-8', errors: str = 'strict', *, valid: bool = True) -> Self:
        if type(object) is ValidString:
            return object  # type: ignore[returnValue]
        obj = super().__new__(cls, object, encoding, errors)  # type: ignore[call-arg]
        obj._valid = valid
        return obj

    def __bool__(self) -> bool:
        return self._valid and len(self) > 0

    @property
    def valid(self) -> bool:
        """Is string valid."""
        return self._valid


class InvalidString(str):
    """String is always invalid (false)."""

    def __bool__(self) -> bool:
        return False

    @property
    def valid(self) -> bool:
        """Is string valid."""
        return False


# --- DEBUG & TESTS ---


if __name__ == '__main__':
    print(list(dump_obj_gets({'xxx': 123})))
    from contextlib import contextmanager
    from typing import get_args

    @contextmanager
    def assert_raises(*exceptions):
        try:
            yield
        except BaseException as exc:
            if version_info >= (3, 11):
                if issubclass(exc.__class__, BaseExceptionGroup):
                    exceptions = tuple(s for e in exceptions for s in (get_args(e) or (e,)))
                    match, rest = exc.split(lambda e: issubclass(e.__class__, exceptions))
                    if match:
                        return
            # exceptions = tuple(s for e in exceptions for s in (get_args(e) or (e,)))
            if issubclass(exc.__class__, exceptions):
                return
        raise AssertionError(f'Expected exception: {", ".join(e.__name__ for e in exceptions)}') from None

    @suppress(KeyError)
    def foo(exc):
        raise exc('a')

    foo(KeyError)
    with assert_raises(ValueError):
        foo(ValueError)
    with suppress(KeyError):
        raise KeyError('a')
    with assert_raises(ValueError):
        with suppress(KeyError):
            raise ValueError('a')
    if version_info >= (3, 11):
        with suppress(KeyError):
            raise ExceptionGroup('x', [KeyError('a')])
        with suppress(KeyError, ValueError):
            raise ExceptionGroup('x', [KeyError('a'), ValueError('a')])
        with assert_raises(ExceptionGroup[ValueError]):
            with suppress(KeyError):
                raise ExceptionGroup('x', [KeyError('a'), ValueError('a')])
        with suppress(ExceptionGroup):
            raise ExceptionGroup('x', [KeyError('a')])

    assert dict_diff_new({1: 2, 3: 4, 5: 6}, {1: 2, 8: 9, 5: 7}) == {8: 9, 5: 7}
    assert dict_diff_new({0: {1: 2, 3: 4, 5: 6}}, {0: {1: 2, 8: 9, 5: 7}}) == {0: {8: 9, 5: 7}}
    assert dict_diff_new({0: {1: 2, 3: 4, 5: 6}}, {0: {1: 2, 8: 9, 5: 7}}, recursion=False) == {0: {1: 2, 8: 9, 5: 7}}
