"""アカウントライフサイクル管理（AL層）— 削除層（Phase 3）の core 実装。

正本ドキュメント: ``docs/features/account_lifecycle_management_design.md`` §8
（削除のセマンティクス）。DDL は ``backend/db/068_account_lifecycle.sql``。

このモジュールが担うのは削除の3段構造（設計書 §8.1）の後半2段:

1. 予約（``status='pending_deletion'`` + ``purge_after``）— API 層（``routes/admin.py``）
2. **スイーパ**（期限到来の検出）— :func:`due_users`。実行は
   ``core/versioning/worker.py::sweep_once`` に相乗りする（独立 worker を増やさない）
3. **purge**（前提チェック → 個人データの明示 DELETE → 墓標化）— :func:`purge_user`

加えて purge の前提を満たすための所有物移管（設計書 §8.2）を :func:`transfer_ownership`
として持つ（API 8 の実体）。

不変条項（設計書 §1）:

- **AL1 users 行を物理 DELETE しない。** 削除 = 状態遷移 + 墓標化（匿名化）+ 個人データの
  明示 purge。``DELETE FROM users`` をこのモジュールに書かない（17 の NO ACTION FK と
  CASCADE 連鎖を構造的に無害化する唯一の方法）。:data:`PURGE_TABLES` に ``users`` を
  入れないことを import 時に自己検査する。
- **AL8 情報を落とさない。** 監査（``theory_review_events``）・テレメトリ（``auth_events`` /
  ``llm_usage_events`` / ``discuss_metric_events``）・共同体の記録（承認・引用・疑義）は
  墓標化後も残す。何を消し何を残すかは :data:`PURGE_TABLES` / :data:`RETAIN_TABLES` の
  2つの明示リストが正本で、``REFERENCES users(id)`` を持つ全テーブルがどちらかに
  現れることをガードレール（設計書 §12-11）が固定する。
- **AL9 purge は前提条件を構造的に強制する。** 所有オブジェクト（documents /
  learning_courses / groups）が1件でも残っていれば purge を中止し、SYSTEM_ADMIN 宛に
  事実文で通知する。**黙って巻き添え削除しない。** 完了フラグを持たず状態から毎回
  導出するため、スイーパは次周期に再試行する（G1 と同型）。

設計方針:

- FastAPI 非 import（開発ルール2）。LLM も呼ばない。
- 監査記帳は ``core/versioning/audit.py`` と同じ「core 内で完結する直接 INSERT」
  （core が ``api/services.py`` を import しないため。entity_type は必ず
  ``core/schema.py`` のカタログ定数を使う）。
- 通知は ``core/status/cross_layer_notify.py`` と同型の ``source='status'`` 直接 INSERT。
  同モジュールの ``notify_user`` は kind ホワイトリストを持つため相乗りできず、本層の
  kind（:data:`NOTIF_ACCOUNT_PURGE_BLOCKED`）はここで宣言する。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from sqlalchemy import text as sa_text

# 名前 import（from core.postgres import get_session）にすると import 時点の関数参照が
# 束縛され、テストの monkeypatch.setattr(core.postgres, "get_session", ...) が効かない。
# モジュール属性経由で毎回解決する（cross_layer_notify.py と同じ注意）。
import core.postgres
from core import account_status
from core.schema import AUDIT_ENTITY_USER_ACCOUNT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------

#: 監査 action（``theory_review_events.metadata['action']``）。
AUDIT_ACTION_PURGE = "purge"
AUDIT_ACTION_PURGE_BLOCKED = "purge_blocked"
AUDIT_ACTION_TRANSFER_OWNERSHIP = "transfer_ownership"

#: ``user_notifications.kind``（open-vocab。``source='status'`` に相乗りする）。
#: 「所有物が残っていて purge できなかった」ことを SYSTEM_ADMIN に事実として伝える。
NOTIF_ACCOUNT_PURGE_BLOCKED = "account_purge_blocked"

#: purge 中止通知の重複抑止窓（時間）。スイーパは毎周期再試行するため、同じ事実を
#: 毎時間積まない（G4「押し付けない」）。窓を過ぎたら1件だけ再掲する。
BLOCKED_NOTICE_DEDUPE_HOURS = 24

#: 墓標 email のドメイン（実在しないことが保証された予約 TLD）。
TOMBSTONE_EMAIL_DOMAIN = "invalid.local"
#: 墓標 password_hash。検証不能な非 NULL センチネル（設計書 §8.4。NULL にしない —
#: ログイン照合側の NULL ガードと二重防御にするため）。
TOMBSTONE_PASSWORD_HASH = "!"


class PurgeTarget(NamedTuple):
    """purge 時に明示 DELETE する対象（テーブル・列・追加条件）。"""

    table: str
    column: str
    where: str = ""      # 追加の WHERE 条件（例: "scope = 'user'"）
    reason: str = ""     # なぜ本人由来の個人データとみなすか


class RetainNote(NamedTuple):
    """purge しても残すテーブルと、その根拠。"""

    table: str
    reason: str


# ---------------------------------------------------------------------------
# 「DELETE する」表（設計書 §8.4）
# ---------------------------------------------------------------------------
# CASCADE に頼らず明示 DELETE する（どの行が消えるかをコードから読めるようにする）。
# 子テーブルが FK CASCADE で連鎖するものはコメントに明記する。

PURGE_TABLES: tuple[PurgeTarget, ...] = (
    PurgeTarget("learner_profiles", "user_id",
                reason="本人の学習者プロファイル（子3表 learner_concept_states 等は FK CASCADE）"),
    PurgeTarget("learning_states", "user_id", reason="本人の受講状態"),
    PurgeTarget("learning_chat_history", "user_id", reason="本人の学習チャット履歴"),
    PurgeTarget("chat_sessions", "user_id",
                reason="本人の会話セッション（chat_messages は FK CASCADE）"),
    PurgeTarget("course_builder_sessions", "user_id", reason="本人のコース構築作業履歴"),
    PurgeTarget("unanswered_query_logs", "user_id", reason="本人の未回答質問ログ"),
    PurgeTarget("interest_traces", "user_id",
                reason="本人の学習痕跡（FK なし。tension / 問い / 個人地図の素）"),
    PurgeTarget("learner_reconstructions", "user_id", reason="本人の再構成成果物（R層）"),
    PurgeTarget("student_stumble_events", "student_id",
                reason="本人（学習者側）のつまづき痕跡。instructor_id の帰属行は残す"),
    PurgeTarget("atlas_cue_events", "user_id", reason="本人の地図導線履歴"),
    PurgeTarget("assistant_step_dismissals", "user_id", reason="本人の To-Do 却下（G層）"),
    PurgeTarget("assistant_actions", "user_id",
                reason="本人の Copilot 操作スナップショット（before/after を含む）"),
    PurgeTarget("counterfactual_sessions", "owner_id", reason="本人所有の反実仮想セッション"),
    PurgeTarget("user_notifications", "recipient_id", reason="本人宛の通知インボックス"),
    PurgeTarget("shared_version_subscriptions", "subscriber_id", reason="本人の版ピン設定（V層）"),
    PurgeTarget("group_members", "user_id", reason="本人のグループ所属"),
    PurgeTarget("group_invitations", "invitee_user_id", reason="本人宛の招待"),
    PurgeTarget("group_invitations", "inviter_user_id", reason="本人が出した招待"),
    PurgeTarget("llm_model_policies", "user_id", where="scope = 'user'",
                reason="本人の LLM モデル設定（M層。scope='system' 行は残す）"),
    PurgeTarget("sessions", "user_id", reason="未使用テーブルだが網羅性のため明示"),
)

# ---------------------------------------------------------------------------
# 「残す」表（設計書 §8.4）
# ---------------------------------------------------------------------------
# 帰属表示は墓標名（deleted-xxxxxxxx）になる。SET NULL 系・FK なしの帰属列は
# LEFT JOIN users で NULL 安全に解決される。

RETAIN_TABLES: tuple[RetainNote, ...] = (
    # --- 所有オブジェクト（AL9 の前提チェック対象。移管または個別削除が先） ---
    RetainNote("documents", "所有教材。purge の前提チェック対象（移管または個別削除が先）"),
    RetainNote("learning_courses", "所有コース。同上（受講者の learning_states を巻き添えにしない）"),
    RetainNote("groups", "作成グループ。同上（他教員の共有権限を巻き添えにしない）"),
    # --- 監査・テレメトリ ---
    RetainNote("theory_review_events", "監査台帳（AL8）。誰が何をしたかの記録は消さない"),
    RetainNote("auth_events", "認証テレメトリ（FK なし・append-only, AL5）"),
    RetainNote("llm_usage_events", "トークン使用量テレメトリ（FK なし。U層の設計意図を踏襲）"),
    RetainNote("discuss_metric_events", "discuss 観測（FK なし。計測はユーザー削除と独立）"),
    RetainNote("background_tasks", "非同期タスクの実行記録（created_by は SET NULL 済み）"),
    # --- 共同体の記録（消すと他者の状態が黙って変わる） ---
    RetainNote("component_endorsements", "承認記録。消すと他教員の承認数が黙って減る"),
    RetainNote("component_citations", "引用記録。帰属付き再利用の事実を消さない"),
    RetainNote("component_explanations", "説明資産。承認・引用の対象になった共同財"),
    RetainNote("challenges", "疑義（D層）。個人の痕跡ではなく知の記録"),
    RetainNote("verification_proposals", "検証提案（D層 / SL層）。同上"),
    RetainNote("assumption_nodes", "暗黙前提（D層）。確定・却下の判断は共同体の記録"),
    RetainNote("atlas_correction_reports", "分野の地図への修正報告。共同体の記録"),
    RetainNote("atlas_gap_decisions", "カテゴリギャップ候補への教員判断（版非依存の台帳）"),
    RetainNote("schema_proposals", "スキーマ提案・査読の記録"),
    # --- A層/W層の生成物と査読の帰属 ---
    RetainNote("theory_components", "A層成果。created_by は帰属表示のみ"),
    RetainNote("theory_claims", "同上"),
    RetainNote("theory_component_graphs", "同上（updated_by は帰属表示のみ）"),
    RetainNote("theory_component_links", "同上"),
    RetainNote("reconstruction_items", "R層の出題（created_by は帰属表示のみ）"),
    RetainNote("deliberation_sessions", "W層の対話ログ（追記のみ・W4）"),
    RetainNote("element_annotations", "W層の候補・確定注釈（W4: 行削除しない）"),
    RetainNote("element_identity_links", "同一性リンク（KN-2: 非破壊）"),
    RetainNote("document_figures", "図の抽出結果と教員レビュー（mode/analysis_reviewed_by は帰属表示のみ）"),
    RetainNote("course_teaching_figures", "教材図（コースに埋め込まれた配信物。created_by は SET NULL 済み）"),
    RetainNote("teaching_figure_suggestions", "教材図のギャップ候補（同上）"),
    # --- V層 ---
    RetainNote("shared_versions", "不変 Release。published_by は発行時点の事実（付け替え・削除をしない）"),
    RetainNote("shared_version_state", "版状態（delete_scheduled_by は帰属表示のみ）"),
    # --- 学習者側を消しても教員側の帰属行は残す表 ---
    RetainNote("student_stumble_events",
               "instructor_id（教員側の帰属）行は残す。student_id 行は PURGE_TABLES で削除する"),
)

#: purge の前提チェック対象（設計書 §8.3）。(テーブル, 所有者列, 事実文の主語)。
OWNERSHIP_GUARDS: tuple[tuple[str, str, str], ...] = (
    ("documents", "uploaded_by", "教材"),
    ("learning_courses", "user_id", "コース"),
    ("groups", "created_by", "グループ"),
)

# AL1 の自己検査（import 時）。users を purge 対象に混ぜた瞬間に落ちる。
assert all(t.table != "users" for t in PURGE_TABLES), (
    "AL1 違反: users 行は物理削除しない（PURGE_TABLES に users を入れてはならない）"
)


class DueUser(NamedTuple):
    """purge 期限が到来したユーザー（スイーパの入力）。"""

    user_id: str
    scheduled_by: Optional[str]


# ---------------------------------------------------------------------------
# スイーパ入力
# ---------------------------------------------------------------------------


def due_users(session) -> list[DueUser]:
    """purge 期限が到来した ``pending_deletion`` ユーザーを返す（古い順）。

    ``core/versioning/worker.py::sweep_once`` が ``_due_objects()`` と並列に呼ぶ。
    セッションは呼び出し側の責務（読み取りのみ・commit しない）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT id::text, status_changed_by::text
            FROM users
            WHERE status = 'pending_deletion'
              AND purge_after IS NOT NULL
              AND purge_after <= now()
            ORDER BY purge_after
            """
        )
    ).fetchall()
    return [DueUser(user_id=str(r[0]), scheduled_by=(str(r[1]) if r[1] else None)) for r in rows]


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


def _delete_sql(target: PurgeTarget) -> str:
    """PURGE_TABLES の1エントリを DELETE 文にする。

    テーブル名・列名はモジュール定数（外部入力ではない）。値は必ずバインドする。
    """
    clause = f"{target.column} = CAST(:uid AS uuid)"
    if target.where:
        clause = f"{clause} AND {target.where}"
    return f"DELETE FROM {target.table} WHERE {clause}"  # noqa: S608 — 定数のみを補間


def _leftover_counts(session, user_id: str) -> dict[str, int]:
    """所有オブジェクトの残存件数（0 件のテーブルは含めない）。設計書 §8.3。"""
    leftovers: dict[str, int] = {}
    for table, column, _label in OWNERSHIP_GUARDS:
        row = session.execute(
            sa_text(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = CAST(:uid AS uuid)"  # noqa: S608
            ),
            {"uid": user_id},
        ).fetchone()
        count = int(row[0]) if row and row[0] else 0
        if count:
            leftovers[table] = count
    return leftovers


def _leftover_fact_line(leftovers: dict[str, int]) -> str:
    """残存の事実文（煽らない・次にやることだけ書く）。設計書 §8.3。"""
    labels = {table: label for table, _col, label in OWNERSHIP_GUARDS}
    parts = [f"{labels.get(table, table)}が {count} 件" for table, count in leftovers.items()]
    return (
        "アカウント削除を中止しました。"
        + "、".join(parts)
        + "残っています。移管または削除してから再実行されます。"
    )


def _notify_system_admins_blocked(session, user_id: str, display_name: str,
                                  leftovers: dict[str, int]) -> int:
    """purge 中止を SYSTEM_ADMIN 全員へ通知する（best-effort・重複抑止付き）。

    ``source='status'`` に相乗りする（migration 045 の CHECK は ('status','shared') のみ。
    ``core/status/inbox.py`` の既読・却下がそのまま効く）。
    """
    recent = session.execute(
        sa_text(
            """
            SELECT 1 FROM user_notifications
            WHERE kind = :kind AND entity_type = :entity_type AND entity_id = :entity_id
              AND created_at > now() - CAST(:window AS interval)
            LIMIT 1
            """
        ),
        {
            "kind": NOTIF_ACCOUNT_PURGE_BLOCKED,
            "entity_type": AUDIT_ENTITY_USER_ACCOUNT,
            "entity_id": user_id,
            "window": f"{int(BLOCKED_NOTICE_DEDUPE_HOURS)} hours",
        },
    ).fetchone()
    if recent:
        return 0

    admin_rows = session.execute(
        sa_text("SELECT id::text FROM users WHERE role = 'admin' AND status = 'active'")
    ).fetchall()
    recipient_ids = [str(r[0]) for r in admin_rows if r and r[0]]
    if not recipient_ids:
        return 0

    payload = json.dumps(
        {
            "message": _leftover_fact_line(leftovers),
            "target_user_id": user_id,
            "target_display_name": display_name,
            "leftovers": {table: int(count) for table, count in leftovers.items()},
        },
        ensure_ascii=False,
    )
    for rid in recipient_ids:
        session.execute(
            sa_text(
                """
                INSERT INTO user_notifications
                    (recipient_id, kind, entity_type, entity_id, payload, source)
                VALUES (CAST(:uid AS uuid), :kind, :entity_type, :entity_id,
                        CAST(:payload AS jsonb), 'status')
                """
            ),
            {
                "uid": rid,
                "kind": NOTIF_ACCOUNT_PURGE_BLOCKED,
                "entity_type": AUDIT_ENTITY_USER_ACCOUNT,
                "entity_id": user_id,
                "payload": payload,
            },
        )
    return len(recipient_ids)


def tombstone_values(user_id: str) -> dict[str, str]:
    """墓標化後の email / display_name（決定論的 = purge が冪等になる）。設計書 §8.4。"""
    uid = str(user_id)
    return {
        "email": f"deleted+{uid}@{TOMBSTONE_EMAIL_DOMAIN}",
        "display_name": f"deleted-{uid.replace('-', '')[:8]}",
    }


def purge_user(user_id: str, *, scheduled_by: Optional[str] = None) -> bool:
    """個人データを明示 DELETE し、users 行を墓標化する（冪等・独立トランザクション）。

    戻り値: **この呼び出しで墓標化した場合のみ True**。

    - 行が無い / すでに ``status='deleted'`` → False（no-op）
    - 所有オブジェクトが残っている → **users 行を一切変更せず** False
      （SYSTEM_ADMIN へ事実文通知。スイーパが次周期に再試行する。AL9）

    ``DELETE FROM users`` は発行しない（AL1）。
    """
    uid = str(user_id or "")
    if not uid:
        return False

    session = core.postgres.get_session()
    try:
        row = session.execute(
            sa_text("SELECT status, display_name FROM users WHERE id = CAST(:uid AS uuid)"),
            {"uid": uid},
        ).fetchone()
        if row is None:
            logger.info("purge_user: no such user %s (no-op)", uid)
            return False
        status = str(row[0] or "")
        display_name = str(row[1] or "")
        if status == account_status.ACCOUNT_STATUS_DELETED:
            return False  # 冪等: すでに墓標

        leftovers = _leftover_counts(session, uid)
        if leftovers:
            notified = _notify_system_admins_blocked(session, uid, display_name, leftovers)
            session.commit()  # 通知だけをコミットする（users 行は触っていない）
            logger.info("purge_user: aborted for %s — leftovers=%s", uid, leftovers)
            if notified:
                # 抑止窓で通知を見送った周期は記帳もしない（毎時間の同じ事実を積まない）
                _record_audit(uid, status, status, scheduled_by, {
                    "action": AUDIT_ACTION_PURGE_BLOCKED,
                    "leftovers": {t: int(c) for t, c in leftovers.items()},
                })
            return False

        deleted_counts: dict[str, int] = {}
        for target in PURGE_TABLES:
            result = session.execute(sa_text(_delete_sql(target)), {"uid": uid})
            count = int(getattr(result, "rowcount", 0) or 0)
            if count:
                key = f"{target.table}.{target.column}"
                deleted_counts[key] = deleted_counts.get(key, 0) + count

        tombstone = tombstone_values(uid)
        session.execute(
            sa_text(
                """
                UPDATE users
                SET email = :email,
                    display_name = :display_name,
                    password_hash = :password_hash,
                    token_generation = token_generation + 1,
                    status = 'deleted',
                    status_changed_at = now(),
                    status_changed_by = CAST(:by AS uuid),
                    purge_after = NULL,
                    updated_at = now()
                WHERE id = CAST(:uid AS uuid)
                """
            ),
            {
                "email": tombstone["email"],
                "display_name": tombstone["display_name"],
                "password_hash": TOMBSTONE_PASSWORD_HASH,
                "by": scheduled_by or None,
                "uid": uid,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("purge_user failed for %s", uid)
        raise
    finally:
        session.close()

    # 状態が変わったのでキャッシュを落とす（commit の後に呼ぶ。AL3）
    account_status.invalidate(uid)
    _record_audit(uid, status, account_status.ACCOUNT_STATUS_DELETED, scheduled_by, {
        "action": AUDIT_ACTION_PURGE,
        "purged_rows": deleted_counts,
    })
    logger.info("purge_user: tombstoned %s (purged=%s)", uid, deleted_counts)
    return True


# ---------------------------------------------------------------------------
# 所有物の移管（設計書 §8.2）
# ---------------------------------------------------------------------------


def transfer_ownership(session, from_user_id: str, to_user_id: str) -> dict:
    """所有オブジェクトを後任へ付け替える。件数の dict を返す。

    セッションは呼び出し側の責務（**commit しない** — API 層が他の更新と同一
    トランザクションにまとめられるようにする）。

    - ``documents.uploaded_by``
    - ``learning_courses.user_id`` **と ``owner_id`` の両方**（owner_id は認可判定に
      未使用だが INSERT 時に user_id と同値が書かれている実列。黙った不整合を残さない）
    - ``groups.created_by``（併せて移管先を ``group_members`` の admin として保証する）

    受講者の ``learning_states``・共有・V層の版はそのまま生きる（所有者 UUID の
    付け替えだけで権限判定と通知宛先が新所有者へ切り替わる）。
    ``shared_versions.published_by`` は発行時点の事実なので**付け替えない**。
    """
    src = str(from_user_id)
    dst = str(to_user_id)
    params = {"src": src, "dst": dst}

    documents = session.execute(
        sa_text(
            """
            UPDATE documents SET uploaded_by = CAST(:dst AS uuid)
            WHERE uploaded_by = CAST(:src AS uuid)
            """
        ),
        params,
    )
    courses = session.execute(
        sa_text(
            """
            UPDATE learning_courses
            SET user_id = CAST(:dst AS uuid), owner_id = CAST(:dst AS uuid)
            WHERE user_id = CAST(:src AS uuid)
            """
        ),
        params,
    )

    group_rows = session.execute(
        sa_text("SELECT id::text FROM groups WHERE created_by = CAST(:src AS uuid)"),
        {"src": src},
    ).fetchall()
    group_ids = [str(r[0]) for r in group_rows if r and r[0]]
    if group_ids:
        session.execute(
            sa_text(
                """
                UPDATE groups SET created_by = CAST(:dst AS uuid)
                WHERE created_by = CAST(:src AS uuid)
                """
            ),
            params,
        )
        for gid in group_ids:
            # 移管先が admin としてグループを運用できることを保証する（無ければ追加、
            # あれば admin に昇格）。UNIQUE(group_id, user_id) 前提の upsert。
            session.execute(
                sa_text(
                    """
                    INSERT INTO group_members (group_id, user_id, role)
                    VALUES (CAST(:gid AS uuid), CAST(:dst AS uuid), 'admin')
                    ON CONFLICT (group_id, user_id) DO UPDATE SET role = 'admin'
                    """
                ),
                {"gid": gid, "dst": dst},
            )

    return {
        "documents": int(getattr(documents, "rowcount", 0) or 0),
        "courses": int(getattr(courses, "rowcount", 0) or 0),
        "groups": len(group_ids),
    }


# ---------------------------------------------------------------------------
# 監査（core 内で完結。core/versioning/audit.py と同型）
# ---------------------------------------------------------------------------


def _record_audit(user_id: str, old_status: str, new_status: str,
                  actor_id: Optional[str], metadata: Optional[dict] = None) -> None:
    """``theory_review_events`` に1行追記する（best-effort）。

    entity_type は必ずカタログ定数 ``AUDIT_ENTITY_USER_ACCOUNT`` を使う（生文字列禁止）。
    metadata に資格情報（パスワード・ハッシュ）を入れないこと（AL4）。
    """
    session = core.postgres.get_session()
    try:
        session.execute(
            sa_text(
                """
                INSERT INTO theory_review_events
                    (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES
                    (:entity_type, :entity_id, :old_status, :new_status,
                     CAST(:changed_by AS uuid), CAST(:metadata AS jsonb))
                """
            ),
            {
                "entity_type": AUDIT_ENTITY_USER_ACCOUNT,
                "entity_id": str(user_id),
                "old_status": old_status or "",
                "new_status": new_status or "",
                "changed_by": actor_id or None,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("failed to record account lifecycle audit event for %s", user_id,
                       exc_info=True)
    finally:
        session.close()


def default_purge_after(days: int) -> datetime:
    """今から ``days`` 日後（UTC）。既定値は API 層が V層 ``DEFAULT_GRACE_DAYS`` から渡す。"""
    return datetime.now(timezone.utc) + timedelta(days=max(0, int(days)))


__all__ = [
    "AUDIT_ACTION_PURGE",
    "AUDIT_ACTION_PURGE_BLOCKED",
    "AUDIT_ACTION_TRANSFER_OWNERSHIP",
    "BLOCKED_NOTICE_DEDUPE_HOURS",
    "NOTIF_ACCOUNT_PURGE_BLOCKED",
    "OWNERSHIP_GUARDS",
    "PURGE_TABLES",
    "RETAIN_TABLES",
    "TOMBSTONE_PASSWORD_HASH",
    "DueUser",
    "PurgeTarget",
    "RetainNote",
    "default_purge_after",
    "due_users",
    "purge_user",
    "tombstone_values",
    "transfer_ownership",
]
