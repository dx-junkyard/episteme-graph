"""``core/element_context.py``（学習者向け claim / equation 文脈 API の core ロジック）の
単体テスト。

設計書: ``docs/features/learner_element_context_design.md``。

DB 実接続は使わず、``get_session`` を monkeypatch した最小限のフェイクセッション
（``test_component_context_core.py`` の ``_ComponentTableFakeSession`` と同型の方針）、
および ``equation_records`` / ``context_lens_mod.build`` の monkeypatch で検証する。
"""

from __future__ import annotations

import re
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
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return [dict(r) for r in self._rows]


class _ClaimTableFakeSession:
    """実 SQL は評価せず、params（``doc_ids`` / ``raw_id`` / ``uuid_id``）を Python 側で
    同じ意味論（document scope 制約・id 一致・legacy_ids 包含・``id_match`` 付きの
    決定的な並び替え・候補件数の LIMIT）で再現する。"""

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
                matches.append(
                    {
                        "id": row.get("id"),
                        "document_id": row.get("document_id"),
                        "id_match": str(row.get("id")) == raw_id,
                    }
                )
        # ORDER BY (id::text = :raw_id) DESC, document_id ASC, created_at ASC, id::text ASC
        matches.sort(key=lambda r: (not r["id_match"], str(r["document_id"]), str(r["id"])))
        return _FakeMappingsResult(matches[: element_context._CLAIM_CANDIDATE_LIMIT])

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


def _patch_equation_lookup(monkeypatch, records_fn) -> None:
    """equation 解決の外界（artifact 読み取り）を差し替える。

    ``_resolve_equation`` は document ごとに ``document_run_artifacts`` を1回読み、
    その結果を ``equation_records(doc, artifacts=...)`` に渡す（二重 SELECT 回避）。
    テストでは DB へ触らせないため両方を差し替え、``records_fn(doc)`` で
    「その document の equation レコード」だけを与える。
    """
    monkeypatch.setattr(element_context, "document_run_artifacts", lambda doc: {})
    monkeypatch.setattr(
        element_context, "equation_records", lambda doc, artifacts=None: records_fn(doc)
    )


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


class TestResolveClaimAmbiguity:
    """agent 側 ID（``claim_span_001`` 等）は span_id が block ごとに振り直されるため
    文書間・文書内で反復する。コース内で複数行に一致したら「たまたま最初の行」を返さず
    解決を諦める（fail-closed。W層 ``refs._resolve_by_legacy_id`` の裁定と整合）。"""

    def test_same_agent_id_in_two_course_documents_is_ambiguous(self, monkeypatch):
        rows = [
            _claim_row(id="11111111-1111-1111-1111-111111111111", document_id=_DOC_A),
            _claim_row(id="22222222-2222-2222-2222-222222222222", document_id=_DOC_B),
        ]
        fake = _ClaimTableFakeSession(rows)
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim("claim_span_007", {_DOC_A, _DOC_B}) is None

    def test_same_agent_id_twice_in_one_document_is_ambiguous(self, monkeypatch):
        rows = [
            _claim_row(id="11111111-1111-1111-1111-111111111111", document_id=_DOC_A),
            _claim_row(id="22222222-2222-2222-2222-222222222222", document_id=_DOC_A),
        ]
        fake = _ClaimTableFakeSession(rows)
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim("claim_span_007", {_DOC_A}) is None

    def test_single_matching_document_still_resolves(self, monkeypatch):
        """曖昧なのは「一致行が複数」のときだけ。1行に定まるなら従来どおり解決する。"""
        rows = [
            _claim_row(id=_CLAIM_UUID, document_id=_DOC_A),
            _claim_row(id="33333333-3333-3333-3333-333333333333", document_id=_DOC_B,
                       legacy_ids=["claim_span_999"]),
        ]
        fake = _ClaimTableFakeSession(rows)
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim("claim_span_007", {_DOC_A, _DOC_B}) == (
            _CLAIM_UUID,
            _DOC_A,
        )

    def test_uuid_match_wins_even_with_other_legacy_matches(self, monkeypatch):
        """UUID 完全一致は一意なので即決（他文書に同じ文字列を legacy_ids に持つ行が
        あっても曖昧扱いにしない）。"""
        rows = [
            _claim_row(id=_CLAIM_UUID, document_id=_DOC_A, legacy_ids=[]),
            _claim_row(
                id="44444444-4444-4444-4444-444444444444",
                document_id=_DOC_B,
                legacy_ids=[_CLAIM_UUID],
            ),
        ]
        fake = _ClaimTableFakeSession(rows)
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        assert element_context._resolve_claim(_CLAIM_UUID, {_DOC_A, _DOC_B}) == (
            _CLAIM_UUID,
            _DOC_A,
        )

    def test_ambiguous_agent_id_yields_none_from_build(self, monkeypatch):
        """曖昧 → ``build_element_context`` も None（route が 404 → フロントは縮退）。"""
        rows = [
            _claim_row(id="11111111-1111-1111-1111-111111111111", document_id=_DOC_A),
            _claim_row(id="22222222-2222-2222-2222-222222222222", document_id=_DOC_B),
        ]
        fake = _ClaimTableFakeSession(rows)
        monkeypatch.setattr(element_context, "get_session", lambda: fake)

        def _boom(_ref):  # pragma: no cover - must not be called
            raise AssertionError("context_lens must not be built for an ambiguous id")

        monkeypatch.setattr(element_context.context_lens_mod, "build", _boom)

        assert (
            element_context.build_element_context("claim", "claim_span_007", {_DOC_A, _DOC_B})
            is None
        )


# ---------------------------------------------------------------------------
# equation の解決（コース document 集合の走査のみ = fail-closed）
# ---------------------------------------------------------------------------


