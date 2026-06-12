#
# This file needs Python 3.11+
#

from typing_extensions import Union, Annotated, TypeForm, TypeVar, get_origin, get_args
from types import NoneType, UnionType

T = TypeVar('T')


def is_union(ann: TypeForm) -> bool:
    """Return True if type is Union."""
    origin = get_origin(ann)
    return origin is Union or origin is UnionType


def is_optional(ann: TypeForm, *, deannotate: bool = False) -> bool:
    """Return True if `ann` is optional. Optional[X] is equivalent to Union[X, None]."""
    if deannotate:
        origin = get_origin(ann)
        if origin is Annotated and (args := get_args(ann)):
            ann = args[0]
    args = get_args(ann)
    # return is_union(ann) and len(args) == 2 and args[1] is type(None)
    return is_union(ann) and any(a is NoneType for a in args)


def remove_optional(ann: TypeForm[T]) -> TypeForm[T]:
    """Remove Optional[X] (if exists) and returns X."""
    args = get_args(ann)
    # if is_union(ann) and len(args) == 2 and args[1] is type(None):
    if is_union(ann):
        if len(args) == 2 and args[1] is NoneType:
            return args[0]
        return Union[*(a for a in args if a is not NoneType)]  # since py 3.11
    return ann
