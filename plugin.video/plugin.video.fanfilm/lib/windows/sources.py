from __future__ import annotations
import re
from datetime import datetime, timedelta
from html import unescape
from threading import Thread
from urllib.parse import unquote
from copy import deepcopy
from functools import partial
from time import monotonic
from typing import Optional, Union, Any, List, Dict, Iterable, cast, TYPE_CHECKING
from typing_extensions import Literal

from xbmcgui import ListItem, Window, Dialog, ControlLabel, ControlEdit, ControlButton, ControlList, ControlProgress
from xbmcgui import (
    ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT, ACTION_MOVE_UP, ACTION_MOVE_DOWN, ACTION_SELECT_ITEM,
)

from ..ff.control import addonPath, run_plugin, settings, busy_dialog, close_busy_dialog
from ..ff.locales import kodi_locale
from ..ff.log_utils import fflog, fflog_exc
from ..ff.types import is_literal
from ..ff import control
from ..ff.locales import flag_url
from ..ff.info import ffinfo
from ..ff.item import FFItemEpisodeGroup
from ..ff.kotools import xsleep
from ..ff.tricks import FormatObjectGetter, Formatter
from ..sources import Source
if TYPE_CHECKING:
    from typing import Sequence, Iterator, Callable, ClassVar
    from xbmcgui import Action
    from ..ff.menu import ContextMenu
    from ..ff.item import FFItem, FFVTag, FFTitleAlias, FFEpisodeGroup
    from ..ff.sources import sources as SourceFactory, SourceSearchQuery, SourceSearchQueryKey

from ..kolang import L
from .base import BaseDialog, CANCEL_ACTIONS, MENU_ACTIONS, EDIT_ACTIONS
from .context import ContextMenuDialog
from cdefs import PlayMode, is_play_mode
from const import const


TitleQueryKey = Literal['localtitle', 'title', 'originalname']


class RescanSources(Exception):
    """Force rescan sources again."""

    def __init__(self, *, query: Optional['SourceSearchQuery']) -> None:
        super().__init__()
        self.query: Optional['SourceSearchQuery'] = query


def endtime(minutes: Union[int, str]) -> str:
    if not minutes:
        return '–'
    end = datetime.now() + timedelta(minutes=int(minutes))
    return end.strftime("%H:%M")


def clear_source(source):
    return source.rsplit(".", 1)[0]


def format_info(label):
    try:
        return re.sub(r"(\d+(?:[.,]\d+)?\s*[GMK]B)", r"[COLOR ffffffff]\1[/COLOR]", label)
    except Exception:
        return label


class Panel(BaseDialog):

    XML = 'SourcesPanel.xml'

    def onAction(self, action):
        action_id = action.getId()
        fflog(f' ######### {action_id = }, {action = }')
        if action_id in CANCEL_ACTIONS:
            self.close()


class AliasDialog(BaseDialog):
    """Dialog for selecting title aliase."""

    XML = 'SourcesAliases.xml'
    CUSTOMIZED_XML = True

    # OK_BUTTON = 31
    CANCEL_BUTTON = 32
    ITEM_LIST = 51

    def __init__(self,
                 *args,
                 aliases: Iterable[FFTitleAlias],
                 preselect: int = 0,
                 **kwargs,
                 ) -> None:
        super().__init__(*args, **kwargs)
        # fflog(f'AliasDialog: {list(aliases) = }')
        self.aliases = list(aliases)
        self.preselect: int = preselect

    def on_init(self) -> None:
        if widget := self.get_control(self.ITEM_LIST, ControlList):
            widget.addItems(list(self._create_items(self.aliases)))
            widget.selectItem(self.preselect)
            self.setFocus(widget)

    def on_click(self, control_id: int) -> None:
        """Kodi sent the control's focus. Custom callback."""
        if control_id == self.CANCEL_BUTTON:
            self.close(-1)
        elif control_id == self.ITEM_LIST:
            if widget := self.get_control(self.ITEM_LIST, ControlList):
                pos = widget.getSelectedPosition()
                self.close(pos)

    def _create_items(self, aliases: Iterable[FFTitleAlias]) -> Iterator[ListItem]:
        for alias in aliases:
            item = ListItem(label=alias.title)
            item.setProperty('country', alias.country.upper())
            if alias.country:
                flag = flag_url.format(lower=alias.country.lower())
                item.setProperty('flag', flag)
            yield item


