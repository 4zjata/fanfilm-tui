"""
FanFilm const (low-level developer) settings.
Do NOT change if you don't know what you're doing.

Avoid imports here, please.
Supports `local.py` from plugin userdata folder. File is created if missing.

You can override settings in `local.py`.
Just in the same form as in section "CONST SETTINGS",
>>> const.name = value

To define new const settings check also cdefs.py.

Use const settings
------------------

And use in source code.
>>> from const import const
>>> if const.a.b.c == value: ...
"""

from cdefs import const, const_done, CONST_REF, SETTING
from cdefs import (
    PlayCancel, StateMode, MediaWatchedMode, StrmFilename, OwnListDef, AddToMenu, WhenShowListName, ListType, DirItemSource, SourceSearchProgressStyle,
    SourcePattern, SourceAttribute,
    RegEx, Expr, Glob, R, X,
    MINUTE, HOUR, DAY, DO_NOT_CACHE, MiB, NetCache,
)
from lib.kolang import L  # semi-safe import

# ----------------------------------------------------------------------------- #
# -----                          SOME DOCUMENTATION                       ----- #
# ----------------------------------------------------------------------------- #
#
# `votes` – minmum number of votes
#  -  >= 0   - the number
#  -  == -1  - skip votes at all
#  -  None   - use default from user settings


# ----------------------------------------------------------------------------- #
# -----                          CONST SETTINGS                           ----- #
# ----------------------------------------------------------------------------- #

# --- Developing and debugging ---

# Use more logs. Useful with `grep -f LOG` on the Linux.
const.debug.enabled = True
# Use terminal seq (colors) in logs. Useful with `grep -f LOG` on the Linux.
const.debug.tty = False
# Add extra context menu item: CRASH, allows restart Python interpreter.
const.debug.crash_menu = False
# Extra developer menu.
const.debug.dev_menu = False
# Auto-reload modules on changes.
const.debug.autoreload = False
# Detailed service notification log.
const.debug.service_notifications = False
# Log xsleep jitter (shift in seconds). Zero means no log.
const.debug.log_xsleep_jitter = 0
# Log folder details.
const.debug.log_folders = False
# Show "logs" target in CM in "Add to" dialog.
const.debug.add_to_logs = False
# Detailed exception log (eg. in threads).
const.debug.log_exception = True
# Detailed GUI log.
const.debug.log_gui = False

#: Use remote debugger (debugpy, vscode, etc.). Function setup_debugger() in local.py must be declared.
const.debugger.enabled = False

#: Override IMDB API list page size, do not chenge it
const.dev.imdb.api_page_size = CONST_REF.indexer.page_size

# Override TMDB api_key for tests.
const.dev.tmdb.api_key = None
# Override TMDB session_id for tests.
const.dev.tmdb.session_id = None
# Override TMDB v4 read token for tests.
const.dev.tmdb.v4.bearer = None
# Override TMDB v4 access token for tests.
const.dev.tmdb.v4.access_token = None

# Override Trakt.tv client_id for tests.
const.dev.trakt.client = None
# Override Trakt.tv client_secret for tests.
const.dev.trakt.secret = None

# Override MDBList api_key for tests.
const.dev.mdblist.api_key = None

#: Echo all DB queries.
const.dev.db.echo = False
#: Do backup on DB update.
const.dev.db.backup = True
#: Fake source items prepend to found items in the source window.
const.dev.sources.prepend_fake_items = ()
#: Fake source items append to found items in the source window.
const.dev.sources.append_fake_items = ()
#: Detailed source provider exception log.
const.dev.sources.log_exception = True
#: Force all providers and skip source filtering (quality, size, language, codec). For debugging only.
const.dev.sources.force_all_sources = False

#: Force video database version (empty - autodetect).
const.dev.kodidb.ver = ''


# --- Global (independent) definitions ---

# Default language for the country.
const.global_defs.country_language = {
    'AD': 'ca', 'AE': 'ar', 'AL': 'sq', 'AM': 'hy', 'AO': 'pt', 'AR': 'es', 'AT': 'de', 'AZ': 'az', 'BB': 'en',
    'BD': 'bn', 'BE': 'de', 'BF': 'fr', 'BG': 'bg', 'BH': 'ar', 'BI': 'en', 'BJ': 'fr', 'BR': 'pt', 'BS': 'en',
    'BT': 'dz', 'BY': 'ru', 'BZ': 'en', 'CA': 'fr', 'CF': 'fr', 'CH': 'rm', 'CL': 'es', 'CM': 'en', 'CN': 'zh',
    'CO': 'es', 'CR': 'es', 'CU': 'es', 'CY': 'tr', 'DE': 'de', 'DJ': 'fr', 'DK': 'da', 'DM': 'en', 'DO': 'es',
    'EC': 'qu', 'EE': 'et', 'EG': 'ar', 'ER': 'ti', 'ES': 'es', 'ET': 'en', 'FI': 'sv', 'FJ': 'hi', 'FR': 'fr',
    'GA': 'fr', 'GB': 'en', 'GD': 'en', 'GE': 'ka', 'GH': 'en', 'GN': 'fr', 'GQ': 'es', 'GR': 'el', 'GT': 'es',
    'GY': 'en', 'HN': 'es', 'HR': 'hr', 'HU': 'hu', 'IE': 'ga', 'IL': 'he', 'IN': 'en', 'IQ': 'ar', 'IS': 'is',
    'IT': 'it', 'JM': 'en', 'JO': 'ar', 'JP': 'ja', 'KE': 'en', 'KG': 'ru', 'KH': 'km', 'KI': 'en', 'KM': 'fr',
    'KW': 'ar', 'KZ': 'ru', 'LB': 'ar', 'LI': 'de', 'LK': 'ta', 'LR': 'en', 'LS': 'en', 'LT': 'lt', 'LU': 'lb',
    'LV': 'lv', 'LY': 'ar', 'MC': 'fr', 'ME': 'sq', 'MG': 'mg', 'MH': 'en', 'MK': 'sq', 'ML': 'fr', 'MN': 'mn',
    'MR': 'ar', 'MT': 'en', 'MU': 'en', 'MX': 'es', 'MY': 'en', 'MZ': 'pt', 'NA': 'en', 'NE': 'fr', 'NG': 'en',
    'NI': 'es', 'NL': 'nl', 'NP': 'ne', 'OM': 'ar', 'PA': 'es', 'PE': 'qu', 'PK': 'ur', 'PL': 'pl', 'PT': 'pt',
    'QA': 'ar', 'RO': 'ro', 'RS': 'sr', 'RW': 'sw', 'SA': 'ar', 'SB': 'en', 'SD': 'en', 'SE': 'sv', 'SG': 'ta',
    'SK': 'sk', 'SL': 'en', 'SM': 'it', 'SN': 'fr', 'SO': 'ar', 'SR': 'nl', 'SS': 'en', 'SV': 'es', 'TD': 'fr',
    'TG': 'fr', 'TH': 'th', 'TN': 'fr', 'TO': 'to', 'TR': 'tr', 'TV': 'en', 'UA': 'uk', 'UG': 'sw', 'US': 'en',
    'UY': 'es', 'UZ': 'uz', 'VE': 'es', 'VU': 'fr', 'WS': 'en', 'YE': 'ar', 'ZA': 'zu', 'ZM': 'en'}


# --- Core ---

# Call garbage collector (GC) for every nth plugin calls.
const.core.gc_every_nth = 5
# Exit (close Python interpreter) for every nth plugin calls.
const.core.exit_every_nth = 25
# Exit (close Python interpreter) for every nth plugin calls in widgets.
const.core.widgets_exit_every_nth = 1
# Use service to keep volatile DBID (for seasons only at the moment).
const.core.volatile_ffid = True
# Use volatile FFID in all sesons. It allows to monitor kodi operations on seasons.
# const.core.volatile_ffid needs to be set to True.
const.core.volatile_seasons = True
# How to watch list-item changes, ex. set (un)watched.
const.core.media_watched_mode = MediaWatchedMode.WATCH_LISTITEM

# Scanning FF for bulk options (like watched a show) for media details.
const.core.bypass.info_details = 'DEFAULT, SHOW_SEASONS, SEASON_EPISODES, EPISODE_EN'

# Use advancedsettings.xml to determine kodi MyVideo DB.
const.core.kodidb.advanced_settings = True

# Save [ff]info media cache.
const.core.info.save_cache = False
# Copy year in FFItem.copy_from(), eg. episode from season and/or from tv-show.
const.core.info.copy_year = False

# Net-chache backend: 'filesystem', 'sqlite', 'redis'.
const.core.netcache.backend = 'filesystem'
# Net-chache serializer as tuple:
# - serializer type: 'pickle', 'json'
# - compressor:      'zlib', 'gzip', 'bz2', 'lzma' or empty string for no compression
const.core.netcache.serializer = ('pickle', 'gzip')
# Net-cache config.
#  - expires in seconds or settins expression.
#  - size limit in bytes (works only with `filesystem` cache backend).
const.core.netcache.cache = {
    # no cache
    '':         NetCache(DO_NOT_CACHE),
    # other or unknown data
    'other':    NetCache(MINUTE, size_limit=10 * MiB),
    # media data
    'media':    NetCache('{schedCleanMetaCache} * 3600', size_limit=100 * MiB),
    # media art (and similar stuff)
    'art':      NetCache(7 * DAY, size_limit=10 * MiB),
    # discover (best, popular etc.)
    'discover': NetCache('24 * HOUR if {listCache} else netcache.DO_NOT_CACHE', size_limit=10 * MiB),
    # lists (eg. trakt, tmdb, imdb, etc.) - should be short
    'lists':    NetCache('15 * MINUTE if {listCache} else netcache.DO_NOT_CACHE', size_limit=10 * MiB),
    # search - should be quite short I guess
    'search':   NetCache(15 * MINUTE, size_limit=10 * MiB),
}
# Use net-cache in widgets too.
const.core.netcache.widgets = True
# Net-cache cleanup (remove expired entries) interval in seconds.
const.core.netcache.cleanup.interval = 15 * MINUTE
# Net-cache cleanup after expire * factor, zero if not used.
const.core.netcache.cleanup.expire_factor = 2.0
# Net-cache cleanup after expire ( * factor if enabled) + offset.
const.core.netcache.cleanup.expire_offset = 0
# Net-cache redis host ('redis' backend only).
const.core.netcache.redis.host = 'localhost'
# Net-cache redis port ('redis' backend only).
const.core.netcache.redis.port = 6379
# Net-cache redis remove expired ('redis' backend only).
const.core.netcache.redis.ttl = True
# Net-cache redis remove expired time offset ('redis' backend only).
const.core.netcache.redis.ttl_offset = 3600


