from __future__ import annotations
import json
import base64
import random
from typing import Any, Iterable, Sequence, Collection, ClassVar
from typing_extensions import TypeAlias, Literal
from concurrent.futures import ThreadPoolExecutor
from attrs import define, frozen

if __package__:
    # --- FanFilm imports ---
    from ..ff import requests
    from ..ff.requests import netcache
    from ..ff.log_utils import fflog
    from ..ff.item import FFItem
    from ..ff.types import JsonData
    from ..ff.settings import settings
    from ..defs import MediaRef, VideoIds, RefType, ItemList, FFRef
    from ..kolang import L
    from const import const
    API_PAGE_SIZE = const.dev.imdb.api_page_size
else:
    # -- standalone imports --
    import requests
    from stub import fflog, L, FFItem, RefType, MediaRef, VideoIds, ItemList, JsonData, netcache, settings, FFRef
    API_PAGE_SIZE = 20


def _rand_digits(n: int) -> str:
    """Random N-digit string used to fabricate Amazon-style session IDs (ubid-main, x-amzn-sessionid)."""
    return ''.join(random.choices('0123456789', k=n))


ImdbTitleType: TypeAlias = Literal['movie', 'tvSeries', 'short', 'tvEpisode', 'tvMiniSeries',
                                   'tvMovie', 'tvSpecial', 'tvShort', 'videoGame', 'video',
                                   'musicVideo', 'podcastSeries', 'podcastEpisode']


ImdbListType = Literal['TITLES', 'PEOPLE', 'VIDEOS', 'IMAGES']


ListVisibility = Literal['PUBLIC', 'PRIVATE']


@define
class ImdbApiStats:
    request_count: int = 0


@frozen
class UserRating:
    """The authenticated user's rating for one title. `value` is 1..10, `date` is ISO 8601 string."""
    value: int
    date: str


#: Supported award group names for last_oscars_refs; these are legacy IMDB UI group names that we map to AwardEventNominationSearchInput parameters.
AwardNames = Literal['oscar_winner', 'oscar_nominee',
                     'best_picture_winner', 'oscar_best_picture_nominees',
                     'best_director_winner', 'oscar_best_director_nominees',
                     'golden_globe_winner', 'golden_globe_nominee',
                     'razzie_winner', 'razzie_nominee',
                     'emmy_winner', 'emmy_nominee']


# Legacy IMDB UI group-name -> AwardEventNominationSearchInput (eventId, optional category, optional winner-filter).
# Omitted winnerFilter means "any nomination" (winners + non-winners), matching the legacy `_nominee` semantics.
_AWARD_GROUPS: dict[AwardNames, dict[str, Any]] = {
    'oscar_winner':                 {'eventId': 'ev0000003', 'winnerFilter': 'WINNER_ONLY'},
    'oscar_nominee':                {'eventId': 'ev0000003'},
    'best_picture_winner':          {'eventId': 'ev0000003', 'searchAwardCategoryId': 'bestPicture',  'winnerFilter': 'WINNER_ONLY'},
    'oscar_best_picture_nominees':  {'eventId': 'ev0000003', 'searchAwardCategoryId': 'bestPicture'},
    'best_director_winner':         {'eventId': 'ev0000003', 'searchAwardCategoryId': 'bestDirector', 'winnerFilter': 'WINNER_ONLY'},
    'oscar_best_director_nominees': {'eventId': 'ev0000003', 'searchAwardCategoryId': 'bestDirector'},
    'golden_globe_winner':          {'eventId': 'ev0000292', 'winnerFilter': 'WINNER_ONLY'},
    'golden_globe_nominee':         {'eventId': 'ev0000292'},
    'razzie_winner':                {'eventId': 'ev0000558', 'winnerFilter': 'WINNER_ONLY'},
    'razzie_nominee':               {'eventId': 'ev0000558'},
    'emmy_winner':                  {'eventId': 'ev0000223', 'winnerFilter': 'WINNER_ONLY'},
    'emmy_nominee':                 {'eventId': 'ev0000223'},
}


_Q_LIST = '''query L($id: ID!, $first: Int!, $after: ID) {
  list(id: $id) {
    items(first: $first, after: $after) {
      total
      edges { node { item { ... on Title {
        id titleType { id } __USER_RATING__
        series { series { id } episodeNumber { episodeNumber seasonNumber } }
      } } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}'''


_Q_WATCHLIST = '''query W($user: ID!, $first: Int!, $after: ID) {
  predefinedList(classType: WATCH_LIST, userId: $user) {
    items(first: $first, after: $after) {
      total
      edges { node { item { ... on Title {
        id titleType { id } __USER_RATING__
        series { series { id } episodeNumber { episodeNumber seasonNumber } }
      } } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}'''