class EditDialog(BaseDialog):
    """Dialog for editing search query."""

    XML = 'SourcesEdit.xml'
    CUSTOMIZED_XML = True

    #: Edit controls (101..110):
    EDIT_CONTROLS: ClassVar[dict[int, SourceSearchQueryKey]] = {
        101: 'localtitle',
        102: 'title',
        103: 'originalname',
        104: 'year',
        105: 'premiered',
        106: 'imdb',
        107: 'tmdb',
        # 108: 'tvshowtitle',
        109: 'season',
        110: 'episode',
        111: 'episode_group',
    }

    UPDATE_ACTIONS = {*EDIT_ACTIONS, ACTION_SELECT_ITEM}

    SCAN_BUTTON = 31
    CANCEL_BUTTON = 32
    RESET_BUTTON = 33
    TITLE_ALIASES_BUTTON = 121
    EN_TITLE_ALIASES_BUTTON = 122
    ORIG_TITLE_ALIASES_BUTTON = 123
    EPISODE_GROUPS_BUTTON = 131

    EPISODE_GROUP_READONLY_LABEL = 111
    NO_EPISODE_GROUP_LABEL = L(30540, '– none –')

    def __init__(self,
                 *args,
                 query: SourceSearchQuery,
                 default_query: SourceSearchQuery | None = None,
                 raise_rescan: bool = True,
                 **kwargs,
                 ) -> None:
        super().__init__(*args, **kwargs)
        self.init_done = False
        self.raise_rescan = raise_rescan
        # Current (avtive) query.
        self.query: SourceSearchQuery = query.copy()
        # Default query, episode_group must be None.
        self.default_query: SourceSearchQuery = (query if default_query is None else default_query).copy()
        self.ffitem: FFItem | None = self.query.get('ffitem')
        if query.get('episode') is not None:
            mtype = 'episode'
        else:
            mtype = 'movie'
        self.switch_episode_group(update=False)  # skip update, keep season/episode from query
        fflog(f' ... {mtype = }, {query = }, {default_query = }')
        self.setProperty('search.media_type', mtype)

    def get_query(self) -> SourceSearchQuery:
        def value(ctl_id: int, key: str) -> Any:
            if key == 'episode_group':
                # episode group ID is stored in self.query
                if self.ffitem is None or self.ffitem.episode_group.current is None:
                    return None
                return self.ffitem.episode_group.current.tmdb_id
            ctl = self.get_control(ctl_id)
            if isinstance(ctl, ControlEdit):
                edit = cast(ControlEdit, ctl)
                val = edit.getText()
            elif isinstance(ctl, ControlButton):
                button = cast(ControlButton, ctl)
                val = button.getLabel2()
            else:
                return self.query.get(key)
            if key in ('season', 'episode'):
                return int(val) if val else None
            if key in ('year',):
                return int(val or 0)
            return val

        # avoid reading controls if labels were set recently, Kodi GUI is slow, controls may not be updated yet
        if monotonic() < self._set_labels_timestamp + const.tune.gui.set_xml_value_lag:
            return self.query
        # read all edit controls
        query = {key: value(ctl_id, key) for ctl_id, key in self.EDIT_CONTROLS.items()}
        return {**self.query, **query}  # type: ignore[reportReturnType]

    # def get_query_labels(self) -> dict[SourceSearchQueryKey, str]:
    #     query_labels: dict[SourceSearchQueryKey, str] = {k: '' if v is None else str(v) for k, v in self.query.items()}  # type: ignore[reportAttributeAccessIssue]
    #     query_labels['episode_group'] = self.NO_EPISODE_GROUP_LABEL
    #     # if self.query.get('episode') is not None:
    #     if (eg := self.query.get('episode_group')) and (group := self.episode_group_by(id=eg)) is not None:
    #         query_labels['episode_group'] = group.name
    #     return query_labels

    def query_label(self, key: SourceSearchQueryKey) -> str:
        """Get label for given query key."""
        if key == 'episode_group':
            if self.ffitem is None or self.ffitem.episode_group.current is None:
                return self.NO_EPISODE_GROUP_LABEL
            return self.ffitem.episode_group.current.name
        return str(self.query.get(key) or '')

    def update_modified(self, *, query: SourceSearchQuery | None = None) -> None:
        if self.init_done:
            if query is None:
                query = self.get_query()
            for name in self.EDIT_CONTROLS.values():
                the_same = self.default_query.get(name) == query.get(name)
                fflog(f'   . {name}: {the_same = }, {self.default_query.get(name) = }, {query.get(name) = }')
                self.setProperty(f'search.modified.{name}', 'false' if the_same else 'true')
            if (ffitem := self.query.get('ffitem')) is not None and ffitem.ref.is_episode:
                if 'episode_offset' in (const.sources_dialog.edit_search.cache or ()) and ffitem.season:
                    d_episode = self.default_query['episode'] or 0
                    q_episode = query['episode'] or 0
                    episode_group = query.get('episode_group')
                    # fflog(f'    >>>>>>>>>>>>>>>>>  {d_episode=}, {q_episode=}, {ffitem.season=}, {episode_group=}')
                    if d_episode and q_episode and d_episode != q_episode:
                        query['episode_offset'].setdefault(query.get('episode_group'), {})[ffitem.season] = q_episode - d_episode
                    else:
                        if episode_offset := query['episode_offset'].get(query.get('episode_group')):
                            del episode_offset[ffitem.season]
                        if not episode_group:
                            query['episode_offset'] = {}

    def set_labels(self) -> None:
        for ctl_id, key in self.EDIT_CONTROLS.items():
            ctl = self.get_control(ctl_id)
            val = self.query_label(key)
            if isinstance(ctl, ControlEdit):
                edit = cast(ControlEdit, ctl)
                edit.setText(val)
            elif isinstance(ctl, ControlButton):
                button = cast(ControlButton, ctl)
                button.setLabel(button.getLabel(), label2=val)
        self._set_labels_timestamp = monotonic()

    def on_init(self) -> None:
        self.set_labels()
        self.setFocusId(self.SCAN_BUTTON)
        self.init_done = True
        self.update_modified(query=self.query)

    def on_action(self, action: Action) -> None:
        """Kodi sent the action."""
        if self.default_action(action):
            return
        action_id = action.getId()
        if action_id in CANCEL_ACTIONS:
            self.close()
        elif action_id in self.UPDATE_ACTIONS:  # and focused_id == NAME_EDIT:
            self.update_modified()

    def on_focus(self, control_id: int) -> None:
        self.update_modified()
        # self.setFocusId(self.EPISODE_GROUPS_BUTTON)

    def on_click(self, control_id: int) -> None:
        from ..ff.item import FFTitleAlias

        def country_codes(locale: Iterable[str]) -> set[str]:
            return {rx.split(loc)[-1].lower() for loc in locale}

        def handle_aliases(key: TitleQueryKey, *, order: list[set[str]]):
            def order_func(a: FFTitleAlias) -> int:
                for idx, codes in enumerate(order):
                    if a.country.lower() in codes:
                        return idx
                return len(order)

            if ffitem := self.query.get('ffitem'):
                if show := ffitem.show_item:
                    ffitem = show
                vtag = ffitem.vtag
                order = [country_codes(o if o else vtag.getCountryCodes()) for o in order]
                orig = FFTitleAlias(title=self.default_query.get(key, ''), country='')
                aliases_dict = {}
                for a in (orig, *sorted(ffitem.aliases_info, key=order_func)):
                    # aliases_dict.setdefault(a.title, f'[B]{a.country.upper()}[/B] | {a.title}' if a.country else a.title)
                    aliases_dict.setdefault(a.title, a)
                aliases = list(aliases_dict.values())
                current = -1
                if control := self.get_control(edit_controls.get(key, -1)):
                    current_title = cast(ControlEdit, control).getText()
                    try:
                        # current = [a.rpartition('|')[2] for a in aliases].index(current_title)
                        current = [a.title for a in aliases].index(current_title)
                    except ValueError:
                        pass  # current not found, title edited manually
                # selected = Dialog().select(L(30528, 'Alternative titles'), aliases, preselect=current)
                selected = AliasDialog(aliases=aliases, preselect=current).doModal()
                if selected >= 0:
                    if control := self.get_control(edit_controls.get(key, -1)):
                        edit = cast(ControlEdit, control)
                        edit.setText(aliases[selected].title)
                        self.update_modified(query={**self.get_query(), key: aliases[selected].title})  # type: ignore[reportArgumentType]

        rx = re.compile(r'[-_]')
        edit_controls = {v: k for k, v in self.EDIT_CONTROLS.items()}  # reverse dict: key -> control_id
        if control_id == self.SCAN_BUTTON:  # Scan button.
            query = self.get_query()
            if self.raise_rescan:
                self.raise_exception(RescanSources(query=query))
            self.close(query)
        elif control_id == self.CANCEL_BUTTON:  # Cancel button.
            self.close()
        elif control_id == self.RESET_BUTTON:  # Reset to defaults button.
            self.reset()
        elif control_id == self.TITLE_ALIASES_BUTTON:  # Title aliases button.
            handle_aliases(key='localtitle', order=[{ffinfo.tmdb.lang or 'pl_PL'}, {'en_US', 'en_GB'}, set()])
        elif control_id == self.EN_TITLE_ALIASES_BUTTON:  # English Title aliases button.
            handle_aliases(key='title', order=[{'en_US', 'en_GB'}, {ffinfo.tmdb.lang or 'pl_PL'}, set()])
        elif control_id == self.ORIG_TITLE_ALIASES_BUTTON:  # Original Title aliases button.
            handle_aliases(key='originalname', order=[set(), {ffinfo.tmdb.lang or 'pl_PL'}, {'en_US', 'en_GB'}])
        elif control_id == self.EPISODE_GROUPS_BUTTON:  # Choose show episode group.
            self.on_episode_groups_button()

    def on_episode_groups_button(self) -> None:
        if self.ffitem is None or self.ffitem.ref.type != 'show':
            return
        groups = ffinfo.get_episode_groups(self.ffitem) or []
        groups.sort(key=lambda g: g.name)
        fflog(f' ... episode groups: {groups = }')
        current_episode_group = None if self.ffitem.episode_group.current is None else self.ffitem.episode_group.current.tmdb_id
        current = 0
        if current_episode_group:
            try:
                current = [g.tmdb_id for g in groups].index(current_episode_group) + 1
            except ValueError:
                fflog(f'episode group current not found: {current_episode_group!r}')
        group_names: list[str | ListItem] = [self.NO_EPISODE_GROUP_LABEL, *(g.name for g in groups)]
        selected = Dialog().select(L(30541, 'Episode group'), group_names, preselect=current)
        if selected >= 0:
            # none selected
            if selected == 0:
                self.query['episode_group'] = None
            # some group selected
            else:
                self.query['episode_group'] = groups[selected - 1].tmdb_id
            self.switch_episode_group()
            self.set_labels()
            self.update_modified(query=self.query)
            # xsleep(0.1)  # allow GUI to update

    def switch_episode_group(self, *, update: bool = True) -> None:
        """Switch to query current episode group."""
        eg = self.query.get('episode_group')
        if (ffitem := self.query.get('ffitem')) is not None and ffitem.ref.is_episode:
            ffinfo.switch_episode_group(ffitem, eg)
            if update:
                self.query['season'] = self.default_query['season'] = ffitem.season
                self.query['episode'] = self.default_query['episode'] = ffitem.episode
                if 'episode_offset' in (const.sources_dialog.edit_search.cache or ()) and ffitem.season:
                    if epoff := self.query['episode_offset'].get(eg):
                        epoff.pop(ffitem.season, None)
                        if not epoff:
                            del self.query['episode_offset'][eg]

    def reset(self) -> None:
        """Reset all fields to default values."""
        self.query = self.default_query.copy()
        self.switch_episode_group()
        self.query['episode_offset'] = self.default_query['episode_offset'] = {}
        for ctl_id, key in self.EDIT_CONTROLS.items():
            ctl = cast(ControlEdit, self.getControl(ctl_id))
            ctl.setText(str(self.query_label(key)))
        self.update_modified(query=self.default_query)
        xsleep(0.1)  # allow GUI to update
        self.update_modified()

    def on_edit_finished(self, control_id: int) -> None:
        """FanFilm sent the edit control finished editing."""
        self.update_modified()

    def episode_groups(self) -> list[FFEpisodeGroup]:
        if ffitem := self.query.get('ffitem'):
            if show := ffitem.show_item:
                ffitem = show
            if ffitem.type == 'show':
                return ffinfo.get_episode_groups(ffitem) or []
        return []


