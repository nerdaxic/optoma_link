# Projector profile reference

A profile is a JSON file in [`custom_components/optoma_link/projectors/`](../custom_components/optoma_link/projectors). It maps one projector model's entities to Optoma RS232 command codes. Adding a model needs no Python: add a profile and open a pull request.

The bundled profiles are the best worked examples. [`uhz68lv.json`](../custom_components/optoma_link/projectors/uhz68lv.json) is the most complete and is verified on real hardware.

## Protocol basics

Commands are sent as `~{projector_id}{code} {value}` followed by a carriage return. The projector replies:

| Reply | Meaning |
|---|---|
| `P` | Write accepted |
| `F` | Command rejected or not supported |
| `Ok{value}` | Read result (casing varies by firmware) |
| `INFO{n}` | Unsolicited status push (power transitions, faults) |

A read is written in a profile as a `[code, sub_value]` pair, for example `["124", "1"]` for the power state.

## Top-level keys

| Key | Required | Description |
|---|---|---|
| `schema_version` | Yes | Currently `1`. |
| `model_id` | Yes | Unique slug, e.g. `"uhz68lv"`. Must match the file name. |
| `display_name` | Yes | Human-readable name, e.g. `"Optoma UHZ68LV"`. |
| `manufacturer` | No | Defaults to `"Optoma"`. |
| `aliases` | No | Model names the projector may report. Used for auto-detection. |
| `verified` | No | `true` once the profile has been confirmed on real hardware. |
| `source` | No | Where the command codes came from (document title and revision). |
| `detect` | No | How the model identifies itself: `read_code`, `read_sub`, `value_type` (`"string"` or `"index"`). For `"index"`, `index_map` maps a numeric reply to a model name (e.g. `{"1": "X501"}`). |
| `capabilities` | No | Feature flags. `auto_status: true` adds the live **Status** sensor, driven by the projector's `INFO` pushes. `three_d` and `wol` are informational. |
| `test_pattern` | No | `{ "write_code", "on", "off" }`. Enables the setup test-pattern step, the Test Pattern switch and the `set_test_pattern` service. |
| `serial_read` | No | `[code, sub_value]` for the serial number shown on the device page. |
| `device_info` | No | Entity-less reads for the device page (e.g. `firmware_version`, `mac_address`), each `{ "key", "read" }`. Read once the projector is on, not on every poll. |
| `web_ui` | No | `{ "scheme", "port" }` for the device page's **Visit** link, e.g. `{"scheme": "https", "port": 8088}`. |
| `switches`, `selects`, `numbers`, `binary_sensors`, `sensors`, `buttons` | No | Entity lists, see below. |

## Entity specs

### Common keys

| Key | Description |
|---|---|
| `key` | Unique within the profile. Becomes part of the entity's unique ID, so **don't rename it after a release**. |
| `name` | Entity name. |
| `icon` | Optional `mdi:` icon. |
| `read` | `[code, sub_value]`, or `null` for write-only controls. Write-only entities show the last value sent from Home Assistant (assumed state). |
| `entity_category` | Optional: `"config"` or `"diagnostic"`. |
| `enabled_default` | Optional. `false` creates the entity disabled. Disabled entities are never polled. |
| `description` | Optional one-line note, shown as a `note` attribute on the entity. |
| `pattern` | Optional regex a read reply must fully match. Replies that don't match are ignored and the previous value is kept. |
| `default` | Optional starting value for write-only controls, shown until the first write. |
| `refresh_after` | Optional. `true` re-polls shortly after a write, for changes the projector applies with a delay. |
| `error_hint` | Optional text added to the error message when the projector rejects a write (`F`). |

### Per-platform keys

| Platform | Keys |
|---|---|
| `switches` | `on` and `off` as `[code, value]` pairs. Optional `read`: `1` is on. |
| `selects` | `write_code`; `options` as `{ label: value }` (a value may also be a `[code, value]` pair to use a different command); optional `read_options` as `{ raw_reply: label }`. Every label in `read_options` should also be in `options`, or Home Assistant shows the value as unknown. Optional `conflicts` maps option labels to content "universes" (SDR, HDR, Dolby Vision, 3D) to explain rejected picture modes. |
| `numbers` | `write_code`, `min`, `max`, optional `step` (default 1) and `unit`. |
| `binary_sensors` | `read`. Optional `device_class`: `"connectivity"`. |
| `sensors` | `read`; optional `value_type` (`"str"`, `"int"` or `"float"`), `read_options`, `unit`, `format: "ip"` (for `010_127_040_241`-style replies), `device_class` (`"temperature"`, `"duration"` or `"power"`) and `state_class` (`"measurement"` or `"total_increasing"`). |
| `buttons` | `command` as `[code, value]`. |

## Guidelines

- **Only add reads you have confirmed on hardware**, or mark the profile `"verified": false`. Some documented commands return `F` or behave differently in standby.
- **The power read matters.** While the projector is off, the integration only sends the switch with key `power`. Every profile with a power switch should use that key.
- **Keep keys consistent across profiles** for the same setting (e.g. `standby_power_mode`), so automations carry over between models.
- Mark a profile `verified` only after testing it on a real unit.