# --- Media (general) ---

# Service to get media details (tmdb, trakt).
const.media.aliases_service = 'trakt'
# Default media request info details, see ffinfo.get_items().
const.media.info_details = 'DEFAULT, SEASON_EPISODES, EPISODE_EN'

# Watching percent count as watched.
const.media.progress.as_watched = 85
# Media info for count progress.
const.media.progress.info_details = '+DEGRADED_EPISODES'
# Count show and season progress by full watched episodes (ignore partially watched episodes).
const.media.progress.show.episodes_watched = True

# ----- Sources -----

# Search progress dialog display mode (SourceSearchProgressStyle enum).
#  - SIMPLE    - Combined sources (no premium/normal distinction).
#  - EXTENDED  - Combined with premium count in parentheses.
#  - FULL      - Separate lines for premium and normal sources.
const.sources_dialog.searching.progress_style = SourceSearchProgressStyle.FULL

# Color of the list item index (1/99).
const.sources_dialog.index_color = 'B3FFFFFF'
# Show empty source window (no sources).
const.sources_dialog.show_empty = True
# Rescan button open "edit source search".
const.sources_dialog.rescan_edit = False
# Show limit time progress.
const.sources_dialog.time_progress = True
# Define priority to sort language type
const.sources_dialog.language_type_priority = {
    "lektor": 0,
    "dubbing": 1,
    "multi": 2,
    "napisy": 3,
    }
# Remove disabled hosts from sources list - default empty (eg. {'wrzucaj', 'wplik', 'gofile'} )
const.sources_dialog.disabled_hosts = {
    'booster',
    'abbys',  # Not supported by ResolveURL
    'upnshare',  # Not supported by ResolveURL
    'mega',  # Not supported by ResolveURL
    'buzz remux',  # RIP?
    'fd',  # RIP?
    'iframely',  # Not supported by ResolveURL
    'goo',  # RIP
    'bit',  # RIP
    'upvid',  # RIP
    'jetload',  # RIP
    'clip',  # RIP
    'flashx',  # RIP
    'streamsilk',  # RIP
}
# Show DRM sources from cda scrapper
const.sources_dialog.cda_drm = True
# Refresh search for librared items when using cache
# example 'rapideo', 'nopremium', 'twojlimit','tb7', 'xt7'
# tb7/xt7 - long time to scrape
const.sources_dialog.library_cache = {
}
# Sources dialog: movie title
const.sources_dialog.movie_title_format = '[B]{title} ({year})[/B]'
# Sources dialog: episode title
const.sources_dialog.episode_title_format = '[B]{show.title} ({show.year})[/B] – {season}x{episode:02}. [B]{title}[/B]'
# Sources dialog: episode title (with episode group set).
const.sources_dialog.episode_group_title_format = '[B]{show.title} ({show.year})[/B] – {season}x{episode:02}. [B]{title}[/B] [s{ref.season:02}e{ref.episode:02}]'
# Witch poster to show in episode searching dialog ('show' or 'season' or 'episode').
# Remember, the episode poster is often missing, then season is using. If season is missing, then show poster is using.
const.sources_dialog.episode_poster = 'episode'

# Add "edit source search" to video (movie, episode) context-menu.
const.sources_dialog.edit_search.in_menu = False
# Add "edit source search" to sources dialog.
const.sources_dialog.edit_search.in_dialog = True
# Add "edit source search" to sidebar.
const.sources_dialog.edit_search.in_filters = False
# Cache search given modifications. Names are SourceSearchQuery keys. Or None for disable.
#  - 'title'         - English title
#  - 'localtitle"    - Title in local (API) language
#  - 'originalname'  - Original title
#  - 'year'          - Year of release/airdate
#  - 'imdb'          - IMDB id
#  - 'tmdb':         - TMDB id
#  - 'season'        - Season number (only if const.sources_dialog.edit_search.show_granularity is 'season' or 'episode')
#  - 'episode'       - Episode number (only if const.sources_dialog.edit_search.show_granularity is 'episode')
#  - 'tvshowtitle'   - DEPRECATED, use 'title' instead
#  - 'premiered'      - Premiered date (only for episode)
const.sources_dialog.edit_search.cache = {'title', 'localtitle', 'originalname', 'year', 'imdb', 'tmdb', 'episode_group', 'episode_offset'}
# The show granularity (show, season, episode) to remember search modifications.
const.sources_dialog.edit_search.show_granularity = 'show'

# ----- Folders -----

#: Use cacheToDisk in xbmcplugin.endOfDirectory().
const.folder.cache_to_disc = False
#: How long wait for HTTP server sync on directory refresh.
const.folder.lock_wait_timeout = 2.0
#: Extra delay if refresh is detected to allow service close the "folder-ready" semaphore.
const.folder.refresh_delay = 0.7
#: Maximum time between each plugin read in kodi scan process: fast enter into seasons on show set (un)watched.
const.folder.max_scan_step_interval = 1.5
#: Save folder info into DB (old way). Use HTTP /folder instead.
const.folder.db_save = False
# If FF is called as script (handler == -1) and directory is building, try to refresh container.
const.folder.script_autorefresh = True
# When show previous page item (except first page even if 'always'):
#   - `never`        - never show
#   - `always`       - on every page (except first page)
#   - `on_last_page` - only on last page (except first page)
const.folder.previous_page = 'on_last_page'
# Maximum page number to jump to.
const.folder.max_page_jump = 500
# Item fanart fallback (None, 'landscape', ...). If None or when art is missing, plugin fanart is used.
const.folder.fanart_fallback = 'landscape'
# Auto category, how many parent labels to show in the folder label.
const.folder.category = 2
# Auto category, how many parent labels to show in the folder label for given skin.
const.folder.category_by_skin = {
    'skin.estuary': 1,
}
# Format for future item (unaired, non-premiered).
const.folder.separator_label = '———————'

# Format for future item (unaired, non-premiered).
const.folder.style.future = '[COLOR darkred][I]{}[/I][/COLOR]'
# Format for item with role.
const.folder.style.role = '{item.label} [COLOR gray][{item.role}][/COLOR]'
# Format for broken items (eg. not found in TMDB). Eg. '{} [COLOR red]![/COLOR]'
const.folder.style.broken = None
# Format for top position item.
const.folder.style.section_label = '––– [B]{}[/B] –––'
# Format for top position item.
const.folder.style.top = '[B]{}[/B]'
# Format for bottom position item.
const.folder.style.bottom = '[B]{}[/B]'
# Format for separator line.
const.folder.style.separator = None

# ----- Indexes -----

# Default region.
const.indexer.region = 'PL'
# Default view for list of lists (eg. mine).
const.indexer.lists_view = 'sets'
# Show or hide empty folder message (no content to display). Enter custom message or set True for default one.
const.indexer.empty_folder_message = True
# Default page size.
const.indexer.page_size = 20
# Default limit for "Add to…" for general lists.
const.indexer.add_to_limit = 100
# Number of movies/tv-shows from Trakt/TMDB trending used in "library", "add to…" etc.
const.indexer.trending_scan_limit = CONST_REF.indexer.add_to_limit
# Default view for media items in directory(alone=True). (eg. force tvshows for seasons or episodes).
# Key is a media type, valuse is a kodi content type (view).
const.indexer.default_alone_view = {
    # 'MIXED': 'videos',
    # 'season': 'tvshows',
    # 'episode': 'tvshows',
}
# Default view for media items in directory (alone=False) when item types are mixed (eg. movies and tv-shows in one folder).
const.indexer.default_mixed_view = 'videos'

# Fake thumb: alias to landscape (for episode) and poster (for other).
const.indexer.art.fake_thumb = True

# Style to update description (plot). Single '{…}' means progress formatting, double '{{}}' means description formatting.
const.indexer.progressbar.style = '[B]{progressbar} {percent}%[/B]\n{{}}'
# General progress bar source mode:
#   - 'none'                 - do not show progressbar at all
#   - 'watching'             - show video percent progress (PERCENT) if video progress >0% and < 100% else show nothing
#   - 'watched'              - show only watched videos (movies and episodes progresses are skipped)
#   - 'percent'              - show video percent progress
#   - 'percent_and_watched'  - show video percent progress and watched in background (use const.indexer.progressbar.watched.*)
const.indexer.progressbar.mode = 'watched'
# Progress bar width (number of characters).
const.indexer.progressbar.width = 40
# Fill element (watched) color (kodi color eg. darkred, FFCC9900).
const.indexer.progressbar.fill.color = 'darkgreen'
# Fill element (watched) character (eg. ' ', '|', 'l', 'ı', '•', '⸋').
const.indexer.progressbar.fill.char = 'l'
# Partial filled element (watched & unwatched) color (kodi color eg. darkred, FFCC5500).
const.indexer.progressbar.partial.color = 'darkgreen'
# Partial filled element (watched & unwatched) character (eg. ' ', '|', 'l', 'ı', '•', '⸋').
const.indexer.progressbar.partial.char = 'ı'
# Empty element (unwatched) color (kodi color eg. darkred, gray, FF999999 or empty).
const.indexer.progressbar.empty.color = 'gray'
# Empty element (unwatched) character (eg. ' ', '|', 'l', 'ı', '•', '⸋').
const.indexer.progressbar.empty.char = 'ı'
# Already watched element on watching again color (kodi color eg. darkred, gray, FF999999 or empty).
const.indexer.progressbar.watched.color = 'white'
# Already watched element on watching again character (eg. ' ', '|', 'l', 'ı', '•', '⸋').
const.indexer.progressbar.watched.char = CONST_REF.indexer.progressbar.empty.char

