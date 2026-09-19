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
                "teacher_approved",
            ),
            (_CLAIM_UUID_B, "Other claim text", {"legacy_ids": []}, None),
        ]

    def test_resolves_by_legacy_agent_id(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {"claim_span_001_13_sub04"})
        assert index == {
            "claim_span_001_13_sub04": {
                "claim_id": _CLAIM_UUID_A,
                "text": "X" * 200,
                "review_status": "teacher_approved",
                # DB 行で解決したエントリは resolution="db"（artifact 由来と区別する）。
                "resolution": "db",
            }
        }

    def test_resolves_by_span_id(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {"span_001_13"})
        assert index["span_001_13"]["claim_id"] == _CLAIM_UUID_A

    def test_resolves_by_db_uuid_itself(self, monkeypatch):
        # review_status が NULL の行は既定語彙 teacher_review_required に正規化される
        # （グラフ対話レビュー画面の claim 行が承認ボタンの活性判定に使う）。
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {_CLAIM_UUID_B})
        assert index == {
            _CLAIM_UUID_B: {
                "claim_id": _CLAIM_UUID_B,
                "text": "Other claim text",
                "review_status": "teacher_review_required",
                "resolution": "db",
            }
        }

    def test_unreferenced_claim_ids_are_not_included(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index("doc-1", {"claim_span_001_13_sub04"})
        assert _CLAIM_UUID_B not in index
        assert len(index) == 1

    def test_empty_ref_ids_short_circuits_without_db_call(self):
        # ref_ids が空なら _pg_session を呼ばない（未 monkeypatch でも例外にならない）
        assert tc._resolve_claim_reference_index("doc-1", set()) == {}

    def test_missing_artifacts_keeps_db_only_behaviour(self, monkeypatch):
        """artifact 引数なし・空 dict でも DB 解決の結果は従来どおり（古い run）。"""
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        without = tc._resolve_claim_reference_index("doc-1", {"span_001_13"})
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        with_empty = tc._resolve_claim_reference_index("doc-1", {"span_001_13"}, {})
        assert without == with_empty
        assert without["span_001_13"]["resolution"] == "db"


# ---------------------------------------------------------------------------
# claim_object_builder artifact フォールバック（2026-09-02）
#
# グラフのノードが参照する claim ID には theory_claims に行が無いものがある
# （atomic rewrite の子 claim / 式由来の synth_claim_*）。persist_qualified_claims が
# qualified_spans しか永続化しないための構造的欠落なので、読み時に artifact から
# 本文を補う（claim_id は空 = 承認操作の対象外であることを UI が判別できる形）。
# ---------------------------------------------------------------------------


class TestArtifactClaimFallback:
    def _artifacts(self):
        return {
            "claim_object_builder": {
                "document_id": "doc-1",
                "claims": [
                    {
                        "claim_id": "synth_claim_0001",
                        "text": "Z" * 250,  # 200字丸め確認用
                        "normalized_text": "normalized synth",
                        "support_status": "derived",
                        "is_atomic": True,
                        "parent_claim_id": None,
                        "source_span_ids": [],
                        "equation_ids": ["eq_2_7"],
                        "review_status": "teacher_review_required",
                        "confidence": 0.82,  # 生数値は索引に載せない（GR3）
                        "qualification_reason": "LLM が書いた理由文（載せない）",
                    },
                    {
                        "claim_id": "claim_span_001_13",
                        "text": "Compound parent claim",
                        "support_status": "source_backed",
                        "is_atomic": False,
                        "parent_claim_id": None,
                        "source_span_ids": ["span_001_13"],
                    },
                    {
                        "claim_id": "claim_span_001_13_sub01",
                        "text": "Atomic child claim",
                        "normalized_text": "atomic child normalized",
                        "support_status": "source_backed",
                        "is_atomic": True,
                        "parent_claim_id": "claim_span_001_13",
                        "source_span_ids": ["span_001_13"],
                    },
                    {
                        # parent_claim_id を持たない旧 artifact でも「親 ID + 連番」なら
                        # atomic rewrite 由来と判定する。
                        "claim_id": "claim_span_001_13_7",
                        "text": "Legacy atomic child",
                        "support_status": "partially_source_backed",
                        "is_atomic": True,
                        "source_span_ids": ["span_001_13"],
                    },
                    {
                        "claim_id": "claim_span_999_1",
                        "text": "Unreferenced claim",
                        "support_status": "source_backed",
                        "is_atomic": True,
                    },
                ],
            }
        }

    def _rows(self):
        # 親 span claim だけが theory_claims に永続化されている状況。
        return [
            (
                _CLAIM_UUID_A,
                "Persisted parent span claim",
                {"span_id": "span_001_13", "legacy_ids": ["claim_span_001_13"]},
                "teacher_approved",
            )
        ]

    def test_synth_claim_resolved_from_artifact_without_parent(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"synth_claim_0001"}, self._artifacts()
        )
        assert index == {
            "synth_claim_0001": {
                "claim_id": "",
                "text": "Z" * 200,
                "review_status": "",
                "resolution": "artifact",
                "origin": "equation_synthesis",
                "support_status": "derived",
                "is_atomic": True,
                "parent_claim_id": "",
                "parent_review_status": "",
            }
        }

    def test_atomic_subclaim_carries_parent_db_uuid_and_review_status(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"claim_span_001_13_sub01"}, self._artifacts()
        )
        entry = index["claim_span_001_13_sub01"]
        assert entry["resolution"] == "artifact"
        assert entry["origin"] == "atomic_rewrite"
        assert entry["text"] == "Atomic child claim"
        assert entry["claim_id"] == ""
        assert entry["parent_claim_id"] == _CLAIM_UUID_A
        assert entry["parent_review_status"] == "teacher_approved"

    def test_counter_suffixed_child_without_parent_field_is_atomic_rewrite(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"claim_span_001_13_7"}, self._artifacts()
        )
        assert index["claim_span_001_13_7"]["origin"] == "atomic_rewrite"
        # source_span_ids 経由でも親の DB UUID が引ける（追加 SQL は発行しない）。
        assert index["claim_span_001_13_7"]["parent_claim_id"] == _CLAIM_UUID_A

    def test_plain_artifact_claim_origin_is_claim_object(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession([]))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"claim_span_999_1"}, self._artifacts()
        )
        # 親 ID が artifact 内に存在しない連番サフィックスは atomic rewrite と見なさない。
        assert index["claim_span_999_1"]["origin"] == "claim_object"
        assert index["claim_span_999_1"]["parent_claim_id"] == ""

    def test_db_resolution_wins_over_artifact(self, monkeypatch):
        """DB に行がある ID は第1段で解決され、artifact 由来キーで上書きされない。"""
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"claim_span_001_13"}, self._artifacts()
        )
        assert index["claim_span_001_13"] == {
            "claim_id": _CLAIM_UUID_A,
            "text": "Persisted parent span claim",
            "review_status": "teacher_approved",
            "resolution": "db",
        }

    def test_unresolved_id_is_absent_from_index(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"claim_does_not_exist"}, self._artifacts()
        )
        assert index == {}

    def test_no_confidence_or_reason_leaks_into_entries(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession([]))
        index = tc._resolve_claim_reference_index(
            "doc-1", {"synth_claim_0001"}, self._artifacts()
        )
        entry = index["synth_claim_0001"]
        assert "confidence" not in entry
        assert "qualification_reason" not in entry
        assert "reason" not in entry
        assert "equation_ids" not in entry

    def test_malformed_artifact_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
        for artifacts in (
            {"claim_object_builder": {}},
            {"claim_object_builder": {"claims": "not-a-list"}},
            {"claim_object_builder": {"claims": ["not-a-dict", None]}},
        ):
            monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(self._rows()))
            assert tc._resolve_claim_reference_index("doc-1", {"synth_claim_0001"}, artifacts) == {}

    def test_text_falls_back_to_normalized_text(self, monkeypatch):
        artifacts = {
            "claim_object_builder": {
                "claims": [
                    {
                        "claim_id": "synth_claim_0002",
                        "text": "   ",
                        "normalized_text": "normalized only",
                        "support_status": "derived",
                        "is_atomic": True,
                    }
                ]
            }
        }
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession([]))
        index = tc._resolve_claim_reference_index("doc-1", {"synth_claim_0002"}, artifacts)
        assert index["synth_claim_0002"]["text"] == "normalized only"


