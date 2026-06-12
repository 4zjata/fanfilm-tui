from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, DataTable, Header, Footer

from tui.trackers import dl_tracker

class DownloadsScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Powrót"),
        ("r", "refresh_list", "Odśwież")
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="downloads-pane"):
            yield Label("Aktywne i Zakończone Pobierania", classes="title-label")
            yield DataTable(id="downloads-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Plik", "Status", "Postęp", "Prędkość")
        self.timer = self.set_interval(1.0, self.update_table)
        self.update_table()

    def update_table(self):
        table = self.query_one(DataTable)
        table.clear()
        
        # Add current active download if running
        if dl_tracker.state not in ("finished", "broken", "stopped", "") and dl_tracker.filename:
            table.add_row(
                dl_tracker.filename,
                f"[yellow]{dl_tracker.state}[/yellow]",
                f"{dl_tracker.percent}%",
                f"{dl_tracker.speed} MB/s",
                key="current"
            )
            
        # Add history
        for i, item in enumerate(dl_tracker.history):
            color = "green" if item['state'] == "finished" else "red" if item['state'] in ("broken", "error") else "white"
            table.add_row(
                item['filename'],
                f"[{color}]{item['state']}[/{color}]",
                f"{item['percent']}%",
                f"{item['speed']} MB/s",
                key=f"hist_{i}"
            )
            
    def action_refresh_list(self):
        self.update_table()
