import sys
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, ProgressBar

from tui.helpers import rate_source
from tui.trackers import TUIProgressDialog

class ScrapingScreen(Screen):
    def __init__(self, item, queue=None):
        super().__init__()
        self.item = item
        self.queue = queue or [item]
        self.dialog = TUIProgressDialog()
        self.timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="scraping-pane"):
            yield Label(f"Szukanie źródeł: {self.item.title}", id="scrape-title")
            yield ProgressBar(total=100, id="scrape-bar")
            yield Label("Trwa przeszukiwanie...", id="scrape-stats")
            yield Label("", id="scrape-providers")

    def on_mount(self) -> None:
        self.timer = self.set_interval(0.1, self.update_ui)
        self.run_scraping()

    @work(thread=True)
    def run_scraping(self):
        try:
            from lib.ff.info import ffinfo
            from lib.ff.sources import sources
            ffitem = ffinfo.get_item(self.item.ref)
            if not ffitem:
                ffitem = self.item
            vtag = ffitem.getVideoInfoTag()
            itype = 'show' if (ffitem.ref.is_episode or ffitem.ref.is_show) else 'movie'
            
            query = {
                'title': vtag.getEnglishTitle() or vtag.getOriginalTitle() or ffitem.title,
                'localtitle': ffitem.title,
                'originalname': vtag.getOriginalTitle(),
                'year': vtag.getYear(),
                'imdb': vtag.getUniqueID('imdb'),
                'tmdb': vtag.getUniqueID('tmdb'),
                'season': ffitem.season,
                'episode': ffitem.episode,
                'tvshowtitle': (vtag.getEnglishTvShowTitle() or ffitem.title) if itype == 'show' else '',
                'premiered': str(ffitem.date or ''),
                'ffitem': ffitem,
            }
            
            sf = sources()
            sf.getConstants(ffitem=ffitem)
            found = sf.get_sources(**query, progress_dialog=self.dialog)
            self.app.call_from_thread(self.scraping_finished, sf, found)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.scraping_finished, None, [])

    def update_ui(self):
        bar = self.query_one(ProgressBar)
        bar.progress = self.dialog.percent
        self.query_one("#scrape-stats", Label).update(f"Zakończono: {self.dialog.percent}%")
        
        provs = self.dialog.providers
        if provs:
            prov_text = f"[bold cyan]Aktywne wtyczki ({len(provs)}):[/bold cyan] " + ", ".join(provs[:12])
            if len(provs) > 12:
                prov_text += "..."
            self.query_one("#scrape-providers", Label).update(prov_text)

    def scraping_finished(self, sf, found):
        if self.timer: self.timer.stop()
        
        from lib.ff.settings import settings
        is_advanced = settings.getString("tui.advanced_mode") == "true"
        
        if is_advanced:
            from tui.screens.scraper_status import ScraperStatusScreen
            self.app.switch_screen(ScraperStatusScreen(self.dialog.scraper_statuses, found, self.queue))
        else:
            self.proceed_after_scraping(found)

    def proceed_after_scraping(self, found):
        cf_failed_providers = []
        for name, status in self.dialog.scraper_statuses.items():
            status_lower = str(status).lower()
            if "403" in status_lower or "503" in status_lower or "forbidden" in status_lower or "cloudflare" in status_lower:
                cf_failed_providers.append(name.upper())

        if not found:
            if cf_failed_providers:
                from tui.screens.alert import AlertScreen
                msg = (
                    f"Wykryto blokadę Cloudflare (błąd 403/503) w serwisach: {', '.join(cf_failed_providers)}.\n\n"
                    "Prawdopodobnie wygasło ciasteczko cf_clearance lub zmienił się Twój adres IP.\n\n"
                    "Uruchom przeglądarkę, wejdź na te strony i wyślij nowe ciasteczka do TUI."
                )
                def on_alert_close(_=None):
                    if len(self.queue) > 1:
                        self.queue.pop(0)
                        self.app.switch_screen(ScrapingScreen(self.queue[0], self.queue))
                    else:
                        self.app.pop_screen()
                self.app.push_screen(AlertScreen(msg), on_alert_close)
            else:
                self.notify("Nie znaleziono żadnych źródeł dla tej pozycji.", severity="warning")
                if len(self.queue) > 1:
                    self.queue.pop(0)
                    self.app.switch_screen(ScrapingScreen(self.queue[0], self.queue))
                else:
                    self.app.pop_screen()
            return

        if cf_failed_providers:
            self.notify(
                f"Blokada Cloudflare (403/503) w: {', '.join(cf_failed_providers)}. Sprawdź ciasteczka!",
                severity="error",
                timeout=7.0
            )

        found.sort(key=rate_source, reverse=True)
        if len(self.queue) > 1:
            from tui.screens.download import DownloadScreen
            self.app.switch_screen(DownloadScreen(found, self.queue))
        else:
            from tui.screens.source_select import SourceSelectScreen
            self.app.switch_screen(SourceSelectScreen(found, self.queue))
