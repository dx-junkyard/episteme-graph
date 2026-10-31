"""第 25 周 面 C（分野の地図の運用）第 2 周の是正（IK-0884〜IK-0889 + IK-0846 / 0848 / 0851 の残り）。

- IK-0884: 導出から外れた「採用済み・未反映」の判断行を一覧の別枠で返す（ゲートは緩めない）。
- IK-0885: 凍結の可否は 1 関数（freeze と freeze-impact が同じものを読む）。関係の増減を列挙する。
- IK-0886: 下書きに入った辺の候補を状態として返す・向きを文で言う。
- IK-0887: 辺候補は共起 → 近さ順・教員の一覧にも上限と畳み（件数は出さない）・根拠の一文。
- IK-0888: 学習者の地図の「記帳なし」の文が層（台帳）を名乗る。
- IK-0889: ベクトル索引の区画は用途と要否の一文・日時の表示語。
- IK-0846 残り: 論文の海の「縁」も論文の同一性キーで畳む。
- IK-0848 残り: 判断の主語の表示名を鉤括弧で区切る。
- IK-0851 残り: 候補ゼロの理由を言い分ける。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas as atlas_module  # noqa: E402
from core import atlas_lifecycle  # noqa: E402
from core import atlas_review_display as display  # noqa: E402
from core import corpus_view  # noqa: E402
from core import label_vocab  # noqa: E402
from core.atlas_edges import derive  # noqa: E402
from core.atlas_edges import schema as edge_schema  # noqa: E402
from core.atlas_gaps import schema as gap_schema  # noqa: E402
from core.atlas_gaps import store as gap_store  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

PDF_DOC = "aaaaaaaa-0000-0000-0000-000000000001"
TEX_DOC = "bbbbbbbb-0000-0000-0000-000000000002"
OTHER_DOC = "cccccccc-0000-0000-0000-000000000003"
ACTOR = "11111111-1111-1111-1111-111111111111"


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, handlers):
        self.handlers = list(handlers)

    def execute(self, statement, params=None):
        sql = str(statement)
        for needle, rows in self.handlers:
            if needle in sql:
                return FakeResult(rows)
        return FakeResult([])

    def rollback(self):
        pass


def _signal_row(signal_id, document_id, title, *, label="Interstellar magnetic field",
                filename="", source_url=""):
    return (
        signal_id, document_id, None, "astro", "v1", "concept", "r1",
        label, gap_schema.normalize_label(label),
        "reason " + signal_id, "quote " + signal_id, 0.8, "active",
        "2026-10-22T00:00:0" + signal_id[-1], "2026-10-22T00:00:00",
        title, source_url, filename,
    )


def _decision_row(cluster_key, *, status="accepted", draft_node_id="", applied_version=""):
    # _DECISION_COLUMNS_SQL の列順
    return (
        "d-1", cluster_key, status, "", "", draft_node_id, applied_version,
        ACTOR, "2026-10-22T12:00:00+00:00", "2026-10-22T12:00:00+00:00",
        "2026-10-22T12:00:00+00:00",
    )


CLUSTER = gap_schema.build_cluster_key("astro", "r1", "Interstellar magnetic field")


# ---------------------------------------------------------------------------
# IK-0851 残り: 候補ゼロの理由
# ---------------------------------------------------------------------------


class TestEmptyReasons:
    def test_no_signals_says_so(self):
        report: dict = {}
        assert gap_store.derive_candidates(FakeSession([]), domain_key="astro", report=report) == []
        assert display.empty_reason_notes(report) == [display.EMPTY_NO_SIGNALS]

    def test_folded_duplicates_are_named_as_the_reason(self):
        session = FakeSession(
            [
                (
                    "FROM landscape_gap_signals",
                    [
                        _signal_row("s1", PDF_DOC, "2605.26810v1", filename="2605.26810v1.pdf"),
                        _signal_row("s2", TEX_DOC, "Cepheus B",
                                    source_url="https://arxiv.org/abs/2605.26810"),
                    ],
                )
            ]
        )
        report: dict = {}
        assert gap_store.derive_candidates(session, domain_key="astro", report=report) == []
        assert report["below_threshold"] is True
        assert report["folded_duplicates"] is True
        assert report["cluster_states"] == {CLUSTER: "below_threshold"}
        notes = display.empty_reason_notes(report)
        assert notes == [display.EMPTY_BELOW_THRESHOLD, display.EMPTY_FOLDED_DUPLICATES]
        # 件数は書かない（閾値の「2 本」は制度の定数）。
        for note in notes:
            assert "件" not in note

    def test_a_single_paper_is_below_threshold_without_folding(self):
        session = FakeSession(
            [("FROM landscape_gap_signals", [_signal_row("s1", OTHER_DOC, "Other")])]
        )
        report: dict = {}
        gap_store.derive_candidates(session, domain_key="astro", report=report)
        assert display.empty_reason_notes(report) == [display.EMPTY_BELOW_THRESHOLD]

    def test_report_is_optional(self):
        assert gap_store.derive_candidates(FakeSession([]), domain_key="astro") == []


# ---------------------------------------------------------------------------
# IK-0884 導出から外れた判断
# ---------------------------------------------------------------------------


class TestOrphanedDecisions:
    def _session(self, signals, decisions):
        return FakeSession(
            [
                ("FROM atlas_gap_decisions", decisions),
                ("FROM landscape_gap_signals", signals),
            ]
        )

    def test_accepted_decision_outside_the_derivation_is_returned_with_a_reason(self):
        session = self._session(
            [_signal_row("s1", PDF_DOC, "2605.26810v1")],
            [_decision_row(CLUSTER)],
        )
        out = gap_store.list_orphaned_decisions(
            session,
            domain_key="astro",
            derived_cluster_keys=[],
            cluster_states={CLUSTER: "below_threshold"},
            draft_node_ids=[],
        )
        assert len(out) == 1
        item = out[0]
        assert item["cluster_key"] == CLUSTER
        assert item["proposed_label"] == "Interstellar magnetic field"
        assert item["reason"] == gap_store.ORPHAN_REASON_BELOW_THRESHOLD
        assert item["in_draft"] is False

        projected = display.project_orphaned_decisions(FakeSession([]), out)
        assert projected[0]["reason_fact"] == display.ORPHANED_REASON_FACTS["below_threshold"]
        assert "decided_by" not in projected[0]["decision"]
        assert projected[0]["decision"]["decision_line"].startswith(display.DECISION_ACTOR_UNKNOWN)

    def test_derived_decisions_are_not_duplicated_in_the_orphan_list(self):
        session = self._session([], [_decision_row(CLUSTER)])
        assert gap_store.list_orphaned_decisions(
            session, domain_key="astro", derived_cluster_keys=[CLUSTER]
        ) == []

    def test_missing_signals_and_draft_state_are_reported(self):
        session = self._session([], [_decision_row(CLUSTER, draft_node_id="n9")])
        out = gap_store.list_orphaned_decisions(
            session, domain_key="astro", derived_cluster_keys=[], draft_node_ids=["n9"]
        )
        assert out[0]["reason"] == gap_store.ORPHAN_REASON_NO_SIGNALS
        assert out[0]["in_draft"] is True
        projected = display.project_orphaned_decisions(FakeSession([]), out)
        assert projected[0]["in_draft_note"] == display.ORPHANED_IN_DRAFT

    def test_the_freeze_gate_is_not_loosened(self):
        """ゲートは判断行を読むまま（候補一覧から消えても公開を止める）。"""
        session = self._session([], [_decision_row(CLUSTER)])
        pending = gap_store.list_pending_for_freeze(
            session, domain_key="astro", draft_node_ids=[]
        )
        assert [p["cluster_key"] for p in pending] == [CLUSTER]


# ---------------------------------------------------------------------------
# IK-0885 凍結の可否 / 関係の増減
# ---------------------------------------------------------------------------


def _skeleton(edges=()):
    return atlas_module.AtlasSkeleton(
        cartridge="astro",
        status="frozen",
        version="v1",
        regions=(
            atlas_module.SkeletonRegion(
                id="r1", label="降着・ジェット",
                concepts=(atlas_module.SkeletonConcept(id="a", label="降着円盤"),),
            ),
            atlas_module.SkeletonRegion(
                id="r2", label="素粒子・場",
                concepts=(atlas_module.SkeletonConcept(id="c", label="磁場"),),
            ),
        ),
        edges=tuple(edges),
    )


class TestFreezeReadiness:
    def test_pending_blocks_and_lists_labels(self):
        out = atlas_lifecycle.freeze_readiness(["星間磁場"], ["降着円盤 — 磁場"])
        assert out["ready"] is False
        assert out["pending_labels"] == ["星間磁場"]
        assert out["pending_edges"] == ["降着円盤 — 磁場"]
        assert any("星間磁場" in f for f in out["facts"])
        assert atlas_lifecycle.FACT_READY not in out["facts"]

    def test_nothing_pending_is_said_positively(self):
        out = atlas_lifecycle.freeze_readiness([], [])
        assert out == {
            "ready": True,
            "pending_labels": [],
            "pending_edges": [],
            "facts": [atlas_lifecycle.FACT_READY],
        }

    def test_added_edge_is_listed_and_same_set_is_not_claimed(self):
        frozen = _skeleton()
        draft = _skeleton([atlas_module.SkeletonEdge(from_id="a", to_id="c", kind="depends")])
        changes = atlas_lifecycle.edge_changes(draft, frozen)
        # IK-1072（第 9 波）: 有向の関係は向きの句を添える（反映の確認と同じ正本）。
        assert changes == {
            "added_edges": ["降着円盤 — 磁場（依存: 「降着円盤」が「磁場」に依存する）"],
            "removed_edges": [],
            "redirected_edges": [],
        }
        facts = atlas_lifecycle._freeze_impact_facts(
            set(), set(), has_frozen=True, added_edges=changes["added_edges"]
        )
        assert atlas_lifecycle.FACT_SAME_NODE_SET not in facts
        assert atlas_lifecycle.FACT_SAME_NODES_EDGES_CHANGED in facts
        assert "次版で加わる関係: 降着円盤 — 磁場（依存: 「降着円盤」が「磁場」に依存する）" in facts

    def test_same_nodes_and_edges_says_same(self):
        frozen = _skeleton([atlas_module.SkeletonEdge(from_id="a", to_id="c", kind="related")])
        changes = atlas_lifecycle.edge_changes(frozen, frozen)
        assert changes == {"added_edges": [], "removed_edges": [], "redirected_edges": []}
        facts = atlas_lifecycle._freeze_impact_facts(set(), set(), has_frozen=True)
        assert atlas_lifecycle.FACT_SAME_NODE_SET in facts

    def test_freeze_and_impact_read_the_same_function(self):
        src = (BACKEND / "api" / "routes" / "atlas.py").read_text(encoding="utf-8")
        assert "_freeze_readiness(" in extract_function_source(src, "freeze_atlas_skeleton")
        assert "_freeze_readiness(" in extract_function_source(src, "get_atlas_freeze_impact")
        helper = extract_function_source(src, "_freeze_readiness")
        assert "atlas_lifecycle.freeze_readiness(" in helper


# ---------------------------------------------------------------------------
# IK-0886 / IK-0887 辺候補
# ---------------------------------------------------------------------------


def _cand(left, right, origins):
    return {
        "edge_key": edge_schema.build_edge_key("astro", left, right),
        "from_id": left,
        "to_id": right,
        "origins": list(origins),
    }


class TestEdgeReviewCap:
    def test_per_node_cap_folds_vector_only_candidates(self):
        items = [_cand("a", x, ["vector"]) for x in ("b", "c", "d", "e")]
        shown, folded = derive.cap_for_review(items, max_per_node=2, max_total=30)
        assert [c["to_id"] for c in shown] == ["b", "c"]
        assert folded is True

    def test_co_occurrence_and_decided_are_never_folded(self):
        items = [_cand("a", x, ["vector"]) for x in ("b", "c")]
        items.append(_cand("a", "d", ["co_occurrence"]))
        decided = _cand("a", "e", ["vector"])
        items.append(decided)
        shown, folded = derive.cap_for_review(
            items, keep_keys=[decided["edge_key"]], max_per_node=1, max_total=30
        )
        keys = [c["to_id"] for c in shown]
        assert "d" in keys and "e" in keys
        assert folded is True

    def test_nothing_folded_when_under_the_cap(self):
        shown, folded = derive.cap_for_review([_cand("a", "b", ["vector"])])
        assert folded is False and len(shown) == 1

    def test_basis_line_names_what_is_near(self):
        assert display.edge_basis_line(["vector"]) == display.EDGE_BASIS_VECTOR
        assert display.edge_basis_line(["co_occurrence"]) == display.EDGE_BASIS_CO_OCCURRENCE
        assert display.edge_basis_line(["co_occurrence", "vector"]) == display.EDGE_BASIS_BOTH
        assert "論文の配置の根拠はありません" in display.EDGE_BASIS_VECTOR

    def test_direction_line(self):
        assert display.edge_direction_line("depends", "A", "B") == "「A」が「B」に依存する関係として記載されます。"
        assert "向きはありません" in display.edge_direction_line("adjacent", "A", "B")
        assert display.edge_direction_line("unknown", "A", "B") == ""

    def test_folded_note_has_no_count(self):
        assert not any(ch.isdigit() for ch in display.EDGE_FOLDED_NOTE)

    def test_route_marks_in_draft_and_folds(self):
        src = (BACKEND / "api" / "routes" / "atlas_edges.py").read_text(encoding="utf-8")
        listing = extract_function_source(src, "list_atlas_edge_candidates")
        assert "edge_derive.cap_for_review(" in listing
        assert "show_all" in listing
        assert "orphaned_decisions" in listing
        annotate = extract_function_source(src, "_annotate_candidate")
        assert '"in_draft"' in annotate and "EDGE_IN_DRAFT" in annotate


# ---------------------------------------------------------------------------
# IK-0888 台帳の文が層を名乗る
# ---------------------------------------------------------------------------


def test_unrecorded_note_names_the_ledger_layer():
    note = label_vocab.ATLAS_PILL_NOTES["unrecorded"]
    assert "台帳" in note
    assert "論文からの記帳" not in note
    assert "論文の位置" in note


# ---------------------------------------------------------------------------
# IK-0889 ベクトル索引の区画
# ---------------------------------------------------------------------------


class TestVectorStatusFacts:
    def test_current(self):
        facts = display.vector_status_facts(version="2026.1", total_nodes=3, embedded_nodes=3, stale=False)
        assert facts[0] == display.VECTOR_INDEX_PURPOSE
        assert "作り直す必要はありません" in facts[1]

    def test_partial_none_stale(self):
        assert "索引の無い項目" in display.vector_status_facts(
            version="v", total_nodes=3, embedded_nodes=1, stale=False)[1]
        assert "まだありません" in display.vector_status_facts(
            version="v", total_nodes=3, embedded_nodes=0, stale=False)[1]
        assert display.vector_status_facts(
            version="v", total_nodes=3, embedded_nodes=0, stale=True)[1] == display.VECTOR_INDEX_STALE

    def test_datetime_and_refresh(self):
        assert display.display_datetime("2026-10-22T21:56:52.616957+00:00") == "2026-10-22 21:56（UTC）"
        assert display.display_datetime("") == ""
        assert display.vector_refresh_fact({"status": "completed", "embedded": 0}) == display.VECTOR_REFRESH_UNCHANGED
        assert display.vector_refresh_fact({"status": "completed", "embedded": 2}) == display.VECTOR_REFRESH_UPDATED
        assert display.vector_refresh_fact({"status": "skipped"}) == ""


# ---------------------------------------------------------------------------
# IK-0846 残り: 縁を論文の同一性キーで畳む / IK-0848 残り: 主語の区切り
# ---------------------------------------------------------------------------


def test_fringe_folds_duplicate_imports_to_the_representative_title():
    rows = [("r1", "2606.02318v1", PDF_DOC), ("r1", "Neutron Star EoS", TEX_DOC)]
    out = corpus_view._build_fringe(
        rows,
        {"r1": "R1"},
        representatives={PDF_DOC: TEX_DOC, TEX_DOC: TEX_DOC},
        rep_titles={TEX_DOC: "Neutron Star EoS"},
    )
    assert out[0]["paper_titles"] == ["Neutron Star EoS"]


def test_fringe_accepts_legacy_two_column_rows():
    out = corpus_view._build_fringe([("r1", "A"), ("r1", "A")], {"r1": "R1"})
    assert out[0]["paper_titles"] == ["A"]


def test_decision_subject_is_delimited_only_for_names():
    assert display.decision_line(
        {"status": "accepted", "decided_at": "2026-10-22"}, "uxsim_te_02"
    ) == "「uxsim_te_02」が 2026-10-22 に採用しました"
    assert display.decision_line(
        {"status": "accepted", "decided_at": "2026-10-22"}, display.DECISION_ACTOR_DELETED
    ).startswith(display.DECISION_ACTOR_DELETED + "が")


# ---------------------------------------------------------------------------
# 管理画面の描画（静的検査）
# ---------------------------------------------------------------------------


def _admin_js() -> str:
    return (BACKEND.parent / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")


class TestAdminUi:
    def test_freeze_is_blocked_before_the_checklist(self):
        js = _admin_js()
        assert "readiness.ready === false" in js
        assert 'data-ui-anchor="atlas.freeze-blocked-goto"' in js

    def test_orphan_sections_and_empty_reasons(self):
        js = _admin_js()
        assert 'data-ui-anchor="atlas.gap-orphaned"' in js
        assert 'data-ui-anchor="atlas.edge-orphaned"' in js
        assert "data.empty_notes" in js and "item.reason_fact" in js

    def test_edge_fold_and_draft_relations(self):
        js = _admin_js()
        assert 'data-ui-anchor="atlas.edge-show-all"' in js and "data.folded_note" in js
        assert "candidate.in_draft_note" in js and "candidate.direction_line" in js
        assert 'setAttribute("data-ui-anchor", "atlas.draft-relations")' in js

    def test_vector_section_reads_server_facts(self):
        js = _admin_js()
        assert "data.built_at_label" in js and "data.facts" in js
        assert '"最後に作成: " + data.built_at)' not in js
        assert "summary.fact" in js


def test_calibration_script_reads_vectors_only():
    """IK-0887 ③: 閾値の校正材料は保存済みベクトルの読みだけ（embedding を呼ばない）。"""
    from scripts import atlas_anchor_pair_distribution as script

    class _A:
        def __init__(self, node_id, vector):
            self.node_id = node_id
            self.node_kind = "concept"
            self.vector = vector

    values = script.pair_similarities(_skeleton(), [_A("a", [1.0, 0.0]), _A("c", [0.0, 1.0])])
    assert values == [0.0]
    assert script.quantiles([0.1, 0.5, 0.9])["p50"] == 0.5
    src = (BACKEND / "scripts" / "atlas_anchor_pair_distribution.py").read_text(encoding="utf-8")
    for term in ("generate_embeddings", "build_anchor_embeddings", "INSERT", "UPDATE"):
        assert term not in src
