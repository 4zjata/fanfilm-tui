from typing import Optional, Union, Sequence, TYPE_CHECKING
from typing_extensions import Literal
from ..core import Indexer
from ..folder import item_folder_route, pagination, ApiPage, FolderRequest, list_directory, Folder
from ...ff.routing import route, info_for, PathArg
from ...ff.menu import KodiDirectory
from ...ff.tmdb import tmdb
from ...ff.log_utils import fflog
from ...defs import MainMediaType, MainMediaTypeList, ItemList
from ...kolang import L
# from ...ff.log_utils import fflog
from const import const
if TYPE_CHECKING:
    from ...ff.item import FFItem

Sort = Literal['name', 'created_at', 'updated_at', 'item_count']  # not used yet
UserRatingType = Literal['movie', 'show', 'episode', 'all']


class TmdbLists(Indexer):

    @route('/')
    def home(self) -> None:
        """User TMDB lists."""
        with list_directory(view='sets') as kdir:
            kdir.folder(L(30146, 'Favorites'), self.favorites, thumb='services/tmdb/favorites.png', position='top')
            kdir.folder(L(32033, 'Watchlist'), self.watchlists, thumb='services/tmdb/watchlist.png', position='top')
            kdir.folder(L(30546, 'Ratings'), info_for(self.rated), thumb='services/tmdb/ratings.png')
            if const.indexer.tmdb.root.flat:
                self._mine(kdir)
            else:
                kdir.folder(L(30149, 'My Lists'), self.mine, thumb='services/tmdb/my.png')

    @route
    def favorites(self) -> None:
        with list_directory(view='sets') as kdir:
            if const.indexer.tmdb.favorites.mixed:
                kdir.folder(L(30156, 'Mixed Favorites'), info_for(self.favorite), thumb='services/tmdb/favorites.png')
            kdir.folder(L(30157, 'Favorite Movies'), info_for(self.favorite, media='movie'), thumb='services/tmdb/favorites.png')
            kdir.folder(L(30158, 'Favorite TV Shows'), info_for(self.favorite, media='show'), thumb='services/tmdb/favorites.png')

    @route
    def watchlists(self) -> None:
        with list_directory(view='sets') as kdir:
            if const.indexer.tmdb.watchlist.mixed:
                kdir.folder(L(30159, 'Mixed Watchlist'), info_for(self.watchlist), thumb='services/tmdb/watchlist.png')
            kdir.folder(L(30160, 'Watchlist Movies'), info_for(self.watchlist, media='movie'), thumb='services/tmdb/watchlist.png')
            kdir.folder(L(30161, 'Watchlist TV Shows'), info_for(self.watchlist, media='show'), thumb='services/tmdb/watchlist.png')

    def _mine(self, kdir: KodiDirectory, *, page: PathArg[int] = 1, media: Optional[MainMediaType] = None, sort: Optional[Sort] = 'name') -> None:
        """User TMDB lists."""
        kwargs = {} if media is None else {'media': media}

        # with kdir.item_mutate() as mutate:
        #     for it in tmdb.user_lists(page=page):
        #         kdir.add(it, url=info_for(self.user_list, list_id=it.ffid, **kwargs), thumb='services/tmdb/lists.png',)
        #     # sort via mutate, to skip Favorites and Watchlist if root flat is True
        #     mutate.isort('label')

        for it in tmdb.user_lists(page=page):
            kdir.add(it, url=info_for(self.user_list, list_id=it.ffid, **kwargs), thumb='services/tmdb/lists.png',)

        # test sortowania
        from xbmcplugin import addSortMethod, SORT_METHOD_LABEL
        addSortMethod(kdir.handle, SORT_METHOD_LABEL, '%L')

    @route
    def mine(self, *, page: PathArg[int] = 1, media: Optional[MainMediaType] = None) -> None:
        """User TMDB lists."""
        with list_directory(view=const.indexer.tmdb.mine.view) as kdir:
            self._mine(kdir, page=page, media=media)

    @item_folder_route('/list/{list_id}', list_spec='tmdb:user:{list_id}')
    @pagination(api=ApiPage(size=20))
    def user_list(self, list_id: int, *, page: PathArg[int] = 1, media: Optional[MainMediaType] = None):
        """Show TMDB user list."""
        if media and const.indexer.tmdb.mine.align_list_pages:
            from ...api import depaginate
            with depaginate(tmdb) as api:
                items = api.user_list_items(list_id)
                return ItemList.single([it for it in items if it.type == media])
        items = tmdb.user_list_items(list_id, page=page)
        if media:
            items = items.with_content([it for it in items if it.type == media])
        return items

    @item_folder_route('favorite/{media}', list_spec='tmdb:favorites')
    @pagination(api=ApiPage(size=20))
    def favorite(self, media: MainMediaTypeList = 'movie,show', *, page: PathArg[int] = 1):
        """Show TMDB user list."""
        return tmdb.user_general_lists('favorite', media, page=page, chunk=10)

    @item_folder_route('watchlist/{media}', list_spec='tmdb:watchlist')
    @pagination(api=ApiPage(size=20))
    def watchlist(self, media: MainMediaTypeList = 'movie,show', *, page: PathArg[int] = 1):
        """Show TMDB user list."""
        return tmdb.user_general_lists('watchlist', media, page=page, chunk=10)

    @route('/rated')
    @route('/rated/{media}')
    def rated(self, *, media: Optional[UserRatingType] = None) -> None:
        """User TMDB rated sub-menu."""
        with list_directory(view='sets') as kdir:
            if media is None:
                kdir.folder(L(30547, 'Rated movies'), info_for(self.rated, media='movie'), thumb='services/tmdb/ratings.png')
                kdir.folder(L(30548, 'Rated tvshows'), info_for(self.rated, media='show'), thumb='services/tmdb/ratings.png')
                kdir.folder(L(30550, 'Rated episodes'), info_for(self.rated, media='episode'), thumb='services/tmdb/ratings.png')
                kdir.folder(L(30551, 'Rated (mixed)'), info_for(self.rated, media='all'), thumb='services/tmdb/ratings.png')
            else:
                from ...ff.ratings import NORM_RANGE
                kdir.folder(L(30552, 'All rated'), info_for(self.rated_items, media=media, rating='-'), thumb='services/tmdb/ratings.png')
                for rating in reversed(NORM_RANGE):
                    label = L(30553, 'Rated {rating}').format(rating=rating)
                    kdir.folder(label, info_for(self.rated_items, media=media, rating=rating), thumb='services/tmdb/ratings.png')

    @item_folder_route('/rated/{media}/{rating}/{__page}')
    @pagination
    def rated_items(self, *,
                    media: Optional[UserRatingType],
                    rating: Union[int, Literal['-'], None],
                    ) -> 'Sequence[FFItem]':
        """Show TMDB rated items by rating."""
        from time import monotonic
        from ...ff.ratings import all_rating_services
        service = all_rating_services['tmdb']
        if rating == '-':
            rating = None
        if media == 'all':
            media = None
        T = monotonic()
        items = tmdb.user_ratings(media, page=0)
        T = monotonic() - T
        fflog(f'[TMDB] Got {len(items)} rated items for media={media} and rating={rating} in {T:.3f}s')
        for it in items:
            it.vtag.setUserRating(service.from_service_rating(float(it.getProperty('tmdb.user_rating') or 0)))
            if rating is None:
                it.role = L(30554, 'Rating: {rating}').format(rating=it.vtag.getUserRating())
        if rating is None:
            items = sorted(items, key=lambda x: x.vtag.getUserRating(), reverse=True)
        else:
            items = [it for it in items if it.vtag.getUserRating() == rating]
        return ItemList.single(items)
