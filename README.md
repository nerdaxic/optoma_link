# Optoma Link

A profile-driven, transport-agnostic [Home Assistant](https://www.home-assistant.io/)
custom integration for controlling Optoma projectors over their RS232 ASCII
protocol — either across the network (the projector's RJ-45 port, what Optoma's
menus call **"RS232 by Telnet"**) or over a **direct RS232 serial cable** plugged
into the machine running Home Assistant.

> **Status: alpha.** The architecture is in place and the bundled UHZ68LV profile
> is verified against real hardware, but most model profiles are transcribed from
> Optoma's documentation and have not yet been confirmed on a physical unit. Expect
> rough edges and please [open an issue](https://github.com/nerdaxic/optoma_link/issues)
> if something misbehaves.

## Why "profile-driven"?

The integration code knows nothing about any specific projector. Everything it can
read or control comes from a small JSON **profile** under [`projectors/`](projectors/)
that maps a model's logical entities (switches, selects, numbers, sensors, buttons)
to its RS232 command codes. Adding support for a new Optoma projector is a matter of
adding a JSON file and opening a pull request — no Python required. See
[Adding a projector profile](#adding-a-projector-profile) below.

## Features

- Connect over **LAN** ("RS232 by Telnet") **or a direct serial cable**, with the
  same command set either way.
- **Automatic model detection** during setup, with a dropdown to confirm or override.
- Optional **test-pattern** step during setup so you can visually confirm you're
  talking to the right unit.
- Entities generated from the active profile: Power, AV/Audio Mute, Input Source,
  Picture Mode, Aspect Ratio, Brightness/Contrast/Sharpness, 3D controls, Light
  Source Power, Lamp/Light Source hours, temperature, firmware version, serial
  number, Resync, and more (exact set depends on the model).
- Optional **Wake-on-LAN**: some projectors stop answering power-on commands in
  deep standby; supply the MAC address and the Power switch will send a magic
  packet first (also exposed as a standalone button).
- Adjustable **poll interval** and an **RS232 password** field for units with
  serial security enabled.
- Two services: [`send_command`](#services) (raw passthrough) and
  [`set_test_pattern`](#services).

## Supported projectors

| Model profile | `model_id` | Verified on hardware |
| --- | --- | --- |
| Optoma UHZ68LV | `uhz68lv` | ✅ Yes |
| Optoma W501 / EW501 / EH501 / X501 | `w501` | ⚠️ From documentation only |
| Optoma ZU650 / ZU650T / ZU650T+ | `zu650` | ⚠️ From documentation only |

Because the protocol is shared across Optoma's range, the integration will often
work on models not listed here once a matching profile is added.

## Requirements

- Home Assistant (a recent release; this integration uses the modern config-flow
  and `DataUpdateCoordinator` APIs).
- For **serial** connections: `pyserial-asyncio-fast` and `pyserial` — declared in
  [`manifest.json`](manifest.json) and installed by Home Assistant automatically.
- The projector reachable on your network (LAN) or wired to the host (serial), with
  RS232 control enabled in its on-screen menu.

## Installation

This integration is installed as a Home Assistant **custom component**. The folder
name must be `optoma_link` (matching the integration domain).

### Manual / git

```bash
cd /config/custom_components        # your Home Assistant config dir
git clone https://github.com/nerdaxic/optoma_link.git optoma_link
```

Then restart Home Assistant. To update later, `git pull` inside that folder and
restart again.

> **HACS:** this repository currently ships the integration at its root for direct
> cloning, so it is not yet a one-click HACS custom repository. If you'd like HACS
> support, open an issue — it only requires moving the files under
> `custom_components/optoma_link/`.

After restarting, go to **Settings → Devices & Services → Add Integration** and
search for **Optoma Link**.

## Configuration

Setup runs entirely in the UI:

1. **Choose a connection** — network ("RS232 by Telnet") or direct serial.
2. **Enter connection details:**
   - *LAN:* host/IP, port (default `23`), 2-digit projector ID (default `00`), and
     an optional RS232 password. If the projector can't be reached you'll get a
     hint pointing at **Network → LAN / Control** in its on-screen menu.
   - *Serial:* pick (or type) the serial port, baud rate (default `9600`, the
     Optoma standard), projector ID, and optional password.
3. **Confirm the model** — the integration tries to auto-detect it; confirm or pick
   from the dropdown, give the device a name, and optionally enter a MAC address to
   enable Wake-on-LAN.
4. **Test pattern** *(if the model supports it)* — toggle the projector's built-in
   test grid on/off to confirm you're connected to the right unit, then finish.

The **poll interval** can be changed later from the integration's options
(default 30 s, range 5–300 s).

## Services

### `optoma_link.send_command`

Send a raw RS232 command and get the projector's reply back. Use this for anything
not exposed as an entity — consult your projector's Optoma RS232 command table.

| Field | Required | Example | Description |
| --- | --- | --- | --- |
| `code` | yes | `"20"` | Numeric command code from Optoma's RS232 table. |
| `value` | no | `"21"` | Value sent after the code (omit for value-less commands). |
| `entry_id` | no | | Target a specific projector if you have more than one. |

### `optoma_link.set_test_pattern`

Show or hide the projector's built-in test pattern (if its profile defines one).

| Field | Required | Example | Description |
| --- | --- | --- | --- |
| `enabled` | yes | `true` | `true` shows the pattern, `false` hides it. |
| `entry_id` | no | | Target a specific projector if you have more than one. |

## Adding a projector profile

A profile is a JSON file in [`projectors/`](projectors/). It maps a model's logical
entities to RS232 command codes. The integration sends commands as
`~{projector_id}{code} {value}\r` and expects `P` (pass) / `F` (fail) for writes or
`Ok{value}` for reads.

### Top-level keys

| Key | Required | Notes |
| --- | --- | --- |
| `schema_version` | yes | Currently `1`. |
| `model_id` | yes | Unique slug, e.g. `"uhz68lv"`. Used as the profile key. |
| `display_name` | yes | Human-readable, e.g. `"Optoma UHZ68LV"`. |
| `manufacturer` | no | Defaults to `"Optoma"`. |
| `aliases` | no | Other names the model reports as, used for auto-detection. |
| `verified` | no | `true` once confirmed on real hardware. |
| `source` | no | Where the command codes came from (doc title/revision). |
| `detect` | no | `{ "read_code", "read_sub", "value_type", "index_map" }` — `index_map` maps a numeric model reply (e.g. `"1"`) to a model name for older firmwares. |
| `capabilities` | no | e.g. `{ "three_d": true, "wol": true }`. |
| `test_pattern` | no | `{ "write_code", "on", "off" }`. |
| `switches` / `selects` / `numbers` / `binary_sensors` / `sensors` / `buttons` | no | Entity lists (see below). |

### Entity specs

Every entity has a `key` (unique within the profile), a `name`, and an optional
`icon` (`mdi:` name). A `read` value is a `[code, sub_value]` pair, or `null` for
write-only controls (which become assumed-state entities reflecting the last value
sent).

- **switch** — `on` and `off` as `[code, value]` pairs, plus optional `read`.
- **select** — `write_code`, `options` (`{ label: value }` sent on write),
  optional `read` and `read_options` (`{ raw_value: label }` for display).
- **number** — `write_code`, `min`, `max`, optional `step` (default 1), optional
  `unit`, optional `read`.
- **binary_sensor** — `read` (always read-only).
- **sensor** — `read`, optional `value_type` (`"str"`/`"int"`/`"float"`),
  `unit`, `device_class` (`"temperature"`), `state_class`
  (`"measurement"`/`"total_increasing"`), `entity_category` (`"diagnostic"`).
- **button** — `command` as `[code, value]`, optional `entity_category`.

The three bundled profiles in [`projectors/`](projectors/) are worth reading as
worked examples. PRs that add or correct profiles — especially marking one
`verified` after testing on real hardware — are very welcome.

## Disclaimer

This is an unofficial, community-built integration. It is not affiliated with,
endorsed by, or supported by Optoma. "Optoma" and product model names are
trademarks of their respective owners and are used here only to describe
compatibility.

## License

Copyright © 2026 nerdaxic.

Optoma Link is licensed under the **GNU Affero General Public License v3.0
(AGPL-3.0)** — see [LICENSE](LICENSE). You are free to use, study, modify, and
share it (including commercially), **provided that** any distributed or
network-deployed derivative work is also released under the AGPL-3.0 with complete
corresponding source, and that attribution is preserved.

### Commercial / proprietary licensing

If you want to incorporate this code into a **closed-source or commercial product**
and the AGPL-3.0's copyleft terms don't work for you, a separate commercial license
is available. Please [open an issue](https://github.com/nerdaxic/optoma_link/issues)
or get in touch to arrange terms.

## Contributing

Issues and pull requests are welcome — bug reports, new or corrected projector
profiles, and hardware-verification of the unverified profiles especially. By
contributing you agree your contribution is licensed under the AGPL-3.0.
