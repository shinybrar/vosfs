"""Tests for the write contract (section 9)."""

import base64
import gzip
import hashlib
import io
from pathlib import Path
from typing import cast

import httpx
import pytest
import respx
from conftest import (
    NODES_URL,
    ROOT_CONTAINER,
    SYNC_URL,
    make_fs,
    mock_capabilities,
    mock_transfers,
    transfer_details,
)

DATA_NODE = (
    b'<vos:node xmlns:vos="http://www.ivoa.net/xml/VOSpace/v2.0" '
    b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    b'xsi:type="vos:DataNode" uri="vos://example.test!vault/f">'
    b"<vos:properties>"
    b'<vos:property uri="ivo://ivoa.net/vospace/core#length">1</vos:property>'
    b"</vos:properties></vos:node>"
)
_ENDPOINT = "https://staging.canfar.net/arc/files/put-target"


def _mock_put(router: respx.Router, response: httpx.Response) -> None:
    """Wire capabilities, authority, and negotiation to a fixed PUT response."""
    mock_capabilities(router)
    router.get(NODES_URL).mock(return_value=httpx.Response(200, content=ROOT_CONTAINER))
    router.post(SYNC_URL).mock(
        return_value=httpx.Response(
            303, headers={"Location": "https://staging.canfar.net/arc/d"}
        ),
    )
    router.get(url="https://staging.canfar.net/arc/d").mock(
        return_value=httpx.Response(200, content=transfer_details(_ENDPOINT)),
    )
    router.put(_ENDPOINT).mock(return_value=response)


# --- round trips ----------------------------------------------------------------


async def test_pipe_file_overwrite(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router, asynchronous=True)
    await fs._pipe_file("/f", b"hello")
    assert files["/f"] == b"hello"
    assert await fs._cat_file("/f") == b"hello"
    await fs.aclose()


async def test_put_file_round_trip(router: respx.Router, tmp_path: Path) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    local = tmp_path / "src.bin"
    local.write_bytes(b"payload-bytes")
    fs = make_fs(router, asynchronous=True)
    await fs._put_file(str(local), "/dest.bin")
    assert files["/dest.bin"] == b"payload-bytes"
    await fs.aclose()


def test_open_wb_uploads_on_close(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)
    with fs.open("/out.txt", "wb") as handle:
        handle.write(b"chunk1")
        handle.write(b"chunk2")
    assert files["/out.txt"] == b"chunk1chunk2"
    fs.close()


def test_open_w_text(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)
    with fs.open("/t.txt", "w", encoding="utf-8") as handle:
        handle.write("héllo")
    put_bodies = [
        call.request.content for call in router.calls if call.request.method == "PUT"
    ]
    assert put_bodies == ["héllo".encode()]
    fs.close()


def test_open_w_text_infers_compression_from_normalized_path(
    router: respx.Router,
) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)
    with fs.open(
        "/encoded%2Egz",
        "w",
        encoding="utf-8",
        compression="infer",
    ) as handle:
        handle.write("compressed")
    assert gzip.decompress(files["/encoded.gz"]) == b"compressed"
    fs.close()


def test_open_w_text_no_upload_on_error(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)

    def write_then_fail() -> None:
        with fs.open("/never.txt", "w", encoding="utf-8") as handle:
            handle.write("partial")
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        write_then_fail()
    put_calls = [c for c in router.calls if c.request.method == "PUT"]
    assert put_calls == []
    fs.close()


def test_open_w_text_close_failure_no_upload_or_temp_leak(
    router: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)
    handle = cast("io.TextIOWrapper", fs.open("/never.txt", "w", encoding="utf-8"))
    staged_path = Path(handle.buffer.name)
    handle.write("partial")

    def fail_after_buffer_close(writer: io.TextIOWrapper) -> None:
        writer.buffer.close()
        msg = "forced flush failure"
        raise OSError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(type(handle), "flush", fail_after_buffer_close)
        with pytest.raises(OSError, match="forced flush failure"):
            handle.close()

    put_calls = [call for call in router.calls if call.request.method == "PUT"]
    assert (put_calls, staged_path.exists()) == ([], False)
    fs.close()


