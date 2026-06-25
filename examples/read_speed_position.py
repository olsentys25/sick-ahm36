"""Minimal example: print position + speed from the AHM36A encoder.

Runs against the offline simulator out of the box (no hardware needed):

    python examples/read_speed_position.py

To talk to a real encoder, pick your CAN adapter's python-can backend and edit
CONFIG below, e.g.:

    CONFIG = {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 250000}
    CONFIG = {"interface": "socketcan", "channel": "can0"}
    CONFIG = {"interface": "cansub", "channel": "<device>", "bitrate": 250000}
"""

import sys
from pathlib import Path

# allow running directly from the repo without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sick_ahm36 import Ahm36Encoder  # noqa: E402

# --- choose your transport here ---
CONFIG = {"interface": "sim", "sim_speed_rpm": 120.0}
# CONFIG = {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 250000}


def main() -> None:
    print(f"Opening encoder with config: {CONFIG}")
    with Ahm36Encoder(CONFIG) as enc:
        try:
            for _ in range(20):
                data = enc.read_process_data(timeout=2.0)
                if data is None:
                    print("(no frame within timeout - check wiring/baud/address)")
                    continue
                print(data)
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