# No directory content: show no-content item.
const.indexer.no_content.show_item = True
# No directory content: show no-content notification.
const.indexer.no_content.notification = True

# Content view type for search folder.
const.indexer.search.view = 'tags'
# Ask user if sure on history clear.
const.indexer.search.clear_if_sure = True
# When to show numeric dialog to enter year:
#   - never         - never show numeric dialog
#   - context-menu  - add context menu to "new search"
#   - entry         - add separate entry "new search with year"
#   - always        - always show numeric dialog in "new search"
const.indexer.search.year_dialog = 'entry'
# General pattern for search movie or tv-show by year.
const.indexer.search.year_pattern = r'\b(?:y(?:ear)?:([12]\d{3}))|\(([12]\d{3})\)'
# Color for search query option (eg. "y:1987").
const.indexer.search.query_option_format = '[COLOR gray]{}[/COLOR]'
# Show multi search entry if True.
const.indexer.search.multi_search = True
# Limit of search results (0 means no limit).
const.indexer.search.limit = 0

# Show CM: "add to..." anything :-)
# Empty AddToMenu uses const.dialog.add_to.lists.services.
# AddToMenu with service name uses const.dialog.add_to.lists.services[service].
# AddToMenu with service and list adds item to the list without dialog.
const.indexer.context_menu.add_to = (
    # AddToMenu(name=L('Add to own favorites'), enabled=True, service='own', list=':favorites'),
    AddToMenu(name=L(30307, 'Add to...')),  # const.indexer.context_menu.add_to equivalent
    # AddToMenu(name=L('Add to library'), enabled='enable_library', service='library'),
    # AddToMenu(name=L('Add to trakt'), enabled='ListsInfo.trakt_enabled()', service='trakt'),  # TEST
    # AddToMenu(name='Add to logs (debug)', enabled='const.debug.add_to_logs', service='logs'),  # TEST
)

# -- Directories: main menu

# Show Trakt, TMDB, etc in separate folder. If False, show them in main menu.
const.indexer.navigator.lists_folder = False

# -- Directories: movies

# Default region for movies.
const.indexer.movies.region = CONST_REF.indexer.region
# Sort by `movies.sort` option index (popularity, year, ...) in movie discovery.
const.indexer.movies.discover_sort_by = (
    'popularity.desc',
    'primary_release_date.desc',
    'title.asc',
)
#: Time in seconds for movies / episodes without duration.
const.indexer.movies.missing_duration = 0
#: Means future (not premiered yet) movie if no date (None/null).
const.indexer.movies.future_if_no_date = True
#: Generate date from "year" (year-01-01) value if no movie date defined.
const.indexer.movies.date_from_year = False
#: Future (not premiered yet) movie can by played.
const.indexer.movies.future_playable = True
# Last watched date-time format in movie resume list. None if disabled.
const.indexer.movies.resume.watched_date_format = '%Y-%m-%d'
# Number of movies from TMDB discovery (or Trakt) used in "library", "add to…" etc.
const.indexer.movies.discovery_scan_limit = CONST_REF.indexer.add_to_limit
#: Override vote count for top-rated movies.
const.indexer.movies.top_rated.votes = 300
#: Override vote count for movies by genre.
const.indexer.movies.genre.votes = 50
#: Override vote count for movies by year.
const.indexer.movies.year.votes = 100
#: List of movie genres (main menu level).    NOT USED NOW !!!
# const.indexer.movies.genre.menu = {
#     12, 14, 16, 18, 27, 28, 35, 36, 37, 53, 80, 99, 878, 9648, 10402, 10749, 10751, 10752,
# }
#: This is just a joke, so treat it as an Easter egg. Show hundreds of thousands production_companies.
const.indexer.movies.joke.production_company = False
#: This is just a joke, so treat it as an Easter egg. Show hundreds of thousands keywords.
const.indexer.movies.joke.keyword = False
#: Trending source (tmdb, trakt):
const.indexer.movies.trending.service = 'trakt'
# Number of last days to see movies in cinema.
const.indexer.movies.cinema.last_days = 60
# Number of next days to see movies in cinema (ongoing).
const.indexer.movies.cinema.next_days = 4
# Filter cinema movies by selected region.
const.indexer.movies.cinema.use_region = False

# Default TV Movies page size with movies details (for list_mode: media. mixed).
const.indexer.movies.tv.media_page_size = 25
# Default TV Movies page size with movies stubs (for list_mode: folders. direct. direct_or_folder).
const.indexer.movies.tv.stub_page_size = 0
# Default Movies in TV service (filmweb, onet, teleman or sequence of them).
const.indexer.movies.tv.service = ('filmweb', 'teleman')
# Movies in TV (fanfilm) list mode :
#   - media             - all movies found (could be many for one tv item)
#   - mixed             - movie if single or folder if many
#   - folders           - folder always
#   - direct            - movie if item has media-ref else ignore TV item (works only with teleman)
#   - direct_or_folder  - movie if item has media-ref else folder
const.indexer.movies.tv.list_mode = 'direct_or_folder'
# Content view for TV services menu.
const.indexer.movies.tv.service_view = 'sets'
# Sort TV movies:
#   - ''           - no sort (default order from service)
#   - 'aired_date' - by aired date
#   - 'title'      - by title
const.indexer.movies.tv.sort_by = 'aired_date'
# Range of day offset to show movies in TV service. -1 means yesterday, +1 - tomorrow etc.
# Any range is safe, services have their own limits.
const.indexer.movies.tv.day_range = range(-1, 3)
# Enabled TV channels in all services, or None if all are enabled.
# Names must match exactly channel name. Exceptions are possible, ex. remove space before last number, add/remove HD etc.
# Order is important when multiple programs from different channels have the same time.
const.indexer.movies.tv.channels = (
    'TVP 1', 'TVP 2', 'Polsat', 'TVN', 'TVN Siedem', 'TV Puls', 'TV4', 'Puls 2', 'TV6', 'HBO', 'HBO 2', 'HBO 3', 'Cinemax', 'Cinemax 2',
    'CANAL+', 'CANAL+ Film', 'CANAL+ Seriale', 'CANAL+ Family', 'CANAL+ 1', 'CANAL+ Discovery', 'CANAL+ 4K Ultra HD',
    'AXN', 'AXN White', 'AXN Black', 'AXN Spin', 'FX', 'FX Comedy', 'Comedy Central', 'Polsat Comedy Central Extra', 'Kino Polska',
    'Warner TV', 'ale kino+', 'Sundance Channel', 'FilmBox Extra HD', 'FilmBox Action', 'FilmBox Arthouse', 'FilmBox Premium HD',
    'Film Cafe', 'Red Carpet TV', 'Sci Fi Universal',
)
# Hour of day (0-23) to start a day in show movies in TV service. E.g. 3 means that movies aired after 3:00 belong to the next day, and movies aired before 3:00 belong to the previous day.
# NOTE: No supported well yet.
const.indexer.movies.tv.day_first_hour = 3
# Show non-id movies in filmweb TV service (usually documentaries, reportages, etc).
const.indexer.movies.tv.filmweb.show_non_id = True
# Short program description to skip TV item in filmweb TV service.
const.indexer.movies.tv.filmweb.skip_items = {
    'film dokumentalny',
    'felieton',
}
# Number of channel pages to scan in onet TV service. Total pages are 9.
const.indexer.movies.tv.onet.channel_page_count = 6
# Number of worker threads to scan channels in onet TV service. Scraping all 9 pages hangs a little.
const.indexer.movies.tv.onet.channel_worker_count = 3
# Obtain more data pre each TV item in onet TV service. It allows to filter out more items, but it is much slower.
const.indexer.movies.tv.onet.more_details = False
# Number of pages to scan in teleman TV service. Should be enough to match whole day for performance reasons.
const.indexer.movies.tv.teleman.pages_to_scan = 8

# New movies votes.
const.indexer.movies.new.votes = 50

# New VoD movies: time window (days).
const.indexer.movies.new_vod.days = 3
# New VoD movies: list of services (shortName). See: const.justwatch.*
const.indexer.movies.new_vod.services = {
    'nfx', 'mxx', 'prv', 'dnp', 'cda', 'plp', 'pbg', 'sst', 'vtp', 'cpr',
}

# Movie style to update description (plot). Single '{…}' means progress formating, double '{{}}' means description formating.
const.indexer.movies.progressbar.style = CONST_REF.indexer.progressbar.style
# Movie progress bar source mode:
#   - 'none'                 - do not show progressbar at all
#   - 'watching'             - show video percent progress (PERCENT) if video progress >0% and < 100% else show nothing
#   - 'watched'              - show only watched videos (movies and episodes progresses are skiped)
#   - 'percent'              - show video percent progress
#   - 'percent_and_watched'  - show video percent progress and watched in background (use const.indexer.progressbar.watched.*)
const.indexer.movies.progressbar.mode = 'percent'
#: Movie progress bar width (number of characters).
const.indexer.movies.progressbar.width = CONST_REF.indexer.progressbar.width
# Enable "my lists" in movie directory.
const.indexer.movies.my_lists.enabled = True
# Show root level "my lists" flat (show sub-list on root level).
const.indexer.movies.my_lists.root.flat = True
# Pattern for search movie by year.
const.indexer.movies.search.year_pattern = CONST_REF.indexer.search.year_pattern