_Q_USER_LISTS = '''query U($user: ID!, $first: Int!, $after: ID) {
  userListSearch(first: $first, after: $after, listOwnerUserId: $user, filter: { anyListTypes: [TITLES] }) {
    total
    edges {
      node {
        id
        name { originalText }
        description { originalText { plainText } }
        primaryImage { image { url } }
        items(first: 0) { total }
        author { userId nickName }
        listType { id }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}'''


_Q_USER_INFO = '''query I($user: ID!) {
  userListSearch(first: 1, listOwnerUserId: $user) {
    total
    edges {
      node {
        author {
          userId
          nickName
          bio { text { plainText } }
          primaryImage { image { url } }
        }
      }
    }
  }
}'''


_Q_SEARCH = '''query S($first: Int!, $after: String, $award: AwardSearchConstraint, $types: [String!]) {
  advancedTitleSearch(
    first: $first
    after: $after
    sort: { sortBy: YEAR, sortOrder: DESC }
    constraints: { awardConstraint: $award, titleTypeConstraint: { anyTitleTypeIds: $types } }
  ) {
    total
    edges { node { title { id titleType { id } __USER_RATING__ } } }
    pageInfo { hasNextPage endCursor }
  }
}'''


_Q_TITLE_RATING = '''query TR($id: ID!) {
  title(id: $id) { userRating { value date } }
}'''


_Q_USER_RATINGS_SEARCH = '''query UR($first: Int!, $after: String, $minR: Int, $maxR: Int, $types: [String!]) {
  advancedTitleSearch(
    first: $first
    after: $after
    sort: { sortBy: MY_RATING_DATE, sortOrder: DESC }
    constraints: {
      myRatingConstraint: { filterType: INCLUDE, ratingRange: { min: $minR, max: $maxR } }
      titleTypeConstraint: { anyTitleTypeIds: $types }
    }
  ) {
    total
    edges { node { title {
      id titleType { id } titleText { text }
      userRating { value date }
      series { series { id } episodeNumber { episodeNumber seasonNumber } }
    } } }
    pageInfo { hasNextPage endCursor }
  }
}'''


_Q_WHOAMI = '''query Whoami {
  user {
    fullName
    profile {
      userId
      nickName
      bio { text { plainText } }
      primaryImage { image { url } }
    }
  }
}'''


# Mutations require an `at-main` cookie; itemElementId is the regular tt-id for title list items.
_M_RATE = '''mutation Rate($id: ID!, $rating: Int!) {
  rateTitle(input: { titleId: $id, rating: $rating }) { rating { value } }
}'''

_M_UNRATE = '''mutation Unrate($id: ID!) {
  deleteTitleRating(input: { titleId: $id }) { date }
}'''

_M_ADD_WATCHLIST = '''mutation AddWatchlist($id: ID!) {
  addItemToPredefinedList(input: { classType: WATCH_LIST, item: { itemElementId: $id } }) { listId }
}'''

_M_REMOVE_WATCHLIST = '''mutation RemoveWatchlist($id: ID!) {
  removeElementFromPredefinedList(input: { classType: WATCH_LIST, itemElementId: $id }) { listId }
}'''

_M_ADD_LIST = '''mutation AddList($list: ID!, $id: ID!) {
  addItemToList(input: { listId: $list, item: { itemElementId: $id } }) { listId }
}'''

_M_REMOVE_LIST = '''mutation RemoveList($list: ID!, $id: ID!) {
  removeElementFromList(input: { listId: $list, itemElementId: $id }) { listId }
}'''

_M_CREATE_LIST = '''mutation Create($name: String!, $desc: String, $type: ListTypeId!, $vis: ListVisibilityId!, $dup: Boolean!) {
  createList(input: { name: $name, listDescription: $desc, listType: $type, visibility: $vis, allowDuplicates: $dup }) { listId }
}'''

_M_DELETE_LIST = '''mutation Delete($list: ID!) {
  deleteList(input: { listId: $list }) { listId }
}'''


