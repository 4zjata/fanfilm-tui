from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from tui.helpers import get_truncated_plot, PosterRenderable, sanitize_title
from lib.ff.settings import settings

class MetaPanel(Vertical):
    def update_meta(self, item):
        # Clear the old poster immediately on the main thread
        container = self.query_one("#poster-container")
        for child in container.children:
            child.remove()

        if item is None:
            self.query_one("#details", Static).update("Wybierz pozycję z listy...")
            return

        if isinstance(item, dict):
            name = item.get('name', 'Gatunek')
            details = f"[bold cyan]Gatunek:[/bold cyan] {name}\n\n[dim]Wybierz ten gatunek, aby wyświetlić powiązane pozycje.[/dim]"
            self.query_one("#details", Static).update(details)
            return

        vtag = item.getVideoInfoTag()
        title = sanitize_title(item.title)
        year = item.year or vtag.getYear() or "????"
        rating = f"{vtag.getRating():.1f}/10" if vtag.getRating() else "N/A"
        genre = "Nieznany"
        if hasattr(self.screen, "get_genre_string"):
            genre = self.screen.get_genre_string(item)
        plot = get_truncated_plot(vtag.getPlot() or "Brak opisu.")
        
        details = f"[bold cyan]Tytuł:[/bold cyan] {title}\n"
        details += f"[bold cyan]Rok:[/bold cyan] {year}\n"
        details += f"[bold cyan]Ocena:[/bold cyan] {rating}\n"
        details += f"[bold cyan]Gatunek:[/bold cyan] {genre}\n\n[dim]{plot}[/dim]"
        
        self.query_one("#details", Static).update(details)
        
        url = item.getArt("poster")
        if url:
            self.load_poster(url)


    @work(thread=True, exclusive=True)
    def load_poster(self, url: str) -> None:
        try:
            poster_type = settings.getString("tui.poster.type") or "auto"
            
            import urllib.request
            import io
            from PIL import Image as PILImage
            
            # Download the image bytes
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                img_data = response.read()
                
            # Parse using PIL
            img = PILImage.open(io.BytesIO(img_data)).convert("RGB")
            
            # Build the widget according to settings preference
            if poster_type == "sixel":
                from textual_image.widget import SixelImage
                widget = SixelImage(img, id="poster")
            elif poster_type == "kitty":
                from textual_image.widget import TGPImage
                widget = TGPImage(img, id="poster")
            elif poster_type == "halfcell":
                from textual_image.widget import HalfcellImage
                widget = HalfcellImage(img, id="poster")
            elif poster_type == "ascii":
                widget = Static(PosterRenderable(img), id="poster")
            else: # "auto"
                try:
                    from textual_image.widget import Image as TImage
                    widget = TImage(img, id="poster")
                except (ImportError, ModuleNotFoundError):
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
