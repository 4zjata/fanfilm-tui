# -*- coding: utf-8 -*-
"""
FanFilm TUI - Screen: Source Selection Redesign
Copyright (C) 2026 :)
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, DataTable, Label, Static, Tabs, Tab

from tui.screens.stream import StreamScreen
from tui.screens.download import DownloadScreen


class SourceSelectScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Powrót"),
        ("enter", "stream_source", "Streamuj (MPV)"),
        ("d", "download_source", "Pobierz"),
        ("t", "filter_torrents", "Zakładka: Torrenty"),
        ("w", "filter_web", "Zakładka: Webowe"),
        ("a", "filter_all", "Zakładka: Wszystko"),
        ("q", "cycle_quality", "Jakość"),
        ("l", "cycle_lang", "Język"),
    ]

    DEFAULT_CSS = """
    SourceSelectScreen {
        background: $panel;
    }
    #sources-header {
        height: 3;
        background: $primary-darken-1;
        color: $text;
        border-bottom: solid $primary-darken-2;
        padding: 0 2;
        content-align: center middle;
    }
    #sources-split {
        height: 1fr;
    }
    #sources-left {
        width: 65%;
        height: 100%;
    }
    #sources-right {
        width: 35%;
        height: 100%;
        padding: 1 2;
        background: $panel-lighten-1;
        border-left: solid $primary-darken-2;
    }
    .detail-title {
        text-style: bold;
        color: $primary;
        border-bottom: solid $primary;
        margin-bottom: 1;
    }
    .detail-label {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }
    .detail-val {
        color: $text;
    }
    #detail-filename-container {
        background: $panel-lighten-2;
        padding: 0 1;
        border: solid $accent;
        height: 5;
    }
    #detail-filename {
        color: $text;
    }
    #sources-table {
        height: 100%;
    }
    """

    def __init__(self, sources_list, queue):
        super().__init__()
        
        # Filter out dead torrents (seeds = 0) upfront
        clean_sources = []
        for s in sources_list:
            is_torrent = s.meta.get('local', False) or 'magnet:' in s.url.lower() or 'torrentio' in s.provider.lower()
            if is_torrent:
                seeds = s.meta.get('seeds')
                if seeds is not None:
                    try:
                        seeds = int(seeds)
                    except (ValueError, TypeError):
                        seeds = None
                if seeds is None:
                    info = s.meta.get('info', '')
                    if info:
                        import re
                        match = re.search(r'👤\s*(\d+)', info)
                        if match:
                            seeds = int(match.group(1))
                if seeds is not None and seeds <= 0:
                    # Skip dead torrent (0 seeds)
                    continue
            clean_sources.append(s)

        self.sources_list = clean_sources
        self.queue = queue
        self.filtered_sources = []
        
        # Filter states
        self.active_type_filter = "all"      # all / torrents / web
        self.active_quality_filter = "all"   # all / 1080p+ / 720p+
        self.active_lang_filter = "all"      # all / pl / en

        # Compute initial totals
        self.total_torrents = sum(
            1 for s in self.sources_list 
            if s.meta.get('local', False) or 'magnet:' in s.url.lower() or 'torrentio' in s.provider.lower()
        )
        self.total_web = len(self.sources_list) - self.total_torrents

    def compose(self) -> ComposeResult:
        yield Label("", id="sources-header")
        yield Tabs(
            Tab("Wszystko", id="tab-all"),
            Tab("Torrenty (P2P)", id="tab-torrents"),
            Tab("Webowe / Direct", id="tab-web"),
        )
        with Horizontal(id="sources-split"):
            with Vertical(id="sources-left"):
                yield DataTable(id="sources-table", cursor_type="row")
            with Vertical(id="sources-right"):
                yield Label("Szczegóły źródła", classes="detail-title")
                yield Label("Nazwa pliku / Opis:", classes="detail-label")
                with VerticalScroll(id="detail-filename-container"):
                    yield Label("", id="detail-filename")
                yield Label("Jakość:", classes="detail-label")
                yield Label("", id="detail-quality", classes="detail-val")
                yield Label("Język:", classes="detail-label")
                yield Label("", id="detail-language", classes="detail-val")
                yield Label("Dostawca:", classes="detail-label")
                yield Label("", id="detail-provider", classes="detail-val")
                yield Label("Hosting / Protokół:", classes="detail-label")
                yield Label("", id="detail-hosting", classes="detail-val")
                yield Label("Inne informacje:", classes="detail-label")
                yield Label("", id="detail-info", classes="detail-val")
        yield Footer(show_command_palette=False)

    def format_media_title(self) -> str:
        if not self.sources_list:
            return "Wybór źródła"
        ffitem = self.sources_list[0].ffitem
        if not ffitem:
            return "Wybór źródła"
        
        if ffitem.show_item:
            title = ffitem.show_item.title or ffitem.show_item.label
            season = getattr(ffitem, 'season', 1)
            episode = getattr(ffitem, 'episode', 1)
            return f"{title} S{season:02d}E{episode:02d}"
        
        title = ffitem.title or ffitem.label
        year = getattr(ffitem, 'year', '')
        year_str = f" ({year})" if year else ""
        return f"{title}{year_str}"

    def update_header(self) -> None:
        header = self.query_one("#sources-header", Label)
        
        type_str = {
            "all": "Wszystko",
            "torrents": "Tylko Torrenty",
            "web": "Tylko Webowe"
        }[self.active_type_filter]
        
        qual_str = self.active_quality_filter.upper()
        
        lang_str = {
            "all": "Wszystkie",
            "pl": "Tylko PL",
            "en": "Tylko EN"
        }[self.active_lang_filter]
        
        title_str = self.format_media_title()
        
        header.update(
            f"🎬 [bold]{title_str}[/bold]  |  "
            f"💾 Torrenty: [bold]{self.total_torrents}[/bold], Web: [bold]{self.total_web}[/bold]  |  "
            f"🔍 Typ: [yellow]{type_str}[/yellow], Jakość: [yellow]{qual_str}[/yellow], Język: [yellow]{lang_str}[/yellow]"
        )

    def on_mount(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.add_columns("Serwis / Typ", "Jakość", "Język", "Informacje")
        self.apply_filters()
        table.focus()

    def apply_filters(self) -> None:
        filtered = []
        for s in self.sources_list:
            # 1. Type filter
            is_torrent = s.meta.get('local', False) or 'magnet:' in s.url.lower() or 'torrentio' in s.provider.lower()
            if self.active_type_filter == 'torrents' and not is_torrent:
                continue
            if self.active_type_filter == 'web' and is_torrent:
                continue
            
            # 2. Quality filter
            qual = s.meta.get('quality', 'SD').lower()
            if self.active_quality_filter == '1080p+':
                if qual not in ('1080p', '4k', '2160p'):
                    continue
            elif self.active_quality_filter == '720p+':
                if qual not in ('720p', '1080p', '4k', '2160p'):
                    continue
            
            # 3. Language filter
            lang = s.meta.get('language', 'en').lower()
            if self.active_lang_filter == 'pl' and lang != 'pl':
                continue
            if self.active_lang_filter == 'en' and lang != 'en':
                continue
                
            filtered.append(s)
            
        self.filtered_sources = filtered
        self.update_table()

    def update_table(self) -> None:
        table = self.query_one("#sources-table", DataTable)
        table.clear()
        
        for i, s in enumerate(self.filtered_sources):
            lang = s.meta.get('language', s.get('language', '')).upper() or "EN"
            qual = s.meta.get('quality', 'UNK')
            info = s.meta.get('info', '')
            
            is_torrent = s.meta.get('local', False) or 'magnet:' in s.url.lower() or 'torrentio' in s.provider.lower()
            if is_torrent:
                provider_styled = f"[green]⚡ {s.provider}[/green]"
            else:
                provider_styled = f"[cyan]🌐 {s.provider}[/cyan]"
                
            table.add_row(provider_styled, qual, lang, info, key=str(i))
            
        self.update_header()
        
        if self.filtered_sources:
            table.cursor_coordinate = (0, 0)
            self.update_details(self.filtered_sources[0])
        else:
            self.update_details(None)

    def update_details(self, source) -> None:
        if not source:
            self.query_one("#detail-filename", Label).update("[italic]Brak wyników spełniających kryteria filtru.[/italic]")
            self.query_one("#detail-quality", Label).update("")
            self.query_one("#detail-language", Label).update("")
            self.query_one("#detail-provider", Label).update("")
            self.query_one("#detail-hosting", Label).update("")
            self.query_one("#detail-info", Label).update("")
            return

        filename = source.meta.get('filename', '')
        if not filename:
            filename = source.meta.get('info', 'Brak nazwy pliku')
            
        quality = source.meta.get('quality', 'UNK')
        language = source.meta.get('language', 'EN').upper()
        provider = source.provider
        hosting = source.hosting.upper()
        info = source.meta.get('info', '')

        is_torrent = source.meta.get('local', False) or 'magnet:' in source.url.lower() or 'torrentio' in source.provider.lower()
        if is_torrent:
            type_str = "[green]Torrent (P2P)[/green]"
        else:
            type_str = "[cyan]Direct / Web Stream[/cyan]"

        self.query_one("#detail-filename", Label).update(filename)
        self.query_one("#detail-quality", Label).update(quality)
        self.query_one("#detail-language", Label).update(language)
        self.query_one("#detail-provider", Label).update(f"{provider} ({type_str})")
        self.query_one("#detail-hosting", Label).update(hosting)
        self.query_one("#detail-info", Label).update(info)

    def get_selected_source(self):
        table = self.query_one("#sources-table", DataTable)
        row_idx = table.cursor_row
        if row_idx is not None and row_idx < len(self.filtered_sources):
            return self.filtered_sources[row_idx]
        return None

    def action_stream_source(self) -> None:
        source = self.get_selected_source()
        if source:
            self.app.push_screen(StreamScreen(source))

    def action_download_source(self) -> None:
        source = self.get_selected_source()
        if source:
            candidates = [source] + [s for s in self.filtered_sources if s != source]
            self.app.switch_screen(DownloadScreen(candidates, self.queue))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_stream_source()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            idx = int(event.row_key.value)
            if idx < len(self.filtered_sources):
                self.update_details(self.filtered_sources[idx])
        except Exception:
            pass

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id
        if tab_id == "tab-all":
            self.active_type_filter = "all"
        elif tab_id == "tab-torrents":
            self.active_type_filter = "torrents"
        elif tab_id == "tab-web":
            self.active_type_filter = "web"
        self.apply_filters()

    def action_filter_torrents(self) -> None:
        self.query_one(Tabs).active = "tab-torrents"

    def action_filter_web(self) -> None:
        self.query_one(Tabs).active = "tab-web"

    def action_filter_all(self) -> None:
        self.query_one(Tabs).active = "tab-all"

    def action_cycle_quality(self) -> None:
        qualities = ["all", "1080p+", "720p+"]
        idx = qualities.index(self.active_quality_filter)
        self.active_quality_filter = qualities[(idx + 1) % len(qualities)]
        self.apply_filters()

    def action_cycle_lang(self) -> None:
        langs = ["all", "pl", "en"]
        idx = langs.index(self.active_lang_filter)
        self.active_lang_filter = langs[(idx + 1) % len(langs)]
        self.apply_filters()
