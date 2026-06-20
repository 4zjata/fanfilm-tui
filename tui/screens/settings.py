from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Label, Input, Select, Button, Footer, Tabs, Tab
from textual.screen import Screen

from lib.ff.settings import settings

class SettingsScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Powrót")]

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-pane"):
            yield Label("Ustawienia", classes="title-label")
            
            yield Tabs(
                Tab("Ogólne", id="tab-general"),
                Tab("Ścieżki", id="tab-paths"),
                Tab("Torrenty", id="tab-torrents"),
            )
            
            with VerticalScroll(id="settings-content"):
                with Vertical(id="pane-general"):
                    yield Label("Język źródeł (Scrapery):")
                    lang_options = [("Tylko Polskie", "Polish"), ("Tylko Angielskie", "English"), ("Polskie i Angielskie", "Polish+English")]
                    yield Select(lang_options, id="lang-select", allow_blank=False)
                    
                    yield Label("Styl wyświetlania plakatów:")
                    poster_options = [
                        ("Automatyczny (Detekcja)", "auto"),
                        ("SIXEL (Wysoka rozdzielczość)", "sixel"),
                        ("Kitty Protocol (Wysoka rozdzielczość)", "kitty"),
                        ("Blokowy (Unicode Halfcell)", "halfcell"),
                        ("Tekstowy (ASCII Art)", "ascii")
                    ]
                    yield Select(poster_options, id="poster-select", allow_blank=False)

                    yield Label("Motyw kolorystyczny TUI:")
                    theme_options = [
                        ("Textual Dark (Ciemny)", "textual-dark"),
                        ("Textual Light (Jasny)", "textual-light"),
                        ("Tokyo Night (Tokio)", "tokyo-night"),
                        ("Dracula (Wampir)", "dracula"),
                        ("Nord (Północ)", "nord"),
                        ("Gruvbox (Retro)", "gruvbox"),
                        ("Catppuccin Mocha (Kawa)", "catppuccin-mocha"),
                        ("Monokai (Klasyczny)", "monokai"),
                        ("Solarized Dark", "solarized-dark"),
                        ("Rose Pine (Różany)", "rose-pine")
                    ]
                    yield Select(theme_options, id="theme-select", allow_blank=False)
                    
                    yield Label("Tryb zaawansowany scraperów:")
                    adv_options = [
                        ("Wyłączony (Standardowy)", "false"),
                        ("Włączony (Pokazuj błędy)", "true")
                    ]
                    yield Select(adv_options, id="advanced-select", allow_blank=False)

                    yield Label("Włącz Discord RPC (Status aktywności):")
                    rpc_options = [
                        ("Włączony", "true"),
                        ("Wyłączony", "false")
                    ]
                    yield Select(rpc_options, id="rpc-select", allow_blank=False)

                    yield Label("Discord Client ID (Opcjonalny):")
                    yield Input(placeholder="Wpisz własny Client ID...", id="rpc-client-id")

                    yield Label("Discord RPC - Pokazuj aktywność w menu:")
                    yield Select([("Tak", "true"), ("Nie", "false")], id="rpc-menu-select", allow_blank=False)

                    yield Label("Discord RPC - Pokazuj szukanie źródeł:")
                    yield Select([("Tak", "true"), ("Nie", "false")], id="rpc-scraping-select", allow_blank=False)

                    yield Label("Discord RPC - Pokazuj szczegóły oglądania:")
                    yield Select([("Tak", "true"), ("Nie", "false")], id="rpc-watching-select", allow_blank=False)

                    yield Label("Discord RPC - Pokazuj pozostały czas:")
                    yield Select([("Tak", "true"), ("Nie", "false")], id="rpc-time-select", allow_blank=False)

                    yield Label("Discord RPC - Pokazuj ikony (grafiki):")
                    yield Select([("Tak", "true"), ("Nie", "false")], id="rpc-images-select", allow_blank=False)
                    
                with Vertical(id="pane-paths"):
                    yield Label("Katalog pobierania filmów:")
                    yield Input(id="movie-path")
                    
                    yield Label("Katalog pobierania seriali:")
                    yield Input(id="tv-path")
                    
                with Vertical(id="pane-torrents"):
                    yield Label("Silnik strumieniowania torrentów:")
                    engine_options = [
                        ("qBittorrent (Lokalny)", "qbittorrent"),
                        ("WebTorrent CLI (npx)", "webtorrent")
                    ]
                    yield Select(engine_options, id="engine-select", allow_blank=False)

                    yield Label("Torrentio Base URL (Config):")
                    yield Input(id="torrentio-url")
                    
                    yield Label("qBittorrent WebUI URL:")
                    yield Input(id="qb-url")
                    
                    yield Label("qBittorrent Użytkownik:")
                    yield Input(id="qb-username")
                    
                    yield Label("qBittorrent Hasło:")
                    yield Input(id="qb-password", password=True)

                    yield Label("Próg buforowania torrenta (Procent) [np. 5.0]:")
                    yield Input(id="buffering-threshold-input")

                    yield Label("Automatyczne limity seedowania:")
                    seeding_limit_options = [
                        ("Wyłączone (Seeding bez limitu)", "false"),
                        ("Włączone (Zgodnie z limitami poniżej)", "true")
                    ]
                    yield Select(seeding_limit_options, id="seeding-limits-select", allow_blank=False)
                    
                    yield Label("Maksymalny współczynnik (Ratio) [np. 1.0]:")
                    yield Input(id="ratio-limit-input")
                    
                    yield Label("Maksymalny czas seedowania (Godziny) [np. 168]:")
                    yield Input(id="time-limit-input")
                    
                    yield Label("Akcja po przekroczeniu limitu:")
                    action_options = [
                        ("Zatrzymaj seedowanie (Pauza)", "stop"),
                        ("Usuń torrent i pobrane pliki", "delete")
                    ]
                    yield Select(action_options, id="seeding-action-select", allow_blank=False)
            
            with Horizontal(id="settings-buttons"):
                yield Button("Zapisz", variant="success", id="save-btn")
                yield Button("Anuluj", variant="error", id="cancel-btn")
        yield Footer(show_command_palette=False)

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.update_panes(event.tab.id)
        self.query_one("#settings-content").scroll_to(y=0, animate=False)

    def on_descendant_focus(self, event) -> None:
        try:
            widget = event.widget
            if widget:
                content = self.query_one("#settings-content")
                if content in widget.ancestors:
                    content.scroll_to_widget(widget, animate=True)
        except Exception:
            pass

    def update_panes(self, active_tab_id: str) -> None:
        self.query_one("#pane-general").display = (active_tab_id == "tab-general")
        self.query_one("#pane-paths").display = (active_tab_id == "tab-paths")
        self.query_one("#pane-torrents").display = (active_tab_id == "tab-torrents")

    def on_mount(self) -> None:
        lang_val = settings.getString("providers.lang")
        if not lang_val: lang_val = "Polish+English"
        
        poster_val = settings.getString("tui.poster.type")
        if not poster_val: poster_val = "auto"

        theme_val = settings.getString("tui.theme")
        if not theme_val: theme_val = "textual-dark"
        
        adv_val = settings.getString("tui.advanced_mode")
        if not adv_val: adv_val = "false"

        discord_rpc_enabled = settings.getString("tui.discord_rpc_enabled")
        if not discord_rpc_enabled: discord_rpc_enabled = "true"

        discord_client_id = settings.getString("tui.discord_client_id")
        if not discord_client_id: discord_client_id = "1517667151920496821"

        rpc_menu = settings.getString("tui.discord_show_menu")
        if not rpc_menu: rpc_menu = "true"

        rpc_scraping = settings.getString("tui.discord_show_scraping")
        if not rpc_scraping: rpc_scraping = "true"

        rpc_watching = settings.getString("tui.discord_show_watching")
        if not rpc_watching: rpc_watching = "true"

        rpc_time = settings.getString("tui.discord_show_time")
        if not rpc_time: rpc_time = "true"

        rpc_images = settings.getString("tui.discord_show_images")
        if not rpc_images: rpc_images = "false"
        
        torrentio_url = settings.getString("torrentio.base_url")
        if not torrentio_url: torrentio_url = "https://torrentio.strem.fun"
        
        engine_val = settings.getString("torrent.engine")
        if not engine_val: engine_val = "qbittorrent"
        
        qb_url = settings.getString("qbittorrent.url")
        if not qb_url: qb_url = "http://localhost:8080"
        
        qb_username = settings.getString("qbittorrent.username")
        qb_password = settings.getString("qbittorrent.password")

        seeding_limits_enabled = settings.getString("torrent.seeding_limits_enabled")
        if not seeding_limits_enabled: seeding_limits_enabled = "false"

        ratio_limit = settings.getString("torrent.ratio_limit")
        if not ratio_limit: ratio_limit = "1.0"

        time_limit = settings.getString("torrent.seeding_time_limit")
        if not time_limit: time_limit = "168"

        action_on_limit = settings.getString("torrent.action_on_limit")
        if not action_on_limit: action_on_limit = "stop"

        buffering_threshold = settings.getString("torrent.buffering_threshold")
        if not buffering_threshold: buffering_threshold = "5.0"
        
        self.query_one("#lang-select", Select).value = lang_val
        self.query_one("#movie-path", Input).value = settings.getString("movie.download.path")
        self.query_one("#tv-path", Input).value = settings.getString("tv.download.path")
        self.query_one("#poster-select", Select).value = poster_val
        self.query_one("#theme-select", Select).value = theme_val
        self.query_one("#advanced-select", Select).value = adv_val
        self.query_one("#rpc-select", Select).value = discord_rpc_enabled
        self.query_one("#rpc-client-id", Input).value = discord_client_id
        self.query_one("#rpc-menu-select", Select).value = rpc_menu
        self.query_one("#rpc-scraping-select", Select).value = rpc_scraping
        self.query_one("#rpc-watching-select", Select).value = rpc_watching
        self.query_one("#rpc-time-select", Select).value = rpc_time
        self.query_one("#rpc-images-select", Select).value = rpc_images
        self.query_one("#rpc-client-id", Input).value = discord_client_id
        
        self.query_one("#torrentio-url", Input).value = torrentio_url
        self.query_one("#engine-select", Select).value = engine_val
        self.query_one("#qb-url", Input).value = qb_url
        self.query_one("#qb-username", Input).value = qb_username
        self.query_one("#qb-password", Input).value = qb_password

        self.query_one("#seeding-limits-select", Select).value = seeding_limits_enabled
        self.query_one("#ratio-limit-input", Input).value = ratio_limit
        self.query_one("#time-limit-input", Input).value = time_limit
        self.query_one("#seeding-action-select", Select).value = action_on_limit
        self.query_one("#buffering-threshold-input", Input).value = buffering_threshold

        self.update_panes("tab-general")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            lang_val = self.query_one("#lang-select", Select).value
            movie_path = self.query_one("#movie-path", Input).value
            tv_path = self.query_one("#tv-path", Input).value
            poster_val = self.query_one("#poster-select", Select).value
            theme_val = self.query_one("#theme-select", Select).value
            adv_val = self.query_one("#advanced-select", Select).value
            rpc_enabled = self.query_one("#rpc-select", Select).value
            rpc_client_id = self.query_one("#rpc-client-id", Input).value
            if not rpc_client_id or not rpc_client_id.strip():
                rpc_client_id = "1517667151920496821"
            rpc_menu = self.query_one("#rpc-menu-select", Select).value
            rpc_scraping = self.query_one("#rpc-scraping-select", Select).value
            rpc_watching = self.query_one("#rpc-watching-select", Select).value
            rpc_time = self.query_one("#rpc-time-select", Select).value
            rpc_images = self.query_one("#rpc-images-select", Select).value
            
            torrentio_url = self.query_one("#torrentio-url", Input).value
            engine_val = self.query_one("#engine-select", Select).value
            qb_url = self.query_one("#qb-url", Input).value
            qb_username = self.query_one("#qb-username", Input).value
            qb_password = self.query_one("#qb-password", Input).value

            seeding_limits_enabled = self.query_one("#seeding-limits-select", Select).value
            ratio_limit = self.query_one("#ratio-limit-input", Input).value
            time_limit = self.query_one("#time-limit-input", Input).value
            action_on_limit = self.query_one("#seeding-action-select", Select).value
            buffering_threshold = self.query_one("#buffering-threshold-input", Input).value

            # Validate input values
            try:
                float(ratio_limit)
            except ValueError:
                ratio_limit = "1.0"

            try:
                float(time_limit)
            except ValueError:
                time_limit = "168"

            try:
                val = float(buffering_threshold)
                if val < 0.0:
                    buffering_threshold = "1.0"
            except ValueError:
                buffering_threshold = "1.0"
            
            settings.set("providers.lang", lang_val)
            settings.set("movie.download.path", movie_path)
            settings.set("tv.download.path", tv_path)
            settings.set("tui.poster.type", poster_val)
            settings.set("tui.theme", theme_val)
            settings.set("tui.advanced_mode", adv_val)
            settings.set("tui.discord_rpc_enabled", rpc_enabled)
            settings.set("tui.discord_client_id", rpc_client_id)
            settings.set("tui.discord_show_menu", rpc_menu)
            settings.set("tui.discord_show_scraping", rpc_scraping)
            settings.set("tui.discord_show_watching", rpc_watching)
            settings.set("tui.discord_show_time", rpc_time)
            settings.set("tui.discord_show_images", rpc_images)

            if hasattr(self.app, "discord_rpc") and self.app.discord_rpc:
                self.app.discord_rpc.update_config(rpc_enabled == "true", rpc_client_id)
            
            settings.set("torrentio.base_url", torrentio_url)
            settings.set("torrent.engine", engine_val)
            settings.set("qbittorrent.url", qb_url)
            settings.set("qbittorrent.username", qb_username)
            settings.set("qbittorrent.password", qb_password)

            settings.set("torrent.seeding_limits_enabled", seeding_limits_enabled)
            settings.set("torrent.ratio_limit", ratio_limit)
            settings.set("torrent.seeding_time_limit", time_limit)
            settings.set("torrent.action_on_limit", action_on_limit)
            settings.set("torrent.buffering_threshold", buffering_threshold)
            
            # Re-run setup to re-configure enabled providers
            self.app.setup_settings()
            
            # Apply theme immediately
            self.app.theme = theme_val
            
            self.notify("Zapisano ustawienia", severity="information")
            self.app.pop_screen()
        elif event.button.id == "cancel-btn":
            self.app.pop_screen()
