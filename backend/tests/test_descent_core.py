"""構造の降下路（structure_descent_design.md Phase 3）— core/descent のテスト。

対象仕様: docs/features/structure_descent_design.md §2/§4/§5（SD1〜SD6）。
test_return_door_core.py と同型の手法で DB・LLM なしに検証する:

- 純粋合成部（``compose_ladder`` / ``compose_backstage_path``）は fake dict で直接呼ぶ。
- ``build_ladder`` / ``build_backstage_path`` は DB 読み（resolve_element /
  fetch_component_graph / *_records / explanations_for_element）を engine モジュール
  名前空間で monkeypatch し、fake artifacts（dict）だけで全経路を通す。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core.descent import engine  # noqa: E402
from core.descent.engine import (  # noqa: E402
    BACKSTAGE_DECLARATION,
    RECALL_PROMPTS,
    REVEAL_NOTE_EMPTY,
    REVEAL_NOTE_WITH_ITEMS,
    SYMBOLS_MAX,
    build_backstage_path,
    build_ladder,
    compose_backstage_path,
    compose_ladder,
)
from core.descent.resolve import (  # noqa: E402
    SUPPORTED_ELEMENT_TYPES,
    ResolvedElement,
)
from core.element_vocab import (  # noqa: E402
    THEORY_STAGE_DISPLAY_TO_KEY,
    THEORY_STAGE_LABELS,
    definition_missing_fact,
)

# SD5/P7: DTO に出てはいけない生数値キー。
_FORBIDDEN_NUMERIC_KEYS = {"confidence", "load_score", "score", "weight"}


# ===========================================================================
# fake 素材（fake rows / fake artifacts）
# ===========================================================================


def _resolved(element_type="equation", element_id="eq_1", document_id="doc-1",
              match_ids=None):
    return ResolvedElement(
        element_type=element_type,
        element_id=element_id,
        document_id=document_id,
        match_ids=match_ids if match_ids is not None else {element_id},
    )


def _main_node(label="Equation system", linked_equation_ids=("eq_1",),
               description="Linearized field equations."):
    return {
        "id": "node-main-1",
        "graph_layer": "main",
        "label": label,
        "description": description,
        "linked_equation_ids": list(linked_equation_ids),
        "member_component_ids": [],
    }


def _symbol_record(symbol, eq_ids=("eq_1",), status="defined",
                   evidence=("x denotes the comoving position.",),
                   scope="document", variants=("x", "\\vec{x}")):
    return {
        "canonical_symbol": symbol,
        "defining_equation_ids": list(eq_ids),
        "used_in_equation_ids": [],
        "definition_status": status,
        "review_reasons": [],
        "definition_evidence_texts": list(evidence),
        "scope": scope,
        "notation_variants": list(variants),
    }


_LONG_REASON = (
    "Substituting the perturbed metric into the field equations and keeping "
    "only terms linear in h yields the linearized equation of motion, "
    "as stated verbatim in the source derivation chain." * 3
)


def _chains(reason=_LONG_REASON):
    return [{
        "chain_id": "chain-1",
        "steps": [{
            "step_id": "s1",
            "input_equation_ids": ["eq_0"],
            "output_equation_ids": ["eq_1"],
            "operation": "linearize",
            "reason": reason,
        }],
    }]


def _equations():
    return [
        {"equation_id": "eq_0", "label": "(1)"},
        {"equation_id": "eq_1", "label": "(2)"},
    ]


def _patch_engine(monkeypatch, *, resolved, graph_nodes=None, symbols=None,
                  chains=None, equations=None):
    """engine の DB/artifact 読みを fake に差し替える（core は非改変のまま）。"""
    monkeypatch.setattr(engine, "resolve_element", lambda et, eid, cd: resolved)
    monkeypatch.setattr(engine, "document_run_artifacts", lambda did: {})
    monkeypatch.setattr(
        engine, "fetch_component_graph", lambda did: {"nodes": graph_nodes or []}
    )
    monkeypatch.setattr(
        engine, "symbol_records", lambda did, artifacts=None: symbols or []
    )
    monkeypatch.setattr(
        engine, "derivation_records", lambda did, artifacts=None: chains or []
    )
    monkeypatch.setattr(
        engine, "equation_records", lambda did, artifacts=None: equations or []
    )
    # ラベルラダー本体は labels.py のテスト対象。ここでは決定論の索引だけ再現する。
    monkeypatch.setattr(
        engine, "equation_label",
        lambda record: SimpleNamespace(text=str((record or {}).get("label") or "")),
    )


def _full_ladder(monkeypatch):
    _patch_engine(
        monkeypatch,
        resolved=_resolved(),
        graph_nodes=[_main_node()],
        symbols=[_symbol_record("x")],
        chains=_chains(),
        equations=_equations(),
    )
    return build_ladder({"title": "c"}, "course-1", "equation", "eq_1")


def _all_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _all_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _all_keys(value)


# ===========================================================================
# 1. 梯子の4段構成と順序（recall_prompt → stage_fact → symbols → reveal）
# ===========================================================================


class TestLadderComposition:
    def test_four_rungs_in_fixed_order(self, monkeypatch):
        ladder = _full_ladder(monkeypatch)
        assert ladder["available"] is True
        assert [r["kind"] for r in ladder["rungs"]] == [
            "recall_prompt", "stage_fact", "symbols", "reveal",
        ]

    def test_ladder_is_deterministic(self, monkeypatch):
        """SD2: 同じ素材からは同じ梯子（読み時決定論）。"""
        assert _full_ladder(monkeypatch) == _full_ladder(monkeypatch)

    def test_compose_ladder_pure_function_order(self):
        """合成部単体でも順序固定（stage → symbols → reveal の挿入順）。"""
        reveal = {"kind": "reveal", "items": [], "note": REVEAL_NOTE_EMPTY}
        ladder = compose_ladder(
            "claim",
            stage_label="消去",
            stage_description="",
            symbol_items=[{"symbol": "H"}],
            reveal=reveal,
        )
        assert [r["kind"] for r in ladder["rungs"]] == [
            "recall_prompt", "stage_fact", "symbols", "reveal",
        ]

    def test_stage_label_vocabulary_is_element_vocab_canonical(self, monkeypatch):
        """§5: stage 語彙が正本（element_vocab.THEORY_STAGE_LABELS）由来。"""
        ladder = _full_ladder(monkeypatch)
        stage = next(r for r in ladder["rungs"] if r["kind"] == "stage_fact")
        assert stage["stage_label"] == THEORY_STAGE_LABELS["equation_system"]
        assert stage["stage_label"] == "式の体系"
        assert "「式の体系」" in stage["text"]


# ===========================================================================
# 2. 想起プロンプト（問いの形・stage 名/答えを含まない — SD3 / pretesting 修正）
# ===========================================================================


class TestRecallPrompt:
    def test_recall_prompt_exists_for_every_supported_type(self):
        assert set(RECALL_PROMPTS) == set(SUPPORTED_ELEMENT_TYPES)

    def test_first_rung_is_the_fixed_recall_prompt(self, monkeypatch):
        ladder = _full_ladder(monkeypatch)
        first = ladder["rungs"][0]
        assert first == {"kind": "recall_prompt", "text": RECALL_PROMPTS["equation"]}

    @pytest.mark.parametrize("element_type", SUPPORTED_ELEMENT_TYPES)
    def test_prompt_is_invitation_in_own_words(self, element_type):
        prompt = RECALL_PROMPTS[element_type]
        assert "自分の語で" in prompt
        assert prompt.endswith("ください")

    @pytest.mark.parametrize("element_type", SUPPORTED_ELEMENT_TYPES)
    def test_prompt_does_not_leak_stage_names(self, element_type):
        """答え（stage 名）を先に出さない — 訳語・英語表示名のどちらも含めない。"""
        prompt = RECALL_PROMPTS[element_type]
        for label in THEORY_STAGE_LABELS.values():
            assert label not in prompt, f"stage 訳語 {label!r} がプロンプトに漏れている"
        for display in THEORY_STAGE_DISPLAY_TO_KEY:
            assert display not in prompt.lower(), (
                f"stage 英語表示名 {display!r} がプロンプトに漏れている"
            )


# ===========================================================================
# 3. stage が引けないとき stage_fact rung を出さない（推測穴埋め禁止）
# ===========================================================================


class TestStageFactAbsence:
    def test_no_stage_rung_when_node_label_is_not_a_stage(self, monkeypatch):
        """一致ノードはあるが label が stage 語彙でない（equation-id label 等）
        → 推測で穴埋めせず stage_fact rung 自体を出さない。"""
        _patch_engine(
            monkeypatch,
            resolved=_resolved(),
            graph_nodes=[_main_node(label="Define eq_2_7")],
            chains=_chains(),
            equations=_equations(),
        )
        ladder = build_ladder({}, "course-1", "equation", "eq_1")
        kinds = [r["kind"] for r in ladder["rungs"]]
        assert "stage_fact" not in kinds
        assert kinds[0] == "recall_prompt"
        assert kinds[-1] == "reveal"

    def test_no_stage_rung_when_graph_is_empty(self, monkeypatch):
        _patch_engine(monkeypatch, resolved=_resolved())
        ladder = build_ladder({}, "course-1", "equation", "eq_1")
        assert "stage_fact" not in [r["kind"] for r in ladder["rungs"]]


# ===========================================================================
# 4. 記号段（上限8・定義無し記号の missing_fact — §6 精査記録①）
# ===========================================================================


class TestSymbolsRung:
    def _symbols_rung(self, monkeypatch, symbols):
        _patch_engine(
            monkeypatch,
            resolved=_resolved(),
            graph_nodes=[_main_node()],
            symbols=symbols,
            chains=_chains(),
            equations=_equations(),
        )
        ladder = build_ladder({}, "course-1", "equation", "eq_1")
        return next((r for r in ladder["rungs"] if r["kind"] == "symbols"), None)

    def test_symbols_capped_at_eight(self, monkeypatch):
        assert SYMBOLS_MAX == 8
        symbols = [_symbol_record(f"sym_{i}") for i in range(12)]
        rung = self._symbols_rung(monkeypatch, symbols)
        assert rung is not None
        assert len(rung["items"]) == SYMBOLS_MAX

    def test_missing_definition_symbol_gets_verbatim_missing_fact(self, monkeypatch):
        record = _symbol_record("H", status="definition_missing", evidence=())
        rung = self._symbols_rung(monkeypatch, [record])
        item = rung["items"][0]
        assert item["missing_fact"] == definition_missing_fact()
        assert item["missing_fact"] == "論文中に明示的な定義が見つかりません"
        assert item["meaning"] == ""
        assert item["definition_status_label"] == "定義なし"

    def test_missing_via_review_reasons_also_flagged(self, monkeypatch):
        record = _symbol_record("G", status="used", evidence=())
        record["review_reasons"] = ["definition_missing"]
        rung = self._symbols_rung(monkeypatch, [record])
        assert rung["items"][0]["missing_fact"] == definition_missing_fact()

    def test_defined_symbol_meaning_is_verbatim_evidence(self, monkeypatch):
        evidence = "x denotes the comoving position of the detector."
        rung = self._symbols_rung(monkeypatch, [_symbol_record("x", evidence=(evidence,))])
        item = rung["items"][0]
        assert item["meaning"] == evidence
        assert "missing_fact" not in item
        assert item["scope_label"] == "論文全体"  # SYMBOL_SCOPE_LABELS 正本由来

    def test_no_symbols_rung_when_no_symbol_touches_the_equations(self, monkeypatch):
        symbols = [_symbol_record("y", eq_ids=("eq_other",))]
        assert self._symbols_rung(monkeypatch, symbols) is None


# ===========================================================================
# 5. 出典リビール（reason 逐語・素材ゼロは items 空 + note）
# ===========================================================================


class TestRevealRung:
    def test_reason_is_verbatim_not_reworked(self, monkeypatch):
        ladder = _full_ladder(monkeypatch)
        reveal = ladder["rungs"][-1]
        assert reveal["kind"] == "reveal"
        item = reveal["items"][0]
        assert item["reason"] == _LONG_REASON  # 加工・切り詰めなし
        assert "…" not in item["reason"]
        assert item["operation_label"] == "線形化"  # OPERATION_LABELS 正本由来
        assert item["input_labels"] == ["(1)"]
        assert item["output_labels"] == ["(2)"]
        assert reveal["note"] == REVEAL_NOTE_WITH_ITEMS

    def test_zero_material_reveal_has_empty_items_and_honest_note(self, monkeypatch):
        """素材ゼロでも reveal rung 自体は返し、note で正直に伝える（空は沈黙ではない）。"""
        _patch_engine(monkeypatch, resolved=_resolved())
        ladder = build_ladder({}, "course-1", "equation", "eq_1")
        reveal = ladder["rungs"][-1]
        assert reveal["kind"] == "reveal"
        assert reveal["items"] == []
        assert reveal["note"] == REVEAL_NOTE_EMPTY

    def test_unrelated_steps_are_not_included(self, monkeypatch):
        chains = [{"steps": [{
            "input_equation_ids": ["eq_x"],
            "output_equation_ids": ["eq_y"],
            "operation": "derive",
            "reason": "unrelated step",
        }]}]
        _patch_engine(
            monkeypatch, resolved=_resolved(), chains=chains, equations=_equations()
        )
        ladder = build_ladder({}, "course-1", "equation", "eq_1")
        assert ladder["rungs"][-1]["items"] == []


# ===========================================================================
# 6. 全段不成立（要素が解決できない）→ available: false（fail-closed）
# ===========================================================================


class TestAvailability:
    def test_unresolved_element_returns_available_false_only(self, monkeypatch):
        monkeypatch.setattr(engine, "resolve_element", lambda et, eid, cd: None)
        assert build_ladder({}, "course-1", "equation", "eq_x") == {"available": False}

    def test_resolved_but_material_less_ladder_is_still_available(self, monkeypatch):
        """解決済みで素材ゼロなら骨格（想起 + 空リビール）で成立（隠さない）。"""
        _patch_engine(monkeypatch, resolved=_resolved())
        ladder = build_ladder({}, "course-1", "equation", "eq_1")
        assert ladder["available"] is True
        assert [r["kind"] for r in ladder["rungs"]] == ["recall_prompt", "reveal"]


# ===========================================================================
# 7. 生数値キー非漏洩（confidence / load_score 等 — P7/UC9 同系）
# ===========================================================================


class TestNoNumericKeysInDto:
    def test_full_ladder_has_no_numeric_keys(self, monkeypatch):
        ladder = _full_ladder(monkeypatch)
        leaked = set(_all_keys(ladder)) & _FORBIDDEN_NUMERIC_KEYS
        assert leaked == set(), f"梯子 DTO に生数値キーが漏れている: {leaked}"

    def test_backstage_path_has_no_numeric_keys(self):
        path = compose_backstage_path(
            [{"id": "np1", "pattern": "δ", "concept_type": "perturbation"}],
            [{"symbol": "x", "meaning": "m", "scope_label": "論文全体",
              "variants": [], "definition_status_label": "この論文で定義"}],
            [{"body": "generic explanation"}],
        )
        leaked = set(_all_keys(path)) & _FORBIDDEN_NUMERIC_KEYS
        assert leaked == set(), f"降下路 DTO に生数値キーが漏れている: {leaked}"


# ===========================================================================
# 8. 楽屋の降下路（宣言の逐語・素材の無い step を並べない — SD4/SD6）
# ===========================================================================


class TestBackstagePath:
    def test_declaration_is_verbatim(self):
        assert BACKSTAGE_DECLARATION == (
            "ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります"
        )

    def test_empty_materials_produce_no_steps(self):
        assert compose_backstage_path([], [], []) == {
            "declaration": BACKSTAGE_DECLARATION,
            "steps": [],
        }

    def test_only_present_materials_become_steps(self):
        notation = [{"id": "np1", "pattern": "δ", "concept_type": "perturbation"}]
        path = compose_backstage_path(notation, [], [])
        assert [s["kind"] for s in path["steps"]] == ["notation_patterns"]

    def test_step_order_is_notation_then_symbols_then_generic(self):
        path = compose_backstage_path(
            [{"id": "np1", "pattern": "δ", "concept_type": "x"}],
            [{"symbol": "x"}],
            [{"body": "generic"}],
        )
        assert [s["kind"] for s in path["steps"]] == [
            "notation_patterns", "symbol_definitions", "generic_explanations",
        ]

    def test_notation_step_absent_without_explicit_cartridge(self, monkeypatch):
        """cartridge_id 未設定コースでは load_cartridge を呼ばず規約差の段を出さない。

        load_cartridge(None) は既定カートリッジ（particle_physics）へ黙って縮退するため、
        明示 cartridge のみ規約差の段を出す（G層 Phase 0 の DEFAULT_CARTRIDGE 撤去と
        同じ原則。2026-08-15 レビュー是正）。
        """
        def _must_not_be_called(*args, **kwargs):
            raise AssertionError(
                "cartridge_id 無しの course_data で load_cartridge が呼ばれた"
                "（既定カートリッジへの黙った縮退）"
            )

        monkeypatch.setattr(engine, "load_cartridge", _must_not_be_called)
        monkeypatch.setattr(engine, "resolve_element", lambda et, eid, cd: None)
        for course_data in ({}, {"title": "c"}, {"cartridge_id": "  "}, None):
            path = build_backstage_path(course_data, "course-1", "equation", "eq_1")
            assert path["declaration"] == BACKSTAGE_DECLARATION
            assert path["steps"] == []

    def test_notation_step_uses_explicit_cartridge_id_only(self, monkeypatch):
        """明示 cartridge_id のときだけ、その id で load_cartridge が呼ばれ段が出る。"""
        calls: list[str] = []

        def _fake_load(cartridge_id):
            calls.append(cartridge_id)
            return SimpleNamespace(
                ontology=SimpleNamespace(
                    notation_patterns=[
                        {"id": "np1", "pattern": "δ", "concept_type": "perturbation"},
                    ]
                )
            )

        monkeypatch.setattr(engine, "load_cartridge", _fake_load)
        monkeypatch.setattr(engine, "resolve_element", lambda et, eid, cd: None)
        path = build_backstage_path(
            {"cartridge_id": "particle_physics"}, "course-1", "equation", "eq_1"
        )
        assert calls == ["particle_physics"]
        assert [s["kind"] for s in path["steps"]] == ["notation_patterns"]
        assert path["steps"][0]["items"] == [
            {"id": "np1", "pattern": "δ", "concept_type": "perturbation"},
        ]

    def test_declaration_returned_even_when_element_unresolved(self, monkeypatch):
        """規約差の段は cartridge 由来のため、要素が解決できなくても宣言 + 規約差は出る。"""
        monkeypatch.setattr(engine, "resolve_element", lambda et, eid, cd: None)
        monkeypatch.setattr(
            engine, "_notation_pattern_items",
            lambda cd: [{"id": "np1", "pattern": "δ", "concept_type": "x"}],
        )
        path = build_backstage_path({}, "course-1", "equation", "eq_x")
        assert path["declaration"] == BACKSTAGE_DECLARATION
        assert [s["kind"] for s in path["steps"]] == ["notation_patterns"]

    def test_generic_explanations_are_approved_generic_role_less_only(self, monkeypatch):
        """candidate / contextual / role 付き（discussion_seed 等）/ 空 body を学習者に出さない。"""
        _patch_engine(monkeypatch, resolved=_resolved())
        monkeypatch.setattr(engine, "_notation_pattern_items", lambda cd: [])
        monkeypatch.setattr(
            engine, "explanations_for_element",
            lambda ref: [
                {"status": "approved", "kind": "generic", "role": None,
                 "body": "承認済みの一般説明"},
                {"status": "candidate", "kind": "generic", "role": None,
                 "body": "候補の説明"},
                {"status": "approved", "kind": "contextual", "role": None,
                 "body": "文脈説明"},
                {"status": "approved", "kind": "generic", "role": "discussion_seed",
                 "body": "議論のきっかけ"},
                {"status": "approved", "kind": "generic", "role": None, "body": "   "},
            ],
        )
        path = build_backstage_path({}, "course-1", "equation", "eq_1")
        generic_steps = [s for s in path["steps"] if s["kind"] == "generic_explanations"]
        assert len(generic_steps) == 1
        assert generic_steps[0]["items"] == [{"body": "承認済みの一般説明"}]
        blob = str(path)
        for hidden in ("候補の説明", "文脈説明", "議論のきっかけ"):
            assert hidden not in blob, f"非承認/対象外の説明 {hidden!r} が漏れている"
