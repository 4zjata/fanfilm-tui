
from __future__ import annotations

import re
import unicodedata
from datetime import date as dt_date, timedelta
from typing import Collection, Iterable, TYPE_CHECKING
from typing_extensions import TypedDict, NotRequired

from ..ff import cleantitle
from ..ff.log_utils import fflog
from const import const

if TYPE_CHECKING:
    from ..defs import MainMediaType
    from ..ff.item import FFItem


#: JustWatch full path, e.g. "/us/film/inception".
JWPath = str
#: Opaque JustWatch node ID.
JWNodeId = str
#: Content type: "movie" or "show".
ContentType = str


class JWExternalIds(TypedDict):
    imdbId: str | None


class JWContent(TypedDict):
    title: str
    originalReleaseYear: int
    externalIds: JWExternalIds | None
    fullPath: JWPath
    seasonNumber: NotRequired[int]
    episodeNumber: NotRequired[int]


class JWSearchNode(TypedDict):
    content: JWContent


class JWSearchEdge(TypedDict):
    node: JWSearchNode


class JWSeason(TypedDict):
    id: JWNodeId
    content: JWContent


class JWEpisode(TypedDict):
    id: JWNodeId
    content: JWContent


class JWPackage(TypedDict):
    clearName: NotRequired[str]
    shortName: str


class JWOffer(TypedDict):
    package: JWPackage
    standardWebURL: NotRequired[str]
    deeplinkRoku: NotRequired[str]


class JWOffers(TypedDict):
    flatrate: NotRequired[list[JWOffer]]
    buy: NotRequired[list[JWOffer]]
    rent: NotRequired[list[JWOffer]]
    free: NotRequired[list[JWOffer]]


class JWNewTitle(TypedDict):
    type: MainMediaType       # 'movie' or 'show'
    imdb_id: str              # IMDB id (e.g. 'tt1234567'), empty string if missing
    services: dict[str, str]  # [shortNames] = clearNames for display (e.g. ['nfx'] = 'Netflix')
    season: int               # season number (0 for movies / unknown)
    date: dt_date | None      # YYYY-MM-DD


# --- TypedDicts for newTitleBuckets response ---

class JWShowContent(TypedDict):
    showExternalIds: JWExternalIds | None


class JWShowRef(TypedDict):
    showObjectId: int
    showContent: NotRequired[JWShowContent]


class JWSeasonContent(TypedDict):
    seasonNumber: int


class JWNewTitleNode(TypedDict):
    objectId: int
    content: NotRequired[JWContent]
    objectType: NotRequired[str]
    show: NotRequired[JWShowRef]
    seasonContent: NotRequired[JWSeasonContent]


class JWNewOffer(TypedDict):
    package: NotRequired[JWPackage]


class JWNewTitleEdge(TypedDict):
    node: JWNewTitleNode
    newOffer: NotRequired[JWNewOffer]


class JWBucketKey(TypedDict):
    date: str
    package: NotRequired[JWPackage]


class JWBucketNode(TypedDict):
    edges: list[JWNewTitleEdge]


class JWBucketEdge(TypedDict):
    key: JWBucketKey
    node: NotRequired[JWBucketNode]


class JWPageInfo(TypedDict):
    hasNextPage: bool
    endCursor: str


class JWNewTitleBuckets(TypedDict):
    edges: list[JWBucketEdge]
    pageInfo: JWPageInfo


