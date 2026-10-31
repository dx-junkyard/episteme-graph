"""第 25 周 面 B 第 3 周の是正（第 3 波）のガードレール。

- IK-0930: 主張参照の解決の正本 ``core/knowledge_objects/claim_refs.py``（UUID はそのまま・生成時の
  局所 ID は ``theory_claims_live`` の (document_id, agent_claim_id) / legacy_ids で UUID へ・無ければ
  ``claim_object_builder`` の本文で名前だけ）。支える主張・観測の選択肢・支持根・表示名・依存グラフの
  種がこれを通る。
- IK-0931: 台帳行の無い対象の GET は、閲覧ゲートと実在の確認を通れば 200（``exists: false``）で
  名前・疑義の入口・``can_edit`` を返す。実在しなければ 404。
- IK-0932: 学習者の地図の札（直接検証・間接的な支持・反証）に「記帳された条件」の 1 行を添える
  （根拠の逐語・記帳者・数値なし）。AI への画面の事実文も地図に合わせる。
- IK-0933: D層の一覧の母集合から claim チェーンの導出ステップを外す（理論モジュール層の判定を通す）。
- IK-0934 / IK-0901: 台帳の見出しは「論文の題名 › 対象の名前」・主グラフのノードは段の日本語名。
- IK-0935: 開幕の「確かめていないこと」は document_id で各論文の区画へ。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "api"))

from fastapi import HTTPException  # noqa: E402

from api.routes import doubt  # noqa: E402

_DOC = "aaaaaaaa-0000-0000-0000-000000000001"
_CLAIM = "dddddddd-0000-0000-0000-000000000004"
_FRONT = _REPO / "frontend" / "public"


class _Result:
    def __init__(self, rows):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Session:
    def __init__(self, handlers=None):
        self.handlers = list(handlers or [])
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        for predicate, rows in self.handlers:
            if predicate(sql, dict(params or {})):
                return _Result(rows)
        return _Result([])

    def rollback(self):
        pass

    def close(self):
        pass


def _live_claims(rows):
    return (lambda sql, p: "FROM theory_claims_live" in sql and "agent_claim_id" in sql, rows)


# ---------------------------------------------------------------------------
# IK-0930: 主張参照の解決器
# ---------------------------------------------------------------------------


class TestClaimRefResolver:
    def _resolver(self, rows, artifact=None):
        from core.knowledge_objects.claim_refs import ClaimRefResolver

        return ClaimRefResolver(_Session([_live_claims(rows)]), _DOC, artifact_claim_texts=artifact)

    def test_uuid_and_agent_id_and_legacy_resolve_to_the_same_uuid(self):
        r = self._resolver([(_CLAIM, "synth_claim_0003", "観測の主張", {"legacy_ids": ["blk_1:span_002"]})])
        assert r.uuid_for(_CLAIM) == _CLAIM
        assert r.uuid_for("synth_claim_0003") == _CLAIM
        assert r.uuid_for("claim:blk_1:span_002") == _CLAIM
        assert r.uuid_for({"claim_id": "blk_1:span_002"}) == _CLAIM
        assert r.label_for("synth_claim_0003") == "観測の主張"

    def test_unknown_agent_id_falls_back_to_artifact_text_without_uuid(self):
        r = self._resolver([], artifact={"claim_span_001_142": "artifact の本文"})
        assert r.uuid_for("claim_span_001_142") == ""
        assert r.label_for("claim_span_001_142") == "artifact の本文"
        assert r.resolve("claim_span_001_142") == {
            "claim_id": "", "label": "artifact の本文", "ref": "claim_span_001_142",
        }

    def test_nothing_known_returns_none_and_never_the_id(self):
        r = self._resolver([], artifact={})
        assert r.resolve("claim_span_9") is None
        assert r.label_for("claim_span_9") == ""

    def test_without_document_nothing_is_read(self):
        from core.knowledge_objects.claim_refs import ClaimRefResolver

        session = _Session()
        assert ClaimRefResolver(session, None, artifact_claim_texts={}).uuid_for("synth_claim_1") == ""
        assert session.calls == []

    def test_module_does_not_import_fastapi_or_llm(self):
        src = (_BACKEND / "core" / "knowledge_objects" / "claim_refs.py").read_text(encoding="utf-8")
        assert "fastapi" not in src
        assert "core.llm" not in src
        # 綴りの正規化は references.py の正本を使う（二重に持たない）。
        assert "from core.knowledge_objects.references import" in src


class TestReadersUseTheResolver:
    def test_dependency_graph_seeds_by_uuid_when_graph_uses_agent_ids(self):
        from core.doubt.dependency import build_dependency_graph, seed_nodes_for_target

        graph_json = {"nodes": [{"component_id": "theory_op_0001", "linked_claim_ids": ["synth_claim_0003"]}]}
        session = _Session([
            (lambda sql, p: "FROM theory_component_graphs" in sql, [(_DOC, graph_json)]),
            _live_claims([(_CLAIM, "synth_claim_0003", "観測の主張", {})]),
        ])
        graph = build_dependency_graph(session, document_ids=[_DOC])
        assert seed_nodes_for_target(graph, "claim", "synth_claim_0003") == {"theory_op_0001"}
        assert seed_nodes_for_target(graph, "claim", _CLAIM) == {"theory_op_0001"}

    def test_observation_targets_merge_graph_ref_and_claim_type_uuid(self):
        from core.doubt.observation_targets import observation_claim_targets

        graph_json = {"nodes": [{
            "component_id": "theory_op_0003", "graph_layer": "main", "label": "Diagnostic / application",
            "linked_claim_ids": ["synth_claim_0003"],
        }]}
        session = _Session([
            (lambda sql, p: "FROM theory_component_graphs" in sql, [(_DOC, graph_json)]),
            (lambda sql, p: "claim_type = ANY" in sql, [(_CLAIM, _DOC)]),
            _live_claims([(_CLAIM, "synth_claim_0003", "観測の主張", {})]),
            (lambda sql, p: "id::text = ANY(:ids)" in sql, [(_CLAIM, "観測の主張")]),
        ])
        items = observation_claim_targets(session, document_ids=[_DOC])
        assert [i["claim_id"] for i in items] == [_CLAIM]
        assert items[0]["identified_via"] == "theory_stage"
        assert items[0]["label"] == "観測の主張"

    def test_target_label_names_agent_claim_ids(self):
        from core.doubt.target_labels import LedgerTargetLabels

        session = _Session([_live_claims([(_CLAIM, "claim_span_001_142", "支持の根の本文", {})])])
        labels = LedgerTargetLabels(session)
        assert labels.label("claim", "claim_span_001_142", document_id=_DOC) == "支持の根の本文"


# ---------------------------------------------------------------------------
# IK-0931 / IK-0934: 行の無い台帳の GET
# ---------------------------------------------------------------------------


class TestLedgerWithoutRow:
    def _get(self, monkeypatch, *, exists, can_edit):
        session = _Session()
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(
            doubt, "_ledger_target_access",
            lambda *a, **k: {"document_id": _DOC, "can_edit": can_edit},
        )
        monkeypatch.setattr(doubt, "_scoped_target_in_document", lambda *a, **k: exists)
        monkeypatch.setattr(doubt, "ledger_target_label", lambda *a, **k: "理論の土台")
        monkeypatch.setattr(doubt, "_document_title_entry", lambda s, d: {"document_title": "Cepheus B の論文"})
        monkeypatch.setattr(
            doubt, "supporting_claims_for_target",
            lambda *a, **k: [{"claim_id": _CLAIM, "label": "主張の本文"}],
        )
        return doubt.get_ledger_entry(
            "component", "theory_op_0001", document_id=_DOC, current_user={"id": "u1"},
        )

    def test_existing_target_without_row_is_200_with_facts(self, monkeypatch):
        out = self._get(monkeypatch, exists=True, can_edit=False)
        assert out["exists"] is False
        assert out["not_recorded_note"] == doubt.LEDGER_NOT_YET_RECORDED_NOTE
        assert out["target_label"] == "理論の土台"
        assert out["document_title"] == "Cepheus B の論文"
        assert out["can_edit"] is False and out["can_edit_note"] == doubt.LEDGER_READ_ONLY_NOTE
        assert out["challenge_allowed"] is False
        assert out["supporting_claims"] == [{"claim_id": _CLAIM, "label": "主張の本文"}]
        # 数値を持たない（負荷・依存数を「0」と言わない）
        assert "load_level" not in out and "consensus_behavioral_dependents" not in out

    def test_missing_target_is_still_404(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._get(monkeypatch, exists=False, can_edit=True)
        assert exc.value.status_code == 404

    def test_document_title_falls_back_without_filename(self, monkeypatch):
        import core.library.structural_matches as sm

        monkeypatch.setattr(sm, "load_display_titles", lambda s, ids: ({_DOC: "arXiv-2508.06322v2"}, {}))
        assert doubt._document_title_entry(_Session(), _DOC) == {
            "document_title": doubt.DOCUMENT_TITLE_UNKNOWN_LABEL,
        }
        monkeypatch.setattr(sm, "load_display_titles", lambda s, ids: ({_DOC: "Magnetic fields in Cepheus B"}, {}))
        assert doubt._document_title_entry(_Session(), _DOC) == {"document_title": "Magnetic fields in Cepheus B"}

    def test_admin_ui_reads_exists_false_and_heading(self):
        js = (_FRONT / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
        assert 'entry.exists === false' in js
        assert "not_recorded_note" in js
        assert "document_title" in js and " › " in js
        assert "challengeEntryHtml(entry, targetType)" in js


# ---------------------------------------------------------------------------
# IK-0932: 地図の札の条件の 1 行
# ---------------------------------------------------------------------------


class TestLearnerScopeLine:
    def test_only_verified_like_statuses_get_a_line(self):
        from core.doubt.schema import LEARNER_SCOPE_LINE_PREFIX, learner_scope_line

        scopes = [{
            "condition": "DCF の等分配の仮定が成り立つ範囲", "domain": "分子雲の偏光観測",
            "precision": "10%", "system": "Cep B", "evidence_quote": "逐語の根拠", "recorded_by": "user-1",
        }]
        line = learner_scope_line("directly_verified", scopes)
        assert line.startswith(LEARNER_SCOPE_LINE_PREFIX)
        assert "DCF の等分配の仮定" in line and "分子雲の偏光観測" in line
        for hidden in ("逐語の根拠", "user-1", "10%", "Cep B"):
            assert hidden not in line
        assert learner_scope_line("untested", scopes) == ""
        assert learner_scope_line("unknown", scopes) == ""
        assert learner_scope_line("indirectly_supported", scopes)
        assert learner_scope_line("refuted", scopes)
        assert learner_scope_line("directly_verified", [{"precision": "1%"}]) == ""

    def test_more_scopes_say_so_without_a_count(self):
        from core.doubt.schema import LEARNER_SCOPE_LINE_MORE, learner_scope_line

        scopes = [{"domain": f"領域{i}"} for i in range(4)]
        line = learner_scope_line("directly_verified", scopes)
        assert line.endswith(LEARNER_SCOPE_LINE_MORE)
        assert not any(ch.isdigit() for ch in LEARNER_SCOPE_LINE_MORE)

    def test_nearby_verification_carries_scope_line(self):
        from core.personal_graph.nearby import _verification
        from core.personal_graph.queries import ComponentLedgerStatuses

        ledger = ComponentLedgerStatuses({"theory_op_0001": "directly_verified", "theory_op_0002": "untested"})
        ledger.scope_lines["theory_op_0001"] = "記録された条件: 領域「分子雲」"
        assert _verification("theory_op_0001", ledger)["scope_line"] == "記録された条件: 領域「分子雲」"
        assert "scope_line" not in _verification("theory_op_0002", ledger)
        # 既存の plain dict の読み手（テスト・旧経路）は壊さない。
        assert _verification("x", {"x": "directly_verified"}) == {
            "status": "directly_verified", "label": "直接検証の記録あり",
        }

    def test_map_renders_scope_line_and_ai_fact_matches(self):
        from core.assistant_context.schema import LEARNING_PERSONAL_MAP_SCREEN_FACT

        js = (_FRONT / "js" / "personal-map-home.js").read_text(encoding="utf-8")
        assert "verification.scope_line" in js
        assert "nearbyScopeLinesHtml(" in js
        # AI に渡す画面の事実は、地図の札の下に条件が出ることを言う（画面と一致させる）。
        assert "札の下" in LEARNING_PERSONAL_MAP_SCREEN_FACT
        assert "出典タブ" not in LEARNING_PERSONAL_MAP_SCREEN_FACT


# ---------------------------------------------------------------------------
# IK-0933: 母集合から claim チェーンの手順を外す
# ---------------------------------------------------------------------------


class TestClaimChainExclusion:
    def test_excludes_only_claim_chain_detail_nodes(self, monkeypatch):
        from core.doubt import population

        graph_json = {"nodes": [
            {"component_id": "theory_op_0001", "graph_layer": "main"},
            {"component_id": "eq_op_0100", "graph_layer": "equation_detail", "linked_derivation_ids": ["deriv_claims"]},
            {"component_id": "eq_op_0200", "graph_layer": "equation_detail", "linked_derivation_ids": ["deriv_eq"]},
        ]}
        artifacts = {"derivation_chain": {"chains": [
            {"derivation_id": "deriv_claims", "chain_type": "claim_chain"},
            {"derivation_id": "deriv_eq", "chain_type": "equation_chain"},
        ]}}
        import core.document_pipeline.persistence as persistence

        monkeypatch.setattr(persistence, "document_run_artifacts", lambda doc, **k: artifacts)
        session = _Session([(lambda sql, p: "FROM theory_component_graphs" in sql, [(graph_json,)])])
        excl = population.ClaimChainExclusion(session)
        assert excl.excludes("component", "eq_op_0100", _DOC) is True
        assert excl.excludes("component", "eq_op_0200", _DOC) is False
        assert excl.excludes("component", "theory_op_0001", _DOC) is False
        assert excl.excludes("claim", "eq_op_0100", _DOC) is False
        assert excl.excluded is True

    def test_fact_line_has_no_numbers_or_internal_words(self):
        from core.doubt.population import CLAIM_CHAIN_EXCLUDED_NOTE

        assert not any(ch.isdigit() for ch in CLAIM_CHAIN_EXCLUDED_NOTE)
        assert "claim" not in CLAIM_CHAIN_EXCLUDED_NOTE

    def test_lists_route_through_the_exclusion(self):
        src = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
        assert src.count("ClaimChainExclusion(session)") >= 3  # 地図・合意リスト（教員・学習者）
        brief = (_BACKEND / "core" / "doubt" / "seminar_brief.py").read_text(encoding="utf-8")
        assert "ClaimChainExclusion(session)" in brief
        oa = (_BACKEND / "core" / "doubt" / "open_assumptions.py").read_text(encoding="utf-8")
        assert "exclusion.excludes(" in oa
        # 判定の正本は理論モジュール層（二重に持たない）。
        pop = (_BACKEND / "core" / "doubt" / "population.py").read_text(encoding="utf-8")
        assert "from core.theory_modules.builder import claim_sequence_node_ids" in pop


# ---------------------------------------------------------------------------
# IK-0901 / IK-0934: 主グラフのノード名
# ---------------------------------------------------------------------------


class TestMainNodeLabel:
    def test_stage_english_name_becomes_japanese(self):
        from core.doubt.seminar_brief import _ReadableLabels

        labels = _ReadableLabels.__new__(_ReadableLabels)
        labels.equation_records = {}
        labels.claim_texts = {"c1": "磁場は乱流で支えられる"}
        assert labels._readable_node_label("Theory basis", operation="", claim_ids=["c1"]) == (
            "理論の土台: 磁場は乱流で支えられる"
        )
        assert labels._readable_node_label("Theory basis", operation="", claim_ids=[]) == "理論の土台"


# ---------------------------------------------------------------------------
# IK-0935: 開幕の前提を論文ごとに
# ---------------------------------------------------------------------------


class TestOpeningAssumptionsByDocument:
    def test_assumption_points_keep_the_ledger_document(self):
        from core.discuss.opening import project_fragile_points

        points, _ = project_fragile_points(
            [{"statement": "等分配の仮定", "document_id": _DOC, "target_type": "assumption"},
             {"statement": "コース全体の前提", "target_type": "assumption"}],
            {},
        )
        docs = [p["document_id"] for p in points if p["kind"] == "assumption"]
        assert docs == [_DOC, None]

    def test_discuss_js_places_assumptions_per_document(self):
        js = (_FRONT / "js" / "discuss.js").read_text(encoding="utf-8")
        assert "renderPaperUnverifiedSection(fragile, \"\", doc.document_id)" in js
        assert "このコースの教材全体で確かめていないこと" in js
