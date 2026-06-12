
from __future__ import annotations
from typing_extensions import Iterable
from ..ff.threads import Timer
from .base import BaseDialog
from .gui import CustomXmlRequest
from ..ff.log_utils import fflog
from ..ff.item import FFItem
from ..ff.ratings import rating_services, MediaRating, RatingService, NORM_MIN, NORM_MAX
from ..kolang import L
from .. import FAKE
from const import const
from cdefs import RatingServiceName


class RatingsDialog(BaseDialog[int]):
    """
    Dialog for handling user ratings.

    Returns:
      - 1..10 - the selected rating (int) on close
      - None  - if cancelled
      - (-1)  - for rating removal
    """

    XML = f'Ratings-{const.dialog.ratings.style}.xml'
    CUSTOMIZED_XML = True
    RATE_BUTTON = 40  # ID + N for the rate button N
    CANCEL_BUTTON = 51
    REMOVE_BUTTON = 52

    REMOVE_RATING = -1

    @classmethod
    def xml_request(cls, *,
                    ratings: Iterable[MediaRating],
                    services: dict[RatingServiceName, RatingService] | None = None,
                    **kwargs,
                    ) -> CustomXmlRequest | None:
        ratings = list(ratings)
        if ratings:
            average_rating = int(sum(r.rating for r in ratings) / len(ratings) + .5)
            fflog(f'>>>>>>>>>>>>>>>>>>>>> !!!!!! Average rating calculated: {average_rating} from {[r.rating for r in ratings]}')
        else:
            average_rating = int(NORM_MAX + NORM_MIN + .5) // 2 + NORM_MIN
        return CustomXmlRequest(
            xgui_parse=True,
            vars={
                'style': 'triple',
                'services': dict(rating_services if services is None else services),
                'ratings': ratings,
                'average_rating': average_rating,
                'MIN_RATING': NORM_MIN,
                'MAX_RATING': NORM_MAX,
                'RATING_RANGE': range(NORM_MIN, NORM_MAX + 1),
                'COLORS': const.dialog.ratings.colors,
            },
        )

    def __init__(self,
                 *args,
                 ffitem: FFItem,
                 title: str = L(30542, 'Rate'),
                 **kwargs,
                 ):
        fflog(f'Initializing RatingsDialog {ffitem=}')
        super().__init__(*args, **kwargs)
        self.ffitem: FFItem = ffitem
        self.setProperty('title', title)
        self.setProperty('media.title', self.get_media_label())
        self._close_timer: Timer | None = None

    def get_media_label(self) -> str:
        it = self.ffitem
        label = it.title
        if it.ref.is_season:
            label = f'{it.show_item.title} - Season {it.season}'
        elif it.ref.is_episode:
            label = f'{it.show_item.title} - {it.season}x{it.episode:02} - {it.title}'
        return label

    def _start_autoclose_timer(self):
        if const.dialog.ratings.autoclose_timeout > 0:
            self._cancel_autoclose_timer()  # Ensure any existing timer is cancelled
            self._close_timer = Timer(const.dialog.ratings.autoclose_timeout, self.close, args=[None])
            self._close_timer.start()

    def _cancel_autoclose_timer(self):
        if self._close_timer:
            self._close_timer.cancel()
            self._close_timer = None

    def on_init(self):
        average_rating: int = int(self._customised_data.request.vars['average_rating'])
        average_rating = max(NORM_MIN, min(NORM_MAX, average_rating))
        if control := self.get_control(self.RATE_BUTTON + average_rating):
            self.setFocus(control)
        self._start_autoclose_timer()

    def on_closing(self) -> None:
        self._cancel_autoclose_timer()
        return super().on_closing()

    def on_click(self, control_id: int):
        self._cancel_autoclose_timer()
        if control_id == self.CANCEL_BUTTON:
            self.close(None)
        elif control_id == self.REMOVE_BUTTON:
            self.close(self.REMOVE_RATING)
        elif self.RATE_BUTTON + NORM_MIN <= control_id <= self.RATE_BUTTON + NORM_MAX:
            rating = control_id - self.RATE_BUTTON
            self.close(rating)

    def on_number_button(self, button: int) -> bool:
        self._cancel_autoclose_timer()
        if button == 0 and NORM_MIN > 0:  # button 0 means rating 10
            button = 10
        if NORM_MIN <= button <= NORM_MAX:
            self.close(button)
            return True
        return False

    # Debug fake implementation (command-line testing).
    if FAKE:
        def do_modal(self) -> int | None:
            print('-----  Rating Dialog for item  -----')
            button = self._customised_data.request.vars['average_rating']
            print(f'-- Ref:    {self.ffitem.ref:a}')
            print(f'-- Title:  {self.ffitem.title}')
            print(f'-- Button: {button}')
            for srv in self._customised_data.request.vars['services'].values():
                if rat := next((r for r in self._customised_data.request.vars['ratings'] if r.service.name == srv.name), None):
                    print(f'-- {srv.name:8}: {rat.rating} ({rat.raw_rating})')
                else:
                    print(f'-- {srv.name:8}: ---')
            # rating = input(f'Enter rating ({NORM_MIN}-{NORM_MAX}, R-remove) [{button}]: ')
            rating = input(f'Enter rating ({NORM_MIN}-{NORM_MAX}, R-remove): ')
            if rating.lower() == 'r':
                return self.REMOVE_RATING
            try:
                rating_int = int(rating)
                if NORM_MIN <= rating_int <= NORM_MAX:
                    return rating_int
            except ValueError:
                pass
            return None


if __name__ == '__main__':
    from ..ff.ratings import RatingService, MediaRating

    def test1():
        a = RatingService(name='trakt', min=1, max=10)
        # b = RatingService(name='tmdb', min=.5, max=20, step=.5, offset=.5)
        b = RatingService(name='tmdb', min=1, max=10, step=.5)
        c = RatingService(name='rev', min=5, max=1, step=-1)

        # trakt
        assert a.from_service_rating(1) == 1
        assert a.from_service_rating(7) == 7
        assert a.from_service_rating(10) == 10
        assert a.to_service_rating(1) == 1
        assert a.to_service_rating(7) == 7
        assert a.to_service_rating(10) == 10

        # tmdb
        assert b.from_service_rating(.5) == 1
        assert b.from_service_rating(1) == 1
        assert b.from_service_rating(7) == 7
        assert b.from_service_rating(10) == 10
        assert b.to_service_rating(1) == 1.0
        assert b.to_service_rating(7) == 7.0
        assert b.to_service_rating(10) == 10.0

        assert c.from_service_rating(5) == 1
        assert c.from_service_rating(1) == 10
        assert c.to_service_rating(1) == 5
        assert c.to_service_rating(10) == 1

    def test2():
        a = MediaRating(raw_rating=7, service=rating_services['trakt'])
        b = MediaRating(raw_rating=7, service=rating_services['tmdb'])
        c = MediaRating(raw_rating=7, service=rating_services['mdblist'])
        assert a.service is rating_services['trakt']
        win = RatingsDialog(ffitem=FFItem(), ratings=[a, b, c])

    test1()
    # test2()
