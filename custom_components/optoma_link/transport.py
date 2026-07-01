"""Transports for Optoma's RS232 ASCII protocol.

The same command set (``~{projector_id}{code} {value}\\r`` -> ``P``/``F``/
``Ok...``) is used whether you reach the projector over its RJ-45 port
("RS232 by Telnet" in Optoma's own menus -- a plain TCP socket, no real
Telnet option negotiation happens) or over a genuine RS232 serial cable
plugged into the host running Home Assistant. ``OptomaTransport`` factors
out everything that's identical between the two (framing, locking, the
optional password retry) and leaves only "open a connection" / "write
bytes" / "read until CR" to the two concrete subclasses.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import serial_asyncio_fast
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import (
    COMMAND_TIMEOUT,
    CONF_BAUD_RATE,
    CONF_CONNECTION_TYPE,
    CONF_PASSWORD,
    CONF_SERIAL_PORT,
    CONNECT_TIMEOUT,
    CONNECTION_TYPE_SERIAL,
    DEFAULT_BAUD_RATE,
    RESPONSE_FAIL,
    TERMINATOR,
)

_LOGGER = logging.getLogger(__name__)

_TERMINATOR_BYTES = TERMINATOR.encode("ascii")


class OptomaCommandError(Exception):
    """Raised when the projector reports a command failure (``F``)."""


class OptomaConnectionError(Exception):
    """Raised when the connection to the projector fails or times out."""


class OptomaTransport(ABC):
    """Serializes ASCII RS232 commands over a single persistent connection."""

    def __init__(self, password: str | None = None) -> None:
        self._projector_id = "00"
        self._password = password
        self._lock = asyncio.Lock()

    def bind(self, projector_id: str) -> None:
        """Set the projector ID used in every command (e.g. '00')."""
        self._projector_id = projector_id

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether the underlying connection is currently open."""

    @abstractmethod
    async def _async_open(self) -> None:
        """Open the underlying connection if it isn't already open."""

    @abstractmethod
    async def _async_close(self) -> None:
        """Close the underlying connection."""

    @abstractmethod
    async def _async_write(self, payload: bytes) -> None:
        """Write raw bytes to the connection."""

    @abstractmethod
    async def _async_read_until_terminator(self) -> bytes:
        """Read bytes up to and including the protocol terminator (CR)."""

    async def async_connect(self) -> None:
        if self.connected:
            return
        try:
            await self._async_open()
        except (TimeoutError, OSError) as err:
            raise OptomaConnectionError(str(err)) from err

    async def async_disconnect(self) -> None:
        try:
            await self._async_close()
        except OSError:
            pass

    def _build_command(self, code: str, value: str | None, *, with_password: bool) -> bytes:
        cmd = f"~{self._projector_id}{code}"
        if value is not None:
            cmd += f" {value}"
        if with_password and self._password:
            cmd += f" ~{self._password}"
        cmd += TERMINATOR
        try:
            return cmd.encode("ascii")
        except UnicodeEncodeError as err:
            raise OptomaCommandError(
                f"Command '{code} {value or ''}' contains non-ASCII characters; "
                "the RS232 protocol is ASCII-only"
            ) from err

    async def async_send(
        self, code: str, value: str | None = None, *, expect_reply: bool = True
    ) -> str:
        """Send a single command and return the decoded reply (without CR).

        Raises ``OptomaConnectionError`` on transport failure and
        ``OptomaCommandError`` if the projector replies with ``F`` (even
        after retrying once with the configured password appended, if any).
        """
        async with self._lock:
            for attempt in range(2):
                try:
                    await self.async_connect()
                    reply = await self._async_send_once(code, value, expect_reply=expect_reply)
                    return reply
                except _RetryWithPassword:
                    try:
                        reply = await self._async_send_once(
                            code, value, expect_reply=expect_reply, with_password=True
                        )
                    except (
                        TimeoutError,
                        asyncio.IncompleteReadError,
                        asyncio.LimitOverrunError,
                        OSError,
                    ) as err:
                        await self.async_disconnect()
                        raise OptomaConnectionError(f"Lost connection: {err}") from err
                    return reply
                except (
                    TimeoutError,
                    asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError,
                    OSError,
                ) as err:
                    _LOGGER.debug(
                        "Command failed on attempt %s (%s); reconnecting", attempt, err
                    )
                    await self.async_disconnect()
                    if attempt == 1:
                        raise OptomaConnectionError(f"Lost connection: {err}") from err
            raise OptomaConnectionError("Unreachable")  # pragma: no cover

    async def _async_send_once(
        self,
        code: str,
        value: str | None,
        *,
        expect_reply: bool,
        with_password: bool = False,
    ) -> str:
        payload = self._build_command(code, value, with_password=with_password)
        log_payload = payload
        if with_password and self._password:
            log_payload = payload.replace(
                f" ~{self._password}".encode("ascii"), b" ~<redacted>"
            )
        _LOGGER.debug("TX -> %s", log_payload)
        await self._async_write(payload)

        if not expect_reply:
            return ""

        raw = await asyncio.wait_for(
            self._async_read_until_terminator(), timeout=COMMAND_TIMEOUT
        )
        reply = raw.decode("ascii", errors="replace").strip()
        _LOGGER.debug("RX <- %s", reply)

        if reply == RESPONSE_FAIL or reply.startswith(RESPONSE_FAIL):
            if not with_password and self._password:
                raise _RetryWithPassword
            sent = f"~{self._projector_id}{code} {value or ''}".strip()
            raise OptomaCommandError(f"Projector rejected command '{sent}' (replied 'F')")
        return reply


