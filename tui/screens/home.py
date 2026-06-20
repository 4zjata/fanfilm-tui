import sys
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, DataTable, OptionList
from textual.widgets.option_list import Option
from textual.binding import Binding

from tui.screens.base import BaseScreen
from tui.helpers import rate_source

class HomeScreen(BaseScreen):
    BINDINGS = [
        Binding("escape", "escape", "Powrót", show=False),
        ("f", "filter_movies", "Filmy"),
        ("s", "filter_shows", "Seriale"),
        ("a", "filter_all", "Wszystko"),
        ("delete", "delete_progress", "Usuń"),
        Binding("d", "delete_progress", "Usuń", show=False),
    ]

    def action_escape(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    DEFAULT_CSS = """
    HomeScreen #left-pane > Horizontal {
        height: 1fr;
    }
    #home-sidebar {
        width: 28;
        border-right: solid $primary-darken-2;
        height: 100%;
        padding: 0 1 0 0;
    }
    #home-sidebar:focus-within {
        border-right: solid $accent;
    }
    #home-main {
        width: 1fr;
        height: 100%;
        padding: 0 0 0 1;
    }
    #search-input {
        margin-bottom: 1;
    }
    #sidebar-list {
        height: 100%;
        background: transparent;
        border: none;
        padding: 0;
    }
    #sidebar-list > .option-list--option {
        padding: 0 1;
    }
    #sidebar-list > .option-list--separator {
        color: $primary-darken-2;
    }
    /* Menu highlight when active/focused */
    #sidebar-list:focus > .option-list--option-highlighted {
        background: $accent;
        color: $text;
        text-style: none;
    }
    /* Menu highlight when inactive/unfocused */
    #sidebar-list > .option-list--option-highlighted {
        background: $surface;
        color: $text-muted;
        text-style: none;
    }
    /* Table row highlight when active/focused */
    #results-table:focus > .datatable--cursor {
        background: $accent;
        color: $text;
    }
    /* Table row highlight when inactive/unfocused */
    #results-table > .datatable--cursor {
        background: $surface;
        color: $text-muted;
    }
    """

    def __init__(self, start_search=False):
        super().__init__()
        self.current_menu_id = "menu-trending"
        self.results = []
        self.progress_map = {}
        self.start_search = start_search
        self.media_filter = "all"

        # Infinite scroll state
        self.current_page = 1
        self.is_loading_more = False
        self.has_more = True
        self.current_search_query = ""
        self.highlighted_idx = None

    def compose_left(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="home-sidebar"):
                yield OptionList(
                    Option("\uf015 Start", id="menu-trending"),
                    None,
                    Option("\ue232 Popularne", id="menu-popular"),
                    None,
                    Option("\uf005 Najlepsze", id="menu-top-rated"),
                    None,
                    Option("\U000f040d Gatunki", id="menu-genres"),
                    None,
                    Option("\uf252 W toku", id="menu-progress"),
                    None,
                    Option("\uf002 Szukaj", id="menu-search"),
                    id="sidebar-list"
                )
            with Vertical(id="home-main"):
                yield Input(placeholder="Wpisz szukaną frazę i naciśnij Enter...", id="search-input")
                yield DataTable(id="results-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns("Tytuł", "Rok", "Typ")
        
        inp = self.query_one("#search-input", Input)
        inp.display = False
        
        # Select initial option
        option_list = self.query_one("#sidebar-list", OptionList)
        if self.start_search:
            option_list.highlighted_index = 10  # Option index for Szukaj
            self.current_menu_id = "menu-search"
            inp.display = True
            inp.focus()
            if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                self.app.discord_rpc.set_status("Przegląda menu", "Wyszukiwanie")
        else:
            option_list.highlighted_index = 0
            self.current_menu_id = "menu-trending"
            self.load_trending()
            if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                self.app.discord_rpc.set_status("Przegląda menu", "Strona główna")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.current_menu_id = opt_id

        # Reset page states
        self.current_page = 1
        self.is_loading_more = False
        self.has_more = True
        
        table = self.query_one("#results-table", DataTable)
        table.clear()
        self.results = []
        self.progress_map = {}
        self.highlighted_idx = None
        
        inp = self.query_one("#search-input", Input)
        
        if opt_id == "menu-search":
            inp.display = True
            inp.disabled = False
            inp.value = ""
            inp.focus()
            if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                self.app.discord_rpc.set_status("Przegląda menu", "Wyszukiwanie")
        else:
            inp.display = False
            if opt_id == "menu-trending":
                self.load_trending()
                if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                    self.app.discord_rpc.set_status("Przegląda menu", "Strona główna")
            elif opt_id == "menu-popular":
                self.load_popular()
                if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                    self.app.discord_rpc.set_status("Przegląda menu", "Popularne")
            elif opt_id == "menu-top-rated":
                self.load_top_rated()
                if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                    self.app.discord_rpc.set_status("Przegląda menu", "Najlepsze")
            elif opt_id == "menu-genres":
                self.load_genres()
                if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                    self.app.discord_rpc.set_status("Przegląda menu", "Gatunki")
            elif opt_id == "menu-progress":
                self.load_progress()
                if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                    self.app.discord_rpc.set_status("Przegląda menu", "W toku")

    @work(thread=True)
    def load_trending(self):
        try:
            from lib.ff.tmdb import tmdb
            movies = []
            shows = []
            for p in (1, 2):
                movies.extend(list(tmdb.trending('movie', 'week', page=p))[:15])
                shows.extend(list(tmdb.trending('show', 'week', page=p))[:15])
            results = []
            for m, s in zip(movies, shows):
                results.append(m)
                results.append(s)
            if len(movies) > len(shows):
                results.extend(movies[len(shows):])
            elif len(shows) > len(movies):
                results.extend(shows[len(movies):])
            
            self.app.call_from_thread(self.show_results, results, "menu-trending", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-trending")

    @work(thread=True)
    def load_popular(self):
        try:
            from lib.ff.tmdb import tmdb
            movies = []
            shows = []
            for p in (1, 2):
                movies.extend(list(tmdb.discover_list('movie', 'popular', page=p))[:15])
                shows.extend(list(tmdb.discover_list('show', 'popular', page=p))[:15])
            results = []
            for m, s in zip(movies, shows):
                results.append(m)
                results.append(s)
            if len(movies) > len(shows):
                results.extend(movies[len(shows):])
            elif len(shows) > len(movies):
                results.extend(shows[len(movies):])
            
            self.app.call_from_thread(self.show_results, results, "menu-popular", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-popular")

    @work(thread=True)
    def load_top_rated(self):
        try:
            from lib.ff.tmdb import tmdb
            movies = []
            shows = []
            for p in (1, 2):
                movies.extend(list(tmdb.discover_list('movie', 'top_rated', page=p))[:15])
                shows.extend(list(tmdb.discover_list('show', 'top_rated', page=p))[:15])
            results = []
            for m, s in zip(movies, shows):
                results.append(m)
                results.append(s)
            if len(movies) > len(shows):
                results.extend(movies[len(shows):])
            elif len(shows) > len(movies):
                results.extend(shows[len(movies):])
            
            self.app.call_from_thread(self.show_results, results, "menu-top-rated", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-top-rated")

    @work(thread=True)
    def load_genres(self):
        try:
            from lib.ff.tmdb import tmdb
            movie_genres = list(tmdb.genres('movie'))
            show_genres = list(tmdb.genres('show'))
            
            results = []
            for g in movie_genres:
                g_copy = g.copy()
                g_copy['media_type'] = 'movie'
                results.append(g_copy)
            for g in show_genres:
                g_copy = g.copy()
                g_copy['media_type'] = 'show'
                results.append(g_copy)
                
            self.app.call_from_thread(self.show_genres, results, "menu-genres")
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-genres")

    @work(thread=True)
    def load_genre_results(self, media_type, genre_id, genre_name):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            for p in (1, 2):
                results.extend(list(tmdb.discover(media_type, with_genres=str(genre_id), page=p)))
            menu_id = f"genre-{media_type}:{genre_id}"
            self.app.call_from_thread(self.show_genre_results, results, menu_id, genre_name, page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            menu_id = f"genre-{media_type}:{genre_id}"
            self.app.call_from_thread(self.load_failed, str(e), menu_id)

    @work(thread=True)
    def load_progress(self):
        try:
            from lib.ff.info import ffinfo
            from tui.helpers import load_local_progress
            
            local_progress = load_local_progress()
            
            progress_items = []
            
            for ref_str, data in local_progress.items():
                try:
                    from lib.defs import MediaRef
                    ref = MediaRef.from_slash_string(ref_str)
                    if ref:
                        item = ffinfo.get_item(ref)
                        if item:
                            percent = int(data["percent"])
                            ts = data.get("updated_at", 0)
                            progress_items.append((item, percent, ts))
                except Exception:
                    pass
            
            # Sort progress entries by timestamp descending (newest first)
            progress_items.sort(key=lambda x: x[2], reverse=True)
            
            results = []
            progress_map = {}
            for item, percent, ts in progress_items:
                results.append(item)
                progress_map[str(len(results) - 1)] = f"{percent}%"
                    
            self.app.call_from_thread(self.show_results, results, "menu-progress", progress_map)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-progress")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value
        if not query: return
        
        # Reset page states
        self.current_page = 1
        self.is_loading_more = False
        self.has_more = True
        self.current_search_query = query
        
        self.query_one("#results-table", DataTable).clear()
        self.query_one("#search-input", Input).disabled = True
        self.run_search(query)
        if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
            self.app.discord_rpc.set_status("Przegląda menu", f"Szuka: {query}")

    @work(thread=True)
    def run_search(self, query):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            for p in (1, 2):
                results.extend(list(tmdb.search('multi', query, page=p)))
            self.app.call_from_thread(self.show_results, results, "menu-search", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-search")

    def load_failed(self, error_msg, menu_id):
        self.is_loading_more = False
        if self.current_menu_id != menu_id:
            return
        inp = self.query_one("#search-input", Input)
        inp.disabled = False
        if menu_id == "menu-search":
            inp.focus()
        self.notify(f"Błąd ładowania danych: {error_msg}", severity="error")

    def prepare_media_table(self):
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Tytuł", "Rok", "Typ")

    def update_table_rows(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear()
        
        # Row 0 is the back option in genre results mode
        if self.current_menu_id.startswith("genre-"):
            table.add_row(
                "⬅️ Powrót do listy gatunków",
                "—",
                "—",
                key="0"
            )
            
        for i, item in enumerate(self.results):
            if item is None:
                continue
                
            # Filter checks
            if hasattr(item, 'ref'):
                is_movie = item.ref.is_movie
                if self.media_filter == "movie" and not is_movie:
                    continue
                if self.media_filter == "show" and is_movie:
                    continue
            
            itype = "Film" if item.ref.is_movie else "Serial"
            if item.ref.is_episode:
                itype = "Odcinek"
                
            title = item.title
            if item.ref.is_episode:
                show_title = item.vtag.getTvShowTitle() or item.vtag.getEnglishTvShowTitle() or "Serial"
                title = f"{show_title} - S{item.season:02d}E{item.episode:02d}"
                
            if self.current_menu_id == "menu-progress":
                pct = self.progress_map.get(str(i), "0%")
                title = f"{title} ({pct})"
                
            table.add_row(
                title, 
                str(item.year or '????'), 
                itype, 
                key=str(i)
            )
            
        inp = self.query_one("#search-input", Input)
        inp.disabled = False
        if self.current_menu_id != "menu-search" and table.row_count > 0:
            table.focus()

    def show_results(self, results, menu_id, progress_map=None, page=1):
        if self.current_menu_id != menu_id:
            return
            
        self.results = results
        self.progress_map = progress_map or {}
        self.current_page = page
        self.prepare_media_table()
        self.update_table_rows()

    def update_genres_table_rows(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Nazwa gatunku", "Typ")
        
        for i, genre in enumerate(self.results):
            media_type = genre.get('media_type', 'movie')
            if self.media_filter == "movie" and media_type != "movie":
                continue
            if self.media_filter == "show" and media_type != "show":
                continue
                
            itype = "Filmy" if media_type == "movie" else "Seriale"
            table.add_row(
                genre.get('name', 'Nieznany'),
                itype,
                key=str(i)
            )
            
        inp = self.query_one("#search-input", Input)
        inp.disabled = False
        if table.row_count > 0:
            table.focus()

    def show_genres(self, genres, menu_id):
        if self.current_menu_id != menu_id:
            return
            
        self.results = genres
        self.progress_map = {}
        self.update_genres_table_rows()

    def show_genre_results(self, results, menu_id, genre_name, page=1):
        if self.current_menu_id != menu_id:
            return
            
        self.results = [None] + list(results)
        self.progress_map = {}
        self.current_page = page
        
        self.prepare_media_table()
        self.update_table_rows()

    def action_filter_movies(self) -> None:
        self.media_filter = "movie"
        if self.current_menu_id == "menu-genres":
            self.update_genres_table_rows()
        else:
            self.update_table_rows()
        self.notify("Filtrowanie: Tylko filmy", severity="information")

    def action_filter_shows(self) -> None:
        self.media_filter = "show"
        if self.current_menu_id == "menu-genres":
            self.update_genres_table_rows()
        else:
            self.update_table_rows()
        self.notify("Filtrowanie: Tylko seriale", severity="information")

    def action_filter_all(self) -> None:
        self.media_filter = "all"
        if self.current_menu_id == "menu-genres":
            self.update_genres_table_rows()
        else:
            self.update_table_rows()
        self.notify("Filtrowanie: Wszystko", severity="information")


    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = int(event.row_key.value)
        self.highlighted_idx = idx
        if idx >= len(self.results): return
        item = self.results[idx]
        if hasattr(self, "_meta_timer") and self._meta_timer:
            self._meta_timer.stop()
        self._meta_timer = self.set_timer(0.25, lambda: self.query_one("#meta-panel").update_meta(item))

        # Check for Infinite Scroll trigger
        if self.has_more and not self.is_loading_more:
            # Check if active menu category is paginatable
            is_paginatable = (
                self.current_menu_id in (
                    "menu-trending", "menu-popular", "menu-top-rated",
                    "menu-search"
                ) or self.current_menu_id.startswith("genre-")
            )
            if is_paginatable and idx > 0:
                # Trigger when highlighting one of the last 6 items
                if len(self.results) - idx <= 6:
                    self.is_loading_more = True
                    self.load_next_page(self.current_menu_id, self.current_page + 1)

    @work(thread=True)
    def load_next_page(self, menu_id, page):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            
            if menu_id == "menu-trending":
                movies = list(tmdb.trending('movie', 'week', page=page))[:15]
                shows = list(tmdb.trending('show', 'week', page=page))[:15]
                for m, s in zip(movies, shows):
                    results.append(m)
                    results.append(s)
                if len(movies) > len(shows):
                    results.extend(movies[len(shows):])
                elif len(shows) > len(movies):
                    results.extend(shows[len(movies):])
            elif menu_id == "menu-popular":
                movies = list(tmdb.discover_list('movie', 'popular', page=page))[:15]
                shows = list(tmdb.discover_list('show', 'popular', page=page))[:15]
                for m, s in zip(movies, shows):
                    results.append(m)
                    results.append(s)
                if len(movies) > len(shows):
                    results.extend(movies[len(shows):])
                elif len(shows) > len(movies):
                    results.extend(shows[len(movies):])
            elif menu_id == "menu-top-rated":
                movies = list(tmdb.discover_list('movie', 'top_rated', page=page))[:15]
                shows = list(tmdb.discover_list('show', 'top_rated', page=page))[:15]
                for m, s in zip(movies, shows):
                    results.append(m)
                    results.append(s)
                if len(movies) > len(shows):
                    results.extend(movies[len(shows):])
                elif len(shows) > len(movies):
                    results.extend(shows[len(movies):])
            elif menu_id.startswith("genre-"):
                parts = menu_id.split("-")[1].split(":")
                media_type = parts[0]
                genre_id = int(parts[1])
                results = list(tmdb.discover(media_type, with_genres=str(genre_id), page=page))[:30]
            elif menu_id == "menu-search":
                if self.current_search_query:
                    results = list(tmdb.search('multi', self.current_search_query, page=page))
            
            if not results:
                self.has_more = False
                self.is_loading_more = False
                return
                
            self.app.call_from_thread(self.append_results, results, menu_id, page)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.has_more = False
            self.is_loading_more = False

    def append_results(self, results, menu_id, page):
        if self.current_menu_id != menu_id:
            return
            
        self.current_page = page
        start_idx = len(self.results)
        self.results.extend(results)
        
        table = self.query_one("#results-table", DataTable)
        
        for i, item in enumerate(results):
            idx = start_idx + i
            is_movie = item.ref.is_movie
            if self.media_filter == "movie" and not is_movie:
                continue
            if self.media_filter == "show" and is_movie:
                continue
                
            itype = "Film" if is_movie else "Serial"
            if item.ref.is_episode:
                itype = "Odcinek"
                
            title = item.title
            if item.ref.is_episode:
                show_title = item.vtag.getTvShowTitle() or item.vtag.getEnglishTvShowTitle() or "Serial"
                title = f"{show_title} - S{item.season:02d}E{item.episode:02d}"
                
            table.add_row(
                title, 
                str(item.year or '????'), 
                itype, 
                key=str(idx)
            )
            
        self.is_loading_more = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = int(event.row_key.value)
        
        # 1. If in genre list mode
        if self.current_menu_id == "menu-genres":
            genre = self.results[idx]
            genre_id = genre['id']
            genre_name = genre['name']
            media_type = genre.get('media_type', 'movie')
            
            self.current_menu_id = f"genre-{media_type}:{genre_id}"
            
            # Reset page states for genre results
            self.current_page = 1
            self.is_loading_more = False
            self.has_more = True
            
            table = self.query_one("#results-table", DataTable)
            table.clear()
            
            self.load_genre_results(media_type, genre_id, genre_name)
            if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                self.app.discord_rpc.set_status("Przegląda menu", f"Gatunek: {genre_name}")
            return
            
        # 2. If in genre results mode and index is 0 (back option)
        if self.current_menu_id.startswith("genre-") and idx == 0:
            self.current_menu_id = "menu-genres"
            
            # Reset page states for genre list
            self.current_page = 1
            self.is_loading_more = False
            self.has_more = True
            
            table = self.query_one("#results-table", DataTable)
            table.clear()
            
            self.load_genres()
            if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                self.app.discord_rpc.set_status("Przegląda menu", "Gatunki")
            return
            
        # 3. Normal item selection
        item = self.results[idx]
        if item.ref.is_movie or item.ref.is_episode:
            from tui.screens.scraping import ScrapingScreen
            self.app.push_screen(ScrapingScreen(item))
        else:
            from tui.screens.season import SeasonScreen
            self.app.push_screen(SeasonScreen(item))

    def action_delete_progress(self) -> None:
        if self.current_menu_id != "menu-progress":
            return
        if self.highlighted_idx is None or self.highlighted_idx < 0 or self.highlighted_idx >= len(self.results):
            return
        
        try:
            item = self.results[self.highlighted_idx]
            ref_str = str(item.ref)
            
            from tui.helpers import delete_local_progress
            delete_local_progress(ref_str)
            
            self.notify(f"Usunięto z w toku: {item.title}")
            
            # Reset highlight state and reload list
            self.highlighted_idx = None
            self.load_progress()
        except Exception as e:
            self.app.log(f"Error deleting progress item: {e}")


