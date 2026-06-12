from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Input, Select, Button

from tui.screens.base import BaseScreen
from lib.ff.settings import settings

class SettingsScreen(BaseScreen):
    BINDINGS = [("escape", "app.pop_screen", "Powrót")]

    def compose_left(self) -> ComposeResult:
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
            
            with Horizontal():
                yield Button("Zapisz", variant="success", id="save-btn")
                yield Button("Anuluj", variant="error", id="cancel-btn")

    def on_mount(self) -> None:
        lang_val = settings.getString("providers.lang")
        if not lang_val: lang_val = "Polish+English"
        
        poster_val = settings.getString("tui.poster.type")
        if not poster_val: poster_val = "auto"
        
        self.query_one("#lang-select", Select).value = lang_val
        self.query_one("#movie-path", Input).value = settings.getString("movie.download.path")
        self.query_one("#tv-path", Input).value = settings.getString("tv.download.path")
        self.query_one("#poster-select", Select).value = poster_val

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            lang_val = self.query_one("#lang-select", Select).value
            movie_path = self.query_one("#movie-path", Input).value
            tv_path = self.query_one("#tv-path", Input).value
            poster_val = self.query_one("#poster-select", Select).value
            
            settings.set("providers.lang", lang_val)
            settings.set("movie.download.path", movie_path)
            settings.set("tv.download.path", tv_path)
            settings.set("tui.poster.type", poster_val)
            
            # Re-run setup to re-configure enabled providers
            self.app.setup_settings()
            
            self.notify("Zapisano ustawienia", severity="information")
            self.app.pop_screen()
        elif event.button.id == "cancel-btn":
            self.app.pop_screen()
