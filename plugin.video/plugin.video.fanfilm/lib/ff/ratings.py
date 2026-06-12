
from __future__ import annotations
from typing_extensions import Literal, Mapping, Collection, Iterable, TypeVar, Generic, TypedDict, cast, TYPE_CHECKING
from typing_extensions import Union  # Union for py3.8
# from numbers import Number
from decimal import Decimal
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor
from attrs import define, frozen
from .tricks import round_to_step
from .log_utils import fflog, fflog_exc
from ..kolang import L
from const import const
if TYPE_CHECKING:
    from cdefs import RatingServiceName, RatingAction
    from ..defs import FFRef, RefType, MediaRef
    from ..api.tmdb import TmdbApi
    from ..api.trakt import TraktApi
    from ..api.mdblist import MdbList
# from typing_extensions import reveal_type  # TESTING


# N = TypeVar('N', bound=int | float | Decimal, contravariant=True)
N = TypeVar('N', bound=Union[int, float, Decimal], contravariant=True)  # Union for py3.8
# T = TypeVar('T', bound=Number, default=int)
# T = TypeVar('T', bound=int | float | Decimal, default=int)
# T = TypeVar('T', bound=int | float, default=int)
T = TypeVar('T', int, float, Decimal, default=int)

NORM_MIN = 1
NORM_MAX = 10
RatingType = Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

NORM_LEN = NORM_MAX - NORM_MIN
NORM_RANGE = range(NORM_MIN, NORM_MAX + 1)

SERVICE_ENABLED_SETTING = '{service}.rating'


@define
class MediaRating(Generic[T]):
    """Media rating with normalization."""

    #: The raw service rating value before any normalization.
    raw_rating: T
    #: The rating service.
    service: RatingService[T]

    @property
    def rating(self) -> RatingType:
        """The normalized rating value (1-10 scale)."""
        return self.service.from_service_rating(self.raw_rating)

    @rating.setter
    def rating(self, value: int):
        self.raw_rating = self.service.to_service_rating(value)


@frozen(kw_only=True)
class RatingService(Generic[T]):
    """Rating service configuration."""

    name: RatingServiceName
    title: str = ''
    type: type[T] = int  # type: ignore[reportAssignmentType]
    min: T = type(1)
    max: T = type(10)
    step: T = type(1)
    offset: T = type(0)
    real_types: Collection[RefType] = ()
    settings: str | bool = SERVICE_ENABLED_SETTING
    color: str = ''

    def __attrs_post_init__(self):
        """Post-initialization adjustments."""
        if not self.title:
            object.__setattr__(self, 'title', self.name.capitalize())

    def from_service_rating(self, rating: T) -> RatingType:
        """Normalize a rating (from service's scale to general)."""
        # if self.min >= self.max:
        #     return NORM_MIN
        # Normalize to a 1-10 scale
        normalized = NORM_MIN + (rating - self.min - self.offset) * NORM_LEN / (self.max - self.min)
        norm = int(max(NORM_MIN, min(NORM_MAX, normalized)))
        return norm  # type: ignore[return-value]

    def to_service_rating(self, rating: int) -> T:
        """Denormalize a rating (from general scale to service's)."""
        # if self.min >= self.max:
        #     return self.min
        # Normalize to the service's scale (from 1-10 scale)
        normalized = self.type(round_to_step(self.min + (rating - NORM_MIN) * (self.max - self.min) / NORM_LEN, self.step))
        if self.step < 0:
            return min(self.min, max(self.max, normalized))
        return max(self.min, min(self.max, normalized))

    def get_raw_rating(self, ref: FFRef) -> MediaRating | T | None:
        """Get service rating for the given media reference. To override in subclasses."""
        return None

    def set_raw_rating(self, ref: FFRef, raw_rating: T) -> bool:
        """Set service rating for the given media reference. To override in subclasses."""
        return False

    def remove_raw_rating(self, ref: FFRef) -> bool:
        """Remove rating for the given media reference. To override in subclasses."""
        return False

    def list_raw_ratings(self, refs: Iterable[FFRef]) -> Mapping[MediaRef, MediaRating[T] | T]:
        raise NotImplementedError()

    def get_rating(self, ref: FFRef) -> MediaRating[T] | None:
        """Get normalized rating for the given media reference."""
        if ref.ref.real_type not in self.real_types:
            return None
        rating = self.get_raw_rating(ref)
        if rating is None:
            return None
        if isinstance(rating, MediaRating):
            assert rating.service is self
            return rating  # type: ignore[return-value]
        return MediaRating(raw_rating=rating, service=self)

    def set_rating(self, ref: FFRef, rating: int | MediaRating[T]) -> bool:
        """Set normalized rating for the given media reference."""
        if ref.ref.real_type not in self.real_types:
            return False
        if isinstance(rating, MediaRating):
            return self.set_raw_rating(ref, rating.raw_rating)
        return self.set_raw_rating(ref, self.to_service_rating(rating))

    def remove_rating(self, ref: FFRef) -> bool:
        """Remove rating for the given media reference."""
        if ref.ref.real_type not in self.real_types:
            return False
        return self.remove_raw_rating(ref)

    def list_ratings(self, refs: Iterable[FFRef]) -> Mapping[MediaRef, MediaRating[T]]:
        """List ratings for the given media references."""
        refs = [ref for ref in refs if ref.ref.real_type in self.real_types]
        try:
            raw_ratings = self.list_raw_ratings(refs)
        except NotImplementedError:
            pass
        else:
            # got all ratings at once
            return {ref: rating if isinstance(rating, MediaRating) else MediaRating(raw_rating=rating, service=self)
                    for ref, rating in raw_ratings.items()}
        # fallback: get ratings one by one
        with ThreadPoolExecutor() as ex:
            futures = {ref.ref: ex.submit(self.get_rating, ref) for ref in refs}
        return {ref: rating for ref, fut in futures.items() if (rating := fut.result()) is not None}


