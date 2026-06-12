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

# import re
from datetime import date as dt_date, timedelta
from typing import Optional, List, Sequence, TYPE_CHECKING

from ..ff.routing import route, subobject, info_for, PathArg
from ..ff.item import FFItem
from ..ff.tmdb import tmdb
from ..kolang import L
from ..api.tmdb import DiscoveryFilters
# from ..ff.log_utils import fflog, log
from .core import MainIndexer
from .folder import list_directory, item_folder_route, pagination
from .movies import Movies
from .tvshows import TVShows
from const import const
if TYPE_CHECKING:
    from ..ff.routing import EndpointInfo
    from ..api.tv import TvMovie


class AnimeMovies(Movies):
    """Anime movies navigation"""

    DISCOVERY_FILTERS = {
        'with_keywords': '210024',  # Anime
    }
    SEARCH_NAME = 'anime/movie'


class AnimeShows(TVShows):
    """Anime shows navigation"""

    DISCOVERY_FILTERS = {
        'with_keywords': '210024',  # Anime
    }
    TRAKT_FILTERS = {
        'genres': 'anime',
    }
    SEARCH_NAME = 'anime/show'

    @item_folder_route('/popular', limit=const.indexer.tvshows.discovery_scan_limit)
    @pagination
    def popular(self, *, page: PathArg[int] = 1) -> Sequence[FFItem]:
        """Popular anime shows"""

        today = self.today()
        filters: DiscoveryFilters = {
            'sort_by': 'popularity.desc',
            'first_air_date': tmdb.Date <= today,
            'vote_count': tmdb.VoteCount >= 100,
            'with_watch_monetization_types': 'flatrate|free|ads|rent|buy',
            'without_genres': '10763,10764,10767',
        }
        if const.indexer.anime.region:
            filters['watch_region'] = const.indexer.anime.region
        return self.discover_items(page=page, **filters)

    @item_folder_route('/aired', limit=const.indexer.tvshows.discovery_scan_limit)
    @pagination
    def aired_today(self, *, page: PathArg[int] = 1) -> Sequence[FFItem]:
        """Last aired (14 days)"""
        today = self.today()
        old = today - timedelta(days=const.indexer.anime.aired.last_days)
        filters: DiscoveryFilters = {
            'sort_by': 'popularity.desc',
            'air_date': tmdb.Date.range(old, tmdb.Today),
            'vote_count': tmdb.VoteCount >= 20,
            'with_watch_monetization_types': 'flatrate|free|ads|rent|buy',
        }
        if const.indexer.anime.region:
            filters['watch_region'] = const.indexer.anime.region
        self.discover(page=page, **filters)
        return self.discover_items(page=page, **filters)


class Anime(MainIndexer):
    """Anime navigation"""

    @route('/')
    def home(self) -> None:
        """Create root / main menu."""
        with list_directory(view='addons', icon='anime/main.png') as kdir:
            kdir.folder(L(30514, 'Popular movies'), info_for(self.movie.popular), thumb='anime/mostpopular.png')
            kdir.folder(L(30515, 'Popular shows'), info_for(self.show.popular), thumb='anime/mostpopular.png')
            kdir.folder(L(32017, 'People Watching'), info_for(self.show.trending), thumb='anime/peoplewatching.png')
            kdir.folder(L(30516, 'Premieres'), info_for(self.show.premiere), thumb='anime/new.png')
            kdir.folder(L(30517, 'Last aired'), info_for(self.show.aired_today), thumb='anime/airingtoday.png')
            kdir.folder(L(32012, 'Year'), info_for(self.show.year), thumb='anime/year.png')
            kdir.folder(L(30263, 'Search movies'), self.movie.search, thumb='movies/search.png')
            kdir.folder(L(30529, 'Search tvshows'), self.show.search, thumb='tvshows/search.png')

    @subobject
    def movie(self) -> AnimeMovies:
        """Menu /anime/movie"""
        return AnimeMovies()

    @subobject
    def show(self) -> AnimeShows:
        """Menu /anime/show"""
        return AnimeShows()
