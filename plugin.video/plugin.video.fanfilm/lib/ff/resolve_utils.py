# -*- coding: utf-8 -*-
"""
Shared embed resolvers used by FanFilm scrapers.

  URL builders (used by any scraper):
    build_isa_url(url, referer, origin, ua, cookie)  → isa+url|headers
    build_drmff(manifest, widevine_url, lic_referer, ua)  → DRMFF|{repr(adaptive_data)}

  Full resolvers:
    resolve_megacloud(embed_url)   → (isa+url|headers, tracks) or None
      Players: streameeeeee.site, videostr.net (Megacloud/Vidcloud family)
      Flow: GET embed → file_id + nonce → getSources?id=&_k= → decrypt → m3u8

    resolve_vsembed(embed_url)     → url|headers or None
      Flow: GET vsembed.ru/embed/... → rcp hash → prorcp hash → CDN m3u8

    resolve_rcp(rcp_hash, referer) → url|headers or None
      Use when rcp hash is already known (e.g. from vidsrc.me data-hash).
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from random import shuffle
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlparse

from lib.ff import cache, client, control, requests
from lib.ff.source_utils import DEFAULT_UA, FF_UA
from lib.ff.log_utils import fflog, fflog_exc


# ══════════════════════════════════════════════════════════════════════════════
# URL builders
# ══════════════════════════════════════════════════════════════════════════════

def build_isa_url(url: str, referer: str, origin: str = None,
                  ua: str = DEFAULT_UA, cookie: str = None) -> str:
    """Build isa+url|headers string for ISA/HLS playback.

    Header order: Referer, Origin, User-Agent, Cookie.
    Pass origin=None to omit it (e.g. when only Referer is needed).
    Pass ua=None to omit User-Agent.
    """
    parts = [f"Referer={referer}"]
    if origin:
        parts.append(f"Origin={origin}")
    if ua:
        parts.append(f"User-Agent={ua}")
    if cookie:
        parts.append(f"Cookie={cookie}")
    return f"isa+{url}|{'&'.join(parts)}"


def build_drmff(manifest: str, widevine_url: str = None,
                lic_referer: str = None, ua: str = DEFAULT_UA) -> str:
    """Build DRMFF|{repr(adaptive_data)} string for MPD/DASH streams.

    Used by scrapers that return Ninateka-style DASH+Widevine content.
    With widevine_url=None returns an unencrypted DASH manifest entry.
    """
    adaptive_data: Dict = {
        'protocol': 'mpd',
        'mimetype': 'application/dash+xml',
        'manifest': manifest,
        'licence_type': '',
        'licence_url': '',
        'licence_header': '',
        'post_data': '',
        'response_data': '',
    }
    if widevine_url:
        lic_headers = {'User-Agent': ua, 'Referer': lic_referer or '', 'Content-Type': ''}
        adaptive_data['licence_type'] = 'com.widevine.alpha'
        adaptive_data['licence_url'] = widevine_url
        adaptive_data['licence_header'] = urlencode(lic_headers)
        adaptive_data['post_data'] = 'R{SSM}'
    return f"DRMFF|{repr(adaptive_data)}"


# ══════════════════════════════════════════════════════════════════════════════
# Free-proxy IP rotation — shared bypass for per-IP rate-limits / quota captchas
# ══════════════════════════════════════════════════════════════════════════════

_PROXY_LIST_SOURCES = (
    'https://api.proxyscrape.com/v4/free-proxy-list/get'
    '?request=display_proxies&protocol=http&proxy_format=ipport&format=text',
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
)
_PROXY_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$')


def get_free_proxies(max_age: int = 1800) -> List[str]:
    """Return a pool of free HTTP proxies as 'ip:port' strings.

    Fetched from public lists and cached (~30 min) so repeated calls within a
    session are cheap. Returns an empty list if nothing could be fetched.
    """
    row = cache.cache_get('ff_free_proxies', control.providercacheFile)
    if row:
        try:
            if row['value'] and (time.time() - row['date']) < max_age:
                return [p for p in row['value'].split(',') if p]
        except (KeyError, IndexError, TypeError):
            pass

    proxies: List[str] = []
    for url in _PROXY_LIST_SOURCES:
        try:
            resp = requests.Session().get(url, timeout=15)
            if not resp:
                continue
            proxies = [ln.strip() for ln in resp.text.splitlines() if _PROXY_RE.match(ln.strip())]
            if proxies:
                break
        except Exception:
            continue

    if proxies:
        cache.cache_insert('ff_free_proxies', ','.join(proxies), control.providercacheFile)
    else:
        fflog('get_free_proxies: could not fetch any proxy list')
    return proxies


def request_with_proxy_rotation(attempt, *, attempts: int = 40, parallel: int = 20):
    """Retry ``attempt`` through rotating free proxies until it succeeds.

    ``attempt(proxies)`` receives a dict ready for requests' ``proxies=`` kwarg
    ({'http': 'http://ip:port', 'https': 'http://ip:port'}) and must return the
    desired result on success, or ``None`` if that proxy was blocked /
    rate-limited / dead (the helper then moves on to the next one).

    Strategy: try the last known-good proxy first, then a shuffled pool of free
    proxies in parallel batches. The first non-``None`` result wins and its
    proxy is remembered for next time. Returns that result, or ``None`` if
    every attempt failed.

    Generic bypass for *per-IP* gates only — does NOT help against full-site
    Cloudflare challenges (those need a `cf_clearance` cookie).
    """
    def _run(proxy):
        try:
            return proxy, attempt({'http': f'http://{proxy}', 'https': f'http://{proxy}'})
        except Exception:
            return proxy, None

    last_good = cache.cache_value('ff_proxy_last_good', control.providercacheFile, default='')
    if last_good:
        _, result = _run(last_good)
        if result is not None:
            fflog(f'proxy rotation: reused last-good proxy {last_good}')
            return result

    pool = [p for p in get_free_proxies() if p != last_good]
    if not pool:
        fflog('proxy rotation: no free proxies available')
        return None
    shuffle(pool)
    pool = pool[:attempts]

    ex = ThreadPoolExecutor(max_workers=parallel)
    try:
        futures = [ex.submit(_run, p) for p in pool]
        for fut in as_completed(futures):
            proxy, result = fut.result()
            if result is not None:
                for f in futures:
                    f.cancel()
                cache.cache_insert('ff_proxy_last_good', proxy, control.providercacheFile)
                fflog(f'proxy rotation: success via {proxy}')
                return result
    finally:
        ex.shutdown(wait=False)

    fflog(f'proxy rotation: all {len(pool)} proxies failed')
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Megacloud / Vidcloud
# ══════════════════════════════════════════════════════════════════════════════

_MC_CHARSET = [chr(i + 32) for i in range(95)]
_MC_SECRET = 'nTAygRRNLS3wo82OtMyfPrWgD9K2UIvcwlj'


def resolve_megacloud(embed_url: str) -> Optional[Tuple[str, List[Dict]]]:
    """Resolve a Megacloud/Vidcloud embed URL.

    Returns (isa+url|headers, tracks) or None on failure.
    tracks is the raw 'tracks' list from getSources (subtitles/chapters).
    """
    try:
        parsed_url = urlparse(embed_url)
        embed_origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        headers = {
            'User-Agent': DEFAULT_UA,
            'Referer': embed_origin + '/',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': '*/*',
        }

        result = client.request(embed_url, headers=headers, output='extended')
        if not result:
            return None
        html, _resp_headers, embed_cookies = result

        m = re.search(r'id="[^"]*-player"[^>]*data-id="([^"]+)"', html)
        if not m:
            m = re.search(r'data-id="([^"]+)"[^>]*id="[^"]*-player"', html)
        if not m:
            fflog(f"megacloud: no player element in {embed_url[:60]}")
            return None
        file_id = m.group(1)

        nm = re.search(r'\b([a-zA-Z0-9]{48})\b', html)
        if nm:
            nonce = nm.group(1)
        else:
            nm3 = re.search(
                r'\b([a-zA-Z0-9]{16})\b.{0,10}\b([a-zA-Z0-9]{16})\b.{0,10}\b([a-zA-Z0-9]{16})\b',
                html,
            )
            if not nm3:
                fflog(f"megacloud: no nonce found (html={len(html)}b)")
                return None
            nonce = ''.join(nm3.groups())

        get_sources_url = f"{embed_origin}/embed-1/v3/e-1/getSources?id={file_id}&_k={nonce}"
        fflog(f"megacloud: getSources → {get_sources_url}")

        sources_result = client.request(
            get_sources_url, headers=headers,
            cookie=embed_cookies or None,
            output='extended',
        )
        if not sources_result:
            return None
        sources_data, _src_headers, src_cookies = sources_result

        if not sources_data:
            return None

        jd = json.loads(sources_data)

        if jd.get('encrypted'):
            decrypted = _mc_decrypt(jd['sources'], nonce)
            fflog(f"megacloud: decrypted → {decrypted[:120]}")
            try:
                video_url = json.loads(decrypted)[0]['file']
            except Exception:
                m2 = re.search(r'"file"\s*:\s*"((?:[^"\\]|\\.)*)"', decrypted)
                if not m2:
                    return None
                video_url = m2.group(1).replace('\\/', '/')
        else:
            sources_arr = jd.get('sources', [])
            if not sources_arr:
                return None
            video_url = sources_arr[0].get('file', '')

        if not video_url:
            return None

        all_cookies = '; '.join(c for c in [embed_cookies, src_cookies] if c)
        fflog(f"megacloud: resolved → {video_url[:80]}")
        return build_isa_url(video_url, f"{embed_origin}/", embed_origin,
                             cookie=all_cookies or None), jd.get('tracks', [])

    except Exception:
        fflog_exc()
        return None


def _mc_deterministic_shuffle(arr, seed):
    state = 0
    for c in seed:
        state = (state * 31 + ord(c)) & 0xFFFFFFFF
    result = list(arr)
    for i in range(len(result) - 1, 0, -1):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        j = state % (i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def _mc_columnar_transposition(input_str, key):
    cols = len(key)
    rows = math.ceil(len(input_str) / cols)
    grid = [[''] * cols for _ in range(rows)]
    sorted_indices = sorted(range(cols), key=lambda i: ord(key[i]))
    curr = 0
    for idx in sorted_indices:
        for r in range(rows):
            if curr < len(input_str):
                grid[r][idx] = input_str[curr]
                curr += 1
    result = ''
    for r in range(rows):
        for c in range(cols):
            result += grid[r][c]
    return result


def _mc_generate_key(nonce):
    combined = _MC_SECRET + nonce
    hash_val = 0
    for c in combined:
        hash_val = ord(c) + hash_val * 31 + (hash_val << 7) - hash_val
    if hash_val < 0:
        hash_val = -hash_val
    mod_hash = hash_val % 0x7FFFFFFFFFFFFFFF
    xored = ''.join(chr(ord(c) ^ (13886967 & 0xFF)) for c in combined)
    shift = (mod_hash % len(xored)) + 5
    xored = xored[shift:] + xored[:shift]
    rev_nonce = nonce[::-1]
    merged = ''
    for i in range(max(len(xored), len(rev_nonce))):
        merged += (xored[i] if i < len(xored) else '') + (rev_nonce[i] if i < len(rev_nonce) else '')
    key_len = 96 + (mod_hash % 33)
    return ''.join(chr((ord(c) % 95) + 32) for c in merged[:key_len])


def _mc_decrypt(encrypted_data, nonce, iterations=3):
    secret_key = _mc_generate_key(nonce)
    decoded = base64.b64decode(encrypted_data).decode()
    charset = list(_MC_CHARSET)
    for round_num in range(iterations, 0, -1):
        key = secret_key + str(round_num)
        state = 0
        for c in key:
            state = (state * 31 + ord(c)) & 0xFFFFFFFF

        def next_rand(s=[state]):
            s[0] = (s[0] * 1103515245 + 12345) & 0x7FFFFFFF
            return s[0] % 95

        _mc_deterministic_shuffle(charset, key)

        output = ''
        for char in decoded:
            if char not in charset:
                output += char
                continue
            idx = charset.index(char)
            r = next_rand()
            output += charset[(idx - r + 95) % 95]

        output = _mc_columnar_transposition(output, key)
        shuffled = _mc_deterministic_shuffle(charset, key)
        mapping = {shuffled[i]: charset[i] for i in range(len(charset))}
        decoded = ''.join(mapping.get(c, c) for c in output)

    return decoded


# ══════════════════════════════════════════════════════════════════════════════
# Cloudnestra / vsembed
# ══════════════════════════════════════════════════════════════════════════════

_CN_CDN_DOMAINS: List[str] = [
    "neonhorizonworkshops.com",
    "wanderlynest.com",
    "orchidpixelgardens.com",
    "cloudnestra.com",
]


def resolve_vsembed(embed_url: str) -> Optional[str]:
    """Resolve vsembed.ru/embed/... → direct HLS m3u8 URL|headers string."""
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": FF_UA})

        resp = sess.get(embed_url, timeout=15, headers={"Referer": "https://vsembed.ru/"})
        iframe_m = re.search(r'id="player_iframe" src="//cloudnestra\.com/rcp/([^"]+)"', resp.text)
        if not iframe_m:
            fflog(f"cloudnestra: no rcp hash in {embed_url}")
            return None
        return resolve_rcp(iframe_m.group(1), referer=embed_url, _sess=sess)

    except Exception:
        fflog_exc()
        return None


def resolve_rcp(rcp_hash: str, referer: str = "https://cloudnestra.com/",
                _sess=None, entry: str = "rcp") -> Optional[str]:
    """Resolve a cloudnestra rcp/rcpvip hash → HLS m3u8 URL|headers string.

    `entry` selects the first-stage path: "rcp" (default, used by vidsrc.me /
    vsembed.ru) or "rcpvip" (used by vidsrc-embed.ru). Both flow into the
    same prorcp → CDN chain. cloudnestra Turnstile-gates rcp per-IP; on
    direct failure the whole chain is retried via rotating free proxies.
    """
    result = _resolve_rcp_attempt(rcp_hash, referer, entry, _sess)
    if result is None:
        fflog("cloudnestra: direct attempt failed, rotating through free proxies")
        result = request_with_proxy_rotation(
            lambda proxies: _resolve_rcp_attempt(rcp_hash, referer, entry, None, proxies))
    return result


def _resolve_rcp_attempt(rcp_hash: str, referer: str, entry: str,
                         _sess=None, proxies: Optional[Dict[str, str]] = None) -> Optional[str]:
    """One full rcp → prorcp → CDN attempt, optionally routed via a proxy."""
    try:
        if proxies:
            sess = requests.Session()
            sess.headers.update({"User-Agent": FF_UA})
            sess.proxies.update(proxies)
        else:
            sess = _sess or requests.Session()
            if not _sess:
                sess.headers.update({"User-Agent": FF_UA})

        resp2 = sess.get(
            f"https://cloudnestra.com/{entry}/{rcp_hash}", timeout=15,
            headers={"Referer": referer},
        )
        prorcp_m = re.search(r"src: '/prorcp/([^']+)'", resp2.text)
        if not prorcp_m:
            if not proxies:
                fflog(f"cloudnestra: no prorcp hash for {entry}/{rcp_hash[:20]}")
            return None
        prorcp_hash = prorcp_m.group(1)

        resp3 = sess.get(
            f"https://cloudnestra.com/prorcp/{prorcp_hash}", timeout=15,
            headers={"Referer": f"https://cloudnestra.com/{entry}/{rcp_hash}"},
        )
        content = resp3.text

        pass_m = re.search(r'pass_path = "//([^/]+)/rt_ping\.php"', content)
        tmstr_prefix = pass_m.group(1).split(".")[0] if pass_m else "tmstr5"

        path_m = re.search(r'https://[^.]+\.\{v\d+\}/pl/(H4sI[^ "]+)/master\.m3u8', content)
        if not path_m:
            if not proxies:
                fflog("cloudnestra: no m3u8 path in prorcp page")
            return None
        m3u8_path = path_m.group(1)

        for cdn in _CN_CDN_DOMAINS:
            m3u8_url = f"https://{tmstr_prefix}.{cdn}/pl/{m3u8_path}/master.m3u8"
            try:
                r = sess.get(m3u8_url, timeout=10, headers={"Referer": "https://cloudnestra.com/"})
                if r.status_code == 200 and b"#EXTM3U" in r.content[:20]:
                    fflog(f"cloudnestra: resolved via {cdn}")
                    return (
                        f"{m3u8_url}|User-Agent={quote(FF_UA)}"
                        f"&Referer=https%3A%2F%2Fcloudnestra.com%2F"
                    )
            except Exception:
                pass

        if not proxies:
            fflog("cloudnestra: all CDN domains failed")
        return None

    except Exception:
        if not proxies:
            fflog_exc()
        return None


def _lm2_extract_m3u8(html: str) -> Optional[str]:
    """Decode JW packer from lookmovie2.skin page and return best stream URL.

    hls3 is preferred: real MPEG-TS segments (served as .woff2 but video/MP2T).
    hls4 is skipped: its segments are anti-leech fake PNGs for non-browser clients.
    hls2 is fallback: external CDN, often 403 without browser session.
    """
    m = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',\d+,\d+,'(.*?)'\.split\('\|'\)\)\)",
        html, re.S
    )
    if not m:
        return None
    packed, keys_str = m.group(1), m.group(2)
    k = keys_str.split('|')

    def decode_token(mo):
        tok = mo.group(0)
        idx = int(tok, 36) if tok else 0
        return k[idx] if idx < len(k) and k[idx] else tok

    decoded = re.sub(r'\b[0-9a-z]+\b', decode_token, packed)

    hls3 = re.search(r'"hls3"\s*:\s*"(https?://[^"]+)"', decoded)
    if hls3:
        return hls3.group(1)
    hls2 = re.search(r'"hls2"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', decoded)
    if hls2:
        return hls2.group(1)
    return None


def resolve_swish(swish_id: str, referer: str = "https://stream.vidapi.xyz/") -> Optional[str]:
    """Resolve a swish player ID (used by 2embed.cc, onlyflix.to) → ISA HLS URL.

    Fetches lookmovie2.skin/e/{id}, unpacks JW packer, extracts hls3/hls2.
    """
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": FF_UA, "Referer": referer})
        embed_url = f"https://lookmovie2.skin/e/{swish_id}"
        resp = sess.get(embed_url, timeout=15)
        m3u8 = _lm2_extract_m3u8(resp.text)
        if not m3u8:
            fflog(f"swish: no m3u8 for {swish_id}")
            return None
        return build_isa_url(m3u8, "https://lookmovie2.skin/", ua=FF_UA)
    except Exception:
        fflog_exc()
        return None
