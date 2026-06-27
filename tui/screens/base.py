from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static

from tui.components.meta_panel import MetaPanel

class BaseScreen(Screen):
    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left-pane"):
                yield from self.compose_left()
            with Vertical(id="right-pane"):
                yield MetaPanel(id="meta-panel")
        yield Footer(show_command_palette=False)
        
    def on_mount(self) -> None:
        from lib.ff.settings import settings
        right_p = settings.getString("tui.right_pane_width")
        if right_p:
            try:
                p_val = int(right_p)
                self.query_one("#right-pane").styles.width = f"{p_val}%"
                self.query_one("#left-pane").styles.width = f"{100 - p_val}%"
            except Exception:
                pass
        self.update_footer_command_palette()

    def on_screen_resume(self) -> None:
        self.update_footer_command_palette()

    def update_footer_command_palette(self) -> None:
        from lib.ff.settings import settings
        try:
            footer = self.query_one(Footer)
            menu_type = settings.getString("tui.menu_type") or "sidebar"
            footer.show_command_palette = (menu_type == "command_palette")
        except Exception:
            pass

    def compose_left(self):
        yield Static("")
