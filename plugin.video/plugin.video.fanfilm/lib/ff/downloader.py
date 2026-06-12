import json
import hashlib
import os
import re
import time
import pyxbmct
import requests
import xbmc
import xbmcgui
import xbmcvfs
import threading
from functools import lru_cache
from contextlib import contextmanager

from sqlite3 import dbapi2 as database
from urllib.parse import parse_qsl, urlparse, unquote
from ..ff import control, source_utils
from ..ff.control import busy_dialog, close_busy_dialog
from lib.ff import cleantitle
from .log_utils import fflog, fflog_exc
from ..ff.settings import settings
from ..kolang import L

try:
    import resolveurl
except ImportError:
    resolveurl = None

# Semaphore for controlling concurrent downloads
_download_semaphore = None
_download_semaphore_lock = threading.Lock()

def get_download_semaphore():
    global _download_semaphore
    with _download_semaphore_lock:
        if _download_semaphore is None:
            try:
                val = settings.getInt("download.max_concurrent")
                if val < 1:
                    val = 3
            except Exception:
                val = 3
            _download_semaphore = threading.BoundedSemaphore(val)
        return _download_semaphore

def reset_download_semaphore():
    global _download_semaphore
    with _download_semaphore_lock:
        try:
            val = settings.getInt("download.max_concurrent")
            if val < 1:
                val = 3
        except Exception:
            val = 3
        _download_semaphore = threading.BoundedSemaphore(val)

# Stałe do konfiguracji
CHUNK_SIZE = 1024 * 50
SPEED_UPDATE_INTERVAL = 1.0
MAX_ERRORS = 5
SLEEP_TIME = 5
CHECK_INTERVAL = 0.5

@lru_cache(maxsize=128)
def makeLegalFilename(name):
    safe = name.translate(str.maketrans("", "", '\\/:*?"<>|'))
    return safe

@contextmanager
def database_connection(db_path):
    conn = None
    try:
        conn = database.connect(db_path)
        yield conn
    finally:
        if conn:
            conn.close()

def download(name, year, image, downinfo, url):
    if not url:
        return

    if not isinstance(url, str):
        url = url[0]

    try:
        headers = dict(parse_qsl(url.rsplit("|", 1)[1]))
    except Exception:
        headers = {}

    url = url.split("|")[0]
    name = unquote(name)
    name = cleantitle.normalize(name)
    print(f"DEBUG: download() name normalized: {name}")
    downinfo = downinfo.replace("|", ".").replace("/", "-").replace(" ", "") if downinfo else ""

    # Updated regex to handle season and episode for TV shows
    content = re.compile(r"(.+?)[. ]S(\d+)E(\d+)", re.IGNORECASE).findall(name)
    print(f"DEBUG: download() regex content: {content}")

    if not content:
        # Movie logic
        transname = makeLegalFilename(name)
        transname = f'{transname} ({year})'
        dest = control.transPath(settings.getString("movie.download.path"))
        print(f"DEBUG: download() movie dest: {dest}")
        _ensure_directory_exists(dest)
        dest = os.path.join(dest, transname)
        # control.make_dir(dest)
        season = None
    else:
        # TV Show logic
        show_title, season_num, episode_num = content[0]
        transtvshowtitle = makeLegalFilename(show_title.strip())
        # Construct filename: Title (Year).SxxExx
        transname = f"{transtvshowtitle}.S{int(season_num):02d}E{int(episode_num):02d}"
        dest = control.transPath(settings.getString("tv.download.path"))
        print(f"DEBUG: download() tv dest: {dest}")
        _ensure_directory_exists(dest)
        season = season_num
        # Add year to the show's main directory
        transtvshowtitle_with_year = f"{transtvshowtitle} ({year})"
        dest = os.path.join(dest, transtvshowtitle_with_year)

    ext = _get_file_extension(url)
    transname += (f".{d}" if (d := makeLegalFilename(downinfo).lstrip('.')) else "") if downinfo else ""
    transname = f"{transname}.{ext}"
    display_manager = settings.getBool("download.show_manager")
    doDownload(url, dest, name, image, json.dumps(headers), transname, season, ext, display_manager=display_manager)

def _ensure_directory_exists(dest):
    levels = ["../../../..", "../../..", "../..", ".."]
    for level in levels:
        try:
            path = os.path.abspath(os.path.join(dest, level))
            control.make_dir(path)
        except Exception:
            fflog_exc()
    control.make_dir(dest)

def _get_file_extension(url):
    ext = os.path.splitext(urlparse(url).path)[1][1:]
    valid = {"mp4","mkv","flv","avi","mpg","mov","webm","ts","wmv","vob","m2ts","mts"}
    chosen = ext if ext in valid else "mp4"
    return chosen

def getResponse(url, headers, size, requests_test_only=False):
    try:
        if size > 0:
            headers["Range"] = f"bytes={size}-"
        timeout = (10, 30)
        if requests_test_only:
            resp = requests.head(url, headers=headers, verify=False, allow_redirects=True, timeout=timeout)
        else:
            resp = requests.get(url, headers=headers, verify=False, stream=True, timeout=timeout)
        return resp
    except Exception:
        fflog_exc()
        return None
def parse_time_to_seconds(t_str):
    try:
        parts = t_str.strip().split(':')
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
    except:
        pass
    return 0.0

def probe_media_streams(url_or_path, headers=None):
    import subprocess
    import json
    import sys
    
    cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type:stream_tags=language,title", "-of", "json"]
    
    is_remote = url_or_path.startswith("http://") or url_or_path.startswith("https://")
    if is_remote and headers:
        hdrs_dict = {}
        if isinstance(headers, str):
            try:
                hdrs_dict = json.loads(headers)
            except:
                pass
        elif isinstance(headers, dict):
            hdrs_dict = headers
        
        headers_str = ""
        for k, v in hdrs_dict.items():
            headers_str += f"{k}: {v}\r\n"
        if headers_str:
            cmd.extend(["-headers", headers_str])
            
    cmd.append(url_or_path)
    print(f"DEBUG: Running ffprobe command: {cmd}")
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            return data.get("streams", [])
        else:
            print(f"DEBUG: ffprobe failed with code {res.returncode}, stderr: {res.stderr}")
    except Exception as e:
        print(f"DEBUG: ffprobe exception: {e}")
    return []

