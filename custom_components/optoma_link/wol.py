"""Wake-on-LAN magic packet sender.

Projectors that drop into a very low power "eco standby" mode sometimes
stop responding to RS232/network Power On commands until they're woken up
first. If you know the projector's MAC address (read it off the OSD LAN
menu, or your router/UDM's client list), this lets a single button or
service call wake it before the normal power-on command is sent.

Implemented by hand (no extra dependency) since a magic packet is just six
0xFF bytes followed by the target MAC repeated 16 times, sent as a UDP
broadcast.
"""
from __future__ import annotations

import asyncio
import re
import socket

_MAC_CLEAN_RE = re.compile(r"[^0-9A-Fa-f]")


def _normalize_mac(mac: str) -> bytes:
    hex_only = _MAC_CLEAN_RE.sub("", mac)
    if len(hex_only) != 12:
        raise ValueError(f"'{mac}' is not a valid MAC address")
    return bytes.fromhex(hex_only)


def build_magic_packet(mac: str) -> bytes:
    """Build the raw magic-packet payload for the given MAC address."""
    mac_bytes = _normalize_mac(mac)
    return b"\xff" * 6 + mac_bytes * 16


async def async_send_magic_packet(
    mac: str, *, broadcast_ip: str = "255.255.255.255", port: int = 9
) -> None:
    """Send a Wake-on-LAN magic packet as a UDP broadcast."""
    packet = build_magic_packet(mac)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, family=socket.AF_INET, allow_broadcast=True
    )
    try:
        transport.sendto(packet, (broadcast_ip, port))
    finally:
        transport.close()