# -- Directories: tv-shows

# Default region for tv-shows.
const.indexer.tvshows.region = CONST_REF.indexer.region
# Sort by `tvshows.sort` option index (popularity, year, ...) in tv-show discovery.
const.indexer.tvshows.discover_sort_by = (
    'popularity.desc',
    'first_air_date.desc',
    'name.asc',
)
#: Means future (not aired yet) tvshow if no date (None/null).
const.indexer.tvshows.future_if_no_date = True
#: Override vote count for top-rated tv-shows.
const.indexer.tvshows.top_rated.votes = 200
#: Override vote count for tvshow by genre.
const.indexer.tvshows.genre.votes = 50
#: Override vote count for tvshow by year.
const.indexer.tvshows.year.votes = 100
#: List of tvshow genres (main menu level).    NOT USED NOW !!!
# const.indexer.tvshows.genre.menu = {
#     16, 18, 35, 37, 80, 99, 9648, 10751, 10759, 10762, 10764, 10765, 10766, 10767, 10768,
# }
#: This is just a joke, so treat it as an Easter egg. Show hundreds of thousands production_companies.
const.indexer.tvshows.joke.production_company = False
#: Trending source (tmdb, trakt):
const.indexer.tvshows.trending.service = 'trakt'
# Minimal number of votes in tv-show premier (new tv-shows).
const.indexer.tvshows.premiere.votes = 20

# New VoD tv-shows: time window (days).
const.indexer.tvshows.new_vod.days = 3
# New VoD tv-shows: list of services (shortName). See: const.justwatch.*
const.indexer.tvshows.new_vod.services = {
    'nfx', 'mxx', 'prv', 'dnp', 'cda', 'plp', 'pbg', 'sst', 'vtp', 'cpr',
}

# What to show in progress folder.
const.indexer.tvshows.progress.show = 'episode'
# What to show in progress folder.
#   - last      - episode are calculated using the last aired episode the user has watched.
#   - continued - episode are calculated using last watched episode (last activity).
#   - first     - episode are calculated using first unwatched episode.
#   - newest    - episode are calculated using the last aired episode at all.
const.indexer.tvshows.progress.next_policy = 'last'
# If True, show next episode as link to season folder
# if False, show next episode as playable video.
const.indexer.tvshows.progress.episode_folder = True
# Focus next episode on episodes list.
const.indexer.tvshows.progress.episode_focus = True
# Select next episode on episodes list (not all skins handle it).
const.indexer.tvshows.progress.episode_select = False
# Format next episode on episodes list ('{}' means title, '{item}' is FFItem).
const.indexer.tvshows.progress.episode_label_style = None
# Show 100% watched shows in progress folder.
const.indexer.tvshows.progress.show_full_watched = False

# Number of days to see tvshows in trakt calendar
# +3 - 3 days after today date, -10 - 10 days before todays date
# Range must be lower than 33 days
const.indexer.tvshows.calendar_range = (+3, -10)
# Number of tv-shows from TMDB discovery (or Trakt) used in "library", "add to…" etc.
const.indexer.tvshows.discovery_scan_limit = CONST_REF.indexer.add_to_limit
# Get all seasons with details (including episodes) on every tv-show load.
const.indexer.tvshows.season_details = 'DEFAULT, SEASON_EN'
# Show progress in direct /show folder limited to given number. If zero, folder is hidden.
const.indexer.tvshows.last_show_progress_folder = 0

# Enable "my lists" in tv-show directory.
const.indexer.tvshows.my_lists.enabled = True
# Show root level "my lists" flat (show sub-list on root level).
const.indexer.tvshows.my_lists.root.flat = True
# Pattern for search movie by year.
const.indexer.tvshows.search.year_pattern = CONST_REF.indexer.search.year_pattern

# -- Directories: seasons

#: Season label format if no season title, selected by user setting `tvshow.season_label`.
#: Season FFItem is available as `item`.
const.indexer.seasons.no_title_labels = (
    '{locale.season} {season}',  # option: Season 1
    '{locale.season} {season}',  # option: Season 1 – Title
    '{locale.season} {season}',  # option: 1. Title
)
# Season label format if season has its own title, selected by user setting `tvshow.season_label`.
# Season FFItem is available as `item`. `title` could be in English if there is no in api language.
const.indexer.seasons.with_title_labels = (
    '{locale.season} {season}',            # option: Season 1
    '{locale.season} {season} – {title}',  # option: Season 1 – Title
    '{season}. {title}',                   # option: 1. Title
)
# Season label format for alone season (season in list of any media, need to show show title too).
# 'label' is generateed from const.indexer.seasons.with_title_labels or const.indexer.seasons.no_title_labels.
const.indexer.seasons.alone_label = '{show.title} – {label}'
#: Force override season title by generated label. Should be false.
const.indexer.seasons.override_title_by_label = False
#: Means future (not aired yet) season if no date (None/null).
const.indexer.seasons.future_if_no_date = True

# -- Directories: episodes

# Time in seconds for movies / episodes without duration.
const.indexer.episodes.missing_duration = 0
# Skip future (not aired yet) episodes in season (and show) progress.
const.indexer.episodes.progress_if_aired = True
# Means future (not aired yet) episode if no date (None/null).
const.indexer.episodes.future_if_no_date = True
# Generate date from "year" (year-01-01) value if no episode date defined.
const.indexer.episodes.date_from_year = False
# Future (not aired yet) episode can by played.
const.indexer.episodes.future_playable = True
# If True, episodes number must be from 1 to N (faster but dangerous).
const.indexer.episodes.continuing_numbers = False
# Episode label format. `title` could be in English if there is no in api language.
const.indexer.episodes.label = '{season}x{episode:02d}. {title}'
# Episode label format for alone episode (episode in list of any media, need to show show title too).
# 'label' is generateed from const.indexer.episodes.label.
const.indexer.episodes.alone_label = '{show.title} – {label}'
# Last watched date-time format in episode resume list. None if disabled.
const.indexer.episodes.resume.watched_date_format = '%Y-%m-%d'
# Episode style to update description (plot). Single '{…}' means progress formatting, double '{{}}' means description formatting.
const.indexer.episodes.progressbar.style = CONST_REF.indexer.progressbar.style
# Episode progress bar source mode:
#   - 'none'                 - do not show progressbar at all
#   - 'watching'             - show video percent progress (PERCENT) if video progress >0% and < 100% else show nothing
#   - 'watched'              - show only watched videos (movies and episodes progresses are skiped)
#   - 'percent'              - show video percent progress
#   - 'percent_and_watched'  - show video percent progress and watched in background (use const.indexer.progressbar.watched.*)
const.indexer.episodes.progressbar.mode = 'percent'
#: Episode progress bar width (number of characters).
const.indexer.episodes.progressbar.width = CONST_REF.indexer.progressbar.width

# -- Directories: anime

# Default region for anime or None. If you want to use default region use CONST_REF.indexer.region
const.indexer.anime.region = None
# Number of last days to see aired shows.
const.indexer.anime.aired.last_days = 14

# -- Directories: persons

# Enable person directory.
const.indexer.persons.enabled = True
# Number of persons from TMDB discovery (or Trakt) used in "library", "add to…" etc.
const.indexer.persons.discovery_scan_limit = CONST_REF.indexer.add_to_limit
# Show tv-shows with persons role as "Self".
const.indexer.persons.show.include_self = False
# Enable "my lists" in person directory.
const.indexer.persons.my_lists.enabled = True
# Show "my lists" directly in person directory.
const.indexer.persons.my_lists.flat = True
# Show root level "my lists" flat (show sub-list on root level).
const.indexer.persons.my_lists.root.flat = True

# -- Directories: details

# Media details in details folder.
const.indexer.details.info_details = 'DEFAULT, SEASON_EPISODES, EPISODE_EN, EPISODE_GROUPS'
# Show episode groups in details folder.
const.indexer.details.show_episode_groups = True

# Content view type for videos in details folder.
const.indexer.details.videos.view = 'studios'

# Crew details in details folder (.../info). AGGREGATE_CREDITS works only for show, ignored otherwise.
const.indexer.details.credits.info_details = 'INFO_LANG, COMMON_DETAILS, AGGREGATE_CREDITS, IMAGES, TRANSLATIONS'

# -- Directories: stump idexers

# Number of votes in stump directories (campanies, keywords).
const.indexer.stump.votes = 0

# Search "first N keywords" folders. In order.
const.indexer.stump.keywords.search_n_keywords = [5, 3, 2]
# Enable select keywords dialog in keyword folders.
const.indexer.stump.keywords.select = True

# -- Directories: lists

# - trakt

