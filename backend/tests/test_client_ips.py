import pytest
from starlette.requests import Request

from backend.app.channels import ChannelSettings, client_ip


@pytest.mark.parametrize(("peer", "forwarded", "expected"), [
    ("192.0.2.50", ["203.0.113.1"], "192.0.2.50"),
    ("192.0.2.10", ["203.0.113.1, 198.51.100.20, 10.2.0.1"], "198.51.100.20"),
    ("192.0.2.10", ["malformed, 198.51.100.20", "10.2.0.1"], "198.51.100.20"),
    ("192.0.2.10", ["203.0.113.1, malformed, 10.2.0.1"], "192.0.2.10"),
    ("192.0.2.10", ["10.2.0.2, 10.2.0.1"], "192.0.2.10"),
    ("192.0.2.10", [], "192.0.2.10"),
    ("2001:db8:44:2::1", ["203.0.113.1"], "2001:db8:44:2::/64"),
    ("2001:db8:99::1", ["2001:db8:44:2::1"], "2001:db8:44:2::/64"),
    ("::ffff:192.0.2.10", ["::ffff:198.51.100.20"], "198.51.100.20"),
])
def test_client_identity_uses_only_a_trusted_proxy_suffix(monkeypatch, peer, forwarded, expected):
    monkeypatch.setenv("SOURCED_TRUSTED_PROXIES", "192.0.2.10, 10.2.0.0/24, 2001:db8:99::/64")
    request = Request({
        "type": "http",
        "client": (peer, 4000),
        "headers": [(b"x-forwarded-for", value.encode()) for value in forwarded],
        "state": {"channels": ChannelSettings.from_env()},
    })
    assert client_ip(request) == expected


def test_invalid_trusted_proxies_fail_configuration(monkeypatch):
    monkeypatch.setenv("SOURCED_TRUSTED_PROXIES", "192.0.2.10, *")
    with pytest.raises(ValueError, match="SOURCED_TRUSTED_PROXIES"):
        ChannelSettings.from_env()
