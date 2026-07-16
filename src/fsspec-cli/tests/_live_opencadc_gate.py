"""Run one sanitized read-only OpenCADC plain-``ls`` observation."""

from __future__ import annotations

import json
import os
import platform
import re
import site
import ssl
import sys
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import distribution, version
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import fsspec_cli
import httpx
from fsspec_cli import App
from typer.testing import CliRunner

import vosfs
from vosfs import VOSpaceError, VOSpaceFileSystem

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_LABEL_PATTERN = re.compile(r"[A-Za-z0-9._ -]{1,100}")


class _SetupError(Exception):
    pass


@dataclass(frozen=True)
class _Configuration:
    endpoint: str
    directory: str
    repository_root: Path
    cli_wheel: Path
    vosfs_wheel: Path


@dataclass(frozen=True)
class _Observation:
    classification: str
    reason: str
    command_exit: int | None
    call_shape: str
    output_shape: str
    stderr_shape: str
    cleanup: str


@dataclass
class _Probe:
    calls: list[str]
    info_shape_valid: bool = False
    listing_shape_valid: bool = False
    call_arguments_valid: bool = True
    source_error: Exception | None = None
    operation_error: Exception | None = None
    cleanup_attempted: bool = False
    cleanup_succeeded: bool = False


def _output_is_nonempty_plain_listing(output: str) -> bool:
    if not output or not output.endswith("\n"):
        return False
    lines = output[:-1].split("\n")
    return bool(lines) and all(line and "/" not in line for line in lines)


def _source(
    filesystem_factory: Callable[[], AsyncFileSystem],
    probe: _Probe,
    expected_directory: str,
):
    @asynccontextmanager
    async def acquire():
        try:
            filesystem = filesystem_factory()
        except Exception as error:
            probe.source_error = error
            raise
        try:
            original_info = filesystem._info
            original_ls = filesystem._ls

            async def info(path: str, **kwargs: object) -> object:
                probe.calls.append("_info")
                probe.call_arguments_valid &= path == expected_directory and not kwargs
                try:
                    result = await original_info(path, **kwargs)
                except Exception as error:
                    probe.operation_error = error
                    raise
                probe.info_shape_valid = (
                    isinstance(result, Mapping) and result.get("type") == "directory"
                )
                return result

            async def ls(
                path: str,
                detail: bool = True,  # noqa: FBT002 - mirrors the fsspec hook.
                **kwargs: object,
            ) -> object:
                probe.calls.append("_ls")
                probe.call_arguments_valid &= (
                    path == expected_directory and detail is False and not kwargs
                )
                try:
                    result = await original_ls(path, detail=detail, **kwargs)
                except Exception as error:
                    probe.operation_error = error
                    raise
                probe.listing_shape_valid = (
                    isinstance(result, list)
                    and bool(result)
                    and all(isinstance(child, str) for child in result)
                )
                return result

            setattr(filesystem, "_info", info)  # noqa: B010 - probe this instance.
            setattr(filesystem, "_ls", ls)  # noqa: B010 - probe this instance.
            yield filesystem
        except Exception as error:
            if not probe.calls:
                probe.source_error = error
            raise
        finally:
            probe.cleanup_attempted = True
            await filesystem.aclose()
            probe.cleanup_succeeded = True

    return acquire


def _inconclusive_reason(error: Exception) -> str | None:
    if isinstance(error, PermissionError):
        reason = "authentication"
    elif isinstance(error, (FileNotFoundError, NotADirectoryError, IsADirectoryError)):
        reason = "setup"
    elif isinstance(error, (ConnectionError, TimeoutError)) or (
        isinstance(error, VOSpaceError)
        and error.status is None
        and isinstance(error.__cause__, httpx.RequestError)
    ):
        reason = "connectivity"
    elif isinstance(error, ssl.SSLError):
        reason = "authentication"
    else:
        status = getattr(error, "status", None)
        reason = (
            "service_unavailable"
            if isinstance(status, int)
            and (status in {408, 425, 429} or 500 <= status <= 599)
            else None
        )
    return reason