def get_mapped_args(streams, providers_lang):
    if not streams:
        return ["-map", "0:v?", "-map", "0:a?", "-map", "0:s?"]
        
    video_indices = [s["index"] for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_indices = [s["index"] for s in streams if s.get("codec_type") in ("subtitle", "subtitles")]
    other_indices = [s["index"] for s in streams if s.get("codec_type") not in ("video", "audio", "subtitle", "subtitles")]
    
    mapped_args = []
    for idx in video_indices:
        mapped_args.extend(["-map", f"0:{idx}"])
        
    if providers_lang == "Polish" and len(audio_streams) > 1:
        pl_audio = []
        other_audio = []
        for s in audio_streams:
            lang = s.get("tags", {}).get("language", "").lower()
            title = s.get("tags", {}).get("title", "").lower()
            is_pl = (
                lang in ("pol", "pl", "polski", "pl_pl") or
                any(w in title for w in ("pl", "lektor", "dubbing", "polski", "polish"))
            )
            if is_pl:
                pl_audio.append(s["index"])
            else:
                other_audio.append(s["index"])
                
        for idx in pl_audio + other_audio:
            mapped_args.extend(["-map", f"0:{idx}"])
    else:
        for s in audio_streams:
            mapped_args.extend(["-map", f"0:{s['index']}"])
            
    for idx in subtitle_indices:
        mapped_args.extend(["-map", f"0:{idx}"])
    for idx in other_indices:
        mapped_args.extend(["-map", f"0:{idx}"])
        
    return mapped_args

def post_process_audio_mapping(dest):
    lang_pref = settings.getString("providers.lang")
    if lang_pref != "Polish":
        return
        
    if not os.path.exists(dest):
        return
        
    streams = probe_media_streams(dest)
    if not streams:
        return
        
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audio_streams) <= 1:
        return
        
    first_audio = audio_streams[0]
    lang = first_audio.get("tags", {}).get("language", "").lower()
    title = first_audio.get("tags", {}).get("title", "").lower()
    first_is_pl = (
        lang in ("pol", "pl", "polski", "pl_pl") or
        any(w in title for w in ("pl", "lektor", "dubbing", "polski", "polish"))
    )
    if first_is_pl:
        return
        
    has_pl = False
    for s in audio_streams:
        lang = s.get("tags", {}).get("language", "").lower()
        title = s.get("tags", {}).get("title", "").lower()
        if lang in ("pol", "pl", "polski", "pl_pl") or any(w in title for w in ("pl", "lektor", "dubbing", "polski", "polish")):
            has_pl = True
            break
            
    if not has_pl:
        return
        
    import subprocess
    mapping_args = get_mapped_args(streams, lang_pref)
    
    temp_dest = dest + ".tmp.mp4"
    cmd = ["ffmpeg", "-y", "-i", dest] + mapping_args + ["-c", "copy", temp_dest]
    try:
        print(f"DEBUG: Post-processing remux cmd: {cmd}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if res.returncode == 0 and os.path.exists(temp_dest) and os.path.getsize(temp_dest) > 0:
            os.replace(temp_dest, dest)
            print("DEBUG: Post-processing remux completed successfully")
        else:
            print(f"DEBUG: Post-processing remux failed, stderr: {res.stderr}")
            if os.path.exists(temp_dest):
                os.remove(temp_dest)
    except Exception as e:
        print(f"DEBUG: Post-processing remux exception: {e}")
        if os.path.exists(temp_dest):
            os.remove(temp_dest)


def _perform_hls_download(url, dest, title, headers, id_downloading):
    print(f"DEBUG: _perform_hls_download started for {title}")
    prepareDatabase()
    with database_connection(control.downloadsFile) as dbcon:
        dbcur = dbcon.cursor()
        existing = dbcur.execute("SELECT filesize FROM download_manager WHERE filename=?", (title,)).fetchone()
        if not existing:
            insertIntoDb(title, 100, "0%", 0, True, "queued", "", url, headers, dest, id_downloading)
        else:
            update(title, state="queued", speed="")

    xbmcgui.Window(10000).setProperty(id_downloading, title)
    
    semaphore = get_download_semaphore()
    acquired = False
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if xbmcgui.Window(10000).getProperty(id_downloading) == 'break':
            update(title, state="stopped")
            return
        if semaphore.acquire(blocking=True, timeout=0.5):
            acquired = True
            break
    else:
        update(title, state="stopped")
        return

    try:
        import subprocess
        
        hdrs = json.loads(headers) if headers else {}
        url_only = url.split("|")[0]
        
        try:
            if "|" in url:
                from urllib.parse import parse_qsl
                url_hdrs = dict(parse_qsl(url.rsplit("|", 1)[1]))
                hdrs.update(url_hdrs)
        except:
            pass
            
        lang_pref = settings.getString("providers.lang")
        streams = probe_media_streams(url_only, hdrs)
        mapping_args = get_mapped_args(streams, lang_pref)

        cmd = ["ffmpeg", "-y"]
        headers_str = ""
        for k, v in hdrs.items():
            headers_str += f"{k}: {v}\r\n"
        if headers_str:
            cmd.extend(["-headers", headers_str])
            
        cmd.extend(["-i", url_only])
        cmd.extend(mapping_args)
        cmd.extend(["-c", "copy", dest])
        print(f"DEBUG: running ffmpeg command: {cmd}")
        
        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True, bufsize=1)
        
        total_seconds = 0.0
        current_seconds = 0.0
        speed = "0"
        
        duration_regex = re.compile(r"Duration:\s*(\d{2}:\d{2}:\d{2}\.\d{2})")
        time_regex = re.compile(r"time=\s*(\d{2}:\d{2}:\d{2}\.\d{2})")
        speed_regex = re.compile(r"speed=\s*([\d\.]+)x")
        
        update(title, state="running")

        monitor = xbmc.Monitor()
        while True:
            if (xbmcgui.Window(10000).getProperty(id_downloading) == 'break') or monitor.abortRequested():
                process.terminate()
                update(title, state="stopped")
                break
                
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if not line:
                continue
                
            if total_seconds == 0.0:
                m = duration_regex.search(line)
                if m:
                    total_seconds = parse_time_to_seconds(m.group(1))
                    
            m_time = time_regex.search(line)
            if m_time:
                current_seconds = parse_time_to_seconds(m_time.group(1))
                m_speed = speed_regex.search(line)
                if m_speed:
                    speed = m_speed.group(1)
                
                percent = 0
                if total_seconds > 0.0:
                    percent = min(100, int(100 * current_seconds / total_seconds))
                
                update(title, f"{percent}%", int(current_seconds), "running", f"{speed}x")
                
        if process.poll() == 0:
            update(title, "100%", int(total_seconds or 100), "finished", "")
            control.infoDialog(L(30425, "Download finished"), title, icon="")
        elif xbmcgui.Window(10000).getProperty(id_downloading) != 'break':
            update(title, state="broken")
            control.infoDialog(L(30418, "Download error"), title, icon="ERROR")
    except Exception:
        fflog_exc()
        update(title, state="broken")
        control.infoDialog(L(30418, "Download error"), title, icon="ERROR")
    finally:
        xbmcgui.Window(10000).clearProperty(id_downloading)
        semaphore.release()

