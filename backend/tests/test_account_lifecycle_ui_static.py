"""アカウントライフサイクル管理の管理画面 UI（admin.js）静的ガードレール。

正本: docs/features/account_lifecycle_management_design.md §9（UI）+ §1 の不変条項
  AL2  停止は認証の拒否のみ（所有権・共有・受講状態を触らない → 文言でもそう書く）
  AL6/AL7 学生の個票（パスワード再設定・利用状況・削除予約）は SYSTEM_ADMIN のみ
  AL10 自分自身と Administrator は停止・削除できない（UI では理由付きで無効化）

バックエンド（`routes/admin.py` の一覧・停止・リセット・削除予約・移管 API、
`dependencies.py` の token_generation 照合）は別ファイル
（`test_account_lifecycle_{api,auth,purge}.py` / `test_account_lifecycle_guardrails.py`）が
担当する。ここでは admin フロント + アンカー3点セットの静的契約だけを検証する
（`test_atlas_gaps_admin_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
TEACHERS_MANUAL = ROOT / "docs" / "manual" / "system_admin" / "10-admin-teachers.md"
STUDENTS_MANUAL = ROOT / "docs" / "manual" / "teacher" / "16-admin-students.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402

# 追加した data-ui-anchor（マニュアル節参照）の全集合。
TEACHER_TAB_ANCHORS = (
    "teachers.user-list",
    "teachers.user-suspend",
    "teachers.user-reset",
    "teachers.user-activity",
    "teachers.user-delete",
    "teachers.user-transfer",
)
# TEACHER にも見える要素（teacher/ 節を指す）。
STUDENT_TAB_TEACHER_ANCHORS = (
    "students.user-list",
    "students.user-suspend",
)
# SYSTEM_ADMIN 限定要素（system_admin/ 節を指す = AL7 の fail-closed）。
STUDENT_TAB_ADMIN_ANCHORS = (
    "students.user-reset",
    "students.user-activity",
    "students.user-delete",
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _lifecycle_segment() -> str:
    """admin.js に追加したアカウントライフサイクル節だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── アカウントライフサイクル管理")
    end = src.index("  // ── Role-based UI setup", start)
    return src[start:end]


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n  }", start)]


def _strip_comment_lines(src: str) -> str:
    """行頭コメント（`// …`）を落とす。

    実装コメントは設計条項（「数値を見せない」等）を引用するため、禁止語彙検査に
    混ぜると自分の禁止条項の記述で落ちる。検査対象は利用者に出る文字列とコードだけ。
    """
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


# ===========================================================================
# 1. 節の定型（ES5・ポーリングなし）
# ===========================================================================


class TestSegmentConventions:
    def test_segment_exists(self):
        seg = _lifecycle_segment()
        assert "function alAccountSectionHtml(scope)" in seg
        assert "function alLoadUsers(scope)" in seg
        assert "function alInitAccountLifecycle()" in seg

    def test_es5_only(self):
        """admin.js は ES5 縛り（const/let/アロー関数/テンプレートリテラルを使わない）。"""
        seg = _lifecycle_segment()
        assert "=>" not in seg
        assert not re.search(r"(?<![\w.])const\s", seg)
        assert not re.search(r"(?<![\w.])let\s", seg)
        assert "`" not in seg

    def test_no_polling(self):
        """自動更新しない（タブ表示・操作成功・明示の更新ボタンだけで再取得する）。"""
        seg = _lifecycle_segment()
        assert "setInterval" not in seg
        assert "setTimeout" not in seg
        assert 'onTabActivate(scope, function () { alLoadUsers(scope); });' in seg

    def test_refresh_paths_are_explicit(self):
        seg = _lifecycle_segment()
        init = _fn_block(seg, "function alInitAccountLifecycle() {")
        assert "refreshBtn.addEventListener" in init
        assert "onTabActivate(" in init

    def test_no_scores_or_pressure_wording(self):
        """数値スコア・煽り・督促の語彙を出さない。"""
        seg = _strip_comment_lines(_lifecycle_segment())
        for banned in ("スコア", "ランキング", "至急", "危険度", "ポイント", "評価点", "連続日数"):
            assert banned not in seg, f"禁止語彙 {banned!r} が UI 文言に含まれている"


