"""Pure lexical path helpers."""


def _strip_trailing_slashes(path: str) -> str:
    return path.rstrip("/")


def _is_root(path: str) -> bool:
    return bool(path) and not _strip_trailing_slashes(path)


def _lexical_root(path: str) -> str:
    return _strip_trailing_slashes(path) or "/"


def _has_dot_segment(path: str) -> bool:
    return any(component in {".", ".."} for component in path.split("/"))


def _lexical_components(path: str) -> tuple[str, ...]:
    return tuple(component for component in path.split("/") if component)


def _same_lexical_path(left: str, right: str) -> bool:
    return _lexical_components(left) == _lexical_components(right)


def _is_same_or_descendant(parent: str, candidate: str) -> bool:
    parent_components = _lexical_components(parent)
    candidate_components = _lexical_components(candidate)
    return candidate_components[: len(parent_components)] == parent_components


def _lexical_relative(parent: str, candidate: str) -> str | None:
    if not _is_same_or_descendant(parent, candidate):
        return None
    return "/".join(_lexical_components(candidate)[len(_lexical_components(parent)) :])


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
