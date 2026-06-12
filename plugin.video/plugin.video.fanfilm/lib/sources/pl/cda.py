# -*- coding: utf-8 -*-
"""
FanFilm ‑ źródło: cda.pl
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from ast import literal_eval
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

from urllib.parse import urlencode
from lib.ff import cache, cleantitle, control, requests, source_utils
from lib.ff.item import FFItem
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.settings import settings
from lib.ff.source_utils import convert_size, ShowData, show_data_asdict, ShowDataDict, CHROME_UA
from const import const


USER_AGENT: str = CHROME_UA
HASH_SALT: bytes = b'NpmMLBWRgtEX8vp3Kf3d0tasBpFt0tuGswL9hR0qt7bQdaxuvDGoczFGeqd68Nj2'
BASIC_AUTH: str = (
    'Basic NzdjMGYzYzUtMzZhMC00YzNkLWIwZDQtMGM0ZGZiZmQ1NmQ1Ok5wbU1MQldSZ3RFWDh2'
    'cDNLZjNkMHRhc0JwRnQwdHVHc3dMOWhSMHF0N2JRZGF4dXZER29jekZHZXFkNjhOajI='
)
BASE_LINK: str = 'https://api.cda.pl'
SEARCH_LINK: str = f"{BASE_LINK}/video/search"

TITLE_STOPWORDS: Set[str] = {
    'dubbing',
    'napisy',
    'zwiastun',
    'trailer',
    'recenzja',
    'omowienie',
    'podsumowanie',
    'gameplay',
    'nazywo',
    'walktrough',
    'stream',
    'opis',
    'newsy',
}
TITLE_EXCLUDE_WORDS: Set[str] = {
    'cały sezon',
    'kolekcja',
    'box',
    'antologia',
    'zwiastun',
    'trailer',
    'recenzja',
    'omowienie',
    'podsumowanie',
    'omówienie',
    'gameplay',
    'nazywo',
    'walktrough',
    'stream',
    'opis',
    'newsy',
    'playstation',
    'xbox',
    'ps3',
    'ps4',
    'ps5',
    'xbox360',
    'xboxone',
    'xboxone s',
    'xboxone x',
    'xbox series s',
    'xbox series x',
    'nintendo',
    'switch',
    'soundtrack',
}
EPISODE_RE = re.compile(r'\bs\d{1,2}e\d{2,4}\b', re.I)
# Non-standard episode markers used on cda.pl titles (e.g. "Cuda (1) - subtitle")
_NXM_EP_RE = re.compile(r'\b\d{1,2}x\d{1,4}\b')
_NAMED_EP_RE = re.compile(r'\b(?:odcinek|odc|episode|ep)\s*\.?\s*\d+\b', re.I)
_BARE_EP_RE = re.compile(r'\(\s*\d{1,3}\s*\)')
SEQUEL_REGEX = re.compile(r'\b([2-9]|[1-9][0-9])\b')
# Roman numerals used in sequel titles (I excluded – too common as standalone word)
_ROMAN_SEQUEL_RE = re.compile(
    r'\b(ii|iii|iv|vi|vii|viii|ix|xi|xii)\b', re.I
)
# Words appended to video titles that don't identify the film (audio/quality tags)
_TITLE_QUALIFIERS: frozenset = frozenset({
    'lektor', 'lector', 'hdtv', 'bluray', 'bdrip', 'dvdrip', 'dvd',
    'webrip', 'xvid', 'x264', 'x265', 'hevc', 'avc', '1080p', '720p',
    '480p', '4k', 'uhd', 'fhd', 'mkv', 'avi', 'mp4', 'scr', 'cam',
    'proper', 'repack', 'extended', 'unrated', 'remastered',
    'napisy', 'subbed', 'dubbing', 'pllek', 'plsub', 'pldub', 'multi',
})


# ─── source ──────────────────────────────────────────────────────────────────────

class source:  # noqa: N801

    has_sort_order: bool = True
    has_color_identify2: bool = True
    use_premium_color: bool = True
    ffitem: FFItem

    def __init__(self) -> None:
        self.priority: int = 1
        self.language: List[str] = ['pl']
        self.domains: List[str] = ['cda.pl']
        self.debug = const.sources.cda.debug

        self.email: str = settings.getString('cda.username')
        passwd_ctrl: str = settings.getString('cda.password')
        self.passwd: str = self._hash_password(passwd_ctrl)
        email_hash = hashlib.md5(self.email.encode()).hexdigest()[:8]
        self._token_cache_key: str = f"cda_token_{email_hash}"

        self.session: requests.Session = requests.Session()
        self.headers: Dict[str, str] = {
            'User-Agent': USER_AGENT,
            'Accept': 'application/vnd.cda.public+json',
        }
        self.headers_basic: Dict[str, str] = {'Authorization': BASIC_AUTH}
        self.token: Optional[str] = cache.cache_value(self._token_cache_key, control.providercacheFile)

        self.year: int = 0
        self._is_episode: bool = False

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[List[str]]:
        return self._search(title, localtitle, year, aliases)

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> ShowDataDict:
        return show_data_asdict(ShowData(tvshowtitle, localtvshowtitle, aliases, int(year)))

    def episode(
        self, url: dict[str, Any], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str
    ) -> Optional[List[str]]:
        data = ShowData(**url)
        self.year = data.year
        self._is_episode = True
        ep_no = f"s{season.zfill(2)}e{episode.zfill(2)}"
        return self._search_episode(data.title, data.local_title, self.year, ep_no, data.aliases)

    def sources(self, ids: Optional[List[str]], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        sources = []
        try:
            if not ids:
                return sources

            fflog(f"processing {len(ids)} candidates")
            # Store all DRM sources to add them at the end
            drm_sources = []

            for i, url in enumerate(ids):
                fflog(f"candidate {i + 1}/{len(ids)} (id: {url})")
                try:
                    response = self._make_authenticated_request('GET', f'{BASE_LINK}/video/{url}', headers=self.headers)
                    query = response.json()
                except Exception:
                    fflog_exc()
                    continue

                if query.get('error'):
                    if self.debug:
                        fflog(f"skip (api error): id={url} ({query.get('error')})")
                    continue
                else:
                    video = query.get('video')
                    if not self._compare_duration(self.ffitem.vtag.getDuration(), video.get('duration')):
                        if self.debug:
                            _exp = self.ffitem.vtag.getDuration()
                            _cda = video.get('duration') or 0
                            _diff = abs(_exp - _cda)
                            _ratio = _diff / _exp if _exp else 0
                            fflog(
                                f"skip (duration mismatch): '{video['title']}' (cda={_cda}s, expected={_exp}s, diff={_diff}s, ε_needed={_ratio:.3f}, ε_set={const.sources.cda.duration_epsilon})")
                        continue

                    info_parts = []
                    # Handle LEKTOR, NAPISY, DUBBING - various formats
                    title = video['title']
                    title_lower = title.lower()

                    # Check for dubbing variants
                    if any(phrase in title_lower for phrase in ['dubbing pl', 'pldub', 'pl dub', 'dubbing']):
                        info_parts.append('DUBBING')
                    # Check for lektor variants
                    elif any(phrase in title_lower for phrase in ['lektor pl', 'pllek', 'pl lek', 'lektor']):
                        info_parts.append('LEKTOR')
                    # Check for napisy variants
                    elif any(phrase in title_lower for phrase in ['napisy pl', 'plsub', 'pl sub', 'napisy']):
                        info_parts.append('NAPISY')
                    # Handle PREMIUM.
                    if video.get('premium'):
                        if video.get('premium_free'):
                            info_parts.append(const.sources.cda.show_premium_free)
                        else:
                            info_parts.append('Premium')
                    # TODO: handle video['audio_51']
                    if video.get('audio_51'):
                        info_parts.append('5.1')
                    # build info
                    info = ' | '.join(part for part in info_parts if part)
                    premium = 'premium' in info.lower()

                    if const.sources_dialog.cda_drm:
                        # check adaptive stream
                        adaptive_data = video.get('quality_adaptive')
                        if adaptive_data:
                            highest_quality = self._get_highest_quality_direct(video)
                            adaptive_quality = source_utils.get_quality(highest_quality)

                            if self.debug:
                                fflog(f"DRM candidate: '{title}' (quality={highest_quality}, manifest={adaptive_data.get('manifest')})")

                            # Add each DRM source to the list with unique identifier
                            drm_source_info = {
                                'adaptive_data': adaptive_data,
                                'video_info': {
                                    'title': title,
                                    'quality': highest_quality,
                                    'adaptive_quality': adaptive_quality,
                                    'info': info,
                                    'premium': premium,
                                    'url': url,
                                }
                            }
                            drm_sources.append(drm_source_info)

                    # Regular direct sources
                    for variant in video['qualities']:
                        if variant.get('file'):
                            size_in_bytes = variant.get('length', 0)
                            min_size_bytes = const.sources.cda.min_size_mb * 1024 * 1024
                            if size_in_bytes < min_size_bytes:
                                if self.debug:
                                    fflog(f"skip (too small): '{video['title']}' ({convert_size(size_in_bytes)})")
                                continue

                            sources.append({
                                'source': 'CDA',
                                'quality': variant['name'] if variant['name'] in ('1080p', '720p') else 'SD',
                                'language': 'pl',
                                'url': f"DRMFF|{url}|{variant['name']}",
                                'info': info,
                                'filename': video['title'],
                                'direct': True,
                                'debridonly': False,
                                'size': convert_size(variant['length']),
                                'premium': premium,
                            })
                        else:
                            if self.debug:
                                fflog(
                                    f"skip (no link): '{video['title']}' {variant.get('name')} — not logged in or Premium")

            # Now add ALL DRM sources
            if const.sources_dialog.cda_drm and drm_sources:
                if self.debug:
                    fflog(f"adding {len(drm_sources)} DRM sources")
                for i, drm_source in enumerate(drm_sources):
                    adaptive_data = drm_source['adaptive_data']
                    video_info = drm_source['video_info']

                    if self.debug:
                        fflog(f"adding DRM source #{i + 1}: {video_info['quality']}")
                    # Create unique cache key for each DRM source
                    cache_key = f"DRMFF_{video_info['url']}_{i}"
                    cache.cache_insert(cache_key, repr(adaptive_data), control.providercacheFile)

                    source_info = f"{video_info['info']} | DRM" if video_info['info'] else 'DRM'

                    sources.append({
                        'source': 'CDA',
                        'quality': video_info['adaptive_quality'],
                        'language': 'pl',
                        'url': f"{cache_key}|{video_info['url']}",
                        'info': source_info,
                        'filename': video_info['title'],
                        'direct': True,
                        'debridonly': False,
                        'premium': video_info['premium'],
                    })
            fflog(f'sources: {len(sources)}')
            return sources
        except Exception:
            fflog_exc()
            return sources

    def resolve(self, url: str) -> Optional[str]:
        # Data for InputStreamHelper.
        # InputStream old properties – deprecated (Kodi 18-22).
        # See: https://github.com/xbmc/inputstream.adaptive/wiki/Integration-DRM
        from lib.service.client import service_client

        url_parts = url.split('|')
        if len(url_parts) == 3:  # New format: DRMFF|{video_id}|{quality}
            video_id = url_parts[1]
            requested_quality = url_parts[2]

            if self.debug:
                fflog(f'resolving forced DRM for video_id: {video_id}, quality: {requested_quality}')

            try:
                import resolveurl
            except ImportError:
                resolveurl = None

            try:
                response = self._make_authenticated_request(
                    'GET', f'{BASE_LINK}/video/{video_id}', headers=self.headers)
                query = response.json()
            except Exception:
                fflog_exc()
                if resolveurl:
                    fflog("CDA resolve API failed, falling back to resolveurl")
                    cda_web_url = f"https://ebd.cda.pl/647x500/{video_id}"
                    resolved = resolveurl.resolve(cda_web_url)
                    if resolved:
                        return resolved
                raise ValueError(f'Failed to fetch video info for {video_id}')

            if not query.get('error'):
                video = query.get('video')
                if video:
                    adaptive = video.get('quality_adaptive')
                    if not adaptive:
                        if self.debug:
                            fflog(f'no adaptive stream for video_id: {video_id}, checking for direct link')
                            fflog(f'video data: {query}')
                        for variant in video.get('qualities', []):
                            if variant.get('name') == requested_quality and (file_url := variant.get('file')):
                                # Append User-Agent header to the URL for Kodi player
                                headers = {'User-Agent': USER_AGENT}
                                header_string = '&'.join(f"{k}={v}" for k, v in headers.items())
                                if self.debug:
                                    fflog(
                                        f"found direct link for quality {requested_quality}: {file_url}|{header_string}")
                                return f"{file_url}|{header_string}"
                        if self.debug:
                            fflog(
                                f'no adaptive stream and no direct link for video_id: {video_id}, quality: {requested_quality}')
                        raise ValueError(f'No playable stream found for video_id: {video_id}')

                else:
                    fflog(f'no video data for video_id: {video_id}')
                    raise ValueError(f'No video data found for video_id: {video_id}')
            else:
                fflog(f"error fetching video info for {video_id}: {query.get('error')}")
                if resolveurl:
                    fflog("CDA resolve API error, falling back to resolveurl")
                    cda_web_url = f"https://ebd.cda.pl/647x500/{video_id}"
                    resolved = resolveurl.resolve(cda_web_url)
                    if resolved:
                        return resolved
                raise ValueError(f"Error fetching video info for {video_id}: {query.get('error')}")
        elif len(url_parts) > 1:  # Existing format: DRMFF|{cache_key}
            cache_key = url_parts[0]
            if adaptive := literal_eval(cache.cache_value(cache_key, control.providercacheFile)):
                if self.debug:
                    fflog(f'DRM resolve: {adaptive=}')
                pass
            else:
                fflog(f'DRM failed: no {cache_key}')
                raise ValueError(f'DRM failed: no {cache_key}')
        else:
            fflog(f'unexpected DRMFF URL format: {url}')
            raise ValueError(f'Unexpected DRMFF URL format: {url}')

        PROTOCOL = 'mpd'
        DRM = 'com.widevine.alpha'
        manifest_url = adaptive.get('manifest')
        lic_url = adaptive.get('widevine_license')

        if not manifest_url:
            fflog('DRM failed: no manifest URL')
            raise ValueError('DRM failed: no manifest URL')

        manifest_url = adaptive.get('manifest_h264') or adaptive.get('manifest')
        if self.debug and adaptive.get('manifest_h264'):
            fflog('using H264 manifest for higher quality')

        drm_header = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.62',
            'X-Dt-Custom-Data': adaptive.get('drm_header_value'),
            'Content-Type': 'application/octet-stream',
        }

        if lic_url:
            lic_url = f'{service_client.url}drm={lic_url}'
        else:
            if self.debug:
                fflog('no license URL found')

        adaptive_data = {
            'protocol': PROTOCOL,
            'licence_type': DRM,
            'mimetype': 'application/xml+dash',
            'manifest': manifest_url,
            'licence_url': lic_url,
            'licence_header': urlencode(drm_header) if lic_url else '',
            'post_data': 'R{SSM}' if lic_url else '',
            'response_data': '',
            'stream_headers': drm_header,
        }

        link = f'DRMFF|{repr(adaptive_data)}'

        try:
            test = literal_eval(link.split('|')[-1])
            if self.debug:
                fflog(f"DRM data validation successful: manifest={test.get('manifest')}")
        except Exception as e:
            fflog(f'DRM data validation failed: {e}')

        return link

    # ── helpers ────────────────────────────────────────────────────────────

    def oauth(self, email: str, passwd: str) -> bool:  # name required
        """Obtain access token and store in cache."""
        self.headers.update(self.headers_basic)
        if not self._check_email(email):
            fflog('email check failed')
            return False

        payload = {'grant_type': 'password', 'login': email, 'password': passwd}
        try:
            response = self.session.post(
                f"{BASE_LINK}/oauth/token", params=payload, headers=self.headers
            )

            user = response.json()

            if user.get('access_token') and user.get('refresh_token'):
                self.token = user['access_token']
                cache.cache_insert(self._token_cache_key, self.token, control.providercacheFile)
                fflog('login: ok')
                return True
            else:
                fflog(f'login: failed — {user}')
                return False
        except Exception as e:
            fflog_exc()
            fflog(f"OAuth error: {e}")
            return False

    @staticmethod
    def _hash_password(passwd: str) -> str:
        """PBKDF-like hash used by CDA API for login."""
        md5 = hashlib.md5(passwd.encode('utf-8')).hexdigest().encode('utf-8')
        digest = hmac.new(HASH_SALT, md5, hashlib.sha256).digest()
        return (
            base64.b64encode(digest).decode('utf-8').replace('/', '_')
            .replace('+', '-')
            .replace('=', '')
        )

    def _normalize_title(self, txt: str) -> str:
        """Lowercase, remove stopwords and bracketed year."""
        if not txt:
            return ''
        txt = cleantitle.normalize(txt)
        txt = re.sub(r'[\(\[]\s*\d{4}\s*[\)\]]', '', txt)
        txt = re.sub(r'\bpl\b', '', txt, flags=re.I)
        txt = re.sub(r'[^0-9a-ząćęłńóśźż]+', ' ', txt.lower())
        txt = re.sub(
            r'\b([a-ząćęłńóśźż])\b(?: \b[a-ząćęłńóśźż]\b)+',
            lambda m: m.group(0).replace(' ', ''),
            txt,
        )
        return ' '.join(
            w for w in txt.split() if w and w not in TITLE_STOPWORDS
        )

    def _all_words_match(
        self,
        expected: str,
        candidate: str,
        *,
        rel_tol: float = 0.25,
        abs_tol_short: int = 1,
        abs_tol_long: int = 2,
        min_coverage: float = 0.40,
    ) -> bool:
        exp_words: List[str] = self._normalize_title(expected).split()
        cand_words: List[str] = self._normalize_title(candidate).split()
        if not exp_words or not cand_words:
            return False

        # 1. every expected word must match (typo-tolerant)
        for exp_word in exp_words:
            if len(exp_word) <= 2:
                continue
            if not any(
                self._words_similar(exp_word, cand_word, rel_tol,
                                    abs_tol_short, abs_tol_long)
                for cand_word in cand_words
            ):
                return False

        # 2. coverage – fraction of candidate words matched
        matched = sum(
            1
            for cand_word in cand_words
            if any(
                self._words_similar(exp_word, cand_word, rel_tol,
                                    abs_tol_short, abs_tol_long)
                for exp_word in exp_words
            )
        )
        return matched / len(cand_words) >= min_coverage

    def _words_similar(
        self,
        exp_word: str,
        cand_word: str,
        rel_tol: float,
        abs_tol_short: int,
        abs_tol_long: int,
    ) -> bool:
        dist: int = source_utils._levenshtein(exp_word, cand_word)
        if (len(exp_word) <= 4 and dist <= abs_tol_short) or (
            len(exp_word) > 4 and dist <= abs_tol_long
        ):
            return True
        return dist / max(len(exp_word), len(cand_word)) <= rel_tol

    def _matches_episode_marker(self, cand_title: str, ep_no: str) -> bool:
        """Check if cand_title contains episode marker matching ep_no ('sNNeNN' format).

        Accepts: SnEn, NxN, 'odcinek N' / 'odc N' / 'episode N' / 'ep N',
        and '(N)' (only for season 1, to avoid matching standalone numbers).
        """
        parsed = re.match(r's(\d+)e(\d+)', ep_no, re.I)
        if not parsed:
            return ep_no in cand_title.lower().replace(' ', '')
        season = int(parsed.group(1))
        episode = int(parsed.group(2))
        title_lower = cand_title.lower()

        if re.search(rf'\bs0*{season}e0*{episode}\b', title_lower):
            return True
        if re.search(rf'\b0*{season}x0*{episode}\b', title_lower):
            return True
        if re.search(rf'\b(?:odcinek|odc|episode|ep)\s*\.?\s*0*{episode}\b', title_lower):
            return True
        # "(N)" — only for season 1 to avoid false positives from standalone numbers
        if season == 1 and re.search(rf'\(\s*0*{episode}\s*\)', cand_title):
            return True
        return False

    def _strip_episode_part(self, text: str) -> str:
        """Strip earliest episode marker and everything after it.

        Used to isolate show-title portion from candidate (and search term) for
        title matching. Handles SnEn, NxN, 'odcinek N', '(N)'.
        """
        earliest = None
        for pattern in (EPISODE_RE, _NXM_EP_RE, _NAMED_EP_RE, _BARE_EP_RE):
            match = pattern.search(text)
            if match and (earliest is None or match.start() < earliest):
                earliest = match.start()
        return text[:earliest].strip() if earliest is not None else text

    @staticmethod
    def _sequel_numbers(normalized: str) -> Set[int]:
        """Extract sequel numbers from a normalized title – both Arabic and Roman."""
        nums = {int(n) for n in SEQUEL_REGEX.findall(normalized)}
        for m in _ROMAN_SEQUEL_RE.finditer(normalized):
            nums.add(source_utils.roman_to_int(m.group()))
        return nums

    def _check_email(self, email: str) -> bool:
        self.headers.update(self.headers_basic)
        resp = self.session.post(
            f"{BASE_LINK}/register/check-email",
            data={'email': email},
            headers=self.headers,
        )
        return bool(resp.json())

    def _is_token_expired(self, token_cache_row) -> bool:
        """Return True if cached token is older than 7 days."""
        if not token_cache_row:
            return True

        token_age = time.time() - int(token_cache_row['date'])
        return token_age > 7 * 24 * 3600  # 7 days

    def _make_authenticated_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make authenticated request with auto-login on token expiry."""

        token = self.token

        # try loading from cache
        if not token and self.email:
            token_cache_row = cache.cache_get(self._token_cache_key, control.providercacheFile)
            if token_cache_row and not self._is_token_expired(token_cache_row):
                token = token_cache_row['value']
                self.token = token
            else:
                # token expired or missing
                if token_cache_row:
                    cache.cache_insert(self._token_cache_key, '', control.providercacheFile)
                if self.oauth(self.email, self.passwd):
                    token = self.token

        if token:
            if 'headers' not in kwargs:
                kwargs['headers'] = self.headers.copy()
            kwargs['headers']['Authorization'] = f"Bearer {token}"

        try:
            response = self.session.request(method, url, **kwargs)

            # re-login on auth error
            if response.status_code in [400, 401, 403] and self.email and token:
                fflog(f"auth error {response.status_code}, attempting relogin")
                self.token = None
                cache.cache_insert(self._token_cache_key, '', control.providercacheFile)

                if self.oauth(self.email, self.passwd):
                    new_token = self.token
                    if new_token:
                        if 'headers' not in kwargs:
                            kwargs['headers'] = self.headers.copy()
                        kwargs['headers']['Authorization'] = f"Bearer {new_token}"
                        response = self.session.request(method, url, **kwargs)
                    else:
                        fflog('relogin: no token')
                else:
                    fflog('relogin: failed')

            return response

        except Exception as e:
            fflog_exc()
            fflog(f"request error: {e}")
            return None

    def _search(
        self,
        title: str,
        localtitle: str,
        year: int,
        aliases: 'list[SourceTitleAlias] | None' = None,
    ) -> List[str]:
        pl_title = cleantitle.normalize(cleantitle.getsearch(localtitle))
        en_title = cleantitle.normalize(cleantitle.getsearch(title))

        short_pl_title = ''
        if localtitle and ':' in localtitle:
            short_pl_title = cleantitle.normalize(cleantitle.getsearch(localtitle.split(':', 1)[0].strip()))

        short_en_title = ''
        if title and ':' in title:
            short_en_title = cleantitle.normalize(cleantitle.getsearch(title.split(':', 1)[0].strip()))

        titles_differ = pl_title != en_title

        search_variants = []

        # Primary: Polish title with year
        if pl_title:
            search_variants.append(f"{pl_title} {year}")

        # Short PL variant (title with ":")
        if short_pl_title:
            search_variants.append(f"{short_pl_title} {year}")

        # English variant with year
        if en_title and titles_differ:
            search_variants.append(f"{en_title} {year}")

        # Short EN variant
        if short_en_title and short_en_title != short_pl_title:
            search_variants.append(f"{short_en_title} {year}")

        # Production-country original title (e.g. "Supercondriaque" for the French
        # "Superchondriac" / "Przychodzi facet do lekarza") — only when it differs
        # from PL and EN variants we already query.
        if aliases:
            original_title = next((entry.get('originalname') for entry in aliases if entry.get('originalname')), None)
            if original_title:
                original_clean = cleantitle.normalize(cleantitle.getsearch(original_title))
                if original_clean and original_clean not in (pl_title, en_title, short_pl_title, short_en_title):
                    search_variants.append(f"{original_clean} {year}")

        # Bare Polish title without the year token — some uploads obfuscate the
        # name or drop the year (e.g. "M4trix PL" for The Matrix) and surface only
        # on a plain title query. Year filtering still runs per candidate.
        if pl_title:
            search_variants.append(pl_title)

        return self._aggregate_candidates(search_variants, year)

    def _aggregate_candidates(self, variants: List[str], year: int, **search_kwargs: Any) -> List[str]:
        """Run each unique (case-insensitive) variant and collect deduplicated ids."""
        seen_variants: Set[str] = set()
        seen_ids: Set[str] = set()
        all_results: List[str] = []
        for variant in variants:
            if variant.lower() in seen_variants:
                continue
            seen_variants.add(variant.lower())
            for video_id in self._search_single_term(variant, year, **search_kwargs):
                if video_id not in seen_ids:
                    seen_ids.add(video_id)
                    all_results.append(video_id)

        if all_results:
            fflog(f"total {len(all_results)} unique candidates")
        else:
            fflog('no match')
        return all_results

    def _search_single_term(self, term: str, year: int, ep_no: Optional[str] = None, cc_titles: Optional[List[str]] = None, franchise_patterns: Optional[Dict[str, List[str]]] = None) -> List[str]:
        fflog(f"query: '{term}'")
        q_term = term.replace("'", '').replace(':', '').replace('-', ' ').lower()
        params = {
            'query': q_term,
            'duration': 'medium',
            'page': 1,
            'limit': 100,
            'sort': 'best',
        }

        try:
            response = self._make_authenticated_request('GET', SEARCH_LINK, params=params, headers=self.headers)
            if not response:
                return []
            data = response.json()
        except Exception:
            fflog_exc()
            return []

        # strip year – checked separately
        term_for_matching = term.replace(str(year), '').strip()

        results: List[str] = []
        fflog(f"search: {len(data.get('data', []))} results")
        for item in data.get('data', []):
            cand_title = item['title']

            # Episode marker check (multiple formats: SnEn, NxN, "odcinek N", "(N)" for S1)
            if ep_no and not self._matches_episode_marker(cand_title, ep_no):
                if self.debug:
                    fflog(f"skip (episode mismatch): '{cand_title}' (expected: {ep_no})")
                continue

            excluded_word = next((word for word in TITLE_EXCLUDE_WORDS if word in cand_title.lower()), None)
            if excluded_word:
                if self.debug:
                    fflog(f"skip (excluded keyword): '{cand_title}' (word: {excluded_word})")
                continue

            # Reject candidates that contain a sequel number not present in the search term
            # e.g. searching "Krzyk" must not match "Krzyk 3", searching "Krzyk 2" must not match "Krzyk 3"
            exp_seq = self._sequel_numbers(self._normalize_title(term_for_matching))
            # For episode searches strip episode references so "Odcinek 2" / "S01E02"
            # isn't mistaken for a sequel number
            cand_for_seq = cand_title
            if ep_no:
                cand_for_seq = EPISODE_RE.sub(' ', cand_for_seq)
                cand_for_seq = re.sub(
                    r'\b(?:odcinek|odc|episode|ep|sezon|season)\s*\.?\s*\d+\b',
                    ' ', cand_for_seq, flags=re.I,
                )
                # Strip "(N)" episode markers (cda non-standard) so they aren't read as sequels
                cand_for_seq = _BARE_EP_RE.sub(' ', cand_for_seq)
            cand_seq = self._sequel_numbers(self._normalize_title(cand_for_seq))
            if cand_seq - exp_seq:
                if self.debug:
                    fflog(f"skip (sequel mismatch): '{cand_title}' (expected {exp_seq or 'none'}, got {cand_seq})")
                continue

            # Country variant check (e.g. Biuro vs Biuro US vs Biuro UK)
            if ep_no and cc_titles:
                cand_norm_all = re.sub(r'[^0-9a-z]+', ' ', cand_title.lower()).split()
                if conflict_cc := source_utils.detect_country_variant_conflict(cand_norm_all, cc_titles):
                    if self.debug:
                        fflog(f"skip (country variant {conflict_cc}): '{cand_title}'")
                    continue

            # For episode searches, strip the episode marker from both sides so
            # subtitle/quality tags after the marker don't dilute coverage
            if ep_no:
                term_for_match = self._strip_episode_part(term_for_matching)
                title_for_match = self._strip_episode_part(cand_title)
            else:
                term_for_match = term_for_matching
                title_for_match = cand_title

            if not self._all_words_match(term_for_match, title_for_match):
                # If direct match fails, try franchise patterns
                franchise_match = franchise_patterns and any(
                    re.search(pat, cand_title, re.I)
                    for pat_list in franchise_patterns.values()
                    for pat in pat_list
                )
                if not franchise_match:
                    if self.debug:
                        fflog(f"skip (title mismatch): '{cand_title}' (searched: '{term_for_matching}')")
                    continue
            else:
                franchise_match = False

            # When search term is ≤2 content words, reject candidates with extra
            # non-qualifier words in the show title portion — e.g. "Krzyk" must not match "Krzyk gory"
            exp_norm = self._normalize_title(term_for_matching).split()
            content_words = [
                w for w in exp_norm
                if len(w) > 3
                and w not in _TITLE_QUALIFIERS
                and not re.fullmatch(r's\d{1,2}e\d{1,4}', w, re.I)
            ]
            if not franchise_match and len(content_words) <= 2:
                # Isolate the show-title part by stripping the earliest episode marker
                show_title_part = self._strip_episode_part(cand_title) if ep_no else cand_title

                cand_norm = self._normalize_title(show_title_part).split()
                extra = [
                    word for word in cand_norm
                    if len(word) > 3
                    and word not in _TITLE_QUALIFIERS
                    and not re.match(r'^(?:19|20)\d{2}$', word)
                    and not any(
                        self._words_similar(exp_word, word, 0.25, 1, 2)
                        for exp_word in exp_norm if len(exp_word) > 3
                    )
                ]

                if extra:
                    # Allow extra words that are valid 2-letter country codes —
                    # they passed detect_country_variant_conflict above so they're expected.
                    if ep_no:
                        extra = [w for w in extra if w not in source_utils.FRANCHISE_CC]

                    if extra:
                        if self.debug:
                            fflog(f"skip (extra words in title): '{cand_title}' (extra: {extra})")
                        continue

            if not source_utils.year_matches(cand_title, int(year)):
                if self.debug:
                    fflog(f"skip (year mismatch): '{cand_title}' (expected: {year})")
                continue

            if self.debug:
                fflog(f"accept: '{cand_title}'")
            results.append(item['id'])

        fflog(f"found {len(results)} candidates for '{term}'")
        return results

    # episodes

    def _search_episode(
        self,
        title: str,
        localtitle: str,
        year: int,
        ep_no: str,
        aliases: list[SourceTitleAlias],
    ) -> List[str]:
        fflog(f"searching episode: '{localtitle}' {ep_no} ({year})")
        pl_title = cleantitle.normalize(cleantitle.getsearch(localtitle))
        en_title = cleantitle.normalize(cleantitle.getsearch(title))

        titles_differ = pl_title != en_title

        search_variants = [
            f"{pl_title} {ep_no}",
        ]

        # EN variant only if EN<>PL
        if en_title and titles_differ:
            search_variants.append(f"{en_title} {ep_no}")

        # Add aliases to search variants
        prep_aliases = source_utils.prepare_alias_search_list(aliases, str(year)) if aliases else []
        for alias in prep_aliases[:3]:  # Limit to 3 extra variants
            alias_clean = cleantitle.normalize(cleantitle.getsearch(alias))
            search_variants.append(f"{alias_clean} {ep_no}")

        # Add short variant (often works better on CDA for some shows like JAG)
        if len(en_title) <= 5 or len(pl_title) <= 5:
            short_title = en_title if len(en_title) <= len(pl_title) else pl_title
            search_variants.append(f"{short_title} {ep_no}")

        # Fallback: bare title — some series use non-standard episode markers
        # (e.g. "Cuda (1) - subtitle") that won't appear in s##e## search results
        if pl_title:
            search_variants.append(pl_title)
        if en_title and titles_differ:
            search_variants.append(en_title)

        # Titles for CC conflict detection and franchise matching
        all_titles = [title, localtitle] + prep_aliases
        franchise_patterns = source_utils.build_relevant_franchises(
            all_titles, const.sources.franchise_names, const.sources.franchise_names_sep
        )

        return self._aggregate_candidates(
            search_variants, year, ep_no=ep_no, cc_titles=all_titles, franchise_patterns=franchise_patterns
        )

    def _compare_duration(self, ffduration: int, duration: int | None) -> bool:
        duration = duration or 0
        if not ffduration or not duration:
            return True
        epsilon = const.sources.cda.episode_duration_epsilon if self._is_episode else const.sources.cda.duration_epsilon
        return abs(ffduration - duration) <= ffduration * epsilon

    def _get_highest_quality_direct(self, video: Dict[str, Any]) -> str:
        """Return highest available quality based on video dimensions and variants."""
        height = video.get('height', 0)
        width = video.get('width', 0)

        available_qualities = set()
        for variant in video.get('qualities', []):
            if variant.get('name'):
                available_qualities.add(variant['name'])

        if self.debug:
            fflog(f"video dimensions: {width}x{height}, available: {available_qualities}")
        if height >= 1080 or width >= 1920:
            if '1080p' in available_qualities:
                return '1080p'
        elif height >= 720 or width >= 1280:
            if '720p' in available_qualities:
                return '720p'

        quality_priority = ['1080p', '720p', '480p', '360p']
        for quality in quality_priority:
            if quality in available_qualities:
                if self.debug:
                    fflog(f"selected quality by priority: {quality}")
                return quality

        fallback = video.get('quality', '480p')
        if self.debug:
            fflog(f"using fallback quality: {fallback}")
        return fallback

    # DRM / direct links
