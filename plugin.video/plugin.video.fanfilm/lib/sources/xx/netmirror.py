# -*- coding: utf-8 -*-
"""
FanFilm – source: netmirror (net52.cc)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Premium cookies (t_hash_t + user_token) come from settings, populated by
the Tampermonkey script (web/mod/fanfilm.user.js) → /cookies web_server
endpoint. Falls back to runtime bypass via /verify.php (like the JS
provider in All-in-One-Nuvio). No cookies → no sources. Without `_p` flag
the CDN serves an "Only Valid Users Allowed" placeholder.

net52.cc is the premium playback endpoint. Landing redirectors
(netmirror.gg, net11.cc, etc.) are *separate* backends that don't honor
the user's premium session — they hand out masters with empty CDN host
(https:///files/...) that fail DNS. Always go straight to net52.cc.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from html import unescape
from typing import TYPE_CHECKING, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urljoin

from const import const
from lib.ff import cleantitle, control, requests, source_utils
from lib.ff.item import FFItem
from lib.ff.resolve_utils import build_isa_url
from lib.ff.settings import settings
from lib.ff.source_utils import CHROME_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.service.client import service_client

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias


_BASE = 'https://net52.cc'

# Multi-platform OTT endpoints - każda platforma ma inny path + ott cookie
_OT_PLATFORMS = [
    ('nf', '',       'nf'),     # (ott_value, subpath, platform_label)
    ('pv', 'pv/',    'pv'),
    ('hs', 'hs/',    'hs'),
]

_RES_QUALITY = [
    ('1920x1080', '1080p'),
    ('1280x720',  '720p'),
    ('854x480',   'SD'),
]
_QUALITY_TO_RES = {quality: resolution for resolution, quality in _RES_QUALITY}

_master_cache: 'Dict[str, Tuple[float, str, str, List[dict]]]' = {}
_MASTER_TTL = 300

_bypass_cache: 'Dict[str, object]' = {}
_BYPASS_TTL = 54000  # 15h, same as JS COOKIE_EXPIRY

_AUDIO_NOISE = frozenset({'', 'und'})

# Subtitle CDN file pattern: //subscdn.top/files/<id>/<id>-<lang>.[CC].srt
_CAPTION_LANG_RE = re.compile(r'-([a-zA-Z]{2,3})(?:-[a-zA-Z]+)?(?:\(\d+\))?(?:\.\[[^]]+\])?\.(?:srt|vtt)$')


class source:
    ffitem: FFItem
    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.domains = [_BASE.split('://', 1)[-1]]
        self.debug = const.sources.netmirror.debug

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        try:
            titles = [title, localtitle] + [a.get('title') for a in (aliases or []) if a.get('title')]
            content_id = self._search(list(dict.fromkeys(filter(None, titles))))
            return f'nm:{content_id}' if content_id else None
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        try:
            titles = [tvshowtitle, localtvshowtitle] + [a.get('title') for a in (aliases or []) if a.get('title')]
            content_id = self._search(list(dict.fromkeys(filter(None, titles))))
            return f'nm:{content_id}' if content_id else None
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if not url or not url.startswith('nm:'):
                return url
            show_id = url.split(':', 1)[1]
            cookies = self._cookies()
            ua = self._user_agent()

            # NetMirror uses English titles for episodes. Use English/Original from vtag.
            titles = [t for t in (self.ffitem.vtag.getEnglishTitle(), self.ffitem.vtag.getOriginalTitle()) if t] or [title]

            episode_id = self._find_episode(show_id, season, episode, cookies, ua, titles)
            if episode_id:
                if self.debug:
                    fflog(f'S{season}E{episode} → id={episode_id}')
                return f'nm:{episode_id}'
        except Exception:
            fflog_exc()
        return url

    def _find_episode(self, show_id: str, season: str, episode: str, cookies: Optional[dict], ua: str, titles: List[str]) -> Optional[str]:
        """Find episode ID for S{season}E{episode} of show {show_id}."""
        if not cookies:
            return None

        target_s, target_e = f'S{season}', f'E{episode}'
        clean_targets = {cleantitle.get(t) for t in titles if t}
        clean_targets.discard(None)

        # Netflix mapping – single-request fast path using seasid
        netflix_map = source_utils.get_netflix_ep_id(show_id, season, episode, titles) if show_id.isdigit() else None
        if netflix_map:
            netflix_epid, netflix_seasid = netflix_map[0], netflix_map[1]
            fflog(f'netflix mapping: {target_s}{target_e} -> epid={netflix_epid} seasid={netflix_seasid}')
            try:
                resp = requests.get(
                    f'{_BASE}/mobile/episodes.php',
                    params={'s': netflix_seasid, 'series': show_id, 'page': 1},
                    cookies={**cookies, 'ott': 'nf'},
                    headers={'User-Agent': ua, 'Referer': f'{_BASE}/mobile/', 'Accept': 'application/json, text/plain, */*', 'X-Requested-With': 'XMLHttpRequest'},
                    timeout=10,
                )
                if resp and resp.status_code == 200:
                    data = resp.json()
                    for ep in data.get('episodes') or []:
                        if str(ep.get('id')) == netflix_epid:
                            if self.debug:
                                fflog(f"MATCH BY NETFLIX ID (fast): {netflix_epid}")
                            return netflix_epid
            except Exception:
                fflog_exc()

        # Try multi-platform loop (nf → pv → hs) with ott cookie
        for ott, subpath, label in _OT_PLATFORMS:
            try:
                # For Netflix content, we can try to match by epid if we have the mapping
                is_nf = ott == 'nf'

                resp = requests.get(
                    f'{_BASE}/mobile/{subpath}post.php',
                    params={'id': show_id, 'tm': int(time.time() * 1000)},
                    cookies={**cookies, 'ott': ott},
                    headers={'User-Agent': ua, 'X-Requested-With': 'XMLHttpRequest', 'Referer': f'{_BASE}/mobile/{subpath}'},
                    timeout=10,
                )
                if not resp or resp.status_code != 200:
                    continue
                data = resp.json()
                if data.get('status') != 'y':
                    continue

                seasons_list = data.get('season') or []
                if not seasons_list:
                    continue

                # Priority 1: Requested season. Priority 2: All other seasons
                ordered_seasons = sorted(seasons_list, key=lambda s: 0 if str(s.get('s')) == str(season) else 1)
                fallback_id = None

                for s_info in ordered_seasons:
                    s_id = s_info.get('id')
                    if not s_id:
                        continue

                    page = 1
                    while True:
                        resp = requests.get(
                            f'{_BASE}/mobile/{subpath}episodes.php',
                            params={'s': s_id, 'series': show_id, 'page': page},
                            cookies={**cookies, 'ott': ott},
                            headers={'User-Agent': ua, 'Referer': f'{_BASE}/mobile/{subpath}', 'Accept': 'application/json, text/plain, */*', 'X-Requested-With': 'XMLHttpRequest'},
                            timeout=10,
                        )
                        if not resp or resp.status_code != 200:
                            break
                        data = resp.json()
                        episodes = data.get('episodes') or []

                        for ep in episodes:
                            ep_id_str = str(ep.get('id'))
                            # Priority 1: Exact Netflix epid match
                            if is_nf and netflix_map and ep_id_str == netflix_map[0]:
                                if self.debug:
                                    fflog(f"MATCH BY NETFLIX ID: {ep_id_str} ({label})")
                                return ep_id_str

                            # Priority 2: Title match
                            ep_t = ep.get('t') or ''
                            if clean_targets and cleantitle.get(ep_t) in clean_targets:
                                if self.debug:
                                    fflog(f"MATCH BY TITLE: {ep_t!r} → id={ep_id_str} ({label}, s_id={s_id})")
                                return ep_id_str

                            # Priority 3: S/E number match (only for the requested season)
                            if not fallback_id and str(s_info.get('s')) == str(season):
                                if ep.get('s') == target_s and ep.get('ep') == target_e:
                                    fallback_id = ep_id_str

                        if not data.get('nextPageShow') or page > 5:
                            break
                        page += 1

                if fallback_id:
                    if self.debug:
                        fflog(f"MATCH BY NUMBER (fallback): {target_s}{target_e} → id={fallback_id} ({label})")
                    return fallback_id
            except Exception:
                fflog_exc()
                continue

        # Fallback: /pv/ paths (premium, no ott cookie)
        try:
            resp = requests.get(
                f'{_BASE}/pv/post.php',
                params={'id': show_id, 'tm': int(time.time() * 1000)},
                cookies=cookies,
                headers={'User-Agent': ua, 'X-Requested-With': 'XMLHttpRequest', 'Referer': f'{_BASE}/pv/'},
                timeout=10,
            )
            if resp and resp.status_code == 200:
                data = resp.json()
                if data.get('status') == 'y':
                    seasons_list = data.get('season') or []
                    ordered_seasons = sorted(seasons_list, key=lambda s: 0 if str(s.get('s')) == str(season) else 1)
                    fallback_id = None

                    for s_info in ordered_seasons:
                        s_id = s_info.get('id')
                        if not s_id:
                            continue

                        page = 1
                        while True:
                            resp = requests.get(
                                f'{_BASE}/pv/episodes.php',
                                params={'s': s_id, 'series': show_id, 'page': page},
                                cookies=cookies,
                                headers={'User-Agent': ua, 'Referer': f'{_BASE}/pv/', 'Accept': 'application/json, text/plain, */*', 'X-Requested-With': 'XMLHttpRequest'},
                                timeout=10,
                            )
                            if not resp or resp.status_code != 200:
                                break
                            data = resp.json()
                            episodes = data.get('episodes') or []

                            for ep in episodes:
                                ep_t = ep.get('t') or ''
                                if clean_targets and cleantitle.get(ep_t) in clean_targets:
                                    if self.debug:
                                        fflog(f"MATCH BY TITLE: {ep_t!r} → id={ep.get('id')} (pv-fallback, s_id={s_id})")
                                    return ep.get('id')
                                if not fallback_id and str(s_info.get('s')) == str(season):
                                    if ep.get('s') == target_s and ep.get('ep') == target_e:
                                        fallback_id = ep.get('id')

                            if not data.get('nextPageShow') or page > 5:
                                break
                            page += 1

                    if fallback_id:
                        if self.debug:
                            fflog(f"MATCH BY NUMBER (fallback): {target_s}{target_e} → id={fallback_id} (pv-fallback)")
                        return fallback_id
        except Exception:
            fflog_exc()
        return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        results: List[dict] = []
        try:
            if not url or not url.startswith('nm:'):
                return results
            content_id = url.split(':', 1)[1].split('?', 1)[0]
            if not content_id:
                return results

            cookies = self._cookies()
            ua = self._user_agent()
            master_text = self._cached_master(content_id, cookies, ua)
            if not master_text:
                if self.debug:
                    fflog('master unavailable, falling back to fixed quality list')
                audios = set()
                resolutions = {res for res, _ in _RES_QUALITY}
            else:
                audios = self._audio_langs(master_text)
                resolutions = self._video_resolutions(master_text)
                if self.debug:
                    fflog(f'audio={sorted(audios)} resolutions={sorted(resolutions)}')

            captions = self._cached_captions(content_id, cookies)
            language, info = self._classify_audio(audios, captions)

            for resolution, quality in _RES_QUALITY:
                if resolution not in resolutions:
                    continue
                results.append({
                    'source':     'netmirror',
                    'quality':    quality,
                    'language':   language,
                    'url':        f'nm:{content_id}?q={quality}',
                    'info':       info,
                    'filename':   str(content_id),
                    'direct':     False,
                    'debridonly': False,
                })
        except Exception:
            fflog_exc()
        fflog(f'sources: {len(results)}')
        return results

    @staticmethod
    def _audio_langs(master_text: str) -> 'set[str]':
        # 1. Try LANGUAGE attribute
        langs = re.findall(r'#EXT-X-MEDIA:[^\n]*TYPE=AUDIO[^\n]*LANGUAGE="([^"]*)"', master_text)
        # 2. Try NAME attribute
        names = re.findall(r'#EXT-X-MEDIA:[^\n]*TYPE=AUDIO[^\n]*NAME="([^"]*)"', master_text)
        # 3. Extract from URI path as fallback (e.g. /a/pol/0.m3u8)
        uris = re.findall(r'#EXT-X-MEDIA:[^\n]*TYPE=AUDIO[^\n]*URI="([^"]*)"', master_text)
        path_langs = []
        for uri in uris:
            m = re.search(r'/a/([^/]+)/', uri)
            if m:
                path_langs.append(m.group(1))

        return {l.lower() for l in (langs + names + path_langs) if l}

    @staticmethod
    def _video_resolutions(master_text: str) -> 'set[str]':
        return set(re.findall(r'#EXT-X-STREAM-INF[^\n]*RESOLUTION=(\d+x\d+)', master_text))

    @staticmethod
    def _classify_audio(audios: 'set[str]', captions: 'Optional[List[dict]]' = None) -> 'tuple[str, str]':
        meaningful = audios - _AUDIO_NOISE
        if not meaningful:
            language, info_parts = 'en', []
        else:
            # Common variants in attributes or URI paths
            has_pol = any(x in meaningful for x in ('pol', 'pl', 'polski', 'polish', 'p'))
            has_eng = any(x in meaningful for x in ('eng', 'en', 'english', 'e'))

            if has_pol:
                language = 'pl'
                info_parts = ['LEKTOR']
                if len(meaningful) > 1:
                    info_parts.append('MULTI')
            elif has_eng:
                language = 'en'
                info_parts = ['MULTI'] if len(meaningful) > 1 else []
            else:
                language = 'en'
                info_parts = ['MULTI']
        if captions:
            info_parts.append('NAPISY')
        return (language, ' '.join(info_parts))

    def resolve(self, url: str) -> Optional[str]:
        try:
            if not url or not url.startswith('nm:'):
                return None
            body = url[3:]
            if '?' in body:
                content_id, query = body.split('?', 1)
                quality = parse_qs(query).get('q', ['1080p'])[0]
            else:
                content_id, quality = body, '1080p'
            target_res = _QUALITY_TO_RES.get(quality, '1920x1080')

            cookies = self._cookies()
            if not cookies:
                fflog('no premium cookies (settings empty, bypass failed)')
                return None

            ua = self._user_agent()
            master_text = self._cached_master(content_id, cookies, ua)
            cache_key = f'{content_id}:{self._cookies_fingerprint(cookies)}'
            cached = _master_cache.get(cache_key)
            master_url = cached[1] if cached else None
            if not master_url:
                if self.debug:
                    fflog(f'no signed master URL for id={content_id}')
                return None

            captions = self._cached_captions(content_id, cookies)
            if captions:
                try:
                    subtitle_urls = [caption['file'] for caption in captions if caption.get('file')]
                    control.window().setProperty('source.subtitles', json.dumps(subtitle_urls))
                    if self.debug:
                        fflog(f'{len(subtitle_urls)} subtitle(s) passed to player')
                except Exception:
                    fflog_exc()

            proxy_url = self._build_proxy(master_url, target_res, ua, master_text, cookies)
            if proxy_url:
                if self.debug:
                    fflog(f'{quality} → proxy {proxy_url}')
                return build_isa_url(proxy_url, _BASE + '/', ua=ua)

            if self.debug:
                fflog(f'{quality} → direct master (proxy fail)')
            return build_isa_url(master_url, _BASE + '/', ua=ua)
        except Exception:
            fflog_exc()
            return None

    # ── premium cookies (settings → bypass fallback) ────────────────────────

    def _cookies(self) -> Optional[dict]:
        cookies = self._cookies_from_settings()
        if cookies and 't_hash_t' in cookies:
            return cookies
        now = time.time()
        t_hash_t = _bypass_cache.get('t_hash_t')
        ts = _bypass_cache.get('ts', 0.0)
        if not t_hash_t or (now - ts) > _BYPASS_TTL:
            t_hash_t = self._verify_canary(self.debug)
            if t_hash_t:
                _bypass_cache['t_hash_t'] = t_hash_t
                _bypass_cache['ts'] = now
        if not t_hash_t:
            return cookies
        if cookies:
            cookies['t_hash_t'] = t_hash_t
            if self.debug:
                fflog('supplemented t_hash_t via bypass')
            return cookies
        if self.debug:
            fflog('using bypassed t_hash_t cookie')
        return {'t_hash_t': t_hash_t, 'hd': 'on'}

    @staticmethod
    def _verify_canary(debug: bool = False) -> Optional[str]:
        try:
            ua = settings.getString('netmirror.user_agent').strip(' "\'')
            resp = requests.post(
                f'{_BASE}/verify.php',
                data={'g-recaptcha-response': str(uuid.uuid4())},
                headers={
                    'User-Agent': ua,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': _BASE,
                    'Referer': _BASE + '/verify2',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                allow_redirects=False,
                timeout=10,
            )
            if resp and 'set-cookie' in resp.headers:
                m = re.search(r't_hash_t=([^;]+)', resp.headers['set-cookie'])
                if m:
                    value = m.group(1)
                    if debug:
                        fflog('verify.php returned t_hash_t')
                    return value
            sc = getattr(resp, 'status_code', None)
            if debug:
                fflog(f'verify.php unreachable (status={sc})')
        except Exception:
            if debug:
                fflog('verify.php exception - site may be down')
        return None

    @staticmethod
    def _user_agent() -> str:
        return settings.getString('netmirror.user_agent').strip(' "\'')

    @staticmethod
    def _cookies_from_settings() -> Optional[dict]:
        t_hash_p = settings.getString('netmirror.cookies_t_hash_p').strip(' "\'')
        t_hash_t = settings.getString('netmirror.cookies_t_hash_t').strip(' "\'')
        t_hash = settings.getString('netmirror.cookies_t_hash').strip(' "\'')
        user_token = settings.getString('netmirror.cookies_user_token').strip(' "\'')

        if not (user_token and (t_hash_p or t_hash_t or t_hash)):
            return None

        cookies = {'user_token': unquote(user_token)}

        if t_hash_p:
            cookies['t_hash_p'] = unquote(t_hash_p)
        if t_hash_t:
            cookies['t_hash_t'] = unquote(t_hash_t)
        if t_hash:
            cookies['t_hash'] = unquote(t_hash)

        return cookies

    @staticmethod
    def _cookies_fingerprint(cookies: Optional[dict]) -> str:
        # Scopes _master_cache to current cookies — pasting new cookies
        # changes the fingerprint and forces a fresh master fetch.
        if not cookies:
            return 'none'
        material = ';'.join(f'{name}={value}' for name, value in sorted(cookies.items()))
        return hashlib.md5(material.encode()).hexdigest()[:8]

    # ── search ─────────────────────────────────────────────────────────────

    def _search(self, titles: List[str]) -> Optional[str]:
        if not titles:
            return None

        all_queries = list(titles)
        fflog(f'queries: {all_queries!r}')
        ua = settings.getString('netmirror.user_agent').strip(' "\'')
        cookies = self._cookies()

        clean_targets = {cleantitle.get(t) for t in all_queries if t}
        clean_targets.discard(None)

        if cookies:
            for ott, subpath, _label in _OT_PLATFORMS:
                for query in all_queries:
                    try:
                        resp = requests.get(
                            f'{_BASE}/mobile/{subpath}search.php',
                            params={'s': query, 't': int(time.time() * 1000)},
                            headers={
                                'User-Agent': ua,
                                'Accept': 'application/json, text/plain, */*',
                                'Referer': f'{_BASE}/mobile/{subpath}',
                                'X-Requested-With': 'XMLHttpRequest',
                            },
                            cookies={**cookies, 'ott': ott},
                            timeout=10,
                        )
                        if not resp or resp.status_code != 200:
                            continue
                        data = resp.json()
                        if data.get('searchResult'):
                            for item in data['searchResult']:
                                if cleantitle.get(unescape(item.get('t') or '')) in clean_targets:
                                    if self.debug:
                                        fflog(f"matched {item['t']!r} → id={item['id']} ({_label})")
                                    return item.get('id')
                    except Exception:
                        fflog_exc()
                        continue

        # Fallback: /search.php
        for query in all_queries:
            try:
                resp = requests.get(
                    f'{_BASE}/search.php',
                    params={'s': query, 't': int(time.time() * 1000)},
                    headers={
                        'User-Agent': ua,
                        'Accept': 'application/json, text/plain, */*',
                        'Referer': _BASE + '/',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    timeout=10,
                )
                if resp and resp.status_code == 200:
                    results = resp.json().get('searchResult') or []
                    for item in results:
                        if cleantitle.get(unescape(item.get('t') or '')) in clean_targets:
                            if self.debug:
                                fflog(f"matched {item.get('t')!r} → id={item.get('id')}")
                            return item.get('id')
                    if results and query == all_queries[0]:
                        return results[0].get('id')
            except Exception:
                fflog_exc()

        if self.debug:
            fflog(f'no search hits for {titles[0]!r}')
        return None

    # ── master cache (sources → resolve handoff) ───────────────────────────

    def _cached_master(self, content_id: str, cookies: Optional[dict], ua: str) -> Optional[str]:
        now = time.time()
        key = f'{content_id}:{self._cookies_fingerprint(cookies)}'
        cached = _master_cache.get(key)
        if cached and (now - cached[0]) < _MASTER_TTL:
            return cached[2]
        if not cookies:
            return None
        urls = self._signed_master_url(content_id, cookies, ua)
        if not urls:
            return None
        master_url, fallback_url = urls
        master_text = None
        working_url = None
        for url in (master_url, fallback_url):
            try:
                resp = requests.get(
                    url,
                    cookies=cookies,
                    headers={
                        'User-Agent': ua,
                        'Referer': f'{_BASE}/play.php?id={content_id}',
                        'Accept': '*/*',
                        'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
                        'Connection': 'keep-alive',
                    },
                    timeout=12,
                )
                if resp and resp.status_code == 200 and resp.text.startswith('#EXTM3U'):
                    master_text = resp.text
                    working_url = url
                    break
                if self.debug:
                    fflog(f'master fetch failed for {url[:100]}: status {getattr(resp,"status_code",None)}')
            except Exception:
                fflog_exc()
        if not master_text:
            return None
        captions = self._captions_from_mobile(content_id, ua)
        _master_cache[key] = (now, working_url, master_text, captions)
        return master_text

    def _cached_captions(self, content_id: str, cookies: Optional[dict]) -> List[dict]:
        """Read captions from the master cache (populated by _cached_master)."""
        key = f'{content_id}:{self._cookies_fingerprint(cookies)}'
        cached = _master_cache.get(key)
        return cached[3] if cached and len(cached) >= 4 else []

    # ── premium master URL via /pv/playlist.php ────────────────────────────

    def _signed_master_url(self, content_id: str, cookies: dict, ua: str) -> Optional[Tuple[str, str]]:
        # Returns (primary_url, fallback_url) tuple.
        # primary: /hls/ path (full master with all audio+resolutions for NF content)
        # fallback: original /pv/hls/ path (needed for PV content).
        try:
            params = {'id': content_id, 'tm': int(time.time() * 1000)}
            if self.debug:
                fflog(f'fetching pv/playlist.php id={content_id}')
            resp = requests.get(
                f'{_BASE}/pv/playlist.php',
                params=params,
                cookies=cookies,
                headers={
                    'User-Agent': ua,
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': f'{_BASE}/pv/',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                timeout=12,
            )
            if not resp or resp.status_code != 200:
                if self.debug:
                    fflog(f'pv/playlist.php status {getattr(resp,"status_code",None)}')
                return None

            try:
                data = resp.json()
            except ValueError:
                return None
            if not isinstance(data, list) or not data:
                return None
            sources_list = data[0].get('sources') or []
            if not sources_list:
                return None
            file_url = sources_list[0].get('file', '')
            if not file_url:
                return None

            if file_url.startswith('/'):
                file_url = _BASE + file_url

            # Primary: /hls/ path (full master NF), Fallback: /pv/hls/ path (PV)
            primary = file_url.replace('/pv/hls/', '/hls/').replace('/mobile/hls/', '/hls/')
            if '::p::' not in primary:
                if self.debug:
                    fflog(f'master URL missing premium flag (::p::): {primary[:100]}')
            return (primary, file_url)

        except Exception:
            fflog_exc()
            return None

    def _captions_from_mobile(self, content_id: str, ua: str) -> List[dict]:
        for ott, subpath, _label in [(None, '', 'no-ott')] + _OT_PLATFORMS:
            try:
                kwargs = {
                    'headers': {
                        'User-Agent': ua,
                        'Accept': 'application/json, text/plain, */*',
                        'Referer': f'{_BASE}/mobile/{subpath}',
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    'timeout': 10,
                }
                if ott:
                    kwargs['cookies'] = {'ott': ott}
                resp = requests.get(
                    f'{_BASE}/mobile/{subpath}playlist.php',
                    params={'id': content_id, 'tm': int(time.time() * 1000)},
                    **kwargs,
                )
                if not resp or resp.status_code != 200:
                    continue
                data = resp.json()
                if not isinstance(data, list) or not data:
                    continue
                tracks = data[0].get('tracks') or []
            except Exception:
                continue

            captions: List[dict] = []
            for track in tracks:
                if track.get('kind') != 'captions':
                    continue
                file_url = track.get('file', '')
                if not file_url:
                    continue
                match = _CAPTION_LANG_RE.search(file_url)
                if not match:
                    continue
                if file_url.startswith('//'):
                    file_url = 'https:' + file_url
                captions.append({'file': file_url, 'label': track.get('label', match.group(1).lower()), 'code': match.group(1).lower()})
            if captions:
                if self.debug:
                    fflog(f'captions for {content_id}: {[c["label"] for c in captions]} ({_label})')
                return captions
        return []

    # ── HLS proxy ──────────────────────────────────────────────────────────

    def _build_proxy(self, master_url: str, target_res: str, ua: str,
                     master_text: Optional[str] = None,
                     cookies: Optional[dict] = None) -> Optional[str]:
        # Prune master to selected video + filtered audio tracks, fix each
        # sub-playlist (abs URLs, #EXT-X-PLAYLIST-TYPE:VOD, #EXT-X-ENDLIST),
        # register the rewritten master+subs in service_client at /media/.
        try:
            text = master_text
            if not text:
                resp = requests.get(
                    master_url,
                    cookies=cookies,
                    headers={'User-Agent': ua, 'Referer': _BASE + '/'},
                    timeout=12,
                )
                if not resp or resp.status_code != 200:
                    if self.debug:
                        fflog(f'master fetch status {getattr(resp,"status_code",None)}')
                    return None
                text = resp.text
            if not text.startswith('#EXTM3U'):
                if self.debug:
                    fflog(f'master not m3u8, body[:120]={text[:120]!r}')
                return None

            proxy_map: Dict[str, str] = {}
            proxy_base = urljoin(service_client.url, '/media')

            # Netmirror sometimes returns audio URIs with missing host (https:///files/...).
            # Extract host from video variants to fix them.
            cdn_host = None
            host_m = re.search(r'https?://([^/]+)/files/', text)
            if host_m:
                cdn_host = host_m.group(1)

            # Dynamic host discovery: Extract from first video stream
            video_cdn_host = None
            for line in text.splitlines():
                if line.startswith('http') and '/files/' in line:
                    host_m = re.search(r'https?://([^/]+)/files/', line)
                    if host_m:
                        video_cdn_host = host_m.group(1)
                        break

            if self.debug:
                fflog(f'resolved CDN host from playlist: {video_cdn_host}')

            new_lines: List[str] = []
            kept_variant = False
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.startswith('#EXT-X-MEDIA') and 'TYPE=AUDIO' in line:
                    uri_m = re.search(r'URI="([^"]+)"', line)
                    if uri_m:
                        uri = uri_m.group(1)
                        # Fix malformed URI by prepending the discovered CDN host
                        if uri.startswith('https:///'):
                            path = uri.replace('https:///', '/')
                            if video_cdn_host:
                                uri = f'https://{video_cdn_host}{path}'
                            if self.debug:
                                fflog(f'patched audio URI: {uri}')

                        proxied = self._proxy_sub(uri, proxy_map, ua, video_cdn_host)
                        if proxied:
                            line = line.replace(f'URI="{uri_m.group(1)}"', f'URI="{proxy_base}{proxied}"')
                            new_lines.append(line)
                    i += 1
                    continue

                if line.startswith('#EXT-X-STREAM-INF'):
                    res_m = re.search(r'RESOLUTION=(\d+x\d+)', line)
                    is_target = res_m and res_m.group(1) == target_res
                    nxt = lines[i + 1] if i + 1 < len(lines) else ''
                    if is_target and nxt.startswith('http'):
                        proxied = self._proxy_sub(nxt.strip(), proxy_map, ua, cdn_host)
                        if proxied:
                            new_lines.append(line)
                            new_lines.append(f'{proxy_base}{proxied}')
                            kept_variant = True
                    i += 2
                    continue

                new_lines.append(line)
                i += 1

            if not kept_variant:
                if self.debug:
                    fflog(f'target resolution {target_res} not in master')
                return None

            new_master = '\n'.join(new_lines)
            master_hash = hashlib.md5((master_url + target_res).encode()).hexdigest()[:12]
            master_path = f'/nm_{master_hash}.m3u8'
            proxy_map[master_path] = new_master
            service_client.set_media_files(proxy_map)
            return f'{proxy_base}{master_path}'
        except Exception:
            fflog_exc()
            return None

    def _proxy_sub(self, sub_url: str, proxy_map: Dict[str, str], ua: str, cdn_host: Optional[str] = None) -> Optional[str]:
        try:
            # Master sometimes hands us audio URIs missing the host
            # (https:///files/...). Patch with the video-variant host.
            if sub_url.startswith('https:///'):
                if not cdn_host:
                    return None
                sub_url = sub_url.replace('https:///', f'https://{cdn_host}/')

            resp = requests.get(
                sub_url,
                headers={'User-Agent': ua, 'Referer': _BASE + '/'},
                timeout=10,
            )
            if not resp or resp.status_code != 200:
                if self.debug:
                    fflog(f'sub status {getattr(resp,"status_code",None)} for {sub_url[:80]}')
                return None
            text = resp.text
            if not text.lstrip().startswith('#EXTM3U'):
                if self.debug:
                    fflog(f'sub not m3u8 for {sub_url[:80]}')
                return None
            base_url = sub_url.split('?', 1)[0].rsplit('/', 1)[0] + '/'
            fixed = _fix_sub_playlist(text, base_url)
            sub_hash = hashlib.md5(sub_url.encode()).hexdigest()[:12]
            sub_path = f'/nm_sub_{sub_hash}.m3u8'
            proxy_map[sub_path] = fixed
            return sub_path
        except Exception:
            fflog_exc()
            return None


def _fix_sub_playlist(text: str, base_url: str) -> str:
    # Make CDN segment URLs absolute, force VOD type, append ENDLIST.
    # Without VOD/ENDLIST, ISA treats the stream as live.
    base_root = base_url.rstrip('/')
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith('#'):
            out.append(line if line.startswith('http') else f"{base_root}/{line.lstrip('/')}")
        else:
            out.append(raw)
    content = '\n'.join(out)
    if '#EXT-X-PLAYLIST-TYPE' not in content and '#EXT-X-MEDIA-SEQUENCE' in content:
        content = content.replace(
            '#EXT-X-MEDIA-SEQUENCE',
            '#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-MEDIA-SEQUENCE',
            1,
        )
    if '#EXT-X-ENDLIST' not in content:
        content += '\n#EXT-X-ENDLIST\n'
    return content
    