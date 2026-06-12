"""
Library service, adding in background.
"""

from __future__ import annotations
from typing import Optional, Sequence, Iterable, Callable, TYPE_CHECKING
from attrs import define
from xbmc import executebuiltin
from ..ff.threads import Thread, Event, Queue, ShutDown
from ..ff.libtools import LibTools
from ..ff.calendar import make_datetime, timestamp
from ..ff.log_utils import fflog
from ..ff.settings import settings
from ..ff.control import infoDialog
from .exc import ExitBaseException
from ..kolang import L

from const import const
if TYPE_CHECKING:
    from ..defs import FFRef


@define(kw_only=True)
class Batch:
    """Batch of items to add to library."""
    #: Unique batch ID.
    id: int
    #: Name for batch, used in notifications and logs.
    name: str
    #: Items to add to library.
    items: Sequence[FFRef]
    #: Sync with kodi library after processing batch (update the library):
    #:  True  – force sync,
    #:  False – prevent sync (e.g. used in intermediate chunks),
    #:  None  – sync if empty or sync_every_batch.
    sync: bool | None = None
    #: Skip notifications and progress if True.
    quiet: bool = False
    #: ID of previous chunk batch, if this batch is part of chunked set.
    previous_chunk_id: int | None = None
    #: Optional callback to call after batch is finished, with batch as argument.
    on_finish: Callable[[Batch], None] | None = None


class Library(Thread):
    """Library service, adding in background."""

    def __init__(self) -> None:
        super().__init__(name='Library Service')
        self.library = LibTools()
        self.queue: Queue[Batch] = Queue()
        self.current: Optional[Batch] = None
        self._next_batch_id: int = int(timestamp())
        self.active: bool = False

    def stop(self) -> None:
        """Stop service gracefully."""
        self.active = False
        self.queue.shutdown(immediate=True)

    def generate_batch_id(self) -> int:
        """Generate unique batch id."""
        bid, self._next_batch_id = self._next_batch_id, self._next_batch_id + 1
        return bid

    def add(self,
            items: Iterable[FFRef] | Batch,
            *,
            name: Optional[str] = None,
            sync: bool | None = None,
            quiet: bool = False,
            skip_empty: bool = True,
            chunk: int = 0,
            ) -> int:
        """Add set of items to library. Return new set/batch id."""
        if isinstance(items, Batch):
            self.queue.put_nowait(items)
            return items.id
        items = tuple(items)
        if skip_empty and not items:
            return 0
        if not name:
            name = str(make_datetime(None))
        bid = 0
        if not chunk:
            chunk = len(items)
        for off in range(0, len(items), chunk):
            last = off + chunk >= len(items)
            prev_bid, bid = bid, self.generate_batch_id()
            if last:
                batch = Batch(id=bid, name=name, items=items[off:], sync=sync, quiet=quiet, previous_chunk_id=prev_bid)
            else:
                batch = Batch(id=bid, name=f'{name} ({off // chunk + 1})', items=items[off:off+chunk], sync=False, quiet=True, previous_chunk_id=prev_bid)
            self.queue.put_nowait(batch)
        return bid

    def reload_library(self) -> int:
        """Async reload - send UpdateLibrary(video) request."""
        return self.add((), sync=True, quiet=True)

    def run(self) -> None:
        """Main activity, read batches and process items to libtool."""
        self.active = True
        stop_reason = ''
        fflog('[Library] started')
        try:
            while self.active:
                try:
                    batch = self.queue.get()
                except ExitBaseException as exc:
                    stop_reason = f'({exc})'
                    break
                except ShutDown:
                    break
                if not self.active:
                    break
                fflog(f'[LIB] Start batch {batch.id} ({batch.name}): {len(batch.items)} element(s)')
                if not settings.getBool('library.service.notification'):
                    batch.quiet = True  # force quiet if notifications are not allowd
                self.batch_started(batch)
                self.library.add(batch.items, reload=False)
                empty = self.queue.empty()
                if settings.getBool('library.update') and batch.sync is not False:
                    # sync = True  – force sync,
                    # sync = False – prevent sync (e.g. used in intermediate chunks),
                    # sync = None  – sync if empty or sync_every_batch
                    if empty or const.library.service.sync_every_batch or batch.sync:
                        executebuiltin('UpdateLibrary(video)', const.library.service.sync_wait)
                self.batch_finished(batch)
                fflog(f'[LIB] Batch {batch.id} ({batch.name}) finished')
                self.queue.task_done()
        except BaseException as exc:
            fflog(f'[Library] aborted ({exc})')
        else:
            fflog(f'[Library] stopped {stop_reason}')

    def batch_started(self, batch: Batch) -> None:
        """Start batch notification."""
        self.current = batch  # for info/notification only
        if not batch.quiet:
            infoDialog(L(30322, 'Started'), L(30321, 'Adding to library...'), icon="")

    def batch_finished(self, batch: Batch) -> None:
        """Finish batch notification."""
        self.current = None  # for info/notification only
        if batch.on_finish is not None:
            batch.on_finish(batch)
        if not batch.quiet:
            infoDialog(L(30323, 'Finished'), L(30321, 'Adding to library...'), icon="")