# ===========================================================================
# 2. 一覧（§9.1 / §9.2）
# ===========================================================================


class TestUserList:
    def test_section_rendered_in_both_panels(self):
        src = _read(ADMIN_JS)
        assert 'alAccountSectionHtml("students")' in src
        assert 'alAccountSectionHtml("teachers")' in src

    def test_list_containers_carry_anchors(self):
        seg = _lifecycle_segment()
        assert '"students.user-list"' in seg
        assert '"teachers.user-list"' in seg
        assert 'data-ui-anchor="\' + AL_ANCHORS[scope].list + \'"' in seg

    def test_table_columns(self):
        seg = _lifecycle_segment()
        row = _fn_block(seg, "function alRenderUsers(scope, users) {")
        for col in ("ユーザー名", "メール", "状態", "最終ログイン", "最終アクセス", "操作"):
            assert "<th>" + col + "</th>" in row

    def test_role_query_param_uses_db_vocabulary(self):
        """role クエリは DB 語彙（learner / instructor）で送る。"""
        seg = _lifecycle_segment()
        assert 'role: "learner"' in seg
        assert 'role: "instructor"' in seg
        load = _fn_block(seg, "function alLoadUsers(scope) {")
        assert '"/admin/users?role=" + encodeURIComponent(cfg.role)' in load

    def test_search_and_pagination_params(self):
        seg = _lifecycle_segment()
        load = _fn_block(seg, "function alLoadUsers(scope) {")
        assert "&limit=" in load
        assert '"&q=" + encodeURIComponent(q)' in load

    def test_list_error_is_a_fact_sentence(self):
        seg = _lifecycle_segment()
        load = _fn_block(seg, "function alLoadUsers(scope) {")
        assert "一覧を取得できませんでした" in load

    def test_last_seen_is_labelled_as_approximate(self):
        """最終アクセスはスロットル更新の近似値である事実を注記する（§4.2-4）。"""
        seg = _lifecycle_segment()
        assert "目安の値" in seg


class TestStatusChip:
    def test_status_vocabulary(self):
        seg = _lifecycle_segment()
        assert re.search(r"var AL_STATUS_LABELS = \{", seg)
        block = seg[seg.index("var AL_STATUS_LABELS = {") : seg.index("};", seg.index("var AL_STATUS_LABELS = {"))]
        for key, label in (
            ("active", "有効"),
            ("suspended", "停止中"),
            ("pending_deletion", "削除予定"),
            ("deleted", "削除済み"),
        ):
            assert key + ':' in block
            assert '"' + label + '"' in block

    def test_chip_exposes_status_data_attribute(self):
        seg = _lifecycle_segment()
        chip = _fn_block(seg, "function alStatusChip(status) {")
        assert 'data-al-status="' in chip

    def test_deleted_rows_have_no_action_buttons(self):
        seg = _lifecycle_segment()
        row = _fn_block(seg, "function alUserRowHtml(scope, u) {")
        assert 'if (status !== "deleted") {' in row


# ===========================================================================
# 3. 行操作（データ属性・委譲・SYSTEM_ADMIN ゲート）
# ===========================================================================