class SourceDialog(BaseDialog):

    XML = 'SourcesDialog.xml'
    CUSTOMIZED_XML = True

    SEARCH_TITLE = 201
    ITEM_LIST = 5000
    # NO_ITEMS_LABEL = 5001
    RESCAN = 5005
    EDIT_SEARCH = 5006
    PROGRESS_CONTROL = 60
    TIME_PROGRESS_CONTROL = 61
    CANCEL_BUTTON = 80  # Cancel button during search

    rx_provider_rename: re.Pattern = re.compile(r'(?P<suffix>\.\d)')
    provider_suffixes = {
        '.1': '¹',
        '.2': '²',
        '.3': '³',
    }

    def __init__(self,
                 *args,
                 sources: SourceFactory,
                 item: FFItem,
                 items: Sequence[Source] | None = None,
                 query: SourceSearchQuery,
                 default_query: SourceSearchQuery | None = None,
                 edit_search: bool = False,
                 **kwargs,
                 ) -> None:
        super().__init__(*args)
        self.source_factory: SourceFactory = sources
        self.ffitem: FFItem = item
        self.ui_lang = kodi_locale()
        self.sources: list[Source] = [] if items is None else self.process_debug_items(items)
        self.lists: list[ListItem] = []  # Created later in on_init to avoid lag
        self.resolved: Source | None = None
        self.focused_constrol_id: int = 0
        self.query = query.copy()
        self.default_query = query.copy() if default_query is None else default_query.copy()
        self._call_edit_search: bool = edit_search
        self._searching: bool = False  # True when actively searching for sources
        self._search_canceled: bool = False  # True if user canceled search
        if items is None:
            self.start_search()  # Start search mode if no items provided

    def process_debug_items(self, items: Sequence[Source]) -> list[Source]:
        item = self.ffitem
        items = [*(Source.from_meta(ffitem=item, meta=it) for it in const.dev.sources.prepend_fake_items),
                 *items,
                 *(Source.from_meta(ffitem=item, meta=it) for it in const.dev.sources.append_fake_items)]
        return items

    def apply_source(self, source: Source, url: str, *, play: Optional[PlayMode] = None) -> None:
        """Apply selected source, return to the sources manager and player."""
        self.resolved = Source(url=url, provider=source.provider, hosting=source.hosting, ffitem=source.ffitem, attr=source.attr,
                               meta=source.meta.copy(), resolved=True)
        if play:
            self.resolved.set_play_mode(play)
        self.close(self.resolved)

    def _set_window_title(self) -> None:
        ffitem = self.ffitem
        ref = ffitem.ref
        # window title
        if label := cast(ControlLabel, self.getControl(self.SEARCH_TITLE)):
            if ref.is_episode:
                if ffitem.episode_group.current is None:
                    title_format = const.sources_dialog.episode_title_format
                else:
                    title_format = const.sources_dialog.episode_group_title_format
            else:
                title_format = const.sources_dialog.movie_title_format
            season = self.query['season'] or ffitem.season or 0
            episode = self.query['episode'] or ffitem.episode or 0
            fmt = Formatter()
            it = FormatObjectGetter(ffitem)
            window_title = fmt.format(title_format, label=ffitem.title,
                                      it=it, item=it, show=FormatObjectGetter(ffitem.show_item), ref=ffitem.ref,  # locale=labels,
                                      title=ffitem.title, season=season, episode=episode, year=ffitem.year, date=ffitem.date)
            label.setLabel(window_title)

    def on_init(self) -> None:
        ffitem = self.ffitem
        # window title
        self._set_window_title()

        # create list items here (not in __init__) to avoid lag before dialog shows
        if not self._searching:
            self.lists = self.list_items(self.sources)

        default_color = settings.getString('default.color.identify2')
        duration_in_mins = int(ffitem.vtag.getDuration() / 60)
        duration_str = str(duration_in_mins) if duration_in_mins else '–'
        the_same = all(self.query.get(k) == self.default_query.get(k) for k in self.query.keys() if k not in ('ffitem', 'episode_offset'))
        fflog(f'the same: {the_same}')
        # for k in self.query.keys():
        #     if k not in ('ffitem', 'episode_offset'):
        #         fflog(f'   . {int(self.query.get(k) == self.default_query.get(k))} {k}: {self.query.get(k)!r} == {self.default_query.get(k)!r}')

        self.setProperty('item.title', str(ffitem.title))
        self.setProperty('item.tvshowtitle', ffitem.vtag.getTvShowTitle())
        self.setProperty('item.year', str(ffitem.year))
        self.setProperty('item.season', str(ffitem.season))
        self.setProperty('item.episode', str(ffitem.episode))
        self.setProperty('item.duration', L(30111, 'Duration: [B]{duration}[/B] minute|||Duration: [B]{duration}[/B] minutes', n=duration_in_mins, duration=duration_str))
        self.setProperty('item.art.fanart', ffitem.getArt('fanart'))

        poster_item: FFItem | None = None
        if ffitem.ref.is_episode:
            if const.sources_dialog.episode_poster == 'show':
                poster_item = ffitem.show_item
            elif const.sources_dialog.episode_poster == 'season':
                poster_item = ffitem.season_item
        if poster_item is None:
            poster_item = ffitem
        self.setProperty('item.art.poster', poster_item.getArt('poster'))
        self.setProperty('item.endtime', endtime(duration_in_mins))
        self.setProperty('item.colored.default', default_color)
        self.setProperty('sources.edit_button', 'true' if const.sources_dialog.edit_search.in_dialog else 'false')
        self.setProperty('sources.edit_modified', 'true' if (const.sources_dialog.edit_search.in_dialog and not the_same) else 'false')
        self.setProperty('panel.visible', 'false')
        # colors MUST be set in known window, HOME is very know :-D
        home = Window(10000)
        home.setProperty('fanfilm.sources_dialog.info.index.color', const.sources_dialog.index_color)
        # items
        self.add_items(self.ITEM_LIST, self.lists)
        self.setProperty('sources.time_progress_visible', 'true' if const.sources_dialog.time_progress else 'false')

        # Set focus based on state
        if self._searching:
            # During search, focus on Cancel button
            self.setFocusId(self.CANCEL_BUTTON)
        elif self.lists:
            self.setFocusId(self.ITEM_LIST)
        else:
            self.setFocusId(self.RESCAN)

        if self._call_edit_search:
            if not self.edit_search():
                self.close()  # force close on "back"

    def list_items(self, items: Iterable[Source]) -> List[ListItem]:
        # Cache translations outside loop for performance
        provider_translations = const.sources.translations.providers.get(self.ui_lang, {})
        hosting_translations = const.sources.translations.hostings.get(self.ui_lang, {})
        return [self.create_list_item(item, provider_translations, hosting_translations) for item in items]

    def handle_source(self, *, play_mode: Optional[PlayMode] = None):
        """Try to resolve source. If success keep busy dialog active."""
        try:
            busy_dialog()

            position = self.item_list_widget.getSelectedPosition()
            auto_select = settings.getBool('auto.select.next.item.to.play')
            play_mode = play_mode

            if auto_select:
                for i in range(position, len(self.sources)):
                    if resolved := self.source_factory.resolve_source(src := self.sources[i]):
                        fflog(f'[WIN] auto select: {position=}, {i=}, {resolved=}')
                        self.apply_source(src, resolved, play=play_mode)
                        break
                    play_mode = None
                else:
                    fflog(f'[WIN] auto select: not resolved ({position=}, len={len(self.sources)})')
                    close_busy_dialog()
            else:
                if resolved := self.source_factory.resolve_source(src := self.sources[position]):
                    fflog(f'[WIN] select: {position=}, {resolved=}')
                    self.apply_source(src, resolved, play=play_mode)
                else:
                    fflog(f'[WIN] select: not resolved ({position=})')
                    close_busy_dialog()
        except Exception:
            fflog_exc()
            close_busy_dialog()

    def handle_rescan(self):
        self.raise_exception(RescanSources(query=self.query))
        self.close()
        # url = f"{control.plugin_urresolve_source.item.ffid}"
        # run_plugin(url)

    def handle_download(self):
        position = self.item_list_widget.getSelectedPosition()
        if resolved := self.source_factory.resolve_source(self.sources[position]):
            if settings.getBool('download.downinfo'):
                downitem = self.sources[position]
                info_content = downitem.get('info', '').strip()
                daudio_type = next((t for t in ['Lektor', 'Dubbing', 'Napisy'] if t in info_content), '')
                dlanguage = downitem.get('language', '').upper()
                dquality = downitem.get('quality', '')
                dinfo2 = downitem.get('info2', '')
                downinfo = f'{daudio_type} | {dlanguage} | {dquality} |{dinfo2}'
            else:
                downinfo = ''
            year = self.ffitem.year
            if settings.getBool('download.downlocaltitle'):
                if self.ffitem.episode:
                    dname = f"{self.ffitem.vtag.getTvShowTitle()}.S{self.ffitem.season:02d}E{self.ffitem.episode:02d}"
                    self.show_item = self.ffitem if self.ffitem.ref.is_show else self.ffitem.show_item
                    year = int(self.show_item.vtag.getYear())
                else:
                    dname = self.ffitem.title
            else:
                if self.ffitem.episode:
                    dname = f"{self.ffitem.vtag.getEnglishTvShowTitle()}.S{self.ffitem.season:02d}E{self.ffitem.episode:02d}"
                    self.show_item = self.ffitem if self.ffitem.ref.is_show else self.ffitem.show_item
                    year = int(self.show_item.vtag.getYear())
                else:
                    dname = self.ffitem.vtag.getOriginalTitle()
            from lib.ff.downloader import download
            thread = Thread(
                target=download,
                args=(
                    dname,
                    year,
                    self.getProperty('item.art.poster'),
                    downinfo,
                    resolved,
                ),
            )
            thread.start()

    @property
    def item_list_widget(self) -> ControlList:
        return self.getControl(self.ITEM_LIST)  # type: ignore[reportReturnType]

    def handle_rebuy(self):
        position = self.item_list_widget.getSelectedPosition()
        src = self.sources[position]
        if resolved := self.source_factory.resolve_source(src, for_resolve={"buy_anyway": True}):
            self.apply_source(src, resolved)

    # Progress dialog compatibility methods (for source searching)

    def start_search(self) -> None:
        """Start search mode - show progress UI."""
        self._searching = True
        self._search_canceled = False
        self.setProperty('searching', 'true')
        self.setProperty('noitem', 'false')  # Hide "no sources" during search
        self.setProperty('search.message', '')
        self.setProperty('search.percent', '0')
        self.setProperty('search.time.percent', '0')
        self.setProperty('search.time.spend', '0')
        self.setProperty('search.time.left', '0')
        self.setProperty('search.time.total', '0')
        # Focus will be set in onInit() when window is shown

    def update(self, percent: int, message: str = '', *, providers: Sequence[str] | None = None) -> None:
        """Update search progress (compatible with SourcesProgressDialog API)."""
        if not self._searching:
            return
        try:
            # Update progress bar
            if ctrl := self.get_control(self.PROGRESS_CONTROL, ControlProgress):
                ctrl.setPercent(percent)
            self.setProperty('search.percent', str(percent))
            # Split message into lines and set properties (like old SourcesProgressDialog)
            lines = message.split('\n') if message else []
            # Clear previous properties
            for prop in ['search.line.0', 'search.line.1', 'search.line.2', 'search.line.3', 'search.line.4', 'search.line.remaining']:
                self.setProperty(prop, '')
            # Set new lines - map lines[0-4] to properties
            for i in range(5):
                self.setProperty(f'search.line.{i}', lines[i] if i < len(lines) else '')
            # Handle remaining lines
            if providers:
                # Build remaining scrapers line
                label = L(32406, 'Remaining providers: %s')
                if len(providers) > 6:
                    remaining = label % len(providers)
                elif len(providers) > 0:
                    remaining = label % (', '.join(self.pretty_provider_name(p) for p in providers))
                else:
                    remaining = ''
                self.setProperty('search.line.remaining', remaining)
        except Exception:
            fflog_exc()

    def update_time(self, spend: float, total: float) -> None:
        """Update search progress time."""
        if not self._searching:
            return
        try:
            percent = 100 * spend / (total or 1)
            percent = max(0, min(100, int(percent)))  # clamp percent to [0, 100]
            if ctrl := self.get_control(self.TIME_PROGRESS_CONTROL, ControlProgress):
                ctrl.setPercent(percent)
            self.setProperty('search.time.percent', str(percent))
            self.setProperty('search.time.spend', str(int(spend)))
            self.setProperty('search.time.left', str(int(total - spend)))
            self.setProperty('search.time.total', str(int(total)))
        except Exception:
            fflog_exc()

    def iscanceled(self) -> bool:
        """Check if search was canceled (compatible with SourcesProgressDialog API)."""
        return self._search_canceled

    def finish_search(self, sources: Sequence[Source]) -> None:
        """Finish search mode - hide progress UI, show results."""
        self.sources = list(sources)
        self._searching = False
        self.setProperty('searching', 'false')
        # Refresh list items with found sources
        items = self.process_debug_items(self.sources)
        self.lists = self.list_items(items)
        self.add_items(self.ITEM_LIST, self.lists)
        self.setProperty('noitem', 'false' if self.lists else 'true')
        if self.lists:
            self.setFocusId(self.ITEM_LIST)

    def edit_search(self) -> bool:
        if query := EditDialog(query=self.query, default_query=self.default_query, raise_rescan=False).doModal():
            self.query = query
            # self._set_window_title()
            self.raise_exception(RescanSources(query=query))
            self.close()
            return True
        return False

    def on_click(self, control_id: int) -> None:
        control_id = control_id
        if control_id == self.ITEM_LIST:
            self.handle_source()
        elif control_id == self.RESCAN:
            if const.sources_dialog.rescan_edit:
                self.edit_search()
            else:
                self.handle_rescan()
        elif control_id == self.EDIT_SEARCH:
            self.edit_search()
        elif control_id == self.CANCEL_BUTTON:
            # Cancel search
            self._search_canceled = True

    def on_action(self, action: Action):
        action_id = action.getId()
        if action_id in CANCEL_ACTIONS:
            # If searching, cancel search instead of closing window
            if self._searching:
                self._search_canceled = True
            else:
                self.close()
        elif action_id in MENU_ACTIONS:
            context_menu_items: ContextMenu = []
            if settings.getBool("downloads"):
                context_menu_items.append((control.lang(30115), self.handle_download))
            position = self.item_list_widget.getSelectedPosition()
            src = self.sources[position]
            for act in src.menu_actions():
                label = src.action_label(act)
                if act == 'buy':
                    context_menu_items.append((label, self.handle_rebuy))
                elif is_play_mode(act):
                    context_menu_items.append((label, partial(self.handle_source, play_mode=act)))
            if len(context_menu_items) >= 1:
                ContextMenuDialog(menu=context_menu_items).doModal()
        # elif self.focused_constrol_id == self.ITEM_LIST and action_id == ACTION_MOVE_LEFT:
        #     Panel().doModal()

    def on_focus(self, control_id: int) -> None:
        control_id = control_id
        self.focused_constrol_id = control_id
        # fflog(f' ######### {control_id = }')
        if control_id == 5011:
            # self.setProperty('panel.visible', 'true')
            Panel().doModal()
            self.setFocusId(self.ITEM_LIST)

    def pretty_provider_name(self, provider: str) -> str:
        return self.rx_provider_rename.sub(lambda mch: self.provider_suffixes.get(mch['suffix'], mch['suffix']), provider)

    def create_list_item(self, item: Source, provider_translations: Dict[str, str], hosting_translations: Dict[str, str]) -> ListItem:
        from ..ff.sources import sources
        hosting = item.hosting
        if not hosting.startswith('plugin.video.'):
            hosting = clear_source(item.hosting)
        info = " ".join(
                    sorted(
                        format_info(item.get("info") or "").strip().lower().split(),
                        key=lambda p: sources.language_type_priority.get(p, 999)
                    )
                )
        fflog(f'[WINDOWS]:  {item=}')
        try:
            label = item['label']
        except KeyError:
            label = f'NONAME from {hosting}'
        li = ListItem(label=label)
        li.setProperty('item.info', info)
        li.setProperty('item.hosting', hosting_translations.get(hosting, hosting))
        li.setProperty('item.colored', item.get('color_identify') or '')
        li.setProperty('item.colored.default', settings.getString("default.color.identify2"))

        size = item.get('size')
        if size is not None:
            li.setProperty('item.size', str(size))

        if item.get('on_account'):
            li.setProperty('item.on_account_expires', item.get('on_account_expires') or 'konto')

        if item.get('no_transfer'):
            li.setProperty('item.no_transfer', str(item.get('no_transfer') or ''))

        for key in ['url', 'language', 'quality']:
            if item.get(key) is None:
                fflog(f'[WINDOWS]: ERORR: No key {key!r} in item {item!r} !!!')
            li.setProperty(f'item.{key}', (item.get(key) or '').strip())
        li.setProperty('item.info2', (item.get('info2') or '').strip())
        # replace provider suffix
        provider = self.pretty_provider_name(item.provider)
        li.setProperty('item.provider', provider_translations.get(provider, provider))

        if not li.getProperty('item.info2'):
            li.setProperty('item.info', li.getProperty('item.info').lstrip('| '))

        if settings.getBool('sources.filename_in_2nd_line'):
            filename = item.get('filename', '')
            if filename:
                filename = unescape(unquote(filename))
                if hosting:
                    filename = f': {filename}'
            li.setProperty('item.id', filename)

        if settings.getBool('icon.external') and (icon := item.get('icon')):
            li.setProperty('item.icon', icon)
        return li

# Fonts:
#   - title / heading: ~40, font40_title (Estuary), font38_title/font40 (Eestouchy), font_topbar (AH2R)
