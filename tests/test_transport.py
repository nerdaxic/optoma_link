"""Unit tests for Optoma transport framing and connection handling.

These pin down the failure modes discovered while bringing up the UHD60
profile (see projectors/uhd60.json's "source" note): its LAN "RS232 by
Telnet" bridge echoes a shell-style console prompt ahead of replies, in
several different shapes. Every case here is either a byte sequence
captured from a live UHD60 over LAN, or a well-formed reply shape from the
protocol spec shared by all four bundled profiles, to guard against a
UHD60-motivated fix corrupting another model.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from optoma_link.transport import (
    OptomaConnectionError,
    OptomaTcpTransport,
    OptomaTimeoutError,
    _strip_console_prompt,
)


# --- Well-formed replies, as any of the four profiles would send them:
# --- stripping must always be a no-op. ---

def test_bare_pass_untouched():
    assert _strip_console_prompt("P") == "P"


def test_bare_fail_untouched():
    assert _strip_console_prompt("F") == "F"


def test_plain_ok_value_untouched():
    assert _strip_console_prompt("Ok1") == "Ok1"


def test_ok_value_with_alnum_payload_untouched():
    # e.g. a firmware/serial read, UHZ68LV- or ZU650-shaped.
    assert _strip_console_prompt("OkC22M11S32") == "OkC22M11S32"


def test_info_status_line_untouched():
    assert _strip_console_prompt("INFO0") == "INFO0"
    assert _strip_console_prompt("INFO24") == "INFO24"


def test_empty_line_untouched():
    assert _strip_console_prompt("") == ""


# --- UHD60 console-noise shapes, captured live over LAN. ---

def test_single_prompt_prefix():
    assert _strip_console_prompt("Optoma_PJ> OK1") == "OK1"


def test_doubled_prompt_prefix():
    # Seen intermittently -- the console re-prints its prompt twice before
    # the reply lands. An earlier single-pass prefix-strip left a residual
    # "Optoma_PJ> OK1" behind here.
    assert _strip_console_prompt("Optoma_PJ> Optoma_PJ> OK1") == "OK1"


def test_tripled_prompt_prefix():
    assert _strip_console_prompt("Optoma_PJ> Optoma_PJ> Optoma_PJ> OK1") == "OK1"


def test_prompt_ahead_of_pass_ack():
    assert _strip_console_prompt("Optoma_PJ> P") == "P"


def test_noise_with_embedded_newline_before_prompt():
    # The case a loop of prefix-strips still couldn't handle: something
    # non-whitespace (control/escape bytes from the console redrawing its
    # prompt) sits before "Optoma_PJ>", so an anchored ``^\S+>`` strip
    # never matches at position 0 and gives up.
    assert _strip_console_prompt("  \nOptoma_PJ> OK1") == "OK1"


def test_arbitrary_noise_before_prompt():
    assert _strip_console_prompt("garbage\nmore garbage\nOptoma_PJ> OK1") == "OK1"


# --- Genuinely unparseable input: passes through unchanged, no crash. ---

def test_no_marker_anywhere_returns_input_unchanged():
    assert _strip_console_prompt("totally unparseable nonsense") == "totally unparseable nonsense"


# --- Marker search must be word-boundary-anchored, not a bare substring
# --- search -- otherwise "ok"/"p"/"f" embedded mid-word gets mistaken for
# --- a real protocol marker. ---

def test_ok_embedded_mid_word_is_not_a_false_match():
    assert _strip_console_prompt("broken") == "broken"


def test_trailing_p_at_end_of_a_word_is_not_a_false_pass_ack():
    assert _strip_console_prompt("help") == "help"
    assert _strip_console_prompt("stop") == "stop"


@pytest.mark.asyncio
async def test_tcp_close_sends_documented_telnet_logout():
    transport = OptomaTcpTransport("192.0.2.1", 23)
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    writer.is_closing.return_value = False
    transport._writer = writer
    transport._reader = MagicMock()

    await transport._async_close()

    writer.write.assert_called_once_with(b"Close\r")
    writer.drain.assert_awaited_once()
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
    assert transport._writer is None
    assert transport._reader is None


# --- regression: issue #8 -- a silent projector must raise a distinguishable
# --- OptomaTimeoutError, even after async_send's one reconnect-and-retry,
# --- so callers can tell "nothing answered" apart from a dead connection.


def _patch_open_close(transport: OptomaTcpTransport) -> None:
    """Make connect/disconnect instant no-ops: no real socket, no real reader.

    Leaves ``transport._replies`` as a genuinely empty queue, so a command
    send can only ever resolve by timing out -- modeling a projector that
    accepts the TCP connection but never answers anything, the deep-standby
    behavior reported in issue #8.
    """

    async def fake_open() -> None:
        transport._writer = MagicMock()
        transport._writer.is_closing.return_value = False
        transport._writer.drain = AsyncMock()
        transport._writer.close = MagicMock()
        transport._writer.wait_closed = AsyncMock()
        transport._reader = MagicMock()

    async def fake_close() -> None:
        transport._writer = None
        transport._reader = None

    transport._async_open = fake_open
    transport._async_close = fake_close
    # No background read loop, so nothing ever completes transport._replies.
    transport._ensure_read_loop = lambda: None


@pytest.mark.asyncio
async def test_no_reply_raises_timeout_specific_error(monkeypatch):
    # async_send disconnects and retries once on any OptomaConnectionError.
    # A naive retry can re-wrap the second failure as the base
    # OptomaConnectionError, losing the "just silent" signal callers rely on
    # to avoid marking entities unavailable for a sleeping projector -- this
    # covers the type surviving both the first failure and that retry.
    monkeypatch.setattr("optoma_link.transport.COMMAND_TIMEOUT", 0.01)
    transport = OptomaTcpTransport("192.0.2.1", 23)
    _patch_open_close(transport)

    with pytest.raises(OptomaTimeoutError):
        await transport.async_send("124", "1")


@pytest.mark.asyncio
async def test_connect_failure_is_not_mistaken_for_a_timeout():
    # The other half of the distinction: actually failing to reach the
    # projector must stay a plain OptomaConnectionError, not the "it's
    # probably just asleep" OptomaTimeoutError -- the whole point is that
    # callers treat these two differently.
    transport = OptomaTcpTransport("192.0.2.1", 23)

    async def fake_open() -> None:
        raise OSError("Network unreachable")

    transport._async_open = fake_open
    transport._async_close = AsyncMock()
    transport._ensure_read_loop = lambda: None

    with pytest.raises(OptomaConnectionError) as exc_info:
        await transport.async_send("124", "1")
    assert not isinstance(exc_info.value, OptomaTimeoutError)


@pytest.mark.asyncio
async def test_tcp_close_still_releases_stream_when_logout_write_fails():
    transport = OptomaTcpTransport("192.0.2.1", 23)
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    writer.is_closing.return_value = False
    writer.drain.side_effect = ConnectionResetError
    transport._writer = writer
    transport._reader = MagicMock()

    await transport._async_close()

    writer.write.assert_called_once_with(b"Close\r")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
    assert transport._writer is None
    assert transport._reader is None
