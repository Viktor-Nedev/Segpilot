"""Client-side shadow store: resolves [REF:id] tags we cannot resolve any
other way, since we bypass Paritok's own CompressionPipeline shadow storage."""

from segpilot.policy.shadow import ShadowStore, extract_shadow_id


def test_extract_shadow_id_with_src():
    assert extract_shadow_id("[REF:abc123 src=foo.py] the compressed body") == "abc123"


def test_extract_shadow_id_without_src():
    assert extract_shadow_id("[REF:deadbeef] body") == "deadbeef"


def test_extract_shadow_id_none_when_no_tag():
    assert extract_shadow_id("just plain compressed text") is None


def test_extract_shadow_id_none_on_empty():
    assert extract_shadow_id("") is None


def test_store_put_and_get_round_trips():
    store = ShadowStore()
    store.put("abc123", "the original text", kind="file_read")
    entry = store.get("abc123")
    assert entry.original == "the original text"
    assert entry.kind == "file_read"


def test_get_unknown_id_returns_none():
    store = ShadowStore()
    assert store.get("does-not-exist") is None


def test_record_extracts_and_stores_in_one_call():
    store = ShadowStore()
    sid = store.record("[REF:cafe01 src=a.py] short body", "the long original", kind="file_read")
    assert sid == "cafe01"
    assert store.get("cafe01").original == "the long original"


def test_record_returns_none_and_stores_nothing_when_no_ref_tag():
    store = ShadowStore()
    sid = store.record("no ref tag here", "original", kind="file_read")
    assert sid is None
    assert len(store) == 0


def test_len_reflects_stored_entries():
    store = ShadowStore()
    store.put("a", "x")
    store.put("b", "y")
    assert len(store) == 2
