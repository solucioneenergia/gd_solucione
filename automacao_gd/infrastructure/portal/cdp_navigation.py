from urllib.parse import urlparse


PORTAL_GD_HOST = "gdneoenergiapernambuco.neoenergia.com"


def is_insecure_portal_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "http" and parsed.netloc.lower() == PORTAL_GD_HOST


def _is_unsafe_navigation_url(current_url: str | None, listing_url: str | None) -> bool:
    current = urlparse(current_url or "")
    listing = urlparse(listing_url or "")
    if is_insecure_portal_http_url(listing_url or ""):
        return True
    if listing.scheme and listing.scheme.lower() != "https":
        return True
    if current.scheme.lower() in {"about", "edge", "chrome"}:
        return True
    if not current.netloc:
        return True
    if is_insecure_portal_http_url(current_url or ""):
        return True
    expected_host = urlparse(listing_url or "").netloc.lower()
    if expected_host and current.netloc.lower() != expected_host:
        return True
    if _is_portal_root_or_index_url(current_url or "", expected_host):
        return True
    return False


def _is_unsafe_authenticated_control_context_url(
    current_url: str | None,
    listing_url: str | None,
) -> bool:
    current = urlparse(current_url or "")
    if current.scheme.lower() in {"about", "edge", "chrome"}:
        return True
    if not current.netloc:
        return True
    if is_insecure_portal_http_url(current_url or ""):
        return True
    expected_host = urlparse(listing_url or "").netloc.lower()
    if expected_host and current.netloc.lower() != expected_host:
        return True
    return False


def _is_portal_root_or_index_url(url: str, expected_host: str | None = None) -> bool:
    parsed = urlparse(url or "")
    host = (expected_host or PORTAL_GD_HOST).lower()
    if parsed.netloc.lower() != host:
        return False
    path = (parsed.path or "/").rstrip("/").lower()
    return path in {"", "/index.jsf"}
