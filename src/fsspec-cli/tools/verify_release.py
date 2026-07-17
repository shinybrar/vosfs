"""Validate an fsspec-cli GitHub release dispatch and its artifacts."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_TAG_PATTERN = re.compile(
    r"^fsspec-cli-v(?P<version>(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$"
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_ARTIFACT_COUNT = 2


def _metadata_matches(payload: bytes, version: str) -> bool:
    metadata = BytesParser().parsebytes(payload)
    return metadata.get("Name") == "fsspec-cli" and metadata.get("Version") == version


def _validate_wheel(
    parser: argparse.ArgumentParser,
    wheel: Path,
    version: str,
) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                parser.error("wheel must contain exactly one METADATA file")
            payload = archive.read(metadata_files[0])
    except (OSError, zipfile.BadZipFile):
        parser.error("wheel must be a readable zip archive")
    if not _metadata_matches(payload, version):
        parser.error(f"wheel metadata must identify fsspec-cli {version}")


def _validate_sdist(
    parser: argparse.ArgumentParser,
    source: Path,
    version: str,
) -> None:
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            metadata_files = [
                member
                for member in archive.getmembers()
                if member.name.endswith("/PKG-INFO")
            ]
            if len(metadata_files) != 1:
                parser.error("sdist must contain exactly one PKG-INFO file")
            extracted = archive.extractfile(metadata_files[0])
            if extracted is None:
                parser.error("sdist PKG-INFO must be a regular file")
            payload = extracted.read()
    except (OSError, tarfile.TarError):
        parser.error("sdist must be a readable gzip tar archive")
    if not _metadata_matches(payload, version):
        parser.error(f"sdist metadata must identify fsspec-cli {version}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--tag", required=True)
    dispatch.add_argument("--release-sha", required=True)
    dispatch.add_argument("--checkout-sha", required=True)
    dispatch.add_argument("--is-draft", required=True)
    artifacts = commands.add_parser("artifacts")
    artifacts.add_argument("--tag", required=True)
    artifacts.add_argument("--dist-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the requested release operation."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    match = _TAG_PATTERN.fullmatch(arguments.tag)
    if match is None:
        parser.error("release tag must be an exact fsspec-cli-vX.Y.Z tag")
    version = match.group("version")
    if arguments.command == "artifacts":
        directory = arguments.dist_dir
        if not directory.is_dir():
            parser.error("artifact directory does not exist")
        artifacts = sorted(
            path
            for path in directory.iterdir()
            if path.is_file()
            and (path.name.endswith(".whl") or path.name.endswith(".tar.gz"))
        )
        wheel_pattern = re.compile(
            rf"^fsspec_cli-{re.escape(version)}-[A-Za-z0-9_.-]+\.whl$"
        )
        wheels = [path for path in artifacts if wheel_pattern.fullmatch(path.name)]
        source_name = f"fsspec_cli-{version}.tar.gz"
        sources = [path for path in artifacts if path.name == source_name]
        if (
            len(artifacts) != _EXPECTED_ARTIFACT_COUNT
            or len(wheels) != 1
            or len(sources) != 1
        ):
            parser.error("release requires exactly one fsspec-cli wheel and one sdist")
        _validate_wheel(parser, wheels[0], version)
        _validate_sdist(parser, sources[0], version)
        for artifact in (*wheels, *sources):
            print(artifact.name)  # noqa: T201
        return 0

    if (
        _SHA_PATTERN.fullmatch(arguments.release_sha) is None
        or _SHA_PATTERN.fullmatch(arguments.checkout_sha) is None
    ):
        parser.error("release dispatch requires a full commit SHA")
    if arguments.release_sha != arguments.checkout_sha:
        parser.error("released tag does not match the dispatched commit")
    if arguments.is_draft != "true":
        parser.error("release must remain draft until artifacts are attached")
    print(version)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
