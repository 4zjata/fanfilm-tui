# -*- coding: utf-8 -*-

"""
    FanFilm Add-on

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from __future__ import annotations
from typing import Any, Iterable, Iterator, List, TypeVar, TYPE_CHECKING
from typing_extensions import TypedDict
import re
import time
from attrs import frozen, asdict as _attrs_asdict
from urllib.parse import quote_plus, unquote, urlparse

import xbmc
from . import cache, control, requests
from ..ff import apis, client, cleantitle
from .settings import settings
# TODO: TRAKT  from lib.ff import trakt
from lib.ff.debug import log_exception
from .log_utils import fflog
if TYPE_CHECKING:
    from ..sources import Source, SourceItem


S = TypeVar('S', bound='Source|SourceItem')


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class AliasItem(TypedDict, total=False):
    """Single entry from TMDB aliases list."""
    title: str
    country: str
    originalname: str


class ParsedFilename(TypedDict):
    """Result of parse_filename_title()."""
    title: str
    year: int | None
    season: int | None
    episode: int | None

FF_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0'
EDGE_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.3485.94'
CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
ANDROID_UA = 'Mozilla/5.0 (Linux; Android 8.0)'
DEFAULT_UA = CHROME_UA

# _USER_AGENTS = [FF_UA, EDGE_UA, CHROME_UA]
# RANDOM_UA = choice(_USER_AGENTS)

RES_4K = [
    " 4k",
    " hd4k",
    " 4khd",
    " uhd",
    " ultrahd",
    " ultra hd",
    " 2160",
    " 2160p",
    " hd2160",
    " 2160hd",
]
RES_1080 = [
    " 1080",
    " 1080p",
    " 1080i",
    " hd1080",
    " 1080hd",
    " m1080p",
    " fullhd",
    " full hd",
    " fhd",
    " 1o8o",
    " 1o8op",
]
RES_720 = [" 720", " 720p", " 720i", " hd720", " 720hd", " 72o", " 72op"]
RES_SD = [
    " 576",
    " 576p",
    " 576i",
    " sd576",
    " 576sd",
    " 480",
    " 480p",
    " 480i",
    " sd480",
    " 480sd",
    " 360",
    " 360p",
    " 360i",
    " sd360",
    " 360sd",
    " 240",
    " 240p",
    " 240i",
    " sd240",
    " 240sd",
]
RES_SCR = [" scr", " screener", " dvdscr", " dvd scr", " r5", " r6"]
RES_CAM = [
    " camrip",
    " tsrip",
    " hdcam",  # czy to zaliczać też?
    " hqcam",  # czy to zaliczać też?
    " hd cam",  # czy to zaliczać też?
    " cam rip",
    " hdts",  # czy to zaliczać też?
    " hd-ts",  # czy to zaliczać też?
    " dvdcam",
    " dvdts",
    " cam",
    " telesync",
    " ts",
]
AVC = [" h 264 ", " h264 ", " x264 ", " avc "]

# ADDITIONAL FOR EN SOURCES INFOS
CODEC_H265 = ["hevc", "h265", "x265"]
CODEC_H264 = ["avc", "h264", "x264"]
CODEC_XVID = ["xvid"]
CODEC_DIVX = ["divx", "div2", "div3"]
CODEC_MPEG = [
    "mpeg",
    "m4v",
    "mpg",
    "mpg1",
    "mpg2",
    "mpg3",
    "mpg4",
    "msmpeg",
    "msmpeg4",
    "mpegurl",
]
CODEC_MP4 = ["mp4"]
CODEC_M3U = ["m3u8", "m3u"]
CODEC_AVI = ["avi"]
CODEC_MKV = ["mkv", "matroska"]

AUDIO_8CH = ["ch8", "8ch", "ch7", "7ch", "7 1", "ch7 1", "7 1ch"]
AUDIO_6CH = ["ch6", "6ch", "6 1", "ch6 1", "6 1ch", "5 1", "ch5 1", "5 1ch"]
AUDIO_2CH = ["ch2", "2ch", "stereo", "dualaudio", "dual", "2 0", "ch2 0", "2 0ch"]
AUDIO_1CH = ["ch1", "1ch", "mono", "monoaudio", "ch1 0", "1 0ch"]

VIDEO_3D = [
    "3d",
    "sbs",
    "hsbs",
    "sidebyside",
    "side by side",
    "stereoscopic",
    "tab",
    "htab",
    "topandbottom",
    "top and bottom",
]

# EN sources tem workaround
# quick workaround
host_limit = True  # control.setting("host.limit") or "true"  # XXX missing setting 'host.limit'
host_limit_count = 3  # int(control.setting('host.count')) or '3'  # XXX missing setting 'host.count'

TMDB_API_URL = "https://api.themoviedb.org/3"
TMDB_API_KEY = (
    settings.getString("tmdb.api_key") or apis.tmdb_API
)  # Używaj metody control.setting do uzyskania klucza


def check_host_limit(item, items):
    try:
        if not host_limit:
            return False
        items = [i["source"] for i in items if "source" in i] or list(items)
        return items.count(item) == host_limit_count
    except Exception:
        return False


def supported_video_extensions():  # ok
    supported_video_extensions = xbmc.getSupportedMedia("video").split("|")
    unsupported = [
        "",
        ".url",
        ".bin",
        ".zip",
        ".rar",
        ".001",
        ".disc",
        ".7z",
        ".tar.gz",
        ".tar.bz2",
        ".tar.xz",
        ".tgz",
        ".tbz2",
        ".gz",
        ".bz2",
        ".xz",
        ".tar",
    ]
    return [i for i in supported_video_extensions if i not in unsupported]


def get_host(url):  # EN Sources
    try:
        url = url.replace(r"\/", r"/").replace(r"///", r"//")
        elements = urlparse(url)
        domain = elements.netloc or elements.path
        domain = domain.split("@")[-1].split(":")[0]
        res = re.search(r"(?:www\.)?([\w\-]*\.[\w\-]{2,3}(?:\.[\w\-]{2,3})?)$", domain)
        if res:
            domain = res.group(1)
        domain = domain.lower()
    except Exception:
        elements = urlparse(url)
        host = elements.netloc
        domain = host.replace("www.", "")
    return domain


def get_codec(term):  # EN Sources
    if any(value in term for value in CODEC_H265):
        _codec = "HEVC | "
    elif any(value in term for value in CODEC_H264):
        _codec = "AVC | "
    elif any(value in term for value in CODEC_MKV):
        _codec = "MKV | "
    elif any(value in term for value in CODEC_DIVX):
        _codec = "DIVX | "
    elif any(value in term for value in CODEC_MPEG):
        _codec = "MPEG | "
    elif any(value in term for value in CODEC_MP4):
        _codec = "MP4 | "
    elif any(value in term for value in CODEC_M3U):
        _codec = "M3U | "
    elif any(value in term for value in CODEC_XVID):
        _codec = "XVID | "
    elif any(value in term for value in CODEC_AVI):
        _codec = "AVI | "
    else:
        _codec = "0"
    return _codec


def get_audio(term):  # EN Sources
    if any(value in term for value in AUDIO_8CH):
        _audio = "7.1 | "
    elif any(value in term for value in AUDIO_6CH):
        _audio = "5.1 | "
    elif any(value in term for value in AUDIO_2CH):
        _audio = "2.0 | "
    elif any(value in term for value in AUDIO_1CH):
        _audio = "Mono | "
    else:
        _audio = "0"
    return _audio


def get_size(term):  # EN Sources
    try:
        _size = re.findall(r"(\d+(?:\.|/,|)?\d+(?:\s+|)(?:gb|GiB|mb|MiB|GB|MB))", term)
        _size = _size[0].encode("utf-8")
        _size = _size + " | "
    except Exception:
        _size = "0"
    return _size


def get_3D(term):  # EN Sources
    if any(value in term for value in VIDEO_3D):
        _3D = "3D | "
    else:
        _3D = "0"
    return _3D


_QUALITY_FN_RE = re.compile(r'\b(2160|1080|720|576|480)[pPi]?\b', re.I)


def get_quality_from_filename(name: str) -> Optional[str]:
    m = _QUALITY_FN_RE.search(name)
    if m:
        res = int(m.group(1))
        if res >= 2160:
            return '4K'
        elif res >= 1080:
            return '1080p'
        elif res >= 720:
            return '720p'
        elif res >= 576:
            return 'SD'
        else:
            return 'SD'
    return None


def get_quality(term1, term2=None):  # OK + workaround for en sources
    term = " {} ".format(term1 + (term2 or "")).lower()
    if any(i in term for i in RES_SCR):
        return "SCR"
    elif any(i in term for i in RES_CAM):
        return "CAM"
    elif any(i in term for i in RES_4K) and not any(i in term for i in RES_1080):
        return "4K"
    elif any(i in term for i in RES_1080):
        return "1080p"
    elif any(i in term for i in RES_720):
        return "720p"
    elif any(i in term for i in RES_SD):
        return "SD"
    elif "remux " in term and any(i in term for i in AVC):
        return "1080p"
    elif "remux " in term:
        return "4K"
    else:
        return "SD"


def get_info(term1, term2=None):  # EN Sources
    term = term1 + (term2 or "")
    _codec = get_codec(term)
    if not _codec or _codec == "0":
        _codec = ""
    _audio = get_audio(term)
    if not _audio or _audio == "0":
        _audio = ""
    _size = get_size(term)
    if not _size or _size == "0":
        _size = ""
    _3D = get_3D(term)
    if not _3D or _3D == "0":
        _3D = ""
    _info = _codec + _audio + _size + _3D
    return _info


def cleanup(term):  # EN Sources
    try:
        _term = strip_domain(term)
        _term = unquote(_term)
        _term = _term.lower()
        _term = re.sub("[^a-z0-9 ]+", " ", _term)
    except Exception:
        _term = str(term.lower())
    return _term


def cleanupALT(term):  # EN Sources
    try:
        _term = strip_domain(term)
        _term = _term.upper()
        _term = re.sub(r"(.+)(\.|\(|\[|\s)(\d{4}|S\d*E\d*|S\d*)(\.|\)|\]|\s)", "", _term)
        _term = re.split(r"\.|\(|\)|\[|\]|\s|-", _term)
        _term = [i.lower() for i in _term]
    except Exception:
        _term = str(term.lower())
    return _term


def get_release_quality(release_name, release_link=None):  # EN Sources
    try:
        if not release_name:
            return "SD", []
        try:
            release_name = cleanup(release_name)
            if release_link:
                release_link = cleanup(release_link)
        except Exception:
            release_name = cleanupALT(release_name)
            if release_link:
                release_link = cleanupALT(release_link)
        if release_link and release_link == release_name:
            release_link = None
        quality = get_quality(release_name, release_link)
        info = get_info(release_name, release_link)
        return quality, info
    except Exception:
        return "SD", []


def getFileType(url):
    try:
        url = url.lower()
        url = url.replace(" ", ".")  # for easier detection
        url = url.replace("_", ".")  # the same as above
        url = url.replace(".1080", ".Full")  # for case ..1995.1080p.. for 5.1 channels
        url = url.replace(".1440", "..1440")  # for case ..1995.1440p.. for 5.1 channels
        url = url.replace(
            ".10bit", "..10bit"
        )  # for case ..265.10bit..  for 5.1 channels
        url = url.replace(
            ".12bit", "..12bit"
        )  # for case ..265.12bit..  for 5.1 channels
        url = url.replace(".x26", "..x26")  # for case dts.x264
        ext = re.search(r"\.\w{2,4}$", url)  # search extension
        ext = ext[0] if ext else ""  # remember extension
        url = re.sub(r"\.\w{2,4}$", ".", url)  # extension out
    except Exception:
        url = str(url)

    type = ""

    if "bluray" in url:
        type += " BLURAY /"
    if "blu-ray" in url:
        type += " BLU-RAY /"
    if ".web-dl" in url:
        type += " WEB-DL /"
    if ".webdl" in url:
        type += " WEB-DL /"
    if ".web." in url:
        type += " WEB /"
    if "dvdrip" in url:
        type += " DVDRip /"
    if "dvd-rip" in url:
        type += " DVD-Rip /"
    if "hdrip" in url:
        type += " HDRip /"
    if "bd-r." in url:
        type += " BD-R /"
    if "bd-rip" in url:
        type += " BD-RIP /"
    if "bd.r." in url:
        type += " BD-R /"
    if "bd.rip" in url:
        type += " BD-RIP /"
    if "bdr." in url:
        type += " BD-R /"
    if "bdrip" in url:
        type += " BDRIP /"
    if any(i in url for i in ["dcprip", "dcp-rip", "dcp.rip"]):
        type += " DCP-Rip /"
    if "hdtv" in url:
        type += " HDTV /"
    if "tvrip" in url:
        type += " TVRip /"
    if any(
        i in url
        for i in [
            "camrip",
            "tsrip",
            "hdcam",
            "hqcam",
            "hdts",
            "hd-ts",
            "dvdcam",
            "dvdts",
            "cam",
            "telesync",
            ".ts",
        ]
    ):
        type += " cam /"
    if any(i in url for i in [".scr.", "scr.", ".screener", "dvdscr", ".r5.", ".r6."]):
        type += " SCR /"
    if ".md" in url:
        type += " MD /"
    if any(
        i in url
        for i in ["custom audio", "custom.audio", "custom-audio", "custom_audio"]
    ):
        type += " custom audio /"
    if any(i in url for i in ["ac3", "ac-3"]):
        if any(i in url for i in ["eac3", "e-ac3", "eac-3", "e-ac-3"]):
            type += " E-AC3 /"
        else:
            type += " AC3 /"
    if ".dd" in url:
        if ".ddp" in url:
            type += " DD+ /"
        else:
            if any(i in url for i in ["dd-ex", "dd.ex"]):
                type += " DD-EX /"
            else:
                type += " DD /"
    if ".dts" in url:
        if any(i in url for i in ["dts-hd", "dts.hd."]):
            if any(i in url for i in ["dts-hd.ma", "dts.hd.ma"]):
                type += " DTS-HD MA /"
            else:
                type += " DTS-HD /"
        elif any(i in url for i in ["dts-es", "dts.es"]):
            type += " DTS-ES /"
        elif any(i in url for i in ["dts-ex", "dts.ex"]):
            type += " DTS-EX /"
        elif any(i in url for i in ["dts-x", "dts.x", "dtsx"]):
            type += " DTS-X /"
        else:
            type += " DTS /"
    if ".truehd" in url:
        type += " TrueHD /"
    if ".lpcm" in url:
        type += " LPCM /"
    if ".aac" in url:
        type += " AAC /"
    if "5.1" in url:
        type += " 5.1 /"
    if "6.1" in url:
        type += " 6.1 /"
    if "7.1" in url:
        type += " 7.1 /"
    if "2.0" in url:
        type += " 2.0 /"
    if "5.0" in url:
        type += " 5.0 /"
    if "1.0" in url:
        type += " 1.0 /"
    if "atmos" in url:
        type += " ATMOS /"
    if "hdr" in url:
        type += " HDR /"
    if ".dv." in url or "dolbyvision" in url or ".dovi." in url:
        type += " DV /"
    if ".imax." in url:
        type += " IMAX /"
    if ".3d." in url:
        type += " 3D /"
    if any(i in url for i in ["subbed", " subs ", " sub "]):
        if type != "":
            type = type.rstrip("/")
            type += "| SUBS |"
        else:
            type = " SUBS |"
    #    if ".multi." in url:
    #        type += " MULTI /"
    #    if ".dual." in url:
    #        type += " DUAL /"
    if ".xvid" in url:
        type += " XVID /"
    if ".h.264" in url:
        type += " H.264 /"
    if ".h.265" in url:
        type += " H.265 /"
    if ".h264" in url:
        type += " H264 /"
    if ".x264" in url:
        type += " x264 /"
    if ".x265" in url:
        type += " x265 /"
    if ".h265" in url:
        type += " h265 /"
    if ".hevc" in url:
        type += " hevc /"
    if ".avc." in url:
        type += " AVC /"
    if ".av1." in url:
        type += " AV1 /"
    if ".vc1." in url or ".vc-1." in url:
        type += " VC1 /"
    if any(i in url for i in ["mpeg2", "mpeg-2"]):
        type += " MPEG-2 /"
    if any(i in url for i in ["mpeg4", "mpeg-4"]):
        type += " MPEG-4 /"
    if any(i in url for i in ["mpeg1", "mpeg-1"]):
        type += " MPEG-1 /"
    if "48fps" in url:
        type += " 48 fps /"
    if "50fps" in url:
        type += " 50 fps /"
    if "60fps" in url:
        type += " 60 fps /"
    if "100fps" in url:
        type += " 100 fps /"
    if "120fps" in url:
        type += " 120 fps /"
    if "extended" in url:
        type += " extended /"
    if "directors.cut" in url:
        type += " directors cut /"
    if "remastered" in url:
        type += " REMASTERED /"
    if "rekonstrukcja" in url:
        type += " Rekonstrukcja cyfrowa /"
    if "remux" in url:
        type += " REMUX /"
    # if ".mp4" in ext:
    # type += " MP4 /"
    if ".avi" in ext:
        type += " AVI /"
    if ".ts" in ext:
        type += " .TS /"
    if "mpg" in ext:
        type += " MPG /"
    type = type.rstrip("/")
    return type


def check_sd_url(release_link):
    try:
        release_link = release_link.lower()
        release_link = re.sub(r"\.\w{2,4}$", "", release_link)  # extension out
        release_link = release_link.replace(" ", ".").replace("_", ".")

        # Wykrywanie CAM - tylko jako osobne słowo (z separatorami), nie część większego (np. "CAMBiO")
        # CAM występuje jako: .cam. lub -cam. lub -cam- lub na końcu: .cam lub -cam
        if any(
            i in release_link
            for i in [
                "camrip",
                "tsrip",
                "hdcam",
                "hqcam",
                "hdts",
                "hd-ts",
                "dvdcam",
                "dvdts",
                "telesync",
                ".ts",
                ".cam.",
                "-cam.",
                "-cam-",
            ]
        ) or release_link.endswith(".cam") or release_link.endswith("-cam"):
            return "CAM"


        if "2160" in release_link or ".4k" in release_link or ".uhd" in release_link:
            return "4K"
        elif "1440" in release_link:
            return "1440p"
        elif "1080i" in release_link:
            quality = "1080i"
        elif "1080" in release_link:
            quality = "1080p"
        elif "720" in release_link:
            quality = "720p"
        elif ".hd." in release_link:
            quality = "720p"
        elif any(i in release_link for i in ["dvdscr", "r5", "r6"]):
            quality = "SCR"
        # CAM jest już wykrywany na początku funkcji
        else:
            quality = "SD"
        return quality
    except Exception:
        return "SD"


def quality_from_resolution(width: int = 0, height: int = 0) -> str:
    """Return quality string from pixel dimensions (pass width, height, or both)."""
    if width >= 3840 or height >= 2160:
        return '4K'
    if width >= 1920 or height >= 1080:
        return '1080p'
    if width >= 1280 or height >= 720:
        return '720p'
    return 'SD'


def strip_domain(url):  # ok
    try:
        if url.lower().startswith("http") or url.startswith("/"):
            url = re.findall("(?://.+?|)(/.+)", url)[0]
        url = client.replaceHTMLCodes(url)
        url = url.encode("utf-8")
        return url
    except Exception:
        log_exception()


def is_host_valid(url, domains):  # ok
    try:
        host = __top_domain(url)
        hosts = [
            domain.lower() for domain in domains if host and host in domain.lower()
        ]

        if hosts and "." not in host:
            host = hosts[0]
        if hosts and any([h for h in ["google", "picasa", "blogspot"] if h in host]):
            host = "gvideo"
        if hosts and any([h for h in ["akamaized", "ocloud"] if h in host]):
            host = "CDN"
        return any(hosts), host
    except Exception:
        log_exception()
        return False, ""


def __top_domain(url):  # ok
    elements = urlparse(url)
    domain = elements.netloc or elements.path
    domain = domain.split("@")[-1].split(":")[0]
    regex = r"(?:www\.)?([\w\-]*\.[\w\-]{2,3}(?:\.[\w\-]{2,3})?)$"
    res = re.search(regex, domain)
    if res:
        domain = res.group(1)
    domain = domain.lower()
    return domain


def append_headers(headers: dict[str, str]) -> str:  # ok
    return "|%s" % "&".join(
        ["%s=%s" % (key, quote_plus(headers[key])) for key in headers]
    )


_M3U8_RESOLUTION_RE = re.compile(r'RESOLUTION=\s*\d+\s*x\s*(\d+)', re.I)


def probe_m3u8_quality(url: str, headers: dict[str, str] | None = None,
                       timeout: float = 4.0,
                       on_unknown: str | None = None) -> str | None:
    """Fetch HLS master playlist, return quality label from max RESOLUTION.

    Returns one of '4K' / '1440p' / '1080p' / '720p' / 'SD' on success.
    Returns None when the fetch itself fails (timeout / non-2xx / exception)
    — distinguishes "dead URL" from "valid m3u8 without resolution metadata"
    so callers can filter unreachable streams.
    Returns `on_unknown` (default None) when the m3u8 has no RESOLUTION tags
    (single-bitrate stream) but was fetched successfully.
    """
    try:
        r = requests.get(url, headers=headers or {}, timeout=timeout)
        if not r.ok:
            return None
        heights = [int(m.group(1)) for m in _M3U8_RESOLUTION_RE.finditer(r.text)]
        if not heights:
            return on_unknown
        h = max(heights)
        if h >= 2160:
            return '4K'
        if h >= 1440:
            return '1440p'
        if h >= 1080:
            return '1080p'
        if h >= 720:
            return '720p'
        return 'SD'
    except Exception:
        return None


def convert_size(size_bytes: int | None) -> str:
    import math

    if size_bytes is None:
        return ''
    if size_bytes == 0:
        return '0 B'
    units = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
    exp = int(math.log(size_bytes, 1024))
    val: float = size_bytes / 1024 ** exp
    if val < 10:
        val = round(val, 2)
    elif val < 100:
        val = round(val, 1)
    else:
        val = int(val)
    return f'{val} {units[exp]}'


def is_ai_lector(text: str) -> bool:
    """Detect AI-generated 'lektor' markers in filenames/labels (PL.AI, PL.Ai, lektor_pl_ai, etc)."""
    if not text:
        return False
    t = text.lower()
    # normalize brackets/extra whitespace; keep dots/underscores/dashes in place
    t = re.sub(r'[\[\]\(\)]+', ' ', t)
    # allow up to 2 separator chars (space, dot, underscore, dash) between tokens
    # so "lektor. AI", "PL.AI", "PL_AI", "lektor_pl_ai", "ai lektor" all match
    return bool(re.search(
        r'\b(?:pl[ _.-]{0,2}ai|ai[ _.-]{0,2}pl|lektor[ _.-]{0,2}(?:pl[ _.-]{0,2})?ai|ai[ _.-]{0,2}lektor|plai)\b',
        t, flags=re.I,
    ))


def get_lang_by_type(text: str) -> tuple[str, str]:
    text = text.lower()
    text = re.sub(r"[\][._-]", " ", text)
    text = f" {text} "  # dla jednowyrazowców, jak w ekino
    if re.search(r'\b(?:plsub[ _.-]{0,2}ai|napisy[ _.-]{0,2}(?:pl[ _.-]{0,2})?ai|ai[ _.-]{0,2}(?:plsub|napisy))\b', text, flags=re.I):
        return "pl", "Napisy AI"
    if is_ai_lector(text):
        return "pl", "Lektor AI"
    # multi: both PL and ENG markers present in the same filename
    if re.search(r'\b(?:po?l|eng?)\b', text) and re.search(r'\beng?\b', text) and re.search(r'\bpo?l\b', text):
        return "multi", ""
    if any(x in text for x in [" multi ", " mul "]):
        return "multi", ""
    if any(x in text for x in [" dubbing", "pldub", "pl dub", " dub "]):
        if any(x in text for x in ["kino", "md"]):
            return "pl", "Dubbing Kino"
        return "pl", "Dubbing"
    if "lektor pl" in text or "lektor" in text:
        return "pl", "Lektor"
    if any(x in text for x in ["plsub", "napisy pl", "napisy", "nap pl"]):
        return "pl", "Napisy"
    if any(x in text for x in ["polski", "polish", " pl ", "pl ", " pol "]):
        return "pl", ""
    if re.search(r"[ąęółśżźćń]", text, flags=re.I):
        return "pl", ""
    if any(x in text for x in ["subbed", " subs ", " sub "]):
        return "", "Napisy"
    return "", ""


_RE_LABEL_ENG = re.compile(r'\beng?\b')
_RE_FILENAME_NAPISY = re.compile(r'\bnapisy\b', re.IGNORECASE)


def parse_label_language(label: str, filename: str = '', has_subs: bool = False) -> tuple[str, str]:
    """Detect language/audio type from a player-provided label, using filename as context.

    For streaming sites that annotate each quality variant with a label
    (e.g. 'English', 'Lektor', 'Napisy PL', 'Dubbing'). Priority:
      1. filename-based detection when it yields an audio type
      2. label-based rules
      3. default → ('pl', 'Lektor')
    """
    fn_lang, fn_audio = get_lang_by_type(filename) if filename else ('', '')
    if fn_audio:
        return fn_lang or 'pl', fn_audio
    label_lower = label.lower()
    if _RE_LABEL_ENG.search(label_lower) and 'lektor' not in label_lower:
        if has_subs or _RE_FILENAME_NAPISY.search(filename):
            return 'pl', 'Napisy'
        return 'en', ''
    if 'dubbing' in label_lower or 'pldub' in label_lower:
        return 'pl', 'Dubbing'
    if 'napisy' in label_lower or ('sub' in label_lower and 'eng' not in label_lower):
        return 'pl', 'Napisy'
    return fn_lang or 'pl', 'Lektor'


def parse_source_quality_lang(filename: str) -> tuple[str, str, str]:
    """Return (quality, language, info) from a warez filename.

    Convenience combining check_sd_url + get_lang_by_type for the common scraper pattern.
    """
    quality = check_sd_url(filename)
    language, info = get_lang_by_type(filename)
    return quality, language, info


def convert_size_to_bytes(size: str | int) -> int:
    if isinstance(size, int):
        return size
    suffixes = ("", "k", "m", "g", "t")
    multipliers = {"{}b".format(l): 1024**i for i, l in enumerate(suffixes)}
    sre = re.compile(
        r"(\d+(?:[.,]\d+)?)\s?({})".format("|".join(x + "b" for x in suffixes)),
        re.IGNORECASE,
    )

    def _convert_match(m):
        return str(
            float(m.group(1).replace(",", ".")) * multipliers[m.group(2).lower()]
        )

    bytes = sre.sub(_convert_match, size)

    try:
        return int(float(bytes))
    except ValueError:
        return 0




def get_netflix_ep_id(netflix_id: str, season: int | str, episode: int | str, episode_titles: list[str] | None = None) -> tuple[str, str] | None:
    """Fetch Netflix episode ID (epid) and season ID (seasid) from uNoGS API.

    If *episode_titles* is provided, matches by episode title (preferred — handles
    TMDB vs Netflix season numbering mismatches). Falls back to (season, episode).
    """
    try:
        user_id = {'user_name': '1683364584.456'}
        response = requests.post('http://unogs.com/api/user', data=user_id, timeout=10)
        token = response.json().get('token', {}).get('access_token')
        if not token:
            return None

        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
            'REFERRER': 'http://unogs.com',
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36',
            'X-Requested-With': 'XMLHttpRequest',
        }

        r = requests.get(f'http://unogs.com/api/title/episodes?netflixid={netflix_id}', headers=headers, timeout=10)
        r.raise_for_status()
        apianswer = r.json()

        # Try title match first (bypasses TMDB vs Netflix numbering mismatches)
        if episode_titles:
            clean_targets = {cleantitle.get(t) for t in episode_titles if t}
            clean_targets.discard(None)
            for season_data in apianswer:
                for ep in season_data.get('episodes', []):
                    if cleantitle.get(ep.get('title', '')) in clean_targets:
                        return str(ep['epid']), str(ep['seasid'])

        # Fall back to season/episode number match
        target_s, target_e = int(season), int(episode)
        season_data = next((s for s in apianswer if s.get('season') == target_s), None)
        if not season_data:
            return None

        ep_data = next((e for e in season_data.get('episodes', []) if e.get('epnum') == target_e), None)
        if not ep_data:
            return None

        return str(ep_data['epid']), str(ep_data['seasid'])
    except Exception:
        log_exception()
        return None


def numbers_in_pattern(pat, mode=0, prefix=""):
    numbers = (
        "zero",
        "one|jeden|i",
        "two|dwa|ii",
        "three|trzy|iii",
        "four|cztery|iv",
        "five|pięć|v",
        "six|sześć|vi",
        "seven|siedem|vii",
        "eight|osiem|viii",
        "nine|dziewięć|ix",
    )
    if mode != 2:
        pat = re.sub(
            r"(?<!\|)\b\d\b(?!\|)",
            lambda x: f"({prefix}{x.group()}|{numbers[int(x.group())-0]})",
            pat,
        )  # cyfry na słowa
    if mode != 1:
        pat = re.sub(
            r"(?<!\|)\b(\w{3,8}|[iIvVxX]{1,2})\b(?!\|)",
            lambda x: f"({prefix}{x.group()}|{[x.group().lower() in n.split('|') for n in numbers].index(True)+0})"
            if any(x.group().lower() in n.split('|') for n in numbers)
            else x.group(),
            pat,
        )  # słowa na cyfry
    return pat


_DIAKRYT: dict[str, str] = {
    "ą": "a",
    "ć": "c",
    "ę": "e",
    "ł": "l",
    "ń": "n",
    "ó": "o",
    "ś": "s",
    "ż": "z",
    "ź": "z",
}
_DIAKRYT_REV: dict[str, str] = {v: k for k, v in _DIAKRYT.items()}
_DIAKRYT_REV["z"] = "żź"  # korekta – ź i ż oba na z

_DIAKRYT_TABLE = str.maketrans(
    ''.join(_DIAKRYT) + ''.join(_DIAKRYT).upper(),
    ''.join(_DIAKRYT.values()) + ''.join(_DIAKRYT.values()).upper(),
)


def strip_diacritics(text: str) -> str:
    return text.translate(_DIAKRYT_TABLE)


def _diacritic_replacer(L="", mode=0, prefix="[", postfix="]"):
    def _replace(L):
        # if type(L) is not str:
        if isinstance(L, re.Match):
            L = L.group()
        l = L.lower()
        if l in _DIAKRYT and mode != 2:
            return f"{prefix}{l}{_DIAKRYT[l]}{postfix}"  # ą -> [ąa]
        elif l in _DIAKRYT_REV and mode != 1:
            return f"{prefix}{l}{_DIAKRYT_REV[l]}{postfix}"  # a -> [aą]
        else:
            return L

    if L:
        return _replace(L)
    else:
        return _replace


def normalize_unicode_in_pattern(pattern, prefix="[", postfix="]", qm="?"):
    t1 = pattern
    t2 = cleantitle.normalize(t1)
    if t1 != t2:
        if len(t1) == len(t2):
            t3 = ""
            for i, t in enumerate(t1):
                t3 += f"{prefix}{t}{t2[i]}{qm}{postfix}" if t != t2[i] else t
            t2 = t3.replace(prefix+prefix, prefix).replace(postfix+postfix, postfix)
            # dodatkowe czyszczenia
            # pr = re.escape(prefix)
            # po = re.escape(postfix)
            # t2 = re.sub(rf"(?<={pr})(\w*(?:{pr})?\w+{po}[\w\?]*)?(?={po})", lambda x: x.group().replace(prefix,"").replace(postfix,""), t2)
            # t2 = re.sub(fr"(?<={pr})(\w)(\w)(\2|\1)(?=\??{po})", r"\1\2", t2)
    return t2


_ROMAN_VALUES: list[tuple[int, str]] = [
    (1000, 'M'),
    (900, 'CM'),
    (500, 'D'),
    (400, 'CD'),
    (100, 'C'),
    (90, 'XC'),
    (50, 'L'),
    (40, 'XL'),
    (10, 'X'),
    (9, 'IX'),
    (5, 'V'),
    (4, 'IV'),
    (1, 'I'),
]


_ROMAN_BY_CHAR: dict[str, int] = {r: i for i, r in _ROMAN_VALUES if len(r) == 1}


def roman_to_int(s: str) -> int:
    """Convert Roman numeral string to integer. Returns 0 for invalid input."""
    result = 0
    prev = 0
    for ch in reversed(s.upper()):
        curr = _ROMAN_BY_CHAR.get(ch, 0)
        result += curr if curr >= prev else -curr
        prev = curr
    return result


# def get_trakt_id_from_tmdb(tmdb_id):
#     result = trakt.getTraktAsJson(f"/search/tmdb/{tmdb_id}?type=show")
#     if result:
#         return result[0]["show"]["ids"]["trakt"]
#     return None


# def get_absolute_number_trakt(tmdb, episode, season):
#     trakt_show_id = get_trakt_id_from_tmdb(tmdb)
#     if not trakt_show_id:
#         return None

#     seasons = trakt.getTraktAsJson(f"/shows/{trakt_show_id}/seasons")
#     if not seasons:
#         return None

#     # Convert to integers if they are strings
#     season = int(season)
#     episode = int(episode)

#     absolute_number = 0

#     for season_data in seasons:
#         if season_data["number"] < season:
#             absolute_number += season_data["aired_episodes"]
#         elif season_data["number"] == season:
#             absolute_number += episode
#             break

#     return absolute_number



def detect_script(s, mode=0):
    from unicodedata import category

    def _detect_char_script(c):
        v = ord(c or ' ')
        # print(c, hex(v), v, (category(c)))  # debug
        if not (c and category(c)[0] == 'L'):  # if not a letter
            return ""
        if 0x20 <= v < 0x370:
            return False
        if 0x370 <= v <= 0x3ff:
            return 'gr'
        if 0x400 <= v <= 0x52f:
            return "rus"
        return True

    s = s.strip()
    if not len(s):
        return None

    if mode == 0:  # whole text (only letters)
        r = [_detect_char_script(l) for l in s if category(l)[0] == 'L']
    elif mode == 2:
        r = [_detect_char_script(s[i]) for i in [0, -1]]  # first and last letter
    else:
        r = []
    if r.count("gr"):
        return "gr"
    if r.count("rus"):
        return "rus"
    return any(r)



def deduplicate_list_ci(items: list[str]) -> list[str]:
    """Deduplicate list preserving order (case-insensitive).

    Args:
        items: List of strings to deduplicate

    Returns:
        List with duplicates removed (case-insensitive), preserving original order
    """
    seen_lower = set()
    unique = []
    for item in items:
        item_lower = item.lower()
        if item_lower not in seen_lower:
            seen_lower.add(item_lower)
            unique.append(item)
    return unique


def is_anime(ffitem) -> bool:
    """Check if content (movie or TV show) is anime.

    Args:
        ffitem: FFItem object with keywords attribute

    Returns:
        True if content is tagged as anime, False otherwise
    """
    from const import const
    # For TV shows/episodes, check show_item.keywords; for movies, check ffitem.keywords
    if ffitem.show_item:  # Episode/TV show
        return bool(ffitem.show_item.keywords and const.sources.check_anime in ffitem.show_item.keywords)
    else:  # Movie
        return bool(ffitem.keywords and const.sources.check_anime in ffitem.keywords)


# można będzie dopisywać frazy, które mogą być pomijane przy szukaniu
antifalse_filter_exceptions = [
    "The TV Series",
]


def extract_cookie(cookie: str, cookie_name: str) -> str:
    """Get cookie value from string."""
    import json
    # replace all cookies to wanted one
    cookie_re = re.escape(cookie_name)
    cookie = re.sub(rf'^.*\b{cookie_re}=([^;]*)(?:;.*|$)', r'\1', cookie)
    # JSON, used by Copy Cookies web extension
    if re.search(rf'"name"\s*:\s*"{cookie_re}"', cookie):
        try:
            for cookies in json.loads(cookie):
                if cookies.get('name') == cookie_name:
                    return cookies.get('value', '')
        except json.JSONDecodeError:
            pass
        return ''
    return cookie.strip(' "\'')


def setting_cookie(setting_name: str, cookie_name: str) -> str:
    """Get cookie value from settings."""
    val = settings.getString(setting_name)
    return extract_cookie(val, cookie_name)


def sources_with_links(sources: Iterable[S]) -> list[S]:
    """Wywalanie zepsutych url (jesli nie są to pliki lokalne)."""
    def match(s: S) -> bool:
        if s.get('local'):
            return True
        if (url := getattr(s, 'url', ...)) is ...:  # support for Source object and SourceItem dict
            url = s.get('url', '')
        if not url:
            return False
        return '://' in url or url.startswith('DRMFF')

    return [src for src in sources if match(src)]


# ---------------------------------------------------------------------------
# Asian content detection (shared by dramaclub24, dramaqueen, …)
# ---------------------------------------------------------------------------

ASIAN_COUNTRIES: frozenset[str] = frozenset({'KR', 'JP', 'CN', 'TW', 'HK'})
ASIAN_LANGUAGES: frozenset[str] = frozenset({'ko', 'ja', 'zh'})

# TMDB keyword → frozenset of ISO 3166-1 country codes it implies.
# Used as fallback when production country alone is insufficient
# (e.g. Shōgun: US co-production with Japanese keywords).
ASIAN_KEYWORD_COUNTRIES: dict[str, frozenset[str]] = {
    'japan':        frozenset({'JP'}),
    'japanese':     frozenset({'JP'}),
    'samurai':      frozenset({'JP'}),
    'jidaigeki':    frozenset({'JP'}),
    'feudal japan': frozenset({'JP'}),
    'sengoku period': frozenset({'JP'}),
    'edo period':   frozenset({'JP'}),
    'meiji period': frozenset({'JP'}),
    'anime':        frozenset({'JP'}),
    'manga':        frozenset({'JP'}),
    'ninja':        frozenset({'JP'}),
    'korea':        frozenset({'KR'}),
    'korean':       frozenset({'KR'}),
    'k-drama':      frozenset({'KR'}),
    'seoul':        frozenset({'KR'}),
    'china':        frozenset({'CN'}),
    'chinese':      frozenset({'CN'}),
    'hong kong':    frozenset({'HK'}),
    'taiwan':       frozenset({'TW'}),
    'taiwanese':    frozenset({'TW'}),
    'wuxia':        frozenset({'CN'}),
    'thailand':     frozenset({'TH'}),
    'thai':         frozenset({'TH'}),
}


def get_asian_country_codes(item) -> frozenset[str]:
    """Return effective Asian country codes for an ffitem/show_item.

    Checks production countries first, then falls back to TMDB keywords.
    Returns a frozenset of ISO 3166-1 codes (may include non-ASIAN_COUNTRIES
    codes if keywords imply them — callers decide what to do with the result).
    """
    try:
        codes = frozenset(item.vtag.getCountryCodes())
    except Exception:
        codes = frozenset()

    asian = codes & ASIAN_COUNTRIES
    if asian:
        return asian

    # Keyword fallback
    try:
        keywords = set((item.keywords or {}).keys())
    except Exception:
        return frozenset()

    extra: set[str] = set()
    for kw, implied in ASIAN_KEYWORD_COUNTRIES.items():
        if kw in keywords:
            extra |= implied
    return frozenset(extra)


def is_asian_content(item) -> bool:
    """Return True if item is Asian content by country, language or keyword."""
    try:
        if ASIAN_LANGUAGES & set(item.vtag.getLanguageCodes()):
            return True
    except Exception:
        pass
    return bool(get_asian_country_codes(item))


# ---------------------------------------------------------------------------
# Indian / Bollywood content detection (used by filmlinks4u, …)
# ---------------------------------------------------------------------------

INDIAN_COUNTRIES: frozenset[str] = frozenset({'IN'})
INDIAN_LANGUAGES: frozenset[str] = frozenset({'hi', 'ta', 'te', 'ml', 'kn', 'bn', 'mr', 'pa', 'gu'})
INDIAN_KEYWORDS: frozenset[str] = frozenset({
    'bollywood', 'india', 'indian', 'indian cinema',
    'hindi cinema', 'kollywood', 'tollywood', 'mollywood',
    'mumbai', 'delhi', 'shankar', 'dharma productions',
})


def is_indian_content(item) -> bool:
    """Return True if item is Indian/Bollywood content by country, language or keyword."""
    try:
        codes = set(item.vtag.getCountryCodes())
        if INDIAN_COUNTRIES & codes:
            return True
        langs = set(item.vtag.getLanguageCodes())
        if INDIAN_LANGUAGES & langs:
            return True
    except Exception:
        pass
    try:
        keywords = set((item.keywords or {}).keys())
        if INDIAN_KEYWORDS & keywords:
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Shared scraper utilities
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def get_original_title(aliases: list[AliasItem]) -> str:
    """Extract the original (non-localised) title from TMDB aliases, skipping non-Latin scripts."""
    if not aliases:
        return ""
    for a in aliases:
        name = a.get("originalname") or ""
        if name:
            return "" if detect_script(name) else name
    return ""


def prepare_alias_search_list(aliases: list[AliasItem], year: str | int) -> list[str]:
    """Clean raw TMDB aliases into a deduplicated list of search-compatible strings.

    Strips year, empty brackets, non-Latin scripts. Applies NFKC normalisation.
    """
    import unicodedata
    year_str = str(year)
    result = []
    for a in aliases:
        t = a.get("title") or a.get("originalname") or ""
        t = t.replace(year_str, "").replace("()", "").strip()
        t = unicodedata.normalize("NFKC", t)
        t = re.sub(r' ?(\d)/', r' \1 ', t)  # ⅓-style fractions → "1 3"
        t = t.strip()
        if t and not detect_script(t):
            result.append(t)
    return deduplicate_list_ci(result)


def year_matches(text: str, expected_year: int, tolerance: int = 1) -> bool:
    """Check if any year found in text is within tolerance of expected_year."""
    if expected_year <= 0:
        return True
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if not years:
        return True
    return any(abs(yr - expected_year) <= tolerance for yr in years)


def format_episode_key(season, episode, with_wildcard: bool = False) -> str:
    """Format season/episode as sNNeNN (e.g. s01e05)."""
    s = str(int(season)).zfill(2)
    e = str(int(episode)).zfill(2)
    return f"s{s}*e{e}" if with_wildcard else f"s{s}e{e}"


def search_queries(title: str, local_title: str) -> Iterator[str]:
    """Yield deduplicated search queries: original title first, then local if different."""
    if title:
        yield title
    if local_title and cleantitle.get_simple(title or '') != cleantitle.get_simple(local_title):
        yield local_title


def search_queries_extended(title: str, local_title: str) -> Iterator[str]:
    """Yield deduplicated search query variants for sites with tricky search.

    Tries (in order, skipping duplicates per cleantitle):
      original → "." as ":" → "–"/"-" normalised → "." removed
    Extracted from old zaluknijcc.py manual search fallback chain.
    """
    seen: set = set()

    def _emit(s: str):
        s = s.strip()
        key = cleantitle.get_simple(s)
        if s and key not in seen:
            seen.add(key)
            return s
        return None

    titles = [title or "", local_title or ""]
    transforms = [
        lambda t: t,
        lambda t: t.replace(".", ":"),
        lambda t: t.replace("–", "-").replace(" - ", " "),
        lambda t: t.replace(".", ""),
    ]

    for fn in transforms:
        for t in titles:
            if v := _emit(fn(t)):
                yield v


# ---------------------------------------------------------------------------
# Country variant conflict detection (franchise show disambiguation)
# E.g. reject "The.Office.UK.S01E01" when searching for "The Office (US)".
# ---------------------------------------------------------------------------

# 2-letter country codes used as franchise variant identifiers in filenames.
# Intentionally excludes common English words: 'in', 'no', 'be', 'it'.
FRANCHISE_CC = frozenset({
    'uk', 'au', 'us', 'pl', 'de', 'fr', 'nl', 'se',
    'dk', 'fi', 'cz', 'hu', 'ro', 'tr', 'ru', 'il',
    'br', 'mx', 'ar', 'jp', 'kr', 'cn', 'pt', 'ch', 'za', 'nz', 'ca', 'es',
})
_EP_MARKER_RE = re.compile(r'^s\d{1,2}e\d{2,4}$')
_CC_NONALNUM_RE = re.compile(r'[^a-z0-9 ]')


def extract_alias_country_codes(aliases: list[AliasItem], base_title: str) -> set[str]:
    """Extract 2-letter country codes from TMDB aliases that qualify the base title.
    E.g. "The Office (US)" alias + base "The Office" → {'us'}.
    Returns empty set if no qualifier found → country check is skipped.
    """
    base_words = set(_CC_NONALNUM_RE.sub(' ', base_title.lower()).split())
    result = set()
    for a in (aliases or []):
        alias_str = (a.get('title', '') or a.get('originalname', '')) if isinstance(a, dict) else ''
        if not alias_str:
            continue
        words = _CC_NONALNUM_RE.sub(' ', alias_str.lower()).split()
        if len(words) >= 2 and words[-1] in FRANCHISE_CC and base_words.issubset(set(words)):
            result.add(words[-1])
    return result


def detect_country_variant_conflict(filename_words_lower: list[str], titles: Iterable[str]) -> str | None:
    """Return a conflicting 2-letter country code if the filename appears to be a
    country-specific remake that doesn't match our search target, else None.

    Scans ALL words before the SxxExx episode marker for franchise country codes.
    Using all-pre-episode words (not just the adjacent ones) correctly handles
    patterns like "The.Office.PL.Pilot.S01E01" where an episode name sits between
    the country code and the episode marker.

    If titles carry no country qualifier, returns None (no filter applied).

    Args:
        filename_words_lower: list of lowercase words (dots/special chars stripped).
        titles: iterable of search title strings (any case/format).
    """
    ep_idx = next((i for i, w in enumerate(filename_words_lower) if _EP_MARKER_RE.match(w)), None)
    if ep_idx is None or ep_idx == 0:
        return None

    # Collect all franchise CCs that appear before the episode marker
    cc_in_pre_ep = {w for w in filename_words_lower[:ep_idx] if w in FRANCHISE_CC}
    if not cc_in_pre_ep:
        return None

    # Collect expected country codes from TMDB-provided titles (e.g. "the office us" → 'us')
    our_countries = set()
    for t in titles:
        words_t = _CC_NONALNUM_RE.sub(' ', t.lower()).split()
        if len(words_t) >= 2 and words_t[-1] in FRANCHISE_CC:
            our_countries.add(words_t[-1])

    if not our_countries:
        return None

    conflicts = cc_in_pre_ep - our_countries
    return next(iter(conflicts)) if conflicts else None


def check_parsed_title_cc(parsed_title: str, search_title: str, expected_cc: set[str]) -> str | None:
    """Return a conflicting country code if parsed_title has an extra CC not in expected_cc.
    Returns None if no conflict or expected_cc is empty (→ no check performed).
    Used by scrapers that compare the already-extracted title portion of a filename.
    """
    if not expected_cc:
        return None
    parsed_words = set(_CC_NONALNUM_RE.sub(' ', parsed_title.lower()).split())
    search_words = set(_CC_NONALNUM_RE.sub(' ', search_title.lower()).split())
    for word in parsed_words - search_words:
        if word in FRANCHISE_CC and word not in expected_cc:
            return word
    return None


def build_alias_list(title: str, local_title: str, aliases: list[AliasItem], year: int) -> list[str]:
    """Build deduplicated alias list: title + local_title + originalname + filtered alias titles."""
    seen: dict[str, None] = {}
    year_str = str(year)

    def _add(t: str) -> None:
        t = t.strip() if t else ""
        if t:
            seen[t] = None

    _add(title)
    _add(local_title)
    _add(next((a.get("originalname") for a in aliases if a.get("originalname")), ""))

    for alias_item in aliases:
        alias_title = (alias_item.get("title") or alias_item.get("originalname") or "")
        alias_title = alias_title.replace(year_str, "").replace("()", "").strip()
        if alias_title and not detect_script(alias_title):
            _add(alias_title)

    return list(seen)


def build_relevant_franchises(titles: list[str], franchise_names: dict[str, list[str]], sep: str) -> dict[str, list[str]]:
    """Return the subset of *franchise_names* patterns relevant to *titles*.

    Builds escaped regex patterns for each franchise name and returns only
    the entries whose key appears in at least one of the given titles.
    """
    franchise_aliases = {
        k: [sep.join(map(re.escape, n.split())) for n in v]
        for k, v in franchise_names.items()
    }
    relevant: dict = {}
    for t in titles:
        tl = t.lower()
        for key, patterns in franchise_aliases.items():
            if key.lower() in tl:
                relevant[key] = patterns
    return relevant


# ---------------------------------------------------------------------------
# Warez filename utilities  (shared across Polish scrapers)
# ---------------------------------------------------------------------------

_WAREZ_TOKENS_PAT = (
    r"po?l(?:dub|sub)?|lektor|dubbing|subbed|napisy|polish"
    r"|eng?|de|fr|es|it|nl|us|dual|multi|p2p"
    r"|bluray|blu-ray|bdrip|brip|hdrip|dvdrip|hdtv|hddvd|hdcam"
    r"|web[.\-]?dl|webrip|webhdrip|remux|3d|imax"
    r"|cam(?:rip)?|ts|telesync|tc|telecine|scr|screener|r5"
    r"|fullhd|uhd|4k|2160p|1080p|720p|480p|hdr|sdr"
    r"|bd|dvd|proper|repack|retail|extended|theatrical|unrated"
    r"|amzn|nf|dsnp|hmax|atvp|md|x264|x265|hevc|avc|web"
    r"|s\d{1,2}(?:e\d{1,3})?|sezon|odcinek|ep(?:isode)?"
)
WAREZ_TOKENS_RE = re.compile(r'\b(?:' + _WAREZ_TOKENS_PAT + r')\b', re.IGNORECASE)

_RE_FN_EXT         = re.compile(r'\.\w{2,4}$')
_RE_FN_YEAR_PAR    = re.compile(r'^(.+?)\s*\((\d{4})\)')
_RE_FN_EP          = re.compile(
    r'[Ss](?P<sea>\d{1,2})[Ee](?P<ep>\d{1,2})'
    r'|(?P<sea2>\d{1,2})[xX](?P<ep2>\d{1,2})'
    r'|(?:cz\.|odc\.|ep\.)\s*(?P<ep3>\d{1,3})',
    re.IGNORECASE,
)
_RE_FN_YEAR_BARE   = re.compile(
    r'^(.+?)\s+(\d{4})\s+(?:V\d+\s+)?(?:' + _WAREZ_TOKENS_PAT + r')',
    re.IGNORECASE,
)
_RE_FN_TITLE_TRAIL = re.compile(r'[\s.]+(?:4[Kk]|UHD)\s*$', re.IGNORECASE)
_RE_FN_ARTICLE     = re.compile(r'^(?:the|a|an)\s+', re.IGNORECASE)
_RE_FN_ROMAN       = re.compile(r'\b(viii|vii|vi|iv|ix|iii|ii|x|v|i)\b', re.IGNORECASE)
_RE_EP_RANGE       = re.compile(r"(?:s([\dO]{2})-?)?e(\d{2,4})-e?(\d{2,4})(?!\w)", re.I)
_RE_EP_UNIV2       = re.compile(
    r"(S\d{2})?[.,-]?(E(\d{2,4}))".replace(r"\d", r"[\dO]"),
    re.I,
)
# Year pattern for warez filenames: handles OCR O/0 substitution (e.g. 2O2O → 2020).
# Use this when matching years in filenames from warez sources (xt7, tb7, etc.).
RE_YEAR_FILENAME = re.compile(r'\b(19[\dOo]{2}|2[Oo0][\dOo]{2})\b')

# Full episode-marker pattern: S01E05, .E05, cz., odc., ep., episode, odcinek, (05), - 05 [-, 1x05
# Used to detect TV episode filenames (e.g. to reject them in movie searches).
RE_EPISODE_FILENAME = re.compile(
    r'((S[\dO]{1,2})?[.,-]?E[\dO]{2,4}|\bcz\.|\bodc\.|\bep\.|episode|odcinek'
    r'|[\(\[]\d{2,3}[\)\]]|\- \d{2,3} [([-]|\b\dx\d{2}\b)',
    re.I,
)


def is_episode_filename(filename: str) -> bool:
    """Return True if *filename* contains a TV-episode marker (S01E01, odc., etc.).

    Use this to reject episode files when the search target is a movie.
    """
    return bool(RE_EPISODE_FILENAME.search(filename))


def parse_filename_title(filename: str) -> ParsedFilename:
    """Parse a warez filename into {title, year, season, episode}."""
    normalized = _RE_FN_EXT.sub('', filename).replace('.', ' ').replace('_', ' ')
    year_match = _RE_FN_YEAR_PAR.match(normalized)
    ep_match   = _RE_FN_EP.search(normalized)
    if year_match:
        title = year_match.group(1).strip()
        year  = int(year_match.group(2))
    elif ep_match:
        title = normalized[:ep_match.start()].strip()
        year  = None
    else:
        bare  = _RE_FN_YEAR_BARE.match(normalized)
        title = bare.group(1).strip() if bare else normalized
        year  = int(bare.group(2)) if bare else None
    title = _RE_FN_TITLE_TRAIL.sub('', title).strip()
    if ep_match:
        sea         = ep_match.group('sea')  or ep_match.group('sea2')
        ep          = ep_match.group('ep')   or ep_match.group('ep2') or ep_match.group('ep3')
        season_val  = int(sea) if sea else 1
        episode_val = int(ep)  if ep  else None
    else:
        season_val = episode_val = None
    return {'title': title, 'year': year, 'season': season_val, 'episode': episode_val}


def build_title_pattern(title: str) -> str:
    """Build a flexible filename-matching regex pattern from a title string.

    Handles diacritics, Roman numerals, numbers-as-words, fractions, and
    non-alphanumeric separators.  Ported from tb7's prepare_pattern_for_titles_v2.
    """
    title   = title.lower()
    pattern = re.sub(r'([^\w&]+)', r"[\1 .–-]+", title)
    pattern = re.sub(r'(\[[^ \w]+ \.–\-\])\+', r'\1*', pattern)
    pattern = pattern.replace("[^", r"[\^").replace("[]", r"[\]").replace("[[", r"[\[")
    pattern = re.sub(
        r"(?<=\[)\W+?(?= \.–-])",
        lambda x: "".join(dict.fromkeys(re.sub("[ .–-]", "", x[0]))),
        pattern,
    )
    pattern = pattern.replace(r"&", r"(\&|and|i)")
    sp      = "[ .–-]+"
    pattern = re.sub(
        rf'(\d{re.escape(sp)})((\d){re.escape(sp)}(\d({re.escape(sp)})?))' ,
        rf'\1(i{sp})?\3[ .,/-]\4',
        pattern,
    )
    pattern = re.sub(rf'(\d)({re.escape(sp)})([a-zA-Z])', r'\1\2*\3', pattern).replace("+*", "*")
    pattern = numbers_in_pattern(pattern)
    pattern = normalize_unicode_in_pattern(pattern)
    return pattern


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 2:
        return abs(la - lb)
    if la == 0:
        return lb
    if lb == 0:
        return la
    if la < lb:
        a, b, la, lb = b, a, lb, la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost   = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[lb]


def match_filename_to_titles(filename: str, titles: Iterable[str], season: str | int | None = None, episode: str | int | None = None) -> tuple[bool, str]:
    """Check if *filename* matches any of the given *titles*.

    Step 1: sliding-window word match (rapideo-style).
    Step 2: Levenshtein fallback on the parsed filename title.

    Returns ``(matched: bool, reason: str)``.
    """
    normalized = re.sub(r'[^a-zA-Z0-9]+', ' ', cleantitle.normalize(filename)).lower()
    words      = normalized.split()
    seen_lower = {t.lower() for t in titles}

    # Multi-word sliding window
    for t in (t for t in titles if ' ' in t):
        twords = t.lower().split()
        for i in range(len(words) - len(twords) + 1):
            if words[i:i + len(twords)] == twords:
                if season and episode:
                    conflict = detect_country_variant_conflict(words, titles)
                    if conflict:
                        return False, f"country variant conflict ({conflict})"
                return True, f"multi-word title match ({t})"

    # Single-word check
    for t in (t for t in titles if ' ' not in t):
        tl = t.lower()
        if season and episode:
            for idx, word in enumerate(words):
                if not word.startswith(tl):
                    continue
                rest = word[len(tl):]
                if rest and rest[0].isalpha():
                    continue
                if idx + 1 < len(words):
                    nw = words[idx + 1]
                    if (
                        not re.search(r'^\d{4}$', nw)
                        and not WAREZ_TOKENS_RE.search(nw)
                        and nw not in seen_lower
                    ):
                        continue
                return True, f"single-word title match ({t})"
        else:
            if words and words[0] == tl:
                if len(words) == 1:
                    return True, f"single-word title match ({t})"
                for word in words[1:]:
                    if re.search(r'^\d{4}$', word) or WAREZ_TOKENS_RE.search(word) or word in seen_lower:
                        return True, f"single-word title match ({t})"
                    break

    # Levenshtein fallback on parsed filename title
    parsed_title = parse_filename_title(filename).get('title', '')
    if parsed_title:
        candidate = re.sub(r'[^a-zA-Z0-9]+', ' ', cleantitle.normalize(parsed_title)).lower().strip()
        variants  = {candidate}
        stripped  = _RE_FN_ARTICLE.sub('', candidate)
        if stripped != candidate:
            variants.add(stripped)
        all_variants = set()
        for v in variants:
            all_variants.add(v)
            roman_conv = _RE_FN_ROMAN.sub(lambda m: str(roman_to_int(m.group(0))), v)
            if roman_conv != v:
                all_variants.add(roman_conv)
        alias_cleans = {cleantitle.get_simple(t) for t in titles}
        for cv in all_variants:
            clean = cleantitle.get_simple(cv)
            if clean and (clean in alias_cleans or any(_levenshtein(clean, a) <= 2 for a in alias_cleans if a)):
                return True, "fuzzy title match"

    return False, ""


# ---------------------------------------------------------------------------
# Shared parsed-title matching  (used by rapideo / tb7 / wrzucaj)
# ---------------------------------------------------------------------------

_RE_FN_GROUP_TAG = re.compile(r'^\s*\[[^\]]+\]\s*')


def build_alias_cleans(titles: Iterable[str]) -> set[str]:
    """Build a set of get_simple-cleaned strings for use with match_parsed_title.

    Handles 'Show: Subtitle' splitting, reversed subtitle ordering, and
    filters out parts shorter than 3 characters after cleaning.
    """
    alias_cleans: set[str] = set()
    for t in filter(None, titles):
        c = cleantitle.get_simple(t)
        if c:
            alias_cleans.add(c)
        for sep in (' - ', ': '):
            for part in t.split(sep):
                part = part.strip()
                cp = cleantitle.get_simple(part)
                if cp and len(cp) >= 3:
                    alias_cleans.add(cp)
        if ': ' in t:
            head, _, tail = t.partition(': ')
            cr = cleantitle.get_simple(tail + ' ' + head)
            if cr:
                alias_cleans.add(cr)
    return alias_cleans


def match_parsed_title(title: str, alias_cleans: set[str]) -> bool:
    """Check if an already-parsed title string matches any entry in alias_cleans.

    Strips leading [GROUP] tag, splits on ' - ' separators, handles English
    articles and Roman numerals, then compares via get_simple + Levenshtein(≤2).
    """
    title = _RE_FN_GROUP_TAG.sub('', title).strip()
    parts = [p.strip() for p in title.replace(': ', ' - ').split(' - ') if p.strip()]
    if not parts:
        return False
    for part in parts:
        variants: set[str] = {part}
        stripped = _RE_FN_ARTICLE.sub('', part)
        if stripped != part:
            variants.add(stripped)
        all_variants: set[str] = set()
        for v in variants:
            all_variants.add(v)
            roman_conv = _RE_FN_ROMAN.sub(lambda m: str(roman_to_int(m.group(0))), v)
            if roman_conv != v:
                all_variants.add(roman_conv)
        for cv in all_variants:
            clean = cleantitle.get_simple(cv)
            if not clean:
                continue
            if clean in alias_cleans:
                return True
            if len(clean) >= 3 and any(_levenshtein(clean, a) <= 2 for a in alias_cleans if a):
                return True

    # Fallback: handle filenames where PL + EN titles are concatenated without separator
    # e.g. "Maszyna.do.zabijania.War.Machine.2026" → parsed "Maszyna do zabijania War Machine"
    # → clean "maszynadozabijaniawarmachine" = concat of two alias_cleans.
    # Also handles typos in one part (Levenshtein ≤ 2 on the remaining segment).
    def _ac_matches(segment: str) -> bool:
        return segment in alias_cleans or (
            len(segment) >= 4
            and any(_levenshtein(segment, a) <= 2 for a in alias_cleans if a)
        )

    full_clean = cleantitle.get_simple(' '.join(parts))
    for ac in alias_cleans:
        if not ac or len(ac) < 4:
            continue
        if full_clean.startswith(ac):
            remainder = full_clean[len(ac):]
            if remainder and _ac_matches(remainder):
                return True
        if full_clean.endswith(ac):
            prefix = full_clean[:-len(ac)]
            if prefix and _ac_matches(prefix):
                return True

    return False


def match_filename_title_parsed(filename: str, alias_cleans: set[str]) -> bool:
    """Convenience wrapper: parse filename title then call match_parsed_title."""
    return match_parsed_title(parse_filename_title(filename)['title'], alias_cleans)


def check_year_in_filename(filename: str, year: int | str | None, premiered: str | None, year_in_title: str = '', current_year: int = 0) -> bool | None:
    """Check whether the year embedded in *filename* matches *year* / *premiered*.

    Returns ``True`` (match), ``False`` (mismatch), or ``None`` (no year found).
    """
    if not premiered and not year:
        return None
    if year_in_title:
        filename = filename.replace(year_in_title, "")
    m = re.search(r"\b\d{4}\b", filename)
    if m:
        found = m[0]
        if not current_year:
            import time
            current_year = int(time.strftime('%Y'))
        if 1900 <= int(found) <= int(current_year) + 1:
            if (
                (premiered and (premiered.startswith(found) or premiered.endswith(found)))
                or (year and str(year) == found)
            ):
                return True
            return False
    return None


def filename_passes_episode(filename, season, episode, absolute_episode=None):
    """Return ``True`` if *filename* contains the expected season/episode marker.

    Handles S01E05 notation, O/0 letter substitution, episode ranges, and
    anime absolute episode numbers.  Always returns ``True`` when *season* or
    *episode* are falsy.
    """
    if not season or not episode:
        return True

    season_int  = int(season)
    episode_int = int(episode)
    season_num  = f"{season_int:02d}"

    # Main sXXeYY pattern
    ep_pat = rf"s0?{season_int}[.,-]?e(\d{{2,4}}-?)?e?0?{episode_int}(?!\d)"
    if season_num == "01":
        ep_pat = (
            rf"({ep_pat}"
            rf"|(?<![se]\d{{1}}[.,-])(?<![se]\d{{1}})(?<![se]\d{{2}}[.,-])(?<![se]\d{{2}})"
            rf"(?<!e\d{{3}}[.,-])(?<!e\d{{3}})(?<!e\d{{4}}[.,-])(?<!e\d{{4}})"
            rf"e(\d{{2,4}}-?)?e?0?{episode_int}(?!\d))"
        )
    ep_pat = ep_pat.replace(r"\d", r"[\dO]").replace("0", "[0O]")
    ep_re  = re.compile(ep_pat, re.I)

    # Season-1 alternative patterns (cz./odc./ep. markers)
    if season_num == "01":
        ep2_pat = (
            rf"(?<!\d[2-9])[ .](cz\.|odc\.|ep\.|episode|odcinek)[ .-]{{,3}}0{{,2}}{episode_int}\b"
            rf"|[([]0{{,2}}{episode_int}[)\]](?!\.[a-z]{{2,3}}$)"
            rf"|\- 0{{,2}}{episode_int} [([-]"
            rf"|\b0?{season_int}x0{{,2}}{episode_int}\b"
            rf"|\b0{{,2}}{episode_int}\.[a-z]{{2,3}}$"
            rf"|[a-z][ .]0{{,2}}{episode_int}[ .][a-z]"
        )
        ep2_pat = ep2_pat.replace("0", "[0O]")
        ep2_re = re.compile(ep2_pat, re.I)
    else:
        ep2_re = re.compile("impossibletomatch")

    abs_ep_re = None
    if absolute_episode:
        abs_ep_re = re.compile(rf"\be0*{int(absolute_episode)}(?!\d)", re.I)

    def _ep_in_range(fn):
        rang = _RE_EP_RANGE.search(fn)
        if rang:
            s_grp = rang.group(1)
            if (not s_grp and season_int == 1) or (s_grp and int(s_grp.replace('O', '0')) == season_int):
                return episode_int in range(int(rang.group(2)), int(rang.group(3)) + 1)
        return False

    return bool(
        ep_re.search(filename)
        or _ep_in_range(filename)
        or (not _RE_EP_UNIV2.search(filename) and ep2_re.search(filename))
        or (abs_ep_re and abs_ep_re.search(filename))
    )


# =============================================================================
# Shared types for PL scrapers
# =============================================================================

class ShowDataDict(TypedDict):
    """Typed-dict form of ShowData — tvshow() return value / episode() url parameter."""
    title: str
    local_title: str
    aliases: list[AliasItem]
    year: int
    tmdb: str | None


@frozen
class ShowData:
    """Carries show identity between tvshow() and episode() calls.

    Serialised to dict by tvshow() via show_data_asdict(), deserialised in episode() via ShowData(**url).
    """
    title: str
    local_title: str
    aliases: list[AliasItem]
    year: int
    tmdb: str | None = None


def show_data_asdict(sd: ShowData) -> ShowDataDict:
    """Return ShowData as a plain dict (wraps attrs asdict)."""
    return _attrs_asdict(sd)  # type: ignore[return-value]


def confirm_transfer_dialog(filename: str, charge: str = "", remaining: str = "") -> bool:
    """Show a 'confirm download / transfer cost' yesno dialog.

    Closes any open notification popup first, then shows:
      [I]filename[/I]
      Od transferu zostanie odliczone: <charge>   (if provided)
      Aktualnie posiadasz: <remaining>             (if provided)

    Returns True when the user confirms ("Pobierz"), False on cancel.
    """
    import xbmcgui
    from lib.ff import control as _control
    body = f"[I]{filename}[/I]"
    if charge:
        body += f"\nOd transferu zostanie odliczone: [B]{charge.replace(' ', chr(0xa0))}[/B]"
    if remaining:
        body += f"\nAktualnie posiadasz: [B]{remaining.replace(' ', chr(0xa0))}[/B]"
    if _control.condVisibility('Window.IsActive(notification)'):
        _control.execute('Dialog.Close(notification,true)')
    return xbmcgui.Dialog().yesno(
        "Czy chcesz pobrać ten plik?",
        body,
        yeslabel="Pobierz",
        nolabel="Anuluj",
    )


def add_colon_reversed_titles(titles: List[str]) -> List[str]:
    """Append colon-reversed variants to *titles* and return the extended list.

    For any title containing ': ' but no '-', adds the reversed form:
      "Spirited Away: In the Land of Gods"  ->  "In the Land of Gods - Spirited Away"

    Used by the three PL premium scrapers when building the title filter list.
    """
    tmp = []
    for t in titles:
        if ': ' in t and '-' not in t:
            rev = ' - '.join(t.split(': ')[::-1])
            if rev not in titles and rev not in tmp:
                tmp.append(rev)
    return titles + tmp


def detect_torrent_language(filename: str, title: str) -> tuple[str, str]:
    """Detect language and audio type specifically for P2P torrent filenames and Stremio titles."""
    title_lower = title.lower()
    
    # 1. Check for explicit flag emojis in Stremio's title / description
    has_pl_flag = '🇵🇱' in title
    
    # Other flags indicating explicitly non-PL language
    other_flags = [
        '🇮🇹', '🇷🇺', '🇪🇸', '🇫🇷', '🇩🇪', '🇵🇹', '🇧🇷', '🇯🇵', '🇰🇷', '🇨🇳', 
        '🇺🇦', '🇹🇷', '🇳🇱', '🇸🇪', '🇫🇮', '🇳🇴', '🇩🇰', '🇬🇷', '🇨🇿', '🇭🇺', 
        '🇷🇴', '🇧🇬', '🇸🇰', '🇭🇷', '🇱🇹', '🇱🇻', '🇪🇪', '🇮🇪', '🇮🇸', '🇮🇳', '🇸a'
    ]
    has_other_flag = any(f in title for f in other_flags)
    
    if has_pl_flag:
        # Polish flag found -> It's Polish! Parse the audio type
        audio_type = _parse_torrent_audio_type(filename, title)
        return 'pl', audio_type or 'Dubbing'
        
    if has_other_flag and not has_pl_flag:
        # Non-Polish flag found without a Polish flag -> definitely not Polish
        return 'en', ''

    # 2. Check for explicit language/dubbing text in description lines
    # (e.g. "Dubbing: Russian", "Audio: Italian", "Languages: Spanish", "Subtitles: German")
    lines = [line.strip() for line in title_lower.split('\n')]
    has_explicit_polish = False
    has_explicit_other = False
    
    for line in lines:
        if any(keyword in line for keyword in ['dubbing', 'dubbed', 'audio', 'language', 'napisy', 'sub']):
            # If the line contains "polish" or similar PL tags
            if any(pl_tag in line for pl_tag in ['polish', ' pol ', ' pl ', 'polski', 'plk']):
                has_explicit_polish = True
            
            # Check if this line explicitly declares a non-Polish language
            for lang in [
                'russian', 'italian', 'spanish', 'french', 'german', 'portuguese', 
                'japanese', 'korean', 'chinese', 'ukrainian', 'turkish', 'dutch', 
                'swedish', 'finnish', 'norwegian', 'danish', 'greek', 'czech', 
                'slovak', 'croatian', 'bulgarian', 'romanian'
            ]:
                if lang in line:
                    has_explicit_other = True
                    
    if has_explicit_polish:
        audio_type = _parse_torrent_audio_type(filename, title)
        return 'pl', audio_type or 'Dubbing'
        
    if has_explicit_other and not has_explicit_polish:
        return 'en', ''

    # 3. Fallback to parsing filename
    filename_lower = filename.lower()
    text = re.sub(r"[\][._-]", " ", filename_lower)
    text = f" {text} "
    
    # Check for explicit Polish audio markers (Lektor, Dubbing, pl dub)
    # Ensure they are not prefixed/suffixed to represent another language
    is_pl_audio = False
    audio_type = ''
    
    if 'lektor' in text:
        is_pl_audio = True
        audio_type = 'Lektor'
    elif any(x in text for x in ['pldub', 'pl dub', 'pl-dub']) or ('dub' in text and not any(x in text for x in ['rus', 'ita', 'ger', 'fre', 'spa', 'fra', 'esp'])):
        is_pl_audio = True
        audio_type = 'Dubbing'
        
    # Check for Polish subtitle markers
    is_pl_subs = False
    if any(x in text for x in ['plsub', 'napisy pl', 'napisy', 'nap pl', 'sub pl', 'pl sub', 'pl-sub']):
        is_pl_subs = True
        audio_type = 'Napisy'
        
    # Check for general Polish country/language code tag
    is_pl_tag = any(f" {x} " in text for x in ['pl', 'pol', 'polish', 'plk'])
    
    if is_pl_audio or is_pl_subs or is_pl_tag:
        return 'pl', audio_type
        
    # Polish diacritics check
    if re.search(r"[ąęółśżźćń]", filename_lower):
        return 'pl', ''

    return 'en', ''


def _parse_torrent_audio_type(filename: str, title: str) -> str:
    filename_lower = filename.lower()
    text = re.sub(r"[\][._-]", " ", filename_lower)
    text = f" {text} "
    
    if 'lektor' in text:
        return 'Lektor'
    if any(x in text for x in ['dubbing', 'pldub', 'pl dub', ' dub ']):
        return 'Dubbing'
    if any(x in text for x in ['plsub', 'napisy pl', 'napisy', 'nap pl', 'sub pl', 'pl sub']):
        return 'Napisy'
        
    title_lower = title.lower()
    if 'lektor' in title_lower:
        return 'Lektor'
    if 'dubbing' in title_lower or 'dubbed' in title_lower:
        return 'Dubbing'
    if 'napisy' in title_lower or 'subtitles' in title_lower or 'plsub' in title_lower:
        return 'Napisy'
        
    return ''
