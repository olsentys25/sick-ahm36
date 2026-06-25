# sick_ahm36

A small, modular Python library to read **speed** and **position** from a
**SICK AHM36A-BDJC014X12** absolute multiturn encoder over **SAE J1939 / CAN**.

The encoder *broadcasts* its process data by default (PGN `0xFFE0` every 10 ms),
so reading speed and position is a **listen-and-decode** operation — there is
nothing to request. Configuration of other parameters is a planned phase 2.

## Environment setup (recommended for the team)

Use a project virtual environment on **Python 3.12** (stable, with the best
prebuilt-wheel and CAN-driver support). Avoid the Microsoft Store Python — it
sandboxes file access and can interfere with USB CAN-adapter drivers.

**Windows (one-shot bootstrap):**

```powershell
.\setup.ps1
```

This finds Python 3.12 (or tells you where to install it), creates `.venv`,
installs the package editable with dev tools, and runs the tests.

**Or do it manually (any OS):**

```bash
py -3.12 -m venv .venv            # Windows
# python3.12 -m venv .venv        # macOS/Linux
.venv\Scripts\activate            # Windows  (source .venv/bin/activate elsewhere)
pip install -e .[dev]             # installs sick_ahm36 + python-can + pytest
```

After an editable install you can `import sick_ahm36` from anywhere and run
`pytest` directly — no `sys.path` juggling needed.

## Quick start (no hardware)

```bash
python examples/read_speed_position.py
```

This runs against the built-in **simulator** (`interface="sim"`) and prints a
steadily advancing position with a fixed speed — useful for developing and
testing the decode/API path with nothing connected.

## Using real hardware

1. Install the CAN backend dependency:
   ```bash
   pip install -r requirements.txt
   ```
2. Pick your CAN adapter's [python-can](https://python-can.readthedocs.io)
   backend and point the config at it:
   ```python
   from sick_ahm36 import Ahm36Encoder

   config = {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 250000}
   # examples:
   #   {"interface": "socketcan", "channel": "can0"}
   #   {"interface": "cansub",    "channel": "<device>", "bitrate": 250000}

   with Ahm36Encoder(config) as enc:
       data = enc.read_process_data()      # decoded ProcessData
       print(data.position_counts, data.speed_raw, data.status_word)
       print(enc.get_position(), enc.get_speed())
   ```

The adapter backend is the *only* thing that changes between hardware vendors —
the rest of the library is backend-neutral.

### Non-blocking reads

```python
with Ahm36Encoder(config) as enc:
    enc.start_background()      # caches the latest frame in a thread
    ...
    enc.get_position()         # returns immediately from the cache
```

## GUI

A Tkinter desktop app shows live position/speed/status and lets you read and
write configuration parameters over the CAN bus. Tkinter ships with Python, so
there's nothing extra to install.

```bash
python -m sick_ahm36.gui        # or, after `pip install -e .`, run: sick-ahm36-gui
```

It opens against `interface="sim"` by default — pick your real backend and
channel in the Connection bar, then **Connect**. Features:

- **Live panel** — position (counts / revolutions / degrees), speed with unit,
  and status (green OK / red fault with decoded messages).
- **Configuration panel** — read/write every parameter (`Read all` populates the
  table), plus Preset, Power cycle, and Factory reset.
- **Safety confirmations** — writing baud rate or node address, and the
  power-cycle / factory-reset actions, pop a confirmation first, since those can
  drop the encoder off the bus or are destructive.

The simulator answers configuration messages too, so the whole GUI — including
the config panel — works with no hardware connected.

## Wiring (M12, 5-pin male)

| Pin | Signal        | Wire   |
|-----|---------------|--------|
| 1   | CAN Shield    | White  |
| 2   | V+ (10–30 V)  | Red    |
| 3   | GND / CAN GND | Blue   |
| 4   | CAN High      | Black  |
| 5   | CAN Low       | Pink   |

- Default bit rate **250 kbit/s**; default node (source) address **224 (0xE0)**.
- A **120 Ω terminator** is required between CAN High and CAN Low at **each end**
  of the bus (the encoder has no built-in terminator).

## Layout

```
sick_ahm36/
  protocol.py   constants/spec: PGNs, status-word bits, parameter table
  j1939.py      generic 29-bit identifier encode/decode
  decode.py     raw bytes -> ProcessData (position, speed, status)
  config.py     PGN 0xEF00 configuration message framing
  transport.py  python-can wrapper; backend chosen by config
  sim.py        offline simulator (process data + config replies)
  encoder.py    high-level Ahm36Encoder client (read + configure)
  gui.py        Tkinter monitor + config app
examples/read_speed_position.py
tests/test_decode.py, tests/test_config.py
```

`protocol`, `j1939`, and `decode` are pure and unit-testable with no hardware:

```bash
python -m pytest tests/        # or: python tests/test_decode.py
```

## Configuration (PGN 0xEF00)

Configuration uses point-to-point PGN `0xEF00` read/write messages. The full
parameter table (baud rate, steps/rev, speed format, preset, node address,
resets, …) lives in `protocol.PARAM_INFO` / `protocol.Param`. From code:

```python
from sick_ahm36 import Ahm36Encoder, Param

with Ahm36Encoder(config) as enc:
    enc.start_background()                 # recommended: runs the frame dispatcher
    print(enc.read_param(Param.SPEED_FORMAT))
    enc.write_param(Param.SPEED_FORMAT, 4) # 4 = rps
    enc.preset(0)                          # set current position to 0
    all_values = enc.read_all_params()
```

Writes that change baud rate or node address will drop the encoder off the bus
until the master matches the new setting; `power_cycle()` and `factory_reset()`
are disruptive/destructive. The GUI guards these with confirmation dialogs.