class TestResolveEquation:
    def test_scans_course_documents_and_returns_first_match(self, monkeypatch):
        records = {
            _DOC_A: [{"equation_id": "eq_1"}],
            _DOC_B: [{"equation_id": "eq_9"}],
        }
        _patch_equation_lookup(monkeypatch, lambda doc: records.get(doc, []))

        assert element_context._resolve_equation("eq_9", {_DOC_A, _DOC_B}) == ("eq_9", _DOC_B)

    def test_equation_only_in_out_of_course_document_is_not_resolved(self, monkeypatch):
        records = {_DOC_B: [{"equation_id": "eq_9"}]}
        _patch_equation_lookup(monkeypatch, lambda doc: records.get(doc, []))

        assert element_context._resolve_equation("eq_9", {_DOC_A}) is None

    def test_artifact_read_failure_on_one_document_is_fail_soft(self, monkeypatch):
        def _records(doc):
            if doc == _DOC_A:
                raise RuntimeError("artifact read failed")
            return [{"equation_id": "eq_9"}]

        _patch_equation_lookup(monkeypatch, _records)

        assert element_context._resolve_equation("eq_9", {_DOC_A, _DOC_B}) == ("eq_9", _DOC_B)

    def test_artifact_read_exception_is_fail_soft(self, monkeypatch):
        """``document_run_artifacts`` 自体が落ちても他 document の走査は続ける。"""
        def _artifacts(doc):
            if doc == _DOC_A:
                raise RuntimeError("run read failed")
            return {"equation_semantics": {"equations": [{"equation_id": "eq_9"}]}}

        monkeypatch.setattr(element_context, "document_run_artifacts", _artifacts)
        monkeypatch.setattr(
            element_context,
            "equation_records",
            lambda doc, artifacts=None: ((artifacts or {}).get("equation_semantics") or {}).get(
                "equations"
            )
            or [],
        )

        assert element_context._resolve_equation("eq_9", {_DOC_A, _DOC_B}) == ("eq_9", _DOC_B)

    def test_empty_document_set_returns_none(self, monkeypatch):
        def _boom(_doc):  # pragma: no cover - must not be called
            raise AssertionError("equation_records must not be called for an empty document set")

        _patch_equation_lookup(monkeypatch, _boom)
        monkeypatch.setattr(
            element_context,
            "document_run_artifacts",
            lambda doc: (_ for _ in ()).throw(
                AssertionError("artifacts must not be read for an empty document set")
            ),
        )

        assert element_context._resolve_equation("eq_1", set()) is None

    def test_artifacts_are_read_once_per_document_and_reused(self, monkeypatch):
        """``document_run_artifacts``（巨大 JSONB）は document ごとに1回だけ読み、
        ``equation_records(doc, artifacts=...)`` に渡して二重読みを避ける。"""
        artifact_reads = []
        passed = []

        def _artifacts(doc):
            artifact_reads.append(doc)
            return {"equation_semantics": {"equations": [{"equation_id": "eq_9"}]}}

        def _records(doc, artifacts=None):
            passed.append((doc, artifacts))
            assert artifacts is not None, "artifacts must be reused, not re-read inside"
            stage = artifacts.get("equation_semantics") or {}
            return stage.get("equations") or []

        monkeypatch.setattr(element_context, "document_run_artifacts", _artifacts)
        monkeypatch.setattr(element_context, "equation_records", _records)

        assert element_context._resolve_equation("eq_9", {_DOC_A}) == ("eq_9", _DOC_A)
        assert artifact_reads == [_DOC_A]
        assert [d for d, _ in passed] == [_DOC_A]


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
        projected = element_context._visible_items([_item(label_source="text")])[0]
        assert set(projected) == {
            "id", "element_type", "label", "sublabel", "qualifier", "group", "unresolved",
            "relation_label", "relation_status", "navigable",
        }
        assert "evidence_refs" not in projected
        assert "relation" not in projected
        # 来歴（label_source）は教員のみ（§6 の学習者/教員差分表）。
        assert "label_source" not in projected

    def test_lane_capped_at_max(self):
        items = [_item(element_id=f"e{i}") for i in range(30)]
        assert len(element_context._visible_items(items)) == element_context._LANE_MAX

    def test_non_list_input_yields_empty_lane(self):
        assert element_context._visible_items(None) == []


class TestInternalIdLabels:
    """W層はラベル解決に失敗した項目に内部 ID をそのまま label として入れる
    （図の DB UUID / ``ev_0001`` / ``synth_claim_0001`` / ``support:...``）。学習者向け
    射影で一般ラベルへ置換する（LE4。関係情報は保持し項目自体は落とさない）。"""

    def test_uuid_label_is_replaced_with_generic_label(self):
        item = _item(
            element_type="figure",
            element_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
            label="ffffffff-ffff-ffff-ffff-ffffffffffff",
            relation="evidenced_by_figure",
            relation_label="を図で裏付ける",
        )
        projected = element_context._visible_items([item])[0]

        assert projected["label"] == "図"
        assert projected["relation_label"] == "を図で裏付ける"  # 関係情報は保持

    def test_evidence_id_label_is_replaced(self):
        item = _item(element_type="evidence", element_id=None, label="ev_0012")
        assert element_context._visible_items([item])[0]["label"] == "本文の根拠箇所"

    def test_synth_claim_and_agent_claim_labels_are_replaced(self):
        for raw in ("synth_claim_0001", "claim_span_001", "claim_0004", "span_001"):
            item = _item(element_type="theory_claim", element_id=None, label=raw)
            assert element_context._visible_items([item])[0]["label"] == "関連する主張", raw

    def test_support_node_id_label_is_replaced(self):
        item = _item(
            element_type="thesis",
            element_id=None,
            label="support:mechanism:3",
            relation="supports_thesis",
            relation_label="（中心命題）を支持する",
        )
        assert element_context._visible_items([item])[0]["label"] == "中心命題"

    def test_label_equal_to_raw_id_is_replaced(self):
        item = _item(element_type="figure", element_id="fig_5_2", label="fig_5_2")
        assert element_context._visible_items([item])[0]["label"] == "図"

    def test_equation_number_label_is_kept(self):
        """``eq_2_7`` は論文の式番号由来で学習者にも可読なため置換しない（§4 の裁定）。"""
        item = _item(
            element_type="equation", element_id="eq_2_7", label="eq_2_7",
            relation="quantified_by", relation_label="に定量化される",
        )
        assert element_context._visible_items([item])[0]["label"] == "eq_2_7"

    def test_normal_labels_are_untouched(self):
        for label in ("上位コンポーネント", "E=mc^2", "第3節 実験装置", "\\alpha"):
            item = _item(label=label)
            assert element_context._visible_items([item])[0]["label"] == label, label

    def test_unknown_element_type_falls_back_to_generic_label(self):
        item = _item(
            element_type="mystery",
            element_id="11111111-1111-1111-1111-111111111111",
            label="11111111-1111-1111-1111-111111111111",
        )
        assert element_context._visible_items([item])[0]["label"] == "関連する要素"


