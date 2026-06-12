from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from tui.helpers import get_truncated_plot, PosterRenderable

class MetaPanel(Vertical):
    def update_meta(self, item):
        vtag = item.getVideoInfoTag()
        title = item.title
        year = item.year or vtag.getYear() or "????"
        rating = f"{vtag.getRating():.1f}/10" if vtag.getRating() else "N/A"
        genre = vtag.getGenre() or "Nieznany"
        plot = get_truncated_plot(vtag.getPlot() or "Brak opisu.")
        
        details = f"[bold cyan]Tytuł:[/bold cyan] {title}\n"
        details += f"[bold cyan]Rok:[/bold cyan] {year}\n"
        details += f"[bold cyan]Ocena:[/bold cyan] {rating}\n"
        details += f"[bold cyan]Gatunek:[/bold cyan] {genre}\n\n[dim]{plot}[/dim]"
        
        self.query_one("#details", Static).update(details)
        
        # Clear the old poster immediately on the main thread
        container = self.query_one("#poster-container")
        for child in container.children:
            child.remove()
            
        url = item.getArt("poster")
        if url:
            self.load_poster(url)

    @work(thread=True, exclusive=True)
    def load_poster(self, url: str) -> None:
        try:
            import urllib.request
            import io
            from PIL import Image as PILImage
            
            # Download the image bytes
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                img_data = response.read()
                
            # Parse using PIL
            img = PILImage.open(io.BytesIO(img_data)).convert("RGB")
            
            # Try to load high-res textual-image widget
            try:
                from textual_image.widget import Image as TImage
                widget = TImage(img, id="poster")
            except (ImportError, ModuleNotFoundError):
                # Fallback to ASCII art via PosterRenderable (using already downloaded img)
                widget = Static(PosterRenderable(img), id="poster")
                
            # Mount the widget in the main thread
            self.app.call_from_thread(self._mount_widget, widget)
            
        except Exception as e:
            # If download or conversion failed completely, show fallback or error
            try:
                # Try to fall back directly (might fail if URL itself is broken)
                widget = Static(PosterRenderable(url), id="poster")
                self.app.call_from_thread(self._mount_widget, widget)
            except Exception as e2:
                widget = Static(f"[red]Błąd: {e}[/red]", id="poster")
                self.app.call_from_thread(self._mount_widget, widget)

    def _mount_widget(self, widget) -> None:
        container = self.query_one("#poster-container")
        # Ensure it is clean (in case of race condition or multiple triggers)
        for child in container.children:
            child.remove()
        container.mount(widget)

    def compose(self) -> ComposeResult:
        yield Static("Wybierz pozycję z listy...", id="details")
        with Vertical(id="poster-container"):
            pass
