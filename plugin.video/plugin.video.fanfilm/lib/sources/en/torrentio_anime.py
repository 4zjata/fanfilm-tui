# -*- coding: utf-8 -*-
"""
FanFilm - źródło: Torrentio Anime (Stremio Addon API)
Copyright (C) 2026 :)

Dystrybuowane na licencji MIT <https://mit-license.org>
"""

from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING, ClassVar, List, Optional

if TYPE_CHECKING:
    from lib.sources import SourceItem, SourceTitleAlias
    from lib.ff.item import FFItem

from lib.ff import requests, source_utils
from lib.ff.source_utils import FF_UA
from lib.ff.log_utils import fflog, fflog_exc
from lib.ff.settings import settings
from lib.api import kitsu as kitsu_api


class source:
    ffitem: FFItem

    priority: ClassVar[int] = 1
    language: ClassVar[List[str]] = ['en', 'pl']

    def __init__(self):
        pass

    def movie(self, imdb: str, title: str, localtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not source_utils.is_anime(self.ffitem):
                return None
            
            kitsu_id = None
            tmdb_id = getattr(self.ffitem, 'tmdb_id', '')
            
            # 1. Try to get kitsu ID using TMDB ID
            if tmdb_id:
                try:
                    entries = kitsu_api._get_kitsu_ids(str(tmdb_id), 'movie')
                    if entries:
                        kitsu_id = entries[0][0]
                        fflog(f'[Torrentio Anime] Resolved movie via kitsu_api: TMDB ID {tmdb_id} -> Kitsu ID {kitsu_id}')
                except Exception as e:
                    fflog(f'[Torrentio Anime] Local kitsu movie lookup failed: {e}')

            # 2. Try Ani.zip mappings by IMDb ID
            if not kitsu_id and imdb:
                try:
                    url = f"https://api.ani.zip/mappings?imdb_id={imdb}"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        kitsu_id = data.get("mappings", {}).get("kitsu_id")
                        if kitsu_id:
                            fflog(f'[Torrentio Anime] Resolved movie via Ani.zip: IMDb ID {imdb} -> Kitsu ID {kitsu_id}')
                except Exception as e:
                    fflog(f'[Torrentio Anime] Ani.zip movie lookup error: {e}')

            # 3. Fallback to Kitsu Search by title
            if not kitsu_id:
                try:
                    search_url = "https://kitsu.io/api/edge/anime"
                    params = {"filter[text]": title, "filter[subtype]": "movie", "page[limit]": 5}
                    resp = requests.get(search_url, params=params, timeout=10)
                    if resp.status_code == 200:
                        results = resp.json().get("data", [])
                        if results:
                            kitsu_id = results[0].get("id")
                            fflog(f'[Torrentio Anime] Resolved movie via Kitsu search: Title "{title}" -> Kitsu ID {kitsu_id}')
                except Exception as e:
                    fflog(f'[Torrentio Anime] Kitsu movie search error: {e}')

            if not kitsu_id:
                fflog(f'[Torrentio Anime] Could not resolve movie: Title="{title}", IMDb={imdb}')
                return None

            return urllib.parse.urlencode({'kitsu_id': kitsu_id, 'type': 'movie'})
        except Exception:
            fflog_exc()
            return None

    def tvshow(self, imdb: str, tvdb: str, tvshowtitle: str, localtvshowtitle: str, aliases: list[SourceTitleAlias], year: str) -> Optional[str]:
        try:
            if not source_utils.is_anime(self.ffitem):
                return None
            return urllib.parse.urlencode({'tvshowtitle': tvshowtitle, 'imdb': imdb, 'tvdb': tvdb})
        except Exception:
            fflog_exc()
            return None

    def episode(self, url: Optional[str], imdb: str, tvdb: str, title: str, premiered: str, season: str, episode: str) -> Optional[str]:
        try:
            if not url:
                return None
            params = {k: v[0] for k, v in urllib.parse.parse_qs(url).items()}
            tvshowtitle = params.get('tvshowtitle', '')
            imdb_id = params.get('imdb', imdb)
            tvdb_id = params.get('tvdb', tvdb)

            kitsu_id = None
            local_ep = None

            # 1. Try local kitsu_api with TMDB ID
            tmdb_id = None
            if hasattr(self.ffitem, 'show_item') and self.ffitem.show_item:
                tmdb_id = getattr(self.ffitem.show_item, 'tmdb_id', None)
            if not tmdb_id:
                tmdb_id = getattr(self.ffitem, 'tmdb_id', None)

            if tmdb_id:
                try:
                    cours = kitsu_api.get_cour_structure(tmdb_id)
                    if cours:
                        cour, local_ep_num = kitsu_api.resolve_cour(cours, self.ffitem)
                        if cour and local_ep_num is not None:
                            kitsu_id = cour.get('kitsu_id')
                            local_ep = local_ep_num
                            fflog(f'[Torrentio Anime] Resolved via kitsu_api: TMDB ID {tmdb_id} -> Kitsu ID {kitsu_id}, Ep {local_ep}')
                except Exception as e:
                    fflog(f'[Torrentio Anime] kitsu_api lookup failed: {e}')

            # 2. Fallback to Kitsu Search + Ani.zip mappings
            if not kitsu_id:
                queries = [f"{tvshowtitle} Season {season}", tvshowtitle]
                for query in queries:
                    if not query:
                        continue
                    search_url = "https://kitsu.io/api/edge/anime"
                    search_params = {"filter[text]": query, "page[limit]": 5}
                    try:
                        resp = requests.get(search_url, params=search_params, timeout=10)
                        if resp.status_code == 200:
                            results = resp.json().get("data", [])
                            for item in results:
                                k_id = item.get("id")
                                
                                mapping_url = f"https://api.ani.zip/mappings?kitsu_id={k_id}"
                                m_resp = requests.get(mapping_url, timeout=10)
                                if m_resp.status_code == 200:
                                    m_data = m_resp.json()
                                    m_tvdb = m_data.get("mappings", {}).get("thetvdb_id")
                                    
                                    # Match TVDB ID if available
                                    if tvdb_id and m_tvdb and str(m_tvdb) != str(tvdb_id):
                                        continue
                                        
                                    episodes = m_data.get("episodes", {})
                                    for ep_key, ep_val in episodes.items():
                                        if ep_val.get("seasonNumber") == int(season) and ep_val.get("episodeNumber") == int(episode):
                                            kitsu_id = k_id
                                            local_ep = ep_val.get("episode")
                                            fflog(f'[Torrentio Anime] Resolved via Kitsu fallback: Kitsu ID {kitsu_id}, Ep {local_ep}')
                                            break
                                if kitsu_id:
                                    break
                    except Exception as e:
                        fflog(f'[Torrentio Anime] Fallback search error: {e}')
                    if kitsu_id:
                        break

            if not kitsu_id or local_ep is None:
                fflog(f'[Torrentio Anime] Could not resolve episode: Show="{tvshowtitle}" S{season}E{episode}')
                return None

            return urllib.parse.urlencode({
                'kitsu_id': kitsu_id,
                'episode': local_ep,
                'type': 'tv'
            })
        except Exception:
            fflog_exc()
            return None

    def sources(self, url: Optional[str], hostDict: List[str], hostprDict: List[str]) -> List[SourceItem]:
        result = []
        try:
            if not url:
                return result

            data = {k: v[0] for k, v in urllib.parse.parse_qs(url).items()}
            kitsu_id = data.get('kitsu_id', '')
            content_type = data.get('type', 'movie')

            if not kitsu_id:
                return result

            base_url = settings.getString("torrentio.base_url")
            if not base_url:
                base_url = "https://torrentio.strem.fun"
            base_url = base_url.rstrip('/')

            config = settings.getString("torrentio.config")
            if config:
                base_url += f"/{config.strip('/')}"

            if content_type == 'tv':
                episode = data.get('episode', '1')
                target_url = f"{base_url}/stream/series/kitsu:{kitsu_id}:{episode}.json"
            else:
                target_url = f"{base_url}/stream/movie/kitsu:{kitsu_id}.json"

            fflog(f'[Torrentio Anime] Querying: {target_url}')
            
            sess = requests.Session()
            resp = sess.get(target_url, headers={'User-Agent': FF_UA}, timeout=15)
            if resp.status_code != 200:
                fflog(f'[Torrentio Anime] Query returned status: {resp.status_code}')
                return result

            streams_data = resp.json()
            streams = streams_data.get('streams', [])
            fflog(f'[Torrentio Anime] Found {len(streams)} streams')

            anime_trackers = [
                "http://nyaa.tracker.wf:7777/announce",
                "http://anidex.moe:6969/announce",
                "http://tracker.anirena.com:80/announce",
                "udp://tracker.uw0.xyz:6969/announce",
                "http://share.camoe.cn:8080/announce",
                "http://t.nyaatracker.com:80/announce",
                "udp://47.ip-51-68-199.eu:6969/announce",
                "udp://9.rarbg.me:2940",
                "udp://9.rarbg.to:2820",
                "udp://exodus.desync.com:6969/announce",
                "udp://explodie.org:6969/announce",
                "udp://ipv4.tracker.harry.lu:80/announce",
                "udp://open.stealth.si:80/announce",
                "udp://opentor.org:2710/announce",
                "udp://opentracker.i2p.rocks:6969/announce",
                "udp://retracker.lanta-net.ru:2710/announce",
                "udp://tracker.cyberia.is:6969/announce",
                "udp://tracker.dler.org:6969/announce",
                "udp://tracker.ds.is:6969/announce",
                "udp://tracker.internetwarriors.net:1337",
                "udp://tracker.openbittorrent.com:6969/announce",
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://tracker.tiny-vps.com:6969/announce",
                "udp://tracker.torrent.eu.org:451/announce",
                "udp://valakas.rollo.dnsabr.com:2710/announce",
                "udp://www.torrent.eu.org:451/announce"
            ]
            tracker_query = "&tr=" + "&tr=".join(urllib.parse.quote_plus(tr) for tr in anime_trackers)

            for s in streams:
                name = s.get('name', '')
                title = s.get('title', '')
                info_hash = s.get('infoHash', '')
                file_idx = s.get('fileIdx', 0)
                stream_url = s.get('url')

                lines = title.split('\n')
                filename = lines[0].strip() if lines else ''

                seeds = 0
                size_str = ''
                if len(lines) > 1:
                    second_line = lines[1]
                    seeds_match = re.search(r'👤\s*(\d+)', second_line)
                    if seeds_match:
                        seeds = int(seeds_match.group(1))
                    size_match = re.search(r'💾\s*([\d\.]+\s*(?:GB|MB|KB|TB))', second_line, re.IGNORECASE)
                    if size_match:
                        size_str = size_match.group(1)

                quality = 'SD'
                for q in ['2160p', '4k', '1080p', '720p', '480p']:
                    if q in name.lower() or q in filename.lower():
                        if q == '4k' or q == '2160p':
                            quality = '4K'
                        elif q == '1080p':
                            quality = '1080p'
                        elif q == '720p':
                            quality = '720p'
                        break

                # Detect if Polish
                filename_lower = filename.lower()
                is_pl = False
                if any(x in filename_lower for x in ['.pl.', 'plk', 'lektor', 'dubbing', 'polski', 'polish', 'multisubs', 'multi-sub', 'pl-dub', 'pl.dub']):
                    is_pl = True
                if '🇵🇱' in title:
                    is_pl = True

                info_label = f"👤 {seeds} | 💾 {size_str}"
                
                if stream_url:
                    info_label += " (Direct)"
                    resolved_url = stream_url
                    direct = True
                    local = False
                else:
                    info_label += " (P2P)"
                    # Construct magnet link for client downloading
                    magnet = f"magnet:?xt=urn:btih:{info_hash}"
                    if filename:
                        magnet += f"&dn={urllib.parse.quote_plus(filename)}"
                    magnet += tracker_query
                    magnet += f"&file_idx={file_idx}"
                    resolved_url = magnet
                    direct = False
                    local = True

                result.append({
                    'source': 'Torrentio Anime',
                    'quality': quality,
                    'language': 'pl' if is_pl else 'en',
                    'url': resolved_url,
                    'info': info_label,
                    'filename': filename,
                    'direct': direct,
                    'debridonly': False,
                    'local': local,
                })

        except Exception:
            fflog_exc()

        return result

    def resolve(self, url: str) -> Optional[str]:
        return url
