"""Contracts package version — bump on breaking changes to event schemas."""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("conductor-contracts")
except Exception:
    __version__ = "0.1.0"

CONTRACTS_VERSION = f"conductor-contracts@{__version__}"
