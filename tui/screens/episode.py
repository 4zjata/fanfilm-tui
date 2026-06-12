import sys
from textual import work
from textual.app import ComposeResult
from textual.widgets import Label, DataTable

from tui.screens.base import BaseScreen

class EpisodeScreen(BaseScreen):
    BINDINGS = [("escape", "app.pop_screen", "Powrót")]

    def __init__(self, tvshow, season):
        super().__init__()
        self.tvshow = tvshow
        self.season = season

    def compose_left(self) -> ComposeResult:
        yield Label(f"Odcinki - Sezon {self.season.season}", classes="title-label")
        yield DataTable(id="episode-table", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one("#meta-panel").update_meta(self.tvshow)
        self.load_episodes()

    @work(thread=True)
    def load_episodes(self):
        try:
            from lib.ff.info import ffinfo
            from lib.defs import MediaRef
            season_ref = MediaRef.tvshow(self.tvshow.ffid, self.season.season)
            season_item = ffinfo.get_item(season_ref)
            episodes = list(season_item.episode_iter()) if season_item else []
            self.app.call_from_thread(self.show_episodes, episodes)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e))

    def load_failed(self, error_msg):
        self.notify(f"Błąd ładowania: {error_msg}", severity="error")

    def show_episodes(self, episodes):
        self.episodes = episodes
        table = self.query_one(DataTable)
        table.add_column("Odcinek")
        table.add_row("[ POBIERZ CAŁY SEZON ]", key="ALL")
        for i, e in enumerate(episodes):
            table.add_row(f"Odcinek {e.episode}: {e.title}", key=str(i))
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        from tui.screens.scraping import ScrapingScreen
        if key == "ALL":
            self.app.push_screen(ScrapingScreen(self.episodes[0], queue=self.episodes))
        else:
            self.app.push_screen(ScrapingScreen(self.episodes[int(key)]))
