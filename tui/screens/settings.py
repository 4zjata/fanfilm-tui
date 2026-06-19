from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Input, Select, Button, Footer
from textual.screen import Screen

from lib.ff.settings import settings

class SettingsScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Powrót")]

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-pane"):
            yield Label("Ustawienia", classes="title-label")
            
            yield Label("Język źródeł (Scrapery):")
            lang_options = [("Tylko Polskie", "Polish"), ("Tylko Angielskie", "English"), ("Polskie i Angielskie", "Polish+English")]
            yield Select(lang_options, id="lang-select", allow_blank=False)
            
            yield Label("Katalog pobierania filmów:")
            yield Input(id="movie-path")
            
            yield Label("Katalog pobierania seriali:")
            yield Input(id="tv-path")
            
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
            
            yield Label("--- Konfiguracja Torrent & Torrentio ---", classes="title-label")
            
            yield Label("Torrentio Base URL (Config):")
            yield Input(id="torrentio-url")
            
            yield Label("Silnik strumieniowania torrentów:")
            engine_options = [
                ("qBittorrent (Lokalny)", "qbittorrent"),
                ("WebTorrent CLI (npx)", "webtorrent")
            ]
            yield Select(engine_options, id="engine-select", allow_blank=False)
            
            yield Label("qBittorrent WebUI URL:")
            yield Input(id="qb-url")
            
            yield Label("qBittorrent Użytkownik:")
            yield Input(id="qb-username")
            
            yield Label("qBittorrent Hasło:")
            yield Input(id="qb-password", password=True)
            
            with Horizontal(id="settings-buttons"):
                yield Button("Zapisz", variant="success", id="save-btn")
                yield Button("Anuluj", variant="error", id="cancel-btn")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        lang_val = settings.getString("providers.lang")
        if not lang_val: lang_val = "Polish+English"
        
        poster_val = settings.getString("tui.poster.type")
        if not poster_val: poster_val = "auto"

        theme_val = settings.getString("tui.theme")
        if not theme_val: theme_val = "textual-dark"
        
        adv_val = settings.getString("tui.advanced_mode")
        if not adv_val: adv_val = "false"
        
        torrentio_url = settings.getString("torrentio.base_url")
        if not torrentio_url: torrentio_url = "https://torrentio.strem.fun"
        
        engine_val = settings.getString("torrent.engine")
        if not engine_val: engine_val = "qbittorrent"
        
        qb_url = settings.getString("qbittorrent.url")
        if not qb_url: qb_url = "http://localhost:8080"
        
        qb_username = settings.getString("qbittorrent.username")
        qb_password = settings.getString("qbittorrent.password")
        
        self.query_one("#lang-select", Select).value = lang_val
        self.query_one("#movie-path", Input).value = settings.getString("movie.download.path")
        self.query_one("#tv-path", Input).value = settings.getString("tv.download.path")
        self.query_one("#poster-select", Select).value = poster_val
        self.query_one("#theme-select", Select).value = theme_val
        self.query_one("#advanced-select", Select).value = adv_val
        
        self.query_one("#torrentio-url", Input).value = torrentio_url
        self.query_one("#engine-select", Select).value = engine_val
        self.query_one("#qb-url", Input).value = qb_url
        self.query_one("#qb-username", Input).value = qb_username
        self.query_one("#qb-password", Input).value = qb_password

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            lang_val = self.query_one("#lang-select", Select).value
            movie_path = self.query_one("#movie-path", Input).value
            tv_path = self.query_one("#tv-path", Input).value
            poster_val = self.query_one("#poster-select", Select).value
            theme_val = self.query_one("#theme-select", Select).value
            adv_val = self.query_one("#advanced-select", Select).value
            
            torrentio_url = self.query_one("#torrentio-url", Input).value
            engine_val = self.query_one("#engine-select", Select).value
            qb_url = self.query_one("#qb-url", Input).value
            qb_username = self.query_one("#qb-username", Input).value
            qb_password = self.query_one("#qb-password", Input).value
            
            settings.set("providers.lang", lang_val)
            settings.set("movie.download.path", movie_path)
            settings.set("tv.download.path", tv_path)
            settings.set("tui.poster.type", poster_val)
            settings.set("tui.theme", theme_val)
            settings.set("tui.advanced_mode", adv_val)
            
            settings.set("torrentio.base_url", torrentio_url)
            settings.set("torrent.engine", engine_val)
            settings.set("qbittorrent.url", qb_url)
            settings.set("qbittorrent.username", qb_username)
            settings.set("qbittorrent.password", qb_password)
            
            # Re-run setup to re-configure enabled providers
            self.app.setup_settings()
            
            # Apply theme immediately
            self.app.theme = theme_val
            
            self.notify("Zapisano ustawienia", severity="information")
            self.app.pop_screen()
        elif event.button.id == "cancel-btn":
            self.app.pop_screen()
