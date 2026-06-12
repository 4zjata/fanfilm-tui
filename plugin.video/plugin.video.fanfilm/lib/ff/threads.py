"""
Our wrapper for threading.Thread.

Adds result value, on_finished callbacks (rx. to clean DB connection).
"""

from __future__ import annotations
from sys import version_info as PY
from time import monotonic
from threading import Thread as _Thread, local as _local, Event as _Event
from threading import Lock, RLock, Condition, current_thread   # noqa: F401  (simulate threading import)
import queue
from contextlib import contextmanager
from weakref import WeakKeyDictionary
from typing import Optional, Any, Set, Iterable, Iterator, Mapping, Callable, TypeVar, TYPE_CHECKING
from typing_extensions import Self, TypeAlias, Generic
if PY >= (3, 13):
    from queue import ShutDown  # since python 3.13
else:
    class ShutDown(Exception):
        '''Raised when put/get with shut-down queue.'''

from xbmc import Monitor
from const import const

if TYPE_CHECKING:
    from typing_extensions import ClassVar, Type
    from .kotools import ExitBaseException

T = TypeVar('T')

ThreadOnFinished: TypeAlias = Callable[[], None]

# Safe future timestamp (signed 32-bit minus more then one day).
MAX_TIMESTAMP = 2**31 - 100000


class ThreadCanceled(BaseException):
    """Thread method should be canceled. BaseException to avoid `except Exception:` everywhere."""


class local(_local):
    """threading.local wrapper."""


class ThreadSingleLocal(local):
    """Thread wide threading.local data, singleton per thread."""

    _instances: ClassVar[WeakKeyDictionary[_Thread, ThreadSingleLocal]] = WeakKeyDictionary()
    _lock: ClassVar[Lock] = Lock()

    def __new__(cls: type[Self]) -> ThreadSingleLocal:
        """Create a new instance of ThreadLocal, singleton per thread."""
        with ThreadSingleLocal._lock:
            th = current_thread()
            data = cls._instances.get(th)
            if data is None:
                data = super().__new__(cls)
                cls._instances[th] = data
        return data


class Event(_Event):
    """threading.Event wrapper. Honore Kodi exit."""

    def wait(self, timeout: Optional[float] = None, *, monitor: Optional[Monitor] = None) -> bool:
        """Block until the internal flag is true. See: threading.Event."""
        if monitor is None:
            monitor = Monitor()
        end = MAX_TIMESTAMP if timeout is None else monotonic() + timeout
        while (delta := end - monotonic()) > 0 and not monitor.abortRequested():
            if super().wait(min(delta, const.tune.event_step)):
                return True
        return False
        # while not self.is_set() and (delta := end - monotonic()) > 0:
        #     xsleep(delta, cancel_event=self)
        # return self.is_set()


class PriorityQueue(queue.PriorityQueue, Generic[T]):
    """threading.PriorityQueue wrapper, allows preview first item (peek)."""

    def peek(self) -> T | None:
        """Return the first item in the queue without removing it."""
        with self.mutex:
            if self.queue:
                return self.queue[0]

    def remove(self, item: T, /) -> bool:
        """Remove item."""
        with self.mutex:
            try:
                self.queue.remove(item)
            except ValueError:
                return False
        self.task_done()
        return True

    def __contains__(self, item: T, /) -> bool:
        """Return True if item in the queue."""
        with self.mutex:
            return item in self.queue


