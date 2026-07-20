"""横断インボックス fan-out（N14）— C/D/R/L 層の個別イベント通知。

`notification_rules.py`（教材・コースの状態遷移通知。watcher が発火した status_events を
6種にマップして fan-out する）や `core/versioning/notifications.py`（V層の共有先通知）は
いずれも「宛先を決めて `user_notifications` に INSERT する」という最終ステップを持つ。

本モジュールは第一弾4種（C層: 承認受領 / D層: 疑義の被起票 / R層: item の flagged 遷移 /
L層: エントリ retire）向けの、watcher を経由しない**直接** fan-out ヘルパーを提供する
（各イベントは同期リクエスト内で発生し、対象は常に1〜数名・低頻度・行動可能。G4「押し付けない」
原則により、レビューキュー滞留数のような集計系はここに乗せない — 個別イベントのみ）。

- **source は既存の 'status' を再利用する**（新規 source を追加しない）。理由:
  `user_notifications.source` は `CHECK (source IN ('status', 'shared'))`（migration 045）で
  制約されており、新しい値を使うには migration 追加が要る。加えて統合一覧・既読 API
  （`routes/notifications.py::list_notifications` / `read_notification` / `read_all_notifications`）
  は source 不問だが、却下（`dismiss_notification`）は `core.status.inbox.dismiss` を呼ぶため
  `source='status'` の行にしか効かない（N39 として記録済みの非対称）。この4種は「個別・低頻度・
  却下したくなる」性質の通知であり、新しい source 値を選ぶと却下だけができない通知が増えて
  N39 の非対称を拡大させてしまう。migration や `routes/notifications.py` の改修は本タスクの
  編集許可外のため、`source='status'` に相乗りすることで list/read/read-all/dismiss すべてを
  変更なしに使えるようにする。
- **kind は open-vocab**（migration 045 で `user_notifications.kind` の DB CHECK は撤廃済み）。
  ただし `core/status/schema.py::NOTIFICATION_KINDS` / `EVENT_TO_NOTIFICATION_KIND` には加えない
  ——  あちらは watcher（`status_events` 経由）専用の6種で、
  `assert set(EVENT_TO_NOTIFICATION_KIND.values()) == set(NOTIFICATION_KINDS)` という
  bijection 契約を持つ。本モジュールの4種は watcher を経由しないため、別語彙として管理する。
- fan-out は best-effort（例外を握りつぶし log のみ）。呼び出し側は本処理のコミット後に
  呼ぶこと（本処理の成否と通知の成否を独立させる。V層 `_stamp_citation_source_version` と
  同じ設計）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

# 名前 import（from core.postgres import get_session）にすると import 時点の関数参照が
# 束縛され、テストの monkeypatch.setattr(core.postgres, "get_session", ...) が効かない。
# モジュール属性経由で毎回解決する。
import core.postgres

logger = logging.getLogger(__name__)

# --- kind vocab（open-vocab。core/status/schema.py の NOTIFICATION_KINDS とは別語彙）-------

NOTIF_EXPLANATION_ENDORSED = "explanation_endorsed"                # C層: 承認受領
NOTIF_LEDGER_TARGET_CHALLENGED = "ledger_target_challenged"        # D層: 疑義の被起票
NOTIF_RECONSTRUCTION_ITEM_FLAGGED = "reconstruction_item_flagged"  # R層: item の flagged 遷移
NOTIF_LIBRARY_ENTRY_RETIRED = "library_entry_retired"              # L層: エントリ retire
NOTIF_ATLAS_SKELETON_FROZEN = "atlas_skeleton_frozen"              # 分野の地図: 骨格の凍結
NOTIF_ATLAS_DOMAIN_RETIRED = "atlas_domain_retired"                # 分野の地図: ドメインの retire

CROSS_LAYER_NOTIFICATION_KINDS = (
    NOTIF_EXPLANATION_ENDORSED,
    NOTIF_LEDGER_TARGET_CHALLENGED,
    NOTIF_RECONSTRUCTION_ITEM_FLAGGED,
    NOTIF_LIBRARY_ENTRY_RETIRED,
    NOTIF_ATLAS_SKELETON_FROZEN,
    NOTIF_ATLAS_DOMAIN_RETIRED,
)


def notify_user(
    recipient_id: str | None,
    kind: str,
    entity_type: str,
    entity_id: str,
    payload: dict | None = None,
) -> bool:
    """1名へ通知を1件 INSERT する（best-effort・source='status'）。

    ``recipient_id`` が空、または ``kind`` が本モジュールの語彙外なら no-op で False を返す
    （呼び出し側で「自己操作は通知しない」を判定してから呼ぶこと。ここでは自己判定しない
    ——recipient_id 以外に actor を渡さない薄いヘルパーに留める）。
    """
    if not recipient_id:
        return False
    if kind not in CROSS_LAYER_NOTIFICATION_KINDS:
        logger.warning("cross_layer_notify: unknown kind %r (skipped)", kind)
        return False
    session = core.postgres.get_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO user_notifications
                    (recipient_id, kind, entity_type, entity_id, payload, source)
                VALUES (CAST(:uid AS uuid), :kind, :entity_type, :entity_id,
                        CAST(:payload AS jsonb), 'status')
            """),
            {
                "uid": recipient_id,
                "kind": kind,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": json.dumps(payload or {}, ensure_ascii=False),
            },
        )
        session.commit()
        return True
    except Exception:
        session.rollback()
        logger.exception(
            "cross_layer notify failed: kind=%s entity_type=%s entity_id=%s",
            kind, entity_type, entity_id,
        )
        return False
    finally:
        session.close()
