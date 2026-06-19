import sys
import os
import urllib.parse
import time
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, ProgressBar, Footer

from tui.helpers import play_in_mpv
from lib.ff.settings import settings

class StreamScreen(Screen):
    BINDINGS = [
        ("escape", "cancel_streaming", "Anuluj")
    ]

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.progress_timer = None
        self.cancelled = False

    def compose(self) -> ComposeResult:
        with Vertical(id="scraping-pane"):
            yield Label("Rozwiązywanie linku...", id="stream-status")
            yield ProgressBar(total=100, id="stream-bar")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        self.query_one(ProgressBar).progress = 5
        self.progress_timer = self.set_interval(0.2, self.tick_progress)
        self.start_streaming()

    def tick_progress(self):
        bar = self.query_one(ProgressBar)
        if bar.progress < 95:
            bar.progress += 1

    def action_cancel_streaming(self):
        self.cancelled = True
        self.notify("Anulowano strumieniowanie torrenta", severity="warning")
        self.app.pop_screen()

    def update_progress(self, percent):
        try:
            bar = self.query_one("#stream-bar", ProgressBar)
            bar.progress = percent
        except:
            pass

    @work(thread=True)
    def start_streaming(self):
        try:
            import shutil
            if not shutil.which("mpv"):
                self.app.call_from_thread(self.notify, "Brak odtwarzacza 'mpv' w systemie. Zainstaluj go, aby korzystać ze streamingu.", severity="error")
                self.app.call_from_thread(self.app.pop_screen)
                return

            from lib.ff.sources import sources
            self.app.call_from_thread(self.update_status, "Rozwiązywanie linku do streamingu...")
            sf = sources()
            sf.getConstants(ffitem=self.source.ffitem)
            resolved = sf.resolve_source(self.source)
            
            if self.cancelled:
                return

            info_hash = None
            client = None

            if resolved and resolved.startswith("magnet:"):
                # Intercept magnet link for P2P streaming
                parsed = urllib.parse.urlparse(resolved)
                params = urllib.parse.parse_qs(parsed.query)
                file_idx = int(params.get('file_idx', [0])[0])
                
                engine = settings.getString("torrent.engine") or "qbittorrent"
                if engine == "webtorrent":
                    # WebTorrent fallback
                    if self.progress_timer:
                        self.progress_timer.stop()
                    self.app.call_from_thread(self.update_status, "Uruchamianie WebTorrent...")
                    cmd = ["npx", "-y", "webtorrent-cli", resolved, "-s", str(file_idx), "--mpv"]
                    
                    def run_webtorrent():
                        import subprocess
                        try:
                            with self.app.suspend():
                                subprocess.run(cmd)
                        except Exception as e:
                            self.app.call_from_thread(self.notify, f"Błąd WebTorrent: {e}", severity="error")
                        finally:
                            self.app.call_from_thread(self.app.pop_screen)
                    
                    self.app.call_from_thread(run_webtorrent)
                    return
                else:
                    # qBittorrent engine
                    self.app.call_from_thread(self.update_status, "Łączenie z qBittorrent...")
                    from tui.qbittorrent import QBittorrentClient
                    url = settings.getString("qbittorrent.url") or "http://localhost:8080"
                    username = settings.getString("qbittorrent.username")
                    password = settings.getString("qbittorrent.password")
                    
                    client = QBittorrentClient(url, username, password)
                    if not client.login():
                        if getattr(client, "last_error", None) == "IP_BANNED":
                            msg = "Błąd: Twój adres IP został zbanowany w qBittorrent (zbyt wiele nieudanych logowań). Zrestartuj go, aby odblokować IP."
                        elif getattr(client, "last_error", None) == "WRONG_CREDENTIALS":
                            msg = "Błąd: Błędne hasło lub użytkownik w qBittorrent WebUI. Sprawdź Ustawienia."
                        else:
                            msg = "Błąd: Nie można zalogować się do qBittorrent WebUI. Sprawdź konfigurację."
                        self.app.call_from_thread(self.notify, msg, severity="error")
                        self.app.call_from_thread(self.app.pop_screen)
                        return
                        
                    if self.cancelled:
                        return
                    
                    self.app.call_from_thread(self.update_status, "Dodawanie torrenta do pobierania...")
                    info_hash = client.add_torrent(resolved)
                    if not info_hash:
                        self.app.call_from_thread(self.notify, "Błąd: Nie udało się dodać torrenta.", severity="error")
                        self.app.call_from_thread(self.app.pop_screen)
                        return
                        
                    if self.progress_timer:
                        self.progress_timer.stop()
                        
                    # 1. Wait for metadata resolution (downloading files list)
                    metadata_resolved = False
                    for _ in range(45): # wait up to 45s for metadata
                        if self.cancelled:
                            client.delete_torrent(info_hash, delete_files=True)
                            return
                        info = client.get_torrent_info(info_hash)
                        if info and info.get('size', 0) > 0:
                            files = client.get_torrent_files(info_hash)
                            if files:
                                metadata_resolved = True
                                break
                        seeds = info.get('num_seeds', 0) if info else 0
                        self.app.call_from_thread(self.update_status, f"Pobieranie metadanych torrenta... (Seedy: {seeds})")
                        time.sleep(1)
                        
                    if not metadata_resolved:
                        self.app.call_from_thread(self.notify, "Błąd: Nie udało się pobrać metadanych torrenta.", severity="error")
                        client.delete_torrent(info_hash, delete_files=True)
                        self.app.call_from_thread(self.app.pop_screen)
                        return
                        
                    if self.cancelled:
                        client.delete_torrent(info_hash, delete_files=True)
                        return
                        
                    # Apply seeding limits natively on qBittorrent
                    client.apply_seeding_limits(info_hash)
                    
                    # 2. Set file priorities (only download the requested file)
                    client.set_file_priorities(info_hash, file_idx)
                    
                    # 3. Buffer target file until we have enough data (1% or 5MB)
                    files = client.get_torrent_files(info_hash)
                    target_file = files[file_idx]
                    file_name = target_file['name']
                    file_size = target_file['size']
                    
                    buffered = False
                    while not buffered:
                        if self.cancelled:
                            client.delete_torrent(info_hash, delete_files=True)
                            return
                        info = client.get_torrent_info(info_hash)
                        files = client.get_torrent_files(info_hash)
                        if not info or not files or file_idx >= len(files):
                            time.sleep(1)
                            continue
                            
                        target_file = files[file_idx]
                        progress = target_file.get('progress', 0.0)
                        downloaded = int(progress * file_size)
                        
                        seeds = info.get('num_seeds', 0)
                        speed = info.get('dlspeed', 0) / (1024 * 1024) # MB/s
                        progress_pct = int(progress * 100)
                        
                        status = f"Buforowanie torrenta: {progress_pct}% | Seedy: {seeds} | Prędkość: {speed:.2f} MB/s"
                        self.app.call_from_thread(self.update_status, status)
                        self.app.call_from_thread(self.update_progress, progress_pct)
                        
                        # Buffering complete when 1% progress or 5MB is downloaded
                        if progress >= 0.01 or downloaded >= 5 * 1024 * 1024:
                            buffered = True
                            break
                            
                        time.sleep(1)
                        
                    # Update resolved to the local path of the downloaded file
                    resolved = os.path.join(info['save_path'], file_name)

            if self.progress_timer:
                self.progress_timer.stop()
                
            if resolved:
                self.app.call_from_thread(self.update_status, "Uruchamianie odtwarzacza MPV...")
                ffitem = self.source.ffitem
                if ffitem.ref.is_episode:
                    title = f"{ffitem.vtag.getTvShowTitle() or ffitem.title} - S{ffitem.season:02d}E{ffitem.episode:02d}"
                else:
                    title = f"{ffitem.title} ({ffitem.year})"
                cmd = play_in_mpv(resolved, title)
                if cmd:
                    def run_mpv():
                        import subprocess
                        import tempfile
                        import socket
                        import json
                        import os
                        from threading import Thread

                        socket_path = os.path.join(tempfile.gettempdir(), f"fanfilm_mpv_{os.getpid()}")
                        if os.path.exists(socket_path):
                            try: os.unlink(socket_path)
                            except: pass

                        run_cmd = list(cmd)
                        run_cmd.append(f"--input-ipc-server={socket_path}")

                        playback_state = {"percent": 0.0}

                        def monitor():
                            time.sleep(2)
                            while not getattr(monitor_thread, "stop", False):
                                if not os.path.exists(socket_path):
                                    time.sleep(1)
                                    continue
                                try:
                                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                                        s.settimeout(1.0)
                                        s.connect(socket_path)
                                        req = {"command": ["get_property", "percent-pos"]}
                                        s.sendall(json.dumps(req).encode('utf-8') + b'\n')
                                        res = s.recv(4096)
                                        
                                        for line in res.decode('utf-8').split('\n'):
                                            if line.strip():
                                                data = json.loads(line)
                                                if "data" in data and isinstance(data["data"], (int, float)):
                                                    playback_state["percent"] = float(data["data"])
                                                elif "error" in data and data["error"] == "property unavailable":
                                                    # Some formats/states can have percent-pos unavailable temporarily
                                                    pass
                                except Exception:
                                    pass
                                time.sleep(2)

                        monitor_thread = Thread(target=monitor, daemon=True)
                        monitor_thread.start()

                        try:
                            with self.app.suspend():
                                subprocess.run(run_cmd)
                        finally:
                            monitor_thread.stop = True
                            if os.path.exists(socket_path):
                                try: os.unlink(socket_path)
                                except: pass

                        percent_watched = playback_state["percent"]
                        self.app.log(f"MPV finished playback. Last watched percent: {percent_watched}%")

                        # Up Next logic
                        if ffitem.ref.is_episode and percent_watched >= 75.0:
                            from lib.defs import MediaRef
                            from lib.ff.info import ffinfo
                            next_ep = ffitem.episode + 1
                            next_ref = MediaRef.tvshow(ffitem.ref.ffid, ffitem.season, next_ep)
                            next_item = ffinfo.get_item(next_ref)
                            if next_item:
                                def handle_yesno(result):
                                    if result:
                                        from tui.screens.scraping import ScrapingScreen
                                        self.app.switch_screen(ScrapingScreen(next_item))
                                    else:
                                        self.app.pop_screen()
                                        
                                from tui.screens.yesno import YesNoScreen
                                title = next_item.vtag.getTvShowTitle() or next_item.title
                                self.app.push_screen(YesNoScreen(f"Odtworzyć następny odcinek?\n{title} S{next_item.season:02d}E{next_item.episode:02d}"), handle_yesno)
                            else:
                                self.app.pop_screen()
                        else:
                            self.app.pop_screen()

                    self.app.call_from_thread(run_mpv)
            else:
                self.app.call_from_thread(self.notify, "Nie udało się rozwiązać linku.", severity="error")
                self.app.call_from_thread(self.app.pop_screen)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.notify, f"Błąd podczas uruchamiania streamingu: {e}", severity="error")
            self.app.call_from_thread(self.app.pop_screen)

    def update_status(self, text):
        self.query_one("#stream-status", Label).update(text)
