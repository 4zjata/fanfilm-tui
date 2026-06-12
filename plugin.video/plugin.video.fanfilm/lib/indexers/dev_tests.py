
from typing import Optional, Union, Sequence, Iterator, TYPE_CHECKING
from typing_extensions import Literal
from time import monotonic
from ..ff.routing import route, RouteObject, url_for, info_for
from ..ff.menu import directory, CMenu
from ..ff.log_utils import fflog
from ..ff.kotools import Notification, xsleep
from ..ff.info import ffinfo
from ..defs import MediaRef, MainMediaType
from .folder import list_directory, item_folder_route, pagination, Folder
from ..kolang import L
from const import const


def print_settings(name: str):
    from ..ff.settings import settings
    for getter in (settings.getBool, settings.getInt, settings.getNumber, settings.getString,
                   settings.getBoolList, settings.getIntList, settings.getNumberList, settings.getStringList):
        try:
            value = getter(name)
        except Exception as exc:
            fflog(f'{name=}: {exc}')
        else:
            fflog(f'{name=}: {getter.__name__}({value!r})')


class DevMenu(RouteObject):
    """Głowne menu FanFilm."""

    def __init__(self) -> None:
        super().__init__()
        self.notif = Notification('FF dev', 'Test continous notification')

    @route('/')
    def home(self) -> None:
        """Create root / main menu."""
        with directory(view='sets') as kdir:
            kdir.action('Videos', self.videos)
            kdir.action('Notification', self.notification)
            kdir.action('Send notif', self.send_notif)
            kdir.action('Video DB info', self.video_db)
            kdir.action('Simple dialog', self.simple_dialog)
            kdir.action('Auth dialog', self.auth_dialog)
            kdir.folder('Add-to stuff', self.add_to)
            kdir.action('Test GUI', self.test_gui)
            kdir.folder('Source dialog', self.source_dialog)
            kdir.folder('Library', self.library)
            kdir.action('Empty folder', self.empty)
            kdir.action('Mixed IDs', self.mixed_ids)
            kdir.folder('Ratings', self.ratings)
            kdir.action('Test settings engine', self.test_settings)
            kdir.action('Nothing', kdir.no_op)  # do nothing
            kdir.action('Exit', self.exit)  # The Last One

    @route
    def exit(self) -> None:
        """The iterpreter exit. Reload sources in next plugin call."""
        import sys
        sys.exit()

    @route
    def empty(self) -> None:
        """Folder with no items."""
        with directory(view='sets'):
            pass

    @route
    def videos(self) -> None:
        from ..main import fake_play_movie
        with directory(view='videos') as kdir:
            # kdir.play('Fake video', url_for(fake_play_movie, ref=MediaRef('movie', 909_000_001)))
            kdir.play('Fake video', url_for(fake_play_movie, ffid=100_000_646))
            ffitem = ffinfo.find_item(MediaRef('movie', 100_000_646))
            if ffitem:
                ffitem.label = 'Fake video'
                ffitem.url = str(url_for(fake_play_movie, ffid=ffitem.ref.ffid))
                kdir.add(ffitem)

    @route
    def notification(self) -> None:
        from ..ff.kotools import Notification, xsleep
        if 1:
            # xsleep(1.025)
            with Notification('FF dev', 'This is a test notification') as notif:
                fflog('[DEV] Notification start')
                xsleep(3)
                fflog('[DEV] Notification stop')
                xsleep(2)
        if 0:
            fflog('[DEV] Notification show 3s')
            self.notif.show()
            xsleep(3)
            fflog('[DEV] Notification show 5s (re-show)')
            # self.notif.message = 'Re-shown notification'
            self.notif.show(message='Continue notification')
            xsleep(8)
            fflog('[DEV] Notification show 5s (re-show)')
            # self.notif.message = 'Re-shown notification'
            self.notif.show(message='Re-show notification', interval=2)
            xsleep(5)
            fflog('[DEV] Notification script exit')

    @route
    def simple_dialog(self) -> None:
        from ..windows.dialogs import SimpleDialog, ButtonBox
        win = SimpleDialog('label|text', label='Bla bla bla', text="...", buttons=ButtonBox.YES_NO)
        result = win.doModal()
        fflog(f'[DEV] Dialog result: {result!r}')
        Notification('FF dev', f'Dialog result: {result!r}').show()

    @route('/auth/{__what}')
    def auth_dialog(self, what: Literal['tmdb', 'trakt'] = 'trakt') -> None:
        from ..windows.site_auth import SiteAuthWindow
        from ..ff.control import dataPath as DATA_PATH
        from pathlib import Path
        import segno
        if what == 'tmdb':  # TMDB
            token = ('eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI5MDgwNjQ3ZmYxNjQ3ZiIsIm5iZiI6mYwMmI3NDc4ZGI5MDBmMTIzYMTc0MzU5Mjc2MS44MTkzLCJqdGkiOiI2N2VkMW'
                     'QzOTM0YzZjOTQxMDhkMGJmZmUiLCJzY29wZXMiOlsRva2VuIl0sInZlcnNpb24icGVuZGluZ19yZXF1ZXN0X3iOjIsImV4cCI6MTc0MzU5MzY2MX0.U8Tq8eLlC3e0Tb5O1P2lOYPyKdsTvgEufWG4tQGWnOk')
            verification_url = f'https://www.themoviedb.org/auth/access?request_token={token}'

            code_hash = f'{hash(verification_url):08x}'
            icon = Path(DATA_PATH) / f'tmp/dev-auth-qrcode.{code_hash}.png'
            icon.parent.mkdir(parents=True, exist_ok=True)
            qrcode = segno.make(verification_url)
            qrcode.save(str(icon), scale=const.tmdb.auth.qrcode_size)
        else:  # Trakt
            token = '12345678'
            verification_url = f'https://trakt.tv/activate/{token}'
            code_hash = f'{hash(verification_url):08x}'
            icon = Path(DATA_PATH) / f'tmp/dev-auth-qrcode.{code_hash}.png'
            icon.parent.mkdir(parents=True, exist_ok=True)
            qrcode = segno.make(verification_url)
            qrcode.save(str(icon), scale=const.trakt.auth.qrcode.size)
        modal = False
        win = SiteAuthWindow(code=token, url=verification_url, icon=icon, modal=modal)
        if modal:
            win.update(100)
            result = win.doModal()
        else:
            win.doModal()  # this dialog is modeless (!)
            try:
                for progress in range(0, 101, 5):
                    # Current timestamp.
                    now = monotonic()
                    # check if dialog is canceled
                    if win.dialog_is_canceled():
                        break
                    # check if user authorized this device
                    # if access := self._get_access_token(device_code, cred):
                    #     break
                    # update progress-bar
                    win.update(int(progress))
                    # sleep given interval
                    xsleep(0.5)
            except KeyboardInterrupt:
                print('''\nCancelled. Enter '0'.\n''')
            finally:
                # finish - close dialog
                result = win.result()
                win.destroy()
                del win
        icon.unlink()
        fflog(f'[DEV] Dialog result: {result!r}')
        Notification('FF dev', f'Dialog result: {result!r}').show()

    @route
    def add_to(self) -> None:
        with list_directory(view='sets') as kdir:
            kdir.action('Add-to dialog', self.add_to_dialog)
            kdir.action('New-list dialog', self.new_list_dialog)
            kdir.folder('Example items', info_for(self.item_examples))

    def _item_examples(self) -> Sequence[MediaRef]:
        from ..defs import VideoIds
        tmdb = VideoIds.ffid_from_tmdb_id
        items = [
            MediaRef('movie', tmdb(78)),
            MediaRef('show', tmdb(119051)),
            MediaRef('show', tmdb(1434), 2),
            MediaRef('show', tmdb(2734), 3, 4),
            MediaRef('person', tmdb(190)),
            MediaRef('collection', tmdb(726871)),
            MediaRef('genre', tmdb(12)),
            # MediaRef('list', 123),  # not-supported
        ]
        return items

    @item_folder_route
    def item_examples(self) -> Sequence[MediaRef]:
        return self._item_examples()

    @route
    def add_to_dialog(self) -> None:
        from ..windows.add_to import AddToDialog, ListInfo
        from ..ff.control import notification
        items = self._item_examples()
        win = AddToDialog(items=items)
        lst: Optional[ListInfo] = win.doModal()
        fflog(f'Add-to dialog {lst=}')
        if lst:
            num = lst.add_items(items=items)
            notification(L(30476, 'Add to {pointer} list').format(pointer=lst.service.pointer),
                         L(30478, 'Added {n} item to {name}|||Added {n} items to {name}', n=num, name=lst.name), visible=True)

    @route
    def new_list_dialog(self) -> None:
        from ..windows.new_list import NewWindowDialog
        win = NewWindowDialog()
        result = win.doModal()
        fflog(f'New-list dialog {result=}')

    @route
    def video_db(self) -> None:
        import sys
        from ..ff.kodidb import video_db
        # video_db.get_players()
        # video_db.get_player_item()
        x = tuple(video_db.get_library('movie'))
        fflog(f'ITEMS count {len(x)}')
        if x:
            fflog(f'First item {x[0]}')
        sys.exit(0)

    @route
    def send_notif(self) -> None:
        # import json
        # import xbmc
        # req = json.dumps({
        #     'id': 123,
        #     'jsonrpc': '2.0',
        #     'method': 'JSONRPC.NotifyAll',
        #     'params': {
        #         'sender': 'Dupa',
        #         'message': 'Blada',
        #         'data': {'a': 42},
        #     },
        # })
        # fflog(f'RPC {req = }')
        # resp = xbmc.executeJSONRPC(req)
        # fflog(f'RPC {resp = }')
        from ..ff.kotools import KodiRpc
        req  = {'dupa': 'blada'}
        fflog(f'RPC {req  = }')
        T = monotonic()
        resp = KodiRpc().service_call('ServicePing', req, timeout=3)
        T = monotonic() - T
        fflog(f'RPC {resp = }  [{T:.3f}s]')

    @route
    def test_gui(self) -> None:
        import xbmcgui
        from ..windows.base import BaseDialog

        class Test(BaseDialog):
            XML = 'Test.xml'

            def on_action(self, action: 'xbmcgui.Action') -> None:
                aid = action.getId()
                if xbmcgui.ACTION_JUMP_SMS2 <= aid <= xbmcgui.ACTION_JUMP_SMS9:
                    btn = aid - xbmcgui.ACTION_JUMP_SMS2 + 2
                elif xbmcgui.REMOTE_0 <= aid <= xbmcgui.REMOTE_9:
                    btn = aid - xbmcgui.REMOTE_0
                else:
                    btn = None
                name = ''
                for k in dir(xbmcgui):
                    if k.isupper() and getattr(xbmcgui, k) == aid:
                        name = k
                        break
                fflog(f'[Test] Action: code={action.getId()}, digit={btn}')
                self.get_control(1000).setLabel(f'Action: code={action.getId()}, button={action.getButtonCode()}, name={name}\nDIGIT={btn}')
                super().on_action(action)

        Test().doModal()

    @route
    def library(self) -> None:
        from ..ff.kodidb import video_db
        video_db.get_library('movie')
        with list_directory(view='sets') as kdir:
            kdir.folder('Mixed', info_for(self.library_items))
            for media in ('movie', 'show', 'season', 'episode'):
                kdir.folder(media.capitalize(), info_for(self.library_items, media=media))

    @item_folder_route('/library/media/{__media}')
    @pagination(20)
    def library_items(self, media: Optional[Literal['movie', 'show', 'season', 'episode']] = None):
        from ..ff.kodidb import video_db
        return Folder(video_db.get_library_ffitems(media), alone=True)
        # items = video_db.get_library_dict(media)
        # return Folder([it.ffitem() for ref, it in items.items() if not media or media == ref.real_type], alone=True)

    @route
    def test_settings(self) -> None:
        from ..ff.settings import settings
        for name in (
            # 'library.service.update',
            # 'library.days_delay',
            'movie.download.path',
        ):
            print_settings(name)
            fflog(f'DEF: {settings.definitions[name]}')
            val = settings.eval(f'0 or {name}')
            # val = settings.eval(f'0 or {{{name}}}')
            fflog(f'VAL: {val}')
        print(settings.eval('{schedCleanMetaCache}'))

    @route('/source_dialog')
    @route('/source_dialog/{media}/{tmdb}')
    def source_dialog(self, media: Union[MainMediaType, Literal['alias', 'progress'], None] = None, tmdb: int = 0, edit: bool = False) -> None:
        from ..windows.sources import SourceDialog, EditDialog
        from ..ff.sources import Source, SourceSearchQuery, SourceResolveKwargs, sources as SourceFactory, RescanSources, FFItem
        from ..ff.db.search import sources_edit_db

        class FakeSources:
            def resolve_source(self, item: 'Source', /, info: bool = False, for_resolve: 'SourceResolveKwargs | None' = None, **kwargs) -> 'str | None':
                return item.url

        def fake_source(ffitem: 'FFItem') -> 'Iterator[Source]':
            for i in range(5):
                yield Source.from_meta(ffitem=ffitem, meta={
                    'direct': True,
                    'filename': f'video_bla_bla_{i+1:02d}.mp4',
                    'info': f'| kicha {i+1:02d} | 1 TB',
                    'language': 'pl',
                    'on_account': False,
                    'quality': '720p',
                    'size': '1 TB',
                    'hosting': 'pustka',
                    'url': f'ftp://dupa/zbita/{i+1:02d}',
                    'provider': 'FAKE',
                    'info2': 'NIC | ZUPEŁNIE',
                    'color_identify': 'FF202020',
                    'debrid': '',
                    'label': '[COLOR FF57A4CB]00 | [LIGHT][B]KICHA[/B][/LIGHT] | [B]PL[/B] | DUPA',
                })

        def make_query(ffitem: 'FFItem') -> SourceSearchQuery:
            ref = ffitem.ref
            ffitem.copy_from(ffitem.season_item, ffitem.show_item)
            if ref.type == 'show' and (sh_item := ffitem.show_item):
                vtag = sh_item.getVideoInfoTag()
                premiered = sh_item.date
            else:
                vtag = ffitem.getVideoInfoTag()
                premiered = ffitem.date
            # get aliases (new way)
            if show_ffitem := ffitem.show_item:
                ffinfo.get_title_aliases(show_ffitem)
            else:
                ffinfo.get_title_aliases(ffitem)
            # `title` and `show_title` should be in English.
            title = vtag.getTitle()
            en_title = vtag.getEnglishTitle() or vtag.getOriginalTitle() or title
            query: SourceSearchQuery = {
                'title': en_title,
                'localtitle': title,  # title is in api locale
                'year': vtag.getYear(),
                'imdb': vtag.getUniqueID('imdb'),
                'tmdb': vtag.getUniqueID('tmdb'),
                'season': ffitem.season,
                'episode': ffitem.episode,
                'tvshowtitle': en_title if ref.type == 'show' else '',
                'premiered': str(premiered or ''),
                'originalname': vtag.getOriginalTitle(),
                'episode_group': None,
                'episode_offset': {},
                'ffitem': ffitem,
            }
            return query

        if not media:
            with directory(view='sets') as kdir:
                for mtype, tmdb in (('movie', 78), ('show', 119051),  # Blade Runner, Wednesday
                                    ('movie', 1311031), ('show', 85937),  # Demon Slayer
                                    ('movie', 262391),  # Za jakie grzechy, dobry Boże? (2014)
                                    ):
                    if ffitem := ffinfo.find_item(MediaRef.from_tmdb(mtype, tmdb)):
                        label = f'{mtype.capitalize()}: {ffitem.title}'
                        if mtype == 'show':
                            label += ' (S02E05)'
                        menu = [CMenu('Edit', info_for(self.source_dialog, media=mtype, tmdb=tmdb, edit=True))]
                        kdir.action(label, info_for(self.source_dialog, media=mtype, tmdb=tmdb), menu=menu)
                kdir.action('= Alias Dialog =', info_for(self.source_dialog, media='alias'))
                kdir.action('= Progress Dialog =', info_for(self.source_dialog, media='progress'))

            return

        if media == 'progress':
            from threading import Thread
            from contextlib import suppress
            from ..ff.threads import xsleep

            # Search in background thread while window is shown
            def search_thread(win: SourceDialog) -> None:
                # NOW enable search mode (progress bar appears)
                win.start_search()
                # Search for sources
                for t in range(0, 101, 1):
                    i = int(100 - ((100 - t)/100)**3 * 100)
                    win.update(i, f'Bla bla bla... {i}% at {t}T')
                    win.update_time(t, 100)
                    xsleep(0.1)
                # Set found sources to window
                win.finish_search([])

            if ffitem := ffinfo.find_item(MediaRef.from_tmdb('movie', 78)):  # Demon Slayer
                query = make_query(ffitem)
                defaults = query.copy()
                win = SourceDialog(sources=FakeSources(), item=ffitem, items=[], query=query, default_query=defaults)
                thread = Thread(target=search_thread, args=(win,), name="SourcesSearch")
                thread.start()

                # Show window (blocks until user closes or selects source)
                # Search thread updates window in background
                try:
                    source_to_play = win.do_modal()
                    thread.join(timeout=1)  # wait for thread to finish (should be done already)
                finally:
                    with suppress(Exception):
                        win.destroy()
                    win = None  # remove reference to closed window
            return

        if media == 'alias':
            from ..windows.sources import AliasDialog
            if ffitem := ffinfo.find_item(MediaRef.from_tmdb('show', 85937)):  # Demon Slayer
                ffinfo.get_title_aliases(ffitem)
                win = AliasDialog(aliases=ffitem.aliases_info)
                result = win.doModal()
                fflog(f'Alias dialog {result=}')
            return

        se = [2, 5] if media == 'show' else []
        ffitem = ffinfo.find_item(MediaRef.from_tmdb(media, tmdb, *se))
        fflog(f'[DEV][source_dialog] found {ffitem=}')
        if not ffitem:
            Notification('FF dev', f'Item not found: {media} / {tmdb}').show()
            return
        factory: SourceFactory = FakeSources()  # type: ignore
        sources: 'list[Source]' = list(fake_source(ffitem))

        query = make_query(ffitem)
        defaults = query.copy()
        if const.sources_dialog.edit_search.cache and (se := sources_edit_db.get(ffitem)):
            fflog(f'Loaded saved search edits: {se}')
            saved_data = se.data
            query.update(saved_data)  # type: ignore[reportArgumentType]
        if (eg := query.get('episode_group')):
            ffinfo.switch_episode_group(ffitem, eg)
            if ffitem.episode_group.current is not None:
                defaults['season'] = ffitem.season
                defaults['episode'] = ffitem.episode
                if const.sources_dialog.edit_search.show_granularity == 'show':
                    query['season'] = ffitem.season
                    query['episode'] = ffitem.episode
                elif const.sources_dialog.edit_search.show_granularity == 'season':
                    query['episode'] = ffitem.episode
        elif eg == '':
            query['episode_group'] = None
        if edit:
            try:
                EditDialog(query=query, default_query=defaults).doModal()
            except RescanSources as exc:
                fflog(f'Edit dialog result: {exc.query}')
                if exc.query is not None:
                    query = exc.query
                    if allowed := const.sources_dialog.edit_search.cache:
                        if query == defaults:
                            sources_edit_db.delete(ffitem)
                        else:
                            sources_edit_db.set(ffitem, {k: v for k, v in query.items() if k in allowed})
        else:
            while True:
                win = SourceDialog(sources=factory, item=ffitem, items=sources, query=query, default_query=defaults)
                try:
                    result = win.doModal()
                    fflog(f'Source dialog {result=}')
                except RescanSources as exc:
                    fflog(f'Source dialog rescanning sources: {exc.query}')
                    if exc.query is not None:
                        query = exc.query
                else:
                    if allowed := const.sources_dialog.edit_search.cache:
                        if query == defaults:
                            sources_edit_db.delete(ffitem)
                        else:
                            sources_edit_db.set(ffitem, {k: v for k, v in query.items() if k in allowed})
                    break

    @route
    def mixed_ids(self) -> None:
        """Test mixed media IDS (mixed services)."""
        from ..defs import VideoIds
        from ..ff.item import FFItem
        media: 'list[tuple[MediaRef, str]]' = [
            (MediaRef.from_tmdb('movie', 78), 'TMDB Movie (Blade Runner)'),
            (MediaRef.from_tmdb('show', 119051), 'TMDB Show (Wednesday)'),
            (MediaRef.movie(VideoIds(imdb='tt0068646').ffid), 'IMDB Movie (The Godfather)'),
            (MediaRef.tvshow(VideoIds(imdb='tt0141842').ffid), 'IMDB Show (The Sopranos)'),
            (MediaRef('', VideoIds(imdb='tt0110912').ffid), 'IMDB Movie (Pulp Fiction) – guess type'),
            (MediaRef('', VideoIds(imdb='tt2560140').ffid), 'IMDB Show (Shingeki no Kyojin/Titans) – guess type'),
            (MediaRef.movie(VideoIds(trakt=458837).ffid), 'Trakt Movie (Nobody)'),
            (MediaRef.tvshow(VideoIds(trakt=173996).ffid), 'Trakt Show (Wednesday)'),
            # (MediaRef.movie(VideoIds(tvdb=0).ffid), 'tvdb Movie (Iron Man)'),  # tvdb has no movies ID (but has movie records on the site)
            (MediaRef.tvshow(VideoIds(tvdb=81189).ffid), 'tvdb Show (Breaking Bad)'),
        ]
        ids, descrs = zip(*media)
        print(', '.join(map(str, ids)))
        with directory() as kdir:
            # xx = ffinfo.get_items(ids, keep_missing=True)
            for it, descr in zip(ffinfo.get_items(ids, keep_missing=True), descrs):
                if it is None:
                    it = FFItem(f'MISS: {descr}', mode=FFItem.Mode.Separator)
                it.vtag.setPlot(f'{descr}\n—————\n{it.vtag.getPlot()}')
                kdir.add(it, url=kdir.no_content)

    @route('/ratings/{__ref}')
    def ratings(self, *, ref: Optional[MediaRef] = None) -> None:
        """Test ratings display."""
        from ..ff.info import ffinfo
        from ..ff.ratings import RatingService, MediaRating, all_rating_services

        services = {**all_rating_services, **{srv.name: srv for srv in (
            # --- TESTS and DEBUG ---
            RatingService(name='x100', min=1, max=100),
            RatingService(name='bin', min=0, max=1),
            RatingService(name='float', min=0, max=1, step=.25),
            RatingService(name='neg', min=-5, max=5),
            RatingService(name='rev', min=10, max=1, step=-1),
            RatingService(name='dup1', min=1, max=10, step=-1),
            RatingService(name='dup2', min=1, max=10, step=-1),
        )}}
        media = [
            (MediaRef.from_tmdb('movie', 550),        # Fight Club
             [
                 MediaRating(5, service=services['trakt']),
                 MediaRating(10, service=services['tmdb']),
                 MediaRating(5, service=services['mdblist']),
                 MediaRating(50, service=services['x100']),
                 MediaRating(1, service=services['bin']),
                 MediaRating(.5, service=services['float']),
                 MediaRating(0, service=services['neg']),
                 MediaRating(2, service=services['rev']),
                 MediaRating(5, service=services['dup1']),
                 MediaRating(5, service=services['dup2']),
             ]),
            (MediaRef.from_tmdb('movie', 278),        # The Shawshank Redemption
             []),
            (MediaRef.from_tmdb('show', 1399),        # Game of Thrones
             []),
            (MediaRef.from_tmdb('show', 66732),       # Stranger Things
             []),
            (MediaRef.from_tmdb('show', 66732, 1),       # Stranger Things S01
             []),
            (MediaRef.from_tmdb('show', 66732, 1, 5),       # Stranger Things S01E05
             []),
            # MediaRef.from_tmdb('movie', 1),          # missing
            # []),
        ]

        if ref:
            from ..windows.ratings import RatingsDialog
            if ffitem := ffinfo.find_item(ref):
                ratings = next((m[1] for m in media if m[0] == ref), [])
                # fflog.error(f'RAT: {len(ratings)=} for {ref=}')
                result = RatingsDialog(ffitem=ffitem, ratings=ratings, services=services).doModal()
                fflog(f'Ratings dialog result: {result!r}')
            return

        refs = [m[0] for m in media]
        with list_directory(view='sets') as kdir:
            for it in ffinfo.get_items(refs):
                label = it.title
                if it.ref.is_season:
                    label = f'{it.show_item.title} - Season {it.season}'
                elif it.ref.is_episode:
                    label = f'{it.show_item.title} - {it.season}x{it.episode:02} - {it.title}'
                it.label = label
                it.mode = it.Mode.Command
                kdir.add(it, url=info_for(self.ratings, ref=it.ref))
