from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, DataTable

from tui.screens.stream import StreamScreen
from tui.screens.download import DownloadScreen

class SourceSelectScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Powrót"),
        ("enter", "stream_source", "Streamuj (MPV)"),
        ("d", "download_source", "Pobierz"),
    ]

    def __init__(self, sources_list, queue):
        super().__init__()
        self.sources_list = sources_list
        self.queue = queue

    def compose(self) -> ComposeResult:
        yield DataTable(id="sources-table", cursor_type="row")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Serwis", "Jakość", "Język", "Informacje")
        for i, s in enumerate(self.sources_list):
            lang = s.get('language', '').upper() or "EN"
            qual = s.meta.get('quality', 'UNK')
            info = s.meta.get('info', '')
            table.add_row(s.provider, qual, lang, info, key=str(i))
        table.focus()

    def get_selected_source(self):
        table = self.query_one(DataTable)
        row_idx = table.cursor_row
        if row_idx is not None and row_idx < len(self.sources_list):
            return self.sources_list[row_idx]
        return None

    def action_stream_source(self) -> None:
        source = self.get_selected_source()
        if source:
            self.app.push_screen(StreamScreen(source))

    def action_download_source(self) -> None:
        source = self.get_selected_source()
        if source:
            candidates = [source] + [s for s in self.sources_list if s != source]
            self.app.switch_screen(DownloadScreen(candidates, self.queue))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_stream_source()