def _call_shape(probe: _Probe) -> str:
    if not probe.calls:
        return "not-reached"
    if probe.calls == ["_info"] and probe.call_arguments_valid:
        return "_info only"
    if probe.calls == ["_info", "_ls"] and probe.call_arguments_valid:
        return "_info then _ls(detail=False)"
    return "mismatch"


def _observe_plain_ls(
    directory: str,
    filesystem_factory: Callable[[], AsyncFileSystem],
    *,
    _invoked_directory: str | None = None,
) -> _Observation:
    probe = _Probe(calls=[])
    invoked_directory = directory if _invoked_directory is None else _invoked_directory
    result = CliRunner().invoke(
        App({"opencadc": _source(filesystem_factory, probe, directory)}).typer_app,
        ["ls", f"opencadc:{invoked_directory}"],
    )
    call_shape = _call_shape(probe)
    call_shape_valid = (
        call_shape == "_info then _ls(detail=False)"
        and probe.info_shape_valid
        and probe.listing_shape_valid
    )
    output_valid = _output_is_nonempty_plain_listing(result.stdout)
    stderr_empty = result.stderr == ""
    cleanup_succeeded = probe.cleanup_attempted and probe.cleanup_succeeded
    passed = (
        result.exit_code == 0
        and call_shape_valid
        and output_valid
        and stderr_empty
        and cleanup_succeeded
    )
    if probe.cleanup_attempted and not probe.cleanup_succeeded:
        classification = "fail"
        reason = "cleanup_contract_mismatch"
    elif probe.source_error is not None:
        classification = "unverified"
        reason = "setup"
    elif probe.operation_error is not None:
        inconclusive_reason = _inconclusive_reason(probe.operation_error)
        classification = "unverified" if inconclusive_reason else "fail"
        reason = inconclusive_reason or "command_contract_mismatch"
    else:
        classification = "pass" if passed else "fail"
        reason = "observed" if passed else "command_contract_mismatch"
    if probe.cleanup_attempted:
        cleanup = "awaited-success" if probe.cleanup_succeeded else "awaited-failure"
    else:
        cleanup = "not-created"
    return _Observation(
        classification=classification,
        reason=reason,
        command_exit=result.exit_code,
        call_shape=call_shape,
        output_shape=(
            "nonempty-valid"
            if output_valid
            else "empty"
            if result.stdout == ""
            else "invalid"
        ),
        stderr_shape="empty" if stderr_empty else "nonempty",
        cleanup=cleanup,
    )


def _native_vosfs_factory(endpoint: str) -> Callable[[], AsyncFileSystem]:
    def create() -> AsyncFileSystem:
        return VOSpaceFileSystem(
            endpoint,
            asynchronous=True,
            skip_instance_cache=True,
        )

    return create


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise _SetupError
    return value


def _path(environment: Mapping[str, str], name: str, *, directory: bool) -> Path:
    path = Path(_required(environment, name)).resolve()
    valid = path.is_dir() if directory else path.is_file()
    if not valid:
        raise _SetupError
    return path


def _actions_url(environment: Mapping[str, str], run_id_name: str) -> str:
    server = _required(environment, "GITHUB_SERVER_URL").rstrip("/")
    repository = _required(environment, "GITHUB_REPOSITORY")
    run_id = _required(environment, run_id_name)
    try:
        parsed_server = urlsplit(server)
    except ValueError:
        raise _SetupError from None
    if (
        _REPOSITORY_PATTERN.fullmatch(repository) is None
        or not run_id.isdigit()
        or parsed_server.scheme != "https"
        or not parsed_server.netloc
        or parsed_server.username is not None
        or parsed_server.password is not None
        or parsed_server.path
        or parsed_server.query
        or parsed_server.fragment
    ):
        raise _SetupError
    return f"{server}/{repository}/actions/runs/{run_id}"


