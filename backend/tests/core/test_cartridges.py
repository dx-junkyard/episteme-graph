"""カートリッジローダーの単体テスト (Issue #176)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import cartridges as cartridge_loader
from core.cartridges import (
    DomainCartridge,
    clear_cache,
    list_cartridges,
    load_cartridge,
    resolve_maturity,
)
from core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_cache():
    """各テスト前後に設定キャッシュとカートリッジキャッシュをクリアする。"""
    get_settings.cache_clear()
    clear_cache()
    yield
    get_settings.cache_clear()
    clear_cache()


# ---------------------------------------------------------------------------
# Required files
# ---------------------------------------------------------------------------


REQUIRED_FILES = (
    "cartridge.json",
    "ontology.json",
    "component_types.json",
    "relation_types.json",
    "maturity_levels.json",
    "support_statuses.json",
    "extraction_prompt.md",
    "review_prompt.md",
    "validation_rules.json",
)


def _cartridge_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "cartridges" / "particle_physics"


def test_required_files_exist():
    base = _cartridge_dir()
    for name in REQUIRED_FILES:
        path = base / name
        assert path.exists(), f"missing required cartridge file: {name}"


def test_examples_directory_exists():
    examples_dir = _cartridge_dir() / "examples"
    assert examples_dir.is_dir()
    assert any(examples_dir.glob("*.json")), "examples directory should contain at least one JSON example"


# ---------------------------------------------------------------------------
# load_cartridge
# ---------------------------------------------------------------------------


def test_load_particle_physics_cartridge():
    cart = load_cartridge("particle_physics")
    assert isinstance(cart, DomainCartridge)
    assert cart.cartridge_id == "particle_physics"
    assert cart.version
    assert "particle_physics" in cart.target_domain


def test_default_cartridge_is_particle_physics():
    default = load_cartridge()
    assert default.cartridge_id == "particle_physics"


def test_concept_types_contain_theory():
    cart = load_cartridge("particle_physics")
    assert "Theory" in cart.ontology.concept_type_ids
    assert "Observable" in cart.ontology.concept_type_ids


def test_component_types_have_default_maturity():
    cart = load_cartridge("particle_physics")
    types_by_id = {entry["id"]: entry for entry in cart.component_types}
    assert "PaperClaimComponent" in types_by_id
    assert types_by_id["PaperClaimComponent"]["default_maturity_level"] == "paper_claim"
    assert types_by_id["DomainTheoryComponent"]["default_maturity_level"] == "canonical"


def test_paper_claim_in_maturity_levels():
    cart = load_cartridge("particle_physics")
    assert "paper_claim" in cart.maturity_level_ids
    assert "canonical" in cart.maturity_level_ids


def test_maturity_sources_include_llm_proposed():
    cart = load_cartridge("particle_physics")
    assert "llm_proposed" in cart.maturity_source_ids
    assert "teacher_reviewed" in cart.maturity_source_ids


def test_support_status_ids_unique_and_complete():
    cart = load_cartridge("particle_physics")
    ids = list(cart.support_status_ids)
    assert "source_backed" in ids
    assert "domain_inferred" in ids
    assert len(ids) == len(set(ids))


def test_relation_type_ids_no_duplicates():
    cart = load_cartridge("particle_physics")
    ids = list(cart.relation_type_ids)
    assert "REQUIRES" in ids
    assert len(ids) == len(set(ids))


def test_extraction_prompt_loaded():
    cart = load_cartridge("particle_physics")
    assert cart.extraction_prompt
    assert "理論コンポーネント" in cart.extraction_prompt


def test_examples_loaded():
    cart = load_cartridge("particle_physics")
    assert len(cart.examples) >= 2
    component_ids = {ex.get("component_id") for ex in cart.examples}
    assert "domain.hqet" in component_ids


# ---------------------------------------------------------------------------
# list_cartridges
# ---------------------------------------------------------------------------


def test_list_cartridges_includes_particle_physics():
    summaries = list_cartridges()
    ids = [s.cartridge_id for s in summaries]
    assert "particle_physics" in ids


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_cartridge_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        load_cartridge("does_not_exist")


def test_to_dict_round_trip():
    cart = load_cartridge("particle_physics")
    payload = cart.to_dict()
    assert payload["cartridge_id"] == "particle_physics"
    assert isinstance(payload["component_types"], list)
    assert payload["ontology"]["concept_types"]


# ---------------------------------------------------------------------------
# resolve_maturity
# ---------------------------------------------------------------------------


def test_resolve_maturity_uses_curated_registry_first():
    cart = load_cartridge("particle_physics")
    component = {
        "name": "Heavy Quark Effective Theory",
        "component_type": "DomainTheoryComponent",
        "origin": "domain",
    }
    registry = {
        "Heavy Quark Effective Theory": {"maturity_level": "reviewed_consensus"},
    }
    resolved = resolve_maturity(component, cart, curated_registry=registry)
    assert resolved["maturity_level"] == "reviewed_consensus"
    assert resolved["maturity_source"] == "curated_registry"


def test_resolve_maturity_falls_back_to_component_type_default():
    cart = load_cartridge("particle_physics")
    component = {
        "name": "Some Paper Claim",
        "component_type": "PaperClaimComponent",
        "origin": "paper",
    }
    resolved = resolve_maturity(component, cart)
    assert resolved["maturity_level"] == "paper_claim"
    assert resolved["maturity_source"] == "cartridge_default"


def test_resolve_maturity_paper_origin_without_type():
    cart = load_cartridge("particle_physics")
    component = {"name": "x", "origin": "paper"}
    resolved = resolve_maturity(component, cart)
    assert resolved["maturity_level"] == "paper_claim"
    assert resolved["maturity_source"] == "cartridge_default"


def test_resolve_maturity_unknown_when_no_signals():
    cart = load_cartridge("particle_physics")
    resolved = resolve_maturity({"name": "x"}, cart)
    assert resolved["maturity_level"] == "unknown"
    assert resolved["maturity_source"] == "unknown"


# ---------------------------------------------------------------------------
# Settings integration (Issue #176 follow-up)
# ---------------------------------------------------------------------------


def test_settings_default_cartridge_id_field(monkeypatch):
    """Settings に EPISTEME_DEFAULT_CARTRIDGE_ID が読み込まれること。"""
    monkeypatch.setenv("EPISTEME_DEFAULT_CARTRIDGE_ID", "particle_physics")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.default_cartridge_id == "particle_physics"


def test_settings_cartridges_dir_field(monkeypatch, tmp_path):
    """Settings に EPISTEME_CARTRIDGES_DIR が読み込まれること。"""
    monkeypatch.setenv("EPISTEME_CARTRIDGES_DIR", str(tmp_path))
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.cartridges_dir == str(tmp_path)


def test_load_cartridge_uses_settings_default_cartridge_id(monkeypatch):
    """EPISTEME_DEFAULT_CARTRIDGE_ID が load_cartridge() のデフォルトに反映されること。"""
    monkeypatch.setenv("EPISTEME_DEFAULT_CARTRIDGE_ID", "particle_physics")
    get_settings.cache_clear()
    clear_cache()
    cart = load_cartridge()
    assert cart.cartridge_id == "particle_physics"


def test_load_cartridge_falls_back_when_env_unset(monkeypatch):
    """EPISTEME_DEFAULT_CARTRIDGE_ID 未設定時は particle_physics にフォールバックすること。"""
    monkeypatch.delenv("EPISTEME_DEFAULT_CARTRIDGE_ID", raising=False)
    get_settings.cache_clear()
    clear_cache()
    cart = load_cartridge()
    assert cart.cartridge_id == "particle_physics"


def test_load_cartridge_falls_back_when_env_empty(monkeypatch):
    """EPISTEME_DEFAULT_CARTRIDGE_ID が空文字の場合も particle_physics にフォールバックすること。"""
    monkeypatch.setenv("EPISTEME_DEFAULT_CARTRIDGE_ID", "")
    get_settings.cache_clear()
    clear_cache()
    cart = load_cartridge()
    assert cart.cartridge_id == "particle_physics"


def test_cartridges_dir_from_settings(tmp_path, monkeypatch):
    """EPISTEME_CARTRIDGES_DIR を Settings 経由で設定できること。"""
    # particle_physics カートリッジを tmp_path にコピーして読み込めることを確認
    import shutil
    real_root = Path(__file__).resolve().parents[2] / "cartridges"
    shutil.copytree(str(real_root), str(tmp_path / "copied"), dirs_exist_ok=True)

    monkeypatch.setenv("EPISTEME_CARTRIDGES_DIR", str(tmp_path / "copied"))
    get_settings.cache_clear()
    clear_cache()
    cart = load_cartridge("particle_physics")
    assert cart.cartridge_id == "particle_physics"


# ---------------------------------------------------------------------------
# Internal consistency (negative cases via Settings-aware env override)
# ---------------------------------------------------------------------------


def _write_minimal_cartridge(cart_dir: Path, extra_component_types: str = "[]") -> None:
    """テスト用最小カートリッジファイル一式を書き出す。"""
    (cart_dir / "cartridge.json").write_text(
        f'{{"cartridge_id": "{cart_dir.name}", "name": "test", "version": "0.0.1", "files": {{}}}}',
        encoding="utf-8",
    )
    (cart_dir / "ontology.json").write_text('{"concept_types": []}', encoding="utf-8")
    (cart_dir / "component_types.json").write_text(
        f'{{"component_types": {extra_component_types}}}', encoding="utf-8"
    )
    (cart_dir / "relation_types.json").write_text('{"relation_types": []}', encoding="utf-8")
    (cart_dir / "maturity_levels.json").write_text(
        '{"maturity_levels": [{"id": "canonical"}]}', encoding="utf-8"
    )
    (cart_dir / "support_statuses.json").write_text('{"support_statuses": []}', encoding="utf-8")
    (cart_dir / "validation_rules.json").write_text('{"validation_rules": []}', encoding="utf-8")
    (cart_dir / "extraction_prompt.md").write_text("", encoding="utf-8")
    (cart_dir / "review_prompt.md").write_text("", encoding="utf-8")


def test_duplicate_ids_raise(tmp_path, monkeypatch):
    cart_dir = tmp_path / "broken"
    cart_dir.mkdir()
    _write_minimal_cartridge(cart_dir, '[{"id": "Dup"}, {"id": "Dup"}]')

    monkeypatch.setenv("EPISTEME_CARTRIDGES_DIR", str(tmp_path))
    get_settings.cache_clear()
    clear_cache()
    with pytest.raises(ValueError, match="duplicate id"):
        load_cartridge("broken")


def test_unknown_default_maturity_raises(tmp_path, monkeypatch):
    cart_dir = tmp_path / "unknown_maturity"
    cart_dir.mkdir()
    _write_minimal_cartridge(cart_dir, '[{"id": "X", "default_maturity_level": "no_such"}]')

    monkeypatch.setenv("EPISTEME_CARTRIDGES_DIR", str(tmp_path))
    get_settings.cache_clear()
    clear_cache()
    with pytest.raises(ValueError, match="unknown default_maturity_level"):
        load_cartridge("unknown_maturity")
