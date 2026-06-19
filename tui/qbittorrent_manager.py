# -*- coding: utf-8 -*-
"""
FanFilm TUI - Background Seeding Manager for qBittorrent
Copyright (C) 2026 :)
"""

import threading
import time
from lib.ff.settings import settings
from tui.qbittorrent import QBittorrentClient

def start_seeding_manager() -> threading.Event:
    stop_event = threading.Event()
    thread = threading.Thread(target=seeding_manager_loop, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event

def seeding_manager_loop(stop_event: threading.Event):
    # Wait a few seconds for startup
    time.sleep(5)
    
    while not stop_event.is_set():
        try:
            enabled = settings.getBool("torrent.seeding_limits_enabled")
            if enabled:
                url = settings.getString("qbittorrent.url") or "http://localhost:8080"
                username = settings.getString("qbittorrent.username")
                password = settings.getString("qbittorrent.password")
                
                client = QBittorrentClient(url, username, password)
                if client.login():
                    torrents = client.get_fanfilm_torrents()
                    if torrents:
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
                            
                        action = settings.getString("torrent.action_on_limit") or "stop"
                        
                        for tr in torrents:
                            info_hash = tr.get("hash")
                            if not info_hash:
                                continue
                                
                            # Skip if torrent is already paused/stopped and the action is stop
                            state = tr.get("state", "").lower()
                            if action == "stop" and state in ("pausedup", "pauseddl", "checkingup", "checkingdl"):
                                continue
                                
                            ratio = tr.get("ratio", 0.0)
                            seeding_time = tr.get("seeding_time", 0) # in seconds
                            
                            limit_reached = False
                            if ratio >= ratio_limit:
                                limit_reached = True
                            elif seeding_time >= time_limit_hours * 3600:
                                limit_reached = True
                                
                            if limit_reached:
                                if action == "delete":
                                    client.delete_torrent(info_hash, delete_files=True)
                                else:
                                    client.pause_torrent(info_hash)
        except Exception:
            pass
            
        # Check every 30 seconds
        for _ in range(30):
            if stop_event.is_set():
                break
            time.sleep(1)
