
from typing import Optional, Union, Iterator, Sequence, TYPE_CHECKING
from typing_extensions import TypeAlias, Literal
from contextlib import contextmanager
from sqlite3 import Cursor
from pathlib import Path
import json as json_mod
import re

from .db import db_manager, db_create_columns, Lock, update_db_version, PrimaryKey, MISSING
from .orm import OrmDatabase, DbTable, DbCursor, select, delete, AND, OR
from ..log_utils import fflog, fflog_exc
from ..calendar import utc_timestamp
from ..item import FFItem
from ..tricks import MissingType, JsonEncoder
from ..types import JsonData
from ...defs import SearchType, MediaRef, XMediaRef, FFRef
from const import const

if TYPE_CHECKING:
    from typing import overload
    from .orm import Expr as SqlExpr


SourceEditShowGranularity: TypeAlias = Literal['show', 'season', 'episode']

#: Search DB version.
DB_VERSION: int = 2


class SearchEntry(DbTable):
    """Single row in search table."""

    __tablename__ = 'search'
    __table_args__ = ('UNIQUE("search_name", "key", "options")',)

    #: The primary DB key.
    id: PrimaryKey = None
    #: What is searched, name of history.
    search_name: str
    #: What is searched, type of media.
    search_type: SearchType
    #: Query text to search.
    query: str
    #: Timestamp for create / update the search.
    updated_at: int
    #: Timestamp for create / update the search.
    last_used_at: int = 0
    #: Extra options (JSON).
    options: str = '{}'
    #: Query key to search (lower-case query).
    key: str = ''

    def __attrs_post_init__(self) -> None:
        """Post-init hook to set the key."""
        if not self.key:
            self.key = re.sub(r'\s+', '', self.query.lower()).strip()


def _migrate_old(cur: DbCursor) -> None:
    """Migrate old (FF2) search entries (search.1.db)."""
    from sqlite3 import connect as db_connect
    from ..control import dataPath

    @contextmanager
    def old_cursor() -> Iterator[Cursor]:
        db = db_connect(path, timeout=1.)
        cur = db.cursor()
        try:
            yield cur
        finally:
            try:
                cur.connection.commit()
            finally:
                cur.close()
            db.close()

    path = Path(dataPath) / 'search.1.db'
    if path.exists():
        try:
            with old_cursor() as old_cur:
                yesterday = int(utc_timestamp()) - 86400  # a day ago
                for stype, old_tab in (('movie', 'movies'), ('show', 'tvshow')):
                    old_cur.execute(f'SELECT term FROM {old_tab} ORDER BY id DESC')
                    for i, row in enumerate(old_cur.fetchall()):
                        term = row[0]
                        cur.add(SearchEntry(search_name=stype, search_type=stype, query=term, updated_at=yesterday - i, last_used_at=yesterday-i))
        except Exception:
            fflog('Migrate old search history FAILED')
            fflog_exc()
        else:
            target = Path(dataPath) / 'search.ff2.db'
            try:
                path.replace(target)
            except OSError as exc:
                fflog(f'Failed to rename old search history file {target}: {exc}')


# -----


def get_search_history_by_type(type: SearchType) -> Sequence[SearchEntry]:
    """Get search list."""
    with db.cursor() as cur:
        return cur.exec(select(SearchEntry).where(SearchEntry.search_type == type).order_by('last_used_at DESC')).all()


def get_search_history(name: str) -> Sequence[SearchEntry]:
    """Get search list."""
    with db.cursor() as cur:
        return cur.exec(select(SearchEntry).where(SearchEntry.search_name == name).order_by('last_used_at DESC')).all()


def get_search_item(id: int, *, name: Optional[str] = None) -> Optional[SearchEntry]:
    """Get search list."""
    with db.cursor() as cur:
        entry = cur.get(SearchEntry, id)
        if entry and name == entry.search_name:
            return entry
        return None


