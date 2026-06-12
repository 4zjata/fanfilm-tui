"""
    FanFilm Add-on
    UpNext integration module

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
from typing import TypedDict, NotRequired, TYPE_CHECKING
import xbmc
from ..defs import MediaRef
from .log_utils import fflog
from .settings import settings
from .info import ffinfo
from .control import plugin_id
from .kotools import KodiRpc
from .threads import Timer
if TYPE_CHECKING:
    from .item import FFItem, ArtValues

UPNEXT_ADDON_ID = 'service.upnext'


class UpNextVideo(TypedDict):
    """Data structure representing the episode information sent to UpNext."""
    title: str
    plot: str
    rating: float
    playcount: int
    season: int
    episode: int
    showtitle: str
    runtime: int
    firstaired: str
    art: ArtValues
    episodeid: NotRequired[int]  # could be Kodi DB ID (used in the library)
    tvshowid: NotRequired[int]   # could be Kodi DB ID (used in the library)


class UpNextEvent(TypedDict):
    """Data structure for the UpNext event sent via JSONRPC.NotifyAll."""
    current_episode: UpNextVideo
    next_episode: UpNextVideo
    next_video: UpNextVideo
    play_url: str


def is_available() -> bool:
    """Checks if the UpNext service add-on is available in the system."""
    try:
        return xbmc.getCondVisibility(f'System.AddonIsEnabled({UPNEXT_ADDON_ID})')
    except Exception:
        return False


def is_enabled() -> bool:
    """Checks if UpNext integration is enabled in settings and available in the system."""
    return settings.getBool('upnext.enabled') and is_available()


def _extract_episode_dict(ffitem: FFItem, ffid: int = 0) -> UpNextVideo:
    """Extracts the relevant episode information from an FFItem into a dict suitable for UpNext."""
    vtag = ffitem.vtag
    src = ffitem.getArt()
    art: ArtValues = {
        key: value
        for key in ('thumb', 'fanart', 'poster', 'landscape', 'clearart', 'clearlogo', 'tvshow.fanart')
        if (value := src.get(key))
    }
    if landscape := art.get('landscape'):
        art.setdefault('thumb', landscape)
    for key in ('fanart', 'landscape', 'poster', 'clearart', 'clearlogo'):
        if image := art.get(key):
            art.setdefault(f'tvshow.{key}', image)

    return {
        # 'episodeid': episode,
        # 'tvshowid': ffid,
        'title': ffitem.title,
        'plot': vtag.getPlot(),
        'rating': vtag.getRating(),
        'playcount': vtag.getPlayCount(),
        'season': ffitem.season or 0,
        'episode': ffitem.episode or 0,
        'showtitle': vtag.getTVShowTitle(),
        'runtime': ffitem.duration or 0,
        'firstaired': vtag.getFirstAiredAsW3C(),
        'art': art,
    }

# For testing - unnused 
# def _make_minimal_episode_dict(season: int, episode: int, showtitle: str, ffid: int = 0) -> UpNextVideo:
#    """Creates a minimal UpNextVideo dict when no full episode info is available."""
#    return {
#        # 'episodeid': episode,
#        # 'tvshowid': ffid,
#        'title': '',
#        'plot': '',
#        'rating': 0.0,
#        'playcount': 0,
#        'season': season,
#        'episode': episode,
#        'showtitle': showtitle,
#        'runtime': 0,
#        'firstaired': '',
#        'art': {},
#    }


def _send_signal(data: UpNextEvent) -> None:
    """Sends the UpNext data via JSONRPC.NotifyAll using the AddonSignals envelope."""
    try:
        KodiRpc().addon_signal('upnext_data', data)
        sz, ep = data['next_episode']['season'], data['next_episode']['episode']
        fflog(f'[UPNEXT] Sent S{sz:>02}E{ep:>02} via JSONRPC.NotifyAll')
    except Exception as e:
        fflog(f'[UPNEXT] Error: {e}')


def _get_play_url(ref: MediaRef) -> str:
    """Constructs a plugin URL for the given media reference."""
    # -- correct but imports heavy modules --
    # from .routing import url_for
    # from ..indexers.navigator import play
    # return str(url_for(play, ref=ref) or '')

    # -- lightweight but less correct (doesn't follow route changes, but should be fine for this use case) --
    return f'plugin://{plugin_id}/play/{ref}'


def signal_upnext(current_item: FFItem, ref: MediaRef) -> None:
    """Main function to signal UpNext with the current episode and the next episode information."""

    if not is_enabled():
        return

    assert ref is not None
    assert ref.ffid

    if ref.season is None or ref.episode is None:
        fflog.warning('[UPNEXT] Not an episode ref, skipping')
        return

    showtitle = current_item.vtag.getTVShowTitle()
    try:
        next_season = ref.season
        next_episode = ref.episode + 1
        # TODO: check if ffinfo next-unwatched-episode will be better here

        next_ref = MediaRef('show', ref.ffid, next_season, next_episode)
        next_item = ffinfo.find_item(next_ref, progress=ffinfo.Progress.NO)

        if next_item is not None:
            next_episode_data = _extract_episode_dict(next_item, ffid=ref.ffid)
        else:
            next_ref = MediaRef('show', ref.ffid, ref.season + 1, 1)
            next_item = ffinfo.find_item(next_ref, progress=ffinfo.Progress.NO)
            if next_item is not None:
                next_episode_data = _extract_episode_dict(next_item, ffid=ref.ffid)
                next_season = ref.season + 1
                next_episode = 1
            else:
                # No next episode exists (last episode of the series) — do not signal UpNext.
                fflog('[UPNEXT] No next episode found via TMDB, skipping UpNext signal')
                # next_episode_data = _make_minimal_episode_dict(next_season, next_episode, showtitle, ffid=ref.ffid)
                return

        upnext_event: UpNextEvent = {
            'current_episode': _extract_episode_dict(current_item, ffid=ref.ffid),
            'next_episode': next_episode_data,
            'next_video': next_episode_data,
            'play_url': _get_play_url(next_ref),
        }
        # Defer the signal so UpNext's initial Player.OnAVStart self-check finishes
        # first — for plugin:// items not in Kodi's library it fails ("Skip video
        # check") and would reset the tracking our signal just installed.
        Timer(1.5, _send_signal, args=[upnext_event]).start()

    except Exception as e:
        fflog(f'[UPNEXT] Error: {e}')
