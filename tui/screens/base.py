from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Header, Footer, Static

from tui.components.meta_panel import MetaPanel

class BaseScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left-pane"):
                yield from self.compose_left()
            with Vertical(id="right-pane"):
                yield MetaPanel(id="meta-panel")
        yield Footer()
        
    def compose_left(self):
        yield Static("")
