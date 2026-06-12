
from __future__ import annotations
import signal
import os
import sys
from pathlib import Path
from enum import IntEnum
from typing import TYPE_CHECKING
from attrs import define
if TYPE_CHECKING:
    from typing_extensions import Self
    from argparse import Namespace


class AlarmInterrupt(RuntimeError):
    pass


class ServiceMode(IntEnum):
    """How to use FF service."""
    # Do not use service at all (use FakeServiceClient).
    NONE = 0
    # Comunicate with external service (kodi, another python process).
    KODI = 1
    # Launch service (fork process).
    FULL = 2
    # Launch only service not the plugin.
    ONLY = 3

    @classmethod
    def parse(cls, value: str) -> Self:
        if value.isdecimal():
            return cls(int(value))
        return cls[value.upper()]


@define(kw_only=True)
class ServiceHander:
    mode: ServiceMode
    pid: int | None = None


class Alarm:

    def __init__(self) -> None:
        self.prev = ...

    def _handle(self, sig, frame):
        if self.prev is not ...:
            signal.signal(signal.SIGALRM, self.prev)
        raise AlarmInterrupt()

    def __call__(self, seconds: int) -> None:
        prev = signal.signal(signal.SIGALRM, self._handle)
        if self.prev is ...:
            self.prev = prev
        signal.alarm(seconds)

    def cancel(self) -> None:
        if self.prev is not ...:
            signal.signal(signal.SIGALRM, self.prev)
        signal.alarm(0)


alarm = Alarm()


def signal_name(sig: int) -> str:
    """Get signal name from number, or 'EXIT' for 0."""
    if not sig:
        return 'EXIT'
    try:
        return signal.Signals(sig).name
    except ValueError:
        return f'UNKNOWN({sig})'


def _service_process():
    from threading import enumerate as threading_enumerate

    def _no_break(sig: int = 0, frame=None):
        print(f' ** SERVICE {signal_name(sig)}: I know, you want to stop, be patient')

    def _stop_service(sig: int = 0, frame=None):
        # raise KodiExit()
        threads = ', '.join(str(th) for th in threading_enumerate())  # LOG
        print(f' ** Service signal {sig} ({signal_name(sig)}) received, stopping...\n    with threads {threads}')
        if TYPE_CHECKING:  # import for type checker only
            from ..fake.xbmc import _exit_kodi
        else:  # real import
            from xbmc import _exit_kodi
        _exit_kodi()
        # signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, _no_break)

    if pid := os.fork():
        # parent process, return child PID
        return pid

    # child (service) process
    if TYPE_CHECKING:  # import for type checker only
        from ..fake import xbmc
    else:  # real import
        import xbmc
    from ..fake.fake_api import PID_NAMES
    from .. import service
    from ..service import main
    from ..service.exc import ExitBaseException

    signal.signal(signal.SIGTERM, _stop_service)
    signal.signal(signal.SIGINT, _stop_service)

    xbmc._Service.start_service(mode=xbmc._Service.SERVER)
    PID_NAMES[os.getpid()] = 'service'
    service.SERVICE = True

    try:
        print(f' ** Run service in process {os.getpid()}, startup sync: {main.START_SYNC_ON_START}')
        main.run()
        print(' ** Service finished gracefully')
    # except KodiExit:
    #     pass
    except (KeyboardInterrupt, AlarmInterrupt, ExitBaseException):
        try:
            _stop_service()
        except KeyboardInterrupt:
            print(' ** Service interrupted')
            return
    finally:
        print(' ** Service gone')

    threads = ', '.join(str(th) for th in threading_enumerate())  # LOG
    print(f' ** Service process exit with threads {threads}')
    os._exit(0)  # child (service) process finish


