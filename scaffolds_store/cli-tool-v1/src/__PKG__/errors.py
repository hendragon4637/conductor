"""Typed exceptions for expected error conditions."""


class UserError(Exception):
    """Raised for errors caused by bad user input (reported, exit 1)."""


class DataError(Exception):
    """Raised for errors caused by bad/unexpected data (reported, exit 1)."""
