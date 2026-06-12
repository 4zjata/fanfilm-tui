
from __future__ import annotations
from typing_extensions import Protocol, Iterator, TypeAlias, ClassVar, TYPE_CHECKING
from contextlib import contextmanager
import termios
from os import terminal_size, get_terminal_size, isatty
import sys
import re
from datetime import datetime
from threading import Thread
from attrs import define, frozen, field, setters

# stable
class HasFileno(Protocol):
    def fileno(self) -> int: ...


FileDescriptor: TypeAlias = int  # stable
FileDescriptorLike: TypeAlias = 'int | HasFileno'  # stable
# FileDescriptorOrPath: TypeAlias = int | StrOrBytesPath


default_terminal: Terminal


@define(kw_only=True)
class Cursor:
    line: int = 0
    column: int = 0
    # term: Terminal = field(factory=lambda: default_terminal, on_setattr=setters.frozen)

    _RX_GET_POS: ClassVar[re.Pattern[str]] = re.compile(r'\033\[(\d+);(\d+)R')

    def __bool__(self) -> bool:
        return bool(self.line or self.column)

    @classmethod
    def get(cls) -> Cursor:
        with raw_term_input():
            print('\033[6n', end='', flush=True)
            resp = ''
            for _ in range(100):
                ch = sys.stdin.read(1)
                resp += ch
                if ch == 'R':
                    break
        if mch := cls._RX_GET_POS.match(resp):
            return cls(line=int(mch[1]), column=int(mch[2]))
        return cls()

    def __call__(self) -> None:
        if self.line:
            print(f'\033[{self.line};{self.column}H', end='', flush=True)

    def apply(self) -> None:
        if self.line:
            print(f'\033[{self.line};{self.column}H', end='', flush=True)

    def up(self, count: int = 1) -> Cursor:
        self.line = max(1, self.line - count)
        return self

    def down(self, count: int = 1) -> Cursor:
        self.line = max(1, self.line - count)
        return self

    def right(self, count: int = 1) -> Cursor:
        self.column += count
        return self

    def left(self, count: int = 1) -> Cursor:
        self.column = max(1, self.column - count)
        return self

    def set(self, line: int, column: int) -> Cursor:
        self.line = max(1, line)
        self.column = max(0, column)
        return self

    def save(self) -> None:
        print('\033[s', end='', flush=True)

    def restore(self) -> None:
        print('\033[u', end='', flush=True)


@define(kw_only=True)
class Screen:

    size: terminal_size = field(factory=get_terminal_size, on_setattr=setters.frozen)
    bottom: int = 0
    # term: Terminal = field(factory=lambda: default_terminal, on_setattr=setters.frozen)

    # Constants for clear line/screen directions (ANSI terminal codes).
    AFTER = 0   # after / below, right
    BEFORE = 1  # before / above, left
    ALL = 2

    def __bool__(self) -> bool:
        return bool(self.size.columns and self.size.lines)

    @staticmethod
    def from_terminal_size() -> Screen:
        return Screen(size=get_terminal_size())

    def erase(self, *, dir=ALL) -> None:
        print(f'\033[{dir}J', end='', flush=True)

    def erase_line(self, *, dir=ALL) -> None:
        print(f'\033[{dir}K', end='', flush=True)

    def scroll_up(self, n: int, *, keep_cursor: bool = True) -> None:
        if keep_cursor:
            cursor = Cursor.get()
            print(f'\033[{n}S')  # scroll screen up
            cursor.up(n)
            cursor()
        else:
            print(f'\033[{n}S', flush=True)  # scroll screen up

    def scroll_down(self, n: int, *, keep_cursor: bool = True) -> None:
        if keep_cursor:
            cursor = Cursor.get()
            print(f'\033[{n}T')  # scroll screen down
            cursor.down(n)
            cursor()
        else:
            print(f'\033[{n}T', flush=True)  # scroll screen down

    def scroll_region(self, *, top: int = 1, bottom: int = 0, keep_cursor: bool = True) -> None:
        if keep_cursor:
            cursor = Cursor.get()
            print(f'\033[{top};{bottom or ""}r')
            cursor()
        else:
            print(f'\033[{top};{bottom or ""}r', flush=True)

    def set_bottom(self, bottom: int = 0) -> None:
        if self.bottom == bottom:
            return
        cursor = Cursor.get()
        old = self.size.lines - self.bottom  # old bottom area line
        new = self.size.lines - bottom       # new bottom area line
        cursor_in_main_area = cursor.line <= old
        self.bottom = bottom
        self.scroll_region(bottom=new)
        if new < old:  # expand bottom area, shrink main scroll area (need to scroll up)
            self.scroll_up(old - new, keep_cursor=False)
            if cursor_in_main_area:
                # cursor in main area, need to move up
                cursor.up(old - new)
        if self.bottom > 0:
            Cursor(line=new + 1, column=0)()
            self.erase(dir=Screen.AFTER)
        if cursor_in_main_area or self.bottom == 0:
            cursor()

    def bottom_cursor(self) -> Cursor:
        if self.bottom > 0:
            return Cursor(line=self.size.lines - self.bottom + 1, column=0)
        return Cursor()


@define
class Terminal:
    tty: bool


@contextmanager
def raw_term_input(*, input: FileDescriptorLike | None = None) -> Iterator[None]:
    if input is None:
        input = sys.stdin
    assert input is not None
    old = termios.tcgetattr(input)
    if old[3] & (termios.ECHO | termios.ICANON) == 0:
        yield
        return
    new = termios.tcgetattr(input)
    new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
    try:
        termios.tcsetattr(input, termios.TCSADRAIN, new)
        yield
    finally:
        termios.tcsetattr(input, termios.TCSADRAIN, old)


@contextmanager
def term(*, input: FileDescriptorLike | None = None, output: FileDescriptorLike | None = None) -> Iterator[Terminal]:
    if input is None:
        input = sys.stdin
    if output is None:
        output = sys.stdout
    assert input is not None
    assert output is not None
    input_tty = isatty(input if isinstance(input, int) else input.fileno())
    output_tty = isatty(output if isinstance(output, int) else output.fileno())
    if not input_tty or not output_tty:
        yield Terminal(tty=False)
        return
    old = termios.tcgetattr(input)
    new = termios.tcgetattr(input)
    new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
    # src = Screen()
    try:
        termios.tcsetattr(input, termios.TCSADRAIN, new)
        yield Terminal(tty=True)
    finally:
        termios.tcsetattr(input, termios.TCSADRAIN, old)


if __name__ == '__main__':
    from time import sleep

    print('prepage...')
    scr = Screen()
    for line in range(1, scr.size.lines + 1):
        print(f'Line {line:3}  ', end='' if line == scr.size.lines else '\n', flush=True)
    sleep(1)
    try:
        scr.set_bottom(5)
    finally:
        scr.set_bottom()
    sleep(1)