# If true, show entry in listing: /tools/trakt/entry.
const.indexer.trakt.show_sync_entry = True
# Trakt mixed collections enabled.
const.indexer.trakt.collection.mixed = True
# Trakt mixed recommendations enabled.
const.indexer.trakt.recommendation.mixed = True
# Trakt my list (list of the lists) view.
const.indexer.trakt.mine.view = CONST_REF.indexer.lists_view
# Watched date-time format in trakt lists (like history). None if disabled.
const.indexer.trakt.lists.watched_date_format = '%Y-%m-%d'
# Media details for trakt list progress.
const.indexer.trakt.lists.info_details = 'DEFAULT, SHOW_SEASONS, EPISODE_EN'
# const.indexer.trakt.lists.info_details = None
# Show ••• progress bar for shows.
const.indexer.trakt.progress.bar = True  # XXX: NOT USED
# Split any media progress into pages (default value).
const.indexer.trakt.progress.page_size = CONST_REF.indexer.page_size
# Split watching movies into pages.
const.indexer.trakt.progress.movies_page_size = CONST_REF.indexer.trakt.progress.page_size
# Split watching episodes into pages.
const.indexer.trakt.progress.episodes_page_size = CONST_REF.indexer.trakt.progress.page_size
# Split tv-shows progress into pages.
const.indexer.trakt.progress.shows_page_size = CONST_REF.indexer.trakt.progress.page_size
# Exact match for tv-shows progress into pages size. If false page could be smaller, but faster.
const.indexer.trakt.progress.shows_page_size_exact_match = True
#: Default trakt.tv list sorting ('.asc' could be replaced with '.desc'):
#: - 'trakt'          - trakt.tv settings (trakt user sets in WWW)
#: - 'added.asc'      - added time
#: - 'collected.asc'  - collection added time
#: - 'rank.asc'       - rank
#: - 'released.asc'   - movie/show released
#: - 'title.asc'      - movie/show title
#: - 'runtime.asc'    - movie/show runtime (duration)
#: - 'votes.asc'      - number of votes
#: - 'popularity.asc' - fake popularity (ranking is used)
#: - 'random'         - random order
const.indexer.trakt.sort.default = 'trakt'
#: Trakt.tv watchlist sorting.
const.indexer.trakt.sort.watchlist = 'added.desc'
#: Trakt.tv collection sorting.
#: NOTE: Mixed collection does NOT support 'trakt' sorting.
const.indexer.trakt.sort.collections = 'collected.asc'
#: Fix trakt sort order (X-Sort-how) to make 'asc' really ascending.
const.indexer.trakt.sort.reverse_order = {
    'added': False,
    'collected': True,
    'rank': False,
    'released': False,
    'title': False,
    'runtime': False,
    'votes': False,
    'popularity': False,
}

# - tmdb

#: TMDB root directory is flat (own lists at root level).
const.indexer.tmdb.root.flat = False
#: TMDB mixed collections enabled.
const.indexer.tmdb.favorites.mixed = True
#: TMDB mixed recommendations enabled.
const.indexer.tmdb.watchlist.mixed = True
#: TMDB my list (list of the lists) view.
const.indexer.tmdb.mine.view = CONST_REF.indexer.lists_view
#: True if TMDB user lists should be alligned (to 20 items) for movies or shows. It takes more time (depagination).
const.indexer.tmdb.mine.align_list_pages = True

# - imdb

#: IMDB media list page size (NOTE: using IMDB ID is slow).
const.indexer.imdb.page_size = CONST_REF.indexer.page_size
#: IMDB mixed recommendations enabled.
const.indexer.imdb.watchlist.mixed = True
#: IMDB my list (list of the lists) view.
const.indexer.imdb.mine.view = CONST_REF.indexer.lists_view

# - mdblist lists

# MDBList enabled if true.
const.indexer.mdblist.enabled = True
#: MDBList media list page size.
const.indexer.mdblist.page_size = CONST_REF.indexer.page_size
#: MDBList root directory is flat (own lists at root level).
const.indexer.mdblist.root.flat = False
#: MDBList my list (list of the lists) view.
const.indexer.mdblist.mine.view = CONST_REF.indexer.lists_view
#: MDBList my list (list of the lists) view.
const.indexer.mdblist.top.view = CONST_REF.indexer.lists_view

# - justwatch (vod) lists

# Enabled JustWatch, only new VoD at the moment.
const.indexer.justwatch.enabled = False
# New VoD titles: time window (days).
const.indexer.justwatch.new_vod.days = 3
# New VoD titles: list of services (shortName). See: const.justwatch.*
const.indexer.justwatch.new_vod.services = {
    'nfx', 'mxx', 'prv', 'dnp', 'cda', 'plp', 'pbg', 'sst', 'vtp', 'cpr',
}

# - user own lists

# Own media list enabled if true.
const.indexer.own.enabled = True
# Own media list page size.
const.indexer.own.page_size = CONST_REF.indexer.page_size
# Show "my lists" directly in own list directory.
const.indexer.own.flat = True
# Show root level "my lists" flat (show sub-list on root level).
const.indexer.own.root.flat = True
# Own my list (list of the lists) view.
const.indexer.own.mine.view = CONST_REF.indexer.lists_view
# When to show list name as role. Used in "my lists" top level.
const.indexer.own.mine.show_list_name = WhenShowListName.IF_MANY
# Show flat "my lists" directly in any directory which has no own flat setting. If all indexers has own settings, this value does not matter.
const.indexer.own.mine.flat_default = True
# Role for flat root lists if list is direct (media type, not items from flated list of lists).
# String or "{name}" or "" for no role.
const.indexer.own.mine.root_direct_role = ''
# Own build-in list (without favorites and watchlist).
const.indexer.own.generic = (
    OwnListDef(':favorites', 'mixed', 'db://own/:favorites', label=L(30146, 'Favorites'), icon='DefaultMovies.png', mixed=True),
    # OwnListDef(':watchlist', 'media', 'db://own/:watchlist', label=L('Watchlist'), icon='DefaultMovies.png', mixed=True),
    # OwnListDef(':collection', 'media', 'db://own/:collection', label=L('Collection'), icon='DefaultMovies.png', mixed=True),
)
# Own custom lists.
const.indexer.own.lists = (
    OwnListDef('/', 'list', 'db://own/'),
    # XXX EXAMPLE – REMOVE IT
    # OwnListDef('Argentyńske seriale kryminalne z odcinkami poniżej 30 min', 'show',
    #            'tmdb://show?with_origin_country=AR&with_genres=80&sort_by=first_air_date.desc&with_runtime<30',
    #            icon='https://www.themoviedb.org/t/p/w600_and_h900_bestv2/dtfwJKDN3cinNpXw86r89TUpuc8.jpg'),
    # OwnListDef('Łubu dubu', 'list', 'profile://example-list_of_lists.csv',
    #            icon='https://png.pngtree.com/png-clipart/20191123/original/pngtree-list-icon-png-image_5194124.jpg'),
    # OwnListDef('Abc', 'person', 'profile://example-list_of_lists.csv'),
)

# - history

# Show watching videos (partial watched, even if not played) in history folder.
const.indexer.history.show_watching = False
# Format for history item role.
const.indexer.history.role = '{played_at:%Y-%m-%d}'
#: How many items get from hostory in "Add to...".
const.indexer.history.limit = CONST_REF.indexer.add_to_limit

# - tools

# Content view type for tools folders.
const.indexer.tools.view = 'sets'

# -- Genre menu

#: Fix TMDB genres translations [kodi-language][tmdb-genre-id].
const.indexer_group.genres.translations = {
    'pl-PL': {
        10762: 'Dla dzieci',
        10763: 'Informacje',
        10764: 'Reality TV',
        10766: 'Romans',
        10767: 'Talk Shows',
        10768: 'Wojna i polityka',
        10770: 'Filmy telewizyjne',
    },
}
# Genre icons active set (fanfilm, kodi).
const.indexer_group.genres.icons_set = 'fanfilm'
# Genre icons by TMDB genre ID (is active icons_set).
const.indexer_group.genres.icons = {
    'fanfilm': {
        12: 'genres/Adventure.png',
        14: 'genres/Fantasy.png',
        16: 'genres/Animation.png',
        18: 'genres/Drama.png',
        27: 'genres/Horror.png',
        28: 'genres/Action.png',
        35: 'genres/Comedy.png',
        36: 'genres/History.png',
        37: 'genres/Western.png',
        53: 'genres/Thriller.png',
        80: 'genres/Crime.png',
        99: 'genres/Documentary.png',
        878: 'genres/Sci-Fi.png',
        9648: 'genres/Mystery.png',
        10402: 'genres/Music.png',
        10749: 'genres/Romance.png',
        10751: 'genres/Family.png',
        10752: 'genres/War.png',
        10759: 'genres/Action_and_Adventure.png',
        10762: 'genres/Kids.png',
        10763: 'genres/News.png',
        10764: 'genres/Reality_show.png',
        10765: 'genres/Sci_fi_and_fantasy.png',
        10766: 'genres/Romance.png',
        10767: 'genres/Television.png',
        10768: 'genres/Historical.png',
        10770: 'genres/Television.png',
    },
    'kodi': {
        12: 'resource://resource.images.moviegenreicons.white/Adventure.jpg',
    }
}

# -- Language menu

#: Set of favorite languages.
const.indexer_group.languages.default = {
    'cs', 'de', 'en', 'es', 'fr', 'it', 'ja', 'ko', 'pl', 'sv', 'uk', 'zh',
}
#: Set of top languages from favorites. Current Kodi language will be on top too.
const.indexer_group.languages.top = {
    'pl',
}
#: Custom language groups. Use `|` for OR, `,` for AND.
const.indexer_group.languages.groups = (
    # DirItemSource('pl|lt|uk|be', "I Rzeczpospolita"),
)
#: Override vote count for media by language. None for use default.
const.indexer_group.languages.votes = 0

# -- Country menu

#: Set of favorite countries.
const.indexer_group.countries.default = {
    'GB', 'CN', 'CZ', 'DE', 'DK', 'ES', 'FR', 'IT', 'JP', 'KR', 'PL', 'SE', 'UK', 'US',
}
#: Set of top countries from favorites. Current Kodi region will be on top too.
const.indexer_group.countries.top = {
    'PL',
}
#: Custom country groups. Use `|` for OR, `,` for AND.
const.indexer_group.countries.groups = (
    DirItemSource('DK|FI|IS|NO|SE', L(30102, 'Scandinavian')),  # Scandinavian
)
#: Override vote count for media by country. None for use default.
const.indexer_group.countries.votes = 0


# ----- tmdb.org settings -----