class JustWatchGraphQL:
    """Low-level JustWatch GraphQL API client."""

    def __init__(self, country: str = '', language: str = '') -> None:
        from jwgraph.gql import Client
        from jwgraph.gql.transport.requests import RequestsHTTPTransport
        self.base_url: str = 'https://apis.justwatch.com/graphql'
        self.transport = RequestsHTTPTransport(
            url=self.base_url,
            verify=True,
            retries=3,
        )
        self.client = Client(transport=self.transport, fetch_schema_from_transport=False)
        self.country: str = country if country else 'US'
        self.language: str = language if language else 'en'

    def search_item(self, title: str):
        from .justwatch_queries import SUGGESTED_TITLES
        params = {
            "country": self.country,
            "language": self.language,
            "first": 4,
            "filter": {
                "searchQuery": title
            }
        }
        return self.client.execute(SUGGESTED_TITLES, variable_values=params)

    def get_providers(self, node_id: str):
        from .justwatch_queries import TITLE_OFFERS
        params = {
            "platform": "WEB",
            "nodeId": node_id,
            "country": self.country,
            "language": self.language,
            "filterBuy": {
                "monetizationTypes": [
                    "BUY"
                ],
                "bestOnly": True
            },
            "filterFlatrate": {
                "monetizationTypes": [
                    "FLATRATE",
                    "FLATRATE_AND_BUY",
                    "ADS",
                    "FREE"
                ],
                "bestOnly": True
            },
            "filterRent": {
                "monetizationTypes": [
                    "RENT"
                ],
                "bestOnly": True
            },
            "filterFree": {
                "monetizationTypes": [
                    "ADS",
                    "FREE"
                ],
                "bestOnly": True
            }
        }
        return self.client.execute(TITLE_OFFERS, variable_values=params)

    def get_title(self, full_path: str):
        from .justwatch_queries import URL_TITLE_DETAILS
        params = {
            "platform": "WEB",
            "fullPath": full_path,
            "language": self.language,
            "country": self.country,
            "episodeMaxLimit": const.justwatch.episode_max_limit,
        }
        return self.client.execute(URL_TITLE_DETAILS, variable_values=params)

    def new_titles(self, after: str = '', *, object_types: Iterable[str] = ('MOVIE',)):
        from .justwatch_queries import NEW_TITLE_BUCKETS
        params = {
                "allowSponsoredRecommendations": {
                  "country": "PL",
                  "platform": "ANDROID"
                },
                "country": self.country,
                "filter": {
                  "excludeIrrelevantTitles": False,
                  "objectTypes": list(object_types),
                  "packages": []
                },
                "first": 5,
                "imageFormat": "WEBP",
                "language": self.language,
                "packages": [],
                "pageType": "NEW",
                "platform": "ANDROID",
                "priceDrops": False
              }
        if after:
            params['after'] = after
        return self.client.execute(NEW_TITLE_BUCKETS, variable_values=params)


