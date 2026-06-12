import sys
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, ProgressBar

from tui.helpers import play_in_mpv

class StreamScreen(Screen):
    def __init__(self, source):
        super().__init__()
        self.source = source
        self.progress_timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="scraping-pane"):
            yield Label("Rozwiązywanie linku...", id="stream-status")
            yield ProgressBar(total=100, id="stream-bar")

    def on_mount(self) -> None:
        self.query_one(ProgressBar).progress = 5
        self.progress_timer = self.set_interval(0.2, self.tick_progress)
        self.start_streaming()

    def tick_progress(self):
        bar = self.query_one(ProgressBar)
        if bar.progress < 95:
            bar.progress += 1

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
                        import time
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
                            while os.path.exists(socket_path) or not getattr(monitor_thread, "stop", False):
                                if not os.path.exists(socket_path):
                                    time.sleep(1)
                                    continue
                                try:
                                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                                    s.settimeout(1.0)
                                    s.connect(socket_path)
                                    req = {"command": ["get_property", "percent-pos"]}
                                    s.sendall(json.dumps(req).encode('utf-8') + b'\n')
                                    res = s.recv(4096)
                                    s.close()
                                    
                                    for line in res.decode('utf-8').split('\n'):
                                        if line.strip():
                                            data = json.loads(line)
                                            if "data" in data and isinstance(data["data"], (int, float)):
                                                playback_state["percent"] = float(data["data"])
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
                                        self.app.switch_screen(ScrapingScreen([next_item]))
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