# ---------------------------------------------------------------------------
# 旧 artifact の式由来合成 claim に読み時で ``$...$`` を補う投影
# （2026-09-03。新しい artifact は生成時点で ``$`` 付きなので対象外）
# ---------------------------------------------------------------------------


def _synth_artifact(text: str, concept_names: list[str], claim_id: str = "synth_claim_0001") -> dict:
    return {
        "claim_object_builder": {
            "claims": [
                {
                    "claim_id": claim_id,
                    "text": text,
                    "support_status": "derived",
                    "is_atomic": True,
                    "concepts": [
                        {"name": n, "normalized": n.lower(), "concept_type": "symbol"}
                        for n in concept_names
                    ],
                }
            ]
        }
    }


class TestLegacySynthClaimMathDelimiting:
    def _index(self, monkeypatch, artifacts, ref_id="synth_claim_0001"):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession([]))
        return tc._resolve_claim_reference_index("doc-1", {ref_id}, artifacts)[ref_id]

    def test_latex_symbols_get_delimiters(self, monkeypatch):
        text = (
            r"In equation (delta-fourier), F_n(\mathbf{k}_1,\ldots,\mathbf{k}_n) "
            r"depends on \sum_{n=1}^{\infty}, n!, \mathbf{k}."
        )
        artifacts = _synth_artifact(text, [
            r"F_n(\mathbf{k}_1,\ldots,\mathbf{k}_n)",
            r"\sum_{n=1}^{\infty}",
            "n!",
            r"\mathbf{k}",
        ])
        entry = self._index(monkeypatch, artifacts)
        assert entry["text"] == (
            r"In equation (delta-fourier), $F_n(\mathbf{k}_1,\ldots,\mathbf{k}_n)$ "
            r"depends on $\sum_{n=1}^{\infty}$, $n!$, $\mathbf{k}$."
        )
        # 数式区間の外（テンプレート語）には ``$`` を入れない。
        assert entry["text"].startswith("In equation (delta-fourier), $")

    def test_short_plain_symbols_only_match_whole_tokens(self, monkeypatch):
        # "n" は "In equation" の中に現れるが、独立トークンではないので包まない。
        # "a" はテンプレート語（"supports a solve"）と衝突するため対象外
        # （包み損ねは表示上の劣化に留まる／誤包みは本文を壊すので慎重側に倒す）。
        artifacts = _synth_artifact(
            "In equation (eq_1), n depends on a, k2.", ["n", "a", "k2"]
        )
        entry = self._index(monkeypatch, artifacts)
        assert entry["text"] == "In equation (eq_1), $n$ depends on a, $k2$."

    def test_template_words_are_never_wrapped(self, monkeypatch):
        artifacts = _synth_artifact(
            "The equation system supports a solve relating the listed quantities.",
            ["a", "the", "system"],
        )
        entry = self._index(monkeypatch, artifacts)
        assert entry["text"] == (
            "The equation system supports a solve relating the listed quantities."
        )

    def test_already_delimited_text_is_unchanged(self, monkeypatch):
        text = r"Equation (eq_2) defines $P_{\rm L}(k)$."
        artifacts = _synth_artifact(text, [r"P_{\rm L}(k)"])
        assert self._index(monkeypatch, artifacts)["text"] == text
        assert "$$" not in text

    def test_atomic_rewrite_and_claim_object_records_are_untouched(self, monkeypatch):
        for claim_id in ("claim_span_001_13_sub01", "claim_span_777"):
            text = r"P_{\rm L}(k) grows with the scale factor."
            artifacts = _synth_artifact(text, [r"P_{\rm L}(k)"], claim_id=claim_id)
            artifacts["claim_object_builder"]["claims"][0]["parent_claim_id"] = (
                "claim_span_001_13" if claim_id.endswith("sub01") else None
            )
            entry = self._index(monkeypatch, artifacts, ref_id=claim_id)
            assert entry["origin"] in ("atomic_rewrite", "claim_object")
            assert entry["text"] == text

    def test_truncation_does_not_split_a_math_span(self, monkeypatch):
        symbol = "\\alpha_{" + "x" * 60 + "}"
        prefix = "In equation (eq_9), y depends on " + "z" * 140 + ", "
        text = prefix + symbol + "."
        assert len(prefix) < tc._REFERENCE_TEXT_SNIPPET_MAX < len(text)
        artifacts = _synth_artifact(text, [symbol])
        entry = self._index(monkeypatch, artifacts)
        assert entry["text"].count("$") % 2 == 0
        assert not entry["text"].endswith("$")
        assert entry["text"] == prefix

    def test_plain_truncation_still_applies_without_math(self, monkeypatch):
        artifacts = _synth_artifact("Z" * 250, [])
        assert self._index(monkeypatch, artifacts)["text"] == "Z" * 200


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
                "teacher_review_required",
            )
        ]
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(claim_rows))
        monkeypatch.setattr(tc, "document_run_artifacts", lambda document_id: self._artifacts())

        index = tc._build_graph_reference_index("doc-1", self._graph_payload())

        assert index["claims"] == {
            "claim_span_001_13_sub04": {
                "claim_id": _CLAIM_UUID_A,
                "text": "Full claim text",
                "review_status": "teacher_review_required",
                "resolution": "db",
            }
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
            (_CLAIM_UUID_A, "Full claim text", {"legacy_ids": ["claim_span_001_13_sub04"]}, "teacher_review_required")
        ]
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(claim_rows))

        def _raise(_document_id):
            raise RuntimeError("stage_outputs unavailable")

        monkeypatch.setattr(tc, "document_run_artifacts", _raise)

        index = tc._build_graph_reference_index("doc-1", self._graph_payload())

        assert index["claims"] == {
            "claim_span_001_13_sub04": {
                "claim_id": _CLAIM_UUID_A,
                "text": "Full claim text",
                "review_status": "teacher_review_required",
                "resolution": "db",
            }
        }
        assert index["evidence"] == {}
        assert index["derivations"] == {}

    def test_claim_not_in_db_is_resolved_from_claim_object_builder_artifact(self, monkeypatch):
        """DB 行の無い claim（atomic rewrite の子）も本文が出る（2026-09-02）。"""
        graph_payload = {
            "nodes": [
                {
                    "component_id": "n1",
                    "linked_claim_ids": ["claim_span_001_13_sub04", "synth_claim_0001"],
                }
            ],
            "edges": [],
        }
        claim_rows = [
            (
                _CLAIM_UUID_A,
                "Persisted parent span claim",
                {"span_id": "span_001_13", "legacy_ids": ["claim_span_001_13"]},
                "teacher_approved",
            )
        ]
        artifacts = {
            "claim_object_builder": {
                "claims": [
                    {
                        "claim_id": "claim_span_001_13_sub04",
                        "text": "Atomic child claim",
                        "support_status": "source_backed",
                        "is_atomic": True,
                        "parent_claim_id": "claim_span_001_13",
                        "source_span_ids": ["span_001_13"],
                    },
                    {
                        "claim_id": "synth_claim_0001",
                        "text": "Equation-synthesised claim",
                        "support_status": "derived",
                        "is_atomic": True,
                    },
                ]
            }
        }
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession(claim_rows))
        monkeypatch.setattr(tc, "document_run_artifacts", lambda document_id: artifacts)

        index = tc._build_graph_reference_index("doc-1", graph_payload)

        child = index["claims"]["claim_span_001_13_sub04"]
        assert child["text"] == "Atomic child claim"
        assert child["resolution"] == "artifact"
        assert child["origin"] == "atomic_rewrite"
        assert child["parent_claim_id"] == _CLAIM_UUID_A
        assert child["parent_review_status"] == "teacher_approved"
        synth = index["claims"]["synth_claim_0001"]
        assert synth["origin"] == "equation_synthesis"
        assert synth["text"] == "Equation-synthesised claim"

    def test_artifacts_are_loaded_once_for_claims_evidence_and_derivations(self, monkeypatch):
        """claim / evidence / derivation で artifacts の SELECT を重複させない。"""
        calls = []
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeClaimsSession([]))

        def _artifacts(document_id):
            calls.append(document_id)
            return self._artifacts()

        monkeypatch.setattr(tc, "document_run_artifacts", _artifacts)
        tc._build_graph_reference_index("doc-1", self._graph_payload())
        assert calls == ["doc-1"]

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
            (_CLAIM_UUID_A, "Full claim text", {"legacy_ids": ["claim_span_001_13_sub04"]}, "teacher_review_required")
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
