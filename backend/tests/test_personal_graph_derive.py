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

import pytest
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


class TestTopicAnchorLabelIsTopicTitle:
    """topic 縮退アンカー（N3 の問い・未接続 tension）の ``anchor_label`` にトピック題名が
    入ること。「わたしの地図」の中心選択チップが学習者の発話原文にフォールバックせず、
    トピック題名を出せるようにするための是正（設計書
    ``docs/features/personal_map_nearby_design.md``）。"""

    _LABELS = {"topic1": "干渉計の基礎", "topic2": "雑音の見積り"}

    def test_question_topic_fallback_uses_topic_title(self):
        trace = _trace(
            id_="q-none", kind="question", status="open", topic_id="topic1",
            payload={"text": "うん、塗ってみる。"},
        )
        net = build_network([trace], [], _ATLAS, topic_labels=self._LABELS)
        anchor = net.nodes[0].anchor
        assert anchor.anchor_type == "topic"
        assert anchor.anchor_id == "topic1"
        assert anchor.anchor_label == "干渉計の基礎"
        # 発話原文は label 側に残るだけで anchor_label には入らない
        assert net.nodes[0].label == "うん、塗ってみる。"

    def test_llm_candidate_topic_fallback_uses_topic_title(self):
        trace = _trace(
            id_="q-llm", kind="question", status="open", topic_id="topic2",
            payload={"structure_anchor": {
                "attribution_source": "llm_candidate", "status": "active",
                "anchor_type": "concept", "anchor_id": "concept_xyz",
                "anchor_label": "LLM が推した概念",
            }},
        )
        net = build_network([trace], [], _ATLAS, topic_labels=self._LABELS)
        anchor = net.nodes[0].anchor
        assert anchor.anchor_type == "topic"
        assert anchor.anchor_label == "雑音の見積り"

    def test_unconnected_tension_topic_fallback_uses_topic_title(self):
        trace = _trace(id_="t-open", kind="tension", status="open", topic_id="topic1")
        net = build_network([trace], [], _ATLAS, topic_labels=self._LABELS)
        anchor = net.nodes[0].anchor
        assert anchor.anchor_type == "topic"
        assert anchor.anchor_label == "干渉計の基礎"

    def test_omitted_topic_labels_keeps_backward_compatible_empty_label(self):
        traces = [
            _trace(id_="q1", kind="question", status="open", topic_id="topic1"),
            _trace(id_="t1", kind="tension", status="open", topic_id="topic1"),
        ]
        net = build_network(traces, [], _ATLAS)
        assert [n.anchor.anchor_label for n in net.nodes] == ["", ""]

    def test_unknown_topic_keeps_empty_label_without_fabrication(self):
        """題名が引けない topic（コース削除済み・title 未設定）は空のまま（P4）。"""
        trace = _trace(
            id_="q-unknown", kind="question", status="open", topic_id="topic_gone",
            payload={"text": "発話原文"},
        )
        net = build_network([trace], [], _ATLAS, topic_labels=self._LABELS)
        assert net.nodes[0].anchor.anchor_label == ""

    def test_confirmed_structure_anchor_label_is_not_overwritten(self):
        """N2（本人確定済み帰属）の anchor_label は structure_anchor 側の値のまま。"""
        trace = _trace(
            id_="q-confirmed", kind="question", status="open", topic_id="topic1",
            payload={"structure_anchor": {
                "attribution_source": "confirmed", "status": "active",
                "anchor_type": "claim", "anchor_id": "claim_abc",
                "anchor_label": "Some claim",
            }},
        )
        net = build_network([trace], [], _ATLAS, topic_labels=self._LABELS)
        anchor = net.nodes[0].anchor
        assert anchor.anchor_type == "claim"
        assert anchor.anchor_label == "Some claim"

    def test_connected_tension_component_anchor_has_no_topic_label(self):
        """component アンカーへ解決した tension はトピック題名を持ち込まない。"""
        trace = _trace(
            id_="t-connected", kind="tension", status="connected", topic_id="topic1",
            payload={"connected_refs": {"component_ids": ["c1"]}},
        )
        net = build_network([trace], [], _ATLAS, topic_labels=self._LABELS)
        anchor = net.nodes[0].anchor
        assert anchor.anchor_type == "component"
        assert anchor.anchor_label == ""


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


