import sys
import os
from pathlib import Path

# --- Environment Setup ---
os.environ['FF_FAKE'] = '1'
REPO_ROOT = Path(__file__).resolve().parent.parent

# Save real streams
real_stdout = sys.__stdout__
real_stderr = sys.__stderr__

# Redirect standard output and error to log file
log_path = REPO_ROOT / 'fanfilm_tui.log'
try:
    log_file = open(log_path, 'a', encoding='utf-8', buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
except Exception:
    class DummyWriter:
        def write(self, s): pass
        def flush(self): pass
    sys.stdout = DummyWriter()
    sys.stderr = DummyWriter()

ADDON_DIR = REPO_ROOT / 'plugin.video' / 'plugin.video.fanfilm'
sys.path.insert(0, str(ADDON_DIR))
sys.path.insert(0, str(ADDON_DIR / 'lib'))
sys.path.insert(0, str(ADDON_DIR / 'lib' / '3rd'))
sys.path.insert(0, str(ADDON_DIR / 'lib' / 'fake'))

import types
pyxbmct_mock = types.ModuleType('pyxbmct')
class MockBase: pass
pyxbmct_mock.AddonDialogWindow = MockBase
pyxbmct_mock.BlankRightList = MockBase
sys.modules['pyxbmct'] = pyxbmct_mock

import requests
import requests_cache
class MockCachedSession(requests.Session):
    def __init__(self, *args, **kwargs):
        valid_kwargs = {k: v for k, v in kwargs.items() if k not in (
            'cache_name', 'expire_after', 'backend', 'serializer', 'urls_expire_after',
            'cache_control', 'allowable_codes', 'allowable_methods', 'always_revalidate',
            'ignored_parameters', 'match_headers', 'filter_fn', 'key_fn', 'stale_if_error',
            'readonly'
        )}
        super().__init__(**valid_kwargs)
requests_cache.CachedSession = MockCachedSession
requests_cache.install_cache = lambda *args, **kwargs: None

import lib
lib.FAKE = True

from lib.service.fake_client import FakeServiceClient
import lib.service.client as service_client_mod
service_client_mod.service_client = FakeServiceClient()

import xbmcgui
class TUIDialogMock:
    def notification(self, *args, **kwargs): pass
    def yesno(self, *args, **kwargs): return True
    def ok(self, *args, **kwargs): return True
    def yesnocustom(self, *args, **kwargs): return 2
xbmcgui.Dialog = lambda: TUIDialogMock()
