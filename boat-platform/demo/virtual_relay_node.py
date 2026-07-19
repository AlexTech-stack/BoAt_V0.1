#!/usr/bin/env python3
"""Virtual relay / contactor node (interactive, SDK/BusNode based).

The Python counterpart of the ``virtual_relay`` C++ plugin. Models a single
relay (e.g. an ignition KL15 or main-power KL30 contactor) as named signals.

Signals consumed (subscribed):
    relay.<id>.set      commanded coil state (0 = open, else closed)

Signals published:
    relay.<id>.state    settled contact state (0.0 = open, 1.0 = closed)

A wall-clock debounce models contact settling. Pair with the restbus demo's
``--kl15-relay`` gate to start/stop cyclic transmission from an ignition switch.

Usage:
    python3 demo/virtual_relay_node.py --id kl15
    # then, from anywhere:
    #   BusNode().publish("relay.kl15.set", True)   # ignition on
    #   BusNode().publish("relay.kl15.set", False)  # ignition off
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from boat.bus_node import BusNode


class VirtualRelayNode(BusNode):
    def __init__(self, address: str, dev_id: str, default_closed: bool,
                 debounce_s: float) -> None:
        super().__init__(address=address, node_id=f"virtual_relay.{dev_id}")
        self._id = dev_id
        self._debounce_s = debounce_s
        self._lock = threading.Lock()
        self._contact_closed = default_closed
        self._timer: threading.Timer | None = None

        self.sig_set = f"relay.{dev_id}.set"
        self.sig_state = f"relay.{dev_id}.state"

    def announce(self) -> None:
        """Publish the initial contact state once at startup."""
        self.publish(self.sig_state, 1.0 if self._contact_closed else 0.0)

    def on_signal(self, signal) -> None:  # noqa: ANN001
        if signal.name != self.sig_set:
            return
        want = signal.number_value != 0.0
        with self._lock:
            if want == self._contact_closed:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._settle, args=(want,))
            self._timer.daemon = True
            self._timer.start()

    def _settle(self, want: bool) -> None:
        with self._lock:
            self._contact_closed = want
        self.publish(self.sig_state, 1.0 if want else 0.0)

    def stop(self) -> None:  # noqa: D102
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
        super().stop()


def main() -> int:
    ap = argparse.ArgumentParser(description="Virtual relay / contactor node")
    ap.add_argument("--address", default="localhost:50051")
    ap.add_argument("--id", dest="dev_id", default="kl15")
    ap.add_argument("--default-closed", action="store_true",
                    help="start with the contact closed")
    ap.add_argument("--debounce", type=float, default=0.0, help="settling time [s]")
    args = ap.parse_args()

    node = VirtualRelayNode(
        address=args.address, dev_id=args.dev_id,
        default_closed=args.default_closed, debounce_s=args.debounce,
    )

    def _sigint(_sig, _frame):
        node.stop()

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    node.announce()
    print(f"[virtual_relay.{args.dev_id}] running — Ctrl-C to stop", file=sys.stderr)
    node.run(names=[node.sig_set])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
