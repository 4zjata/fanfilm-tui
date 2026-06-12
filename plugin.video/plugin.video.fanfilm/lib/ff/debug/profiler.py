
from __future__ import annotations
from typing import Any, Callable, overload, TYPE_CHECKING
from typing_extensions import TypedDict, NotRequired, Unpack
import atexit
from threading import Lock
from .timing import P, R, WithDescrProtocol
from ...ff.log_utils import fflog as _fflog
from ...ff.log_utils import LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL  # noqa: F401
if TYPE_CHECKING:
    from typing_extensions import TextIO, Literal, Iterable, Awaitable, ClassVar
    # see https://docs.python.org/3/library/profile.html#pstats.Stats.sort_stats
    SortBy = Literal['calls', 'cumulative', 'cumtime', 'file', 'filename', 'module', 'ncalls', 'pcalls', 'line', 'name', 'nfl', 'stdname', 'time', 'tottime']


class ProfilerOptions(TypedDict):
    file: NotRequired[TextIO | str | None]
    fflog: NotRequired[int]
    sort_by: NotRequired[Iterable[SortBy] | SortBy | None]
    builtins: NotRequired[bool]
    cumulative: NotRequired[bool]


class Profiler:

    # Keep track of all cumulative profiler instances
    _cumulative_instances: ClassVar[set[Profiler]] = set()

    def __init__(self, *,
                 name: str | None = None,
                 file: TextIO | str | None = None,
                 fflog: int = LOGINFO,
                 sort_by: Iterable[SortBy] | SortBy | None = None,
                 builtins: bool = False,
                 cumulative: bool = False,
                 ) -> None:
        import cProfile
        self.name: str | None = name
        self.file: TextIO | str | None = file
        self.fflog: int = fflog
        self._lock = Lock()
        self._enabled_level: int = 0
        self._profiler = cProfile.Profile(builtins=builtins)
        self.sort_by: list[SortBy] = [
            'ncalls',
            'tottime',
            'cumtime',
        ] if sort_by is None else [sort_by] if isinstance(sort_by, str) else list(sort_by)
        self.cumulative: bool = cumulative
        if self.cumulative:
            Profiler._cumulative_instances.add(self)

    @overload
    def __call__(self, wrapped: Callable[P, R]) -> Callable[P, R]: ...

    @overload
    def __call__(self, wrapped: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]: ...

    def __call__(self, wrapped: Callable[P, R]) -> Callable[P, R]:
        from inspect import iscoroutinefunction
        from functools import update_wrapper

        async def awrapper(*args: P.args, **kwargs: P.kwargs) -> Awaitable[R]:
            """Log async call time consumption."""
            try:
                result = await wrapped(*args, **kwargs)
            finally:
                self.log(f'async call {wrapped.__qualname__}()')
            return result

        def swrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Log call time consumption."""
            try:
                # print(f'{self}: lvl = {self._enabled_level=} ±1')
                result = self._profiler.runcall(wrapped, *args, **kwargs)
            finally:
                self.log(f'call {wrapped.__qualname__}()')
            return result

        if iscoroutinefunction(wrapped):
            wrapper = awrapper
        else:
            wrapper = swrapper
        update_wrapper(wrapper, wrapped)
        return wrapper

    def _enter(self):
        with self._lock:
            # print(f'{self}: lvl = {self._enabled_level=} +1')
            self._enabled_level += 1
            if self._enabled_level == 1:
                self._profiler.enable()

    def _exit(self):
        with self._lock:
            if self._enabled_level > 0:
                # print(f'{self}: lvl = {self._enabled_level=} -1')
                self._enabled_level -= 1
                if self._enabled_level == 0:
                    self._profiler.disable()

    def __enter__(self):
        self._enter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._exit()
        self.log('with statement')

    async def __aenter__(self):
        self._enter()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self._exit()
        self.log('async with statement')

    def log(self, label: str, *, final: bool = False) -> None:
        if self.cumulative and not final:
            if self.name is None:
                self.name = label
            return

        from pstats import Stats
        from io import StringIO
        if self.name is not None:
            label = self.name
        if self.file is None:
            stream = StringIO()
        elif isinstance(self.file, str):
            stream = StringIO()
            _fflog(f'{label} profile in file {self.file}', level=self.fflog)
        else:
            stream = self.file
            _fflog(f'{label} profile in stream {self.file}', level=self.fflog)
            print(f'{label} profile ...', file=stream)
        stats = Stats(self._profiler, stream=stream)
        stats.sort_stats(*self.sort_by)
        stats.print_stats()
        if self.file is None:
            if TYPE_CHECKING:
                assert isinstance(stream, StringIO)
            _fflog(f'{label} profile ...\n{stream.getvalue()}', level=self.fflog)
        elif isinstance(self.file, str):
            if TYPE_CHECKING:
                assert isinstance(stream, StringIO)
            _fflog(stream.getvalue(), level=self.fflog)
            with open(self.file, 'a') as f:
                f.write(f'{label} profile ...\n{stream.getvalue()}')

    def finish(self) -> None:
        """Finish profiling and output the results."""
        self.log(self.name or 'final', final=True)
        Profiler._cumulative_instances.discard(self)

    @classmethod
    def close_all(cls) -> None:
        """Finish all cumulative profiler instances."""
        cumulative_instances = Profiler._cumulative_instances
        Profiler._cumulative_instances = set()
        for instance in cumulative_instances:
            instance.finish()

    @staticmethod
    def _at_exit_handler():
        Profiler.close_all()


@overload
def profiler(wrapped: Callable[P, R], /, *, name: str | None = None, **kwargs: Unpack[ProfilerOptions]) -> Callable[P, R]: ...


@overload
def profiler(label: str, /, **kwargs: Unpack[ProfilerOptions]) -> WithDescrProtocol: ...


@overload
def profiler(wrapped: Literal[None] = None, /, *, name: str | None = None, **kwargs: Unpack[ProfilerOptions]) -> WithDescrProtocol: ...


def profiler(wrapped_or_label: Callable[P, R] | str | None = None, /, *, name: str | None = None, **kwargs: Unpack[ProfilerOptions]) -> Any:
    """
    A profiler decorator and context manager.

    Can be used as a decorator to profile function call time consumption,
    or as a context manager to profile a code block.

    Args:
        name: An optional name to identify the profiled block.
        file: The output file for profiling results. If None, uses fflog.
        fflog: The log level for fflog. If negative, prints to the specified file.
    """
    if isinstance(wrapped_or_label, str):
        if name is None:
            name = wrapped_or_label
        wrapped_or_label = None
    obj = Profiler(name=name, **kwargs)
    if wrapped_or_label is None:   # with statement or decorator with arguments
        return obj
    return obj(wrapped_or_label)   # decorator without arguments


atexit.register(Profiler._at_exit_handler)


# Example usage
if __name__ == '__main__':
    import time

    @profiler
    def test_function():
        time.sleep(0.2)

    test_function()

    def do_sleep():
        time.sleep(0.1)

    with profiler('Test Block', builtins=True):
        do_sleep()
