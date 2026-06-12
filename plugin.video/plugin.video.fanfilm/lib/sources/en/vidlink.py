# -*- coding: utf-8 -*-
"""
FanFilm - źródło: vidlink.pro
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import time
import struct
import base64
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
        self.domains = ['vidlink.pro']

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            write_log(f"[VIDLINK] movie() called with: imdb={imdb!r}, title={title!r}, year={year!r}\n")
            tmdb = ''
            if hasattr(self, 'ffitem') and self.ffitem:
                tmdb = str(self.ffitem.tmdb_id or '')
                write_log(f"[VIDLINK] movie() ffitem found, tmdb_id={tmdb!r}\n")
                if not tmdb:
                    try:
                        tmdb = self.ffitem.getVideoInfoTag().getUniqueID('tmdb')
                        write_log(f"[VIDLINK] movie() read tmdb from video tag: {tmdb!r}\n")
                    except Exception as e:
                        write_log(f"[VIDLINK] movie() video tag err: {e!r}\n")
            ret = urlencode({'imdb': imdb, 'tmdb': tmdb, 'type': 'movie'})
            write_log(f"[VIDLINK] movie() returns query: {ret}\n")
            return ret
        except Exception as e:
            write_log(f"[VIDLINK] movie() exception: {e!r}\n")
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            write_log(f"[VIDLINK] tvshow() called with: imdb={imdb!r}, tvshowtitle={tvshowtitle!r}, year={year!r}\n")
            tmdb = ''
            if hasattr(self, 'ffitem') and self.ffitem:
                show_item = getattr(self.ffitem, 'show_item', None)
                tmdb = str((show_item.tmdb_id if show_item else None) or self.ffitem.tmdb_id or '')
                write_log(f"[VIDLINK] tvshow() ffitem found, tmdb_id={tmdb!r}\n")
                if not tmdb:
                    try:
                        tmdb = self.ffitem.getVideoInfoTag().getUniqueID('tmdb')
                        write_log(f"[VIDLINK] tvshow() read tmdb from video tag: {tmdb!r}\n")
                    except Exception as e:
                        write_log(f"[VIDLINK] tvshow() video tag err: {e!r}\n")
            ret = urlencode({'imdb': imdb, 'tmdb': tmdb, 'type': 'tv'})
            write_log(f"[VIDLINK] tvshow() returns query: {ret}\n")
            return ret
        except Exception as e:
            write_log(f"[VIDLINK] tvshow() exception: {e!r}\n")
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            write_log(f"[VIDLINK] episode() called with: url={url!r}, season={season!r}, episode={episode!r}\n")
            if not url:
                write_log(f"[VIDLINK] episode() url is empty!\n")
                return None
            data = {k: v[0] for k, v in parse_qs(url).items()}
            data.update({'season': season, 'episode': episode})
            ret = urlencode(data)
            write_log(f"[VIDLINK] episode() returns query: {ret}\n")
            return ret
        except Exception as e:
            write_log(f"[VIDLINK] episode() exception: {e!r}\n")
            fflog_exc()
            return None

    def _encrypt_token(self, media_id: str) -> str:
        write_log(f"[VIDLINK] _encrypt_token() encrypting media_id={media_id!r}\n")
        import nacl.secret
        KEY_HEX = "c75136c5668bbfe65a7ecad431a745db68b5f381555b38d8f6c699449cf11fcd"
        KEY = bytes.fromhex(KEY_HEX)
        BOX = nacl.secret.SecretBox(KEY)
        NONCE = bytes(24)

        timestamp = int(time.time() + 480)
        message = media_id.encode("utf-8") + struct.pack(">Q", timestamp)
        encrypted = BOX.encrypt(message, NONCE)
        full_payload = NONCE + encrypted.ciphertext
        token = base64.urlsafe_b64encode(full_payload).decode("utf-8").rstrip("=")
        write_log(f"[VIDLINK] _encrypt_token() success, token generated\n")
        return token

    def _get_tmdb_by_imdb(self, imdb: str) -> str:
        try:
            write_log(f"[VIDLINK] _get_tmdb_by_imdb() performing lookup for imdb={imdb!r}\n")
            from lib.ff import apis
            from lib.ff.settings import settings
            api_key = settings.getString("tmdb.api_key") or apis.tmdb_API
            url = f"https://api.themoviedb.org/3/find/{imdb}?api_key={api_key}&external_source=imdb_id"
            r_tmdb = requests.get(url, timeout=10)
            write_log(f"[VIDLINK] _get_tmdb_by_imdb() tmdb api status: {r_tmdb.status_code}\n")
            if r_tmdb.status_code == 200:
                js = r_tmdb.json()
                tmdb = js.get('movie_results', [{}])[0].get('id', '')
                if not tmdb:
                    tmdb = js.get('tv_results', [{}])[0].get('id', '')
                write_log(f"[VIDLINK] _get_tmdb_by_imdb() tmdb found: {tmdb!r}\n")
                return str(tmdb) if tmdb else ''
        except Exception as e:
            write_log(f"[VIDLINK] _get_tmdb_by_imdb() exception: {e!r}\n")
        return ''

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            write_log(f"[VIDLINK] sources() called with url={url!r}\n")
            if not url:
                write_log(f"[VIDLINK] sources() url is empty!\n")
                return result

            data = {k: v[0] for k, v in parse_qs(url).items()}
            imdb = data.get('imdb', '')
            tmdb = data.get('tmdb', '')
            content_type = data.get('type', 'movie')
            write_log(f"[VIDLINK] sources() parsed data: imdb={imdb!r}, tmdb={tmdb!r}, type={content_type!r}\n")

            # Extract tmdb from ffitem if missing
            if not tmdb and hasattr(self, 'ffitem') and self.ffitem:
                try:
                    show_item = getattr(self.ffitem, 'show_item', None)
                    if content_type == 'tv':
                        tmdb = str((show_item.tmdb_id if show_item else None) or self.ffitem.tmdb_id or '')
                    else:
                        tmdb = str(self.ffitem.tmdb_id or '')
                    write_log(f"[VIDLINK] sources() tmdb from ffitem: {tmdb!r}\n")
                    
                    if not tmdb:
                        tmdb = self.ffitem.getVideoInfoTag().getUniqueID('tmdb')
                        write_log(f"[VIDLINK] sources() tmdb from video tag: {tmdb!r}\n")
                except Exception as e:
                    write_log(f"[VIDLINK] sources() ffitem tmdb extraction exception: {e!r}\n")

            if not tmdb and imdb:
                write_log(f"[VIDLINK] sources() tmdb missing, calling _get_tmdb_by_imdb({imdb})\n")
                tmdb = self._get_tmdb_by_imdb(imdb)

            if not tmdb:
                write_log(f"[VIDLINK] sources() query failed: tmdb ID is missing\n")
                fflog(f'vidlink query failed: tmdb ID is missing')
                return result

            if content_type == 'tv':
                season = data.get('season', '1')
                episode = data.get('episode', '1')
                target_url = f'https://vidlink.pro/tv/{tmdb}/{season}/{episode}'
                api_url_part = f'tv/{tmdb}/{season}/{episode}'
            else:
                target_url = f'https://vidlink.pro/movie/{tmdb}'
                api_url_part = f'movie/{tmdb}'

            write_log(f"[VIDLINK] sources() target_url={target_url}\n")
            fflog(f'vidlink query: tmdb={tmdb!r} ({content_type})')
            
            # Check if target exists
            from lib.ff.source_utils import setting_cookie
            from lib.ff.settings import settings

            cf_cookie = setting_cookie(setting_name='vidlink.cookies_cf', cookie_name='cf_clearance')
            custom_ua = settings.getString('vidlink.user_agent').strip(' "\'')
            ua = custom_ua if custom_ua else FF_UA

            sess = requests.Session()
            headers = {'User-Agent': ua}
            cookies = {}
            if cf_cookie:
                cookies['cf_clearance'] = cf_cookie
                headers['Cookie'] = f'cf_clearance={cf_cookie}'

            resp = sess.get(target_url, headers=headers, cookies=cookies, timeout=10)
            write_log(f"[VIDLINK] sources() get target status: {resp.status_code}\n")
            if resp.status_code != 200:
                fflog(f'vidlink query returned status: {resp.status_code}')
                return result

            result.append({
                'source': 'vidlink',
                'quality': '1080p',
                'language': 'en',
                'url': urlencode({'api_part': api_url_part, 'referer': target_url}),
                'info': '',
                'filename': imdb or tmdb,
                'direct': False,
                'debridonly': False,
            })
            write_log(f"[VIDLINK] sources() returns 1 source\n")
            fflog('vidlink sources: 1 result')
        except Exception as e:
            write_log(f"[VIDLINK] sources() exception: {e!r}\n")
            fflog_exc()
        return result

    def resolve(self, url: str) -> Optional[str]:
        try:
            write_log(f"[VIDLINK] resolve() called with url={url!r}\n")
            fflog(f'vidlink resolve url: {url}')
            data = {k: v[0] for k, v in parse_qs(url).items()}
            api_part = data.get('api_part', '')
            referer = data.get('referer', 'https://vidlink.pro/')
            write_log(f"[VIDLINK] resolve() api_part={api_part!r}, referer={referer!r}\n")

            if not api_part:
                write_log(f"[VIDLINK] resolve() api_part is empty!\n")
                return None

            parts = api_part.split('/')
            media_id = parts[1]

            # Encrypt the token
            token = self._encrypt_token(media_id)
            write_log(f"[VIDLINK] resolve() encrypted token={token!r}\n")

            if parts[0] == 'tv':
                api_url = f'https://vidlink.pro/api/b/tv/{token}/{parts[2]}/{parts[3]}?multiLang=1'
            else:
                api_url = f'https://vidlink.pro/api/b/movie/{token}?multiLang=1'

            from lib.ff.source_utils import setting_cookie
            from lib.ff.settings import settings

            cf_cookie = setting_cookie(setting_name='vidlink.cookies_cf', cookie_name='cf_clearance')
            custom_ua = settings.getString('vidlink.user_agent').strip(' "\'')
            ua = custom_ua if custom_ua else FF_UA

            sess = requests.Session()
            headers = {
                'User-Agent': ua,
                'Origin': 'https://vidlink.pro',
                'Referer': referer,
                'Accept': 'application/json'
            }
            if cf_cookie:
                sess.cookies.set('cf_clearance', cf_cookie)
                headers['Cookie'] = f'cf_clearance={cf_cookie}'

            write_log(f"[VIDLINK] resolve() requesting api_url={api_url}\n")
            fflog(f'vidlink calling API: {api_url}')
            r = sess.get(api_url, headers=headers, timeout=10)
            write_log(f"[VIDLINK] resolve() api response status={r.status_code}\n")
            if r.status_code != 200:
                fflog(f'vidlink API returned status: {r.status_code}')
                return None

            resp_json = r.json()
            stream_data = resp_json.get('stream', {})
            playlist_url = stream_data.get('playlist', '')
            write_log(f"[VIDLINK] resolve() extracted playlist_url={playlist_url!r}\n")
            if not playlist_url:
                fflog('vidlink API response missing playlist URL')
                return None

            # Extract custom headers
            extra_headers = {}
            if '?' in playlist_url:
                try:
                    parsed_playlist = parse_qs(playlist_url.split('?')[1])
                    if 'headers' in parsed_playlist:
                        import json
                        hdr_json = json.loads(parsed_playlist['headers'][0])
                        if isinstance(hdr_json, dict):
                            extra_headers = hdr_json
                            write_log(f"[VIDLINK] resolve() parsed extra_headers={extra_headers!r}\n")
                except Exception as e:
                    write_log(f"[VIDLINK] resolve() extra headers parsing exception: {e!r}\n")

            stream_referer = extra_headers.get('referer') or extra_headers.get('Referer') or 'https://megacloud.live/'
            stream_origin = extra_headers.get('origin') or extra_headers.get('Origin') or 'https://megacloud.live'

            cookie_param = f"cf_clearance={cf_cookie}" if cf_cookie else None
            resolved_url = build_isa_url(playlist_url, referer=stream_referer, origin=stream_origin, ua=ua, cookie=cookie_param)
            write_log(f"[VIDLINK] resolve() returns resolved_url={resolved_url!r}\n")
            fflog(f'vidlink resolved HLS: {playlist_url}')
            return resolved_url

        except Exception as e:
            write_log(f"[VIDLINK] resolve() exception: {e!r}\n")
            fflog_exc()
            return None
