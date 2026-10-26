"""Свой ли сайт прислал запрос: сверка Origin/Referer с BASE_URL (защита от CSRF, в том числе для WebSocket)."""

from urllib.parse import urlsplit

from app.config import settings

_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(value: str) -> str | None:
    """«схема://хост[:порт]» из Origin или полного URL (Referer); None, если не разобрать."""
    try:
        parts = urlsplit(value.strip())
        scheme, host, port = parts.scheme.lower(), parts.hostname, parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None  # в том числе «Origin: null»
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}" + (f":{port}" if port and port != _DEFAULT_PORTS[scheme] else "")


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _loopback_aliases(origin: str) -> set[str]:
    """Локально 127.0.0.1, localhost и [::1] — один и тот же сайт: открыть можно по любому из адресов."""
    parts = urlsplit(origin)
    if parts.hostname not in _LOOPBACK:
        return {origin}
    port = f":{parts.port}" if parts.port else ""
    return {f"{parts.scheme}://{f'[{h}]' if ':' in h else h}{port}" for h in _LOOPBACK}


def same_site(header: str | None) -> bool:
    source = origin_of(header) if header is not None else None
    if source is None:
        return False
    own = origin_of(settings.base_url)
    if source == own:
        return True
    return settings.debug and own is not None and source in _loopback_aliases(own)
