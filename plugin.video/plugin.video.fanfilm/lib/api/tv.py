"""
Simple TV scrappers.

Now supports:
- filmweb.pl
- programtv.onet.pl
"""

from __future__ import annotations
from typing import Any, Sequence, Iterator, Iterable, ClassVar, TYPE_CHECKING
from typing_extensions import Literal, TypeAlias, Pattern, Match, TypeVar
import re
from contextlib import suppress
from html import unescape
import json
from attrs import define, field, fields

from ..defs import Pagina, ItemList, VideoIds, MediaRef
from ..ff.item import FFItem
from .tmdb import TmdbApi
from ..ff.tmdb import tmdb
from ..ff.calendar import fromisoformat, datetime, dt_date, dt_time, timedelta
from ..ff.control import max_thread_workers
from ..ff import requests
from ..ff.requests import RequestsPoolExecutor
from ..ff.log_utils import fflog
from ..ff.debug.timing import logtime
from ..ff.types import PagedItemList
# from ..ff.tricks import str_removeprefix
from const import const
if TYPE_CHECKING:
    from cdefs import ProgramTvService
    from ..ff.types import JsonData


@define(kw_only=True)
class TvMovie:
    #: FilmWeb ID
    id: str
    #: Title (PL)
    title: str
    #: Movie year
    year: int
    #: TV air time.
    time: datetime
    #: TV air date, uses const.indexer.movies.tv.day_first_hour.
    date: dt_date = field(init=False)
    #: TV channel.
    channel: str
    #: Image URL.
    image: str
    #: URL to media details.
    url: str = ''
    #: Description
    descr: str = ''
    #: Movie duration is seconds.
    duration: int = 0
    #: Country codes (iso 3166-1)
    countries: set[str] = field(factory=set)
    #: Extra info (used by service)
    service_data: dict[str, Any] | None = None
    #: FF media reference if any.
    ref: MediaRef | None = None
    #: Movie ratings.
    ratings: dict[str, tuple[float, int]] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        if self.time == datetime.min:
            self.date = dt_date.min
        elif self.time.hour < const.indexer.movies.tv.day_first_hour:
            self.date = (self.time - timedelta(days=1)).date()
        else:
            self.date = self.time.date()

    def role(self) -> str:
        time = f'{self.time:%H:%M}' if self.time and self.time != self.time.min else ''
        if time and self.channel:
            return f'{time:0>5}, {self.channel}'
        if time:
            return f'{time:0>5}'
        return self.channel


P = TypeVar('P', bound=PagedItemList[TvMovie])


