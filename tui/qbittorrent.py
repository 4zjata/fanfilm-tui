import requests
import urllib.parse
import os
import time

class QBittorrentClient:
    def __init__(self, url="http://localhost:8080", username="admin", password=""):
        self.url = url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.last_error = None
        
    def login(self) -> bool:
        self.last_error = None
        try:
            # Check if already authenticated by calling an endpoint
            resp = self.session.get(f"{self.url}/api/v2/app/webapiVersion", timeout=3)
            if resp.status_code == 200:
                return True
        except:
            pass
            
        try:
            resp = self.session.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=5
            )
            # qBittorrent API returns 200 and 'Ok.' text on success
            if resp.status_code == 200 and "Ok" in resp.text:
                return True
            elif resp.status_code == 403 and "banned" in resp.text:
                self.last_error = "IP_BANNED"
            elif resp.status_code == 200 and "Ok" not in resp.text:
                self.last_error = "WRONG_CREDENTIALS"
        except Exception as e:
            self.last_error = str(e)
            print(f"[qBittorrent] Login failed: {e}")
        return False
        
    def add_torrent(self, magnet_url: str) -> str | None:
        # Extract infohash from magnet url
        # magnet:?xt=urn:btih:<hash>
        info_hash = None
        parsed = urllib.parse.urlparse(magnet_url)
        params = urllib.parse.parse_qs(parsed.query)
        xts = params.get('xt', [])
        for xt in xts:
            if xt.startswith('urn:btih:'):
                # Clean up urn:btih: prefix, take the hex string, and format to lowercase
                info_hash = xt.split('urn:btih:')[-1].lower()
                break
                
        if not info_hash:
            return None
            
        data = {
            "urls": magnet_url,
            "sequentialDownload": "true",
            "firstLastPiecePrio": "true",
            "tags": "fanfilm"
        }
            
        try:
            resp = self.session.post(f"{self.url}/api/v2/torrents/add", data=data, timeout=10)
            if resp.status_code == 200:
                # Also explicitly call addTags for compatibility
                self.add_tags(info_hash, "fanfilm")
                self.apply_seeding_limits(info_hash)
                return info_hash
        except Exception as e:
            print(f"[qBittorrent] Error adding torrent: {e}")
            
        return None

    def get_torrent_info(self, info_hash: str) -> dict | None:
        try:
            resp = self.session.get(f"{self.url}/api/v2/torrents/info", params={"hashes": info_hash}, timeout=3)
            if resp.status_code == 200:
                torrents = resp.json()
                if torrents:
                    return torrents[0]
        except Exception as e:
            print(f"[qBittorrent] Error getting torrent info: {e}")
        return None

    def get_torrent_files(self, info_hash: str) -> list | None:
        try:
            resp = self.session.get(f"{self.url}/api/v2/torrents/files", params={"hash": info_hash}, timeout=3)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"[qBittorrent] Error getting torrent files: {e}")
        return None

    def set_file_priorities(self, info_hash: str, file_idx: int) -> bool:
        files = self.get_torrent_files(info_hash)
        if not files:
            return False
            
        # Set all files priority to 0 (do not download) except the target index file which is set to 1
        skip_ids = [str(idx) for idx, _ in enumerate(files) if idx != file_idx]
                
        try:
            # Set target file priority to 1 (normal)
            self.session.post(
                f"{self.url}/api/v2/torrents/filePrio",
                data={"hash": info_hash, "id": str(file_idx), "priority": 1},
                timeout=5
            )
            # Set skipped files priority to 0 (do not download)
            if skip_ids:
                self.session.post(
                    f"{self.url}/api/v2/torrents/filePrio",
                    data={"hash": info_hash, "id": "|".join(skip_ids), "priority": 0},
                    timeout=5
                )
            
            # Ensure sequential download is enabled on the torrent
            self.session.post(
                f"{self.url}/api/v2/torrents/toggleSequentialDownload",
                data={"hashes": info_hash},
                timeout=5
            )
            self.session.post(
                f"{self.url}/api/v2/torrents/toggleFirstLastPiecePrio",
                data={"hashes": info_hash},
                timeout=5
            )
            return True
        except Exception as e:
            print(f"[qBittorrent] Error setting file priorities: {e}")
        return False

    def delete_torrent(self, info_hash: str, delete_files: bool = True) -> bool:
        try:
            resp = self.session.post(
                f"{self.url}/api/v2/torrents/delete",
                data={"hashes": info_hash, "deleteFiles": "true" if delete_files else "false"},
                timeout=5
            )
            return resp.status_code == 200
        except Exception as e:
            print(f"[qBittorrent] Error deleting torrent: {e}")
        return False

    def add_tags(self, info_hash: str, tags: str) -> bool:
        try:
            resp = self.session.post(
                f"{self.url}/api/v2/torrents/addTags",
                data={"hashes": info_hash, "tags": tags},
                timeout=5
            )
            return resp.status_code == 200
        except Exception as e:
            print(f"[qBittorrent] Error adding tags: {e}")
        return False

    def pause_torrent(self, info_hash: str) -> bool:
        try:
            resp = self.session.post(
                f"{self.url}/api/v2/torrents/pause",
                data={"hashes": info_hash},
                timeout=5
            )
            return resp.status_code == 200
        except Exception as e:
            print(f"[qBittorrent] Error pausing torrent: {e}")
        return False

    def get_fanfilm_torrents(self) -> list | None:
        try:
            resp = self.session.get(
                f"{self.url}/api/v2/torrents/info",
                params={"tag": "fanfilm"},
                timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"[qBittorrent] Error getting fanfilm torrents: {e}")
        return None

    def set_share_limits(self, info_hash: str, ratio_limit: float, seeding_time_limit_minutes: int, action: str = None) -> bool:
        # qBittorrent WebUI API shareLimitAction values:
        # 0 = Pause (Stop)
        # 1 = Remove (Remove from list only)
        # 2 = Remove and delete files (Remove with content)
        # -1 = Use global setting (Default)
        mapped_action = -1
        if action == "delete":
            mapped_action = 2
        elif action == "stop":
            mapped_action = 0

        data = {
            "hashes": info_hash,
            "ratioLimit": ratio_limit,
            "seedingTimeLimit": seeding_time_limit_minutes,
        }
        
        data_with_action = dict(data)
        data_with_action["shareLimitAction"] = mapped_action

        try:
            resp = self.session.post(
                f"{self.url}/api/v2/torrents/setShareLimits",
                data=data_with_action,
                timeout=5
            )
            if resp.status_code == 200:
                return True
            print(f"[qBittorrent] setShareLimits response: {resp.status_code} - {resp.text}")
            
            if resp.status_code == 400:
                resp = self.session.post(
                    f"{self.url}/api/v2/torrents/setShareLimits",
                    data=data,
                    timeout=5
                )
                if resp.status_code == 200:
                    return True
                print(f"[qBittorrent] setShareLimits fallback response: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"[qBittorrent] Error setting share limits: {e}")
        return False

    def apply_seeding_limits(self, info_hash: str) -> bool:
        try:
            from lib.ff.settings import settings
            enabled = settings.getBool("torrent.seeding_limits_enabled")
            if enabled:
                ratio_limit_str = settings.getString("torrent.ratio_limit")
                try:
                    ratio_limit = float(ratio_limit_str) if ratio_limit_str else 1.0
                except ValueError:
                    ratio_limit = 1.0
                    
                time_limit_str = settings.getString("torrent.seeding_time_limit")
                try:
                    time_limit_hours = float(time_limit_str) if time_limit_str else 168.0
                except ValueError:
                    time_limit_hours = 168.0
                
                seeding_time_limit_minutes = int(time_limit_hours * 60)
                action = settings.getString("torrent.action_on_limit") or "stop"
            else:
                ratio_limit = -1.0
                seeding_time_limit_minutes = -1
                action = "Default"
                
            return self.set_share_limits(info_hash, ratio_limit, seeding_time_limit_minutes, action)
        except Exception as e:
            print(f"[qBittorrent] Error applying seeding limits: {e}")
            return False
