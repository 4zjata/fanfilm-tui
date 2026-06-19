# -*- coding: utf-8 -*-
"""
FanFilm - źródło: SeaDex (Best Releases from releases.moe)
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

            anilist_id = None
            tmdb_id = getattr(self.ffitem, 'tmdb_id', '')

            # 1. Try Ani.zip using IMDb
            if imdb:
                try:
                    url = f"https://api.ani.zip/mappings?imdb_id={imdb}"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        anilist_id = resp.json().get("mappings", {}).get("anilist_id")
                        if anilist_id:
                            fflog(f'[SeaDex] Resolved movie via Ani.zip (IMDb): {imdb} -> AniList ID {anilist_id}')
                except Exception as e:
                    fflog(f'[SeaDex] Ani.zip movie lookup error: {e}')

            # 2. Try Ani.zip using TMDB
            if not anilist_id and tmdb_id:
                try:
                    url = f"https://api.ani.zip/mappings?themoviedb_id={tmdb_id}"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        anilist_id = resp.json().get("mappings", {}).get("anilist_id")
                        if anilist_id:
                            fflog(f'[SeaDex] Resolved movie via Ani.zip (TMDB): {tmdb_id} -> AniList ID {anilist_id}')
                except Exception as e:
                    fflog(f'[SeaDex] Ani.zip movie TMDB lookup error: {e}')

            # 3. Fallback to Kitsu Search + Ani.zip
            if not anilist_id:
                try:
                    search_url = "https://kitsu.io/api/edge/anime"
                    params = {"filter[text]": title, "filter[subtype]": "movie", "page[limit]": 5}
                    resp = requests.get(search_url, params=params, timeout=10)
                    if resp.status_code == 200:
                        results = resp.json().get("data", [])
                        if results:
                            kitsu_id = results[0].get("id")
                            if kitsu_id:
                                url = f"https://api.ani.zip/mappings?kitsu_id={kitsu_id}"
                                k_resp = requests.get(url, timeout=10)
                                if k_resp.status_code == 200:
                                    anilist_id = k_resp.json().get("mappings", {}).get("anilist_id")
                                    if anilist_id:
                                        fflog(f'[SeaDex] Resolved movie via Kitsu fallback: Title "{title}" -> AniList ID {anilist_id}')
                except Exception as e:
                    fflog(f'[SeaDex] Kitsu fallback search error: {e}')

            if not anilist_id:
                fflog(f'[SeaDex] Could not resolve movie to AniList ID: Title="{title}"')
                return None

            return urllib.parse.urlencode({'anilist_id': anilist_id, 'type': 'movie'})
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
                            fflog(f'[SeaDex] Resolved via kitsu_api: TMDB ID {tmdb_id} -> Kitsu ID {kitsu_id}, Ep {local_ep}')
                except Exception as e:
                    fflog(f'[SeaDex] kitsu_api lookup failed: {e}')

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
                                            fflog(f'[SeaDex] Resolved via Kitsu fallback: Kitsu ID {kitsu_id}, Ep {local_ep}')
                                            break
                                if kitsu_id:
                                    break
                    except Exception as e:
                        fflog(f'[SeaDex] Fallback search error: {e}')
                    if kitsu_id:
                        break

            if not kitsu_id or local_ep is None:
                fflog(f'[SeaDex] Could not resolve episode: Show="{tvshowtitle}" S{season}E{episode}')
                return None

            # Get AniList ID from kitsu_id
            anilist_id = None
            try:
                mapping_url = f"https://api.ani.zip/mappings?kitsu_id={kitsu_id}"
                m_resp = requests.get(mapping_url, timeout=10)
                if m_resp.status_code == 200:
                    anilist_id = m_resp.json().get("mappings", {}).get("anilist_id")
            except Exception as e:
                fflog(f'[SeaDex] Error mapping kitsu_id to anilist_id: {e}')

            if not anilist_id:
                fflog(f'[SeaDex] Could not resolve AniList ID for kitsu_id {kitsu_id}')
                return None

            return urllib.parse.urlencode({
                'anilist_id': anilist_id,
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
            anilist_id = data.get('anilist_id', '')
            content_type = data.get('type', 'movie')

            if not anilist_id:
                return result

            pb_url = f"https://releases.moe/api/collections/entries/records?page=1&perPage=1&filter=alID=%22{anilist_id}%22&skipTotal=1&expand=trs"
            fflog(f'[SeaDex] Querying releases.moe: {pb_url}')
            
            resp = requests.get(pb_url, headers={'User-Agent': FF_UA}, timeout=15)
            if resp.status_code != 200:
                fflog(f'[SeaDex] releases.moe returned status: {resp.status_code}')
                return result

            items = resp.json().get('items', [])
            if not items:
                fflog(f'[SeaDex] No entry found on releases.moe for AniList ID {anilist_id}')
                return result

            record = items[0]
            trs = record.get('expand', {}).get('trs', [])
            fflog(f'[SeaDex] Found {len(trs)} torrent records')

            trackers = [
                "http://nyaa.tracker.wf:7777/announce",
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.stealth.si:80/announce",
                "udp://tracker.coppersurfer.tk:6969/announce",
                "udp://exodus.desync.com:6969/announce"
            ]
            tracker_query = "&tr=" + "&tr=".join(urllib.parse.quote_plus(tr) for tr in trackers)

            for tr in trs:
                info_hash = tr.get('infoHash')
                if not info_hash or info_hash == '<redacted>':
                    continue
                if tr.get('tracker') != 'Nyaa':
                    continue

                release_group = tr.get('releaseGroup', 'Unknown')
                dual_audio = tr.get('dualAudio', False)
                files = tr.get('files', [])

                # Calculate sizes
                total_bytes = sum(f.get('length', 0) for f in files)
                size_str = self.format_size(total_bytes)

                # Determine file index for TV shows
                file_idx = 0
                filename = ""
                if content_type == 'tv':
                    episode = int(data.get('episode', '1'))
                    file_idx, filename = self.find_file_index(files, episode)
                
                # If filename not resolved or it's a movie, fallback to the first file's name or torrent title
                if not filename:
                    if files:
                        filename = files[0].get('name', '')
                    else:
                        filename = f"[{release_group}] SeaDex Best Release"

                # Parse quality
                quality = 'SD'
                for q in ['2160p', '4k', '1080p', '720p', '480p']:
                    if q in filename.lower():
                        if q == '4k' or q == '2160p':
                            quality = '4K'
                        elif q == '1080p':
                            quality = '1080p'
                        elif q == '720p':
                            quality = '720p'
                        break

                # Scrape seeds/leechers from Nyaa.si to show actual health
                seeds, leechers, scrape_size = self.scrape_nyaa(tr.get('url', ''))
                if scrape_size:
                    size_str = scrape_size

                # Construct magnet link
                magnet = f"magnet:?xt=urn:btih:{info_hash}"
                if filename:
                    magnet += f"&dn={urllib.parse.quote_plus(filename.split('/')[-1])}"
                magnet += tracker_query
                if content_type == 'tv':
                    magnet += f"&file_idx={file_idx}"

                lang_code, audio_type = source_utils.get_lang_by_type(filename)
                is_pl = (lang_code == 'pl' or lang_code == 'multi')

                info_label = f"👤 {seeds} | 💾 {size_str} | [SeaDex Recommendation]"
                if dual_audio:
                    info_label += " | Dual-Audio"
                if audio_type:
                    info_label += f" | {audio_type}"

                result.append({
                    'source': 'SeaDex',
                    'quality': quality,
                    'language': 'pl' if is_pl else 'en',
                    'url': magnet,
                    'info': info_label,
                    'filename': filename.split('/')[-1],
                    'direct': False,
                    'debridonly': False,
                    'local': True,
                    'seeds': seeds,
                })

        except Exception:
            fflog_exc()

        return result

    def scrape_nyaa(self, url: str) -> tuple[int, int, str]:
        if not url or not url.startswith('http'):
            return 0, 0, ""
        try:
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if resp.status_code == 200:
                html = resp.text
                seeders_match = re.search(r'<div class="col-md-1">Seeders:</div>\s*<div class="col-md-[^"]*">(.*?)</div>', html, re.DOTALL)
                leechers_match = re.search(r'<div class="col-md-1">Leechers:</div>\s*<div class="col-md-[^"]*">(.*?)</div>', html, re.DOTALL)
                size_match = re.search(r'<div class="col-md-1">File size:</div>\s*<div class="col-md-[^"]*">(.*?)</div>', html, re.DOTALL)
                
                def clean(val):
                    return re.sub(r'<[^>]*>', '', val).strip()
                    
                seeds_str = clean(seeders_match.group(1)) if seeders_match else '0'
                leech_str = clean(leechers_match.group(1)) if leechers_match else '0'
                size_str = clean(size_match.group(1)) if size_match else ''
                
                seeds = int(re.search(r'\d+', seeds_str).group()) if re.search(r'\d+', seeds_str) else 0
                leech = int(re.search(r'\d+', leech_str).group()) if re.search(r'\d+', leech_str) else 0
                return seeds, leech, size_str
        except Exception:
            pass
        return 0, 0, ""

    def find_file_index(self, files: list, episode_num: int) -> tuple[int, str]:
        patterns = [
            rf'[Ee]0*{episode_num}\b',
            rf'\b0*{episode_num}\b',
            rf'\b0*{episode_num}(?:\.mkv|\.mp4|\.avi)?$',
            rf'-\s*0*{episode_num}\b',
            rf'_\s*0*{episode_num}\b',
            rf'\[0*{episode_num}\]',
        ]
        for pattern in patterns:
            for idx, file in enumerate(files):
                filename = file.get('name', '')
                name_part = filename.split('/')[-1].lower()
                if re.search(pattern, name_part):
                    return idx, filename
        return 0, ""

    def format_size(self, bytes_val: int) -> str:
        if bytes_val >= 1024**4:
            return f"{bytes_val / 1024**4:.2f} TB"
        elif bytes_val >= 1024**3:
            return f"{bytes_val / 1024**3:.2f} GB"
        elif bytes_val >= 1024**2:
            return f"{bytes_val / 1024**2:.2f} MB"
        elif bytes_val >= 1024:
            return f"{bytes_val / 1024:.2f} KB"
        return f"{bytes_val} B"

    def resolve(self, url: str) -> Optional[str]:
        return url
