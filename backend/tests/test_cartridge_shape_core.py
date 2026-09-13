"""カートリッジの形の宣言と適合事実の core（概念レジストリ P3-7 / §8）。

固定するのは:

1. ``load_shape`` — 同梱の宣言が読める / 無い分野は ``None`` / 壊れた JSON は ``None``。
2. ``validate_shape`` — ``expects.*`` の語彙外を warning 文で返す（例外にしない）。
3. ``fit_facts`` — 事実文と**名前の列挙**のみ。数値を返さない（KR6）。
4. **語境界付き照合**（P0-2）— ``SM`` が ``cosmological`` に当たらない。
5. **閉世界の正直さ**（KR8）— 「この分野の論文ではない」と断じない。
6. 同梱 ``particle_physics/shape.json`` が語彙表と整合し、宣言が実内容を指す。
7. core の純度（FastAPI / LLM / embedding を import しない）。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import cartridge_shape  # noqa: E402
from core.cartridge_shape import (  # noqa: E402
    FACT_NO_MATERIAL,
    FACT_NO_PLACEMENT,
    FACT_NO_SHAPE,
    FACT_PLACED,
    fit_facts,
    load_shape,
    validate_all_shapes,
    validate_shape,
)
from core.schema import CLAIM_TYPES, COMPONENT_TYPES  # noqa: E402

SHAPE_JSON = BACKEND / "cartridges" / "particle_physics" / "shape.json"
CARTRIDGE_JSON = BACKEND / "cartridges" / "particle_physics" / "cartridge.json"


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeSession:
    """``landscape_placements`` の live 行の有無だけを答える最小フェイク。"""

    def __init__(self, *, placed=False, fail=False):
        self.placed = placed
        self.fail = fail
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        if self.fail:
            raise RuntimeError("db down")
        return _Result((1,) if self.placed else None)


# ---------------------------------------------------------------------------
# 1. load_shape
# ---------------------------------------------------------------------------


class TestLoadShape:
    def test_bundled_shape_is_loaded(self):
        shape = load_shape("particle_physics")
        assert isinstance(shape, dict)
        assert shape["atlas_domain_key"] == "particle_physics"
        assert shape["covers"]

    @pytest.mark.parametrize("key", ["", None, "does_not_exist", "../etc", ".hidden", "a/b"])
    def test_unknown_or_unsafe_keys_return_none(self, key):
        assert load_shape(key) is None

    def test_broken_json_returns_none(self, tmp_path, monkeypatch):
        root = tmp_path / "cartridges"
        (root / "broken").mkdir(parents=True)
        (root / "broken" / "shape.json").write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(cartridge_shape, "_cartridges_root", lambda: root)
        assert load_shape("broken") is None

    def test_non_object_json_returns_none(self, tmp_path, monkeypatch):
        root = tmp_path / "cartridges"
        (root / "list").mkdir(parents=True)
        (root / "list" / "shape.json").write_text("[1, 2]", encoding="utf-8")
        monkeypatch.setattr(cartridge_shape, "_cartridges_root", lambda: root)
        assert load_shape("list") is None


# ---------------------------------------------------------------------------
# 2. validate_shape
# ---------------------------------------------------------------------------


class TestValidateShape:
    def test_bundled_shape_has_no_violations(self):
        assert validate_shape(load_shape("particle_physics")) == []

    def test_all_bundled_shapes_are_clean(self):
        assert validate_all_shapes() == []

    def test_unknown_component_type_is_a_warning_not_an_exception(self):
        violations = validate_shape({"expects": {"component_types": ["NotAType"]}})
        assert len(violations) == 1
        assert "NotAType" in violations[0]

    def test_unknown_claim_type_is_reported(self):
        violations = validate_shape({"expects": {"claim_types": ["not_a_claim_type"]}})
        assert violations and "not_a_claim_type" in violations[0]

    def test_wrong_shapes_are_reported(self):
        assert validate_shape("nope") == ["shape.json は JSON オブジェクトである必要があります。"]
        assert validate_shape({"covers": "flavor"})
        assert validate_shape({"expects": []})

    def test_none_is_silent(self):
        assert validate_shape(None) == []


# ---------------------------------------------------------------------------
# 3〜5. fit_facts
# ---------------------------------------------------------------------------


def _artifacts(**overrides):
    base = {
        "paper_skeleton": {
            "title": "Form factors in semileptonic B decay",
            "central_question": {"text": "How well is Vcb determined?"},
            "logical_blocks": [{"label": "HQET expansion"}],
        }
    }
    base.update(overrides)
    return base


class TestFitFacts:
    def test_no_shape_is_stated_as_a_fact(self):
        result = fit_facts(None, cartridge_id="unknown", document_id="d", artifacts=None)
        assert result["available"] is False
        assert result["facts"] == [FACT_NO_SHAPE]

    def test_no_material_is_stated_as_a_fact(self):
        result = fit_facts(None, cartridge_id="particle_physics", document_id="d", artifacts=None)
        assert result["facts"] == [FACT_NO_MATERIAL]
        assert result["available"] is False

    def test_placement_present(self):
        session = FakeSession(placed=True)
        result = fit_facts(
            session, cartridge_id="particle_physics", document_id="d", artifacts=_artifacts()
        )
        assert FACT_PLACED in result["facts"]
        assert result["available"] is True

    def test_placement_absent_with_the_analysis_reason(self):
        session = FakeSession(placed=False)
        artifacts = _artifacts(
            landscape_placement={
                "unplaced_domains": [
                    {"domain_key": "particle_physics", "reason": "骨格の概念に対応する主題が見当たりません"}
                ]
            }
        )
        result = fit_facts(
            session, cartridge_id="particle_physics", document_id="d", artifacts=artifacts
        )
        assert FACT_NO_PLACEMENT in result["facts"]
        assert any("骨格の概念に対応する主題" in f for f in result["facts"])

    def test_covered_terms_are_listed_by_name(self):
        result = fit_facts(
            None, cartridge_id="particle_physics", document_id="d", artifacts=_artifacts()
        )
        assert "semileptonic B decay" in result["covered_terms"]
        assert "HQET" in result["covered_terms"]
        assert "Vcb" in result["covered_terms"]

    def test_out_of_scope_terms_are_listed(self):
        artifacts = _artifacts(
            paper_skeleton={"title": "Cosmology and large-scale structure of the universe"}
        )
        result = fit_facts(
            None, cartridge_id="particle_physics", document_id="d", artifacts=artifacts
        )
        assert "cosmology" in result["out_of_scope_terms"]
        assert "large-scale structure" in result["out_of_scope_terms"]

    def test_thesis_artifact_is_also_scanned(self):
        artifacts = {
            "thesis_reconstruction": {
                "central_thesis": {"text": "The lattice QCD prediction constrains the form factor."}
            }
        }
        result = fit_facts(
            None, cartridge_id="particle_physics", document_id="d", artifacts=artifacts
        )
        assert "lattice QCD" in result["covered_terms"]

    def test_word_boundary_matching_prevents_the_F7_accident(self):
        """``SM`` を宣言に足しても ``cosmological`` には当たらない（P0-2 / F-7）。"""
        shape = {"covers": ["SM"], "atlas_domain_key": "x"}
        matched = cartridge_shape._matched_terms(
            ["SM"], "cosmological constraints on the cosmos"
        )
        assert matched == []
        assert cartridge_shape._matched_terms(["SM"], "the SM prediction") == ["SM"]
        assert shape  # 宣言の形は変えていない

    def test_no_numbers_anywhere_in_the_result(self):
        """件数・スコア・cosine を返さない（KR6）。"""
        session = FakeSession(placed=True)
        result = fit_facts(
            session, cartridge_id="particle_physics", document_id="d", artifacts=_artifacts()
        )
        blob = json.dumps(result, ensure_ascii=False)
        for forbidden in ("confidence", "score", "weight", "count", "cosine"):
            assert forbidden not in blob
        for fact in result["facts"]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_closed_world_wording(self):
        """分野の外を断じない（KR8）。禁止語を固定文に入れない。"""
        for line in (FACT_PLACED, FACT_NO_PLACEMENT, FACT_NO_MATERIAL, FACT_NO_SHAPE):
            for forbidden in ("ふさわしくない", "適していません", "世界", "誰も", "べきです"):
                assert forbidden not in line
        # 「配置がありません」は分野の地図についての事実であって、論文の否定ではない。
        assert "この論文には" in FACT_NO_PLACEMENT

    def test_placement_lookup_failure_is_fail_soft(self):
        session = FakeSession(fail=True)
        result = fit_facts(
            session, cartridge_id="particle_physics", document_id="d", artifacts=_artifacts()
        )
        # 配置の行は落ちるが、主題語の事実は残る。
        assert FACT_PLACED not in result["facts"]
        assert FACT_NO_PLACEMENT not in result["facts"]
        assert result["covered_terms"]

    def test_listed_terms_are_capped(self):
        assert cartridge_shape._MAX_LISTED_TERMS <= 10


# ---------------------------------------------------------------------------
# 6. 同梱の宣言
# ---------------------------------------------------------------------------


class TestBundledDeclaration:
    def test_shape_json_is_registered_in_the_manifest(self):
        manifest = json.loads(CARTRIDGE_JSON.read_text(encoding="utf-8"))
        assert manifest["files"]["shape"] == "shape.json"
        # cartridge_id は参照が多いので変えない（乖離の解消は宣言側で行う）。
        assert manifest["cartridge_id"] == "particle_physics"

    def test_description_states_the_actual_scope(self):
        """名前と実内容の乖離（F-8）を宣言側で解消していること。"""
        manifest = json.loads(CARTRIDGE_JSON.read_text(encoding="utf-8"))
        assert "フレーバー物理" in manifest["description"]
        assert "flavor_physics" in manifest["target_domain"]

    def test_expects_uses_only_catalog_vocabularies(self):
        shape = json.loads(SHAPE_JSON.read_text(encoding="utf-8"))
        expects = shape["expects"]
        assert set(expects["component_types"]) <= set(COMPONENT_TYPES)
        assert set(expects["claim_types"]) <= set(CLAIM_TYPES)

    def test_entry_types_match_the_registry_vocabulary_when_available(self):
        from core import schema as core_schema

        vocabulary = getattr(core_schema, "LIBRARY_ENTRY_TYPES", None)
        if not vocabulary:
            pytest.skip("LIBRARY_ENTRY_TYPES はまだ core/schema.py に無い（担当 A の範囲）")
        shape = json.loads(SHAPE_JSON.read_text(encoding="utf-8"))
        assert set(shape["expects"]["entry_types"]) <= set(vocabulary)

    def test_does_not_cover_names_other_fields(self):
        shape = json.loads(SHAPE_JSON.read_text(encoding="utf-8"))
        assert "cosmology" in shape["does_not_cover"]


# ---------------------------------------------------------------------------
# 7. core の純度
# ---------------------------------------------------------------------------


class TestCoreGuardrails:
    def _imports(self) -> set[str]:
        source = (BACKEND / "core" / "cartridge_shape.py").read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_does_not_import_fastapi_or_llm(self):
        imported = self._imports()
        assert not any(m.startswith("fastapi") for m in imported)
        assert "core.llm" not in imported
        assert "openai" not in imported

    def test_uses_the_shared_alias_matching_primitive(self):
        """語境界照合を再実装しない（正本は agents/alias_matching.py）。"""
        assert "episteme_graph.agents.alias_matching" in self._imports()

    def test_uses_the_shared_normalize_label(self):
        assert "core.atlas_gaps.schema" in self._imports()

    def test_has_no_write_paths(self):
        source = (BACKEND / "core" / "cartridge_shape.py").read_text(encoding="utf-8")
        for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert statement not in source
