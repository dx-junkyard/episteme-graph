"""削除予約・取消・物理 purge。

- schedule_deletion: 猶予付き削除予約（lifecycle='pending_deletion'）。期限は所有者指定（既定14日）。
- cancel_deletion: 予約取消。
- purge_object: 全ユーザーからの物理削除（冪等）。既存 delete_material/delete_course 相当に加え、
  現状消し残す document_analysis_runs / document スコープ theory_* の orphan gap も解消する。
  実削除は worker から呼ばれる（手動削除エンドポイントもこの経路に合流させる）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.storage import get_storage_client
from core.teaching_figures import store as teaching_figures_store

from . import schema

logger = logging.getLogger(__name__)


def default_purge_after(days: int = schema.DEFAULT_GRACE_DAYS) -> datetime:
    """今から days 日後（UTC）を返す。"""
    return datetime.now(timezone.utc) + timedelta(days=max(0, int(days)))


def schedule_deletion(
    *,
    object_type: str,
    object_id: str,
    purge_after: datetime,
    scheduled_by: str | None,
    reason: str = "",
) -> dict:
    """削除を予約する（猶予表示のため lifecycle='pending_deletion'）。

    raises: VersioningError — purge_after が過去 / purged 済み。
    """
    if not schema.is_valid_object_type(object_type):
        raise schema.VersioningError(f"invalid object_type: {object_type}")
    now = datetime.now(timezone.utc)
    pa = purge_after if purge_after.tzinfo else purge_after.replace(tzinfo=timezone.utc)
    if pa <= now:
        raise schema.VersioningError("purge_after must be in the future")

    session = get_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO shared_version_state (object_type, object_id, lifecycle)
                VALUES (:ot, :oid, 'active')
                ON CONFLICT (object_type, object_id) DO NOTHING
            """),
            {"ot": object_type, "oid": object_id},
        )
        row = session.execute(
            sa_text("""
                SELECT lifecycle FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        if row and row[0] == schema.LIFECYCLE_PURGED:
            raise schema.PurgedError("object has been purged")
        session.execute(
            sa_text("""
                UPDATE shared_version_state
                SET lifecycle = 'pending_deletion',
                    delete_scheduled_at = now(),
                    delete_purge_after = :pa,
                    delete_scheduled_by = CAST(:by AS uuid),
                    delete_reason = :reason,
                    updated_at = now()
                WHERE object_type = :ot AND object_id = :oid
            """),
            {"pa": pa, "by": scheduled_by or None, "reason": reason or "",
             "ot": object_type, "oid": object_id},
        )
        session.commit()
    except schema.VersioningError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        logger.exception("schedule_deletion failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()
    from . import releases
    return releases.get_state(object_type, object_id) or {}


def cancel_deletion(*, object_type: str, object_id: str, cancelled_by: str | None) -> dict:
    """削除予約を取り消す（active へ戻す）。"""
    session = get_session()
    try:
        session.execute(
            sa_text("""
                UPDATE shared_version_state
                SET lifecycle = 'active',
                    delete_scheduled_at = NULL,
                    delete_purge_after = NULL,
                    delete_scheduled_by = NULL,
                    delete_reason = '',
                    updated_at = now()
                WHERE object_type = :ot AND object_id = :oid
                  AND lifecycle = 'pending_deletion'
            """),
            {"ot": object_type, "oid": object_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("cancel_deletion failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()
    from . import releases
    return releases.get_state(object_type, object_id) or {}


# ---------------------------------------------------------------------------
# 物理削除（冪等）
# ---------------------------------------------------------------------------

def _purge_teaching_figures(session, course_id: str) -> list[str]:
    """教材図（``course_teaching_figures`` + ``teaching_figure_suggestions``、migration 063）を
    削除し、MinIO キー一覧を返す（教材図スタジオ設計書 §3.1）。

    どちらのテーブルも course_id への FK を持たないため CASCADE では消えない
    （object_group_permissions と同じ orphan gap パターン）。MinIO オブジェクトの削除は
    purge の commit 後に呼び出し側が best-effort で行う。
    """
    return list(teaching_figures_store.delete_figures_for_course(session, course_id) or [])


def _remove_figure_objects(minio_keys: list[str]) -> None:
    """図画像（教材図 063 / PDF 抽出図 041）の MinIO オブジェクトを best-effort で削除する。

    どちらも ``figure-images`` バケットに置かれる。失敗は WARN ログのみで、正本である
    DB 行の削除を無効化しない。
    """
    if not minio_keys:
        return
    try:
        storage = get_storage_client()
    except Exception:  # noqa: BLE001 — ストレージ不達でも DB 削除は有効
        logger.warning("figure object cleanup skipped (storage unavailable)", exc_info=True)
        return
    for key in minio_keys:
        if not key:
            continue
        try:
            storage.remove_object("figure-images", key)
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("Failed to remove figure object %s", key, exc_info=True)


def _purge_course(session, course_id: str) -> list[str]:
    session.execute(sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"), {"cid": course_id})
    figure_keys = _purge_teaching_figures(session, course_id)
    # object_group_permissions（migration 044）は course_id への FK を持たない
    # ポリモーフィックテーブルなので、CASCADE には頼れず明示的に削除する
    # （統合前の専用テーブル=migration 010 は learning_courses への FK CASCADE で
    # 自動的に消えていたが、統合後はこの明示 DELETE が無いと孤児行が残る）。
    session.execute(
        sa_text(
            "DELETE FROM object_group_permissions "
            "WHERE object_type = 'course' AND object_id = :cid"
        ),
        {"cid": course_id},
    )
    # CASCADE で learning_states / course スコープ theory_* も削除される
    session.execute(sa_text("DELETE FROM learning_courses WHERE id = :cid"), {"cid": course_id})
    return figure_keys


class PurgedDocument(NamedTuple):
    """``_purge_document`` の戻り値（教材削除の巻き添え範囲を呼び出し側へ返す）。

    - ``course_ids``: 巻き添えで削除した所有者のコース（HTTP 層の応答・監査・V層 teardown 用）
    - ``teaching_figure_keys``: 教材図（``course_teaching_figures``）の MinIO キー
    - ``figure_image_keys``: PDF 抽出図（``document_figures``）の MinIO キー

    MinIO の削除は core では行わない（core は storage を触らない規律）。commit 後に
    呼び出し側が best-effort で消す。
    """

    course_ids: list[str]
    teaching_figure_keys: list[str]
    figure_image_keys: list[str]


def _purge_document(session, document_id: str) -> PurgedDocument:
    """1教材（document）とその巻き添え範囲を物理削除する（削除経路の正本）。

    ``purge_object``（V層スイーパ）と ``routes/admin.py::delete_material``（教員の即時削除）の
    **両方がこの関数に委譲する**（設計書 §8.1）。HTTP 層に残るのは所有者確認・確認名照合・
    監査・V層 teardown・MinIO の best-effort 削除だけで、DB の削除本体はここ1本にする。

    migration 080 以降、下記の明示 DELETE のうち document_id に FK CASCADE が付いた表
    （theory_claims / theory_components / theory_component_links / theory_component_graphs /
    document_analysis_runs / document_figures / epistemic_ledger / counterfactual_sessions /
    element_annotations / deliberation_sessions / element_identity_links 等）は
    ``DELETE FROM documents`` でも消える。**それでも明示 DELETE を残す**のは、
    ①削除の順序（challenges / epistemic_ledger の target_id 掃除は theory_* を消す前に
    対象 id を集める必要がある）②何がこの教材と一緒に消えるのかがコードだけで読めること
    を優先するため。
    """
    doc = session.execute(
        sa_text("SELECT source_path, uploaded_by::text FROM documents WHERE id = CAST(:id AS uuid)"),
        {"id": document_id},
    ).fetchone()
    source_path = (doc[0] if doc else "") or ""
    owner = doc[1] if doc else None
    doc_ref = {"a": document_id}

    # 1) この教材を source に含む所有者のコースを削除（delete_material と同スコープ）
    teaching_figure_keys: list[str] = []
    course_ids: list[str] = []
    if owner and source_path:
        needle = json.dumps([{"material_id": source_path}])
        course_rows = session.execute(
            sa_text("""
                SELECT id FROM learning_courses
                WHERE user_id = CAST(:owner AS uuid)
                  AND data->'sources' @> CAST(:needle AS jsonb)
            """),
            {"owner": owner, "needle": needle},
        ).fetchall()
        for (cid,) in course_rows:
            course_id = str(cid)
            session.execute(sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"), {"cid": course_id})
            # object_group_permissions は course_id への FK が無いため明示削除する
            # （_purge_course と同じ理由。ここは _purge_course を経由しない独立した
            # コース削除経路なので、同じ後始末をここでも行う必要がある）。
            session.execute(
                sa_text(
                    "DELETE FROM object_group_permissions "
                    "WHERE object_type = 'course' AND object_id = :cid"
                ),
                {"cid": course_id},
            )
            # 教材図（migration 063）も course_id への FK が無いため明示削除する
            # （_purge_course と同じ理由。ここは _purge_course を経由しない独立した
            # コース削除経路なので、同じ後始末をここでも行う必要がある）。
            teaching_figure_keys.extend(_purge_teaching_figures(session, course_id))
            session.execute(sa_text("DELETE FROM learning_courses WHERE id = :cid"), {"cid": course_id})
            course_ids.append(course_id)

    # 2) document スコープの成果物。
    #    D層（migration 029-033）の challenges / epistemic_ledger は claim / component の id を
    #    target_id で参照するため、theory_* を消す前に対象 id を集める（FK が無く CASCADE されない）。
    target_rows = session.execute(
        sa_text("""
            SELECT id::text FROM theory_claims WHERE document_id = CAST(:a AS uuid)
            UNION
            SELECT id::text FROM theory_components WHERE document_id = CAST(:a AS uuid)
        """),
        doc_ref,
    ).fetchall()
    target_ids = [r[0] for r in target_rows]

    # PDF 抽出図（migration 041）の MinIO キーは行を消す前に集める（削除後は引けない）。
    # 教材図（063）と同じく core は storage を触らず、キーだけ呼び出し側へ返す。
    figure_image_keys = [
        str(r[0])
        for r in session.execute(
            sa_text("SELECT minio_key FROM document_figures WHERE document_id = CAST(:a AS uuid)"),
            doc_ref,
        ).fetchall()
        if r[0]
    ]

    for tbl in ("theory_claims", "theory_component_links", "theory_components", "theory_component_graphs"):
        session.execute(sa_text(f"DELETE FROM {tbl} WHERE document_id = CAST(:a AS uuid)"), doc_ref)

    # D層 polymorphic 行（削除済み document / claim / component を指す孤児を掃除する）。
    # verification_proposals は challenges に ON DELETE CASCADE なので challenges 削除で自動的に消える。
    session.execute(sa_text("DELETE FROM epistemic_ledger WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    session.execute(sa_text("DELETE FROM counterfactual_sessions WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    # W層 同一性リンク（migration 048）: instance 側。shared_part_id 側は library_entries への
    # 実 FK があるが、library_entries 自体は document 削除で消えないため触らない。
    session.execute(
        sa_text("DELETE FROM element_identity_links WHERE instance_document_id = CAST(:a AS uuid)"),
        doc_ref,
    )
    # W層 対話セッション + 候補注釈（migration 049）: scope='document' 行のみ。
    # scope='domain' 行は document_id が NULL のため、この WHERE には一致せず触らない
    # （L層 library_entry のライフサイクルに従う・P4）。
    session.execute(sa_text("DELETE FROM element_annotations WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    session.execute(sa_text("DELETE FROM deliberation_sessions WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    # 図（migration 041）: MinIO キーは上で集めてある。
    session.execute(sa_text("DELETE FROM document_figures WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    # Track A（hierarchical_context_explanation_design.md §5.2）の二層説明台帳。
    session.execute(sa_text("DELETE FROM element_explanations WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    if target_ids:
        session.execute(sa_text("DELETE FROM challenges WHERE target_id = ANY(:ids)"), {"ids": target_ids})
        session.execute(sa_text("DELETE FROM epistemic_ledger WHERE target_id = ANY(:ids)"), {"ids": target_ids})

    # object_group_permissions は document_id への FK が無いポリモーフィックテーブル
    # なので明示削除する（統合前の専用テーブル=migration 035 は documents への FK CASCADE で
    # 自動的に消えていた）。object_id は書き込み時と同じ正規化（小文字 canonical text）で比較する
    # （**この ::text は残す** — object_id は course / document を混在させる TEXT 列で、
    # migration 080 の UUID 化の対象ではない）。
    session.execute(
        sa_text(
            "DELETE FROM object_group_permissions "
            "WHERE object_type = 'document' AND object_id = CAST(:a AS uuid)::text"
        ),
        doc_ref,
    )
    # 3) チャンク → ドキュメント → 解析 Run。
    #    documents.active_analysis_run_id → document_analysis_runs(id) は NO ACTION なので、
    #    runs を先に消すと documents 行が残ったまま参照が切れて失敗する。documents を先に
    #    消せば（migration 080 の CASCADE で runs も一緒に消え）参照チェックは文末に通る。
    session.execute(sa_text("DELETE FROM chunks WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    session.execute(sa_text("DELETE FROM documents WHERE id = CAST(:a AS uuid)"), doc_ref)
    # 080 以前の DB（FK 無し）で取り残さないための明示削除。080 以降は上の CASCADE で
    # 既に消えているため 0 行になる。
    session.execute(sa_text("DELETE FROM document_analysis_runs WHERE document_id = CAST(:a AS uuid)"), doc_ref)
    return PurgedDocument(course_ids, teaching_figure_keys, figure_image_keys)


def _cleanup_version_tables(session, object_type: str, object_id: str) -> None:
    """版テーブルを FK 順（notifications/subscriptions → versions）に明示削除し、
    state を 'purged' 墓標として残す（同一 id の再発行で古いピンが復活しないように）。

    通知は migration 045 で user_notifications に統合済み。source='shared' を必ず
    付けて削除する（付け忘れると status 層（状態管理・通知基盤）の通知履歴まで
    削除してしまう。status 層由来の通知は教材削除でも残す現行の非対称を維持する）。
    """
    session.execute(
        sa_text("""
            INSERT INTO shared_version_state (object_type, object_id, lifecycle, active_release_id, updated_at)
            VALUES (:ot, :oid, 'purged', NULL, now())
            ON CONFLICT (object_type, object_id)
            DO UPDATE SET lifecycle = 'purged', active_release_id = NULL, updated_at = now()
        """),
        {"ot": object_type, "oid": object_id},
    )
    session.execute(
        sa_text(
            "DELETE FROM user_notifications "
            "WHERE source = 'shared' AND entity_type = :ot AND entity_id = :oid"
        ),
        {"ot": object_type, "oid": object_id},
    )
    session.execute(
        sa_text("DELETE FROM shared_version_subscriptions WHERE object_type = :ot AND object_id = :oid"),
        {"ot": object_type, "oid": object_id},
    )
    session.execute(
        sa_text("DELETE FROM shared_versions WHERE object_type = :ot AND object_id = :oid"),
        {"ot": object_type, "oid": object_id},
    )


def purge_object(*, object_type: str, object_id: str) -> dict:
    """全ユーザーから物理削除する（冪等）。purged 済みなら no-op。

    版テーブル（notifications/subscriptions/versions）は FK 順に明示削除し、
    state は 'purged' 墓標として残す（同一 id の再発行で古いピンが復活しないように）。
    """
    session = get_session()
    try:
        state = session.execute(
            sa_text("""
                SELECT lifecycle FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        if state and state[0] == schema.LIFECYCLE_PURGED:
            session.rollback()
            return {"purged": False, "already_purged": True}

        if object_type == schema.OBJECT_TYPE_COURSE:
            figure_keys = _purge_course(session, object_id)
        else:
            purged = _purge_document(session, object_id)
            figure_keys = list(purged.teaching_figure_keys) + list(purged.figure_image_keys)

        _cleanup_version_tables(session, object_type, object_id)
        session.commit()
        # MinIO オブジェクトは commit 後に best-effort で削除する（DB 削除が確定して
        # から消す — ロールバック時に画像だけ消える事故を防ぐ）。
        _remove_figure_objects(figure_keys)
        return {"purged": True}
    except Exception:
        session.rollback()
        logger.exception("purge_object failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()


def teardown_versioning(
    object_type: str,
    object_id: str,
    *,
    extra_recipients: list[str] | None = None,
    actor: str | None = None,
) -> dict:
    """既存の即時削除エンドポイント（delete_material / delete_course）用の V層あと片付け。

    物理行は呼び出し側で既に削除済みの前提で、版テーブルを掃除して state を 'purged' 墓標にし、
    購読者（+ 事前収集した宛先）へ 'deleted' 通知を配信する（best-effort・冪等）。
    共有版を一度も使っていない（state 行が無い）オブジェクトでは何もしない。
    これにより手動削除でも幽霊ピン・陳腐化通知・active のままの state を残さない。
    """
    from . import audit, notifications, subscriptions

    recipients = list(extra_recipients or [])
    try:
        recipients += subscriptions.subscriber_ids(object_type, object_id)
    except Exception:  # noqa: BLE001 — 宛先収集の失敗は片付けを止めない
        logger.debug("teardown: subscriber collection skipped for %s %s", object_type, object_id, exc_info=True)

    session = get_session()
    try:
        state = session.execute(
            sa_text("""
                SELECT lifecycle FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        if not state:
            session.rollback()
            return {"purged": False, "no_versioning": True}
        if state[0] == schema.LIFECYCLE_PURGED:
            session.rollback()
            return {"purged": False, "already_purged": True}
        _cleanup_version_tables(session, object_type, object_id)
        session.commit()
    except Exception:  # noqa: BLE001 — 片付けの失敗は削除本体を無効化しない
        session.rollback()
        logger.exception("teardown_versioning failed: %s %s", object_type, object_id)
        return {"purged": False, "error": True}
    finally:
        session.close()

    if recipients:
        notifications.notify_users(
            recipients, object_type, object_id, schema.NOTIF_DELETED,
            payload={"reason": "deleted_by_owner"},
        )
    audit.record_event(
        schema.AUDIT_DELETION, object_id, schema.LIFECYCLE_ACTIVE, schema.LIFECYCLE_PURGED,
        actor, {"object_type": object_type, "trigger": "manual_delete"},
    )
    return {"purged": True}
