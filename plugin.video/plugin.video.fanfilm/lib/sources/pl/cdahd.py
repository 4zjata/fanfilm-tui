# -*- coding: utf-8 -*-
"""
FanFilm - źródło: cdahd.cc
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations
from typing import Optional, List, Dict, TYPE_CHECKING, ClassVar
import re
from html import unescape

from lib.ff import requests
from lib.ff import source_utils, control, cache, client, cleantitle
from lib.ff.item import FFItem
from lib.ff.source_utils import setting_cookie, DEFAULT_UA
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
        self.base_link = 'https://cda-hd.cc'
        self.search_link = '/?s=%s'
        self.domains = ['cda-hd.cc']
        self.headers = {
            'User-Agent': DEFAULT_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3', 'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            }
        if UA := settings.getString('cdahd.user_agent').strip(' "\''):
            self.headers.update({'User-Agent': UA})

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        return self._search(title, localtitle, year, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        return self._search(tvshowtitle, localtvshowtitle, year, aliases)

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        return self._find_episode(url, season, episode)

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        if not url:
            return []
        sources = []
        url0 = url
        try:
            cookies = self._get_cookies()
            headers = self.headers.copy()
            headers.update({'Referer': self.base_link}) # Fallback referer

            cookiesD = {}
            if cookies:
                cookiesD = {x[0].strip(): x[1].strip() for x in [c.split('=') for c in cookies.strip('; ').split(';')]}

            result = requests.get(url, cookies=cookiesD, headers=headers)
            if not result:
                fflog(f'request failed: {result=}')
                return
            try:
                result = result.text
            except Exception:
                fflog_exc()
                return sources

            if '/episode/' in url:
                serial = True
                result = client.parseDOM(result, 'div', attrs={'class': 'player2'})
                results_player = client.parseDOM(result, 'div', attrs={'class': 'embed2'})
                results_player = client.parseDOM(results_player, 'div')
                results_player = list(filter(None, results_player))
                results_navi = client.parseDOM(result, 'div', attrs={'class': 'navplayer2'})
                results_navi = client.parseDOM(results_navi, 'a', attrs={'href': ''})
                results_navi = list(filter(None, results_navi))
            else:
                serial = False
                results_player = client.parseDOM(result, 'div', attrs={'id': 'player2'})
                results_player = client.parseDOM(results_player, 'div', attrs={'class': 'movieplay'})
                results_player = list(filter(None, results_player))
                results_navi = []
                player_nav_html = client.parseDOM(result, 'div', attrs={'class': 'player_nav'})
                if player_nav_html:
                    all_a = client.parseDOM(player_nav_html[0], 'a')
                    all_hrefs = client.parseDOM(player_nav_html[0], 'a', ret='href')
                    results_navi = [a for a, href in zip(all_a, all_hrefs) if a and 'div1' not in href]
            if len(results_navi) != len(results_player):
                fflog(f'cannot continue: {len(results_navi)=} != {len(results_player)=}')
                return sources
            try:
                quality = client.parseDOM(result, 'span', attrs={'class': 'calidad2'})[0]
            except IndexError:
                quality = ''

            i = -1
            for item in results_navi:
                try:
                    i += 1
                    lang = item
                    lang = re.sub('<[^>]+>', '', lang)
                    lang, info = source_utils.get_lang_by_type(lang)
                    url = results_player[i]
                    try:
                        url = client.parseDOM(url, 'a', ret='href')[0]
                    except:
                        try:
                            domain = client.parseDOM(url, 'div', ret='domain')[0]
                            encoded_vid = client.parseDOM(url, 'div', ret='id')[0]
                            decoded_vid = self._decode_id(encoded_vid)
                            url = f"https://{domain}/e/{decoded_vid}"
                        except Exception:
                            try:
                                url = client.parseDOM(url, 'iframe', ret='src')[0]
                                url = url.replace('player.cda-hd.co/', 'hqq.to/')
                            except Exception:
                                try:
                                    if 'src="https://player.cda-hd.co/player/hash.php?hash=' not in url:
                                        raise Exception()
                                    hash = re.search(r'hash=(\d+)', url)[1]
                                    url = f"https://hqq.to/e/{hash}"
                                except Exception:
                                    fflog(f"can't find proper url for this source  |  {url=}")
                                    continue
                    # valid, host = source_utils.is_host_valid(url, hostDict)
                    from urllib.parse import urlparse
                    host = urlparse(url).netloc.split(':')[0].rsplit('.', 1)[0]

                    if 'wysoka' in quality.lower() or quality == 'HD':
                        qual = '1080p'
                    elif 'rednia' in quality.lower():
                        qual = 'SD'
                    elif 'niska' in quality.lower() or quality == 'CAM':
                        qual = 'SD'
                        info = f"{info} | CAM"
                    else:
                        qual = 'SD'
                        qual = 'HD' if serial else qual
                    info2 = url0.rstrip('/').rsplit('/')
                    info2 = info2[-1]
                    sources.append({'source': host,
                                    'quality': qual,
                                    'language': lang,
                                    'url': url,
                                    'info': info,
                                    'filename': info2,
                                    'direct': False,
                                    'debridonly': False})
                except:
                    fflog_exc()
                    continue
            fflog(f'sources: {len(sources)}')
            return sources
        except Exception:
            fflog_exc()
            return sources

    def resolve(self, url: str) -> Optional[str]:
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _search(self, title, localtitle, year, aliases=None) -> str | None:
        try:
            if aliases:
                originalname = source_utils.get_original_title(aliases)
                aliases2 = source_utils.prepare_alias_search_list(aliases, year)
            else:
                originalname = ''
                aliases2 = []
            titles = [localtitle, originalname, title]
            titles = list(filter(None, titles))  # remove empty
            # fflog(f'titles before aliases {titles=}')
            titles += aliases2

            # get_simple() strips spaces, colons, hyphens — ideal for deduplication
            def normalize_for_dedup(title):
                if not title:
                    return ''
                return cleantitle.get_simple(title)

            seen = set()
            unique_titles = []
            for t in titles:
                if t:
                    normalized = normalize_for_dedup(t)
                    if normalized not in seen:
                        unique_titles.append(t)
                        seen.add(normalized)
            titles = unique_titles[:4]
            # fflog(f"aliases={aliases2}")
            # fflog(f'{titles=}')

            # Use cleantitle.get_simple for comparisons
            titles_for_compare = [cleantitle.get_simple(t) for t in titles]

            cookies = setting_cookie(setting_name='cdahd.cookies_cf', cookie_name='cf_clearance')

            if cookies:
                cookies = f'cf_clearance={cookies}'
                cache.cache_insert('cdahd_cookies', cookies, control.providercacheFile)
                self.headers.update({'Cookie': cookies})

            # Numbered log entries to track search order
            for idx, title in enumerate(titles):
                try:
                    if not title:
                        fflog(f"[{idx+1}/{len(titles)}] skip (empty title)")
                        continue
                    fflog(f"query: '{title}'")
                    params = {'s': title}
                    url = self.base_link
                    headers = self.headers.copy()
                    headers.update({'Referer': self.base_link})
                    result = requests.get(url, headers=headers, params=params).text
                    if 'rak wynik' in result:
                        fflog(f"[{idx+1}/{len(titles)}] no results for title='{title}' (params={params})")
                        continue
                    elif 'ykryto niezgodność wartości' in result:
                        fflog(f"[{idx+1}/{len(titles)}] value mismatch for title='{title}' (params={params}) {url=}")
                        continue
                    try:
                        result = client.parseDOM(result, 'div', attrs={'class': 'peliculas'})[0]
                        res = client.parseDOM(result, 'div', attrs={'class': 'item_1 items'})[0]
                        rows = client.parseDOM(res, 'div', attrs={'class': 'item'})
                        fflog(f"search: {len(rows)} results")
                    except Exception:
                        if '<title>Just a moment...</title>' in result:
                            fflog(f'[{idx+1}/{len(titles)}] Cloudflare challenge for "{title}" — aborting search')
                            return None  # no point retrying; also avoids caching the result
                        fflog(f'[{idx+1}/{len(titles)}] parse error for "{title}"')
                        fflog_exc()
                        continue

                    for row_idx, row in enumerate(rows):
                        try:
                            rok = client.parseDOM(row, 'span', attrs={'class': 'year'})
                            rok = rok[0] if rok else ''
                            tytul = client.parseDOM(row, 'h2')[0].replace(f" ({rok})", '').rstrip()
                            tytul = unescape(tytul)
                            tytuly = tytul.split(' / ')
                            title1 = tytuly[0]
                            title2 = tytuly[-1]
                            title1_clean = cleantitle.get_simple(title1)
                            title2_clean = cleantitle.get_simple(title2)
                            fflog(f"[{idx+1}/{len(titles)}][{row_idx+1}] checking: '{tytul}' (year: {rok})")
                            fflog(f"[{idx+1}/{len(titles)}][{row_idx+1}] comparing: '{title1_clean}' vs {titles_for_compare}")

                            # match: year + any title variant after normalisation
                            if str(year) in str(rok) and any(
                                t == title1_clean or t == title2_clean
                                for t in titles_for_compare
                            ):
                                url = client.parseDOM(row, 'a', ret='href')[0]
                                fflog(f'[{idx+1}/{len(titles)}] match found: {url=} for "{title}" -> "{tytul}"')
                                return url
                        except Exception:
                            fflog(f'[{idx+1}/{len(titles)}][{row_idx+1}] error processing row')
                            fflog_exc()
                            continue
                    fflog(f"[{idx+1}/{len(titles)}] no match for '{title}'")
                except Exception:
                    fflog(f'[{idx+1}/{len(titles)}] error for "{title}"')
                    fflog_exc()
                    continue

            fflog('all titles searched — no match found')
        except Exception:
            fflog_exc()
            return None

    def _find_episode(self, url, season, episode) -> str | None:
        if not url:
            return None

        cookies = cache.cache_get('cdahd_cookies', control.providercacheFile)
        cookies = cookies.get('value') if cookies else ''
        headers = self.headers.copy()
        headers.update({'Referer': self.base_link}) # Fallback referer
        cookiesD = {}
        if cookies:
             cookiesD = {x[0].strip(): x[1].strip() for x in [c.split('=') for c in cookies.strip('; ').split(';')]}

        result = requests.get(url, cookies=cookiesD, headers=headers)
        if not result:
            fflog(f'{result=}')
            return None

        result = result.text
        seasons = client.parseDOM(result, 'ul', attrs={'class': 'episodios'})
        episodes = client.parseDOM(seasons, 'a', ret='href')

        for episode_url in episodes:
            if f"sezon-{season}-odcinek-{episode}-" in episode_url:
                return episode_url

        fflog('no match')
        return None

    def _get_cookies(self):
        cookies = cache.cache_get('cdahd_cookies', control.providercacheFile)
        return cookies.get('value') if cookies else ''

    def _decode_id(self, id_hex: str) -> str:
        s1 = ''.join(chr(int(id_hex[i:i+2], 16) ^ 0x02) for i in range(0, len(id_hex), 2))
        return ''.join(format(ord(ch) ^ 0x04, 'x') for ch in s1)