class Thread(_Thread, Generic[T]):
    """threading.Thread wrapper. Keeps result."""

    if TYPE_CHECKING:
        _target: Callable[..., T] | None
        _args: tuple[Any, ...]
        _kwargs: dict[str, Any]

    def __init_subclass__(cls, /, **kwargs) -> None:
        """Override __init_subclass__ to ensure that run() is overridden."""
        # force override run() to catch return value
        def run(self: Self) -> None:
            try:
                self.result = cls_run(self)
            except Exception:
                from .log_utils import log_exc
                log_exc(internal=True)
            for cb in self.on_finished:
                cb()

        super().__init_subclass__(**kwargs)
        cls_run = cls.run
        cls.run = run  # type: ignore[method-assign]

    def __init__(self,
                 group: None = None,
                 target: Optional[Callable[..., Any]] = None,
                 name: Optional[str] = None,
                 args: Iterable[Any] = (),
                 kwargs: Mapping[str, Any] | None = None,
                 *,
                 daemon: Optional[bool] = None,
                 on_finished: Optional[ThreadOnFinished] = None,
                 ) -> None:
        super().__init__(group=group, target=target, name=name, args=args, kwargs=kwargs, daemon=daemon)
        if PY < (3, 10):
            if name is None and callable(target):
                self._name = f'{self._name} ({target.__name__})'
        self._local: Optional[local] = None
        self.stop_event = Event()
        self.result: T | None = None
        self.exception: BaseException | None = None
        self.on_finished: Set[ThreadOnFinished] = set()
        if on_finished is not None:
            self.on_finished.add(on_finished)

    # taken from PY (3.8-3.12)
    def run(self) -> T:  # type: ignore[override]
        """
        Method representing the thread's activity.

        You may override this method in a subclass. The standard run() method
        invokes the callable object passed to the object's constructor as the
        target argument, if any, with sequential and keyword arguments taken
        from the args and kwargs arguments, respectively.
        """
        try:
            if self.stop_event.is_set():
                raise ThreadCanceled()
            if self._target is not None:
                return self._target(*self._args, **self._kwargs)
        except ThreadCanceled:
            from .log_utils import fflog
            fflog(f'Thread {self.name} graceful canceled', internal=True)
        except Exception as exc:
            from .log_utils import fflog, fflog_exc
            fflog(f'Thread {self.name} raises an exception: {exc}', internal=True)
            self.exception = exc
            if const.debug.log_exception:
                fflog_exc(internal=True)
            raise
        finally:
            # Avoid a refcycle if the thread is running a function with
            # an argument that has a member that points to the thread.
            del self._target, self._args, self._kwargs

    @property
    def local(self) -> local:
        """Returns threading.local() variables as class local instance."""
        if self._local is None:
            self._local = local()
        return self._local

    def stop(self):
        """Safe stop of the thread. Works only if the thread checks for stop requests by check_thread_cancel(), like ff.requests, ff.cache do."""
        self.stop_event.set()

    @property
    def is_canceled(self) -> bool:
        """Return True if the thread is canceled."""
        return self.stop_event.is_set()


class WeakThread(Thread):
    """Helper thread, abort on script exit, do not count in standard threads."""


def check_thread_cancel() -> None:
    """Check if the current thread should be canceled. Raise ThreadCanceled if so."""
    if (event := getattr(current_thread(), 'stop_event', None)) is not None and event.is_set():
        raise ThreadCanceled()


if PY < (3, 9):
    # Fake typing for queue.Queue in python < 3.9
    queue.Queue.__class_getitem__ = classmethod(lambda cls, item: cls)  # type: ignore[reportAttributeAccessIssue]


