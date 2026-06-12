# This file is generated from Kodi source code and post-edited
# to correct code style and docstrings formatting.
# License: GPL v.3 <https://www.gnu.org/licenses/gpl-3.0.en.html>
"""
**Virtual file system functions on Kodi.**

Offers classes and functions offers access to the Virtual File Server (VFS)
which you can use to manipulate files and folders.
"""
from typing import Union, List, Tuple, Optional
from pathlib import Path
import os
import re

__kodistubs__ = True


class File:

    def __init__(self, filepath: str, mode: str = 'r') -> None:
        self._path = Path(filepath)
        self._mode = mode
        # If mode allows writing, ensure directory exists
        if any(m in mode for m in 'wax'):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, self._mode)

    def __enter__(self) -> 'File':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def read(self, numBytes: int = -1) -> str:
        return self._file.read(numBytes)

    def readBytes(self, numBytes: int = -1) -> bytearray:
        return bytearray(self._file.read(numBytes))

    def write(self, buffer: Union[str, bytes, bytearray]) -> bool:
        return self._file.write(buffer) > 0

    def size(self) -> int:
        return self._path.stat().st_size

    def seek(self, seekBytes: int, iWhence: int = 0) -> None:
        self._file.seek(seekBytes, iWhence)

    def tell(self) -> int:
        return self._file.tell()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None


class Stat:

    def __init__(self, path: str) -> None:
        pass

    def st_mode(self) -> int:
        return 0

    def st_ino(self) -> int:
        return 0

    def st_dev(self) -> int:
        return 0

    def st_nlink(self) -> int:
        return 0

    def st_uid(self) -> int:
        return 0

    def st_gid(self) -> int:
        return 0

    def st_size(self) -> int:
        return 0

    def st_atime(self) -> int:
        return 0

    def st_mtime(self) -> int:
        return 0

    def st_ctime(self) -> int:
        return 0


def copy(strSource: str, strDestination: str) -> bool:
    return True


def delete(file: str) -> bool:
    return True


def rename(file: str, newFile: str) -> bool:
    return True


def exists(path: str) -> bool:
    return Path(path).exists()


def makeLegalFilename(filename: str) -> str:
    def legal(fname: str) -> str:  # see: CUtil::MakeLegalFileName
        if WINDOWS:
            fname = re.sub(r'[/\\?:*"<>|]', '_', fname)
        else:
            fname = re.sub('[/\\?]', '_', fname)
        if fname.endswith('. '):
            fname = fname[:-2]
        return fname

    # see: CUtil::MakeLegalPath
    import platform
    WINDOWS = platform.system() == 'Windows'
    scheme, sep, path = filename.rpartition('://')
    if sep and scheme not in ('file', 'win-lib', 'smb', 'nfs'):
        return filename
    if trailing_slash := filename.endswith('/'):
        filename = filename[:-1]
    if sep and not path.startswith('/'):
        path = f'/{path}'
    if WINDOWS:
        parts = re.split(r'[\\/]', path)
    else:
        parts = path.split('/')
    filename = '/'.join(parts[:1] + [legal(p) for p in parts[1:]])
    if trailing_slash:
        filename += '/'
    return filename


def translatePath(path: str) -> str:
    # see: https://kodi.wiki/view/Special_protocol
    from lib.fake.fake_api import KODI_PATH
    if path == 'special://userdata':
        return str(KODI_PATH / 'userdata')
    if path == 'special://database':
        return str(KODI_PATH / 'userdata' / 'Database')
    if path == 'special://home':
        return str(KODI_PATH)
    if path == 'special://temp':
        return str(KODI_PATH / 'temp')

    if path.startswith('special://'):
        import os.path
        from lib.ff.kotools import get_platform

        platform = get_platform()
        if path == 'special://logpath':
            if platform == 'windows':
                return os.path.expandvars(Path('%APPDATA%') / 'Kodi')
            if platform == 'darwin':  # macos
                return os.path.expanduser('~/Library/Logs')
            return str(KODI_PATH / 'temp')

    return str(path)


def validatePath(path: str) -> str:
    return str(Path(path).absolute())


def mkdir(path: str) -> bool:
    p = Path(path)
    p.mkdir(exist_ok=True)
    return p.is_dir()


def mkdirs(path: str) -> bool:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p.is_dir()


def rmdir(path: str, force: bool = False) -> bool:
    return True


def listdir(path: str) -> Tuple[List[str], List[str]]:
    try:
        return next(os.walk(path))[1:]
    except StopIteration:
        return ([], [])
