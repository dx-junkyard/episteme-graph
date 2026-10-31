"""人に見せる名前のガードレール（IK-0848 / IK-1007・第 25 周 第 5 波・migration 094）。

``users.display_name`` はログインの照合列を兼ねる（``routes/auth.py`` の ``auth_login``）。
帰属・所有者・査読者・記帳者の表示にこの列を使うと、人の名前ではなくログイン ID を開示する。

固定すること:

1. ``backend/api`` / ``backend/core`` の SQL 文字列で ``display_name`` を読む経路は
   allowlist（ログイン照合・アカウント管理・別表の同名列・グループ招待）に限る。
2. 帰属の表示は ``core/account_display.py`` だけが読む ``profile_name`` を通り、
   ログイン ID（``display_name`` / ``username``）へフォールバックしない。
3. 学習者向けの経路（learning.py / component_context.py / my_profile.py）にログイン ID が出ない。
4. ログインの照合列と判定順序は不変（``auth_login`` は ``display_name`` で引き、資格情報 → status）。
5. migration 094 は列追加だけ（DELETE なし・バックフィルなし）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
for _path in (BACKEND, BACKEND / "api"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

_DISPLAY_NAME_RE = re.compile(r"\bdisplay_name\b")
_SQL_RE = re.compile(r"\b(SELECT|UPDATE|INSERT|WHERE|JOIN|FROM|ILIKE|ORDER BY)\b|,", re.IGNORECASE)

#: ``display_name`` を SQL で読んでよい経路（file:symbol → 理由）。ここに無い経路が
#: ``display_name`` を SELECT したら赤になる — 帰属の表示は ``core/account_display.py`` を通す。
ALLOWED_DISPLAY_NAME_SQL = {
    "api/main.py:_lifespan": "bootstrap Administrator の存在確認（ログイン名の照合）",
    "api/routes/auth.py:auth_login": "ログインの照合列（判定順序は資格情報 → status）",
    "api/routes/admin.py:_USER_COLUMNS_SQL": "アカウント管理（username = ログイン ID が対象そのもの）",
    "api/routes/admin.py:list_users": "アカウント管理の検索（ログイン ID で探す）",
    "api/routes/admin.py:create_student": "アカウント作成時のログイン名の重複確認",
    "api/routes/admin.py:create_teacher": "アカウント作成時のログイン名の重複確認",
    "api/routes/admin.py:get_materials_stats": "SYSTEM_ADMIN 専用の運用統計（1アカウント単位の運用）",
    "api/routes/admin.py:create_cb_session": "course_builder_sessions.display_name（別表の同名列）",
    "api/routes/admin.py:list_cb_sessions": "course_builder_sessions.display_name（別表の同名列）",
    "api/routes/admin.py:get_cb_session": "course_builder_sessions.display_name（別表の同名列）",
    "api/routes/admin.py:update_cb_session": "course_builder_sessions.display_name（別表の同名列）",
    "api/routes/groups.py:get_group_detail": "グループ招待はログイン名で行う（残課題: 参加者一覧）",
    "api/routes/groups.py:list_group_invitations": "グループ招待はログイン名で行う（残課題）",
    "api/routes/groups.py:list_my_invitations": "グループ招待はログイン名で行う（残課題）",
    "api/routes/groups.py:_respond_invitation": "グループ招待はログイン名で行う（残課題）",
    "api/routes/groups.py:invite_user": "招待先をログイン名で引く",
    "api/routes/llm_usage.py:_attach_user_display_names": "SYSTEM_ADMIN 専用の利用実績（アカウント運用）",
    "core/account_lifecycle.py:tombstone_values": "削除時の墓標化（ログイン名を匿名化する）",
    "core/account_lifecycle.py:purge_user": "削除時の墓標化（ログイン名を匿名化する）",
}


def _sql_display_name_sites() -> dict[str, list[int]]:
    """``display_name`` を含む SQL 文字列（と ``"display_name"`` キーそのもの）の所在。"""
    sites: dict[str, list[int]] = {}
    for base in ("api", "core"):
        for path in sorted((BACKEND / base).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents: dict[ast.AST, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                value = node.value
                if not _DISPLAY_NAME_RE.search(value):
                    continue
                if isinstance(parents.get(node), ast.Expr):
                    continue  # docstring（SQL ではない）
                if value != "display_name" and not _SQL_RE.search(value):
                    continue  # SQL ではない文字列
                symbol = "<module>"
                cur = node
                while cur in parents:
                    cur = parents[cur]
                    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbol = cur.name
                        break
                    if (
                        symbol == "<module>"
                        and isinstance(cur, ast.Assign)
                        and isinstance(cur.targets[0], ast.Name)
                    ):
                        symbol = cur.targets[0].id
                rel = path.relative_to(BACKEND).as_posix()
                sites.setdefault(f"{rel}:{symbol}", []).append(node.lineno)
    return sites


class TestNoLoginIdInAttribution:
    def test_display_name_sql_only_on_allowed_paths(self):
        sites = _sql_display_name_sites()
        unexpected = {k: v for k, v in sites.items() if k not in ALLOWED_DISPLAY_NAME_SQL}
        assert unexpected == {}, (
            "帰属・所有者・査読者の表示に users.display_name（= ログイン ID）を読む経路がある: "
            f"{unexpected}。人に見せる名前は core/account_display.py（profile_name）を通すこと（IK-1007）"
        )

    def test_allowlist_has_no_stale_entries(self):
        sites = _sql_display_name_sites()
        stale = sorted(set(ALLOWED_DISPLAY_NAME_SQL) - set(sites))
        assert stale == [], f"allowlist に実在しない経路: {stale}（消したら allowlist からも外す）"

    @pytest.mark.parametrize(
        "relpath",
        [
            "core/atlas_review_display.py",
            "core/atlas_reports.py",
            "core/atlas_state.py",
            "core/component_context.py",
            "core/doubt/open_assumptions.py",
            "api/routes/doubt.py",
            "api/routes/theory_components.py",
            "api/routes/learning.py",
            "api/routes/simulation_bridge.py",
            "api/routes/lecture_studio/topics.py",
            "api/routes/my_profile.py",
        ],
    )
    def test_attribution_surfaces_read_profile_name_not_login_id(self, relpath):
        src = (BACKEND / relpath).read_text(encoding="utf-8")
        assert "u.display_name" not in src
        assert "owner_u.display_name" not in src
        assert 'current_user.get("display_name")' not in src
        assert 'current_user["username"]' not in src
        assert 'current_user.get("username")' not in src

    def test_no_fallback_to_ids_in_reporter_name(self):
        src = (BACKEND / "core" / "atlas_reports.py").read_text(encoding="utf-8")
        assert "reporter_id::text) AS reporter_name" not in src
        assert 'report.get("reporter_id")' not in src


class TestAccountDisplayCore:
    def test_display_label_never_reads_login_id(self):
        from core import account_display

        src = (BACKEND / "core" / "account_display.py").read_text(encoding="utf-8")
        body = src[src.index("def display_label"): src.index("def label_or_unregistered")]
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith(("#", '"""'))
        )
        assert '"display_name"' not in code and '"username"' not in code
        assert account_display.display_label(
            {"profile_name": "", "display_name": "uxsim_te_01", "username": "uxsim_te_01"}
        ) is None
        assert account_display.display_label({"profile_name": None, "display_name": "x"}) is None

    def test_display_label_and_fact(self):
        from core import account_display
        from core.label_vocab import ACCOUNT_NAME_UNREGISTERED_ACTOR

        assert account_display.display_label({"profile_name": "  浦川  先生 "}) == "浦川 先生"
        assert account_display.display_label({"profile_name": "山田", "status": "deleted"}) is None
        assert account_display.label_or_unregistered("") == ACCOUNT_NAME_UNREGISTERED_ACTOR
        assert account_display.label_or_unregistered(None) == ACCOUNT_NAME_UNREGISTERED_ACTOR
        assert account_display.label_or_unregistered("山田") == "山田"

    def test_normalize_strips_control_and_limits_length(self):
        from core import account_display

        assert account_display.normalize_profile_name("a\x1b[31mb\x00c\n d") == "abc d"
        assert len(account_display.normalize_profile_name("あ" * 500)) == 200

    def test_fact_text_has_no_numbers(self):
        from core import label_vocab

        for text in (
            label_vocab.ACCOUNT_NAME_UNREGISTERED,
            label_vocab.ACCOUNT_NAME_UNREGISTERED_ACTOR,
            label_vocab.ACCOUNT_PROFILE_NAME_SAVED,
            label_vocab.ACCOUNT_PROFILE_NAME_CLEARED,
        ):
            assert not re.search(r"[0-9０-９]", text)

    def test_profile_name_sql_rejects_injection(self):
        from core import account_display

        assert "profile_name" in account_display.profile_name_sql("u")
        with pytest.raises(ValueError):
            account_display.profile_name_sql("u; DROP TABLE users")

    def test_fetch_display_labels_maps_unregistered_to_none(self):
        from core import account_display

        uid_named = "11111111-1111-1111-1111-111111111111"
        uid_blank = "22222222-2222-2222-2222-222222222222"
        uid_deleted = "33333333-3333-3333-3333-333333333333"

        class _Session:
            def execute(self, stmt, params):
                assert "display_name" not in str(stmt)

                class _R:
                    def fetchall(self_inner):
                        return [
                            (uid_named, "山田", "active"),
                            (uid_blank, None, "active"),
                            (uid_deleted, "旧名", "deleted"),
                        ]

                return _R()

        out = account_display.fetch_display_labels(
            _Session(), [uid_named, uid_blank, uid_deleted, "not-a-uuid"]
        )
        assert out == {uid_named: "山田", uid_blank: None, uid_deleted: None}


