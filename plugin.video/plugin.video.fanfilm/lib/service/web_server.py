"""
Simple HTTP Web server for FanFilm Kodi addon users.

Used to help users set up their Kodi addon by providing a web interface.
"""

from typing import Optional, Dict, Type, Mapping, TYPE_CHECKING
from pathlib import Path
from weakref import WeakSet
from fnmatch import fnmatch
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from .http_request import (
    RequestHandler, Route, request, Address,
    HTTPBadRequest, HTTPNotFound, HTTPUnprocessableContent, ExpectedJsonObjectError,
)
from ..ff.threads import Thread, Event, Lock
from ..ff.routing import URL  # , Router
from ..ff.settings import settings
from ..ff.kotools import Notification
from ..ff.types import JsonData, JsonResult
from ..ff.log_utils import fflog, fflog_exc
from ..kolang import L
from const import const
if TYPE_CHECKING:
    from .main import Works
    from socketserver import BaseRequestHandler
    import socket


# DEBUG (command line tests)
if __name__ == '__main__':
    from ..ff.cmdline import DebugArgumentParser
    p = DebugArgumentParser()
    p.add_argument('-p', '--port', type=int, default=8663, help='server port')
    p.add_argument('-t', '--threading', action='store_true', help='multithreading / concurent requests')
    args = p.parse_args()


if TYPE_CHECKING:
    class ServerBase(ThreadingHTTPServer):
        pass
elif __name__ == '__main__' and not args.threading:  # DEBUG only !!!
    class ServerBase(HTTPServer):
        pass
else:
    class ServerBase(ThreadingHTTPServer):
        pass