class TestNavigable:
    """``navigable`` は「学習者が実際に再フェッチできるか」で作り直す（W層の値は
    教員向けの可否なので、そのままでは契約が成立しない）。"""

    def test_learner_fetchable_types_are_navigable(self):
        for element_type in ("theory_claim", "equation", "theory_component"):
            item = _item(element_type=element_type, element_id="x1", navigable=True)
            assert element_context._visible_items([item])[0]["navigable"] is True, element_type

    def test_types_without_learner_context_api_are_not_navigable(self):
        for element_type in (
            "figure", "section", "thesis", "derivation", "symbol", "evidence", "stage", "part",
        ):
            item = _item(element_type=element_type, element_id="x1", navigable=True)
            assert element_context._visible_items([item])[0]["navigable"] is False, element_type

    def test_missing_id_is_never_navigable(self):
        item = _item(element_type="theory_claim", element_id=None, navigable=True)
        assert element_context._visible_items([item])[0]["navigable"] is False


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

    def test_role_containing_internal_id_is_dropped(self):
        """役割文は W層が上位項目のラベルから合成するため、ラベル解決に失敗した項目の
        内部 ID がそのまま「この論文での役割」に出る。含まれていたらキーごと落とす。"""
        for role in (
            "synth_claim_0001を定量化する",
            "claim_span_001の下位主張である",
            "ffffffff-ffff-ffff-ffff-ffffffffffffに証拠を与える",
            "support:mechanism:3（中心命題）を支持する",
            "ev_0012を根拠とする",
        ):
            raw = _lens_result()["focus"]
            raw["contextual_role"] = role
            raw["contextual_role_status"] = "source_backed"
            focus = element_context._project_focus(
                raw, element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID
            )
            assert "contextual_role" not in focus, role
            assert "contextual_role_status" not in focus, role

    def test_role_without_internal_id_is_kept(self):
        for role in (
            "中心命題を支持する",
            "エネルギー保存則を定量化する",
            "支持構造「機構」: 観測値の再現に成功したを支持する",
        ):
            raw = _lens_result()["focus"]
            raw["contextual_role"] = role
            raw["contextual_role_status"] = "source_backed"
            focus = element_context._project_focus(
                raw, element_context.ELEMENT_TYPE_CLAIM, _CLAIM_UUID
            )
            assert focus["contextual_role"] == role, role

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
        _patch_equation_lookup(monkeypatch, lambda doc: [{"equation_id": "eq_1"}])
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

    def test_degenerate_lens_dict_yields_available_false(self, monkeypatch):
        """``context_lens.build()`` は builder 失敗時にも dict（``_degenerate_result``）を
        返す。これは投影ではなく「読めなかった」形なので、``available:true`` +
        ``focus.label = "<uuid>"`` で出さない（LE8）。"""
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {
                    "element_type": "theory_claim",
                    "element_id": ref.element_id,
                    "document_id": ref.document_id,
                    "label": ref.element_id,
                    "intrinsic_summary": "",
                    "contextual_role": None,
                    "contextual_role_status": "unidentified",
                    "provenance": [],
                    "generic": None,
                },
                "upper": [],
                "lower": [],
                "notes": ["要素が見つからないか読み取りに失敗したため、文脈は表示できません"],
            },
        )

        result = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        assert result == {"available": False, "note": element_context.NOTE_NO_CONTEXT}

    def test_sparse_but_real_projection_is_not_treated_as_degenerate(self, monkeypatch):
        """上位・下位が空でもラベル・本文が引けている投影は正常（縮退させない）。判定は
        4条件すべてを要求する保守的な形にしてある。"""
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: _lens_result(
                upper=[],
                lower=[],
                notes=["thesis_reconstruction artifact が無いため中心命題との関係を判定できません"],
            ),
        )

        result = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})

        assert result["available"] is True
        assert result["focus"]["label"] == "主張ラベル"

    def test_empty_lanes_with_resolved_label_and_no_degenerate_note_stay_available(
        self, monkeypatch
    ):
        """degenerate の目印（専用 note）が無ければ、レーンが空でも縮退させない。"""
        fake = _ClaimTableFakeSession([_claim_row()])
        monkeypatch.setattr(element_context, "get_session", lambda: fake)
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: _lens_result(upper=[], lower=[], notes=[]),
        )

        assert (
            element_context.build_element_context("claim", "claim_span_007", {_DOC_A})["available"]
            is True
        )

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


# ---------------------------------------------------------------------------
# EC1〜EC5: 数式「文脈を見る」パネルの表示是正
# docs/features/equation_context_panel_display_design.md
# ---------------------------------------------------------------------------


