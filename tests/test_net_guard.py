"""Outbound URL containment for the opt-in network tools.

The threat is not a malicious user — approved shell commands already run as them.
It is a hostile repository talking the model into fetching something on the host or
the LAN and returning it into the conversation. Prompt-injection resistance is not
claimed anywhere, so this check has to be deterministic Python.
"""
import pytest

from coding_agent.browser import Browser
from coding_agent.github import GitHub
from coding_agent.net import BlockedURLError, check_url, get_checked, safe_api_path


def _resolve_to(monkeypatch, address):
    """Pin DNS so the guard is tested, not the network."""
    import ipaddress
    monkeypatch.setattr('coding_agent.net._resolved_ips',
                        lambda host: [ipaddress.ip_address(address)])


# ── addresses that must be refused ──────────────────────────────────────────

@pytest.mark.parametrize('url,address', [
    ('http://169.254.169.254/latest/meta-data/', '169.254.169.254'),   # cloud metadata
    ('http://localhost:8080/admin', '127.0.0.1'),
    ('http://127.0.0.1/', '127.0.0.1'),
    ('http://10.0.0.5/', '10.0.0.5'),
    ('http://192.168.1.1/', '192.168.1.1'),
    ('http://172.16.4.2/', '172.16.4.2'),
    ('http://0.0.0.0/', '0.0.0.0'),
    ('http://[::1]/', '::1'),
    ('http://metadata.google.internal/', '169.254.169.254'),
])
def test_private_and_metadata_targets_are_refused(monkeypatch, url, address):
    _resolve_to(monkeypatch, address)
    with pytest.raises(BlockedURLError):
        check_url(url)


def test_a_public_name_resolving_privately_is_still_refused(monkeypatch):
    """DNS rebinding: the hostname looks fine, the address does not."""
    _resolve_to(monkeypatch, '127.0.0.1')
    with pytest.raises(BlockedURLError, match='loopback'):
        check_url('https://totally-legit.example.com/')


def test_ipv4_mapped_ipv6_loopback_is_unwrapped(monkeypatch):
    """::ffff:127.0.0.1 is loopback wearing a different shape."""
    _resolve_to(monkeypatch, '::ffff:127.0.0.1')
    with pytest.raises(BlockedURLError, match='loopback'):
        check_url('https://sneaky.example.com/')


@pytest.mark.parametrize('url', [
    'file:///etc/passwd',
    'ftp://example.com/x',
    'gopher://example.com/',
    'data:text/html,hello',
    'not-a-url',
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(BlockedURLError):
        check_url(url)


def test_unresolvable_host_is_refused(monkeypatch):
    import socket
    def boom(host):
        raise socket.gaierror('nope')
    monkeypatch.setattr('coding_agent.net._resolved_ips', boom)
    with pytest.raises(BlockedURLError, match='cannot resolve'):
        check_url('https://nx.example.com/')


# ── addresses that must be allowed ──────────────────────────────────────────

@pytest.mark.parametrize('address', ['93.184.216.34', '8.8.8.8', '2606:2800:220:1:248:1893:25c8:1946'])
def test_public_targets_are_allowed(monkeypatch, address):
    _resolve_to(monkeypatch, address)
    assert check_url('https://example.com/page') == 'https://example.com/page'


def test_the_escape_hatch_skips_the_check_entirely(monkeypatch):
    """Pointing the agent at localhost deliberately must remain possible."""
    _resolve_to(monkeypatch, '127.0.0.1')
    assert check_url('http://localhost:3000/', allow_private=True)


# ── redirects are re-checked, not trusted ───────────────────────────────────

class _Resp:
    def __init__(self, redirect_to=None, text='ok'):
        self.is_redirect = redirect_to is not None
        self.headers = {'location': redirect_to} if redirect_to else {}
        self.next_request = None
        self.text = text

    def raise_for_status(self):
        return None


def test_a_redirect_onto_a_private_address_is_caught(monkeypatch):
    """follow_redirects=True would have checked only the first URL."""
    import ipaddress
    hops = {'https://public.example.com/': '93.184.216.34',
            'http://169.254.169.254/latest/': '169.254.169.254'}
    monkeypatch.setattr('coding_agent.net._resolved_ips',
                        lambda host: [ipaddress.ip_address(
                            '169.254.169.254' if '169.254' in host else '93.184.216.34')])
    monkeypatch.setattr('httpx.get',
                        lambda url, **kw: _Resp(redirect_to='http://169.254.169.254/latest/'))
    with pytest.raises(BlockedURLError, match='link-local'):
        get_checked('https://public.example.com/')
    assert hops


def test_a_redirect_chain_terminates(monkeypatch):
    import ipaddress
    monkeypatch.setattr('coding_agent.net._resolved_ips',
                        lambda host: [ipaddress.ip_address('93.184.216.34')])
    monkeypatch.setattr('httpx.get',
                        lambda url, **kw: _Resp(redirect_to='https://example.com/loop'))
    with pytest.raises(BlockedURLError, match='too many redirects'):
        get_checked('https://example.com/start')


def test_a_normal_response_comes_back(monkeypatch):
    import ipaddress
    monkeypatch.setattr('coding_agent.net._resolved_ips',
                        lambda host: [ipaddress.ip_address('93.184.216.34')])
    monkeypatch.setattr('httpx.get', lambda url, **kw: _Resp(text='hello world'))
    assert get_checked('https://example.com/').text == 'hello world'


# ── the GitHub path cannot re-target the host ───────────────────────────────

@pytest.mark.parametrize('path', [
    '//evil.com/steal',
    'https://evil.com/steal',
    '/repos/x@evil.com',
    'repos/no-leading-slash',
    '',
])
def test_api_paths_that_would_move_the_host_are_refused(path):
    """github_get attaches a bearer token; the wrong host means a leaked credential."""
    with pytest.raises(BlockedURLError):
        safe_api_path(path)


@pytest.mark.parametrize('path', [
    '/repos/octocat/hello-world',
    '/user/repos?per_page=5',
    '/search/issues?q=state%3Aopen',
])
def test_ordinary_api_paths_pass(path):
    assert safe_api_path(path) == path


# ── the tools refuse before reaching the network ────────────────────────────

def test_browser_fetch_refuses_a_blocked_target(monkeypatch):
    _resolve_to(monkeypatch, '169.254.169.254')
    called = []
    monkeypatch.setattr('httpx.get', lambda *a, **k: called.append(a))
    with pytest.raises(BlockedURLError):
        Browser(enabled=True).fetch('http://169.254.169.254/latest/meta-data/')
    assert called == [], 'no request may be made for a blocked URL'


def test_disabled_browser_still_reports_being_disabled(monkeypatch):
    _resolve_to(monkeypatch, '93.184.216.34')
    with pytest.raises(PermissionError, match='disabled'):
        Browser(enabled=False).fetch('https://example.com/')


def test_github_refuses_a_retargeting_path(monkeypatch):
    called = []
    monkeypatch.setattr('httpx.request', lambda *a, **k: called.append(a))
    gh = GitHub(enabled=True)
    with pytest.raises(BlockedURLError):
        gh.request('GET', '//evil.com/x')
    assert called == []
