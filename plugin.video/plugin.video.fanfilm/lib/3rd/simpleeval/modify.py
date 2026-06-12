
from __future__ import annotations
from typing_extensions import Union, Any, Iterable, Callable, TypeGuard, TYPE_CHECKING
from functools import partial
from itertools import zip_longest
import ast
import warnings
from . import EvalWithCompoundTypes, FeatureNotAvailable, DISALLOW_PREFIXES, DISALLOW_METHODS, AssignmentAttempted


EvalModExecutor = Union[Callable[[Any], None], Callable[[], None]]  # for Python 3.9 compatibility


class MISSING:
    pass


class EvalWithModification(EvalWithCompoundTypes):

    nodes: dict[type[ast.AST], Callable[[ast.AST], Any]]
    mod_nodes: dict[type[ast.AST], Callable[[ast.AST], EvalModExecutor]]

    def __init__(self,
                 operators: dict[ast.operator, Callable] | None = None,
                 functions: dict[str, Callable] | None = None,
                 names: dict[str, Any] | None = None,
                 *,
                 vars: dict[str, Any] | None = None,
                 ) -> None:
        super().__init__(operators, functions, names)
        self.vars: dict[str, Any] = {} if vars is None else vars  # no copy
        self.nodes[ast.AnnAssign] = self._eval_ann_assign
        self.nodes[ast.NamedExpr] = self._eval_named_expr
        self.nodes[ast.Delete] = self._eval_delete
        self.mod_nodes = {
            ast.Name: self._mod_eval_name,
            ast.Tuple: self._mod_eval_tuple,
            ast.Subscript: self._mod_eval_subscript,
            ast.Attribute: self._mod_eval_attribute,
        }

    def assign_allowed(self, node: ast.AST) -> bool:
        return True

    def delete_allowed(self, node: ast.AST) -> bool:
        return True

    def is_modifiable(self, node: ast.AST) -> bool:
        return True

    def _eval_name(self, node: ast.AST) -> Any:
        assert type(node) is ast.Name
        try:
            return self.vars[node.id]
        except KeyError:
            pass
        return super()._eval_name(node)

    def _target_assign(self, target: ast.AST, value: Any) -> None:
        if not self.is_modifiable(target):
            raise FeatureNotAvailable(f'Assignment target {ast.dump(target)} is not allowed')
        setter = self._mod_eval(target)
        if TYPE_CHECKING:
            def is_call_with_arg(_: Callable[..., Any]) -> TypeGuard[Callable[[Any], Any]]:
                return True
            assert is_call_with_arg(setter)
        setter(value)

    def _eval_assign(self, node: ast.AST) -> Any:
        assert type(node) is ast.Assign
        if not self.assign_allowed(node):
            return super()._eval_assign(node)
        val = self._eval(node.value)
        for target in node.targets:
            self._target_assign(target, val)
        return val

    def _eval_ann_assign(self, node: ast.AST) -> Any:
        assert type(node) is ast.AnnAssign
        if not self.assign_allowed(node):
            return super()._eval_assign(node)
        val = self._eval(node.value)
        self._target_assign(node.target, val)
        return val

    def _eval_named_expr(self, node: ast.AST) -> Any:
        assert type(node) is ast.NamedExpr
        val = self._eval(node.value)
        if self.assign_allowed(node):
            self._target_assign(node.target, val)
        else:
            warnings.warn(f'Assignment in expression is not allowed: {ast.dump(node)}', AssignmentAttempted)
        return val

    def _eval_delete(self, node: ast.AST) -> Any:
        assert type(node) is ast.Delete
        if not self.delete_allowed(node):
            raise FeatureNotAvailable('Deletion is not allowed in this evaluator')
        for target in node.targets:
            if not self.is_modifiable(target):
                raise FeatureNotAvailable(f'Deletion target {ast.dump(target)} is not allowed')
            deleter = self._mod_eval(target)
            if TYPE_CHECKING:
                def is_call_no_args(_: Callable[..., Any]) -> TypeGuard[Callable[[], Any]]:
                    return True
                assert is_call_no_args(deleter)
            deleter()
        return None

    def _mod_eval(self, node: ast.AST) -> EvalModExecutor:
        try:
            handler = self.mod_nodes[type(node)]
        except KeyError:
            raise FeatureNotAvailable('Sorry, modification {0} is not available in this evaluator'.format(type(node).__name__))
        return handler(node)

    def _mod_eval_name(self, node: ast.AST) -> EvalModExecutor:
        assert type(node) is ast.Name
        # if node.id == 'FF':
        #     raise FeatureNotAvailable('`FF` could not be modified')
        if type(node.ctx) is ast.Store:
            return partial(self.vars.__setitem__, node.id)
        elif type(node.ctx) is ast.Del:
            # def deleter(value: Any) -> None:
            #     del self.vars[node.id]
            #     del self.names[node.id]
            # return deleter
            return partial(self.vars.__delitem__, node.id)
        raise AssertionError(f'Unsupported name context: {type(node.ctx).__name__}')

    def _mod_eval_tuple(self, node: ast.AST) -> EvalModExecutor:
        assert type(node) is ast.Tuple
        if type(node.ctx) is ast.Store:
            def setter(values: Iterable[Any]) -> None:
                def value_iter() -> Iterable[Any]:
                    while True:
                        try:
                            yield next(val_iter)
                        except StopIteration:
                            break
                starred_var = None
                val_iter = iter(values)
                i = 0
                for val, target in zip_longest(value_iter(), node.elts, fillvalue=MISSING):
                    if target is MISSING:
                        raise ValueError(f'too many values to unpack (expected {len(node.elts)})')
                    if TYPE_CHECKING:
                        assert isinstance(target, ast.AST)
                    if type(target) is ast.Starred and type(target.value) is ast.Name:
                        if starred_var is not None:
                            raise SyntaxError('multiple starred expressions in assignment')
                        starred_var = target.value.id
                        if val is MISSING:
                            star_values = list(val_iter)
                        else:
                            star_values = [val, *val_iter]
                        split = len(star_values) - (len(node.elts) - i - 1)
                        val_iter = iter(star_values[split:])
                        del star_values[split:]
                        self.vars[starred_var] = star_values
                    else:
                        if val is MISSING:
                            if starred_var is None:
                                raise ValueError(f'not enough values to unpack (expected {len(node.elts)}, got {i})')
                            else:
                                raise ValueError(f'not enough values to unpack (expected at least {len(node.elts) - 1}, got {i})')
                        mod_setter = self._mod_eval(target)
                        if TYPE_CHECKING:
                            def is_call_with_arg(_: Callable[..., Any]) -> TypeGuard[Callable[[Any], Any]]:
                                return True
                            assert is_call_with_arg(mod_setter)
                        mod_setter(val)
                        i += 1
            return setter
        raise AssertionError(f'Unsupported tuple context: {type(node.ctx).__name__}')

    def _mod_eval_subscript(self, node: ast.AST) -> EvalModExecutor:
        assert type(node) is ast.Subscript
        container = self._eval(node.value)
        key = self._eval(node.slice)
        if type(node.ctx) is ast.Store:
            return partial(container.__setitem__, key)
        elif type(node.ctx) is ast.Del:
            return partial(container.__delitem__, key)
        raise AssertionError(f'Unsupported subscript context: {type(node.ctx).__name__}')

    def _mod_eval_attribute(self, node) -> EvalModExecutor:
        for prefix in DISALLOW_PREFIXES:
            if node.attr.startswith(prefix):
                raise FeatureNotAvailable('Sorry, access to __attributes or func_ attributes is not available. ({0})'.format(node.attr))
        if node.attr in DISALLOW_METHODS:
            raise FeatureNotAvailable('Sorry, this method is not available. ({0})'.format(node.attr))
        # eval node
        obj = self._eval(node.value)
        if type(node.ctx) is ast.Store:
            return partial(obj.__setattr__, node.attr)
        elif type(node.ctx) is ast.Del:
            return partial(obj.__delattr__, node.attr)
        raise AssertionError(f'Unsupported subscript context: {type(node.ctx).__name__}')
