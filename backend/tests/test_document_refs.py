"""教材の2つの ID 体系（documents.id / source_path = material_id）の規則（IK-0550）。

規則: クライアントへ document を渡すキーは常に ``document_id``（documents.id = UUID）。
``material_id`` はアップロード時の ID（documents.source_path）を名指すところにだけ残す。
受け付ける側は両形を同じ document に着地させ、その突き合わせは単数なら
``services._resolve_document``、複数なら ``core.document_refs.resolve_document_refs`` が行う。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services  # noqa: E402
from core import document_refs  # noqa: E402
_DOC_1 = "aaaaaaaa-0000-0000-0000-000000000001"
_DOC_2 = "aaaaaaaa-0000-0000-0000-000000000002"
# アップロード時の ID は UUID に見えるが documents.id ではない（第 17 周の「712e0e36-f82…」）。
_MAT_1 = "712e0e36-f820-4000-8000-000000000001"
_MAT_2 = "arXiv-2407.01221v2"

_DOCUMENTS = [
    {"id": _DOC_1, "source_path": _MAT_1, "title": "Paper One"},
    {"id": _DOC_2, "source_path": _MAT_2, "title": "Paper Two"},
]


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _DocumentsFake:
    def __init__(self, documents=None):
        self.documents = documents if documents is not None else _DOCUMENTS
        self.calls: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append(sql)
        params = params or {}
        if "ANY(:refs)" in sql:
            refs = set(params["refs"])
            return _Result([
                (d["id"], d["source_path"]) for d in self.documents
                if d["id"] in refs or d["source_path"] in refs
            ])
        if "ANY(:ids)" in sql:
            ids = set(params["ids"])
            return _Result([(d["id"], d["title"]) for d in self.documents if d["id"] in ids])
        return _Result([])

    def close(self):
        pass

    def rollback(self):
        pass


class TestResolveDocumentRefs:
    def test_both_forms_land_on_the_same_document(self):
        out = document_refs.resolve_document_refs(_DocumentsFake(), [_DOC_1, _MAT_1, _MAT_2, "missing"])
        assert out[_DOC_1] == {"document_id": _DOC_1, "material_id": _MAT_1}
        assert out[_MAT_1] == {"document_id": _DOC_1, "material_id": _MAT_1}
        assert out[_MAT_2]["document_id"] == _DOC_2
        assert "missing" not in out

    def test_id_match_wins_over_another_rows_source_path(self):
        docs = [
            {"id": _DOC_1, "source_path": "m-x", "title": ""},
            {"id": _DOC_2, "source_path": _DOC_1, "title": ""},  # 病的な衝突
        ]
        out = document_refs.resolve_document_refs(_DocumentsFake(docs), [_DOC_1])
        assert out[_DOC_1]["document_id"] == _DOC_1

    def test_empty_refs_issue_no_sql(self):
        fake = _DocumentsFake()
        assert document_refs.resolve_document_refs(fake, ["", None]) == {}
        assert fake.calls == []

    def test_canonical_material_ids_keeps_order_and_drops_unknown(self):
        assert document_refs.canonical_material_ids(
            _DocumentsFake(), [_DOC_2, "missing", _MAT_1, _DOC_1]
        ) == [_MAT_2, _MAT_1]

    def test_rows_carry_requested_columns_and_reject_unknown(self):
        fake = _ColumnsFake()
        out = document_refs.resolve_document_rows(fake, [_MAT_1], columns=("title", "uploaded_by"))
        assert out[_MAT_1] == {
            "document_id": _DOC_1, "material_id": _MAT_1, "title": "Paper One", "uploaded_by": "u1",
        }
        assert "uploaded_by::text AS uploaded_by" in fake.calls[0]
        with pytest.raises(ValueError):
            document_refs.resolve_document_rows(fake, [_MAT_1], columns=("title; DROP TABLE x",))

    def test_single_ref_and_canonical_document_ids(self):
        assert document_refs.resolve_document_row(_DocumentsFake(), _MAT_2)["document_id"] == _DOC_2
        assert document_refs.resolve_document_row(_DocumentsFake(), "") is None
        assert document_refs.canonical_document_ids(
            _DocumentsFake(), [_MAT_2, _DOC_1, _MAT_1, "missing"]
        ) == [_DOC_2, _DOC_1]

    def test_list_keeps_every_row_sharing_a_source_path(self):
        docs = [
            {"id": _DOC_1, "source_path": "m-x", "title": ""},
            {"id": _DOC_2, "source_path": "m-x", "title": ""},
        ]
        rows = document_refs.list_documents_for_refs(_DocumentsFake(docs), ["m-x", _DOC_1])
        assert [r["document_id"] for r in rows] == [_DOC_1, _DOC_2]
        # 1 ref = 1 行の解決は 1 行に畳む。
        assert set(document_refs.resolve_document_refs(_DocumentsFake(docs), ["m-x"])) == {"m-x"}

    def test_uuid_shape_is_not_proof(self):
        assert document_refs.is_document_uuid(_MAT_1) is True  # 形は UUID でも
        out = document_refs.resolve_document_refs(_DocumentsFake(), [_MAT_1])
        assert out[_MAT_1]["document_id"] == _DOC_1  # 着地先は documents.id


class _ColumnsFake(_DocumentsFake):
    def execute(self, stmt, params=None):
        self.calls.append(str(stmt))
        refs = set((params or {}).get("refs") or [])
        return _Result([
            {"id": d["id"], "source_path": d["source_path"], "title": d["title"], "uploaded_by": "u1"}
            for d in self.documents if d["id"] in refs or d["source_path"] in refs
        ])


@pytest.fixture
def fake_db(monkeypatch):
    monkeypatch.setattr(services, "_pg_session", lambda: _DocumentsFake())


class TestCourseSourcesAcceptBothForms:
    def test_document_ids_from_uuid_material_id(self, fake_db):
        data = {"sources": [{"material_id": _DOC_1}, {"material_id": _MAT_2}]}
        assert services.list_course_source_document_ids(data) == {_DOC_1, _DOC_2}

    def test_titles_keyed_by_stored_ref(self, fake_db):
        data = {"sources": [{"material_id": _DOC_1}, {"material_id": _MAT_2}]}
        assert services.resolve_course_source_titles(data) == {_DOC_1: "Paper One", _MAT_2: "Paper Two"}

    def test_sources_get_uuid_document_id(self, fake_db):
        data = {"sources": [
            {"material_id": _MAT_1, "title": "x"},
            {"material_id": _MAT_2, "title": "y", "document_id": _DOC_2},
            {"material_id": "missing", "title": "z"},
        ]}
        out = services.with_course_source_document_ids(data)
        assert out["sources"][0]["document_id"] == _DOC_1
        assert out["sources"][0]["material_id"] == _MAT_1  # material_id は保存値のまま
        assert out["sources"][1]["document_id"] == _DOC_2
        assert "document_id" not in out["sources"][2]
        assert "document_id" not in data["sources"][0]  # 保存データは変えない

    def test_db_failure_is_fail_soft(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(services, "_pg_session", _boom)
        data = {"sources": [{"material_id": _MAT_1}]}
        assert services.with_course_source_document_ids(data) is data


def _iter_document_id_values(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "document_id":
                yield inner
            yield from _iter_document_id_values(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _iter_document_id_values(inner)


class TestLearnerDtoDocumentIdIsUuid:
    def test_course_detail_sources_carry_uuid_document_id(self, fake_db):
        import routes.learning as learning
        from schemas import LearningCourseDetail

        data = {
            "id": "c1", "title": "c", "topics": [],
            "sources": [{"material_id": _MAT_1, "title": _MAT_1}, {"material_id": _DOC_2, "title": "t"}],
        }
        projected = learning._with_resolved_source_titles(data)
        dto = LearningCourseDetail(**projected).model_dump()
        values = list(_iter_document_id_values(dto))
        assert values, "sources に document_id が載っていない"
        assert all(document_refs.is_document_uuid(v) for v in values), values
        assert {v for v in values} == {_DOC_1, _DOC_2}
        # material_id 側は documents.id に書き換えない（アップロード時の ID のまま）。
        assert dto["sources"][0]["material_id"] == _MAT_1


class TestCourseBuilderAcceptsDocumentIds:
    def test_selected_ids_are_canonicalized_to_material_ids(self, monkeypatch):
        import routes.admin as admin

        monkeypatch.setattr(admin, "_pg_session", lambda: _DocumentsFake())
        assert admin._canonical_selected_material_ids([_DOC_1, _MAT_2, "unknown"]) == [_MAT_1, _MAT_2, "unknown"]

    def test_failure_keeps_original_values(self, monkeypatch):
        import routes.admin as admin

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(admin, "_pg_session", _boom)
        assert admin._canonical_selected_material_ids([_DOC_1]) == [_DOC_1]

    def test_injected_sources_carry_document_id(self):
        src = (_BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
        assert "body.selected_material_ids = _canonical_selected_material_ids(" in src
        assert 'source["document_id"] = str(r[3])' in src


# ---------------------------------------------------------------------------
# ガードレール: 両形の突き合わせを書いてよい場所
# ---------------------------------------------------------------------------

_BOTH_FORMS_RE = re.compile(
    r"id::text\s*=\s*(?:ANY\()?:\w+\)?\s+OR\s+(?:\w+\.)?source_path"
    r"|source_path\s*=\s*(?:ANY\()?:\w+\)?\s+OR\s+(?:\w+\.)?id::text"
)

#: 正本（単数 / 複数）。
_CANONICAL = {"api/services.py", "core/document_refs.py"}
#: IK-0550 是正（2026-10-01）より前からあった突き合わせ（get_material / projector / radar /
#: corpus / next_steps / seminar_brief）は IK-0594（2026-10-02）で core.document_refs に寄せ、
#: 空になった。**ここに足さない** — 新しい経路は ``core.document_refs`` /
#: ``services._resolve_document`` を使う（``test_document_id_resolution_guardrails.py``）。
_LEGACY_ALLOWLIST: set[str] = set()


def test_both_forms_disambiguation_only_in_known_places():
    offenders: list[str] = []
    for path in sorted(list((_BACKEND / "api").rglob("*.py")) + list((_BACKEND / "core").rglob("*.py"))):
        rel = path.relative_to(_BACKEND).as_posix()
        if not _BOTH_FORMS_RE.search(path.read_text(encoding="utf-8")):
            continue
        if rel in _CANONICAL or rel in _LEGACY_ALLOWLIST:
            continue
        offenders.append(rel)
    assert offenders == [], (
        "教材 ref の両形（documents.id / source_path）の突き合わせは services._resolve_document か "
        f"core.document_refs.resolve_document_refs に委ねる（IK-0550）: {offenders}"
    )


def test_canonical_places_still_disambiguate():
    for rel in _CANONICAL:
        assert _BOTH_FORMS_RE.search((_BACKEND / rel).read_text(encoding="utf-8")), rel


def test_document_refs_core_is_fastapi_free():
    src = (_BACKEND / "core" / "document_refs.py").read_text(encoding="utf-8")
    assert "import fastapi" not in src and "from fastapi" not in src
