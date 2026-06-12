
from __future__ import annotations
import re
from typing import Optional, Any, Tuple, List, Sequence, Iterator, TYPE_CHECKING
from typing_extensions import TypedDict, Unpack, NotRequired, Type, Literal, Self, get_args as get_typing_args
from contextlib import contextmanager
from argparse import ArgumentParser, Namespace, Action, ArgumentError
from ..defs import MediaRef, VideoIds, RefType
# from ..api.tmdb import MediaRequest
if TYPE_CHECKING:
    from argparse import _FormatterClass, _SubParsersAction
    from .ratings import RatingService


class ParserKwArgs(TypedDict):
    prog: NotRequired[Optional[str]]
    usage: NotRequired[Optional[str]]
    description: NotRequired[Optional[str]]
    epilog: NotRequired[Optional[str]]
    parents: NotRequired[Sequence[ArgumentParser]]
    formatter_class: NotRequired[_FormatterClass]
    prefix_chars: NotRequired[str]
    fromfile_prefix_chars: NotRequired[Optional[str]]
    argument_default: NotRequired[Optional[str]]
    conflict_handler: NotRequired[Literal['error', 'resolve']]
    add_help: NotRequired[bool]
    allow_abbrev: NotRequired[bool]
    exit_on_error: NotRequired[bool]


class AddSubparsersKwArgs(TypedDict):
    title: NotRequired[str]
    description: NotRequired[Optional[str]]
    prog: NotRequired[Any]
    parser_class: NotRequired[Type[ArgumentParser]]
    action: NotRequired[str]
    option_strings: NotRequired[Optional[str]]
    dest: NotRequired[Optional[str]]
    required: NotRequired[bool]
    help: NotRequired[Optional[str]]
    metavar: NotRequired[Optional[str]]


class AddParserKwArgs(TypedDict):
    help: NotRequired[Optional[str]]
    aliases: NotRequired[Sequence[str]]
    prog: NotRequired[Optional[str]]
    usage: NotRequired[Optional[str]]
    description: NotRequired[Optional[str]]
    epilog: NotRequired[Optional[str]]
    parents: NotRequired[Sequence[ArgumentParser]]
    formatter_class: NotRequired[_FormatterClass]
    prefix_chars: NotRequired[str]
    fromfile_prefix_chars: NotRequired[Optional[str]]
    argument_default: NotRequired[Any]
    conflict_handler: NotRequired[str]
    add_help: NotRequired[bool]
    allow_abbrev: NotRequired[bool]
    exit_on_error: NotRequired[bool]


class RatingAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        from .ratings import NORM_MIN, NORM_MAX
        try:
            if isinstance(values, list) and len(values) > 1:
                val, *refs = values
                if '.' in val:
                    val = float(val)
                else:
                    val = int(val)
                if 1 or NORM_MIN <= val <= NORM_MAX:
                    setattr(namespace, self.dest, [val, *(parse_ref(ref) for ref in refs)])
                    return
        except Exception:
            pass
        raise ArgumentError(self, f'RATING REF ... ({NORM_MIN}..{NORM_MAX} movie/ID|show/ID[/SEASON[/EPISODE]]...)')


def rating_action(service: RatingService) -> Type[Action]:
    """Factory for RatingAction class."""

    class XRatingAction(Action):
        def __call__(self, parser, namespace, values, option_string=None):
            from .ratings import NORM_MIN, NORM_MAX
            try:
                if isinstance(values, list) and len(values) > 1:
                    val, *refs = values
                    val = service.type(val)
                    if NORM_MIN <= val <= NORM_MAX:
                        setattr(namespace, self.dest, [val, *(parse_ref(ref) for ref in refs)])
                        return
            except Exception:
                pass
            raise ArgumentError(self, f'RATING REF ... ({NORM_MIN}..{NORM_MAX} movie/ID|show/ID[/SEASON[/EPISODE]]...)')

    return XRatingAction