def start_service():
    from time import sleep
    import requests
    from ..service.client import service_client
    pid = _service_process()
    if service_client._url:
        for i in range(10):
            try:
                print(f' ** Try service HTTP ({i=}, {pid=})')
                if requests.get(f'{service_client._url}info').status_code == 200:
                    print(f' ** Service HTTP is ready ({i=}, {pid=})')
                    break
            except OSError:
                pass
            if not i:
                print(f' ** Waiting for service HTTP... ({i=}, {pid=})')
            sleep(.1)
    return pid


def stop_service(service: int | None):
    if TYPE_CHECKING:  # import for type checker only
        from ..fake.xbmc import _exit_kodi
    else:  # real import
        from xbmc import _exit_kodi
    _exit_kodi()
    if not service:
        print(' ** Service already stopped')
        return
    print(' ** Stopping service...')
    try:
        alarm(6)
        os.waitpid(service, 0)
        alarm.cancel()
        print(' ** Service is stopped')
        return
    except AlarmInterrupt:
        pass
    os.kill(service, signal.SIGINT)
    try:
        alarm(3)
        os.waitpid(service, 0)
        alarm.cancel()
    except AlarmInterrupt:
        os.kill(service, signal.SIGTERM)
        try:
            alarm(10)
            os.waitpid(service, 0)
            alarm.cancel()
        except AlarmInterrupt:
            print(f' ** Waiting too long for service stop ({service}), kill em all')
            os.kill(service, signal.SIGKILL)


def parse_args():
    # from argparse import ArgumentParser
    # from .. import cmdline_argv
    from ..ff.cmdline import DebugArgumentParser
    from os import pathsep

    # p = ArgumentParser()
    p = DebugArgumentParser(add_help=False)
    p.add_argument('url', nargs='?', default='/', help='plugin url (or just path) [plugin://host]/path[?query]')
    p.add_argument('handle', nargs='?', default='1', help='plugin handle')
    p.add_argument('query', nargs='?', default='', help='plugin query, if not in url')
    p.add_argument('resume', nargs='?', default='resume:false', help='plugin resume?')
    p.add_argument('--lang', help='language (default: pl_PL)')
    p.add_argument('--api-lang', help='override override media API language (default from settings)')
    p.add_argument('--const', metavar='ID[:TYPE]=VALUE', action='append', help='override const value, only simple types are supported')
    p.add_argument('--info-label', metavar='NAME=VALUE', action='append', help='override global info label (`Container.PluginName=X` for emulate widget)')
    p.add_argument('--setting', metavar='ID=VALUE', action='append', help='override settings value')
    p.add_argument('--readonly-settings', action='store_true', help='do not write the settings')
    p.add_argument('--import-path', metavar='PATH', action='append', help='add python import path')
    p.add_argument('--import-addon', metavar='ADDON_ID', action='append', help='add kodi addon import path')
    p.add_argument('--single-provider', metavar='PROVIDER', help='use single source provider (w/o threads)')
    p.add_argument('-T', '--tui', choices=('simple', 'main', 'tree'), default='simple', help='TUI variant')
    p.add_argument('-r', '--run', action='store_true', help='run in loop')
    p.add_argument('--service-mode', dest='service', type=ServiceMode.parse, choices=list(ServiceMode),
                   metavar=f'{{{",".join(e.name.lower() for e in ServiceMode)}}}', default=ServiceMode.FULL, help='skip service')
    p.add_argument('-S', '--no-service', action='store_const', dest='service', const=ServiceMode.NONE, help='skip service')
    p.add_argument('--no-service-startup-sync', action='store_true', help='skip service sync on startup')
    p.add_argument('--video-db', nargs='?', metavar='PATH', const=True,
                   help='path to kodi MyVideos.db or without path to use default')
    p.add_argument('-K', '--kodi-path', metavar=f'PATH[{pathsep}PATH]...',
                   help='path to KODI user data and optional additional installation paths')
    # p.add_argument('--json-rpc-addr', metavar='[HOST]:[PORT]', help='JSON RPC addres [localhost:9090]')
    args, _ = p.parse_known_args()
    # print(f'Parsed args: {args}  !!!', file=sys.stderr)

    pp = DebugArgumentParser(parents=[p], description='FF3 console')
    if args.tui == 'simple':
        pp.add_argument('-m', '--menu', metavar='INDEX|*', type=lambda s: -1 if s == '*' else int(s),
                        help='show context menu at given index, use `*` for all')
        pp.add_argument('-i', '--info', metavar='INDEX', type=lambda s: -1 if s == '*' else int(s),
                        help='show info at given index, use `*` for all')
        pp.add_argument('-x', '--extra-info', metavar='INDEX', type=lambda s: -1 if s == '*' else int(s),
                        help='show extra debug info at given index, use `*` for all')
        pp.add_argument('-X', '--xxx', action='store_true', help='tmp debug test xxx...')
        pp.add_argument('-F', '--more-folder-info', action='store_true', help='show more folder info (table view)')
        # print(pp.parse_args()); exit()
    return pp.parse_args()
    # return p.parse_args(cmdline_argv[1:])


