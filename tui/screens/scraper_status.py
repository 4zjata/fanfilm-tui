from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, DataTable, Footer

from tui.helpers import rate_source

class ScraperStatusScreen(Screen):
    BINDINGS = [
        ("escape", "continue_flow", "Kontynuuj"),
        ("enter", "continue_flow", "Kontynuuj"),
    ]

    def __init__(self, statuses, found, queue):
        super().__init__()
        self.statuses = statuses
        self.found = found
        self.queue = queue

    def compose(self) -> ComposeResult:
        with Vertical(id="scraper-status-pane"):
            yield Label("Statusy Scraperów (Tryb Zaawansowany)", classes="title-label")
            yield DataTable(id="status-table", cursor_type="row")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Scraper", "Status / Błąd")
        for scraper, status in sorted(self.statuses.items()):
            if "Błąd" in status or "Exception" in status or "failed" in status.lower():
                color = "red"
            elif "OK" in status:
                color = "green"
            elif "Brak" in status:
                color = "yellow"
            else:
                color = "white"
            table.add_row(scraper, f"[{color}]{status}[/{color}]")
        table.focus()

    def action_continue_flow(self):
        # Normal scraping finished flow
        if not self.found:
            self.notify("Nie znaleziono żadnych źródeł dla tej pozycji.", severity="warning")
            if len(self.queue) > 1:
                self.queue.pop(0)
                from tui.screens.scraping import ScrapingScreen
                self.app.switch_screen(ScrapingScreen(self.queue[0], self.queue))
            else:
                self.app.pop_screen()
            return

        self.found.sort(key=rate_source, reverse=True)
        if len(self.queue) > 1:
            from tui.screens.download import DownloadScreen
            self.app.switch_screen(DownloadScreen(self.found, self.queue))
        else:
            from tui.screens.source_select import SourceSelectScreen
            self.app.switch_screen(SourceSelectScreen(self.found, self.queue))
