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
                        
                        if hasattr(self.server, 'app'):
                            self.server.app.call_from_thread(
                                self.server.app.notify,
                                f"Zaktualizowano ciasteczka dla {prefix}",
                                severity="information"
                            )
                        
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                print(f"Error parsing cookie data: {e}")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/mod/fanfilm.user.js':
            # Serve the tampermonkey script
            script_path = Path(__file__).parent.parent / "plugin.video.fanfilm" / "web" / "mod" / "fanfilm.user.js"
            if script_path.exists():
                self.send_response(200)
                self.send_header('Content-type', 'application/javascript')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(script_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
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
