from __future__ import annotations

from pathlib import Path

# Expose generated stubs as `boat.v1.*` while keeping files in `boat/stubs/boat/v1`.
_STUBS_V1_DIR = Path(__file__).resolve().parents[1] / "stubs" / "boat" / "v1"
if _STUBS_V1_DIR.is_dir():
    __path__.append(str(_STUBS_V1_DIR))
