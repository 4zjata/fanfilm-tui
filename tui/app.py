import os
from textual.app import App
from textual.command import Provider, Hit, DiscoveryHit

from lib.ff.settings import settings
from tui.screens.home import HomeScreen

class FanFilmCommands(Provider):
    async def discover(self):
        commands = [
            ("Szukaj (Search)", self.app.action_goto_search),
            ("Ustawienia (Settings)", self.app.action_goto_settings),
            ("Pobierane (Downloads)", self.app.action_goto_downloads),
            ("Wyjście (Quit)", self.app.action_quit),
        ]
        for name, callback in commands:
            yield DiscoveryHit(name, callback, help="Nawigacja")

    async def search(self, query: str):
        matcher = self.matcher(query)
        
        commands = [
            ("Szukaj (Search)", self.app.action_goto_search),
            ("Ustawienia (Settings)", self.app.action_goto_settings),
            ("Pobierane (Downloads)", self.app.action_goto_downloads),
            ("Wyjście (Quit)", self.app.action_quit),
        ]
        
        for name, callback in commands:
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help="Nawigacja")

class FanFilmApp(App):
    CSS = """
    Screen { layout: vertical; }
    BaseScreen > Horizontal { height: 1fr; }
    #left-pane { width: 60%; border-right: tall $accent; padding: 1; overflow-y: auto; }
    #right-pane { width: 40%; padding: 1; }
    DataTable { height: 1fr; margin-top: 1; }
    #search-input { margin-bottom: 1; }
    #poster-container { align: center middle; height: 1fr; margin-top: 1; }
    #poster { width: 28; height: 21; }
    .title-label { text-style: bold; color: cyan; margin-bottom: 1; }
    #scraping-pane, #download-pane { align: center middle; content-align: center middle; padding: 2; height: 100%; }
    #downloads-pane, #scraper-status-pane { padding: 1; height: 100%; }
    ProgressBar { width: 80%; margin: 1; }
    
    SettingsScreen {
        align: center middle;
    }
    #settings-pane {
        width: 75;
        height: auto;
        max-height: 90%;
        border: double $accent;
        background: $panel;
        padding: 1 2;
        overflow-y: auto;
    }
    #settings-buttons {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #settings-pane Label {
        margin-top: 1;
        margin-bottom: 0;
        text-style: bold;
        color: $text;
    }
    #settings-pane Select {
        margin-bottom: 1;
    }
    #settings-pane Input {
        margin-bottom: 1;
    }
    #settings-pane Button {
        margin-top: 1;
        margin-right: 1;
    }
    
    CommandPalette {
        background: rgba(0, 0, 0, 0.4);
    }
    CommandPalette.-ready > Vertical {
        visibility: visible;
        width: 60%;
        height: auto;
        max-height: 15;
        border: double $accent;
        background: $panel;
        margin-top: 3;
    }
    CommandPalette #--results {
        overlay: none;
    }
    """

    COMMANDS = {FanFilmCommands}
    BINDINGS = [
        ("q", "quit", "Wyjście"),
        ("ctrl+p", "command_palette", "Menu"),
    ]

    def setup_settings(self):
        if not settings.getString("tui.theme"):
            settings.set("tui.theme", "textual-dark")
        if not settings.getString("tui.poster.type"):
            settings.set("tui.poster.type", "auto")

        if not settings.getString("movie.download.path") or not settings.getString("tv.download.path"):
            path = os.path.abspath("./downloads")
            os.makedirs(path, exist_ok=True)
            settings.set("movie.download.path", path)
            settings.set("tv.download.path", path)
            settings.set("downloads", "true")
            settings.set("download.show_manager", "false")

        lang = settings.getString("providers.lang")
        if not lang:
            settings.set("providers.lang", "Polish+English")
            lang = "Polish+English"

        ENGLISH_PROVIDERS = [
            "african", "animerealms", "dahmermovies", "embed2", "filmlinks4u", 
            "goojara", "multivid", "onlyflix", "streamimdb", "videasy", 
            "vidlink", "vidzee", "vixsrc", "vsembed_vidsrc", "webstreamr", "yesmovies"
        ]
        POLISH_PROVIDERS = [
            "adapterpl", "animezone", "bajeczki24", "cda", "cdahd", "desuonline", 
            "docchi", "dramaclub24", "dramaqueen", "ekinotv", "filman", "filmoteka", 
            "frixysubs", "ninateka", "obejrzyj_filmy", "ogladajcc", "rapideo_nopremium_twojlimit", 
            "shinden", "starekino", "tb7_xt7", "tvpvod", "vestroiakr", "vodpl", 
            "wrzucaj", "youtube_channels", "zaluknijcc"
        ]

        if lang == "Polish":
            for p in ENGLISH_PROVIDERS:
                settings.set(f"provider.{p}", "false")
            for p in POLISH_PROVIDERS:
                settings.set(f"provider.{p}", "true")
        elif lang == "English":
            for p in ENGLISH_PROVIDERS:
                settings.set(f"provider.{p}", "true")
            for p in POLISH_PROVIDERS:
                settings.set(f"provider.{p}", "false")
        else: # Polish+English
            for p in ENGLISH_PROVIDERS:
                settings.set(f"provider.{p}", "true")
            for p in POLISH_PROVIDERS:
                settings.set(f"provider.{p}", "true")

    def on_mount(self):
        from tui.server import CloudflareServer
        self.cf_server = CloudflareServer(self)
        self.cf_server.start()
        
        self.setup_settings()
        
        # Load theme from settings
        theme_val = settings.getString("tui.theme")
        if not theme_val:
            theme_val = "textual-dark"
        self.theme = theme_val
        
        self.push_screen(HomeScreen())

    def on_unmount(self):
        if hasattr(self, 'cf_server') and self.cf_server:
            self.cf_server.stop()

    def action_goto_search(self):
        self.push_screen(HomeScreen(start_search=True))

    def action_goto_settings(self):
        # We will implement this screen next
        from tui.screens.settings import SettingsScreen
        self.push_screen(SettingsScreen())

    def action_goto_downloads(self):
        # We will implement this screen next
        from tui.screens.downloads import DownloadsScreen
        self.push_screen(DownloadsScreen())
