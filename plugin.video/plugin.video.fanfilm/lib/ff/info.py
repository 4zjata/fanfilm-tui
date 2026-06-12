"""Tiny TheMovieDB.org API getter."""

from __future__ import annotations
from typing import Optional, Union, Any, Sequence, Mapping, Iterable, Iterator, Callable, ClassVar, NamedTuple
from typing import overload, TYPE_CHECKING
from typing_extensions import get_args as get_typing_args, Literal, TypedDict, NotRequired
import re
from enum import Enum
from time import time as cur_time
from datetime import datetime, date as dt_date, timedelta
from itertools import chain
from inspect import isgenerator
import json
from concurrent.futures import ThreadPoolExecutor
from attrs import evolve
from ..api.tmdb import TmdbApi, TmdbCredentials, TmdbItemJson, ExternalIdType, DetailsAllowed, SkelOptions, seasons_iter, episodes_iter
from ..api.tmdb import MediaRequest, LOCALE_EN, LOCALE_ORIG
from ..api.trakt import TraktApi
from ..defs import MediaRef, MediaProgress, MediaProgressItem, NextWatchPolicy, VideoIds, Pagina, ItemList, RefType, FFRef
from . import apis
from .types import JsonData
from .settings import settings
from .locales import country_translations
from .calendar import fromisoformat
from .item import FFItem, FFItemDict, FFActor, FFTitleAlias, FFEpisodeGroup, FFEpisodeGroupType
from .db.playback import get_playback, get_playback_info, set_playback, MediaPlayInfo, MediaPlayInfoDict
from .db.media import find_media_info, get_media_info, set_media_info, MediaInfoRow
from .tmdb import tmdb as tmdb_provider
from .art import Art
from .trakt import trakt as trakt_provider, Trakt
from ..api.mdblist import mdblist as mdblist_provider, MdbList
from .tricks import pairwise, AlwaysFalse
from .kodidb import video_db, KodiVideoInfo
from ..service.client import service_client
from ..kolang import KodiLabels
# from .calendar import fromisoformat
from .log_utils import fflog, fflog_exc
from .debug import logtime
from .debug.profiler import profiler
from ..ff.control import apiLanguage, max_thread_workers
from cdefs import InfoDetails
from const import const
if TYPE_CHECKING:
    from types import EllipsisType
    from ..indexers.defs import CodeId


def tmdb_locale() -> str:
    """Get user locale (language)."""
    return apiLanguage().get('tmdb', 'pl-PL')


class Credits(NamedTuple):
    actors: list[FFActor]
    directors: list[str]
    writers: list[str]
    crew: tuple[FFActor, ...] = ()


