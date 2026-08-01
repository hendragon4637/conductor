"""CLI tests — invoke main() directly, assert exit code + streams."""

from __PKG__.cli import main


def test_help_exits_zero(capsys) -> None:
    with __import__("pytest").raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out


def test_valid_invocation(capsys) -> None:
    assert main(["shout", "hi"]) == 0
    assert capsys.readouterr().out.strip() == "HI"


def test_bad_flag_exits_two(capsys) -> None:
    with __import__("pytest").raises(SystemExit) as exc:
        main(["--definitely-not-a-flag"])
    assert exc.value.code == 2