def test_open_wb_no_upload_on_error(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)

    def write_then_fail() -> None:
        with fs.open("/never.txt", "wb") as handle:
            handle.write(b"partial")
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        write_then_fail()
    assert "/never.txt" not in files
    put_calls = [c for c in router.calls if c.request.method == "PUT"]
    assert put_calls == []
    fs.close()


def test_touch_puts_zero_bytes(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)
    fs.touch("/empty")
    assert files["/empty"] == b""
    fs.close()


def test_touch_no_truncate_unsupported(router: respx.Router) -> None:
    mock_transfers(router, {})
    fs = make_fs(router)
    with pytest.raises(NotImplementedError):
        fs.touch("/x", truncate=False)
    fs.close()


# --- create / exclusive preflight -----------------------------------------------


async def test_pipe_create_new_path(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    router.get(f"{NODES_URL}/f").mock(return_value=httpx.Response(404))
    fs = make_fs(router, asynchronous=True)
    await fs._pipe_file("/f", b"new", mode="create")
    assert files["/f"] == b"new"
    await fs.aclose()


async def test_pipe_create_existing_path(router: respx.Router) -> None:
    mock_transfers(router, {})
    router.get(f"{NODES_URL}/f").mock(
        return_value=httpx.Response(200, content=DATA_NODE)
    )
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(FileExistsError):
        await fs._pipe_file("/f", b"nope", mode="create")
    await fs.aclose()


def test_open_xb_existing_path(router: respx.Router) -> None:
    mock_transfers(router, {})
    router.get(f"{NODES_URL}/f").mock(
        return_value=httpx.Response(200, content=DATA_NODE)
    )
    fs = make_fs(router)
    with pytest.raises(FileExistsError):
        fs.open("/f", "xb")
    fs.close()


# --- integrity and failures -----------------------------------------------------


async def test_precondition_failed_is_integrity_error(router: respx.Router) -> None:
    _mock_put(router, httpx.Response(412, text="checksum mismatch"))
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(OSError, match="integrity"):
        await fs._pipe_file("/f", b"data")
    await fs.aclose()


async def test_failed_put_is_uncertain_write(router: respx.Router) -> None:
    _mock_put(router, httpx.Response(500, text="server error"))
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(OSError, match="uncertain"):
        await fs._pipe_file("/f", b"data")
    await fs.aclose()


async def test_returned_md5_mismatch_raises(router: respx.Router) -> None:
    wrong = base64.b64encode(hashlib.md5(b"other").digest()).decode()  # noqa: S324
    _mock_put(router, httpx.Response(201, headers={"Content-MD5": wrong}))
    fs = make_fs(router, asynchronous=True)
    with pytest.raises(OSError, match="MD5"):
        await fs._pipe_file("/f", b"data")
    await fs.aclose()


async def test_returned_md5_match_succeeds(router: respx.Router) -> None:
    good = hashlib.md5(b"data").hexdigest()  # noqa: S324
    _mock_put(router, httpx.Response(201, headers={"Content-MD5": good}))
    fs = make_fs(router, asynchronous=True)
    await fs._pipe_file("/f", b"data")
    await fs.aclose()


async def test_content_type_is_sent(router: respx.Router) -> None:
    _mock_put(router, httpx.Response(201))
    fs = make_fs(router, asynchronous=True)
    await fs._pipe_file("/f", b"data", content_type="text/csv")
    put_call = next(c for c in router.calls if c.request.method == "PUT")
    assert put_call.request.headers["Content-Type"] == "text/csv"
    await fs.aclose()


def test_append_mode_unsupported(router: respx.Router) -> None:
    mock_transfers(router, {})
    fs = make_fs(router)
    with pytest.raises(NotImplementedError):
        fs.open("/f", "ab")
    fs.close()


def test_open_write_autocommit_false_is_unsupported(router: respx.Router) -> None:
    files: dict[str, bytes] = {}
    mock_transfers(router, files)
    fs = make_fs(router)
    with pytest.raises(NotImplementedError, match="autocommit"):
        fs.open("/x.txt", "wb", autocommit=False)
    fs.close()
