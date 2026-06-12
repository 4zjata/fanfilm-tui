# -*- coding: utf-8 -*-
"""
FanFilm ‑ źródło: obejrzyj.to, filmyonline.cc, premiumsmart.eu
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
from typing import Optional, Union, Sequence, Iterator, ClassVar, List, Dict, TYPE_CHECKING
from typing_extensions import Literal
from urllib.parse import urlparse, quote, unquote
import re
import json
from json import JSONDecodeError
from base64 import b64encode
from lib.ff import requests
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.source_utils import sources_with_links
from lib.sources import Provider
if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.ff.types import JsonData
    from lib.ff.sources import SourceItem
    from lib.sources import SourceModule, SourceTitleAlias

R = re.compile


# ─── _source ─────────────────────────────────────────────────────────────────────

class _source(Provider):
    """Base scraper for sites like obejrzyj.to, filmyonline.cc, etc."""

    # --- scraper api ---
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ['pl']

    # --- internal ---
    PROVIDER: ClassVar[str]
    URL: ClassVar[str]
    HAS_TMDB_SUPPORT: ClassVar[bool] = True

    # what info get from video name
    INFO_HIT: ClassVar[Dict[Union[str, re.Pattern[str]], str]] = {
        # cam video: remove, sources.py add it itself
        R(r'\b(cam\d*|camrip|tsrip|hdcam|hqcam|dvdcam|dvdts|telesync)\b'): '',
        # audio type
        'lektor': 'lektor',
        'napisy': 'napisy',
        'dubbing': 'dubbing',
        # AI flag (audio or subtitles)
        R(r'\bai\b', flags=re.IGNORECASE): 'AI',
        # audio CAM
        R(r'\b(md|(dubbing|lektor|audio)[ _.-]+(kino|cam\d*))\b', flags=re.IGNORECASE): 'kino',
    }

    def __init__(self) -> None:
        self._session: Optional[requests.Session] = None
        self._found: JsonData = {}

    @property
    def session(self) -> requests.Session:
        """Return request session."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
                'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
                'DNT': '1',
            })
            self._session.get(self.URL)  # to get session "active" (cookies)
        return self._session

    def request(self, url: str) -> JsonData:
        """Site get request."""
        resp = self.session.get(url, headers={
            'accept': 'application/json',
            'referer': self.URL,
        })
        fflog.debug(f'[{self.PROVIDER.upper()}] request: {url} -> {resp.status_code}')
        if resp.status_code == 200:
            return resp.json()
        return {}

    def make_id(self, tid: Union[int, str],
                *,
                type: Optional[Literal['movie', 'series', 'person']] = None,
                service: Optional[Literal['tmdb', 'imdb']] = None,
                ) -> str:
        """Make tmdb/imdb id/"""
        if type and service:
            tid = b64encode(f'{service}|{type}|{tid}'.encode()).decode('ascii')
        return str(tid)

    def source_name(self, item: JsonData) -> str:
        """Retrun source (service) name from video JSON."""
        return urlparse(item['src']).hostname or ''

    def video_language(self, item: JsonData) -> str:
        """Retrun video language from video JSON."""
        return item.get('language', 'pl').lower()

    # ── public api ─────────────────────────────────────────────────────────

    @fflog_exc
    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        ids = self.ffitem.ids
        # get movie details
        if data := self._obtain_media(self.ffitem):
            self._found = {
                'id': data['id'],
                'titles': [v for k in ('name', 'original_title') if (v := data.get(k))],
                'videos': [v for v in data['videos'] if v.get('category') == 'full'],
            }
            # return json.dumps(self._found)
            return data['id']
        fflog(f'[{self.PROVIDER.upper()}] movie {self.ffitem} not found')
        return None  # movie not found

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        return '_'  # not needed, search episode is enough

    @fflog_exc
    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        ref = self.ffitem.ref
        if (show_item := self.ffitem.show_item) is None:
            return None
        titles = []

        # only movie and series work with tmbd/imdb; for episode we have to get internal show id first
        if data := self._obtain_media(show_item):
            media_id = data['id']
            titles = [v for k in ('name', 'original_title') if (v := data.get(k))]
        else:
            fflog(f'[{self.PROVIDER.upper()}] show {self.ffitem} not found')
            return None  # show not found

        # get episde details
        if data := self.request(f'{self.URL}api/v1/titles/{media_id}/seasons/{ref.season}/episodes/{ref.episode}?loader=episodePage'):
            data = data['episode']
            if name := data.get('name'):
                titles.append(name)  # add episode name
            self._found = {
                'id': media_id,
                'titles': titles,
                'videos': [v for v in data['videos'] if v.get('category') == 'full']
            }
            # return json.dumps(self._found)
            return media_id
        fflog(f'[{self.PROVIDER.upper()}] episode {self.ffitem} / {ref.season} / {ref.episode} not found')
        return None  # episode not found

    def sources(self, url: str, hostDict: List[str], hostprDict: List[str], from_cache: bool = False) -> 'List[SourceItem]':
        if not url:
            return []
        result = list(sources_with_links(self._videos({})))
        fflog(f'sources: {len(result)}')
        return result

    def resolve(self, url: str, *, buy_anyway: bool = False) -> str:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _query(self, title: str) -> str:
        """Prepare title to search."""
        title = re.sub(r'(?:^|(?<=\s))(?:I{1,3}[VX]?|[VX]I{0,3}|\d+)(?:(?=\s|$))',
                       '', title)  # remove numbers (digits and roman)
        title = re.sub(r'\b\w\b', '', title)  # remove single letters
        title = re.sub(r'\s+', ' ', title).strip()  # clear spaces
        if '/' in title:  # '/' is not sopported by site at all
            title = max(title.split('/'), key=lambda t: len(t.strip()))  # the longest word to search
        return title.strip()

    def _search(self, query: str, *, tmdb: int | str | None = None, imdb: str | None = None) -> list[JsonData]:
        fflog(f'query: {query!r}')
        try:
            data = self.request(f"{self.URL}api/v1/search/{quote(query, safe='')}?loader=searchPage")
        except JSONDecodeError:
            fflog(f'[{self.PROVIDER.upper()}] JSON error for query={query!r}')
            return []
        raw = data['results']
        fflog(f'search: {len(raw)} results')
        if tmdb:
            tmdb = int(tmdb)
        if tmdb or imdb:
            return [item for item in raw
                    if (not tmdb or tmdb == item['tmdb_id']) and (not imdb or imdb == item['imdb_id'])]
        return raw

    def _obtain_media(self, ffitem: FFItem) -> JsonData:
        """Return media details JSON."""
        ids = ffitem.ids
        mtype = ffitem.ref.type
        if mtype == 'movie':
            pass
        elif mtype == 'show':
            mtype = 'series'
        else:
            return {}
        # get movie details by tmdb/imdb id if service support it
        if self.HAS_TMDB_SUPPORT:
            for meta_service in ('tmdb', 'imdb'):
                if meta_id := ids.get(meta_service):
                    media_id = self.make_id(meta_id, type=mtype, service=meta_service)
                    try:
                        if data := self.request(f'{self.URL}api/v1/titles/{media_id}?loader=titlePage'):
                            return data['title']
                    except JSONDecodeError:
                        fflog(f'[{self.PROVIDER.upper()}] JSON error for {self.ffitem} id={media_id!r}')
                        break
        # IDs not supported by site or not found
        # When HAS_TMDB_SUPPORT=False there is no ID filtering — use the full title
        # so sequel numbers (e.g. "Pirates 3") are preserved for disambiguation.
        query = ffitem.title if (mtype == 'series' or not self.HAS_TMDB_SUPPORT) else self._query(ffitem.title)
        id_filter = ids if self.HAS_TMDB_SUPPORT else {}
        if found := self._search(query, tmdb=id_filter.get('tmdb'), imdb=id_filter.get('imdb')):
            fflog(f'[{self.PROVIDER.upper()}] {ffitem} found by search: {len(found)} item(s)')
            for media_data in found:
                media_id = media_data['id']
                try:
                    if data := self.request(f'{self.URL}api/v1/titles/{media_id}?loader=titlePage'):
                        return data['title']
                except JSONDecodeError:
                    pass
        # not found
        return {}

    @fflog_exc
    def _videos(self, data: JsonData) -> Iterator[SourceItem]:
        """Retrun list of found videos in movie() or episode(), agruments don't matter."""
        data = self._found
        if not data:
            data = {}
        media_id = data.get('id')
        videos = data.get('videos', ())
        fflog(f'[{self.PROVIDER.upper()}] {media_id=}, process {len(videos)} video(s)')
        if not videos:
            return
        # vtag = self.ffitem.vtag
        # titles = '|'.join(re.escape(t) for t in (self.ffitem.title, vtag.getEnglishTitle(), vtag.getOriginalTitle(),
        #                                          *data.get('titles', ())) if t)
        # rx_title = re.compile(fr'^({titles})\s*', flags=re.IGNORECASE)
        # known_sources = 'booster'  # '|'-separated list of well known sources
        for item in videos:
            # fflog(f'[{self.PROVIDER}][VIDEO]  video = {json.dumps(item, indent=2)}')
            if item.get('category') == 'full':
                name: str = item['name']
                lower_name = f"{name} {item.get('language_type', '')}".lower()
                quality: str = item['quality']
                lang: str = self.video_language(item)
                src: str = item['src']
                info = ' '.join(v for k, v in self.INFO_HIT.items() if (
                    k in lower_name if isinstance(k, str) else k.search(lower_name)) and v)
                fflog(f'[{self.PROVIDER.upper()}] id={media_id} video={src!r}, {info=}')
                yield {
                    'source': self.source_name(item) or '?',
                    'quality': quality.lower() if quality[:3].isdigit() else quality.upper(),  # 1080p.. but 4K, HD...
                    'language': lang,
                    'url': src,
                    # 'info': re.sub(fr'\b(?:((?:lektor|dubb?ing|napisy)\s?)?{lang})\b', r'\1', rx_title.sub('', name), flags=re.IGNORECASE).split(None, 1)[-1],
                    'info': info,
                    'direct': False,
                    'debridonly': False,
                    'filename': '',  # name
                    'premium': False,
                }