# Backport from Python 3.13 + clear() + custom shutdown exception.
# See: https://stackoverflow.com/a/31892187/9935708
class Queue(queue.Queue[T], Generic[T]):
    '''Create a queue object with a given maximum size, provides a :meth:`clear` method and support KodiExit.

    If maxsize is <= 0, the queue size is infinite.
    '''

    WAITING_QUEUES: ClassVar[dict[Queue[Any], int]] = {}
    WAITING_LOCK: ClassVar[Lock] = Lock()

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize)
        if PY < (3, 13):
            self.is_shutdown = False
        self._shutdown_exception: type[BaseException] = ShutDown

    @contextmanager
    def waiting_queue(self) -> Iterator[None]:
        """Context manager to track waiting threads on this queue."""
        cls = self.__class__
        with cls.WAITING_LOCK:
            cls.WAITING_QUEUES[self] = cls.WAITING_QUEUES.get(self, 0) + 1
        try:
            yield
        finally:
            with cls.WAITING_LOCK:
                wait_counter = cls.WAITING_QUEUES.get(self, 0)
                if wait_counter > 1:
                    cls.WAITING_QUEUES[self] = wait_counter - 1
                else:
                    cls.WAITING_QUEUES.pop(self, None)

    # taken from py3.13
    def put(self, item: T, block: bool = True, timeout: float | None = None) -> None:
        '''Put an item into the queue.

        If optional args 'block' is true and 'timeout' is None (the default),
        block if necessary until a free slot is available. If 'timeout' is
        a non-negative number, it blocks at most 'timeout' seconds and raises
        the Full exception if no free slot was available within that time.
        Otherwise ('block' is false), put an item on the queue if a free slot
        is immediately available, else raise the Full exception ('timeout'
        is ignored in that case).

        Raises ShutDown if the queue has been shut down.
        '''
        with self.not_full:
            if self.is_shutdown:
                raise self._shutdown_exception
            if self.maxsize > 0:
                if not block:
                    if self._qsize() >= self.maxsize:
                        raise queue.Full
                elif timeout is None:
                    while self._qsize() >= self.maxsize:
                        with self.waiting_queue():
                            self.not_full.wait()
                        if self.is_shutdown:
                            raise self._shutdown_exception
                elif timeout < 0:
                    raise ValueError("'timeout' must be a non-negative number")
                else:
                    endtime = monotonic() + timeout
                    while self._qsize() >= self.maxsize:
                        remaining = endtime - monotonic()
                        if remaining <= 0.0:
                            raise queue.Full
                        with self.waiting_queue():
                            self.not_full.wait(remaining)
                        if self.is_shutdown:
                            raise self._shutdown_exception
            self._put(item)
            self.unfinished_tasks += 1
            self.not_empty.notify()

    # taken from py3.13
    def get(self, block: bool = True, timeout: float | None = None) -> T:
        '''Remove and return an item from the queue.

        If optional args 'block' is true and 'timeout' is None (the default),
        block if necessary until an item is available. If 'timeout' is
        a non-negative number, it blocks at most 'timeout' seconds and raises
        the Empty exception if no item was available within that time.
        Otherwise ('block' is false), return an item if one is immediately
        available, else raise the Empty exception ('timeout' is ignored
        in that case).

        Raises ShutDown if the queue has been shut down and is empty,
        or if the queue has been shut down immediately.
        '''
        with self.not_empty:
            if self.is_shutdown and not self._qsize():
                raise self._shutdown_exception
            if not block:
                if not self._qsize():
                    raise queue.Empty
            elif timeout is None:
                while not self._qsize():
                    with self.waiting_queue():
                        self.not_empty.wait()
                    if self.is_shutdown and not self._qsize():
                        raise self._shutdown_exception
            elif timeout < 0:
                raise ValueError("'timeout' must be a non-negative number")
            else:
                endtime = monotonic() + timeout
                while not self._qsize():
                    remaining = endtime - monotonic()
                    if remaining <= 0.0:
                        raise queue.Empty
                    with self.waiting_queue():
                        self.not_empty.wait(remaining)
                    if self.is_shutdown and not self._qsize():
                        raise self._shutdown_exception
            item = self._get()
            self.not_full.notify()
            return item

    # taken from py3.13
    def shutdown(self, immediate: bool = False, *, exception: type[BaseException] | None = None) -> None:
        '''Shut-down the queue, making queue gets and puts raise ShutDown.

        By default, gets will only raise once the queue is empty. Set
        'immediate' to True to make gets raise immediately instead.

        All blocked callers of put() and get() will be unblocked.

        If 'immediate', the queue is drained and unfinished tasks
        is reduced by the number of drained tasks.  If unfinished tasks
        is reduced to zero, callers of Queue.join are unblocked.
        '''
        with self.mutex:
            if exception is not None:
                self._shutdown_exception = exception
            self.is_shutdown = True
            if immediate:
                while self._qsize():
                    self._get()
                    if self.unfinished_tasks > 0:
                        self.unfinished_tasks -= 1
                # release all blocked threads in `join()`
                self.all_tasks_done.notify_all()
            # All getters need to re-check queue-empty to raise ShutDown
            self.not_empty.notify_all()
            self.not_full.notify_all()

    def clear(self):
        """Clears all items from the queue."""
        with self.mutex:
            unfinished = self.unfinished_tasks - len(self.queue)
            if unfinished <= 0:
                if unfinished < 0:
                    raise ValueError('task_done() called too many times')
                self.all_tasks_done.notify_all()
            self.unfinished_tasks = unfinished
            self.queue.clear()
            self.not_full.notify_all()


