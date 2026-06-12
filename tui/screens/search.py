import sys
from textual import work
from textual.app import ComposeResult
from textual.widgets import Input, DataTable

from tui.screens.base import BaseScreen

class SearchScreen(BaseScreen):
    BINDINGS = [("escape", "app.pop_screen", "Powrót")]

    def compose_left(self) -> ComposeResult:
        yield Input(placeholder="Wpisz szukaną frazę i naciśnij Enter...", id="search-input")
        yield DataTable(id="search-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Tytuł", "Rok", "Typ")
        self.load_popular_content()

    @work(thread=True)
    def load_popular_content(self):
        try:
            from lib.ff.tmdb import tmdb
            movies = list(tmdb.trending('movie', 'week', page=1))[:15]
            shows = list(tmdb.trending('show', 'week', page=1))[:15]
            results = []
            for m, s in zip(movies, shows):
                results.append(m)
                results.append(s)
            if len(movies) > len(shows):
                results.extend(movies[len(shows):])
            elif len(shows) > len(movies):
                results.extend(shows[len(movies):])
            
            if not self.query_one(Input).value:
                self.app.call_from_thread(self.show_results, results)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value
        if not query: return
        self.query_one(DataTable).clear()
        self.query_one(Input).disabled = True
        self.run_search(query)

    @work(thread=True)
    def run_search(self, query):
        try:
            from lib.ff.tmdb import tmdb
            results = tmdb.search('multi', query)
            self.app.call_from_thread(self.show_results, results)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.search_failed, str(e))

    def search_failed(self, error_msg):
        self.query_one(Input).disabled = False
        self.query_one(Input).focus()
        self.notify(f"Błąd wyszukiwania: {error_msg}", severity="error")

    def show_results(self, results):
        self.results = results
        table = self.query_one(DataTable)
        for i, item in enumerate(results):
            itype = "Film" if item.ref.is_movie else "Serial"
            table.add_row(item.title, str(item.year or '????'), itype, key=str(i))
        self.query_one(Input).disabled = False
        table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = int(event.row_key.value)
        item = self.results[idx]
        if hasattr(self, "_meta_timer") and self._meta_timer:
            self._meta_timer.stop()
        self._meta_timer = self.set_timer(0.25, lambda: self.query_one("#meta-panel").update_meta(item))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = int(event.row_key.value)
        item = self.results[idx]
        if item.ref.is_movie:
            from tui.screens.scraping import ScrapingScreen
            self.app.push_screen(ScrapingScreen(item))
        else:
            from tui.screens.season import SeasonScreen
            self.app.push_screen(SeasonScreen(item))
