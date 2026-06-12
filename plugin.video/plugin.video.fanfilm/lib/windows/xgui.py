
from __future__ import annotations
from typing import Any, Sequence, Mapping, Iterable, Iterator, ClassVar, Callable, overload, TYPE_CHECKING
from typing_extensions import TypeGuard
from copy import deepcopy
from enum import Enum
from pathlib import Path
# from lxml import etree as ET
import xml.etree.ElementTree as ET
from inspect import Signature, Parameter
from functools import partial
import re
from sys import version_info
import ast
import math
from attrs import define, frozen, field
# from simpleeval import simple_eval
from simpleeval import EvalWithCompoundTypes, FeatureNotAvailable, DISALLOW_PREFIXES, DISALLOW_METHODS
from simpleeval.format import SafeFormatter
from simpleeval.modify import EvalWithModification
from ..ff.types import Args, KwArgs
from ..ff.tricks import IterableItemInfo, MissingType, MISSING, DEFAULT_FUNCTIONS, DEFAULT_NAMES
from ..ff.log_utils import fflog
try:
    from .widget import XWidget
except ImportError:
    # Fake widget, not implemented yet.
    class XWidget:
        NAME: ClassVar[str] = 'Widget'
        _widget_classes: ClassVar[dict[str, type[XWidget]]] = {}
from const import const
if TYPE_CHECKING:
    from simpleeval.format import EvaluatorClass

zip_has_strict = version_info >= (3, 10)


def get_signature(func: str) -> Signature | None:
    try:
        a = ast.parse(func)  # parse
    except SyntaxError:
        return None
    if not a.body:
        return None
    a = a.body[0]  # skip module
    if type(a) is not ast.FunctionDef:
        return None

    def default(expr: ast.expr | None) -> Any:
        if type(expr) is ast.Constant:
            return expr.value
        return Parameter.empty

    params = []
    d = len(a.args.defaults) - len(a.args.posonlyargs) - len(a.args.args)
    for arg in a.args.posonlyargs:
        params.append(Parameter(arg.arg, Parameter.POSITIONAL_ONLY,
                                default=default(None if d < 0 else a.args.defaults[d])))
        d += 1
    for arg in a.args.args:
        params.append(Parameter(arg.arg, Parameter.POSITIONAL_OR_KEYWORD,
                                default=default(None if d < 0 else a.args.defaults[d])))
        d += 1
    if a.args.vararg is not None:
        params.append(Parameter(a.args.vararg.arg, Parameter.VAR_POSITIONAL))
    for arg, df in zip(a.args.kwonlyargs, a.args.kw_defaults):
        params.append(Parameter(arg.arg, Parameter.KEYWORD_ONLY, default=default(df)))
    if a.args.kwarg is not None:
        params.append(Parameter(a.args.kwarg.arg, Parameter.VAR_KEYWORD))
    return Signature(params)


@frozen
class LoopInfo(IterableItemInfo):
    """Information about current loop iteration. info.last is not set for infinite loops."""

    var: str | None
    depth: int
    _template: XmlTemplate = field(kw_only=True, alias='template', repr=False)

    @property
    def parent(self) -> LoopInfo | None:
        if 0 <= self.depth - 1 < len(self._template._loops):
            return self._template._loops[self.depth - 1]
        return None


def loop_iter(iterable, var: str | None, template: XmlTemplate, *, infinite: bool = False) -> Iterator[tuple[LoopInfo, Any]]:
    """s -> (info0, s0), (info1, s1), ..."""
    depth = len(template._loops) - 1
    if infinite:
        for i, item in enumerate(iterable):
            yield LoopInfo(i, False, var=var, depth=depth, template=template), item
        return
    i, it = 0, iter(iterable)
    try:
        prev = next(it)
    except StopIteration:
        return
    while True:
        try:
            item = next(it)
        except StopIteration:
            yield LoopInfo(i, True, var=var, depth=depth, template=template), prev
            return
        yield LoopInfo(i, False, var=var, depth=depth, template=template), prev
        prev = item
        i += 1


