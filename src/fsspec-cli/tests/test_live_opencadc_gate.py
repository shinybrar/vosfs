"""Hermetic tests for the sanitized live OpenCADC gate."""

from __future__ import annotations

import asyncio
import json
import ssl
from datetime import datetime, timezone

import httpx
import pytest
from fsspec.asyn import AsyncFileSystem

from vosfs import VOSpaceError

from . import _live_opencadc_gate as live_gate

_observe_plain_ls = live_gate._observe_plain_ls


class _FakeFilesystem(AsyncFileSystem):
    async_impl = True
    cachable = False

    def __init__(
        self,
        *,
        info_result: object | None = None,
        listing_result: object | None = None,
        operation_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        super().__init__(asynchronous=True, skip_instance_cache=True)
        self.close_calls = 0
        self.operation_loops: list[asyncio.AbstractEventLoop] = []
        self.info_result = {"type": "directory"} if info_result is None else info_result
        self.listing_result = (
            ["/docs/sensitive-entry-name"] if listing_result is None else listing_result
        )
        self.operation_error = operation_error
        self.close_error = close_error

    async def _info(self, path: str, **kwargs: object) -> object:
        del path, kwargs
        self.operation_loops.append(asyncio.get_running_loop())
        if self.operation_error is not None:
            raise self.operation_error
        return self.info_result

    async def _ls(
        self,
        path: str,
        detail: bool = True,  # noqa: FBT002 - mirrors the fsspec hook.
        **kwargs: object,
    ) -> object:
        del kwargs
        self.operation_loops.append(asyncio.get_running_loop())
        assert detail is False
        if isinstance(self.listing_result, list):
            return [
                f"{path}/{child.rsplit('/', 1)[-1]}" for child in self.listing_result
            ]
        return self.listing_result

    async def aclose(self) -> None:
        self.close_calls += 1
        self.operation_loops.append(asyncio.get_running_loop())
        if self.close_error is not None:
            raise self.close_error


def test_plain_ls_observation_passes_without_retaining_entry_names() -> None:
    filesystem = _FakeFilesystem()
    factory_loops: list[asyncio.AbstractEventLoop] = []

    def factory() -> _FakeFilesystem:
        factory_loops.append(asyncio.get_running_loop())
        return filesystem

    observation = _observe_plain_ls("/docs", factory)

    assert observation.classification == "pass"
    assert observation.reason == "observed"
    assert observation.command_exit == 0
    assert observation.call_shape == "_info then _ls(detail=False)"
    assert observation.output_shape == "nonempty-valid"
    assert observation.stderr_shape == "empty"
    assert observation.cleanup == "awaited-success"
    assert filesystem.close_calls == 1
    assert all(loop is factory_loops[0] for loop in filesystem.operation_loops)
    assert "sensitive-entry-name" not in repr(observation)


def test_wrong_operation_path_fails_without_retaining_either_path() -> None:
    configured_directory = "/configured-sensitive-path"
    invoked_directory = "/wrong-sensitive-path"
    filesystem = _FakeFilesystem()

    observation = _observe_plain_ls(
        configured_directory,
        lambda: filesystem,
        _invoked_directory=invoked_directory,
    )

    assert observation.classification == "fail"
    assert observation.reason == "command_contract_mismatch"
    assert observation.call_shape == "mismatch"
    assert observation.cleanup == "awaited-success"
    rendered = repr(observation)
    assert configured_directory not in rendered
    assert invoked_directory not in rendered


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("infrastructure unavailable"), "authentication"),
        (FileNotFoundError("infrastructure unavailable"), "setup"),
        (ConnectionError("infrastructure unavailable"), "connectivity"),
        (TimeoutError("infrastructure unavailable"), "connectivity"),
        (
            VOSpaceError(
                "service unavailable",
                status=503,
            ),
            "service_unavailable",
        ),
    ],
)
def test_infrastructure_failures_are_unverified_and_always_cleaned(
    error: Exception,
    reason: str,
) -> None:
    filesystem = _FakeFilesystem(operation_error=error)

    observation = _observe_plain_ls("/docs", lambda: filesystem)

    assert observation.classification == "unverified"
    assert observation.reason == reason
    assert observation.command_exit == 1
    assert observation.call_shape == "_info only"
    assert observation.cleanup == "awaited-success"
    assert filesystem.close_calls == 1
    rendered = repr(observation)
    assert "top-secret" not in rendered
    assert "sensitive-entry-name" not in rendered


@pytest.mark.parametrize(
    "filesystem",
    [
        _FakeFilesystem(info_result={"type": "unknown"}),
        _FakeFilesystem(listing_result={"not": "a-list"}),
        _FakeFilesystem(operation_error=NotImplementedError("entry-name")),
    ],
)
def test_reached_command_mismatches_fail_and_cleanup(
    filesystem: _FakeFilesystem,
) -> None:
    observation = _observe_plain_ls("/docs", lambda: filesystem)

    assert observation.classification == "fail"
    assert observation.reason == "command_contract_mismatch"
    assert observation.cleanup == "awaited-success"
    assert filesystem.close_calls == 1
    assert "entry-name" not in repr(observation)


