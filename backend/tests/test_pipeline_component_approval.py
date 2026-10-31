"""パイプライン生成 component の承認可能性（縦の糸 / 実データ由来の是正）。

実測（2026-09-19・2609.* 10 本、component 227 件）では 0 件しか承認できなかった。
パイプラインの component は

- ``source_chunks`` が空・``primary_chunk_id`` が NULL（出典は根拠 claim 側にある）、
- 各項目の出典は ``claim_ids`` / ``equation_ids``（``source_refs`` / ``evidence_claims``
  ではない。``TheoryIOItem`` がこの2キーを持たず読み出しで落としていた）

という形なので、文字どおりに読むと「出典が無い」ことになる。読み時に綴りを広げる
（弁そのもの = 出典必須は緩めない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import routes.theory_components as tc  # noqa: E402
from schemas import TheoryComponentOut, TheoryIOItem  # noqa: E402

_TEACHER = {"id": "22222222-2222-2222-2222-222222222222", "role": "TEACHER"}
_DOC = "11111111-1111-1111-1111-111111111111"
_COMPONENT = "33333333-3333-3333-3333-333333333333"
_CLAIM = "44444444-4444-4444-4444-444444444444"
_CHUNK = "55555555-5555-5555-5555-555555555555"


def _pipeline_component(**overrides) -> TheoryComponentOut:
    """実データ（theory_components_live）と同じ形の component。"""
    data = {
        "id": _COMPONENT,
        "course_id": "",
        "name": "w(theta) observable basis",
        "primary_chunk_id": None,
        "source_chunks": [],
        "inputs": [TheoryIOItem(label="projected galaxy sample", claim_ids=[_CLAIM])],
        "outputs": [TheoryIOItem(label="angular correlation", claim_ids=[_CLAIM])],
        "evidence_claims": [_CLAIM],
        "source_scope": {"document_id": _DOC},
    }
    data.update(overrides)
    return TheoryComponentOut(**data)


class TestSchemaKeepsPipelineEvidence:
    def test_io_item_keeps_claim_and_equation_ids(self):
        item = TheoryIOItem(label="x", claim_ids=["c1"], equation_ids=["eq_1"])
        assert item.claim_ids == ["c1"]
        assert item.equation_ids == ["eq_1"]

    def test_normalize_io_items_does_not_drop_them(self):
        [item] = tc._normalize_io_items([{"name": "x", "claim_ids": ["c1"], "equation_ids": ["eq_1"]}])
        assert item["claim_ids"] == ["c1"]
        assert item["equation_ids"] == ["eq_1"]


class TestItemSourcePresence:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"claim_ids": ["c1"]},
            {"equation_ids": ["eq_1"]},
            {"evidence_claims": ["c1"]},
        ],
    )
    def test_any_reference_counts_as_a_source(self, kwargs):
        assert tc._item_source_present(TheoryIOItem(label="x", **kwargs))

    def test_an_item_without_any_reference_still_fails(self):
        """弁は緩めない — 4キーとも空なら従来どおり出典なし。"""
        assert not tc._item_source_present(TheoryIOItem(label="x"))


class TestApprovalProblems:
    def test_pipeline_component_is_approvable_when_chunks_resolve(self):
        problems = tc._component_approval_problems(
            _pipeline_component(), derived_source_chunk_ids=[_CHUNK]
        )
        assert problems == []

    def test_missing_source_chunks_still_blocks_when_nothing_resolves(self):
        problems = tc._component_approval_problems(
            _pipeline_component(), derived_source_chunk_ids=[]
        )
        assert problems == ["出典チャンク（source_chunks）が未設定です"]

    def test_unsourced_item_still_blocks(self):
        problems = tc._component_approval_problems(
            _pipeline_component(outputs=[TheoryIOItem(label="出力")]),
            derived_source_chunk_ids=[_CHUNK],
        )
        assert any("出典" in p for p in problems)

    def test_needs_source_still_blocks(self):
        problems = tc._component_approval_problems(
            _pipeline_component(
                outputs=[TheoryIOItem(label="出力", claim_ids=[_CLAIM], needs_source=True)]
            ),
            derived_source_chunk_ids=[_CHUNK],
        )
        assert any("needs_source" in p for p in problems)

    def test_empty_name_still_blocks(self):
        problems = tc._component_approval_problems(
            _pipeline_component(name=" "), derived_source_chunk_ids=[_CHUNK]
        )
        assert "名前が空です" in problems


class TestDerivedSourceChunks:
    def test_reads_chunk_ids_of_the_backing_claims(self, monkeypatch):
        captured = {}

        class _Session:
            def execute(self, _stmt, params=None):
                captured["params"] = params

                class _Result:
                    @staticmethod
                    def fetchall():
                        return [(_CHUNK,), (None,)]

                return _Result()

            def close(self):
                captured["closed"] = True

        monkeypatch.setattr(tc, "_pg_session", lambda: _Session())
        assert tc._component_derived_source_chunk_ids(_pipeline_component()) == [_CHUNK]
        assert captured["params"] == {"claim_0": _CLAIM}
        assert captured["closed"] is True

    def test_no_sql_when_there_is_no_backing_claim(self, monkeypatch):
        def _boom():
            raise AssertionError("claim が無いときに SQL を発行しない")

        monkeypatch.setattr(tc, "_pg_session", _boom)
        component = _pipeline_component(
            evidence_claims=[], inputs=[TheoryIOItem(label="x")], outputs=[TheoryIOItem(label="y")]
        )
        assert tc._component_derived_source_chunk_ids(component) == []

    def test_db_failure_degrades_to_empty(self, monkeypatch):
        class _Session:
            def execute(self, _stmt, params=None):
                raise RuntimeError("db unavailable")

            def close(self):
                pass

        monkeypatch.setattr(tc, "_pg_session", lambda: _Session())
        assert tc._component_derived_source_chunk_ids(_pipeline_component()) == []

    def test_backing_claim_ids_include_item_claim_ids(self):
        assert tc._component_backing_claim_ids(_pipeline_component()) == [_CLAIM]


class TestApproveRoute:
    def test_approve_accepts_a_pipeline_component(self, monkeypatch):
        existing = _pipeline_component()
        monkeypatch.setattr(tc, "_get_component", lambda _id: existing)
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)
        monkeypatch.setattr(tc, "_component_derived_source_chunk_ids", lambda c: [_CHUNK])
        captured = {}

        def _fake_transition(component_id, component, **kwargs):
            captured.update(kwargs)
            return existing

        monkeypatch.setattr(tc, "_transition_component_review", _fake_transition)
        tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert captured["review_status"] == "teacher_approved"

    def test_approve_still_refuses_when_nothing_is_sourced(self, monkeypatch):
        existing = _pipeline_component(
            inputs=[TheoryIOItem(label="入力")], outputs=[TheoryIOItem(label="出力")]
        )
        monkeypatch.setattr(tc, "_get_component", lambda _id: existing)
        monkeypatch.setattr(tc, "_ensure_component_editable", lambda component, user: None)
        monkeypatch.setattr(tc, "_component_derived_source_chunk_ids", lambda c: [])
        with pytest.raises(HTTPException) as exc:
            tc.approve_theory_component(_COMPONENT, current_user=_TEACHER)
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# R-3（2026-09-19 レビュー）: PUT 承認経路と approve 経路の読みを揃える／復元式は出典に数えない
# ---------------------------------------------------------------------------


class TestPutAndApproveGatesAgree:
    def _pipeline_payload(self, **overrides) -> dict:
        payload = {
            "name": "w(theta) observable basis",
            "status": "teacher_reviewed",
            "source_chunks": [],
            "inputs": [{"label": "projected galaxy sample", "claim_ids": [_CLAIM], "source_refs": []}],
            "outputs": [{"label": "angular correlation", "claim_ids": [_CLAIM], "source_refs": []}],
            "preconditions": [],
            "constraints": [],
            "invalid_conditions": [],
            "evidence_claims": [_CLAIM],
            "source_scope": {"document_id": _DOC},
        }
        payload.update(overrides)
        return payload

    def test_refs_present_reads_claim_ids_like_the_approve_gate(self):
        assert tc._refs_present({"claim_ids": [_CLAIM]}) is True
        assert tc._refs_present({"equation_ids": ["eq_1"]}) is True
        assert tc._refs_present({"source_refs": [{"chunk_id": _CHUNK}]}) is True
        assert tc._refs_present({"source_refs": [], "claim_ids": [], "equation_ids": []}) is False

    def test_reconstructed_only_equation_ids_are_not_a_source_in_both_gates(self):
        reconstructed = frozenset({"eq_1"})
        assert tc._refs_present(
            {"equation_ids": ["eq_1"]}, reconstructed_equation_ids=reconstructed
        ) is False
        assert tc._refs_present(
            {"equation_ids": ["eq_1", "eq_2"]}, reconstructed_equation_ids=reconstructed
        ) is True
        item = TheoryIOItem(label="x", equation_ids=["eq_1"])
        assert tc._item_source_present(item, reconstructed_equation_ids=reconstructed) is False
        assert tc._item_source_present(item) is True

    def test_approve_gate_rejects_items_backed_only_by_reconstructed_equations(self):
        component = _pipeline_component(
            inputs=[TheoryIOItem(label="bias relation", equation_ids=["eq_1"])],
        )
        problems = tc._component_approval_problems(
            component,
            derived_source_chunk_ids=[_CHUNK],
            reconstructed_equation_ids=frozenset({"eq_1"}),
        )
        assert any("入力" in p for p in problems)
        problems = tc._component_approval_problems(
            component,
            derived_source_chunk_ids=[_CHUNK],
            reconstructed_equation_ids=frozenset(),
        )
        assert problems == []

    def test_put_gate_accepts_the_pipeline_shape_when_claims_resolve_to_chunks(self, monkeypatch):
        monkeypatch.setattr(tc, "_derived_source_chunk_ids_for_claims", lambda ids: [_CHUNK])
        monkeypatch.setattr(tc, "_reconstructed_equation_ids_for_document", lambda doc: frozenset())
        tc._validate_for_review(self._pipeline_payload())  # 例外なし = 承認可

    def test_put_gate_still_blocks_when_nothing_resolves(self, monkeypatch):
        monkeypatch.setattr(tc, "_derived_source_chunk_ids_for_claims", lambda ids: [])
        monkeypatch.setattr(tc, "_reconstructed_equation_ids_for_document", lambda doc: frozenset())
        with pytest.raises(HTTPException) as exc:
            tc._validate_for_review(self._pipeline_payload(evidence_claims=[]))
        fields = {w["field"] for w in exc.value.detail}
        assert "source_chunks" in fields

    def test_put_gate_blocks_reconstructed_only_equation_sources(self, monkeypatch):
        monkeypatch.setattr(tc, "_derived_source_chunk_ids_for_claims", lambda ids: [_CHUNK])
        monkeypatch.setattr(
            tc, "_reconstructed_equation_ids_for_document", lambda doc: frozenset({"eq_1"})
        )
        payload = self._pipeline_payload(
            inputs=[{"label": "bias relation", "equation_ids": ["eq_1"], "source_refs": []}],
        )
        with pytest.raises(HTTPException) as exc:
            tc._validate_for_review(payload)
        assert any(w["field"].startswith("inputs.") for w in exc.value.detail)

    def test_reconstructed_ids_come_from_the_adopted_equation_semantics_artifact(self, monkeypatch):
        artifacts = {
            "equation_semantics": {
                "equations": [
                    {"equation_id": "eq_1", "confidence_policy": {"must_not_treat_as_source_extracted": True}},
                    {"equation_id": "eq_2", "confidence_policy": {"must_not_treat_as_source_extracted": False}},
                    {"equation_id": "eq_3"},
                ]
            }
        }
        monkeypatch.setattr(tc, "document_run_artifacts", lambda doc: artifacts)
        assert tc._reconstructed_equation_ids_for_document(_DOC) == frozenset({"eq_1"})
        assert tc._reconstructed_equation_ids_for_document("") == frozenset()

    def test_reconstructed_ids_fail_soft_when_artifacts_are_unavailable(self, monkeypatch):
        def _boom(doc):
            raise RuntimeError("db down")

        monkeypatch.setattr(tc, "document_run_artifacts", _boom)
        assert tc._reconstructed_equation_ids_for_document(_DOC) == frozenset()

    def test_both_gates_read_the_same_source_keys(self):
        """dict 版と model 版の出典キーが食い違わない（非対称の再発防止）。"""
        import inspect

        dict_src = inspect.getsource(tc._refs_present)
        model_src = inspect.getsource(tc._item_source_present)
        for key in ("source_refs", "evidence_claims", "claim_ids", "equation_ids"):
            assert key in dict_src, key
            assert key in model_src, key
        assert "_has_extracted_equation_source" in dict_src
        assert "_has_extracted_equation_source" in model_src
