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
        
        self.query_one("#lang-select", Select).value = lang_val
        self.query_one("#movie-path", Input).value = settings.getString("movie.download.path")
        self.query_one("#tv-path", Input).value = settings.getString("tv.download.path")
        self.query_one("#poster-select", Select).value = poster_val
        self.query_one("#theme-select", Select).value = theme_val
        self.query_one("#advanced-select", Select).value = adv_val

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            lang_val = self.query_one("#lang-select", Select).value
            movie_path = self.query_one("#movie-path", Input).value
            tv_path = self.query_one("#tv-path", Input).value
            poster_val = self.query_one("#poster-select", Select).value
            theme_val = self.query_one("#theme-select", Select).value
            adv_val = self.query_one("#advanced-select", Select).value
            
            settings.set("providers.lang", lang_val)
            settings.set("movie.download.path", movie_path)
            settings.set("tv.download.path", tv_path)
            settings.set("tui.poster.type", poster_val)
            settings.set("tui.theme", theme_val)
            settings.set("tui.advanced_mode", adv_val)
            
            # Re-run setup to re-configure enabled providers
            self.app.setup_settings()
            
            # Apply theme immediately
            self.app.theme = theme_val
            
            self.notify("Zapisano ustawienia", severity="information")
            self.app.pop_screen()
        elif event.button.id == "cancel-btn":
            self.app.pop_screen()
