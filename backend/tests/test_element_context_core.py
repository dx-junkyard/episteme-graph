"""``core/element_context.py``（学習者向け claim / equation 文脈 API の core ロジック）の
単体テスト。

設計書: ``docs/features/learner_element_context_design.md``。

DB 実接続は使わず、``get_session`` を monkeypatch した最小限のフェイクセッション
（``test_component_context_core.py`` の ``_ComponentTableFakeSession`` と同型の方針）、
および ``equation_records`` / ``context_lens_mod.build`` の monkeypatch で検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import element_context  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_does_not_import,
)


_DOC_A = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_DOC_B = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_CLAIM_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# ---------------------------------------------------------------------------
# フェイク DB（theory_claims に対する _resolve_claim のクエリを意味論的に再現）
# ---------------------------------------------------------------------------


class _FakeMappingsResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def fetchone(self):
        return dict(self._row) if self._row is not None else None


class _ClaimTableFakeSession:
    """実 SQL は評価せず、params（``doc_ids`` / ``raw_id`` / ``uuid_id``）を Python 側で
    同じ意味論（document scope 制約・id 一致・legacy_ids 包含・id 一致優先の並び替え）で
    再現する。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.closed = False
        self.execute_calls: list[dict] = []

    def execute(self, _stmt, params):
        self.execute_calls.append(dict(params))
        doc_ids = set(params.get("doc_ids") or [])
        raw_id = str(params.get("raw_id") or "")
        uuid_id = params.get("uuid_id")
        matches = []
        for row in self._rows:
            if str(row.get("document_id")) not in doc_ids:
                continue
            legacy_ids = [str(x) for x in (row.get("legacy_ids") or [])]
            matched_by_legacy = raw_id in legacy_ids
            matched_by_uuid = uuid_id is not None and str(row.get("id")) == str(uuid_id)
            if matched_by_legacy or matched_by_uuid:
                matches.append(row)
        matches.sort(key=lambda r: str(r.get("id")) == raw_id, reverse=True)
        return _FakeMappingsResult(matches[0] if matches else None)

    def close(self):
        self.closed = True


def _claim_row(**kwargs) -> dict:
    base = {"id": _CLAIM_UUID, "document_id": _DOC_A, "legacy_ids": ["claim_span_007"]}
    base.update(kwargs)
    return base


def _lens_result(**overrides) -> dict:
    result = {
        "focus": {
            "element_type": "theory_claim",
            "element_id": _CLAIM_UUID,
            "document_id": _DOC_A,
            "label": "主張ラベル",
            "intrinsic_summary": "主張の本文",
            "contextual_role": "中心命題を支持する",
            "contextual_role_status": "source_backed",
            "provenance": [f"theory_claims:{_CLAIM_UUID}", "span:span_007"],
            "generic": None,
        },
        "upper": [],
        "lower": [],
        "notes": [],
    }
    result.update(overrides)
    return result


