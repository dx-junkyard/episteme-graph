"""分野マップの関係表示（RE層）— core の振る舞いテスト。

正本: ``docs/features/atlas_relation_edges_design.md`` §4 / §6（不変条項 RE1〜RE8 は §2）。

検査観点:
  1. edge_key の正準化（無向・版非依存）と fail-safe な parse
  2. vector 由来の導出（閾値・除外規則・決定論の並び・段階ラベル）
  3. co_occurrence 由来の導出（反復閾値・タイトル列挙・件数非漏洩）
  4. 候補合成（origins のマージ・非該当キーの不在・並び）
  5. 判断の遷移が CandidateFlow を通る（採用の種別必須・見送りの理由必須・
     restore の対象なし・監査の語彙）
  6. 凍結との接続（空入力で SQL 非発行 = fail-closed / 下書きにある辺の除外）
  7. patch のガード（種別・端点・自己ループ・重複）と apply_json_patch での適用可能性
  8. 推定の糸（上限・見送り除外・キャッシュ・fail-soft）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas as atlas_module  # noqa: E402
from core.atlas_edges import derive, patching, schema, store, threads  # noqa: E402
from core.atlas_generator import apply_json_patch  # noqa: E402
from core.candidate_flow import CandidateTransitionError  # noqa: E402
from core.label_vocab import ANCHOR_NEARNESS_SCALE  # noqa: E402

ACTOR = "11111111-1111-1111-1111-111111111111"
DOMAIN = "astro_physics"


# ---------------------------------------------------------------------------
# テストダブル
# ---------------------------------------------------------------------------


class FakeAnchor:
    """``core.atlas_vectors.store.AnchorVector`` の最小の代役。"""

    def __init__(self, node_id, vector, *, node_kind="concept", label="", region_id=""):
        self.node_id = node_id
        self.node_kind = node_kind
        self.label = label or node_id
        self.region_id = region_id
        self.region_label = region_id
        self.vector = list(vector) if vector else None


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """SQL 本文の部分一致で応答を返すセッション（実行履歴を保持する）。"""

    def __init__(self, handlers=None):
        self.handlers = list(handlers or [])
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        for needle, rows in self.handlers:
            if needle in sql:
                return FakeResult(rows(sql, params) if callable(rows) else rows)
        return FakeResult([])

    def sql_texts(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def skeleton(*, regions, edges=(), version="v1"):
    return atlas_module.AtlasSkeleton(
        cartridge=DOMAIN,
        status="frozen",
        version=version,
        regions=tuple(
            atlas_module.SkeletonRegion(
                id=region_id,
                label=region_id.upper(),
                concepts=tuple(
                    atlas_module.SkeletonConcept(id=c, label=c.upper())
                    for c in concepts
                ),
            )
            for region_id, concepts in regions
        ),
        edges=tuple(
            atlas_module.SkeletonEdge(from_id=a, to_id=b, kind=k) for a, b, k in edges
        ),
    )


SKELETON = skeleton(
    regions=[("r1", ["a", "b"]), ("r2", ["c", "d"])],
)

#: cosine が 1.0 / 0.0 になるだけの単純なベクトル（生値は DTO に出ない）。
V_X = [1.0, 0.0, 0.0]
V_Y = [0.0, 1.0, 0.0]


def decision_row(
    edge_key,
    *,
    status=schema.DECISION_STATUS_CANDIDATE,
    edge_kind="",
    review_note="",
    applied_version="",
):
    return (
        "00000000-0000-0000-0000-000000000001",
        edge_key,
        status,
        edge_kind,
        review_note,
        applied_version,
        None,
        None,
        None,
        None,
    )


# ---------------------------------------------------------------------------
# 1. schema
# ---------------------------------------------------------------------------


class TestEdgeKey:
    def test_is_undirected(self):
        assert schema.build_edge_key(DOMAIN, "b", "a") == schema.build_edge_key(
            DOMAIN, "a", "b"
        )

    def test_format_is_the_documented_one(self):
        assert schema.build_edge_key(DOMAIN, "b", "a") == f"edge|{DOMAIN}|a|b"

    def test_does_not_carry_the_skeleton_version(self):
        """版非依存（凍結のたびに却下済み候補が蘇らない = §4.2 裁定の継承）。"""
        assert "v1" not in schema.build_edge_key(DOMAIN, "a", "b")

    def test_round_trip(self):
        key = schema.build_edge_key(DOMAIN, "z_node", "a_node")
        assert schema.parse_edge_key(key) == (DOMAIN, "a_node", "z_node")

    @pytest.mark.parametrize(
        "value", ["", "gap|d|r|x", "edge|d|a", "edge|d|a|b|c", "edge|d||b"]
    )
    def test_malformed_keys_are_fail_safe(self, value):
        assert schema.parse_edge_key(value) == ("", "", "")

    def test_domain_prefix_ends_with_the_separator(self):
        prefix = schema.edge_key_domain_prefix(DOMAIN)
        assert prefix == f"edge|{DOMAIN}|"
        assert schema.build_edge_key(DOMAIN, "a", "b").startswith(prefix)

    def test_undirected_pair_sorts_and_strips(self):
        assert schema.undirected_pair(" b ", "a") == ("a", "b")


class TestDecisionVocabulary:
    def test_statuses(self):
        assert schema.DECISION_STATUSES == ("candidate", "accepted", "dismissed")

    def test_labels_match_the_gap_wording(self):
        from core.atlas_gaps import schema as gap_schema

        for status in schema.DECISION_STATUSES:
            assert schema.decision_status_label(status) == (
                gap_schema.decision_status_label(status)
            )

    def test_unknown_status_is_returned_as_is(self):
        assert schema.decision_status_label("merged") == "merged"


# ---------------------------------------------------------------------------
# 2. vector 由来
# ---------------------------------------------------------------------------


class TestDeriveVectorPairs:
    def test_near_pair_across_regions_is_a_candidate(self):
        pairs = derive.derive_vector_pairs(
            SKELETON, [FakeAnchor("a", V_X), FakeAnchor("c", V_X)]
        )
        assert [(p["from_id"], p["to_id"]) for p in pairs] == [("a", "c")]
        assert pairs[0]["from_label"] == "A"
        assert pairs[0]["nearness_label"] == ANCHOR_NEARNESS_SCALE.label_for(1.0)

    def test_far_pair_is_dropped(self):
        pairs = derive.derive_vector_pairs(
            SKELETON, [FakeAnchor("a", V_X), FakeAnchor("c", V_Y)]
        )
        assert pairs == []

    def test_same_region_pair_is_excluded(self):
        pairs = derive.derive_vector_pairs(
            SKELETON, [FakeAnchor("a", V_X), FakeAnchor("b", V_X)]
        )
        assert pairs == []

    def test_existing_skeleton_edge_is_excluded_undirected(self):
        sk = skeleton(
            regions=[("r1", ["a"]), ("r2", ["c"])], edges=[("c", "a", "related")]
        )
        pairs = derive.derive_vector_pairs(
            sk, [FakeAnchor("a", V_X), FakeAnchor("c", V_X)]
        )
        assert pairs == []

    def test_region_anchors_are_excluded(self):
        pairs = derive.derive_vector_pairs(
            SKELETON,
            [FakeAnchor("r1", V_X, node_kind="region"), FakeAnchor("c", V_X)],
        )
        assert pairs == []

    def test_nodes_absent_from_the_skeleton_are_excluded(self):
        pairs = derive.derive_vector_pairs(
            SKELETON, [FakeAnchor("ghost", V_X), FakeAnchor("c", V_X)]
        )
        assert pairs == []

    def test_anchors_without_vectors_are_skipped(self):
        pairs = derive.derive_vector_pairs(
            SKELETON, [FakeAnchor("a", None), FakeAnchor("c", V_X)]
        )
        assert pairs == []

    def test_order_is_deterministic_by_similarity_then_id(self):
        sk = skeleton(regions=[("r1", ["a"]), ("r2", ["c", "d"])])
        anchors = [
            FakeAnchor("a", [1.0, 0.0]),
            FakeAnchor("c", [0.8, 0.6]),  # cosine 0.8
            FakeAnchor("d", [1.0, 0.0]),  # cosine 1.0
        ]
        pairs = derive.derive_vector_pairs(sk, anchors)
        assert [(p["from_id"], p["to_id"]) for p in pairs] == [("a", "d"), ("a", "c")]
        # 入力順を変えても結果は同じ（決定論）
        assert derive.derive_vector_pairs(sk, list(reversed(anchors))) == pairs


# ---------------------------------------------------------------------------
# 3. co_occurrence 由来
# ---------------------------------------------------------------------------


def placement_session(rows):
    return FakeSession([("landscape_placements", rows)])


class TestDeriveCoOccurrencePairs:
    def test_two_shared_documents_make_a_candidate(self):
        session = placement_session(
            [
                ("d2", "Zeta paper", "a"),
                ("d1", "Alpha paper", "a"),
                ("d1", "Alpha paper", "c"),
                ("d2", "Zeta paper", "c"),
            ]
        )
        pairs = derive.derive_co_occurrence_pairs(
            session, domain_key=DOMAIN, skeleton=SKELETON
        )
        assert len(pairs) == 1
        assert (pairs[0]["from_id"], pairs[0]["to_id"]) == ("a", "c")
        assert pairs[0]["documents"] == [
            {"document_id": "d1", "title": "Alpha paper"},
            {"document_id": "d2", "title": "Zeta paper"},
        ]

    def test_single_shared_document_is_below_the_threshold(self):
        session = placement_session([("d1", "Alpha", "a"), ("d1", "Alpha", "c")])
        assert schema.MIN_DOCUMENTS_FOR_EDGE == 2
        assert (
            derive.derive_co_occurrence_pairs(
                session, domain_key=DOMAIN, skeleton=SKELETON
            )
            == []
        )

    def test_dead_placement_statuses_are_excluded_in_sql(self):
        session = placement_session([])
        derive.derive_co_occurrence_pairs(
            session, domain_key=DOMAIN, skeleton=SKELETON
        )
        _, params = session.calls[0]
        assert set(params["excluded_statuses"]) == {"superseded", "rejected"}

    def test_no_domain_means_no_sql(self):
        session = placement_session([("d1", "Alpha", "a")])
        assert (
            derive.derive_co_occurrence_pairs(session, domain_key="", skeleton=SKELETON)
            == []
        )
        assert session.calls == []

    def test_same_region_and_unknown_nodes_are_excluded(self):
        session = placement_session(
            [
                ("d1", "Alpha", "a"),
                ("d1", "Alpha", "b"),
                ("d1", "Alpha", "ghost"),
                ("d2", "Beta", "a"),
                ("d2", "Beta", "b"),
                ("d2", "Beta", "ghost"),
            ]
        )
        assert (
            derive.derive_co_occurrence_pairs(
                session, domain_key=DOMAIN, skeleton=SKELETON
            )
            == []
        )


# ---------------------------------------------------------------------------
# 4. 候補合成
# ---------------------------------------------------------------------------


class TestDeriveEdgeCandidates:
    def test_merges_origins_and_keeps_keys_conditional(self):
        session = placement_session(
            [
                ("d1", "Alpha", "a"),
                ("d1", "Alpha", "c"),
                ("d2", "Beta", "a"),
                ("d2", "Beta", "c"),
            ]
        )
        out = derive.derive_edge_candidates(
            session,
            domain_key=DOMAIN,
            skeleton=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
        )
        assert len(out) == 1
        item = out[0]
        assert item["origins"] == ["co_occurrence", "vector"]
        assert item["edge_key"] == schema.build_edge_key(DOMAIN, "a", "c")
        assert item["domain_key"] == DOMAIN
        assert item["skeleton_version"] == "v1"
        assert "nearness_label" in item and "documents" in item

    def test_vector_only_candidate_has_no_documents_key(self):
        session = placement_session([])
        out = derive.derive_edge_candidates(
            session,
            domain_key=DOMAIN,
            skeleton=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
        )
        assert out[0]["origins"] == ["vector"]
        assert "documents" not in out[0]

    def test_co_occurrence_only_candidate_has_no_nearness_key(self):
        session = placement_session(
            [
                ("d1", "Alpha", "a"),
                ("d1", "Alpha", "c"),
                ("d2", "Beta", "a"),
                ("d2", "Beta", "c"),
            ]
        )
        out = derive.derive_edge_candidates(
            session, domain_key=DOMAIN, skeleton=SKELETON, anchors=[]
        )
        assert out[0]["origins"] == ["co_occurrence"]
        assert "nearness_label" not in out[0]

    def test_no_raw_numbers_leak_into_the_dto(self):
        session = placement_session([])
        out = derive.derive_edge_candidates(
            session,
            domain_key=DOMAIN,
            skeleton=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
        )
        assert set(out[0]) == {
            "edge_key",
            "domain_key",
            "from_id",
            "from_label",
            "to_id",
            "to_label",
            "origins",
            "nearness_label",
            "skeleton_version",
        }

    def test_sorted_by_node_ids(self):
        sk = skeleton(regions=[("r1", ["a"]), ("r2", ["c", "d"])])
        out = derive.derive_edge_candidates(
            placement_session([]),
            domain_key=DOMAIN,
            skeleton=sk,
            anchors=[
                FakeAnchor("a", [1.0, 0.0]),
                FakeAnchor("c", [0.8, 0.6]),
                FakeAnchor("d", [1.0, 0.0]),
            ],
        )
        assert [(i["from_id"], i["to_id"]) for i in out] == [("a", "c"), ("a", "d")]

    def test_empty_skeleton_is_fail_closed(self):
        session = placement_session([("d1", "Alpha", "a")])
        assert (
            derive.derive_edge_candidates(
                session, domain_key=DOMAIN, skeleton=skeleton(regions=[]), anchors=[]
            )
            == []
        )
        assert session.calls == []


# ---------------------------------------------------------------------------
# 5. 判断（CandidateFlow 経由）
# ---------------------------------------------------------------------------


KEY = schema.build_edge_key(DOMAIN, "a", "c")


def decide_session(current_row, updated_row=None):
    """SELECT → （INSERT DO NOTHING）→ UPDATE を模す。"""
    return FakeSession(
        [
            ("UPDATE atlas_edge_decisions", [updated_row] if updated_row else []),
            ("INSERT INTO atlas_edge_decisions", []),
            ("SELECT", [current_row] if current_row else []),
        ]
    )


class TestDecide:
    def test_accept_requires_a_valid_edge_kind(self):
        session = decide_session(decision_row(KEY))
        with pytest.raises(ValueError, match="invalid edge kind"):
            store.decide(
                session,
                edge_key=KEY,
                action="accept",
                actor_id=ACTOR,
                edge_kind="neighbour",
                record_audit=Mock(),
            )
        assert session.calls == []

    def test_accept_writes_the_kind_and_records_the_audit(self):
        updated = decision_row(KEY, status="accepted", edge_kind="related")
        session = decide_session(decision_row(KEY), updated)
        audit = Mock()
        out = store.decide(
            session,
            edge_key=KEY,
            action="accept",
            actor_id=ACTOR,
            edge_kind="related",
            record_audit=audit,
        )
        assert out["action"] == "accept"
        assert out["new_status"] == "accepted"
        assert out["decision"]["edge_kind"] == "related"
        kwargs = audit.call_args.kwargs
        assert kwargs["entity_type"] == "atlas_edge"
        assert kwargs["action"] == "accept"
        assert kwargs["entity_id"] == KEY
        assert kwargs["actor_id"] == ACTOR

    def test_dismiss_requires_a_reason(self):
        session = decide_session(decision_row(KEY))
        with pytest.raises(CandidateTransitionError):
            store.decide(
                session,
                edge_key=KEY,
                action="dismiss",
                actor_id=ACTOR,
                record_audit=Mock(),
            )

    def test_dismiss_with_a_reason_transitions(self):
        updated = decision_row(KEY, status="dismissed", review_note="別物です")
        session = decide_session(decision_row(KEY), updated)
        audit = Mock()
        out = store.decide(
            session,
            edge_key=KEY,
            action="dismiss",
            actor_id=ACTOR,
            review_note="別物です",
            record_audit=audit,
        )
        assert out["new_status"] == "dismissed"
        assert audit.call_args.kwargs["action"] == "dismiss"

    def test_actor_is_required(self):
        session = decide_session(decision_row(KEY))
        with pytest.raises(CandidateTransitionError):
            store.decide(
                session,
                edge_key=KEY,
                action="accept",
                actor_id="",
                edge_kind="related",
                record_audit=Mock(),
            )

    def test_restore_without_a_row_returns_none_and_creates_nothing(self):
        session = decide_session(None)
        assert (
            store.decide(
                session,
                edge_key=KEY,
                action="restore",
                actor_id=ACTOR,
                record_audit=Mock(),
            )
            is None
        )
        assert "INSERT INTO" not in session.sql_texts()

    def test_restore_from_dismissed(self):
        updated = decision_row(KEY, status="candidate", review_note="別物です")
        session = decide_session(decision_row(KEY, status="dismissed"), updated)
        audit = Mock()
        out = store.decide(
            session,
            edge_key=KEY,
            action="restore",
            actor_id=ACTOR,
            record_audit=audit,
        )
        assert out["new_status"] == "candidate"
        assert audit.call_args.kwargs["action"] == "restore"

    def test_re_accepting_an_accepted_edge_is_refused(self):
        session = decide_session(decision_row(KEY, status="accepted"))
        with pytest.raises(CandidateTransitionError):
            store.decide(
                session,
                edge_key=KEY,
                action="accept",
                actor_id=ACTOR,
                edge_kind="related",
                record_audit=Mock(),
            )

    def test_unknown_action(self):
        with pytest.raises(ValueError, match="invalid action"):
            store.decide(
                decide_session(decision_row(KEY)),
                edge_key=KEY,
                action="delete",
                actor_id=ACTOR,
                record_audit=Mock(),
            )

    def test_empty_key(self):
        with pytest.raises(ValueError, match="edge_key is required"):
            store.decide(
                FakeSession(),
                edge_key=" ",
                action="accept",
                actor_id=ACTOR,
                edge_kind="related",
                record_audit=Mock(),
            )

    def test_lost_update_is_reported_not_silently_ignored(self):
        session = decide_session(decision_row(KEY), None)
        with pytest.raises(ValueError):
            store.decide(
                session,
                edge_key=KEY,
                action="accept",
                actor_id=ACTOR,
                edge_kind="related",
                record_audit=Mock(),
            )


class TestMergeDecisions:
    def test_dismissed_is_hidden_by_default_and_restorable(self):
        candidates = [{"edge_key": KEY, "from_id": "a", "to_id": "c"}]
        decisions = {KEY: {"edge_key": KEY, "status": "dismissed"}}
        assert store.merge_decisions_into(candidates, decisions) == []
        merged = store.merge_decisions_into(
            candidates, decisions, include_dismissed=True
        )
        assert merged[0]["decision"]["status"] == "dismissed"

    def test_input_is_not_mutated(self):
        candidates = [{"edge_key": KEY}]
        store.merge_decisions_into(
            candidates, {KEY: {"edge_key": KEY, "status": "accepted"}}
        )
        assert candidates == [{"edge_key": KEY}]


# ---------------------------------------------------------------------------
# 6. 凍結との接続
# ---------------------------------------------------------------------------


class TestFreezeIntegration:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"domain_key": "", "frozen_version": "v2", "frozen_edge_pairs": [("a", "c")]},
            {"domain_key": DOMAIN, "frozen_version": "", "frozen_edge_pairs": [("a", "c")]},
            {"domain_key": DOMAIN, "frozen_version": "v2", "frozen_edge_pairs": []},
            {"domain_key": DOMAIN, "frozen_version": "v2", "frozen_edge_pairs": [("a", "a")]},
        ],
    )
    def test_stamp_is_fail_closed_on_empty_input(self, kwargs):
        session = FakeSession()
        assert store.stamp_applied_versions(session, **kwargs) == []
        assert session.calls == []

    def test_stamp_uses_undirected_keys(self):
        session = FakeSession([("UPDATE atlas_edge_decisions", [(KEY,)])])
        stamped = store.stamp_applied_versions(
            session,
            domain_key=DOMAIN,
            frozen_version="v2",
            frozen_edge_pairs=[{"from": "c", "to": "a"}],
        )
        assert stamped == [KEY]
        _, params = session.calls[0]
        assert params["keys"] == [KEY]
        assert params["version"] == "v2"

    def test_pending_excludes_edges_already_in_the_draft(self):
        other = schema.build_edge_key(DOMAIN, "b", "d")
        session = FakeSession(
            [
                (
                    "SELECT",
                    [
                        decision_row(KEY, status="accepted", edge_kind="related"),
                        decision_row(other, status="accepted", edge_kind="depends"),
                    ],
                )
            ]
        )
        pending = store.list_pending_for_freeze(
            session, domain_key=DOMAIN, draft_edge_pairs=[("c", "a")]
        )
        assert [p["edge_key"] for p in pending] == [other]
        assert pending[0] == {
            "edge_key": other,
            "from_id": "b",
            "to_id": "d",
            "edge_kind": "depends",
            "review_note": "",
        }

    def test_pending_without_domain_issues_no_sql(self):
        session = FakeSession()
        assert store.list_pending_for_freeze(
            session, domain_key="", draft_edge_pairs=[]
        ) == []
        assert session.calls == []

    def test_dismissed_edge_keys(self):
        session = FakeSession([("SELECT edge_key", [(KEY,), ("",)])])
        assert store.dismissed_edge_keys(session, DOMAIN) == {KEY}
        assert store.dismissed_edge_keys(session, "") == set()


# ---------------------------------------------------------------------------
# 7. patching
# ---------------------------------------------------------------------------


DRAFT = {
    "atlas_skeleton": {
        "version": "v2",
        "regions": [
            {"id": "r1", "label": "領域1", "concepts": [{"id": "a", "label": "A"}]},
            {"id": "r2", "label": "領域2", "concepts": [{"id": "c", "label": "C"}]},
        ],
        "edges": [],
    }
}


class TestBuildEdgePatch:
    def test_add_only_and_deterministic_value(self):
        out = patching.build_edge_patch(DRAFT, from_id="c", to_id="a", kind="related")
        assert out["from_id"] == "a" and out["to_id"] == "c"
        assert len(out["patch"]) == 1
        op = out["patch"][0]
        assert op["op"] == "add"
        assert op["path"] == "/atlas_skeleton/edges/-"
        assert json.loads(op["value_json"]) == {
            "from": "a",
            "to": "c",
            "kind": "related",
        }
        assert "関連" in out["summary"]

    def test_bare_skeleton_dict_gets_a_root_relative_path(self):
        out = patching.build_edge_patch(
            DRAFT["atlas_skeleton"], from_id="a", to_id="c", kind="adjacent"
        )
        assert out["patch"][0]["path"] == "/edges/-"

    def test_apply_json_patch_appends_the_edge(self):
        """既存の ``apply_json_patch`` がそのまま適用できる（専用 apply は作らない）。"""
        out = patching.build_edge_patch(DRAFT, from_id="a", to_id="c", kind="depends")
        patched = apply_json_patch(DRAFT, out["patch"])
        assert patched["atlas_skeleton"]["edges"] == [
            {"from": "a", "to": "c", "kind": "depends"}
        ]
        # 入力は変更しない（非破壊）
        assert DRAFT["atlas_skeleton"]["edges"] == []

    @pytest.mark.parametrize("kind", ["", "neighbour", "ADJACENT"])
    def test_invalid_kind(self, kind):
        with pytest.raises(patching.EdgePatchError):
            patching.build_edge_patch(DRAFT, from_id="a", to_id="c", kind=kind)

    def test_self_loop(self):
        with pytest.raises(patching.EdgePatchError, match="同じノード"):
            patching.build_edge_patch(DRAFT, from_id="a", to_id="a", kind="related")

    def test_missing_endpoint(self):
        with pytest.raises(patching.EdgePatchError, match="ないノード"):
            patching.build_edge_patch(DRAFT, from_id="a", to_id="ghost", kind="related")

    def test_duplicate_undirected_pair(self):
        draft = json.loads(json.dumps(DRAFT))
        draft["atlas_skeleton"]["edges"] = [
            {"from": "c", "to": "a", "kind": "adjacent"}
        ]
        with pytest.raises(patching.EdgePatchError, match="すでに"):
            patching.build_edge_patch(draft, from_id="a", to_id="c", kind="related")

    def test_non_mapping_draft(self):
        with pytest.raises(patching.EdgePatchError):
            patching.build_edge_patch([], from_id="a", to_id="c", kind="related")

    def test_every_edge_kind_is_accepted(self):
        for kind in atlas_module.EDGE_KINDS:
            out = patching.build_edge_patch(DRAFT, from_id="a", to_id="c", kind=kind)
            assert out["kind"] == kind


# ---------------------------------------------------------------------------
# 8. 推定の糸
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_thread_cache():
    threads.reset_cache()
    yield
    threads.reset_cache()


def patch_sources(monkeypatch, *, sk, anchors, version="v1"):
    from core import atlas_store
    from core.atlas_vectors import builder

    monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: sk)
    monkeypatch.setattr(
        builder, "anchors_with_labels", lambda s, d, v="": (list(anchors), version)
    )


class TestThreads:
    def test_items_carry_only_the_documented_keys(self, monkeypatch):
        patch_sources(
            monkeypatch,
            sk=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
        )
        out = threads.threads_for_domain(FakeSession(), DOMAIN)
        assert out["available"] is True
        assert out["skeleton_version"] == "v1"
        assert set(out["items"][0]) == {
            "from",
            "to",
            "from_label",
            "to_label",
            "nearness_label",
        }

    def test_dismissed_edges_disappear(self, monkeypatch):
        patch_sources(
            monkeypatch,
            sk=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
        )
        session = FakeSession([("SELECT edge_key", [(KEY,)])])
        out = threads.threads_for_domain(session, DOMAIN)
        assert out == {"available": True, "skeleton_version": "v1", "items": []}

    def test_per_node_and_total_caps(self, monkeypatch):
        # 1つの region に 1 concept、他の region に多数 — a から伸びる糸は 2 本まで。
        sk = skeleton(regions=[("r1", ["a"]), ("r2", ["c", "d", "e"])])
        anchors = [
            FakeAnchor("a", [1.0, 0.0]),
            FakeAnchor("c", [1.0, 0.0]),
            FakeAnchor("d", [0.99, 0.1]),
            FakeAnchor("e", [0.98, 0.2]),
        ]
        patch_sources(monkeypatch, sk=sk, anchors=anchors)
        out = threads.threads_for_domain(FakeSession(), DOMAIN)
        from_a = [i for i in out["items"] if "a" in (i["from"], i["to"])]
        assert len(from_a) == schema.THREADS_MAX_PER_NODE

    def test_total_cap_is_enforced(self, monkeypatch):
        left = [f"l{i:02d}" for i in range(40)]
        right = [f"r{i:02d}" for i in range(40)]
        sk = skeleton(regions=[("r1", left), ("r2", right)])
        anchors = [FakeAnchor(n, V_X) for n in left + right]
        patch_sources(monkeypatch, sk=sk, anchors=anchors)
        out = threads.threads_for_domain(FakeSession(), DOMAIN)
        assert len(out["items"]) == schema.THREADS_MAX_TOTAL

    def test_no_anchors_is_unavailable(self, monkeypatch):
        patch_sources(monkeypatch, sk=SKELETON, anchors=[])
        assert threads.threads_for_domain(FakeSession(), DOMAIN) == {"available": False}

    def test_no_skeleton_is_unavailable(self, monkeypatch):
        patch_sources(monkeypatch, sk=None, anchors=[])
        assert threads.threads_for_domain(FakeSession(), DOMAIN) == {"available": False}

    def test_stale_anchor_version_is_unavailable(self, monkeypatch):
        patch_sources(
            monkeypatch,
            sk=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
            version="v0",
        )
        assert threads.threads_for_domain(FakeSession(), DOMAIN) == {"available": False}

    def test_failures_are_soft(self, monkeypatch):
        from core import atlas_store

        def _boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", _boom)
        assert threads.threads_for_domain(FakeSession(), DOMAIN) == {"available": False}

    def test_empty_domain_is_unavailable(self):
        assert threads.threads_for_domain(FakeSession(), "") == {"available": False}

    def test_pairs_are_cached_per_domain_and_version(self, monkeypatch):
        calls = {"n": 0}
        real = derive.derive_vector_pairs

        def _counted(sk, anchors):
            calls["n"] += 1
            return real(sk, anchors)

        patch_sources(
            monkeypatch,
            sk=SKELETON,
            anchors=[FakeAnchor("a", V_X), FakeAnchor("c", V_X)],
        )
        monkeypatch.setattr(derive, "derive_vector_pairs", _counted)
        threads.threads_for_domain(FakeSession(), DOMAIN)
        threads.threads_for_domain(FakeSession(), DOMAIN)
        assert calls["n"] == 1
        threads.reset_cache()
        threads.threads_for_domain(FakeSession(), DOMAIN)
        assert calls["n"] == 2