def test_statusless_vospace_error_is_a_reached_contract_failure() -> None:
    filesystem = _FakeFilesystem(
        operation_error=VOSpaceError("sensitive-path contract fault")
    )

    observation = _observe_plain_ls("/docs", lambda: filesystem)

    assert observation.classification == "fail"
    assert observation.reason == "command_contract_mismatch"
    assert observation.call_shape == "_info only"
    assert observation.cleanup == "awaited-success"
    assert "sensitive-path" not in repr(observation)


def test_statusless_vospace_error_caused_by_request_error_is_connectivity() -> None:
    request = httpx.Request("GET", "https://example.test/arc")
    cause = httpx.ConnectError("Bearer top-secret", request=request)
    error = VOSpaceError("sensitive-path transport fault")
    error.__cause__ = cause
    filesystem = _FakeFilesystem(operation_error=error)

    observation = _observe_plain_ls("/docs", lambda: filesystem)

    assert observation.classification == "unverified"
    assert observation.reason == "connectivity"
    assert observation.call_shape == "_info only"
    assert observation.cleanup == "awaited-success"
    rendered = repr(observation)
    assert "top-secret" not in rendered
    assert "sensitive-path" not in rendered


def test_raw_ssl_certificate_failure_is_unverified_and_cleaned() -> None:
    filesystem = _FakeFilesystem(
        operation_error=ssl.SSLError("cert=/sensitive-path top-secret")
    )

    observation = _observe_plain_ls("/docs", lambda: filesystem)

    assert observation.classification == "unverified"
    assert observation.reason == "authentication"
    assert observation.call_shape == "_info only"
    assert observation.cleanup == "awaited-success"
    assert filesystem.close_calls == 1
    rendered = repr(observation)
    assert "sensitive-path" not in rendered
    assert "top-secret" not in rendered


def test_cleanup_failure_is_a_contract_failure_without_error_details() -> None:
    filesystem = _FakeFilesystem(
        operation_error=PermissionError("Bearer top-secret"),
        close_error=RuntimeError("sensitive-entry-name"),
    )

    observation = _observe_plain_ls("/docs", lambda: filesystem)

    assert observation.classification == "fail"
    assert observation.reason == "cleanup_contract_mismatch"
    assert observation.cleanup == "awaited-failure"
    assert filesystem.close_calls == 1
    rendered = repr(observation)
    assert "top-secret" not in rendered
    assert "sensitive-entry-name" not in rendered


def test_factory_setup_failure_is_unverified_without_source_cleanup() -> None:
    def factory() -> _FakeFilesystem:
        message = "cert=/secret/path"
        raise ValueError(message)

    observation = _observe_plain_ls("/docs", factory)

    assert observation.classification == "unverified"
    assert observation.reason == "setup"
    assert observation.call_shape == "not-reached"
    assert observation.cleanup == "not-created"
    assert "/secret/path" not in repr(observation)


def test_native_factory_constructs_every_filesystem_on_its_invocation_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_calls: list[
        tuple[str, dict[str, object], asyncio.AbstractEventLoop]
    ] = []
    filesystems: list[_FakeFilesystem] = []

    def constructor(endpoint: str, **kwargs: object) -> _FakeFilesystem:
        constructor_calls.append((endpoint, kwargs, asyncio.get_running_loop()))
        filesystem = _FakeFilesystem()
        filesystems.append(filesystem)
        return filesystem

    monkeypatch.setattr(live_gate, "VOSpaceFileSystem", constructor)
    factory = live_gate._native_vosfs_factory("https://example.test/arc")

    first = _observe_plain_ls("/docs", factory)
    second = _observe_plain_ls("/docs", factory)

    assert first.classification == second.classification == "pass"
    assert len(constructor_calls) == 2
    assert (
        constructor_calls[0][:2]
        == constructor_calls[1][:2]
        == (
            "https://example.test/arc",
            {"asynchronous": True, "skip_instance_cache": True},
        )
    )
    assert constructor_calls[0][2] is not constructor_calls[1][2]
    assert [filesystem.close_calls for filesystem in filesystems] == [1, 1]


