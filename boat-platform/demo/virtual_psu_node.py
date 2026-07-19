#!/usr/bin/env python3
"""Virtual bench power-supply node (interactive, SDK/BusNode based).

The Python counterpart of the ``virtual_psu`` C++ plugin. It models a single
programmable DC supply purely as named signals on the always-on signal bus, so
it needs no gateway rebuild and can be started/stopped like any other node.

Signals consumed (subscribed):
    psu.<id>.voltage.set        target output voltage [V]  (clamped v_min..v_max)
    psu.<id>.enable             output on/off (0 = off, else on)
    psu.<id>.load.resistance    load resistance [ohm] (0 = open circuit, I = 0)

Signals published:
    psu.<id>.voltage.meas       measured output voltage [V]
    psu.<id>.current.meas       measured output current [A] = V / R, i_limit-capped

Unlike the C++ plugin (tick-driven, deterministic), this node ramps on a
wall-clock timer — handy for interactive bench-style use, not for the
determinism seed test.

Usage:
    python3 demo/virtual_psu_node.py --id main --v-set 13.5 --r-load 3.0
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from boat.bus_node import BusNode


class VirtualPsuNode(BusNode):
    def __init__(self, address: str, dev_id: str, v_set: float, v_min: float,
                 v_max: float, ramp_v_per_s: float, r_load: float,
                 i_limit: float, enabled: bool, period_s: float) -> None:
        super().__init__(address=address, node_id=f"virtual_psu.{dev_id}")
        self._id = dev_id
        self._v_min = v_min
        self._v_max = v_max
        self._ramp_v_per_s = ramp_v_per_s
        self._i_limit = i_limit
        self._period_s = period_s

        self._lock = threading.Lock()
        self._enabled = enabled
        self._v_target = self._clamp(v_set)
        self._r_load = max(0.0, r_load)
        self._v_meas = 0.0
        self._i_meas = 0.0
        self._last_pub: tuple[float, float] | None = None
        self._stop = threading.Event()

        base = f"psu.{dev_id}."
        self.sig_v_set = base + "voltage.set"
        self.sig_enable = base + "enable"
        self.sig_r_load = base + "load.resistance"
        self.sig_v_meas = base + "voltage.meas"
        self.sig_i_meas = base + "current.meas"

    def _clamp(self, v: float) -> float:
        return max(self._v_min, min(self._v_max, v))

    # --- input signals -------------------------------------------------
    def on_signal(self, signal) -> None:  # noqa: ANN001
        with self._lock:
            if signal.name == self.sig_v_set:
                self._v_target = self._clamp(signal.number_value)
            elif signal.name == self.sig_enable:
                self._enabled = signal.number_value != 0.0
            elif signal.name == self.sig_r_load:
                self._r_load = max(0.0, signal.number_value)

    # --- ramp / publish loop ------------------------------------------
    def run_model(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                target = self._v_target if self._enabled else 0.0
                step = self._ramp_v_per_s * self._period_s
                delta = target - self._v_meas
                if abs(delta) <= step:
                    self._v_meas = target
                else:
                    self._v_meas += step if delta > 0 else -step
                self._i_meas = (self._v_meas / self._r_load) if self._r_load > 0 else 0.0
                if self._i_limit > 0 and self._i_meas > self._i_limit:
                    self._i_meas = self._i_limit
                v, i = round(self._v_meas, 6), round(self._i_meas, 6)

            if self._last_pub != (v, i):
                self._last_pub = (v, i)
                self.publish(self.sig_v_meas, v)
                self.publish(self.sig_i_meas, i)
            self._stop.wait(self._period_s)

    def stop(self) -> None:  # noqa: D102
        self._stop.set()
        super().stop()


def main() -> int:
    ap = argparse.ArgumentParser(description="Virtual bench power-supply node")
    ap.add_argument("--address", default="localhost:50051")
    ap.add_argument("--id", dest="dev_id", default="main")
    ap.add_argument("--v-set", type=float, default=13.5)
    ap.add_argument("--v-min", type=float, default=0.0)
    ap.add_argument("--v-max", type=float, default=60.0)
    ap.add_argument("--ramp-v-per-s", type=float, default=50.0)
    ap.add_argument("--r-load", type=float, default=0.0, help="ohms; 0 = open circuit")
    ap.add_argument("--i-limit", type=float, default=0.0, help="amps; 0 = unlimited")
    ap.add_argument("--disabled", action="store_true", help="start with output off")
    ap.add_argument("--period", type=float, default=0.05, help="model/publish period [s]")
    args = ap.parse_args()

    node = VirtualPsuNode(
        address=args.address, dev_id=args.dev_id, v_set=args.v_set,
        v_min=args.v_min, v_max=args.v_max, ramp_v_per_s=args.ramp_v_per_s,
        r_load=args.r_load, i_limit=args.i_limit, enabled=not args.disabled,
        period_s=args.period,
    )

    def _sigint(_sig, _frame):
        node.stop()

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    model = threading.Thread(target=node.run_model, daemon=True)
    model.start()
    print(f"[virtual_psu.{args.dev_id}] running — Ctrl-C to stop", file=sys.stderr)
    node.run(names=[node.sig_v_set, node.sig_enable, node.sig_r_load])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