class TvProgram:
    """Base for TV scrappers."""

    SERVICES: ClassVar[dict[str, 'TvProgram']] = {}
    RX_CHAN_SIMPLIFY: ClassVar[Pattern[str]] = re.compile(r'(.+)\s+(\d+)(?: PREMIUM)?(?: HD)?')

    #: Days to look ahead.
    DAY_RANGE: ClassVar[range] = range(0, 1)

    def __init__(self, *, tmdb: TmdbApi) -> None:
        self.tmdb: TmdbApi = tmdb
        self.cache: dict[str, TvMovie] = {}
        self._session: requests.Session | None = None

    @classmethod
    def tv_service(cls, service: ProgramTvService) -> TvProgram | None:
        """Get tv program service by name."""
        return cls.SERVICES.get(service)

    @property
    def session(self) -> requests.Session:
        """Requests session (with keep-alive)."""
        if self._session is None:
            self._session = requests.Session(cache='search')
            self._session.headers.update({'Connection': 'keep-alive'})
        return self._session

    def _tv_movie_iter(self, *, day_offset: int = 0, **_) -> Iterator[TvMovie]:
        raise NotImplementedError(f'class {self.__class__.__name__} has no _tv_movie_iter()')

    def _tv_ffitems(self, tv: TvMovie, *, details: bool = False) -> Sequence[FFItem]:
        """Get items for single TV movie."""

        def select1(items: Iterable[FFItem]) -> Iterator[FFItem]:
            """Select by year (±1)."""
            for it in items:
                if not it.year or not tv.year or it.year in range(tv.year - 1, tv.year + 2):
                    yield it

        def select2(items: Iterable[FFItem]) -> Iterator[FFItem]:
            """Select by countries, after ffinto.get_items()."""
            role = tv.role()
            for it in items:
                if role:
                    it.role = role
                if not tv.countries:
                    yield it
                elif not (ff_countries := it.vtag.getCountryCodes()):
                    if ff_languages := it.vtag.getLanguageCodes():
                        # only if match any language if TV movie countries with language of the item
                        if set(ff_languages) & {lng for ctr in tv.countries for lng in const.global_defs.country_language.get(ctr) if lng}:
                            yield it
                    else:
                        yield it
                elif tv.countries & set(ff_countries):
                    yield it

        from ..ff.info import ffinfo
        if tv.ref is not None:
            ffinfo.get_items([tv.ref])
        if tv.year:
            # tv_years = [tv.year, tv.year + 1, tv.year - 1, 0]  # try year range or no year
            tv_years = [tv.year, 0]  # try year and no year
        else:
            tv_years = [0]  # no year in tv
        for tv_year in tv_years:
            kwargs = {}
            if tv_year:
                kwargs['year'] = tv_year
            fflog.debug(f'[tv] Searching {tv.title!r} ({tv_year}), countries={tv.countries}')
            if items := list(select1(self.tmdb.search(query=tv.title, type='movie', **kwargs))):
                if details:
                    items = ffinfo.get_items(items)
                if items := list(select2(items)):
                    return items
        return []

    def _get_info(self, items: P) -> P:
        return items

    def _tv_item(self, id: str, *, day_offset: int = 0) -> TvMovie | None:
        """Get single TV movie by ID."""
        return self.cache.get(id)

    def fix_channel_name(self, channel: str) -> Iterable[str]:
        """Fix/simplify/normalize channel name."""
        yield channel
        if mch := self.RX_CHAN_SIMPLIFY.fullmatch(channel):
            yield f'{mch[1]}{mch[2]}'
        if not channel.endswith(' HD'):
            yield f'{channel} HD'
        if not channel.endswith(' PREMIUM'):
            yield f'{channel} PREMIUM'

    def channel_names(self) -> list[str]:
        """Get list of enabled channel names."""
        return [name for ch in const.indexer.movies.tv.channels or () for name in self.fix_channel_name(ch)]

    def _tv_movies(self, *, day_offset: int = 0) -> Iterable[TvMovie]:
        """Get today (±day_offset) in TV movies."""
        tv_list = tuple(self._tv_movie_iter(day_offset=day_offset))
        self.cache = {tv.id: tv for tv in tv_list}
        # remove duplicates (same title+year)
        channels_order = {ch: idx for idx, ch in enumerate(self.channel_names() or ())}
        tv_list = sorted(tv_list, key=lambda tv: (channels_order.get(tv.channel, 9999), tv.time), reverse=True)
        tv_list = {(tv.title, tv.year): tv for tv in tv_list}.values()
        # sort by time (and channel, title if the same)
        if const.indexer.movies.tv.sort_by == 'aired_date':
            tv_list = sorted(tv_list, key=lambda tv: (tv.time, tv.title, tv.channel))
        elif const.indexer.movies.tv.sort_by == 'title':
            tv_list = sorted(tv_list, key=lambda tv: (tv.title, tv.channel, tv.time))
        else:
            pass  # keep order
        return tv_list

    def tv_movies(self, *, page: int = 1, limit: int = 20, day_offset: int = 0) -> Pagina[TvMovie]:
        """Get today ± day_offset in TV movies as page."""
        tv_list = self._tv_movies(day_offset=day_offset)
        return self._get_info(Pagina(tv_list, page=page, limit=limit))

    def tv_movie_all_items(self, *, page: int = 1, limit: int = 20, day_offset: int = 0) -> PagedItemList[FFItem]:
        """Get today in TV movies."""
        tv_list = self._tv_movies(day_offset=day_offset)
        # tv_list = self._tv_movie_iter(day_offset=day_offset)
        tv_list = Pagina(tv_list, page=page, limit=limit)
        # get more info about this page
        tv_list = self._get_info(tv_list)
        # search movies in this page
        with RequestsPoolExecutor(max_thread_workers()) as ex:
            list_of_items = ex.map(self._tv_ffitems, tv_list)
        return ItemList((it for items in list_of_items for it in items), page=page, total_pages=tv_list.total_pages)

    def tv_movie_items(self, id: str, *, day_offset: int = 0) -> Sequence[FFItem]:
        """Get movies for single (TV) movie."""
        if not (tv := self._tv_item(id, day_offset=day_offset)):
            self.tv_movies(page=1, limit=1_000_000_000)
            tv = self.cache.get(id)
        if tv:
            return self._tv_ffitems(tv, details=True)
        return []

    def tv_movie_mixed_items(self, *, page: int = 1, limit: int = 20, day_offset: int = 0) -> PagedItemList[FFItem | TvMovie]:
        """Get today in TV movies."""
        tv_list = self.tv_movies(page=page, limit=limit, day_offset=day_offset)
        with RequestsPoolExecutor(max_thread_workers()) as ex:
            list_of_items = ex.map(self._tv_ffitems, tv_list)
        return ItemList((items[0] if len(items) == 1 else tv for tv, items in zip(tv_list, list_of_items) if items),
                        page=page, total_pages=tv_list.total_pages)

    def country_codes(self) -> dict[str, str]:
        """Get country name to code mapping."""
        from ..ff.locales import country_translations
        country_codes = {name: code for code, name in country_translations('pl-PL').items()}
        country_codes['USA'] = 'US'
        return country_codes