def xsleep(interval: float,
           *,
           cancel_event: _Event | None = None,
           ) -> bool:
    """Sleep in safe mode. Exit on Kodi exit or module reload. Return True if timer expired, False if cancelled."""
    from .kotools import KodiMonitor, KodiExit, ReloadExit
    from .log_utils import fflog  # XXX
    xmonitor = KodiMonitor.instance()
    # if xmonitor is None:
    #     return  # Monitor is destroying.
    if cancel_event is not None and cancel_event.is_set():
        # cancel_event.clear()  # clear event if set
        return False
    timer = xmonitor.new_timer(interval, event=cancel_event)
    T = monotonic()
    expired = xmonitor.wait(timer)
    if const.debug.log_xsleep_jitter and (jitter := abs((dt := monotonic() - T) - interval)) >= const.debug.log_xsleep_jitter:
        fflog(f'[KOTOOLS]  xsleep({interval}) {jitter:.3f} mismatch, finished after {dt:.3f} seconds, {timer=} (ev={timer.event.is_set()}),'
              f' {cancel_event=} / {cancel_event and cancel_event.is_set()=}', internal=True)
    # timer.event.clear()  # clear event after wait
    if xmonitor and (xmonitor.aborting or xmonitor.abortRequested()):
        raise KodiExit()
    if const.debug.autoreload:
        from ..service.reload import ReloadMonitor
        if ReloadMonitor.reloading:
            raise ReloadExit()
    return expired


def xsleep_until_exit(interval: float) -> Optional[Type[ExitBaseException]]:
    try:
        xsleep(interval)
    except ExitBaseException as exc:
        return type(exc)
    return None


class Timer(Thread, Generic[T]):
    """
    Call a function after a specified number of seconds:

    >>> t = Timer(30.0, f, args=None, kwargs=None)
    >>> t.start()
    >>> t.cancel()     # stop the timer's action if it's still waiting

    Modified Python's version. Keep function result.
    """

    def __init__(self, interval: float, function: Callable[..., T], args=None, kwargs=None) -> None:
        super().__init__()
        self.interval = interval
        self.function = function
        self.args = args if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}
        self.finished = Event()
        self.result: Optional[T] = None

    def cancel(self) -> None:
        """Stop the timer if it hasn't finished yet."""
        self.finished.set()

    def run(self) -> None:
        xsleep(self.interval, cancel_event=self.finished)
        if not self.finished.is_set():
            self.result = self.function(*self.args, **self.kwargs)
        self.finished.set()


if __name__ == '__main__':
    class MT(Thread):
        def run(self):
            loc.__dict__.setdefault('a', 42)
            print(42, id(loc), loc.__dict__)
            return 42

    class A:
        def print44(self, a=1, b=2):
            loc.__dict__.setdefault('a', 44)
            print(f'44: {a=}, {b=}', id(loc), loc.__dict__)
            return 44

    loc = _local()
    t1 = MT()
    t2 = Thread(target=A().print44, args=(1, 2))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    print(t1.name, t2.name)
    print(t1.result, t2.result)
