import sys
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, DataTable, OptionList
from textual.widgets.option_list import Option
from textual.binding import Binding

from lib.ff.settings import settings
from tui.screens.base import BaseScreen
from tui.helpers import rate_source, sanitize_title

class HomeScreen(BaseScreen):
    GENRE_MAP = {
        28: "Akcja", 12: "Przygodowy", 16: "Animacja", 35: "Komedia",
        80: "Kryminał", 99: "Dokumentalny", 18: "Dramat", 10751: "Familijny",
        14: "Fantasy", 36: "Historyczny", 27: "Horror", 10402: "Muzyczny",
        9648: "Tajemnica", 10749: "Romantyczny", 878: "Sci-Fi", 10770: "Film TV",
        53: "Thriller", 10752: "Wojenny", 37: "Western",
        10759: "Akcja i Przygoda", 10762: "Dla dzieci", 10763: "Wiadomości",
        10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Telenowela",
        10767: "Talk-show", 10768: "Wojna i Polityka"
    }

    BINDINGS = [
        Binding("escape", "escape", "Powrót", show=False),
        ("f", "filter_movies", "Filmy"),
        ("s", "filter_shows", "Seriale"),
        ("a", "filter_all", "Wszystko"),
        ("delete", "delete_progress", "Usuń"),
        Binding("d", "delete_progress", "Usuń", show=False),
        Binding("[", "resize_menu_decrease", "Menu -"),
        Binding("]", "resize_menu_increase", "Menu +"),
        Binding("{", "resize_desc_decrease", "Opis -"),
        Binding("}", "resize_desc_increase", "Opis +"),
    ]

    def action_escape(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    def action_resize_menu_decrease(self) -> None:
        sidebar = self.query_one("#home-sidebar")
        current_w = 28
        val = settings.getString("tui.menu_sidebar_width")
        if val:
            try:
                current_w = int(val)
            except ValueError:
                pass
        new_w = max(15, current_w - 1)
        sidebar.styles.width = new_w
        settings.set("tui.menu_sidebar_width", str(new_w))
        self.call_after_refresh(self.recalculate_column_widths)

    def action_resize_menu_increase(self) -> None:
        sidebar = self.query_one("#home-sidebar")
        current_w = 28
        val = settings.getString("tui.menu_sidebar_width")
        if val:
            try:
                current_w = int(val)
            except ValueError:
                pass
        new_w = min(50, current_w + 1)
        sidebar.styles.width = new_w
        settings.set("tui.menu_sidebar_width", str(new_w))
        self.call_after_refresh(self.recalculate_column_widths)

    def action_resize_desc_decrease(self) -> None:
        val = settings.getString("tui.right_pane_width")
        current_w = 40
        if val:
            try:
                current_w = int(val)
            except ValueError:
                pass
        new_w = max(15, current_w - 2)
        try:
            self.query_one("#right-pane").styles.width = f"{new_w}%"
            self.query_one("#left-pane").styles.width = f"{100 - new_w}%"
        except Exception:
            pass
        settings.set("tui.right_pane_width", str(new_w))
        self.call_after_refresh(self.recalculate_column_widths)

    def action_resize_desc_increase(self) -> None:
        val = settings.getString("tui.right_pane_width")
        current_w = 40
        if val:
            try:
                current_w = int(val)
            except ValueError:
                pass
        new_w = min(60, current_w + 2)
        try:
            self.query_one("#right-pane").styles.width = f"{new_w}%"
            self.query_one("#left-pane").styles.width = f"{100 - new_w}%"
        except Exception:
            pass
        settings.set("tui.right_pane_width", str(new_w))
        self.call_after_refresh(self.recalculate_column_widths)

    def recalculate_column_widths(self) -> None:
        try:
            table = self.query_one("#results-table", DataTable)
            if not table or not table.columns:
                return
            
            # Only adjust if we are in media results mode (not genres)
            if self.current_menu_id == "menu-genres":
                return
                
            # Get the first column (Tytuł)
            title_key = list(table.columns.keys())[0]
            col = table.columns[title_key]
            
            # Get other columns render widths
            other_w = 0
            for k, c in list(table.columns.items())[1:]:
                other_w += c.get_render_width(table)
                
            # Scrollbar, cell padding, and borders margin
            margin = 5
            
            # Table container width
            container_w = table.container_size.width
            if container_w > 0:
                target_w = max(10, container_w - other_w - margin)
                col.auto_width = False
                col.width = target_w
                table._require_update_dimensions = True
                table.refresh()
        except Exception:
            pass

    def on_resize(self) -> None:
        self.recalculate_column_widths()

    def get_genre_string(self, item) -> str:
        if item is None:
            return "Nieznany"
            
        def extract(it):
            if it is None:
                return None
            vtag = it.getVideoInfoTag()
            genre = vtag.getGenre()
            if genre and genre != "Nieznany":
                return genre
                
            if hasattr(it, "source_data") and isinstance(it.source_data, dict):
                genre_ids = it.source_data.get("genre_ids")
                if genre_ids and isinstance(genre_ids, list):
                    names = []
                    for gid in genre_ids:
                        name = self.GENRE_MAP.get(gid) or self.GENRE_MAP.get(str(gid)) or self.GENRE_MAP.get(int(gid) if isinstance(gid, str) and gid.isdigit() else None)
                        if name:
                            names.append(name)
                    if names:
                        return ", ".join(names[:2])
                        
                genres_list = it.source_data.get("genres")
                if genres_list and isinstance(genres_list, list):
                    names = []
                    for g in genres_list:
                        if isinstance(g, dict) and g.get("name"):
                            names.append(g["name"])
                    if names:
                        return ", ".join(names[:2])
            return None

        # 1. Try item itself
        g = extract(item)
        if g:
            return g
            
        # 2. Try show_item if available
        if hasattr(item, "show_item") and item.show_item:
            g = extract(item.show_item)
            if g:
                return g
                
        # 3. Resolve show item from cache/API for season or episode
        if hasattr(item, "ref") and (item.ref.is_season or item.ref.is_episode):
            try:
                from lib.defs import MediaRef
                from lib.ff.info import ffinfo
                show_ref = MediaRef(type="show", ffid=item.ref.ffid)
                show_item = ffinfo.get_item(show_ref)
                g = extract(show_item)
                if g:
                    return g
            except Exception:
                pass
                
        return "Nieznany"

    @work(thread=True)
    def load_genre_map(self):
        try:
            from lib.ff.tmdb import tmdb
            movie_genres = tmdb.genres('movie')
            show_genres = tmdb.genres('show')
            for g in movie_genres + show_genres:
                gid = g.get('id')
                name = g.get('name')
                if gid and name:
                    self.GENRE_MAP[gid] = name
        except Exception as e:
            try:
                self.app.log(f"Error loading genre map: {e}")
            except Exception:
                pass

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

    def __init__(self, start_search=False, start_menu_id: str = "menu-trending"):
        super().__init__()
        self.current_menu_id = start_menu_id
        if start_search:
            self.current_menu_id = "menu-search"
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
        self.romanized_refs = set()

    def compose_left(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="home-sidebar"):
                yield OptionList(
                    Option("\uf015 Start", id="menu-trending"),
                    None,
                    Option("\U000f0422 Popularne", id="menu-popular"),
                    None,
                    Option("\uf005 Najlepsze", id="menu-top-rated"),
                    None,
                    Option("\U000f040d Gatunki", id="menu-genres"),
                    None,
                    Option("\uf252 W toku", id="menu-progress"),
                    None,
                    Option("\uf002 Szukaj", id="menu-search"),
                    None,
                    Option("\uf019 Pobierane", id="menu-downloads"),
                    None,
                    Option("\uf013 Ustawienia", id="menu-settings"),
                    None,
                    Option("\uf011 Wyjście", id="menu-quit"),
                    id="sidebar-list"
                )
            with Vertical(id="home-main"):
                yield Input(placeholder="Wpisz szukaną frazę i naciśnij Enter...", id="search-input")
                yield DataTable(id="results-table", cursor_type="row")

    def on_mount(self) -> None:
        super().on_mount()
        
        # Restore menu sidebar width from settings
        menu_w = settings.getString("tui.menu_sidebar_width")
        if menu_w:
            try:
                self.query_one("#home-sidebar").styles.width = int(menu_w)
            except Exception:
                pass

        self.prepare_media_table()
        self.load_genre_map()
        
        inp = self.query_one("#search-input", Input)
        inp.display = False
        
        # Show or hide sidebar depending on setting
        menu_type = settings.getString("tui.menu_type") or "sidebar"
        sidebar = self.query_one("#home-sidebar")
        if menu_type == "command_palette":
            sidebar.display = False
        else:
            sidebar.display = True

        # Select initial option
        option_list = self.query_one("#sidebar-list", OptionList)
        initial_idx = 0
        for idx in range(option_list.option_count):
            opt = option_list.get_option_at_index(idx)
            if opt and opt.id == self.current_menu_id:
                initial_idx = idx
                break
        
        option_list.highlighted_index = initial_idx
        self.select_menu_option(self.current_menu_id)

        # Set initial focus
        if self.current_menu_id == "menu-search":
            inp.focus()
        else:
            if menu_type == "command_palette":
                self.query_one("#results-table").focus()
            else:
                option_list.focus()

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        try:
            menu_type = settings.getString("tui.menu_type") or "sidebar"
            sidebar = self.query_one("#home-sidebar")
            if menu_type == "command_palette":
                sidebar.display = False
                # Focus results-table if nothing else has active focus inside home-sidebar
                if self.app.focused == sidebar or sidebar.has_focus:
                    self.query_one("#results-table").focus()
            else:
                sidebar.display = True
            self.call_after_refresh(self.recalculate_column_widths)
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.select_menu_option(event.option.id)

    def select_menu_option(self, opt_id: str) -> None:
        self.current_menu_id = opt_id

        # Update OptionList highlighted index to match, if possible, to keep state in sync
        try:
            option_list = self.query_one("#sidebar-list", OptionList)
            for idx in range(option_list.option_count):
                opt = option_list.get_option_at_index(idx)
                if opt and opt.id == opt_id:
                    option_list.highlighted_index = idx
                    break
        except Exception:
            pass

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
        elif opt_id == "menu-downloads":
            self.app.action_goto_downloads()
        elif opt_id == "menu-settings":
            self.app.action_goto_settings()
        elif opt_id == "menu-quit":
            self.app.action_quit()
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

    def get_equivalent_genre_id(self, gid, source_type):
        mapping = {
            28: 10759, 12: 10759, 16: 16, 35: 35, 80: 80, 99: 99, 18: 18, 
            10751: 10751, 14: 10765, 9648: 9648, 
            878: 10765, 53: 80, 10752: 10768, 37: 37,
            10759: [28, 12], 10762: 10751, 10765: [878, 14], 10768: 10752
        }
        return mapping.get(gid)

    @work(thread=True)
    def load_genres(self):
        try:
            from lib.ff.tmdb import tmdb
            movie_genres = list(tmdb.genres('movie'))
            show_genres = list(tmdb.genres('show'))
            
            groups = [
                {
                    'name': 'Akcja / Przygoda',
                    'movie_ids': [28, 12],
                    'show_ids': [10759]
                },
                {
                    'name': 'Sci-Fi / Fantasy',
                    'movie_ids': [878, 14],
                    'show_ids': [10765]
                },
                {
                    'name': 'Wojna / Polityka',
                    'movie_ids': [10752],
                    'show_ids': [10768]
                },
                {
                    'name': 'Dla dzieci / Familijny',
                    'movie_ids': [10751],
                    'show_ids': [10762, 10751]
                },
                {
                    'name': 'Kryminał / Thriller',
                    'movie_ids': [80, 53],
                    'show_ids': [80]
                }
            ]
            
            grouped_movie_ids = set()
            grouped_show_ids = set()
            for group in groups:
                grouped_movie_ids.update(group['movie_ids'])
                grouped_show_ids.update(group['show_ids'])
                
            results = []
            for group in groups:
                results.append({
                    'name': group['name'],
                    'movie_id': group['movie_ids'],
                    'show_id': group['show_ids']
                })
                
            remaining_movie = [g for g in movie_genres if g.get('id') not in grouped_movie_ids]
            remaining_show = [g for g in show_genres if g.get('id') not in grouped_show_ids]
            
            merged = {}
            for g in remaining_movie:
                name = g.get('name')
                if name:
                    key = name.lower().strip()
                    merged[key] = {
                        'name': name,
                        'movie_id': g.get('id'),
                        'show_id': None
                    }
            for g in remaining_show:
                name = g.get('name')
                if name:
                    key = name.lower().strip()
                    if key in merged:
                        merged[key]['show_id'] = g.get('id')
                    else:
                        merged[key] = {
                            'name': name,
                            'movie_id': None,
                            'show_id': g.get('id')
                        }
            
            results.extend(merged.values())
            results.sort(key=lambda x: x['name'])
            
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
    def load_genre_results_combined(self, movie_id, show_id, genre_name, page=1):
        try:
            from lib.ff.tmdb import tmdb
            
            movies = []
            shows = []
            
            if movie_id:
                if isinstance(movie_id, list):
                    genre_query = "|".join(str(x) for x in movie_id)
                else:
                    genre_query = str(movie_id)
                movies.extend(list(tmdb.discover('movie', with_genres=genre_query, page=page))[:30])
                    
            if show_id:
                if isinstance(show_id, list):
                    genre_query = "|".join(str(x) for x in show_id)
                else:
                    genre_query = str(show_id)
                shows.extend(list(tmdb.discover('show', with_genres=genre_query, page=page))[:30])
                
            results = []
            for m, s in zip(movies, shows):
                results.append(m)
                results.append(s)
            if len(movies) > len(shows):
                results.extend(movies[len(shows):])
            elif len(shows) > len(movies):
                results.extend(shows[len(movies):])
                
            m_str = ",".join(str(x) for x in movie_id) if isinstance(movie_id, list) else str(movie_id)
            s_str = ",".join(str(x) for x in show_id) if isinstance(show_id, list) else str(show_id)
            menu_id = f"genre-combined:{m_str}:{s_str}"
            self.app.call_from_thread(self.show_genre_results, results, menu_id, genre_name, page=page)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            m_str = ",".join(str(x) for x in movie_id) if isinstance(movie_id, list) else str(movie_id)
            s_str = ",".join(str(x) for x in show_id) if isinstance(show_id, list) else str(show_id)
            menu_id = f"genre-combined:{m_str}:{s_str}"
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
        table.add_column("Tytuł", key="title")
        table.add_column("Gatunek", key="genre", width=17)
        table.add_column("Rok", key="year", width=5)
        table.add_column("Typ", key="type", width=7)

    def update_table_rows(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear()
        
        # Row 0 is the back option in genre results mode
        if self.current_menu_id.startswith("genre-"):
            table.add_row(
                "⬅️ Powrót do listy gatunków",
                "—",
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
                
            title = self.get_display_title(i, item)
            
            genre = self.get_genre_string(item)
                
            table.add_row(
                title, 
                genre,
                str(item.year or '????'), 
                itype, 
                key=str(i)
            )
            
        inp = self.query_one("#search-input", Input)
        inp.disabled = False
        if self.current_menu_id != "menu-search" and table.row_count > 0:
            table.focus()
            
        self.recalculate_column_widths()

    def show_results(self, results, menu_id, progress_map=None, page=1):
        if self.current_menu_id != menu_id:
            return
            
        self.results = results
        self.progress_map = progress_map or {}
        self.current_page = page
        self.romanized_refs.clear()
        self.prepare_media_table()
        self.update_table_rows()
        self.fetch_romanized_titles(results, menu_id)

    def update_genres_table_rows(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Nazwa gatunku", "Typ")
        
        for i, genre in enumerate(self.results):
            movie_id = genre.get('movie_id')
            show_id = genre.get('show_id')
            if self.media_filter == "movie" and not movie_id:
                continue
            if self.media_filter == "show" and not show_id:
                continue
                
            types = []
            if movie_id:
                types.append("Filmy")
            if show_id:
                types.append("Seriale")
            itype = " + ".join(types)
            table.add_row(
                sanitize_title(genre.get('name', 'Nieznany')),
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
        self.romanized_refs.clear()
        
        self.prepare_media_table()
        self.update_table_rows()
        self.fetch_romanized_titles(self.results, menu_id)

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
            elif menu_id.startswith("genre-combined:"):
                parts = menu_id.split(":")
                movie_ids_str = parts[1]
                show_ids_str = parts[2]
                
                movies = []
                shows = []
                
                if movie_ids_str and movie_ids_str != "None":
                    genre_query = movie_ids_str.replace(",", "|")
                    movies.extend(list(tmdb.discover('movie', with_genres=genre_query, page=page))[:30])
                        
                if show_ids_str and show_ids_str != "None":
                    genre_query = show_ids_str.replace(",", "|")
                    shows.extend(list(tmdb.discover('show', with_genres=genre_query, page=page))[:30])
                
                results = []
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
                
            title = self.get_display_title(idx, item)
            genre = self.get_genre_string(item)
                
            table.add_row(
                title, 
                genre,
                str(item.year or '????'), 
                itype, 
                key=str(idx)
            )
            
        self.is_loading_more = False
        self.recalculate_column_widths()
        self.fetch_romanized_titles(self.results, menu_id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = int(event.row_key.value)
        
        # 1. If in genre list mode
        if self.current_menu_id == "menu-genres":
            genre = self.results[idx]
            genre_name = genre['name']
            movie_id = genre.get('movie_id')
            show_id = genre.get('show_id')
            
            if not movie_id and show_id:
                movie_id = self.get_equivalent_genre_id(show_id, 'show')
            if not show_id and movie_id:
                show_id = self.get_equivalent_genre_id(movie_id, 'movie')
                
            m_str = ",".join(str(x) for x in movie_id) if isinstance(movie_id, list) else str(movie_id)
            s_str = ",".join(str(x) for x in show_id) if isinstance(show_id, list) else str(show_id)
            self.current_menu_id = f"genre-combined:{m_str}:{s_str}"
            
            # Reset page states for genre results
            self.current_page = 1
            self.is_loading_more = False
            self.has_more = True
            
            table = self.query_one("#results-table", DataTable)
            table.clear()
            
            self.load_genre_results_combined(movie_id, show_id, genre_name)
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

    def get_display_title(self, i, item):
        title = item.title
        if item.ref.is_episode:
            show_title = item.vtag.getEnglishTvShowTitle() or item.vtag.getTvShowTitle() or "Serial"
            title = f"{show_title} - S{item.season:02d}E{item.episode:02d}"
            
        title = sanitize_title(title)
        
        if self.current_menu_id == "menu-progress":
            pct = self.progress_map.get(str(i), "0%")
            title = f"{title} ({pct})"
            
        return title

    @work(thread=True)
    def fetch_romanized_titles(self, results, menu_id):
        local_results = list(results)
        from tui.helpers import is_latin_only
        from lib.ff.tmdb import tmdb
        
        indices_to_fetch = []
        for i, item in enumerate(local_results):
            if item is None or isinstance(item, dict):
                continue
            if item.ref in self.romanized_refs:
                continue
                
            title = item.title
            if item.ref.is_episode:
                show_title = item.vtag.getEnglishTvShowTitle() or item.vtag.getTvShowTitle() or "Serial"
                title = f"{show_title} - S{item.season:02d}E{item.episode:02d}"
                
            if not is_latin_only(title):
                indices_to_fetch.append((i, item))
                
        if not indices_to_fetch:
            return
            
        # Add to set so we don't request them again
        for _, item in indices_to_fetch:
            self.romanized_refs.add(item.ref)
            
        refs = [it.ref for _, it in indices_to_fetch]
        try:
            en_items = tmdb.get_skel_en_media(refs)
            for i, item in indices_to_fetch:
                if self.current_menu_id != menu_id:
                    break
                    
                ref = item.ref
                en_data = en_items.get(ref)
                if en_data:
                    en_title = en_data.get('name') or en_data.get('title')
                    if en_title:
                        if ref.is_episode:
                            item.vtag.setEnglishTvShowTitle(en_title)
                        else:
                            item.title = en_title
                        
                        self.app.call_from_thread(self.update_single_title, i, menu_id)
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)

    def update_single_title(self, index, menu_id):
        if self.current_menu_id != menu_id:
            return
        table = self.query_one("#results-table", DataTable)
        try:
            item = self.results[index]
            new_title = self.get_display_title(index, item)
            col_key = list(table.columns.keys())[0]
            table.update_cell(str(index), col_key, new_title)
            
            if self.highlighted_idx == index:
                self.query_one("#meta-panel").update_meta(item)
        except Exception:
            pass