class TestEquationPanelDisplay:
    """W層 ``_equation_label`` は plain_text の無い式で latex を採用し 80 字で
    切り詰めるため、focus.label / intrinsic_summary に「コマンド途中で切れた TeX」が
    入る。KaTeX に渡せば赤いエラー、素で出せば読めない文字列にしかならないので、
    学習者向け射影で落とす（式そのものは教材本文に整形表示されている）。
    """

    _TRUNCATED_TEX = (
        "\\begin{aligned} \\delta(t, {\\bm{x}}) {} \\overset{\\mathrm{}}{{}:={}}{} \\frac{\\rho("
    )

    def test_truncated_tex_is_dropped_from_label_and_summary(self):
        focus = element_context._project_focus(
            {"label": self._TRUNCATED_TEX, "intrinsic_summary": self._TRUNCATED_TEX},
            element_context.ELEMENT_TYPE_EQUATION,
            "eq_tex_b14",
        )
        assert "\\begin" not in focus["label"]
        assert "\\begin" not in focus["intrinsic_summary"]

    def test_synthetic_id_yields_empty_label(self):
        """EC1: 合成 ID は見出しにしない（種別チップだけになる）。"""
        focus = element_context._project_focus(
            {"label": self._TRUNCATED_TEX, "intrinsic_summary": self._TRUNCATED_TEX},
            element_context.ELEMENT_TYPE_EQUATION,
            "eq_tex_b14",
        )
        assert focus["label"] == ""

    def test_paper_equation_number_is_kept_as_label(self):
        focus = element_context._project_focus(
            {"label": self._TRUNCATED_TEX, "intrinsic_summary": self._TRUNCATED_TEX},
            element_context.ELEMENT_TYPE_EQUATION,
            "eq_2_7",
        )
        assert focus["label"] == "eq_2_7"

    def test_readable_reading_is_preserved(self):
        """読み下し（plain_text 由来）は TeX ではないので残す。"""
        focus = element_context._project_focus(
            {"label": "デルタは密度コントラスト", "intrinsic_summary": "デルタは密度コントラスト"},
            element_context.ELEMENT_TYPE_EQUATION,
            "eq_2_7",
        )
        assert focus["label"] == "デルタは密度コントラスト"
        assert focus["intrinsic_summary"] == "デルタは密度コントラスト"

    def test_explanatory_fields_come_from_the_equation_record(self):
        """EC5: ホバーと同じ材料（役割 / 意味の要約 / 記号の意味）を focus に載せる。"""
        focus = element_context._project_focus(
            {"label": self._TRUNCATED_TEX, "intrinsic_summary": self._TRUNCATED_TEX},
            element_context.ELEMENT_TYPE_EQUATION,
            "eq_2_7",
            equation_record={
                "equation_id": "eq_2_7",
                "semantics": {
                    "role_in_argument": "definition",
                    "summary": "密度揺らぎの定義",
                    "defined_symbols": [
                        {"symbol": "\\delta", "meaning": "物質密度揺らぎ"},
                        {"symbol": "k"},  # meaning 無しは落とす
                    ],
                },
            },
        )
        assert focus["role_in_argument"] == "definition"
        assert focus["semantic_kind"] == "密度揺らぎの定義"
        assert focus["symbols"] == [{"symbol": "\\delta", "meaning": "物質密度揺らぎ"}]

    def test_explanatory_fields_absent_when_no_record(self):
        """材料が無ければキーごと出さない（推測で埋めない）。"""
        focus = element_context._project_focus(
            {"label": "", "intrinsic_summary": ""},
            element_context.ELEMENT_TYPE_EQUATION,
            "eq_2_7",
        )
        assert "role_in_argument" not in focus
        assert "symbols" not in focus

    def test_lane_equation_label_drops_tex(self):
        """EC1: 上位・下位レーンの相手式ラベルも同じ経路で TeX になる。"""
        item = element_context._project_item({
            "element_type": "equation",
            "element_id": "eq_tex_b16",
            "label": self._TRUNCATED_TEX,
            "relation_label": "につながる",
            "relation_status": element_context.CONTEXT_STATUS_SOURCE_BACKED,
        })
        assert "\\begin" not in item["label"]
        assert item["label"] == "関連する数式"
        assert item["relation_label"] == "につながる"

    def test_derivation_sentence_with_internal_ids_is_replaced(self):
        """EC3: ID を埋め込んだ事実文も遮る。関係の意味は保持する。"""
        item = element_context._project_item({
            "element_type": "derivation",
            "element_id": "derivation_eq_tex_b16",
            "label": "導出「derivation_eq_tex_b16」のステップ「step_001」",
            "relation_label": "の導出に属する",
            "relation_status": element_context.CONTEXT_STATUS_SOURCE_BACKED,
        })
        assert "derivation_eq_tex_b16" not in item["label"]
        assert "step_001" not in item["label"]
        assert item["label"] == "導出の流れ"
        assert item["relation_label"] == "の導出に属する"

    def test_system_derivation_sentence_is_replaced(self):
        item = element_context._project_item({
            "element_type": "derivation",
            "element_id": "system_derivation_0001",
            "label": "導出「system_derivation_0001」のステップ「sys_001_step_1」",
            "relation_label": "の導出に属する",
            "relation_status": element_context.CONTEXT_STATUS_SOURCE_BACKED,
        })
        assert "system_derivation_0001" not in item["label"]
        assert "sys_001_step_1" not in item["label"]

    def test_human_derivation_label_is_not_touched(self):
        """内部 ID を含まない導出ラベルはそのまま残す（過剰置換しない）。"""
        item = element_context._project_item({
            "element_type": "derivation",
            "element_id": "derivation_eq_tex_b16",
            "label": "線形化から成長方程式までの導出",
            "relation_label": "の導出に属する",
            "relation_status": element_context.CONTEXT_STATUS_SOURCE_BACKED,
        })
        assert item["label"] == "線形化から成長方程式までの導出"


# ---------------------------------------------------------------------------
# ITEM v2 / focus v2 の学習者射影
# docs/features/element_context_presentation_redesign.md §4（DTO 契約 v2）/ §6
# ---------------------------------------------------------------------------


_TRUNCATED_TEX = (
    "\\begin{aligned} \\delta(t, {\\bm{x}}) {} \\overset{\\mathrm{}}{{}:={}}{} \\frac{\\rho("
)


def _v2_item(**overrides) -> dict:
    """ITEM v2（追加キー込み）の素材。"""
    item = _item()
    item.update(
        {
            "sublabel": "この節で導入される観測量",
            "qualifier": "definition",
            "group": "claim",
            "unresolved": False,
            "label_source": "text",
        }
    )
    item.update(overrides)
    return item


class TestItemV2Keys:
    """W層が新しく載せる区別材料（sublabel / qualifier / group / unresolved）を
    透過し、来歴（label_source）だけを落とす。"""

    def test_new_keys_are_passed_through(self):
        projected = element_context._project_item(_v2_item())

        assert projected["sublabel"] == "この節で導入される観測量"
        assert projected["qualifier"] == "definition"
        assert projected["group"] == "claim"
        assert projected["unresolved"] is False

    def test_missing_new_keys_yield_defaults(self):
        """旧形の ITEM（v1）でもキーは必ず生える（フロントの分岐を単純にする）。"""
        projected = element_context._project_item(_item())

        assert projected["sublabel"] == ""
        assert projected["qualifier"] == ""
        assert projected["group"] == element_context.ITEM_GROUP_FALLBACK
        assert projected["unresolved"] is False

    def test_unknown_group_falls_back_to_related(self):
        for raw in ("", None, "mystery_group"):
            projected = element_context._project_item(_v2_item(group=raw))
            assert projected["group"] == "related", raw

    def test_known_groups_are_preserved(self):
        for group in element_context.ITEM_GROUPS:
            projected = element_context._project_item(_v2_item(group=group))
            assert projected["group"] == group, group

    def test_unresolved_flag_from_lens_is_preserved(self):
        projected = element_context._project_item(_v2_item(unresolved=True))
        assert projected["unresolved"] is True


class TestEquationDetailLayerIsHiddenFromLearners:
    """CP3: 式単位の操作ノード（traceability 層）は学習者に出さない。"""

    def test_equation_detail_item_is_dropped_entirely(self):
        item = _v2_item(
            element_type="theory_component",
            element_id="theory-op-uuid",
            label="消去: 背景密度",
            qualifier=element_context.QUALIFIER_EQUATION_DETAIL,
            group="operation",
        )
        assert element_context._project_item(item) is None

    def test_lane_keeps_other_items_and_drops_only_detail_layer(self):
        items = [
            _v2_item(element_id="keep-1", qualifier="definition", group="claim"),
            _v2_item(
                element_id="detail-1",
                qualifier=element_context.QUALIFIER_EQUATION_DETAIL,
                group="operation",
            ),
            _v2_item(element_id="keep-2", qualifier="", group="stage"),
        ]
        assert [i["id"] for i in element_context._visible_items(items)] == ["keep-1", "keep-2"]

    def test_main_stage_item_is_kept(self):
        """main 層（理論段階）は学習者にも出す（区画②）。"""
        item = _v2_item(
            element_type="stage",
            element_id=None,
            label="理論の土台",
            sublabel="観測量を密度ゆらぎとして定義する",
            qualifier="theory_basis",
            group="stage",
            relation_label="の理論段階に属する",
        )
        projected = element_context._project_item(item)

        assert projected["label"] == "理論の土台"
        assert projected["sublabel"] == "観測量を密度ゆらぎとして定義する"
        assert projected["qualifier"] == "theory_basis"
        assert projected["navigable"] is False