@netcache('lists')
class ImdbScraper:
    """Scrape IMDB lists and watchlists via their GraphQL API."""

    MEDIA_TYPES: ClassVar[dict[str, RefType]] = {
        'movie': 'movie',
        'short': 'movie',
        'tvMovie': 'movie',
        'tvShort': 'movie',
        'tvSpecial': 'movie',
        'video': 'movie',
        'videoGame': 'movie',
        'musicVideo': 'movie',
        'tvSeries': 'show',
        'tvMiniSeries': 'show',
        'podcastSeries': 'show',
        'tvEpisode': 'episode',
        'podcastEpisode': 'episode',
    }

    TITLE_TYPES: ClassVar[dict[RefType, Collection[ImdbTitleType]]] = {
        'movie':   ('movie', 'short', 'tvMovie', 'tvShort', 'tvSpecial'),
        'show':    ('tvSeries', 'tvMiniSeries'),
        'episode': ('tvEpisode',),
    }

    PAGE_SIZE: ClassVar[int] = API_PAGE_SIZE
    # IMDB silently caps `first` per query: userListSearch at 250 edges, advancedTitleSearch at 1000.
    # Both have HMAC-signed cursors -> only sequential chain via endCursor is possible (no random-access by forge).
    USER_LISTS_BATCH: ClassVar[int] = 250
    SEARCH_BATCH: ClassVar[int] = 1000

    _DEBUG_STATS: ClassVar[ImdbApiStats] = ImdbApiStats()
    _FAKE: ClassVar[bool] = False

    def __init__(self) -> None:
        self._cache_url: str = 'https://caching.graphql.imdb.com/'   # public read-side, cached
        self._auth_url: str = 'https://graphql.imdb.com/'            # write-side; also returns private data when authenticated
        self._ubid: str = f'{_rand_digits(3)}-{_rand_digits(7)}-{_rand_digits(7)}'  # stable per-instance fake ubid-main
        self.headers: dict[str, str] = {'Content-Type': 'application/json', 'Accept': 'application/json'}

    @property
    def authenticated(self) -> bool:
        """True when an `at-main` cookie is present in settings (does not verify token validity)."""
        return bool(settings.get_string('imdb.at-main'))

    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> JsonData:
        op = query.split('{', 1)[0].strip().replace('\n', ' ')
        at_main = settings.get_string('imdb.at-main')
        # Inject auth-only Title fields when authenticated; placeholder is a no-op otherwise.
        query = query.replace('__USER_RATING__', 'userRating { value }' if at_main else '')
        headers = dict(self.headers)
        cookies: dict[str, str] = {}
        if at_main:
            url = self._auth_url
            headers['x-amzn-sessionid'] = f'{_rand_digits(3)}-{_rand_digits(7)}-{_rand_digits(7)}'
            headers['x-imdb-client-name'] = 'imdb-web-next'
            headers['x-imdb-user-language'] = 'en-US'
            headers['x-imdb-user-country'] = 'US'
            cookies['at-main'] = at_main
            cookies['ubid-main'] = self._ubid
        else:
            url = self._cache_url
        if ua := settings.get_string('imdb.user_agent'):
            headers['User-Agent'] = ua
        fflog(f'GraphQL post: {op} variables={variables} auth={bool(at_main)}')
        self._DEBUG_STATS.request_count += 1
        if self._FAKE:
            return {}
        req = requests.post(url, json={'query': query, 'variables': variables or {}}, headers=headers, cookies=cookies or None)
        if req.status_code != 200:
            fflog(f'GraphQL HTTP {req.status_code} for {op}: {req.text[:200]}')
            return {}
        resp = req.json()
        if errors := resp.get('errors'):
            fflog(f'GraphQL error for {op}: {errors[0].get("message", "")[:200]}')
            return {}
        return resp.get('data') or {}

    @classmethod
    def _list_cursor(cls, page: int | None) -> str | None:
        """Synthesize an `after` cursor for direct random-access to `page` on list.items (unsigned cursor)."""
        if not page or page <= 1:
            return None
        offset = (page - 1) * cls.PAGE_SIZE - 1
        payload = {'sort': {'by': 'LIST_ORDER', 'order': 'ASC'}, 'offset': offset}
        return base64.b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()

    def _user(self, user: str) -> Sequence[str]:
        return [f'ur{usr}' if usr.isdecimal() else usr for name in user.split(',') if (usr := name.strip())]

    def _make_list(self,items: Iterable[T], *, page: int, total: int,
                   ratings: dict[str, int | None] | None = None,
                   ) -> ItemList[T]:
        items = ItemList(items, page=page or 1, total_pages=(total + self.PAGE_SIZE - 1) // self.PAGE_SIZE, total_results=total)
        # if ratings is not None:
        #     items.ratings = ratings  # type: ignore[attr-defined]  # parallel ffid->rating dict, populated only when authenticated
        return items

    def _get_list(self,
                  query: str,
                  variables: dict[str, Any],
                  *,
                  data_key: str,
                  media_type: RefType | None = None,
                  page: int | None = 0,
                  ) -> ItemList[MediaRef]:
        page = max(1, page or 1)
        variables = {**variables, 'first': self.PAGE_SIZE, 'after': self._list_cursor(page)}
        data = self._gql(query, variables)
        container = data.get(data_key) or {}
        items_data = container.get('items') if isinstance(container, dict) else None
        if not items_data:
            return ItemList([], page=page, total_pages=0, total_results=0)
        edges = items_data.get('edges') or []
        total = items_data.get('total') or 0
        refs: list[MediaRef] = []
        for edge in edges:
            item = (edge.get('node') or {}).get('item')
            if not item:
                continue
            type_id = (item.get('titleType') or {}).get('id')
            media = self.MEDIA_TYPES.get(type_id) if type_id else None
            if not media or (media_type and media_type != media):
                continue
            refs.append(self._title_to_ref(item, media))
        ratings: dict[str, int | None] | None = None
        # if self.authenticated:
        #     ratings = {item['id']: ((item.get('userRating') or {}).get('value'))
        #                for edge in edges if (item := (edge.get('node') or {}).get('item')) and item.get('id')}
        return self._make_list(refs, page=page, total=total, ratings=ratings)

    @staticmethod
    def _title_to_ref(title: JsonData, media: RefType) -> MediaRef:
        """Build a MediaRef from a Title node. For tvEpisode with `series` populated, produces canonical
        FanFilm episode form (type='show', ffid=series, season=N, episode=M); otherwise plain (type=media, ffid=title)."""
        type_id = (title.get('titleType') or {}).get('id')
        series = title.get('series') or {}
        if type_id == 'tvEpisode' and series:
            parent = (series.get('series') or {})
            ep = series.get('episodeNumber') or {}
            series_id = parent.get('id')
            season_num = ep.get('seasonNumber')
            ep_num = ep.get('episodeNumber')
            if series_id and season_num and ep_num:
                return MediaRef(type='show', ffid=VideoIds.make_ffid(imdb=series_id), season=season_num, episode=ep_num)
        return MediaRef(type=media, ffid=VideoIds.make_ffid(imdb=title['id']))

    def watch_list(self, user: str, *, media_type: RefType | None = None, page: int | None = None) -> list[MediaRef]:
        """Get a user's watchlist items as a paginated list of MediaRefs. Only the first user is used if `user` is U1,U2,..."""
        users = self._user(user)
        if not users:
            return []
        return self._get_list(_Q_WATCHLIST, {'user': users[0]}, data_key='predefinedList', media_type=media_type, page=page)

    def list(self, list_id: str, *, media_type: RefType | None = None, page: int | None = None) -> list[MediaRef]:
        """Get a list's items as a paginated list of MediaRefs. List ID can be with or without ls prefix."""
        if list_id.isdecimal():
            list_id = f'ls{list_id}'
        return self._get_list(_Q_LIST, {'id': list_id}, data_key='list', media_type=media_type, page=page)

    @netcache('discover')
    def last_oscars_refs(self,
                         name: AwardNames = 'oscar_winner',
                         *,
                         title_type: ImdbTitleType | Collection[ImdbTitleType] | None = None,
                         page: int | None = None,
                         ) -> ItemList[MediaRef]:
        """Get last Oscar winners/nominees as a paginated list of MediaRefs. `name` is a legacy IMDB UI group name like 'oscar_winner' or 'emmy_winner'."""
        award = _AWARD_GROUPS.get(name)
        if not award:
            fflog(f'IMDB: unknown award group {name!r}')
            return []
        if title_type is None:
            types: list[str] | None = None
        elif isinstance(title_type, str):
            types = [title_type]
        else:
            types = list(title_type)
        # advancedTitleSearch caps `first` at 1000 edges and the cursor is HMAC-signed -> chain-from-start through
        # SEARCH_BATCH-sized batches until we have enough nodes to slice page N; @netcache amortises repeat batches.
        page = max(1, page or 1)
        start = (page - 1) * self.PAGE_SIZE
        end = start + self.PAGE_SIZE

        titles: list[JsonData] = []
        after: str | None = None
        total = 0
        while len(titles) < end:
            batch = min(self.SEARCH_BATCH, end - len(titles))
            data = self._gql(_Q_SEARCH, {'first': batch, 'after': after,
                                         'award': {'allEventNominations': [award]}, 'types': types})
            search = data.get('advancedTitleSearch') or {}
            edges = search.get('edges') or []
            total = search.get('total') or total
            if not edges:
                break
            titles.extend(t for edge in edges if (t := (edge.get('node') or {}).get('title')))
            info = search.get('pageInfo') or {}
            if not info.get('hasNextPage') or not (after := info.get('endCursor')):
                break

        page_titles = titles[start:end]
        items = [MediaRef(media, VideoIds.make_ffid(imdb=t['id']))
                 for t in page_titles
                 if (media := self.MEDIA_TYPES.get((t.get('titleType') or {}).get('id')))]
        ratings: dict[str, int | None] | None = None
        # if self.authenticated:
        #     ratings = {t['id']: ((t.get('userRating') or {}).get('value')) for t in page_titles if t.get('id')}
        return self._make_list(items, page=page, total=total, ratings=ratings)

    def user_lists(self, user: str, *, page: int | None = None) -> ItemList[FFItem]:
        """Get a single user's lists as a paginated ItemList of FFItems.

        If `user` is a CSV of IDs, only the first one is used (caller is expected to split users at a higher level).
        userListSearch caps `first` at 250 and its cursor is HMAC-signed, so we walk a chain-from-start of cap-250
        batches until we have enough nodes to slice page N; @netcache amortises repeat batches across calls.
        """
        def make_item(node: JsonData) -> FFItem:
            def var(*keys: str) -> Any:
                val: Any = node
                for key in keys:
                    if not isinstance(val, dict):
                        return None
                    val = val.get(key)
                return val
            name = (node.get('name') or {}).get('originalText') or ''
            ffitem = FFItem(name, mode=FFItem.Mode.Folder)
            ffitem.title = name
            ffitem.vtag.setUniqueID(node['id'], 'imdb')
            ffitem.vtag.setIMDBNumber(node['id'])
            if descr := var('description', 'originalText', 'plainText'):
                ffitem.vtag.setPlot(descr)
            if url := var('primaryImage', 'image', 'url'):
                ffitem.setArt({'poster': url})
            if (count := node.get('items')) and (total := count.get('total')) is not None:
                ffitem._children_count = total
                ffitem.role = L(30331, '{n} title|||{n} titles', n=total)
            return ffitem

        users = self._user(user)
        if not users:
            return self._make_list([], page=1, total=0)
        user_id = users[0]
        page = max(1, page or 1)
        start = (page - 1) * self.PAGE_SIZE
        end = start + self.PAGE_SIZE

        nodes: list[JsonData] = []
        after: str | None = None
        total = 0
        while len(nodes) < end:
            batch = min(self.USER_LISTS_BATCH, end - len(nodes))
            data = self._gql(_Q_USER_LISTS, {'user': user_id, 'first': batch, 'after': after})
            search = data.get('userListSearch') or {}
            edges = search.get('edges') or []
            total = search.get('total') or total
            if not edges:
                break
            nodes.extend(n for edge in edges if (n := edge.get('node')))
            info = search.get('pageInfo') or {}
            if not info.get('hasNextPage') or not (after := info.get('endCursor')):
                break

        items = (make_item(n) for n in nodes[start:end])
        return self._make_list(items, page=page, total=total)

    def user_info_list(self, user: str) -> list[FFItem]:
        """Get a single user's info as a list of FFItems (one per user if `user` is a CSV of IDs)."""
        def get(user: str) -> FFItem | None:
            data = self._gql(_Q_USER_INFO, {'user': user})
            search = data.get('userListSearch') or {}
            edges = search.get('edges') or []
            if not edges:
                return None
            author: JsonData = (edges[0].get('node') or {}).get('author') or {}
            imdb = author.get('userId') or user
            nick = author.get('nickName') or user
            ffitem = FFItem(nick, mode=FFItem.Mode.Folder)
            ffitem.title = nick
            ffitem.vtag.setUniqueID(imdb, 'imdb')
            ffitem.vtag.setIMDBNumber(imdb)
            if url := ((author.get('primaryImage') or {}).get('image') or {}).get('url'):
                ffitem.setArt({'poster': url})
            if plain := ((author.get('bio') or {}).get('text') or {}).get('plainText'):
                ffitem.vtag.setPlot(plain)
            if (total := search.get('total')) is not None:
                ffitem.role = L(30331, '{n} title|||{n} titles', n=total)
            return ffitem

        users = self._user(user)
        with ThreadPoolExecutor() as executor:
            results = list(executor.map(get, users))
        return [it for it in results if it]

    def user_info(self, user: str) -> FFItem | None:
        """Get user info as FFItem (first user if user is comma separated list)."""
        items = self.user_info_list(user)
        return items[0] if items else None

    def whoami(self) -> FFItem | None:
        """Return the currently authenticated user as FFItem, or None if no `at-main` cookie or token expired."""
        if not self.authenticated:
            return None
        data = self._gql(_Q_WHOAMI)
        user = data.get('user') or {}
        profile = user.get('profile') or {}
        imdb = profile.get('userId') or ''
        if not imdb:
            return None
        nick = profile.get('nickName') or user.get('fullName') or imdb
        ffitem = FFItem(nick, mode=FFItem.Mode.Folder)
        ffitem.title = nick
        ffitem.vtag.setUniqueID(imdb, 'imdb')
        ffitem.vtag.setIMDBNumber(imdb)
        if url := ((profile.get('primaryImage') or {}).get('image') or {}).get('url'):
            ffitem.setArt({'poster': url})
        if plain := ((profile.get('bio') or {}).get('text') or {}).get('plainText'):
            ffitem.vtag.setPlot(plain)
        return ffitem

    def _mutate(self, query: str, variables: dict[str, Any], result_key: str) -> bool:
        """Run an authenticated mutation; returns True iff response contains a non-null `result_key`."""
        if not self.authenticated:
            fflog(f'IMDB: not authenticated, skipping mutation ({result_key})')
            return False
        data = self._gql(query, variables)
        return data.get(result_key) is not None

    def rate_title(self, imdb_id: str, rating: int) -> bool:
        """Rate a title (1..10). Returns False if not authenticated or rejected."""
        return self._mutate(_M_RATE, {'id': imdb_id, 'rating': rating}, 'rateTitle')

    def delete_rating(self, imdb_id: str) -> bool:
        """Remove your rating for a title."""
        return self._mutate(_M_UNRATE, {'id': imdb_id}, 'deleteTitleRating')

    def add_to_watchlist(self, imdb_id: str) -> bool:
        """Add a title to the authenticated user's watchlist."""
        return self._mutate(_M_ADD_WATCHLIST, {'id': imdb_id}, 'addItemToPredefinedList')

    def remove_from_watchlist(self, imdb_id: str) -> bool:
        """Remove a title from the authenticated user's watchlist."""
        return self._mutate(_M_REMOVE_WATCHLIST, {'id': imdb_id}, 'removeElementFromPredefinedList')

    def add_to_list(self, list_id: str, imdb_id: str) -> bool:
        """Add a title to one of the authenticated user's lists. List ID can be with or without `ls` prefix."""
        if list_id.isdecimal():
            list_id = f'ls{list_id}'
        return self._mutate(_M_ADD_LIST, {'list': list_id, 'id': imdb_id}, 'addItemToList')

    def remove_from_list(self, list_id: str, imdb_id: str) -> bool:
        """Remove a title from one of the authenticated user's lists. List ID can be with or without `ls` prefix."""
        if list_id.isdecimal():
            list_id = f'ls{list_id}'
        return self._mutate(_M_REMOVE_LIST, {'list': list_id, 'id': imdb_id}, 'removeElementFromList')

    def create_list(self,
                    name: str,
                    description: str = '',
                    *,
                    list_type: ImdbListType = 'TITLES',
                    visibility: ListVisibility = 'PUBLIC',
                    allow_duplicates: bool = False,
                    ) -> str | None:
        """Create a new list. Returns the new list's `ls...` id on success, None on failure or if not authenticated."""
        if not self.authenticated:
            fflog('IMDB: not authenticated, skipping mutation (createList)')
            return None
        data = self._gql(_M_CREATE_LIST, {
            'name': name,
            'desc': description or None,
            'type': list_type,
            'vis': visibility,
            'dup': allow_duplicates,
        })
        return (data.get('createList') or {}).get('listId')

    def delete_list(self, list_id: str) -> bool:
        """Delete one of the authenticated user's lists. List ID can be with or without `ls` prefix."""
        if list_id.isdecimal():
            list_id = f'ls{list_id}'
        return self._mutate(_M_DELETE_LIST, {'list': list_id}, 'deleteList')

    def rating(self, ref: FFRef) -> UserRating | None:
        """Get the authenticated user's rating for a single title. Returns UserRating(value, date) or None if not rated,
        not authenticated, or the ref has no IMDB id. Accepts MediaRef or FFItem (both expose `.ref`)."""
        if not self.authenticated:
            return None
        imdb = ref.ref.video_ids.imdb
        if not imdb:
            return None
        data = self._gql(_Q_TITLE_RATING, {'id': imdb})
        rating = ((data.get('title') or {}).get('userRating') or {})
        value = rating.get('value')
        if value is None:
            return None
        return UserRating(value=int(value), date=rating.get('date') or '')

    def user_ratings(self,
                     *,
                     media_type: RefType | None = None,
                     min_rating: int | None = None,
                     max_rating: int | None = None,
                     page: int | None = None,
                     ) -> ItemList[FFItem]:
        """Get the authenticated user's rated titles, paginated, sorted by rating-date desc. Filters are server-side:
        `media_type` ('movie'/'show'/'episode'), `min_rating`/`max_rating` (1..10 inclusive). Each FFItem carries
        properties 'imdb.user_rating' (str of int) and 'imdb.user_rating.date' (ISO 8601 string)."""
        if not self.authenticated:
            return self._make_list([], page=1, total=0)
        types: list[str] | None = list(self.TITLE_TYPES[media_type]) if media_type in self.TITLE_TYPES else None
        page = max(1, page or 1)
        start = (page - 1) * self.PAGE_SIZE
        end = start + self.PAGE_SIZE

        titles: list[JsonData] = []
        after: str | None = None
        total = 0
        while len(titles) < end:
            batch = min(self.SEARCH_BATCH, end - len(titles))
            data = self._gql(_Q_USER_RATINGS_SEARCH,
                             {'first': batch, 'after': after, 'minR': min_rating, 'maxR': max_rating, 'types': types})
            search = data.get('advancedTitleSearch') or {}
            edges = search.get('edges') or []
            total = search.get('total') or total
            if not edges:
                break
            titles.extend(t for edge in edges if (t := (edge.get('node') or {}).get('title')))
            info = search.get('pageInfo') or {}
            if not info.get('hasNextPage') or not (after := info.get('endCursor')):
                break

        items: list[FFItem] = []
        for title in titles[start:end]:
            type_id = (title.get('titleType') or {}).get('id')
            media = self.MEDIA_TYPES.get(type_id) if type_id else None
            if not media:
                continue
            rating = title.get('userRating') or {}
            value = rating.get('value')
            if value is None:
                continue
            ref = self._title_to_ref(title, media)
            tt = title['id']
            name = (title.get('titleText') or {}).get('text') or ''
            ffitem = FFItem(ref=ref, title=name, mode=FFItem.Mode.Folder)
            ffitem.vtag.setUniqueID(tt, 'imdb')
            ffitem.vtag.setIMDBNumber(tt)
            ffitem.setProperty('imdb.user_rating', str(int(value)))
            ffitem.setProperty('imdb.user_rating.date', rating.get('date') or '')
            items.append(ffitem)
        return self._make_list(items, page=page, total=total)

    def true(self, *args, **kwargs) -> bool:
        return True

    def false(self, *args, **kwargs) -> bool:
        return False


if __name__ == '__main__':
    from math import log10
    from contextlib import contextmanager
    from typing import TypeVar, Generator, TYPE_CHECKING
    from time import sleep
    from wrapt import ObjectProxy

    if __package__:
        # --- FanFilm imports ---
        from ..ff.cmdline import DebugArgumentParser
        from ..service.web_server import WebServer
    else:
        # -- standalone imports --
        from argparse import ArgumentParser as DebugArgumentParser
        from stub import WebServer

    T = TypeVar('T')

    if TYPE_CHECKING:
        ItemProxyBase = (ObjectProxy, T)
    else:
        ItemProxyBase = (ObjectProxy,)

    class ItemProxy(*ItemProxyBase):
        def __init__(self, item: T, *, index: int, width: int = 0) -> None:
            super().__init__(item)
            self._self_index = index
            self._self_width = width

        @property
        def num(self) -> str:
            return f'{self._self_index:{self._self_width}d}'

    awards = ('oscar_winner', 'oscar_nominee', 'best_picture_winner', 'oscar_best_picture_nominees', 'best_director_winner', 'oscar_best_director_nominees',
              'golden_globe_winner', 'golden_globe_nominee', 'razzie_winner', 'razzie_nominee',
              'emmy_winner', 'emmy_nominee', 'golden_globe_winner', 'golden_globe_nominee',
              )
    p = DebugArgumentParser()
    p.add_argument('--me', action='store_true', help='show info about me (authenticated user)')
    p.add_argument('-w', '--watchlist', nargs='?', const='me', help='IMDB user ID like ur123456789')
    p.add_argument('-u', '--user', help='IMDB user ID like ur23892615')
    p.add_argument('-l', '--list', help='IMDB list ID like ls005762314')
    p.add_argument('-p', '--page', type=int, help='page number')
    p.add_argument('-P', '--page-size', type=int, help='page size (default 20)')
    p.add_argument('-d', '--depaginate', action='store_true', help='depaginate list')
    p.add_argument('-i', '--info', action='store_true', help='get info for: user (-u), ...')
    p.add_argument('-a', '--awards', choices=awards, default='oscar_winner', help='get last awards winners/nominees for given award group')
    p.add_argument('-S', '--cookie-server', action='store_true', help='run a local server to set the at-main cookie from browser session')
    p.add_argument('-A', '--add', metavar='IMDB_ID', help='add to list / watchlist')
    p.add_argument('-R', '--remove', metavar='IMDB_ID', help='remove from list / watchlist')
    args = p.parse_args()

    cookie_server: WebServer | None = None
    if args.cookie_server:
        cookie_server = WebServer()
        cookie_server.start()
        for i in range(100):  # 10 s
            if settings.get_string('imdb.at-main'):
                break
            if not i:
                fflog('Waiting for at-main cookie to be set via cookie server...')
            sleep(.1)
        fflog(f'at-main cookie: {settings.get_string("imdb.at-main")!r}')

    # ur23892615  - 1967 title lists (3719 all types)
    # ls005762314 - 519 items, 3 pages

    ImdbScraper.PAGE_SIZE = max(1, args.page_size or 20)
    imdb = ImdbScraper()

    # settings.get_string('imdb.at-main')

    @contextmanager
    def enum(items: Sequence[T], *, offset: int = 0, page: int | None = None, page_size: int = ImdbScraper.PAGE_SIZE) -> Generator[Sequence[T]]:
        if not offset and page is None and page_size and (pg := getattr(items, 'page', None)):
            page = pg
        if not offset and page and page_size:
            offset = (page - 1) * page_size
        total = getattr(items, 'total_results', None)
        all_pages = getattr(items, 'total_pages', None)
        if not all_pages and page and total is not None and page_size:
            all_pages = (total + page_size - 1) // page_size
        w = int(1 + log10(offset + len(items) or 1))
        items = [ItemProxy(it, index=i, width=w) for i, it in enumerate(items, offset + 1)]
        fflog(f'Page {page} / {all_pages or "?"}, items {offset + 1}-{offset + len(items)} / {total or "?"}')
        yield items

    @contextmanager
    def modify(name: str) -> Generator[ImdbScraper]:
        class Modifier:
            def __getattr__(self, attr: str) -> Any:
                if not hasattr(imdb, attr):
                    raise AttributeError(f'{type(imdb).__name__!r} object has no attribute {attr!r}')
                if not callable(method := getattr(imdb, attr)):
                    return method
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    result = method(*args, **kwargs)
                    aa = ', '.join(repr(v) for v in args)
                    kw = ', '.join(f'{k}={v!r}' for k, v in kwargs.items())
                    aa = ', '.join(part for part in (aa, kw) if part)
                    print(f'{name}: {"success" if result else "FAILED"} {method.__name__}({aa})')
                    return result
                return wrapper
        yield Modifier()  # type: ignore

    try:
        if args.me:
            me = imdb.whoami()
            if me:
                print(f'Me[{me.vtag.getIMDBNumber()}]: {me}')
            else:
                print(f'Me: (not authenticated), has at-main cookie: {bool(settings.get_string("imdb.at-main"))}')
        elif args.watchlist:
            if args.add:
                if args.watchlist == 'me':
                    with modify('Add') as api:
                        api.add_to_watchlist(args.add)
                else:
                    print('Adding to other users\' watchlists is not supported by the API')
            elif args.remove:
                if args.watchlist == 'me':
                    with modify('Remove') as api:
                        api.remove_from_watchlist(args.remove)
                else:
                    print('Removing from other users\' watchlists is not supported by the API')
            else:
                if args.watchlist == 'me':
                    if user := imdb.whoami():
                        args.watchlist = user.vtag.getIMDBNumber()
                if args.watchlist:
                    items = imdb.watch_list(args.watchlist, page=args.page)
                    with enum(items) as items:
                        for ref in items:
                            print(f'{ref.num}. {ref}')
                else:
                    print('No user specified for watchlist; use --watchlist ur123456789 or --watchlist me')
        elif args.user:
            if args.info:
                items = imdb.user_info_list(args.user)
                w = int(1 + log10(len(items) or 1))
                for i, it in enumerate(items, 1):
                    print(f'{i:{w}d}. {it.vtag.getIMDBNumber():12}: {it.label} [{it.role}]')
            else:
                items = imdb.user_lists(args.user, page=args.page)
                with enum(items) as items:
                    for it in items:
                        print(f'{it.num}. {it.vtag.getIMDBNumber():12} [{it.role}] {it}')
        elif args.list:
            if args.depaginate:
                from . import depaginate
                with depaginate(imdb) as api:
                    items = api.list(args.list)
                    w = int(1 + log10(len(items) or 1))
                    for i, ref in enumerate(items, 1):
                        print(f'{i:{w}d}. {ref}')
            elif args.add:
                with modify('Add') as api:
                    api.add_to_list(args.list, args.add)
            elif args.remove:
                with modify('Remove') as api:
                    api.remove_from_list(args.list, args.remove)
            else:
                items = imdb.list(args.list, page=args.page)
                with enum(items) as items:
                    for ref in items:
                        print(f'{ref.num}. {ref}')
        else:
            items = imdb.last_oscars_refs(args.awards, page=args.page)
            with enum(items) as items:
                for ref in items:
                    print(f'{ref.num}. {ref}')
    finally:
        if cookie_server:
            cookie_server.stop()
