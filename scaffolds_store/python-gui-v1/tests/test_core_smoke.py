from __PKG__.core.logic import greeting


def test_greeting():
    assert greeting("world") == "Hello, world!"