class Server(ServerBase):
    """FanFilm Web Server."""

    daemon_threads = True  # terminate worker threads when main thread terminates

    MIME_TYPES = {
        '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.html': 'text/html; charset=utf-8',
    }

    # list of all supported credentials settings names, taken from const.tune.service.web_server.cookies
    CREDENTIALS = [name for hc in const.tune.service.web_server.cookies.values() for name in hc.values()]

    def __init__(self,
                 server_address: Address,
                 RequestHandlerClass: Type[BaseHTTPRequestHandler] = RequestHandler,
                 *,
                 works: Optional['Works'] = None,
                 ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        host, port = self.server_address
        self.url = URL(f'http://{host}:{port}/')
        self.db_lock = Lock()
        self.works: Optional['Works'] = works
        self.events: Dict[str, Event] = {} if works is None else works.events  # no copy - shared events
        self.path = Path(__file__).parent.parent.parent / 'web'
        self._active_requests: 'WeakSet[socket.socket]' = WeakSet()
        self._update_notif = Notification('FanFilm', L(30519, 'Received cookies'))

    def finish_request(self, request, client_address):
        """Finish one request by instantiating RequestHandlerClass."""
        fflog(f'New request {request} from {client_address}')
        self._active_requests.add(request)
        req = self.RequestHandlerClass(request, client_address, self)
        self._active_requests.discard(request)
        fflog(f'Finish request {request} from {client_address}')

    @Route.get(r'/(index.html)?')
    def root(self) -> str:
        try:
            return (self.path / 'index.html').read_text(encoding='utf-8')
        except OSError:
            fflog_exc()
            return '<!DOCTYPE html><html><head><title>FanFilm Web Server</title></head><body>Not supported</body></html>'

    @Route.post(r'/echo')
    def echo(self) -> JsonResult:
        data = request.json
        return data

    def file(self, path: Path) -> 'tuple[int, bytes, dict[str, str]]':
        ext = path.suffix.lower()
        headers: 'dict[str, str]' = {}
        if ctype := self.MIME_TYPES.get(ext):
            headers['Content-Type'] = ctype
        try:
            return 200, path.read_bytes(), headers
        except FileNotFoundError:
            fflog.info(f'Missing static file: {path}')
        except OSError:
            fflog_exc()
        raise HTTPNotFound(f'File not found: {path}')

    @Route.get(r'/(?P<path>(?:static|mod)/.+)')
    def static(self, path: str) -> 'tuple[int, bytes, dict[str, str]]':
        return self.file(self.path / path)

    @Route.get(r'/addon/(?P<path>(?:fanart\.jpg|icon\.png))')
    def static_addon(self, path: str) -> 'tuple[int, bytes, dict[str, str]]':
        return self.file( Path(__file__).parent.parent.parent / path)

    def _list_credentials(self) -> JsonData:
        def label(js_name: str, setting_name: str) -> str:
            if js_name == ':user_agent':
                return L(30353, 'User agent')
            if js_name.startswith(':'):
                return js_name[1:].replace('_', ' ').title()
            if js_name == 'cf_clearance':
                return L(30526, 'Cloudflare cookie')
            return L(30527, 'Cookie ({name})').format(name=js_name)

        return {
            'settings': [
                {
                    'name': name,
                    'value': value,
                    'section': host,
                    'cookie': js_name,
                    'label': label(js_name, name),
                    # 'buttons': ['user_agent'] if js_name == ':user_agent' else [],
                } for host, hsets in const.tune.service.web_server.cookies.items()
                for js_name, name in hsets.items()
                if (value := settings.get(name)) is not None
            ]
        }

    def _get_cookie_value(self, setting_name: str, cookie: str) -> str:
        from ..ff.source_utils import extract_cookie
        for cookies_defs in const.tune.service.web_server.cookies.values():
            for cookie_name, setting in cookies_defs.items():
                if setting == setting_name:
                    return extract_cookie(cookie=cookie, cookie_name=cookie_name)
        return cookie

    @Route.get(r'/credentials/?')  # (?P<name>[^/]+)')
    def list_credentials(self) -> JsonData:
        return self._list_credentials()

    @Route.post(r'/credentials/?')
    def set_all_credentials(self) -> JsonData:
        data = request.json
        if not isinstance(data, Mapping):
            raise ExpectedJsonObjectError()
        if setting_list := data.get('settings'):
            fflog(f'Updating credentials: {setting_list}')
            for setting in setting_list:
                name, cookie = setting.get('name'), setting.get('value')
                if name in self.CREDENTIALS:
                    cookie = self._get_cookie_value(name, cookie)
                    settings.setString(name, cookie)
        return self._list_credentials()

    @Route.get(r'/credentials/(?P<name>[^/]+)')
    def get_credentials(self, name: str) -> JsonData:
        value = settings.get(name)
        return {
            'name': name,
            'value': value,
        }

    @Route.post(r'/credentials/(?P<name>[^/]+)')
    def set_credentials(self, name: str) -> JsonData:
        data = request.json
        if not isinstance(data, Mapping):
            raise ExpectedJsonObjectError()
        value = data.get('value')
        settings.setString(name, value)
        return {
            'name': name,
            'value': value,
        }

    @Route.post(r'/cookies')
    def update_cookies(self) -> JsonData:
        data = request.json
        if not isinstance(data, Mapping):
            raise ExpectedJsonObjectError()
        import json
        fflog(f'Updating cookies:\n{json.dumps(data, indent=2)}')
        settings_count = 0
        changed: 'set[str]' = set()
        if host := data.get('host'):
            for host_def, conv in const.tune.service.web_server.cookies.items():
                if fnmatch(host, host_def) if isinstance(host_def, str) else host_def.fullmatch(host):
                    cookies = {name: value for cookie in data.get('cookies', []) if (name := cookie.get('name')) and (value := cookie.get('value'))}
                    for cookie_name, settings_name in conv.items():
                        if cookie_name == ':UA':  # user-agent
                            cookie_name = ':user_agent'
                        if cookie_name.startswith(':'):  # meta, ex: user-agent
                            value = data.get(cookie_name[1:], '')
                            if value != settings.getString(settings_name):
                                changed.add(settings_name)
                            settings.setString(settings_name, value)
                            settings_count += 1
                        else:
                            value = cookies.get(cookie_name, '')
                            if value != settings.getString(settings_name):
                                changed.add(settings_name)
                            settings.setString(settings_name, value)
                            settings_count += 1
        if changed or data.get('forced_by_user'):
            fflog(f'Changed settings: {", ".join(sorted(changed))}')
            if const.tune.service.web_server.update_notification:
                self._update_notif.show()
        return {
            'success': True,
            'settings_count': settings_count,
        }


class WebServer:
    """The main function that starts the HTTP Web Server."""

    def __init__(self,
                 *,
                 works: Optional['Works'],
                 ) -> None:
        self._running: bool = False
        self._server: Optional[Server] = None
        self._httpd_thread: Optional[Thread] = None
        self.works: Optional['Works'] = works

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start proxy server (in new therad)."""
        if self._running:
            return

        address = '0.0.0.0'  # any address
        try:
            # create HTTP server and start serve in new therad
            self._server = Server((address, const.tune.service.web_server.port), RequestHandler, works=self.works)
            self._server.allow_reuse_address = True
            self._httpd_thread = Thread(target=self._server.serve_forever, name='Service WEB')
            self._httpd_thread.start()
            self._running = True

            # get port number and set server URL in the settings
            fflog(f'=== Web Server Started: {self._server.url} ===')

        except Exception:
            # creating HTTP server failed, clear URL in the settings
            self._running = False
            fflog_exc()
            raise

    def stop(self):
        """Stop proxy server."""
        if not self._running:
            return

        # shutdown and close the server
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                if self._server._active_requests:
                    fflog(f'Active requests: {self._server._active_requests}')
                for req in self._server._active_requests:
                    try:
                        req.shutdown(2)
                    except Exception as exc:
                        fflog(f'Exception during closing request socket: {exc}')
                    try:
                        req.close()
                    except Exception as exc:
                        fflog(f'Exception during closing request socket: {exc}')
                self._server._active_requests.clear()
                self._server.socket.close()
                self._server._update_notif.close()
                self._server = None
            if self._httpd_thread is not None:
                self._httpd_thread.join()
                self._httpd_thread = None
        except Exception as exc:
            fflog(f'Exception during stopping web server: {exc}')
            fflog_exc()
        self._running = False
        fflog('=== Web Server Stopped ===')


if __name__ == '__main__':
    from .main import Works
    address = '0.0.0.0'
    works = Works(
        events={
            'folder': Event(),
        },
    )
    works.events['folder'].set()
    server = Server((address, args.port), RequestHandler, works=works)
    server.allow_reuse_address = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('   Interrupted, shutting down ...')
        server.shutdown()
        server.server_close()
        print('Server stopped.')
    finally:
        from .main import stop
        from ..ff.kotools import destroy_xmonitor
        stop()
        destroy_xmonitor()
    # from threading import enumerate as thread_enumerate
    # from .main import threads
    # print('Active threads:', thread_enumerate())
    # print('FanFilm threads:', threads)