def _live_environment(tmp_path) -> dict[str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    cli_wheel = tmp_path / "fsspec_cli-0.1.0.whl"
    vosfs_wheel = tmp_path / "vosfs-0.3.3.whl"
    certificate = tmp_path / "credential-secret.pem"
    cli_wheel.write_bytes(b"wheel")
    vosfs_wheel.write_bytes(b"wheel")
    certificate.write_text("private-key-secret", encoding="utf-8")
    return {
        "FSSPEC_CLI_LIVE_ENDPOINT": "https://example.test/arc",
        "FSSPEC_CLI_LIVE_DIRECTORY": "/home/sensitive-entry-name",
        "FSSPEC_CLI_REPOSITORY_ROOT": str(repository),
        "FSSPEC_CLI_LIVE_CLI_WHEEL": str(cli_wheel),
        "FSSPEC_CLI_LIVE_VOSFS_WHEEL": str(vosfs_wheel),
        "FSSPEC_CLI_LIVE_COMMIT": "a" * 40,
        "FSSPEC_CLI_LIVE_CI_RUN_ID": "12000",
        "VOSFS_CERT_FILE": str(certificate),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "shinybrar/vosfs",
        "GITHUB_RUN_ID": "12345",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
        "ImageOS": "ubuntu24",
        "ImageVersion": "20260714.1",
        "CADC_PASSWORD": "password-secret",
    }


def test_execute_emits_only_sanitized_exact_evidence(
    tmp_path,
) -> None:
    environment = _live_environment(tmp_path)
    filesystem = _FakeFilesystem()

    evidence = live_gate._execute(
        environment,
        filesystem_factory=lambda: filesystem,
        installation_check=lambda _configuration: True,
        observed_at=datetime(2026, 7, 16, 20, 30, tzinfo=timezone.utc),
    )

    assert evidence["classification"] == "pass"
    assert evidence["packages"]["fsspec-cli"] == "0.1.0"
    assert set(evidence["packages"]) == {"fsspec-cli", "fsspec", "typer", "vosfs"}
    assert evidence["source_mode"] == "vosfs / native async"
    assert evidence["python"]
    assert evidence["runner"] == {
        "architecture": "X64",
        "image": "ubuntu24",
        "image_version": "20260714.1",
        "operating_system": "Linux",
    }
    assert evidence["observed_at"] == "2026-07-16T20:30:00Z"
    assert evidence["commit"] == "a" * 40
    assert (
        evidence["run_url"] == "https://github.com/shinybrar/vosfs/actions/runs/12345"
    )
    assert evidence["ci_run_id"] == "12000"
    assert (
        evidence["ci_run_url"]
        == "https://github.com/shinybrar/vosfs/actions/runs/12000"
    )
    rendered = json.dumps(evidence, sort_keys=True)
    for sensitive in (
        "sensitive-entry-name",
        "credential-secret",
        "private-key-secret",
        "password-secret",
    ):
        assert sensitive not in rendered


def test_missing_setup_is_unverified_without_calling_a_factory() -> None:
    factory_called = False

    def factory() -> _FakeFilesystem:
        nonlocal factory_called
        factory_called = True
        return _FakeFilesystem()

    evidence = live_gate._execute(
        {},
        filesystem_factory=factory,
        installation_check=lambda _configuration: True,
        observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    assert evidence["classification"] == "unverified"
    assert evidence["reason"] == "setup"
    assert evidence["call_shape"] == "not-reached"
    assert factory_called is False
    assert live_gate._exit_status(evidence["classification"]) == 2


def test_invalid_evidence_url_is_not_rendered_on_setup_failure(tmp_path) -> None:
    environment = _live_environment(tmp_path)
    environment["GITHUB_SERVER_URL"] = (
        "https://user:password-secret@github.com/sensitive-entry-name"
    )

    evidence = live_gate._execute(
        environment,
        installation_check=lambda _configuration: True,
        observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    assert evidence["classification"] == "unverified"
    assert evidence["run_url"] == "unavailable"
    assert evidence["ci_run_url"] == "unavailable"
    rendered = json.dumps(evidence, sort_keys=True)
    assert "password-secret" not in rendered
    assert "sensitive-entry-name" not in rendered


def test_actions_url_builder_is_strict_for_live_and_ci_runs(tmp_path) -> None:
    environment = _live_environment(tmp_path)

    assert live_gate._actions_url(environment, "GITHUB_RUN_ID") == (
        "https://github.com/shinybrar/vosfs/actions/runs/12345"
    )
    assert (
        live_gate._actions_url(
            environment,
            "FSSPEC_CLI_LIVE_CI_RUN_ID",
        )
        == "https://github.com/shinybrar/vosfs/actions/runs/12000"
    )

    environment["GITHUB_SERVER_URL"] = "https://user:secret@github.com/path"
    with pytest.raises(live_gate._SetupError):
        live_gate._actions_url(environment, "GITHUB_RUN_ID")


@pytest.mark.parametrize(
    ("classification", "exit_status"),
    [("pass", 0), ("fail", 1), ("unverified", 2)],
)
def test_gate_exit_status_preserves_evidence_classification(
    classification: str,
    exit_status: int,
) -> None:
    assert live_gate._exit_status(classification) == exit_status
