from typing import Optional, Union, Sequence, Iterator, ClassVar
from typing_extensions import Literal
from ..core import Indexer
from ..folder import item_folder_route, pagination, ApiPage, FolderRequest, list_directory, Folder
from ...ff.routing import route, url_for, info_for, PathArg
from ...ff.menu import directory
from ...ff.item import FFItem
from ...ff.info import ffinfo
from ...ff.trakt import trakt
from ...service.client import service_client
from ...ff.calendar import fromisoformat, day_label
from ...api.trakt import UserListType, UserGeneralListType as TraktUserGeneralListType, SortType as TraktSortType, UserRatingType as TraktUserRatingType
from ...defs import RefType, MainMediaType, MainMediaTypeList, Pagina, ItemList
from ...kolang import L
# from ...ff.log_utils import fflog
from const import const
from cdefs import InfoDetails


UserRatingType = Literal['movie', 'show', 'season', 'episode', 'all']


class TraktLists(Indexer):

    # Fale user name for official list.
    OFFICIAL: ClassVar[str] = '-'

    @route('/')
    def home(self) -> None:
        """User Trakt lists."""
        with directory(view='sets') as kdir:
            kdir.folder(L(30144, 'Likes'), url_for(self.likes), thumb='services/trakt/likes.png')
            kdir.folder(L(30145, 'Collections'), self.collection, thumb='services/trakt/collection.png')
            kdir.folder(L(32036, 'History'), url_for(self.ulist, list_type='history'), thumb='services/trakt/history.png')
            kdir.folder(L(32033, 'Watchlist'), url_for(self.ulist, list_type='watchlist', sort=const.indexer.trakt.sort.watchlist), thumb='services/trakt/watchlist.png')
            kdir.folder(L(30146, 'Favorites'), url_for(self.ulist, list_type='favorites'), thumb='services/trakt/favorites.png')
            kdir.folder(L(30131, 'Recommendations'), self.recommendations, thumb='services/trakt/featured.png')
            kdir.folder(L(30147, 'Trending Lists'), url_for(self.user_general, list_type='trending'), thumb='services/trakt/trending.png')
            kdir.folder(L(30148, 'Popular Lists'), url_for(self.user_general, list_type='popular'), thumb='services/trakt/popular.png')
            kdir.folder(L(30546, 'Ratings'), url_for(self.rated), thumb='services/trakt/ratings.png')
            kdir.folder(L(30149, 'My Lists'), self.mine, thumb='services/trakt/my.png')

    @route
    def collections(self) -> None:
        """Trakt collection sub-menu."""
        with list_directory(view='sets') as kdir:
            if const.indexer.trakt.collection.mixed:
                kdir.folder(L(30150, 'Mixed Collection'), info_for(self.collection), thumb='services/trakt/collection.png')
            kdir.folder(L(30151, 'Movies Collection'), info_for(self.collection, media='movie'), thumb='services/trakt/collection.png')
            kdir.folder(L(30152, 'TV Shows Collection'), info_for(self.collection, media='show'), thumb='services/trakt/collection.png')

    @route
    def recommendations(self) -> None:
        """Trakt recommendations sub-menu."""
        with list_directory(view='sets') as kdir:
            if const.indexer.trakt.recommendation.mixed:
                kdir.folder(L(30153, 'Mixed Recommendations'), info_for(self.recommendation), thumb='services/trakt/featured.png')
            kdir.folder(L(30154, 'Movie Recommendations'), info_for(self.recommendation, media='movie'), thumb='services/trakt/featured.png')
            kdir.folder(L(30155, 'TV Show Recommendations'), info_for(self.recommendation, media='show'), thumb='services/trakt/featured.png')

    @route
    def mine(self, *, page: PathArg[int] = 1, user: str = 'me', likes: bool = False, media: Optional[MainMediaType] = None):
        """Show my lists and my likes."""
        with list_directory(view=const.indexer.trakt.mine.view) as kdir:
            kwargs = {} if media is None else {'media': media}
            # user lists extra added on the first page
            if page == 1:
                user_lists = sorted(trakt.user_lists(), key=lambda x: x.title.lower())
                for it in user_lists:
                    kdir.add(it, url=info_for(self.own_list, list_id=it.ffid, **kwargs), thumb='services/trakt/lists.png')
            # user likes
            if likes:
                likes_items = sorted(trakt.user_generic_list('likes', page=page, limit=100), key=lambda x: x.title.lower())
                for it in likes_items:
                    kdir.add(it, url=info_for(self.user_list, list_id=it.vtag.getUniqueID('trakt.list'),
                                              user=it.vtag.getUniqueID('trakt.user') or self.OFFICIAL, **kwargs), thumb='services/trakt/lists.png', menu=[])

    @route
    def likes(self, *, page: PathArg[int] = 1, user: str = 'me') -> None:
        """User Trakt generic user lists (likes etc.)."""
        with list_directory(view='sets') as kdir:
            for it in trakt.user_generic_list('likes'):
                kdir.add(it, url=info_for(self.user_list, list_id=it.vtag.getUniqueID('trakt.list'),
                                          user=it.vtag.getUniqueID('trakt.user') or self.OFFICIAL))

    @item_folder_route('collection/{media}', list_spec='trakt:collection')
    @pagination(const.trakt.page.limit)
    def collection(self,
                   req: FolderRequest,  # extra item added by @item_folder_route
                   /,
                   media: MainMediaTypeList = 'movie,show',
                   *,
                   page: PathArg[int] = 1,
                   user: str = 'me',
                   ) -> Folder:
        """User Trakt generic user lists (likes etc.)."""
        limit = const.trakt.page.limit  # items per page
        if not req.as_folder:
            limit = 0  # no limit for library [optimize]
        items = trakt.user_collection(media, user=user, chunk=limit//2, sort=const.indexer.trakt.sort.collections)
        # if req.as_folder:
        #     return Pagina(items, page=page, limit=limit)
        return Folder(items, page=page, limit=limit)

    @item_folder_route
    def recommendation(self,
                       req: FolderRequest,  # extra item added by @itemfolder_route
                       /, *,
                       media: PathArg[MainMediaTypeList] = 'movie,show',
                       page: PathArg[int] = 1,
                       ) -> Sequence[FFItem]:
        """User Trakt generic user lists (likes etc.)."""
        limit = const.trakt.page.limit  # items per page
        items = trakt.recommendations(media, limit=const.trakt.recommendations_limit, chunk=limit//2)  # limit=100 max recommendations (many pages)
        if req.as_library:
            return items
        return Pagina(items, page=page, limit=limit)

    @item_folder_route
    @pagination(const.trakt.page.limit)
    def calendarium(self, media: PathArg[MainMediaTypeList] = 'movie,show', *, page: PathArg[int] = 1):
        """Calendarium - mainly for tvshows."""
        def role(it: FFItem, index: int) -> str:
            return day_label(it.date, week_without_date=False)
        chunk = const.trakt.page.limit // 2 if page else 0
        items = trakt.calendarium(media, chunk=chunk)  # limit=100 max recommendations (many pages)
        return Folder(items, role_format=role)

    @item_folder_route('/{list_type}', list_spec='trakt:{list_type}')
    def ulist(self,
              req: FolderRequest,  # extra item added by @itemfolder_route
              /, *,
              list_type: UserListType,
              page: PathArg[int] = 1,
              media: Optional[RefType] = None,
              user: str = 'me',
              sort: Optional[TraktSortType] = 'trakt',
              ) -> Sequence[FFItem]:
        """User Trakt generic user lists (likes etc.)."""
        items = trakt.user_generic_list(list_type, media_type=media, page=page, user=user, sort=sort)
        if req.as_library:
            return items
        if fmt := const.indexer.trakt.lists.watched_date_format:
            for it in items:
                if date := it.vtag.getLastPlayedDateTime():
                    it.role = f'{date:{fmt}}'
        req.show.alone = True  # use nav.show_items(items, alone=True)
        return items

    def _user_list(self,
                   req: FolderRequest,  # extra item added by @itemfolder_route
                   /, *,
                   list_id: str,
                   page: PathArg[int] = 1,
                   user: str = 'me',
                   media: Optional[MainMediaType] = None,
                   sort: Optional[TraktSortType] = 'trakt',
                   ) -> Sequence[FFItem]:
        """Show Trakt user list."""
        if not req.pagination:
            sort = None
        if not user or user == self.OFFICIAL:
            items = trakt.the_list(list_id, page=page, media_type=media)
        else:
            items = trakt.user_list_items(list_id, user=user, media_type=media, sort=sort)
        return items

    @item_folder_route('/list/me/{list_id}', list_spec='trakt:user:{list_id}')
    @pagination(const.trakt.page.limit, api=ApiPage(size=20, min=10, max=100))
    def own_list(self,
                 req: FolderRequest,  # extra item added by @itemfolder_route
                 /, *,
                 list_id: str,
                 page: PathArg[int] = 1,
                 media: Optional[MainMediaType] = None,
                 sort: Optional[TraktSortType] = 'trakt',
                 ) -> Sequence[FFItem]:
        """Show Trakt own user list."""
        return self._user_list(req, list_id=list_id, page=page, user='me', media=media, sort=sort)

    @item_folder_route('/list/{user}/{list_id}')
    @pagination(const.trakt.page.limit, api=ApiPage(size=20, min=10, max=100))
    def user_list(self,
                  req: FolderRequest,  # extra item added by @itemfolder_route
                  /, *,
                  list_id: str,
                  page: PathArg[int] = 1,
                  user: str = 'me',
                  media: Optional[MainMediaType] = None,
                  sort: Optional[TraktSortType] = 'trakt',
                  ) -> Sequence[FFItem]:
        """Show Trakt other (liked) user list."""
        return self._user_list(req, list_id=list_id, page=page, user=user, media=media, sort=sort)

    @route('/{list_type}')
    def user_general(self, list_type: TraktUserGeneralListType, *, page: PathArg[int] = 1) -> None:
        items = trakt.user_general_lists(list_type, page=page)
        with list_directory(items, view='sets') as kdir:
            for it in items:
                kdir.add(it, url=info_for(self.user_list, list_id=it.vtag.getUniqueID('trakt.list'),
                                          user=it.vtag.getUniqueID('trakt.user') or self.OFFICIAL),
                         thumb='services/trakt/lists.png')

    @item_folder_route('/progress')
    def shows_progress(self, *, page: PathArg[int] = 1, limit: int = 0) -> Union[Folder, Sequence[FFItem]]:
        """List of watched shows with theirs progress."""

        def get_next(items: Sequence[FFItem], *, force: bool = False) -> Iterator[FFItem]:
            for it in items:
                ffinfo.find_last_next_episodes(it)
                if it.progress and (nxt := it.progress.next_episode):
                    # TODO: make a discussion if it is necessary  (show progress mark on next episode)
                    # nxt.progress = it.progress  # ???
                    if show == 'show':
                        yield it
                    elif show == 'season':
                        if sz := nxt.season_item:
                            yield sz
                        else:
                            yield it
                    else:  # show == 'episode'
                        nxt.descr_style = ffinfo.progress_descr_style(it.progress)
                        yield nxt
                elif force:
                    yield it

        # from ...ff.db.playback import get_playback
        # service_client.trakt_sync()
        show = const.indexer.tvshows.progress.show
        size = const.indexer.trakt.progress.shows_page_size
        hide_100 = not const.indexer.tvshows.progress.show_full_watched
        watched = trakt.user_generic_list('watched', media_type='show', hide_100=hide_100)
        items = Pagina(watched, page=page, limit=size)
        if show == 'show':
            return Folder(items, page=page)
        if page == 1 and limit > 0:
            items = Pagina(items[:limit])
        items = ffinfo.get_items(items, details=const.indexer.trakt.lists.info_details, progress=ffinfo.Progress.NO)  # w/o progress, the progress will recounted
        ffinfo.update_progress_list_progress(items)
        items = ItemList.from_list(get_next(items, force=True), items)
        if page == 1 and limit > 0:
            return Folder(items, alone=True, link=const.indexer.tvshows.progress.episode_folder, skip_ffinfo=True,
                          next_page=url_for(self.shows_progress, page=1))
        return Folder(items, page=page, alone=True, link=const.indexer.tvshows.progress.episode_folder, skip_ffinfo=True)

    # TODO: remove it
    @item_folder_route('/raw_progress')
    def shows_progress_api(self, *, page: PathArg[int] = 1, user: str = 'me') -> Folder:
        """List of watched shows with theirs progress."""

        def get_next(items: Sequence[FFItem]) -> Iterator[FFItem]:
            for it in items:
                ffinfo.find_last_next_episodes(it)
                if it.progress and (nxt := it.progress.next_episode):
                    # TODO: make a discussion if it is necessary  (show progress mark on next episode)
                    # nxt.progress = it.progress  # ???
                    nxt.descr_style = ffinfo.progress_descr_style(it.progress)
                    yield it

        preselect = False
        watched = trakt.user_generic_list('watched', media_type='show')
        if size := const.indexer.trakt.progress.shows_page_size:
            if preselect := (size and const.indexer.trakt.progress.shows_page_size_exact_match):
                watched = Pagina(get_next(ffinfo.get_en_skel_items(watched)), page=page, limit=size)
            else:
                watched = Pagina(watched, page=page, limit=size)
        items = ffinfo.get_items(watched, progress=ffinfo.Progress.NO)  # w/o progress, the progress will recounted
        if not preselect:
            items = get_next(items)
        paged = ItemList([it.progress.next_episode
                          for it in items if it and it.progress is not None and it.progress.next_episode],
                         page=page, total_pages=watched.total_pages)
        return Folder(paged, page=page, alone=True, link=const.indexer.tvshows.progress.episode_folder)

    @route('/rated')
    @route('/rated/{media}')
    def rated(self, *, media: Optional[UserRatingType] = None) -> None:
        """User Trakt rated sub-menu."""
        with list_directory(view='sets') as kdir:
            if media is None:
                kdir.folder(L(30547, 'Rated movies'), info_for(self.rated, media='movie'), thumb='services/trakt/ratings.png')
                kdir.folder(L(30548, 'Rated tvshows'), info_for(self.rated, media='show'), thumb='services/trakt/ratings.png')
                kdir.folder(L(30549, 'Rated seasons'), info_for(self.rated, media='season'), thumb='services/trakt/ratings.png')
                kdir.folder(L(30550, 'Rated episodes'), info_for(self.rated, media='episode'), thumb='services/trakt/ratings.png')
                kdir.folder(L(30551, 'Rated (mixed)'), info_for(self.rated, media='all'), thumb='services/trakt/ratings.png')
            else:
                from ...ff.ratings import NORM_RANGE
                kdir.folder(L(30552, 'All rated'), info_for(self.rated_items, media=media, rating='-'), thumb='services/trakt/ratings.png')
                for rating in reversed(NORM_RANGE):
                    label = L(30553, 'Rated {rating}').format(rating=rating)
                    kdir.folder(label, info_for(self.rated_items, media=media, rating=rating), thumb='services/trakt/ratings.png')

    @item_folder_route('/rated/{media}/{rating}/{__page}')
    @pagination(const.trakt.page.limit)
    def rated_items(self, *,
                    media: Optional[UserRatingType],
                    rating: Union[int, Literal['-'], None],
                    page: int = 1,
                    ) -> Sequence[FFItem]:
        """Show Trakt rated items by rating."""
        from ...ff.ratings import all_rating_services
        service = all_rating_services['trakt']
        if rating == '-':
            rating = None
        if media == 'all':
            media = None
        items = trakt.user_ratings(media, rating=rating)
        for it in items:
            it.vtag.setUserRating(service.from_service_rating(int(it.getProperty('trakt.user_rating') or 0)))
            if rating is None:
                it.role = L(30554, 'Rating: {rating}').format(rating=it.vtag.getUserRating())
        if rating is None:
            items = sorted(items, key=lambda x: x.vtag.getUserRating(), reverse=True)
        return ItemList.single(items)