class TestSublabelGating:
    """sublabel は区別材料なので**項目は落とさず欄だけ**を遮断する。"""

    def test_sublabel_with_embedded_internal_id_is_emptied(self):
        for sublabel in (
            "導出「derivation_eq_tex_b16」のステップ「step_001」",
            "synth_claim_0001 に由来する",
            "ffffffff-ffff-ffff-ffff-ffffffffffff の図に対応",
            "ev_0012 を根拠とする",
            "theory_op_0001 のノード",
        ):
            projected = element_context._project_item(_v2_item(sublabel=sublabel))
            assert projected["sublabel"] == "", sublabel
            assert projected["label"] == "上位コンポーネント", sublabel  # 項目は残す

    def test_sublabel_with_raw_tex_is_emptied(self):
        projected = element_context._project_item(_v2_item(sublabel=_TRUNCATED_TEX))
        assert projected["sublabel"] == ""

    def test_plain_sublabel_is_kept(self):
        for sublabel in (
            "操作: transform → linearize",
            "式 (2.7) から導かれる",
            "第3.2節",
            "系レベルの操作 ／ 条件: 線形領域",
        ):
            projected = element_context._project_item(_v2_item(sublabel=sublabel))
            assert projected["sublabel"] == sublabel, sublabel


class TestGenericLabelKeepsSublabel:
    """RC6 の回帰テスト: 一般ラベルへ縮退しても sublabel で区別できること。"""

    def test_generic_replacement_sets_unresolved_and_keeps_sublabel(self):
        item = _v2_item(
            element_type="theory_claim",
            element_id=None,
            label="synth_claim_0001",
            sublabel="密度コントラストの定義に依存する",
            group="claim",
        )
        projected = element_context._project_item(item)

        assert projected["label"] == "関連する主張"
        assert projected["unresolved"] is True
        assert projected["sublabel"] == "密度コントラストの定義に依存する"

    def test_two_generic_derivation_items_stay_distinguishable(self):
        """スクショの「導出の流れ」×2（区別不能）が、sublabel + qualifier で解ける。"""
        items = [
            _v2_item(
                element_type="derivation",
                element_id="derivation_eq_tex_b16",
                label="導出「derivation_eq_tex_b16」のステップ「step_001」",
                sublabel="操作: transform → linearize",
                qualifier="equation_chain",
                group="derivation_out",
                relation_label="の導出に入力として使われる",
            ),
            _v2_item(
                element_type="derivation",
                element_id="system_derivation_0001",
                label="導出「system_derivation_0001」のステップ「sys_001_step_1」",
                sublabel="操作: eliminate ／ 消える記号: b_s",
                qualifier="system_level",
                group="derivation_out",
                relation_label="の導出に入力として使われる",
            ),
        ]
        projected = element_context._visible_items(items)

        assert [i["label"] for i in projected] == ["導出の流れ", "導出の流れ"]
        assert [i["unresolved"] for i in projected] == [True, True]
        # CP7: label + sublabel の組が同一になる2件を作らない。
        pairs = {(i["label"], i["sublabel"]) for i in projected}
        assert len(pairs) == 2
        assert [i["qualifier"] for i in projected] == ["equation_chain", "system_level"]

    def test_equation_tex_label_without_paper_number_sets_unresolved(self):
        projected = element_context._project_item(
            _v2_item(
                element_type="equation",
                element_id="eq_tex_b16",
                label=_TRUNCATED_TEX,
                sublabel="フーリエ空間の密度コントラスト",
                group="equation_down",
            )
        )
        assert projected["label"] == "関連する数式"
        assert projected["unresolved"] is True
        assert projected["sublabel"] == "フーリエ空間の密度コントラスト"

    def test_equation_tex_label_with_paper_number_is_not_unresolved(self):
        projected = element_context._project_item(
            _v2_item(
                element_type="equation", element_id="eq_2_7", label=_TRUNCATED_TEX, group="equation_up"
            )
        )
        assert projected["label"] == "eq_2_7"
        assert projected["unresolved"] is False


class TestSymbolItems:
    """記号は式の再掲ではなく読解の部品（§5.4）。label は TeX でも遮断しない。"""

    def test_tex_symbol_label_is_not_blocked(self):
        projected = element_context._project_item(
            _v2_item(
                element_type="symbol",
                element_id=None,
                label="\\delta(t,\\bm{x})",
                sublabel="密度コントラスト",
                qualifier="defined",
                group="symbol_defined",
                relation_label="で記号を定義する",
            )
        )
        assert projected["label"] == "\\delta(t,\\bm{x})"
        assert projected["sublabel"] == "密度コントラスト"
        assert projected["group"] == "symbol_defined"
        assert projected["navigable"] is False

    def test_non_symbol_tex_labels_fall_back_to_generic(self):
        """記号以外の型は生 TeX を出さない（EC1。equation 以外の型でも同じ）。"""
        projected = element_context._project_item(
            _v2_item(
                element_type="evidence",
                element_id=None,
                label=_TRUNCATED_TEX,
                sublabel="2.1 Density contrast",
                group="evidence",
            )
        )
        assert projected["label"] == "本文の根拠箇所"
        assert projected["unresolved"] is True
        assert projected["sublabel"] == "2.1 Density contrast"

    def test_defined_and_used_symbols_are_separate_groups(self):
        items = [
            _v2_item(element_type="symbol", element_id=None, label="δ", group="symbol_defined",
                     relation_label="で記号を定義する", sublabel="密度コントラスト"),
            _v2_item(element_type="symbol", element_id=None, label="ρ", group="symbol_used",
                     relation_label="を用いる", sublabel="物質密度"),
        ]
        projected = element_context._visible_items(items)
        assert [i["group"] for i in projected] == ["symbol_defined", "symbol_used"]
        assert [i["relation_label"] for i in projected] == ["で記号を定義する", "を用いる"]


# ---------------------------------------------------------------------------
# focus v2（headline / intrinsic / placement / contextual_role_source / derivations）
# ---------------------------------------------------------------------------


