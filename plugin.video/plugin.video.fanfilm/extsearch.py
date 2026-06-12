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
# MUST be before any kodi or FF import
from lib import autoinstall  # noqa: F401, pylint: disable=W0611  # type: ignore
import xbmc
from urllib.parse import urlencode
import re
from lib.ff import control

title = xbmc.getInfoLabel('Listitem.Title') or xbmc.getInfoLabel('Listitem.Label') or xbmc.getInfoLabel('VideoPlayer.Title')
episode_info = xbmc.getInfoLabel('Listitem.Episode') or xbmc.getInfoLabel('VideoPlayer.Episode')
if episode_info:
    titletv = re.sub(r'\s*\d+$', '', title)
    params = {'query': titletv}
    url = f"{control.plugin_url}tvshow/search/ext?{urlencode(params)}"
else:
    year = xbmc.getInfoLabel('Listitem.Year') or xbmc.getInfoLabel('VideoPlayer.Year')
    params = {'query': title}
    if year and year.isdigit():
        params['year'] = int(year)
    url = f"{control.plugin_url}movie/search/ext?{urlencode(params)}"
control.execute(f'ActivateWindow(Videos,"{url}", return)')