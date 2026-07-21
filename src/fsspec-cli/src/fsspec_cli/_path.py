"""Pure lexical path helpers."""


def _lexical_basename(path: str) -> str:
    if path and all(character == "/" for character in path):
        return "/"
    return path.rstrip("/").rsplit("/", 1)[-1]
