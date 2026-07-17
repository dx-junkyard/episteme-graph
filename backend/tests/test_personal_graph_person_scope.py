"""``core/personal_graph/derive.py`` の本人スコープ導出（Phase P-0.5）純粋ユニットテスト。

正本は ``/Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md``
§5（特に §5.1〜5.3）と、それを継承する
``docs/features/personal_knowledge_network_design.md``。

検証対象は ``build_person_network(traces, reconstructions, atlas_by_course)`` /
``group_nodes_by_anchor(network)``（両方とも DB 非依存の純粋関数）。
``test_personal_graph_derive.py`` と同じ fake row ビルダの流儀を踏襲する。

検証観点:
- 複数コースの trace が1つの個人ネットワークにマージされ、各ノードに ``course_id``
  が provenance として付く（所有境界ではない。§5.1）。
- 同一 (anchor_type, anchor_id) を持つ複数コースのノードが1つの anchor グループに束ねられ、
  ``course_ids`` が出現順ユニークで載る（§5.2「同じ公共アンカーをコースごとに複製しない」）。
- ``payload.map_excluded`` が truthy な trace はノード化されない（本人が「地図には
  反映しない」を選んだ痕跡。行は保持され導出から外れるだけ）。
- 出力順序が (created_at, id) で決定論的（入力順に依存しない）。
- topic 縮退ノードが、そのノードが属するコースの atlas binding で解決される
  （コースをまたいで binding を混同しない）。
- anchor_id が空文字のノードは anchor グループに入らない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.personal_graph.derive import build_network, build_person_network, group_nodes_by_anchor  # noqa: E402
from core.personal_graph.schema import (  # noqa: E402
    NODE_KIND_QUESTION,
    NODE_KIND_TENSION,
)


# ---------------------------------------------------------------------------
# fake row ビルダ（test_personal_graph_derive.py と同型。course_id を追加で持てる）
# ---------------------------------------------------------------------------


def _trace(
    *,
    id_: str,
    kind: str,
    status: str,
    topic_id: str = "topic1",
    course_id: str = "courseA",
    payload: dict | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "id": id_,
        "kind": kind,
        "status": status,
        "topic_id": topic_id,
        "course_id": course_id,
        "payload": payload or {},
        "created_at": created_at,
    }


def _recon(
    *,
    id_: str,
    item_id: str = "item1",
    claim_id: str = "claim1",
    course_id: str = "courseA",
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
        "course_id": course_id,
        "machine_verdict": machine_verdict,
        "self_check": self_check,
        "descended_to_symbol": descended_to_symbol,
        "revision_of": revision_of,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# ① 複数コースの trace が1ネットワークにマージされ course_id が付く
# ---------------------------------------------------------------------------


class TestCrossCourseMerge:
    def test_traces_from_multiple_courses_merge_into_one_network_with_course_id(self):
        traces = [
            _trace(id_="t-a", kind="tension", status="open", topic_id="topicA", course_id="courseA"),
            _trace(id_="t-b", kind="tension", status="open", topic_id="topicB", course_id="courseB"),
        ]
        net = build_person_network(traces, [], {})
        assert len(net.nodes) == 2
        by_id = {n.id: n for n in net.nodes}
        assert by_id["t-a"].course_id == "courseA"
        assert by_id["t-b"].course_id == "courseB"

    def test_reconstructions_from_multiple_courses_merge_with_course_id(self):
        reconstructions = [
            _recon(id_="r-a", course_id="courseA", created_at="2026-01-01T00:00:00Z"),
            _recon(id_="r-b", course_id="courseB", created_at="2026-01-02T00:00:00Z"),
        ]
        net = build_person_network([], reconstructions, {})
        assert len(net.nodes) == 2
        by_id = {n.id: n for n in net.nodes}
        assert by_id["r-a"].course_id == "courseA"
        assert by_id["r-b"].course_id == "courseB"

    def test_merged_network_matches_per_course_build_network_semantics(self):
        """build_person_network はコースごとに既存 build_network を呼ぶだけなので、
        本人確定ルール（PN-3 等）は per-course の build_network と同じ結果になる。"""
        traces = [
            _trace(id_="t-cand", kind="tension", status="candidate", course_id="courseA"),
            _trace(id_="t-open", kind="tension", status="open", course_id="courseA"),
        ]
        net = build_person_network(traces, [], {})
        # candidate は除外、open のみ採用（TENSION_OWNED_STATUSES と同じ判定）
        assert [n.id for n in net.nodes] == ["t-open"]


# ---------------------------------------------------------------------------
# ② 同一 (anchor_type, anchor_id) の複数コースノードが1グループに束ねられる
# ---------------------------------------------------------------------------


class TestAnchorGrouping:
    def test_same_component_anchor_across_courses_groups_together(self):
        traces = [
            _trace(
                id_="t-a", kind="tension", status="connected", topic_id="topicA", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
                created_at="2026-01-01T00:00:00Z",
            ),
            _trace(
                id_="t-b", kind="tension", status="connected", topic_id="topicB", course_id="courseB",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
                created_at="2026-01-02T00:00:00Z",
            ),
        ]
        net = build_person_network(traces, [], {})
        groups = group_nodes_by_anchor(net)
        assert len(groups) == 1
        group = groups[0]
        assert group["anchor"]["anchor_type"] == "component"
        assert group["anchor"]["anchor_id"] == "c1"
        assert group["node_ids"] == ["t-a", "t-b"]
        assert group["course_ids"] == ["courseA", "courseB"]
        # 件数フィールド（count/coverage 等）を持たない（PN-4）
        assert "count" not in group
        assert "coverage" not in group

    def test_different_anchor_ids_stay_in_separate_groups(self):
        traces = [
            _trace(
                id_="t-a", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
            ),
            _trace(
                id_="t-b", kind="tension", status="connected", course_id="courseB",
                payload={"connected_refs": {"component_ids": ["c2"], "edge_ids": []}},
            ),
        ]
        net = build_person_network(traces, [], {})
        groups = group_nodes_by_anchor(net)
        assert len(groups) == 2
        anchor_ids = sorted(g["anchor"]["anchor_id"] for g in groups)
        assert anchor_ids == ["c1", "c2"]

    def test_course_ids_within_group_are_unique_in_order_of_appearance(self):
        """同一コース由来の複数ノードが同じアンカーに束ねられても course_ids は重複しない。"""
        traces = [
            _trace(
                id_="t-1", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
                created_at="2026-01-01T00:00:00Z",
            ),
            _trace(
                id_="t-2", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
                created_at="2026-01-02T00:00:00Z",
            ),
        ]
        net = build_person_network(traces, [], {})
        groups = group_nodes_by_anchor(net)
        assert len(groups) == 1
        assert groups[0]["course_ids"] == ["courseA"]
        assert groups[0]["node_ids"] == ["t-1", "t-2"]

    def test_groups_sorted_by_anchor_type_then_anchor_id(self):
        traces = [
            _trace(
                id_="t-z", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["z9"], "edge_ids": []}},
            ),
            _trace(
                id_="t-a", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["a1"], "edge_ids": []}},
            ),
        ]
        net = build_person_network(traces, [], {})
        groups = group_nodes_by_anchor(net)
        assert [g["anchor"]["anchor_id"] for g in groups] == ["a1", "z9"]


# ---------------------------------------------------------------------------
# ③ map_excluded=true の trace はノード化されない
# ---------------------------------------------------------------------------


class TestMapExcludedFilter:
    def test_map_excluded_tension_is_not_nodified(self):
        traces = [
            _trace(
                id_="t-hidden", kind="tension", status="open",
                payload={"map_excluded": True},
            ),
            _trace(id_="t-visible", kind="tension", status="open", payload={}),
        ]
        net = build_network(traces, [], {})
        assert [n.id for n in net.nodes] == ["t-visible"]

    def test_map_excluded_question_is_not_nodified(self):
        traces = [
            _trace(
                id_="q-hidden", kind="question", status="open",
                payload={"map_excluded": True},
            ),
        ]
        net = build_network(traces, [], {})
        assert net.nodes == []

    def test_map_excluded_false_or_absent_is_unaffected(self):
        traces = [
            _trace(id_="t-false", kind="tension", status="open", payload={"map_excluded": False}),
            _trace(id_="t-absent", kind="tension", status="open", payload={}),
        ]
        net = build_network(traces, [], {})
        assert {n.id for n in net.nodes} == {"t-false", "t-absent"}

    def test_map_excluded_also_applies_within_build_person_network(self):
        traces = [
            _trace(id_="t-hidden", kind="tension", status="open", course_id="courseA", payload={"map_excluded": True}),
            _trace(id_="t-other", kind="tension", status="open", course_id="courseB", payload={}),
        ]
        net = build_person_network(traces, [], {})
        assert [n.id for n in net.nodes] == ["t-other"]


# ---------------------------------------------------------------------------
# ④ 決定論順
# ---------------------------------------------------------------------------


class TestDeterministicOrderingAcrossCourses:
    def test_nodes_sorted_by_created_at_then_id_across_courses(self):
        traces = [
            _trace(id_="t-c", kind="tension", status="open", course_id="courseB", created_at="2026-03-01T00:00:00Z"),
            _trace(id_="t-a", kind="tension", status="open", course_id="courseA", created_at="2026-01-01T00:00:00Z"),
            _trace(id_="t-b", kind="tension", status="open", course_id="courseA", created_at="2026-02-01T00:00:00Z"),
        ]
        net = build_person_network(traces, [], {})
        keys = [(n.created_at, n.id) for n in net.nodes]
        assert keys == sorted(keys)

    def test_output_independent_of_input_order(self):
        traces = [
            _trace(id_="t1", kind="tension", status="open", course_id="courseA", created_at="2026-01-01T00:00:00Z"),
            _trace(id_="t2", kind="tension", status="open", course_id="courseB", created_at="2026-02-01T00:00:00Z"),
            _trace(id_="t3", kind="tension", status="open", course_id="courseA", created_at="2026-03-01T00:00:00Z"),
        ]
        net_forward = build_person_network(traces, [], {})
        net_reversed = build_person_network(list(reversed(traces)), [], {})
        assert net_forward.to_dict() == net_reversed.to_dict()

    def test_edges_concatenated_in_course_id_lexicographic_order(self):
        traces = [
            _trace(
                id_="t-b", kind="tension", status="connected", course_id="courseB",
                payload={"connected_refs": {"component_ids": ["cb"], "edge_ids": []}},
            ),
            _trace(
                id_="t-a", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["ca"], "edge_ids": []}},
            ),
        ]
        net = build_person_network(traces, [], {})
        # courseA が courseB より辞書順で先なので、courseA の辺が先に来る
        ref_ids = [e.to_ref["ref_id"] for e in net.edges]
        assert ref_ids == ["ca", "cb"]


# ---------------------------------------------------------------------------
# ⑤ topic 縮退ノードが各コースの atlas binding で解決される
# ---------------------------------------------------------------------------


class TestPerCourseAtlasBinding:
    def test_topic_anchor_resolved_by_its_own_course_atlas(self):
        traces = [
            _trace(id_="t-a", kind="tension", status="open", topic_id="topic1", course_id="courseA"),
            _trace(id_="t-b", kind="tension", status="open", topic_id="topic1", course_id="courseB"),
        ]
        # 同じ topic_id="topic1" でも、コースごとに atlas binding が異なりうる
        atlas_by_course = {
            "courseA": {"topic1": "atlas_from_A"},
            "courseB": {"topic1": "atlas_from_B"},
        }
        net = build_person_network(traces, [], atlas_by_course)
        by_id = {n.id: n for n in net.nodes}
        assert by_id["t-a"].anchor.atlas_node_id == "atlas_from_A"
        assert by_id["t-b"].anchor.atlas_node_id == "atlas_from_B"

    def test_course_without_atlas_entry_falls_back_to_no_binding(self):
        traces = [
            _trace(id_="t-a", kind="tension", status="open", topic_id="topic1", course_id="courseA"),
        ]
        net = build_person_network(traces, [], {})  # atlas_by_course が空
        assert net.nodes[0].anchor.atlas_node_id is None


# ---------------------------------------------------------------------------
# ⑥ anchor_id 空のノードはグループに入らない
# ---------------------------------------------------------------------------


class TestEmptyAnchorIdExcludedFromGrouping:
    def test_topic_anchor_with_empty_topic_id_is_not_grouped(self):
        trace = _trace(
            id_="t-no-topic", kind="tension", status="open", topic_id="", course_id="courseA",
        )
        net = build_person_network([trace], [], {})
        assert len(net.nodes) == 1
        assert net.nodes[0].anchor.anchor_id == ""
        groups = group_nodes_by_anchor(net)
        assert groups == []

    def test_question_node_kind_participates_in_grouping_when_anchor_present(self):
        trace = _trace(
            id_="q-a", kind="question", status="open", course_id="courseA",
            payload={
                "structure_anchor": {
                    "attribution_source": "learner_selected",
                    "status": "active",
                    "anchor_type": "claim",
                    "anchor_id": "claim_shared",
                }
            },
        )
        net = build_person_network([trace], [], {})
        assert net.nodes[0].node_kind == NODE_KIND_QUESTION
        groups = group_nodes_by_anchor(net)
        assert len(groups) == 1
        assert groups[0]["anchor"]["anchor_id"] == "claim_shared"


# ---------------------------------------------------------------------------
# 補助: to_dict() に集計数値が漏れていないこと（PN-4、group_nodes_by_anchor も対象）
# ---------------------------------------------------------------------------


class TestNoAggregateNumbersInGrouping:
    def test_anchor_group_dicts_have_no_count_like_keys(self):
        import json

        traces = [
            _trace(
                id_="t-a", kind="tension", status="connected", course_id="courseA",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
            ),
            _trace(
                id_="t-b", kind="tension", status="connected", course_id="courseB",
                payload={"connected_refs": {"component_ids": ["c1"], "edge_ids": []}},
            ),
        ]
        net = build_person_network(traces, [], {})
        groups = group_nodes_by_anchor(net)
        blob = json.dumps(groups, ensure_ascii=False).lower()
        for forbidden in ("count", "coverage", "score", "percentage", "ranking", "weight"):
            assert forbidden not in blob, f"aggregate-looking key/value {forbidden!r} leaked"


# ---------------------------------------------------------------------------
# ⑦ `/api/me/personal-network` ルート関数の挙動（fail-closed 422・フィルタ・journey 404）。
# test_personal_graph_map_ops.py の TestRouteBehaviorViaFakeService と同型:
# ルート関数を直接呼び、モジュール名前空間に import 済みの依存を monkeypatch で差し替える
# （DB 不要）。422 の fail-closed は提案書 §5.3 / P-0.5 完了条件に関わる挙動なので、
# ガードレールのソース語彙検査だけでなく実挙動でも固定する。
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


def _person_network_two_courses():
    """courseA/courseB が同じ component アンカーへ connect した2ノードの個人ネットワーク。"""
    traces = [
        _trace(
            id_="t-a", kind="tension", status="connected", course_id="courseA",
            payload={"connected_refs": {"component_ids": ["comp-1"], "edge_ids": []}},
            created_at="2026-01-01T00:00:00Z",
        ),
        _trace(
            id_="t-b", kind="tension", status="connected", course_id="courseB",
            payload={"connected_refs": {"component_ids": ["comp-1"], "edge_ids": []}},
            created_at="2026-02-01T00:00:00Z",
        ),
    ]
    return build_person_network(traces, [], {})


class TestMePersonalNetworkRouteBehavior:
    def test_include_candidate_links_true_is_422_before_any_derivation(self, monkeypatch):
        """候補リンクの opt-in は 422 で拒否し、導出（DB 読み）にすら到達しない。"""
        from fastapi import HTTPException
        from api.routes import personal_map

        def _must_not_be_called(user_id):
            raise AssertionError("derive_person_network must not run when 422 applies")

        monkeypatch.setattr(personal_map, "derive_person_network", _must_not_be_called)
        with pytest.raises(HTTPException) as exc_info:
            personal_map.get_me_personal_network(
                include_candidate_links=True, current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 422

    def test_unknown_context_type_is_422(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import personal_map

        monkeypatch.setattr(
            personal_map, "derive_person_network",
            lambda user_id: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        with pytest.raises(HTTPException) as exc_info:
            personal_map.get_me_personal_network(
                context_type="document", context_id="d1", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 422

    def test_context_type_without_context_id_is_422(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import personal_map

        monkeypatch.setattr(
            personal_map, "derive_person_network",
            lambda user_id: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        with pytest.raises(HTTPException) as exc_info:
            personal_map.get_me_personal_network(
                context_type="course", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 422

    def test_success_bundles_anchor_groups_and_keeps_titleless_course_nodes(self, monkeypatch):
        """タイトルが引けないコース（削除済み等）のノードも残る（P-0.5 完了条件）。"""
        from api.routes import personal_map

        monkeypatch.setattr(
            personal_map, "derive_person_network",
            lambda user_id: _person_network_two_courses(),
        )
        monkeypatch.setattr(
            personal_map, "fetch_course_titles",
            lambda course_ids: {"courseA": "コースA"},  # courseB は削除済み想定
        )
        result = personal_map.get_me_personal_network(current_user={"id": "user-1"})

        assert set(result.keys()) == {"nodes", "edges", "anchor_groups", "courses"}
        assert [n["id"] for n in result["nodes"]] == ["t-a", "t-b"]
        assert result["courses"] == {"courseA": {"title": "コースA"}}
        # courseB のノードは courses にタイトルが無くても消えない
        assert any(n["course_id"] == "courseB" for n in result["nodes"])
        # 同一アンカーの2コースが1グループに束なる（§5.2）
        assert len(result["anchor_groups"]) == 1
        assert result["anchor_groups"][0]["course_ids"] == ["courseA", "courseB"]

    def test_context_course_filter_projects_to_single_course(self, monkeypatch):
        from api.routes import personal_map

        monkeypatch.setattr(
            personal_map, "derive_person_network",
            lambda user_id: _person_network_two_courses(),
        )
        monkeypatch.setattr(personal_map, "fetch_course_titles", lambda course_ids: {})
        result = personal_map.get_me_personal_network(
            context_type="course", context_id="courseB", current_user={"id": "user-1"},
        )
        assert [n["id"] for n in result["nodes"]] == ["t-b"]
        # edges も絞り込み後の node 集合に from が含まれるものだけ
        assert all(e["from_node_id"] == "t-b" for e in result["edges"])

    def test_focus_anchor_id_filter(self, monkeypatch):
        from api.routes import personal_map

        monkeypatch.setattr(
            personal_map, "derive_person_network",
            lambda user_id: _person_network_two_courses(),
        )
        monkeypatch.setattr(personal_map, "fetch_course_titles", lambda course_ids: {})
        hit = personal_map.get_me_personal_network(
            focus_anchor_id="comp-1", current_user={"id": "user-1"},
        )
        miss = personal_map.get_me_personal_network(
            focus_anchor_id="comp-none", current_user={"id": "user-1"},
        )
        assert len(hit["nodes"]) == 2
        assert miss["nodes"] == [] and miss["anchor_groups"] == []


class TestMeJourneyRouteBehavior:
    def test_journey_passthrough_and_404(self, monkeypatch):
        """journey_for_person_node の結果をそのまま返し、None は 404 に写像する。
        （関数内 import のため、正本モジュール側の属性を差し替える。）"""
        from fastapi import HTTPException
        from api.routes import personal_map
        import core.personal_graph.journey as journey_mod

        payload = {"steps": [], "frontier_note": None, "truncated": False}
        monkeypatch.setattr(
            journey_mod, "journey_for_person_node",
            lambda user_id, node_id, can_view_document=None: payload,
        )
        assert personal_map.get_me_personal_network_journey(
            node_id="n1", current_user={"id": "user-1"},
        ) == payload

        monkeypatch.setattr(
            journey_mod, "journey_for_person_node",
            lambda user_id, node_id, can_view_document=None: None,
        )
        with pytest.raises(HTTPException) as exc_info:
            personal_map.get_me_personal_network_journey(
                node_id="missing", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# N16（2026-07-17）: 本人スコープでの reconstruction claim → topic 解決
# （claim_topic_by_course はコースごとに独立して効く）
# ---------------------------------------------------------------------------


class TestPersonScopeClaimTopicResolution:
    def test_claim_topic_by_course_is_applied_per_course(self):
        reconstructions = [
            _recon(id_="r-a", claim_id="claimX", course_id="courseA",
                   created_at="2026-01-01T00:00:00Z"),
            _recon(id_="r-b", claim_id="claimX", course_id="courseB",
                   created_at="2026-01-02T00:00:00Z"),
        ]
        atlas_by_course = {"courseA": {"topicA1": "atlas_a1"}}
        claim_topic_by_course = {"courseA": {"claimX": "topicA1"}}  # courseB には無い
        net = build_person_network([], reconstructions, atlas_by_course, claim_topic_by_course)
        by_id = {n.id: n for n in net.nodes}
        assert by_id["r-a"].topic_id == "topicA1"
        assert by_id["r-a"].anchor.atlas_node_id == "atlas_a1"
        # courseB 側はマップが無いので従来どおり未解決（fail-closed / P4）
        assert by_id["r-b"].topic_id is None
        assert by_id["r-b"].anchor.atlas_node_id is None

    def test_claim_topic_by_course_omitted_is_backward_compatible(self):
        reconstructions = [_recon(id_="r-a", claim_id="claimX", course_id="courseA")]
        net = build_person_network([], reconstructions, {})
        assert net.nodes[0].topic_id is None
        assert net.nodes[0].anchor.atlas_node_id is None
