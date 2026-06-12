# -*- coding: utf-8 -*-
"""
FanFilm - źródło: ekinotv
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
from typing import Optional, List, Dict, Sequence, Mapping, ClassVar, TYPE_CHECKING
from functools import cached_property
import json
import re
from time import monotonic
import urllib.parse as urlparse
from html import unescape
from lib.ff import requests
from lib.ff import cleantitle, client, source_utils, control
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.settings import settings
from lib.sources import SourceModule
from lib.ff.kotools import xsleep
from const import const

import xbmcgui
if TYPE_CHECKING:
    from lib.ff.types import JsonResult
    from lib.sources import SourceItem, SourceTitleAlias


# ─── _source ─────────────────────────────────────────────────────────────────────

class _source:
    """Base scraper for site ekino.tv with premium support as separate scraper."""

    ffitem: FFItem

    PROVIDER: ClassVar[str] = ''

    # --- scraper api ---
    priority: ClassVar[int] = 1
    language: ClassVar[Sequence[str]] = ['pl']

    has_sort_order: ClassVar[bool] = False
    has_color_identify2: ClassVar[bool] = False
    use_premium_color: ClassVar[bool] = False

    # --- private "settings", TODO: move to const?
    CONNECTION_INTERVAL: ClassVar[float] = 2.1

    def __init__(self):
        stored_host = settings.getString('ekino.domain').strip()
        self.base_link = f'https://{stored_host}' if stored_host else const.sources.ekinotv.domain
        self.search_link = '/search/qf/?q=%s'
        self.resolve_link = '/watch/f/%s/%s'

        self.year = None
        self.anime = False

        self.title_query = ''
        self.divs = ''
        self.words = []
        self._connect_timestamp: float = 0
        self._sid: Optional[str] = None
        self._ekino_movie_id: Optional[str] = None

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        # fflog(f'searching movie {title=} {localtitle=} {year=} {aliases=}')
        return self._find(title, localtitle, source_utils.get_original_title(aliases), year, '/movie/')

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[List[str]]:
        # fflog(f'searching tvshow {tvshowtitle=} {localtvshowtitle=} {year=} {aliases=}')
        return self._find(tvshowtitle, localtvshowtitle, source_utils.get_original_title(aliases), year, '/serie/')

    def episode(self, url: Optional[List[str]], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        # fflog(f'searching episode {url=} {season=} {episode=}')
        if isinstance(url, list):  # for now
            if not url:  # check list is not empty
                return
            url = url[0]
        if not url:
            return

        if self.api_key:
            # link = url + f"+season[{season}]" + f"+episode[{episode}]"
            link = url + f"/{season}" + f"/{episode}"
            return link

        try:
            cookies = self._get_free_cookies()
            ua = self._get_free_ua()

            url = urlparse.urljoin(self.base_link, url)
            # fflog(f'{url=}')
            resp = requests.get(url, headers={'Cookie': cookies, 'User-Agent': ua})
            if resp:
                page_html = resp.text
            else:
                fflog(f'{resp=}')
                return

            series_divs = client.parseDOM(page_html, 'div', attrs={'id': 'list-series'})
            if series_divs:
                series_div = series_divs[0]
            else:
                return

            season_paragraphs = client.parseDOM(series_div, 'p')
            try:
                index = season_paragraphs.index('Sezon ' + season)
            except Exception:
                fflog(f'season {season} not found (wrong show? no year info)')
                return

            season_ul = client.parseDOM(series_div, 'ul')[index]
            episode_items = client.parseDOM(season_ul, 'li')
            # fflog(f'{len(episode_items)}')
            for row in episode_items:
                ep_no_list = client.parseDOM(row, 'div')
                if not ep_no_list:
                    continue
                ep_no = ep_no_list[0]
                if ep_no == episode:
                    link = client.parseDOM(row, 'a', ret='href')[0]
                    fflog(f' match: {link=}')
                    return link  # link
            fflog('no match')
            return None
        except Exception:
            fflog_exc()

    def sources(self, links, hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        # fflog(f'{links=}')

        sources = []

        if links is None or not links:
            # return sources
            pass

        if links:
            if isinstance(links, str):
                links = [links]
            else:
                links = list(dict.fromkeys(links))  # deduplicate
            # fflog(f'{links=}')
        else:
            links = []

        # url = links[0]  # no longer valid
        try:
            for url in links:
                # fflog(f'{url=}')

                if not self.api_key:
                    cookies = self._get_free_cookies()
                    ua = self._get_free_ua()

                    resp = requests.get(urlparse.urljoin(self.base_link, url),
                                        headers={'Cookie': cookies, 'User-Agent': ua})
                    if resp:
                        resp = resp.text
                    else:
                        fflog(f'{resp=}')
                        continue

                    sid_match = re.search(r"var SID\s*=\s*'([^']+)'", resp)
                    mid_match = re.search(r"var movieId\s*=\s*'?(\d+)'?", resp)
                    if sid_match:
                        self._sid = sid_match.group(1)
                        fflog(f'ekinotv: sources: SID={self._sid[:20]}...')
                    if mid_match:
                        self._ekino_movie_id = mid_match.group(1)
                        fflog(f'ekinotv: sources: movieId={self._ekino_movie_id}')

                    try:
                        rows = client.parseDOM(resp, 'ul', attrs={'class': 'players'})[0]
                        rows = client.parseDOM(rows, 'li')
                        rows.pop()
                        rows2 = client.parseDOM(resp, 'div', attrs={'role': 'tabpanel'})
                    except Exception:
                        rows = []
                        if (brak_linkow := 'Ten materiał nie posiada żadnych linków') in resp:
                            fflog('no links for this item')
                        elif (brak_linkow := ' usunięty') in resp:
                            fflog('content removed')
                    # fflog(f'{len(rows)=}  {len(rows2)=}')
                    for i in range(len(rows)):
                        try:
                            row = rows[i]

                            qual = client.parseDOM(row, 'img ', ret='title')
                            quality = 'SD'
                            if qual and 'Wysoka' in qual[0]:
                                quality = 'HD'
                            if qual and '4k' in qual[0]:
                                quality = '4k'

                            lang_type = client.parseDOM(row, 'i ', ret='title')
                            # fflog(f'{lang_type=}')
                            lang_type = lang_type[0] if lang_type else ''
                            lang, info = source_utils.get_lang_by_type(lang_type)

                            data = client.parseDOM(row, 'a')[0]
                            host = data.splitlines()[0].strip()

                            if host.lower() == 'upzone':  # odrzucenie serwera UPZONE
                                pass

                            ident = client.parseDOM(row, 'a', ret='href')[0]
                            ident = ident[1:]
                            ident = ident.rsplit('-')
                            # row2 = rows2[i]  # wrong when len(rows) != len(rows2)
                            # link = client.parseDOM(row2, "a", ret="onClick")[0]
                            links = client.parseDOM(rows2, 'a', ret='onClick')
                            if links:
                                for link in links:
                                    # if ident[0] in link and ident[1] in link:
                                    if all(x in link for x in (ident[0], ident[1])):
                                        break

                                if not link:
                                    continue

                                filename = url.rsplit('/')
                                filename = filename[-2] if 'movie' in url else filename[-1]
                                # opcjonalnie
                                filename = re.sub(rf"[.([_-]?\b(pl|napisy|dubbing|lektor)\b[.)\]_]?",
                                                  '', filename, flags=re.I)
                                filename = re.sub(r' {2,}', ' ', filename).strip()

                                sources.append({'source': host,
                                                'quality': quality,
                                                'language': lang,
                                                'url': link,
                                                'info': info,
                                                'direct': False,
                                                'debridonly': False,
                                                'filename': filename,
                                                'premium': False,
                                                })

                        except Exception:
                            fflog_exc()
                            continue

                else:
                    urlp = url.strip('/').rsplit('/')

                    video_type = urlp[0]

                    if video_type == 'movie':
                        video_id = urlp[-1]
                        data = self.api_connect(f'/movies/links/{video_id}')
                    else:
                        video_id = urlp[-3]
                        season = urlp[-2]
                        episode = urlp[-1]
                        data = self.api_connect(f'/series/links/{video_id}/{season}/{episode}')

                    # fflog(f'{data=}', 1)
                    if not isinstance(data, list):
                        continue

                    for d in data:

                        host = d.get('title', '')
                        lang, info = source_utils.get_lang_by_type(d.get('lang') or '')
                        link = d.get('source')
                        if not link:
                            fflog(f'ekinotv: sources API: no URL for host={host}')
                            continue

                        quality = 'HD'
                        if 'CAM' in host:
                            quality = 'CAM'  # best guess
                        if '4K' in host:
                            quality = '4K'
                        host = host.replace(f"[{quality}]", '').strip()  # little experience with this

                        filename = url.rsplit('/')
                        filename = filename[-2] if 'movie' in url else filename[-4] + \
                            f"-s{int(season):02}-e{int(episode):02}"

                        sources.append({'source': host,
                                        'quality': quality,
                                        'language': lang,
                                        'url': link,
                                        'info': info,
                                        'direct': False,   # False because link requires resolve()
                                        'debridonly': False,
                                        'filename': filename,
                                        'premium': True,
                                        })

            # fflog(f'{sources=}')
            fflog(f'sources: {len(sources)}')
            return sources

        except Exception:
            fflog_exc()
            return sources

    def resolve(self, url: str) -> Optional[str]:
        if self.api_key:
            return self._resolve_api_url(url)
        try:
            parts = url.split("'")
            host, video_id = parts[1], parts[3]

            _host_path_map = {
                'player': 'playerp',
                'playerbox': 'playerp',
                'dood': 'dood',
                'vidoo': 'vidoo',
                'hqq': 'hqq',
            }
            host_path = _host_path_map.get(host, host)

            if self._sid and self._ekino_movie_id:
                watch_url = f'{self.base_link}/watch/ex/{host_path}/{video_id}/{self._sid}/1/{self._ekino_movie_id}'
                fflog(f'ekinotv: resolve: mode /watch/ex/ {watch_url}')
            else:
                watch_url = urlparse.urljoin(self.base_link, self.resolve_link) % (host, video_id)
                fflog(f'ekinotv: resolve: mode /watch/f/ {watch_url}')

            cookies = self._get_free_cookies()
            ua = self._get_free_ua()
            resp = requests.get(watch_url, allow_redirects=False, headers={'Cookie': cookies + '; prch=true', 'User-Agent': ua})
            if not resp:
                fflog('ekinotv: resolve: no response from server')
                return None
            page = resp.text

            if hit := self._find_m3u8(page):
                return hit

            # primary ekino.ws format — link in href attribute
            href_matches = re.findall(r'href="([^"]+)"\s*target=".+?"\s*class=".+?"', page, re.DOTALL)
            if href_matches:
                stream_url = href_matches[0].replace('player.ekino-tv.link', 'hqq.to')
                fflog(f'ekinotv: resolve: href={stream_url[:100]}')

                if 'play.ekino.link' in stream_url:
                    result = self._fetch_player_url(stream_url)
                    if result:
                        return result
                    fflog('ekinotv: resolve: play.ekino.link returned no stream')
                    return None

                if 'streamsilk.' in stream_url:
                    stream_url += f'$${self.base_link}'
                return stream_url

            # fallback — embed via var url or iframe in response
            embed_url = None
            var_url_match = re.search(r"var url\s*=\s*'([^']+)'", page)
            if var_url_match:
                embed_url = var_url_match.group(1)
                fflog(f'ekinotv: resolve: var url embed={embed_url[:120]}')
            else:
                http_iframes = [s for s in client.parseDOM(page, 'iframe', ret='src') if s.startswith('http')]
                if http_iframes:
                    embed_url = http_iframes[0]
                    fflog(f'ekinotv: resolve: iframe embed={embed_url[:120]}')

            if not embed_url:
                fflog(f'ekinotv: resolve: nothing found ({len(page)} chars)')
                return None

            if 'play.ekino.link' in embed_url:
                result = self._fetch_player_url(embed_url)
                if result:
                    return result
                fflog('ekinotv: resolve: play.ekino.link (embed) returned no stream')
                return None

            if 'vidoo.stream' in embed_url:
                vidoo_id_match = re.search(r'vidoo\.stream/(?:p|e)/([A-Za-z0-9]+)', embed_url)
                if vidoo_id_match:
                    vid_id = vidoo_id_match.group(1)
                    vid_api = requests.post(
                        f'https://vidoo.stream/api/source/{vid_id}',
                        data=f'r={urlparse.quote(self.base_link)}&d=vidoo.stream',
                        headers={
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'Referer': f'https://vidoo.stream/p/{vid_id}',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Origin': 'https://vidoo.stream',
                        },
                    )
                    fflog(
                        f"ekinotv: vidoo api: status={getattr(vid_api, 'status_code', None)} body={getattr(vid_api, 'text', '')[:200]}")
                    if vid_api and vid_api.ok:
                        for entry in (json.loads(vid_api.text).get('data') or []):
                            file_url = entry.get('file', '')
                            if '.m3u8' in file_url:
                                fflog('ekinotv: resolve: vidoo m3u8 ok')
                                return file_url
                return None

            # unknown player — fetch and search for m3u8, fallback referer
            inner_page = client.request(embed_url)
            if inner_page:
                if hit := self._find_m3u8(inner_page):
                    return hit
            return embed_url + f'$${self.base_link}'

        except Exception:
            fflog_exc()
            return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _find(self, title, localtitle, originaltitle, year, search_type):
        # fflog(f'{title=} {localtitle=} {originaltitle=} {year=} {search_type=}')

        titles = [localtitle, originaltitle, title]  # establish priority order
        titles = list(filter(None, titles))  # remove empty
        titles = [t.lower() for t in titles]  # lowercasing helps deduplicate
        # titles = [t.replace("&", "&amp;") for t in titles]  # verify if needed
        titles = list(dict.fromkeys(titles))  # deduplicate

        url = None

        for title_original in titles:
            # Try with full title first (without removing subtitle after ":")
            title_full = title_original.replace("'", '').replace('-', ' ')
            url = self._search(title_full, year, search_type, titles)
            if url:
                break

            # If not found, try with cleaned title (removes subtitle after ":")
            title = cleantitle.query(title_original)
            if title != title_full:  # Only search if different
                url = self._search(title, year, search_type, titles)
                if url:
                    break

        # memory cleanup (worth it?)
        self.title_query = ''
        self.divs = ''  # this takes the most memory
        self.words = []

        # return results
        if not url:
            # fflog("search failed")
            pass
        return url

    def _search(self, search_string: str, year, search_type, all_titles: Optional[List[str]] = None) -> Optional[List[str]]:
        if all_titles is None:
            all_titles = []

        all_titles = [cleantitle.normalize(cleantitle.getsearch(t)) for t in all_titles]
        # all_titles = list(dict.fromkeys(all_titles))  # deduplicate

        titles_like_link = cleantitle.geturl('-'.join(all_titles))
        # titles_like_link += f"-{year}"  # may differ, e.g. for "Nocne graffiti"
        titles_like_link = titles_like_link.replace('⁄', '')
        titles_like_link = titles_like_link.replace('-', '')  # more universal (especially for fractions)

        cookies: str = ''
        if not self.api_key:
            try:
                cookies = client.request(self.base_link, output='cookie')
            except Exception:
                fflog_exc()

        search_titles: List[str] = [cleantitle.normalize(cleantitle.getsearch(search_string))]
        # fflog(f'{search_titles=}')

        all_titles = search_titles + all_titles
        all_titles = list(dict.fromkeys(all_titles))  # deduplication is important here
        relevant_franchises = source_utils.build_relevant_franchises(
            all_titles, const.sources.franchise_names, const.sources.franchise_names_sep
        )
        words = []
        for title in all_titles:
            if title:
                words += [title.split(' ')]
        words = tuple(words)
        if len(words) == 1:
            words = words[0]

        dopiski_do_usuniecia = '(HD|(HD)?(CAM|TS)|DUBBING( KINO(WY)?)?|lektor|pl|eng|napisy|translator!?|IVO|(DOBRA )?(KOPIA|JAKOSC)|4K)|V[2-4]'

        links: List[str] = []
        for title in search_titles:  # loop is redundant — always one title
            try:
                if not title:
                    continue

                if not words:
                    words = title.split(' ')

                if words == self.words:
                    continue
                self.words = words

                title_query = cleantitle.query(title)  # is this needed?
                title_query = urlparse.quote_plus(title_query).lower()

                divs = []
                if not self.api_key:
                    if title_query != self.title_query:  # avoid re-requesting when not needed
                        self.title_query = title_query
                        search_link = urlparse.urljoin(self.base_link, self.search_link) % title_query
                        # fflog(f'{search_link=}')

                        resp = requests.get(search_link, headers={'Cookie': cookies}, )  # request to server
                        if resp:
                            resp = resp.text
                        else:
                            fflog(f'{resp=}')
                            return

                        if '<title>Just a moment...</title>' in resp:
                            fflog('ekinotv: page currently behind Cloudflare')
                            return

                        divs = client.parseDOM(resp, 'div', attrs={'class': 'movies-list-item'})
                        # fflog(f'{div=}')
                        self.divs = divs
                    else:
                        divs = self.divs  # reuse previously cached result
                else:
                    # search_type = search_type.replace("/", "")
                    title_query = urlparse.unquote(title_query.replace('+', ' '))
                    divs = self._api_search(title_query, search_type.replace('/', ''))

                # fflog(f'\n')
                for row in divs or ():
                    # fflog(f'{row=}')
                    # row = client.parseDOM(row, 'div', attrs={'class': 'movieDesc'})[0]  # probably no longer valid

                    if not self.api_key:
                        link = client.parseDOM(row, 'a', ret='href')[0]
                        # fflog(f'{link=}')
                        if search_type not in link:
                            # fflog(f'\n')
                            continue

                        titles_found = client.parseDOM(row, 'a')
                        # fflog(f'{titles_found=}')
                        title1_found = titles_found[1]
                        # if present, usually the English title
                        title2_found = titles_found[2] if len(titles_found) > 2 else ''

                    else:
                        if row.get('type') != search_type.replace('/', ''):
                            continue

                        titles_found = row.get('title')
                        titles_found = titles_found.partition(' | ')[-1].strip()

                        link = titles_found.replace(' / ', ' ').replace(' ', '-')
                        if row.get('type') == 'movie':
                            link += f"-{row.get('year')}" + f"-{row.get('lang')}"
                        link += f"/{row.get('id')}"
                        link = re.sub(r'-{2,}', '-', link)  # clean up consecutive dashes
                        link = link.replace('(', '').replace(')', '')
                        link = link.translate(str.maketrans('', '', ":*?\"'\\.<>|&!,")
                                              )  # hopefully harmless sanitisation
                        link = link.lower()

                        titles_found = titles_found.split('/')
                        title1_found = titles_found[0]
                        title2_found = titles_found[-1]
                        if title2_found == title1_found:
                            title2_found = ''

                    # title1_found = title1_found.replace("&nbsp;", "")
                    title1_found = unescape(title1_found).strip()
                    title1_found = cleantitle.normalize(cleantitle.getsearch(title1_found))

                    title2_found = unescape(title2_found).strip()
                    title2_found = cleantitle.normalize(cleantitle.getsearch(title2_found))

                    link_for_compare = link.split('/')[-2]
                    link_for_compare = re.sub(f"-{dopiski_do_usuniecia}", '', link_for_compare, flags=re.I)

                    link_for_compare = re.sub(rf"-{year}(-\d{{4}})?", '',
                                              link_for_compare) if year else link_for_compare
                    link_for_compare = re.sub(rf"-{int(year)-1}(-\d{{4}})?", '',
                                              link_for_compare) if year else link_for_compare

                    link_for_compare = link_for_compare.replace('frac', '')  # fractions
                    link_for_compare = link_for_compare.replace('-', '')

                    # fflog(f' {title=}  {words=}  {title1_found=}  {title2_found=}  zadany {year=}  {search_type=}  {link=}')
                    if (
                        (
                          self._contains_all_words_v2(title1_found, words)
                          or title2_found and self._contains_all_words_v2(title2_found, words)
                        )
                        and
                        (  # more precise title check
                          re.search(
                              f'^{re.escape(title)}( [(-]? ?{dopiski_do_usuniecia}[,)]?)*$', title1_found, flags=re.I)
                          or title2_found and re.search(f'( / |^){re.escape(title)}( / |$)', title2_found)
                          or any(
                              re.search(pat, title1_found, re.I) or (title2_found and re.search(pat, title2_found, re.I))
                              for pat_list in relevant_franchises.values()
                              for pat in pat_list
                          )
                        )
                        and
                        (
                          (year in link or str(int(year)-1) in link)  # mainly for movies
                          or search_type == '/serie/'  # tvshows don't have year in the link
                        )
                        or link_for_compare == titles_like_link
                       ):
                        if self.api_key:
                            link = search_type + link
                        fflog(f'  match: {link=}')
                        if search_type == '/serie/':
                            return [link]  # for TV shows only 1
                        links.append(link)
            except Exception:
                fflog_exc()
                continue
        # fflog(f'{links=}')
        if not links:
            fflog(f"no match for {search_string=}")
            pass
        return links

    def _api_search(self, query: str, mode: str):
        query = urlparse.quote(query)

        if mode == 'movie':
            data = self.api_connect('/search/m/' + query.strip())
            if not isinstance(data, Sequence):
                fflog('ekinotv: api_search: /search/m/ failed, trying /search/')
                data = self.api_connect('/search/' + query.strip())
        elif mode == 'serie':
            data = self.api_connect('/search/s/' + query.strip())
            if not isinstance(data, Sequence):
                fflog('ekinotv: api_search: /search/s/ failed, trying /search/')
                data = self.api_connect('/search/' + query.strip())
        else:
            data = self.api_connect('/search/' + query.strip())

        if not isinstance(data, Sequence):
            fflog(f'ekinotv: api_search: error {data.__class__.__name__} for {query=} {mode=}')
            return None
        return data

    def _get_free_cookies(self) -> str:
        phpsessid = settings.getString('ekino.phpsessid').strip()
        auth = settings.getString('ekino.cookies_auth').strip()
        if phpsessid and auth:
            fflog('ekinotv: using auth cookies (PHPSESSID + o_autenticate)')
            return f'PHPSESSID={phpsessid}; o_autenticate={auth}'
        if auth:
            fflog('ekinotv: using auth cookies (o_autenticate only)')
            return f'o_autenticate={auth}'
        fflog('ekinotv: no auth cookies, using anonymous session')
        return client.request(self.base_link, output='cookie') or ''

    def _get_free_ua(self) -> str:
        return settings.getString('ekino.user_agent').strip() or source_utils.DEFAULT_UA

    def _find_m3u8(self, text: str) -> Optional[str]:
        found = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', text)
        return found[0] if found else None

    def _fetch_player_url(self, player_url: str) -> Optional[str]:
        """
        Fetch the player page and extract the target stream URL.
        Returns m3u8, iframe src, or None.
        """
        page = client.request(player_url)
        if not page:
            fflog(f'ekinotv: _fetch_player_url: empty response for {player_url[:80]}')
            return None

        if direct := self._find_m3u8(page):
            fflog('ekinotv: _fetch_player_url: found m3u8')
            return direct

        iframes = [s for s in client.parseDOM(page, 'iframe', ret='src') if s.startswith('http')]
        if iframes:
            fflog('ekinotv: _fetch_player_url: found iframe src')
            return iframes[0] + f"$${self.base_link}"

        return None

    def _resolve_api_url(self, url: str) -> Optional[str]:
        """
        Handle URLs returned by the premium API.
        API may return a direct stream or an intermediate page (e.g. play.ekino.link).
        """
        try:
            fflog(f'ekinotv: resolve api: {url[:100]}')

            if re.search(r'\.(m3u8|mp4)(\?|$)', url, re.I):
                return url

            if 'play.ekino.link' in url or 'ekino.link' in url:
                result = self._fetch_player_url(url)
                if result:
                    return result
                # last resort — var url in page content
                page = client.request(url)
                if page:
                    var_url_match = re.search(r"var url\s*=\s*'([^']+)'", page)
                    if var_url_match:
                        fflog('ekinotv: resolve api: var url from play.ekino.link')
                        return var_url_match.group(1) + f'$${self.base_link}'
                fflog('ekinotv: resolve api: play.ekino.link returned nothing')
                return None

            return url + f'$${self.base_link}'

        except Exception:
            fflog_exc()
            return None

    def _contains_all_words(self, str_to_check, words):
        words = list(filter(None, words))  # remove empty elements from list
        if not words or not str_to_check:
            fflog(f'{words=} {str_to_check=}')
            raise Exception('Błąd', 'zmienne nie mogą być puste')
        if self.anime:
            words_to_check = str_to_check.split(' ')
            for word in words_to_check:
                try:
                    liczba = int(word)
                    for word2 in words:
                        try:
                            liczba2 = int(word2)
                            if liczba != liczba2 and liczba2 != self.year and liczba != self.year:
                                return False
                        except ValueError:
                            continue
                except ValueError:
                    continue

        str_to_check = cleantitle.get_title(str_to_check).split()  # convert to list
        for word in words:
            word = cleantitle.get_title(word)
            if not word:
                continue
            if not word in str_to_check:
                return False
        return True

    def _contains_all_words_v2(self, str_to_check, words):
        if isinstance(words, tuple):
            for wds in words:
                ret = self._contains_all_words(str_to_check, wds)
                if ret:
                    break
        else:
            ret = self._contains_all_words(str_to_check, words)
        return ret

    @cached_property
    def api_key(self) -> str:
        return self.get_api()

    @classmethod
    def get_api(cls) -> str:
        """Default API implementation – none."""
        return ''

    def api_connect(self, path: str) -> JsonResult:
        """Return JSON form GET request."""
        BASEURL = 'https://ekino-tv.net/api'
        url = BASEURL + path
        UA = 'kodi-agent/1.1'
        header = {'User-Agent': UA, 'API-KEY': self.api_key}

        # keep at least CONNECTION_INTERVAL between connections
        now = monotonic()
        if self._connect_timestamp + self.CONNECTION_INTERVAL > now:
            xsleep(self._connect_timestamp + self.CONNECTION_INTERVAL - now)
        self._connect_timestamp = monotonic()
        try:
            response = requests.get(url, headers=header, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            fflog(f"error fetching data {e=} {url=}")
            return {'error': str(e)}  # error - empty result
        except requests.exceptions.RequestException as e:
            fflog(f"fetch error {e=} {url=}")
            return {'error': str(e)}  # error - empty result

    def pair_scraper(self):
        if settings.getString('ekinopremium.client_key'):
            ans = xbmcgui.Dialog().yesnocustom(
                'UWAGA - Wykryto zapisany klucz autoryzacyjny', '\nCzy chcesz go zastąpić (wygenerować nowy),\n czy wymazać (skasować)?',
                customlabel='Skasuj',
                yeslabel='Zastąp',
                nolabel='Anuluj',
            )
            if ans < 1:
                control.idle()
                return
            elif ans == 2:
                settings.setString('ekinopremium.pair_status', '')
                settings.setString('ekinopremium.client_key', '')
                fflog('authorization key cleared')
                control.infoDialog('wymazano klucz autoryzacyjny od ekino')
                control.idle()
                return
        code = xbmcgui.Dialog().numeric(0, f'Kod ze strony {self.base_link}/kodi')
        if code:
            data = self.api_connect(f'/autorize/{code.strip()}')
            if not isinstance(data, Mapping):
                xbmcgui.Dialog().ok('Błąd', 'Nieznany')
            elif 'error' in data:
                xbmcgui.Dialog().ok('Błąd', data['error'])
                # control.setSetting('ekinopremium.pair_status', '')
            else:
                settings.setString('ekinopremium.pair_status', 'sparowano')
                settings.setString('ekinopremium.client_key', data['apikey'])
                xbmcgui.Dialog().ok('Gotowe!', 'Scraper ekino został sparowany z kontem.')
                control.idle()
                return True
        else:
            fflog('pairing cancelled')
            pass
        control.idle()


# ─── Ekino ───────────────────────────────────────────────────────────────────────

class Ekino(_source):
    """Scraper for Ekino."""
    PROVIDER = 'ekino'


# ─── EkinoPremium ────────────────────────────────────────────────────────────────

class EkinoPremium(_source):
    """Scraper for Ekino premium."""
    PROVIDER = 'ekinopremium'

    has_sort_order = True
    has_color_identify2 = True
    use_premium_color = True

    @classmethod
    def get_api(cls) -> str:
        """Return premium API key depending on settings."""
        mode = settings.getInt('ekinopremium.premium_mode')
        fflog(f'ekinotv: get_api: premium_mode={mode}')
        if mode == 1:
            addon_ok = control.condVisibility('System.AddonIsEnabled(plugin.video.ekino-tv)')
            fflog(f'ekinotv: get_api: mode 1, addon_ok={addon_ok}')
            if addon_ok:
                key = control.addon('plugin.video.ekino-tv').getSetting('client_key')
                fflog(f"ekinotv: get_api: mode 1, key={'(empty)' if not key else '(set)'}")
                return key
        if mode == 2:
            key = settings.getString('ekinopremium.client_key')
            fflog(f"ekinotv: get_api: mode 2, key={'(empty)' if not key else '(set)'}")
            return key if key else ''
        fflog('ekinotv: get_api: no key (mode mismatch or key missing)')
        return ''


def register(sources: List[SourceModule], group: str) -> None:
    """Register all scrapers."""
    from lib.sources import SourceModule
    for cls in (EkinoPremium, Ekino):
        if settings.getBool(f"provider.{cls.PROVIDER}") or const.dev.sources.force_all_sources:
            sources.append(SourceModule(name=cls.PROVIDER, provider=cls(), group=group))