def set_search_item(name: str,
                    type: SearchType,
                    query: str,
                    *,
                    options: Optional[JsonData] = None,
                    id: Optional[int] = None,
                    ) -> int:
    """Set (update or insert) search item. Return ID. If `id` is not None, update item `id`."""
    now = int(utc_timestamp())
    if options is None:
        options = {}
    opt = json_mod.dumps(options, cls=JsonEncoder)
    key = re.sub(r'\s+', ' ', query.lower()).strip()
    with db.cursor() as cur:
        if id is None:
            entry = cur.exec(select(SearchEntry).where(AND(
                SearchEntry.search_name == name,
                SearchEntry.search_type == type,
                SearchEntry.key == key,
                SearchEntry.options == opt))).first()
        else:
            # re-select id to check type
            entry = cur.exec(select(SearchEntry).where(AND(SearchEntry.search_name == name, SearchEntry.id == id))).first()
        if not entry:
            entry = SearchEntry(search_name=name, search_type=type, key=key, query=query, updated_at=now, options=opt)
        entry.query = query
        entry.updated_at = now
        entry.last_used_at = now
        entry.options = opt
        cur.add(entry)
        cur.commit()
        if const.indexer.search.limit:
            cur.execute(('DELETE FROM search WHERE ROWID IN '
                         '(SELECT ROWID FROM search WHERE search_type == ? ORDER BY last_used_at DESC LIMIT -1 OFFSET ?)'),
                        (type, const.indexer.search.limit))
    assert entry.id
    return entry.id


def remove_search_item(id: int) -> None:
    """Remove search list."""
    with db.cursor() as cur:
        cur.exec(delete(SearchEntry).where(SearchEntry.id == id))
        # cur.execute(f'DELETE FROM {SearchRow.__table__} WHERE id = ?', (id,))


def remove_search_history_by_type(type: Optional[SearchType]) -> None:
    """Remove search history."""
    with db.cursor() as cur:
        if type:
            cur.exec(delete(SearchEntry).where(SearchEntry.search_type == type))
            # cur.execute(f'DELETE FROM {SearchRow.__table__} WHERE search_type = ?', (type,))
        else:
            cur.exec(delete(SearchEntry))
            # cur.execute(f'DELETE FROM {SearchRow.__table__}')


def remove_search_history(name: Optional[str]) -> None:
    """Remove search history."""
    with db.cursor() as cur:
        if name:
            cur.exec(delete(SearchEntry).where(SearchEntry.search_name == name))
        else:
            cur.exec(delete(SearchEntry))


def touch_search_item(id: int) -> None:
    """Touches seartch item, updates `last_used_at`."""
    with db.cursor() as cur:
        entry = cur.get(SearchEntry, id)
        if entry:
            entry.last_used_at = int(utc_timestamp())
            cur.add(entry)


# ------------------------------------------------------------------------------
# Source edit tunning DB
# ------------------------------------------------------------------------------


