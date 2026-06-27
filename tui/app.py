import os
from textual.app import App
from textual.command import Provider, Hit, DiscoveryHit, CommandPalette

from lib.ff.settings import settings
from tui.screens.home import HomeScreen

class FanFilmCommands(Provider):
    async def discover(self):
        commands = [
            ("\uf015 Start\n", self.app.action_goto_trending),
            ("\U000f0422 Popularne\n", self.app.action_goto_popular),
            ("\uf005 Najlepsze\n", self.app.action_goto_top_rated),
            ("\U000f040d Gatunki\n", self.app.action_goto_genres),
            ("\uf252 W toku\n", self.app.action_goto_progress),
            ("\uf002 Szukaj\n", self.app.action_goto_search),
            ("\uf019 Pobierane\n", self.app.action_goto_downloads),
            ("\uf013 Ustawienia\n", self.app.action_goto_settings),
            ("\uf011 Wyjście\n", self.app.action_quit),
        ]
        for name, callback in commands:
            yield DiscoveryHit(name, callback)

    async def search(self, query: str):
        matcher = self.matcher(query)
        
        commands = [
            ("\uf015 Start\n", self.app.action_goto_trending),
            ("\U000f0422 Popularne\n", self.app.action_goto_popular),
            ("\uf005 Najlepsze\n", self.app.action_goto_top_rated),
            ("\U000f040d Gatunki\n", self.app.action_goto_genres),
            ("\uf252 W toku\n", self.app.action_goto_progress),
            ("\uf002 Szukaj\n", self.app.action_goto_search),
            ("\uf019 Pobierane\n", self.app.action_goto_downloads),
            ("\uf013 Ustawienia\n", self.app.action_goto_settings),
            ("\uf011 Wyjście\n", self.app.action_quit),
        ]
        
        for name, callback in commands:
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback)

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
        height: 34;
        max-height: 90%;
        border: double $accent;
        background: $panel;
        padding: 1 2;
    }
    #settings-content {
        height: 1fr;
        overflow-y: auto;
        margin-top: 1;
        padding-right: 1;
    }
    #pane-general, #pane-paths, #pane-torrents, #pane-discord {
        height: auto;
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
        background: rgba(0, 0, 0, 0.65);
    }
    CommandPalette.-ready > Vertical {
        visibility: visible;
        width: 60%;
        height: 21;
        border: solid $accent;
        background: $panel;
        margin-top: 3;
        overflow: hidden;
    }
    CommandPalette #--input {
        border: none;
        border-bottom: solid $accent;
        background: transparent;
        height: 3;
        margin: 0;
        padding: 0 1;
    }
    CommandPalette #--input Label {
        display: none;
    }
    CommandPalette #--results {
        overlay: none;
        height: 1fr;
    }
    CommandPalette CommandList {
        height: 1fr;
        background: transparent;
    }
    CommandPalette CommandList > .option-list--option-highlighted {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    CommandPalette LoadingIndicator {
        display: none;
    }
    CommandPalette LoadingIndicator.--visible {
        display: block;
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
        if not settings.getString("tui.discord_rpc_enabled"):
            settings.set("tui.discord_rpc_enabled", "true")
        if not settings.getString("tui.discord_client_id"):
            settings.set("tui.discord_client_id", "1517667151920496821")
        if not settings.getString("tui.discord_show_menu"):
            settings.set("tui.discord_show_menu", "true")
        if not settings.getString("tui.discord_show_scraping"):
            settings.set("tui.discord_show_scraping", "true")
        if not settings.getString("tui.discord_show_watching"):
            settings.set("tui.discord_show_watching", "true")
        if not settings.getString("tui.discord_show_time"):
            settings.set("tui.discord_show_time", "true")
        if not settings.getString("tui.discord_show_images"):
            settings.set("tui.discord_show_images", "true")
        if not settings.getString("tui.menu_sidebar_width"):
            settings.set("tui.menu_sidebar_width", "28")
        if not settings.getString("tui.right_pane_width"):
            settings.set("tui.right_pane_width", "40")
        if not settings.getString("tui.menu_type"):
            settings.set("tui.menu_type", "sidebar")

        # Torrentio & Streaming defaults
        if not settings.getString("torrentio.enabled"):
            settings.set("torrentio.enabled", "true")
        if not settings.getString("torrentio.base_url"):
            settings.set("torrentio.base_url", "https://torrentio.strem.fun")
        if not settings.getString("torrent.engine"):
            settings.set("torrent.engine", "qbittorrent")
        if not settings.getString("qbittorrent.url"):
            settings.set("qbittorrent.url", "http://localhost:8080")
        if not settings.getString("torrent.buffering_threshold") or settings.getString("torrent.buffering_threshold") == "1.0":
            settings.set("torrent.buffering_threshold", "5.0")
        
        if not settings.getString("qbittorrent.username"):
            qb_user = "admin"
            try:
                for filename in ["qBittorrent.conf", "qBittorrent-nox.conf"]:
                    path = os.path.expanduser(f"~/.config/qBittorrent/{filename}")
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            for line in f:
                                if "WebUI\\Username=" in line:
                                    qb_user = line.split("=", 1)[1].strip()
                                    break
            except Exception:
                pass
            settings.set("qbittorrent.username", qb_user)

        if not settings.getString("qbittorrent.password"):
            settings.set("qbittorrent.password", "")

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
            "goojara", "multivid", "onlyflix", "seadex", "streamimdb", "torrentio", "torrentio_anime", "videasy", 
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

        # Discord RPC
        from tui.discord_rpc import DiscordRPCManager
        rpc_enabled = settings.getString("tui.discord_rpc_enabled") != "false"
        rpc_client_id = settings.getString("tui.discord_client_id") or "1517667151920496821"
        self.discord_rpc = DiscordRPCManager(client_id=rpc_client_id, enabled=rpc_enabled)
        self.discord_rpc.start()
        
        self.push_screen(HomeScreen())

    def on_unmount(self):
        if hasattr(self, 'cf_server') and self.cf_server:
            self.cf_server.stop()
        if hasattr(self, 'discord_rpc') and self.discord_rpc:
            self.discord_rpc.shutdown()

    def action_command_palette(self) -> None:
        if self.use_command_palette and not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(id="--command-palette", placeholder=""))

    def action_goto_trending(self):
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.select_menu_option("menu-trending")
        else:
            self.switch_screen(HomeScreen(start_menu_id="menu-trending"))

    def action_goto_popular(self):
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.select_menu_option("menu-popular")
        else:
            self.switch_screen(HomeScreen(start_menu_id="menu-popular"))

    def action_goto_top_rated(self):
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.select_menu_option("menu-top-rated")
        else:
            self.switch_screen(HomeScreen(start_menu_id="menu-top-rated"))

    def action_goto_genres(self):
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.select_menu_option("menu-genres")
        else:
            self.switch_screen(HomeScreen(start_menu_id="menu-genres"))

    def action_goto_progress(self):
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.select_menu_option("menu-progress")
        else:
            self.switch_screen(HomeScreen(start_menu_id="menu-progress"))

    def action_goto_search(self):
        while len(self.screen_stack) > 2:
            self.pop_screen()
            
        if isinstance(self.screen, HomeScreen):
            self.screen.select_menu_option("menu-search")
        else:
            self.switch_screen(HomeScreen(start_search=True))

    def action_goto_settings(self):
        # We will implement this screen next
        from tui.screens.settings import SettingsScreen
        self.push_screen(SettingsScreen())

    def action_goto_downloads(self):
        # We will implement this screen next
        from tui.screens.downloads import DownloadsScreen
        self.push_screen(DownloadsScreen())
