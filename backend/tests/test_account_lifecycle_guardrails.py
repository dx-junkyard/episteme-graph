"""アカウントライフサイクル管理 — ガードレール（設計書 §12）。

正本: ``docs/features/account_lifecycle_management_design.md``。不変条項 AL1〜AL10 を
**構造的に**守る静的検査。設計書 §12 の 1〜9・11 を実装する
（§12-10 は既存 ``test_llm_usage_guardrails.py``、§12-12 は認証側の
``test_account_lifecycle_auth.py`` の担当）。

観点:
  §12-1  ``DELETE FROM users`` / ORM 削除語彙が本層に無い（AL1）
  §12-2  suspend / restore が所有権・可視性・グループ・共有に触れない（AL2）
  §12-3  auth_events への削除・改変 API が無い（AL5）
  §12-4  password が監査 metadata・auth_events payload・logger に渡らない（AL4）
  §12-5  一覧が TEACHER に対して role=learner へ fail-closed / activity は
         SYSTEM_ADMIN（AL7）
  §12-6  自分自身・Administrator への停止・削除予約が拒否される（AL10）
  §12-7  purge_user が所有物残存時に users 行を変更しない（AL9）
  §12-8  監査 entity_type がカタログ定数（生文字列禁止）
  §12-9  キャッシュ invalidate が suspend / restore / reset 経路に存在する（AL3）
  §12-11 **purge 網羅性**: ``REFERENCES users(id)`` を持つ全テーブルが
         PURGE_TABLES ∪ RETAIN_TABLES に現れる（将来 migration の取りこぼし検出）
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
)


def _code_only(src: str) -> str:
    """docstring と ``#`` コメントを空白化した「実行される部分だけ」のソースを返す。

    禁止語彙の検査は**コードに対して**行う必要がある。素のソースを検査すると
    「``DELETE FROM users`` を書かない」というルールを docstring で説明した時点で
    自分のガードレールに引っかかり、**規約を文書化できなくなる**（実際に一度そうなった）。

    一方、SQL は文字列リテラルとして書かれているため、文字列リテラルを一律に落とすと
    検査が空振りする。そこで落とすのは
      - ``#`` コメント（tokenize で正確に位置を取る）
      - docstring / 単独の文字列式（ast で行範囲を取る）
    だけに限り、**SQL を含む通常の文字列リテラルは残す**。行数は変えない（行を消すと
    ``extract_function_source`` の切り出しがずれる）。
    """
    lines = src.splitlines()
    try:
        for token in tokenize.generate_tokens(io.StringIO(src).readline):
            if token.type == tokenize.COMMENT:
                row, col = token.start
                lines[row - 1] = lines[row - 1][:col]
    except tokenize.TokenError:  # pragma: no cover — 構文が壊れていれば他のテストが落ちる
        pass
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for row in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                lines[row - 1] = ""
    return "\n".join(lines)


_CORE_MODULE = BACKEND / "core" / "account_lifecycle.py"
_CORE_SRC = _CORE_MODULE.read_text(encoding="utf-8")
_ADMIN_ROUTE = BACKEND / "api" / "routes" / "admin.py"
_ADMIN_SRC = _ADMIN_ROUTE.read_text(encoding="utf-8")
_WORKER_SRC = (BACKEND / "core" / "versioning" / "worker.py").read_text(encoding="utf-8")
_RECIPIENTS_SRC = (BACKEND / "core" / "notification_recipients.py").read_text(encoding="utf-8")

_CORE_CODE = _code_only(_CORE_SRC)
_ADMIN_CODE = _code_only(_ADMIN_SRC)
_WORKER_CODE = _code_only(_WORKER_SRC)

#: 本層のソース（AL層が触るファイル）。**コメント・docstring を除いたコード部分**。
_LAYER_SOURCES = {
    "core/account_lifecycle.py": _CORE_CODE,
    "core/versioning/worker.py": _WORKER_CODE,
    "api/routes/admin.py": _ADMIN_CODE,
}

#: アカウントライフサイクルの API 関数（admin.py の User Management 節）。
_LIFECYCLE_ROUTES = (
    "list_users",
    "suspend_user",
    "restore_user",
    "reset_user_password",
    "get_user_activity",
    "schedule_user_deletion",
    "cancel_user_deletion",
    "transfer_user_ownership",
)


def _route_source(fn_name: str) -> str:
    """API 関数の素のソース（docstring 込み。「含むこと」の検査用）。"""
    return extract_function_source(_ADMIN_SRC, fn_name)


def _route_code(fn_name: str) -> str:
    """API 関数のコード部分のみ（禁止語彙の検査用。docstring / コメントを除く）。"""
    return extract_function_source(_ADMIN_CODE, fn_name)


# ===========================================================================
# §12-1 users 行を物理削除しない（AL1）
# ===========================================================================


class TestNoUserRowDeletion:
    def test_no_raw_delete_from_users(self):
        for name, src in _LAYER_SOURCES.items():
            assert_source_forbids(src, ["DELETE FROM users"], context=name)

    def test_no_orm_deletion_vocabulary(self):
        """core/models.py::User は cascade="all, delete-orphan" を持つため、
        文字列 SQL の検査だけでは AL1 を守れない。ORM 経由の削除語彙も禁止する。"""
        forbidden = [
            "session.delete(",
            "delete(User",
            "query(User).delete",
            "DELETE FROM users",
            "TRUNCATE users",
            "DROP TABLE users",
        ]
        for name, src in _LAYER_SOURCES.items():
            assert_source_forbids(src, forbidden, context=name)

    def test_purge_tables_never_target_users(self):
        from core import account_lifecycle

        assert all(t.table != "users" for t in account_lifecycle.PURGE_TABLES)

    def test_purge_user_only_updates_the_users_row(self):
        """purge の実体は UPDATE（墓標化）であって DELETE ではない。"""
        src = extract_function_source(_CORE_CODE, "purge_user")
        assert "UPDATE users" in src
        assert "DELETE FROM users" not in src
        assert "status = 'deleted'" in src

    def test_core_module_does_not_import_fastapi(self):
        assert_module_tree_does_not_import(_CORE_MODULE.parent, ["fastapi"], glob="account_lifecycle.py")


# ===========================================================================
# §12-2 停止は認証の拒否のみ（AL2）
# ===========================================================================


class TestSuspendTouchesNothingElse:
    #: 停止・再開が絶対に触ってはいけない語彙（所有権・可視性・共有・受講）。
    FORBIDDEN = (
        "uploaded_by",
        "learning_courses",
        "learning_states",
        "object_group_permissions",
        "group_members",
        "documents",
        "visibility",
        "shared_version",
    )

    def test_suspend_route_touches_only_status_columns(self):
        src = _route_code("suspend_user")
        assert_source_forbids(src, list(self.FORBIDDEN), context="suspend_user")

    def test_restore_route_touches_only_status_columns(self):
        src = _route_code("restore_user")
        assert_source_forbids(src, list(self.FORBIDDEN), context="restore_user")

    def test_status_update_helper_touches_only_status_columns(self):
        src = extract_function_source(_ADMIN_CODE, "_apply_status_change")
        assert_source_forbids(src, list(self.FORBIDDEN), context="_apply_status_change")
        assert "UPDATE users" in src

    def test_schedule_deletion_does_not_touch_owned_objects(self):
        """削除予約も状態遷移だけ（巻き添え削除は purge の前提チェックで拒否する）。"""
        src = _route_code("schedule_user_deletion")
        assert_source_forbids(
            src, ["uploaded_by", "DELETE FROM", "learning_states"],
            context="schedule_user_deletion",
        )


# ===========================================================================
# §12-3 auth_events は append-only（AL5）
# ===========================================================================


class TestAuthEventsAppendOnly:
    def test_no_delete_or_update_of_auth_events_in_layer(self):
        for name, src in _LAYER_SOURCES.items():
            assert_source_forbids(
                src, ["DELETE FROM auth_events", "UPDATE auth_events"], context=name,
            )

    def test_no_route_mutates_auth_events(self):
        """admin.py に auth_events を書き換える／消す HTTP 経路が無い。
        読み取り（個票）と INSERT（core/auth_events.py 経由）だけ。"""
        assert "auth_events" in _ADMIN_CODE  # 個票が読んでいる
        assert "DELETE FROM auth_events" not in _ADMIN_CODE
        assert "UPDATE auth_events" not in _ADMIN_CODE

    def test_core_auth_events_module_has_no_mutation_helpers(self):
        src = _code_only((BACKEND / "core" / "auth_events.py").read_text(encoding="utf-8"))
        assert_source_forbids(
            src, ["DELETE FROM auth_events", "UPDATE auth_events"], context="core/auth_events.py",
        )


# ===========================================================================
# §12-4 資格情報を監査・ログ・payload に流さない（AL4）
# ===========================================================================


class TestNoCredentialLeak:
    #: 監査・ログ・auth_events payload に現れてはいけない識別子。
    CREDENTIAL_NAMES = ("new_password", "password", "hashed", "password_hash", "pw")
    #: 検査対象の呼び出し（監査記帳・ログ・認証イベント記録）。
    SINKS = ("record_review_event", "_audit_account", "record_auth_event")

    def _credential_leaks(self, src: str, context: str) -> list[str]:
        tree = ast.parse(src)
        leaks: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
                is_logger = isinstance(func.value, ast.Name) and func.value.id == "logger"
            else:
                name = getattr(func, "id", "")
                is_logger = False
            if not (is_logger or name in self.SINKS):
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name) and sub.id in self.CREDENTIAL_NAMES:
                        leaks.append(f"{context}: {name}(... {sub.id} ...)")
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        if sub.value in self.CREDENTIAL_NAMES:
                            leaks.append(f"{context}: {name}(... {sub.value!r} key ...)")
        return leaks

    def test_admin_routes_never_pass_credentials_to_audit_or_logger(self):
        leaks = self._credential_leaks(_ADMIN_SRC, "api/routes/admin.py")
        assert leaks == [], leaks

    def test_core_module_never_passes_credentials_to_audit_or_logger(self):
        leaks = self._credential_leaks(_CORE_SRC, "core/account_lifecycle.py")
        assert leaks == [], leaks

    def test_password_reset_response_model_has_no_secret_field(self):
        from schemas import PasswordResetResponse

        fields = set(PasswordResetResponse.model_fields)
        assert not any("password" in f and f != "password_updated_at" for f in fields)
        assert "new_password" not in fields
        assert "password_hash" not in fields

    def test_tombstone_hash_is_a_sentinel_not_a_real_hash(self):
        from core import account_lifecycle

        assert account_lifecycle.TOMBSTONE_PASSWORD_HASH == "!"
        # NULL にしない（§4.1 の NULL ガードと二重防御）
        assert account_lifecycle.TOMBSTONE_PASSWORD_HASH is not None


# ===========================================================================
# §12-5 一覧の fail-closed / 個票の権限（AL7）
# ===========================================================================


class TestRoleFailClosed:
    def test_list_forces_learner_for_non_system_admin(self):
        src = _route_source("list_users")
        # SYSTEM_ADMIN 判定の else 側で learner に固定している
        assert "_DB_ROLE_LEARNER" in src
        assert "ROLE_SYSTEM_ADMIN" in src
        else_branch = src.split("else:", 1)[1]
        assert "role_filter = _DB_ROLE_LEARNER" in else_branch

    def test_activity_requires_system_admin(self):
        src = _route_source("get_user_activity")
        assert "_require_system_admin" in src
        assert "_require_teacher" not in src

    def test_password_reset_requires_system_admin(self):
        """§14-1 裁定: 対象が学生でも SYSTEM_ADMIN のみ。"""
        src = _route_source("reset_user_password")
        assert "_require_system_admin" in src
        assert "_require_teacher" not in src

    def test_deletion_and_transfer_require_system_admin(self):
        for fn in ("schedule_user_deletion", "cancel_user_deletion", "transfer_user_ownership"):
            src = _route_source(fn)
            assert "_require_system_admin" in src, fn
            assert "_require_teacher" not in src, fn

    def test_target_role_is_read_from_the_database(self):
        """対象ロールをリクエストから受け取らない（TEACHER が偽れない fail-closed）。"""
        helper = extract_function_source(_ADMIN_SRC, "_require_role_for_target")
        assert 'target["db_role"]' in helper
        assert "_ELEVATED_DB_ROLES" in helper
        assert "403" in helper
        for fn in ("suspend_user", "restore_user"):
            assert "_require_role_for_target" in _route_source(fn), fn

    def test_capabilities_are_split_per_target_role(self):
        """required_role は1値なので、対象ロール別に capability を分割する（P1）。"""
        from core.admin_assistant import capabilities as caps

        expected = {
            "users.list": "TEACHER",
            "users.suspend_student": "TEACHER",
            "users.restore_student": "TEACHER",
            "users.suspend_teacher": "SYSTEM_ADMIN",
            "users.restore_teacher": "SYSTEM_ADMIN",
            "users.password_reset": "SYSTEM_ADMIN",
            "users.schedule_deletion": "SYSTEM_ADMIN",
            "users.transfer_ownership": "SYSTEM_ADMIN",
        }
        for cap_id, role in expected.items():
            cap = caps.get_capability(cap_id)
            assert cap is not None, f"{cap_id} が未登録"
            assert cap.required_role == role, f"{cap_id}: {cap.required_role}"
            assert cap.howto_doc, f"{cap_id} に howto_doc が無い"
            assert cap.locate_steps, f"{cap_id} に locate_steps が無い"

    def test_teacher_cannot_reach_teacher_account_capabilities(self):
        from core.admin_assistant import capabilities as caps

        teacher_ids = {c.id for c in caps.capabilities_for("TEACHER")}
        for cap_id in ("users.suspend_teacher", "users.restore_teacher",
                       "users.password_reset", "users.schedule_deletion",
                       "users.transfer_ownership"):
            assert cap_id not in teacher_ids, cap_id
        assert "users.suspend_student" in teacher_ids
        assert "users.list" in teacher_ids

    def test_irreversible_capabilities_require_confirm(self):
        """P2: reversible=False は必ず confirm=True。"""
        from core.admin_assistant import capabilities as caps

        for cap in caps.all_capabilities():
            if cap.id.startswith("users.") and cap.is_action() and not cap.reversible:
                assert cap.confirm, cap.id


# ===========================================================================
# §12-6 ロックアウト防止（AL10）
# ===========================================================================


class TestLockoutGuard:
    def test_guard_helper_rejects_self_and_bootstrap(self):
        src = extract_function_source(_ADMIN_SRC, "_guard_lockout")
        assert "BOOTSTRAP_ADMIN_DISPLAY_NAME" in src
        assert 'current_user.get("id")' in src
        assert src.count("422") >= 2

    def test_bootstrap_is_identified_by_fixed_display_name(self):
        """§14-5 裁定: is_bootstrap 列は追加せず固定名一致で同定する。"""
        import routes.admin as routes

        assert routes.BOOTSTRAP_ADMIN_DISPLAY_NAME == "Administrator"
        assert "is_bootstrap" not in _ADMIN_CODE

    def test_suspend_and_deletion_apply_the_guard(self):
        for fn in ("suspend_user", "schedule_user_deletion"):
            assert "_guard_lockout" in _route_source(fn), fn

    def test_restore_does_not_apply_the_guard(self):
        """再開はロックアウトを作らない（誤って禁止すると復旧できなくなる）。"""
        assert "_guard_lockout" not in _route_source("restore_user")


# ===========================================================================
# §12-7 purge の前提チェック（AL9）
# ===========================================================================


class TestPurgePrecondition:
    def test_leftover_check_precedes_any_mutation(self):
        src = extract_function_source(_CORE_SRC, "purge_user")
        leftover_at = src.index("_leftover_counts(")
        delete_at = src.index("_delete_sql(")
        update_at = src.index("UPDATE users")
        assert leftover_at < delete_at < update_at

    def test_leftovers_return_before_mutating(self):
        src = extract_function_source(_CORE_SRC, "purge_user")
        block = src[src.index("if leftovers:"):src.index("deleted_counts")]
        assert "return False" in block
        assert "UPDATE users" not in block
        assert "_delete_sql" not in block

    def test_ownership_guards_cover_all_three_owner_columns(self):
        from core import account_lifecycle

        assert {t for t, _c, _l in account_lifecycle.OWNERSHIP_GUARDS} == {
            "documents", "learning_courses", "groups",
        }

    def test_blocked_purge_notifies_system_admins(self):
        src = extract_function_source(_CORE_SRC, "_notify_system_admins_blocked")
        assert "role = 'admin'" in src
        assert "INSERT INTO user_notifications" in src
        assert "'status'" in src  # migration 045 の source CHECK に収まる

    def test_blocked_notice_is_a_fact_line_without_blame(self):
        src = extract_function_source(_CORE_CODE, "_leftover_fact_line")
        assert "移管または削除してから再実行されます" in src
        for forbidden in ("失敗しました", "危険", "警告", "早く", "してください！"):
            assert forbidden not in src


# ===========================================================================
# §12-8 監査 entity_type はカタログ定数（生文字列禁止）
# ===========================================================================


class TestAuditCatalog:
    def test_catalog_constant_exists_and_is_registered(self):
        from core.schema import AUDIT_ENTITY_TYPES, AUDIT_ENTITY_USER_ACCOUNT

        assert AUDIT_ENTITY_USER_ACCOUNT == "user_account"
        assert AUDIT_ENTITY_USER_ACCOUNT in AUDIT_ENTITY_TYPES

    def test_layer_uses_the_constant_not_a_literal(self):
        for name, src in _LAYER_SOURCES.items():
            assert '"user_account"' not in src, f"{name}: 生文字列で entity_type を書いている"
            assert "'user_account'" not in src, f"{name}: 生文字列で entity_type を書いている"

    def test_audit_helper_passes_the_constant(self):
        core_audit = extract_function_source(_CORE_SRC, "_record_audit")
        assert "AUDIT_ENTITY_USER_ACCOUNT" in core_audit
        route_audit = extract_function_source(_ADMIN_SRC, "_audit_account")
        assert "AUDIT_ENTITY_USER_ACCOUNT" in route_audit
        assert "record_review_event" in route_audit

    def test_every_state_changing_route_records_an_audit_event(self):
        for fn in ("suspend_user", "restore_user", "reset_user_password",
                   "schedule_user_deletion", "cancel_user_deletion",
                   "transfer_user_ownership"):
            assert "_audit_account" in _route_source(fn), f"{fn} が監査記帳していない"

    def test_read_only_routes_do_not_record_audit_events(self):
        for fn in ("list_users", "get_user_activity"):
            assert "_audit_account" not in _route_source(fn), fn


# ===========================================================================
# §12-9 失効はトークン世代 + キャッシュ破棄（AL3）
# ===========================================================================


class TestTokenGenerationInvalidation:
    def test_state_changing_routes_invalidate_the_cache(self):
        for fn in ("suspend_user", "restore_user", "reset_user_password",
                   "schedule_user_deletion", "cancel_user_deletion"):
            src = _route_source(fn)
            assert "account_status.invalidate(" in src, f"{fn} が invalidate を呼んでいない"

    def test_invalidate_is_called_after_commit(self):
        """commit 前に呼ぶと他スレッドが旧値を再キャッシュする窓が残る。"""
        for fn in ("suspend_user", "restore_user", "reset_user_password"):
            src = _route_source(fn)
            assert src.index("session.commit()") < src.index("account_status.invalidate("), fn

    def test_password_reset_bumps_token_generation(self):
        src = _route_source("reset_user_password")
        assert "token_generation = token_generation + 1" in src

    def test_purge_bumps_generation_and_invalidates(self):
        src = extract_function_source(_CORE_SRC, "purge_user")
        assert "token_generation = token_generation + 1" in src
        assert "account_status.invalidate(" in src

    def test_password_reset_records_the_auth_event(self):
        src = _route_source("reset_user_password")
        assert "AUTH_EVENT_PASSWORD_RESET" in src
        assert "record_auth_event" in src


# ===========================================================================
# §12-11 purge 網羅性（将来 migration の取りこぼし検出）
# ===========================================================================

_CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_0-9]+)", re.I)
_ALTER_TABLE_RE = re.compile(r"ALTER\s+TABLE\s+([A-Za-z_0-9]+)", re.I)


def _tables_referencing_users() -> set[str]:
    """``grep "REFERENCES users(id)" backend/db/*.sql`` のテーブル名集合。

    ``CREATE TABLE`` / ``ALTER TABLE`` を直近の対象として追跡する（同一行・別行の
    両形に対応）。設計書 §12-11 の「正本は grep の再実行結果」をコード化したもの。
    """
    tables: set[str] = set()
    for sql_file in sorted((BACKEND / "db").glob("*.sql")):
        current: str | None = None
        for line in sql_file.read_text(encoding="utf-8").splitlines():
            create = _CREATE_TABLE_RE.search(line)
            if create:
                current = create.group(1)
            alter = _ALTER_TABLE_RE.search(line)
            if alter:
                current = alter.group(1)
            if "REFERENCES users(id)" in line and current:
                tables.add(current)
    return tables


class TestPurgeCoverage:
    def test_parser_finds_the_known_tables(self):
        """パーサ自体の健全性（空 set で全 pass する事故を防ぐ）。"""
        tables = _tables_referencing_users()
        assert len(tables) >= 40, tables
        for known in ("documents", "learning_courses", "groups", "group_members",
                      "learner_profiles", "theory_review_events", "llm_model_policies"):
            assert known in tables, known

    def test_every_fk_table_is_declared_purge_or_retain(self):
        from core import account_lifecycle

        declared = {t.table for t in account_lifecycle.PURGE_TABLES}
        declared |= {n.table for n in account_lifecycle.RETAIN_TABLES}
        missing = sorted(_tables_referencing_users() - declared)
        assert missing == [], (
            "users(id) を参照する以下のテーブルが PURGE_TABLES / RETAIN_TABLES の"
            f"どちらにも宣言されていません（設計書 §8.4 の表を更新してください）: {missing}"
        )

    def test_declared_tables_are_real_tables(self):
        """宣言側のタイポ検出（FK なしで宣言されるテーブルは DDL 上の実在を確認する）。"""
        from core import account_lifecycle

        ddl = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted((BACKEND / "db").glob("*.sql"))
        )
        declared = {t.table for t in account_lifecycle.PURGE_TABLES}
        declared |= {n.table for n in account_lifecycle.RETAIN_TABLES}
        unknown = sorted(t for t in declared if f" {t} " not in ddl and f"{t}(" not in ddl
                         and f"{t} (" not in ddl)
        assert unknown == [], f"DDL に現れないテーブル名が宣言されています: {unknown}"

    def test_personal_learning_traces_are_purged(self):
        """本人の学習痕跡・会話・個人設定が purge 対象に入っている（§8.4）。"""
        from core import account_lifecycle

        purged = {t.table for t in account_lifecycle.PURGE_TABLES}
        for table in ("learner_profiles", "learning_states", "learning_chat_history",
                      "chat_sessions", "course_builder_sessions", "unanswered_query_logs",
                      "interest_traces", "learner_reconstructions", "student_stumble_events",
                      "atlas_cue_events", "assistant_step_dismissals", "assistant_actions",
                      "counterfactual_sessions", "user_notifications",
                      "shared_version_subscriptions", "group_members", "group_invitations",
                      "llm_model_policies", "sessions"):
            assert table in purged, f"{table} が purge 対象に入っていない"

    def test_audit_and_community_records_are_retained(self):
        """監査・テレメトリ・共同体の記録は残す（AL8）。"""
        from core import account_lifecycle

        retained = {n.table for n in account_lifecycle.RETAIN_TABLES}
        purged = {t.table for t in account_lifecycle.PURGE_TABLES}
        for table in ("theory_review_events", "auth_events", "llm_usage_events",
                      "discuss_metric_events", "component_endorsements",
                      "component_citations", "component_explanations", "challenges",
                      "verification_proposals", "atlas_correction_reports",
                      "shared_versions"):
            assert table in retained, f"{table} が「残す」表に無い"
            assert table not in purged, f"{table} を purge してはいけない（AL8）"

    def test_owned_objects_are_never_purged_directly(self):
        """所有オブジェクトは移管または個別削除が前提（AL9）。purge が直接消さない。"""
        from core import account_lifecycle

        purged = {t.table for t in account_lifecycle.PURGE_TABLES}
        for table in ("documents", "learning_courses", "groups"):
            assert table not in purged, table

    def test_group_invitations_covers_both_directions(self):
        from core import account_lifecycle

        columns = {
            t.column for t in account_lifecycle.PURGE_TABLES if t.table == "group_invitations"
        }
        assert columns == {"invitee_user_id", "inviter_user_id"}


# ===========================================================================
# 通知宛先からの墓標除外（§8.4）
# ===========================================================================


class TestDeletedRecipientsExcluded:
    #: 宛先を解決する関数（全数）。
    RESOLVERS = (
        "document_owner_id",
        "course_owner_id",
        "document_group_member_ids",
        "course_group_member_ids",
        "atlas_bound_course_owner_ids",
        "atlas_skeleton_editor_ids",
        "document_legacy_group_visibility_member_ids",
    )

    def test_every_resolver_joins_users_and_excludes_deleted(self):
        missing = []
        for fn in self.RESOLVERS:
            src = extract_function_source(_RECIPIENTS_SRC, fn)
            if "JOIN users" not in src or "u.status <> 'deleted'" not in src:
                missing.append(fn)
        assert missing == [], f"墓標除外の JOIN が無い宛先解決関数: {missing}"

    def test_resolver_list_matches_the_module(self):
        """関数を足したら本テストの一覧にも足す（黙った取りこぼしを防ぐ）。"""
        from core import notification_recipients

        public = {
            name for name in dir(notification_recipients)
            if not name.startswith("_") and callable(getattr(notification_recipients, name))
            and getattr(getattr(notification_recipients, name), "__module__", "")
            == "core.notification_recipients"
        }
        assert public == set(self.RESOLVERS)

    def test_deleted_literal_avoids_extra_bind_params(self):
        """既存呼び出し側・既存テストが params dict の形に依存しているため、
        除外条件はリテラルで書く（バインドパラメータを増やさない）。"""
        src = extract_function_source(_RECIPIENTS_SRC, "document_group_member_ids")
        assert ":deleted" not in src
        assert "u.status <> 'deleted'" in src


# ===========================================================================
# API 契約の静的検査
# ===========================================================================


class TestRouteContract:
    def test_all_eight_endpoints_are_registered(self):
        from api.main import app

        paths = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", ())
        }
        expected = {
            ("/api/admin/users", "GET"),
            ("/api/admin/users/{user_id}/suspend", "POST"),
            ("/api/admin/users/{user_id}/restore", "POST"),
            ("/api/admin/users/{user_id}/password-reset", "POST"),
            ("/api/admin/users/{user_id}/activity", "GET"),
            ("/api/admin/users/{user_id}/deletion", "POST"),
            ("/api/admin/users/{user_id}/deletion", "DELETE"),
            ("/api/admin/users/{user_id}/transfer-ownership", "POST"),
        }
        assert expected <= paths, sorted(expected - paths)

    def test_no_user_deletion_route_exists(self):
        """AL1: users 行を消す HTTP 経路を作らない（削除は予約 → purge のみ）。"""
        from api.main import app

        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set())
            if path == "/api/admin/users/{user_id}" and "DELETE" in methods:
                raise AssertionError("users 行を直接削除するルートが存在する")

    def test_every_lifecycle_route_exists_in_source(self):
        for fn in _LIFECYCLE_ROUTES:
            assert f"def {fn}(" in _ADMIN_SRC, fn

    def test_error_contract_uses_404_for_missing_targets(self):
        src = extract_function_source(_ADMIN_SRC, "_load_user_or_404")
        assert src.count("404") >= 2  # 不正 UUID と不在の両方
        assert "403" not in src  # 存在を教えない（404 統一）

    def test_grace_days_default_comes_from_versioning_layer(self):
        src = _route_source("schedule_user_deletion")
        assert "DEFAULT_GRACE_DAYS" in src
