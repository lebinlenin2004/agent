import socket

import httpx

from app.core.config import Settings
from app.research.fetcher import PoliteFetcher


def _fetcher(handler, monkeypatch, **kw):
    # Pretend every hostname resolves to a public address, except names containing 'internal'.
    def fake_gai(host, *a, **k):
        ip = "10.0.0.5" if "internal" in host else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
    s = Settings(per_domain_delay_seconds=0, http_max_retries=2, user_agent="TestBot/1.0", **kw)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return PoliteFetcher(s, client)


def test_robots_disallow_is_respected(monkeypatch):
    hits = []

    def handler(req):
        hits.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /careers/private")
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})

    f = _fetcher(handler, monkeypatch)
    blocked = f.fetch("https://example.com/careers/private/1")
    assert blocked.blocked_by_robots and "/careers/private/1" not in hits
    assert f.fetch("https://example.com/careers/open").ok


def test_robots_server_error_means_do_not_fetch(monkeypatch):
    f = _fetcher(lambda r: httpx.Response(503), monkeypatch)
    assert f.fetch("https://example.com/careers").blocked_by_robots


def test_retries_then_succeeds_and_gives_up(monkeypatch):
    calls = {"n": 0}

    def flaky(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(200, text="<p>hi</p>", headers={"content-type": "text/html"}) if calls["n"] == 3 \
            else httpx.Response(503, headers={"retry-after": "0"})

    assert _fetcher(flaky, monkeypatch).fetch("https://example.com/a").ok and calls["n"] == 3

    f = _fetcher(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(503, headers={"retry-after": "0"}), monkeypatch)
    res = f.fetch("https://example.com/b")
    assert not res.ok and res.error == "HTTP 503"


def test_timeout_reported_not_raised(monkeypatch):
    def boom(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr("time.sleep", lambda s: None)
    res = _fetcher(boom, monkeypatch).fetch("https://example.com/slow")
    assert res.error == "Timed out" and not res.ok


def test_redirect_to_internal_host_is_blocked(monkeypatch):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://internal.corp/secret"})
        return httpx.Response(200, text="SECRET")
    res = _fetcher(handler, monkeypatch).fetch("https://example.com/jump")
    assert not res.ok and "SECRET" not in res.text and res.error == "URL not allowed"


def test_internal_hosts_never_fetched(monkeypatch):
    f = _fetcher(lambda r: httpx.Response(200, text="x"), monkeypatch)
    assert f.fetch("https://internal.corp/").error == "URL not allowed"


def test_oversized_responses_are_truncated(monkeypatch):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"a" * 50_000, headers={"content-type": "text/html"})
    res = _fetcher(handler, monkeypatch, max_response_bytes=1000).fetch("https://example.com/big")
    assert res.ok and len(res.text) <= 1000