@frozen
class TraktRatingService(RatingService[int]):
    """Trakt rating service."""

    name: RatingServiceName = 'trakt'
    type: type[int] = int
    min: int = 1
    max: int = 10
    step: int = 1
    real_types: Collection[RefType] = {'movie', 'show', 'season', 'episode'}

    def _instance(self) -> TraktApi:
        from .info import ffinfo
        if ffinfo.trakt is None:
            from .trakt import trakt
        else:
            trakt = ffinfo.trakt
        return trakt

    def get_raw_rating(self, ref: FFRef) -> MediaRating | int | None:
        """Get service rating for the given media reference."""
        ref = ref.ref
        rtype = ref.real_type
        if TYPE_CHECKING:
            assert rtype in ('movie', 'show', 'season', 'episode')
        trakt = self._instance()
        items = trakt.user_ratings(rtype)
        for it in items:
            if it.ref == ref and (rating := it.getProperty('trakt.user_rating')):
                return int(rating)

    def set_raw_rating(self, ref: FFRef, raw_rating: int) -> bool:
        """Set service rating for the given media reference."""
        trakt = self._instance()
        ok, _ = trakt.add_user_ratings([ref], rating=raw_rating)
        return ok

    def remove_raw_rating(self, ref: FFRef) -> bool:
        """Remove rating for the given media reference. To override in subclasses."""
        trakt = self._instance()
        ok, _ = trakt.remove_user_ratings([ref])
        return ok

    def list_raw_ratings(self, refs: Iterable[FFRef]) -> Mapping[MediaRef, MediaRating[int] | int]:
        rtypes = {ref.ref.real_type for ref in refs}
        rtype = next(iter(rtypes)) if len(rtypes) == 1 else None
        if TYPE_CHECKING:
            assert rtype in ('movie', 'show', 'season', 'episode', None)
        trakt = self._instance()
        want = {ref.ref for ref in refs}
        return {it.ref: int(rating) for it in trakt.user_ratings(rtype) if it.ref in want and (rating := it.getProperty('trakt.user_rating'))}


