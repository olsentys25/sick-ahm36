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
        {"interface": "cansub", "channel": "...", "bitrate": 250000}
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
        cfg = dict(config)
        cfg.pop("interface", None)  # passed positionally below for clarity
        interface = config.get("interface")
        cfg.setdefault("bitrate", protocol.DEFAULT_BITRATE)
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
