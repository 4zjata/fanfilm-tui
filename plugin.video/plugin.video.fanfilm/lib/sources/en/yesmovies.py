# -*- coding: utf-8 -*-
"""
FanFilm - źródło: ww2.yesmovies.ag
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  sources() looks up the numeric mid for the title via /searching, then for each
  server (sv=1 direct, sv=5 vidara embed) — in parallel:
    1. PBKDF2-HMAC-SHA256(b"player", salt, 1000) → AES-GCM(mid+eid+sv+ts)
    2. GET ployan.me/get/<salt-iv-ct> → JSON {info, mode}
    3. mode=direct: m3u8 = ployan.me/hls/<info>/master.m3u8 (info verbatim)
       mode=embed:  decrypt info → "<vidara_id>-<ts>" → POST vidara.to/api/stream
    4. probe the master playlist (RESOLUTION= or H.264 SPS) to label real quality
  resolve() is a no-op — sources() returns ready-to-play HLS URLs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import TYPE_CHECKING, ClassVar, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin

from pyaes.aes import AES  # lib/3rd/pyaes — pure-Python AES (no GCM)

from lib.ff import cleantitle, requests
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.source_utils import FF_UA, quality_from_resolution

if TYPE_CHECKING:
    from lib.ff.item import FFItem
    from lib.sources import SourceItem, SourceTitleAlias


_BASE = 'https://ww2.yesmovies.ag'
_PLAYER = 'https://ployan.me'
_VIDARA = 'https://vidara.to'

_HEADERS = {'User-Agent': FF_UA, 'Referer': _BASE + '/'}
_PLAYER_HEADERS = {'User-Agent': FF_UA, 'Referer': _PLAYER + '/', 'Origin': _PLAYER}
_VIDARA_HEADERS = {'User-Agent': FF_UA, 'Referer': _VIDARA + '/'}

# sv=1 → direct HLS (ployan.me CDN), sv=5 → vidara.to embed; sv=2 frequently 404.
_SERVERS: Tuple[int, ...] = (1, 5)

# PBKDF2 password captured from the obfuscated player at ployan.me/watch/
_PWD = b'player'

_SLUG_MID_RE = re.compile(r'-(\d+)$')
_TV_SEASON_RE = re.compile(r'\s*-\s*Season\s+\d+\s*$', re.I)
_RE_HLS_RESOLUTION = re.compile(r'RESOLUTION=(\d+)x(\d+)')
_RE_HLS_SEGMENT = re.compile(r'^(?!#)(\S+\.ts[^\n\r]*)', re.M)


# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: 'FFItem'

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    def __init__(self):
        self.domains = ['yesmovies.ag', 'ployan.me', 'vidara.to']

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str,
              aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        try:
            return urlencode({'title': title, 'year': year, 'type': 'm'})
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str,
               aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        try:
            return urlencode({'title': tvshowtitle, 'year': year, 'type': 's'})
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str,
                premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if url is None:
                return None
            data = {k: v[0] for k, v in parse_qs(url).items()}
            data.update({'season': season, 'episode': episode})
            return urlencode(data)
        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[str], hostDict: List[str],
                hostprDict: List[str]) -> 'List[SourceItem]':
        result: List[dict] = []
        try:
            if not url:
                return result

            data = {k: v[0] for k, v in parse_qs(url).items()}
            title = data.get('title', '').strip()
            year = data.get('year', '').strip()
            is_tv = data.get('type') == 's'
            season = data.get('season')

            if not title:
                return result

            fflog(f'query: {title!r} ({year}){" S"+season if is_tv and season else ""}')
            mid = self._find_mid(title, year, is_tv, season)
            if not mid:
                return result

            eid = int(data.get('episode') or 1) if is_tv else 1
            lock = threading.Lock()
            threads = [
                threading.Thread(
                    target=self._resolve_server,
                    args=(mid, eid, sv, result, lock),
                    daemon=True,
                )
                for sv in _SERVERS
            ]
            for t in threads: t.start()
            for t in threads: t.join(timeout=20)

        except Exception:
            fflog_exc()
        fflog(f'sources: {len(result)}')
        return result

    def resolve(self, url: str) -> Optional[str]:
        # sources() resolves vidara → streaming_url upfront so we can probe quality,
        # so every URL handed to resolve() is already a ready-to-play HLS playlist.
        return url

    # ── helpers ────────────────────────────────────────────────────────────

    def _find_mid(self, title: str, year: str, is_tv: bool,
                  season: Optional[str]) -> Optional[str]:
        try:
            resp = requests.get(
                f'{_BASE}/searching?q={quote_plus(title)}&limit=50&offset=0',
                headers=_HEADERS,
                timeout=15,
            )
            results = resp.json().get('data') or []
            want_kind = 's' if is_tv else 'm'
            clean = cleantitle.get(title)

            for r in results:
                if r.get('d') != want_kind:
                    continue
                slug = r.get('s') or ''
                m = _SLUG_MID_RE.search(slug)
                if not m:
                    continue

                cand_title = _TV_SEASON_RE.sub('', r.get('t', ''))
                if cleantitle.get(cand_title) != clean:
                    continue
                if not is_tv and year and str(r.get('y') or '') != year:
                    continue
                if is_tv and season and int(season) != 1:
                    if f'season-{season}' not in slug.lower():
                        continue
                return m.group(1)
            return None
        except Exception:
            fflog_exc()
            return None

    def _resolve_server(self, mid: str, eid: int, sv: int,
                        out: List[dict], lock: threading.Lock) -> None:
        try:
            ts = int(time.time())
            token = self._encrypt(f'{mid}+{eid}+{sv}+{ts}'.encode())
            resp = requests.get(f'{_PLAYER}/get/{token}', headers=_PLAYER_HEADERS, timeout=15)
            if resp.status_code != 200:
                return
            data = resp.json()
            if data.get('code') != 200:
                return

            info = data.get('info') or ''
            mode = data.get('mode')

            if mode == 'direct':
                m3u8 = f'{_PLAYER}/hls/{info}/master.m3u8'
                quality = self._probe_m3u8_quality(m3u8, _PLAYER_HEADERS)
                if quality:
                    self._add(out, lock, 'yesmovies-direct', quality, m3u8, mid)
                return

            if mode == 'embed':
                try:
                    embed_id = self._decrypt(info).decode().rsplit('-', 1)[0]
                except Exception:
                    return
                resolved = self._resolve_vidara(embed_id)
                if not resolved:
                    return
                quality = self._probe_m3u8_quality(resolved, _VIDARA_HEADERS)
                if quality:
                    self._add(out, lock, 'vidara', quality, resolved, mid)
        except Exception:
            fflog_exc()

    def _resolve_vidara(self, filecode: str) -> Optional[str]:
        try:
            resp = requests.post(
                f'{_VIDARA}/api/stream',
                data=json.dumps({'filecode': filecode}).encode(),
                headers={
                    'User-Agent': FF_UA,
                    'Referer': f'{_VIDARA}/e/{filecode}',
                    'Origin': _VIDARA,
                    'Content-Type': 'application/json',
                },
                timeout=15,
            )
            return resp.json().get('streaming_url') or None
        except Exception:
            fflog_exc()
            return None

    @staticmethod
    def _add(out: List[dict], lock: threading.Lock, host: str,
             quality: str, url: str, mid: str) -> None:
        with lock:
            out.append({
                'source': host,
                'quality': quality,
                'language': 'en',
                'url': url,
                'info': '',
                'filename': mid,
                'direct': True,
                'debridonly': False,
            })

    @staticmethod
    def _aes_enc(key: bytes, block: bytes) -> bytes:
        return bytes(AES(key).encrypt(block))

    @staticmethod
    def _gf_mul(x: int, y: int) -> int:
        R = 0xe1 << 120
        z = 0
        v = y
        for i in range(128):
            if (x >> (127 - i)) & 1:
                z ^= v
            v = (v >> 1) ^ R if v & 1 else v >> 1
        return z

    @staticmethod
    def _ghash(h: bytes, data: bytes) -> bytes:
        H = int.from_bytes(h, 'big')
        y = 0
        for i in range(0, len(data), 16):
            y = source._gf_mul(y ^ int.from_bytes(data[i:i + 16].ljust(16, b'\0'), 'big'), H)
        return y.to_bytes(16, 'big')

    @staticmethod
    def _aesgcm_ctr(key: bytes, j0: bytes, data: bytes) -> bytes:
        counter = int.from_bytes(j0, 'big')
        out = bytearray()
        for i in range(0, len(data), 16):
            counter = (counter & 0xffffffffffffffffffffffff00000000) | (((counter & 0xffffffff) + 1) & 0xffffffff)
            ks = source._aes_enc(key, counter.to_bytes(16, 'big'))
            out.extend(b ^ k for b, k in zip(data[i:i + 16], ks))
        return bytes(out)

    @staticmethod
    def _aesgcm_auth(key: bytes, j0: bytes, ct: bytes) -> bytes:
        h = source._aes_enc(key, b'\0' * 16)
        pad = ct + b'\0' * ((-len(ct)) % 16)
        length_block = b'\0' * 8 + (len(ct) * 8).to_bytes(8, 'big')
        s = source._ghash(h, pad + length_block)
        return bytes(a ^ b for a, b in zip(s, source._aes_enc(key, j0)))

    @staticmethod
    def _aesgcm_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        j0 = iv + b'\0\0\0\1'
        ct = source._aesgcm_ctr(key, j0, plaintext)
        return ct + source._aesgcm_auth(key, j0, ct)

    @staticmethod
    def _aesgcm_decrypt(key: bytes, iv: bytes, ct_and_tag: bytes) -> bytes:
        if len(ct_and_tag) < 16:
            raise ValueError('GCM ciphertext too short')
        ct, tag = ct_and_tag[:-16], ct_and_tag[-16:]
        j0 = iv + b'\0\0\0\1'
        if source._aesgcm_auth(key, j0, ct) != tag:
            raise ValueError('GCM tag mismatch')
        return source._aesgcm_ctr(key, j0, ct)

    @staticmethod
    def _encrypt(plaintext: bytes) -> str:
        salt, iv = os.urandom(8), os.urandom(12)
        key = hashlib.pbkdf2_hmac('sha256', _PWD, salt, 1000, 32)
        return f'{salt.hex()}-{iv.hex()}-{source._aesgcm_encrypt(key, iv, plaintext).hex()}'

    @staticmethod
    def _decrypt(token: str) -> bytes:
        salt, iv, ct = (bytes.fromhex(x) for x in token.split('-'))
        key = hashlib.pbkdf2_hmac('sha256', _PWD, salt, 1000, 32)
        return source._aesgcm_decrypt(key, iv, ct)

    @staticmethod
    def _parse_h264_sps(sps: bytes) -> Optional[Tuple[int, int]]:
        try:
            raw = bytearray()
            i = 0
            while i < len(sps):
                if i + 2 < len(sps) and sps[i] == 0 and sps[i + 1] == 0 and sps[i + 2] == 3:
                    raw.extend(sps[i:i + 2]); i += 3
                else:
                    raw.append(sps[i]); i += 1
            buf = bytes(raw)

            class BR:
                __slots__ = ('b', 'i')
                def __init__(s, b): s.b = b; s.i = 0
                def u(s, n):
                    v = 0
                    for _ in range(n):
                        v = (v << 1) | ((s.b[s.i >> 3] >> (7 - (s.i & 7))) & 1)
                        s.i += 1
                    return v
                def ue(s):
                    z = 0
                    while s.u(1) == 0:
                        z += 1
                        if z > 32: return 0
                    return (1 << z) - 1 + (s.u(z) if z else 0)
                def se(s):
                    v = s.ue()
                    return (v + 1) >> 1 if v & 1 else -(v >> 1)

            br = BR(buf)
            profile_idc = br.u(8); br.u(8); br.u(8); br.ue()
            chroma_idc = 1
            if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
                chroma_idc = br.ue()
                if chroma_idc == 3: br.u(1)
                br.ue(); br.ue(); br.u(1)
                if br.u(1):
                    for k in range(8 if chroma_idc != 3 else 12):
                        if br.u(1):
                            last = nxt = 8
                            for _ in range(16 if k < 6 else 64):
                                if nxt:
                                    nxt = (last + br.se() + 256) % 256
                                last = nxt or last
            br.ue()
            poc = br.ue()
            if poc == 0:
                br.ue()
            elif poc == 1:
                br.u(1); br.se(); br.se()
                for _ in range(br.ue()): br.se()
            br.ue(); br.u(1)
            w_mbs = br.ue() + 1
            h_map = br.ue() + 1
            frame_only = br.u(1)
            if not frame_only: br.u(1)
            br.u(1)
            crop_l = crop_r = crop_t = crop_b = 0
            if br.u(1):
                crop_l = br.ue(); crop_r = br.ue()
                crop_t = br.ue(); crop_b = br.ue()
            width = w_mbs * 16
            height = (2 - frame_only) * h_map * 16
            sub_w = 1 if chroma_idc in (0, 3) else 2
            sub_h = 1 if (chroma_idc == 1 and frame_only) else (2 if chroma_idc == 1 else 1)
            width -= sub_w * (crop_l + crop_r)
            height -= sub_h * (2 - frame_only) * (crop_t + crop_b)
            return width, height
        except Exception:
            return None

    @staticmethod
    def _probe_segment_quality(seg_uri: str, headers: dict) -> Optional[str]:
        try:
            r = requests.get(seg_uri, headers={**headers, 'Range': 'bytes=0-65535'}, timeout=5)
            if not r.ok:
                return None
            data = r.content
            for sc in (b'\x00\x00\x00\x01\x67', b'\x00\x00\x01\x67'):
                idx = data.find(sc)
                if idx >= 0:
                    start = idx + len(sc)
                    break
            else:
                return None
            sps_end = data.find(b'\x00\x00\x00\x01', start)
            if sps_end < 0:
                sps_end = min(start + 64, len(data))
            wh = source._parse_h264_sps(data[start:sps_end])
            return quality_from_resolution(*wh) if wh else None
        except Exception:
            return None

    @staticmethod
    def _probe_m3u8_quality(m3u8_url: str, headers: dict) -> Optional[str]:
        try:
            r = requests.get(m3u8_url, headers=headers, timeout=4)
            if not r.ok:
                return None
            sizes = [(int(w), int(h)) for w, h in _RE_HLS_RESOLUTION.findall(r.text)]
            if sizes:
                best = max(sizes, key=lambda wh: wh[0] * wh[1])
                return quality_from_resolution(*best)
            m = _RE_HLS_SEGMENT.search(r.text)
            if not m:
                return None
            seg_uri = m.group(1).strip()
            if not seg_uri.startswith('http'):
                seg_uri = urljoin(m3u8_url, seg_uri)
            return source._probe_segment_quality(seg_uri, headers)
        except Exception:
            return None
