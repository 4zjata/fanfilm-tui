import sys
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, ProgressBar

from lib.ff.settings import settings
from lib.ff.sources import sources
from lib.ff.downloader import download
from tui.trackers import dl_tracker

class DownloadScreen(Screen):
    def __init__(self, sources_list, queue):
        super().__init__()
        self.sources_list = sources_list if isinstance(sources_list, list) else [sources_list]
        self.source = self.sources_list[0]
        self.queue = queue
        self.current_idx = 0
        self.timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="download-pane"):
            yield Label(f"Rozpoczynanie pobierania...", id="dl-title")
            yield ProgressBar(total=100, id="dl-bar")
            yield Label("", id="dl-stats")
            if len(self.queue) > 1:
                yield Label(f"Sezon: Odcinek {self.current_idx + 1} z {len(self.queue)}", id="dl-queue-title")
                yield ProgressBar(total=len(self.queue), id="dl-queue-bar")

    def on_mount(self) -> None:
        ffitem = self.source.ffitem
        if ffitem.ref.is_episode:
            dname = f"{ffitem.vtag.getTvShowTitle() or ffitem.title} S{ffitem.season:02d}E{ffitem.episode:02d}"
        else:
            dname = ffitem.title
        dl_tracker.filename = f"{dname} (rozwiązywanie...)"
        dl_tracker.percent = 5
        dl_tracker.state = "starting"
        dl_tracker.speed = "0"
        self.timer = self.set_interval(0.1, self.update_ui)
        self.start_download()

    @work(thread=True)
    def start_download(self):
        try:
            from lib.ff.downloader import probe_media_streams
            lang_pref = settings.getString("providers.lang")
            
            resolved = None
            chosen_source = None
            fallback_resolved = None
            fallback_source = None
            
            for idx, source in enumerate(self.sources_list):
                self.source = source
                ffitem = source.ffitem
                if ffitem.ref.is_episode:
                    dname_base = f"{ffitem.vtag.getTvShowTitle() or ffitem.title} S{ffitem.season:02d}E{ffitem.episode:02d}"
                else:
                    dname_base = ffitem.title
                dl_tracker.filename = f"{dname_base} (rozwiązywanie {source.provider}...)"
                
                sf = sources()
                sf.getConstants(ffitem=source.ffitem)
                resolved_url = sf.resolve_source(source)
                if not resolved_url:
                    continue
                
                if fallback_resolved is None:
                    fallback_resolved = resolved_url
                    fallback_source = source
                
                if lang_pref == "Polish":
                    url_only = resolved_url.split("|")[0] if isinstance(resolved_url, str) else resolved_url[0].split("|")[0]
                    headers = None
                    if isinstance(resolved_url, str) and "|" in resolved_url:
                        from urllib.parse import parse_qsl
                        try:
                            headers = dict(parse_qsl(resolved_url.rsplit("|", 1)[1]))
                        except:
                            pass
                    
                    streams = probe_media_streams(url_only, headers)
                    
                    if streams:
                        has_pl = False
                        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
                        sub_streams = [s for s in streams if s.get("codec_type") in ("subtitle", "subtitles")]
                        
                        for s in audio_streams:
                            lang = s.get("tags", {}).get("language", "").lower()
                            title = s.get("tags", {}).get("title", "").lower()
                            if lang in ("pol", "pl", "polski", "pl_pl") or any(w in title for w in ("pl", "lektor", "dubbing", "polski", "polish")):
                                has_pl = True
                                break
                        
                        if not has_pl:
                            for s in sub_streams:
                                lang = s.get("tags", {}).get("language", "").lower()
                                title = s.get("tags", {}).get("title", "").lower()
                                if lang in ("pol", "pl", "polski", "pl_pl") or any(w in title for w in ("pl", "napisy", "polski", "polish")):
                                    has_pl = True
                                    break
                                    
                        if not has_pl:
                            print(f"DEBUG: Skipping source {source.provider} because it lacks Polish tracks")
                            continue
                
                resolved = resolved_url
                chosen_source = source
                break
                
            if not resolved and fallback_resolved:
                resolved = fallback_resolved
                chosen_source = fallback_source
                self.source = chosen_source
                
            if resolved:
                ffitem = chosen_source.ffitem
                year = ffitem.year
                if ffitem.ref.is_episode:
                    show_item = ffitem.show_item
                    if show_item and show_item.vtag.getYear():
                        year = int(show_item.vtag.getYear())
                    dname = f"{ffitem.vtag.getTvShowTitle() or ffitem.title}.S{ffitem.season:02d}E{ffitem.episode:02d}"
                else:
                    dname = ffitem.title

                image = ffitem.getArt("poster")

                info_content = chosen_source.get('info', '').strip()
                daudio_type = next((t for t in ['Lektor', 'Dubbing', 'Napisy'] if t in info_content), '')
                dlanguage = chosen_source.get('language', '').upper()
                dquality = chosen_source.get('quality', '')
                dinfo2 = chosen_source.get('info2', '')
                downinfo = f'{daudio_type} | {dlanguage} | {dquality} | {dinfo2}'

                download(dname, year, image, downinfo, resolved)
        except Exception as e:
            print(f"Error in start_download: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
        self.app.call_from_thread(self.download_finished)

    def update_ui(self):
        if dl_tracker.state == "starting":
            if dl_tracker.percent < 90:
                dl_tracker.percent += 1
        self.query_one("#dl-title", Label).update(f"Plik: {dl_tracker.filename}")
        self.query_one("#dl-bar", ProgressBar).progress = dl_tracker.percent
        speed_str = f" | Prędkość: {dl_tracker.speed} MB/s" if dl_tracker.state == "running" else ""
        self.query_one("#dl-stats", Label).update(f"Stan: {dl_tracker.state}{speed_str}")
        if len(self.queue) > 1:
            self.query_one("#dl-queue-bar", ProgressBar).progress = self.current_idx

    def download_finished(self):
        if self.timer: self.timer.stop()
        self.current_idx += 1
        if self.current_idx < len(self.queue):
            item = self.queue[self.current_idx]
            from tui.screens.scraping import ScrapingScreen
            self.app.switch_screen(ScrapingScreen(item, self.queue[self.current_idx:]))
        else:
            self.app.pop_screen()