# Total count of append_to_reposnse items is 20 (TMDB API).
const.tmdb.append_to_response_limit = 20
# Default languages used in `include_image_language` when info get with `append_to_response=images`.
# const.tmdb.image_languages = all_languages
# Mode of image getting:
#   - append       - use `append_to_response=images` with fixed `include_image_language`, no poster at all often
#   - append_en    - use `append_to_response=images` with fixed `include_image_language=en`
#   - append_lang  - use `append_to_response=images` with fixed `include_image_language={lang}`
#   - pull         - like `append` but get images in next request if fails (no images)
#   - full         - always make two requests, support all services (it is forced for non-tmdb services, e.g. fanart.tv)
#   - all          - use /images to get all images in concurrent request
const.tmdb.get_image_mode = 'append'
# Langauge code aliases. Non-ISO-631-1 TMDB extensions.
const.tmdb.language_aliases = (
    ('zh', 'cn'),
)
# Maximum number of HTTP threads in TMDB skeleton.
const.tmdb.skel_max_threads = 10
# Discover items without keywords IDs.
const.tmdb.avoid_keywords = (
    155477,  # softcore
)

# API version (most endpoints uses v3 and v3 could be generated from v4).
const.tmdb.auth.api = 4
# API auth dialog - refresh interval in seconds (ask TMDB for token and update progress).
const.tmdb.auth.dialog_interval = 5
# API auth dialog - expiration time in seconds, max is 900 – TMDB remove token after 15 min.
const.tmdb.auth.dialog_expire = 300
# TMDB auth link QR-Code scale.
const.tmdb.auth.qrcode_size = 10

# Number of tries to send request to TMDB.
const.tmdb.connection.try_count = 3
# Number of seconds between TMDB connection tries.
const.tmdb.connection.try_delay = 1.0
# TMDB request timeout.
const.tmdb.connection.timeout = 15
# TMDB common request session (for all threads).
const.tmdb.connection.common_session = True
# Maximum number of concurrent connections to TMDB.
const.tmdb.connection.max = 0


# ----- trakt.tv settings -----

#: How many items get from recomendations in "Add to...".
const.trakt.recommendations_limit = CONST_REF.indexer.add_to_limit
#: Page size (page limit) in trakt.tv for directory.
const.trakt.page.limit = 20
#: Page limit in scan all pages in trakt.tv. DO NOT edit.
const.trakt.scan.page.limit = 100
#: Interval of first trakt sync (playback, watched, etc) after service started.
const.trakt.sync.startup_interval = 3
#: Interval of cyclic trakt sync (playback, watched, etc) refresh.
const.trakt.sync.interval = 1800
#: Time of cool-down before next sync starts. Used with series sync reqiests.
const.trakt.sync.cooldown = 30
#: How long delay the sync notification. Notification is hidden if sync take less time.
const.trakt.sync.notification_delay = 3
#: Timeout for wait synchronic trakt sync (via service).
const.trakt.sync.wait_for_service_timeout = 180
#: Default sync video -> trakt for alien video (plugins and all non-FF3 sources).
const.trakt.sync.alien.default = False
#: Tune alien video -> trakt URL schema sync: {schema: True/False}.
const.trakt.sync.alien.scheme = {
    # 'plugin': False,   # all plugins are not synced by default, you could stil filter by const.trakt.sync.alien.plugins
    # 'file': True,      # all local files are synced
}
#: Tune alien plugins video -> trakt sync: {plugin_id: True/False}.
const.trakt.sync.alien.plugins = {
    # 'plugin.video.my_super_extra_plugin': False,
    # 'plugin.video.nothing1': True,
}
#: Auto Trakt.tv auth. Register device every time if got 403.
const.trakt.auth.auto = False
#: Trakt.tv auth link QR-Code scale.
const.trakt.auth.qrcode.size = 20

# Number of tries to send request to trakt.tv.
const.trakt.connection.try_count = 3
# Number of seconds between trakt.tv connection tries.
const.trakt.connection.try_delay = 1.0
# Trakt.tv request timeout.
const.trakt.connection.timeout = 15

# ----- mdblist.com settings -----

# Number of tries to send request to trakt.tv.
const.mdblist.connection.try_count = 3
# Number of seconds between trakt.tv connection tries.
const.mdblist.connection.try_delay = 1.0
# Trakt.tv request timeout.
const.mdblist.connection.timeout = 15


# ----- justwatch.com settings -----

#: Max episodes in JustWatch requests
const.justwatch.episode_max_limit = 100

# All VoD services in PL (clearName / shortName), used in const.indexer.movies/tvshows:
#   'Amazon Prime Video' (prv)    'Amazon Video' (amz)
#   'Apple TV' (atp)              'Apple TV Store' (itu)
#   'Arte' (art)                  'CANAL+' (cpr)
#   'CDA Premium' (cda)           'CHILI' (chi)
#   'Cinema City' (cct)           'Cultpix' (ctx)
#   'Curiosity Stream' (cts)      'Disney Plus' (dnp)
#   'DocAlliance Films' (daf)     'DOCSVILLE' (dsv)
#   'FilmBox+' (flb)              'Filmzie' (fmz)
#   'HBO Max' (mxx)               'Helios' (hlo)
#   'MUBI' (mbi)                  'MultiKino' (mko)
#   'Netflix' (nfx)               'Pilot WP' (pwp)
#   'Player' (plp)                'Plex' (plx)
#   'Polsat Box Go' (pbg)         'Rakuten TV' (wki)
#   'SkyShowtime' (sst)           'True Story' (trs)
#   'TVP' (vtp)                   'Viaplay Amazon Channel' (vpy)


# ----- sources settings / tune -----

# All video duration ε, how big the video duration discrepancy can be (0.05 mens 5%).
const.sources.duration_epsilon = .08

# The keyword to detect whether we are dealing with anime
const.sources.check_anime = 'anime'

# List of keywords to exclude sources if found in description or link.
# const.sources.exclude_keywords = ["CAM", "TS", "HD-TS", "lektor AI"]
const.sources.exclude_keywords = []

# Accept extra source languages. Set of codes ot None. Appended to settings `source.sound.lang`.
const.sources.include_languages = None

# Sources rules, color, order, play mode, etc.
# Order does matter, last rule is more important. Only defined attributes override previous one.
# Use `SourcePattern(): False` to disable the source at all.
# Default rules always should be applied. For update in `local.py` use:
# >>> const.sources.rules = {
# >>>     **const.sources.rules,  # IMPORTANT! Keep default rules, then add your own.
# >>>     SourcePattern(…): SourceAttribute(…),
# >>> }
# For color and order attributes description see SourceAttribute class in cdefs.py.
# All matched rules are applied, until the first with `final=True`.
#
# == Pattern (see SourcePattern, RegEx, Glob, Expr from cdefs.py) ==
# Only used attributes are checked, e.g. SourcePattern(provider='tb7') check only provider, other attributes are ignored.
#   - provider – source provider, e.g. 'tb7', 'xt7', 'external', 'wizjacc', etc.
#   - platform – source platform, e.g. 'android', 'windows', etc.
#   - kodi     – [int] Kodi version (int or conditional expression with Kodi version, e.g. '>= 22').
#   - hosting  – source hosting, e.g. 'player', 'lulustream', etc.
#   - setting  – user setting, e.g. 'isa.enabled' (bool or conditional expression with settings, e.g. 'settings..hosts.quality.max > 1').
#   - m3u8     – [bool] source is m3u8 (True) or not (False).
#   - premium  – [bool] source is premium (True) or not (False). Taken from source meta['premium'].
#   - meta     – source meta, e.g. {'size': '1 KB'} one or more, only used keys are checked, e.g. other keys are ignored.
#   - size     – [int] source size in bytes (int or conditional expression with size, e.g. 'size > 1 * MB'). Calculated from source meta['size'].
#   - media    – real media type, e.g. 'movie', 'episode'.
# All patterns are compared to value (str, int, bool) or one of patterns RegEx, Glob, Expr (any type) or range (if int).
#   - RegEx is full mached regular expression, e.g. RegEx('tb7') or R('tb7|xt7').
#   - Glob is full mached glob pattern, e.g. Glob('tb*7') or G('tb?').
#   - Expr is conditional expression with variables,
#     - use `{}` for value, e.g. size=Expr('2*GB < {} < 3*GB')
#     - just use simple compare, e.g. kodi=Expr('< 22')
#     - you can use settings, e.g. Expr('settings.getInt("hosts.quality.max") > 1') or Expr('settings.getBool("isa.enabled")')
#
# == Attribute (see SourceAttribute from cdefs.py) ==
# Only used attributes are set, e.g. SourceAttribute(menu=('buy', 'play'))
#   - color – source background item color, e.g. 'FF990000' (ARGB hex).
#   - order – force source order, bigger value are more important:
#     - +2001..+2006:  download, local, plex, jellyfin, external
#     - +1000:         account (already bought, in library, etc.)
#     - 0..999:        providers
#   - play – force play mode: 'auto', 'direct', 'isa'.
#   - menu – set menu items, sequence of 'direct', 'isa', 'play', 'buy'.
#   - meta – update source meta, e.g. {'info': 'zz', 'language': 'pl'} , only defined keys are set. Update is done on filtering beginning.
#   - final – if True, stop processing rules, no more rules will be applied after this one.
#
const.sources.rules = {
    # buy again CM
    SourcePattern(provider=R('tb7|xt7')): SourceAttribute(menu=('buy', 'play')),
    # default for m3u8 (url or filename) when setting is enabled
    SourcePattern(m3u8=True, setting='isa.enabled'): SourceAttribute(play='isa'),
    # default for non-m3u8 (another file or setting is disabled)
    SourcePattern(setting='not isa.enabled'): SourceAttribute(play='direct'),
    SourcePattern(m3u8=False): SourceAttribute(play='direct'),
    # some not-working ISA
    SourcePattern(hosting='player', platform='android'): SourceAttribute(play='direct'),
    SourcePattern(hosting='lulustream', platform=R('android|windows')): SourceAttribute(play='direct'),
    # providers on Kodi 22+
    SourcePattern(provider='wizjacc', kodi=Expr('< 22')): False,
    SourcePattern(provider='netmirror', kodi=Expr('< 22')): False,
    SourcePattern(provider='vidsrc', kodi=Expr('< 22')): False,
    SourcePattern(provider='vsembed', kodi=Expr('< 22')): False,
}