def _configuration(environment: Mapping[str, str]) -> _Configuration:
    endpoint = _required(environment, "FSSPEC_CLI_LIVE_ENDPOINT")
    parsed_endpoint = urlsplit(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.netloc
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
        or parsed_endpoint.query
        or parsed_endpoint.fragment
    ):
        raise _SetupError

    directory = _required(environment, "FSSPEC_CLI_LIVE_DIRECTORY")
    if not directory.startswith("/") or "\0" in directory or "\n" in directory:
        raise _SetupError

    certificate = _path(environment, "VOSFS_CERT_FILE", directory=False)
    if certificate.stat().st_size == 0:
        raise _SetupError

    commit = _required(environment, "FSSPEC_CLI_LIVE_COMMIT")
    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise _SetupError
    _actions_url(environment, "GITHUB_RUN_ID")
    _actions_url(environment, "FSSPEC_CLI_LIVE_CI_RUN_ID")

    for name in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion"):
        if _LABEL_PATTERN.fullmatch(_required(environment, name)) is None:
            raise _SetupError

    repository_root = _path(
        environment,
        "FSSPEC_CLI_REPOSITORY_ROOT",
        directory=True,
    )
    if certificate.is_relative_to(repository_root):
        raise _SetupError

    return _Configuration(
        endpoint=endpoint,
        directory=directory,
        repository_root=repository_root,
        cli_wheel=_path(
            environment,
            "FSSPEC_CLI_LIVE_CLI_WHEEL",
            directory=False,
        ),
        vosfs_wheel=_path(
            environment,
            "FSSPEC_CLI_LIVE_VOSFS_WHEEL",
            directory=False,
        ),
    )