class TestTopicLabelsFromCourseData:
    """queries._topic_labels_from_data（純粋部・DB 非接続）: topics[].title の収集が
    フラット/章ネスト両形を走査し、題名の無い topic をキーごと省くこと。"""

    def _labels(self, data):
        # 遅延 import（queries は sqlalchemy に依存するがここでは接続しない）
        from core.personal_graph.queries import _topic_labels_from_data
        return _topic_labels_from_data(data)

    def test_flat_topics_titles(self):
        data = {"topics": [
            {"id": "t1", "title": "干渉計の基礎"},
            {"id": "t2", "title": "雑音の見積り"},
        ]}
        assert self._labels(data) == {"t1": "干渉計の基礎", "t2": "雑音の見積り"}

    def test_nested_chapter_topics_are_scanned(self):
        data = {"chapters": [{"topics": [{"id": "t9", "title": "章の中のトピック"}]}]}
        assert self._labels(data) == {"t9": "章の中のトピック"}

    def test_topics_without_id_or_title_are_skipped(self):
        data = {"topics": [
            {"title": "id なし"},
            {"id": "t2"},                 # title なし
            {"id": "t3", "title": ""},    # 空
            {"id": "t4", "title": "   "}, # 空白のみ
        ]}
        assert self._labels(data) == {}

    def test_titles_are_stripped(self):
        data = {"topics": [{"id": "t1", "title": "  題名  "}]}
        assert self._labels(data) == {"t1": "題名"}


class TestDeriveEntrypointsThreadTopicLabels:
    """derive_personal_network / derive_person_network が queries のトピック題名解決を
    build_network / build_person_network へ渡すこと（monkeypatch・DB 非接続）。"""

    def test_course_scope_entrypoint_uses_fetch_topic_labels(self, monkeypatch):
        import core.personal_graph.queries as queries_mod
        from core.personal_graph.derive import derive_personal_network

        monkeypatch.setattr(queries_mod, "fetch_traces", lambda uid, cid: [
            _trace(id_="q1", kind="question", status="open", topic_id="topic1"),
        ])
        monkeypatch.setattr(queries_mod, "fetch_reconstructions", lambda uid, cid: [])
        monkeypatch.setattr(queries_mod, "fetch_topic_atlas_binding", lambda cid: {})
        monkeypatch.setattr(queries_mod, "fetch_claim_topic_map", lambda cid: {})
        monkeypatch.setattr(queries_mod, "fetch_topic_labels", lambda cid: {"topic1": "干渉計の基礎"})

        net = derive_personal_network("user1", "course1")
        assert net.nodes[0].anchor.anchor_label == "干渉計の基礎"

    def test_person_scope_entrypoint_uses_fetch_topic_labels_for_courses(self, monkeypatch):
        import core.personal_graph.queries as queries_mod
        from core.personal_graph.derive import derive_person_network

        trace = _trace(id_="q1", kind="question", status="open", topic_id="topic1")
        trace["course_id"] = "courseA"
        monkeypatch.setattr(queries_mod, "fetch_traces_for_user", lambda uid: [trace])
        monkeypatch.setattr(queries_mod, "fetch_reconstructions_for_user", lambda uid: [])
        monkeypatch.setattr(
            queries_mod, "fetch_topic_atlas_binding_for_courses", lambda cids: {},
        )
        monkeypatch.setattr(
            queries_mod, "fetch_claim_topic_map_for_courses", lambda cids: {},
        )
        monkeypatch.setattr(
            queries_mod, "fetch_topic_labels_for_courses",
            lambda cids: {"courseA": {"topic1": "干渉計の基礎"}},
        )

        net = derive_person_network("user1")
        assert net.nodes[0].course_id == "courseA"
        assert net.nodes[0].anchor.anchor_label == "干渉計の基礎"


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
        monkeypatch.setattr(queries_mod, "fetch_topic_labels", lambda cid: {})

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
        monkeypatch.setattr(queries_mod, "fetch_topic_labels_for_courses", lambda cids: {})

        net = derive_person_network("user1")
        assert len(net.nodes) == 1
        node = net.nodes[0]
        assert node.course_id == "courseA"
        assert node.topic_id == "topic1"
        assert node.anchor.atlas_node_id == "atlas_n1"


