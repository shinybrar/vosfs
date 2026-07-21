"""Tests for the read contract (section 8)."""

import asyncio
import tempfile
from pathlib import Path

import httpx
import pytest
import respx
from conftest import BASE_URL, make_fs, mock_transfers


async def test_get_file_streams_to_disk(router: respx.Router, tmp_path: Path) -> None:
    mock_transfers(router, {"/data.bin": b"hello world"})
    fs = make_fs(router, asynchronous=True)
    local = tmp_path / "out.bin"
    await fs._get_file("/data.bin", str(local))
    assert local.read_bytes() == b"hello world"
    await fs.aclose()


async def test_get_file_empty_204(router: respx.Router, tmp_path: Path) -> None:
    mock_transfers(router, {"/empty": b""})
    fs = make_fs(router, asynchronous=True)
    local = tmp_path / "empty"
    await fs._get_file("/empty", str(local))
    assert local.read_bytes() == b""
    await fs.aclose()


async def test_cat_file_whole(router: respx.Router) -> None:
    mock_transfers(router, {"/f": b"abcdef"})
    fs = make_fs(router, asynchronous=True)
    assert await fs._cat_file("/f") == b"abcdef"
    await fs.aclose()


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (0, 3, b"abc"),
        (2, None, b"cdef"),
        (None, 2, b"ab"),
        (-2, None, b"ef"),
        (3, 3, b""),
        (0, 100, b"abcdef"),
    ],
)
async def test_cat_file_slicing(
    router: respx.Router, start: int | None, end: int | None, expected: bytes
) -> None:
    mock_transfers(router, {"/f": b"abcdef"})
    fs = make_fs(router, asynchronous=True)
    assert await fs._cat_file("/f", start, end) == expected
    await fs.aclose()


async def test_cat_ranges_one_get_per_object(router: respx.Router) -> None:
    mock_transfers(router, {"/f": b"abcdefghij"})
    fs = make_fs(router, asynchronous=True)
    result = await fs._cat_ranges(["/f", "/f", "/f"], [0, 2, 5], [2, 4, 8])
    assert result == [b"ab", b"cd", b"fgh"]
    # Only one byte GET for the single object, despite three ranges.
    byte_calls = [c for c in router.calls if "/files" in str(c.request.url)]
    assert len(byte_calls) == 1
    await fs.aclose()


async def test_cat_ranges_groups_multiple_objects(router: respx.Router) -> None:
    mock_transfers(router, {"/a": b"aaaa", "/b": b"bbbb"})
    fs = make_fs(router, asynchronous=True)
    result = await fs._cat_ranges(["/a", "/b", "/a"], [0, 1, 2], [2, 3, 4])
    assert result == [b"aa", b"bb", b"aa"]
    await fs.aclose()


@pytest.mark.parametrize(
    ("call_batch_size", "filesystem_batch_size", "expected_active"),
    [(2, None, 2), (-1, None, 3), (None, -1, 3)],
)
async def test_cat_ranges_bounds_active_staged_objects(  # noqa: PLR0913 - parametrized public seam
    router: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    call_batch_size: int | None,
    filesystem_batch_size: int | None,
    expected_active: int,
) -> None:
    active = 0
    maximum_active = 0
    maximum_temps = 0
    release = asyncio.Event()

    def temp_count() -> int:
        return sum(path.name.startswith("vosfs-") for path in tmp_path.iterdir())

    class BlockingStream(httpx.AsyncByteStream):
        def __init__(self, content: bytes) -> None:
            self.content = content

        async def __aiter__(self):
            nonlocal active, maximum_active, maximum_temps
            active += 1
            maximum_active = max(maximum_active, active)
            maximum_temps = max(maximum_temps, temp_count())
            if active == expected_active:
                release.set()
            await release.wait()
            try:
                yield self.content
            finally:
                active -= 1

    router.route(url__regex=rf"^{BASE_URL}/files").mock(
        side_effect=lambda request: httpx.Response(
            200,
            stream=BlockingStream(request.url.params["p"].encode() * 4),
        )
    )
    mock_transfers(router, {"/a": b"", "/b": b"", "/c": b""})
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    fs = make_fs(router, asynchronous=True, batch_size=filesystem_batch_size)

    result = await asyncio.wait_for(
        fs._cat_ranges(
            ["/a", "/b", "/c", "/a"],
            [0, 0, 0, 1],
            [2, 2, 2, 3],
            batch_size=call_batch_size,
        ),
        timeout=1,
    )

    assert result == [b"/a", b"/b", b"/c", b"a/"]
    assert maximum_active == expected_active
    assert maximum_temps == expected_active
    assert temp_count() == 0
    await fs.aclose()


