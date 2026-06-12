import http.server
import socketserver
import threading
import json
import urllib.parse
from pathlib import Path
from lib.ff.settings import settings

class CloudflareCookieHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suppress HTTP logging
        
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path in ('/cookie', '/cookies'):
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                ua = data.get('user_agent', '') or data.get('userAgent', '')
                cookies = data.get('cookies', [])
                
                print(f"[TUI_SERVER] Received POST to {self.path} for host: {data.get('host')}")
                print(f"[TUI_SERVER] Cookies names: {[c.get('name') for c in cookies]}")
                print(f"[TUI_SERVER] UA: {ua}")
                
                # Find cf_clearance cookie
                cf_cookie = next((c for c in cookies if c.get('name') == 'cf_clearance'), None)
                
                if cf_cookie and ua:
                    host_lower = data.get('host', '').lower()
                    prefix = None
                    if 'cda-hd' in host_lower:
                        prefix = 'cdahd'
                    elif 'zaluknij' in host_lower:
                        prefix = 'zaluknij'
                    elif 'obejrzyj' in host_lower:
                        prefix = 'obejrzyj'
                    elif 'filmyonline' in host_lower:
                        prefix = 'filmyonline'
                    elif any(domain in host_lower for domain in ('vidlink', 'storm', 'megacloud')):
                        prefix = 'vidlink'
                    
                    if prefix:
                        settings.set(f"{prefix}.cookies_cf", cf_cookie.get('value', ''))
                        settings.set(f"{prefix}.cf_clearance", cf_cookie.get('value', ''))
                        settings.set(f"{prefix}.user_agent", ua)
                        print(f"[TUI_SERVER] Successfully updated settings for prefix: {prefix}")
                        
                        if hasattr(self.server, 'app'):
                            self.server.app.call_from_thread(
                                self.server.app.notify,
                                f"Zaktualizowano ciasteczka dla {prefix}",
                                severity="information"
                            )
                else:
                    print("[TUI_SERVER] Missing cf_clearance cookie or User-Agent string.")
                        
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                print(f"Error parsing cookie data: {e}")
        elif self.path == '/credentials':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                from const import const
                credentials_names = [name for hc in const.tune.service.web_server.cookies.values() for name in hc.values()]
                
                def get_cookie_value(setting_name: str, cookie: str) -> str:
                    from lib.ff.source_utils import extract_cookie
                    for cookies_defs in const.tune.service.web_server.cookies.values():
                        for cookie_name, setting in cookies_defs.items():
                            if setting == setting_name:
                                return extract_cookie(cookie=cookie, cookie_name=cookie_name)
                    return cookie
                
                if setting_list := data.get('settings'):
                    for setting in setting_list:
                        name = setting.get('name')
                        cookie = setting.get('value')
                        if name in credentials_names:
                            cookie = get_cookie_value(name, cookie or '')
                            settings.setString(name, cookie)
                            
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                def label(js_name: str, setting_name: str) -> str:
                    if js_name == ':user_agent':
                        return "User agent"
                    if js_name.startswith(':'):
                        return js_name[1:].replace('_', ' ').title()
                    if js_name == 'cf_clearance':
                        return "Ciasteczko Cloudflare"
                    return f"Ciasteczko ({js_name})"

                res = {
                    'settings': [
                        {
                            'name': name,
                            'value': settings.getString(name) if settings.getString(name) is not None else "",
                            'section': host,
                            'cookie': js_name,
                            'label': label(js_name, name),
                        } for host, hsets in const.tune.service.web_server.cookies.items()
                        for js_name, name in hsets.items()
                    ]
                }
                self.wfile.write(json.dumps(res).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                print(f"Error updating credentials: {e}")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        web_path = Path(__file__).parent.parent / "plugin.video" / "plugin.video.fanfilm" / "web"
        path_unquoted = urllib.parse.unquote(self.path)
        
        if path_unquoted in ('/', '/index.html'):
            index_file = web_path / "index.html"
            if index_file.exists():
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(index_file, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                
        elif path_unquoted.startswith('/static/') or path_unquoted.startswith('/mod/'):
            file_path = web_path / path_unquoted.lstrip('/')
            if file_path.exists():
                self.send_response(200)
                ext = file_path.suffix.lower()
                ctype = 'text/plain'
                if ext == '.js':
                    ctype = 'application/javascript; charset=utf-8'
                elif ext == '.css':
                    ctype = 'text/css; charset=utf-8'
                elif ext == '.png':
                    ctype = 'image/png'
                elif ext == '.jpg' or ext == '.jpeg':
                    ctype = 'image/jpeg'
                self.send_header('Content-type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                
        elif path_unquoted.startswith('/addon/'):
            filename = path_unquoted.split('/')[-1]
            file_path = Path(__file__).parent.parent / "plugin.video" / "plugin.video.fanfilm" / filename
            if file_path.exists():
                self.send_response(200)
                ext = file_path.suffix.lower()
                ctype = 'image/png' if ext == '.png' else 'image/jpeg'
                self.send_header('Content-type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                
        elif path_unquoted == '/credentials':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            from const import const
            def label(js_name: str, setting_name: str) -> str:
                if js_name == ':user_agent':
                    return "User agent"
                if js_name.startswith(':'):
                    return js_name[1:].replace('_', ' ').title()
                if js_name == 'cf_clearance':
                    return "Ciasteczko Cloudflare"
                return f"Ciasteczko ({js_name})"

            res = {
                'settings': [
                    {
                        'name': name,
                        'value': settings.getString(name) if settings.getString(name) is not None else "",
                        'section': host,
                        'cookie': js_name,
                        'label': label(js_name, name),
                    } for host, hsets in const.tune.service.web_server.cookies.items()
                    for js_name, name in hsets.items()
                ]
            }
            self.wfile.write(json.dumps(res).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    pass

class CloudflareServer:
    def __init__(self, app, port=8663):
        self.app = app
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        try:
            self.server = ThreadedHTTPServer(("", self.port), CloudflareCookieHandler)
            self.server.app = self.app
            self.thread = threading.Thread(target=self.server.serve_forever)
            self.thread.daemon = True
            self.thread.start()
            print(f"Cloudflare verification server started on port {self.port}")
        except OSError as e:
            print(f"Failed to start Cloudflare server on port {self.port}: {e}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=1)
            print("Cloudflare verification server stopped.")
