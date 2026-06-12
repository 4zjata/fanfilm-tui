
from typing import Optional, Union, TYPE_CHECKING
from typing_extensions import Literal
from ...defs import MainMediaType, Pagina, ItemList
from ..core import Indexer
from ...ff.routing import route, info_for
from ..folder import list_directory, item_folder_route, pagination
from ...api.mdblist import mdblist
from ...kolang import L
from const import const
if TYPE_CHECKING:
    from typing import Sequence
    from ...ff.menu import KodiDirectory
    from ...ff.item import FFItem

UserRatingType = Literal['movie', 'show', 'season', 'episode', 'all']


class MDBListIndexer(Indexer):
    """Indexer for MDBList service (user lists)."""

    @route('/')
    def home(self) -> None:
        """User MDBList lists."""
        with list_directory(view='sets') as kdir:
            # kdir.folder(L(30144, 'Likes'), info_for(self.likes), thumb='highly-rated.png')  # Not in API yet
            kdir.folder(L(32033, 'Watchlist'), info_for(self.watchlist), thumb='services/mdblist/watchlist.png')
            kdir.folder(L(30148, 'Popular Lists'), info_for(self.top_lists), thumb='services/mdblist/popular.png')
            kdir.folder(L(30546, 'Ratings'), info_for(self.rated), thumb='services/mdblist/ratings.png')
            if const.indexer.mdblist.root.flat:
                self._add_mine(kdir, page=0)
            else:
                kdir.folder(L(30149, 'My Lists'), self.mine, thumb='services/mdblist/my.png')

    @route('/mine/{__page}')
    def mine(self, *, page: int = 1, user: Optional[str] = None, media: Optional[MainMediaType] = None):
        """Show my lists and my likes."""
        with list_directory(view=const.indexer.mdblist.mine.view) as kdir:
            self._add_mine(kdir, page=page, user=user, media=media)

    @route('/mine/{__page}')
    def _add_mine(self, kdir: 'KodiDirectory', *, page: int = 1, user: Optional[str] = None, media: Optional[MainMediaType] = None):
        """Show my lists and my likes."""
        items = sorted(mdblist.user_lists(user=user, media=media), key=lambda x: x.title.lower())
        items = Pagina(items, page=page, limit=const.indexer.mdblist.page_size)
        for it in items:
            kdir.add(it, url=info_for(self.list_items, list_id=it.ffid, media=media), thumb='services/mdblist/lists.png')

    @item_folder_route('/watchlist', list_spec='mdblist:watchlist')
    def watchlist(self, *, media: Optional[MainMediaType] = None):
        """Show watchlist."""
        return mdblist.watchlist_items(media=media)

    @item_folder_route('/list/{list_id}', list_spec='mdblist:user:{list_id}')
    def list_items(self, list_id: int, *, page: int = 1, media: Optional[MainMediaType] = None):
        """Show items in a list."""
        return mdblist.list_items(list_id=list_id, page=page, media=media)

    @route('/top/{__page}')
    def top_lists(self, *, page: int = 1, media: Optional[MainMediaType] = None):
        """Show top lists."""
        # return mdblist.top_lists(media=media)
        with list_directory(view=const.indexer.mdblist.top.view) as kdir:
            items = mdblist.top_lists(media=media)
            items = Pagina(items, page=page, limit=const.indexer.mdblist.page_size)
            for it in items:
                kdir.add(it, url=info_for(self.list_items, list_id=it.ffid, media=media), thumb='services/mdblist/lists.png')

    @route('/rated')
    @route('/rated/{media}')
    def rated(self, *, media: Optional[UserRatingType] = None) -> None:
        """User MDBList rated sub-menu."""
        with list_directory(view='sets') as kdir:
            if media is None:
                kdir.folder(L(30547, 'Rated movies'), info_for(self.rated, media='movie'), thumb='services/mdblist/ratings.png')
                kdir.folder(L(30548, 'Rated tvshows'), info_for(self.rated, media='show'), thumb='services/mdblist/ratings.png')
                kdir.folder(L(30549, 'Rated seasons'), info_for(self.rated, media='season'), thumb='services/mdblist/ratings.png')
                kdir.folder(L(30550, 'Rated episodes'), info_for(self.rated, media='episode'), thumb='services/mdblist/ratings.png')
                kdir.folder(L(30551, 'Rated (mixed)'), info_for(self.rated, media='all'), thumb='services/mdblist/ratings.png')
            else:
                from ...ff.ratings import NORM_RANGE
                kdir.folder(L(30552, 'All rated'), info_for(self.rated_items, media=media, rating='-'), thumb='services/mdblist/ratings.png')
                for rating in reversed(NORM_RANGE):
                    label = L(30553, 'Rated {rating}').format(rating=rating)
                    kdir.folder(label, info_for(self.rated_items, media=media, rating=rating), thumb='services/mdblist/ratings.png')

    @item_folder_route('/rated/{media}/{rating}/{__page}')
    @pagination
    def rated_items(self,
                    *,
                    media: Optional[UserRatingType],
                    rating: Union[int, Literal['-'], None],
                    page: int = 1,
                    ) -> 'Sequence[FFItem]':
        """Show MDBList rated items by rating."""
        from ...ff.ratings import all_rating_services
        service = all_rating_services['mdblist']
        if rating == '-':
            rating = None
        if media == 'all':
            media = None
        items = mdblist.user_ratings()
        if media is not None:
            items = [it for it in items if it.ref.real_type == media]
        for it in items:
            it.vtag.setUserRating(service.from_service_rating(int(it.getProperty('mdblist.user_rating') or 0)))
            if rating is None:
                it.role = L(30554, 'Rating: {rating}').format(rating=it.vtag.getUserRating())
        if rating is None:
            items = sorted(items, key=lambda x: x.vtag.getUserRating(), reverse=True)
        else:
            items = [it for it in items if it.vtag.getUserRating() == rating]
        return ItemList.single(items)
