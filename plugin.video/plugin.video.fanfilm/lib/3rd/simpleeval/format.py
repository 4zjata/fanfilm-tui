"""
Some safe string and format tools.

Author: rysson
"""

from __future__ import annotations
from typing import Optional, Union, Any, Dict, Iterable, Callable, Type, TYPE_CHECKING
from typing_extensions import Protocol
import re
import string
from inspect import isclass
from logging import getLogger
from simpleeval import InvalidExpression, EvalWithCompoundTypes, SimpleEval, simple_eval
if TYPE_CHECKING:
    import ast

logger = getLogger(__name__)


class MISSING:
    pass


class Evaluator(Protocol):
    def __init__(self,
                 operators: Optional[Dict[Type['ast.operator'], Callable]] = None,
                 functions: Optional[Dict[str, Callable]] = None,
                 names: Optional[Dict[str, Any]] = None) -> None: ...
    def eval(self, expr: str, previously_parsed: Optional['ast.Expr'] = None) -> Any: ...
    # def __init__(self, operators=None, functions=None, names=None) -> None: ...
    # def eval(self, expr, previously_parsed=None) -> Any: ...


class FormatProtocol(Protocol):
    def __call__(self, format_string: str, /, *args, **kwargs) -> str: ...


EvaluatorClass = Type[Evaluator]


#: Regex type
regex = type(re.compile(''))


#: RegEx for UUID
re_uuid = re.compile(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}')


re_quote = re.compile(r'''(?:"""(?:\\.|.)*?""")|(?:"(?:\\.|.)*?")'''
                      r"""|(?:'''(?:\\.|.)*?''')|(?:'(?:\\.|.)*?')""")


def fparser(s: str) -> Iterable[tuple[str, str | None, str | None, str | None]]:
    """
    Like _string.formatter_parser() returns (literal_text, field_name, format_spec, conversion).
    Supports "{}" inside field_name, can work with simple_eval.
    Ex. f-string inside {} inside join with list comprehension):
    >>> fmt('out: {", ".join(f"({x})" for x in hosts)}...', hosts=("a", "bb", "ccc"))
    >>> # 'out: (a), (bb), (ccc)'
    """
    i, lvl = 0, 0
    # literal_text, field_name, format_spec, conversion
    oi, vec = 0, ['', None, None, None]
    while i < len(s):
        c = s[i]
        nc = s[i+1:i+2]
        # if c in '{}' and c == nc:
        if c in '{}' and c == nc and (lvl < 2 or c == '{'):
            vec[oi] += c
            i += 1
        elif c == '{':
            if lvl == 0:
                oi = 1
                vec[1] = vec[2] = ''
            else:
                vec[oi] += c
            lvl += 1
        elif c == '}':
            lvl -= 1
            if lvl < 0:
                raise ValueError("Single '}' encountered in format string")
            if lvl == 0:
                yield vec
                oi, vec = 0, ['', None, None, None]
            else:
                vec[oi] += c
        elif lvl == 1 and c == '!' and oi == 1:
            oi = 3
            vec[oi] = ''
        elif lvl == 1 and c == ':' and oi in (1, 3):
            oi = 2
            vec[oi] = ''
        else:
            if lvl > 0:
                rx = re_quote.match(s, i) if c in ('"', "'") else None
                if rx:
                    a, b = rx.span()
                    vec[oi] += s[a:b]
                    i += b - a - 1
                elif c == '[':
                    lvl += 1
                    vec[oi] += c
                elif c == ']':
                    lvl -= 1
                    vec[oi] += c
                else:
                    vec[oi] += c
            else:
                vec[oi] += c
        i += 1
    if lvl:
        raise ValueError("Single '{' encountered in format string")
    if vec[0] or vec[1] is not None:
        yield vec