@frozen
class TmdbRatingService(RatingService[float]):
    """TMDB rating service."""

    name: RatingServiceName = 'tmdb'
    type: type[float] = float
    min: float = 1
    max: float = 10.0
    step: float = 0.5   # TMDB: 0.5 is needed for first rating, then 0.25 could be used for updates
    real_types: Collection[RefType] = ('movie', 'show', 'episode')

    def _instance(self) -> TmdbApi:
        from .info import ffinfo
        if ffinfo.tmdb is None:
            from .tmdb import tmdb
        else:
            tmdb = ffinfo.tmdb
        return tmdb

    def get_raw_rating(self, ref: FFRef) -> MediaRating | float | None:
        """Get service rating for the given media reference."""
        rating = self._instance().user_ref_rating(ref)
        if rating is not None:
            return float(rating)
        return None

    def set_raw_rating(self, ref: FFRef, raw_rating: float) -> bool:
        """Set service rating for the given media reference."""
        tmdb = self._instance()
        return tmdb.add_user_rating(ref, rating=raw_rating)

    def remove_raw_rating(self, ref: FFRef) -> bool:
        """Remove rating for the given media reference. To override in subclasses."""
        tmdb = self._instance()
        return tmdb.remove_user_rating(ref)


@frozen
class MdblistRatingService(RatingService[int]):
    """MDBList rating service."""

    name: RatingServiceName = 'mdblist'
    type: type[int] = int
    min: int = 1
    max: int = 10
    step: int = 1
    real_types: Collection[RefType] = ('movie', 'show', 'season', 'episode')

    def _instance(self) -> MdbList:
        from ..api.mdblist import mdblist
        return mdblist

    def get_raw_rating(self, ref: FFRef) -> MediaRating | int | None:
        """Get service rating for the given media reference."""
        ref = ref.ref
        mdblist = self._instance()
        items = mdblist.user_ratings()
        for it in items:
            if it.ref == ref and (rating := it.getProperty('mdblist.user_rating')):
                return int(rating)

    def set_raw_rating(self, ref: FFRef, raw_rating: int) -> bool:
        """Set service rating for the given media reference."""
        mdblist = self._instance()
        ok, _ = mdblist.add_user_ratings([ref], rating=raw_rating)
        return ok

    def remove_raw_rating(self, ref: FFRef) -> bool:
        """Remove rating for the given media reference. To override in subclasses."""
        mdblist = self._instance()
        ok, _ = mdblist.remove_user_ratings([ref])
        return ok

    def list_raw_ratings(self, refs: Iterable[FFRef]) -> Mapping[MediaRef, MediaRating[int] | int]:
        mdblist = self._instance()
        want = {ref.ref for ref in refs}
        return {it.ref: int(rating) for it in mdblist.user_ratings() if it.ref in want and (rating := it.getProperty('mdblist.user_rating'))}


# AnyRatingService = RatingService[int] | RatingService[float] | RatingService[Decimal]
AnyRatingService = Union[RatingService[int], RatingService[float], RatingService[Decimal]]  # Union for py3.8


# class RatingServiceDict(TypedDict, extra_items=RatingService):
class RatingServiceDict(TypedDict, extra_items=AnyRatingService):
    trakt: TraktRatingService
    tmdb: TmdbRatingService
    mdblist: MdblistRatingService


#: Avaliable rating services.
all_rating_services: RatingServiceDict = {
    'trakt':   TraktRatingService(title='Trakt'),
    'tmdb':    TmdbRatingService(title='TMDB'),
    'mdblist': MdblistRatingService(title='MDBList'),

    # --- TESTS and DEBUG ---
    # RatingService(name='x100', min=1, max=100),
    # RatingService(name='bin', min=0, max=1),
    # RatingService(name='float', min=0, max=1, step=.02),
    # RatingService(name='neg', min=-5, max=5),
    # RatingService(name='rev', min=10, max=1, step=-1),
}
# all_rating_services: RatingServiceDict = {srv.name: srv for srv in ()

#: Enabled rating services.
rating_services: dict[RatingServiceName, AnyRatingService] = cast('dict[RatingServiceName, AnyRatingService]', (
    {} if not const.ratings.enabled else
    all_rating_services if const.ratings.enabled is True else
    {srv.name: srv for name in const.ratings.enabled if (srv := all_rating_services.get(name))}
))


# assign color to each service for UI
for srv, col in zip(rating_services.values(), cycle(const.dialog.ratings.colors)):
    object.__setattr__(srv, 'color', col)


