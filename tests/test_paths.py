"""Tests for VOSpace path identity (contract section 4)."""

import pytest

from vosfs import paths


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("vos://a/b", "/a/b"),
        ("vos:///a/b", "/a/b"),
        ("/a/b", "/a/b"),
        ("a/b", "/a/b"),
        ("vos://", "/"),
        ("vos:///", "/"),
        ("/", "/"),
        ("", "/"),
        ("VOS://a/b", "/a/b"),
        ("a/b/", "/a/b"),
        ("//a/b", "/a/b"),
        ("///a/b", "/a/b"),
        ("vos://a//b", "/a/b"),
    ],
)
def test_normalizes_equivalent_forms(raw: str, expected: str) -> None:
    assert paths.strip_protocol(raw) == expected


def test_decodes_percent_escape_once() -> None:
    assert paths.strip_protocol("vos://dir/file%20name") == "/dir/file name"
    assert paths.strip_protocol("/a/%C3%A9") == "/a/é"


def test_preserves_unicode() -> None:
    assert paths.strip_protocol("/data/éè") == "/data/éè"


def test_normalization_is_idempotent_for_ordinary_paths() -> None:
    for raw in ["vos://a/b c", "/data/é", "/x/y/z", "a/b/c"]:
        once = paths.strip_protocol(raw)
        assert paths.strip_protocol(once) == once


@pytest.mark.parametrize(
    "bad",
    [
        "a/b?x=1",
        "a/b#frag",
        "vos://user@host/b",
        "a/\x00/b",
        "a/%2f/b",
        "a/%2F/b",
        "a/%5c/b",
        "a/../b",
        "../b",
        "a/..",
        "vos://a/b/..",
    ],
)
def test_rejects_dangerous_paths(bad: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - message text is not part of the contract
        paths.strip_protocol(bad)


def test_dot_segment_is_rejected() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        paths.strip_protocol("a/./b")


@pytest.mark.parametrize(
    ("normalized", "expected"),
    [
        ("/a/b/c", "/a/b"),
        ("/a", "/"),
        ("/", "/"),
    ],
)
def test_parent(normalized: str, expected: str) -> None:
    # parent operates on an already-normalized path (no second decode).
    assert paths.parent(normalized) == expected


def test_segments() -> None:
    assert paths.segments("/a/b/c") == ["a", "b", "c"]
    assert paths.segments("/") == []


def test_encode_url_path_reencodes_segments() -> None:
    assert paths.encode_url_path("/dir/file name") == "/dir/file%20name"
    assert paths.encode_url_path("/") == ""
    assert paths.encode_url_path("/a/é") == "/a/%C3%A9"


def test_helpers_do_not_decode_literal_percent() -> None:
    # A name with a literal percent-escape decodes exactly once on strip, and the
    # helpers must not decode it again, so the HTTP URL round-trips to the
    # original object rather than a second-decoded one.
    once = paths.strip_protocol("vos://dir/100%2541")
    assert once == "/dir/100%41"
    assert paths.parent(once) == "/dir"
    assert paths.encode_url_path(once) == "/dir/100%2541"


def test_normalized_literal_percent_is_not_decoded_again() -> None:
    once = paths.strip_protocol("vos://authority/dir/100%2541")

    assert paths.strip_protocol(once) == "/authority/dir/100%41"


def test_encode_url_path_reencodes_a_decoded_space() -> None:
    # vos://dir/file%2520name normalizes once to the literal name "file%20name";
    # encoding it for the URL must target that object, not a second-decoded space.
    once = paths.strip_protocol("vos://dir/file%2520name")
    assert once == "/dir/file%20name"
    assert paths.encode_url_path(once) == "/dir/file%2520name"


def test_encoded_separator_in_decoded_name_is_addressable() -> None:
    # data%2fpart decodes once to a segment containing "%2f"; encoding it for a
    # URL must not raise the encoded-separator rejection a second time.
    once = paths.strip_protocol("vos://dir/data%252fpart")
    assert once == "/dir/data%2fpart"
    assert paths.encode_url_path(once) == "/dir/data%252fpart"


@pytest.mark.parametrize("bad", ["/dir/bad%2Fname", "/dir/bad?name"])
def test_public_assume_literal_still_validates_raw_paths(bad: str) -> None:
    from vosfs import VOSpaceFileSystem

    fs = VOSpaceFileSystem("https://example.invalid", skip_instance_cache=True)
    with pytest.raises(ValueError):  # noqa: PT011 - profile boundary, not message text
        fs.expand_path([bad], assume_literal=True)
    fs.close()


@pytest.mark.parametrize("replacement", ["?", "#", "%2F", "%5C"])
def test_replacing_normalized_path_drops_trust(replacement: str) -> None:
    from vosfs import VOSpaceFileSystem

    normalized = paths.strip_protocol("vos://root/100%2541")
    tainted = normalized.replace("%41", replacement)
    fs = VOSpaceFileSystem("https://example.invalid", skip_instance_cache=True)
    with pytest.raises(ValueError):  # noqa: PT011 - profile boundary, not message text
        fs.expand_path([tainted], assume_literal=True)
    fs.close()


def test_safely_replacing_normalized_path_preserves_percent_suffix() -> None:
    normalized = paths.strip_protocol("vos://root/100%2541")

    remapped = normalized.replace("/root", "/destination")

    assert paths.is_normalized(remapped)
    assert remapped == "/destination/100%41"
