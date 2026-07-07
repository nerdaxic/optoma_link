# Diagnostic build: granular polling

This branch (`test/no-polling`) is a diagnostic fork of Optoma Link that polls
the projector **only for the groups you enable**. Everything else is never
queried — not on a timer, not at setup, not after a write. It always:

- opens the connection (so it still receives the projector's unsolicited
  `INFOn` status pushes — power on/off, warming/cooling, faults), and
- sends the commands you trigger (power, input, picture controls, etc.).

Polling is controlled by the `POLL_GROUPS` table in
[`const.py`](custom_components/optoma_link/const.py). Flip a group `True`/`False`
and reload the integration.

## Why

Some UHZ68LV firmware crashes its internal `ProjectorService` under our read
traffic — the on-screen toast **"ProjectorService: Central service has been
disconnected"**, roughly once an hour. The fully-disabled baseline (every group
`False`, shipped as **2.7.1**) ran stable for ~2 days, which **confirmed the
reads are the trigger** (commands and holding the connection open are fine).
This build now lets us re-enable reads one group at a time to find the specific
read that trips it. Tracked in **GitHub issue #1**.

## Firmware under test

Optoma **UHZ68LV**:

| Component | Version |
|-----------|---------|
| DDP       | C22     |
| MCU       | M12     |
| Scalar    | S32     |

## Poll groups

Each readable entity is mapped to a group in `POLL_GROUP_KEYS`
([`const.py`](custom_components/optoma_link/const.py)). A group toggled `False`
in `POLL_GROUPS` sends none of its reads.

| Group | Reads (code/sub) | Entities |
|-------|------------------|----------|
| `power` | `124/1` | Power state |
| `source` | `121/1` | Input source |
| `picture` | `123/1`, `125/1`, `126/1`, `127/1` | Picture mode, brightness, contrast, aspect ratio |
| `signal` | `150/4`, `150/19` | Resolution, refresh rate |
| `av` | `355/1`, `356/1` | AV mute, audio mute |
| `laser` | `108/1` | Light source hours |
| `temperature` | `150/18`, `155/1` | System temperature, temperature status |
| `device_info` | `122/1`, `555/1`, `87/3`, `353/1`, `558/1` | Firmware, MAC, IP, serial, projector ID |

Read-back-less controls (3D, 3D sync/format, light-source power, sharpness,
image freeze) are never polled in any build, so they are not in a group.

Behavior:

- **Every group `False`** → nothing is polled (the 2.7.1 baseline). The timer is
  `None`; the connection still opens for commands + status pushes.
- **At least one group `True`** → only those reads are sent, on the interval set
  in the integration options (Settings → Devices → Optoma Link → Configure).
  Entities in disabled groups stay "unknown".

## Current setting

`power` only, everything else off — the first re-enable step. Set the poll
interval to **30 s** in the integration options.

## Incremental re-enable plan

Test each step long enough to trust it before moving on. Given the ~hourly
crash rate, that means at least several hours, ideally overnight, per step.

1. **Baseline — no polling.** All groups `False`. (Confirmed stable in 2.7.1.)
2. **Power only.** `power = True`, 30 s interval. ← current build (2.7.2)
3. **Widen one group per run.** Turn on one additional group per test run
   (`source`, then `picture`, `signal`, `av`, `laser`, `temperature`,
   `device_info`). When the toast returns, the group you just enabled contains
   the offending read.
4. **Isolate the read.** Within the offending group, narrow `POLL_GROUP_KEYS`
   (or split the group) to bisect down to the single command that trips it.
5. **Confirm interval.** With the culprit identified/handled, verify stability
   at the normal 30 s interval.

Watch especially: rapid-fire reads with no gap, and any read the firmware
answers but doesn't truly support. If one specific code is the trigger, the fix
is to drop that read from the profile (rely on optimistic state / status pushes
for it), then validate and merge to `main`.

## Restoring normal behavior

Set every group in `POLL_GROUPS` to `True` (or, when merging the fix to `main`,
remove the diagnostic `POLL_GROUPS` machinery and restore the plain
`update_interval`/first-refresh path).