def enabled_rating_services(*,
                            action: RatingAction | None,
                            setting: str | bool = True,
                            logged: bool = True,
                            ref: FFRef | None = None,
                            media_type: RefType | None = None,
                            ) -> dict[RatingServiceName, AnyRatingService]:
    """Get enabled rating services. `setting` can be a boolean or a setting name to evaluate, could contain subname {service}."""
    from .settings import settings

    def is_enabled(srv: AnyRatingService) -> bool:
        if action is not None:
            allowed = const.ratings.filter.get(('*', '*'), True)
            allowed = const.ratings.filter.get(('*', action), allowed)
            allowed = const.ratings.filter.get((srv.name, '*'), allowed)
            allowed = const.ratings.filter.get((srv.name, action), allowed)
            if not allowed:
                return False
        if linfo is not None:
            try:
                if not linfo.enabled(srv.name):
                    return False
            except AttributeError:
                pass
        if ref is not None and ref.ref.real_type not in srv.real_types:
            return False
        if media_type is not None and media_type not in srv.real_types:
            return False
        for setting_expr in (srv.settings, setting):
            try:
                if not settings.eval(setting_expr, {'service': srv.name}):
                    return False
            except KeyError:
                if isinstance(setting_expr, str):
                    from simpleeval.format import safe_xformat
                    fflog.info(f'No setting found for rating service enable check: {safe_xformat(setting_expr, service=srv.name)}')
                pass  # no kodi setting, assume enabled
            except Exception:
                fflog_exc()
        return True

    if logged:
        from ..indexers.lists import ListsInfo
        linfo = ListsInfo()
    else:
        linfo = None
    return {srv.name: srv for srv in rating_services.values() if is_enabled(srv)}


def rate_media(item: FFRef,
               *,
               action: RatingAction | None,
               setting: str | bool = True,
               if_not_rated: bool = False,
               ) -> bool:
    """Show rating dialog and set or remove rating for the given media."""
    from concurrent.futures import ThreadPoolExecutor
    from .control import max_thread_workers
    from .item import FFItem
    from ..windows.ratings import RatingsDialog
    # No rating services enabled.
    # services = enabled_rating_services(setting=f'{{service}}.rate_after_watch.{item.ref.real_type}')
    services = enabled_rating_services(action=action, setting=setting, ref=item)
    if not services:
        fflog('No rating services enabled')
        return False
    # Get FFItem for the given item (if not given).
    if not isinstance(item, FFItem):
        from .info import ffinfo
        if (found := ffinfo.find_item(item)) is None:
            return False
        item = found
    # Get existing ratings (concurrently).
    with ThreadPoolExecutor(max_workers=max_thread_workers()) as ex:
        futures = [ex.submit(srv.get_rating, item) for srv in services.values()]
    ratings = [fut.result() for fut in futures if fut.result() is not None]
    # Skip if already rated.
    if if_not_rated and ratings:
        fflog.debug(f'Media {item.ref:a} already rated, skipping rating dialog')
        return False
    # Show rating dialog.
    win = RatingsDialog(ffitem=item, ratings=ratings, services=services)
    rating = win.do_modal()
    # Cancelled.
    if rating is None:
        return False
    if rating == RatingsDialog.REMOVE_RATING:
        # Remove rating.
        with ThreadPoolExecutor(max_workers=max_thread_workers()) as ex:
            futures = [ex.submit(srv.remove_rating, item) for srv in services.values()]
        ok = any(fut.result() for fut in futures)
    else:
        # Set rating.
        with ThreadPoolExecutor(max_workers=max_thread_workers()) as ex:
            futures = [ex.submit(srv.set_rating, item, rating) for srv in services.values()]  # type: ignore[arg-type]
        ok = any(fut.result() for fut in futures)
    if const.dialog.ratings.notification:
        from .kotools import Notification
        if rating == RatingsDialog.REMOVE_RATING:
            msg = L(30341, 'Rating removed for {title}').format(title=item.title)
        else:
            msg = L(30342, 'Rating {rating} set for {title}').format(rating=rating, title=item.title)
        if ok:
            Notification(L(30343, 'Rating updated'), msg, sound=False).show()
        else:
            Notification(L(30344, 'Rating FAILED'), msg, icon='WARNING').show()
    return ok
