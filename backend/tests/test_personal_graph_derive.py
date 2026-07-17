"""``core/personal_graph/derive.py::build_network`` の純粋ユニットテスト。

設計書 ``docs/features/personal_knowledge_network_design.md`` §2（ノード導出規則）・
§3（エッジ意味論）を検証する。``build_network(traces, reconstructions, topic_atlas)`` は
DB 接続を持たない純粋関数契約（fake row=dict を直接渡してテストできる）。

検証観点:
- N1 引っかかり: 本人が引き受けた status（``TENSION_OWNED_STATUSES``）のみノード化。
  candidate / dismissed / unclassified は除外（PN-3）。
- connected tension は ``payload.connected_refs``（``connect_tension_trace`` が本人の
  connect 操作でのみ書く別キー）の component_ids / edge_ids へ bridge 辺を張り、
  ノード自身のアンカーは component を優先する。LLM 候補生成時点の
  ``payload.target_refs`` は、たとえ非空でも connected の判定に使わない（PN-3）——
  未接続（open/articulated 等）は status を問わず topic 粒度へ縮退する。
- N2/N3 問い: ``learner_selected`` / ``confirmed`` かつ ``structure_anchor.status='active'``
  のみ精密アンカーを使う。``llm_candidate`` 帰属は topic 粒度へ縮退し、LLM の
  anchor_id は使わない（PN-3）。``superseded`` は除外。
- N4 再構成:「同意の汲み取り」opt-out 規則（self_check が NULL/'agreed' は含める、
  'disagreed'/'verdict_wrong' は除外、machine_verdict='mismatch' は除外）。
  revision_of チェーンは終端行のみで判定する。
- 出力順序が (created_at, id) で決定論的（入力順に依存しない）。
- ``to_dict()`` に集計数値（count/coverage/score 等）のキーが出ない（PN-4）。

注意: このファイルの実行には ``backend/core/personal_graph/`` パッケージ（別エージェントが
並行実装中）が必要。存在しない間は import エラーで収集自体が失敗する — それが正しい状態。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.personal_graph.derive import build_network  # noqa: E402
from core.personal_graph.schema import (  # noqa: E402
    EDGE_KIND_BRIDGE,
    NODE_KIND_QUESTION,
    NODE_KIND_RECONSTRUCTION,
    NODE_KIND_TENSION,
)
from core.tension.schema import TENSION_OWNED_STATUSES  # noqa: E402


# ---------------------------------------------------------------------------
# fake row ビルダ（interest_traces / learner_reconstructions の実カラム名に合わせる）
# ---------------------------------------------------------------------------


def _trace(
    *,
    id_: str,
    kind: str,
    status: str,
    topic_id: str = "topic1",
    payload: dict | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "id": id_,
        "kind": kind,
        "status": status,
        "topic_id": topic_id,
        "payload": payload or {},
        "created_at": created_at,
    }


def _recon(
    *,
    id_: str,
    item_id: str = "item1",
    claim_id: str = "claim1",
    machine_verdict: str = "match",
    self_check: str | None = None,
    descended_to_symbol: bool = False,
    revision_of: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "id": id_,
        "item_id": item_id,
        "claim_id": claim_id,
        "machine_verdict": machine_verdict,
        "self_check": self_check,
        "descended_to_symbol": descended_to_symbol,
        "revision_of": revision_of,
        "created_at": created_at,
    }


_ATLAS = {"topic1": "atlas_n1", "topic2": "atlas_n2"}


# ---------------------------------------------------------------------------
# N1: 引っかかり（tension）
# ---------------------------------------------------------------------------


class TestTensionOwnedStatuses:
    def test_all_owned_statuses_become_nodes(self):
        for i, status in enumerate(TENSION_OWNED_STATUSES):
            trace = _trace(
                id_=f"t-{status}",
                kind="tension",
                status=status,
                created_at=f"2026-01-0{i + 1}T00:00:00Z",
            )
            net = build_network([trace], [], _ATLAS)
            assert len(net.nodes) == 1, f"status={status!r} should produce exactly one node"
            assert net.nodes[0].node_kind == NODE_KIND_TENSION

    def test_candidate_dismissed_unclassified_excluded(self):
        traces = [
            _trace(id_="t-cand", kind="tension", status="candidate"),
            _trace(id_="t-dis", kind="tension", status="dismissed"),
            _trace(id_="t-unc", kind="tension", status="unclassified"),
        ]
        net = build_network(traces, [], _ATLAS)
        assert net.nodes == []
        assert net.edges == []


class TestConnectedTensionBridge:
    def test_connected_tension_creates_bridge_edges_and_component_anchor(self):
        trace = _trace(
            id_="t-connected",
            kind="tension",
            status="connected",
            topic_id="topic1",
            payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": ["e1"]}},
        )
        net = build_network([trace], [], _ATLAS)

        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.node_kind == NODE_KIND_TENSION
        assert node.anchor.anchor_type == "component"
        assert node.anchor.anchor_id == "c1"

        assert len(net.edges) == 2
        for edge in net.edges:
            assert edge.edge_kind == EDGE_KIND_BRIDGE
            assert edge.from_node_id == node.id
        to_refs = sorted((e.to_ref["ref_type"], e.to_ref["ref_id"]) for e in net.edges)
        assert to_refs == [("component", "c1"), ("graph_edge", "e1")]

    def test_connected_tension_ignores_llm_candidate_target_refs_even_alongside_connected_refs(self):
        """PN-3 回帰: LLM 候補生成時点の target_refs（別 component）が payload に
        残っていても、アンカー・橋の根拠は connected_refs（本人が connect した component）
        だけを使う。target_refs の component は一切ノードに現れない。"""
        trace = _trace(
            id_="t-connected-both",
            kind="tension",
            status="connected",
            topic_id="topic1",
            payload={
                "target_refs": {"component_ids": ["c_llm_guess"], "edge_ids": []},
                "connected_refs": {"component_ids": ["c_user_picked"], "edge_ids": []},
            },
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.anchor.anchor_type == "component"
        assert node.anchor.anchor_id == "c_user_picked"

        assert len(net.edges) == 1
        assert net.edges[0].to_ref == {"ref_type": "component", "ref_id": "c_user_picked"}
        # LLM 候補由来の component は edges にも一切現れない
        all_ref_ids = {e.to_ref["ref_id"] for e in net.edges}
        assert "c_llm_guess" not in all_ref_ids

    def test_connected_tension_without_connected_refs_falls_back_to_topic(self):
        """後方互換の fail-closed: connected_refs を持たない connect 済み行
        （本機能未コミット時点では実データに存在しないが、将来の古いデータを想定）は
        LLM 候補由来の target_refs があっても使わず topic 粒度へ縮退する。"""
        trace = _trace(
            id_="t-connected-legacy",
            kind="tension",
            status="connected",
            topic_id="topic1",
            payload={"target_refs": {"component_ids": ["c_llm_guess"], "edge_ids": []}},
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.anchor.anchor_type == "topic"
        assert node.anchor.anchor_id == "topic1"
        assert net.edges == []


class TestUnconnectedTensionFallsBackToTopic:
    def test_unconnected_tension_uses_topic_anchor_with_atlas_binding(self):
        trace = _trace(
            id_="t-open", kind="tension", status="open", topic_id="topic1", payload={},
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.anchor.anchor_type == "topic"
        assert node.anchor.anchor_id == "topic1"
        assert node.anchor.atlas_node_id == "atlas_n1"

    def test_topic_without_atlas_binding_keeps_topic_granularity(self):
        trace = _trace(
            id_="t-open2", kind="tension", status="open", topic_id="topic_unbound", payload={},
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.anchor.anchor_type == "topic"
        assert node.anchor.anchor_id == "topic_unbound"
        assert node.anchor.atlas_node_id is None

    def test_open_status_with_llm_candidate_target_refs_does_not_use_component_anchor(self):
        """指摘1（review P1）の核心回帰: tension 候補生成時点で payload.target_refs に
        component_ids が入っており（LLM が本人未確認のまま提案した対象）、本人が
        confirm しただけ（confirm_tension_trace は payload を保持したまま status を
        open/articulated に変えるだけで connect は未実施）の場合、status が 'connected'
        でない限り target_refs を一切アンカーの根拠に使わず topic 粒度へ縮退する
        （PN-3: 本人が接続操作をしていない LLM 帰属を根拠にしない）。"""
        for status in ("open", "articulated", "abstracted"):
            trace = _trace(
                id_=f"t-{status}-llm-refs",
                kind="tension",
                status=status,
                topic_id="topic1",
                payload={"target_refs": {"component_ids": ["c_llm_guess"], "edge_ids": ["e_llm_guess"]}},
            )
            net = build_network([trace], [], _ATLAS)
            assert len(net.nodes) == 1, status
            node = net.nodes[0]
            assert node.anchor.anchor_type == "topic", status
            assert node.anchor.anchor_id == "topic1", status
            # 未接続なので bridge 辺も一切張らない
            assert net.edges == [], status


# ---------------------------------------------------------------------------
# N2/N3: 問い（question）
# ---------------------------------------------------------------------------


class TestQuestionAttributionNodes:
    def test_learner_selected_active_anchor_uses_precise_anchor(self):
        trace = _trace(
            id_="q-selected",
            kind="question",
            status="open",
            topic_id="topic1",
            payload={
                "structure_anchor": {
                    "attribution_source": "learner_selected",
                    "status": "active",
                    "anchor_type": "claim",
                    "anchor_id": "claim_abc",
                    "anchor_label": "Some claim",
                }
            },
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.node_kind == NODE_KIND_QUESTION
        assert node.anchor.anchor_type == "claim"
        assert node.anchor.anchor_id == "claim_abc"
        assert node.anchor.anchor_label == "Some claim"

    def test_confirmed_active_anchor_uses_precise_anchor(self):
        trace = _trace(
            id_="q-confirmed",
            kind="question",
            status="open",
            topic_id="topic1",
            payload={
                "structure_anchor": {
                    "attribution_source": "confirmed",
                    "status": "active",
                    "anchor_type": "equation",
                    "anchor_id": "eq_2_7",
                }
            },
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        assert net.nodes[0].anchor.anchor_type == "equation"
        assert net.nodes[0].anchor.anchor_id == "eq_2_7"

    def test_llm_candidate_falls_back_to_topic_and_ignores_llm_anchor_id(self):
        trace = _trace(
            id_="q-llm",
            kind="question",
            status="open",
            topic_id="topic2",
            payload={
                "structure_anchor": {
                    "attribution_source": "llm_candidate",
                    "status": "active",
                    "anchor_type": "concept",
                    "anchor_id": "concept_xyz",
                }
            },
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.anchor.anchor_type == "topic"
        assert node.anchor.anchor_id != "concept_xyz"
        assert node.anchor.anchor_id == "topic2"
        assert node.anchor.atlas_node_id == "atlas_n2"

    def test_dismissed_structure_anchor_status_falls_back_to_topic(self):
        """帰属自体は confirmed でも structure_anchor.status='dismissed' なら精密アンカーは使わない。"""
        trace = _trace(
            id_="q-anchor-dismissed",
            kind="question",
            status="open",
            topic_id="topic1",
            payload={
                "structure_anchor": {
                    "attribution_source": "confirmed",
                    "status": "dismissed",
                    "anchor_type": "claim",
                    "anchor_id": "claim_zzz",
                }
            },
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        assert net.nodes[0].anchor.anchor_type == "topic"
        assert net.nodes[0].anchor.anchor_id != "claim_zzz"

    def test_missing_structure_anchor_falls_back_to_topic(self):
        trace = _trace(
            id_="q-none", kind="question", status="open", topic_id="topic1", payload={},
        )
        net = build_network([trace], [], _ATLAS)
        assert len(net.nodes) == 1
        assert net.nodes[0].anchor.anchor_type == "topic"

    def test_superseded_question_excluded_even_with_precise_anchor(self):
        trace = _trace(
            id_="q-superseded",
            kind="question",
            status="superseded",
            topic_id="topic1",
            payload={
                "structure_anchor": {
                    "attribution_source": "learner_selected",
                    "status": "active",
                    "anchor_type": "claim",
                    "anchor_id": "claim_abc",
                }
            },
        )
        net = build_network([trace], [], _ATLAS)
        assert net.nodes == []


# ---------------------------------------------------------------------------
# N4: 再構成の成功（reconstruction）+ revision_of チェーン
# ---------------------------------------------------------------------------


class TestReconstructionConsentOptOut:
    def test_match_with_null_self_check_is_included(self):
        net = build_network([], [_recon(id_="r-null", machine_verdict="match", self_check=None)], _ATLAS)
        assert len(net.nodes) == 1
        assert net.nodes[0].node_kind == NODE_KIND_RECONSTRUCTION

    def test_match_with_agreed_is_included(self):
        net = build_network([], [_recon(id_="r-agreed", machine_verdict="match", self_check="agreed")], _ATLAS)
        assert len(net.nodes) == 1

    def test_match_with_disagreed_is_excluded(self):
        net = build_network([], [_recon(id_="r-dis", machine_verdict="match", self_check="disagreed")], _ATLAS)
        assert net.nodes == []

    def test_match_with_verdict_wrong_is_excluded(self):
        net = build_network([], [_recon(id_="r-vw", machine_verdict="match", self_check="verdict_wrong")], _ATLAS)
        assert net.nodes == []

    def test_mismatch_is_excluded_regardless_of_self_check(self):
        for self_check in (None, "agreed"):
            net = build_network(
                [], [_recon(id_="r-mismatch", machine_verdict="mismatch", self_check=self_check)], _ATLAS,
            )
            assert net.nodes == [], f"mismatch with self_check={self_check!r} must be excluded"


class TestRevisionChainTerminalOnly:
    def test_terminal_mismatch_excludes_whole_chain(self):
        row1 = _recon(id_="r1", machine_verdict="match", self_check=None, created_at="2026-01-01T00:00:00Z")
        row2 = _recon(
            id_="r2", machine_verdict="mismatch", self_check=None, revision_of="r1",
            created_at="2026-01-02T00:00:00Z",
        )
        net = build_network([], [row1, row2], _ATLAS)
        assert net.nodes == [], "terminal (r2) is mismatch, so r1 must not be resurrected as a node"

    def test_terminal_match_includes_only_terminal_with_revision_fact(self):
        row1 = _recon(id_="r1", machine_verdict="mismatch", self_check=None, created_at="2026-01-01T00:00:00Z")
        row2 = _recon(
            id_="r2", machine_verdict="match", self_check=None, revision_of="r1",
            created_at="2026-01-02T00:00:00Z",
        )
        net = build_network([], [row1, row2], _ATLAS)
        assert len(net.nodes) == 1
        assert "改訂を経てたどり着いた再構成" in net.nodes[0].facts

    def test_descend_fact_present_when_any_row_in_chain_descended(self):
        row1 = _recon(
            id_="r1", machine_verdict="mismatch", self_check=None, descended_to_symbol=True,
            created_at="2026-01-01T00:00:00Z",
        )
        row2 = _recon(
            id_="r2", machine_verdict="match", self_check=None, revision_of="r1",
            descended_to_symbol=False, created_at="2026-01-02T00:00:00Z",
        )
        net = build_network([], [row1, row2], _ATLAS)
        assert len(net.nodes) == 1
        assert "原因を絞るため記号まで降りた" in net.nodes[0].facts

    def test_no_revision_fact_when_terminal_has_no_revision_of(self):
        row = _recon(
            id_="r-solo", machine_verdict="match", self_check="agreed",
            descended_to_symbol=True, revision_of=None,
        )
        net = build_network([], [row], _ATLAS)
        assert len(net.nodes) == 1
        facts = net.nodes[0].facts
        assert "原因を絞るため記号まで降りた" in facts
        assert "改訂を経てたどり着いた再構成" not in facts


# ---------------------------------------------------------------------------
# 決定論的順序 + 集計数値なし（PN-4）
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_nodes_sorted_by_created_at_then_id(self):
        traces = [
            _trace(id_="t-c", kind="tension", status="open", topic_id="topic1", created_at="2026-03-01T00:00:00Z"),
            _trace(id_="t-a", kind="tension", status="open", topic_id="topic2", created_at="2026-01-01T00:00:00Z"),
            _trace(id_="t-b", kind="tension", status="open", topic_id="topic1", created_at="2026-02-01T00:00:00Z"),
        ]
        net = build_network(traces, [], _ATLAS)
        keys = [(n.created_at, n.id) for n in net.nodes]
        assert keys == sorted(keys)

    def test_output_order_independent_of_input_order(self):
        traces = [
            _trace(id_="t1", kind="tension", status="open", topic_id="topic1", created_at="2026-01-01T00:00:00Z"),
            _trace(id_="t2", kind="tension", status="open", topic_id="topic2", created_at="2026-02-01T00:00:00Z"),
            _trace(id_="t3", kind="tension", status="open", topic_id="topic1", created_at="2026-03-01T00:00:00Z"),
        ]
        net_forward = build_network(traces, [], _ATLAS)
        net_reversed = build_network(list(reversed(traces)), [], _ATLAS)
        assert net_forward.to_dict() == net_reversed.to_dict()

    def test_edges_also_sorted_deterministically(self):
        trace = _trace(
            id_="t-connected2", kind="tension", status="connected", topic_id="topic1",
            payload={"connected_refs": {"component_ids": ["c1", "c2"], "edge_ids": ["e1"]}},
        )
        net1 = build_network([trace], [], _ATLAS)
        net2 = build_network([dict(trace)], [], dict(_ATLAS))
        assert [e.to_ref for e in net1.edges] == [e.to_ref for e in net2.edges]


class TestNoAggregateNumbers:
    def test_to_dict_has_no_count_like_keys(self):
        traces = [
            _trace(
                id_="t-agg", kind="tension", status="connected", topic_id="topic1",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": ["e1"]}},
            ),
        ]
        reconstructions = [_recon(id_="r-agg", machine_verdict="match", self_check="agreed")]
        net = build_network(traces, reconstructions, _ATLAS)
        blob = json.dumps(net.to_dict(), ensure_ascii=False).lower()
        for forbidden in ("count", "coverage", "score", "percentage", "ranking", "weight"):
            assert forbidden not in blob, f"aggregate-looking key/value {forbidden!r} leaked into to_dict()"


# ---------------------------------------------------------------------------
# N16（2026-07-17）: reconstruction ノードの claim → topic 解決
# ---------------------------------------------------------------------------


class TestReconstructionClaimTopicResolution:
    """N16 是正: ``claim_topic_map``（topics[].linked_claim_ids の逆引き）で claim が
    トピック教材に組み込まれていれば topic_id / anchor.atlas_node_id を解決する。
    解決できない claim は従来どおり None（「まだ地図にない」トレイに残る・P4）。
    """

    def test_claim_in_map_with_atlas_binding_resolves_topic_and_atlas(self):
        row = _recon(id_="r1", claim_id="claimX", machine_verdict="match", self_check=None)
        net = build_network([], [row], _ATLAS, {"claimX": "topic1"})
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.topic_id == "topic1"
        assert node.anchor.anchor_type == "claim"
        assert node.anchor.anchor_id == "claimX"
        assert node.anchor.atlas_node_id == "atlas_n1"

    def test_claim_in_map_without_atlas_binding_sets_topic_only(self):
        row = _recon(id_="r1", claim_id="claimX", machine_verdict="match", self_check=None)
        net = build_network([], [row], _ATLAS, {"claimX": "topic-unbound"})
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.topic_id == "topic-unbound"
        assert node.anchor.atlas_node_id is None

    def test_claim_not_in_map_stays_unresolved(self):
        row = _recon(id_="r1", claim_id="claimY", machine_verdict="match", self_check=None)
        net = build_network([], [row], _ATLAS, {"claimX": "topic1"})
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.topic_id is None
        assert node.anchor.atlas_node_id is None

    def test_map_omitted_keeps_backward_compatible_none(self):
        row = _recon(id_="r1", claim_id="claimX", machine_verdict="match", self_check=None)
        net = build_network([], [row], _ATLAS)
        assert len(net.nodes) == 1
        assert net.nodes[0].topic_id is None
        assert net.nodes[0].anchor.atlas_node_id is None

    def test_anchor_remains_claim_type_after_resolution(self):
        """topic 解決してもアンカーは claim のまま（topic アンカーに置き換えない —
        atlas_node_id だけを binding から導出する）。"""
        row = _recon(id_="r1", claim_id="claimX", machine_verdict="match", self_check=None)
        net = build_network([], [row], _ATLAS, {"claimX": "topic1"})
        assert net.nodes[0].anchor.anchor_type == "claim"


class TestClaimTopicMapFromCourseData:
    """queries._claim_topic_map_from_data（純粋部・DB 非接続）: topics[].linked_claim_ids
    の逆引きが決定論的で、フラット/章ネスト両形を走査すること。"""

    def _map(self, data):
        # 遅延 import（queries は sqlalchemy に依存するがここでは接続しない）
        from core.personal_graph.queries import _claim_topic_map_from_data
        return _claim_topic_map_from_data(data)

    def test_flat_topics_reverse_lookup(self):
        data = {"topics": [
            {"id": "t1", "linked_claim_ids": ["c1", "c2"]},
            {"id": "t2", "linked_claim_ids": ["c3"]},
        ]}
        assert self._map(data) == {"c1": "t1", "c2": "t1", "c3": "t2"}

    def test_first_occurrence_wins_deterministically(self):
        data = {"topics": [
            {"id": "t1", "linked_claim_ids": ["c1"]},
            {"id": "t2", "linked_claim_ids": ["c1"]},
        ]}
        assert self._map(data) == {"c1": "t1"}

    def test_nested_chapter_topics_are_scanned(self):
        data = {"chapters": [{"topics": [{"id": "t9", "linked_claim_ids": ["c9"]}]}]}
        assert self._map(data) == {"c9": "t9"}

    def test_topics_without_ids_or_claims_are_skipped(self):
        data = {"topics": [
            {"linked_claim_ids": ["c1"]},          # id なし
            {"id": "t2"},                            # linked_claim_ids なし
            {"id": "t3", "linked_claim_ids": []},   # 空
        ]}
        assert self._map(data) == {}


class TestDeriveEntrypointsThreadClaimTopicMap:
    """derive_personal_network / derive_person_network が queries の claim→topic 解決を
    build_network / build_person_network へ渡すこと（monkeypatch・DB 非接続）。"""

    def test_course_scope_entrypoint_uses_fetch_claim_topic_map(self, monkeypatch):
        import core.personal_graph.queries as queries_mod
        from core.personal_graph.derive import derive_personal_network

        monkeypatch.setattr(queries_mod, "fetch_traces", lambda uid, cid: [])
        monkeypatch.setattr(queries_mod, "fetch_reconstructions", lambda uid, cid: [
            _recon(id_="r1", claim_id="claimX", machine_verdict="match", self_check=None),
        ])
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {"topic1": "atlas_n1"})
        monkeypatch.setattr(queries_mod, "fetch_claim_topic_map", lambda cid: {"claimX": "topic1"})

        net = derive_personal_network("user1", "course1")
        assert len(net.nodes) == 1
        assert net.nodes[0].topic_id == "topic1"
        assert net.nodes[0].anchor.atlas_node_id == "atlas_n1"

    def test_person_scope_entrypoint_uses_fetch_claim_topic_map_for_courses(self, monkeypatch):
        import core.personal_graph.queries as queries_mod
        from core.personal_graph.derive import derive_person_network

        recon = _recon(id_="r1", claim_id="claimX", machine_verdict="match", self_check=None)
        recon["course_id"] = "courseA"
        monkeypatch.setattr(queries_mod, "fetch_traces_for_user", lambda uid: [])
        monkeypatch.setattr(queries_mod, "fetch_reconstructions_for_user", lambda uid: [recon])
        monkeypatch.setattr(
            queries_mod, "fetch_topic_atlas_binding_for_courses",
            lambda cids: {"courseA": {"topic1": "atlas_n1"}},
        )
        monkeypatch.setattr(
            queries_mod, "fetch_claim_topic_map_for_courses",
            lambda cids: {"courseA": {"claimX": "topic1"}},
        )

        net = derive_person_network("user1")
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.course_id == "courseA"
        assert node.topic_id == "topic1"
        assert node.anchor.atlas_node_id == "atlas_n1"
