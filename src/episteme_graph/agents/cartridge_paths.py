"""Cartridge path resolution shared by document analysis agents."""
from __future__ import annotations

import os
from pathlib import Path


def resolve_cartridge_base_dir(default_from_file: str) -> str:
    override = os.environ.get("EPISTEME_CARTRIDGES_DIR")
    if override:
        return str(Path(override).resolve())

    try:
        from core.cartridges import _cartridges_root

        return str(_cartridges_root().resolve())
    except Exception:
        pass

    default_path = Path(default_from_file).resolve()
    candidates = [
        default_path,
        Path.cwd() / "cartridges",
        Path.cwd() / "backend" / "cartridges",
        Path("/app/cartridges"),
        Path("/app/backend/cartridges"),
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate.resolve())
    return str(default_path)