def predefine():
    """Check options on startup and patch FF (set some defaults)."""
    args = parse_args()

    if args.import_path:
        from sys import path
        for p in args.import_path:
            if p not in path:
                path.append(p)
    if args.lang or args.api_lang:
        from ..fake.fake_api import set_locale
        set_locale(args.lang, api=args.api_lang)
    if args.const:
        from cdefs import constdef
        from const import const
        from ..ff.tricks import super_get_attr, super_set_attr
        from ..ff.types import find_arg_descr, eval_annotations
        constdef._locked = False  # type: ignore[reportFunctionMemberAccess]
        for item in args.const:
            key, _, value = item.partition('=')
            key, _, typ = key.partition(':')
            prefix, _, key = key.partition('.')
            if prefix != 'const':
                print(f' ** WARNING: {prefix}.{key} not found for override')
                continue
            try:
                old = super_get_attr(const, key)
            except AttributeError:
                print(f' ** WARNING: const.{key} not found for override')
                continue
            if getattr(old, '_const_def', None):
                print(f' ** WARNING: const.{key} is class, cannot override')
                continue
            if not typ:
                typ = type(old)
            typ = eval_annotations(typ)
            ad, meta = find_arg_descr(typ)  # validate type
            if ad is None:
                print(f' ** WARNING: const.{key} override type {typ} not recognized')
                continue
            super_set_attr(const, key, ad.load(value, typ, meta=meta))
        constdef._locked = True  # type: ignore[reportFunctionMemberAccess]
    if args.info_label:
        from ..fake.fake_api import INFO_LABEL
        INFO_LABEL.update({k: v for k, _, v in (x.partition('=') for x in args.info_label)})
    if args.readonly_settings:
        from ..fake import fake_api
        fake_api.SETTINGS_READONLY = True
    if args.setting:
        from ..fake.fake_api import SETTINGS
        SETTINGS.update({k: v for k, _, v in (x.partition('=') for x in args.setting)})
    if args.video_db:
        from pathlib import Path
        from ..ff.kodidb import video_db, vdb_ver
        from ..fake.fake_api import KODI_PATH
        if args.video_db is True:
            args.video_db = KODI_PATH / 'userdata' / 'Database' / f'MyVideos{vdb_ver}.db'
        video_db.path = Path(args.video_db)
    if args.service is ServiceMode.NONE:
        from .. import service
        # No service, fake service client (base direct support)
        from ..service.fake_client import FakeServiceClient
        from ..service import client
        service.SERVICE = True  # direct access, like in the service
        client.service_client = FakeServiceClient()
        # constdef._locked = False  # type: ignore
        # const.tune.service.http_server.try_count = 1
        # constdef._locked = True  # type: ignore
    if args.no_service_startup_sync:
        from ..service import main
        main.START_SYNC_ON_START = False
    if args.single_provider:
        from lib.ff.sources import sources
        sources.DEBUG_SINGLE_PROVIDER = args.single_provider


def sty_stdout() -> None:
    import sty
    if not sys.stdout.isatty():
        for k in dir(sty):
            o = getattr(sty, k)
            if isinstance(o, sty.Register):
                o.mute()


