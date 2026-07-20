"""``core/personal_graph/bridges.py`` 純粋部のユニットテスト（ビジョン Phase B）。

設計の正本は ``docs/features/knowledge_network_vision.md``
（§3 修正①・§4 KN-4・§7 Phase B）。``collect_bridge_entries`` /
``aggregate_entries`` は DB 接続を持たない純粋関数契約
（fake row=dict を直接渡してテストできる。``derive.py`` の ``build_network`` と同じ流儀）。

検証観点:
- k-匿名: distinct 学習者数が K_ANONYMITY（k=3・core/privacy.py 正本）未満の参照は
  結果に一切含めない（P3。行ごと非表示）
- distinct user で数える: 同一学習者が同じ参照へ複数回 connect しても1と数える
- 人数はレンジ文字列（3-5 / 6-10 / 11+）のみ。生カウント・user_id のキーが出ない
- 決定論順: レンジの大きい順 → ref_type → ref_id の辞書順（入力順に依存しない）
- ``status='connected'`` 以外の行・``kind='tension'`` 以外の行は集計に入らない
- 参照は ``payload.connected_refs`` のみ。LLM 候補由来の ``target_refs`` を持つ行でも
  connected_refs が無ければ何も数えない（KN-4/PN-3）
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.personal_graph.bridges import (  # noqa: E402
    aggregate_entries,
    collect_bridge_entries,
)
from core.privacy import K_ANONYMITY  # noqa: E402


# ---------------------------------------------------------------------------
# fake row ビルダ（interest_traces の実カラム名 + connect_tension_trace が書く
# payload.connected_refs の実キー構造に合わせる）
# ---------------------------------------------------------------------------


def _trace(
    user_id: str,
    *,
    component_ids: tuple = (),
    edge_ids: tuple = (),
    status: str = "connected",
    kind: str = "tension",
    payload: dict | None = None,
) -> dict:
    if payload is None:
        payload = {
            "connected_refs": {
                "component_ids": list(component_ids),
                "edge_ids": list(edge_ids),
            }
        }
    return {"user_id": user_id, "kind": kind, "status": status, "payload": payload}


def _bridges(traces: list[dict]) -> list[dict]:
    return aggregate_entries(collect_bridge_entries(traces))


class TestKAnonymityGate:
    def test_refs_below_k_are_dropped_entirely(self):
        """k 未満（2人）の参照は行ごと結果に出ない（セル秘匿ではなく非表示。P3）。"""
        traces = [
            _trace("u1", component_ids=("comp_a",)),
            _trace("u2", component_ids=("comp_a",)),
        ]
        assert _bridges(traces) == []

    def test_refs_at_k_are_included(self):
        traces = [
            _trace(f"u{i}", component_ids=("comp_a",)) for i in range(K_ANONYMITY)
        ]
        result = _bridges(traces)
        assert len(result) == 1
        assert result[0]["ref_type"] == "component"
        assert result[0]["ref_id"] == "comp_a"
        assert result[0]["learner_count_range"] == "3-5"

    def test_mixed_refs_gate_independently(self):
        """k を満たす参照だけが残り、満たさない参照は同じ結果に混ざらない。"""
        traces = [
            _trace("u1", component_ids=("comp_a", "comp_b")),
            _trace("u2", component_ids=("comp_a",)),
            _trace("u3", component_ids=("comp_a",)),
        ]
        result = _bridges(traces)
        assert [b["ref_id"] for b in result] == ["comp_a"]


class TestDistinctUserCounting:
    def test_same_user_multiple_connects_count_once(self):
        """同一学習者の複数 connect（複数行・同一参照）は distinct で1と数える。"""
        traces = [
            _trace("u1", component_ids=("comp_a",)),
            _trace("u1", component_ids=("comp_a",)),
            _trace("u1", component_ids=("comp_a",)),
            _trace("u2", component_ids=("comp_a",)),
        ]
        # 実人数は2（u1, u2）なので k=3 を満たさず、非表示になる。
        assert _bridges(traces) == []

    def test_duplicate_refs_within_one_payload_count_once(self):
        """1行の connected_refs 内の重複 ID も distinct user で1と数える。"""
        traces = [
            _trace("u1", component_ids=("comp_a", "comp_a")),
            _trace("u2", component_ids=("comp_a",)),
            _trace("u3", component_ids=("comp_a",)),
        ]
        result = _bridges(traces)
        assert len(result) == 1
        assert result[0]["learner_count_range"] == "3-5"


class TestCountRangeLabels:
    def _range_for(self, n: int) -> str:
        traces = [_trace(f"u{i}", edge_ids=("edge_x",)) for i in range(n)]
        result = _bridges(traces)
        assert len(result) == 1
        return result[0]["learner_count_range"]

    def test_range_3_5(self):
        assert self._range_for(3) == "3-5"
        assert self._range_for(5) == "3-5"

    def test_range_6_10(self):
        assert self._range_for(7) == "6-10"

    def test_range_11_plus(self):
        assert self._range_for(12) == "11+"


class TestResponseShape:
    def test_output_keys_have_no_raw_count_or_user_id(self):
        """応答 dict はレンジ + 参照 + label のみ（生カウント・user_id を出さない。PN-1/P3）。"""
        traces = [_trace(f"u{i}", component_ids=("comp_a",)) for i in range(4)]
        result = _bridges(traces)
        assert result, "k を満たす参照が1件あるはず"
        for bridge in result:
            assert set(bridge.keys()) == {
                "ref_type", "ref_id", "learner_count_range", "label",
            }

    def test_edge_refs_use_graph_edge_type(self):
        traces = [_trace(f"u{i}", edge_ids=("edge_x",)) for i in range(3)]
        result = _bridges(traces)
        assert result[0]["ref_type"] == "graph_edge"
        assert result[0]["label"] == ""


class TestDeterministicOrder:
    def test_sorted_by_range_desc_then_ref_type_then_ref_id(self):
        traces = []
        # comp_z: 12人 → "11+"（最上位に来る）
        traces += [_trace(f"a{i}", component_ids=("comp_z",)) for i in range(12)]
        # comp_a / comp_b: 各3人 → "3-5"（同レンジ内は ref_id 辞書順）
        traces += [_trace(f"b{i}", component_ids=("comp_b", "comp_a")) for i in range(3)]
        # edge_x: 7人 → "6-10"
        traces += [_trace(f"c{i}", edge_ids=("edge_x",)) for i in range(7)]

        result = _bridges(traces)
        assert [(b["ref_type"], b["ref_id"], b["learner_count_range"]) for b in result] == [
            ("component", "comp_z", "11+"),
            ("graph_edge", "edge_x", "6-10"),
            ("component", "comp_a", "3-5"),
            ("component", "comp_b", "3-5"),
        ]

    def test_order_is_input_order_independent(self):
        traces = [_trace(f"u{i}", component_ids=("comp_b", "comp_a")) for i in range(3)]
        forward = _bridges(traces)
        backward = _bridges(list(reversed(traces)))
        assert forward == backward
        assert [b["ref_id"] for b in forward] == ["comp_a", "comp_b"]


class TestOnlyConnectedTensionCounts:
    def test_non_connected_statuses_are_excluded(self):
        """connected 以外の status（candidate/open/articulated/dismissed 等）は、
        たとえ payload に connected_refs 形状のデータがあっても数えない。"""
        for status in ("candidate", "open", "articulated", "dismissed", "superseded"):
            traces = [
                _trace(f"u{i}", component_ids=("comp_a",), status=status)
                for i in range(5)
            ]
            assert _bridges(traces) == [], f"status={status} が集計に混入した"

    def test_non_tension_kinds_are_excluded(self):
        traces = [
            _trace(f"u{i}", component_ids=("comp_a",), kind="question")
            for i in range(5)
        ]
        assert _bridges(traces) == []

    def test_llm_candidate_target_refs_are_never_counted(self):
        """connected 行でも、本人が connect した connected_refs が無ければ何も数えない。
        LLM 候補生成時点で書かれる payload.target_refs は根拠にならない（KN-4/PN-3）。"""
        traces = [
            _trace(
                f"u{i}",
                payload={"target_refs": {"component_ids": ["comp_llm"]}},
            )
            for i in range(5)
        ]
        assert _bridges(traces) == []

    def test_malformed_connected_refs_fail_closed(self):
        """connected_refs が dict でない・ID が空などの行は黙って落とす（fail-closed）。"""
        traces = [
            _trace("u1", payload={"connected_refs": "not-a-dict"}),
            _trace("u2", payload={"connected_refs": {"component_ids": ["", None]}}),
            _trace("", component_ids=("comp_a",)),  # user_id 空
        ]
        assert _bridges(traces) == []
