"""Offline checks for reviewed Chewy links; never generate tracking URLs."""
from urllib.parse import urlparse


def secure_url(url, hosts):
    if not isinstance(url, str) or any(c.isspace() or c == "\\" for c in url):
        return False
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "https" and parsed.hostname in hosts
                and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and bool(parsed.path.strip("/")))
    except ValueError:
        return False


def valid_chewy_link(url, source, program):
    """Require approval and an exact, dated record of a dashboard-issued link.

    Hostname alone cannot prove that a link credits this account. The exact
    affiliate URL must also match the link reviewed in our Impact dashboard.
    """
    return bool(
        program.get("status") == "approved"
        and source.get("catalog_status") != "retired"
        and secure_url(url, program.get("verified_tracking_hosts", []))
        and url == source.get("chewy_affiliate_url")
        and secure_url(source.get("chewy_product_url"), {"www.chewy.com", "chewy.com"})
        and source.get("chewy_checked")
        and source.get("chewy_verified_variant")
        and source.get("chewy_link_source")
    )