class DebugArgumentParser(ArgumentParser):

    def __init__(self, *, dest: Optional[str] = None, **kwargs: Unpack[ParserKwArgs]) -> None:
        super().__init__(**kwargs)
        self._ff_subparsers_dest: Optional[str] = dest
        self._ff_subparsers: Optional[_SubParsersAction] = None

    def parse_known_args(self,  # type: ignore
                         args: Optional[Sequence[str]] = None,
                         namespace: Any = None,
                         ) -> Tuple[Namespace, List[str]]:
        if args is None:
            from .. import cmdline_argv
            args = cmdline_argv[1:]
        return super().parse_known_args(args, namespace)

    @contextmanager
    def with_subparser(self, name: str, dest: Optional[str] = None, **kwargs: Unpack[AddParserKwArgs]) -> Iterator[Self]:
        if self._ff_subparsers is None:
            pp_kwargs = {}
            if self._ff_subparsers_dest is not None:
                pp_kwargs['dest'] = self._ff_subparsers_dest
            self._ff_subparsers = self.add_subparsers(**pp_kwargs)
        yield self._ff_subparsers.add_parser(name, dest=dest, **kwargs)


def parse_ref(v: str, *, type: str | None = None) -> MediaRef:
    ref_type_pat = '|'.join(t for t in get_typing_args(RefType) if t)
    if mch := re.fullmatch(r'movie/(\d+)', v):
        media, ids = 'movie', [int(mch[1])]
    elif mch := re.fullmatch(r'show/(\d+(?:/\d+(?:/\d+)?)?)', v):
        media, ids = 'show', list(map(int, mch[1].split('/')))
    elif mch := re.fullmatch(rf'({ref_type_pat})/(?:(\d+)|(?:))', v):
        media, ids = mch[1], [int(mch[2])]
        if TYPE_CHECKING:
            assert media in ('movie', 'show', 'season', 'episode', 'person', 'collection', 'company', 'keyword', 'network',
                             # extra types (has no tmdb details)
                             'genre', 'language', 'country', 'list')
    elif mch := re.fullmatch(r'person/(\d+)', v):
        media, ids = 'person', [int(mch[1])]
    elif mch := re.fullmatch(r'movie/((?:(?:tmdb|imdb|trakt|mdblist)[:.])?\w+)', v):
        media, ids = 'movie', [VideoIds.guess_id(mch[1].replace('.', ':')).ffid]
    elif mch := re.fullmatch(r'show/((?:(?:tmdb|imdb|trakt|mdblist)[:.])?\w+)(?:/(\d+(?:/\d+)?))?', v):
        media, ids = 'show', [VideoIds.guess_id(mch[1].replace('.', ':')).ffid, *(int(x) for x in (mch[2] or '').split('/') if x)]
    elif v[:1] in 'Mm':
        media, ids = 'movie', [int(v[1:])]
    elif v[:1] in 'Tts':
        media, ids = 'show', list(map(int, v[1:].split('/')))
    elif v[:1] in 'S':
        media, ids = 'season', [int(v[1:])]
    elif v[:1] in 'E':
        media, ids = 'episode', [int(v[1:])]
    elif v[:1] in 'Pp':
        media, ids = 'person', [int(v[1:])]
    elif type:
        return parse_ref(f'{type}/{v}')
    else:
        raise ValueError(f'Incorrect media ref {v!r}')
    if ids[0] < 0:
        ids[0] = -ids[0]
    elif ids[0] < VideoIds.TMDB.start:
        ids[0] += VideoIds.TMDB.start
    return MediaRef(media, *ids)


def parse_movie_ref(v: str) -> MediaRef:
    return parse_ref(f'movie/{v}')


def parse_show_ref(v: str) -> MediaRef:
    return parse_ref(f'show/{v}')


if __name__ == '__main__':
    p = DebugArgumentParser(dest='x')
    with p.with_subparser('aa') as o:
        o.add_argument('-a')
    with p.with_subparser('bb') as o:
        o.add_argument('-b')
    print(p.parse_args())