def _distribution_matches_wheel(name: str, wheel: Path) -> bool:
    try:
        direct_url_text = distribution(name).read_text("direct_url.json")
        if direct_url_text is None:
            return False
        direct_url = json.loads(direct_url_text)
        return (
            direct_url.get("url") == wheel.as_uri()
            and direct_url.get("dir_info", {}).get("editable") is not True
            and "archive_info" in direct_url
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _module_is_installed(
    module_file: str | None,
    configuration: _Configuration,
) -> bool:
    if module_file is None:
        return False
    module_path = Path(module_file).resolve()
    site_packages = [Path(path).resolve() for path in site.getsitepackages()]
    return not module_path.is_relative_to(configuration.repository_root) and any(
        module_path.is_relative_to(path) for path in site_packages
    )


def _installed_wheels_are_isolated(configuration: _Configuration) -> bool:
    if sys.flags.isolated != 1 or "PYTHONPATH" in os.environ:
        return False
    if Path.cwd().resolve().is_relative_to(configuration.repository_root):
        return False
    import_paths = (Path(path or ".").resolve() for path in sys.path)
    if any(path.is_relative_to(configuration.repository_root) for path in import_paths):
        return False
    return (
        _module_is_installed(fsspec_cli.__file__, configuration)
        and _module_is_installed(vosfs.__file__, configuration)
        and _distribution_matches_wheel("fsspec-cli", configuration.cli_wheel)
        and _distribution_matches_wheel("vosfs", configuration.vosfs_wheel)
    )


def _safe_label(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    return value if _LABEL_PATTERN.fullmatch(value) else "unavailable"


def _safe_commit(environment: Mapping[str, str]) -> str:
    value = environment.get("FSSPEC_CLI_LIVE_COMMIT", "")
    return value if _COMMIT_PATTERN.fullmatch(value) else "unavailable"


def _safe_actions_url(environment: Mapping[str, str], run_id_name: str) -> str:
    try:
        return _actions_url(environment, run_id_name)
    except _SetupError:
        return "unavailable"


def _package_version(name: str) -> str:
    try:
        value = version(name)
    except Exception:  # noqa: BLE001 - evidence stays sanitized on setup failure.
        return "unavailable"
    return value if _LABEL_PATTERN.fullmatch(value) else "unavailable"


def _format_time(observed_at: datetime) -> str:
    return (
        observed_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _evidence(
    observation: _Observation,
    environment: Mapping[str, str],
    *,
    observed_at: datetime,
    installation_verified: bool,
    service_configured: bool,
) -> dict[str, object]:
    return {
        "classification": observation.classification,
        "reason": observation.reason,
        "gate_kind": "live OpenCADC",
        "command_profile": "plain ls",
        "source_mode": "vosfs / native async",
        "packages": {
            name: _package_version(name)
            for name in ("fsspec-cli", "fsspec", "typer", "vosfs")
        },
        "python": platform.python_version(),
        "platform": {
            "machine": platform.machine(),
            "release": platform.release(),
            "system": platform.system(),
        },
        "runner": {
            "architecture": _safe_label(environment, "RUNNER_ARCH"),
            "image": _safe_label(environment, "ImageOS"),
            "image_version": _safe_label(environment, "ImageVersion"),
            "operating_system": _safe_label(environment, "RUNNER_OS"),
        },
        "observed_at": _format_time(observed_at),
        "commit": _safe_commit(environment),
        "run_url": _safe_actions_url(environment, "GITHUB_RUN_ID"),
        "ci_run_id": (
            environment["FSSPEC_CLI_LIVE_CI_RUN_ID"]
            if environment.get("FSSPEC_CLI_LIVE_CI_RUN_ID", "").isdigit()
            else "unavailable"
        ),
        "ci_run_url": _safe_actions_url(
            environment,
            "FSSPEC_CLI_LIVE_CI_RUN_ID",
        ),
        "service_environment": "configured" if service_configured else "unverified",
        "installation": (
            "exact wheels / isolated" if installation_verified else "unverified"
        ),
        "command_exit": observation.command_exit,
        "stderr": observation.stderr_shape,
        "output": observation.output_shape,
        "call_shape": observation.call_shape,
        "cleanup": observation.cleanup,
    }


def _setup_observation() -> _Observation:
    return _Observation(
        classification="unverified",
        reason="setup",
        command_exit=None,
        call_shape="not-reached",
        output_shape="not-observed",
        stderr_shape="not-observed",
        cleanup="not-created",
    )


def _execute(
    environment: Mapping[str, str],
    *,
    filesystem_factory: Callable[[], AsyncFileSystem] | None = None,
    installation_check: Callable[[_Configuration], bool] = (
        _installed_wheels_are_isolated
    ),
    observed_at: datetime | None = None,
) -> dict[str, object]:
    observation_time = observed_at or datetime.now(timezone.utc)
    try:
        configuration = _configuration(environment)
    except (OSError, ValueError, _SetupError):
        return _evidence(
            _setup_observation(),
            environment,
            observed_at=observation_time,
            installation_verified=False,
            service_configured=False,
        )

    try:
        installation_verified = installation_check(configuration)
    except Exception:  # noqa: BLE001 - evidence must not expose setup details.
        installation_verified = False
    if not installation_verified:
        return _evidence(
            _setup_observation(),
            environment,
            observed_at=observation_time,
            installation_verified=False,
            service_configured=True,
        )

    factory = filesystem_factory or _native_vosfs_factory(configuration.endpoint)
    observation = _observe_plain_ls(configuration.directory, factory)
    return _evidence(
        observation,
        environment,
        observed_at=observation_time,
        installation_verified=True,
        service_configured=True,
    )


def _exit_status(classification: object) -> int:
    if classification == "pass":
        return 0
    if classification == "unverified":
        return 2
    return 1


def main() -> int:
    try:
        evidence = _execute(os.environ)
    except BaseException:  # noqa: BLE001 - never print exception or secret data.
        evidence = {
            "classification": "fail",
            "reason": "gate_internal_error",
            "gate_kind": "live OpenCADC",
        }
    sys.stdout.write(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")
    return _exit_status(evidence.get("classification"))


if __name__ == "__main__":
    raise SystemExit(main())
