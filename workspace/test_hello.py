import pytest

from hello import greet


def test_greet_returns_greeting():
    assert greet("Alice") == "Hello, Alice!"


def test_greet_strips_whitespace():
    assert greet("  Bob ") == "Hello, Bob!"


def test_greet_rejects_non_string():
    with pytest.raises(TypeError):
        greet(42)


def test_greet_rejects_empty_name():
    with pytest.raises(ValueError):
        greet("")
    with pytest.raises(ValueError):
        greet("   ")


def test_shout_returns_uppercase_greeting():
    from hello import shout

    assert shout("Alice") == "HELLO, ALICE!"


def test_shout_rejects_non_string():
    from hello import shout

    with pytest.raises(TypeError):
        shout(42)