class SafeFormatter(string.Formatter):
    r"""
    Safe string formatter.

    Leave unknown arguments, or use default value or evaluate expr:
    ("{a:!!def} {999}") -> "def {999}"
    ("{a + 2}", a=42)   -> "44"
    """

    _escape_trans = {
        '\\': '\\\\',
        '\'': '\\\'',
        '\"': '\\\"',
        '\a': '\\a',
        '\b': '\\b',
        '\f': '\\f',
        '\n': '\\n',
        '\r': '\\r',
        '\t': '\\t',
        '\v': '\\v',
        '\000': '\\000',
    }

    _RX_SHOW_EXPR = re.compile(r'^(.*?)(\s*=\s*)$')

    def __init__(self, *,
                 evaluate: Union[bool, EvaluatorClass] = True,
                 escape: bool = True,
                 extended: bool = False,
                 names: Optional[Dict[str, Any]] = None,
                 functions: Optional[Dict[str, Callable]] = None,
                 subformat: bool | string.Formatter | type[string.Formatter] | FormatProtocol = False
                 ) -> None:
        super().__init__()
        #: Evaluator instance.
        self.evaluator: SimpleEval = None
        #: Evaluator class.
        self.evaluator_class: EvaluatorClass = None
        if isclass(evaluate):
            self.evaluator_class = evaluate
        elif simple_eval and evaluate:
            if evaluate == 'simple':
                self.evaluator_class = SimpleEval
            else:
                self.evaluator_class = EvalWithCompoundTypes
        #: Evaluator built-in names (kwargs). If no evaluator `names` are merged with `kwargs`.
        self.evaluator_names: Optional[Dict[str, Any]] = names
        #: Evaluator built-in functions.
        self.evaluator_functions: Optional[Dict[str, Callable]] = functions
        #: Enable escape conversion `!e`.
        self.escape: bool = escape
        #: Extended (and slower) format parser (allows use advanced expressions).
        self.extended: bool = extended
        #: Sub-formatter for nested format calls.
        self.subformater: FormatProtocol | None
        if subformat is True:
            self.subformater = self.format
        elif subformat is False or subformat is None:
            self.subformater = None
        elif isinstance(subformat, type):
            self.subformater = subformat().format
        elif callable(getattr(subformat, 'format', None)):
            if TYPE_CHECKING:
                assert isinstance(subformat, string.Formatter)
            self.subformater = subformat.format
        else:
            if TYPE_CHECKING:
                assert not isinstance(subformat, string.Formatter)
            self.subformater = subformat

    def parse(self, format_string: str) -> Iterable[tuple[str, str | None, str | None, str | None]]:
        """Loop over the format_string and return an iterable of tuples."""
        if self.extended:
            return fparser(format_string)
        return super().parse(format_string)

    def make_eval(self, *args, **kwargs) -> Evaluator:
        """Prepare the actual work of formatting."""
        if self.evaluator_names:
            names = {**self.evaluator_names, **kwargs}
        else:
            names = kwargs
        if self.evaluator_class:
            if self.evaluator_names:
                names = {**self.evaluator_names, **kwargs}
            else:
                names = kwargs
            return self.evaluator_class(names=names, functions=self.evaluator_functions)
        return SimpleEval(names=names, functions=self.evaluator_functions)

    def vformat(self, format_string: str, args, kwargs) -> str:
        """Do the actual work of formatting."""
        if self.evaluator_names:
            names = {**self.evaluator_names, **kwargs}
        else:
            names = kwargs
        if self.evaluator_class:
            if self.evaluator_names:
                names = {**self.evaluator_names, **kwargs}
            else:
                names = kwargs
            self.evaluator = self.evaluator_class(names=names, functions=self.evaluator_functions)
            return super().vformat(format_string, args, kwargs)
        return super().vformat(format_string, args, names)

    def convert_field(self, value: Any, conversion: str) -> str:
        """Converts the value (returned by get_field()) given a conversion type."""
        if self.escape and conversion == 'e':
            esc = self._escape_trans
            return ''.join(esc.get(c, c) for c in str(value))
        return super().convert_field(value, conversion)

    def get_field(self, field_name: str, args, kwargs) -> tuple[Any, str]:
        """Convert field_name as returned by parse() to an object to be formatted."""
        try:
            return super().get_field(field_name, args, kwargs)
        except Exception:
            pass
        value = MISSING
        suffix = ''
        if '=' in field_name:
            # support for f'{var=}'
            if (mch := self._RX_SHOW_EXPR.match(field_name)) is not None:
                field_name, suffix = mch.groups()
        if field_name.isdecimal():
            # missing positional argument
            try:
                value = self.missing_field(field_name, args, kwargs)
            except Exception:
                pass
        if value is MISSING and self.evaluator:
            try:
                # evaluate expression in {...}
                value = self.evaluator.eval(field_name)
            except (InvalidExpression, SyntaxError):
                # if valied try to expand field_name with format(), to support nested variables like a{b}
                if self.subformater is not None and ('{' in field_name and '}' in field_name):
                    try:
                        ex_field_name = self.subformater(field_name, *args, **kwargs)
                        value = self.evaluator.eval(ex_field_name)
                        field_name = ex_field_name
                    except (InvalidExpression, SyntaxError):
                        pass
        if value is MISSING:
            value, new_field_name = self.missing_field(field_name, args, kwargs)
            if new_field_name:
                field_name = new_field_name
        if suffix:
            value = f'{field_name}{suffix}{value}'
        # return value, ()
        return value, field_name

    def missing_field(self, field_name: str, args, kwargs) -> tuple[Any, str]:
        """Return missing files, by default keep format string."""
        return '{%s}' % field_name, ''
        # return '{%s}' % field_name, field_name

    def format_field(self, value: Any, format_spec: str) -> str:
        """Call the global format() built-in, support for default values."""
        format_spec, sep, val = format_spec.partition('!!')
        unknown = isinstance(value, str) and value[:1] == '{' and value[-1:] == '}'
        if sep and unknown:
            try:
                if '::' in val:
                    value, sep, format_spec = val.partition('::')
                elif format_spec[-1:] in 'bcdoxX':
                    value = int(val)
                elif format_spec[-1:] in 'eEfFgG':
                    value = float(val)
                else:
                    value = val
            except ValueError:
                # format_spec = format_spec[:-1] + 's'
                format_spec = 's'
                value = val
        try:
            return super().format_field(value, format_spec)
        except ValueError:
            logger.warning(f'Field formating failed: value={value!r}, spec={format_spec!r}')
            if format_spec:
                if value[:1] == '{' and value[-1:] == '}':
                    return f'{value[:-1]}:{format_spec}}}'
                return f'{value}:{format_spec}'
            return f'{value}'

    def add_name(self, name: str, value: Any) -> None:
        """Register `name` in easy-eval names."""
        if self.evaluator_names is None:
            self.evaluator_names = {}
        self.evaluator_names[name] = value


safe_format = SafeFormatter().format
safe_xformat = SafeFormatter(extended=True).format
