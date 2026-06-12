from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Button
from textual.screen import ModalScreen

class AlertScreen(ModalScreen):
    """Modal dialog to display warning/alert messages."""
    
    DEFAULT_CSS = """
    AlertScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.4);
    }
    #alert-dialog {
        padding: 1 4;
        border: double red;
        background: $panel;
        width: 70;
        height: auto;
        min-height: 12;
        content-align: center middle;
    }
    #alert-message {
        text-align: center;
        width: 100%;
        margin-bottom: 2;
        content-align: center middle;
    }
    #alert-buttons {
        align: center middle;
        height: 3;
        width: 100%;
    }
    Button {
        margin: 0 2;
    }
    """

    def __init__(self, message: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="alert-dialog"):
            yield Label(self.message, id="alert-message")
            with Horizontal(id="alert-buttons"):
                yield Button("OK", variant="primary", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss()