class TestRowActions:
    def test_action_buttons_carry_data_attributes(self):
        seg = _lifecycle_segment()
        btn = _fn_block(seg, "function alActionBtn(action, anchor, label, uid, uname) {")
        for attr in ("data-al-action=", "data-al-user-id=", "data-al-username="):
            assert attr in btn

    def test_click_is_delegated_and_skips_disabled(self):
        seg = _lifecycle_segment()
        init = _fn_block(seg, "function alInitAccountLifecycle() {")
        assert 'closest("[data-al-action]")' in init
        assert "if (!btn || btn.disabled) return;" in init

    def test_action_dispatch_covers_every_operation(self):
        seg = _lifecycle_segment()
        dispatch = _fn_block(seg, "function alHandleAction(scope, action, uid, uname) {")
        for action in ("suspend", "restore", "reset", "activity", "deletion", "cancel-deletion", "transfer"):
            assert 'action === "' + action + '"' in dispatch

    def test_system_admin_only_actions_are_gated(self):
        """AL7: パスワード再設定・利用状況・削除予約は SYSTEM_ADMIN のときだけ描画する。"""
        seg = _lifecycle_segment()
        row = _fn_block(seg, "function alUserRowHtml(scope, u) {")
        assert 'var isSystemAdmin = state.role === "SYSTEM_ADMIN";' in row
        gate = row[row.index("if (isSystemAdmin) {") :]
        for action in ("reset", "activity", "deletion"):
            assert 'alActionBtn("' + action + '"' in gate, f"{action} が SYSTEM_ADMIN ゲートの外にある"
        before_gate = row[: row.index("if (isSystemAdmin) {")]
        for action in ("reset", "activity", "deletion", "cancel-deletion"):
            assert 'alActionBtn("' + action + '"' not in before_gate

    def test_suspend_and_restore_available_to_teacher(self):
        """停止・再開は TEACHER 以上（SYSTEM_ADMIN ゲートの外に描画する）。"""
        seg = _lifecycle_segment()
        row = _fn_block(seg, "function alUserRowHtml(scope, u) {")
        before_gate = row[: row.index("if (isSystemAdmin) {")]
        assert 'alActionBtn("suspend"' in before_gate
        assert 'alActionBtn("restore"' in before_gate

    def test_transfer_only_on_teachers_tab(self):
        """学生は所有物を持たないため、移管ボタンは学生タブに置かない。"""
        seg = _lifecycle_segment()
        anchors = seg[seg.index("var AL_ANCHORS = {") : seg.index("var AL_STATUS_LABELS")]
        students = anchors[anchors.index("students: {") : anchors.index("teachers: {")]
        assert 'transfer: ""' in students
        teachers = anchors[anchors.index("teachers: {") :]
        assert '"teachers.user-transfer"' in teachers
        row = _fn_block(seg, "function alUserRowHtml(scope, u) {")
        assert "if (a.transfer) {" in row

    def test_lockout_protection_disables_suspend_and_delete(self):
        """AL10: 自分自身と Administrator は停止・削除できない（理由付きで無効化）。"""
        seg = _lifecycle_segment()
        row = _fn_block(seg, "function alUserRowHtml(scope, u) {")
        assert '_meUserId()' in row
        assert '"Administrator"' in row
        assert "alDisabledBtn(a.suspend" in row
        assert "alDisabledBtn(a.remove" in row

    def test_deletion_disabled_reason_states_precondition(self):
        seg = _lifecycle_segment()
        row = _fn_block(seg, "function alUserRowHtml(scope, u) {")
        assert "削除予約は、先に停止したアカウントにだけできます" in row


# ===========================================================================
# 4. 停止・再開（AL2 の文言）
# ===========================================================================