class _RetryWithPassword(Exception):
    """Internal signal: the first attempt failed and a password is configured."""


class OptomaTcpTransport(OptomaTransport):
    """Talks to the projector over its RJ-45 port (Optoma's 'RS232 by Telnet')."""

    def __init__(self, host: str, port: int, password: str | None = None) -> None:
        super().__init__(password)
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _async_open(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=CONNECT_TIMEOUT
        )

    async def _async_close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None

    async def _async_write(self, payload: bytes) -> None:
        assert self._writer is not None
        self._writer.write(payload)
        await self._writer.drain()

    async def _async_read_until_terminator(self) -> bytes:
        assert self._reader is not None
        return await self._reader.readuntil(_TERMINATOR_BYTES)

    def __repr__(self) -> str:
        return f"tcp://{self._host}:{self._port}"


class OptomaSerialTransport(OptomaTransport):
    """Talks to the projector over a genuine RS232 cable on the HA host.

    Uses ``pyserial-asyncio-fast`` (the package modern HA integrations use
    in place of the now-unmaintained ``pyserial-asyncio``) so this looks
    and behaves just like the TCP transport from the caller's perspective.
    """

    def __init__(self, device: str, baudrate: int, password: str | None = None) -> None:
        super().__init__(password)
        self._device = device
        self._baudrate = baudrate
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _async_open(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            serial_asyncio_fast.open_serial_connection(
                url=self._device,
                baudrate=self._baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
            ),
            timeout=CONNECT_TIMEOUT,
        )

    async def _async_close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._reader = None
        self._writer = None

    async def _async_write(self, payload: bytes) -> None:
        assert self._writer is not None
        self._writer.write(payload)
        await self._writer.drain()

    async def _async_read_until_terminator(self) -> bytes:
        assert self._reader is not None
        return await self._reader.readuntil(_TERMINATOR_BYTES)

    def __repr__(self) -> str:
        return f"serial://{self._device}@{self._baudrate}"


def build_transport(data: dict) -> OptomaTransport:
    """Build the right transport for a config entry's data mapping.

    Shared by ``__init__.py`` (real setup) and ``config_flow.py`` (the
    'Test connection' step), so both always agree on how a config entry's
    data is turned into a live connection.
    """
    password = data.get(CONF_PASSWORD) or None
    if data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_SERIAL:
        return OptomaSerialTransport(
            data[CONF_SERIAL_PORT],
            data.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE),
            password=password,
        )
    return OptomaTcpTransport(data[CONF_HOST], data[CONF_PORT], password=password)