class SourceEditEntry(DbTable):
    """Single row in source_edit table."""

    __tablename__ = 'source_edit'
    __table_args__ = ('UNIQUE("xref::type", "xref::ffid", "xref::season", "xref::episode")',)

    #: The primary DB key.
    id: PrimaryKey = None
    #: Media reference.
    xref: XMediaRef = MISSING
    #: Title (for quick listing).
    title: str = ''
    #: English title (for quick listing).
    english_title: str = ''
    #: Year (for quick listing).
    year: int = 0
    #: Modified data (like title, year, etc) in JSON.
    json_string: str = '{}'
    #: Timestamp for create / update the entry.
    updated_at: int = 0

    _ref: Optional[MediaRef] = None
    _data: Optional[JsonData] = None

    def __init__(self,
                 *,
                 id: PrimaryKey = None,
                 ref: MediaRef = MISSING,
                 xref: Union[XMediaRef, MissingType] = MISSING,
                 data: Union[JsonData, MissingType] = MISSING,
                 json_string: Union[str, MissingType] = MISSING,
                 title: str = '',
                 english_title: str = '',
                 year: int = 0,
                 updated_at: int = 0,
                 ) -> None:

        if xref is MISSING:
            xref = ref  # type: ignore  (hack)
        elif ref is not MISSING:
            raise ValueError('cannot specify both `ref` and `xref`')
        if xref is MISSING:
            raise ValueError('have to specify one of `ref` or `xref`')

        if json_string is MISSING and data is MISSING:
            raise ValueError('have to specify one of `data` or `json_string`')
        if data is not MISSING:
            json_string = json_mod.dumps(data, cls=JsonEncoder)
        elif json_string is not MISSING:
            data = None
        else:
            raise ValueError('cannot specify both `data` and `json_string`')

        self.id = id
        self.xref = xref.sql_ref      # type: ignore  (hack, XMediaRef = MediaRef)
        self.json_string = json_string
        self.title = title
        self.english_title = english_title
        self.year = year
        self.updated_at = updated_at or int(utc_timestamp())
        self._ref = None
        self._data = data

    @classmethod
    def from_ffitem(cls, item: 'FFItem', *, data: Optional[JsonData] = None) -> 'SourceEditEntry':
        """Create `SourceEditEntry` from `FFItem`."""
        ref = item.ref
        if ref.type == 'show':
            en_title = item.vtag.getEnglishTvShowTitle()
        else:
            en_title = item.title
        if data is None:
            data = {}
        else:
            data = dict(data)  # copy
        return cls(ref=ref, data=data, title=item.title, english_title=en_title, year=item.year or 0)

    @property
    def ref(self) -> MediaRef:
        """Return media reference."""
        if self._ref is None:
            self._ref = MediaRef.from_sql_ref(self.xref)
        return self._ref

    @ref.setter
    def ref(self, ref: MediaRef) -> None:
        self._ref = ref
        self.xref = ref.sql_ref  # type: ignore  (hack)

    @property
    def data(self) -> JsonData:
        """Return JSON data."""
        if self._data is None:
            self._data = json_mod.loads(self.json_string)
            if TYPE_CHECKING:
                assert isinstance(self._data, dict)
            # fix keys (seasons are int)
            if episode_offset := self._data.get('episode_offset'):
                episode_offset = {k or None: v for k, v in episode_offset.items()}
                for grp, ss in episode_offset.items():
                    episode_offset[grp] = {int(k): v for k, v in ss.items()}
                self._data['episode_offset'] = episode_offset
        return self._data

    @data.setter
    def data(self, data: JsonData) -> None:
        self._data = data
        if episode_offset := self._data.get('episode_offset'):
            self._data['episode_offset'] = {k or '': v for k, v in episode_offset.items()}
        self.json_string = json_mod.dumps(data, cls=JsonEncoder)