class TestLoginUnchanged:
    def test_login_still_matches_display_name_then_status(self):
        src = (BACKEND / "api" / "routes" / "auth.py").read_text(encoding="utf-8")
        assert "WHERE display_name = :username" in src
        assert "profile_name" not in src
        assert src.index("credentials_ok") < src.index("_LOGIN_BLOCKED_STATUSES:")

    def test_profile_route_does_not_touch_login_columns(self):
        src = (BACKEND / "api" / "routes" / "my_profile.py").read_text(encoding="utf-8")
        for term in ("password_hash", "token_generation", "SET display_name", "{user_id}"):
            assert term not in src


class TestMigration094:
    def test_column_added_without_backfill_or_delete(self):
        sql = (BACKEND / "db" / "094_users_profile_name.sql").read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        assert "ADD COLUMN IF NOT EXISTS profile_name" in body
        assert "DELETE" not in body.upper()
        assert "UPDATE" not in body.upper()


class TestFrontendCarriers:
    def test_both_headers_carry_the_name_button(self):
        index = (REPO / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
        admin = (REPO / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
        assert 'data-ui-anchor="topbar.profile-name"' in index
        assert 'data-ui-anchor="header.profile-name"' in admin
        assert "/js/profile-name.js?v=" in index and "/js/profile-name.js?v=" in admin

    def test_js_uses_server_wording_and_no_polling(self):
        js = (REPO / "frontend" / "public" / "js" / "profile-name.js").read_text(encoding="utf-8")
        assert "/api/me/profile" in js
        assert "setInterval" not in js
        assert "display_name" not in js and "username" not in js


class _FakeProfileSession:
    def __init__(self, row=("", "active"), rowcount=1):
        self.row = row
        self.rowcount = rowcount
        self.executed: list[tuple[str, dict]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), dict(params or {})))
        outer = self

        class _R:
            rowcount = outer.rowcount

            def fetchone(self_inner):
                return outer.row

        return _R()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class TestMyProfileRoute:
    USER = {"id": "11111111-1111-1111-1111-111111111111", "username": "login_id", "role": "STUDENT"}

    def _route(self, monkeypatch, session):
        from routes import my_profile

        monkeypatch.setattr(my_profile, "_pg_session", lambda: session)
        import services

        monkeypatch.setattr(services, "record_review_event", lambda *a, **k: None)
        return my_profile

    def test_get_unregistered_returns_fact_not_login_id(self, monkeypatch):
        session = _FakeProfileSession(row=(None, "active"))
        mod = self._route(monkeypatch, session)
        out = mod.get_my_profile(current_user=self.USER)
        from core.label_vocab import ACCOUNT_NAME_UNREGISTERED

        assert out.profile_name == "" and out.label == ACCOUNT_NAME_UNREGISTERED
        assert "login_id" not in out.model_dump_json()

    def test_patch_normalizes_and_saves_own_row_only(self, monkeypatch):
        session = _FakeProfileSession()
        mod = self._route(monkeypatch, session)
        out = mod.update_my_profile(
            mod.ProfileUpdate(profile_name=" 浦川\x1b[0m  先生\n"), current_user=self.USER
        )
        assert out.profile_name == "浦川 先生" and session.committed
        sql, params = session.executed[0]
        assert "UPDATE users SET profile_name" in sql and "display_name" not in sql
        assert params == {"name": "浦川 先生", "uid": self.USER["id"]}

    def test_patch_empty_clears(self, monkeypatch):
        session = _FakeProfileSession()
        mod = self._route(monkeypatch, session)
        out = mod.update_my_profile(mod.ProfileUpdate(profile_name="   "), current_user=self.USER)
        assert out.profile_name == "" and session.executed[0][1]["name"] is None

    def test_patch_too_long_is_422(self, monkeypatch):
        from fastapi import HTTPException

        session = _FakeProfileSession()
        mod = self._route(monkeypatch, session)
        with pytest.raises(HTTPException) as exc:
            mod.update_my_profile(mod.ProfileUpdate(profile_name="あ" * 201), current_user=self.USER)
        assert exc.value.status_code == 422 and session.executed == []
