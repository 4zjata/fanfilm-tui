# -*- coding: utf-8 -*-
"""
FanFilm - źródło: vixsrc.to
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
from urllib.parse import urlencode, parse_qs
from typing import TYPE_CHECKING, ClassVar, List, Optional

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
    from lib.ff.item import FFItem

from lib.ff import requests
from lib.ff.source_utils import FF_UA
from lib.ff.resolve_utils import build_isa_url
from lib.ff.log_utils import fflog, fflog_exc


def write_log(s: str):
    try:
        import time
        with open('/home/voidy/rzeczy/repo/fanfilm/fanfilm_tui.log', 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {s}")
    except Exception:
        pass


class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.domains = ['vixsrc.to']

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            write_log(f"[VIXSRC] movie() called with: imdb={imdb!r}, title={title!r}, year={year!r}\n")
            if not imdb:
                write_log("[VIXSRC] movie() imdb is empty!\n")
                return None
            ret = urlencode({'imdb': imdb, 'type': 'movie'})
            write_log(f"[VIXSRC] movie() returns query: {ret}\n")
            return ret
        except Exception as e:
            write_log(f"[VIXSRC] movie() exception: {e!r}\n")
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            write_log(f"[VIXSRC] tvshow() called with: imdb={imdb!r}, tvshowtitle={tvshowtitle!r}, year={year!r}\n")
            if not imdb:
                write_log("[VIXSRC] tvshow() imdb is empty!\n")
                return None
            ret = urlencode({'imdb': imdb, 'type': 'tv'})
            write_log(f"[VIXSRC] tvshow() returns query: {ret}\n")
            return ret
        except Exception as e:
            write_log(f"[VIXSRC] tvshow() exception: {e!r}\n")
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            write_log(f"[VIXSRC] episode() called with: url={url!r}, season={season!r}, episode={episode!r}\n")
            if not url:
                write_log("[VIXSRC] episode() url is empty!\n")
                return None
            data = {k: v[0] for k, v in parse_qs(url).items()}
            data.update({'season': season, 'episode': episode})
            ret = urlencode(data)
            write_log(f"[VIXSRC] episode() returns query: {ret}\n")
            return ret
        except Exception as e:
            write_log(f"[VIXSRC] episode() exception: {e!r}\n")
            fflog_exc()
            return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            write_log(f"[VIXSRC] sources() called with url={url!r}\n")
            if not url:
                write_log("[VIXSRC] sources() url is empty!\n")
                return result

            data = {k: v[0] for k, v in parse_qs(url).items()}
            imdb = data.get('imdb', '')
            content_type = data.get('type', 'movie')
            write_log(f"[VIXSRC] sources() parsed data: imdb={imdb!r}, type={content_type!r}\n")

            if content_type == 'tv':
                target_url = f'https://vixsrc.to/tv/{imdb}/{data.get("season", "1")}/{data.get("episode", "1")}'
            else:
                target_url = f'https://vixsrc.to/movie/{imdb}'

            write_log(f"[VIXSRC] sources() target_url={target_url}\n")
            fflog(f'vixsrc query: {imdb!r} ({content_type})')
            
            # Simple check if target exists
            sess = requests.Session()
            resp = sess.get(target_url, headers={'User-Agent': FF_UA}, timeout=10)
            write_log(f"[VIXSRC] sources() get target status: {resp.status_code}\n")
            if resp.status_code != 200:
                fflog(f'vixsrc query returned status: {resp.status_code}')
                return result

            result.append({
                'source': 'vixsrc',
                'quality': '1080p',
                'language': 'en',
                'url': target_url,
                'info': '',
                'filename': imdb,
                'direct': False,
                'debridonly': False,
            })
            write_log("[VIXSRC] sources() returns 1 source\n")
            fflog('vixsrc sources: 1 result')
        except Exception as e:
            write_log(f"[VIXSRC] sources() exception: {e!r}\n")
            fflog_exc()
        return result

    def resolve(self, url: str) -> Optional[str]:
        try:
            write_log(f"[VIXSRC] resolve() called with url={url!r}\n")
            fflog(f'vixsrc resolve: {url}')
            sess = requests.Session()
            sess.headers.update({'User-Agent': FF_UA})

            # 1. Establish session / load page (sets cookies)
            write_log(f"[VIXSRC] resolve() requesting url={url}\n")
            r_page = sess.get(url, timeout=10)
            write_log(f"[VIXSRC] resolve() page response status={r_page.status_code}\n")
            if r_page.status_code != 200:
                fflog(f'vixsrc resolve page error: {r_page.status_code}')
                return None

            # 2. Derive API URL
            if '/movie/' in url:
                api_url = url.replace('/movie/', '/api/movie/')
            elif '/tv/' in url:
                api_url = url.replace('/tv/', '/api/tv/')
            else:
                write_log(f"[VIXSRC] resolve() unknown URL structure: {url}\n")
                fflog(f'vixsrc resolve unknown URL structure: {url}')
                return None

            # 3. Call vixsrc JSON API -> returns {"src": "/embed/ID?token=..."}
            write_log(f"[VIXSRC] resolve() calling api_url={api_url}\n")
            r_api = sess.get(api_url, headers={'Referer': url, 'Accept': 'application/json'}, timeout=10)
            write_log(f"[VIXSRC] resolve() api response status={r_api.status_code}\n")
            if r_api.status_code != 200:
                fflog(f'vixsrc resolve api error: {r_api.status_code}')
                return None

            src_path = r_api.json().get('src')
            write_log(f"[VIXSRC] resolve() api returned src={src_path!r}\n")
            if not src_path:
                fflog('vixsrc resolve API response missing "src"')
                return None

            embed_url = 'https://vixsrc.to' + src_path

            # 4. Fetch embed page (contains window.masterPlaylist JS block)
            write_log(f"[VIXSRC] resolve() fetching embed_url={embed_url}\n")
            r_embed = sess.get(embed_url, headers={'Referer': url}, timeout=10)
            write_log(f"[VIXSRC] resolve() embed response status={r_embed.status_code}\n")
            if r_embed.status_code != 200:
                fflog(f'vixsrc resolve embed error: {r_embed.status_code}')
                return None

            # 5. Extract window.masterPlaylist
            mp_block = re.search(
                r"window\.masterPlaylist\s*=\s*\{(.*?)\}[\s,]*\}",
                r_embed.text, re.DOTALL
            )
            if not mp_block:
                write_log("[VIXSRC] resolve() window.masterPlaylist block NOT found!\n")
                fflog('vixsrc resolve: window.masterPlaylist not found in embed page')
                return None

            block = mp_block.group(1)

            token_m   = re.search(r"['\"]token['\"]\s*:\s*['\"]([^'\"]+)['\"]", block)
            expires_m = re.search(r"['\"]expires['\"]\s*:\s*['\"]([^'\"]+)['\"]", block)
            url_m     = re.search(r"\burl\s*:\s*['\"]([^'\"]+)['\"]", r_embed.text)

            write_log(f"[VIXSRC] resolve() extracted fields: token={bool(token_m)}, expires={bool(expires_m)}, url={bool(url_m)}\n")
            if not (token_m and expires_m and url_m):
                fflog(f'vixsrc resolve: missing fields — '
                      f'token={bool(token_m)} expires={bool(expires_m)} url={bool(url_m)}')
                return None

            playlist_url = (
                f"{url_m.group(1)}"
                f"?token={token_m.group(1)}"
                f"&expires={expires_m.group(1)}"
                f"&h=1&lang=en"
            )
            write_log(f"[VIXSRC] resolve() built playlist_url={playlist_url}\n")
            fflog(f'vixsrc resolved HLS: {playlist_url}')

            # 6. Format for InputStream Adaptive
            resolved_url = build_isa_url(playlist_url, referer=embed_url, origin='https://vixsrc.to', ua=FF_UA)
            write_log(f"[VIXSRC] resolve() returns resolved_url={resolved_url!r}\n")
            return resolved_url
        except Exception as e:
            write_log(f"[VIXSRC] resolve() exception: {e!r}\n")
            fflog_exc()
            return None
