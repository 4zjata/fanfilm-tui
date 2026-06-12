# -*- coding: utf-8 -*-
"""
FanFilm – source: onlyflix.to (via vidapi.xyz sub-players)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>

Flow:
  movie() / tvshow() / episode(): encode imdb + season/episode in URL string
  sources():
    1. GET https://vidapi.xyz/embed/{type}/{imdb}[/{season}/{episode}]
    2. Extract all data-src="https://stream.vidapi.xyz/{player}?..." sub-players
    3. Return each as source item
  resolve():
    Sub-player routing:
      vsrc         → vidsrc.me embed → data-hash → resolve_rcp (cloudnestra)
      vpls / vkng  → vsembed.ru embed → player_iframe rcp → resolve_vsembed
      swish        → lookmovie2.skin/e/{id} → unpack JW packer → hls4 m3u8
    Skipped (JS-only/CF-protected): vsrcc, xps
"""

from __future__ import annotations

import re
from html import unescape
from typing import TYPE_CHECKING, List, Optional, ClassVar
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from lib.ff import requests
from lib.ff.item import FFItem
from lib.ff.source_utils import DEFAULT_UA, FF_UA
from lib.ff.resolve_utils import resolve_rcp, resolve_vsembed, resolve_swish, build_isa_url
from lib.ff.log_utils import fflog, fflog_exc

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias

_BASE = 'https://onlyflix.to'
_VIDAPI_EMBED = 'https://vidapi.xyz/embed'

_HEADERS = {
    'User-Agent': FF_UA,
    'Referer': _BASE + '/',
}

# vsrcc requires browser-level JS/cookies; xps is CF-protected JS-only
_SKIP_PLAYERS = {'vsrcc', 'xps'}

_PLAYER_MAP = {
    'vsrc':  ('https://vidsrc.me/embed/movie/{imdb}',
              'https://vidsrc.me/embed/tv/{imdb}/{season}/{episode}'),
    'vpls':  ('https://vsembed.ru/embed/movie/{imdb}/',
              'https://vsembed.ru/embed/tv/{imdb}/{season}/{episode}/'),
    'vkng':  ('https://vsembed.ru/embed/movie/{imdb}/',
              'https://vsembed.ru/embed/tv/{imdb}/{season}/{episode}/'),
    'swish': (None, None),
}

# ─── source ──────────────────────────────────────────────────────────────────────

class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en']

    # ── public api ─────────────────────────────────────────────────────────

    def movie(self, imdb: str, title: str, localtitle: str, aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not imdb:
            return None
        return urlencode({'imdb': imdb})

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: 'list[SourceTitleAlias]', year: str) -> Optional[str]:
        if not imdb:
            return None
        return urlencode({'imdb': imdb})

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        if not url:
            return None
        data = {k: v[0] for k, v in parse_qs(url).items()}
        data.update({'season': season, 'episode': episode})
        return urlencode(data)

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> 'List[SourceItem]':
        result = []
        try:
            if not url:
                return result

            data = {k: v[0] for k, v in parse_qs(url).items()}
            imdb = data.get('imdb', '')
            if not imdb:
                return result

            season = data.get('season')
            episode = data.get('episode')
            is_episode = season is not None and episode is not None

            if is_episode:
                embed_url = f'{_VIDAPI_EMBED}/tv/{imdb}/{season}/{episode}'
            else:
                embed_url = f'{_VIDAPI_EMBED}/movie/{imdb}'

            fflog(f'query: {imdb!r}')
            resp = requests.get(embed_url, headers=_HEADERS, timeout=12)
            if not resp:
                return result
            html = resp.text

            subs = [unescape(u) for u in re.findall(r'data-src="(https://stream\.vidapi\.xyz/[^"]+)"', html)]
            # deduplicate while preserving order
            seen = set()
            unique_subs = []
            for s in subs:
                base = s.split('&ref=')[0]
                if base not in seen:
                    seen.add(base)
                    unique_subs.append(s)

            fflog(f'search: {len(unique_subs)} sub-players')

            for sub_url in unique_subs:
                parsed = urlparse(sub_url)
                player = parsed.path.lstrip('/')
                player_key = re.sub(r'-(tv|movie)$', '', player)
                if player_key in _SKIP_PLAYERS:
                    continue
                result.append({
                    'source': player,
                    'quality': '1080p',
                    'language': 'en',
                    'url': sub_url,
                    'info': '',
                    'filename': imdb,
                    'direct': False,
                    'debridonly': False,
                })
                fflog(f'onlyflix: {player} → {sub_url[:80]}')

        except Exception:
            fflog_exc()
        fflog(f'sources: {len(result)}')
        return result

    def resolve(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            if 'stream.vidapi.xyz' not in parsed.netloc:
                return url

            player = parsed.path.lstrip('/')
            player_key = re.sub(r'-(tv|movie)$', '', player)
            params = dict(parse_qsl(parsed.query))
            imdb = params.get('imdb', '')
            tmdb = params.get('tmdb', '')

            cfg = _PLAYER_MAP.get(player_key)

            # ── vidsrc-type players ─────────────────────────────────────────
            if cfg and cfg[0] and (imdb or tmdb):
                season = params.get('season') or params.get('s')
                episode = params.get('episode') or params.get('e')
                if season and episode:
                    canonical = cfg[1].format(imdb=imdb or tmdb, season=season, episode=episode)
                else:
                    canonical = cfg[0].format(imdb=imdb or tmdb)
                fflog(f'onlyflix resolve: {player} → {canonical}')
                if 'vsembed.ru' in canonical:
                    return resolve_vsembed(canonical)
                return self._resolve_vidsrc_style(canonical)

            # ── swish → lookmovie2.skin ──────────────────────────────────────
            if player_key == 'swish':
                swish_id = params.get('id', '')
                if swish_id:
                    return self._resolve_swish(swish_id)

        except Exception:
            fflog_exc()
        return None

    # ── helpers ────────────────────────────────────────────────────────────

    def _resolve_vidsrc_style(self, embed_url: str) -> Optional[str]:
        """Fetch embed page, find data-hash or rcp iframe, resolve via cloudnestra."""
        try:
            sess = requests.Session()
            sess.headers.update({'User-Agent': FF_UA})
            resp = sess.get(embed_url, timeout=15, allow_redirects=True)
            html = resp.text
            final_url = resp.url

            # vidsrc.me style: data-hash divs
            hashes = re.findall(r'data-hash="([^"]+)"', html)
            for h in hashes:
                try:
                    result = resolve_rcp(h, referer=final_url, _sess=sess)
                    if result:
                        fflog(f'onlyflix _resolve_vidsrc_style: rcp ok')
                        return result
                except Exception:
                    fflog_exc()

            # vsembed/cloudnestra style: player_iframe rcp src
            m = re.search(r'id="player_iframe"[^>]+src="//cloudnestra\.com/rcp/([^"]+)"', html)
            if m:
                result = resolve_rcp(m.group(1), referer=embed_url, _sess=sess)
                if result:
                    fflog(f'onlyflix _resolve_vidsrc_style: cloudnestra rcp ok')
                    return result

            fflog(f'onlyflix _resolve_vidsrc_style: no hash/rcp in {embed_url[:60]}')
        except Exception:
            fflog_exc()
        return None

    def _resolve_swish(self, swish_id: str) -> Optional[str]:
        return resolve_swish(swish_id, referer='https://stream.vidapi.xyz/')