def _v2_focus(**overrides) -> dict:
    focus = {
        "element_type": "equation",
        "element_id": "eq_2_7",
        "document_id": _DOC_A,
        "label": "δ(t,x) を定義する式",
        "headline": "δ(t,x) を定義する式",
        "intrinsic_summary": "",
        "intrinsic": {
            "kind_key": "definition",
            "role_key": "definition",
            "summary": "Definition of the matter density contrast.",
            "summary_is_source_language": True,
            "reading": "",
            "symbols": [
                {
                    "symbol": "\\delta(t,\\bm{x})",
                    "meaning": "密度コントラスト",
                    "defined_here": True,
                    "definition_status": "defined",
                },
                {"symbol": "\\rho(t)", "meaning": "物質密度", "defined_here": False},
            ],
            "conditions": ["線形領域であること"],
            "facts": ["この式は定義であり、前段の式を持ちません。"],
        },
        "placement": {
            "section_label": "2.1 Density contrast",
            "stage": {"key": "theory_basis", "description": "観測量を密度ゆらぎとして定義する"},
            "thesis_role": "中心命題の観測量を定義する",
        },
        "contextual_role": "理論の土台の段階で使われる定義式",
        "contextual_role_status": "source_backed",
        "contextual_role_source": "self_described",
        "review_notes": ["atomic claim が紐づいていません"],
        "provenance": ["equation_semantics:eq_2_7"],
        "generic": None,
    }
    focus.update(overrides)
    return focus


def _project_equation_focus(focus: dict) -> dict:
    return element_context._project_focus(
        focus, element_context.ELEMENT_TYPE_EQUATION, "eq_2_7"
    )


class TestFocusV2Keys:
    def test_headline_intrinsic_placement_are_passed_through(self):
        projected = _project_equation_focus(_v2_focus())

        assert projected["headline"] == "δ(t,x) を定義する式"
        assert projected["intrinsic"]["role_key"] == "definition"
        assert projected["intrinsic"]["summary"].startswith("Definition of the matter")
        assert projected["intrinsic"]["summary_is_source_language"] is True
        assert projected["intrinsic"]["conditions"] == ["線形領域であること"]
        assert projected["intrinsic"]["facts"] == ["この式は定義であり、前段の式を持ちません。"]
        assert projected["placement"]["section_label"] == "2.1 Density contrast"
        assert projected["placement"]["stage"] == {
            "key": "theory_basis",
            "description": "観測量を密度ゆらぎとして定義する",
        }
        assert projected["placement"]["thesis_role"] == "中心命題の観測量を定義する"

    def test_review_notes_are_dropped(self):
        """要確認事項は教員のみ（§6 の差分表）。"""
        assert "review_notes" not in _project_equation_focus(_v2_focus())

    def test_headline_falls_back_to_label(self):
        projected = _project_equation_focus(_v2_focus(headline=""))
        assert projected["headline"] == "δ(t,x) を定義する式"

    def test_headline_with_raw_tex_falls_back_to_label(self):
        projected = _project_equation_focus(
            _v2_focus(headline=_TRUNCATED_TEX, label="式 (2.7)")
        )
        assert projected["headline"] == "式 (2.7)"

    def test_symbol_meanings_survive_and_tex_symbols_are_not_blocked(self):
        """RC5: 記号の意味が evidence_refs に押し込まれて消える問題の回帰テスト。"""
        symbols = _project_equation_focus(_v2_focus())["intrinsic"]["symbols"]

        assert symbols[0]["symbol"] == "\\delta(t,\\bm{x})"
        assert symbols[0]["meaning"] == "密度コントラスト"
        assert symbols[0]["defined_here"] is True
        assert symbols[0]["definition_status"] == "defined"

    def test_reading_with_tex_is_dropped(self):
        projected = _project_equation_focus(_v2_focus(
            intrinsic={"reading": _TRUNCATED_TEX, "summary": "密度コントラストの定義"}
        ))
        assert "reading" not in projected["intrinsic"]
        assert projected["intrinsic"]["summary"] == "密度コントラストの定義"

    def test_readable_reading_is_kept(self):
        projected = _project_equation_focus(_v2_focus(
            intrinsic={"reading": "デルタは、密度から背景密度を引いて背景密度で割った量"}
        ))
        assert projected["intrinsic"]["reading"].startswith("デルタは")

    def test_internal_ids_are_dropped_from_summary_facts_and_stage_description(self):
        projected = _project_equation_focus(_v2_focus(
            intrinsic={
                "summary": "synth_claim_0001 の定義",
                "facts": ["ev_0012 に対応する", "この式は定義であり、前段の式を持ちません。"],
                "conditions": ["step_001 の直後に成立"],
            },
            placement={
                "section_label": "2.1 Density contrast",
                "stage": {"key": "theory_basis", "description": "theory_op_0001 のノード"},
            },
        ))

        assert "summary" not in projected["intrinsic"]
        assert projected["intrinsic"]["facts"] == ["この式は定義であり、前段の式を持ちません。"]
        assert "conditions" not in projected["intrinsic"]
        assert projected["placement"]["stage"] == {"key": "theory_basis"}

    def test_empty_intrinsic_and_placement_keys_are_omitted(self):
        projected = _project_equation_focus(_v2_focus(intrinsic={}, placement={}))
        assert "intrinsic" not in projected
        assert "placement" not in projected


class TestContextualRoleSource:
    """§4.4: 学習者射影が落とすのは ``unidentified`` のみ（candidate 判定からの移行）。"""

    def test_self_described_role_is_kept_even_with_candidate_status(self):
        projected = _project_equation_focus(
            _v2_focus(contextual_role_source="self_described", contextual_role_status="candidate")
        )
        assert projected["contextual_role"] == "理論の土台の段階で使われる定義式"
        assert projected["contextual_role_source"] == "self_described"

    def test_committed_and_structural_roles_are_kept(self):
        for source in ("committed", "structural"):
            projected = _project_equation_focus(_v2_focus(contextual_role_source=source))
            assert projected["contextual_role_source"] == source, source
            assert projected["contextual_role"], source

    def test_unidentified_source_drops_the_whole_role_block(self):
        projected = _project_equation_focus(
            _v2_focus(contextual_role_source="unidentified", contextual_role_status="source_backed")
        )
        assert "contextual_role" not in projected
        assert "contextual_role_status" not in projected
        assert "contextual_role_source" not in projected

    def test_role_with_internal_id_is_dropped_regardless_of_source(self):
        projected = _project_equation_focus(
            _v2_focus(contextual_role="synth_claim_0001を定量化する",
                      contextual_role_source="structural")
        )
        assert "contextual_role" not in projected
        assert "contextual_role_source" not in projected

    def test_legacy_focus_without_source_falls_back_to_status(self):
        """来歴キーを持たない旧形の投影は従来どおり status で判定する（後方互換）。"""
        kept = _project_equation_focus(
            _v2_focus(contextual_role_source="", contextual_role_status="source_backed")
        )
        dropped = _project_equation_focus(
            _v2_focus(contextual_role_source="", contextual_role_status="candidate")
        )
        assert kept["contextual_role"] == "理論の土台の段階で使われる定義式"
        assert "contextual_role_source" not in kept
        assert "contextual_role" not in dropped