class TestSuspendRestore:
    def test_reason_is_required(self):
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alSuspendUser(scope, uid, uname) {")
        assert "停止の理由" in fn
        assert "if (reason === null) return;" in fn
        assert "if (!reason) {" in fn
        assert '"/suspend"' in fn
        assert '{ reason: reason }' in fn

    def test_suspend_message_states_ownership_is_untouched(self):
        """AL2: 停止は認証の拒否のみ。所有・共有・受講状態が変わらない事実を書く。"""
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alSuspendUser(scope, uid, uname) {")
        assert "ログインできなくなります" in fn
        assert "受講状態は変わりません" in fn

    def test_restore_endpoint(self):
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alRestoreUser(scope, uid, uname) {")
        assert '"/restore"' in fn
        assert '{ method: "POST" }' in fn


# ===========================================================================
# 5. パスワード再設定（8文字以上・self_reset で再ログイン）
# ===========================================================================


class TestPasswordReset:
    def test_min_length_checked_client_side(self):
        seg = _lifecycle_segment()
        assert "var AL_MIN_PASSWORD_LENGTH = 8;" in seg
        fn = seg[seg.index("function alOpenPasswordResetModal(scope, uid, uname) {") : seg.index("function alTokenText(value) {")]
        assert "v1.length >= AL_MIN_PASSWORD_LENGTH" in fn
        assert "v1 === v2" in fn

    def test_password_input_is_masked(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenPasswordResetModal(scope, uid, uname) {") : seg.index("function alTokenText(value) {")]
        assert 'type="password" id="al-reset-pw"' in fn
        assert 'type="password" id="al-reset-pw2"' in fn

    def test_self_reset_forces_relogin(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenPasswordResetModal(scope, uid, uname) {") : seg.index("function alTokenText(value) {")]
        assert "data.self_reset" in fn
        assert "再ログイン" in fn
        assert "performLogout();" in fn

    def test_performlogout_is_shared_with_header_logout(self):
        """ログアウト処理を二重実装しない（ヘッダーのログアウトと同じ関数を使う）。"""
        src = _read(ADMIN_JS)
        assert "function performLogout() {" in src
        init = _fn_block(src, "function initLogout() {")
        assert "performLogout();" in init

    def test_reset_endpoint_and_body(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenPasswordResetModal(scope, uid, uname) {") : seg.index("function alTokenText(value) {")]
        assert '"/password-reset"' in fn
        assert "new_password: pw.value" in fn


# ===========================================================================
# 6. 利用状況モーダル（認証イベント語彙 + LLM サマリ）
# ===========================================================================


class TestActivityModal:
    def test_auth_event_labels_cover_the_vocabulary(self):
        seg = _lifecycle_segment()
        block = seg[seg.index("var AL_AUTH_EVENT_LABELS = {") : seg.index("};", seg.index("var AL_AUTH_EVENT_LABELS = {"))]
        for key, label in (
            ("login_success", "ログイン成功"),
            ("login_failed", "ログイン失敗"),
            ("login_rejected_suspended", "停止中のログイン試行"),
            ("token_rejected_suspended", "停止中のアクセス"),
            ("token_rejected_stale", "失効トークンのアクセス"),
            ("password_reset", "パスワード再設定"),
        ):
            assert key + ":" in block
            assert '"' + label + '"' in block

    def test_activity_endpoint(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenActivityModal(scope, uid, uname) {") : seg.index("// 削除予約は取消可能だが")]
        assert '"/activity"' in fn

    def test_usage_unavailable_is_stated_honestly(self):
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alUsageSummaryHtml(usage) {")
        assert "利用実績を取得できませんでした" in fn

    def test_usage_sources_are_not_merged(self):
        """U1: 実測と推計を別行で並べる（混ぜた単一数値を出さない）。

        activity API の `llm_usage` は `{reported: {...}, estimated: {...},
        top_features: [{feature, reported, estimated}]}` の形で返る
        （`backend/api/routes/admin.py::_activity_llm_usage`）。バケットを合算しない。
        """
        seg = _lifecycle_segment()
        block = seg[seg.index("var AL_USAGE_SOURCE_LABELS = {") : seg.index("};", seg.index("var AL_USAGE_SOURCE_LABELS = {"))]
        assert "reported:" in block
        assert "estimated:" in block
        assert 'var AL_USAGE_BUCKETS = ["reported", "estimated"];' in seg
        rows = _fn_block(seg, "function alUsageBucketRows(usage) {")
        assert "usage[name]" in rows
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            assert key in rows
        fn = _fn_block(seg, "function alUsageSummaryHtml(usage) {")
        assert "AL_USAGE_SOURCE_LABELS[r.usage_source]" in fn
        assert "実測と推計は別の行として表示します" in fn

    def test_top_features_keep_reported_and_estimated_apart(self):
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alUsageSummaryHtml(usage) {")
        assert "usage.top_features" in fn
        assert "f.reported" in fn
        assert "f.estimated" in fn

    def test_activity_shows_status_reason_and_purge_date(self):
        """個票だけが持つ情報（status_reason / purge_after）を落とさない。"""
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alActivityHtml(data) {")
        assert "user.status_reason" in fn
        assert "user.purge_after" in fn

    def test_unattributed_batch_note_present(self):
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alUsageSummaryHtml(usage) {")
        assert "未帰属" in fn

    def test_no_evaluation_use_note(self):
        """AL7: 学習評価に使わない旨を個票に明記する。"""
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alActivityHtml(data) {")
        assert "学習の評価には使いません" in fn


# ===========================================================================
# 7. 削除予約（名前入力 confirm）・取消・移管
# ===========================================================================


class TestDeletionAndTransfer:
    def test_deletion_requires_exact_username_input(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenDeletionModal(scope, uid, uname) {") : seg.index("function alCancelDeletion(scope, uid, uname) {")]
        assert 'id="al-deletion-confirm-input"' in fn
        assert "確認のため、ユーザー名を正確に入力してください" in fn
        assert "execBtn.disabled = (input.value !== uname);" in fn
        assert 'disabled>削除を予約する' in fn

    def test_deletion_states_grace_and_reversibility(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenDeletionModal(scope, uid, uname) {") : seg.index("function alCancelDeletion(scope, uid, uname) {")]
        assert "14日" in fn
        assert "削除予約を取消" in fn
        assert "移管" in fn
        assert "grace_days" in fn
        # API の受け付ける範囲（1〜365）を入力側でも示す。
        assert 'min="1" max="365"' in fn
        # レスポンスの purge_after をそのまま事実として見せる。
        assert "data.purge_after" in fn

    def test_cancel_deletion_uses_delete_method(self):
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function alCancelDeletion(scope, uid, uname) {")
        assert '"/deletion"' in fn
        assert '{ method: "DELETE" }' in fn

    def test_transfer_target_candidates_are_active_teachers_excluding_self(self):
        """移管先は「有効な TEACHER 以上」= instructor + admin（設計 §5）。

        admin を候補から外すと、教員が1人だけの環境で引き継ぎ先が空になり、AL9
        （所有物が残る限り purge しない）により削除予約が永久に完了しない。API の
        422 文言（「教員または管理者のアカウントを指定してください」）とも食い違う。
        """
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenTransferModal(scope, uid, uname) {") : seg.index("function alHandleAction(scope, action, uid, uname) {")]
        assert '"/admin/users?role=instructor&status=active' in fn
        assert '"/admin/users?role=admin&status=active' in fn
        assert "users[i].id === uid" in fn
        # 管理者を教員と区別できるようにする（運用上の判断材料）。
        assert 'users[i].role === "SYSTEM_ADMIN"' in fn
        assert "（管理者）" in fn

    def test_transfer_candidate_list_dedupes(self):
        """2回のロール別取得をマージするため、同一 id の重複を落とす。"""
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenTransferModal(scope, uid, uname) {") : seg.index("function alHandleAction(scope, action, uid, uname) {")]
        assert "seen[users[i].id]" in fn

    def test_transfer_goes_through_confirm_with_fact_sentences(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenTransferModal(scope, uid, uname) {") : seg.index("function alHandleAction(scope, action, uid, uname) {")]
        assert "openDangerConfirmModal(" in fn
        assert "所有者が" in fn
        assert '"/transfer-ownership"' in fn
        assert "to_user_id: targetId" in fn

    def test_transfer_result_counts_reported(self):
        seg = _lifecycle_segment()
        fn = seg[seg.index("function alOpenTransferModal(scope, uid, uname) {") : seg.index("function alHandleAction(scope, action, uid, uname) {")]
        assert "data.transferred" in fn
        for word in ("教材", "コース", "グループ"):
            assert word in fn


# ===========================================================================
# 8. アンカー3点セット（data-ui-anchor / registry / マニュアル節）
# ===========================================================================


class TestAnchorTriple:
    def test_all_anchor_ids_present_in_admin_js(self):
        seg = _lifecycle_segment()
        for anchor in TEACHER_TAB_ANCHORS + STUDENT_TAB_TEACHER_ANCHORS + STUDENT_TAB_ADMIN_ANCHORS:
            assert '"' + anchor + '"' in seg, f"{anchor} の担体が admin.js に無い"

    def test_all_anchor_ids_registered(self):
        for anchor in TEACHER_TAB_ANCHORS + STUDENT_TAB_TEACHER_ANCHORS + STUDENT_TAB_ADMIN_ANCHORS:
            assert anchor in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS
            assert anchor in admin_anchors_mod.ADMIN_UI_ANCHORS

    def test_teacher_visible_anchors_point_to_teacher_manual(self):
        for anchor in STUDENT_TAB_TEACHER_ANCHORS:
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor].startswith("teacher/")

    def test_system_admin_only_anchors_point_to_system_admin_manual(self):
        """AL7: SYSTEM_ADMIN 限定要素は system_admin/ 節を指す
        （resolve_admin_ui_anchors の fail-closed が TEACHER への配信を止める）。"""
        for anchor in TEACHER_TAB_ANCHORS + STUDENT_TAB_ADMIN_ANCHORS:
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor].startswith("system_admin/")

    def test_teacher_role_cannot_resolve_admin_only_anchors(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor in TEACHER_TAB_ANCHORS + STUDENT_TAB_ADMIN_ANCHORS:
            assert anchor not in resolved
        for anchor in STUDENT_TAB_TEACHER_ANCHORS:
            assert anchor in resolved
            assert resolved[anchor]["body"]

    def test_system_admin_resolves_everything(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("SYSTEM_ADMIN")
        for anchor in TEACHER_TAB_ANCHORS + STUDENT_TAB_TEACHER_ANCHORS + STUDENT_TAB_ADMIN_ANCHORS:
            assert anchor in resolved
            assert resolved[anchor]["title"]
            assert resolved[anchor]["body"]

    def test_locate_anchors_registered_for_both_screens(self):
        """Copilot 道案内（G8）の解決関数を screen 別に登録する。"""
        src = _read(ADMIN_JS)
        for screen in ("students", "teachers"):
            start = src.index('AA.registerUiAnchors("' + screen + '", {')
            block = src[start : src.index("});", start)]
            for key in ("user_list", "user_suspend_button", "user_restore_button",
                        "user_reset_button", "user_activity_button", "user_delete_button"):
                assert key + ":" in block, f"{screen} に {key} が登録されていない"
        teachers_start = src.index('AA.registerUiAnchors("teachers", {')
        teachers_block = src[teachers_start : src.index("});", teachers_start)]
        assert "user_transfer_button:" in teachers_block

    def test_locate_resolution_is_fail_closed(self):
        """行ボタンは一覧描画前に存在しないので、null を返して案内を止める（P8）。"""
        seg = _lifecycle_segment()
        fn = _fn_block(seg, "function _alRowActionAnchor(scope, action) {")
        assert "if (!listEl) return null;" in fn
        assert 'querySelector(\'[data-al-action="\'' in fn


# ===========================================================================
# 9. マニュアル節（操作要素1つ=1節・「無効になっている場合」）
# ===========================================================================


class TestManualSections:
    def test_teacher_tab_manual_sections_exist(self):
        src = _read(TEACHERS_MANUAL)
        for anchor in ("user-list", "user-suspend", "user-reset", "user-activity",
                       "user-delete", "user-transfer",
                       "student-user-reset", "student-user-activity", "student-user-delete"):
            assert "{#" + anchor + "}" in src, f"{anchor} の節が 10-admin-teachers.md に無い"

    def test_student_tab_manual_sections_exist(self):
        src = _read(STUDENTS_MANUAL)
        for anchor in ("user-list", "user-suspend"):
            assert "{#" + anchor + "}" in src, f"{anchor} の節が 16-admin-students.md に無い"

    def test_disabled_reasons_documented(self):
        """無効化され得る要素には「無効（押せない状態）になっている場合」を必ず書く。"""
        src = _read(TEACHERS_MANUAL)
        assert src.count("無効（押せない状態）になっている場合") >= 3
        # 停止（自分自身・Administrator）
        assert "Administrator" in src
        # 削除予約（停止中のみ）
        assert "停止中" in src

    def test_manual_states_no_evaluation_use(self):
        for path in (TEACHERS_MANUAL, STUDENTS_MANUAL):
            assert "学習の評価には使いません" in _read(path)

    def test_manual_states_suspension_semantics(self):
        """AL2: 停止しても所有・共有・受講状態が変わらないことを両方の節に書く。"""
        teachers = _read(TEACHERS_MANUAL)
        assert "ログインの拒否だけ" in teachers
        assert "共有設定" in teachers
        students = _read(STUDENTS_MANUAL)
        assert "ログインの拒否だけ" in students
        assert "受講状態" in students

    def test_manual_states_deletion_preconditions(self):
        teachers = _read(TEACHERS_MANUAL)
        assert "14日" in teachers
        assert "所有物が残っている" in teachers or "所有したままのアカウント" in teachers

    def test_front_matter_intact(self):
        assert _read(TEACHERS_MANUAL).startswith("---\naudience: system_admin\nscreen: teachers\n---\n")
        assert _read(STUDENTS_MANUAL).startswith("---\naudience: teacher\nscreen: students\n---\n")
