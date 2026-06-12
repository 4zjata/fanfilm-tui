import sys
import textwrap
import urllib.request
from PIL import Image

from lib.ff.settings import settings

def get_truncated_plot(plot: str, width: int = 40, max_lines: int = 15) -> str:
    lines = textwrap.wrap(plot, width=width)
    if not lines: return "Brak opisu."
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines-1]) + "\n" + lines[max_lines-1][:width-4] + "..."
    return "\n".join(lines)

class PosterRenderable:
    def __init__(self, url_or_img, width=28):
        self.width = width
        self.ansi_string = ""
        if isinstance(url_or_img, str):
            self.url = url_or_img
            self._load()
        else:
            self.url = None
            self._load_from_image(url_or_img)
        
    def _load(self):
        try:
            req = urllib.request.Request(self.url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                img = Image.open(response).convert("RGB")
            self._load_from_image(img)
        except Exception:
            self.ansi_string = "[red]Brak obrazka[/red]"

    def _load_from_image(self, img):
        try:
            w = self.width
            h = int((img.height / img.width) * w * 0.5) * 2
            img = img.resize((w, h), Image.Resampling.LANCZOS)
            
            lines = []
            for y in range(0, h, 2):
                line = []
                for x in range(w):
                    r1, g1, b1 = img.getpixel((x, y))
                    r2, g2, b2 = img.getpixel((x, y+1))
                    line.append(f"\033[48;2;{r1};{g1};{b1}m\033[38;2;{r2};{g2};{b2}m▄")
                lines.append("".join(line) + "\033[0m")
            self.ansi_string = "\n".join(lines)
        except Exception:
            self.ansi_string = "[red]Brak obrazka[/red]"
            
    def __rich_console__(self, console, options):
        from rich.ansi import AnsiDecoder
        if self.ansi_string:
            decoder = AnsiDecoder()
            yield from decoder.decode(self.ansi_string)
        else:
            yield ""

def rate_source(source) -> int:
    lang = source.get('language', '').lower()
    info = source.get('info', '').lower()
    score = 0
    
    # Read providers language setting
    lang_pref = settings.getString("providers.lang")
    if not lang_pref:
        lang_pref = "Polish+English"
        
    if lang_pref == "Polish":
        # Only Polish is selected. Ensure we prioritize Polish audio tracks (Lektor/Dubbing)
        # and penalize English-only tracks heavily.
        is_pl_audio = ('lektor' in info or 'dubbing' in info or ('pl' in lang and 'napisy' not in info))
        is_pl_sub = ('napisy' in info)
        
        if is_pl_audio:
            score += 10000
        elif is_pl_sub:
            score += 100
        else:
            score -= 10000 # English only / non-Polish
            
    # Base language scoring
    if 'pl' in lang: score += 50
    if 'lektor' in info or 'dubbing' in info: score += 50
    if 'napisy' in info: score += 30
    
    # Quality scoring
    qual = source.get('quality', '').upper()
    if '4K' in qual: score += 40
    elif '1080' in qual: score += 30
    elif '720' in qual: score += 20
    return score

def play_in_mpv(resolved_url, title=""):
    try:
        from urllib.parse import parse_qsl
        if not isinstance(resolved_url, str):
            resolved_url = resolved_url[0]
            
        try:
            headers = dict(parse_qsl(resolved_url.rsplit("|", 1)[1]))
        except Exception:
            headers = {}
            
        url = resolved_url.split("|")[0]
        
        cmd = ["mpv"]
        for k, v in headers.items():
            if k.lower() == 'user-agent':
                cmd.append(f"--user-agent={v}")
            elif k.lower() == 'referer':
                cmd.append(f"--referrer={v}")
            else:
                cmd.append(f"--http-header-fields={k}: {v}")
        if title:
            cmd.append(f"--force-media-title={title}")
            
        lang_pref = settings.getString("providers.lang")
        if lang_pref == "Polish":
            cmd.append("--alang=pol,pl,polski,polish")
            cmd.append("--slang=pol,pl,polski,polish")
            
        cmd.append(url)
        return cmd
    except Exception as e:
        print(f"Error preparing mpv command: {e}", file=sys.stderr)
        return None
