import sys
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, DataTable, OptionList
from textual.widgets.option_list import Option

from tui.screens.base import BaseScreen
from tui.helpers import rate_source

class HomeScreen(BaseScreen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Powrót"),
        ("m", "focus_menu", "Menu"),
        ("t", "focus_table", "Tabela"),
    ]

    DEFAULT_CSS = """
    HomeScreen #left-pane > Horizontal {
        height: 1fr;
    }
    #home-sidebar {
        width: 28;
        border-right: solid $primary;
        height: 100%;
        padding: 0 1 0 0;
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
    }
    #sidebar-list > .option-list--separator {
        color: $primary-darken-2;
    }
    #sidebar-list > .option-list--option-highlighted {
        background: $accent;
        color: $text;
        text-style: none;
    }
    """

    def __init__(self, start_search=False):
        super().__init__()
        self.current_menu_id = "menu-trending"
        self.results = []
        self.progress_map = {}
        self.start_search = start_search

        # Infinite scroll state
        self.current_page = 1
        self.is_loading_more = False
        self.has_more = True
        self.current_search_query = ""

    def compose_left(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="home-sidebar"):
                yield OptionList(
                    Option("🏠\uFE0F Start", id="menu-trending"),
                    None,
                    Option("🍿\uFE0F Popularne Filmy", id="menu-movies"),
                    None,
                    Option("⭐\uFE0F Najlepsze Filmy", id="menu-top-rated-movies"),
                    None,
                    Option("🎭\uFE0F Gatunki Filmów", id="menu-movie-genres"),
                    None,
                    Option("📺\uFE0F Popularne Seriale", id="menu-shows"),
                    None,
                    Option("⭐\uFE0F Najlepsze Seriale", id="menu-top-rated-shows"),
                    None,
                    Option("🎭\uFE0F Gatunki Seriali", id="menu-show-genres"),
                    None,
                    Option("⏳\uFE0F W toku", id="menu-progress"),
                    None,
                    Option("🔍\uFE0F Szukaj", id="menu-search"),
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
            option_list.highlighted_index = 16  # Option index for Szukaj
            self.current_menu_id = "menu-search"
            inp.display = True
            inp.focus()
        else:
            option_list.highlighted_index = 0
            self.current_menu_id = "menu-trending"
            self.load_trending()

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
        
        inp = self.query_one("#search-input", Input)
        
        if opt_id == "menu-search":
            inp.display = True
            inp.disabled = False
            inp.value = ""
            inp.focus()
        else:
            inp.display = False
            if opt_id == "menu-trending":
                self.load_trending()
            elif opt_id == "menu-movies":
                self.load_movies()
            elif opt_id == "menu-shows":
                self.load_shows()
            elif opt_id == "menu-top-rated-movies":
                self.load_top_rated_movies()
            elif opt_id == "menu-top-rated-shows":
                self.load_top_rated_shows()
            elif opt_id == "menu-movie-genres":
                self.load_movie_genres()
            elif opt_id == "menu-show-genres":
                self.load_show_genres()
            elif opt_id == "menu-progress":
                self.load_progress()

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
    def load_movies(self):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            for p in (1, 2):
                results.extend(list(tmdb.trending('movie', 'week', page=p)))
            self.app.call_from_thread(self.show_results, results, "menu-movies", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-movies")

    @work(thread=True)
    def load_shows(self):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            for p in (1, 2):
                results.extend(list(tmdb.trending('show', 'week', page=p)))
            self.app.call_from_thread(self.show_results, results, "menu-shows", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-shows")

    @work(thread=True)
    def load_top_rated_movies(self):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            for p in (1, 2):
                results.extend(list(tmdb.discover_list('movie', 'top_rated', page=p)))
            self.app.call_from_thread(self.show_results, results, "menu-top-rated-movies", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-top-rated-movies")

    @work(thread=True)
    def load_top_rated_shows(self):
        try:
            from lib.ff.tmdb import tmdb
            results = []
            for p in (1, 2):
                results.extend(list(tmdb.discover_list('show', 'top_rated', page=p)))
            self.app.call_from_thread(self.show_results, results, "menu-top-rated-shows", page=2)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-top-rated-shows")

    @work(thread=True)
    def load_movie_genres(self):
        try:
            from lib.ff.tmdb import tmdb
            genres = list(tmdb.genres('movie'))
            self.app.call_from_thread(self.show_genres, genres, "menu-movie-genres")
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-movie-genres")

    @work(thread=True)
    def load_show_genres(self):
        try:
            from lib.ff.tmdb import tmdb
            genres = list(tmdb.genres('show'))
            self.app.call_from_thread(self.show_genres, genres, "menu-show-genres")
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.app.call_from_thread(self.load_failed, str(e), "menu-show-genres")

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
            from lib.ff.kodidb import video_db
            from lib.ff.info import ffinfo
            
            plays = video_db.get_plays()
            in_progress = [play for play in plays if play.has_progress]
            in_progress.sort(key=lambda x: x.played_at or 0, reverse=True)
            
            results = []
            progress_map = {}
            for play in in_progress:
                try:
                    item = ffinfo.get_item(play.ref)
                    if item:
                        percent = int(play.percent) if play.percent is not None else 0
                        results.append(item)
                        progress_map[str(len(results) - 1)] = f"{percent}%"
                except Exception:
                    pass
                    
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

    def show_results(self, results, menu_id, progress_map=None, page=1):
        if self.current_menu_id != menu_id:
            return
            
        self.results = results
        self.progress_map = progress_map or {}
        self.current_page = page
        self.prepare_media_table()
        table = self.query_one("#results-table", DataTable)
        
        for i, item in enumerate(results):
            itype = "Film" if item.ref.is_movie else "Serial"
            if item.ref.is_episode:
                itype = "Odcinek"
                
            # Formatting title: if episode, show show title and SxxExx
            title = item.title
            if item.ref.is_episode:
                show_title = item.vtag.getTvShowTitle() or item.vtag.getEnglishTvShowTitle() or "Serial"
                title = f"{show_title} - S{item.season:02d}E{item.episode:02d}"
                
            if menu_id == "menu-progress":
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
        if menu_id != "menu-search" and len(results) > 0:
            table.focus()

    def show_genres(self, genres, menu_id):
        if self.current_menu_id != menu_id:
            return
            
        self.results = genres
        self.progress_map = {}
        
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Nazwa gatunku", "Typ")
        
        itype = "Filmy" if menu_id == "menu-movie-genres" else "Seriale"
        for i, genre in enumerate(genres):
            table.add_row(
                genre.get('name', 'Nieznany'),
                itype,
                key=str(i)
            )
            
        inp = self.query_one("#search-input", Input)
        inp.disabled = False
        if len(genres) > 0:
            table.focus()

    def show_genre_results(self, results, menu_id, genre_name, page=1):
        if self.current_menu_id != menu_id:
            return
            
        # Prepends a back option (None) to results
        self.results = [None] + list(results)
        self.progress_map = {}
        self.current_page = page
        
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Tytuł", "Rok", "Typ")
        
        # Row 0 is the back option
        table.add_row(
            "⬅️ Powrót do listy gatunków",
            "—",
            "—",
            key="0"
        )
        
        for i, item in enumerate(results):
            itype = "Film" if item.ref.is_movie else "Serial"
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
                key=str(i + 1)
            )
            
        inp = self.query_one("#search-input", Input)
        inp.disabled = False
        if len(self.results) > 0:
            table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = int(event.row_key.value)
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
                    "menu-trending", "menu-movies", "menu-shows",
                    "menu-top-rated-movies", "menu-top-rated-shows",
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
            elif menu_id == "menu-movies":
                results = list(tmdb.trending('movie', 'week', page=page))[:30]
            elif menu_id == "menu-shows":
                results = list(tmdb.trending('show', 'week', page=page))[:30]
            elif menu_id == "menu-top-rated-movies":
                results = list(tmdb.discover_list('movie', 'top_rated', page=page))[:30]
            elif menu_id == "menu-top-rated-shows":
                results = list(tmdb.discover_list('show', 'top_rated', page=page))[:30]
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
            itype = "Film" if item.ref.is_movie else "Serial"
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
        if self.current_menu_id in ("menu-movie-genres", "menu-show-genres"):
            genre = self.results[idx]
            genre_id = genre['id']
            genre_name = genre['name']
            media_type = 'movie' if self.current_menu_id == "menu-movie-genres" else 'show'
            
            self.current_menu_id = f"genre-{media_type}:{genre_id}"
            
            # Reset page states for genre results
            self.current_page = 1
            self.is_loading_more = False
            self.has_more = True
            
            table = self.query_one("#results-table", DataTable)
            table.clear()
            
            self.load_genre_results(media_type, genre_id, genre_name)
            return
            
        # 2. If in genre results mode and index is 0 (back option)
        if self.current_menu_id.startswith("genre-") and idx == 0:
            media_type = 'movie' if 'movie' in self.current_menu_id else 'show'
            self.current_menu_id = f"menu-{media_type}-genres"
            
            # Reset page states for genre list
            self.current_page = 1
            self.is_loading_more = False
            self.has_more = True
            
            table = self.query_one("#results-table", DataTable)
            table.clear()
            
            if media_type == 'movie':
                self.load_movie_genres()
            else:
                self.load_show_genres()
            return
            
        # 3. Normal item selection
        item = self.results[idx]
        if item.ref.is_movie or item.ref.is_episode:
            from tui.screens.scraping import ScrapingScreen
            self.app.push_screen(ScrapingScreen(item))
        else:
            from tui.screens.season import SeasonScreen
            self.app.push_screen(SeasonScreen(item))

    def action_focus_menu(self) -> None:
        self.query_one("#sidebar-list", OptionList).focus()

    def action_focus_table(self) -> None:
        self.query_one("#results-table", DataTable).focus()
