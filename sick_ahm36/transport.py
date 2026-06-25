"""CAN transport abstraction.

The only hardware-touching layer. It wraps ``python-can`` so the rest of the
library talks to a tiny, backend-neutral interface (``recv`` / ``send`` /
``close``). The actual adapter - CSS CANsub, PEAK, Kvaser, SocketCAN,
``virtual`` - is chosen entirely by the config dict, so picking the hardware
later costs nothing here.

A small :class:`Frame` decouples the upper layers from ``can.Message`` (and lets
the simulator run with no ``python-can`` installed at all).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import protocol

# Config keys consumed by the higher layers (encoder / decode / sim), NOT by
# python-can. They must be stripped before the config is forwarded to can.Bus,
# or backends that validate their kwargs will choke on them.
_NON_BUS_KEYS = frozenset({
    "interface", "source_address", "client_address",
    "speed_format", "steps_per_rev", "config_timeout",
    "sim_speed_rpm", "sim_cycle_ms", "sim_status",
})


def bus_kwargs(config: dict) -> tuple[str | None, dict]:
    """Split a transport config into ``(interface, can.Bus kwargs)``.

    Drops the higher-layer-only keys and applies backend-specific fix-ups.
    Currently the only fix-up is for Vector: python-can's Vector backend wants
    an ``int`` channel and, by default, resolves channel numbers through a
    Vector Hardware Config *application* (``app_name`` defaults to "CANalyzer").
    Forcing ``app_name=None`` addresses the global channel index directly, so a
    plain ``channel=0`` works without any application assignment or a CANalyzer
    license. A ``serial`` key (to pick a specific device) passes straight
    through if the caller supplies one.
    """
    interface = config.get("interface")
    cfg = {k: v for k, v in config.items() if k not in _NON_BUS_KEYS}
    cfg.setdefault("bitrate", protocol.DEFAULT_BITRATE)
    if interface == "vector":
        if cfg.get("channel") is not None:
            cfg["channel"] = int(cfg["channel"])
        cfg.setdefault("app_name", None)
    return interface, cfg


@dataclass
class Frame:
    """A minimal CAN frame, independent of any backend type."""

    arbitration_id: int
    data: bytes
    is_extended_id: bool = True   # J1939 is always 29-bit
    timestamp: float = 0.0


class BaseTransport:
    """Interface every transport (real or simulated) implements."""

    def recv(self, timeout: float | None = None) -> Frame | None:
        raise NotImplementedError

    def send(self, frame: Frame) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> "BaseTransport":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class CanTransport(BaseTransport):
    """Real CAN transport backed by ``python-can``.

    ``config`` is forwarded to ``can.Bus``. Typical keys::

        {"interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 250000}
        {"interface": "socketcan", "channel": "can0"}
        {"interface": "vector", "channel": 0, "bitrate": 250000}

    For Vector hardware (VN16xx, CANcase, ...) the channel is the global channel
    index from Vector Hardware Configuration; :func:`bus_kwargs` coerces it to an
    int and sets ``app_name=None`` so no CANalyzer application assignment is
    needed.
    """

    def __init__(self, config: dict):
        try:
            import can  # imported lazily so sim mode needs no python-can
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "python-can is required for real CAN transport. "
                "Install it with: pip install python-can"
            ) from exc

        self._can = can
        interface, cfg = bus_kwargs(config)
        self._bus = can.Bus(interface=interface, **cfg)

    def recv(self, timeout: float | None = None) -> Frame | None:
        msg = self._bus.recv(timeout=timeout)
        if msg is None:
            return None
        return Frame(
            arbitration_id=msg.arbitration_id,
            data=bytes(msg.data),
            is_extended_id=msg.is_extended_id,
            timestamp=msg.timestamp,
        )

    def send(self, frame: Frame) -> None:
        msg = self._can.Message(
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            is_extended_id=frame.is_extended_id,
        )
        self._bus.send(msg)

    def close(self) -> None:
        try:
            self._bus.shutdown()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def open_transport(config: dict) -> BaseTransport:
    """Factory: build the transport named by ``config['interface']``.

    ``interface="sim"`` returns the built-in simulator; anything else is handed
    to :class:`CanTransport` / ``python-can``.
    """
    if config.get("interface") == "sim":
        from .sim import SimulatedBus
        return SimulatedBus(config)
    return CanTransport(config)
