"""Pure lexical path helpers."""


def _strip_trailing_slashes(path: str) -> str:
    return path.rstrip("/")


def _is_root(path: str) -> bool:
    return bool(path) and not _strip_trailing_slashes(path)


def _lexical_root(path: str) -> str:
    return _strip_trailing_slashes(path) or "/"


def _has_dot_segment(path: str) -> bool:
    return any(component in {".", ".."} for component in path.split("/"))


def _lexical_basename(path: str) -> str:
    if _is_root(path):
        return "/"
    path = _strip_trailing_slashes(path)
    return path.rsplit("/", 1)[-1]


def _has_final_dot_segment(path: str) -> bool:
    return _lexical_basename(path) in {".", ".."}


def _lexical_parent(path: str) -> str:
    if _is_root(path):
        return "/"
    path = _strip_trailing_slashes(path)
    if "/" not in path:
        return "."
    parent = path.rpartition("/")[0]
    return parent or "/"


def _lexical_join(parent: str, child: str) -> str:
    parent = _strip_trailing_slashes(parent)
    child = "" if child == "/" else child
    if parent in {"", "/"}:
        return f"/{child}"
    return f"{parent}/{child}"