@frozen
class TemplateInfo:
    _template: XmlTemplate = field(kw_only=True, alias='template', repr=False)

    @property
    def loop(self) -> LoopInfo | None:
        if self._template._loops:
            return self._template._loops[-1]
        return None


class TextNode(ET.Element):

    def __init__(self, text: str) -> None:
        super().__init__('_')
        self.text = text


class ElementChildren:

    _RX_NAMESPACE = re.compile(r'(?:\{([^}]+)\})?(\w+)')

    def __init__(self, parent: ET.Element) -> None:
        self.parent: ET.Element = parent
        self.index: int = 0

    def __iter__(self) -> Iterator[ET.Element]:
        return self

    def __next__(self) -> ET.Element:
        if 0 <= self.index < len(self.parent):
            index = self.index
            self.index += 1
            return self.parent[index]
        else:
            raise StopIteration()

    def back(self) -> Iterator[ET.Element]:
        if self.index > 0:
            self.index -= 1
        return self

    @property
    def next_element(self) -> ET.Element | None:
        if 0 <= self.index < len(self.parent):
            return self.parent[self.index]
        return None

    @property
    def next_tag(self) -> str:
        if 0 <= self.index < len(self.parent):
            return self.parent[self.index].tag or ''
        return ''

    def next_ff_tag(self, *, strict: bool = True) -> str:
        if 0 <= self.index < len(self.parent):
            tag = self.parent[self.index].tag or ''
            if tag.startswith('{ff}'):
                return tag.partition('}')[2]
            if not strict and '}' not in tag:
                return tag
        return ''

    def next_ns_tag(self) -> tuple[str, str]:
        if 0 <= self.index < len(self.parent):
            tag = self.parent[self.index].tag or ''
            if mch := self._RX_NAMESPACE.fullmatch(tag):
                return mch[1] or '', mch[2]
        return '', ''

    def current(self) -> ET.Element | None:
        if 0 <= self.index - 1 < len(self.parent):
            return self.parent[self.index - 1]
        return None

    def current_ff_tag(self, *, strict: bool = True) -> str:
        if 0 <= self.index - 1 < len(self.parent):
            tag = self.parent[self.index - 1].tag or ''
            if tag.startswith('{ff}'):
                return tag.partition('}')[2]
            if not strict and '}' not in tag:
                return tag
        return ''

    def current_ns_tag(self) -> tuple[str, str]:
        if 0 <= self.index - 1 < len(self.parent):
            tag = self.parent[self.index - 1].tag or ''
            if mch := self._RX_NAMESPACE.fullmatch(tag):
                return mch[1] or '', mch[2]
        return '', ''


class XmlEval(EvalWithModification):

    def __init__(self,
                 operators: dict[ast.operator, Callable] | None = None,
                 functions: dict[str, Callable] | None = None,
                 names: dict[str, Any] | None = None,
                 *,
                 vars: dict[str, Any] | None = None,
                 assignment: bool = False,
                 ) -> None:
        super().__init__(operators, functions, names, vars=vars)
        self.assignment: bool = assignment

    def assign_allowed(self, node: ast.AST) -> bool:
        return self.assignment

    def delete_allowed(self, node: ast.AST) -> bool:
        return self.assignment

    def is_modifiable(self, node: ast.AST) -> bool:
        if not self.assignment:
            return False
        if type(node) is ast.Name and node.id == 'FF':
            return False
        return True

    def assign(self, var: str, value: Any) -> None:
        self.assignment = True
        try:
            node = ast.parse(f'{var} = ...').body[0]
            if type(node) is ast.Assign:
                for target in node.targets:
                    self._target_assign(target, value)
            else:
                raise FeatureNotAvailable(f'Assignment target {ast.dump(node)} is not allowed')
        finally:
            self.assignment = False


