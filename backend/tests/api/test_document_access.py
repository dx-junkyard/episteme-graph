"""backend/api/services.py の resolve_document_access / DocumentAccess の単体テスト。

Tier2 提案10（chunk ごとの再解決による N+1 を解消するための集約判定）で新設された
`resolve_document_access` / `DocumentAccess` を対象にする。判定条件は既存の
`user_can_view_document` / `user_can_edit_document` / `user_owns_document` と完全に
一致する契約なので、同一の mock セッションでいずれも呼び出し、判定が揃うことを
検証する。DB は実接続せず、SQLAlchemy session を差し替えた mock で分離する
（`backend/tests/test_prerequisite_routing.py` と同様に `services._pg_session` を
monkeypatch する方式。`test_document_group_sharing.py` の静的ソース検査とは別に、
実際に関数を呼び出す動的テストとして追加する）。
"""

from __future__ import annotations

import pytest


class _FakeResult:
    """`session.execute(...)` の戻り値。fetchone/fetchall のみサポートする。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeDocumentSession:
    """services.py がドキュメント権限判定で発行する4種の SQL をテキストパターンで
    振り分ける mock session。

    - `_resolve_document`（"FROM documents"）
    - `user_can_access_group`（"FROM group_members" のみ、JOIN なし）
    - `_has_document_group_permission`（"dgp.permission IN (" を含む単一許可チェック）
    - `_document_group_permissions_for_user`（"SELECT DISTINCT dgp.permission" の集合取得）
    """

    def __init__(self, *, doc_row=None, group_member_hit=False, doc_group_permissions=()):
        self.doc_row = doc_row
        self.group_member_hit = group_member_hit
        self.doc_group_permissions = set(doc_group_permissions)
        self.executed: list[tuple[str, dict]] = []
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        self.executed.append((sql, params))

        if "SELECT DISTINCT dgp.permission" in sql:
            return _FakeResult([(p,) for p in self.doc_group_permissions])
        if "dgp.permission IN (" in sql:
            requested = {v for k, v in params.items() if k.startswith("p")}
            hit = bool(requested & self.doc_group_permissions)
            return _FakeResult([(1,)] if hit else [])
        if "FROM document_group_permissions" in sql:
            return _FakeResult([])
        if "FROM group_members" in sql:
            return _FakeResult([(1,)] if self.group_member_hit else [])
        if "FROM documents" in sql:
            return _FakeResult([self.doc_row] if self.doc_row else [])
        return _FakeResult([])

    def close(self):
        self.closed = True


def _doc_row(doc_id="doc-1", source_path="material-1", uploaded_by="owner-1", visibility="private", group_id=None):
    return (doc_id, source_path, uploaded_by, visibility, group_id)


@pytest.fixture
def services_module():
    from api import services

    return services


# ---------------------------------------------------------------------------
# resolve_document_access: 正常系
# ---------------------------------------------------------------------------


class TestResolveDocumentAccessOwner:
    def test_owner_has_full_access(self, services_module, monkeypatch):
        monkeypatch.setattr(
            services_module,
            "_pg_session",
            lambda: _FakeDocumentSession(doc_row=_doc_row(uploaded_by="user-1")),
        )

        access = services_module.resolve_document_access("user-1", "doc-1")

        assert access.found is True
        assert access.is_owner is True
        assert access.can_view is True
        assert access.can_edit is True
        assert access.document_id == "doc-1"
        assert access.source_path == "material-1"
        assert access.uploaded_by == "user-1"


class TestResolveDocumentAccessPublic:
    def test_public_non_owner_can_view_but_not_edit(self, services_module, monkeypatch):
        monkeypatch.setattr(
            services_module,
            "_pg_session",
            lambda: _FakeDocumentSession(doc_row=_doc_row(uploaded_by="owner-x", visibility="public")),
        )

        access = services_module.resolve_document_access("user-2", "doc-1")

        assert access.is_owner is False
        assert access.can_view is True
        assert access.can_edit is False


class TestResolveDocumentAccessGroupPermission:
    def test_viewer_permission_allows_view_only(self, services_module, monkeypatch):
        monkeypatch.setattr(
            services_module,
            "_pg_session",
            lambda: _FakeDocumentSession(
                doc_row=_doc_row(uploaded_by="owner-x", visibility="private"),
                doc_group_permissions={"viewer"},
            ),
        )

        access = services_module.resolve_document_access("user-3", "doc-1")

        assert access.can_view is True
        assert access.can_edit is False

    def test_editor_permission_allows_view_and_edit(self, services_module, monkeypatch):
        monkeypatch.setattr(
            services_module,
            "_pg_session",
            lambda: _FakeDocumentSession(
                doc_row=_doc_row(uploaded_by="owner-x", visibility="private"),
                doc_group_permissions={"editor"},
            ),
        )

        access = services_module.resolve_document_access("user-4", "doc-1")

        assert access.can_view is True
        assert access.can_edit is True

    def test_legacy_single_group_share_grants_view_only(self, services_module, monkeypatch):
        monkeypatch.setattr(
            services_module,
            "_pg_session",
            lambda: _FakeDocumentSession(
                doc_row=_doc_row(uploaded_by="owner-x", visibility="group", group_id="grp-1"),
                group_member_hit=True,
            ),
        )

        access = services_module.resolve_document_access("user-5", "doc-1")

        assert access.can_view is True
        assert access.can_edit is False

    def test_no_permission_denies_view_and_edit(self, services_module, monkeypatch):
        monkeypatch.setattr(
            services_module,
            "_pg_session",
            lambda: _FakeDocumentSession(doc_row=_doc_row(uploaded_by="owner-x", visibility="private")),
        )

        access = services_module.resolve_document_access("user-6", "doc-1")

        assert access.can_view is False
        assert access.can_edit is False


# ---------------------------------------------------------------------------
# resolve_document_access: 見つからない参照
# ---------------------------------------------------------------------------


class TestResolveDocumentAccessNotFound:
    def test_missing_ref_returns_not_found(self, services_module, monkeypatch):
        monkeypatch.setattr(services_module, "_pg_session", lambda: _FakeDocumentSession(doc_row=None))

        access = services_module.resolve_document_access("user-1", "does-not-exist")

        assert access.found is False
        assert access.document_id is None
        assert access.source_path == ""
        assert access.uploaded_by is None
        assert access.is_owner is False
        assert access.can_view is False
        assert access.can_edit is False


class TestDocumentAccessFoundProperty:
    """`.found` は document_id の有無だけで判定される（他フィールドは見ない）。"""

    def test_found_true_when_document_id_set(self, services_module):
        access = services_module.DocumentAccess(document_id="doc-1")
        assert access.found is True

    def test_found_false_when_document_id_none(self, services_module):
        access = services_module.DocumentAccess(document_id=None)
        assert access.found is False

    def test_found_false_by_default(self, services_module):
        """引数なし構築（document_id 省略不可だが None 既定は無い点を明示）でも
        document_id=None を渡せば found=False になる。"""
        access = services_module.DocumentAccess(document_id=None, is_owner=True)
        assert access.found is False


# ---------------------------------------------------------------------------
# documents.id (UUID) / source_path (material_id) の両方の解決経路
# ---------------------------------------------------------------------------


class TestResolveDocumentAcceptsUuidOrSourcePath:
    def test_query_offers_both_resolution_paths(self, services_module, monkeypatch):
        """1クエリで id::text と source_path の両方を OR 条件にしていること。"""
        session = _FakeDocumentSession(doc_row=_doc_row())
        monkeypatch.setattr(services_module, "_pg_session", lambda: session)

        services_module.resolve_document_access("user-1", "material-1")

        doc_query_sql = next(sql for sql, _ in session.executed if "FROM documents" in sql)
        assert "id::text = :ref" in doc_query_sql
        assert "source_path = :ref" in doc_query_sql

    @pytest.mark.parametrize("ref", ["11111111-1111-1111-1111-111111111111", "material-1"])
    def test_resolves_regardless_of_ref_shape(self, services_module, monkeypatch, ref):
        """UUID 形式の ref でも source_path 形式の ref でも同じ経路で解決される。"""
        session = _FakeDocumentSession(doc_row=_doc_row(uploaded_by="user-1"))
        monkeypatch.setattr(services_module, "_pg_session", lambda: session)

        access = services_module.resolve_document_access("user-1", ref)

        assert access.found is True
        _, params = session.executed[0]
        assert params["ref"] == ref


# ---------------------------------------------------------------------------
# 既存判定関数との整合性
# ---------------------------------------------------------------------------


class TestResolveDocumentAccessMatchesLegacyFunctions:
    """resolve_document_access は既存の user_can_view_document / user_can_edit_document /
    user_owns_document と判定が完全に一致する契約（docstring に明記）。同一のデータ集合を
    与えた mock セッションでどちらも呼び出し、結果を突き合わせる。"""

    @pytest.mark.parametrize(
        "case_id, user_id, doc_kwargs, group_member_hit, doc_group_permissions",
        [
            ("owner", "user-1", {"uploaded_by": "user-1"}, False, ()),
            ("public", "user-2", {"uploaded_by": "owner-x", "visibility": "public"}, False, ()),
            (
                "dgp_viewer",
                "user-3",
                {"uploaded_by": "owner-x", "visibility": "private"},
                False,
                ("viewer",),
            ),
            (
                "dgp_editor",
                "user-4",
                {"uploaded_by": "owner-x", "visibility": "private"},
                False,
                ("editor",),
            ),
            (
                "legacy_group",
                "user-5",
                {"uploaded_by": "owner-x", "visibility": "group", "group_id": "grp-1"},
                True,
                (),
            ),
            (
                "no_access",
                "user-6",
                {"uploaded_by": "owner-x", "visibility": "private"},
                False,
                (),
            ),
        ],
    )
    def test_view_edit_owner_match(
        self,
        services_module,
        monkeypatch,
        case_id,
        user_id,
        doc_kwargs,
        group_member_hit,
        doc_group_permissions,
    ):
        def _session_factory():
            return _FakeDocumentSession(
                doc_row=_doc_row(**doc_kwargs),
                group_member_hit=group_member_hit,
                doc_group_permissions=doc_group_permissions,
            )

        monkeypatch.setattr(services_module, "_pg_session", _session_factory)

        access = services_module.resolve_document_access(user_id, "doc-1")
        expect_view = services_module.user_can_view_document(user_id, "doc-1")
        expect_edit = services_module.user_can_edit_document(user_id, "doc-1")
        expect_owner = services_module.user_owns_document(user_id, "doc-1")

        assert access.can_view == expect_view, case_id
        assert access.can_edit == expect_edit, case_id
        assert access.is_owner == expect_owner, case_id
