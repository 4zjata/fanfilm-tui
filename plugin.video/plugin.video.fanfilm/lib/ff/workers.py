# -*- coding: utf-8 -*-

"""
    Fanfilm Add-on

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
from typing import Optional, TYPE_CHECKING
from os import cpu_count
import threading

import xbmc
from .settings import settings
from .threads import ThreadCanceled
from .log_utils import fflog, fflog_exc
from .kotools import xsleep
from const import const

if TYPE_CHECKING:
    from typing import Any, Iterable, Mapping, Callable, ClassVar


class Thread(threading.Thread):

    # Statyczna lista przechowująca wszystkie instancje wątków
    threads: ClassVar[set[Thread]] = set()

    # Ustalanie liczby dostępnych wątków
    threads_count: ClassVar[int] = settings.getInt("threads.count")
    if threads_count > 0:
        available_threads = threads_count
    else:
        available_threads = min(32, (cpu_count() or 1) + 4)  # taken from py 3.8 ThreadPoolExecutor

    if TYPE_CHECKING:
        _target: Callable[..., None]
        _args: tuple[Any]
        _kwargs: dict[str, Any]

    # Ustawienie semafora, który ogranicza liczbę równoczesnych wątków do dostępnej liczby rdzeni
    thread_limiter = threading.Semaphore(available_threads)

    def __init__(self, target, args: Iterable[Any] = (), kwargs: Mapping[str, Any] | None = None, *, name: str | None = None):
        super().__init__(target=target, args=args or (), kwargs=kwargs if kwargs else {}, name=name)
        self.daemon = True
        self.stop_event = threading.Event()
        self.threads.add(self)

    def run(self):
        try:
            if not self._target:
                return
            with self.thread_limiter:
                if xbmc.Monitor().abortRequested():
                    return
                try:
                    self._target(*self._args, **self._kwargs)
                except ThreadCanceled:
                    fflog(f'Thread {self.name} graceful canceled', internal=True)
                    return
                except Exception as exc:
                    fflog(f'Thread {self.name} raises an exception: {exc}', internal=True)
                    if const.dev.sources.log_exception:
                        fflog_exc(internal=True)
                    raise
        finally:
            self.threads.discard(self)

    def stop(self):
        """Safe stop of the thread. Works only if the thread checks for stop requests by check_thread_cancel(), like ff.requests, ff.cache do."""
        self.stop_event.set()

    @classmethod
    def stop_all(cls):
        for thread in cls.threads:
            thread.stop()
