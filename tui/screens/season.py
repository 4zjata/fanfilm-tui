import sys
from textual import work
from textual.app import ComposeResult
from textual.widgets import Label, DataTable

from tui.screens.base import BaseScreen

class SeasonScreen(BaseScreen):
    BINDINGS = [("escape", "app.pop_screen", "Powrót")]

    def __init__(self, tvshow):
        super().__init__()
        self.tvshow = tvshow

    def compose_left(self) -> ComposeResult:
        yield Label(f"Sezony: {self.tvshow.title}", classes="title-label")
        yield DataTable(id="season-table", cursor_type="row")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#meta-panel").update_meta(self.tvshow)
        self.load_seasons()

    @work(thread=True)
    def load_seasons(self):
        try:
            from lib.ff.info import ffinfo
            from cdefs import InfoDetails
            tv_item = ffinfo.get_item(self.tvshow.ref, details=InfoDetails.INFO_LANG | InfoDetails.SHOW_SEASONS)
            seasons = list(tv_item.season_iter()) if tv_item else []
            self.app.call_from_thread(self.show_seasons, seasons)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e))

    def load_failed(self, error_msg):
        self.notify(f"Błąd ładowania: {error_msg}", severity="error")

    def show_seasons(self, seasons):
        self.seasons = seasons
        table = self.query_one(DataTable)
        table.add_column("Sezon")
        for i, s in enumerate(seasons):
            table.add_row(f"Sezon {s.season}", key=str(i))
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = int(event.row_key.value)
        season = self.seasons[idx]
        from tui.screens.episode import EpisodeScreen
        self.app.push_screen(EpisodeScreen(self.tvshow, season))