class FilmWebTv(TvProgram):
    """Tiny filmweb.pl TV provider (uses JSON API)."""

    #: Days to look ahead. Filmweb range is -1..12
    DAY_RANGE = range(-1, 13)

    URL_CHANNELS = 'https://www.filmweb.pl/api/v1/channels'
    URL_TVGUIDE = 'https://www.filmweb.pl/api/v1/channels/{channel_id}/tv-guide'
    URL_TITLE = 'https://www.filmweb.pl/api/v1/title/{id}/info'
    URL_PREVIEW = 'https://www.filmweb.pl/api/v1/film/{id}/preview'
    URL_POSTER = 'https://fwcdn.pl/fpo{path}'
    URL_LANDSCAPE = 'https://fwcdn.pl/fph{path}'  # '$' need to be replaced

    TYPE_MOVIE = 1
    TYPE_SERIES = 2
    TYPE_TVSHOW = 6

    def __init__(self, *, tmdb: TmdbApi) -> None:
        super().__init__(tmdb=tmdb)
        self.url: str = 'https://www.filmweb.pl/program-tv'

    @requests.netcache('search')
    def _tv_movie_iter(self, *, day_offset: int = 0, channel_id: int | str | None = None, **_) -> Iterator[TvMovie]:
        """Get today in TV movies."""

        def get_channel(channel_id: int) -> list[JsonData]:
            resp = self.session.get(self.URL_TVGUIDE.format(channel_id=channel_id), params={'date': date})
            if 200 <= resp.status_code <= 299:
                return resp.json()
            return []

        date = (dt_date.today() + timedelta(days=day_offset)).isoformat()
        with logtime(name='[filmweb] channel list'):
            if channel_id:
                channel_id = int(channel_id)
                channels = {it['id']: it for it in self.session.get(self.URL_CHANNELS).json() if it['id'] == channel_id}
            else:
                channel_list = self.session.get(self.URL_CHANNELS).json()
                if tv_channels := self.channel_names():
                    enabled = set(tv_channels)
                    channel_list = [it for it in channel_list if it.get('name') in enabled]
                # channel_list = channel_list[:35]  # hard limit
                channels = {it['id']: it for it in channel_list}
        with logtime(name='[filmweb] channels'):
            with RequestsPoolExecutor(max_thread_workers()) as ex:
                all_items = [it for channel_items in ex.map(get_channel, channels) for it in channel_items]

        # only movies
        items = [it for it in all_items if it.get('type') == self.TYPE_MOVIE]

        # collect movie data (year..)
        # with logtime(name='[filmweb] titles'):
        #     with RequestsPoolExecutor(max_thread_workers()) as ex:
        #         need_info = (title_id for it in items if (title_id := it.get('film')))
        #         infos = {it['id']: it for it in ex.map(get_info, need_info) if it}

        # result
        fflog(f'[filmweb] Found {len(channels)} channels, {len(all_items)} TV items, {len(items)} TV movies')
        # fflog(f'[filmweb] Found {len(channels)} channels, {len(all_items)} TV items, {len(items)} TV movies, {len(infos)} titles')
        # print(json.dumps([it for it in items if 'film' not in it], indent=2))
        # return
        for it in items:
            cid = it['channel']
            channel = channels.get(cid, {})
            start_time = fromisoformat(it['zonedStartTime'])
            if title_id := it.get('film'):
                iid = str(title_id)
            elif not const.indexer.movies.tv.filmweb.show_non_id:
                iid = f'{cid}-{start_time:%Y%m%d-%H%M}'
            else:
                continue
            descr = []
            if dtype := it.get('description'):
                if dtype in const.indexer.movies.tv.filmweb.skip_items:
                    continue
                descr.append(f'{dtype.capitalize()}.')
            if note := it.get('notes'):
                descr.append(note)
            yield TvMovie(id=iid, title=it['title'], year=0, time=start_time, duration=it.get('duration', 0) * 60,
                          channel=channel.get('name') or '', image='', descr='[CR]\n'.join(descr))

    # @requests.netcache('search')
    def _get_info(self, items: P) -> P:

        # def get_info(title_id: int) -> JsonData | None:
        #     resp = self.session.get(self.URL_TITLE.format(id=title_id))
        #     if 200 <= resp.status_code <= 299:
        #         return resp.json()
        #     return None

        def get_info(title_id: int) -> JsonData | None:
            resp = self.session.get(self.URL_PREVIEW.format(id=title_id))
            if 200 <= resp.status_code <= 299:
                data = resp.json()
                data.setdefault('id', title_id)
                return data
            return None

        # collect movie data (year..)
        with logtime(name='[filmweb] titles'):
            with RequestsPoolExecutor(max_thread_workers()) as ex:
                need_info = (int(tv.id) for tv in items if tv.id.isdecimal())
                infos = {it['id']: it for it in ex.map(get_info, need_info) if it}

        # apply ino (year)
        for tv in items:
            if tv.id.isdecimal() and (info := infos.get(int(tv.id))):
                tv.year = info.get('year') or 0
                # if poster := info.get('posterPath'):  # from URL_TITLE
                #     tv.image = self.URL_POSTER.format(path=poster)
                if poster := info.get('poster', {}).get('path'):  # from URL_PREVIEW
                    tv.image = self.URL_POSTER.format(path=poster)
                if title := info.get('title', {}).get('title'):
                    tv.title = title
                elif title := info.get('originalTitle', {}).get('title'):
                    tv.title = title
                if descr := info.get('plotOrDescriptionSynopsis'):
                    tv.descr = descr
                if countries := info.get('countries'):
                    tv.countries = {c.get('code') for c in countries if c.get('code')}

        return items

    def _tv_item(self, id: str, *, day_offset: int = 0) -> TvMovie | None:
        """Get single TV movie by ID."""
        if id not in self.cache:
            if id.isdecimal():
                tv_list = self._get_info(Pagina.single([TvMovie(id=id, title='', year=0, time=datetime.min, channel='', image='')]))
            else:
                tv_list = tuple(self._tv_movie_iter(channel_id=id.partition('-')[0], day_offset=day_offset))
            self.cache.update({tv.id: tv for tv in tv_list})
        return self.cache.get(id)