def doDownload(url, dest, title, image="", headers="", transname="", season=None, ext="", total=0, display_manager=False):
    print(f"DEBUG: doDownload started for {title}")
    try:
        if resolveurl:
            resolved_url = resolveurl.resolve(url)
            if resolved_url and isinstance(resolved_url, str):
                url = resolved_url
    except Exception as e:
        fflog_exc()

    id_downloading = _generate_download_id(title)
    
    # Check if this is an HLS (m3u8) download
    is_hls = ".m3u8" in url.split("|")[0]
    if is_hls:
        try:
            is_resume = total > 0
            if not is_resume and _check_existing_download(id_downloading):
                if not _handle_existing_download(id_downloading):
                    return
            
            dest = MyAddon._prepare_destination_path(dest, transname, season)
            if not dest.endswith(".mp4"):
                dest = os.path.splitext(dest)[0] + ".mp4"
                
            def hls_worker():
                try:
                    _perform_hls_download(url, dest, title, headers, id_downloading)
                except Exception:
                    fflog_exc()
                    update(title, state="broken")
            threading.Thread(target=hls_worker, daemon=True).start()
            
            notification_title = L(30417, "Download started")
            control.infoDialog(notification_title, title, icon="INFO")
            return
        except Exception:
            fflog_exc()
            update(title, state="broken")
            control.infoDialog(L(30418, "Download error"), title, icon="ERROR")
            return

    try:
        is_resume = total > 0

        if not is_resume and _check_existing_download(id_downloading):
            if not _handle_existing_download(id_downloading):
                return

        hdrs = json.loads(headers) if headers else {}
        content = 0

        if not is_resume:
            # Dla RESTARTU i nowych pobierań, weryfikujemy link od razu
            resp = getResponse(url, hdrs, 0, requests_test_only=True)
            if not MyAddon._validate_response(resp, title):
                # Jeśli link jest zły (np. wygasł), od razu oznacz jako błąd i zakończ
                fflog(f"Restart/nowe pobieranie nie powiodło się dla '{title}': Nieprawidłowa odpowiedź serwera.")
                update(title, state="broken")
                control.infoDialog(L(30414, "Restart error"), L(30415, "The file link may have expired."), icon="ERROR")
                return

            content = MyAddon._get_content_length(resp)
            if content < 1 or not MyAddon._validate_content_type(resp, transname):
                return

            if not MyAddon._confirm_download(transname or title, content, total):
                return

            dest = MyAddon._prepare_destination_path(dest, transname, season)

            file_handling_result = MyAddon._handle_existing_file(dest, content, resp, url, hdrs, total)
            if not file_handling_result:
                return
            elif file_handling_result == "continue":
                if xbmcvfs.exists(dest):
                    st = xbmcvfs.Stat(dest)
                    total = st.st_size()
        else:
            # Dla WZNOWIENIA pobierz rozmiar z bazy
            with database_connection(control.downloadsFile) as dbcon:
                dbcur = dbcon.cursor()
                record = dbcur.execute("SELECT filesize FROM download_manager WHERE filename=?", (title,)).fetchone()
                content = int(record[0]) if record and record[0] else 0

        _start_download_process(url, dest, title, hdrs, content, total, id_downloading, display_manager)

        # Powiadomienie tylko jeśli wszystko powyżej się udało
        notification_title = L(30416, "Download resumed") if is_resume else L(30417, "Download started")
        control.infoDialog(notification_title, title, icon="INFO")

    except Exception:
        fflog_exc()
        update(title, state="broken")
        control.infoDialog(L(30418, "Download error"), title, icon="ERROR")


def _generate_download_id(identifier):
    fid = "FanFilm-downloading-" + hashlib.md5(identifier.encode()).hexdigest()
    return fid

def _check_existing_download(id_downloading):
    prop = xbmcgui.Window(10000).getProperty(id_downloading)
    return bool(prop)

def _handle_existing_download(id_downloading):
    if not xbmcgui.Dialog().yesno(L(30419, "WARNING - A download of this file is already in progress"),
                                  L(30420, "Do you want to interrupt this download?"),
                                  yeslabel=L(30421, "Interrupt"), nolabel=L(30422, "Continue")):
        return False
    xbmcgui.Window(10000).setProperty(id_downloading, 'break')
    for i in range(30):
        if xbmcgui.Window(10000).getProperty(id_downloading) != 'break':
            break
        xbmc.sleep(100)
    else:
        xbmcgui.Window(10000).clearProperty(id_downloading)
    return True

def _start_download_process(url, dest, title, headers, content, total, id_downloading, display_manager):
    print(f"DEBUG: _start_download_process entering for {title}")
    def download_worker():
        print(f"DEBUG: download_worker started for {title}")
        _perform_download(url, dest, title, headers, content, total, id_downloading)
    threading.Thread(target=download_worker, daemon=True).start()
    if display_manager:
        threading.Thread(target=downloadManager, kwargs={"files": count_records()}, daemon=True).start()


def _perform_download(url, dest, title, headers, content, total, id_downloading):
    print(f"DEBUG: _perform_download started for {title}")
    prepareDatabase()
    with database_connection(control.downloadsFile) as dbcon:
        dbcur = dbcon.cursor()
        existing = dbcur.execute("SELECT filesize FROM download_manager WHERE filename=?", (title,)).fetchone()
        if not existing:
            insertIntoDb(title, content, "0%", total, True, "queued", "", url, json.dumps(headers), dest, id_downloading)
        else:
            update(title, state="queued", speed="")

    xbmcgui.Window(10000).setProperty(id_downloading, title)
    
    semaphore = get_download_semaphore()
    acquired = False
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if xbmcgui.Window(10000).getProperty(id_downloading) == 'break':
            update(title, state="stopped")
            return
        if semaphore.acquire(blocking=True, timeout=0.5):
            acquired = True
            break
    else:
        update(title, state="stopped")
        return

    try:
        # Pobierz właściwą odpowiedź GET do strumieniowania danych
        print(f"DEBUG: calling getResponse for {title}")
        resp = getResponse(url, headers, total)
        print(f"DEBUG: getResponse returned for {title}: {resp}")
        if not resp:
            # Jeśli getResponse zwróci None (błąd połączenia), oznacz jako uszkodzony i zakończ
            fflog(f"Pobieranie nie powiodło się dla '{title}': Brak odpowiedzi z serwera przy próbie GET.")
            update(title, state="broken")
            control.infoDialog(L(30418, "Download error"), L(30423, "Unable to connect."), icon="ERROR")
            return

        authoritative_full_size = 0
        with database_connection(control.downloadsFile) as dbcon:
            dbcur = dbcon.cursor()
            existing = dbcur.execute("SELECT filesize FROM download_manager WHERE filename=?", (title,)).fetchone()

            if existing:
                MyAddon.update(title, state="running", speed="")
                authoritative_full_size = int(existing[0]) if existing[0] else 0
            else:
                insertIntoDb(title, content, "0%", total, True, "running", "", url, json.dumps(headers), dest, id_downloading)
                authoritative_full_size = content

        download_status = "broken"
        with xbmcvfs.File(dest, "ab" if total else "wb") as f:
            download_status = _download_chunks(resp, f, authoritative_full_size, total, title, id_downloading)

        if download_status == "finished":
            try:
                post_process_audio_mapping(dest)
            except Exception as e:
                print(f"DEBUG: post_process_audio_mapping error: {e}")
            update(title, "100%", total, "finished", "")
            control.infoDialog(
                L(30425, "Download finished"),
                title,
                icon=""
            )
            MyAddon.done(title, "", True)

    except Exception:
        fflog_exc()
        update(title, state="broken")
    finally:
        xbmcgui.Window(10000).clearProperty(id_downloading)
        semaphore.release()

