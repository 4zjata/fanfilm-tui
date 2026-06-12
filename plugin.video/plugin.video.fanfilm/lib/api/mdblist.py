
from __future__ import annotations
from typing import Sequence, Mapping, TypeVar, TYPE_CHECKING
from typing_extensions import TypedDict, NotRequired, Literal
from itertools import chain
from wrapt.wrappers import ObjectProxy
from ..ff import requests
from ..ff.types import JsonResult, JsonData
from ..defs import MediaRef, VideoIds, MainMediaType, MediaType, RefType, ItemList
from ..ff.item import FFItem
from ..ff.kotools import xsleep
from ..ff.log_utils import fflog, log
from ..ff.settings import settings
from const import const
if TYPE_CHECKING:
    from typing import Iterator, Iterable
    from typing_extensions import Unpack
    from ..ff.types import KwArgs, PagedItemList
    from ..defs import FFRef


LIST_ID = TypeVar('LIST_ID', bound='int | str')

MediaProvider = Literal['imdb', 'tmdb', 'trakt', 'tvdb', 'mal', 'mdblist']


class MdblistIds(TypedDict):
    imdb: str
    tmdb: int
    trakt: int
    tvdb: int | None
    mal: int | None
    mdblist: str


class MdblistRequestKwargs(TypedDict):
    params: NotRequired[KwArgs | None]
    # page: NotRequired[int | None]
    # limit: NotRequired[int | None]
    errors: NotRequired[str]
    login_required: NotRequired[bool]


if TYPE_CHECKING:
    # class PagesBase(ObjectProxy, JsonData, Sequence[JsonData]):
    from typing_extensions import Generic
    B = TypeVar('B', bound=JsonData | Sequence[JsonData])

    class PagesBase(ObjectProxy, Generic[B]):
        pass
else:
    PagesBase = ObjectProxy


class Pages(PagesBase):
    """A class to handle pagination for mdblist.com API responses."""

    def __init__(self, obj: JsonResult, /, *, total_items: int = 0):
        super().__init__(obj)
        # if not total_items and isinstance(obj, Mapping) and (pagination := obj.get('pagination')):
        #     total_items = int(pagination.get('total_items') or 0)
        self._self_total_items = total_items

    def __repr__(self) -> str:
        return f'Pages({self.__wrapped__!r}, total_items={self._self_total_items})'

    @property
    def total_items(self) -> int:
        """Total number of items in the response."""
        return self._self_total_items