class OnetTv(TvProgram):
    """Tiny programtv.onet.pl TV scraper."""

    #: Days to look ahead. Onet range is -1..12
    DAY_RANGE = range(-1, 13)

    ONET_CHANNEL_PAGE_COUNT = const.indexer.movies.tv.onet.channel_page_count  # onet has pages 1..9
    TV_URL = 'https://programtv.onet.pl/filtr/film?dzien={day}'  # &strona=2
    ENTRY_URL = 'https://programtv.onet.pl/tv/{entry_url}'
    _RX_CHANNEL_LIST = re.compile(r'<span\s+class="tvLogo">\s*<img\b[^>]*alt="([^"]*)"', re.DOTALL)
    _RX_CHANNEL = re.compile(r'<div class="adderItem((?:(?!</a>).)*)</a>', re.DOTALL)
    _RX_CHANNEL_NAME = re.compile(r'<span class="tvName">(?P<name>[^<]*)<', re.DOTALL)
    _RX_PROG = re.compile(r'<li\b[^>]*', re.DOTALL)
    _RX_PROG_TIME = re.compile(r'<span\s+class="hour">\s*(?P<time>[:0-9]+)\s*</span>', re.DOTALL)
    _RX_PROG_RATING = re.compile(r'stars stars(?P<rating>\d)"', re.DOTALL)
    _RX_PROG_TITLE = re.compile(r'<span\s+class="title\b[^>]*>\s*<a href="(?P<url>[^"]*)">(?P<title>[^<]*)<', re.DOTALL)
    _RX_PROG_INFO = re.compile(r'<span\s+class="type">\s*(?P<genre>[^<,]*),\s+(?P<countries>[^<]*)\s+(?P<year>\d{4})\s*<', re.DOTALL)

    @requests.netcache('search')
    def _tv_movie_iter(self, *, day_offset: int = 0, **_) -> Iterator[TvMovie]:
        """Get today in TV movies."""

        def get(page: int = 1) -> Iterator[TvMovie]:
            url = self.TV_URL.format(day=day_offset)
            if page > 1:
                url += f'&strona={page}'
            page_content: str = self.session.get(url).text

            for box in self._RX_CHANNEL.finditer(page_content):
                box_end = page_content.find('</ul>', box.end())
                prog_end = box.end()
                if mch := self._RX_CHANNEL_NAME.search(page_content, box.start(), box.end()):
                    channel = mch['name'].strip()
                else:
                    channel = '?'
                while True:
                    prog_start = page_content.find('<li', prog_end, box_end)
                    if prog_start == -1:
                        break
                    prog_end = page_content.find('</li>', prog_start, box_end)
                    mch = self._RX_PROG_TITLE.search(page_content, prog_start, prog_end)
                    if mch is None:
                        continue
                    title = unescape(mch['title']).strip()
                    url = mch['url']
                    if mch := self._RX_PROG_TIME.search(page_content, prog_start, prog_end):
                        time = datetime.combine(date, dt_time.fromisoformat(mch['time']))
                    else:
                        time = datetime.min
                    if mch := self._RX_PROG_RATING.search(page_content, prog_start, prog_end):
                        ratings = {'filmweb': (float(mch['rating']) * 2, 0)}
                    else:
                        ratings = {}
                    if mch := self._RX_PROG_INFO.search(page_content, prog_start, prog_end):
                        year = int(mch['year'])
                        countries = {cc for c in mch['countries'].split(',') for cc in [country_codes.get(c.strip())] if cc}
                        genre = mch['genre'].strip()
                        if 'odc.' in genre:
                            fflog(f'[TV][onet] Skip series: {title} ({year})')
                            continue  # skip series
                    else:
                        year, countries, genre = 0, set(), ''
                    yield TvMovie(id=url.rpartition('/')[2].partition('?')[0],
                                  title=title, year=year, time=time, channel=channel, image='', url=url,
                                  descr=genre, ratings=ratings, countries=countries)

        date = dt_date.today()
        country_codes = self.country_codes()
        with RequestsPoolExecutor(const.indexer.movies.tv.onet.channel_worker_count or max_thread_workers()) as ex:
            yield from (it for ent in ex.map(get, range(1, self.ONET_CHANNEL_PAGE_COUNT + 1)) for it in ent)

    def _scan_tv_item(self, tv: TvMovie | None, id: str = '') -> TvMovie | None:
        """Get single TV movie by ID."""
        def make_air(mch: Match[str]) -> tuple[str, datetime]:
            chan, date_str, time_str = map(str.strip, mch.group('chan', 'date', 'time'))
            date = datetime.strptime(f'{date_str} {time_str}', '%d.%m %H:%M')
            date = date.replace(year=today.year)
            if date.month == 1 and today.month == 12:
                date = date.replace(year=today.year + 1)  # program after new year (tomorrow and next days)
            elif date.month == 12 and today.month == 1:
                date = date.replace(year=today.year - 1)  # program before new year (yesterday)
            return chan, date

        # countryOfOrigin

        today = dt_date.today()
        if not id and tv:
            id = tv.id
        url = self.ENTRY_URL.format(entry_url=id.replace('_', '/'))
        page = self.session.get(url).text
        # with open('/tmp/onet.html', 'w', encoding='utf-8') as f:  # XXX XXX XXX
        #     f.write(page)
        if mch := self._rx_scan_entry_page.search(page):
            data = json.loads(mch[1])
            # print(json.dumps(data, indent=2))
            data = data[-1]
            air = datetime.min
            chan = ''
            if air_list := [make_air(m) for m in self._rx_scan_entry_time.finditer(page)]:
                channels_order = {ch: idx for idx, ch in enumerate(self.channel_names() or ())}
                air_list.sort(key=lambda v: (channels_order.get(v[0], 9999), v[1]))
                chan, air = air_list[0]
            new = TvMovie(id=id, url=self.ENTRY_URL.format(entry_url=id.replace('_', '/')),
                          title=data.get('name') or '', year=int(data.get('datePublished') or 0), time=air, channel=chan,
                          image=data.get('image', {}).get('url', ''), descr=data.get('description') or '')
            if tv is None:
                tv = new
            else:
                for fld in fields(TvMovie):
                    setattr(tv, fld.name, getattr(new, fld.name))
        return tv

    def tv_movies(self, *, page: int = 1, limit: int = 20, day_offset: int = 0) -> Pagina[TvMovie]:
        """Get today ± day_offset in TV movies as page."""
        tv_list = self._tv_movies(day_offset=day_offset)
        return self._get_info(Pagina(tv_list, page=page, limit=limit))

    @requests.netcache('search')
    def _get_info(self, items: P) -> P:
        if const.indexer.movies.tv.onet.more_details:
            with RequestsPoolExecutor(max_thread_workers()) as ex:
                ex.map(self._scan_tv_item, items)
        # if isinstance(items, Pagina) and isinstance(items.items, list):
        #     items.items.sort(key=lambda tv: tv.time)
        return items

    _rx_scan_entry_page = re.compile(r'<script type="application/ld\+json">\s*(\[.*?\])\s*</script>', flags=re.DOTALL)
    _rx_scan_entry_time = re.compile((r'<li class="reRun">.*?<li class="nameTv">(?P<chan>[^<]+)<.*?'
                                      r'<li class="dateTv">\s*(?:<span>[^<]*</span>\s*)?(?P<date>[^<]+)<.*?'
                                      r'<li class="timeTv">\s*<span>(?P<time>[^<]+)<.*?</a>'), flags=re.DOTALL)

    def _tv_item(self, id: str, *, day_offset: int = 0) -> TvMovie | None:
        """Get single TV movie by ID."""
        if id not in self.cache:
            if tv := self._scan_tv_item(None, id):
                self.cache[tv.id] = tv
        return self.cache.get(id)