class SourcesEditDb:
    """Source edit DB manager."""

    def __init__(self, *, db: Optional[OrmDatabase] = None) -> None:
        if db is None:
            db = globals()['db']
            assert db is not None
        self.db: OrmDatabase = db
        SourceEditEntry.model_init()

    @staticmethod
    def _granularity_ref(ref: MediaRef, *, granularity: Optional[SourceEditShowGranularity] = None) -> MediaRef:
        """Return granularity reference for `ref`."""
        if ref.type == 'show':
            if granularity is None:
                granularity = const.sources_dialog.edit_search.show_granularity
            if granularity == 'episode':
                pass
            elif granularity == 'season':
                ref = ref.season_ref or ref
            else:
                ref = ref.show_ref or ref
        return ref

    @staticmethod
    def _where(ref: MediaRef) -> 'Iterator[SqlExpr]':
        if ref.type == 'show':
            # show
            # fflog(f'{dir(SourceEditEntry) = }')
            yield AND(SourceEditEntry.xref.type == ref.sql_ref.type,
                      SourceEditEntry.xref.ffid == ref.sql_ref.ffid)
            # season
            if ref.season is not None:
                yield AND(SourceEditEntry.xref.type == ref.sql_ref.type,
                          SourceEditEntry.xref.ffid == ref.sql_ref.ffid,
                          SourceEditEntry.xref.season == ref.sql_ref.season)
            # episode
            if ref.episode is not None:
                yield SourceEditEntry.xref == ref.sql_ref  # type: ignore[reportReturnType]  -- ORM hack, it is SqlExpr already
        else:
            # movie or other
            yield SourceEditEntry.xref == ref.sql_ref  # type: ignore[reportReturnType]  -- ORM hack, it is SqlExpr already

    def get(self, ref: FFRef, *, granularity: Optional[SourceEditShowGranularity] = None) -> Optional[SourceEditEntry]:
        """Get source edit entry for `ref`."""
        ref = self._granularity_ref(ref.ref, granularity=granularity)
        where = OR(*self._where(ref))
        with self.db.cursor() as cur:
            return cur.exec(select(SourceEditEntry).where(where).order_by('updated_at DESC')).first()

    def set(self, item: FFRef, data: JsonData) -> int:
        """Update or insert source edit entry for `ref`. Return ID."""
        now = int(utc_timestamp())
        ref = self._granularity_ref(item.ref)
        with self.db.cursor() as cur:
            entry = cur.exec(select(SourceEditEntry).where(SourceEditEntry.xref == ref.sql_ref)).first()
            if not entry:
                entry = SourceEditEntry(ref=ref, data=data)
            if isinstance(item, FFItem):
                if ref.type == 'show':
                    entry.english_title = item.vtag.getEnglishTvShowTitle()
                    granularity = const.sources_dialog.edit_search.show_granularity
                    if granularity == 'season' and (parent := item.season_item):
                        item = parent
                    elif granularity == 'show' and (parent := item.show_item):
                        item = parent
                else:
                    entry.english_title = item.title
                entry.title = item.title
                entry.year = item.year or 0
            entry.data = data
            entry.updated_at = now
            cur.add(entry)
            cur.commit()
        assert entry.id
        return entry.id

    def delete(self, ref: FFRef) -> None:
        """Delete source edit entry for `ref`."""
        ref = self._granularity_ref(ref.ref)
        where = OR(*self._where(ref))
        with self.db.cursor() as cur:
            cur.exec(delete(SourceEditEntry).where(where))

    def clear(self) -> None:
        """Delete all source edit entries."""
        with self.db.cursor() as cur:
            cur.exec(delete(SourceEditEntry))
        with self.db.cursor() as cur:
            cur.vacuum()

    def list_ffitems(self) -> Sequence[FFItem]:
        """List all source edit entries."""
        def new(entry: SourceEditEntry) -> FFItem:
            ref = self._granularity_ref(entry.ref)
            item = FFItem(ref)
            item.title = entry.title
            label = entry.title
            if entry.year:
                item.vtag.setYear(entry.year)
                label += f' ({entry.year})'
            item.label = label
            item.vtag.setEnglishTitle(entry.english_title)
            if ref.type == 'show':
                item.vtag.setEnglishTvShowTitle(entry.english_title)
            return item

        with self.db.cursor() as cur:
            res = (new(entry) for entry in cur.exec(select(SourceEditEntry).order_by('title ASC, year DESC')).all())
            return list({res.ref: res for res in res}.values())  # unique by ref


# ------------------------------------------------------------------------------

#: Global state DB.
db = OrmDatabase('search', SearchEntry, SourceEditEntry, version=DB_VERSION, on_create=_migrate_old)
# db: OrmDatabase
# _db: Optional[OrmDatabase] = None

#: Global state for `SourcesEditDb` instance.
sources_edit_db: SourcesEditDb
_sources_edit_db: Optional[SourcesEditDb] = None

# if TYPE_CHECKING:
#     @overload
#     def __getattr__(name: Literal['db']) -> OrmDatabase: ...
#     @overload
#     def __getattr__(name: Literal['sources_edit_db']) -> SourcesEditDb: ...


def __getattr__(name: str):
    """Lazy initialization of global state."""

    # if name == 'db':
    #     global _db
    #     if _db is None:
    #         _db = OrmDatabase('search', SearchEntry, SourceEditEntry, version=DB_VERSION, on_create=_migrate_old)
    #     return _db

    if name == 'sources_edit_db':
        global _sources_edit_db
        if _sources_edit_db is None:
            _sources_edit_db = SourcesEditDb()
        return _sources_edit_db

    raise AttributeError(f'module {__name__} has no attribute {name}')


# with db.cursor() as cur:
#     cur.exec(select(SearchEntry))
# SourceEditEntry.model_init()
# print(f'{SourceEditEntry._fields_info = }')
# print(f'{SourceEditEntry.xref = }')
# print(SourceEditEntry.xref.type == 'movie')
