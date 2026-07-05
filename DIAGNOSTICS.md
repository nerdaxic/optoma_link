# Diagnostic build: no-polling mode

This branch (`test/no-polling`) is a diagnostic fork of Optoma Link that
**never queries the projector**. It only:

- opens the connection (so it still receives the projector's unsolicited
  `INFOn` status pushes — power on/off, warming/cooling, faults), and
- sends the commands you trigger (power, input, picture controls, etc.).

No read/query commands are issued — not on a timer, not at setup, not after a
write. It is controlled by a single switch: `DISABLE_POLLING` in
[`const.py`](custom_components/optoma_link/const.py).

## Why

Some UHZ68LV firmware appears to crash its internal `ProjectorService` under
our polling — the on-screen toast **"ProjectorService: Central service has
been disconnected"**, roughly once an hour. This build isolates whether *our*
read traffic is the trigger.

- **Crashing stops** with this build → our polling is implicated. Re-enable
  polling incrementally (below) to find the specific read that trips it.
- **Crashing continues** → the firmware is at fault on its own; our polling is
  not the cause.

## Firmware under test

Optoma **UHZ68LV**:

| Component | Version |
|-----------|---------|
| DDP       | C22     |
| MCU       | M12     |
| Scalar    | S32     |

## What is disabled vs. kept

| Traffic source | Normal build | This build |
|----------------|--------------|------------|
| Periodic poll (`update_interval` → `_async_update_data`) | every `scan_interval` s | **off** (timer is `None`) |
| First refresh at setup (`async_config_entry_first_refresh`) | read burst | **off** (connect only) |
| Delayed re-poll after a write (`refresh_after`) | on | **off** |
| Options change re-arming the poll timer | on | **off** |
| User commands (power/input/etc.) | on | **on** |
| Unsolicited `INFOn` status pushes | on | **on** (passive; no bytes sent) |

Side effect: device details the first poll would populate (firmware, serial,
MAC) are **empty** in this build by design — they come from reads.

## Incremental re-enable plan

Once you have a clean baseline (no crashes for long enough to trust it — given
the ~hourly rate, aim for well beyond that, e.g. overnight), add polling back
one step at a time. Test each step long enough to trust it before moving on.

1. **Baseline — no polling.** `DISABLE_POLLING = True`. Confirm crashes stop.
2. **Power/status only, slow.** Flip `DISABLE_POLLING = False`, set a long
   `scan_interval` (e.g. 300 s) in the integration options, and temporarily
   narrow the poll to just the power read. The simplest narrowing: in
   `coordinator._iter_readable_entities`, `yield` only the switch whose
   `key == "power"` and skip the rest. Watch for the toast.
3. **Widen the reads.** Add back groups of reads (selects, then numbers, then
   sensors, then `device_info`) one group per test run, keeping the interval
   long. When the toast returns, the group you just added contains the
   offending read.
4. **Isolate the read.** Within the offending group, bisect down to the single
   command code that trips the firmware.
5. **Shorten the interval.** With the offending read identified/handled, bring
   `scan_interval` back down toward the default (30 s) and confirm stability.

Suspected culprits to try last / watch closely: rapid-fire reads with no gap
between them, and any read the firmware may not truly support (it answers but
its service chokes). If one specific code is the trigger, the fix is to drop
that read from the profile (or the poll loop) and rely on optimistic state /
status pushes for it — then validate and merge to `main`.

## Restoring normal behavior

Set `DISABLE_POLLING = False` in [`const.py`](custom_components/optoma_link/const.py)
and reload the integration. That is the only switch.