async def test_cat_ranges_rejects_invalid_batch_size_before_io(
    router: respx.Router,
) -> None:
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(ValueError, match="batch_size"):
        await fs._cat_ranges(["/a"], [0], [1], batch_size=0)
    assert len(router.calls) == 0
    await fs.aclose()


async def test_cat_ranges_honors_invalid_filesystem_batch_size_before_io(
    router: respx.Router,
) -> None:
    fs = make_fs(router, asynchronous=True, batch_size=0)
    with pytest.raises(ValueError, match="batch_size"):
        await fs._cat_ranges(["/a"], [0], [1])
    assert len(router.calls) == 0
    await fs.aclose()


@pytest.mark.parametrize("on_error", ["ignore", "omit"])
async def test_cat_ranges_rejects_invalid_on_error_before_io(
    router: respx.Router, on_error: str
) -> None:
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(ValueError, match="on_error"):
        await fs._cat_ranges(["/a"], [0], [1], on_error=on_error)
    assert len(router.calls) == 0
    await fs.aclose()


async def test_cat_ranges_rejects_cardinality_mismatch_before_io(
    router: respx.Router,
) -> None:
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(ValueError, match="same length"):
        await fs._cat_ranges(["/a", "/b"], [0], [1, 2])
    assert len(router.calls) == 0
    await fs.aclose()


async def test_cat_ranges_broadcasts_scalar_bounds(router: respx.Router) -> None:
    # Regression: fsspec permits a single int for starts/ends applied to all paths.
    mock_transfers(router, {"/a": b"0123456789", "/b": b"abcdefghij"})
    fs = make_fs(router, asynchronous=True)
    assert await fs._cat_ranges(["/a", "/b"], 0, 3) == [b"012", b"abc"]
    await fs.aclose()


async def test_read_consumes_raw_bytes_despite_content_encoding(
    router: respx.Router,
) -> None:
    import gzip

    from vospace_sim import VOSpaceSim

    raw = gzip.compress(b"plain payload")

    async def gz_body() -> object:
        yield raw

    # Register a streamable byte GET declaring Content-Encoding: gzip first so it
    # wins over the simulator's route; httpx would content-decode it via aread().
    router.route(url__regex=rf"^{BASE_URL}/files").mock(
        return_value=httpx.Response(
            200, content=gz_body(), headers={"Content-Encoding": "gzip"}
        ),
    )
    VOSpaceSim().add_file("/f", raw).install(router)
    fs = make_fs(router, asynchronous=True)
    # cat_file must return the RAW (gzipped) bytes, not the httpx-decoded body.
    assert await fs._cat_file("/f") == raw
    await fs.aclose()


async def test_identity_encoding_header_sent(router: respx.Router) -> None:
    mock_transfers(router, {"/f": b"x"})
    fs = make_fs(router, asynchronous=True)
    await fs._cat_file("/f")
    byte_call = next(c for c in router.calls if "/files" in str(c.request.url))
    assert byte_call.request.headers["Accept-Encoding"] == "identity"
    await fs.aclose()


