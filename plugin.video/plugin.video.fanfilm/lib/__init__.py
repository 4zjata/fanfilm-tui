import sys
import os
from pathlib import Path
from typing import List


def is_subinterpreter() -> bool:
    """Detect if module is called in subinterpreter (Kodi) or not (command line)."""
    if os.environ.get('FF_FAKE') == '1':
        return False
    from traceback import format_stack
    st = format_stack()
    return bool(st and 'fanfilm' in st[0] and '/_dev_' not in st[0] and 'python -m' not in sys.argv[0])


SUBINTERPRETER: bool = is_subinterpreter()
if os.environ.get('FF_FAKE') == '1':
    SUBINTERPRETER = False
cmdline_argv: List[str] = []
FAKE: bool = not SUBINTERPRETER
MOCK: bool = False

# Path to the top-level fanfilm folder.
top_ff_path = Path(__file__).parent
# Add paths to 3rd-party libs.
sys.path.insert(0, str(top_ff_path / '3rd'))
sys.path.insert(0, str(top_ff_path / '3rd' / 'jwgraph'))  # contains a few modules
# Add fake xmbc modules (DEBUG & TESTS).
if FAKE:
    import os
    sys.path.insert(0, str(top_ff_path / 'fake'))
    if os.environ.get('XBMC_MOCK') == '1':
        sys.path.insert(0, str(top_ff_path / 'fake' / 'raw'))
        MOCK = True
    # Fake sys.argv for DEBUG & TESTS from command line
    cmdline_argv, sys.argv = sys.argv, ['plugin://fanfilm/', '0', '']
    from lib.fake.fake_api import auto
    auto(cmdline_argv)
    del auto


# Monkey-patching datetime.strptime
# see: https://forum.kodi.tv/showthread.php?tid=112916&pid=2953239
# see: https://bugs.python.org/issue27400
import datetime as datetime_module            # noqa: E402
from datetime import datetime as _datetime    # noqa: E402

# Checked in real code, fixed in py3.13.
if sys.version_info >= (3, 11):
    datetime = _datetime
elif not getattr(datetime_module, '_datetime_is_patched', False):

    class datetime(_datetime):

        @classmethod
        def strptime(cls, date_string: str, format: str) -> _datetime:
            try:
                return _dt_strptime(date_string, format)
            except TypeError:
                import time
                return datetime(*(time.strptime(date_string, format)[0:6]))

    _dt_strptime = _datetime.strptime
    datetime_module.datetime = datetime
    datetime_module._datetime = _datetime
    datetime_module._datetime_is_patched = True
