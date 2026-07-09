"""Example Bus node: engine-state-logger.

Subscribes to 'engine.rpm' and 'engine.temp' signals on the BoAt bus.
Publishes a derived 'engine.state' string signal ('idle', 'normal', 'overheat')
whenever either input changes.
"""
from __future__ import annotations

from boat.bus_node import BusNode


class EngineStateLogger(BusNode):
    def __init__(self) -> None:
        super().__init__(address="localhost:50051", node_id="engine-state-logger")
        self._rpm: float = 0.0
        self._temp: float = 0.0

    def on_signal(self, signal) -> None:
        if signal.name == "engine.rpm":
            self._rpm = signal.number_value
        elif signal.name == "engine.temp":
            self._temp = signal.number_value
        else:
            return

        # Derive state from latest values
        if self._temp > 110.0:
            state = "overheat"
        elif self._rpm < 800.0:
            state = "idle"
        else:
            state = "normal"

        self.publish("engine.state", state)


if __name__ == "__main__":
    node = EngineStateLogger()
    # Subscribe to the two signals we care about; on_signal is called for each.
    node.run(names=["engine.rpm", "engine.temp"])