# ─── ObejrzyjTo ──────────────────────────────────────────────────────────────────


class ObejrzyjTo(_source):
    """Scraper for obejrzyj.to."""
    PROVIDER: ClassVar[str] = 'obejrzyjto'
    URL: ClassVar[str] = 'https://obejrzyj.to/'

    # def source_name(self, item: JsonData) -> str:
    #     """Retrun source (service) name from video JSON."""
    #     return item['name'].split(None, 1)[0] or super().source_name(item)


# ─── FilmyOnlineCc ───────────────────────────────────────────────────────────────

class FilmyOnlineCc(_source):
    """Scraper for filmyonline.cc."""
    PROVIDER: ClassVar[str] = 'filmyonline'
    URL: ClassVar[str] = 'https://filmyonline.cc/'
    HAS_TMDB_SUPPORT: ClassVar[bool] = False


# ─── PremiumsmartEu ──────────────────────────────────────────────────────────────

class PremiumsmartEu(_source):
    """Scraper for premiumsmart.eu."""
    PROVIDER: ClassVar[str] = 'premiumsmart'
    URL: ClassVar[str] = 'https://premiumsmart.eu/'

    def __init__(self) -> None:
        super().__init__()
        self._csrf_token: Optional[str] = None

    @property
    def session(self) -> requests.Session:
        """Return request session with CSRF token."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
                'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
                'DNT': '1',
                'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Linux"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
            })

            # Fetch the home page to get cookies and CSRF token
            resp = self._session.get(self.URL)
            if resp.status_code == 200:
                # Extract CSRF token from bootstrapData
                match = re.search(r'window\.bootstrapData\s*=\s*({.*});', resp.text, re.S)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        self._csrf_token = data.get('csrf_token')
                        fflog.debug(f'[{self.PROVIDER.upper()}] CSRF token obtained from bootstrapData object')
                    except JSONDecodeError:
                        fflog.debug(f'[{self.PROVIDER.upper()}] Could not parse bootstrapData, falling back to regex')

                if not self._csrf_token:
                    match = re.search(r'"csrf_token"\s*:\s*"([^"]+)"', resp.text)
                    if match:
                        self._csrf_token = match.group(1)
                        fflog.debug(f'[{self.PROVIDER.upper()}] CSRF token obtained via regex')
                    else:
                        fflog(f'[{self.PROVIDER.upper()}] failed to extract CSRF token from HTML')

                # Alternatively from cookie (it is URL-encoded)
                if not self._csrf_token and 'XSRF-TOKEN' in self._session.cookies:
                    self._csrf_token = unquote(self._session.cookies['XSRF-TOKEN'])
                    fflog.debug(f'[{self.PROVIDER.upper()}] CSRF token from cookie')
            else:
                fflog(f'[{self.PROVIDER.upper()}] failed to get homepage: {resp.status_code}')

        return self._session

    def request(self, url: str, _retried: bool = False) -> JsonData:
        """Site get request with CSRF token and retry on auth failure."""
        headers = {
            'accept': 'application/json',
            'referer': self.URL,
        }

        # The sess property will create the session and get the token on the first run.
        session = self.session

        if self._csrf_token:
            headers['X-XSRF-TOKEN'] = self._csrf_token
        else:
            # The token might not be found on the initial page load.
            fflog(f'[{self.PROVIDER.upper()}] no CSRF token after session init: {url}')

        resp = session.get(url, headers=headers)
        fflog.debug(f'[{self.PROVIDER.upper()}] request: {url} -> {resp.status_code}')

        if resp.status_code == 200:
            try:
                return resp.json()
            except JSONDecodeError as e:
                fflog(f'[{self.PROVIDER.upper()}] JSON decode error: {e}')
                return {}

        # If there's an auth error and we haven't retried, reset session and retry once.
        if resp.status_code in (401, 403, 419) and not _retried:
            fflog.debug(f'[{self.PROVIDER.upper()}] Auth error ({resp.status_code}). Assuming expired token, retrying.')
            self._session = None
            self._csrf_token = None
            return self.request(url, _retried=True)

        elif resp.status_code == 401:
            fflog(f'[{self.PROVIDER.upper()}] 401 unauthorized - missing or invalid CSRF token')
        elif resp.status_code == 403:
            fflog(f'[{self.PROVIDER.upper()}] 403 forbidden - check headers/cookies')
        else:
            fflog(f'[{self.PROVIDER.upper()}] unexpected status: {resp.status_code}')

        return {}

    def video_language(self, item: JsonData) -> str:
        lang_type = (item.get('language_type') or '').lower()
        if re.search(r'\b(lektor|dubbing|polski|pl)\b', lang_type):
            return 'pl'
        if re.search(r'\b(napisy|sub)\b', lang_type):
            return 'en'
        return 'pl'


def register(sources: List[SourceModule], group: str) -> None:
    """Register all scrapers."""
    from lib.sources import SourceModule
    for src in (ObejrzyjTo, FilmyOnlineCc, PremiumsmartEu):
        sources.append(SourceModule(name=src.PROVIDER, provider=src(), group=group))
