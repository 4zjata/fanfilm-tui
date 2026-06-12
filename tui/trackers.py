import lib.ff.downloader as downloader

class TUIProgressDialog:
    def __init__(self):
        self.percent = 0
        self.message = ""
        self.providers = []
        self.cancelled = False
        self.finished = False
    def update(self, percent, message, providers=None):
        self.percent = percent
        self.message = message
        if providers is not None:
            self.providers = providers
    def update_time(self, elapsed, total): pass
    def iscanceled(self): return self.cancelled
    def close(self): self.finished = True

class DownloadProgressTracker:
    def __init__(self):
        self.filename = ""
        self.percent = 0
        self.state = "starting"
        self.speed = "0"
        self.finished = False
        self.history = [] # list of dicts: {'filename': str, 'percent': int, 'state': str, 'speed': str}

    def update(self, fileName, percentage="", downloaded="", state="running", speed=""):
        # If the filename changed, push the old one to history if it was downloading
        if self.filename and self.filename != fileName and self.state != "starting":
            # Add or update in history
            existing = next((x for x in self.history if x['filename'] == self.filename), None)
            if existing:
                existing['percent'] = self.percent
                existing['state'] = self.state
                existing['speed'] = self.speed
            else:
                self.history.insert(0, {
                    'filename': self.filename,
                    'percent': self.percent,
                    'state': self.state,
                    'speed': self.speed
                })

        self.filename = fileName
        self.state = state
        self.speed = speed
        try:
            self.percent = int(percentage.replace("%", ""))
        except: pass
        if state in ("finished", "broken", "stopped") or percentage == "100%":
            self.finished = True
            # Update history immediately for finished files
            existing = next((x for x in self.history if x['filename'] == self.filename), None)
            if existing:
                existing['state'] = self.state
                existing['percent'] = self.percent
            else:
                self.history.insert(0, {
                    'filename': self.filename,
                    'percent': self.percent,
                    'state': self.state,
                    'speed': self.speed
                })


dl_tracker = DownloadProgressTracker()

def custom_dl_update(fileName, percentage="", downloaded="", state="running", speed=""):
    dl_tracker.update(fileName, percentage, downloaded, state, speed)

def setup_downloader_mocking():
    downloader.update = custom_dl_update
    downloader.MyAddon.update = staticmethod(custom_dl_update)
    downloader.MyAddon._confirm_download = lambda filename, content, total: True
    downloader.MyAddon._handle_existing_file = lambda dest, content, resp, url, headers, total: "continue"

# Initialize mocks on module load
setup_downloader_mocking()