class JustWatchClient:
    """
    Typed wrapper around JustWatchGraphQL for FanFilm use.

    Provides four high-level operations:
      - find_title()           – search + match + slug fallback → JWPath
      - get_movie_node_id()    – JWPath → JWNodeId (movies)
      - find_episode_node_id() – JWPath + S/E → JWNodeId (episodes)
      - get_offers()           – JWNodeId → JWOffers (streaming providers)
    """

    def __init__(self, country: str = 'US', language: str = 'en') -> None:
        self.country = country
        self.language = language
        self._api_en: JustWatchGraphQL | None = None
        self._api_local: JustWatchGraphQL | None = None

    def _get_api_en(self) -> JustWatchGraphQL:
        if self._api_en is None:
            self._api_en = JustWatchGraphQL(country=self.country)
        return self._api_en

    def _get_api_local(self) -> JustWatchGraphQL:
        if self._api_local is None:
            self._api_local = JustWatchGraphQL(country=self.country, language=self.language)
        return self._api_local

    def find_title(
        self,
        title: str,
        localtitle: str,
        year: int,
        imdb_id: str = '',
        *,
        content_type: ContentType = 'movie',
    ) -> JWPath | None:
        """
        Return JustWatch full path for the best-matching title, or None.

        Match priority:
          1. IMDB ID (strongest – skips all further checks).
          2. Normalized title + exact year.
          3. Normalized title + year ±1 (fuzzy).
          4. Direct slug-based lookup (fallback for unpopular titles).

        Local-language titles are searched with the local-language API instance
        so JustWatch returns results in the configured language.
        """
        norm_title = cleantitle.normalize(cleantitle.query(title)).lower()
        norm_local = cleantitle.normalize(cleantitle.query(localtitle)).lower()

        search_terms = [title.lower()]
        if localtitle and localtitle.lower() != title.lower():
            search_terms.append(localtitle.lower())

        edges: list[JWSearchEdge] = []
        for term in search_terms:
            try:
                api = self._get_api_local() if term == localtitle.lower() else self._get_api_en()
                resp = api.search_item(term)
                edges.extend(resp['popularTitles']['edges'])
            except Exception:
                continue

        exact: JWPath | None = None
        fuzzy: JWPath | None = None

        for edge in edges:
            try:
                content = edge.get('node', {}).get('content', {})
                if not content:
                    continue
                content_title = cleantitle.normalize(cleantitle.query(content.get('title', ''))).lower()
                release_year = int(content.get('originalReleaseYear', 0))
                cand_imdb = (content.get('externalIds') or {}).get('imdbId', '')
                title_match = content_title in (norm_title, norm_local)

                if imdb_id and cand_imdb and cand_imdb == imdb_id:
                    fflog(f'JW IMDB match: {content.get("fullPath")} imdb={cand_imdb}')
                    return content.get('fullPath')
                if title_match and release_year == year:
                    exact = content.get('fullPath')
                    break
                if title_match and abs(release_year - year) <= 1 and not fuzzy:
                    fuzzy = content.get('fullPath')
            except (KeyError, ValueError, TypeError):
                continue

        return exact or fuzzy or self._find_by_slug(title, localtitle, year, imdb_id, content_type)

    def get_movie_node_id(self, path: JWPath) -> JWNodeId | None:
        """Return the JustWatch node ID for a movie at *path*."""
        try:
            resp = self._get_api_en().get_title(path)
            return resp['url']['node']['id']
        except (KeyError, TypeError):
            return None

    def find_episode_node_id(
        self,
        show_path: JWPath,
        season: int,
        episode: int,
    ) -> JWNodeId | None:
        """Return the JustWatch node ID for S{season}E{episode} of the show at *show_path*."""
        try:
            api = self._get_api_en()
            show_resp = api.get_title(show_path)
            seasons: list[JWSeason] = show_resp['url']['node']['seasons']
            season_item = next(
                (s for s in seasons if s.get('content', {}).get('seasonNumber') == season),
                None,
            )
            if not season_item:
                return None
            season_path: JWPath = season_item['content']['fullPath']
            season_resp = api.get_title(season_path)
            episodes: list[JWEpisode] = season_resp['url']['node']['episodes']
            ep = next(
                (e for e in episodes if e.get('content', {}).get('episodeNumber') == episode),
                None,
            )
            return ep['id'] if ep else None
        except (KeyError, TypeError, StopIteration):
            return None

    def get_offers(self, node_id: JWNodeId) -> JWOffers | None:
        """Return streaming offers for *node_id*, or None on failure."""
        try:
            resp = self._get_api_en().get_providers(node_id)
            return resp.get('node')
        except Exception:
            return None

    # def new_titles(self, days: int = 3, *, max_pages: int = 20,
    #                allowed_short_names: Collection[str] | None = None) -> list[JWNewTitle]:
    #     """Movies added to VOD within the last *days* days, deduplicated by JW objectId."""

    # def new_shows(self, days: int = 3, *, max_pages: int = 20,
    #               allowed_short_names: Collection[str] | None = None) -> list[JWNewTitle]:
    #     """TV shows added to VOD within the last *days* days, deduplicated by JW showObjectId."""

    def new_items(self,
                  media_type: MainMediaType | None,
                  days: int = 3,
                  *,
                  max_pages: int = 20,
                  allowed_short_names: Collection[str] | None = None,
                  limit: int | None = None,
                  imdb_only: bool = False,
                  ) -> list[JWNewTitle]:
        """Movies/TV-shows added to VOD within the last *days* days, deduplicated by JW objectId/showObjectId."""
        if media_type is None:
            # No media means both movies and shows, so fetch them concurrently and merge results by date.
            def get(media: MainMediaType) -> list[JWNewTitle]:
                jw = JustWatchClient(country=self.country, language=self.language)
                return jw.new_items(media, days, max_pages=max_pages, allowed_short_names=allowed_short_names,
                                    limit=limit, imdb_only=imdb_only)

            from ..ff.requests import RequestsPoolExecutor
            # raise NotImplementedError('media_type=None is not supported yet')  # TODO: make concurrent requests
            with RequestsPoolExecutor() as ex:
                item_lists = ex.map(get, ['movie', 'show'])
                return sorted((it for lst in item_lists for it in lst), key=lambda t: t['date'] or dt_date.max, reverse=True)

        cutoff = dt_date.today() - timedelta(days=days)
        by_id: dict[int, JWNewTitle] = {}
        cursor = ''

        if media_type == 'show':
            object_types = ('SHOW',)
            extid_key = 'showExternalIds'
        else:
            object_types = ('MOVIE',)
            extid_key = 'externalIds'

        for _ in range(max_pages):
            resp = self._get_api_local().new_titles(after=cursor, object_types=object_types)
            buckets = resp.get('newTitleBuckets', {})
            stop = False

            for edge in buckets.get('edges', []):
                key = edge.get('key', {})
                bucket_date = key.get('date') or None
                if bucket_date is not None:
                    bucket_date = dt_date.fromisoformat(bucket_date)
                    if bucket_date < cutoff:
                        stop = True
                        break

                for title_edge in (edge.get('node') or {}).get('edges', []):
                    node = title_edge.get('node', {})
                    if media_type == 'show':
                        if node.get('objectType') != 'SHOW_SEASON':
                            continue
                        show = node.get('show', {})
                        jw_id = show.get('showObjectId', 0)
                        content = show.get('showContent') or {}
                    else:
                        jw_id = node.get('objectId', 0)
                        content = node.get('content') or {}
                    if not jw_id:
                        continue
                    offer_pkg = (title_edge.get('newOffer') or {}).get('package') or {}
                    key_pkg = key.get('package') or {}
                    pkg_short = (offer_pkg or key_pkg).get('shortName', '')
                    pkg_clear = offer_pkg.get('clearName', '') or pkg_short
                    imdb_id = (content.get(extid_key) or {}).get('imdbId') or ''
                    season_num = (node.get('seasonContent') or {}).get('seasonNumber', 0) or 0

                    if imdb_only and not imdb_id:
                        continue
                    if allowed_short_names and pkg_short not in allowed_short_names:
                        continue

                    jw_title = by_id.setdefault(jw_id, {
                        'type': media_type,
                        'imdb_id': imdb_id,
                        'services': {},
                        'season': season_num,
                        'date': bucket_date,
                    })
                    jw_title['services'][pkg_short] = pkg_clear
                    jw_title['season'] = max(jw_title['season'], season_num)

                    if limit and sum(1 for t in by_id.values() if t['imdb_id']) >= limit:
                        stop = True
                        break

            if stop:
                break
            page_info = buckets.get('pageInfo', {})
            if not page_info.get('hasNextPage'):
                break
            cursor = page_info.get('endCursor', '')
            if not cursor:
                break

        result = list(by_id.values())
        fflog(f'JW {media_type}s: {len(result)} movies, cutoff={cutoff}')
        return result

    def new_ffitems(self,
                    media_type: MainMediaType | None,
                    days: int = 3,
                    *,
                    max_pages: int = 20,
                    allowed_short_names: Collection[str] | None = None,
                    limit: int | None = None,
                    ) -> Iterable[FFItem]:
        from ..defs import MediaRef, VideoIds
        from ..ff.locales import datetime_format
        from ..ff.item import FFItem

        date_fmt = datetime_format('day_and_month')
        data = self.new_items(media_type=media_type, days=days, allowed_short_names=allowed_short_names, limit=limit, imdb_only=True)
        for it in data:
            if imdb := it['imdb_id']:
                ffitem = FFItem(ref=MediaRef(it['type'], VideoIds.make_ffid(imdb=imdb), it.get('season')))
                date = it['date']
                date_str = f'{date:{date_fmt}}' if date else ''
                ffitem.role = ' | '.join(p for p in (date_str, *it['services'].values()) if p)
                yield ffitem

    # private helpers

    def _find_by_slug(
        self,
        title: str,
        localtitle: str,
        year: int,
        imdb_id: str,
        content_type: ContentType,
    ) -> JWPath | None:
        """
        Fallback: construct a slug from the title and call get_title() directly.
        JustWatch popularTitles only returns popular content; this covers the rest.
        """
        country_code = self.country.lower()
        type_path = 'film' if content_type == 'movie' else 'serial'
        api = self._get_api_en()

        for cand_title in dict.fromkeys(t for t in [localtitle, title] if t):
            try:
                slug = re.sub(
                    r'[^a-z0-9]+',
                    '-',
                    unicodedata.normalize('NFKD', cand_title.lower())
                    .encode('ascii', 'ignore')
                    .decode(),
                ).strip('-')
                if not slug:
                    continue
                path = f'/{country_code}/{type_path}/{slug}'
                resp = api.get_title(path)
                if not (resp and resp.get('url') and resp['url'].get('node')):
                    continue
                node_content: JWContent = resp['url']['node'].get('content') or {}
                cand_imdb = (node_content.get('externalIds') or {}).get('imdbId', '')
                if imdb_id and cand_imdb:
                    if cand_imdb == imdb_id:
                        fflog(f'JW slug fallback (IMDB match): {path} imdb={cand_imdb}')
                        return path
                else:
                    rel_year = int(node_content.get('originalReleaseYear', 0))
                    if abs(rel_year - year) <= 1:
                        fflog(f'JW slug fallback (year): {path} year={rel_year}')
                        return path
            except Exception:
                continue

        return None