class XmlFormatter(SafeFormatter):

    def __init__(self, *,
                 evaluate: bool | EvaluatorClass = True,
                 escape: bool = True,
                 extended: bool = False,
                 names: dict[str, Any] | None = None,
                 functions: dict[str, Callable] | None = None) -> None:
        super().__init__(evaluate=evaluate, escape=escape, extended=extended,
                         names=names, functions=functions)
        self._string_to_format: str = ''
        self._xml_node: ET.Element[str] | None = None

    def vformat(self, format_string: str, args, kwargs) -> str:
        self._string_to_format = format_string
        return super().vformat(format_string, args, kwargs)

    def missing_field(self, field_name: str, args, kwargs) -> tuple[Any, str]:
        if not self._string_to_format.startswith('{ff}') and not self._string_to_format.startswith('{gui}'):
            node_str = f' node <{self._xml_node.tag}>' if self._xml_node is not None else ''
            fflog(f'Unknown field {{{field_name}}} in xml{node_str} in expression: {self._string_to_format!r}')
        return super().missing_field(field_name, args, kwargs)


class XmlTemplate:

    def __init__(self, *, manager: XmlManager | None = None, vars: dict[str, Any] | None = None) -> None:
        from ..kodi import KODI, version_info as kodi_version_info
        self.manager: XmlManager = XmlManager() if manager is None else manager
        self.src_tree: ET.ElementTree[ET.Element[str]] | None = None
        self.src_root: ET.Element[str] | None = None
        self.dst_root: ET.Element[str] | None = None
        self.vars: dict[str, Any] = {} if vars is None else dict(vars)
        self.e_names = {
            **DEFAULT_NAMES,
            'KODI': KODI,
            'kodi_version': kodi_version_info,
            'const': const,
            # 'VAR': self.vars,
        }
        self.e_functions = {
            **DEFAULT_FUNCTIONS,
            'fflog': fflog,
            '___call': self._fake_call,
        }
        self.fmt = XmlFormatter(names=self.e_names, functions=self.e_functions, evaluate=XmlEval)
        self._loops: list[LoopInfo | None] = []
        self._including: list[ET.Element] = []
        self._including_level: int = 0
        self._fake_func_args: tuple[Args, KwArgs] = ((), {})
        self.info: TemplateInfo = TemplateInfo(template=self)

    # def __iter__(self) -> Self:
    #     return self

    def eval(self, expr: str, *, assignment: bool = False) -> Any:
        evaluator = self.fmt.evaluator or self.fmt.make_eval()
        # evaluator.names.update(self.vars)
        if assignment:
            evaluator.assignment = True
            try:
                evaluator.vars = self.vars
                value = None
                for parsed in ast.parse(re.sub(r'^\s*', '', expr, flags=re.MULTILINE)).body:
                    value = evaluator.eval(expr, previously_parsed=parsed)
            finally:
                evaluator.assignment = False
        else:
            value = evaluator.eval(expr)
        return value

    def _var_assign(self, var: str, value: Any) -> Any:
        evaluator: XmlEval = self.fmt.evaluator or self.fmt.make_eval()  # type: ignore[assignment]
        evaluator.vars = self.vars
        evaluator.assign(var, value)
        return value

    def load(self, path: Path):
        self.src_tree = ET.parse(path)
        self.src_root = self.src_tree.getroot()

    def do(self, path: Path | ET.ElementTree[ET.Element[str]] | ET.Element[str], /, *, vars: dict[str, Any] | None = None) -> ET.Element[str] | None:
        if isinstance(path, ET.ElementTree):
            self.src_tree = path
            self.src_root = self.src_tree.getroot()
        elif isinstance(path, ET.Element):
            self.src_tree = None
            self.src_root = path
        else:
            self.load(path)
        if self.src_root is not None:
            if vars is not None:
                self.vars.update(vars)
            self.vars['FF'] = self.info
            it = iter(self._process(self.src_root))
            new_root = next(it, None)
            self.dst_root = new_root
            try:
                next(it)
            except StopIteration:
                pass
            else:
                fflog.error('ERROR, too many root items')
            return self.dst_root
        return None

    def _format(self, e: ET.Element) -> None:
        fmt = self.fmt
        fargs = self.vars
        self.fmt._xml_node = e
        try:
            attrib = dict(e.attrib)
            e.attrib.clear()
            e.attrib.update((fmt.format(a, **fargs), fmt.format(v, **fargs)) for a, v in attrib.items())
            if e.text is not None:
                e.text = fmt.format(e.text, **fargs)
            if e.tail is not None:
                e.tail = fmt.format(e.tail, **fargs)
        finally:
            self.fmt._xml_node = None

    def _new(self, e: ET.Element) -> ET.Element:
        fmt = self.fmt
        fargs = self.vars
        self.fmt._xml_node = e
        try:
            new = ET.Element(e.tag, {fmt.format(a, **fargs): fmt.format(v, **fargs) for a, v in e.attrib.items()})
            if e.text is not None:
                new.text = fmt.format(e.text, **fargs)
            if e.tail is not None:
                new.tail = fmt.format(e.tail, **fargs)
            return new
        finally:
            self.fmt._xml_node = None

    @overload
    def _attr_var(self, elem: ET.Element, default: str | MissingType = MISSING) -> str: ...

    @overload
    def _attr_var(self, elem: ET.Element, default: None) -> str | None: ...

    def _attr_var(self, elem: ET.Element, default: str | MissingType | None = MISSING) -> str | None:
        var = elem.attrib.get('var', default)
        if var is MISSING:
            raise KeyError(f'Attribute "var" is missing in element: {elem.tag}')
        if var == 'FF':
            fflog.error(f'Variable name "FF" is reserved (element: {elem.tag})')
            return None
        if TYPE_CHECKING:
            assert isinstance(var, str) or var is None
        return var

    def _process(self, elem: ET.Element) -> Iterable[ET.Element]:
        # print(f'src: {elem}')
        if not isinstance(elem.tag, str):
            return ()
        # elem_text = next(iter(self._process_text(elem)), None)
        # print(f'Element {elem.tag} text: {None if elem_text is None else elem_text.text!r}')
        if elem.tag.startswith('{ff}'):
            tag = elem.tag.partition('}')[2]
            handler = getattr(self, f'_handle_ff_{tag}', None)
            if handler is not None:
                return handler(elem)
            fflog(f'No {elem.tag!r} handler')
        return self._process_normal(elem)

    def _process_normal(self, elem: ET.Element) -> Iterable[ET.Element]:
        new = self._new(elem)
        for el in elem:
            sub = self._process(el)
            p = new[-1] if len(new) else None
            for e in sub:
                if type(e) is TextNode:
                    e = e.text or ''
                if isinstance(e, str):
                    if p is None:
                        # print(f'Append {e!r} to {new.tag} text')
                        new.text = (new.text or '') + e
                    else:
                        # print(f'Append {e!r} to {p.tag} tail')
                        p.tail = (p.tail or '') + e
                else:
                    new.append(e)
                    p = e
        return (new, )

    def _process_text(self, elem: ET.Element) -> Iterable[ET.Element]:
        if text := elem.text:
            try:
                self.fmt._xml_node = elem
                text = self.fmt.format(text, **self.vars)
            finally:
                self.fmt._xml_node = None
            yield TextNode(text)

    def _process_tail(self, elem: ET.Element) -> Iterable[ET.Element]:
        if text := elem.tail:
            try:
                self.fmt._xml_node = elem
                text = self.fmt.format(text, **self.vars)
            finally:
                self.fmt._xml_node = None
            yield TextNode(text)

    def _process_children(self, elem: ET.Element) -> Iterable[ET.Element]:
        yield from self._process_text(elem)
        for el in elem:
            yield from self._process(el)
            if self._loops and not self._loops[-1]:
                break

    def _close_loop(self, elem: ET.Element, *, broken: bool, empty: bool) -> Iterable[ET.Element]:
        if not broken:
            hit = False
            for el in elem:
                if el.tag == '{ff}else':
                    hit = True
                elif el.tag == '{ff}empty':
                    hit = False
                elif hit:
                    for sub in self._process(el):
                        empty = False
                        yield sub
        if empty:
            hit = False
            for el in elem:
                if el.tag == '{ff}else':
                    hit = False
                elif el.tag == '{ff}empty':
                    hit = True
                elif hit:
                    yield from self._process(el)

    def _loop(self, elem: ET.Element, loop: Iterable[Any], *, var: str | None = None, infinite: bool = False) -> Iterable[ET.Element]:
        assert var != 'FF'
        empty, broken = True, False
        self._loops.append(None)  # to handle "break"
        for info, v in loop_iter(loop, var=var, template=self, infinite=infinite):
            self._loops[-1] = info
            if var is not None:
                if ',' in var:
                    self._var_assign(var, v)
                else:
                    self.vars[var] = v
            yield from self._process_text(elem)
            for el in elem:
                if el.tag in ('{ff}else', '{ff}empty'):
                    break
                for sub in self._process(el):
                    empty = False
                    yield sub
                if not self._loops[-1]:
                    broken = True
                    break
            if broken:
                break
        self._loops.pop()
        yield from self._close_loop(elem, broken=broken, empty=empty)
        if var is not None:
            self.vars.pop(var, None)
        yield from self._process_tail(elem)

    def _fake_call(self, *args, **kwargs) -> None:
        self._fake_func_args = (args, kwargs)

    # --- ff:node handlers ---

    def _handle_ff_set(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Set variable.
        <ff:set var="name" value="expr"/>
        <ff:set var="name"> expr </ff:set>
        """
        var = self._attr_var(elem)
        if (val_str := elem.get('value')) is None:
            val_str = elem.text or ''
        val = self.eval(val_str)
        if ',' in var:
            self._var_assign(var, val)
        else:
            self.vars[var] = val
        return ()

    def _handle_ff_del(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Delete variable.
        <ff:del var="name" />
        """
        var = self._attr_var(elem)
        self.vars.pop(var, None)
        return ()

    def _handle_ff_vars(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Set multiple variables.
        <ff:vars> var1 = expr1 \n var2 = expr2 \n ... </ff:vars>
        """
        self.eval(elem.text or '', assignment=True)
        return ()

    def _handle_ff_if(self, elem: ET.Element) -> Iterable[ET.Element]:
        """If condition.
        <ff:if if="cond" [var="name"]> ... <ff:elif if="cond"/> ... <ff:else> ... </ff:if>
        """
        cond = self.eval(elem.attrib['if'])
        self.vars['if'] = cond
        if var := self._attr_var(elem, None):
            self.vars[var] = cond
        if hit := bool(cond):
            yield from self._process_text(elem)
        for el in elem:
            if el.tag in ('{ff}elif', '{ff}else'):
                if hit:  # end of hit section, any elif/else couldn't hit
                    break
                if 'if' in el.attrib:
                    self.vars['if'] = cond = self.eval(el.attrib['if'])
                    hit = bool(cond)
                elif el.tag == '{ff}elif':
                    fflog.error('<elif> without if="..."')
                    break
                else:
                    hit = not hit
                if hit:
                    if el.text:
                        fflog.warning(f'Text {el.text!r} inside <{el.tag}> is ignored, append it after </{el.tag}>')
                    yield from self._process_tail(el)
            elif hit:
                yield from self._process(el)
        self.vars.pop('if', None)
        if text := elem.tail:
            yield TextNode(text)

    def _handle_ff_break(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Break from loop.
        <ff:break [number="N"] />
        """
        num = int(elem.get('number') or 1)
        if 0 < num <= len(self._loops):
            for i in range(num):
                self._loops[-i-1] = None
        else:
            fflog.warning('Nothing to break')
        return ()

    def _handle_ff_for(self, elem: ET.Element) -> Iterable[ET.Element]:
        """For loop.
        <ff:for var="item" in="iterable">...</ff:for>
        """
        var = self._attr_var(elem)
        loop = self.eval(elem.attrib['in'])
        yield from self._loop(elem, loop, var=var)

    def _handle_ff_while(self, elem: ET.Element) -> Iterable[ET.Element]:
        """While loop.
        <ff:while if="cond">...</ff:while>
        """
        def loop() -> Iterable[bool]:
            while cond := self.eval(expr):
                self.vars['while'] = self.vars['if'] = cond
                yield True
        expr = elem.attrib['if']
        yield from self._loop(elem, loop(), infinite=True)
        self.vars.pop('if', None)
        self.vars.pop('while', None)

    def _handle_ff_statement(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Block with control flow.
        <ff:statement>  <!-- single line from example below -->
            <ff:if if="cond">...</ff:if>  <ff:elif if="cond">...</ff:elif>  <ff:else>...</ff:else>
            <ff:for var="item" in="iterable">...</ff:for>  <ff:else>...</ff:else>  <ff:empty>...</ff:empty>
            <ff:while if="cond">...</ff:while>             <ff:else>...</ff:else>  <ff:empty>...</ff:empty>
        </ff:statement>
        """

        def check_garbage_text(it: ElementChildren) -> None:
            el = it.current()
            if el is not None and el.tail and (text := el.tail.strip()):
                tag = it.current_ff_tag(strict=False)
                fflog.warning(f'Text {text!r} after <ff:statement> <ff:{tag}> is ignored, put it into sub-element')

        def process_loop(el: ET.Element, loop: Iterable[Any], *, var: str | None = None, infinite: bool = False) -> Iterable[ET.Element]:
            self._loops.append(None)  # to handle "break"
            empty, broken = True, False
            for info, v in loop_iter(loop, var=var, template=self, infinite=infinite):
                self._loops[-1] = info
                if var is not None:
                    if ',' in var:
                        self._var_assign(var, v)
                    else:
                        self.vars[var] = v
                if subs := tuple(self._process_children(el)):
                    empty = False
                yield from subs
                if not self._loops[-1]:
                    broken = True
                    break
            self._loops[-1] = LoopInfo(-1, False, var=var, depth=len(self._loops) - 1, template=self)  # ignore "break" in "else" and "empty"
            else_elem = None
            empty_elem = None
            for el in it:
                check_garbage_text(it)
                tag = it.current_ff_tag(strict=False)
                if tag == 'else':
                    else_elem = el
                elif tag == 'empty':
                    empty_elem = el
            if not broken and else_elem is not None:
                if subs := tuple(self._process_children(else_elem)):
                    ...  # I've changed my mind, yield <ff:empty> even if <ff:else> produced something
                    # empty = False
                yield from subs
            if empty and empty_elem is not None:
                yield from self._process_children(empty_elem)
            if var:
                self.vars.pop(var, None)
            self._loops.pop()

        if elem.text and (text := elem.text.strip()):
            fflog.warning(f'Text {text!r} after <ff:statement> is ignored, put it into sub-element')
        it = ElementChildren(elem)
        for el in it:
            tag = it.current_ff_tag(strict=False)
            check_garbage_text(it)

            if tag in ('set', 'del', 'vars'):
                self._process(el)
                continue

            if tag == 'if':
                it.back()
                for i, el in enumerate(it):
                    if i:
                        check_garbage_text(it)
                    tag = it.current_ff_tag(strict=False)
                    if tag == 'elif' or (tag in ('if', 'else') and 'if' in el.attrib):
                        cond = self.vars['if'] = cond = self.eval(el.attrib['if'])
                        if var := self._attr_var(el, None):
                            self.vars[var] = cond
                        if cond:
                            yield from self._process_children(el)
                            break
                    elif tag == 'else':
                        yield from self._process_children(el)
                        break
                    else:
                        fflog.error(f'Unsupported tag {el.tag} in block/if')
                        break
                else:
                    continue  # no any tag hit (all false conditions)
                break  # hit condition, break <ff:block> lookup

            elif tag == 'for':
                var = self._attr_var(el, None)
                yield from process_loop(el, self.eval(el.attrib['in']), var=var)

            elif tag == 'while':
                def loop() -> Iterable[bool]:
                    while cond := self.eval(expr):
                        self.vars['while'] = self.vars['if'] = cond
                        yield True
                expr = elem.attrib['if']
                var = self._attr_var(el, None)
                yield from process_loop(el, loop(), infinite=True)

            else:
                fflog.warning(f'Unsupported <{el.tag}> in <ff:statement>, ignored')

        yield from self._process_tail(elem)

    def _handle_ff_include(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Include definition from module.
        <ff:include module="modulename" name="defname" [args="arg1, arg2, ..."] />
        """
        if xdef := self.manager.include(elem.attrib['module'], elem.get('name')):
            self._including.append(elem)
            old_vars = self.vars
            try:
                args_str = elem.get('args') or ''
                self.vars = deepcopy(self.vars)
                try:
                    self.eval(f'___call({args_str})')
                    args, kwarg = self._fake_func_args
                    self.vars.update(kwarg)
                    if xdef.sig:
                        bind = xdef.sig.bind(*args, **kwarg)
                        bind.apply_defaults()
                        self.vars.update(bind.arguments)
                except Exception as exc:
                    fflog.warning(f'Incorrect include call: {exc}')
                for el in xdef.elems:
                    yield from self._process(el)
            finally:
                self.vars = old_vars
                self._including.pop()

    def _handle_ff_nested(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Process nested element from including.
        <ff:nested />
        """
        if len(self._including) > self._including_level:
            self._including_level += 1
            try:
                yield from self._process_children(self._including[-self._including_level])
            finally:
                self._including_level -= 1

    def _handle_ff_nested_element(self, elem: ET.Element) -> Iterable[ET.Element]:
        if len(self._including) > self._including_level:
            self._including_level += 1
            nested_var = f'__nested_element_{len(self._including) - self._including_level}__'
            try:
                if (nested := self.vars.get(nested_var)) is not None:
                    yield from self._process(nested)
                # else:
                #     yield from self._process_children(self._including[-self._including_level])
            finally:
                self._including_level -= 1

    def _handle_ff_for_nested(self, elem: ET.Element) -> Iterable[ET.Element]:
        if len(self._including) > self._including_level:
            # self._including_level += 1
            nested_var = f'__nested_element_{self._including_level}__'
            try:
                nested = self._including[-self._including_level]
                yield from self._loop(elem, loop=nested, var=f'__nested_element_{self._including_level}__')
            finally:
                self.vars.pop(nested_var, None)
                # self._including_level -= 1

    def _handle_ff_log(self, elem: ET.Element) -> Iterable[ET.Element]:
        """Log message.
        <ff:log level="info|warning|error|debug">MESSAGE</ff:log>
        """
        level = elem.get('level', 'info').lower()
        message = self.fmt.format(elem.text or '', **self.vars)
        if level in ('debug', 'info', 'warning', 'error'):
            getattr(fflog, level)(message)
        else:
            fflog(message)
        return ()

    # --- supported in gui.py ---

    def _do_handle_known(self, elem: ET.Element[str]) -> Iterable[ET.Element[str]]:
        yield from self._process_normal(elem)

    _handle_ff_defines = _do_handle_known
    _handle_ff_macro = _do_handle_known
    _handle_ff_switch = _do_handle_known


@define
class XmlDefine:
    name: str
    root: ET.Element
    elems: list[ET.Element] = field(factory=list)
    args: str = ''
    sig: Signature | None = None


@define
class XmlModule:
    xml: XmlTemplate | None = None
    defines: dict[str, XmlDefine] = field(factory=dict)

    def load(self, path: Path, *, manager: XmlManager) -> bool:
        self.xml = XmlTemplate(manager=manager)
        self.xml.load(path)
        if self.xml.src_root is None:
            return False
        if self.xml.src_root.tag == '{ff}defines':
            for elem in self.xml.src_root:
                if elem.tag == '{ff}define':
                    xdef = XmlDefine(elem.attrib['name'], elem)
                    try:
                        if args := elem.get('args'):
                            xdef.args = args
                            xdef.sig = get_signature(f'def _({args}): ...')
                        for el in elem:
                            xdef.elems.append(el)
                    except Exception as exc:
                        fflog.warning(f'Incorrect include definition: {exc}')
                    if xdef.name:
                        self.defines[xdef.name] = xdef
        return True

    def include(self, name: str) -> XmlDefine | None:
        return self.defines.get(name)

    def include_elems(self, name: str) -> Sequence[ET.Element]:
        xdef = self.defines.get(name)
        return () if xdef is None else xdef.elems


class XmlManager:

    PATH: ClassVar[Sequence[Path]] = [
        Path(__file__).parent.parent.parent / 'resources' / 'skins' / 'gui',
        Path('/tmp'),
    ]

    def __init__(self, *, auto_register: bool = False):
        self.modules: dict[str, XmlModule] = {}
        self.xml: XmlTemplate | None = None
        self.widgets: dict[str, type[XWidget]] = {}
        self.auto_register: bool = auto_register
        try:
            # hack checking if namespace is registered
            ET.register_namespace._namespace_map['ff']  # type: ignore [reportFunctionMemberAccess]
        except Exception:
            self.startup()

    def load(self, path: Path):
        if self.auto_register and not self.widgets:
            self.register_widgets(XWidget._widget_classes.values())
        self.xml = XmlTemplate(manager=self)
        self.xml.load(path)

    def module(self, module: str) -> XmlModule:
        if mod := self.modules.get(module):
            return mod
        mod = XmlModule()
        # mod.load(Path(f'/tmp/{module}.xml'), manager=self)
        for path in self.PATH:
            path = path / f'{module}.xml'
            if path.exists():
                mod.load(path, manager=self)
                break
        return mod

    def include(self, module: str, name: str | None) -> XmlDefine | None:
        if mod := self.module(module):
            return mod.include(name or '')
        return None

    def include_elems(self, module: str, name: str) -> Sequence[ET.Element]:
        if mod := self.module(module):
            return mod.include_elems(name)
        return ()

    def register_widgets(self, widgets: Iterable[type[XWidget]]):
        self.widgets.update(((w.NAME, w) for w in widgets))

    def template(self) -> XmlTemplate:
        template = XmlTemplate(manager=self)
        return template

    @classmethod
    def startup(cls) -> None:
        ET.register_namespace('ff', 'ff')
        ET.register_namespace('gui', 'gui')


if __name__ == '__main__':
    from ..ff.cmdline import DebugArgumentParser
    from .geom import Rect, Size, Point

    def parse_var(s: str) -> tuple[str, Any]:
        """Parse variable from command line."""
        from ast import literal_eval
        name, sep, val = s.partition('=')
        if not sep:
            return name, True
        if val.lower() == 'true':
            return name, True
        if val.lower() == 'false':
            return name, False
        return name, literal_eval(val)

    p = DebugArgumentParser()
    p.add_argument('path', nargs='?', type=Path, metavar='PATH',
                   default=Path(__file__).parent.parent / 'resources/skins/Default/1080i/dev.xml')
    p.add_argument('-v', '--var', action='append', default=[], type=parse_var, metavar='NAME[:TYPE]=VALUE', help='variable')
    args = p.parse_args()

    # ET.register_namespace('ff', 'ff')
    # ET.register_namespace('gui', 'gui')
    # path = Path('~/work/kodi/xulek/plugin.video.fanfilm/resources/skins/Default/1080i/SourcesEdit.xml').expanduser()
    # path = Path('/tmp/a.xml').expanduser()
    path = args.path.expanduser()

    # XmlManager.startup()
    x = XmlTemplate(manager=XmlManager(), vars=dict(args.var))
    if 0:
        x.load(path)
        for e in x:
            print(e, e.attrib)
    elif 1:
        x.do(path)
        # ET.dump(x.src_root)
        if x.dst_root is not None:
            from io import BytesIO
            # ET.dump(x.dst_root)
            tree = ET.ElementTree(x.dst_root)
            f = BytesIO()
            tree.write(f, encoding='utf-8', xml_declaration=True)
            print(f.getvalue().decode('utf-8'))
        print(f'# {x.vars = }')
    elif 0:
        wdg = XWidget(geometry=Rect((1, 2), (3, 4)), template=None)
        print(wdg.to_xml())
    elif 0:
        class XAaa(XWidget):
            NAME = 'aaa'
        class Xbbb(XWidget):
            NAME = 'bbb'
        print(XWidget._widget_classes)