# Dictionary for franchise names to check in sources.
# Use example: "search phrase": ["franchise1", "franchise2"]
const.sources.franchise_names = {
    'JAG': ['JAG', 'Wojskowe Biuro Śledcze', 'Wojskowe Biuro Sledcze', 'J. A. G.'],
    # Star Wars seriale
    'Acolyte': ['Star Wars The Acolyte', 'Gwiezdne Wojny Akolita'],
    'Andor': ['Star Wars Andor', 'Gwiezdne Wojny Andor'],
    'Ahsoka': ['Star Wars Ahsoka', 'Gwiezdne Wojny Ahsoka'],
    'Mandalorian': ['Star Wars The Mandalorian', 'Gwiezdne Wojny Mandalorian'],
    'Boba Fett': ['Star Wars The Book of Boba Fett', 'Gwiezdne Wojny Księga Boby Fetta'],
    'Bad Batch': ['Star Wars The Bad Batch', 'Gwiezdne Wojny Zła Partia'],
    'Visions': ['Star Wars Visions', 'Gwiezdne Wojny Wizje'],
    # Star Wars filmy
    'Episode I': ['Star Wars Episode I The Phantom Menace', 'Star Wars I The Phantom Menace', 'Gwiezdne Wojny Mroczne Widmo', 'Gwiezdne Wojny Część I Mroczne Widmo'],
    'Episode II': ['Star Wars Episode II Attack of the Clones', 'Star Wars II Attack of the Clones', 'Gwiezdne Wojny Atak Klonów', 'Gwiezdne Wojny Część II Atak Klonów'],
    'Episode III': ['Star Wars Episode III Revenge of the Sith', 'Star Wars III Revenge of the Sith', 'Gwiezdne Wojny Zemsta Sithów', 'Gwiezdne Wojny Część III Zemsta Sithów'],
    'Episode IV': ['Star Wars Episode IV A New Hope', 'Star Wars IV A New Hope', 'Gwiezdne Wojny Nowa Nadzieja', 'Gwiezdne Wojny Część IV Nowa Nadzieja'],
    'Episode V': ['Star Wars Episode V The Empire Strikes Back', 'Star Wars V The Empire Strikes Back', 'Gwiezdne Wojny Imperium Kontratakuje', 'Gwiezdne Wojny Część V Imperium Kontratakuje'],
    'Episode VI': ['Star Wars Episode VI Return of the Jedi', 'Star Wars VI Return of the Jedi', 'Gwiezdne Wojny Powrót Jedi', 'Gwiezdne Wojny Część VI Powrót Jedi'],
    'Episode VII': ['Star Wars Episode VII The Force Awakens', 'Star Wars VII The Force Awakens', 'Gwiezdne Wojny Przebudzenie Mocy', 'Gwiezdne Wojny Część VII Przebudzenie Mocy'],
    'Episode VIII': ['Star Wars Episode VIII The Last Jedi', 'Star Wars VIII The Last Jedi', 'Gwiezdne Wojny Ostatni Jedi', 'Gwiezdne Wojny Część VIII Ostatni Jedi'],
    'Episode IX': ['Star Wars Episode IX The Rise of Skywalker', 'Star Wars IX The Rise of Skywalker', 'Gwiezdne Wojny Skywalker. Odrodzenie', 'Gwiezdne Wojny Część IX Skywalker. Odrodzenie'],
    'Rogue One': ['Rogue One A Star Wars Story', 'Łotr 1. Gwiezdne Wojny – Historia'],
    'Solo': ['Solo A Star Wars Story', 'Han Solo. Gwiezdne Wojny – Historie'],
    # Inne filmy
}
# Dictionary for franchise names seperator.
const.sources.franchise_names_sep = r'[ .]'
# List of supported source languages.
const.sources.language_order = (
    'pl',
    'mul',
    'multi',
    'en',
    'de',
    'fr',
    'it',
    'es',
    'pt',
    'ko',
    'ru',
    '-',
    '',
)
# Media info details for searching sources (used in providers).
const.sources.info_details = '+SHOW_SEASONS'

# Translations for providers names.
const.sources.translations.providers = {
    'pl-PL': {
        'library': 'Biblioteka',
        'download': 'Pobrane',
    }
}
# Translations for hostings names.
const.sources.translations.hostings = {
}


# CDA video duration ε, how big the video discrepancy can be (0.05 means 5%).
const.sources.cda.duration_epsilon = CONST_REF.sources.duration_epsilon
# Looser ε for TV episodes – credits/intro cuts are common.
const.sources.cda.episode_duration_epsilon = .12
# Show CDA premium_free=true as given name. Default is empty (show nothing).
const.sources.cda.show_premium_free = ''
# Minimal file size in MB for CDA provider.
const.sources.cda.min_size_mb = 50

# GIPTV VOD index max age in hours.
const.sources.giptv.index_max_age_h = 24

# Choose domain for ekino https://ekino.ws or https://ekino-tv.pl
const.sources.ekinotv.domain = "https://ekino.ws"

# tb7/xt7 fot before season number.
# If True, add dot before season number (e.g. ".s01")
const.sources.xtb7.dot_before_season = True

# tb7/xt7 space before season number.
const.sources.xtb7.space_before_season = False

# tb7/xt7 max number of search queries for shows.
# 1: Original title + episode  (EN+ep — catches most files)
# 2: Local title + episode     (PL+ep — catches PL-named files, skipped if PL==EN)
# --- fallback stages (redundant in most cases, enable if above miss something) ---
# 3: Original title + season   (EN+season)
# 4: Local title + season      (PL+season)
# 5: Original title only       (EN only)
# 6: Local title only          (PL only)
const.sources.xtb7.max_show_search_queries = 2

# tb7/xt7 use similarity.
const.sources.xtb7.similarity_check = False

# tb7/xt7 similarity threshold.
# If similarity is greater than this value, then the source is considered suitable.
const.sources.xtb7.similarity_threshold = 0.94

# tb7/xt7 title replacements.
const.sources.xtb7.title_replacements = {
    "star wars new hope": "star wars",
}


# Extra debug info for Rapideo / NoPremium / TwojLimit provider.
const.sources.rapideo.debug = False
# Extra debug info for CDA provider.
const.sources.cda.debug = False
# Extra debug info for tb7 / xt7 provider.
const.sources.xtb7.debug = False
# Extra debug info for wrzucaj provider.
const.sources.wrzucaj.debug = False
# Extra debug info for netmirror provider.
const.sources.netmirror.debug = False

# tb7/xt7 use two separate login sessions for parallel EN+PL search.
# When True, a second session is created and logged in independently so that
# both queries can run at the same time. web_parallel cookies are cached
# separately so the extra login cost is paid only once per 3 hours.
const.sources.xtb7.parallel_sessions = True


# Use the no_transfer function in source window
const.sources.premium.no_transfer = True


# ----- Player -----

#: True, if player should try to set Kodi DbId.
const.player.set_dbid = False
#: How to cancel playing video from the beginning.
const.player.cancel_start = PlayCancel.EMPTY
#: How to cancel playing video from the resume point. Avoid PlayCancel.EMPTY, could set `watched`.
const.player.cancel_resume = PlayCancel.TT430
#: How to cancel playing video from the resume point. Avoid PlayCancel.EMPTY, could set `watched`.
const.player.refresh_on_cancel_resume = True
#: Use (kodi default) or not HEAD request in player video.
const.player.head_lookup = False


# ----- Library -----

# Force always flat folder for shows (old format).
# If False new show folders uses season subfolders, existing old one uses flat structure.
# If True always use flat folder structure.
const.library.flat_show_folder = False
# The STRM file name for new movies and shows. Use another format if detected.
const.library.strm_filename = StrmFilename.TITLE_YEAR
# Language of title (and folder) in the library.
const.library.title_language = 'en-US'
# Media info details for library adding.
const.library.info_details = '+SHOW_SEASONS'
# If True, use safe path for library items (replace more characters in title). If false use Kodi safe path (depends on platform).
const.library.safe_filename = False

# If True, sync Kodi library (update library) after each batch (but not a batch chunk).
# if False, sync Kodi library only if no another batch in the queue (sync after batches group).
const.library.service.sync_every_batch = False
# If True, wait for each Kodi library sync (library update).
# If False, start next batch immediately.
const.library.service.sync_wait = True
# Number of items in single chunk for library "add to" operations. Zero means no chunking.
const.library.service.chunk_size = 50
# Sleep time in seconds between library "add to" chunks.
const.library.service.chunk_sleep = 15


# ----- Dialogues -----

# -- Auth --

# Auth dialog: link color.
const.dialog.auth.link_color = 'FFB33737'
# Auth dialog: code color.
const.dialog.auth.code_color = 'FFB3B337'

# -- Add to --