class MdbList:
    """A class to handle the mdblist.com API."""

    def __init__(self):
        self.enabled = const.indexer.mdblist.enabled
        self.api_key = const.dev.mdblist.api_key or settings.getString("mdblist.api_key")
        self.url = 'https://api.mdblist.com/'
        #: Connection session.
        self.sess: requests.Session = requests.Session()
        #: Connection timeout.
        self.timeout: float = const.mdblist.connection.timeout
        #: Number of tries.
        self.try_count: int = const.mdblist.connection.try_count
        #: Delays between tries.
        self.try_delay: float = const.mdblist.connection.try_delay
        #: Premium account status.
        self._premium: bool | None = None

    def is_enabled(self) -> bool:
        """Check if the mdblist.com API is enabled."""
        return self.enabled and bool(self.api_key)

    def is_premium(self) -> bool:
        """Check if the mdblist.com account is premium."""
        if self._premium is None:
            data = self.user_profile()
            if isinstance(data, dict):  # Mapping
                self._premium = bool(data.get('is_supporter', False))
            else:
                self._premium = False
        return self._premium

    @staticmethod
    def _list_id(list_id: LIST_ID) -> LIST_ID:
        if isinstance(list_id, int) and list_id in VideoIds.MDBLIST:
            list_id = VideoIds.MDBLIST.index(list_id)  # convert to real list ID
        return list_id

    def request(self,
                method: str,
                url: str,
                *,
                data: JsonData | None = None,
                params: KwArgs | None = None,
                # page: Optional[int] = None,
                # limit: Optional[int] = None,  # works only with `page`
                errors: str = 'ignore',
                login_required: bool = True,
                ) -> requests.Response | None:
        """Make a request to the mdblist.com API."""
        if '://' not in url:
            url = f'{self.url}{url.lstrip("/")}'
        headers = None
        params = dict(params or ())
        if login_required:
            params['apikey'] = self.api_key
        status_code: int = 0
        resp = None
        for i in range(self.try_count):
            # make the request
            resp_headers = {}
            try:
                resp = self.sess.request(method, url, json=data, params=params, headers=headers, timeout=self.timeout)
                status_code = resp.status_code
                resp_headers = {k: v for k, v in resp.headers.items()}# if 'sort-' in k.lower()}
            except requests.ConnectionError:
                status_code = 0
            except requests.RequestException:
                if errors == 'ignore':
                    status_code = 0
                else:
                    raise
            finally:
                # fflog(f'[MDBLIST] {method} {url} ? {params} < {data!r}  (try: {i+1}) → {status_code}\n  {cred}')
                fflog(f'[MDBLIST] {method} {url} ? {params} < {data!r}  (try: {i+1}) → {status_code}, cred:{bool(self.api_key)}')
                # fflog(f'[MDBLIST]  …  {headers=}')
                fflog(f'[MDBLIST]  …  {resp_headers=}')

            # analyze response
            try_delay = 0
            # with self.auth_lock:
            if 1:
                if status_code in (0, 429):  # Connection Error | Rate Limit Exceeded
                    # See: https://trakt.docs.apiary.io/#introduction/rate-limiting
                    fflog(f'[MDBLIST] Rate limit exceeded {status_code}')
                    try_delay = self.try_delay
                    # TODO, handle response headers
                    # X-Ratelimit: {"name":"UNAUTHED_API_GET_LIMIT","period":300,"limit":1000,"remaining":0,
                    #               "until":"2020-10-10T00:24:00Z"}
                    # Retry-After: 10
                elif status_code == 420:  # If the user's list item limit is exceeded, a 420 HTTP error code is returned
                    log('MdbList list limit exceeded')
                    break
                elif status_code == 502:  # Gateway Error - mdblist.com is not avaliable
                    try_delay = 3 * self.try_delay
                else:
                    # rest: success or error, stop repeating
                    break
            if try_delay:
                xsleep(try_delay)

        if resp is None:
            return None

        # 200: Success
        # 201: Success - new resource created (POST)
        # 204: Success - no content to return (DELETE)
        if 200 <= status_code <= 299:
            return resp

        if status_code >= 500:  # temporary
            fflog(f'[MDBLIST] Temporary error {resp.status_code}')
            return None
        if status_code >= 400:  # permanent
            fflog(f'[MDBLIST] Error {resp.status_code}\n{resp.text}')
            return None

    def _response(self, resp: requests.Response | None) -> Pages | None:
        """Process the response from the mdblist.com API."""
        if resp is None:
            return None
        data = resp.json()
        total_items = int(resp.headers.get('X-Total-Items') or 0)
        return Pages(data, total_items=total_items)

    def get(self, url: str, **kwargs: Unpack[MdblistRequestKwargs]) -> Pages | None:
        """Send GET request to mdblist.com and return JSON."""
        resp = self.request('GET', url, data=None, **kwargs)
        return self._response(resp)

    # def post(self, url: str, data: JsonData, **kwargs: Unpack[MdblistRequestKwargs]) -> Pages | None:
    #     """Send POST request to mdblist.com and return JSON."""
    #     resp = self.request('POST', url, data=data, **kwargs)
    #     return self._response(resp)

    # def put(self, url: str, data: JsonData, **kwargs: Unpack[MdblistRequestKwargs]) -> Pages | None:
    #     """Send PUT request to mdblist.com and return JSON."""
    #     resp = self.request('PUT', url, data=data, **kwargs)
    #     return self._response(resp)

    def post(self, url: str, data: JsonData, **kwargs: Unpack[MdblistRequestKwargs]) -> JsonResult | None:
        """Send POST request to mdblist.com and return JSON."""
        resp = self.request('POST', url, data=data, **kwargs)
        if resp is None:
            return None
        return resp.json()

    def put(self, url: str, data: JsonData, **kwargs: Unpack[MdblistRequestKwargs]) -> Pages | None:
        """Send PUT request to mdblist.com and return JSON."""
        resp = self.request('PUT', url, data=data, **kwargs)
        if resp is None:
            return None
        return resp.json()

    def delete(self, url: str, **kwargs: Unpack[MdblistRequestKwargs]) -> bool:
        """Send DELETE request to mdblist.com and return True if deleted."""
        resp = self.request('DELETE', url, data=None, **kwargs)
        return resp.status_code == 204 if resp else False

    def _parse_lists(self, data: JsonResult | None, *, media: MainMediaType | None = None, static: bool = False) -> Sequence[FFItem]:
        """Parse list of the list from JSON data."""
        def parse(it: JsonData) -> Iterator[FFItem]:
            """Parse a single item."""
            # NOTE: 'mediatype' is always null for static lists (see: https://discord.com/channels/907169977159786516/1377060272698560662)
            media_type = it.get('mediatype')
            if media and media_type and media_type != media:
                return
            if static and it.get('dynamic'):
                return
            ref = MediaRef('list', it['id'] + VideoIds.MDBLIST.start)
            ff = FFItem(ref)
            ff.source_data = it
            ff.label = ff.title = it['name']
            if descr := it.get('description'):
                ff.vtag.setTagLine(descr)
            ff._children_count = it['items']
            # 'likes'
            # 'dynamic'
            # 'private'
            yield ff

        # if not isinstance(data, Sequence):
        if not isinstance(data, list):
            return []
        return tuple(ff for it in data for ff in parse(it))

    def _get_ref(self, it: JsonData, ref_type: RefType | None = None) -> Iterator[MediaRef]:
        if 'movie' in it:
            yield VideoIds.from_ids(it['movie']['ids']).ref('movie')
        elif 'episode' in it:
            d = it['episode']
            vid = VideoIds.from_ids(d['show']['ids'])
            yield MediaRef('show', vid.ffid, season=d['season'], episode=d['number'])
        elif 'season' in it:
            d = it['season']
            vid = VideoIds.from_ids(d['show']['ids'])
            yield MediaRef('show', vid.ffid, season=d['number'])
        elif 'show' in it:
            yield VideoIds.from_ids(it['show']['ids']).ref('show')
        elif 'person' in it:
            yield VideoIds.from_ids(it['person']['ids']).ref('person')
        elif 'list' in it:
            yield VideoIds.from_ids(it['list']['ids']).ref('list')
        elif ref_type is not None:
            iid = it.get('ids', {}).get('trakt')
            if iid:
                yield MediaRef(ref_type, iid)

    def _parse_items(self, data: Pages | None, *, media: MediaType | None = None, page: int = 1, page_size: int | None = None) -> ItemList[FFItem]:
        """Parse list items from JSON data."""
        def parse(it: JsonData) -> FFItem | None:
            """Parse a single item."""
            season = episode = None
            if iid := it.get('id'):
                media: Literal['movie', 'show', 'season', 'episode'] = it['mediatype']
                if media == 'season':
                    iid = it['show_id']
                    season = it['season_number']
                    media = 'show'
                elif media == 'episode':
                    iid = it['show_id']
                    season = it['season_number']
                    episode = it['episode_number']
                    media = 'show'
                ref = MediaRef.from_tmdb(media, iid, season=season, episode=episode)
                ff = FFItem(ref)
                ff.source_data = it
                ff.label = ff.title = it['title']
                if year := it.get('release_year'):
                    ff.vtag.setYear(year)
                for uid in ('tmdb', 'tvdb'):
                    if val := it.get(f'{uid}_id'):
                        ff.vtag.setUniqueID(str(val), uid)
                ff.temp.rank = it.get('rank') or 0  # type: ignore[typeddict-item]
                return ff
            else:
                for ref in self._get_ref(it):
                    ff = FFItem(ref)
                    vtag = ff.vtag
                    info = it.get(ref.real_type, {})
                    ids = info.get('ids', {})
                    for key in ('tmdb', 'imdb', 'trakt', 'mdblist'):
                        if val := ids.get(key):
                            ff.vtag.setUniqueID(str(val), key)
                    if title := info.get('title'):
                        ff.label = ff.title = title
                    if year := info.get('year'):
                        vtag.setYear(year)
                    if show_info := info.get('show'):
                        if title := show_info.get('title'):
                            vtag.setTvShowTitle(title)
                        if year := show_info.get('year'):
                            ff.setProperty('show.year', str(year))
                    if rating := it.get('rating'):
                        ff.setProperty('user_rating', str(rating))
                        ff.setProperty('mdblist.user_rating', str(rating))
                    ff.temp.rank = it.get('rank') or 0  # type: ignore[typeddict-item]
                    return ff
                return None

        if not page_size:
            page_size = const.indexer.mdblist.page_size
        if isinstance(data, dict):  # Mapping
            items = []
            for mtype in ('movie', 'show', 'season', 'episode'):
                if not media or media == mtype:
                    items.extend((ff for it in data.get(f'{mtype}s', ()) if (ff := parse(it)) is not None))
            items.sort(key=lambda it: it.temp.rank)  # type: ignore[typeddict-item]
            total = data.total_items
            return ItemList(items, page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size, total_results=total)
        elif isinstance(data, list):  # Sequence
            items = sorted((ff for it in data if (not media or it['mediatype'] == media) and (ff := parse(it)) is not None),
                           key=lambda x: x.temp.rank)  # type: ignore[typeddict-item]
            total = data.total_items
            return ItemList(items, page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size, total_results=total)
        return ItemList.empty()

    def user_profile(self) -> JsonData:
        """Get user profile."""
        data = self.get('user')
        if isinstance(data, dict):  # Mapping
            return data
        return {}

    def user_lists(self, *, user: str | int | None = None, media: MainMediaType | None = None, static: bool = False) -> Sequence[FFItem]:
        """Get user lists."""
        return self._parse_lists(self.get(f'lists/user/{user or ""}'), media=media, static=static)

    def user_list_by_name(self, *, user: str | int, list: str | int, static: bool = False) -> Sequence[FFItem]:
        """Get user list by name."""
        list = self._list_id(list)
        return self._parse_lists(self.get(f'lists/{user}/{list}'), static=static)

    def watchlist_items(self, *, media: MainMediaType | None = None) -> Sequence[FFItem]:
        """Get watchlist items."""
        return ItemList.single(self._parse_items(self.get('watchlist/items', params={'unified': 'true'}), media=media))

    def top_lists(self, *, media: MainMediaType | None = None) -> PagedItemList[FFItem]:
        """Get top lists."""
        return ItemList.single(self._parse_lists(self.get('lists/top'), media=media))

    def list_items(self, list_id: int, *, page: int = 1, page_size: int | None = None, media: MediaType | None = None) -> Sequence[FFItem]:
        """Get list items."""
        list_id = self._list_id(list_id)
        if not page_size:
            page_size = const.indexer.mdblist.page_size
        params = {'offset': (page - 1) * page_size, 'limit': page_size, 'unified': 'true'}
        items = self.get(f'lists/{list_id}/items', params=params)
        return self._parse_items(items, media=media, page=page, page_size=page_size)

    def _modify_items(self, url: str, items: Iterable[FFRef]) -> tuple[bool, int]:
        """Add items to any list."""
        remove = url.endswith('/remove')
        items = tuple(items)
        to_modify = {
            'movies': [{'tmdb': ref.tmdb_id} for it in items if (ref := it.ref) and ref.is_movie],
            'shows': [{'tmdb': ref.tmdb_id} for it in items if (ref := it.ref) and ref.is_show],
            'seasons': [{'tmdb': ref.tmdb_id, 'season_number': ref.season} for it in items if (ref := it.ref) and ref.is_season],
            'episodes': [{'tmdb': ref.tmdb_id, 'season_number': ref.season, 'episode_number': ref.episode} for it in items if (ref := it.ref) and ref.is_episode],
        }
        data = self.post(url, data=to_modify)
        fflog(f'[MDBLIST] {"Remove" if remove else "Add"} items to {items} → {data!r}')
        if isinstance(data, dict):  # Mapping
            res_name = 'removed' if remove else 'added'
            added = sum(v for v in data.get(res_name, {}).values())
            # existing = sum(v for v in data.get('existing', {}).values())
            not_found = sum(v for v in data.get('not_found', {}).values())
            return added > 0 and not_found == 0, added
        return False, 0

    def _proc_list_items(self,
                         items: Iterable[FFRef],
                         *,
                         path: str,
                         remove: bool,
                         people_enabled: bool,
                         show_only: bool = False,  # only shows, no seasons or episodes
                         extra: Iterable[JsonData] = (),  # extra data for each item to set in mdblist (like rating, ...)
                         ) -> tuple[bool, int]:
        def ex_iter(items: Iterable[FFRef]) -> Iterable[tuple[FFRef, JsonData]]:
            xit = iter(extra)
            for it in items:
                yield it, next(xit, {})

        if not hasattr(items, '__len__'):
            items = tuple(items)
        if not hasattr(extra, '__len__'):
            extra = tuple(extra)
        if not items:
            return True, 0
        movies = tuple({'ids': ref.video_ids.ids(), **ex} for item, ex in ex_iter(items) if (ref := item.ref).is_movie)
        if show_only:
            shows = tuple({'ids': ref.video_ids.ids(), **ex} for item, ex in ex_iter(items) if (ref := item.ref).is_show)
        else:
            shows = tuple(chain(
                ({'ids': ref.video_ids.ids(), **ex}
                 for item, ex in ex_iter(items) if (ref := item.ref).is_show),
                ({'ids': ref.video_ids.ids(), 'seasons': [{'number': ref.season, **ex}]}
                 for item, ex in ex_iter(items) if (ref := item.ref).is_season),
                ({'ids': ref.video_ids.ids(), 'seasons': [{'number': ref.season, 'episodes': [{'number': ref.episode, **ex}]}]}
                 for item, ex in ex_iter(items) if (ref := item.ref).is_episode),  # type: ignore[reportArgumentType]
            ))
        if people_enabled:
            people = tuple({'ids': ref.video_ids.ids(), **ex} for item, ex in ex_iter(items) if (ref := item.ref).type == 'person')
        else:
            people = ()
        if not movies and not shows and not people:
            return True, 0
        items_to_send = {
            'movies': movies,
            'shows': shows,
        }
        if people_enabled:
            items_to_send['people'] = people
        data = self.post(path, data=items_to_send, login_required=True)
        import json; print(json.dumps(items_to_send, indent=2)); print(json.dumps(data, indent=2))
        # res_name = 'deleted' if remove else 'added'
        res_name = 'deleted' if remove else 'updated'
        if isinstance(data, Mapping):
            updated = sum(v for v in data.get(res_name, {}).values())
            not_found = sum(len(v) for v in data.get('not_found', {}).values())
            return updated > 0 and not_found == 0, updated
        return False, 0

    def add_to_watchlist(self, items: Iterable[FFRef]) -> tuple[bool, int]:
        """Add items to watchlist."""
        return self._modify_items('watchlist/items/add', items)

    def add_to_user_list(self, list_id: int | str, items: Iterable[FFRef]) -> tuple[bool, int]:
        """Add items to user list."""
        list_id = self._list_id(list_id)
        return self._modify_items(f'lists/{list_id}/items/add', items)

    def remove_from_watchlist(self, items: Iterable[FFRef]) -> tuple[bool, int]:
        """Remove items from watchlist."""
        return self._modify_items('watchlist/items/remove', items)

    def remove_from_user_list(self, list_id: int | str, items: Iterable[FFRef]) -> tuple[bool, int]:
        """Remove items from user list."""
        list_id = self._list_id(list_id)
        return self._modify_items(f'lists/{list_id}/items/remove', items)

    # https://api.mdblist.com/rating/media_type/return_rating?apikey=abc123
    # movie , show
    # trakt , imdb , tmdb , letterboxd , tomatoes , audience , metacritic , rogerebert , mal , score , score_average

    def user_ratings(self, *, media: MediaType | None = None) -> ItemList[FFItem]:
        """Get user media ratings."""
        resp = self.request('GET', 'sync/ratings', data=None, login_required=True)
        if resp is None:
            return ItemList.empty()
        data = resp.json()
        if isinstance(data, dict) and (pagination := data.get('pagination')):
            total_items = sum(count for k in ('movie', 'show', 'season', 'episode')
                              if (media is None or media == k) and (count := pagination.get(f'total_{k}s')))
        else:
            total_items = 0
        pages = Pages(data, total_items=total_items)
        items = self._parse_items(pages, media=media)
        # data = self.get(f'sync/ratings', login_required=True)
        # return self._item_list(data, pages=None)
        return items

    def add_user_ratings(self, items: Iterable[FFRef], *, rating: int | None) -> tuple[bool, int]:
        """Add (set) user ratings. If `rating` is None, get ratings from FFItems."""
        items = tuple(items)
        if rating is None:
            # no rating set, get form FFItems (there must be all set, MediaRef is not allowed here)
            ratings = [{'rating': ur} if ur else {} for it in items if isinstance(it, FFItem) and (ur := it.vtag.getUserRating()) is not ...]
            if len(ratings) != len(items):
                raise ValueError('MdbList.add_user_ratings() got refs with missing ratings')
        else:
            # rating is set for all items (works with MediaRef too)
            if not (1 <= rating <= 10):
                raise ValueError(f'MdbList.add_user_ratings() got incorrect rating {rating!r}')
            ratings = ({'rating': rating} for _ in items)
        return self._proc_list_items(items, path='sync/ratings', remove=False, people_enabled=False, extra=ratings)

    def remove_user_ratings(self, items: Iterable[FFRef]) -> tuple[bool, int]:
        """Remove user ratings."""
        return self._proc_list_items(items, path='sync/ratings/remove', remove=True, people_enabled=False)

    def ref_ids(self, ref: FFRef) -> VideoIds | None:
        """Get media IDs from mdblist.com for a given media reference."""
        ref = ref.ref
        if not ref.is_movie and not ref.is_show:
            return None
        srv, mid = ref.video_ids.service_and_value()
        if srv not in ('imdb', 'tmdb', 'trakt', 'tvdb', 'mal', 'mdblist'):
            return None
        data = self.get(f'{srv}/{ref.type}/{mid}')
        if isinstance(data, dict):  # Mapping
            ids = data.get('ids', {})
            return VideoIds.from_ids(ids)
        return None

    # def get_media(self, media_provider: MediaProvider, media_type: MainMediaType, media_id: int | str) -> FFItem | None:

    def get_full_ids(self, media_provider: MediaProvider, media_type: MainMediaType, media_id: Sequence[int | str]) -> list[MdblistIds]:
        """Get media IDs for a given media provider and media type."""
        chain = 200  # mdblist.com API limit for POST /{media_provider}/{media_type} endpoint
        url = f'{media_provider}/{media_type}'
        if len(media_id) > chain:
            def get(offset: int) -> list[JsonData]:
                d = self.post(url, data={'ids': media_id[offset:offset + chain]})
                return d if isinstance(d, list) else []
            with requests.RequestsPoolExecutor() as ex:
                data = [it for d in ex.map(get, range(0, len(media_id), chain)) for it in d]
        else:
            data = self.post(url, data={'ids': media_id})
        if isinstance(data, list):
            return [MdblistIds(
                imdb=ids.get('imdb', ''),
                tmdb=ids.get('tmdb', 0),
                trakt=ids.get('trakt', 0),
                tvdb=ids.get('tvdb'),
                mal=ids.get('mal'),
                mdblist=ids.get('mdblist', ''),
            ) for item in data if (ids := item.get('ids'))]
        return []


