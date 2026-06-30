"""Deprecated module name, kept only so old imports don't hard-crash.

This integration used to be Optoma-UHZ68LV-specific and only ever talked
to the projector over its network port, hence "telnet client". It now
supports both network and direct-serial RS232 connections, so the real
implementation lives in :mod:`.transport` as ``OptomaTcpTransport`` /
``OptomaSerialTransport``. This shim re-exports the old names against the
TCP transport for backward compatibility and will be removed in a future
release.
"""
from __future__ import annotations

from .transport import (
    OptomaCommandError,
    OptomaConnectionError,
    OptomaTcpTransport as OptomaTelnetClient,
)

__all__ = ["OptomaCommandError", "OptomaConnectionError", "OptomaTelnetClient"]
