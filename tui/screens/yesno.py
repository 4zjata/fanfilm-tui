from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Button
from textual.screen import ModalScreen

class YesNoScreen(ModalScreen[bool]):
    """Modal dialog that asks a Yes/No question and returns a boolean."""
    
    DEFAULT_CSS = """
    YesNoScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.4);
    }
    #dialog {
        padding: 1 4;
        border: double $accent;
        background: $panel;
        width: 70;
        height: auto;
    }
    #question {
        text-align: center;
        width: 100%;
        margin-bottom: 1;
        content-align: center middle;
    }
    #buttons {
        align: center middle;
        height: 3;
        width: 100%;
    }
    Button {
        margin: 0 2;
    }
    """

    def __init__(self, question: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question")
            with Horizontal(id="buttons"):
                yield Button("Tak", variant="success", id="yes")
                yield Button("Nie", variant="error", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
