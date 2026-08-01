"""Pure logic tests — no CLI involved."""

from __PKG__.core.ops import echo_upper


def test_echo_upper() -> None:
    assert echo_upper("hello") == "HELLO"


def test_echo_upper_empty() -> None:
    assert echo_upper("") == ""