mdblist = MdbList()


if __name__ == '__main__':
    import json
    from ..ff.cmdline import DebugArgumentParser

    def print_ffitems(items: Sequence[FFItem]) -> None:
        if isinstance(items, ItemList):
            print(f'Page {items.page} of {items.total_pages}, {len(items)} items, total: {items.total_results}')
        for it in items:
            count = f' [#{it.children_count}]' if it.children_count else ''
            print(f'{it.ref:16a}: {it.title} ({it.year}){count}')

    p = DebugArgumentParser(dest='op', description='Test script for mdblist.com')
    p.add_argument('-p', '--page', type=int, default=1, help='Page number for paginated results')
    p.add_argument('-P', '--page-size', type=int, default=const.indexer.mdblist.page_size, help='Page size for list items')
    with p.with_subparser('profile') as pp:
        pass
    with p.with_subparser('lists') as pp:
        pp.add_argument('-u', '--user', help='User name to get lists for')
        pp.add_argument('-l', '--list', help='Name of user list')
        pp.add_argument('-s', '--static', action='store_true', help='Only static lists')
    with p.with_subparser('items') as pp:
        pp.add_argument('list', type=int, help='List ID to get items for')
        pp.add_argument('-t', '--type', choices=('movie', 'show'), help='Filter items by media type')
    args = p.parse_args()

    mdb = MdbList()
    if args.op == 'lists':
        # print(json.dumps(mdb.user_lists(user='garycrawfordgc'), indent=2))
        # print(json.dumps(mdb.user_lists(user=args.user), indent=2))
        if args.list:
            if not args.user:
                args.user = mdb.user_profile()['username']
            print_ffitems(mdb.user_list_by_name(user=args.user, list=args.list, static=args.static))
        else:
            print_ffitems(mdb.user_lists(user=args.user, static=args.static))
    elif args.op == 'items':
        # print(json.dumps(mdb.list_items(args.list), indent=2))
        print_ffitems(mdb.list_items(args.list, page=args.page, page_size=args.page_size, media=args.type))
    else:
        print(json.dumps(mdb.user_profile(), indent=2))
