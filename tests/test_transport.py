"""Unit tests for transport.py's console-noise-tolerant reply parsing.

These pin down the failure modes discovered while bringing up the UHD60
profile (see projectors/uhd60.json's "source" note): its LAN "RS232 by
Telnet" bridge echoes a shell-style console prompt ahead of replies, in
several different shapes. Every case here is either a byte sequence
captured from a live UHD60 over LAN, or a well-formed reply shape from the
protocol spec shared by all four bundled profiles, to guard against a
UHD60-motivated fix corrupting another model.
"""
from optoma_link.transport import _strip_console_prompt


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