def _download_chunks(resp, file_handle, full_size, total, title, id_downloading):
    # Trzeci argument to teraz 'full_size' - autorytatywny pełny rozmiar pliku.
    # Usunęliśmy stare, błędne obliczanie 'full_size' wewnątrz tej funkcji.
    monitor = xbmc.Monitor()
    start_total = total
    percent = 0
    speed = 0
    last_time = time.time()
    last_total = total

    next_notify = 10
    was_cancelled = False

    try:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                break
            if total - start_total > 1024*1024 and _should_cancel_download(id_downloading, monitor):
                was_cancelled = True
                break

            file_handle.write(chunk)
            total += len(chunk)

            now = time.time()
            if now - last_time >= SPEED_UPDATE_INTERVAL:
                speed = (total - last_total) / (1024*1024) / (now - last_time)
                if full_size > 0:
                    percent = min(100 * total / full_size, 100)
                else:
                    percent = 0
                update(title, f"{int(percent)}%", total, "running", f"{speed:.2f}")
                last_time = now
                last_total = total

                if percent >= next_notify:
                    control.infoDialog(
                        f"{title}: {int(percent)}%",
                        L(30424, "Download progress"),
                        icon=""
                    )
                    next_notify += 10

        # Ustal końcowy stan na podstawie tego co się stało
        # Porównanie 'total' z 'full_size' jest teraz wiarygodne
        if was_cancelled:
            update(title, f"{int(percent)}%", total, "stopped", "")
            return "stopped"
        elif full_size > 0 and total >= full_size * 0.99:
            # Pobieranie ukończone
            return "finished"
        else:
            # Pobieranie przerwane z innego powodu (błąd sieci, zły rozmiar itp.)
            update(title, f"{int(percent)}%", total, "broken", "")
            control.infoDialog(
                L(30418, "Download error"),
                L(30426, "{title} (downloaded {downloaded_size} of {full_size})").format(
                    title=title,
                    downloaded_size=source_utils.convert_size(total),
                    full_size=source_utils.convert_size(full_size)
                ),
                icon="ERROR"
            )
            MyAddon.done(title, "", False)
            return "broken"

    except Exception:
        fflog_exc()
        update(title, state="broken", speed="")
        control.infoDialog(
            L(30418, "Download error"),
            title,
            icon="ERROR"
        )
        return "broken"

def _should_cancel_download(id_downloading, monitor):
    cancel = (xbmcgui.Window(10000).getProperty(id_downloading) == 'break') or monitor.abortRequested()
    return cancel

def _plain(text: str) -> str:
    """
    Usuwa tagi BBCode ([COLOR], [B]…), pierwszą ikonę (⏸▶✓✖🗑) i zwraca
    wynik w małych literach – przydatne do porównywania etykiet przycisków.
    """
    txt = re.sub(r'\[/?[^\]]+\]', '', text)
    txt = txt.lstrip('⏸▶✓✖🗑').strip()
    return txt.lower()