class MyTmdb(TmdbApi):
    """API for themoviedb.org."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def credentials(self) -> TmdbCredentials:
        """Return current credencials."""
        api_key: str = const.dev.tmdb.api_key or settings.getString("tmdb.api_key") or apis.tmdb_API
        return TmdbCredentials(api_key=api_key)


class Progress(Enum):
    """FFItem progress mode."""
    NO = 'no'
    BASIC = 'basic'
    FULL = 'full'


class ProgressBarMode(Enum):
    """FFItem progress bar mode."""
    #: Do not show progressbar at all.
    NONE = 'none'
    #: Show video percent progress (PERCENT) if video progress >0% and < 100% else show nothing.
    WATCHING = 'watching'
    #: Show only watched videos (movies and episodes progresses are skiped).
    WATCHED = 'watched'
    #: Show video percent progress.
    PERCENT = 'percent'
    #: Show video percent progress and watched in background (use const.indexer.progressbar.watched.*).
    PERCENT_AND_WATCHED = 'percent_and_watched'


"""Object to mark already watched progress in progreassbar."""
AREADY_WATCHED = AlwaysFalse()


class ItemInfoKwargs(TypedDict):
    """ffinfo.get_items() args."""
    #: FFinfo data limit, now only cast/crew limit, None - default, 0 - no cast, N – max N actors.
    crew_limit: NotRequired[int | None]
    #: Obtain progress details.
    progress: NotRequired[Progress]
    #: Scan details, what exaclty to obtain.
    details: NotRequired[InfoDetails]


class GetItemKwargs(ItemInfoKwargs):
    """ffinfo.get_items() args."""
    #: If true, return None in ref is not obtained (useful for zip()) else skip missing item.
    keep_missing: NotRequired[bool]
    #: Get more info about shows (and its seasons) to obtain episodes.
    duplicate: NotRequired[bool]
    #: Convert to episode groups. If ID (str) is used extra data will be collected.
    episode_group: NotRequired[str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None]


class InfoProvider:
    """Media info provider."""

    # Must be supported by TMDB API get_temss() to mark as broken.
    _supported_details_types = {'movie', 'show', 'season', 'episode', 'person', 'collection'}

    Progress = Progress

    # All language aliases
    LANGUAGE_ALIASES: ClassVar[dict[str, Sequence[str]]] = {lng: alias for alias in const.tmdb.language_aliases for lng in alias}

    # Field names in translation object.
    TRANSLATE_FILEDS: ClassVar[Iterable[str]] = ('homepage', 'overview', 'runtime', 'tagline', 'title')

    # Skeleton EN locales
    EN_LOCALES: ClassVar[Sequence[str]] = ('en-US', 'en-GB', 'en')
    # Media (like image) non-locales
    NON_LOCALES: ClassVar[Sequence[str]] = ('xx-XX', 'xx', 'null')

    def __init__(self, tmdb: Optional[TmdbApi] = None, trakt: Trakt | TraktApi | None = None, mdblist: MdbList | None = None) -> None:
        if tmdb is None:
            if trakt is not None and hasattr(trakt, 'tmdb'):
                tmdb = trakt.tmdb
            else:
                lang: str = tmdb_locale()
                tmdb = MyTmdb(lang=lang)

        self.tmdb: TmdbApi = tmdb
        self.trakt: TraktApi | None = trakt
        self._mdbist: MdbList | None = mdblist
        #: Lazy loaded trakt playback progress.
        self._trakt_playback: Optional[MediaPlayInfoDict] = None
        #: Lazy loaded kodi playback progress.
        self._kodi_playback: Optional[list[KodiVideoInfo]] = None
        #: API locale labels
        self.api_labels: KodiLabels = KodiLabels(tmdb_locale())
        #: Art services
        self.art = Art()

    def reset(self) -> None:
        """Reset settings to obtain settings change."""
        # Forget current language, will be taken from settings (api.language).
        self.tmdb.lang = tmdb_locale()
        self.api_labels = KodiLabels(self.tmdb.lang)
        self.art.reset()

    @property
    def mdblist(self) -> MdbList | None:
        """MDBList API."""
        if self._mdbist is None or not self._mdbist.api_key:
            return None
        return self._mdbist

    def parse_credits(self, item: TmdbItemJson, *, limit: Optional[int] = None) -> Credits:
        """Parse movie/show/season/episode credits (cast and crew)."""

        def make_img(person: JsonData) -> str:
            img = person.get('profile_path')
            if img:
                img = f'{iurl}{img}'
            return img or ''

        def make_actor(persons: list[JsonData], i: int = -1) -> Iterator[FFActor]:
            for p in persons:
                # TODO: id, gender, popularity
                yield FFActor(name=p['name'], role=p.get('character'), order=p.get('order', i), thumbnail=make_img(p),
                              ffid=VideoIds.ffid_from_tmdb(p))

        def make_crew_name(persons: list[JsonData], jobs: set[str]) -> Iterator[str]:
            for p in persons:
                if p.get('job') in jobs:
                    # TODO: id, gender, popularity, job, department
                    yield p['name']

        def make_crew(persons: list[JsonData]) -> Iterator[FFActor]:
            for i, p in enumerate(persons):
                # TODO: id, gender, popularity, job, department
                yield FFActor(name=p['name'], role=p.get('job'), order=p.get('order', i), thumbnail=make_img(p),
                              ffid=VideoIds.ffid_from_tmdb(p))

        def make_agg_actor(persons: list[JsonData]) -> Iterator[FFActor]:
            for i, p in enumerate(persons):
                # TODO: id, gender, popularity
                character = ', '.join(ch for r in p.get('roles', ()) for ch in (r.get('character'),) if ch)
                yield FFActor(name=p['name'], role=character, order=p.get('order', i), thumbnail=make_img(p),
                              ffid=VideoIds.ffid_from_tmdb(p))

        def make_agg_crew_name(persons: list[JsonData], jobs: set[str]) -> Iterator[str]:
            for p in persons:
                for job in p.get('jobs', ()):
                    # TODO: id, gender, popularity, job, department
                    if job['job'] in jobs:
                        yield p['name']

        def make_agg_crew(persons: list[JsonData]) -> Iterator[FFActor]:
            for i, p in enumerate(persons):
                for job in p.get('jobs', ()):
                    # TODO: id, gender, popularity, job, department
                    if isinstance(job, Mapping):
                        job = job.get('job')
                    if isinstance(job, str):
                        yield FFActor(name=p['name'], role=job, order=p.get('order', i), thumbnail=make_img(p),
                                      ffid=VideoIds.ffid_from_tmdb(p))

        if limit is None:
            limit = settings.getInt('actor_count_limit')
        no_limit = 10**9
        if not limit:
            limit = no_limit
        iurl = self.tmdb.person_image_url
        dir_jobs = {'Director'}
        wr_jobs = {'Writer', 'Staff Writer', 'Story'}
        # TODO: parse "guest_stars" ?
        crew = ()
        if 'aggregate_credits' in item:
            credits = item['aggregate_credits']
            actors = make_agg_actor(credits.get('cast', ()))
            directors = make_agg_crew_name(credits.get('crew', ()), dir_jobs)
            writers = make_agg_crew_name(credits.get('crew', ()), wr_jobs)
            if limit >= no_limit:
                crew = make_agg_crew(credits.get('crew', ()))
        else:
            credits = item.get('credits', {})
            actors = make_actor(credits.get('cast', ()))
            directors = make_crew_name(credits.get('crew', ()), dir_jobs)
            writers = make_crew_name(credits.get('crew', ()), wr_jobs)
            if limit >= no_limit:
                crew = make_crew(credits.get('crew', ()))
        actors = list({a.getName(): a for a in actors}.values())
        directors = list(dict.fromkeys(directors))
        writers = list(dict.fromkeys(writers))
        crew = tuple(dict.fromkeys(crew))
        if limit:
            actors, directors, writers = actors[:limit], directors[:limit], writers[:limit]
        return Credits(actors=actors, directors=directors, writers=writers, crew=crew)

    def parse_tmdb_item(self,
                        item: TmdbItemJson,
                        *,
                        locale: str | None = None,
                        crew_limit: int | None = None,
                        parent_ffitem: FFItem | None = None,
                        ) -> Optional[FFItem]:
        """Parse single movie/show/season/episode data."""

        def dget(dct: dict, key: str, default: Any = None) -> Any:
            keys = key.split('/')
            for key in keys[:-1]:
                dct = dct.get(key, {})
            return dct.get(keys[-1], default)

        def set_if(meth: Callable, *keys: str) -> None:
            for data in (item, *en_translations, *orig_translations):
                for key in keys:
                    value = dget(data, key)
                    if value:
                        meth(value)
                        return

        def set_dt_if(meth: Callable, value: str, fmt: str = '%Y-%m-%d') -> None:
            if value:
                try:
                    dt = fromisoformat(value)
                except ValueError:
                    pass
                else:
                    meth(dt)

        def translate(*keys:
                      Literal['title', 'name', 'tagline', 'overview'],
                      default: str = '',
                      order: Optional[Sequence[str]] = None,
                      direct: bool = True,
                      ) -> str:
            if order is None:
                order = trans_order
            for lang in order:
                for key in keys:
                    if val := item_translations.get(lang, {}).get(key):
                        return val
            if direct:
                for key in keys:
                    if val := item.get(key):
                        return val
            return default

        def lang_aliases(lng: str) -> Sequence[str]:
            return self.LANGUAGE_ALIASES.get(lng, (lng,))

        if not item.get('success', True):
            return None

        if locale is None:
            locale = tmdb_locale()
        lang = locale.partition('-')[0]

        # Translation order.
        orig_lang: str = item.get('original_language') or 'en'
        orig_langs: Sequence[str] = lang_aliases(orig_lang)
        orig_countries: Sequence[str] = item.get('origin_country') or ()
        trans_order = tuple(dict.fromkeys((locale, *(lang_aliases(lang)), *self.EN_LOCALES, *self.NON_LOCALES,
                                           *(f'{lng}-{c}' for lng in orig_langs for c in orig_countries),
                                           *orig_langs)))
        en_trans_order = self.EN_LOCALES

        # Translations by locale (eg. en-US) and language (eg. en),
        # values: name, homepage, tagline, overview.
        item_translations: dict[str, JsonData] = {f"{t['iso_639_1']}-{t['iso_3166_1']}": t['data']
                                                  for t in item.get('translations', {}).get('translations', ())}
        # Add default translation if it's not in "translations" object.
        if 'original_language' in item:
            data = {key: value for key in ('title', 'name') if (value := item.get(f'original_{key}'))}
            for c in item.get('origin_country', ()):
                item_translations.setdefault(f'{orig_lang}-{c}', data)
            item_translations.setdefault(orig_lang, data)
        else:
            item_translations.setdefault(locale, item)
        # Fix missing names if translation is item directly.
        # If lang == orig_lang same values (as "title") has empty value in translation object. But item['title'] has translated name.
        if orig_lang == lang:  # fix missing names if translation is item directly
            for key in self.TRANSLATE_FILEDS:
                if value := item.get(key):
                    for c in ('', *orig_countries):
                        tr = item_translations.get(f'{lang}-{c}' if c else lang, {})
                        if not tr.get(key):
                            tr[key] = value
        # Add iso_639_1 keys (language only, without country code).
        item_translations = {k: tr for loc, tr in item_translations.items()  # if tr.get('name') or tr.get('title')
                             for k in (loc, loc.partition('-')[0])}
        # Fix broken translation for original language.
        if orig_lang == lang and lang not in item_translations:
            item_translations[lang] = {k: item[k] for k in self.TRANSLATE_FILEDS if item.get(k)}

        en_translations: Sequence[JsonData] = tuple(tr for lng in self.EN_LOCALES if (tr := item_translations.get(lng)))
        orig_translations: Sequence[JsonData]
        if orig_lang:
            orig_translations = tuple(tr for lng, tr in item_translations.items() if lng.partition('-')[0] == orig_lang)
        else:
            orig_translations = ()

        ref = MediaRef(*item['_ref'])
        ff = FFItem(ref)
        if ref.season is not None:
            if 'id' not in item:
                fflog(json.dumps(item, indent=2, cls=JsonEncoder))
            if ref.is_season:
                # fake tmdb season id (show_id and season_number)
                # NOTE: now season FFID is replaced to volatile FFID in InfoProvider._set_volatile_ffid().
                if sz_ref := ref.denormalize():
                    ff.ffid = sz_ref.ffid
                else:
                    # TODO: find first episode and use its ffid as 'season' ffid
                    ...
        if not ff.ffid:
            ff.ffid = VideoIds.ffid_from_tmdb(item)
        if ref.real_type in ('movie', 'episode'):
            ff.mode = ff.Mode.Playable
        else:
            ff.mode = ff.Mode.Folder
        ff.source_data = item

        vtag = ff.getVideoInfoTag()
        titletag: str = 'title' if ref.type == 'movie' else 'name'

        if 'id' not in item:
            fflog.error(f'ERROR: no ID !!!   XXXXXXXXXXXXXXXXX\n\n{json.dumps(item, indent=2, cls=JsonEncoder)}')
        elif ff.ffid in VideoIds.KODI:
            vtag.setDbId(ff.ffid)  # Kodi DBID
        set_if(vtag.setIMDBNumber, 'imdb_id', 'external_ids/imdb_id')
        vtag.setUniqueIDs({name: str(val)
                           for name, key in (('tmdb', 'id'), ('tmdb_id', 'id'), ('imdb', 'imdb_id'), ('imdb', 'external_ids/imdb_id'),
                                             ('tvdb', 'tvdb_id'), ('tvdb', 'external_ids/tvdb_id'))
                           for val in (dget(item, key),) if val},
                          'imdb')  # default id type
        vtag.setMediaType(ref.real_type)

        # season and episode should be set in FFItem(ref)
        # skip vtag.setSeason()
        # skip vtag.setEpisode()

        # duration
        runtime = item.get('runtime')
        if runtime is not None:
            vtag.setDuration(runtime * 60)

        # year and dates (premiered, first aired)
        date = None
        if ref.type == 'movie':
            date_keys = ('release_date', )
        else:
            date_keys = ('first_air_date', 'air_date')
        for date_key in date_keys:
            if date_str := item.get(date_key):
                try:
                    date = fromisoformat(date_str)
                    break
                except ValueError:
                    pass
        if date:
            vtag.setYear(date.year)
        set_if(vtag.setFirstAired, 'first_air_date', 'air_date')
        set_if(vtag.setPremiered, 'release_date')
        # fallback to first aired date of first episode
        if not date and ref.is_season and (episodes := item.get('episodes')):
            if date_str := episodes[0].get('air_date'):
                try:
                    date = fromisoformat(date_str)
                    vtag.setYear(date.year)
                    vtag.setFirstAired(date_str)
                except ValueError:
                    pass

        # titles
        vtag.setTitle(translate('title', 'name', default=ff.title))
        set_if(vtag.setOriginalTitle, f'original_{titletag}')
        orig_en_title = vtag.getOriginalTitle() if item.get('original_language', 'en') == 'en' else ''
        vtag.setEnglishTitle(translate('title', 'name', order=en_trans_order, direct=False, default=orig_en_title))
        if ref.type == 'show':
            if ref.season is None:  # show
                vtag.setTvShowTitle(ff.title)
                vtag.setEnglishTvShowTitle(vtag.getEnglishTitle())
            elif parent_ffitem:  # season or episode
                vtag.setTvShowTitle(parent_ffitem.vtag.getTVShowTitle())
                vtag.setEnglishTvShowTitle(parent_ffitem.vtag.getEnglishTvShowTitle())
        ff.label = ff.title
        if aliases := item.get('alternative_titles'):
            ff.aliases_info = self._parse_alternative_titles(aliases, locale=(*self._aliases_locales(), *orig_countries))

        # plot, outline and tag line
        vtag.setPlotBase(translate('overview', default=vtag.getPlot()))
        # vtag.setPlotOutline()
        vtag.setTagLine(translate('tagline', default=vtag.getTagLine()))

        # set genres
        vtag.setGenres([g['name'] for g in item.get('genres', ())])

        # casts / actors, directors, writers
        if ref.type == 'person':
            self._parse_tmdb_person(item, ff, en_translations=en_translations, orig_translations=orig_translations)
        elif ref.type == 'collection':
            self._parse_tmdb_collection(item, ff, en_translations=en_translations, orig_translations=orig_translations)
        else:
            credits = self.parse_credits(item, limit=crew_limit)
            vtag.setCast(credits.actors)
            vtag.setDirectors(credits.directors)
            vtag.setWriters(credits.writers)
            vtag.setCrew(credits.crew)  # FF extension

            # --- art ---
            ff.setArt(self.art.parse_art(item=item, translate_order=trans_order, ref=ref))

        # countries and MPAA
        countries = country_translations()
        vtag.setLanguageCodes([item.get('original_language', ['en'])])
        vtag.setCountryCodes([c['iso_3166_1'] for c in item.get('production_countries', ())])
        vtag.setCountries([countries.get(c['iso_3166_1'], c['name']) for c in item.get('production_countries', ())])
        vtag.setMpaaList(sorted(cert for rel in item.get('release_dates', {}).get('results', ())
                                for cert in (rel.get('certification', ''),) if cert))
        vtag.setStudios([name for st in item.get('production_companies', ()) for name in (st.get('name'),) if name])

        # trailers:
        trailers_lang_priority = {lang: 0, 'en': 1}
        trailers = sorted(
            item.get('videos', {}).get('results', []),
            key=lambda t: (not t['official'], trailers_lang_priority.get(t['iso_639_1'], 9))
        )
        for t in trailers:
            if t['type'] == 'Trailer' and t['site'] == 'YouTube':
                vtag.setTrailer(f'plugin://plugin.video.youtube/play/?video_id={t["key"]}')
                break

        # ratings:
        vtag.setRatings({'tmdb': (item.get('vote_average', 0.0), item.get('vote_count', 0))}, 'tmdb')

        # keywords
        ff.keywords = {it['name']: it['id'] for it in next(iter(item.get('keywords', {}).values()), ())}

        # episode groups (show only)
        if episode_groups := item.get('episode_groups', {}):
            self._parse_episode_groups(episode_groups, ff)

        # rest:
        if episode_type := self.tmdb.parse_episode_type(item):
            vtag.setEpisodeType(episode_type)

        # vtag.setDateAdded() – LIBRARY
        # vtag.setEpisodeGuide() – JSON ?!
        # vtag.setFilenameAndPath ???

        # vtag.setPath ???
        # vtag.setProductionCode() ?
        # vtag.setSet() / vtag.setSetId() / vtag.setSetOverview() ???
        # vtag.setShowLinks()

        # vtag.setTags() - append_to_response=keywords & "keywords"
        # vtag.setTop250() ?
        # vtag.setTrailer()
        # vtag.setTvShowStatus() ?
        # vtag.setUniqueID() / vtag.setUniqueIDs()
        # vtag.setUserRating()

        # vtag.setSortTitle()
        # vtag.setSortSeason()
        # vtag.setSortEpisode()

        # vtag.setLastPlayed() - TRAKT
        # vtag.setPlaycount() – TRAKT
        # vtag.setResumePoint() – TRAKT

        # music videos info: vtag.setAlbum(), vtag.setArtists() vtag.setTrackNumber()

        # -- item --
        # setArt()
        # setAvailableFanart()
        # setContentLookup() – PLAYER
        # setDateTime() ???
        # setInfo() !!!  overlay, setoverview
        # setIsFolder()
        # setLabel()
        # setLabel2()
        # setProperties() / setProperty
        # setSubtitles() ???

        # --- seasons & episodes ---
        if ref.is_show:
            set_if(vtag.setSeriesStatus, 'status')
        if ref.type == 'show':
            # propagate seasons and episodes translations
            def update_episodes(it: JsonData) -> None:
                locales = it.get('_locales', {})
                for sub_it, *loc_items in episodes_iter(it, *locales.values()):
                    for loc, loc_it in zip(locales, loc_items):
                        sub_it.setdefault('_locales', {}).setdefault(loc, {}).update(loc_it)
            locales = item.get('_locales', {})
            for sub_it, *loc_items in seasons_iter(item, *locales.values()):
                for loc, loc_it in zip(locales, loc_items):
                    sub_it.setdefault('_locales', {}).setdefault(loc, {}).update(loc_it)
                    update_episodes(sub_it)
            update_episodes(item)
            # get extra info
            if count := item.get('number_of_episodes'):
                ff.episodes_count = count
            if ep := item.get('last_episode_to_air'):
                ff.last_episode_to_air = self._make_sub_item(ff, ep, {}, {}, main_item=item)
            if ep := item.get('next_episode_to_air'):
                ff.next_episode_to_air = self._make_sub_item(ff, ep, {}, {}, main_item=item)
            # count episodes if there is only show seasons (without 'season/1' etc.)
            if seasons := item.get('seasons'):
                episode_counts = {it['season_number']: it['episode_count'] for it in seasons}
                if (not ff._continuous_episode_number
                        and (ep := ff.last_episode_to_air)
                        and ((cnt := episode_counts.get(ep.ref.season)) is None or not 1 <= (ep.ref.episode or 0) <= cnt)):
                    ff._continuous_episode_number = True
                if (not ff._continuous_episode_number
                        and (ep := ff.next_episode_to_air)
                        and ((cnt := episode_counts.get(ep.ref.season)) is None or not 1 <= (ep.ref.episode or 0) <= cnt)):
                    ff._continuous_episode_number = True
            # parse sub-item (seasons and episodes in parent item)
            self._parse_tmdb_item_children(item, ff, en_translations=en_translations, orig_translations=orig_translations)
            if ff.continuous_episode_number:
                enum = 1
                for sz in ff.season_iter():
                    sz._first_episode_number = enum
                    enum += sz.episodes_count or 0
            # fix season air date (from the first episode date)
            for sz in ff.season_iter():
                if sz.date is None:
                    if (ep := next(sz.episode_iter(), None)) and ep.date:
                        sz.vtag.setFirstAired(ep.date)
            # guess seasons date if missing (from previous season)
            if lst := ff.last_episode_to_air:
                date = lst.date if lst.ref.season == 1 else FFItem.AIRED_DATE
                for sz in ff.season_iter():
                    if sz.date is None:
                        sz._guessed_date = date
                    else:
                        date = sz.date
                    if sz.ref.season == lst.ref.season:
                        break

        return ff

    def _make_sub_item(self, ff: FFItem, it: JsonData, en_it: JsonData, orig_it: JsonData, *, main_item: JsonData) -> FFItem:
        ref = ff.ref
        fi = FFItem(evolve(ref, **{itype: num for itype in ('season', 'episode') if (num := it.get(f'{itype}_number')) is not None}))
        vtag = fi.vtag
        # NOTE: now season FFID is replaced to volatile FFID in InfoProvider._set_volatile_ffid().
        if fi.ref.is_season and (denorm := fi.ref.denormalize()):
            fi.ffid = denorm.ffid
        if not fi.ffid:
            fi.ffid = VideoIds.ffid_from_tmdb(it)
        req: MediaRequest | None = main_item.get('_request') if main_item else None
        fi.title = it.get('name', str(fi.season))
        fi.label = fi.title
        vtag.setUniqueIDs({'tmdb': str(it['id'])})
        if has_air := it.get('air_date'):
            air = datetime.fromisoformat(has_air).date()
            vtag.setFirstAired(str(air))
            vtag.setYear(air.year)
        if overview := it.get('overview'):
            fi.vtag.setPlotBase(overview)
        elif overview := en_it.get('overview'):
            fi.vtag.setPlotBase(overview)
        if 'vote_average' in it:
            vtag.setRatings({'tmdb': (it['vote_average'], it.get('vote_count', 0))}, 'tmdb')
        art, ff_art = {}, ff.getArt()
        if poster := it.get('poster_path'):
            art['poster'] = f'{self.tmdb.art_image_url}{poster}'
        if landscape := it.get('still_path'):
            art['landscape'] = f'{self.tmdb.art_landscape_url}{landscape}'
        if fanart := it.get('backdrop_path'):
            fanart = f'{self.tmdb.art_landscape_url}{fanart}'
            art['fanart'] = fanart
            art.setdefault('landscape', fanart)
        runtime = it.get('runtime')
        if runtime is not None:
            vtag.setDuration(runtime * 60)
        if count := it.get('episode_count'):
            fi._children_count = fi.episodes_count = count
        if episode_type := self.tmdb.parse_episode_type(it, tvshow_status=ff.vtag.getSeriesStatus()):
            vtag.setEpisodeType(episode_type)
        if title := en_it.get('name'):
            vtag.setEnglishTitle(title)
        elif req and req.locale.partition('-')[0] == 'en':
            vtag.setEnglishTitle(fi.title)
        if title := orig_it.get('name'):
            vtag.setOriginalTitle(title)
        elif req and req.locale.partition('-')[0] == main_item.get('original_language', 'en'):
            vtag.setOriginalTitle(fi.title)
        vtag.setTvShowTitle(ff.vtag.getTVShowTitle())
        vtag.setEnglishTvShowTitle(ff.vtag.getEnglishTvShowTitle())
        if img := ff_art.get('poster'):
            art['tvshow.poster'] = img
        if img := ff_art.get('fanart'):
            art['tvshow.fanart'] = img
        if art:
            fi.setArt(art)
        if en_title := en_it.get('name'):
            vtag.setEnglishTitle(en_title)
        fi.source_data = it
        if ref.is_show:
            fi.show_item = ff
        elif ref.is_season:
            fi.season_item = ff
            fi.show_item = ff.show_item
        return fi

    def _parse_tmdb_item_children(self, item: JsonData, ff: FFItem, *,
                                  en_translations: Sequence[JsonData],
                                  orig_translations: Sequence[JsonData]) -> None:
        """Parse single season/episode data for tv-show item."""

        ref = ff.ref
        if ref.season is None:
            # season_number > 0 -- omit season "0" (extra materials)
            seasons = item.get('seasons', ())
            if seasons:
                if seasons[-1]['season_number'] > 0 and 'episodes' in seasons[-1]:
                    # full season info, InfoDetails.SHOW_SEASONS
                    ff.children_items = [sz for it in seasons if it['season_number'] > 0 for sz in (self.parse_tmdb_item(it, parent_ffitem=ff),) if sz]
                else:
                    # shorten seasons info
                    locales = item.get('_locales', {})
                    variant_seasons = seasons_iter(seasons, locales.get(LOCALE_EN, {}), locales.get(LOCALE_ORIG, {}))
                    ff.children_items = [self._make_sub_item(ff, *sss, main_item=item) for sss in variant_seasons if sss[0]['season_number'] > 0]
                    # update kodi seasons
                    ff.vtag.addSeasons([(sz.season, sz.title) for sz in ff.children_items])
        elif ref.episode is None:
            locales = item.get('_locales', {})
            variant_episodes = episodes_iter(item, locales.get(LOCALE_EN, {}), locales.get(LOCALE_ORIG, {}))
            ff.children_items = [self._make_sub_item(ff, *eee, main_item=item) for eee in variant_episodes]

    def _parse_tmdb_person(self,
                           item: JsonData,
                           ff: FFItem,
                           en_translations: Sequence[JsonData],
                           orig_translations: Sequence[JsonData]) -> None:
        """Parse single person data."""

        def set_if(meth: Callable, *keys: str) -> None:
            for data in (item, *en_translations, *orig_translations):
                for key in keys:
                    value = data.get(key)
                    if value:
                        meth(value)
                        return

        vtag = ff.vtag
        set_if(vtag.setPlotOutline, 'biography')
        # year and dates (premiered, first aired)
        date = None
        try:
            birthday = item.get('birthday')
            if birthday:
                date = fromisoformat(birthday)
        except ValueError:
            pass
        if date:
            vtag.setYear(date.year)
            vtag.setPremiered(str(date))

        imgs = item.get('images', {}).get('profiles', [])
        if imgs:
            path = imgs[0]['file_path']
            if path:
                path = f'{self.tmdb.art_image_url}{path}'
                ff.setArt({'thumb': path, 'icon': path})

    def _parse_tmdb_collection(self,
                               item: JsonData,
                               ff: FFItem,
                               en_translations: Sequence[JsonData],
                               orig_translations: Sequence[JsonData]) -> None:
        """Parse single collection data."""

    def parse_item(self, item: JsonData, *, type: Optional[RefType] = None) -> Optional[FFItem]:
        """Parse abstract item (get id, name, poster, etc.)."""

        def set_if(setter: Callable, key: str) -> None:
            val = item.get(key)
            if val:
                setter(val)

        if item is None:
            return None
        ffid = VideoIds.make_ffid(tmdb=item['id'])
        ff = FFItem(ffid=ffid, type=type)
        vtag = ff.vtag
        ff.label = ff.title = item.get('title', item.get('name', ''))
        set_if(vtag.setPlotBase, 'overview')
        art = {}
        if poster := item.get('poster_path'):
            poster = f'{self.tmdb.person_image_url}/{poster}'
            art.update({'thumb': poster, 'poster': poster})
        if fanart := item.get('backdrop_path'):
            fanart = f'{self.tmdb.art_landscape_url}{fanart}'
            art['fanart'] = fanart
            art.setdefault('landscape', fanart)
        if art:
            ff.setArt(art)
        return ff

    def _parse_episode_groups(self, episode_groups_data: JsonData, ff: FFItem) -> None:
        """Parse episode groups data."""
        ff.episode_group.groups = [FFEpisodeGroup(tmdb_id=it['id'], name=it['name'], description=it.get('description', ''),
                                                  episode_count=it.get('episode_count', ()), group_count=it.get('group_count', ()),
                                                  type=FFEpisodeGroupType(it.get('type', 0)))
                                   for it in episode_groups_data.get('results', ())]

    def parse_items(self, items: Sequence[JsonData], *, type: Optional[RefType] = None) -> list[Optional[FFItem]]:
        return [self.parse_item(it, type=type) for it in items]

    def item_to_row(self, item: FFItem, now: Optional[float] = None) -> MediaInfoRow:
        """Convert FFItem to cache media info row."""
        if now is None:
            now = cur_time()
        ref: MediaRef = item.ref
        vtag = item.vtag
        return MediaInfoRow(
            type=ref.type,
            ffid=item.ffid or 0,
            main_ffid=ref.sql_main_ffid,
            season=ref.sql_season,
            episode=ref.sql_episode,
            tmdb=None,
            imdb=None,
            trakt=None,
            ui_lang=None,
            title=item.title,
            en_title=None,
            duration=vtag.getDuration(),
            data_type='json',
            data=json.dumps(item.__to_json__(), cls=JsonEncoder),
            updated_at=int(now),
        )

    def parse_tmdb_skel_item(self,
                             item: TmdbItemJson,
                             *,
                             locale: Optional[str] = None,
                             options: SkelOptions = SkelOptions.SHOW_LAST_SEASONS | SkelOptions.SHOW_EPISODE_DATE_APPROXIMATE,
                             ) -> FFItem:
        """Create items with skeleton children items (eg. episodes based on episodes count only)."""

        def dget(dct: dict, key: str, default: Any = None) -> Any:
            keys = key.split('/')
            for key in keys[:-1]:
                dct = dct.get(key, {})
            return dct.get(keys[-1], default)

        def set_if(meth: Callable, *keys: str) -> None:
            for data in (item,):
                for key in keys:
                    value = dget(data, key)
                    if value:
                        meth(value)
                        return

        ref = MediaRef(*item['_ref'])
        if item.get('success') is False:
            msg = item.get('status_message', '')
            raise ValueError(f'TMDB: get {ref} failed: {msg}')
        ff = FFItem(ref)
        if not ff.ffid:
            ff.ffid = VideoIds.ffid_from_tmdb(item)
        if ref.real_type in ('movie', 'episode'):
            ff.mode = ff.Mode.Playable
        else:
            ff.mode = ff.Mode.Folder
        ff.source_data = item
        vtag = ff.getVideoInfoTag()
        titletag: str = 'title' if ref.type == 'movie' else 'name'

        if 'id' not in item:
            fflog(f'ERROR: XXXXXXXXXXXXXXXXX\n\n{json.dumps(item, indent=2, cls=JsonEncoder)}')
        elif ff.ffid in VideoIds.KODI:
            vtag.setDbId(ff.ffid)  # Kodi DBID
        set_if(vtag.setIMDBNumber, 'imdb_id', 'external_ids/imdb_id')
        vtag.setUniqueIDs({name: str(val)
                           for name, key in (('tmdb', 'id'), ('tmdb_id', 'id'), ('imdb', 'imdb_id'), ('imdb', 'external_ids/imdb_id'))
                           for val in (dget(item, key),) if val},
                          'imdb')  # default id type
        vtag.setMediaType(ref.real_type)

        # duration
        runtime = item.get('runtime')
        if runtime is not None:
            vtag.setDuration(runtime * 60)

        # year and dates (premiered, first aired)
        date = None
        if ref.type == 'movie':
            date_keys = ('release_date', )
        else:
            date_keys = ('first_air_date', 'air_date')
        for date_key in date_keys:
            date_str = item.get(date_key)
            if date_str:
                try:
                    date = fromisoformat(date_str)
                    break
                except ValueError:
                    pass
        if date:
            vtag.setYear(date.year)
        if date:
            if ref.type == 'show':
                vtag.setFirstAired(date)
            else:
                vtag.setPremiered(date)

        # titles
        set_if(vtag.setTitle, 'title', 'name')
        set_if(vtag.setOriginalTitle, f'original_{titletag}')
        orig_lang = item.get('original_language', 'en')
        en_title = vtag.getOriginalTitle() if orig_lang == 'en' else vtag.getTitle()  # api lang is EN
        vtag.setEnglishTitle(en_title)
        if ref.main_type == 'show':
            vtag.setTvShowTitle(ff.title)
            vtag.setEnglishTvShowTitle(vtag.getEnglishTitle())
        if locale and locale not in self.EN_LOCALES:
            lang, _, region = locale.partition('-')
            if orig_lang == lang or locale in {f'{orig_lang}-{c}' for c in item.get('origin_country', ())}:
                # original title matches locale
                if title := item.get('original_title') or item.get('original_name'):
                    vtag.setTitle(title)
                    if ref.main_type == 'show':
                        vtag.setTvShowTitle(ff.title)
            elif translations := item.get('translations', {}).get('translations'):
                # search title in translations
                if title := (tr := next(iter(chain((it for it in translations if lang == it['iso_639_1'] and region == it['iso_3166_1']),
                                                   (it for it in translations if lang == it['iso_639_1']))), {}).get('data', {})).get('title') or tr.get('name'):
                    vtag.setTitle(title)
                    if ref.main_type == 'show':
                        vtag.setTvShowTitle(ff.title)
        ff.label = ff.title

        # ratings
        vtag.setRatings({'tmdb': (item.get('vote_average', 0.0), item.get('vote_count', 0))}, 'tmdb')

        # --- seasons & episodes ---
        return_ffitem: FFItem = ff
        if ref.type == 'show':
            if seasons := item.get('seasons'):
                last_ref: Optional[MediaRef]
                # episode-to-episode air date delta (air date step), used with SkelOptions.SHOW_EPISODE_DATE_APPROXIMATE
                ep_air_delta = timedelta()
                next_date: Optional[dt_date] = None
                if next_to_air := item.get('next_episode_to_air', {}):
                    next_ref = ref.with_season(next_to_air['season_number']).with_episode(next_to_air['episode_number'])
                    if date := next_to_air.get('air_date'):
                        next_date = dt_date.fromisoformat(date)
                else:
                    next_ref = None
                if last := item.get('last_episode_to_air', {}):
                    last_ref = ref.with_season(last['season_number']).with_episode(last['episode_number'])
                    last_aired = dt_date.fromisoformat(last['air_date'])
                    # guess air date delta from number of aired episodes from the last season
                    if last_ref.episode and last_ref.episode > 1:
                        if last_season_aired := next(iter(sz.get('air_date') for sz in seasons if sz['season_number'] == last_ref.season), None):
                            ep_air_delta = (last_aired - dt_date.fromisoformat(last_season_aired)) / last_ref.episode
                    # guess air date delta from from last and next air(ed) episodes
                    elif next_date:
                        ep_air_delta = next_date - last_aired
                else:
                    last_ref = None
                    last_aired = None
                ff.children_items = []
                for sz in seasons:
                    if (snum := sz['season_number']) > 0:  # skip special "Season 0"
                        if ref.season is not None and ref.season != snum:
                            continue
                        zref = ref.with_season(snum)
                        fz = FFItem(zref, ffid=VideoIds.ffid_from_tmdb(sz))
                        if ref.season is not None:
                            return_ffitem = fz
                        fz.show_item = ff
                        ff.children_items.append(fz)
                        fz.vtag.setTvShowTitle(vtag.getTitle())
                        fz.vtag.setEnglishTvShowTitle(vtag.getEnglishTitle())
                        sz_aired: Optional[dt_date] = None
                        if aired := sz.get('air_date'):
                            sz_aired = dt_date.fromisoformat(aired)
                            fz.vtag.setFirstAired(sz_aired)
                            fz.vtag.setYear(sz_aired.year)
                        if title := sz.get('name'):
                            fz.title = title
                        # season details contain episode list
                        if (zz := item.get(f'season/{snum}', {})) and 'episodes' in zz:
                            fz.children_items = []
                            for ep in zz['episodes']:
                                fe = FFItem(zref.with_episode(ep['episode_number']))
                                fe.show_item = ff
                                fe.season_item = fz
                                fz.children_items.append(fe)
                                fe.vtag.setTvShowTitle(vtag.getTitle())
                                fe.vtag.setEnglishTvShowTitle(vtag.getEnglishTitle())
                                if aired := ep.get('air_date'):
                                    ep_aired = dt_date.fromisoformat(aired)
                                    fe.vtag.setFirstAired(ep_aired)
                                    fe.vtag.setYear(ep_aired.year)
                                if name := ep.get('name'):
                                    fe.vtag.setTitle(name)
                                    fe.vtag.setEnglishTitle(name)
                        # no details, need to approximate episode air dates
                        elif count := sz.get('episode_count'):
                            fz.children_items = []
                            for enum in range(1, count + 1):
                                fe = FFItem(zref.with_episode(enum))
                                fe.show_item = ff
                                fe.season_item = fz
                                fz.children_items.append(fe)
                                fe.vtag.setTvShowTitle(vtag.getTitle())
                                fe.vtag.setEnglishTvShowTitle(vtag.getEnglishTitle())
                                # exact last aired match
                                if last_ref == fe.ref and last_aired:
                                    fe.vtag.setFirstAired(last_aired)
                                    fe.vtag.setYear(last_aired.year)
                                    # tune date approximation
                                    if sz_aired:
                                        ep_date = sz_aired + (enum - 1) * ep_air_delta
                                        if last_aired > ep_date:
                                            sz_aired += last_aired - ep_date
                                # exact last aired match
                                elif next_ref == fe.ref and next_date:
                                    fe.vtag.setFirstAired(next_date)
                                    fe.vtag.setYear(next_date.year)
                                    # tune date approximation
                                    if sz_aired:
                                        ep_date = sz_aired + (enum - 1) * ep_air_delta
                                        if next_date > ep_date:
                                            sz_aired += next_date - ep_date
                                # air date linear approximate
                                elif options & SkelOptions.SHOW_EPISODE_DATE_APPROXIMATE:
                                    # old episodes
                                    if last_aired and sz_aired:
                                        ep_date = min(last_aired, sz_aired + (enum - 1) * ep_air_delta)
                                        fe.vtag.setFirstAired(ep_date)
                                        fe.vtag.setYear(ep_date.year)
                                elif options & SkelOptions.SHOW_EPISODE_DATE_FIRST:
                                    if sz_aired:
                                        fe.vtag.setFirstAired(sz_aired)
                                        fe.vtag.setYear(sz_aired.year)
                                elif options & SkelOptions.SHOW_EPISODE_DATE_LAST:
                                    if last_ref and sz_aired:
                                        ep_date = None
                                        if snum < last_ref.season:
                                            if aired := next(iter(sz.get('air_date') for sz in seasons if sz['season_number'] == snum + 1), None):
                                                ep_date = max(sz_aired, dt_date.fromisoformat(aired) - timedelta(days=1))
                                        elif last_aired:
                                            ep_date = last_aired
                                        if ep_date:
                                            fe.vtag.setFirstAired(ep_date)
                                            fe.vtag.setYear(ep_date.year)
                                # if last_aired:
                                #     fe.vtag.setFirstAired(last_aired) # all episodes up to last aired are marked with the same date
                                # newer then last aired – we don't know nothing about them (except explicit "next_episode_to_air")
                                if last_ref == fe.ref:
                                    last_aired = None
                                    sz_aired = None

            if ref.is_episode:
                for ep in item.get(f'season/{ref.season}', {}).get('episodes', ()):
                    if (enum := ep['episode_number']) == ref.episode:
                        eref = ref
                        fe = FFItem(eref, ffid=VideoIds.ffid_from_tmdb(ep))
                        fe.show_item = ff
                        fe.season_item = return_ffitem
                        return_ffitem = fe
                        fe.vtag.setTvShowTitle(vtag.getTitle())
                        fe.vtag.setEnglishTvShowTitle(vtag.getEnglishTitle())
                        if aired := ep.get('air_date'):
                            ep_aired = dt_date.fromisoformat(aired)
                            fe.vtag.setFirstAired(ep_aired)
                            fe.vtag.setYear(ep_aired.year)
                        if title := ep.get('name'):
                            fe.title = title
                        break

        return return_ffitem

    def row_to_item(self, row: MediaInfoRow) -> FFItem:
        """Convert cache media info row to FFItem."""
        ref = row.ref
        if type(row.data) is not str:
            raise TypeError(f'Data of {ref} row is unsupported')
        data = json.loads(row.data)
        # return FFItem.__from_json__(data)
        item = FFItem(ref)
        item.__set_json__(data)
        return item

    def _update_items_progress(self,
                               items: Mapping[MediaRef, FFItem],
                               *,
                               progress: Progress,
                               ) -> None:
        # Append progress info.
        if progress is Progress.BASIC:
            trakt_playback_dict = get_playback_info(items)
            for it in items.values():
                self.update_item_progress(it, playback_info=trakt_playback_dict)
        elif progress is Progress.FULL:
            trakt_playback_dict = get_playback_info(items)
            for it in items.values():
                self.update_item_progress_full(it, playback_info=trakt_playback_dict)

    def _set_volatile_ffid(self, items: FFItemDict) -> None:
        """Change seasons FFID for volatile one."""
        def list_all(items: Iterable[FFItem]) -> Iterator[tuple[MediaRef, FFItem]]:
            for ffitem in items:
                yield ffitem.ref, ffitem
                if children := ffitem.children_items:
                    yield from list_all(children)

        all_items = dict(list_all(items.values()))

        if const.core.volatile_ffid and const.core.volatile_seasons:
            if seasons_refs := [ref for ref in all_items if ref.is_season]:
                for ref, ffid in service_client.create_ffid_dict(seasons_refs).items():
                    all_items[ref].ffid = ffid

    def find_item(self,
                  ref: MediaRef | FFItem | MediaRequest,
                  *,
                  progress: Progress = Progress.BASIC,
                  crew_limit: Optional[int] = None,
                  details: InfoDetails | str | None = None,
                  ) -> Optional[FFItem]:
        """Get single item info, ref can be denormalized."""
        if details is None:
            details = InfoDetails.new(const.media.info_details)
        elif isinstance(details, str):
            details = InfoDetails.new(details)
        # Take refs (from FFItem, ref.ref is correct too).
        req = ref
        ref = ref.ref
        # Resolve volatile FFID.
        if ref.is_volatile and const.core.volatile_ffid and (norm := service_client.get_ffid_ref(ref.ffid)):
            ref = norm
        # Resolve external ID (IMDB only).
        if not ref.tmdb_id:
            vid = ref.video_ids
            if vid.imdb:
                imdb = self.tmdb.find_id('imdb', vid.imdb)
                if imdb is None:
                    return None
                ref = imdb
            elif vid.mdblist and self.mdblist is not None:
                vid = self.mdblist.ref_ids(ref)
                if vid is None or not vid.tmdb:
                    return None
                ref = MediaRef.from_tmdb(ref.type, vid.tmdb, ref.season, ref.episode)
            else:
                return None
        # try to normalize
        # refs = ref.ref_list()  # all refs (ex. tvshow for episode)
        refs = [ref]  # no show/season for episode, TMDB already supports it
        if ref.is_normalized:
            rows = get_media_info(refs)
        else:
            rows = find_media_info(ref)
            if rows:
                refs = tuple(rows)
                recovered_ref = next((row.ref for row in rows.values() if row.denormalized_ref() == ref), None)
                if recovered_ref is not None:
                    ref = recovered_ref
        items = {item.ref: item for row in rows.values() for item in (self.row_to_item(row),)}
        missing = tuple(set(refs) - rows.keys())
        # something is missing and ref is not normalized (ex. episode ID not show/se/ep)
        if missing and not ref.is_normalized:
            if norm := ref.normalize():
                ref = norm
            # elif ref.is_volatile and const.core.volatile_ffid and (norm := service_client.get_ffid_ref(ref.ffid)):
            #     ref = norm
            else:
                if self.trakt is None:
                    return None
                try:
                    vid = ref.video_ids
                    mid = self.trakt.id_lookup(id=vid.value or 0, service=vid.service() or 'tmdb', type=ref.type)
                except ValueError:
                    fflog_exc()
                    return None
                if mid.ref is None:
                    return None
                ref = mid.ref
                # we have normalized ref, then we can try get it again
            refs = ref.ref_list()  # all refs (ex. tvshow for episode)
            rows = get_media_info(refs)
            items = {item.ref: item for row in rows.values() for item in (self.row_to_item(row),)}
            missing = tuple(set(refs) - rows.keys())
        # something is missing need to get data from tmdb
        if missing:
            requests = [req if ref == req.ref else ref for ref in missing]
            data = self.tmdb.get_media_dict_by_ref(requests, details=details).values()
            if not data:
                return None
            new_items = [self.parse_tmdb_item(d, crew_limit=crew_limit) for d in data]
            if const.core.info.save_cache:
                set_media_info([self.item_to_row(item) for item in new_items if item])
            items.update((item.ref, item) for item in new_items if item)
        try:
            ffitem = items[ref]
        except KeyError:
            ffitem: FFItem | None = None
            if details & InfoDetails.EPISODE_SKIP and ref.is_episode and (season_ref := ref.season_ref):
                for it in items.values():
                    if it.ref == season_ref and (children := it.children_items):
                        for ch in children:
                            if ch.ref == ref:
                                ffitem = ch
                                break
                        else:
                            continue
                        break
            if ffitem is None:
                fflog(f'No {ref} found in TMDB')
                return None
        # Join show info (eg. show_item in episode).
        if ref.season:
            show = items[MediaRef.tvshow(ref.ffid)]
            ffitem.show_item = show
            if show is not None:
                ffitem.vtag.setTvShowTitle(show.title)
                ffitem.vtag.setEnglishTvShowTitle(show.vtag.getEnglishTitle())
            if ref.episode:
                season = items[MediaRef.tvshow(ref.ffid, ref.season)]
                season.show_item = show
                ffitem.season_item = season
        # Change seasons FFID for volatile one.
        self._set_volatile_ffid(items)
        # Append progress info.
        self._update_items_progress({ffitem.ref: ffitem}, progress=progress)
        return ffitem

    @overload
    def get_items(self,
                  refs: Pagina[MediaRef] | Pagina[FFItem] | Pagina[FFRef] | ItemList[MediaRef] | ItemList[FFItem] | ItemList[FFRef],
                  *,
                  crew_limit: Optional[int] = None,
                  progress: Progress = Progress.BASIC,
                  details: InfoDetails | str | None = None,
                  keep_missing: Literal[False] = False,
                  duplicate: bool = True,
                  episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                  create_degraded: bool | None = None,
                  ) -> ItemList[FFItem]: ...

    @overload
    def get_items(self,
                  refs: Pagina[MediaRef] | Pagina[FFItem] | Pagina[FFRef] | ItemList[MediaRef] | ItemList[FFItem] | ItemList[FFRef],
                  *,
                  crew_limit: Optional[int] = None,
                  progress: Progress = Progress.BASIC,
                  details: InfoDetails | str | None = None,
                  keep_missing: Literal[True],
                  duplicate: bool = True,
                  episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                  create_degraded: bool | None = None,
                  ) -> ItemList[FFItem | None]: ...

    @overload
    def get_items(self,
                  refs: Iterable[MediaRef | FFItem | MediaRequest],
                  *,
                  crew_limit: Optional[int] = None,
                  progress: Progress = Progress.BASIC,
                  details: InfoDetails | str | None = None,
                  keep_missing: Literal[False] = False,
                  duplicate: bool = True,
                  episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                  create_degraded: bool | None = None,
                  ) -> list[FFItem]: ...

    @overload
    def get_items(self,
                  refs: Iterable[MediaRef | FFItem | MediaRequest],
                  *,
                  crew_limit: Optional[int] = None,
                  progress: Progress = Progress.BASIC,
                  details: InfoDetails | str | None = None,
                  keep_missing: Literal[True],
                  duplicate: bool = True,
                  episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                  create_degraded: bool | None = None,
                  ) -> list[FFItem | None]: ...

    @overload  # generic, used with GetItemKwargs (**kwargs)
    def get_items(self,
                  refs: Iterable[MediaRef | FFItem | MediaRequest],
                  *,
                  crew_limit: Optional[int] = None,
                  progress: Progress = Progress.BASIC,
                  details: InfoDetails | str | None = None,
                  keep_missing: bool,
                  duplicate: bool,
                  episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                  create_degraded: bool | None = None,
                  ) -> list[FFItem | None] | ItemList[FFItem | None]: ...

    def get_items(self,
                  refs: Iterable[MediaRef | FFItem | MediaRequest] | Pagina[MediaRef | FFItem],
                  *,
                  #: Limit the crew (actors, cast etc.).
                  crew_limit: Optional[int] = None,
                  #: Obtain progress mode.
                  progress: Progress = Progress.BASIC,
                  #: Scan episodes too (mostly not necessary).
                  details: InfoDetails | str | None = None,
                  #: If true, return None in ref is not obtained (useful for zip()) else skip missing item.
                  keep_missing: bool = False,
                  #: Keep refs duplicates in return list.
                  duplicate: bool = True,
                  #: Convert to episode groups. If ID (str) is used extra data will be collected.
                  episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                  #: Create degraded items (like episode with no data) if not enough data (e.g. show with seasons only).
                  create_degraded: bool | None = None,
                  ) -> Any:
        """Get list of item info in order, refs must be normalized."""
        # arguments: GetItemKwargs

        # Find one item in received info.
        def get_ff(ref: MediaRef, source: MediaRef | FFItem | None) -> FFItem | None:
            if ff := used.get(ref):
                if ff and duplicate:
                    if not isinstance(source, FFItem):
                        source = items.get(ref)
                    return self._set_dynamic_stuff(ff.clone(), source)
                return ff
            ff = items.get(ref)
            if not ff:
                ff = items.get(ref)
                if ff and ff.type in self._supported_details_types:
                    # Broken item: no into in TMDB, we get input FFItem (from `refs`)
                    fflog(f'BROKEN ITEM: {ff.ref} {ff.vtag.getUniqueIDs()}')
                    ff.broken = True
                    if broken_details and not ff.vtag.getTagLine():
                        ids = '\n'.join(f'{k}: {v}' for k, v in ff.vtag.getUniqueIDs().items())
                        dbs = '/'.join(str(x) for x in ff.ref[1:] if x is not None)
                        ff.vtag.setPlotBase(f'[B]BROKEN ITEM[/B]\ntype: {ff.ref.type}\nffid: {dbs}\n{ids}')
            if duplicate and ff and isinstance(source, FFItem):
                self._set_dynamic_stuff(ff, source)
            used[ref] = ff
            return ff

        if not hasattr(refs, '__getitem__'):
            refs = tuple(refs)
        if TYPE_CHECKING:
            refs = tuple(refs)  # only for type-checking
        # Get info.
        requests, req_refs, items = self._get_items(refs, crew_limit=crew_limit, progress=progress, details=details, episode_group=episode_group,
                                                    create_degraded=create_degraded)
        # Get item from MediaRequest
        refs_argument = refs
        # refs = [ref.item if isinstance(ref, MediaRequest) else ref for ref in refs]
        # Refs "used" to clone ref (and item) duplicated.
        used: dict[MediaRef, FFItem | None] = {}
        # Return list of FFItem in `refs` order.
        broken_details = bool(const.folder.style.broken)  # style is used in menu, here we check if add info for broken ffitem
        if keep_missing:
            it_gen = [None if req is None or ref is None else get_ff(ref, req.item) for req, ref in zip(requests, req_refs)]
        else:
            it_gen = [item for req, ref in zip(requests, req_refs) if req is not None and ref is not None and (item := get_ff(ref, req.item))]
        if isinstance(refs_argument, (Pagina, ItemList)):
            return ItemList.from_list(it_gen, refs_argument)
        return list(it_gen)

    def get_item_dict(self,
                      refs: Iterable[MediaRef | FFItem | MediaRequest],
                      *,
                      crew_limit: Optional[int] = None,
                      progress: Progress = Progress.BASIC,
                      details: InfoDetails | str | None = None,
                      #: Convert to episode groups. If ID (str) is used extra data will be collected.
                      episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                      #: Create degraded items (like episode with no data) if not enough data (e.g. show with seasons only).
                      create_degraded: bool | None = None,
                      ) -> FFItemDict:
        """Get dict of item info, refs must be normalized."""
        # Get info.
        if not isinstance(refs, Sequence):
            refs = tuple(refs)
        _, _, items = self._get_items(refs, crew_limit=crew_limit, progress=progress, details=details, episode_group=episode_group,
                                      create_degraded=create_degraded)
        # Restore children (seasons and episodes).
        stack = list(items.values())
        while stack:
            it = stack.pop()
            items.setdefault(it.ref, it)
            if it.children_items:
                stack.extend(it.children_items)
        # Return all known items.
        return items

    def get_item(self,
                 ref: Union[MediaRef, FFItem],
                 *,
                 crew_limit: Optional[int] = None,
                 progress: Progress = Progress.BASIC,
                 details: InfoDetails | str | None = None,
                 #: Convert to episode groups. If ID (str) is used extra data will be collected.
                 episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                 #: Create degraded items (like episode with no data) if not enough data (e.g. show with seasons only).
                 create_degraded: bool | None = None,
                 ) -> Optional[FFItem]:
        """Get single item info in order, refs must be normalized."""
        lst = self.get_items([ref], crew_limit=crew_limit, progress=progress, details=details, episode_group=episode_group,
                             create_degraded=create_degraded)
        return lst[0] if lst else None

    @logtime
    def _get_items(self,
                   refs: Sequence[MediaRef | FFItem | MediaRequest | None],
                   *,
                   #: Limit the crew (actors, cast etc.).
                   crew_limit: Optional[int] = None,
                   #: Obtain progress mode.
                   progress: Progress = Progress.BASIC,
                   #: Scan episodes too (mostly not necessary).
                   details: InfoDetails | str | None = None,
                   #: Media info locale (for titles etc.).
                   locale: str | None = None,
                   #: Convert to episode groups. If ID (str) is used extra data will be collected.
                   episode_group: str | FFEpisodeGroup | dict[MediaRef, MediaRef] | None = None,
                   #: Create degraded items (like episode with no data) if not enough data (e.g. show with seasons only).
                   create_degraded: bool | None = None,
                   ) -> tuple[Sequence[MediaRequest | None], Sequence[MediaRef | None], FFItemDict]:
        """Helper. Get list of item info in order, refs must be normalized."""
        if details is None:
            details = InfoDetails.new(const.media.info_details)
        elif isinstance(details, str):
            details = InfoDetails.new(details)
        if not locale:
            locale = self.tmdb.locale
        # Need to get episode group data from TMDB
        need_episode_group = False
        if episode_group:
            details |= InfoDetails.EPISODE_GROUPS
            if (isinstance(episode_group, str)
                    or (isinstance(episode_group, FFEpisodeGroup) and episode_group.items is None)):
                # if no details, need to get new details
                if isinstance(episode_group, FFEpisodeGroup):
                    episode_group = episode_group.tmdb_id

        # Input requests (or ffitems or refs).
        requests = [None if req is None else MediaRequest.new(req, default_details=details, default_locale=locale) for req in refs]
        req_refs = [None if req is None else req.ref for req in requests]
        del refs
        # Try to recover volatile ffid if allowed.
        if const.core.volatile_ffid:
            if volatile := [req.ref.ffid for req in requests if req and req.ref.is_volatile]:
                if norm := service_client.get_ffid_ref_dict(volatile):
                    req_refs = [ref if x is not None and x.is_volatile and (ref := norm.get(x.ffid)) else x for x in req_refs]
                    assert len(req_refs) == len(requests)
        # Resolve external IDs (IMDB, Trakt, tvdb).
        req_refs = self.lookup_refs(req_refs)
        assert len(req_refs) == len(requests)
        # Expand season/episode ref to all possible refs (parent season and show).
        all_refs: set[MediaRef] = {ref for ref in req_refs if ref}  # DO NOT expand season/episode ref, TMDB module already do it
        # Get rows from cache at once.
        rows = get_media_info(all_refs)
        # Create all existing items.
        items = {item.ref: item for row in rows.values() for item in (self.row_to_item(row),)}
        # Find missing refs and try get it from TMDB.
        allowed = get_typing_args(DetailsAllowed)
        if missing := set(all_refs) - rows.keys() - {ref for ref in all_refs if ref is None or ref.type not in allowed}:
            # Get data from TMDB for all missing info at once.
            need = [req.copy(item=ref) for req, ref in zip(requests, req_refs) if req is not None and ref in missing]
            data = self.tmdb.get_media_dict_by_ref(need, details=details)
            if isinstance(data, Mapping):
                # XXX --- DEBUG ---
                # if 0:
                #     from .tricks import JsonEncoder
                #     def safe_data(d):
                #         if isinstance(d, Mapping):
                #             return {f'{k}' if isinstance(k, MediaRef) else k: safe_data(v) for k, v in d.items()}
                #         if not isinstance(d, str) and isinstance(d, Sequence):
                #             return [safe_data(v) for v in d]
                #         return d
                #     with open('/tmp/media.json', 'w') as f:
                #         json.dump(safe_data(data), f, indent=2, ensure_ascii=False, cls=JsonEncoder)
                # XXX ---
                # Parse data and create items.
                new_items = [self.parse_tmdb_item(d, crew_limit=crew_limit) for d in data.values()]
                # Save new items in he cache.
                if const.core.info.save_cache:
                    set_media_info([self.item_to_row(item) for item in new_items if item])
                # Take new items, now we should have all items.
                items.update((item.ref, item) for item in new_items if item)
        # Restore episodes.
        if details & InfoDetails.EPISODE_SKIP:
            all_seasons = [it for ref, it in items.items() if ref.is_season and it.children_items]
            items.update((it.ref, it) for sz in all_seasons for it in sz.children_items or ())

        # Create degraded items if needed.
        if create_degraded is None:
            create_degraded = bool(details & InfoDetails.DEGRADED_EPISODES)
        if create_degraded:
            self._create_degraded(items)

        # Get user ratings.
        if (mode := const.ratings.all_folders) and mode != 'none' and settings.getBool('get_user_ratings'):
            from .ratings import enabled_rating_services
            rating_services = enabled_rating_services(action='get')
            with ThreadPoolExecutor(max_thread_workers()) as ex:
                futures = {srv.name: ex.submit(srv.list_ratings, all_refs) for srv in rating_services.values()}
                ratings = {}
                for srv_name, fut in futures.items():
                    for ref, rate in fut.result().items():
                        ratings.setdefault(ref, {})[srv_name] = rate.rating
            rate_funcs = {
                'min': lambda r: min(r.values()),
                'max': lambda r: max(r.values()),
                'avg': lambda r: int(sum(r.values()) / len(r) + .5),
                'first': lambda r: next(iter(r)),
            }
            print(ratings)
            func = rate_funcs.get(mode, (lambda r: r.get(mode)))
            for ref, rates in ratings.items():
                if rates and (item := items.get(ref)) and (ratings := func(rates)):
                    item.vtag.setUserRating(ratings)

        # Convert to episode groups if needed.
        # if isinstance(episode_group, str):
        #     episode_group = next(iter(gr for show in items.values() if show.episode_group.groups
        #                               for gr in show.episode_group.groups if gr.tmdb_id == episode_group), None)
        #     if episode_group and episode_group.items is None:
        #         episode_group = self.tmdb.episode_group(episode_group.tmdb_id)
        if need_episode_group and isinstance(episode_group, str):
            episode_group = self.tmdb.episode_group(episode_group)
        if episode_group:
            if isinstance(episode_group, FFEpisodeGroup) and episode_group.items is not None:
                for show in tuple(items.values()):
                    if show.ref.is_show:
                        if self.switch_episode_group(show, group=episode_group):
                            for sz in show.season_iter():
                                items[sz.ref] = sz
                                for ep in sz.episode_iter():
                                    items[ep.ref] = ep

        # Join show info (e.g. show_item in episode).
        for ref, ffitem in items.items():
            if ref.season:
                show = items.get(MediaRef.tvshow(ref.ffid))
                ffitem.show_item = show
                if show is not None:
                    ffitem.vtag.setTvShowTitle(show.title)
                    ffitem.vtag.setEnglishTvShowTitle(show.vtag.getEnglishTitle())
                if ref.episode:
                    season = items.get(MediaRef.tvshow(ref.ffid, ref.season))
                    if season:
                        season.show_item = show
                        ffitem.season_item = season
                else:
                    show_title = show.vtag.getTvShowTitle() if show else ''
                    show_en_title = show.vtag.getEnglishTvShowTitle() if show else ''
                    for ep in ffitem.episode_iter():
                        ep.show_item = show
                        ep.season_item = ffitem
                        if show_title:
                            ep.vtag.setTvShowTitle(show_title)
                            ep.vtag.setEnglishTvShowTitle(show_en_title)
            elif ref.type == 'show':
                show_title = ffitem.vtag.getTvShowTitle()
                for sz in ffitem.season_iter():
                    sz.show_item = ffitem
                    sz.vtag.setTvShowTitle(show_title)
                    for ep in sz.episode_iter():
                        ep.show_item = ffitem
                        ep.season_item = sz
                        ep.vtag.setTvShowTitle(show_title)
        # Change seasons FFID for volatile one.
        self._set_volatile_ffid(items)
        # Append progress info.
        self._update_items_progress(items, progress=progress)
        # Rewrite roles and another dynamic stuff (for input ffitems).
        for req, ref in zip(requests, req_refs):
            if ref is not None and (oitem := items.get(ref)) is not None and req is not None and (iitem := req.ffitem) is not None:
                self._set_dynamic_stuff(oitem, iitem)
        # Return finding parts (request, FFItems from request, found FFItems).
        return requests, req_refs, items

    def lookup_refs(self, refs: Iterable[MediaRef | None]) -> Sequence[MediaRef | None]:
        """Lookup external services refs (IMDB, Trakt, TvDB)."""
        def get(ref: MediaRef) -> MediaRef | None:
            resolved: MediaRef | None = None
            vid = ref.video_ids
            if vid.tmdb:
                return ref
            if vid.imdb:
                resolved = self.tmdb.find_id('imdb', vid.imdb)
            else:
                srv, val = vid.service_and_value()
                if self.trakt is not None:
                    if srv in ('trakt', 'tvdb') and ref.type in ('movie', 'show', 'season', 'episode') and val is not None:
                        resolved = self.trakt.id_lookup(id=val, service=srv, type=ref.type).ref
                elif self.mdblist is not None:
                    if srv in ('mdblist',) and ref.type in ('movie', 'show', 'season', 'episode') and val is not None:
                        vid = self.mdblist.ref_ids(ref)
                        if vid and vid.tmdb:
                            resolved = MediaRef.from_tmdb(ref.type, vid.tmdb, ref.season, ref.episode)
            if resolved is not None and ref.type == 'show' and resolved.type == 'show':
                resolved = evolve(ref, ffid=resolved.ffid)  # keep season/episode for show ref
            return resolved

        is_external: list[bool] = []
        external_refs: list[MediaRef] = []
        refs = list(refs)
        # from typing_extensions import reveal_type
        # reveal_type(refs)  # list[MediaRef | None]
        for ref in refs:
            if ref is None or ref.tmdb_id:
                is_external.append(False)
            else:
                is_external.append(True)
                external_refs.append(ref)
        if not external_refs:
            return refs
        # optimization, try to get all refs at once (for multiple external refs) if mdblist API key is available,
        # and all refs are the same type and service (ex. movies with IMDB ID)
        if len(external_refs) > 1 and self.mdblist is not None:
            if len(providers := {ref.video_ids.service() for ref in external_refs}) == 1 and len(mtypes := {ref.type for ref in external_refs}) == 1:
                from ..api.mdblist import MediaProvider
                provider = providers.pop()
                mtype = mtypes.pop()
                if provider in get_typing_args(MediaProvider) and mtype in ('movie', 'show'):
                    if TYPE_CHECKING:
                        assert provider in ('imdb', 'tmdb', 'trakt', 'tvdb', 'mal', 'mdblist')
                    ids = {it.get(provider): it.get('tmdb')
                           for it in self.mdblist.get_full_ids(provider, mtype,
                                                               [val for ref in external_refs for val in [ref.video_ids.value] if val is not None])}
                    external_refs = []
                    for i, (ref, ext) in enumerate(zip(refs, is_external)):
                        if ext and ref is not None:
                            if tmdb := ids.get(ref.video_ids.value):
                                is_external[i] = False
                                refs[i] = evolve(ref, ffid=VideoIds.ffid_from_tmdb_id(tmdb))
                            else:
                                external_refs.append(ref)
                    if not external_refs:
                        return refs
        # get all refs in parallel (for multiple external refs)
        with ThreadPoolExecutor(max_thread_workers()) as pool:
            it = iter(pool.map(get, external_refs))
            return [next(it) if ext else ref for ref, ext in zip(refs, is_external)]

    # @logtime
    # @profiler(sort_by='tottime')
    def _create_degraded(self, items: FFItemDict) -> None:
        """Create degraded items (episodes)."""
        # FFItem._no_init_ = True

        def update_episodes(ffitem: FFItem, *, episode_start: int = 1, date: dt_date | None = None) -> tuple[int, dt_date | None]:
            """Create degraded episode items for season and return next episode number and date."""
            if ffitem.children_items is not None or ffitem._children_count is None:
                return episode_start, date
            snum = ffitem.ref.season or 0
            enum = 1
            border: MediaRef | None = None
            nxt: FFItem | None = None
            lst: FFItem | None = None
            if show := ffitem.show_item or items.get(MediaRef.tvshow(ffitem.ref.ffid)):
                lst = show.last_episode_to_air
                nxt = show.next_episode_to_air
                if ep := lst or nxt:
                    border = ep.ref
                    # check if episode numbering is continuous
                    if snum > 1 and (ep.ref.episode or 0) > ffitem.season_episode_count(ep.ref.season):
                        enum = episode_start
            ffitem.children_items = []
            if ffitem.date:
                date = ffitem.date
            elif date is None and border and ffitem.ref < border:
                date = dt_date(2000, 1, 1)  # arbitrary old date
            if date and ffitem.date is None:
                ffitem.vtag.setFirstAired(date)
            for _ in range(ffitem._children_count):
                ep_ref = ffitem.ref.with_episode(enum)
                if ep := items.get(ep_ref):
                    ffitem.children_items.append(ep)
                else:
                    if lst and ep_ref == lst.ref:
                        ep = lst
                    elif nxt and ep_ref == nxt.ref:
                        ep = nxt
                    else:
                        if border and ep_ref > border:
                            date = None
                        ep = FFItem(ep_ref, season=ep_ref.season, episode=ep_ref.episode)
                        vtag = ep.vtag
                        vtag.setFirstAired(date)
                    ffitem.children_items.append(ep)
                    items[ep_ref] = ep
                enum += 1
            # if 1:  # --- XXX --- DEBUG --- XXX ---
            #     def ff2s(ff: FFItem | None) -> str:
            #         if ff is None:
            #             return 'None'
            #         return f'<{ff.ref:a}, date={ff.date}, title={ff.title!r}>'
            #     print(f'season {snum}:  date={ffitem.date}, lst={ff2s(lst)}, nxt={ff2s(nxt)}, border={f"{border:a}" if border else "None"}')
            #     for ep in ffitem.children_items:
            #         print(f' - ep {ep.ref.episode}: {ep.ref:a}, date={ep.date}, title={ep.title!r}')
            return enum, date

        for ffitem in tuple(items.values()):
            ref = ffitem.ref
            if ref.is_show:
                enum, date = 1, None
                for sz in ffitem.season_iter():
                    enum, date = update_episodes(sz, episode_start=enum, date=date)
            elif ref.is_season:
                update_episodes(ffitem)

    def _set_dynamic_stuff(self, item: FFItem, source: Optional[FFItem]) -> FFItem:
        """Rewrite roles and another dynamic stuff."""
        if source:
            item.role = source.role
            item.descr_style = source.descr_style
            item.vtag.setLastPlayed(source.vtag.getLastPlayedDateTime())
            item.cm_menu.extend(source.cm_menu)
            item.temp.__dict__.update(source.temp.__dict__)
            if item.progress is None:
                item.progress = source.progress
        return item

    def find_ids(self, source: ExternalIdType, refs: Iterable[Optional[MediaRef]]) -> Sequence[Optional[MediaRef]]:
        return self.tmdb.find_ids(source, (vid.imdb or '' for ref in refs if ref
                                           for vid in (ref.video_ids,) if vid.imdb))

    @overload
    def get_en_skel_items(self,
                          refs: Sequence[Union[MediaRef, FFItem]],
                          *,
                          keep_missing: Literal[False] = False,
                          locale: Optional[str] = None,
                          options: SkelOptions = SkelOptions.SHOW_LAST_SEASONS,
                          ) -> list[FFItem]: ...

    @overload
    def get_en_skel_items(self,
                          refs: Sequence[Union[MediaRef, FFItem]],
                          *,
                          keep_missing: Literal[True],
                          locale: Optional[str] = None,
                          options: SkelOptions = SkelOptions.SHOW_LAST_SEASONS,
                          ) -> list[Optional[FFItem]]: ...

    def get_en_skel_items(self,
                          refs: Union[Sequence[Union[MediaRef, FFItem]], Pagina[Union[MediaRef, FFItem]]],
                          *,
                          keep_missing: bool = False,
                          locale: Optional[str] = None,
                          options: SkelOptions = SkelOptions.SHOW_LAST_SEASONS,
                          ) -> Any:
        """Get list of item info in order, refs must be normalized."""

        # Find one item in received info.
        def get_ff(ref: MediaRef) -> Optional[FFItem]:
            if ff := items.get(ref):
                return ff
            ff = in_items.get(ref)
            if ff and ff.type in self._supported_details_types:
                # Broken item: no into in TMDB, we get input FFItem (from `refs`)
                fflog(f'BROKEN ITEM: {ff.ref} {ff.vtag.getUniqueIDs()}')
                ff.broken = True
            return ff

        # Get info.
        in_refs, in_items, items = self._get_en_skel_items(refs, locale=locale, options=options)
        # Return list of FFItem in `refs` order.
        if keep_missing:
            it_gen = [get_ff(ref) for ref in in_refs if ref]
        else:
            it_gen = [item for ref in in_refs if ref and (item := get_ff(ref))]
        if isinstance(refs, Pagina):
            return ItemList(it_gen, page=refs.page, total_pages=refs.total_pages)
        return list(it_gen)

    def _get_en_skel_items(self,
                           refs: Sequence[Union[MediaRef, FFItem]],
                           *,
                           locale: Optional[str] = None,
                           options: SkelOptions = SkelOptions.SHOW_LAST_SEASONS,
                           ) -> tuple[Sequence[Optional[MediaRef]], FFItemDict, FFItemDict]:
        """Get skeleton items by refs (or ffitems). Only base EN info."""
        # fix locale
        if locale:
            lng, _, rgn = locale.replace('_', '-').partition('-')
            locale = f'{lng}-{rgn.upper()}'
        # Take refs (from FFItem, ref.ref is correct too).
        in_items = {x.ref: x for x in refs if isinstance(x, FFItem)}
        refs = [x.ref for x in refs]
        # Easy normalize refs (TMDB seasons only).
        refs = [x.normalize() or x for x in refs]
        # Resolve external IDs (IMDB only).
        if any(not ref.tmdb_id for ref in refs):
            tmdb_refs = self.tmdb.find_ids('imdb', (vid.imdb for ref in refs for vid in (ref.video_ids,) if vid.imdb))
            xit = iter(tmdb_refs)
            in_refs = [next(xit) if vid.imdb else ref for ref in refs for vid in (ref.video_ids,)]
        else:
            in_refs = refs
        # Get rows from cache at once.
        # TODO: cache support !!!
        items, missing = {}, tuple({ref for ref in in_refs if ref})
        if True:
            # Need translation if different locale.
            if locale and locale not in self.EN_LOCALES:
                options |= SkelOptions.TRANSLATIONS
            # Get data from TMDB for all missing info at once.
            data = self.tmdb.get_skel_en_media(missing, options=options)
            if data:
                # Parse data and create items.
                new_items = [self.parse_tmdb_skel_item(d, locale=locale, options=options) for d in data.values()]
                # Save new items in he cache.
                # if const.core.info.save_cache:
                #     set_media_info([self.item_to_row(item) for item in new_items if item])
                # Take new items, now we should have all items.
                items.update((item.ref, item) for item in new_items if item)
        # Change seasons FFID for volatile one.
        self._set_volatile_ffid(items)
        # Rewrite roles and another dynamic stuff.
        for ref, ffitem in items.items():
            iff = in_items.get(ref)
            if iff:
                ffitem.role = iff.role
                ffitem.descr_style = iff.descr_style
                ffitem.vtag.setLastPlayed(iff.vtag.getLastPlayedDateTime())
                ffitem.temp.__dict__.update(iff.temp.__dict__)
                ffitem.cm_menu.extend(iff.cm_menu)
                if ffitem.progress is None:
                    ffitem.progress = iff.progress
        # Return finding parts (request, FFItems from request, found FFItems).
        return in_refs, in_items, items

    def item_genres(self, item: FFItem, *, translations: Optional[dict[CodeId, str]] = {}) -> list[FFItem]:
        """Return itm collection."""
        if item.source_data:
            genres = {g['id']: g['name'] for g in item.source_data.get('genres', ())}
            if translations is not None:
                genres = {gid: translations.get(gid, gname) for gid, gname in genres.items()}
            return [FFItem(type='genre', ffid=gid, mode=FFItem.Mode.Folder, label=gname)
                    for gid, gname in genres.items()]
        return []

    def item_collection(self, item: FFItem) -> Optional[FFItem]:
        """Return itm collection."""
        if item.source_data:
            return self.parse_item(item.source_data.get('belongs_to_collection'), type='collection')
        return None

    # def recount_progress(self, item: FFItem) -> bool:
    #     """Recount progress from watched item.progress info. Item must have all episodes."""
    #     if item.progress is None:
    #         return False

    # --- trakt sync progress ---

    @property
    def trakt_playback(self) -> MediaPlayInfoDict:
        """Get trakt playback progress."""
        if self._trakt_playback is None:
            with logtime(name='load trakt playback'):
                self._trakt_playback = get_playback()
        return self._trakt_playback

    @property
    def kodi_playback(self) -> list[KodiVideoInfo]:
        """Get kodi playback progress."""
        if self._kodi_playback is None:
            with logtime(name='load kodi playback'):
                self._kodi_playback = video_db.get_plays()
        return self._kodi_playback

    def reset_playback_info(self) -> None:
        """Forget playback info, will be loaded again."""
        self._trakt_playback = None
        self._kodi_playback = None

    # def progress_analize(self, progress_list: TraktPlaybackList) -> list[FFItem]:
    #     """Return FFItem with progreass from low-level trakty sync progeress rows."""

    def update_progress_list_progress(self, items: Iterable[FFItem] | ItemList[FFItem]) -> None:
        """
        Update progress info of the last watched list for shows in items based on MediaProgress from Trakt progress and ffinfo show skel from TMDB.
        `items` should be after ffinfo.get_items(..., progress=ffinfo.Progress.NO).
        """
        for it in items:
            pb = it.progress
            if pb is not None:
                pes = {x.ref: x for x in pb.bar}
                it.progress = pb = evolve(pb, bar=tuple(pes.get(ep.ref) or MediaProgressItem(ep.ref)
                                                        for ep in it.surrogate_episodes(aired=True)))
                cnt = it.progress.items_count()
                if cnt.total > 0:
                    it.progress = evolve(pb, progress=(100 * cnt.watched / cnt.total))

    def find_last_next_episodes(self,
                                # full tv-show item (show item with seasons and episodes)
                                it: FFItem,
                                # tree of  watched episodes: [season][episode] = MediaProgressItem()
                                *,
                                policy: Optional[NextWatchPolicy] = None,
                                today: Optional[dt_date] = None,
                                recount_progress: bool = True,
                                ) -> tuple[Optional[FFItem], Optional[FFItem]]:
        """Find last and next episode in tv-show progress."""
        # linear all aired episodes
        if today is None:
            today = dt_date.today()
        if policy is None:
            policy = NextWatchPolicy(const.indexer.tvshows.progress.next_policy)
        episodes = tuple(ep for sz in it.season_iter() for ep in sz.episode_iter() if ep.aired_before(today))
        if not episodes:
            return None, None
        watched: dict[MediaRef, datetime] = {}
        if it.progress is not None:
            watched = {epp.ref: epp.last_watched_at for epp in it.progress.bar if epp.has_last_watched_at}
        for ep in episodes:
            ep.temp.__dict__.setdefault('watched', watched.get(ep.ref))

        lst = nxt = None
        # Example: last watched: B, episodes: aBcDef
        if policy == NextWatchPolicy.LAST:
            # find first unwatched episode after latest watched episode (result: D, e)
            for ep in episodes:
                if ep.temp.watched:
                    lst = ep
            for pe, ne in pairwise(episodes):
                if pe.temp.watched and not ne.temp.watched:
                    nxt = ne
        elif policy == NextWatchPolicy.CONTINUED:
            # find last watched episode (result: B, c)
            ind, lst = max(enumerate(episodes), key=lambda x: x[1].temp.watched or datetime.min)
            for i in range(ind + 1, len(episodes)):
                if not (ep := episodes[i]).temp.watched:
                    nxt = ep
                    break
        elif policy == NextWatchPolicy.FIRST:
            # find first unwatched episode (result: B, a)
            for pe, ne in pairwise(episodes):
                if pe.temp.watched and not ne.temp.watched:
                    lst, nxt = pe, ne
                    break
        elif policy == NextWatchPolicy.NEWEST:
            # return newest episode (result: D, f)
            for ep in episodes:
                if ep.temp.watched:
                    lst = ep
                else:
                    nxt = ep

        if recount_progress:
            if it.progress is None:
                ep_progress = {}
            else:
                ep_progress = {epp.ref: epp for epp in it.progress.bar or () if epp.has_last_watched_at}
            bar = tuple(ep_progress.get(ep.ref, MediaProgressItem(ep.ref))
                        for sz in it.season_iter() for ep in sz.episode_iter() if ep.aired_before(today))
            progress = 100 * len(ep_progress) / (len(bar) or 1)
            play_count = min((epp.play_count for epp in bar), default=0)
            it.progress = MediaProgress(it.ref, bar=bar, progress=progress, play_count=play_count,
                                        last_episode=lst, next_episode=nxt)

        return lst, nxt  # last, next

    def update_item_progress(self,
                             it: FFItem,
                             *,
                             playback_info: Optional[MediaPlayInfoDict] = None,
                             ) -> FFItem:
        """Update item progress form playback DB (trakt sync cache). Base info."""
        # breakpoint()
        if playback_info is None:
            playback_info = self.trakt_playback  # TODO: some optimizations, try do not load all db
        # ---
        # if it.ref not in playback_info:
        #     playback_info[it.ref] = MediaPlayInfo(ref=it.ref)
        # ---
        if (pb := playback_info.get(ref := it.ref)):
            if ref.type == 'show' and it.aired_episodes_count and pb.progress_map and (new_count := it.aired_episodes_count - len(pb.progress_map)) > 0:
                fflog.debug(f'Update {it.ref} progress: {new_count} new episodes')
                pb.update_new_episodes(new_count)
                set_playback(pb)
            it.progress = pb.as_media_progress()
            vtag = it.vtag
            if not vtag.getUniqueID('slug') and pb.slug:
                vtag.setUniqueID(pb.slug, 'slug')
        for ch in it.children_items or ():
            if ch.progress is None:
                self.update_item_progress(ch, playback_info=playback_info)
        return it

    def update_item_progress_full(self, it: FFItem, *, playback_info: Optional[MediaPlayInfoDict] = None) -> FFItem:
        """Update item progress form playback DB (trakt sync cache). Full info."""
        if playback_info is None:
            playback_info = self.trakt_playback  # TODO: some optimizations, try do not load all db
        if (pb := playback_info.get(it.ref)):
            vtag = it.vtag
            if not vtag.getUniqueID('slug') and pb.slug:
                vtag.setUniqueID(pb.slug, 'slug')
        # TODO: implement it !!!
        return self.update_item_progress(it, playback_info=playback_info)  # TODO: replace it with full implementation

    def progressbar_str(self, watched: Sequence[Any], *, width: int = const.indexer.progressbar.width) -> str:
        """Return ||| progressbar for MediaProgress.bar."""
        col0 = const.indexer.progressbar.empty.color
        col1 = const.indexer.progressbar.partial.color
        col2 = const.indexer.progressbar.fill.color
        col3 = const.indexer.progressbar.watched.color
        ch0 = const.indexer.progressbar.empty.char
        ch1 = const.indexer.progressbar.partial.char
        ch2 = const.indexer.progressbar.fill.char
        ch3 = const.indexer.progressbar.watched.char
        bar, color = '', ''
        for i in range(width):
            a = len(watched) * i // width
            b = max(a + 1, len(watched) * (i + 1) // width)
            block = watched[a:b]
            if all(block):
                col, ch = col2, ch2
            elif any(block):
                col, ch = col1, ch1
            elif any(e is AREADY_WATCHED for e in block):
                col, ch = col3, ch3
            else:
                col, ch = col0, ch0
            if color != col:
                if color:
                    bar += '[/COLOR]'
                if col:
                    bar += f'[COLOR {col}]'
                color = col
            bar += ch
        if color:
            bar += '[/COLOR]'
        return bar

    def progress_descr_style(self, progress: MediaProgress) -> str:
        if settings.getBool('show.progressbar'):
            """Return progress description (tagline) style."""
            p = progress
            mtype = progress.ref.real_type
            # get const settings
            ON, OFF = 1, 0
            if mtype == 'movie':
                style = const.indexer.movies.progressbar.style
                bar_mode = ProgressBarMode(const.indexer.movies.progressbar.mode)
                width = const.indexer.movies.progressbar.width
            elif mtype == 'episode':
                style = const.indexer.episodes.progressbar.style
                bar_mode = ProgressBarMode(const.indexer.episodes.progressbar.mode)
                width = const.indexer.episodes.progressbar.width
            else:
                style = const.indexer.progressbar.style
                bar_mode = ProgressBarMode(const.indexer.progressbar.mode)
                width = const.indexer.progressbar.width
            # preselect mode
            if bar_mode is ProgressBarMode.NONE:
                return ''
            if bar_mode is ProgressBarMode.WATCHING:
                if 0 < p.progress < 100:
                    bar_mode = ProgressBarMode.PERCENT
                else:
                    return ''
            elif bar_mode is ProgressBarMode.PERCENT_AND_WATCHED:
                if progress.play_count and 0 < p.progress < 100:  # watching already watched video
                    OFF = AREADY_WATCHED
                bar_mode = ProgressBarMode.PERCENT
            # select mode
            pp = p.total_progress
            if bar_mode is ProgressBarMode.WATCHED:
                bar = p.bar
            elif bar_mode is ProgressBarMode.PERCENT:
                if progress.play_count and not 0 < p.progress < 100:
                    bar = (ON,)
                else:
                    pp = p.progress
                    bar = (ON,) * int(p.progress + .5) + (OFF,) * (100 - int(p.progress + .5))
            else:
                bar = (OFF,)
            # draw bar
            return style.format('{}', descr='{}', tagline='{}', p=p, progress=pp, percent=round(pp),
                                watching_progress=p.progress, progressbar=self.progressbar_str(bar, width=width))

    def update_item_kodi_data(self, items: Sequence[FFItem]) -> None:
        """Update raw kodi playback info (FFItem.kodi_data)."""
        plays = {pb.ref: pb for pb in video_db.get_plays_for(items)}
        for item in items:
            if pb := plays.get(item.ref):
                item.kodi_data = pb

    def web_url(self, ref: MediaRef) -> str:
        """Return link to media for humans."""
        if ref.tmdb_id:
            return self.tmdb.web_url(ref)
        return ''

    def _aliases_locales(self, locale: Union[None, str, Iterable[str]] = None) -> Iterable[str]:
        """Return locale for aliases search."""
        if locale is None:
            locale = (self.tmdb.lang or 'pl', 'en_US', 'en_GB')
        elif isinstance(locale, str):
            locale = (locale, 'en_US', 'en_GB')
        return locale

    def _parse_alternative_titles(self, data: JsonData, *, locale: str | Iterable[str] | None = None) -> tuple[FFTitleAlias, ...]:
        """Parse title aliases from TMDB data."""
        rx = re.compile(r'[-_]')
        locale = self._aliases_locales(locale)
        countries = {rx.split(loc)[-1].upper() for loc in locale}
        return tuple(FFTitleAlias(title=it['title'], country=it['iso_3166_1'].lower())
                     for it in data.get('results', data.get('titles', ()))
                     if it['iso_3166_1'] in countries)

    @overload
    def get_title_aliases(self, item: MediaRef, *, locale: str | Iterable[str] | None = None, orig_only_romanized: bool = True) -> FFItem | None: ...

    @overload
    def get_title_aliases(self, item: FFItem, *, locale: str | Iterable[str] | None = None, orig_only_romanized: bool = True) -> FFItem: ...

    def get_title_aliases(self,
                          item: MediaRef | FFItem,
                          *,
                          locale: str | Iterable[str] | None = None,
                          orig_only_romanized: bool = True,
                          ) -> FFItem | None:
        """Get ot update title alaises (from trakt.tv). Could update trakt_slug unique ID."""
        if not isinstance(item, FFItem):
            if (it := self.get_item(item)) is None:
                return None
            item = it
        ref = item.ref
        if (mtype := ref.type) not in ('movie', 'show'):
            return None
        # TMDB aliases.
        if const.media.aliases_service == 'tmdb':
            # already loaded (append_to_response)
            pass
        # Anything else means const.media.aliases_service == 'trakt'
        elif self.trakt is not None and (mtype := ref.real_type) in ('movie', 'show'):
            # get trakt ID
            ids = item.ids
            tid = ids.get('trakt', ids.get('slug'))  # type: ignore
            # missing, lookup trakt ID by TMDB ID
            if not tid and (real_type := ref.real_type) in ('movie', 'show', 'season', 'episode'):
                multi = self.trakt.id_lookup(ids.get('tmdb') or 0, service='tmdb', type=real_type)
                if multi:
                    if multi.vid.trakt:
                        tid = multi.vid.trakt
                    if multi.trakt_slug and not item.vtag.getUniqueID('trakt_slug'):
                        # update trakt_slug unique ID
                        item.vtag.setUniqueID(multi.trakt_slug, 'trakt_slug')
            # fallaback to IMDB ID
            if not tid:
                tid = ids.get('imdb')
            # get aliases from trakt
            if tid:
                rx = re.compile(r'[-_]')
                locale = self._aliases_locales(locale)
                countries = {rx.split(loc)[-1].lower() for loc in locale}
                orig_coutries = {c.lower() for c in item.vtag.getCountryCodes()}
                aliases = dict.fromkeys(FFTitleAlias(**it)
                                        for it in self.trakt.aliases(mtype, tid)
                                        if (it['country'] in countries
                                            or (it['country'] in orig_coutries and (not orig_only_romanized or is_romanized(it['title'])))))
                item.aliases_info = tuple(aliases)

            # Fallback to TMDB aliases if nothing found yet.
            if not item.aliases_info and (tmdb_id := ref.tmdb_id):
                # Get TMDB aliases.
                if data := self.tmdb.aliases(mtype, tmdb_id):
                    item.aliases_info = self._parse_alternative_titles({'results': data}, locale=locale)

    def get_episode_groups(self, show: FFRef | int) -> list[FFEpisodeGroup]:
        """Get episode groups for the show."""
        if isinstance(show, FFItem) and (groups := show.episode_group.groups) is not None:
            return groups
        groups = self.tmdb.episode_groups(show)
        if isinstance(show, FFItem):
            show.episode_group.groups = groups
        return groups

    def get_episode_group_items(self, group: str, *, info: bool = True, details: InfoDetails | str | None = InfoDetails.DEFAULT) -> list[FFItem]:
        """Get episode group items from TMDB by group hex ID."""
        items = self.tmdb.episode_group_list(group)
        if info:
            items = self.get_items(items, details=details)
        return items

    def switch_episode_group(self,
                             item: FFItem,
                             group: str | FFEpisodeGroup | None,
                             *,
                             #: Force safe operation, request all data from TMDB. If false use data from item if possible.
                             safe: bool = False,
                             ) -> FFItem | None:
        """
        Switch FFItem episode group. None means no episode group (tmdb order). Works only for show and episode.
        Group must exists in the item (media).
        Return the item or None if could not switch (the episode is not in the group).
        NOTE: item.ref.episode and item.episode could differ. The season too.
        The show is changed with all episodes (and new quasi-seasons).
        The episode is changed itself (season and episode numbers only).
        """
        # was tmdb order and is the same
        if item.episode_group.current is None and group is None:
            return
        ref = item.ref
        if not (ref.is_show or ref.is_episode):
            raise TypeError(f'Item {item.ref} is not show or episode')

        # show must contains all original episodes to avoid miss any episode
        # if the show has no episode groups should have episodes, if not user get_episodes=True.
        refreshed = False
        if ref.is_show and safe:
            details = InfoDetails.new(const.media.info_details)
            details &= InfoDetails.SEASON_MASK | InfoDetails.EPISODE_MASK
            details |= InfoDetails.INFO_LANG | InfoDetails.SHOW_SEASONS | InfoDetails.SEASON_EPISODES
            if it := self.get_item(item, details=details):
                item.children_items = it.children_items
                refreshed = True

        # -- switch to tmdb order (reset episode group) --
        if group is None:
            # reset single episode
            if ref.is_episode:
                item.season = ref.season
                item.episode = ref.episode
            # reset ffitem and all dependencies (show, seasons, episodes), only if not obtained from TMDB already
            elif not refreshed:
                # recover all original episodes
                episodes = list(item.episode_iter(special=True))
                for ep in episodes:
                    ep.season = ep.ref.season
                    ep.episode = ep.ref.episode
                # recover seasons, without details
                sz_label = self.api_labels('Season')  # Kodi global string: "Season"
                item.children_items = []
                for sz_num in sorted({ep.ref.season or 0 for ep in episodes}):
                    sz = FFItem(ref=MediaRef.tvshow(item.ref.ffid, sz_num), type='season', label=f'{sz_label} {sz_num}')
                    sz.season = sz_num
                    sz.title = sz.label
                    sz.vtag.setEnglishTitle(f'Season {sz_num}')
                    sz.vtag.setTvShowTitle(item.vtag.getTvShowTitle())
                    sz.vtag.setEnglishTvShowTitle(item.vtag.getEnglishTvShowTitle())
                    sz.children_items = [ep for ep in episodes if ep.ref.season == sz_num]
                    if sz.children_items:
                        sz.vtag.setFirstAired(sz.children_items[0].date)
                    item.children_items.append(sz)
            item.episode_group.current = None
            return item

        # -- switch to specific group --

        # group ID (str)
        if isinstance(group, str):
            if item.episode_group.groups is None:
                item.episode_group.groups = self.tmdb.episode_groups(item)
            try:
                # find group ID in show episode groups
                group = next(iter(gr for gr in item.episode_group.groups if gr.tmdb_id == group))
            except StopIteration:
                raise KeyError(group) from None
        # group object (FFEpisodeGroup), safe – get data from TMDB
        else:
            # find group ID in show episode groups, if not, just user `group`
            if item.episode_group.groups is not None:
                group = next(iter(gr for gr in item.episode_group.groups if gr.tmdb_id == group), group)

        # the same group?
        if (current_id := item.episode_group.tmdb_id) and current_id == group.tmdb_id:
            return None

        # the episode group details (mapping children)
        if group.items is None:
            ffgroup = self.tmdb.episode_group(show=item, group=group.tmdb_id)
            if ffgroup is None or ffgroup.items is None:
                raise KeyError(f'No episode group {group.tmdb_id} in item {item.ref}')
            group.items = ffgroup.items
        # mapping for the requested group
        mapping = group.mapping()

        # switch the episode itself only
        if ref.is_episode:
            if eref := mapping.get(ref):
                item.season = eref.season
                item.episode = eref.episode
                item.episode_group.current = group
                return item
            # no mapping, no episode in the episode group
            fflog.info(f'No episode mapping for {ref} in group {group.name!r} ({group.tmdb_id})')
            # item.season = 0
            # item.episode = 0
            # item.episode_group.current = group
            return None

        # switch show and all its children, the show shoud contains episodes
        episodes = {ep.ref: ep for ep in item.episode_iter(special=True)}
        item.children_items = []
        for gr in group.items:
            # sz = gr.clone()  ###
            sz = FFItem(ref=gr.ref, type='season')
            sz.title = gr.title
            item.children_items.append(sz)
            sz.children_items = []
            if gr.children_items is not None:
                if gr.children_items and (date := gr.children_items[0].date):
                    sz.vtag.setFirstAired(date)
                for ep in gr.children_items:
                    ep = episodes.get(ep.ref, ep)
                    if eref := mapping.get(ep.ref):
                        ep.season = eref.season
                        ep.episode = eref.episode
                        ep.episode_group.current = group
                    sz.children_items.append(ep)

        item.episode_group.current = group
        return item

    def reset_episode_group(self, item: FFItem) -> FFItem:
        """Reset episode group, set TMDB original season and episode order. Return the item."""
        self.switch_episode_group(item, group=None)
        return item

    def guess_label(self, item: FFItem):
        """Get best label, support missing episode titles and so on."""
        ref = item.ref
        if ref.is_episode:
            loc_label = self.api_labels['Episode']
            en_label = f'Episode {it.episode}'
            if item.title in (f'{loc_label} {it.episode}', en_label) and (en := item.vtag.getEnglishTitle()) != en_label:
                # locale title is generic and English title is specific, return English title
                return en
        elif ref.is_season:
            loc_label = self.api_labels['Season']
            en_label = f'Season {it.season}'
            if item.title in (f'{loc_label} {it.season}', en_label) and (en := item.vtag.getEnglishTitle()) != en_label:
                # locale title is generic and English title is specific, return English title
                return en
        if item.title:
            return item.title
        if en := item.vtag.getEnglishTitle():
            return en
        return item.label


def is_romanized(s: str) -> bool:
    """Check if string is romanized (contains only latin letters, digits, space and some punctuation)."""
    from unicodedata import category
    allowed_categories = {
        'Ll',  # Letter, lowercase
        'Lu',  # Letter, uppercase
        'Nd',  # Number, decimal digit
        'Pd',  # Punctuation, dash
        'Po',  # Punctuation, other
        'Zs',  # Separator, space
    }
    allowed_letters = '+'
    return all(category(c) in allowed_categories or c in allowed_letters for c in s)


#: Global media info provider.
ffinfo = InfoProvider(tmdb=tmdb_provider, trakt=trakt_provider, mdblist=mdblist_provider)


if __name__ == '__main__':
    from sys import stderr
    from pathlib import Path
    from pprint import pformat
    from textwrap import wrap
    import sty
    # from .tricks import dump_obj_gets
    from .cmdline import DebugArgumentParser, RatingAction, parse_ref
    from ..fake.fake_term import print_table, formatting, text_width, text_left, dim
    from .ratings import all_rating_services

    def parse_any_id(s: str) -> int:
        return VideoIds.guess_id(s).ffid

    def parse_id(v: str) -> tuple[int, ...]:
        ids = tuple(map(int, v.split('/')))
        if ids[0] < VideoIds.TMDB.start:
            return (ids[0] + VideoIds.TMDB.start, *ids[1:])
        return ids

    def parse_request(s: str) -> MediaRequest:
        """Parse request (ref, details, lang) string."""
        vv = s.split(':', 2)
        ref = parse_ref(vv[0])
        try:
            details = InfoDetails.new(vv[1]) if len(vv) > 1 else InfoDetails.NOT_DEFINED
        except KeyError as e:
            fflog.error(f'Unknown InfoDetails flag {e.args[0]!r}')
            raise ValueError(s)
        lang = vv[2] if len(vv) > 2 else ''
        return MediaRequest(ref, details=details, locale=lang)

    def parse_ref_or_request(s: str) -> MediaRef | MediaRequest:
        """Parse pure ref or request (ref, details, lang) string."""
        if ':' in s:
            return parse_request(s)
        return parse_ref(s)

    @overload
    def update_media_request(req: MediaRef, *, details: InfoDetails = InfoDetails.DEFAULT) -> MediaRef: ...

    @overload
    def update_media_request(req: MediaRequest, *, details: InfoDetails = InfoDetails.DEFAULT) -> MediaRequest: ...

    def update_media_request(req: MediaRef | MediaRequest, *, details: InfoDetails = InfoDetails.DEFAULT) -> MediaRef | MediaRequest:
        """Update media request with default details and locale."""
        if isinstance(req, MediaRequest):
            if not req.locale:
                req.locale = getattr(args, 'req_locale', '')
            if req.details == InfoDetails.NOT_DEFINED:
                req.details = getattr(args, 'details', details)
        return req

    def show_info(item: FFItem, *, indent: int = 0, aliases: bool = False, width: int = 199) -> None:
        pre = ' ' * indent
        it = item
        print(f'{pre}{sty.ef.bold}{sty.fg(35)}-- Info --{sty.rs.all}')
        vtag = it.getVideoInfoTag()
        descr = formatting(str(vtag.getPlot() or '')).replace('\n', f'{dim}[CR]{sty.rs.bg}')
        if text_width(descr) > width:
            descr = f'{text_left(descr, width)}{dim}…{sty.rs.bg}'
        if aliases:
            ffinfo.get_title_aliases(item)
        progress = f'{it.progress.progress:.2f}%' if it.progress else 'None'
        print_table((
            ('REF:', f'{it.ref:a}'),
            ('Label:', repr(it.getLabel())),
            ('Label2:', repr(it.getLabel2())),
            ('Title:', repr(vtag.getTitle())),
            ('TVShowTitle:', repr(vtag.getTVShowTitle())),
            ('EnglishTitle:', repr(vtag.getEnglishTitle())),
            ('EnglishTVShowTitle:', repr(vtag.getEnglishTvShowTitle())),
            ('OriginalTitle:', repr(vtag.getOriginalTitle())),
            ('Year:', repr(vtag.getYear())),
            ('Duration:', repr(vtag.getDuration())),
            ('IDs:', repr(vtag.getUniqueIDs())),
            ('Art:', pformat(it.getArt())),
            ('Descr:', descr),
            ('Progress:', progress),
            ('Aliases:', pformat([f'[{a.country}] {a.title}' for a in item.aliases_info], width=width)),
            ('Keywords:', '\n'.join(wrap(', '.join(sorted(item.keywords)), width=width))),
            ('Episode groups:', '\n'.join(f'- [{g.tmdb_id}] {g.name} ({g.type.name}): groups={g.group_count}, episodes={g.episode_count}' for g in item.episode_group.groups or ())),
            ('Abosulte number:', repr(it.absolute_episode_number())),
        ), indent=indent+1)
        print(f'{pre}{sty.ef.bold}{sty.fg(35)}--{sty.rs.all}')

    def print_full(item: Optional[FFItem], *, level: int = 0, parents: bool = True, recursive: bool = True,
                   ref: Optional[MediaRef] = None, details: bool = False, aliases: bool = False) -> None:
        today = dt_date.today()
        indent = '  ' * level
        if item:
            info = {}
            if ref is None:
                ref = item.ref
            elif ref != item.ref:
                print(f'{indent}{ref:a} =/= {item.ref:a} !!!')
            info[None] = f'{indent}{ref:a}, {item=}, en:{item.vtag.getEnglishTitle()!r}, orig:{item.vtag.getOriginalTitle()!r}'
            if item.date and item.date > today:
                date_str = f'{sty.fg.red}{item.date}{sty.rs.fg}'
            else:
                date_str = f'{item.date}'
            info['date'] = date_str
            # rate = item.vtag.getRating()
            if urate := item.vtag.getUserRating():
                info['user_rating'] = urate
            print(', '.join(f'{k}:{v}' if k is not None else v for k, v in info.items()))
            if details:
                show_info(item, indent=2*level, aliases=aliases)
            if parents:
                if it := item.season_item:
                    print(f'{indent}  - season: {it.ref:a}, {it!r}, en:{it.vtag.getEnglishTitle()!r}')
                if it := item.show_item:
                    print(f'{indent}  - show:   {it.ref:a}, {it!r}, en:{it.vtag.getEnglishTitle()!r}')
            if recursive:
                for ch in item.children_items or ():
                    print_full(ch, level=level + 1, parents=False, aliases=aliases)
        else:
            if ref is None:
                print(f'{indent}-- None')
            else:
                print(f'{indent}{ref:a} -- None')

    def add_args(pp: DebugArgumentParser, *, default_info: bool = False, default_details: InfoDetails | str | None = const.media.info_details) -> None:
        default_details = InfoDetails.new(default_details)
        if default_details is InfoDetails.DEFAULT:
            dd_str = 'DEFAULT'
        elif default_details & InfoDetails.DEFAULT == InfoDetails.DEFAULT:
            dd_str = f'DEFAULT,{default_details & ~InfoDetails.DEFAULT}'
        else:
            dd_str = str(default_details)
        dd_str = dd_str.replace('InfoDetails.', '').replace('|', ',').lower()
        pp.add_argument('-r', '--recursive', action='store_true', help='print children')
        pp.add_argument('-d', '--details', metavar='FLAG[,FLAG]…', type=InfoDetails.new, default=default_details,
                        help=f'set media detail flags (see InfoDetails) [{dd_str}]')
        # pp.add_argument('-c', '--aggregate-credits', action='store_true', help='get aggregate credits')
        pp.add_argument('--req-locale', default='', help='language (e.g. pl-PL) for InfoDetails.INFO_ORIG')
        pp.add_argument('-i', '--info', action='store_true', default=default_info, help='print extra info')
        pp.add_argument('--no-info', dest='info', action='store_false', help='do not print extra info')
        pp.add_argument('-J', '--json', action='store_true', help='print json')

    p = DebugArgumentParser(dest='op')
    p.add_argument('-K', '--kodi-path', metavar='PATH', type=Path, help='path to KODI user data')
    p.add_argument('--lang', help='language (default: pl_PL)')
    p.add_argument('--api-lang', help='override override media API language (default from settings)')
    with p.with_subparser('get', help='get (find) media request') as pp:
        pp.add_argument('id', type=parse_ref_or_request, metavar='REF[:DETAILS[:LANG]]', help='media request')
        pp.add_argument('--force-tmdb', action='store_true', help='skip cache and force TMDB')
        add_args(pp, default_info=True, default_details=const.sources.info_details)
        # pp.add_argument('--append-seasons', default=[], type=lambda s: [int(v) for v in s.split(',')], help='extra seasons for show')
        pp.add_argument('-p', '--progress', choices=[x.value for x in ffinfo.Progress], default='basic', help='get progress mode [basic]')
        pp.add_argument('-A', '--aliases', action='store_true', help='print aliases')
    with p.with_subparser('find') as pp:
        # pp.add_argument('type', choices=('movie', 'show', 'season', 'episode'))
        # pp.add_argument('id', type=parse_tmdb_id)
        # pp.add_argument('season', type=int, nargs='?')
        # pp.add_argument('episode', type=int, nargs='?')
        pp.add_argument('id', type=parse_ref_or_request, metavar='REF[:DETAILS[:LANG]]', help='media request')
        pp.add_argument('--aggregate-credits', action='store_true', help='get aggregate credits (flag AGGREGATE_CREDITS)')
        add_args(pp, default_info=True)
        pp.add_argument('-p', '--progress', choices=[x.value for x in ffinfo.Progress], default='basic', help='get progress mode [basic]')
        pp.add_argument('-A', '--aliases', action='store_true', help='print aliases')
    with p.with_subparser('list') as pp:
        pp.add_argument('ids', nargs='+', type=parse_ref_or_request, help='list of ids (movie/id, show/id[/season[/episode]]')
        add_args(pp)
    with p.with_subparser('dict') as pp:
        # pp.add_argument('type', choices=('movie', 'show'))
        pp.add_argument('ids', nargs='+', type=parse_ref_or_request, help='list of media (movie/id, show/id[/season[/episode]]')
        add_args(pp)
    with p.with_subparser('skel') as pp:
        # pp.add_argument('type', choices=('movie', 'show'))
        pp.add_argument('ids', nargs='+', type=parse_ref, help='list of ids (movie, show[/season[/episode]]')
        pp.add_argument('-r', '--recursive', action='store_true', help='print children')
        pp.add_argument('-p', '--parents', action='store_true', help='print parents (for season or episode)')
        pp.add_argument('-i', '--info', action='store_true', help='print extra info')
        pp.add_argument('-D', '--print-only-date', action='store_true', help='print only ref and date')
        pp.add_argument('-l', '--locale', help='change skeleton language/locale')
        pp.add_argument('-s', '--show', choices=('first', 'last', 'all', 'none'), default='last',
                        help='show seasons options [last]')
        pp.add_argument('-d', '--date', choices=('first', 'last', 'approx', 'none'), default='approx',
                        help='episode date guess [approx]')
    with p.with_subparser('episode_group') as pp:
        pp.add_argument('id', help='episode group TMDB ID')
        pp.add_argument('-i', '--info', action='store_true', help='print extra info')
        pp.add_argument('-r', '--recursive', action='store_true', help='print children')
        pp.add_argument('--mode', choices=('refs', 'items', 'switch'), default='refs', help='mode')
    with p.with_subparser('ratings') as pp:
        pp.add_argument('type', nargs='?', choices=('movie', 'show', 'season', 'episode', 'all'), default='all', help='get rating type')
        pp.add_argument('rating', nargs='*', type=int, action='extend', default=[], help='get rating value(s)')
        pp.add_argument('-s', '--set', action=RatingAction, nargs='+', metavar=('RATING', 'REF'), help='set rating for media ref')
        pp.add_argument('-r', '--remove', type=parse_ref, metavar='REF', nargs='+', help='remove rating for media ref')
        pp.add_argument('-S', '--services', type=lambda v: [s.strip() for s in v.split(',')], default=True, metavar='|'.join(all_rating_services) + '[,...]',
                        help='rating services to use (default: all)')
    args = p.parse_args()
    # print(args); raise SystemExit(0)  # DEBUG

    if args.lang or args.api_lang:
        from ..fake.fake_api import set_locale
        set_locale(args.lang, api=args.api_lang)

    if ids := getattr(args, 'ids', None):
        for req in ids:
            update_media_request(req)

    if True:
        from .. import service
        from ..service.fake_client import FakeServiceClient
        from ..service import client
        service.SERVICE = True  # direct access, like in the service
        service_client = client.service_client = FakeServiceClient()  # NOQA: F811

    item: Optional[FFItem]
    if args.op == 'get':
        if args.force_tmdb:
            # x = ffinfo.tmdb.get_media_list_by_ref([MediaRef('show', 84958)])
            x = ffinfo.tmdb.get_media_by_ref(args.id)
            print(json.dumps(x, indent=2))
            y = ffinfo.parse_tmdb_item(x)
            print(('-----------------------------------------------------------------'
                   f' ({len(json.dumps(y.__to_json__()))} B)'), file=stderr)
            print(json.dumps(y.__to_json__(), indent=2), file=stderr)
            set_media_info([ffinfo.item_to_row(y)])
            # xx = get_media_info([ref])
        else:
            req = update_media_request(args.id)
            item = ffinfo.get_item(req, details=args.details, progress=ffinfo.Progress(args.progress))
            print_full(item, recursive=args.recursive, ref=req.ref, details=args.info, aliases=args.aliases)
            if item and args.json:
                from .tricks import JsonEncoder
                print(json.dumps(item.__to_json__(), indent=2, cls=JsonEncoder))
    elif args.op == 'find':
        # ref = MediaRef(args.type, args.id, args.season, args.episode)
        if args.aggregate_credits:
            args.details |= InfoDetails.AGGREGATE_CREDITS
        ref = update_media_request(args.id)
        item = ffinfo.find_item(ref, details=args.details, progress=ffinfo.Progress(args.progress))
        print_full(item, recursive=args.recursive, ref=ref, details=args.info)
        if item and args.json:
            from .tricks import JsonEncoder
            print(json.dumps(item.__to_json__(), indent=2, cls=JsonEncoder))
    elif args.op == 'list':
        requests: list[MediaRef | MediaRequest] = args.ids
        items = ffinfo.get_items(requests, details=args.details, keep_missing=True)
        for ref, item in zip(requests, items):
            print_full(item, recursive=args.recursive, ref=ref.ref, details=args.info)
        if args.json:
            print(json.dumps([it.__to_json__() for it in items], indent=2))
    elif args.op == 'dict':
        requests: list[MediaRef | MediaRequest] = args.ids
        for ref, item in ffinfo.get_item_dict(requests, details=args.details).items():
            print_full(item, recursive=args.recursive, ref=ref, details=args.info)
    elif args.op == 'skel':
        sz_opts = {
            'none': SkelOptions.NONE,
            'first': SkelOptions.SHOW_FIRST_SEASONS,
            'last': SkelOptions.SHOW_LAST_SEASONS,
            'all': SkelOptions.SHOW_ALL_SEASONS,
        }
        ep_opts = {
            'none': SkelOptions.NONE,
            'first': SkelOptions.SHOW_EPISODE_DATE_FIRST,
            'last': SkelOptions.SHOW_EPISODE_DATE_LAST,
            'approx': SkelOptions.SHOW_EPISODE_DATE_APPROXIMATE,
        }
        opt = sz_opts[args.show] | ep_opts[args.date]
        for ref, item in zip(args.ids, ffinfo.get_en_skel_items(args.ids, locale=args.locale, options=opt)):
            if args.print_only_date:
                def print_date(it: FFItem):
                    print(f'{it.ref:20} : {it.date}')
                    if args.recursive:
                        for it in it.children_items or ():
                            print_date(it)
                print_date(item)
            else:
                print_full(item, recursive=args.recursive, details=args.info, parents=args.parents)
    elif args.op == 'episode_group':
        def parse() -> tuple[MediaRef | None, str | None]:
            ref, sep, gr = args.id.rpartition(':')
            if not sep and ('/' in gr or (gr.isdecimal() and len(gr) < 16)):  # media ref, list all episode groups
                return parse_ref(gr, type='show'), None
            if ref:
                ref = parse_ref(ref, type='show')
            if ref and gr.isdecimal() and len(gr) < 3:
                gr = ffinfo.tmdb.episode_groups(show=ref)[int(gr)].tmdb_id
            else:
                try:
                    if len(gr) < 24:
                        raise ValueError()
                    int(gr, 16)
                except ValueError:
                    if ref and (eg := next(iter(g for g in ffinfo.tmdb.episode_groups(show=ref) if g.name == gr), None)):
                        gr = eg.tmdb_id
            return ref, gr

        if args.mode == 'refs':
            ref, gr = parse()
            if ref and not gr:
                for group in ffinfo.tmdb.episode_groups(show=ref):
                    print(f'- [{group.tmdb_id}] {group.name} ({group.type.name}): groups={group.group_count}, episodes={group.episode_count}')
            elif gr:
                refs = ffinfo.tmdb.episode_group_mapping(show=ref, group=gr)
                for r1, r2 in refs.items():
                    print(f'{r1:a} -> {r2:a}')
        elif args.mode == 'items':
            ref, gr = parse()
            if gr:
                for it in ffinfo.get_episode_group_items(gr, info=True):
                    print_full(it, recursive=args.recursive, ref=it.ref, details=args.info)
        elif args.mode == 'switch':
            ref, gr = parse()
            if ref and gr:
                details = InfoDetails.new(const.media.info_details)
                details |= InfoDetails.SHOW_EPISODE_GROUPS | InfoDetails.SHOW_SEASONS  # | InfoDetails.SEASON_EPISODES
                details |= InfoDetails.SEASON_EN | InfoDetails.EPISODE_EN
                if item := ffinfo.find_item(ref, details=details):
                    print('\n --- \n')
                    print_full(item, recursive=args.recursive, ref=item.ref, details=args.info)
                    # raise SystemExit(0)
                    ffinfo.switch_episode_group(item, group=gr)
                    print('\n --- \n')
                    print_full(item, recursive=args.recursive, ref=item.ref, details=args.info)
                    ffinfo.reset_episode_group(item)
                    print('\n --- \n')
                    print_full(item, recursive=args.recursive, ref=item.ref, details=args.info)
        else:
            print(f'Unknown mode: {args.mode!r}')
    elif args.op == 'ratings':
        def print_it(it: FFItem) -> None:
            info = raw_rating = ''
            if it.season is not None and (title := it.vtag.getTvShowTitle()):
                info = f'   [{title or "–"} ({it.getProperty("show.year")})]'
            for srv in all_rating_services.values():
                if (rs := it.getProperty(f'{srv.name}.user_rating') or it.getProperty('user_rating')):
                    it.vtag.setUserRating(srv.from_service_rating(float(rs)))
                    if '.' in rs:
                        raw_rating = f'[{rs:>4}]'
                    else:
                        raw_rating = f'[{rs:>2}]'
                    # info += f'  {{{rs}}}'
                    break
            print(f' - {it.ref:20a} {it.vtag.getUserRating():>2}  {raw_rating}  {it.title or "–"} ({it.year or ""}){info}')
        from ..api.mdblist import mdblist
        if args.services is True:
            args.services = list(all_rating_services)
        if args.type == 'all':
            args.type = None
        if args.set:
            if 'trakt' in args.services and ffinfo.trakt is not None:
                print(ffinfo.trakt.add_user_ratings(args.set[1:], rating=args.set[0]))
            if 'tmdb' in args.services and ffinfo.tmdb is not None:
                for ref in args.set[1:]:
                    print(ffinfo.tmdb.add_user_rating(ref, rating=args.set[0]))
            if 'mdblist' in args.services:
                print(mdblist.add_user_ratings(args.set[1:], rating=args.set[0]))
        elif args.remove:
            if 'trakt' in args.services and ffinfo.trakt is not None:
                print(ffinfo.trakt.remove_user_ratings(args.remove))
            if 'tmdb' in args.services and ffinfo.tmdb is not None:
                for ref in args.remove:
                    print(ffinfo.tmdb.remove_user_rating(ref))
            if 'mdblist' in args.services:
                print(mdblist.remove_user_ratings(args.remove))
        else:
            if 'trakt' in args.services and ffinfo.trakt is not None:
                print('Trakt ratings')
                for it in ffinfo.trakt.user_ratings(args.type, rating=args.rating):
                    print_it(it)
            if 'tmdb' in args.services and ffinfo.tmdb is not None:
                print('TMDB ratings')
                for it in ffinfo.tmdb.user_ratings(args.type):
                    print_it(it)
            if 'mdblist' in args.services:
                print('MDBList ratings')
                for it in mdblist.user_ratings():
                    print_it(it)

    print(f'---\n  (( {ffinfo.tmdb._DEBUG_STATS} ))')
