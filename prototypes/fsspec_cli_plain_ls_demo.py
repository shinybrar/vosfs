# ruff: noqa: INP001
"""Run the throwaway plain-ls prototype against a seeded Memory filesystem."""

from fsspec.implementations.memory import MemoryFileSystem
from fsspec_cli_plain_ls import App


def main() -> None:
    """Seed a tiny filesystem and hand control to the prototype Typer app."""
    filesystem = MemoryFileSystem(skip_instance_cache=True)
    filesystem.makedirs("/docs")
    filesystem.pipe_file("/docs/guide.md", b"guide")
    filesystem.pipe_file("/docs/notes.txt", b"notes")
    App({"memory": filesystem}).typer_app()


if __name__ == "__main__":
    main()