class TelemanTV(TvProgram):
    """Tiny teleman.pl TV provider."""

    DAY_RANGE: ClassVar[range] = range(-1, 6)

    _RX_ENTRY = re.compile(r'<a\s+class="movie-search-item"\s+href="(?P<url>(?:/tv/)?(?P<id>[^"]+))"[^<]*'
                           r'(?:<img\b[^>]*src="(?P<image>[^"]*)"[^>]*>[^<]*|<div class="no-photo"></div>)'
                           r'(?:<div\s+class="imdb"\s+data-tconst="(?P<imdb>[^"]*)">(?P<rating>[^<]*)</div>[^<]*)?'
                           r'<h3\s+class="title">(?P<title>[^<]*)</h3>[^<]*'
                           r'<div\s+class="info">\s*(?:<div>(?P<genre>[^<]*)</div>\s*<div>(?P<year>\d+)\s*(?P<country>[^<]*)</div>[^<]*)?</div>[^<]*'
                           r'<div\s+class="airing">\s*<div>(?:<figure[^>]*:url\([^)]*/(?P<channel>\d+)\.png[^)]*\)"[^>]*></figure>)?</div>\s*'
                           r'<div>[^<0-9]*(?P<air_date>\d[^<]*)</div>\s*<div>(?P<air_time>[^<]*)</div>[^<]*</div>[^<]*'
                           r'</a>')

    # _RX_ENTRY = re.compile(r'<a\s+class="movie-search-item"\s+href="(?P<url>[^"]+)".*?'
    #                        r'</a>')

    def __init__(self, *, tmdb: TmdbApi) -> None:
        super().__init__(tmdb=tmdb)
        self._channels: dict[str, str] | None = None

    # @requests.netcache('search')
    def _tv_movie_iter(self, *, day_offset: int = 0, **_) -> Iterator[TvMovie]:
        def get_page(page: int = 1) -> str:
            url = f'https://www.teleman.pl/filmy?hour_start=0&imdb_min=0&page={page}&since={since:%Y%m%d}0000'
            page_content = self.session.get(url).text
            start = page_content.find('<div id="movie-search-items">')
            end = page_content.find('<div class="movie-search-fill">')
            return page_content[max(0, start):max(len(page_content), end)]

        since = dt_date.today() + timedelta(days=day_offset)
        country_codes = self.country_codes()
        channels = self.channels

        with RequestsPoolExecutor(max_thread_workers()) as ex:
            page_content = '\n'.join(ex.map(get_page, range(1, const.indexer.movies.tv.teleman.pages_to_scan + 1)))

        for mch in self._RX_ENTRY.finditer(page_content):
            mch.groupdict()
            title = mch['title'].strip()
            descr = mch['genre'] or ''
            if mch['year']:
                descr += f', {mch["year"]} {mch["country"]}'
            time_str = mch['air_time'].strip()
            if time_str and mch['air_date'].strip():
                date = datetime.strptime(mch['air_date'].strip() + ' ' + time_str, '%d.%m %H:%M')
                date = date.replace(year=since.year)
                if date.month == 1 and since.month == 12:
                    date = date.replace(year=since.year + 1)  # program after new year (tomorrow and next days)
                elif date.month == 12 and since.month == 1:
                    date = date.replace(year=since.year - 1)  # program before new year (yesterday)
            else:
                date = datetime.min
            if date.date() != since:
                break
            image = mch['image'] or ''
            if image.startswith('//'):
                image = f'https:{image}'
            ref: MediaRef | None = None
            if mch['imdb']:
                ref = MediaRef.movie(VideoIds.make_ffid(imdb=mch['imdb']))
            ratings = {}
            if mch['rating']:
                with suppress(ValueError):
                    ratings['imdb'] = float(mch['rating'].replace(',', '.')), 0  # no votes count
            ch_id = mch['channel'].strip()
            countries = {code for country in mch['country'].split('/') for code in [country_codes.get(country)] if code}
            yield TvMovie(id=mch['id'], title=title, year=int(mch['year'] or 0), time=date, countries=countries,
                          channel=channels.get(ch_id, ch_id), image=image, url=mch['url'], descr=descr, ref=ref, ratings=ratings)

    @property
    def channels(self) -> dict[str, str]:
        """Get channel id to name mapping."""
        # <a href="/stations/BBC-Lifestyle" style="background-image:url(//media.teleman.pl/logos/54x54/33.png?v=1529327046)">BBC Lifestyle</a>
        if self._channels is None:
            rx = re.compile(r'<a\s+href="/stations/[^"]+"\s+style="background-image:url\([^)]*/(?P<id>\d+)\.png[^)]*\)">(?P<name>[^<]+)</a>')
            self._channels = {mch['id']: mch['name'] for mch in rx.finditer(self.session.get('https://m.teleman.pl/stations').text)}
        return self._channels

    # <a class="movie-search-item" href="/tv/Cenny-Czas-2216772">
    # 	<img src="//media.teleman.pl/photos/crop-230x142/Cenny-Czas2017.jpeg" width="230" height="142" class="photo" alt="zdjęcie" loading="lazy" />
    # 	<div class="imdb" data-tconst="tt6010828">6,3</div>
    # 	<h3 class="title">Cenny czas</h3>
    # 	<div class="info">
    # 		<div>KOMEDIODRAMAT</div>
    # 		<div>2017 Holandia</div>
    # 	</div>
    # 	<div class="airing">
    # 		<div>
    # 			<figure style="background-image:url(//media.teleman.pl/logos/140x50/116.png?v=1545227809)"></figure>
    # 		</div>
    # 		<div>Dziś 20.02</div>
    # 		<div>20:05</div>
    # 	</div>
    # </a>


