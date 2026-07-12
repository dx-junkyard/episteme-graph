"""episteme_graph.agents.cartridge_loader / cartridge_context の単体テスト。

共通 ``CartridgeLoader``（``backend/cartridges/<cartridge_id>/*.json`` を読み込み
``CartridgeContext`` を組み立てる, Tier2 提案9: 「cartridge 読み込みの統合」）と、
固有フィールドを重ねる3つのサブクラス（apparatus_semantics / component_assembly /
component_graph）を対象にする。実在の ``backend/cartridges/particle_physics/`` と
tmp_path 上の合成カートリッジの両方で検証する。外部 API は一切呼び出さない。
"""

from __future__ import annotations

import json

import pytest


def _write_json(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def synthetic_cartridge_dir(tmp_path):
    """最小限の JSON 一式を持つ合成カートリッジ（`synthetic`）のベースディレクトリ。"""
    base = tmp_path / "cartridges"
    cart_dir = base / "synthetic"
    cart_dir.mkdir(parents=True)
    _write_json(
        cart_dir / "ontology.json",
        {
            "aliases": [{"canonical": "Foo", "aliases": ["foo", "F"]}],
            "notation_patterns": [{"id": "p1", "pattern": "X_[0-9]+"}],
            "normalization_rules": [{"id": "r1", "description": "lowercase"}],
            "extraction_hints": {"note": "be careful"},
        },
    )
    _write_json(cart_dir / "validation_rules.json", {"validation_rules": ["rule1"]})
    _write_json(cart_dir / "component_types.json", {"component_types": ["TypeA", "TypeB"]})
    _write_json(cart_dir / "relation_types.json", {"relation_types": ["REL_A"]})
    return base


# ---------------------------------------------------------------------------
# 共通 CartridgeLoader.load()
# ---------------------------------------------------------------------------


class TestCartridgeLoaderLoad:
    def test_loads_real_cartridge_fields(self):
        """実在の backend/cartridges/particle_physics/ を読み込み、全フィールドが埋まる。"""
        from episteme_graph.agents.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader()
        ctx = loader.load("particle_physics")

        assert ctx.cartridge_id == "particle_physics"
        assert ctx.ontology
        assert ctx.validation_rules
        assert ctx.aliases
        assert "Heavy Quark Effective Theory" in ctx.aliases
        assert ctx.notation_patterns
        assert ctx.normalization_rules
        assert ctx.extraction_hints

    def test_synthetic_cartridge_fields_match_source_json(self, synthetic_cartridge_dir):
        from episteme_graph.agents.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader(cartridge_base_dir=str(synthetic_cartridge_dir))
        ctx = loader.load("synthetic")

        assert ctx.cartridge_id == "synthetic"
        # aliases は {canonical: [aliases...]} 形式に変換される
        assert ctx.aliases == {"Foo": ["foo", "F"]}
        assert ctx.notation_patterns == [{"id": "p1", "pattern": "X_[0-9]+"}]
        assert ctx.normalization_rules == [{"id": "r1", "description": "lowercase"}]
        assert ctx.extraction_hints == {"note": "be careful"}
        assert ctx.validation_rules == {"validation_rules": ["rule1"]}


class TestCartridgeLoaderMissingOptionalFiles:
    def test_missing_json_files_default_to_empty_or_none(self, tmp_path):
        """ontology.json / validation_rules.json が無いディレクトリでも例外にせず、
        空 dict / None にフォールバックする（情報を落とさない設計に反しない）。"""
        from episteme_graph.agents.cartridge_loader import CartridgeLoader

        base = tmp_path / "cartridges"
        (base / "bare").mkdir(parents=True)
        loader = CartridgeLoader(cartridge_base_dir=str(base))

        ctx = loader.load("bare")

        assert ctx.ontology == {}
        assert ctx.validation_rules == {}
        assert ctx.aliases is None
        assert ctx.notation_patterns is None
        assert ctx.normalization_rules is None
        assert ctx.extraction_hints is None


class TestCartridgeLoaderNotFound:
    def test_nonexistent_cartridge_id_raises_file_not_found(self, tmp_path):
        """契約: 存在しない cartridge_id は FileNotFoundError（None にフォールバックしない）。"""
        from episteme_graph.agents.cartridge_loader import CartridgeLoader

        base = tmp_path / "cartridges"
        base.mkdir()
        loader = CartridgeLoader(cartridge_base_dir=str(base))

        with pytest.raises(FileNotFoundError):
            loader.load("does-not-exist")

    def test_none_cartridge_id_is_not_special_cased_by_loader(self, tmp_path):
        """契約: cartridge_id=None の単独動作フォールバックは各 agent の
        `_load_cartridge`（例: equation_semantics/agent.py, apparatus_semantics/agent.py）
        が担う。共有 CartridgeLoader 自体は None を特別扱いしないため
        os.path.join に None が渡り TypeError になる。"""
        from episteme_graph.agents.cartridge_loader import CartridgeLoader

        base = tmp_path / "cartridges"
        base.mkdir()
        loader = CartridgeLoader(cartridge_base_dir=str(base))

        with pytest.raises(TypeError):
            loader.load(None)


# ---------------------------------------------------------------------------
# 共通 CartridgeContext dataclass
# ---------------------------------------------------------------------------


class TestCartridgeContextDataclass:
    def test_positional_construction_with_required_fields(self):
        from episteme_graph.agents.cartridge_context import CartridgeContext

        ctx = CartridgeContext("cid", {"a": 1}, {"b": 2})

        assert ctx.cartridge_id == "cid"
        assert ctx.ontology == {"a": 1}
        assert ctx.validation_rules == {"b": 2}

    def test_optional_fields_default_to_none(self):
        from episteme_graph.agents.cartridge_context import CartridgeContext

        ctx = CartridgeContext(cartridge_id="cid", ontology={}, validation_rules={})

        assert ctx.aliases is None
        assert ctx.notation_patterns is None
        assert ctx.normalization_rules is None
        assert ctx.extraction_hints is None

    def test_json_serializable(self):
        import json as _json
        from dataclasses import asdict

        from episteme_graph.agents.cartridge_context import CartridgeContext

        ctx = CartridgeContext(
            cartridge_id="cid",
            ontology={"a": 1},
            validation_rules={"b": 2},
            aliases={"X": ["x"]},
            notation_patterns=[{"id": "p"}],
            normalization_rules=[{"id": "r"}],
            extraction_hints={"h": 1},
        )

        serialized = _json.dumps(asdict(ctx))
        restored = _json.loads(serialized)

        assert restored["cartridge_id"] == "cid"
        assert restored["extraction_hints"] == {"h": 1}


# ---------------------------------------------------------------------------
# サブクラス: apparatus_semantics
# ---------------------------------------------------------------------------


class TestApparatusSemanticsCartridgeLoaderSubclass:
    def test_folds_component_types_into_extraction_hints(self):
        """apparatus_semantics 固有: component_types.json を extraction_hints に折り込む。"""
        from episteme_graph.agents.apparatus_semantics.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader()
        ctx = loader.load("particle_physics")

        assert isinstance(ctx.extraction_hints, dict)
        assert "component_types" in ctx.extraction_hints
        assert ctx.extraction_hints["component_types"]

    def test_preserves_existing_dict_extraction_hints_when_folding(self, synthetic_cartridge_dir):
        """ontology.json の extraction_hints が dict のときは既存キーを保持したまま
        component_types を追加する。"""
        from episteme_graph.agents.apparatus_semantics.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader(cartridge_base_dir=str(synthetic_cartridge_dir))
        ctx = loader.load("synthetic")

        assert ctx.extraction_hints["note"] == "be careful"
        assert ctx.extraction_hints["component_types"] == ["TypeA", "TypeB"]

    def test_missing_component_types_leaves_extraction_hints_untouched(self, tmp_path):
        """component_types.json が無ければ折り込みをスキップし、
        ontology 由来の extraction_hints をそのまま返す。"""
        from episteme_graph.agents.apparatus_semantics.cartridge_loader import CartridgeLoader

        base = tmp_path / "cartridges"
        cart_dir = base / "no-components"
        cart_dir.mkdir(parents=True)
        _write_json(cart_dir / "ontology.json", {"extraction_hints": {"note": "plain"}})

        loader = CartridgeLoader(cartridge_base_dir=str(base))
        ctx = loader.load("no-components")

        assert ctx.extraction_hints == {"note": "plain"}


# ---------------------------------------------------------------------------
# サブクラス: component_assembly
# ---------------------------------------------------------------------------


class TestComponentAssemblyCartridgeLoaderSubclass:
    def test_loads_component_types_and_relation_types(self):
        """component_assembly 固有: component_types / relation_types を必須フィールドとして持つ。"""
        from episteme_graph.agents.component_assembly.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader()
        ctx = loader.load("particle_physics")

        assert ctx.component_types
        assert ctx.relation_types
        assert ctx.validation_rules

    def test_synthetic_cartridge_component_and_relation_types(self, synthetic_cartridge_dir):
        from episteme_graph.agents.component_assembly.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader(cartridge_base_dir=str(synthetic_cartridge_dir))
        ctx = loader.load("synthetic")

        assert ctx.component_types == {"component_types": ["TypeA", "TypeB"]}
        assert ctx.relation_types == {"relation_types": ["REL_A"]}


# ---------------------------------------------------------------------------
# サブクラス: component_graph
# ---------------------------------------------------------------------------


class TestComponentGraphCartridgeLoaderSubclass:
    def test_loads_relation_types_without_component_types_field(self):
        """component_graph 固有: relation_types のみ追加し component_types は持たない。"""
        from episteme_graph.agents.component_graph.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader()
        ctx = loader.load("particle_physics")

        assert ctx.relation_types
        assert not hasattr(ctx, "component_types")

    def test_synthetic_cartridge_relation_types(self, synthetic_cartridge_dir):
        from episteme_graph.agents.component_graph.cartridge_loader import CartridgeLoader

        loader = CartridgeLoader(cartridge_base_dir=str(synthetic_cartridge_dir))
        ctx = loader.load("synthetic")

        assert ctx.relation_types == {"relation_types": ["REL_A"]}
