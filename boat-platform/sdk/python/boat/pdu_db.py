"""PDU database loader.

Loads a pdu_db.json file (see config/pdu_db.schema.json) and provides
lookup by DbId, MessageName, or MessageName+Bus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class PduDatabase:
    def __init__(self, path: str | Path) -> None:
        with open(path) as f:
            raw = json.load(f)
        self._messages: Dict[int, dict]       = {}
        self._by_name:  Dict[str, list[dict]] = {}
        self._by_name_bus: Dict[str, dict]    = {}
        self._routes:   List[dict]            = raw.get("signal_routes", [])

        for msg in raw.get("messages", []):
            db_id = msg["DbId"]
            name  = msg["MessageName"]
            bus   = msg.get("Bus", "")
            self._messages[db_id] = msg
            self._by_name.setdefault(name, []).append(msg)
            self._by_name_bus[f"{name}\x1f{bus}"] = msg

    # ------------------------------------------------------------------

    def by_id(self, db_id: int) -> Optional[dict]:
        return self._messages.get(db_id)

    def by_name(self, name: str) -> Optional[list[dict]]:
        """Return all messages matching *name* (may appear on different buses)."""
        return self._by_name.get(name)

    def by_name_and_bus(self, name: str, bus: str) -> Optional[dict]:
        return self._by_name_bus.get(f"{name}\x1f{bus}")

    def signal_routes(self) -> List[dict]:
        return list(self._routes)

    def names(self) -> List[str]:
        return list(self._by_name.keys())

    def messages(self) -> List[dict]:
        return list(self._messages.values())