class MyAddon(pyxbmct.AddonDialogWindow):
    _action_labels = {
        "play": L(30427, "[COLOR limegreen]PLAY[/COLOR]"),
        "stop": L(30428, "[COLOR red]STOP[/COLOR]"),
        "resume": L(30429, "[COLOR orange]RESUME[/COLOR]"),
        "restart": L(30430, "[COLOR yellow]RESTART[/COLOR]"),
        "delete": L(30431, "[COLOR red]DELETE[/COLOR]"),
        "starting": L(30432, "[COLOR gray]Starting...[/COLOR]"),
        "updating": L(30433, "[COLOR gray][B]Updating...[/B][/COLOR]"),
        "refresh_list": L(30434, "[COLOR lightblue][B]Refresh list[/B][/COLOR]"),
        "no_action": "", # For empty button labels
    }
    def __init__(self, title="", files=5, start_list_update=True):
        super(MyAddon, self).__init__(title)
        self.max_rows_files = max(2, files)

        self.window_rows = self.max_rows_files + 5

        base_height = 150
        row_height = 60
        total_height = base_height + (self.max_rows_files * row_height)

        self.setGeometry(1200, total_height, self.window_rows, 11)

        self._create_controls()
        self.set_navigation()
        self.connect(pyxbmct.ACTION_NAV_BACK, self.close)

        self.abort = False
        self.cancelThread = False
        self.worker_active = False
        self._update_lock = threading.Lock()

        self.setProperty('windowtitle', '[COLOR cyan][B]FanFilm Download Manager[/B][/COLOR]')

        if start_list_update:
            self.list_update()
        self.setFocus(self.close_button)

    def _create_controls(self):
        headers = [
            (L(30435, "[COLOR white][B]Name[/B][/COLOR]"),    0, 0, 4),
            ("[COLOR white][B]%[/B][/COLOR]",        0, 4, 1),
            (L(30436, "[COLOR white][B]Downloaded[/B][/COLOR]"),  0, 5, 1),
            (L(30437, "[COLOR white][B]Size[/B][/COLOR]"),  0, 6, 1),
            ("[COLOR white][B]MB/s[/B][/COLOR]",     0, 7, 1),
            (L(30438, "[COLOR white][B]Time[/B][/COLOR]"),     0, 8, 1),
            (L(30439, "[COLOR white][B]Action[/B][/COLOR]"),    0, 9, 1),
            (L(30440, "[COLOR white][B]Delete[/B][/COLOR]"),     0, 10, 1),
        ]
        for text, row, col, span in headers:
            lbl = pyxbmct.Label(text, alignment=pyxbmct.ALIGN_CENTER)
            self.placeControl(lbl, row, col, 1, span)

        # separator = pyxbmct.Label(
        #     '[COLOR gray]' + '-' * 100 + '[/COLOR]',
        #     alignment=pyxbmct.ALIGN_CENTER
        # )
        # self.placeControl(separator, 1, 0, 1, 11)

        self.button_show_list = pyxbmct.Button(
            L(30434, "[COLOR lightblue][B]Refresh list[/B][/COLOR]")
        )
        self.placeControl(self.button_show_list, self.window_rows - 2, 1, 2, 4)
        self.connect(self.button_show_list, self.list_update)

        self.close_button = pyxbmct.Button(
            L(30441, "[COLOR red][B]Close[/B][/COLOR]")
        )
        self.placeControl(self.close_button, self.window_rows - 2, 5, 2, 4)
        self.connect(self.close_button, self.close)

        self.info_label = pyxbmct.Label(
            L(30442, "[COLOR yellow]Loading...[/COLOR]"),
            alignment=pyxbmct.ALIGN_CENTER
        )
        self.placeControl(self.info_label, self.window_rows - 3, 0, 1, 11)

        self._create_row_controls()

    def _create_row_controls(self):
            """
            Tworzy kontrolki widoczne wierszy oraz *tablice pomocnicze*,
            które zastępują ukryte Label-e z danymi technicznymi.
            """
            ROW_START = 2
            VC = pyxbmct.ALIGN_CENTER_Y

            self.items_fileName = [
                pyxbmct.FadeLabel(_alignment=pyxbmct.ALIGN_LEFT | VC)
                for _ in range(self.max_rows_files)
            ]
            self.items_fileSize   = [pyxbmct.Label("", alignment=pyxbmct.ALIGN_CENTER | VC)
                                    for _ in range(self.max_rows_files)]
            self.items_percent    = [pyxbmct.Label("", alignment=pyxbmct.ALIGN_CENTER | VC)
                                    for _ in range(self.max_rows_files)]
            self.items_downloaded = [pyxbmct.Label("", alignment=pyxbmct.ALIGN_CENTER | VC)
                                    for _ in range(self.max_rows_files)]
            self.items_speed      = [pyxbmct.Label("", alignment=pyxbmct.ALIGN_CENTER | VC)
                                    for _ in range(self.max_rows_files)]
            self.items_time       = [pyxbmct.Label("", alignment=pyxbmct.ALIGN_CENTER | VC)
                                    for _ in range(self.max_rows_files)]
            self.items_action     = [pyxbmct.Button("") for _ in range(self.max_rows_files)]
            self.items_delete     = [pyxbmct.Button("") for _ in range(self.max_rows_files)]


            self.row_filename         = [""     for _ in range(self.max_rows_files)]
            self.row_state            = [""     for _ in range(self.max_rows_files)]
            self.row_resumable        = [False  for _ in range(self.max_rows_files)]
            self.row_data             = [{}     for _ in range(self.max_rows_files)]
            self.row_bytesdownloaded  = [0      for _ in range(self.max_rows_files)]
            self.row_action           = [""     for _ in range(self.max_rows_files)]


            col_spans = [
                (self.items_fileName,    0, 4),
                (self.items_percent,     4, 1),
                (self.items_downloaded,  5, 1),
                (self.items_fileSize,    6, 1),
                (self.items_speed,       7, 1),
                (self.items_time,        8, 1),
                (self.items_action,      9, 1),
                (self.items_delete,     10, 1),
            ]

            for items, col, span in col_spans:
                for i, ctl in enumerate(items):
                    self.placeControl(ctl, ROW_START + i, col, 1, span)
                    if items is self.items_action:
                        self.connect(ctl, lambda r=i: self.do_action(r))
                    elif items is self.items_delete:
                        self.connect(ctl, lambda r=i: self._delete_row(r))


            for i in range(self.max_rows_files):
                self._clear_row(i)


    def _delete_row(self, r):
        """
        Usuwa rekord z bazy i czyści wiersz w GUI.
        """
        fname = self.row_filename[r]
        if not fname:
            return

        if not xbmcgui.Dialog().yesno(L(30443, "Delete entry"), fname,
                                      yeslabel=L(30444, "Delete"), nolabel=L(30445, "Cancel")):
            return


        MyAddon.remove(fname, 0)

        self._clear_row(r)

    def worker(self):
        if self.worker_active:
            return
        self.worker_active=True
        monitor=xbmc.Monitor()
        try:
            with database_connection(control.downloadsFile) as dbcon:
                dbcur=dbcon.cursor()
                table=dbcur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='download_manager'").fetchone()
                if not table:
                    control.infoDialog(L(30446, 'No downloads in the database'), icon="WARNING")
                    return
                while not self.cancelThread and not monitor.abortRequested():
                    with self._update_lock:
                        self._update_display(dbcur)
                    if monitor.waitForAbort(CHECK_INTERVAL):
                        break
        except Exception:
            fflog_exc()
        finally:
            self.worker_active=False

    def _update_display(self, dbcur):
        """
        Odświeża widoczną listę pobierań:
        • pobiera maksymalnie self.max_rows_files rekordów z bazy,
        • aktualizuje pasek statystyk (łącznie / aktywne / ukończone),
        • wypełnia istniejące wiersze lub je czyści w razie braku danych.
        """
        try:
            # --- rekordy do wyświetlenia ---
            dbcur.execute(
                "SELECT * FROM download_manager LIMIT ?",
                (self.max_rows_files,)
            )
            records = dbcur.fetchall()

            # --- pasek statystyk (odświeżany co ≥2 s) ---
            now = time.time()
            if (not hasattr(self, "_last_info_update") or
                    now - self._last_info_update > 2.0):

                total, running, finished = dbcur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM download_manager),
                        (SELECT COUNT(*) FROM download_manager WHERE state='running'),
                        (SELECT COUNT(*) FROM download_manager WHERE state='finished')
                """).fetchone()

                self.info_label.setLabel(
                    L(30490, "[COLOR white]Total: [COLOR cyan]{total}[/COLOR] | Active: [COLOR yellow]{running}[/COLOR] | Finished: [COLOR limegreen]{finished}[/COLOR][/COLOR]").format(
                        total=total,
                        running=running,
                        finished=finished
                    )
                )
                self._last_info_update = now

            # --- aktualizacja / wypełnianie wierszy ---
            for row, item in enumerate(records):
                if row >= self.max_rows_files:
                    break
                self._update_row_item(row, item)

            # --- czyszczenie pozostałych pustych wierszy ---
            for row in range(len(records), self.max_rows_files):
                # sprawdzamy etykietę widocznego FadeLabel-a
                if self.items_fileName[row]:
                    self._clear_row(row)

        except Exception:
            fflog_exc()


    def _update_row_item(self, row, item):
        """Aktualizuje pojedynczy wiersz w GUI i tablicach pomocniczych."""
        if not item:
            self._clear_row(row)
            return

        (filename, fsize, pct, dled, resumable,
        state, speed, url, headers, dest, id_dl, *_) = item


        if filename != self.row_filename[row]:
            self.items_fileName[row].reset()
            self.items_fileName[row].addLabel(filename)
            self.items_fileSize[row].setLabel(
                source_utils.convert_size(int(fsize))
            )

        dled_int = int(dled) if str(dled).isdigit() else 0
        self.items_downloaded[row].setLabel(
            source_utils.convert_size(dled_int) if dled_int else ""
        )

        pct_num = 0
        if fsize and int(fsize) > 0:
            pct_num = int(dled_int * 100 / int(fsize))
        pct_str = f"{pct_num}%"

        color = ("limegreen" if pct_num >= 100 else
                "yellow"     if pct_num >= 75  else
                "orange"     if pct_num >= 50  else
                "white")
        self.items_percent[row].setLabel(f"[COLOR {color}]{pct_str}[/COLOR]")

        self.items_speed[row].setLabel(str(speed) if speed else "")


        self.row_filename[row]        = filename
        self.row_state[row]           = state
        self.row_resumable[row]       = bool(resumable)
        self.row_bytesdownloaded[row] = dled_int
        self.row_data[row] = {
            "url": url, "headers": headers, "dest": dest, "id_downloading": id_dl
        }

        self._calculate_time_remaining(row, fsize, dled_int, speed)
        self.set_button_label(row)


        for ctl in (self.items_fileName[row], self.items_percent[row],
                    self.items_downloaded[row], self.items_fileSize[row],
                    self.items_speed[row], self.items_time[row],
                    self.items_action[row], self.items_delete[row]):
            ctl.setVisible(True)


    def _calculate_time_remaining(self, row, fsize, dled_int, speed_str):
        try:
            if not speed_str:
                self.items_time[row].setLabel("[COLOR gray]--:--[/COLOR]")
                return

            try:
                speed = float(speed_str)
            except ValueError:
                speed = 0.0

            if speed <= 0:
                self.items_time[row].setLabel("[COLOR gray]--:--[/COLOR]")
                return

            if not fsize or int(fsize) <= 0:
                self.items_time[row].setLabel("[COLOR gray]--:--[/COLOR]")
                return

            rem_bytes = int(fsize) - dled_int
            if rem_bytes <= 0:
                self.items_time[row].setLabel("[COLOR limegreen]00:00[/COLOR]")
                return

            rem_mb = rem_bytes / (1024 * 1024)
            secs = rem_mb / speed

            m, s = divmod(int(secs), 60)
            h, m = divmod(m, 60)

            if h:
                txt = f"{h:02d}:{m:02d}:{s:02d}"
                color = "yellow" if h < 1 else "orange"
            else:
                txt = f"{m:02d}:{s:02d}"
                color = "limegreen" if m < 10 else "yellow"

            self.items_time[row].setLabel(f"[COLOR {color}]{txt}[/COLOR]")

        except Exception:
            fflog_exc()
            self.items_time[row].setLabel(L(30447, "[COLOR red]error[/COLOR]"))

    def _clear_row(self, row: int):
        """Czyści i ukrywa widoczne kontrolki + zeruje dane pomocnicze."""

        self.items_fileName[row].reset()
        self.items_fileName[row].setVisible(False)


        for ctl in (
            self.items_percent[row],
            self.items_downloaded[row],
            self.items_fileSize[row],
            self.items_speed[row],
            self.items_time[row],
        ):
            ctl.setLabel("")
            ctl.setVisible(False)


        for btn in (self.items_action[row], self.items_delete[row]):
            btn.setLabel("[COLOR gray]-[/COLOR]")
            btn.setEnabled(False)
            btn.setVisible(False)


        self.row_filename[row]        = ""
        self.row_state[row]           = ""
        self.row_resumable[row]       = False
        self.row_data[row]            = {}
        self.row_bytesdownloaded[row] = 0

    def list_update(self):
        self.button_show_list.setEnabled(False)
        self.button_show_list.setLabel(self._action_labels["updating"])

        def update_worker():
            try:
                if not self.worker_active:
                    threading.Thread(target=self.worker, daemon=True).start()
            finally:
                self.button_show_list.setEnabled(True)
                self.button_show_list.setLabel(self._action_labels["refresh_list"])

        threading.Thread(target=update_worker, daemon=True).start()

    def prepareDatabase():
        """
        Tworzy (lub aktualizuje) tabelę download_manager.
        Dodajemy UNIQUE na `filename`, żeby REPLACE działał-jak-powinien
        i nie powstawały duplikaty przy wznowieniach.
        """
        control.make_dir(control.dataPath)
        with database_connection(control.downloadsFile) as dbcon:
            dbcur = dbcon.cursor()

            dbcur.execute("""
                CREATE TABLE IF NOT EXISTS download_manager (
                    filename        TEXT NOT NULL,
                    filesize        INTEGER,
                    percentage      TEXT,
                    downloaded      INTEGER,
                    resumable       INTEGER,
                    state           TEXT,
                    speed           TEXT,
                    url             TEXT,
                    headers         TEXT,
                    dest            TEXT,
                    id_downloading  TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(filename)             -- <<< WAŻNE
                )
            """)
            dbcon.commit()

    def insertIntoDb(fileName, filesize, percentage, downloaded,
                    resumable=True, state="running", speed="",
                    url="", headers="", dest="", id_downloading=""):
        """
        Wstawia lub nadpisuje rekord (dzięki UNIQUE na filename duplikaty znikają).
        """
        with database_connection(control.downloadsFile) as dbcon:
            dbcur = dbcon.cursor()
            ri = int(resumable) if isinstance(resumable, bool) else resumable
            dbcur.execute("""
                INSERT INTO download_manager
                    (filename, filesize, percentage, downloaded, resumable,
                        state, speed, url, headers, dest, id_downloading)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    filesize    = excluded.filesize,
                    percentage  = excluded.percentage,
                    downloaded  = excluded.downloaded,
                    resumable   = excluded.resumable,
                    state       = excluded.state,
                    speed       = excluded.speed,
                    url         = excluded.url,
                    headers     = excluded.headers,
                    dest        = excluded.dest,
                    id_downloading = excluded.id_downloading
            """, (fileName, filesize, percentage, downloaded,
                ri, state, speed, url, headers, dest, id_downloading))
            dbcon.commit()
            cnt = dbcur.execute("SELECT COUNT(*) FROM download_manager").fetchone()[0]
            return cnt

    def update(fileName, percentage="", downloaded="", state="running", speed=""):
        with database_connection(control.downloadsFile) as dbcon:
            dbcur=dbcon.cursor()
            dbcur.execute("""
                UPDATE download_manager
                SET percentage=?, downloaded=?, state=?, speed=?
                WHERE filename=?
            """,(percentage,downloaded,state,speed,fileName))
            dbcon.commit()

    def count_records():
        try:
            with database_connection(control.downloadsFile) as dbcon:
                dbcur=dbcon.cursor()
                cnt=dbcur.execute("SELECT count(*) FROM download_manager").fetchone()[0]
                return cnt
        except Exception:
            fflog_exc()
            return 0

    def downloadManager(window=None, files=0):
        if xbmcgui.Window(10000).getProperty("FanFilm-downloadManager-active"):
            return
        busy_dialog()
        try:
            prepareDatabase()
            files = files or count_records() or 5
            xbmcgui.Window(10000).setProperty("FanFilm-downloadManager-active","true")
            if not window:
                window=MyAddon(L(30448, "Download Manager"),files=files)
            close_busy_dialog()
            window.doModal()
        finally:
            xbmcgui.Window(10000).clearProperty("FanFilm-downloadManager-active")
            try:
                if window:
                    window.cancelThread=True
                    del window
            except Exception:
                fflog_exc()

    def _verify_continuation(dest, total, title):
        if not xbmcvfs.exists(dest):
            control.infoDialog(L(30449, "Continuation impossible"),L(30450, "Missing file from previous download\n")+title)
            update(title, state="broken")
            return False
        st=xbmcvfs.Stat(dest)
        if st.st_size()!=total:
            control.infoDialog(L(30449, "Continuation impossible"),L(30451, "File size from previous download does not match\n")+title)
            update(title, downloaded=total, state="broken")
            return False
        return True

    def _validate_response(resp, title):
        if not resp:
            control.infoDialog(L(30418, "Download error"),L(30452, "No valid server response.\n\n{title}\n\nDownload impossible.").format(title=title))
            return False
        return True

    def _get_content_length(resp):
        try:
            length=int(resp.headers.get("Content-Length",0))
            return length
        except Exception:
            fflog_exc()
            return 0

    def _validate_content_type(resp, transname):
        content_type=resp.headers.get("Content-Type","")
        if content_type=="application/vnd.apple.mpegurl":
            control.infoDialog(L(30453, "Download will not be processed"),L(30454, "Unsupported video file type\n\n[I]{content_type}[/I]").format(content_type=content_type))
            return False
        return True

    def _get_extension_from_content_type(content_type):
        if "video/" not in content_type:
            return "mp4"
        ext_map={
            "video/mp4":"mp4","video/mpeg":"mpg","video/quicktime":"qt",
            "video/x-ms-wmv":"wmv","video/x-msvideo":"avi","video/x-flv":"flv",
            "video/webm":"webm"
        }
        ext=ext_map.get(content_type,"mp4")
        return ext

    def _confirm_download(filename, content, total):
        action=L(30455, "resume download") if total else L(30456, "download")
        return xbmcgui.Dialog().yesno(
            L(30457, "Confirm {action}").format(action=action),
            L(30458, "{filename}\nto download{left_text}: {size}").format(
                filename=filename,
                left_text=' left' if total else '',
                size=source_utils.convert_size(content)
            ),
            yeslabel=L(30115, "Download"), nolabel=L(30445, "Cancel")
        )

    def _prepare_destination_path(dest, transname, season):
        if not transname:
            return dest
        if season:
            control.make_dir(dest)
            dest = os.path.join(dest, f"Season {int(season)}")
            control.make_dir(dest)
        else:
            control.make_dir(dest)
        final=os.path.join(dest,transname)
        return final

    def _handle_existing_file(dest, content, resp, url, headers, total):
        if not xbmcvfs.exists(dest) or total:
            return True
        st=xbmcvfs.Stat(dest)
        fsize=st.st_size()
        resumable="bytes" in resp.headers.get("Accept-Ranges"," ").lower() if resp else False
        if resumable and fsize<content:
            ans=xbmcgui.Dialog().yesnocustom(
                L(30460, "WARNING - Existing file detected"),
                L(30461, "on disk: {fsize}, on server: {content}.\n\nDo you want to continue?").format(
                    fsize=source_utils.convert_size(fsize),
                    content=source_utils.convert_size(content)
                ),
                customlabel=L(30422, "Continue"), yeslabel=L(30462, "Replace"), nolabel=L(30445, "Cancel")
            )
            if ans<1:
                control.infoDialog(L(30463, "Download cancelled"),L(30453, "Download will not be processed"),"WARNING")
                resp.close()
                return False
            elif ans==2:
                resp.close()
                return "continue"
        else:
            if not xbmcgui.Dialog().yesno(L(30460, "WARNING - Existing file detected"),L(30464, "Do you want to replace it?"),yeslabel=L(30462, "Replace"),nolabel=L(30445, "Cancel")):
                control.infoDialog(L(30463, "Download cancelled"),L(30453, "Download will not be processed"),"WARNING")
                resp.close()
                return False
        return True

    @classmethod
    def done(cls, title, dest, downloaded):
        """
        Tylko dla finished - nie dla stopped/cancelled by użytkownika.
        """
        if downloaded:

            pass
        else:

            pass

    def remove(fileName, filesize):
        try:
            with database_connection(control.downloadsFile) as dbcon:
                dbcur=dbcon.cursor()
                dbcur.execute("DELETE FROM download_manager WHERE filename=?", (fileName,))
                dbcon.commit()
                dbcur.execute("VACUUM")
        except Exception:
            fflog_exc()

    def clear_db():
        if False:
            return
        try:
            with database_connection(control.downloadsFile) as dbcon:
                dbcur=dbcon.cursor()
                dbcur.execute("UPDATE download_manager SET state='stopped' WHERE state='running'")
                dbcur.execute("DELETE FROM download_manager WHERE state NOT IN ('stopped','running')")
                dbcon.commit()
                dbcur.execute("VACUUM")
        except Exception:
            fflog_exc()

    def clear_db0():
        if False:
            return
        try:
            with database_connection(control.downloadsFile) as dbcon:
                dbcur=dbcon.cursor()
                dbcur.execute("DROP TABLE IF EXISTS download_manager")
                dbcon.commit()
                dbcur.execute("VACUUM")
        except Exception:
            fflog_exc()

    def set_button_label(self, r):
        """Aktualizuje etykiety i aktywność przycisków w kolumnach AKCJA / USUŃ."""
        state = self.row_state[r]
        resumable = self.row_resumable[r]


        if state == "finished":
            dest = self.row_data[r].get("dest", "")
            if dest and xbmcvfs.exists(dest):
                self.items_action[r].setLabel(self._action_labels["play"])
                self.items_action[r].setEnabled(True)
                self.row_action[r] = "play"
            else:
                self.items_action[r].setLabel(self._action_labels["no_action"])
                self.items_action[r].setEnabled(False)
                self.row_action[r] = ""

        elif state == "running":
            self.items_action[r].setLabel(self._action_labels["stop"])
            self.items_action[r].setEnabled(True)
            self.row_action[r] = "stop"

        elif resumable and state in ("stopped", "canceled"):
            self.items_action[r].setLabel(self._action_labels["resume"])
            self.items_action[r].setEnabled(True)
            self.row_action[r] = "resume"

        elif state == "broken":
            self.items_action[r].setLabel(self._action_labels["restart"])
            self.items_action[r].setEnabled(True)
            self.row_action[r] = "restart"

        else:
            self.items_action[r].setLabel(self._action_labels["no_action"])
            self.items_action[r].setEnabled(False)
            self.row_action[r] = ""

        if state != "running":
            self.items_delete[r].setLabel(self._action_labels["delete"])
            self.items_delete[r].setEnabled(True)
        else:
            self.items_delete[r].setLabel(self._action_labels["no_action"])
            self.items_delete[r].setEnabled(False)

    def do_action(self, r):

        try:
            what = self.row_action[r]

            if what == "stop":
                self._stop_download(r)

            elif what in ("resume", "restart"):
                self._resume_download(r, what)

            elif what == "delete":
                self._delete_row(r)

            elif what == "play":
                self._play_file(r)

        except Exception:
            fflog_exc()

            self.items_action[r].setEnabled(True)
            self.items_delete[r].setEnabled(True)

    def _play_file(self, r):
        """
        Odtwarza pobrany plik – a po wystartowaniu playbacku
        automatycznie zamyka okno Download Managera.
        """
        try:
            data = self.row_data[r]
            dest = data.get("dest", "")

            # czy plik istnieje?
            if dest and xbmcvfs.exists(dest):
                xbmc.executebuiltin(f'PlayMedia("{dest}")')
                self.close()
            else:
                control.infoDialog(
                    L(30465, "File not found"),
                    L(30466, "File does not exist in the expected location:\n{dest}").format(dest=dest)
                )

        except Exception:
            fflog_exc()
            control.infoDialog(
                L(30467, "Playback error"),
                L(30468, "Failed to play the selected file.")
            )

    def _stop_download(self, r):
            """Zatrzymuje aktywny transfer (Window-prop + DB)."""
            try:
                data = self.row_data[r]
                prop = data.get("id_downloading")
                fname = self.row_filename[r]
                dled_bytes = self.row_bytesdownloaded[r]  # Pobierz aktualnie pobrane bajty

                if prop:
                    xbmcgui.Window(10000).setProperty(prop, "break")

                if fname:
                    MyAddon.update(fname, downloaded=dled_bytes, state="stopped", speed="")

                self.row_state[r] = "stopped"
                self.set_button_label(r)
                self.items_action[r].setEnabled(True)

            except Exception:
                fflog_exc()

    def _resume_download(self, r, action):
        """Wznawia (lub zaczyna od nowa) wybrane pobieranie."""
        try:
            self.items_action[r].setEnabled(False)
            self.items_action[r].setLabel(self._action_labels["starting"])

            title = self.row_filename[r]
            if not title:
                self.items_action[r].setEnabled(True)
                self.set_button_label(r)
                return

            with database_connection(control.downloadsFile) as dbcon:
                dbcur = dbcon.cursor()
                record = dbcur.execute("SELECT * FROM download_manager WHERE filename=?", (title,)).fetchone()

                if not record:
                    self.items_action[r].setEnabled(True)
                    self.set_button_label(r)
                    return

                (filename, fsize, pct, dled, resumable,
                 state, speed, url, headers, dest, id_dl, *_) = record

            MyAddon.update(title, state="running", speed="")
            self.row_state[r] = "running"
            self.set_button_label(r)

            total = 0
            if action == "restart":
                total = 0
            else:
                try:
                    total = int(dled) if dled else 0
                except (ValueError, TypeError):
                    total = 0

            threading.Thread(
                target=doDownload,

                args=(url, dest, title, "", headers, filename, None, "mp4", total, False),
                daemon=True
            ).start()

        except Exception:
            fflog_exc()

            self.items_action[r].setEnabled(True)
            self.set_button_label(r)

    def _remove_download(self, r, fileName, fileSize):
        MyAddon.remove(fileName, fileSize)

        for grp in (self.items_fileName, self.items_fileName2,
                    self.items_fileSize, self.items_percent,
                    self.items_downloaded, self.items_speed,
                    self.items_time, self.items_action):
            for i in range(r, len(grp)):
                ctl = grp[i]
                if hasattr(ctl, 'reset'):
                    ctl.reset()
                    ctl.addLabel("")
                else:
                    ctl.setLabel("" if "Button" not in str(type(ctl)) else "-")
        self._clear_row(r)

    # def change_action(self):
    #     try:
    #         focused=self.getFocus()
    #         for i,item in enumerate(self.items_action):
    #             if focused==item:
    #                 cur=item.getLabel()
    #                 cycle={"download":"delete","resume":"delete","delete":"-","stop":"delete"}
    #                 new=cycle.get(cur,cur)
    #                 if new!=cur: item.setLabel(new)
    #                 break
    #     except Exception:
    #         fflog_exc()

    def set_navigation(self):
        """
        Konfiguruje pełną, cykliczną nawigację klawiaturą między wszystkimi
        aktywnymi elementami okna (przyciski dolne <> lista akcji).
        """
        self.button_show_list.controlRight(self.close_button)
        self.close_button.controlLeft(self.button_show_list)

        last_row_action_button = self.items_action[self.max_rows_files - 1]
        self.button_show_list.controlUp(last_row_action_button)
        self.close_button.controlUp(last_row_action_button)

        for i in range(self.max_rows_files):
            action_btn = self.items_action[i]
            delete_btn = self.items_delete[i]

            action_btn.controlRight(delete_btn)
            delete_btn.controlLeft(action_btn)

            up_target = self.close_button if i == 0 else self.items_action[i - 1]
            down_target = self.button_show_list if i == self.max_rows_files - 1 else self.items_action[i + 1]

            action_btn.controlUp(up_target)
            delete_btn.controlUp(up_target)

            action_btn.controlDown(down_target)
            delete_btn.controlDown(down_target)

    def setAnimation(self, control):
        control.setAnimations([
            ("WindowOpen","effect=fade start=0 end=100 time=200"),
            ("WindowClose","effect=fade start=100 end=0 time=100"),
        ])

# ---------------------------------------------------------------------------
# BACKWARD-COMPATIBILITY ALIASES
# ---------------------------------------------------------------------------
prepareDatabase = MyAddon.prepareDatabase
insertIntoDb    = MyAddon.insertIntoDb
update          = MyAddon.update
count_records   = MyAddon.count_records
countRecords    = MyAddon.count_records
clear_db        = MyAddon.clear_db
clear_db0       = MyAddon.clear_db0
clearDb         = MyAddon.clear_db
clearDb0        = MyAddon.clear_db0
downloadManager = MyAddon.downloadManager
