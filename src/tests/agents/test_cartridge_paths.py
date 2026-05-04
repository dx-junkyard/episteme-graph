"""Tests for agent cartridge path resolution."""
from __future__ import annotations

from episteme_graph.agents.cartridge_paths import resolve_cartridge_base_dir


def test_env_override_takes_precedence(monkeypatch, tmp_path):
    root = tmp_path / "cartridges"
    root.mkdir()
    monkeypatch.setenv("EPISTEME_CARTRIDGES_DIR", str(root))

    assert resolve_cartridge_base_dir("/missing/default") == str(root.resolve())