class TestDerivationsProjection:
    """§4.4 derivations[] ストーリーブロックの学習者射影。"""

    def _derivation(self, **overrides) -> dict:
        entry = {
            "derivation_id": "derivation_eq_tex_b16",
            "label": "フーリエ変換による書き換え（式の導出）",
            "sublabel": "操作: transform → linearize",
            "chain_type": "equation_chain",
            "operation_text": "transform → linearize",
            "focus_role": "input",
            "inputs": [
                {
                    "element_type": "equation",
                    "element_id": "eq_2_7",
                    "label": "δ(t,x) を定義する式",
                    "navigable": True,
                }
            ],
            "outputs": [
                {
                    "element_type": "equation",
                    "element_id": "eq_tex_b16",
                    "label": _TRUNCATED_TEX,
                    "navigable": True,
                }
            ],
            "eliminated_symbols": ["b_s"],
            "retained_symbols": ["\\delta_k"],
            "reason": "フーリエ空間での表現へ移す",
            "relation_status": "source_backed",
            "evidence_refs": ["step_001"],
            "navigable": True,
        }
        entry.update(overrides)
        return entry

    def test_candidate_derivations_are_excluded(self):
        focus = _v2_focus(derivations=[
            self._derivation(relation_status="candidate"),
            self._derivation(label="系レベルの消去", relation_status="source_backed"),
        ])
        projected = _project_equation_focus(focus)

        assert [d["label"] for d in projected["derivations"]] == ["系レベルの消去"]

    def test_internal_reference_keys_are_dropped(self):
        projected = _project_equation_focus(_v2_focus(derivations=[self._derivation()]))
        entry = projected["derivations"][0]

        assert "derivation_id" not in entry
        assert "evidence_refs" not in entry
        assert entry["chain_type"] == "equation_chain"
        assert entry["focus_role"] == "input"
        assert entry["operation_text"] == "transform → linearize"
        assert entry["reason"] == "フーリエ空間での表現へ移す"

    def test_derivation_is_never_navigable_for_learners(self):
        projected = _project_equation_focus(_v2_focus(derivations=[self._derivation()]))
        assert projected["derivations"][0]["navigable"] is False

    def test_mini_refs_recompute_navigable_and_replace_tex_labels(self):
        entry = _project_equation_focus(
            _v2_focus(derivations=[self._derivation()])
        )["derivations"][0]

        assert entry["inputs"][0]["navigable"] is True  # equation は旅の対象
        assert entry["outputs"][0]["label"] == "関連する数式"
        assert entry["outputs"][0]["navigable"] is True

    def test_mini_refs_of_non_navigable_types_are_forced_false(self):
        entry = _project_equation_focus(_v2_focus(derivations=[self._derivation(
            inputs=[
                {"element_type": "symbol", "element_id": None, "label": "b_s", "navigable": True},
                {"element_type": "evidence", "element_id": "ev_0012", "label": "ev_0012",
                 "navigable": True},
            ],
        )]))["derivations"][0]

        assert [i["navigable"] for i in entry["inputs"]] == [False, False]
        assert entry["inputs"][1]["label"] == "本文の根拠箇所"

    def test_reason_and_sublabel_with_internal_ids_are_emptied(self):
        entry = _project_equation_focus(_v2_focus(derivations=[self._derivation(
            reason="step_001 で b_s を消去する",
            sublabel="導出「derivation_eq_tex_b16」の一部",
        )]))["derivations"][0]

        assert entry["reason"] == ""
        assert entry["sublabel"] == ""
        assert entry["label"] == "フーリエ変換による書き換え（式の導出）"  # 項目は残す

    def test_label_with_internal_id_falls_back_to_generic(self):
        entry = _project_equation_focus(_v2_focus(derivations=[self._derivation(
            label="導出「derivation_eq_tex_b16」のステップ「step_001」",
        )]))["derivations"][0]

        assert entry["label"] == "導出の流れ"

    def test_symbol_lists_keep_tex_forms(self):
        entry = _project_equation_focus(_v2_focus(derivations=[self._derivation()]))["derivations"][0]

        assert entry["eliminated_symbols"] == ["b_s"]
        assert entry["retained_symbols"] == ["\\delta_k"]

    def test_absent_derivations_key_is_omitted(self):
        assert "derivations" not in _project_equation_focus(_v2_focus())


# ---------------------------------------------------------------------------
# 学習者 DTO 全体の再帰走査（内部 ID / 生 TeX がゼロであること）
# ---------------------------------------------------------------------------


# 内部 ID の形（テスト側で独立に定義する — 実装の正規表現を流用すると
# 「実装が見落とす形」を検出できないため）。
_INTERNAL_ID_TOKEN_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|synth_[A-Za-z0-9_]*[0-9]"
    r"|claim_span_[0-9]"
    r"|(?:^|[^A-Za-z0-9])(?:ev|evidence)_[0-9]{3,}"
    r"|(?:^|[^A-Za-z0-9])span_[0-9]{3,}"
    r"|(?:^|[^A-Za-z0-9])step_[0-9]"
    r"|(?:^|[^A-Za-z0-9])(?:system_)?derivation_[A-Za-z0-9_]+"
    r"|(?:^|[^A-Za-z0-9])(?:theory_op|eq_op)_[0-9]"
    r"|support:",
)

# ITEM.id / element_id / document_id は契約上残る（教材内ジャンプ・再フェッチに使う）。
# 禁じているのは**表示文字列**に内部 ID が現れることなので、ID キーは走査から外す。
_ID_KEYS = frozenset({"id", "element_id", "document_id"})

# 記号は TeX 形のまま渡す（§5.4。レンダリング可否はフロントのゲート）。
_SYMBOL_TEXT_KEYS = frozenset({"symbol", "eliminated_symbols", "retained_symbols"})