class TestReconstructionNodeLabel:
    """N4 のラベルは元 claim 本文（``theory_claims.text`` = row["claim_text"]）を使う。

    ``queries.fetch_reconstructions{,_for_user}`` が LEFT JOIN で併読する列で、引けない
    行（claim 削除済み・claim_text キーの無い古い呼び出し）は従来の固定文字列へ
    フォールバックする（後方互換）。R層 item の伏せフィールド（response_space /
    expected）は引かない。
    """

    def test_claim_text_becomes_the_label(self):
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        row["claim_text"] = "ノイズ床は散射雑音で決まる"
        net = build_network([], [row], _ATLAS)
        assert net.nodes[0].label == "ノイズ床は散射雑音で決まる"

    def test_missing_claim_text_key_keeps_the_legacy_label(self):
        """fake rows に claim_text が無くても動く（getattr/None 既定の後方互換）。"""
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        assert "claim_text" not in row
        net = build_network([], [row], _ATLAS)
        assert net.nodes[0].label == "claim への再構成"

    def test_none_claim_text_keeps_the_legacy_label(self):
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        row["claim_text"] = None
        net = build_network([], [row], _ATLAS)
        assert net.nodes[0].label == "claim への再構成"

    def test_blank_claim_text_keeps_the_legacy_label(self):
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        row["claim_text"] = "   "
        net = build_network([], [row], _ATLAS)
        assert net.nodes[0].label == "claim への再構成"

    def test_long_claim_text_is_truncated_with_the_shared_80_char_rule(self):
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        row["claim_text"] = "あ" * 200
        net = build_network([], [row], _ATLAS)
        assert net.nodes[0].label == "あ" * 80

    def test_person_scope_carries_claim_text_through(self):
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        row["course_id"] = "courseA"
        row["claim_text"] = "干渉計の応答は腕長差に比例する"
        from core.personal_graph.derive import build_person_network

        net = build_person_network([], [row], {"courseA": {}})
        assert net.nodes[0].label == "干渉計の応答は腕長差に比例する"

    def test_label_does_not_expose_hidden_item_fields(self):
        """伏せフィールド（response_space / expected）は row に入っていても使わない。"""
        row = _recon(id_="r1", machine_verdict="match", self_check=None)
        row["claim_text"] = "主張本文"
        row["response_space"] = ["伏せ選択肢A", "伏せ選択肢B"]
        row["expected"] = {"choice_id": "伏せ想定解"}
        net = build_network([], [row], _ATLAS)
        node_json = json.dumps(net.nodes[0].to_dict(), ensure_ascii=False)
        assert "伏せ" not in node_json
        assert net.nodes[0].label == "主張本文"


class TestMalformedPayloadFailsClosed:
    """``interest_traces.payload`` は JSONB なので JSON スカラーも入りうる。

    回帰: ``trace.get("payload") or {}`` の直後に ``.get()`` を呼んでいたため、
    非 dict の payload が 1 行あるだけで AttributeError になった。
    ``api/routes/personal_map.py`` は導出を try/except で包まないので、
    「わたしの地図」/ 近傍 / 旅がまるごと 500 になる（PN-7 は fail-closed を要求）。
    """

    @pytest.mark.parametrize("payload", ["notadict", 5, 1.5, True, ["x"], "", "null"])
    def test_non_dict_payload_does_not_raise(self, payload):
        row = _trace(id_="t1", kind="tension", status="open")
        row["payload"] = payload
        net = build_network([row], [], _ATLAS)
        # 行は落とさず topic 粒度へ縮退する（P4: 削除ではない）。
        assert len(net.nodes) == 1
        assert net.nodes[0].anchor.anchor_type == "topic"
        assert net.nodes[0].label == ""

    def test_non_dict_payload_on_connected_tension_makes_no_bridge(self):
        """connected でも壊れた payload から橋の根拠を作らない（fail-closed）。"""
        row = _trace(id_="t1", kind="tension", status="connected")
        row["payload"] = "notadict"
        net = build_network([row], [], _ATLAS)
        assert net.edges == []
        assert net.nodes[0].anchor.anchor_type == "topic"

    def test_non_dict_payload_on_question_degrades_to_topic(self):
        row = _trace(id_="q1", kind="question", status="active")
        row["payload"] = 42
        net = build_network([row], [], _ATLAS)
        assert len(net.nodes) == 1
        assert net.nodes[0].anchor.anchor_type == "topic"

    def test_map_excluded_still_honoured_for_dict_payloads(self):
        """縮退の追加で既存の map_excluded 判定を壊していないこと。"""
        row = _trace(id_="t1", kind="tension", status="open", payload={"map_excluded": True})
        assert build_network([row], [], _ATLAS).nodes == []
