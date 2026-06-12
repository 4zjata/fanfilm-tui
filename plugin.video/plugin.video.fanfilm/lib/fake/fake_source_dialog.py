
from __future__ import annotations
from typing import Optional, Sequence, TYPE_CHECKING
from functools import partial
from os import get_terminal_size
from lib.ff.item import FFItem
from lib.ff.locales import kodi_locale
from lib.sources import Source
from lib.fake.fake_term import fg, bg, ef, rs, formatting, get_color, print_table
# from lib.ff.types import JsonData
from cdefs import is_play_mode, PlayMode
from const import const
if TYPE_CHECKING:
    from lib.ff.sources import sources as SourceFactory, SourceSearchQuery


class SourceDevDialog:

    def __init__(self,
                 *args,
                 sources: SourceFactory,
                 item: FFItem,
                 items: list[Source],
                 query: SourceSearchQuery,
                 default_query: SourceSearchQuery | None = None,
                 edit_search: bool = False,
                 ) -> None:
        self._item = item
        self._sources = tuple(items or ())
        self._query = query
        self._factory = sources
        self._line_count: int = 0
        self._term_size = get_terminal_size()

    def start_search(self) -> None:
        ...

    def finish_search(self, sources: Sequence[Source]) -> None:
        self._sources = sources
        fake_source_list_dialog(item=self._item, items=self._sources, source_manager=self._factory)

    def do_modal(self) -> Source | None:
        ...

    def destroy(self) -> None:
        ...

    def update(self, percent: int, message: str = '') -> None:
        # print(f'Progress: {percent}% {message}')
        ...

    def update_time(self, spend: float, total: float) -> None:
        ...

    def iscanceled(self) -> bool:
        ...


def fake_source_list_dialog(*, item: FFItem, items: Sequence[Source], source_manager: SourceFactory) -> Optional[Source]:
    """Print sources in terminal and let user make a choice."""
    def fmt_provider(txt, *args, i, **kwargs) -> str:
        if color := get_color(items[i].get('color_identify', '')):
            return f'{fg.white}{bg(*color)}{txt}{rs.all}'
        return f'{fg.white}{txt}{rs.all}'

    def short(text: Optional[str], width: int = 100) -> str:
        text = text or ''
        if len(text) > width:
            a = width // 3
            b = width - a - 1
            text = f'{text[:a]}…{text[-b:]}'
        return text

    def apply_source(source: Source, url: str, *, play: Optional[PlayMode] = None) -> Source:
        """Apply selected source, return to the sources manager and player."""
        resolved = Source(url=url, provider=source.provider, hosting=source.hosting, ffitem=source.ffitem, attr=source.attr,
                          meta=dict(source.meta), resolved=True)
        if play:
            resolved.set_play_mode(play)
        return resolved

    def handle_rebuy() -> Optional[Source]:
        if resolved := source_manager.resolve_source(src, for_resolve={"buy_anyway": True}):
            return apply_source(src, resolved)

    def handle_source(*, play_mode: Optional[PlayMode] = None) -> Optional[Source]:
        if resolved := source_manager.resolve_source(src):
            return apply_source(src, resolved, play=play_mode)

    ui_lang = kodi_locale()
    provider_translations: dict[str, str] = const.sources.translations.providers.get(ui_lang, {})
    hosting_translations: dict[str, str] = const.sources.translations.hostings.get(ui_lang, {})
    cformats = ['', fmt_provider, '', '', '', '', '', '', '', '']
    # vformats = ['>s', '', '', '', '', '', '', '', '']
    table = [[f'{i}.', provider_translations.get(src.provider, src.provider), hosting_translations.get(src.hosting, src.hosting),
              str(src.attr.play), str(src.is_m3u8()), src.get('quality', ''), formatting(src.get('info', '')), short(src.get('filename'), 16),
              src.url, formatting(src.meta.get('info2', '')),
              ]
             for i, src in enumerate(items, 1)]
    print()
    print_table(table, cformats=cformats)  # , vformats=vformats)
    print()

    while True:
        cmd = input('Enter list number: ')
        if cmd[-1:] in 'cmCM' and cmd[:-1].isdigit():
            index = int(cmd[:-1])
            context_menu_items = []
            src = items[index]
            for action in src.menu_actions():
                label = src.action_label(action)
                if action == 'buy':
                    context_menu_items.append((label, handle_rebuy))
                elif is_play_mode(action):
                    context_menu_items.append((label, partial(handle_source, play_mode=action)))
            for i, (label, _) in enumerate(context_menu_items, 1):
                print(f'{i:>}. {label}')
            cmd = input('Enter menu number: ')
            if cmd and cmd.isdigit():
                idx = int(cmd)
                if 0 < idx <= len(items):
                    return items[idx - 1]
                _, handler = context_menu_items[idx]
                if src := handler():
                    return src
        else:
            break
    if cmd and cmd.isdigit():
        idx = int(cmd)
        if 0 < idx <= len(items):
            src = items[idx - 1]
            if url := source_manager.resolve_source(src):
                return apply_source(src, url)
    return None