def _walk_learner_strings(value, path="", *, symbol_slot=False):
    """(path, text, symbol_slot) を列挙する再帰走査。"""
    if isinstance(value, dict):
        is_symbol_item = str(value.get("element_type") or "") == "symbol"
        for key, child in value.items():
            if key in _ID_KEYS:
                continue
            child_symbol = (
                key in _SYMBOL_TEXT_KEYS or (is_symbol_item and key == "label")
            )
            yield from _walk_learner_strings(
                child, f"{path}.{key}", symbol_slot=child_symbol
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_learner_strings(child, f"{path}[{index}]", symbol_slot=symbol_slot)
    elif isinstance(value, str):
        yield path, value, symbol_slot


class TestLearnerDtoHasNoInternalIdsOrTex:
    """DTO 全体（v2 の新キーを含む）を再帰走査し、内部 ID と生 TeX がゼロであること。"""

    def _dirty_lens(self) -> dict:
        """W層のラベル解決が総崩れした最悪ケース（遮断層＝最後の砦の検証）。"""
        return {
            "focus": {
                "element_type": "equation",
                "element_id": "eq_tex_b14",
                "document_id": _DOC_A,
                "label": _TRUNCATED_TEX,
                "headline": _TRUNCATED_TEX,
                "intrinsic_summary": _TRUNCATED_TEX,
                "intrinsic": {
                    "kind_key": "definition",
                    "summary": "synth_claim_0001 の定義",
                    "reading": _TRUNCATED_TEX,
                    "symbols": [
                        {"symbol": "\\delta", "meaning": "ev_0012 に定義がある"},
                        {"symbol": "\\rho", "meaning": "物質密度"},
                    ],
                    "conditions": [_TRUNCATED_TEX, "線形領域であること"],
                    "facts": ["step_001 の直後", "この式は定義であり、前段の式を持ちません。"],
                },
                "placement": {
                    "section_label": "2.1 Density contrast",
                    "stage": {"key": "theory_basis", "description": "theory_op_0001"},
                    "thesis_role": "support:mechanism:3",
                },
                "contextual_role": "synth_claim_0001を定量化する",
                "contextual_role_source": "structural",
                "contextual_role_status": "source_backed",
                "review_notes": ["derivation_eq_tex_b16 の要確認"],
                "provenance": ["equation_semantics:eq_tex_b14"],
                "derivations": [
                    {
                        "derivation_id": "derivation_eq_tex_b16",
                        "label": "導出「derivation_eq_tex_b16」のステップ「step_001」",
                        "sublabel": _TRUNCATED_TEX,
                        "chain_type": "equation_chain",
                        "operation_text": "transform → linearize",
                        "focus_role": "input",
                        "inputs": [
                            {"element_type": "equation", "element_id": "eq_tex_b14",
                             "label": _TRUNCATED_TEX, "navigable": True},
                        ],
                        "outputs": [
                            {"element_type": "theory_claim", "element_id": None,
                             "label": "synth_claim_0001", "navigable": True},
                        ],
                        "eliminated_symbols": ["b_s"],
                        "retained_symbols": ["\\delta_k"],
                        "reason": "step_001 の操作",
                        "relation_status": "source_backed",
                        "evidence_refs": ["step_001"],
                        "navigable": True,
                    }
                ],
                "generic": None,
            },
            "upper": [
                {
                    "element_type": "theory_claim", "element_id": None, "document_id": _DOC_A,
                    "label": "synth_claim_0001", "sublabel": "ev_0012 を根拠とする",
                    "qualifier": "definition", "group": "claim", "unresolved": False,
                    "label_source": "generic", "relation": "quantifies",
                    "relation_label": "を定量化する", "relation_status": "source_backed",
                    "evidence_refs": ["ev_0012"], "navigable": True,
                },
                {
                    "element_type": "stage", "element_id": None, "document_id": _DOC_A,
                    "label": "理論の土台", "sublabel": "観測量を密度ゆらぎとして定義する",
                    "qualifier": "theory_basis", "group": "stage", "unresolved": False,
                    "label_source": "controlled_vocab", "relation": "belongs_to_stage",
                    "relation_label": "の理論段階に属する", "relation_status": "source_backed",
                    "evidence_refs": [], "navigable": False,
                },
            ],
            "lower": [
                {
                    "element_type": "symbol", "element_id": None, "document_id": _DOC_A,
                    "label": "\\delta(t,\\bm{x})", "sublabel": "密度コントラスト",
                    "qualifier": "defined", "group": "symbol_defined", "unresolved": False,
                    "label_source": "text", "relation": "defines_symbol",
                    "relation_label": "で記号を定義する", "relation_status": "source_backed",
                    "evidence_refs": [], "navigable": False,
                },
                {
                    "element_type": "theory_component", "element_id": "op-uuid",
                    "document_id": _DOC_A, "label": "Define eq_tex_b16",
                    "sublabel": "式の詳細層", "qualifier": "equation_detail",
                    "group": "operation", "unresolved": False, "label_source": "text",
                    "relation": "used_in_operation_step",
                    "relation_label": "の操作ステップで使われる",
                    "relation_status": "source_backed", "evidence_refs": [], "navigable": True,
                },
            ],
            "notes": [
                "derivation_eq_tex_b16 に他 3 件あります",
                "上位に他 2 件あるが表示上限のため省略しました",
            ],
        }

    def _build(self, monkeypatch) -> dict:
        _patch_equation_lookup(monkeypatch, lambda doc: [{"equation_id": "eq_tex_b14"}])
        monkeypatch.setattr(
            element_context.context_lens_mod, "build", lambda ref: self._dirty_lens()
        )
        return element_context.build_element_context("equation", "eq_tex_b14", {_DOC_A})

    def test_no_internal_ids_anywhere(self, monkeypatch):
        result = self._build(monkeypatch)

        offenders = [
            (path, text)
            for path, text, _ in _walk_learner_strings(result)
            if _INTERNAL_ID_TOKEN_RE.search(text)
        ]
        assert offenders == []

    def test_no_raw_tex_outside_symbol_slots(self, monkeypatch):
        from core.text_excerpt import looks_like_tex_math

        result = self._build(monkeypatch)

        offenders = [
            (path, text)
            for path, text, symbol_slot in _walk_learner_strings(result)
            if not symbol_slot and looks_like_tex_math(text)
        ]
        assert offenders == []

    def test_equation_detail_item_never_reaches_the_learner(self, monkeypatch):
        result = self._build(monkeypatch)

        assert [i["qualifier"] for i in result["lower"]] == ["defined"]
        assert all("式の詳細層" != i["sublabel"] for i in result["lower"])

    def test_symbol_meaning_and_stage_description_survive(self, monkeypatch):
        """遮断が過剰にならないこと（区別材料は残す）。"""
        result = self._build(monkeypatch)

        assert result["lower"][0]["label"] == "\\delta(t,\\bm{x})"
        assert result["lower"][0]["sublabel"] == "密度コントラスト"
        stage_item = [i for i in result["upper"] if i["group"] == "stage"][0]
        assert stage_item["sublabel"] == "観測量を密度ゆらぎとして定義する"

    def test_notes_with_internal_ids_are_filtered(self, monkeypatch):
        result = self._build(monkeypatch)

        assert result["notes"] == ["上位に他 2 件あるが表示上限のため省略しました"]

    def test_role_with_internal_id_is_dropped_end_to_end(self, monkeypatch):
        result = self._build(monkeypatch)

        assert "contextual_role" not in result["focus"]
        assert "contextual_role_source" not in result["focus"]
