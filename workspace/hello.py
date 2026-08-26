def greet(name: str) -> str:
    """Return a friendly greeting for *name*."""
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    return f"Hello, {name}!"


def shout(name: str) -> str:
    """Return the greeting for *name* in uppercase."""
    return greet(name).upper()
