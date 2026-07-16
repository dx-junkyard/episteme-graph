"""W層 候補注釈の検証・コミットルーティング（``core/deliberation/annotations.py``）の
純粋部の単体テスト。

設計書 `docs/features/element_deliberation_workspace_design.md` §5・§6・§7。DB 実接続は
使わない: ``validate_candidate_item``/``_normalize_body`` は DB 非依存の純粋関数として直接
検証し、``commit``/``_route_commit``/``dismiss`` は ``core.deliberation.store`` の関数を
monkeypatch したフェイクで「どの kind がどの経路に配送されるか」というルーティング分岐の
配線のみを検証する（実際の SQL 実行を伴う各 ``_commit_*`` 関数本体は DB 復帰後に別途
統合テストで確認する。ここでは扱わない）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import annotations as annotations_mod  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    ANNOTATION_KIND_DECOMPOSITION,
    ANNOTATION_KIND_IDENTITY,
    ANNOTATION_KIND_INTERPRETATION,
    ANNOTATION_KIND_MEANING,
    ANNOTATION_KIND_POSITIONING_NOTE,
    ANNOTATION_KIND_STANDARDIZATION,
    ANNOTATION_STATUS_CANDIDATE,
    ANNOTATION_STATUS_COMMITTED,
    ANNOTATION_STATUS_DISMISSED,
    SCOPE_DOCUMENT,
)


# ---------------------------------------------------------------------------
# _normalize_body: kind ごとの body 正規化
# ---------------------------------------------------------------------------


class TestNormalizeBody:
    def test_free_text_kind_wraps_string_into_text_dict(self):
        assert annotations_mod._normalize_body(ANNOTATION_KIND_MEANING, "説明本文") == {"text": "説明本文"}

    def test_free_text_kind_extracts_text_key_from_dict_body(self):
        assert annotations_mod._normalize_body(
            ANNOTATION_KIND_DECOMPOSITION, {"text": "内訳の説明"}
        ) == {"text": "内訳の説明"}

    def test_free_text_kind_rejects_blank_text(self):
        assert annotations_mod._normalize_body(ANNOTATION_KIND_MEANING, "   ") is None
        assert annotations_mod._normalize_body(ANNOTATION_KIND_MEANING, "") is None

    def test_identity_accepts_dict_body_with_shared_part_id(self):
        body = {"shared_part_id": "sp-1", "local_expression": {"label": "V_max"}}
        assert annotations_mod._normalize_body(ANNOTATION_KIND_IDENTITY, body) == body

    def test_identity_accepts_json_encoded_string_body(self):
        raw = '{"shared_part_id": "sp-1", "local_expression": {"label": "V_max"}}'
        result = annotations_mod._normalize_body(ANNOTATION_KIND_IDENTITY, raw)
        assert result == {"shared_part_id": "sp-1", "local_expression": {"label": "V_max"}}

    def test_identity_rejects_missing_shared_part_id(self):
        assert annotations_mod._normalize_body(ANNOTATION_KIND_IDENTITY, {"local_expression": {}}) is None

    def test_identity_rejects_non_json_string(self):
        assert annotations_mod._normalize_body(ANNOTATION_KIND_IDENTITY, "not json") is None

    def test_standardization_accepts_dict_with_status(self):
        body = {"standardization_status": "emerging_common"}
        assert annotations_mod._normalize_body(ANNOTATION_KIND_STANDARDIZATION, body) == body

    def test_standardization_rejects_missing_status(self):
        assert annotations_mod._normalize_body(ANNOTATION_KIND_STANDARDIZATION, {"evidence_kinds": []}) is None


# ---------------------------------------------------------------------------
# validate_candidate_item: W3（evidence/reason 必須）+ kind 語彙 + confidence 型変換
# ---------------------------------------------------------------------------


class TestValidateCandidateItem:
    def _well_formed(self, **overrides) -> dict:
        item = {
            "kind": ANNOTATION_KIND_MEANING,
            "body": "説明",
            "evidence": ["逐語引用"],
            "reason": "理由",
            "confidence": 0.7,
        }
        item.update(overrides)
        return item

    def test_accepts_well_formed_item(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed())
        assert error is None
        assert normalized == {
            "kind": ANNOTATION_KIND_MEANING,
            "body": {"text": "説明"},
            "evidence": ["逐語引用"],
            "reason": "理由",
            "confidence": 0.7,
        }

    def test_rejects_unknown_kind(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed(kind="nonsense"))
        assert normalized is None
        assert "unknown kind" in error

    def test_rejects_empty_evidence_list(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed(evidence=[]))
        assert normalized is None
        assert "evidence" in error

    def test_rejects_evidence_with_only_blank_strings(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed(evidence=["  ", ""]))
        assert normalized is None
        assert "evidence" in error

    def test_rejects_blank_reason(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed(reason="   "))
        assert normalized is None
        assert "reason" in error

    def test_rejects_body_that_cannot_be_normalized(self):
        # identity は shared_part_id が無いと不採用。
        item = self._well_formed(kind=ANNOTATION_KIND_IDENTITY, body="{}")
        normalized, error = annotations_mod.validate_candidate_item(item)
        assert normalized is None
        assert "body" in error

    def test_confidence_string_is_coerced_to_float(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed(confidence="0.42"))
        assert error is None
        assert normalized["confidence"] == pytest.approx(0.42)

    def test_confidence_invalid_becomes_none_not_a_rejection(self):
        normalized, error = annotations_mod.validate_candidate_item(self._well_formed(confidence="not-a-number"))
        assert error is None
        assert normalized["confidence"] is None

    def test_confidence_missing_becomes_none(self):
        item = self._well_formed()
        del item["confidence"]
        normalized, error = annotations_mod.validate_candidate_item(item)
        assert error is None
        assert normalized["confidence"] is None

    def test_identity_kind_with_valid_body_is_accepted(self):
        item = self._well_formed(
            kind=ANNOTATION_KIND_IDENTITY,
            body={"shared_part_id": "sp-1", "local_expression": {"label": "x"}},
        )
        normalized, error = annotations_mod.validate_candidate_item(item)
        assert error is None
        assert normalized["body"]["shared_part_id"] == "sp-1"


# ---------------------------------------------------------------------------
# create_candidates_from_dialogue: 不採用項目は書き込まない(W3)
# ---------------------------------------------------------------------------


class TestCreateCandidatesFromDialogue:
    def test_only_valid_items_are_persisted(self, monkeypatch):
        created_calls = []

        def fake_create_annotation(ref, **kwargs):
            created_calls.append(kwargs)
            return {"id": f"a-{len(created_calls)}", **kwargs}

        monkeypatch.setattr(annotations_mod.store_mod, "create_annotation", fake_create_annotation)

        from core.deliberation.schema import ElementRef

        ref = ElementRef(scope=SCOPE_DOCUMENT, element_type="theory_claim", element_id="c1", document_id="doc-1")
        raw_items = [
            {"kind": ANNOTATION_KIND_MEANING, "body": "ok", "evidence": ["q"], "reason": "r", "confidence": 0.5},
            {"kind": ANNOTATION_KIND_MEANING, "body": "no evidence", "evidence": [], "reason": "r"},
            {"kind": "bogus", "body": "x", "evidence": ["q"], "reason": "r"},
        ]
        result = annotations_mod.create_candidates_from_dialogue(
            ref, session_id="s1", raw_items=raw_items, created_by="u1",
        )
        assert len(result) == 1
        assert len(created_calls) == 1
        assert created_calls[0]["kind"] == ANNOTATION_KIND_MEANING

    def test_empty_items_creates_nothing(self, monkeypatch):
        monkeypatch.setattr(
            annotations_mod.store_mod, "create_annotation",
            lambda *a, **k: pytest.fail("should not be called"),
        )
        from core.deliberation.schema import ElementRef

        ref = ElementRef(scope=SCOPE_DOCUMENT, element_type="theory_claim", element_id="c1", document_id="doc-1")
        result = annotations_mod.create_candidates_from_dialogue(
            ref, session_id="s1", raw_items=[], created_by="u1",
        )
        assert result == []


# ---------------------------------------------------------------------------
# _route_commit: ルーティング分岐（各ハンドラを monkeypatch したフェイクで検証）
# ---------------------------------------------------------------------------


class TestRouteCommitDispatch:
    def _annotation(self, kind: str, **overrides) -> dict:
        base = {
            "id": "a1",
            "status": ANNOTATION_STATUS_CANDIDATE,
            "kind": kind,
            "scope": SCOPE_DOCUMENT,
            "element_type": "theory_component",
            "element_id": "comp-1",
            "document_id": "doc-1",
            "body": {"text": "x"},
            "evidence": ["q"],
            "reason": "r",
            "confidence": 0.5,
        }
        base.update(overrides)
        return base

    def test_interpretation_routes_to_interpretation_handler(self, monkeypatch):
        calls = []
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_INTERPRETATION,
            lambda annotation, uid: calls.append(("interpretation", annotation, uid)) or {"type": "component_explanation"},
        )
        result = annotations_mod._route_commit(self._annotation(ANNOTATION_KIND_INTERPRETATION), "u1")
        assert result == {"type": "component_explanation"}
        assert calls and calls[0][0] == "interpretation"

    def test_meaning_routes_to_meaning_or_decomposition_handler(self, monkeypatch):
        calls = []
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_MEANING,
            lambda annotation, uid: calls.append(annotation["kind"]) or {"type": "theory_component"},
        )
        result = annotations_mod._route_commit(self._annotation(ANNOTATION_KIND_MEANING), "u1")
        assert result == {"type": "theory_component"}
        assert calls == [ANNOTATION_KIND_MEANING]

    def test_decomposition_routes_to_same_meaning_or_decomposition_handler(self, monkeypatch):
        calls = []
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_DECOMPOSITION,
            lambda annotation, uid: calls.append(annotation["kind"]) or {"type": "theory_component"},
        )
        annotations_mod._route_commit(self._annotation(ANNOTATION_KIND_DECOMPOSITION), "u1")
        assert calls == [ANNOTATION_KIND_DECOMPOSITION]

    def test_identity_routes_to_identity_handler(self, monkeypatch):
        calls = []
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_IDENTITY,
            lambda annotation, uid: calls.append("identity") or {"type": "identity_link"},
        )
        result = annotations_mod._route_commit(self._annotation(ANNOTATION_KIND_IDENTITY), "u1")
        assert result == {"type": "identity_link"}
        assert calls == ["identity"]

    def test_standardization_routes_to_standardization_handler(self, monkeypatch):
        # Phase S（migration 050）: standardization は library_entries.standardization_status
        # への commit ルーティングに対応済み（もう unsupported ではない）。
        calls = []
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_STANDARDIZATION,
            lambda annotation, uid: calls.append("standardization") or {"type": "library_entry"},
        )
        result = annotations_mod._route_commit(
            self._annotation(ANNOTATION_KIND_STANDARDIZATION), "u1",
        )
        assert result == {"type": "library_entry"}
        assert calls == ["standardization"]

    @pytest.mark.parametrize("kind", [ANNOTATION_KIND_POSITIONING_NOTE])
    def test_unsupported_kinds_raise_without_calling_any_handler(self, monkeypatch, kind):
        for route_kind in (
            ANNOTATION_KIND_INTERPRETATION, ANNOTATION_KIND_MEANING,
            ANNOTATION_KIND_IDENTITY, ANNOTATION_KIND_STANDARDIZATION,
        ):
            monkeypatch.setitem(
                annotations_mod._COMMIT_ROUTES, route_kind,
                lambda *a, **k: pytest.fail("handler should not be called for an unsupported kind"),
            )
        with pytest.raises(annotations_mod.CommitRoutingError) as excinfo:
            annotations_mod._route_commit(self._annotation(kind), "u1")
        assert excinfo.value.kind == "unsupported"

    def test_unknown_kind_raises_invalid(self):
        with pytest.raises(annotations_mod.CommitRoutingError) as excinfo:
            annotations_mod._route_commit(self._annotation("totally-bogus"), "u1")
        assert excinfo.value.kind == "invalid"


# ---------------------------------------------------------------------------
# commit(): candidate ガード + set_annotation_status への委譲
# ---------------------------------------------------------------------------


class TestCommitTopLevel:
    def test_raises_not_found_when_annotation_missing(self, monkeypatch):
        monkeypatch.setattr(annotations_mod.store_mod, "get_annotation", lambda _id: None)
        with pytest.raises(annotations_mod.CommitRoutingError) as excinfo:
            annotations_mod.commit("missing-id", current_user_id="u1")
        assert excinfo.value.kind == "not_found"

    def test_raises_conflict_when_not_candidate(self, monkeypatch):
        monkeypatch.setattr(
            annotations_mod.store_mod, "get_annotation",
            lambda _id: {"id": _id, "status": ANNOTATION_STATUS_COMMITTED, "kind": ANNOTATION_KIND_MEANING},
        )
        with pytest.raises(annotations_mod.CommitRoutingError) as excinfo:
            annotations_mod.commit("a1", current_user_id="u1")
        assert excinfo.value.kind == "conflict"

    def test_happy_path_calls_set_annotation_status_with_committed_target(self, monkeypatch):
        annotation = {
            "id": "a1", "status": ANNOTATION_STATUS_CANDIDATE, "kind": ANNOTATION_KIND_IDENTITY,
            "scope": SCOPE_DOCUMENT, "element_type": "theory_claim", "element_id": "c1",
            "document_id": "doc-1", "body": {"shared_part_id": "sp-1"}, "evidence": ["q"],
            "reason": "r", "confidence": 0.6,
        }
        monkeypatch.setattr(annotations_mod.store_mod, "get_annotation", lambda _id: annotation)
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_IDENTITY,
            lambda ann, uid: {"type": "identity_link", "id": "link-1", "status": "candidate"},
        )
        set_status_calls = []

        def fake_set_status(annotation_id, *, status, committed_target, updated_by):
            set_status_calls.append((annotation_id, status, committed_target, updated_by))
            return {**annotation, "status": status, "committed_target": committed_target}

        monkeypatch.setattr(annotations_mod.store_mod, "set_annotation_status", fake_set_status)

        result = annotations_mod.commit("a1", current_user_id="u1")
        assert result["status"] == ANNOTATION_STATUS_COMMITTED
        assert set_status_calls == [
            ("a1", ANNOTATION_STATUS_COMMITTED, {"type": "identity_link", "id": "link-1", "status": "candidate"}, "u1")
        ]

    def test_race_condition_when_set_annotation_status_returns_none(self, monkeypatch):
        annotation = {
            "id": "a1", "status": ANNOTATION_STATUS_CANDIDATE, "kind": ANNOTATION_KIND_MEANING,
            "scope": SCOPE_DOCUMENT, "element_type": "theory_component", "element_id": "comp-1",
            "document_id": "doc-1", "body": {"text": "x"}, "evidence": ["q"], "reason": "r", "confidence": None,
        }
        monkeypatch.setattr(annotations_mod.store_mod, "get_annotation", lambda _id: annotation)
        monkeypatch.setitem(
            annotations_mod._COMMIT_ROUTES, ANNOTATION_KIND_MEANING,
            lambda ann, uid: {"type": "theory_component", "id": "comp-1", "field": "summary"},
        )
        monkeypatch.setattr(annotations_mod.store_mod, "set_annotation_status", lambda *a, **k: None)

        with pytest.raises(annotations_mod.CommitRoutingError) as excinfo:
            annotations_mod.commit("a1", current_user_id="u1")
        assert excinfo.value.kind == "conflict"


# ---------------------------------------------------------------------------
# dismiss(): status 遷移のみ委譲する薄いラッパ
# ---------------------------------------------------------------------------


class TestDismiss:
    def test_delegates_to_store_set_annotation_status_with_dismissed(self, monkeypatch):
        calls = []

        def fake_set_status(annotation_id, *, status, committed_target, updated_by):
            calls.append((annotation_id, status, committed_target, updated_by))
            return {"id": annotation_id, "status": status}

        monkeypatch.setattr(annotations_mod.store_mod, "set_annotation_status", fake_set_status)
        result = annotations_mod.dismiss("a1", updated_by="u1")
        assert result == {"id": "a1", "status": ANNOTATION_STATUS_DISMISSED}
        assert calls == [("a1", ANNOTATION_STATUS_DISMISSED, None, "u1")]

    def test_returns_none_when_store_reports_conflict(self, monkeypatch):
        monkeypatch.setattr(annotations_mod.store_mod, "set_annotation_status", lambda *a, **k: None)
        assert annotations_mod.dismiss("a1", updated_by="u1") is None
