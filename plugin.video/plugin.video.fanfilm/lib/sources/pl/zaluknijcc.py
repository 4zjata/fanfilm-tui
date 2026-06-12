# -*- coding: UTF-8 -*-
"""
FanFilm - źródło: zaluknij.cc
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from requests.compat import urlparse
from urllib.parse import quote_plus
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, ClassVar

from lib.ff import requests
from lib.ff import control, cache
from lib.ff.item import FFItem
from lib.ff.client import parseDOM
from lib.ff.source_utils import setting_cookie, detect_script, DEFAULT_UA, search_queries_extended
from lib.ff.settings import settings
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['pl']

    def __init__(self):
        self.base_link = 'https://zaluknij.cc'
        self.search_link = self.base_link + '/wyszukiwarka?phrase='
        self.headers = {
            'Referer': self.base_link + '/',
            'User-Agent': DEFAULT_UA,
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'accept-language': 'pl,en-US;q=0.7,en;q=0.3', }
        self.session = requests.Session()
        if UA := settings.getString('zaluknij.user_agent').strip(' "\''):
            self.headers.update({'User-Agent': UA})

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        # fflog(f'{title=} {localtitle=} {year=}')
        return self._find(title, localtitle, year, 'movie', aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        # fflog(f'{tvshowtitle=} {localtvshowtitle=} {year=}')
        return self._find(tvshowtitle, localtvshowtitle, year, 'tvshow', aliases)

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        # fflog(f'{url=} {season=} {episode=}')
        return self._find_episode(url, season, episode)

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        # fflog(f'{url=}')
        sources = []

        if not url:
            fflog('no sources')
            return sources

        filename = ''
        if isinstance(url, tuple):
            filename = url[1]
            url = url[0]

        out = []
        # fflog(f'{url=}')
        # control.sleep(500)
        cf_cookie = setting_cookie(setting_name='zaluknij.cookies_cf', cookie_name='cf_clearance')
        cookiesD = {'cf_clearance': cf_cookie} if cf_cookie else {}
        headers = self.headers.copy()
        headers.update({'Referer': self.base_link})
        html = self.session.get(url, headers=headers, cookies=cookiesD, timeout=60, verify=False).text
        result = parseDOM(html, 'tbody')
        if result:
            result = result[0]
            videos = parseDOM(result, 'tr')
            for vid in videos:
                hosthrefquallang = re.findall(r'href\s*=\s*"([^"]+).*?<td>([^<]+).*?<td>([^<]+)', vid, re.DOTALL)
                for href, lang, qual in hosthrefquallang:
                    host = urlparse(href).netloc
                    out.append({'href': href, 'host': host, 'lang': lang, 'qual': qual})
            if out:
                for x in out:
                    if (link := x.get('href')):
                        host = (x.get('host') or '').rsplit('.', 1)[0].rsplit('.', 1)[-1]
                        lang = x.get('lang')
                        qual = x.get('qual').lower()
                        # fflog(f'{link=}')
                        sources.append({'source': host,
                                        'quality': '720p' if qual == 'wysoka' else 'CAM' if qual == 'niska' else 'SD',
                                        'language': 'pl',
                                        'url': link,
                                        'info': lang,
                                        'direct': False,
                                        'debridonly': False,
                                        'filename': filename,
                                        'premium': False,
                                        })
        fflog(f'sources: {len(sources)}')
        return sources

    def resolve(self, url: str) -> Optional[str]:
        link = str(url).replace('\\/', '/')
        link = link.replace('//', '/').replace(':/', '://')
        fflog(f'{link=}')
        return link

    # ── helpers ────────────────────────────────────────────────────────────

    def _find(self, title, localtitle, year, type_, aliases=None):
        try:
            if not title:
                return

            # usually only Polish title is available, so swap order
            title, localtitle = localtitle, title

            title = title.lower()
            localtitle = localtitle.lower()

            if title == localtitle and aliases:
                originalname = [a for a in aliases if 'originalname' in a]
                originalname = originalname[0]['originalname'] if originalname else ''
                originalname = '' if detect_script(originalname) else originalname
                if originalname:
                    title = originalname.lower()

            first = True
            for query in search_queries_extended(title, localtitle):
                if not first:
                    control.sleep(500)
                first = False
                if url := self._search(query, year, type_):
                    return url

        except Exception:
            # log_exception(1)
            fflog_exc()
            pass

    def _find_episode(self, url, season, episode):
        # fflog(f'{url=} {season=} {episode=}')
        if not url:
            return

        cookies = cache.cache_get('zaluknij_cookies', control.providercacheFile)
        cookies = cookies.get('value') if cookies else ''
        headers = self.headers.copy()
        headers.update({'Referer': self.base_link})
        cookiesD = {}
        if cookies:
            cookiesD = {x[0].strip(): x[1].strip() for x in [c.split('=') for c in cookies.strip('; ').split(';')]}

        html = self.session.get(url, headers=headers, cookies=cookiesD, timeout=60, verify=False)

        if html.status_code != 200:
            fflog(f'unexpected status code: {html.status_code}')
            if html.status_code == 403:
                fflog('likely Cloudflare challenge active')
                # fflog(f'{req.text=}')
                return

        html = html.text

        sesres = parseDOM(html, 'ul', attrs={'id': 'episode-list'})
        if sesres:
            sesres = sesres[0]
        else:
            return

        sezony = re.findall(r'(<span>.*?</ul>)', sesres, re.DOTALL)

        episode_url = ''
        for sezon in sezony:
            sesx = parseDOM(sezon, 'span')
            ses = ''
            if sesx:
                mch = re.search(r'(\d+)', sesx[0], re.DOTALL)
                ses = mch[1] if mch else '0'
            eps = parseDOM(sezon, 'li')
            for ep in eps:
                href = parseDOM(ep, 'a', ret='href')[0]
                tyt2 = parseDOM(ep, 'a')[0]
                epis = re.findall(r's\d+e(\d+)', tyt2)[0]
                if int(ses) == int(season) and int(epis) == int(episode):
                    episode_url = href
                    break
        # fflog(f'{episode_url=}')
        return episode_url

    def _search(self, title, year, type_):
        fflog(f"query: {title!r} {type_=}")

        if not title:
            return

        fout = []
        sout = []
        results = []
        out_url = ''
        original_title = title

        # Normalise whitespace
        title = re.sub(r'\s+', ' ', title).strip()

        # Strip leading franchise prefix only: "gwiezdne wojny:" / "star wars:" (for search query)
        prefix_patterns = [
            r'(?i)^\s*gwiezdne\s+wojny(?:\s*[:\-–—])?\s*',
            r'(?i)^\s*star\s+wars(?:\s*[:\-–—])?\s*',
        ]
        for p in prefix_patterns:
            if re.search(p, title, flags=re.IGNORECASE):
                title = re.sub(p, '', title, count=1, flags=re.IGNORECASE)
                break

        # Clean up remaining leading punctuation
        title = re.sub(r'^[\s:–—-]+', '', title).strip()
        if not title:
            title = original_title

        search_url = f'{self.search_link}{quote_plus(title)}'

        cookies = setting_cookie(setting_name='zaluknij.cookies_cf', cookie_name='cf_clearance')

        cookiesD = {}
        if cookies:
            cookiesD = {'cf_clearance': cookies}
            cache.cache_insert('zaluknij_cookies', f'cf_clearance={cookies}', control.providercacheFile)

        headers = self.headers.copy()
        headers.update({'Referer': self.base_link + '/'})
        req = self.session.get(search_url, headers=headers, cookies=cookiesD, timeout=60, verify=False)
        if req.status_code != 200:
            fflog(f'unexpected status code: {req.status_code}')
            if req.status_code == 403:
                fflog('likely Cloudflare challenge active')
            return

        html = req.text

        # All found result blocks
        links_blocks = []

        # --- old layout ---
        old_layout = parseDOM(html, 'div', attrs={'id': 'advanced-search'})
        if old_layout:
            inner = old_layout[0]
            cols = parseDOM(inner, 'div', attrs={'class': r'col-sm-\d+'})
            links_blocks.extend(cols)

        # --- new layout ---
        new_layout = parseDOM(html, 'div', attrs={'class': r'item\s+col-sm-\d+'})
        links_blocks.extend(new_layout)

        fflog(f'search: {len(links_blocks)} results')
        if not links_blocks:
            if '<body' not in html:
                fflog(f'{html=}', 1)
            else:
                fflog('an error occurred')
            return

        # Helper to strip franchise prefixes before comparison
        def strip_prefix(s):
            return re.sub(r'(?i)^\s*(gwiezdne\s+wojny|star\s+wars)\s*[:\-–—]?\s*', '', s).strip()

        # --- filter by title and type ---
        search_key = strip_prefix(original_title.lower())
        for link in links_blocks:
            if 'href' in link:
                href = parseDOM(link, 'a', ret='href')[0]
                tytul = parseDOM(link, 'div', attrs={'class': 'title'}) or parseDOM(link, 'a')
                if not tytul:
                    continue
                tytul = tytul[0]

                # Match ignoring franchise prefixes on both sides
                if search_key in strip_prefix(tytul.lower()):
                    if 'serial-online' in href or 'seasons' in href:
                        sout.append({'title': tytul, 'url': href})
                    else:
                        fout.append({'title': tytul, 'url': href})

        # select results by type
        results = fout if type_ == 'movie' else sout
        results.sort(key=lambda k: len(k['title']), reverse=True)

        # --- filter by year ---
        if year:
            year = int(year)
            years = [year]
            if type_ == 'movie':
                years += [year - 1, year + 1]
            if type_ == 'tvshow':
                years += [year + 1]

        date = '0'
        fallback_url = ''  # first title match whose year we couldn't read — used only if no exact-year match exists
        for url in results:
            if type_ == 'movie':
                date = str(url['url'])[-4:]
            elif type_ == 'tvshow':
                html = self.session.get(url['url'], headers=self.headers, timeout=60, verify=False).text
                try:
                    date_info = parseDOM(html, 'div', attrs={'class': 'info'})
                    date = (parseDOM(date_info, 'li')[-1:])[0]
                except IndexError:
                    fflog_exc()
                    date = '0'

            if not year:
                out_url = url['url']
                break
            if date.isnumeric() and int(date):
                if int(date) in years:
                    out_url = url['url']
                    break
                # readable year that does not match — skip this candidate
            elif not fallback_url:
                fallback_url = url['url']

        # prefer the exact-year match; fall back to a title match with an unverifiable year
        return out_url or fallback_url