def apply_lang(lang: str | None) -> None:
    if lang:
        if TYPE_CHECKING:  # import for type checker only
            from ..fake.xbmcaddon import Addon
        else:  # real import
            from xbmcaddon import Addon
        Addon.LANG = lang


def apply_args(args: Namespace) -> None:
    if hasattr(args, 'url'):
        if not args.url:
            args.url = '/'
        if '://' not in args.url:
            assert args.url.startswith('/')
            args.url = f'plugin://plugin.video.fanfilm{args.url or "/"}'  # DEBUG (terminal)
        if args.query:
            args.query = args.query.removeprefix('?')
            if '?' in args.url:
                args.url = f'{args.url}&{args.query}'
            else:
                args.url = f'{args.url}?{args.query}'
    apply_lang(args.lang)


def open_service(args: Namespace) -> ServiceHander:
    if TYPE_CHECKING:  # import for type checker only
        from ..fake import xbmc
    else:  # real import
        import xbmc
    from ..fake.fake_api import PID_NAMES
    from ..service.client import service_client
    handler = ServiceHander(mode=args.service)
    if args.service is ServiceMode.NONE:
        from ..service.http_server import RequestHandler
        RequestHandler.DEFAULT_PORT = 8123
        service_client._url = f'http://127.0.0.1:{RequestHandler.DEFAULT_PORT}/'
        xbmc._DEFAULT_JSONRPC_PORT = 0
        service_client.__class__.LOG_EXCEPTION = False
        xbmc._Service.start_service(mode=xbmc._Service.INTERNAL)
    elif args.service is ServiceMode.KODI:
        xbmc._Service.start_service(mode=xbmc._Service.CLIENT)
    elif args.service is ServiceMode.FULL:
        xbmc._DEFAULT_JSONRPC_PORT = 9099
        handler.pid = start_service()
        xbmc._Service.start_service(mode=xbmc._Service.CLIENT)
    elif args.service is ServiceMode.ONLY:
        service_client.__class__.LOG_EXCEPTION = False
        xbmc._Service.start_service(mode=xbmc._Service.INTERNAL)
    if handler.pid:
        PID_NAMES[handler.pid] = 'service'
    PID_NAMES[os.getpid()] = 'plug-in'
    return handler


def close_service(service: ServiceHander):
    if TYPE_CHECKING:  # import for type checker only
        from ..fake import xbmc
    else:  # real import
        import xbmc
    if service.mode is ServiceMode.FULL:
        stop_service(service.pid)
    xbmc._Service.stop_service()


def service_only(args: Namespace):
    from threading import enumerate as threading_enumerate
    from ..service import main
    from ..service.exc import ExitBaseException

    def _stop_service(sig: int = 0, frame=None):
        # raise KodiExit()
        threads = ', '.join(str(th) for th in threading_enumerate())  # LOG
        print(f' ** Service signal {sig} ({signal_name(sig)}) received, stopping...\n    with threads {threads}')
        if TYPE_CHECKING:  # import for type checker only
            from ..fake.xbmc import _exit_kodi
        else:  # real import
            from xbmc import _exit_kodi
        _exit_kodi()
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    signal.signal(signal.SIGINT, _stop_service)
    service = open_service(args)
    try:
        main.run()
    except (KeyboardInterrupt, AlarmInterrupt, ExitBaseException):
        try:
            _stop_service()
        except KeyboardInterrupt:
            print(' ** Service interrupted')
            return
    finally:
        print(' ** Service gone')
        close_service(service)


def main():
    """Main console app."""
    args = parse_args()
    try:
        if args.service is ServiceMode.ONLY:
            service_only(args)
        elif args.tui == 'main':
            from .tui.main import tui
            tui()
        elif args.tui == 'tree':
            from .tui.tree import tui
            tui()
        else:
            from .tui.simple import tui
            tui()
    except KeyboardInterrupt:
        pass