# Allowed media conversions in add-to dialog.
# Available conversions are limited by lib.ff.lists.ListConvertRules.
const.dialog.add_to.allowed_conversions = {
    'collection': ListType.MOVIE,
    'movie': ListType.COLLECTION,
    'show': ListType.SEASON | ListType.EPISODE,
    'season': ListType.SHOW | ListType.EPISODE,
    'episode': ListType.SHOW | ListType.SEASON,
}
# True if adding should be quiet (no notification).
const.dialog.add_to.quiet = False
# Add-to dialog: show absent (non-existing) media types.
const.dialog.add_to.media_type.absent_visible = ListType.MEDIA
# Add-to dialog: absent (non-existing) media color.
const.dialog.add_to.media_type.absent_color = '99666666'
# Add-to dialog: existing media color.
const.dialog.add_to.media_type.exist_color = 'FFEEEEFF'
# Add-to dialog: media converting color.
const.dialog.add_to.media_type.converting_color = 'FFFFCC66'
# Add-to dialog: disallowed color.
const.dialog.add_to.media_type.disallowed_color = 'FFFF6666'
# Type of media names in the "add to..." dialog header.
const.dialog.add_to.media_type.bar_mode = 'icons'
# All defined services (left column) and theirs lists (central column) in default Add-to dialog.
# Values are ListPointer names.
# This settings is used to handle "Add to..." dialog with no service name, see: const.indexer.context_menu.add_to.
const.dialog.add_to.lists.default = {
    L(30355, 'Local'): ('library', 'own:favorites', 'own:user', 'logs'),
    # L('Local'): ('library', 'own:favorites', 'own:watchlist', 'own:collection', 'own:user', 'logs'),
    L(30356, 'Trakt'): ('trakt:favorites', 'trakt:watchlist', 'trakt:collection', 'trakt:user'),
    L(32775, 'TMDB'): ('tmdb:favorites', 'tmdb:watchlist', 'tmdb:user'),
    L(30357, 'MDBList'): ('mdblist:watchlist', 'mdblist:user'),
    # L('Own lists'): ('own:favorites', 'own:watchlist', 'own:user'),  # TEST
    # L('Library'): ('library',),                                      # TEST
    # L('Logs'): ('logs',),                                            # TEST
    L(30146, 'Favorites'): ('own:favorites', 'trakt:favorites', 'tmdb:favorites'),
}
# All defined services (left column) and theirs lists (central column) in Add-to dialog with service name.
# Keys are ListService names. All keys must be used.
# Values are dialog services definitions: label and ListPointer names. More enrties could be used in single service.
# This settings is used to handle "Add to..." dialog with service name, see: const.indexer.context_menu.add_to.
const.dialog.add_to.lists.services = {
    'local': {
        L(30355, 'Local'): ('library', 'own:favorites', 'own:watchlist', 'own:user', 'logs'),
    },
    'library': {
        L(32541, 'Library'): ('library',),
    },
    'own': {
        L(30358, 'Own lists'): ('own:favorites', 'own:user'),
        # L('Own lists'): ('own:favorites', 'own:watchlist', 'own:collection', 'own:user'),
    },
    'trakt': {
        L(30356, 'Trakt'): ('trakt:favorites', 'trakt:watchlist', 'trakt:collection', 'trakt:user'),
    },
    'tmdb': {
        L(32775, 'TMDB'): ('tmdb:favorites', 'tmdb:watchlist', 'tmdb:user'),
    },
    'mdblist': {
        L(30357, 'MDBList'): ('mdblist:watchlist', 'mdblist:user'),
    },
    'logs': {
        L(30359, 'Logs'): ('logs',),
    },
}

# -- Ratings --

# Style of ratings dialog.
# - triple  - up to tree starts for one rating level (in single line)
# - lines   - one line per rating service
const.dialog.ratings.style = 'stars'
# Show raw (service) rating value in dialog.
const.dialog.ratings.show_raw_rating = False
# Show notification after ratings are set.
const.dialog.ratings.notification = True
# Colors for ratings dialog services (used in cycle).
const.dialog.ratings.colors = (
    'FFBB33FF',  # Trakt (purple)
    'FF0D9BE8',  # TMDB (blue)
    'FFB2B831',  # MDBList (yellow)
    'FF11BAC0',
    'FFDD8C0E',
)
# Autoclose dialog after N seconds. 0 means no autoclose.
const.dialog.ratings.autoclose_timeout = 10


# ----- Ratings -----

# Enabled ratings services, if False, ratings dialog is disabled.
# If true, all available ratings services are used (trakt, tmdb, mdblist).
# Else, list of service names could be used to limit ratings services and set services order.
# Example: const.dratings.enabled = ('trakt', 'tmdb')
const.ratings.enabled = True
# Ask for rating after every watching video if True.
# Ask for rating only once for first time watching if False.
const.ratings.every_watched = False
# What rate show exactly means: sequence of 'show', 'season', 'episode' and 'show_finished', 'season_finished' (if all items are watched).
# Many values means many dialogs in sequence.
const.ratings.watched_show_mode = ('episode', 'season_finished', 'show_finished')
# Filter for ratings dialog. Default is True.
# Key is service name (trakt, tmdb, ... or '*') and actions name (context_menu, watched_movie, watched_episode or '*').
const.ratings.filter = {
    # ('tmdb', 'context_menu'): False,  # disable TMDB rating in context menu
    # ('trakt', 'watched_episode'): False,  # disable Trakt show rating after episode watched
}
# Show ratings in all folders (movies, shows etc.).
# - False   - do not show ratings in folders
# - 'min'   - show lowest rating from all services
# - 'max'   - show highest rating from all services
# - 'avg'   - show average rating from all services
# - 'first' - show first rating from defined services order (see: const.ratings.enabled)
# - service name - show rating from defined service only (e.g. 'trakt')
const.ratings.all_folders = 'avg'


# ----- Low level settings (don't change, you are warned) -----

# Interval in internal sleep loop.
const.tune.sleep_step = 0.1
# Interval in internal threads.Event.wait() loop.
const.tune.event_step = 0.1
# Max workers for api.depagine().
const.tune.depagine_max_workers = None

#: Sqlite3 connection timeout (python default is 5.0).
const.tune.db.connection_timeout = 3.0
#: DB state.wait_for_value() reading interval.
const.tune.db.state_wait_read_interval = 0.2
#: Where keep state varibables by default.
const.tune.db.state_mode = StateMode.SERVICE
#: Tune state varibables.
const.tune.db.module_state_mode = {
    'service': StateMode.DB,
    'trakt': StateMode.DB,
}

# Number of kept settings backup files.
const.tune.settings.vacuum_files = 5

#: Interval in internal sleep loop.
const.tune.service.check_interval = 3
#: Interval in internal sleep loop.
const.tune.service.job_list_sleep = 1
#: Interval for group update (kodi notification for season or show).
const.tune.service.group_update_timeout = 5
#: Show busy dialog on group update (kodi notification for season or show).
const.tune.service.group_update_busy_dialog = True
# Delay to set item focus after plugin directory (plugin exit with 'focus' index).
const.tune.service.focus_item_delay = .5
# Extra hard dealy to start FF service.
const.tune.service.startup_delay = 0
# Timeout for service up.
const.tune.service.startup_timeout = 10
# Timeout for service RPC call response.
const.tune.service.rpc_call_timeout = 3

# Port number for proxy HTTP server. Zero means take any free port.
const.tune.service.http_server.port = 0
# Number of tries to get FF service URL.
const.tune.service.http_server.try_count = 12
# Wait between get FF service URL tries.
const.tune.service.http_server.wait_for_url = .25
# How verbose should be http service server? 0 - off.
const.tune.service.http_server.verbose = 1

# Port number for web HTTP server. Used only if settings is not set.
const.tune.service.web_server.port = 8663
# How verbose should be web http service server? 0 - off.
const.tune.service.web_server.verbose = 1
# Parse cookies from web server and convert them to settings.
const.tune.service.web_server.cookies = {
    'cda-hd.cc': {
        'cf_clearance': 'cdahd.cookies_cf',
        ':user_agent': 'cdahd.user_agent',
    },
    'zaluknij.cc': {
        'cf_clearance': 'zaluknij.cookies_cf',
        ':user_agent': 'zaluknij.user_agent',
    },
    'obejrzyj.to': {
        'cf_clearance': 'obejrzyj.cookies_cf',
        ':user_agent': 'obejrzyj.user_agent',
    },
    'filmyonline.cc': {
        'cf_clearance': 'filmyonline.cookies_cf',
        ':user_agent': 'filmyonline.user_agent',
    },
    'vidlink.pro': {
        'cf_clearance': 'vidlink.cookies_cf',
        ':user_agent': 'vidlink.user_agent',
    },
    'storm.vodvidl.site': {
        'cf_clearance': 'vidlink.cookies_cf',
        ':user_agent': 'vidlink.user_agent',
    },
    'megacloud.live': {
        'cf_clearance': 'vidlink.cookies_cf',
        ':user_agent': 'vidlink.user_agent',
    },
    'ekino.*': {
        'PHPSESSID': 'ekino.phpsessid',
        'o_autenticate': 'ekino.cookies_auth',
        ':user_agent': 'ekino.user_agent',
        ':host': 'ekino.domain',
    },
    'net*.cc': {
        't_hash_t': 'netmirror.cookies_t_hash_t',
        't_hash': 'netmirror.cookies_t_hash',
        't_hash_p': 'netmirror.cookies_t_hash_p',
        'user_token': 'netmirror.cookies_user_token',
        ':user_agent': 'netmirror.user_agent',
    },
}
# Notify if cookies are updated.
const.tune.service.web_server.update_notification = True

# Default QR-Code scale.
const.tune.misc.qrcode.size = 20

# Pattern for output (generated) window/dialog XML.
# const.tune.gui.xml_output_filename = 'tmp--{stem}.{timestamp}.xml'
const.tune.gui.xml_output_filename = 'tmp--{name}'
# Kodi set XML element value lag (time to do not trust xml value right after set).
const.tune.gui.set_xml_value_lag = .25


# ----------------------------------------------------------------------------- #
# -----                              THE END                              ----- #
# ----------------------------------------------------------------------------- #

# --- must be on the bottom of the file
const_done()

# DEBUG: test if all settings are correct
# raise SystemExit(0)
