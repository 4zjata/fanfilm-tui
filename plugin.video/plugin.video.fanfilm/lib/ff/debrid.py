# -*- coding: utf-8 -*-

from __future__ import annotations
from typing import TYPE_CHECKING
from lib.ff import control
from lib.ff.log_utils import fflog
if TYPE_CHECKING:
    from resolveurl.resolver import ResolveUrl


#: Global list of debrid resolvers (internal)
_debrid_resolvers: list[ResolveUrl] | None = None
#: Global list of debrid resolvers
debrid_resolvers: list[ResolveUrl]


def get_resolvers() -> list[ResolveUrl]:
    """Get a list of available debrid resolvers."""
    global _debrid_resolvers
    if _debrid_resolvers is None:
        try:
            import resolveurl

            _debrid_resolvers = [
                resolver()
                for resolver in resolveurl.relevant_resolvers(order_matters=True)
                if resolver.isUniversal()
            ]
            if len(_debrid_resolvers) == 0:
                # Support Rapidgator accounts! Unfortunately, `sources.py` assumes that rapidgator.net is only ever
                # accessed via a debrid service, so we add rapidgator as a debrid resolver and everything just works.
                # As a bonus(?), rapidgator links will be highlighted just like actual debrid links
                _debrid_resolvers = [
                    resolver()
                    for resolver in resolveurl.relevant_resolvers(
                        order_matters=True, include_universal=False
                    )
                    if "rapidgator.net" in resolver.domains
                ]
        except Exception:
            _debrid_resolvers = []
    return _debrid_resolvers


def status() -> bool:
    """Check if any debrid resolvers are enabled."""
    return bool(_debrid_resolvers)


def tor_enabled() -> bool:
    enabled = control.setting('torrent.enabled')  # XXX missing setting 'torrent.enabled'
    return enabled == 'true'


def resolver(url, debrid) -> str | None:
    """Resolve a URL using the specified debrid service."""
    try:
        if debrid_resolver := next(iter(resolver for resolver in get_resolvers() if resolver.name == debrid), None):
            debrid_resolver.login()
            if host_and_id := debrid_resolver.get_host_and_id(url):
                host, media_id = host_and_id
                stream_url = debrid_resolver.get_media_url(host, media_id)
                return stream_url
    except Exception as e:
        fflog.warning(f'{debrid} Resolve Failure: {e}')
    return None


def __getattr__(name: str) -> list[type[ResolveUrl]]:
    """Lazy load debrid resolvers."""
    if name == 'debrid_resolvers':
        return get_resolvers()
    raise AttributeError(name)
