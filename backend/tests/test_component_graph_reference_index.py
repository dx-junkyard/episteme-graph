"""理論グラフの根拠リンク解決用 ``reference_index`` のテスト。

原稿スタジオの理論グラフ（``GET /api/admin/documents/{document_id}/component-graph``）の
edge/node は以下のパイプライン内部 ID を持つが、DB テーブルを持たないためフロントで
解決できない:

- ``linked_claim_ids`` / ``evidence_claim_ids``: ClaimObjectBuilder の atomic claim ID
  （``theory_claims.source_scope.span_id`` / ``legacy_ids`` で解決可能）。
- ``linked_evidence_ids`` / ``source_evidence_ids``: EvidenceRegistryBuilder の
  ``ev_NNNN`` ID（DB テーブルなし。``document_analysis_runs.stage_outputs`` の
  ``evidence_registry`` ステージ出力にのみ存在）。
- ``linked_derivation_ids`` / ``evidence_derivation_ids``: DerivationChainAgent の
  derivation_id / step_id（同じく ``derivation_chain`` ステージ出力にのみ存在）。

``routes/theory_components.py`` に実装した ``_build_graph_reference_index`` と、その
内部で使う純粋関数群（``_collect_graph_reference_ids`` /
``_resolve_claim_reference_index`` / ``_resolve_evidence_reference_index`` /
``_resolve_derivation_reference_index``）を検証する。DB 接続は行わず、
``_pg_session`` / ``document_run_artifacts`` を monkeypatch する
（``test_component_candidates_failure.py`` と同じ monkeypatch 流儀）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.theory_components as tc  # noqa: E402


_CLAIM_UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_CLAIM_UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _FakeClaimsSession:
    """``SELECT id, text, source_scope FROM theory_claims WHERE document_id = ...`` を模擬する。"""

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, _stmt, _params):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class _RaisingSession:
    def execute(self, _stmt, _params):
        raise RuntimeError("db unavailable")

    def close(self):
        pass


# ---------------------------------------------------------------------------
# _collect_graph_reference_ids: 参照済み ID のみを集める（stage_outputs全体を載せない）
# ---------------------------------------------------------------------------


class TestCollectGraphReferenceIds:
    def test_collects_from_node_top_level_fields(self):
        graph_payload = {
            "nodes": [
                {
                    "component_id": "n1",
                    "linked_claim_ids": ["claim_a"],
                    "linked_evidence_ids": ["ev_1"],
                    "linked_derivation_ids": ["der_1"],
                },
                {"component_id": "n2"},  # 参照 ID なしのノードは無視される（例外にしない）
            ],
            "edges": [],
        }
        claim_ids, evidence_ids, derivation_ids = tc._collect_graph_reference_ids(graph_payload)
        assert claim_ids == {"claim_a"}
        assert evidence_ids == {"ev_1"}
        assert derivation_ids == {"der_1"}

    def test_collects_from_edge_top_level_and_nested_evidence_dict(self):
        graph_payload = {
            "nodes": [],
            "edges": [
                {
                    "source_component_id": "n1",
                    "target_component_id": "n2",
                    "evidence_claim_ids": ["claim_b"],
                    "evidence_derivation_ids": ["der_2"],
                    "source_evidence_ids": ["ev_2"],
                    "evidence": {
                        "evidence_claim_ids": ["claim_c"],
                        "evidence_derivation_ids": ["der_3"],
                        "source_evidence_ids": ["ev_3"],
                        "reason": "not an id list, must be ignored",
                    },
                },
                {"source_component_id": "n2", "target_component_id": "n1"},  # 参照なしエッジ
            ],
        }
        claim_ids, evidence_ids, derivation_ids = tc._collect_graph_reference_ids(graph_payload)
        assert claim_ids == {"claim_b", "claim_c"}
        assert evidence_ids == {"ev_2", "ev_3"}
        assert derivation_ids == {"der_2", "der_3"}

    def test_malformed_payload_does_not_raise(self):
        assert tc._collect_graph_reference_ids({}) == (set(), set(), set())
        assert tc._collect_graph_reference_ids({"nodes": "not-a-list", "edges": None}) == (
            set(),
            set(),
            set(),
        )


# ---------------------------------------------------------------------------
# _resolve_claim_reference_index: theory_claims (source_scope.span_id / legacy_ids)
# ---------------------------------------------------------------------------


class TestResolveClaimReferenceIndex:
    def _rows(self):
        return [
            (
                _CLAIM_UUID_A,
                "X" * 250,  # 200字超のスニペット丸め確認用
                {"span_id": "span_001_13", "legacy_ids": ["claim_span_001_13_sub04"]},
            ),
            (_CLAIM_UUID_B, "Other claim text", {"legacy_ids": []}),
        ]

    def test_resolves_by_legacy_agent_id(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {"claim_span_001_13_sub04"})
        assert index == {
            "claim_span_001_13_sub04": {"claim_id": _CLAIM_UUID_A, "text": "X" * 200}
        }

    def test_resolves_by_span_id(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {"span_001_13"})
        assert index["span_001_13"]["claim_id"] == _CLAIM_UUID_A

    def test_resolves_by_db_uuid_itself(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {_CLAIM_UUID_B})
        assert index == {_CLAIM_UUID_B: {"claim_id": _CLAIM_UUID_B, "text": "Other claim text"}}

    def test_unreferenced_claim_ids_are_not_included(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {"claim_span_001_13_sub04"})
        assert _CLAIM_UUID_B not in index
        assert len(index) == 1

    def test_empty_ref_ids_short_circuits_without_db_call(self):
        # ref_ids が空なら _pg_session を呼ばない（未 monkeypatch でも例外にならない）
        assert tc._resolve_claim_reference_index("doc-1", set()) == {}


# ---------------------------------------------------------------------------
# _resolve_evidence_reference_index: evidence_registry stage output
# ---------------------------------------------------------------------------


class TestResolveEvidenceReferenceIndex:
    def test_resolves_referenced_evidence_with_block_id(self):
        artifacts = {
            "evidence_registry": {
                "records": [
                    {
                        "evidence_id": "ev_0042",
                        "evidence_text": "quoted PDF text",
                        "source": {"block_id": "block_9"},
                    },
                    {
                        "evidence_id": "ev_0043",
                        "evidence_text": "unreferenced",
                        "source": {"block_id": "block_1"},
                    },
                ]
            }
        }
        index = tc._resolve_evidence_reference_index(artifacts, {"ev_0042"})
        assert index == {"ev_0042": {"text": "quoted PDF text", "block_id": "block_9"}}

    def test_missing_block_id_falls_back_to_empty_string(self):
        artifacts = {
            "evidence_registry": {
                "records": [{"evidence_id": "ev_0001", "evidence_text": "t", "source": {}}]
            }
        }
        index = tc._resolve_evidence_reference_index(artifacts, {"ev_0001"})
        assert index["ev_0001"]["block_id"] == ""

    def test_text_snippet_truncated_to_200_chars(self):
        long_text = "y" * 500
        artifacts = {
            "evidence_registry": {
                "records": [{"evidence_id": "ev_0099", "evidence_text": long_text, "source": {}}]
            }
        }
        index = tc._resolve_evidence_reference_index(artifacts, {"ev_0099"})
        assert index["ev_0099"]["text"] == "y" * 200

    def test_missing_stage_output_degrades_to_empty(self):
        assert tc._resolve_evidence_reference_index({}, {"ev_0042"}) == {}
        assert tc._resolve_evidence_reference_index({"evidence_registry": {}}, {"ev_0042"}) == {}


# ---------------------------------------------------------------------------
# _resolve_derivation_reference_index: derivation_chain stage output
# ---------------------------------------------------------------------------


class TestResolveDerivationReferenceIndex:
    def _artifacts(self):
        return {
            "derivation_chain": {
                "chains": [
                    {
                        "derivation_id": "derivation_claim_0003",
                        "teaching_takeaway": "Explains how the nuisance parameter is eliminated.",
                        "operation": "solve",
                        "chain_type": "system_level",
                        "steps": [
                            {
                                "step_id": "step_006",
                                "operation": "eliminate",
                                "reason": "Eliminate nuisance parameter via substitution.",
                            },
                            {"step_id": "step_007", "operation": "solve", "reason": ""},
                        ],
                    },
                    {
                        "derivation_id": "derivation_claim_0004",
                        "teaching_takeaway": "",
                        "operation": "",
                        "chain_type": "equation_chain",
                        "steps": [],
                    },
                ]
            }
        }

    def test_derivation_label_prefers_teaching_takeaway(self):
        index = tc._resolve_derivation_reference_index(
            self._artifacts(), {"derivation_claim_0003"}
        )
        assert index == {
            "derivation_claim_0003": {
                "label": "Explains how the nuisance parameter is eliminated.",
                "kind": "derivation",
                "operation": "solve",
            }
        }

    def test_derivation_label_falls_back_to_chain_type_when_no_takeaway_or_operation(self):
        index = tc._resolve_derivation_reference_index(
            self._artifacts(), {"derivation_claim_0004"}
        )
        assert index["derivation_claim_0004"]["label"] == "equation_chain"

    def test_step_label_prefers_reason_then_operation(self):
        index = tc._resolve_derivation_reference_index(
            self._artifacts(), {"step_006", "step_007"}
        )
        assert index["step_006"] == {
            "label": "Eliminate nuisance parameter via substitution.",
            "kind": "step",
            "operation": "eliminate",
        }
        # reason が空文字なら operation にフォールバック
        assert index["step_007"] == {"label": "solve", "kind": "step", "operation": "solve"}

    def test_unreferenced_ids_are_not_included(self):
        index = tc._resolve_derivation_reference_index(
            self._artifacts(), {"derivation_claim_0003"}
        )
        assert "step_006" not in index
        assert "derivation_claim_0004" not in index

    def test_missing_stage_output_degrades_to_empty(self):
        assert tc._resolve_derivation_reference_index({}, {"derivation_claim_0003"}) == {}


# ---------------------------------------------------------------------------
# _build_graph_reference_index: 統合 + fail-open
# ---------------------------------------------------------------------------


class TestBuildGraphReferenceIndex:
    def _graph_payload(self):
        return {
            "nodes": [
                {
                    "component_id": "n1",
                    "label": "N1",
                    "linked_claim_ids": ["claim_span_001_13_sub04"],
                    "linked_evidence_ids": ["ev_0042"],
                    "linked_derivation_ids": ["derivation_claim_0003"],
                }
            ],
            "edges": [
                {
                    "source_component_id": "n1",
                    "target_component_id": "n1",
                    "evidence_claim_ids": [],
                    "evidence_derivation_ids": ["step_006"],
                    "source_evidence_ids": [],
                }
            ],
        }

    def _artifacts(self):
        return {
            "evidence_registry": {
                "records": [
                    {
                        "evidence_id": "ev_0042",
                        "evidence_text": "quoted PDF text",
                        "source": {"block_id": "block_9"},
                    }
                ]
            },
            "derivation_chain": {
                "chains": [
                    {
                        "derivation_id": "derivation_claim_0003",
                        "teaching_takeaway": "Explains X.",
                        "operation": "solve",
                        "steps": [
                            {"step_id": "step_006", "operation": "eliminate", "reason": "Eliminate nuisance."}
                        ],
                    }
                ]
            },
        }

    def test_builds_full_index_from_referenced_ids_only(self, monkeypatch):
        claim_rows = [
            (
                _CLAIM_UUID_A,
                "Full claim text",
                {"legacy_ids": ["claim_span_001_13_sub04"]},
            )
        ]
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(claim_rows))
        monkeypatch.setattr(tc, "document_run_artifacts", lambda document_id: self._artifacts())

        index = tc._build_graph_reference_index("doc-1", self._graph_payload())

        assert index["claims"] == {
            "claim_span_001_13_sub04": {"claim_id": _CLAIM_UUID_A, "text": "Full claim text"}
        }
        assert index["evidence"] == {"ev_0042": {"text": "quoted PDF text", "block_id": "block_9"}}
        assert index["derivations"]["derivation_claim_0003"]["label"] == "Explains X."
        assert index["derivations"]["step_006"]["label"] == "Eliminate nuisance."

    def test_degrades_to_empty_dicts_when_stage_outputs_missing(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession([]))
        monkeypatch.setattr(tc, "document_run_artifacts", lambda document_id: {})

        index = tc._build_graph_reference_index("doc-1", self._graph_payload())

        assert index == {"claims": {}, "evidence": {}, "derivations": {}}

    def test_no_referenced_ids_short_circuits_without_side_effects(self):
        # graph payload に参照 ID が一切無ければ _pg_session / document_run_artifacts を
        # 呼ばずに空 index を返す（未 monkeypatch でも例外にならない）。
        index = tc._build_graph_reference_index("doc-1", {"nodes": [], "edges": []})
        assert index == {"claims": {}, "evidence": {}, "derivations": {}}

    def test_claim_resolution_failure_is_fail_open(self, monkeypatch):
        """DB エラーで claim 解決が失敗しても例外を出さず claims が空になるだけ
        （evidence/derivations は独立して解決される, fail-open）。"""
        monkeypatch.setattr(tc, "_pg_session", lambda: _RaisingSession())
        monkeypatch.setattr(tc, "document_run_artifacts", lambda document_id: self._artifacts())

        index = tc._build_graph_reference_index("doc-1", self._graph_payload())

        assert index["claims"] == {}
        assert index["evidence"] == {"ev_0042": {"text": "quoted PDF text", "block_id": "block_9"}}
        assert index["derivations"]["derivation_claim_0003"]["label"] == "Explains X."

    def test_artifacts_lookup_failure_is_fail_open(self, monkeypatch):
        """document_run_artifacts 自体が例外を出しても claims 解決は独立して継続する。"""
        claim_rows = [
            (_CLAIM_UUID_A, "Full claim text", {"legacy_ids": ["claim_span_001_13_sub04"]})
        ]
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(claim_rows))

        def _raise(_document_id):
            raise RuntimeError("stage_outputs unavailable")

        monkeypatch.setattr(tc, "document_run_artifacts", _raise)

        index = tc._build_graph_reference_index("doc-1", self._graph_payload())

        assert index["claims"] == {
            "claim_span_001_13_sub04": {"claim_id": _CLAIM_UUID_A, "text": "Full claim text"}
        }
        assert index["evidence"] == {}
        assert index["derivations"] == {}

    def test_malformed_graph_payload_returns_empty_index(self):
        assert tc._build_graph_reference_index("doc-1", None) == {
            "claims": {},
            "evidence": {},
            "derivations": {},
        }


# ---------------------------------------------------------------------------
# get_component_graph route wiring: レスポンスに reference_index が乗ること
# ---------------------------------------------------------------------------


class TestGetComponentGraphRouteWiring:
    def test_stored_graph_response_includes_reference_index(self, monkeypatch):
        stored = {
            "graph_id": "graph_doc-1",
            "document_id": "doc-1",
            "scope": {"level": "paper"},
            "nodes": [
                {
                    "component_id": "n1",
                    "label": "N1",
                    "linked_claim_ids": ["claim_span_001_13_sub04"],
                }
            ],
            "edges": [],
            "validation_results": [],
        }
        monkeypatch.setattr(tc, "_ensure_document_viewable", lambda document_id, user: None)
        monkeypatch.setattr(tc, "_components_for_document", lambda document_id: [])
        monkeypatch.setattr(tc, "_stored_component_graph", lambda document_id: dict(stored))
        monkeypatch.setattr(
            tc,
            "_normalize_stored_component_graph",
            lambda document_id, graph, components: dict(stored),
        )
        claim_rows = [
            (_CLAIM_UUID_A, "Full claim text", {"legacy_ids": ["claim_span_001_13_sub04"]})
        ]
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(claim_rows))
        monkeypatch.setattr(tc, "document_run_artifacts", lambda document_id: {})

        result = tc.get_component_graph("doc-1", current_user={"id": "u1", "role": "TEACHER"})

        assert result.reference_index["claims"]["claim_span_001_13_sub04"]["claim_id"] == (
            _CLAIM_UUID_A
        )

    def test_build_path_response_includes_reference_index_key(self, monkeypatch):
        """stored graph が無い（build 経路）場合も reference_index キーは常に存在する
        （build 経路では claim/evidence/derivation 参照 ID を持たないため空 dict）。"""
        monkeypatch.setattr(tc, "_ensure_document_viewable", lambda document_id, user: None)
        monkeypatch.setattr(tc, "_components_for_document", lambda document_id: [])
        monkeypatch.setattr(tc, "_stored_component_graph", lambda document_id: {})
        monkeypatch.setattr(
            tc, "_normalize_stored_component_graph", lambda document_id, graph, components: {}
        )
        monkeypatch.setattr(
            tc,
            "_build_component_graph_payload",
            lambda document_id, components: {
                "graph_id": "graph_doc-1",
                "document_id": "doc-1",
                "scope": {"level": "paper"},
                "nodes": [],
                "edges": [],
                "validation_results": [],
            },
        )

        result = tc.get_component_graph("doc-1", current_user={"id": "u1", "role": "TEACHER"})

        assert result.reference_index == {"claims": {}, "evidence": {}, "derivations": {}}
