"""Outbound URL containment for the opt-in network tools.

`browser_fetch`, `browser_open` and `github_get` take a target chosen by the model,
and the model reads untrusted repository content. Prompt-injection resistance is not
claimed anywhere in this project — the deterministic layer is the control point — so
the check belongs here, in Python, rather than in an instruction the model may ignore.

What it stops: a hostile repo talking the agent into fetching cloud-metadata
(`169.254.169.254`), a service on the developer's loopback, or something on the LAN,
and returning the result into the conversation. It also re-checks every redirect hop,
because a public host may redirect to a private one, and resolves the hostname before
judging it, because `evil.com` can simply have an A record pointing at 127.0.0.1.

This is containment for the network tools, not a general security boundary: approved
shell commands still run as the local user. `VELA_ALLOW_PRIVATE_URLS=1` lifts it for
people who genuinely want to point the agent at something on localhost.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES=('http','https')
MAX_REDIRECTS=5


class BlockedURLError(PermissionError):
    """A URL the network tools refuse to reach."""


def _blocked_reason(ip):
    """Why this address is not a legitimate public target, or None."""
    if ip.is_loopback:return 'loopback address'
    if ip.is_link_local:return 'link-local address (cloud metadata lives here)'
    if ip.is_private:return 'private network address'
    if ip.is_reserved:return 'reserved address'
    if ip.is_multicast:return 'multicast address'
    if ip.is_unspecified:return 'unspecified address'
    return None


def _resolved_ips(host):
    """Every address a hostname resolves to; a literal IP resolves to itself."""
    try:
        return [ipaddress.ip_address(host.strip('[]'))]
    except ValueError:
        pass
    infos=socket.getaddrinfo(host,None)
    out=[]
    for info in infos:
        try:out.append(ipaddress.ip_address(info[4][0]))
        except ValueError:continue
    return out


def check_url(url,allow_private=False):
    """Raise BlockedURLError unless this URL is a public http(s) target.

    Judged on the *resolved* addresses, not the hostname, so a DNS record pointing
    at a private range is caught. An IPv4-mapped IPv6 address is unwrapped first,
    since `::ffff:127.0.0.1` is loopback wearing a different shape.
    """
    parsed=urlparse(str(url))
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedURLError(f'only http/https URLs are allowed, got {parsed.scheme or "no"} scheme')
    host=parsed.hostname
    if not host:raise BlockedURLError(f'no host in URL: {url}')
    if allow_private:return url
    try:
        addresses=_resolved_ips(host)
    except (socket.gaierror,UnicodeError) as exc:
        raise BlockedURLError(f'cannot resolve host {host}: {exc}') from exc
    if not addresses:raise BlockedURLError(f'host {host} resolved to no address')
    for ip in addresses:
        if getattr(ip,'ipv4_mapped',None) is not None:ip=ip.ipv4_mapped
        reason=_blocked_reason(ip)
        if reason:
            raise BlockedURLError(
                f'refusing to fetch {host} ({ip}): {reason}. '
                'Set VELA_ALLOW_PRIVATE_URLS=1 if this is deliberate.')
    return url


def get_checked(url,allow_private=False,timeout=20,headers=None):
    """HTTP GET that validates every redirect hop rather than trusting the first.

    `follow_redirects=True` would check only the URL the model supplied, letting a
    public host bounce the request onto a private one.
    """
    import httpx
    seen=[]
    for _ in range(MAX_REDIRECTS+1):
        check_url(url,allow_private)
        seen.append(url)
        r=httpx.get(url,follow_redirects=False,timeout=timeout,headers=headers or {})
        if r.is_redirect and r.headers.get('location'):
            url=str(r.next_request.url) if r.next_request is not None else r.headers['location']
            continue
        r.raise_for_status()
        return r
    raise BlockedURLError(f'too many redirects ({len(seen)}): {" -> ".join(seen[:3])} …')


def safe_api_path(path):
    """Validate a path appended to a fixed API base.

    A value like `//evil.com/x` or `https://evil.com` would re-target the request —
    and `github_get` attaches a bearer token, so the wrong host means a leaked
    credential.
    """
    text=str(path or '')
    if not text.startswith('/'):
        raise BlockedURLError(f'API path must start with "/", got {text[:60]!r}')
    if text.startswith('//'):
        raise BlockedURLError('API path must not start with "//" (that re-targets the host)')
    if '://' in text or '@' in text.split('?',1)[0]:
        raise BlockedURLError(f'API path must not contain a scheme or userinfo: {text[:60]!r}')
    return text
