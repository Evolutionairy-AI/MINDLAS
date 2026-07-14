"""A deliberately clean sample module, used only by the Mindlas demo's --verify check.
It gives the downstream static check a real, known-clean file to run ruff over."""


def add(a: int, b: int) -> int:
    return a + b