def _item(**overrides) -> dict:
    item = {
        "element_type": "theory_component",
        "element_id": "comp-1",
        "document_id": _DOC_A,
        "label": "上位コンポーネント",
        "relation": "supports_component",
        "relation_label": "の根拠となる",
        "relation_status": "source_backed",
        "evidence_refs": ["ev_001"],
        "navigable": True,
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# claim の解決（DB UUID / agent 側 ID / コース document スコープ）
# ---------------------------------------------------------------------------


class TestResolveClaim:
    def test_resolves_by_db_uuid_within_course_scope(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim(_CLAIM_UUID, {_DOC_A}) == (_CLAIM_UUID, _DOC_A)
        assert fake.closed is True

    def test_resolves_by_agent_legacy_id_within_course_scope(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim("claim_span_007", {_DOC_A}) == (_CLAIM_UUID, _DOC_A)

    def test_claim_outside_course_documents_returns_none(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row(document_id=_DOC_B)])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim("claim_span_007", {_DOC_A}) is None

    def test_empty_course_document_ids_returns_none_without_touching_db(self, monkeypatch):
        def _boom():  # pragma: no cover - must not be called
            raise AssertionError("get_session must not be called for an empty document set")

        monkeypatch.setattr(element_context, "get_session", _boom)

        assert element_context._resolve_claim("claim_span_007", set()) is None

    def test_same_agent_id_in_two_documents_only_matches_in_scope_document(self, monkeypatch):
        rows = [
            _claim_row(id="out-of-scope-claim", document_id=_DOC_B),
            _claim_row(id=_CLAIM_UUID, document_id=_DOC_A),
        ]
        fake = _ClaimTableFakeSession(rows)
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim("claim_span_007", {_DOC_A}) == (_CLAIM_UUID, _DOC_A)

    def test_non_uuid_id_does_not_send_uuid_parameter(self, monkeypatch):
        """agent 側 ID のときは ``id = CAST(:uuid_id AS uuid)`` 条件を組まない
        （PostgreSQL のキャストエラーを避ける ``_is_uuid`` ガード）。"""
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        element_context._resolve_claim("claim_span_007", {_DOC_A})

        assert "uuid_id" not in fake.execute_calls[0]


# ---------------------------------------------------------------------------
# equation の解決（コース document 集合の走査のみ = fail-closed）
# ---------------------------------------------------------------------------


class TestResolveEquation:
    def test_scans_course_documents_and_returns_first_match(self, monkeypatch):
        records = {
            _DOC_A: [{"equation_id": "eq_1"}],
            _DOC_B: [{"equation_id": "eq_9"}],
        }
        monkeypatch.setattr(element_context, "equation_records", lambda doc: records.get(doc, []))

        assert element_context._resolve_equation("eq_9", {_DOC_A, _DOC_B}) == ("eq_9", _DOC_B)

    def test_equation_only_in_out_of_course_document_is_not_resolved(self, monkeypatch):
        records = {_DOC_B: [{"equation_id": "eq_9"}]}
        monkeypatch.setattr(element_context, "equation_records", lambda doc: records.get(doc, []))

        assert element_context._resolve_equation("eq_9", {_DOC_A}) is None

    def test_artifact_read_failure_on_one_document_is_fail_soft(self, monkeypatch):
        def _records(doc):
            if doc == _DOC_A:
                raise RuntimeError("artifact read failed")
            return [{"equation_id": "eq_9"}]

        monkeypatch.setattr(element_context, "equation_records", _records)

        assert element_context._resolve_equation("eq_9", {_DOC_A, _DOC_B}) == ("eq_9", _DOC_B)

    def test_empty_document_set_returns_none(self, monkeypatch):
        def _boom(_doc):  # pragma: no cover - must not be called
            raise AssertionError("equation_records must not be called for an empty document set")

        monkeypatch.setattr(element_context, "equation_records", _boom)

        assert element_context._resolve_equation("eq_1", set()) is None


# ---------------------------------------------------------------------------
# 学習者向けフィルタ
# ---------------------------------------------------------------------------


class TestVisibleItems:
    def test_candidate_items_are_excluded(self):
        items = [
            _item(element_id="up-1", relation_status="source_backed"),
            _item(element_id="up-2", relation_status="candidate"),
            _item(element_id="up-3", relation_status="confirmed"),
        ]
        assert [i["id"] for i in element_context._visible_items(items)] == ["up-1", "up-3"]

    def test_internal_reference_keys_are_dropped(self):
        projected = element_context._visible_items([_item()])[0]
        assert set(projected) == {
            "id", "element_type", "label", "relation_label", "relation_status", "navigable",
        }
        assert "evidence_refs" not in projected
        assert "relation" not in projected

    def test_lane_capped_at_max(self):
        items = [_item(element_id=f"e{i}") for i in range(30)]
        assert len(element_context._visible_items(items)) == element_context._LANE_MAX

    def test_non_list_input_yields_empty_lane(self):
        assert element_context._visible_items(None) == []


class TestProjectFocus:
    def test_source_backed_role_is_kept(self):
        focus = element_context._project_focus(
            _lens_result()["focus"], element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID
        )
        assert focus["contextual_role"] == "中心命題を支持する"
        assert focus["contextual_role_status"] == "source_backed"

    def test_candidate_role_is_suppressed(self):
        raw = _lens_result()["focus"]
        raw["contextual_role_status"] = "candidate"
        focus = element_context._project_focus(raw, element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID)
        assert "contextual_role" not in focus
        assert "contextual_role_status" not in focus

    def test_unidentified_role_is_suppressed(self):
        raw = _lens_result()["focus"]
        raw["contextual_role"] = None
        raw["contextual_role_status"] = "unidentified"
        focus = element_context._project_focus(raw, element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID)
        assert "contextual_role" not in focus
        assert "contextual_role_status" not in focus

    def test_internal_provenance_is_dropped(self):
        focus = element_context._project_focus(
            _lens_result()["focus"], element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID
        )
        assert "provenance" not in focus

    def test_public_element_type_overrides_internal_vocabulary(self):
        focus = element_context._project_focus(
            _lens_result()["focus"], element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID
        )
        assert focus["element_type"] == "claim"

    def test_generic_block_is_kept_when_present(self):
        raw = _lens_result()["focus"]
        raw["generic"] = {"entry_id": "lib-1", "name": "共通部品X", "summary": "説明"}
        focus = element_context._project_focus(raw, element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID)
        assert focus["generic"]["name"] == "共通部品X"

    def test_null_generic_key_is_omitted(self):
        focus = element_context._project_focus(
            _lens_result()["focus"], element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID
        )
        assert "generic" not in focus


# ---------------------------------------------------------------------------
# build_element_context: end-to-end
# ---------------------------------------------------------------------------


class TestBuildElementContext:
    def test_unknown_element_type_returns_none(self):
        assert element_context.build_element_context("figure", "f1", {_DOC_A}) is None

    def test_unresolvable_claim_returns_none(self, monkeypatch):
        fake = _ClaimTableFakeSession([])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context.build_element_context("claim", "claim_x", {_DOC_A}) is None

    def test_claim_outside_course_returns_none(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row(document_id=_DOC_B)])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context.build_element_context("claim", "claim_span_007", {_DOC_A}) is None

    def test_full_claim_dto_shape_with_candidate_excluded(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: _lens_result(
                upper=[
                    _item(element_id="up-1", relation_status="source_backed"),
                    _item(element_id="up-2", relation_status="candidate"),
                ],
                lower=[_item(element_type="equation", element_id="eq_1", relation_status="confirmed")],
                notes=["figure_table_semantics artifact が無いため図との関係を判定できません"],
            ),
        )

        result = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        assert result["available"] is True
        assert result["element_type"] == "claim"
        assert result["element_id"] == _CLAIM_UUID  # 解決済みの DB UUID を返す
        assert result["provenance"] == "course_freeze"
        assert [i["id"] for i in result["upper"]] == ["up-1"]
        assert [i["id"] for i in result["lower"]] == ["eq_1"]
        assert result["notes"] == [
            "figure_table_semantics artifact が無いため図との関係を判定できません"
        ]
        assert result["focus"]["label"] == "主張ラベル"

    def test_element_ref_passed_to_lens_uses_internal_vocabulary(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        seen = {}

        def _build(ref):
            seen["ref"] = ref
            return _lens_result()

        monkeypatch.setattr(element_context.context_lens_mod, "build", _build)

        element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        ref = seen["ref"]
        assert ref.element_type == "theory_claim"
        assert ref.element_id == _CLAIM_UUID
        assert ref.document_id == _DOC_A
        assert ref.scope == "document"

    def test_equation_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            element_context, "equation_records", lambda doc: [{"equation_id": "eq_1"}]
        )
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: _lens_result(
                focus={
                    "element_type": "equation",
                    "element_id": "eq_1",
                    "document_id": _DOC_A,
                    "label": "E=mc^2",
                    "intrinsic_summary": "E=mc^2",
                    "contextual_role": None,
                    "contextual_role_status": "unidentified",
                    "provenance": ["equation_semantics:eq_1"],
                },
                lower=[_item(element_type="symbol", element_id=None, navigable=False)],
            ),
        )

        result = element_context.build_element_context("equation", "eq_1", {_DOC_A})

        assert result["available"] is True
        assert result["element_type"] == "equation"
        assert result["element_id"] == "eq_1"
        assert result["focus"]["label"] == "E=mc^2"
        assert "contextual_role" not in result["focus"]
        assert result["lower"][0]["navigable"] is False

    def test_lens_returning_none_yields_available_false(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        monkeypatch.setattr(element_context.context_lens_mod, "build", lambda ref: None)

        result = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        assert result == {"available": False, "note": element_context.NOTE_NO_CONTEXT}

    def test_lens_exception_yields_available_false(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        def _raise(_ref):
            raise RuntimeError("boom")

        monkeypatch.setattr(element_context.context_lens_mod, "build", _raise)

        result = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        assert result == {"available": False, "note": element_context.NOTE_NO_CONTEXT}

    def test_confidence_is_stripped_recursively(self, monkeypatch):
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: _lens_result(
                focus={
                    "label": "主張",
                    "intrinsic_summary": "本文",
                    "contextual_role": "役割",
                    "contextual_role_status": "confirmed",
                    "generic": {"name": "共通部品X", "confidence": 0.91},
                },
                upper=[_item(element_id="up-1")],
                lower=[],
            ),
        )

        result = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        def _walk(value):
            if isinstance(value, dict):
                assert "confidence" not in value
                for v in value.values():
                    _walk(v)
            elif isinstance(value, list):
                for v in value:
                    _walk(v)

        _walk(result)
        assert result["focus"]["generic"]["name"] == "共通部品X"


# ---------------------------------------------------------------------------
# ガードレール
# ---------------------------------------------------------------------------


class TestGuardrails:
    def test_does_not_import_fastapi_or_routes(self):
        src = Path(element_context.__file__).read_text(encoding="utf-8")
        assert_source_does_not_import(src, ["fastapi", "routes", "api."], context="core/element_context.py")

    def test_module_tree_helper_agrees_on_fastapi(self):
        """``assert_module_tree_does_not_import`` でも同じ不変条項を固定する
        （core/ 配下のモジュールが FastAPI を import しない）。"""
        assert_module_tree_does_not_import(
            BACKEND / "core", ["fastapi"], glob="element_context.py"
        )

    def test_no_write_paths(self):
        """読み取り専用: INSERT / UPDATE / DELETE を発行しない。"""
        src = Path(element_context.__file__).read_text(encoding="utf-8").upper()
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert verb not in src, f"element_context.py must not write: {verb}"

    def test_candidate_status_is_excluded_by_construction(self):
        """candidate 除外が「表示可能ホワイトリスト」ではなく candidate の明示除外で
        実装されていても、可視語彙に candidate が含まれないことを固定する。"""
        assert "candidate" not in element_context._LEARNER_VISIBLE_STATUSES