def test_open_rb_is_seekable(router: respx.Router) -> None:
    mock_transfers(router, {"/f": b"0123456789"})
    fs = make_fs(router)
    with fs.open("/f", "rb") as handle:
        assert handle.read(3) == b"012"
        assert handle.tell() == 3
        handle.seek(5)
        assert handle.read() == b"56789"
        handle.seek(-2, 2)
        assert handle.read() == b"89"
        handle.seek(0)
        buffer = bytearray(4)
        assert handle.readinto(buffer) == 4
        assert bytes(buffer) == b"0123"
    fs.close()


async def test_cat_file_missing_raises(router: respx.Router) -> None:
    mock_transfers(router, {})
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(FileNotFoundError):
        await fs._cat_file("/missing")
    await fs.aclose()


async def test_cat_ranges_on_error_raise(router: respx.Router) -> None:
    mock_transfers(router, {})
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(FileNotFoundError):
        await fs._cat_ranges(["/missing"], [0], [1], on_error="raise")
    await fs.aclose()


async def test_cat_ranges_on_error_return(router: respx.Router) -> None:
    mock_transfers(router, {"/ok": b"hello"})
    fs = make_fs(router, asynchronous=True)
    result = await fs._cat_ranges(["/ok", "/missing"], [0, 0], [2, 1])
    assert result[0] == b"he"
    assert isinstance(result[1], FileNotFoundError)
    await fs.aclose()


def test_open_text_mode(router: respx.Router) -> None:
    mock_transfers(router, {"/f": b"line1\nline2\n"})
    fs = make_fs(router)
    with fs.open("/f", "r") as handle:
        assert handle.readline() == "line1\n"
        assert list(handle) == ["line2\n"]
    fs.close()


def test_cat_head_tail(router: respx.Router) -> None:
    mock_transfers(router, {"/f": b"abcdefghij"})
    fs = make_fs(router)
    assert fs.cat("/f") == b"abcdefghij"
    assert fs.head("/f", 3) == b"abc"
    assert fs.tail("/f", 2) == b"ij"
    fs.close()


def test_direct_byte_endpoint_303_is_consumed_once_without_credentials(
    router: respx.Router,
) -> None:
    from conftest import NODES_URL, ROOT_CONTAINER, SYNC_URL, mock_capabilities

    mock_capabilities(router)
    router.get(NODES_URL).mock(return_value=httpx.Response(200, content=ROOT_CONTAINER))
    endpoint = "http://download.test/files/preauth:TESTTOKEN/d.bin"
    post = router.post(SYNC_URL).mock(
        return_value=httpx.Response(303, headers={"Location": endpoint})
    )
    seen_auth: list[str | None] = []

    class DirectBytes(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"direct-bytes"

        async def aclose(self) -> None:
            self.closed = True

    stream = DirectBytes()

    def byte_get(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        return httpx.Response(200, stream=stream)

    direct = router.get(endpoint).mock(side_effect=byte_get)
    fs = make_fs(router, token="service-token")
    assert fs.cat_file("/d.bin") == b"direct-bytes"
    assert post.call_count == 1
    assert direct.call_count == 1
    assert seen_auth == [None]
    assert stream.closed
    fs.close()


def test_failed_staged_read_removes_temporary_file(
    router: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_transfers(router, {})
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    fs = make_fs(router)

    with pytest.raises(FileNotFoundError):
        fs.open("/missing", "rb")

    assert list(tmp_path.glob("vosfs-*")) == []
    fs.close()


def test_error_response_carries_retry_after_and_fault(router: respx.Router) -> None:
    # End-to-end: a 503 with a Retry-After header and a fault body flows through
    # _raise_for_status into VOSpaceError's fields.
    from conftest import NODES_URL, mock_capabilities

    from vosfs import errors

    mock_capabilities(router)
    router.get(f"{NODES_URL}/x").mock(
        return_value=httpx.Response(
            503, headers={"Retry-After": "42"}, text="ServiceBusy: try again later"
        )
    )
    fs = make_fs(router)
    with pytest.raises(errors.VOSpaceError) as excinfo:
        fs.info("/x")
    assert excinfo.value.retry_after == 42.0
    assert excinfo.value.fault == "ServiceBusy"
    assert excinfo.value.status == 503
    fs.close()