TvProgram.SERVICES['filmweb'] = filmweb = FilmWebTv(tmdb=tmdb)
TvProgram.SERVICES['onet'] = onet = OnetTv(tmdb=tmdb)
TvProgram.SERVICES['teleman'] = teleman = TelemanTV(tmdb=tmdb)


if __name__ == '__main__':
    from typing import get_args
    from ..ff.cmdline import DebugArgumentParser
    from cdefs import ProgramTvService  # noqa: F811
    p = DebugArgumentParser()
    p.add_argument('service', nargs='?', choices=get_args(ProgramTvService), default='filmweb', help='tv service')
    p.add_argument('program', nargs='?', help='program id (onet only)')
    p.add_argument('-d', '--details', action='store_true', help='scan details')
    args = p.parse_args()
    service = TvProgram.SERVICES[args.service]
    if args.program:
        if args.details:
            for tv in service._tv_movie_iter():
                if tv.id == args.program:
                    for tv in service._get_info(Pagina([tv], page=1, limit=1)):
                        print(tv)
                    break
        else:
            print(service.tv_movie_items(args.program))
    else:
        if args.details:
            for x in service.tv_movies(limit=9999999):
                print(x)
        else:
            for i, x in enumerate(service._tv_movie_iter(), 1):
                print(f'{i:3d}. {x}')
            # for x in service._tv_movie_iter():
            #     print(x.id)
    # for it in filmweb.tv_movie_all_items():
    #     print(it)
