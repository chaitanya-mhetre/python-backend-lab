from flowforge.security.api_keys import generate_key, parse_prefix, verify_key


def test_generated_key_roundtrip() -> None:
    key = generate_key()
    assert parse_prefix(key.full_key) == key.prefix
    assert verify_key(key.full_key, key.key_hash)
    assert not verify_key(key.full_key + "x", key.key_hash)
    assert key.full_key not in key.key_hash


def test_malformed_keys_have_no_prefix() -> None:
    for bad in ["ff_live_", "ff_live_abc_secret", "ff_live_abcdefgh", "nope"]:
        assert parse_prefix(bad) is None


def test_keys_are_unique() -> None:
    assert len({generate_key().full_key for _ in range(100)}) == 100
