"""User-level AI configuration stored in ~/.config/boat/ai.toml.

The API key is stored in the config file at ~/.config/boat/ which is
outside any git repository, so it will never be committed.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_CONFIG_PATH = Path.home() / ".config" / "boat" / "ai.toml"

_DEFAULTS = {
    "endpoint": "http://localhost:11434/v1",
    "model": "qwen2.5-coder:3b",
    "timeout": 120,
    "api_key": "",
}


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***" if key else "(not set)"
    return key[:5] + "..." + key[-4:]


@dataclass
class AiConfig:
    endpoint: str
    model: str
    timeout: int
    api_key: str = ""

    @property
    def config_path(self) -> Path:
        return _CONFIG_PATH

    @property
    def masked_api_key(self) -> str:
        return _mask_key(self.api_key)


def load() -> AiConfig:
    cfg = dict(_DEFAULTS)
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as fh:
            data = tomllib.load(fh)
        cfg.update(data.get("ai", {}))
    return AiConfig(
        endpoint=str(cfg["endpoint"]),
        model=str(cfg["model"]),
        timeout=int(cfg["timeout"]),
        api_key=str(cfg.get("api_key", "")),
    )


def save(
    endpoint: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
    api_key: str | None = None,
) -> AiConfig:
    current = load()
    updated = AiConfig(
        endpoint=endpoint if endpoint is not None else current.endpoint,
        model=model if model is not None else current.model,
        timeout=timeout if timeout is not None else current.timeout,
        api_key=api_key if api_key is not None else current.api_key,
    )
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write("[ai]\n")
        fh.write(f'endpoint = "{updated.endpoint}"\n')
        fh.write(f'model    = "{updated.model}"\n')
        fh.write(f'timeout  = {updated.timeout}\n')
        if updated.api_key:
            fh.write(f'api_key  = "{updated.api_key}"\n')
    return updated
