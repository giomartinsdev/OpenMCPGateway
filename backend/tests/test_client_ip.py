"""Which address the traffic log blames, and when a header is a claim.

`X-Forwarded-For` is written by the caller when the caller talks to us directly,
and by the proxy when the proxy does. The difference between those two cases is
the whole audit trail, so the decision is pinned here rather than only through a
live server: the interesting inputs (a chain, a CIDR, a forged left-most entry)
are cheaper to state than to reach.
"""

from starlette.datastructures import Headers

from gateway.proxy.guard import _client_ip

PEER = ("10.244.3.7", 51234)


def _scope(peer=PEER):
    return {"type": "http", "client": peer}


def test_no_trusted_proxy_means_the_header_is_only_a_claim():
    headers = Headers({"x-forwarded-for": "203.0.113.9"})
    assert _client_ip(_scope(), headers, ()) == "10.244.3.7"


def test_a_named_exact_peer_is_believed():
    headers = Headers({"x-forwarded-for": "203.0.113.9"})
    assert _client_ip(_scope(), headers, ("10.244.3.7",)) == "203.0.113.9"


def test_a_peer_inside_a_named_cidr_is_believed():
    # The ingress pod's address changes on every restart; naming the pod CIDR is
    # the difference between a setting that survives a rollout and one that
    # silently stops recording addresses after one.
    headers = Headers({"x-forwarded-for": "203.0.113.9"})
    assert _client_ip(_scope(), headers, ("10.244.0.0/16",)) == "203.0.113.9"


def test_the_chain_is_read_from_the_right():
    # A caller may send its own `X-Forwarded-For`; the proxy appends the address
    # it actually saw. The left-most entry is therefore whatever the caller felt
    # like claiming, and reading it would let the audit trail be dictated.
    headers = Headers({"x-forwarded-for": "1.2.3.4, 203.0.113.9"})
    assert _client_ip(_scope(), headers, ("10.244.0.0/16",)) == "203.0.113.9"


def test_a_garbage_entry_is_ignored():
    headers = Headers({"x-forwarded-for": "not-an-address"})
    assert _client_ip(_scope(), headers, ("10.244.0.0/16",)) == "10.244.3.7"


def test_a_missing_header_falls_back_to_the_peer():
    assert _client_ip(_scope(), Headers({}), ("10.244.0.0/16",)) == "10.244.3.7"
