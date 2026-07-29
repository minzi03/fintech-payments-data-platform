"""Trusted proxy and privacy-preserving address-resolution tests."""

from __future__ import annotations

from portal_api.abuse.client_address import (
    ClientAddressSettings,
    ForwardedHeaderMode,
    TrustedClientAddressResolver,
)

KEY = bytes(range(32))


def _resolver(
    *,
    mode: ForwardedHeaderMode = ForwardedHeaderMode.DIRECT,
    trusted: tuple[str, ...] = (),
    hops: int = 5,
) -> TrustedClientAddressResolver:
    return TrustedClientAddressResolver(
        ClientAddressSettings(
            trusted_proxy_cidrs=trusted,
            forwarded_header_mode=mode,
            max_forwarded_hops=hops,
            ipv4_prefix_length=24,
            ipv6_prefix_length=64,
            fingerprint_key=KEY,
        )
    )


def _scope(address: str) -> dict[str, object]:
    return {"type": "http", "client": (address, 54321)}


def test_untrusted_peer_cannot_spoof_forwarded_address() -> None:
    resolver = _resolver(
        mode=ForwardedHeaderMode.X_FORWARDED_FOR,
        trusted=("10.0.0.0/8",),
    )

    spoofed = resolver.resolve_scope(
        _scope("203.0.113.9"),
        {"x-forwarded-for": "198.51.100.42"},
    )
    direct = resolver.resolve_scope(_scope("203.0.113.9"), {})

    assert spoofed == direct
    assert spoofed.source == "socket"


def test_trusted_proxy_chain_selects_first_untrusted_hop() -> None:
    resolver = _resolver(
        mode=ForwardedHeaderMode.X_FORWARDED_FOR,
        trusted=("10.0.0.0/8",),
    )

    resolved = resolver.resolve_scope(
        _scope("10.0.0.5"),
        {"x-forwarded-for": "198.51.100.42, 10.9.0.2"},
    )
    expected = _resolver().resolve_scope(_scope("198.51.100.42"), {})

    assert resolved.fingerprint == expected.fingerprint
    assert resolved.source == "trusted_proxy"


def test_malformed_or_excessive_forwarding_chain_is_ignored() -> None:
    resolver = _resolver(
        mode=ForwardedHeaderMode.X_FORWARDED_FOR,
        trusted=("10.0.0.0/8",),
        hops=2,
    )
    direct = resolver.resolve_scope(_scope("10.0.0.5"), {})

    malformed = resolver.resolve_scope(
        _scope("10.0.0.5"),
        {"x-forwarded-for": "not-an-ip"},
    )
    excessive = resolver.resolve_scope(
        _scope("10.0.0.5"),
        {"x-forwarded-for": "198.51.100.1, 10.0.0.2, 10.0.0.3"},
    )

    assert malformed == direct
    assert excessive == direct


def test_ipv4_mapped_ipv6_and_prefix_aggregation_are_stable() -> None:
    resolver = _resolver()

    mapped = resolver.resolve_scope(_scope("::ffff:192.0.2.10"), {})
    native = resolver.resolve_scope(_scope("192.0.2.200"), {})
    other_prefix = resolver.resolve_scope(_scope("192.0.3.1"), {})

    assert mapped.address_family == "ipv4"
    assert mapped.fingerprint == native.fingerprint
    assert mapped.fingerprint != other_prefix.fingerprint


def test_ipv6_uses_64_bit_aggregation_and_domain_separation() -> None:
    resolver = _resolver()

    first = resolver.resolve_scope(_scope("2001:db8:abcd:12::1"), {})
    same_prefix = resolver.resolve_scope(_scope("2001:db8:abcd:12::ffff"), {})
    next_prefix = resolver.resolve_scope(_scope("2001:db8:abcd:13::1"), {})

    assert first.address_family == "ipv6"
    assert first.fingerprint == same_prefix.fingerprint
    assert first.fingerprint != next_prefix.fingerprint
    assert resolver.fingerprint_value("session", "same") != resolver.fingerprint_value(
        "subject", "same"
    )


def test_fingerprint_secret_must_have_256_bits() -> None:
    try:
        TrustedClientAddressResolver(
            ClientAddressSettings((), ForwardedHeaderMode.DIRECT, 5, 24, 64, b"short")
        )
    except ValueError as error:
        assert "256 bits" in str(error)
    else:
        raise AssertionError("Short fingerprint key must be rejected")
