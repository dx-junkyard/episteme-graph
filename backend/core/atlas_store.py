"""分野の地図 — 骨格 (S層) の DB ストア (migration 027)。

骨格 draft / 凍結版をカートリッジ同梱ファイルから DB (`atlas_skeletons`) へ移す。
設計は docs/features/field_atlas_db_managed_skeleton.md を正本とする。

- **DB が正本、ファイルはシード**: 読み取りは DB の凍結版を優先し、無ければ
  カートリッジ同梱の `atlas/skeleton.yaml` へフォールバックする。同梱骨格は
  起動時に `import_bundled_skeletons()` で一度だけ DB へ取り込む (冪等)。
- **domain_key = 現行 cartridge_id と同一の名前空間**。ただしカートリッジ
  ファイルが無い domain でも DB 骨格があれば地図は成立する (新分野に
  ファイルデプロイ不要)。
- **楽観ロック**: draft は domain につき1行。保存時に `revision` を照合し、
  ズレていれば `DraftRevisionConflict` (API 層は 409 を返しリロードさせる)。
- **凍結版は履歴保持**: (domain_key, version) で複数版を持ち、現行版は
  created_at 降順の先頭。凍結の実体変換 (version 付与・changelog) は
  `core/atlas.py:freeze_skeleton()` を使い、本モジュールは永続化のみ担う。

セッションは呼び出し側が管理する (`core/postgres.get_session()` + try/finally)。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core import atlas as atlas_module

logger = logging.getLogger(__name__)


class DraftRevisionConflict(Exception):
    """楽観ロックの衝突。current_revision に最新値を持つ (API 層で 409)。"""

    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


# ---------------------------------------------------------------------------
# シリアライズ (content JSONB ⇄ AtlasSkeleton)
# ---------------------------------------------------------------------------


def _content_to_skeleton(content: Any) -> atlas_module.AtlasSkeleton:
    if isinstance(content, str):
        content = json.loads(content)
    return atlas_module.parse_skeleton(content)


def _skeleton_to_content(skeleton: atlas_module.AtlasSkeleton) -> str:
    return json.dumps(atlas_module.skeleton_to_dict(skeleton), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------


def load_frozen_skeleton(session, domain_key: str) -> atlas_module.AtlasSkeleton | None:
    """DB の現行凍結版 (created_at 降順の先頭)。無ければ None。"""
    if session is None or not domain_key:
        return None
    row = session.execute(
        sa_text(
            """
            SELECT content FROM atlas_skeletons
             WHERE domain_key = :domain_key AND status = 'frozen'
             ORDER BY created_at DESC, version DESC
             LIMIT 1
            """
        ),
        {"domain_key": domain_key},
    ).fetchone()
    if not row or row[0] is None:
        return None
    try:
        return _content_to_skeleton(row[0])
    except Exception:  # noqa: BLE001
        logger.error("invalid atlas skeleton content in DB for %s", domain_key, exc_info=True)
        return None


def _bundled_skeleton(domain_key: str) -> atlas_module.AtlasSkeleton | None:
    """カートリッジ同梱の凍結骨格 (フォールバック / シード)。"""
    try:
        from core import cartridges as cartridges_module

        return cartridges_module.load_cartridge(domain_key).learner_atlas_skeleton
    except FileNotFoundError:
        return None
    except ValueError:
        logger.error("invalid bundled atlas skeleton for %s", domain_key, exc_info=True)
        return None


def load_learner_skeleton(
    domain_key: str, session=None
) -> atlas_module.AtlasSkeleton | None:
    """学習者向けの骨格 (凍結版のみ)。DB 優先・同梱ファイルへフォールバック。

    session を渡さない場合は自前で開閉する。骨格が無い場合は None
    (API 層は 404 = 地図機能を出さない、に写像する)。
    """
    if not domain_key:
        return None
    own_session = session is None
    if own_session:
        try:
            from core.postgres import get_session

            session = get_session()
        except Exception:  # noqa: BLE001
            logger.warning("atlas store session unavailable; falling back to bundled", exc_info=True)
            return _bundled_skeleton(domain_key)
    try:
        skeleton = load_frozen_skeleton(session, domain_key)
    except Exception:  # noqa: BLE001
        logger.warning("atlas frozen skeleton query failed for %s", domain_key, exc_info=True)
        skeleton = None
    finally:
        if own_session:
            session.close()
    return skeleton or _bundled_skeleton(domain_key)


def load_draft(session, domain_key: str) -> dict | None:
    """draft 行 (skeleton + revision)。無ければ None。"""
    row = session.execute(
        sa_text(
            """
            SELECT content, revision, generated_by, updated_at
              FROM atlas_skeletons
             WHERE domain_key = :domain_key AND status = 'draft'
             LIMIT 1
            """
        ),
        {"domain_key": domain_key},
    ).fetchone()
    if not row:
        return None
    try:
        skeleton = _content_to_skeleton(row[0])
    except Exception:  # noqa: BLE001
        logger.error("invalid atlas draft content in DB for %s", domain_key, exc_info=True)
        return None
    return {
        "skeleton": skeleton,
        "revision": int(row[1] or 1),
        "generated_by": str(row[2] or ""),
        "updated_at": str(row[3] or ""),
    }


def list_domains(session) -> list[dict]:
    """骨格を持つ domain の一覧 (DB + 同梱カートリッジの合成)。

    返り値: {domain_key, frozen_version, has_draft, draft_revision, source}
    source は 'db' / 'bundled' (DB に凍結版がある domain は 'db')。
    """
    domains: dict[str, dict] = {}
    if session is not None:
        try:
            rows = session.execute(
                sa_text(
                    """
                    SELECT domain_key, status, version, revision,
                           row_number() OVER (
                               PARTITION BY domain_key, status
                               ORDER BY created_at DESC, version DESC
                           ) AS rn
                      FROM atlas_skeletons
                    """
                )
            ).fetchall()
        except Exception:  # noqa: BLE001
            logger.warning("atlas domain list query failed", exc_info=True)
            rows = []
        for domain_key, status, version, revision, rn in rows:
            if int(rn) != 1:
                continue
            entry = domains.setdefault(
                str(domain_key),
                {
                    "domain_key": str(domain_key),
                    "frozen_version": "",
                    "has_draft": False,
                    "draft_revision": None,
                    "source": "db",
                },
            )
            if status == "frozen":
                entry["frozen_version"] = str(version or "")
            elif status == "draft":
                entry["has_draft"] = True
                entry["draft_revision"] = int(revision or 1)

    # 同梱カートリッジ (未取込環境のフォールバック表示)
    try:
        from core import cartridges as cartridges_module

        for summary in cartridges_module.list_cartridges():
            key = summary.cartridge_id
            if key in domains:
                continue
            skeleton = _bundled_skeleton(key)
            if skeleton is None:
                continue
            domains[key] = {
                "domain_key": key,
                "frozen_version": skeleton.version,
                "has_draft": False,
                "draft_revision": None,
                "source": "bundled",
            }
    except Exception:  # noqa: BLE001
        logger.warning("bundled cartridge listing failed", exc_info=True)

    return sorted(domains.values(), key=lambda d: d["domain_key"])


# ---------------------------------------------------------------------------
# 書き込み (教員フロー)
# ---------------------------------------------------------------------------


def save_draft(
    session,
    domain_key: str,
    skeleton: atlas_module.AtlasSkeleton,
    *,
    expected_revision: int | None,
    user_id: str | None = None,
    generated_by: str = "",
) -> int:
    """draft を保存する。

    - expected_revision=None: 新規作成 (既に draft があれば衝突)
    - expected_revision=N: revision=N の行を更新 (ズレていれば衝突)
    返り値は保存後の revision。
    """
    content = _skeleton_to_content(skeleton)
    if expected_revision is None:
        existing = session.execute(
            sa_text(
                "SELECT revision FROM atlas_skeletons "
                "WHERE domain_key = :domain_key AND status = 'draft' LIMIT 1"
            ),
            {"domain_key": domain_key},
        ).fetchone()
        if existing:
            raise DraftRevisionConflict(
                "draft が既に存在します。revision を指定して更新してください",
                current_revision=int(existing[0] or 1),
            )
        session.execute(
            sa_text(
                """
                INSERT INTO atlas_skeletons
                    (domain_key, status, version, content, revision,
                     generated_by, created_by, updated_by)
                VALUES
                    (:domain_key, 'draft', '', CAST(:content AS jsonb), 1,
                     :generated_by, CAST(:user_id AS uuid), CAST(:user_id AS uuid))
                """
            ),
            {
                "domain_key": domain_key,
                "content": content,
                "generated_by": generated_by,
                "user_id": user_id or None,
            },
        )
        return 1

    result = session.execute(
        sa_text(
            """
            UPDATE atlas_skeletons
               SET content = CAST(:content AS jsonb),
                   revision = revision + 1,
                   generated_by = CASE WHEN :generated_by <> '' THEN :generated_by
                                       ELSE generated_by END,
                   updated_by = CAST(:user_id AS uuid),
                   updated_at = now()
             WHERE domain_key = :domain_key AND status = 'draft'
               AND revision = :expected_revision
            """
        ),
        {
            "domain_key": domain_key,
            "content": content,
            "generated_by": generated_by,
            "user_id": user_id or None,
            "expected_revision": int(expected_revision),
        },
    )
    if getattr(result, "rowcount", 0) != 1:
        current = session.execute(
            sa_text(
                "SELECT revision FROM atlas_skeletons "
                "WHERE domain_key = :domain_key AND status = 'draft' LIMIT 1"
            ),
            {"domain_key": domain_key},
        ).fetchone()
        raise DraftRevisionConflict(
            "draft が他の編集で更新されています。最新を読み込み直してください",
            current_revision=int(current[0]) if current else None,
        )
    return int(expected_revision) + 1


def delete_draft(session, domain_key: str) -> None:
    session.execute(
        sa_text(
            "DELETE FROM atlas_skeletons "
            "WHERE domain_key = :domain_key AND status = 'draft'"
        ),
        {"domain_key": domain_key},
    )


def insert_frozen(
    session,
    domain_key: str,
    skeleton: atlas_module.AtlasSkeleton,
    *,
    user_id: str | None = None,
    generated_by: str = "",
) -> None:
    """凍結版を1行追加する (版の実体変換は atlas.freeze_skeleton 済みであること)。"""
    if skeleton.status != atlas_module.STATUS_FROZEN or not skeleton.version:
        raise ValueError("frozen かつ version 付きの骨格のみ保存できます")
    session.execute(
        sa_text(
            """
            INSERT INTO atlas_skeletons
                (domain_key, status, version, content, revision,
                 generated_by, created_by, updated_by)
            VALUES
                (:domain_key, 'frozen', :version, CAST(:content AS jsonb), 1,
                 :generated_by, CAST(:user_id AS uuid), CAST(:user_id AS uuid))
            """
        ),
        {
            "domain_key": domain_key,
            "version": skeleton.version,
            "content": _skeleton_to_content(skeleton),
            "generated_by": generated_by or skeleton.generated_by,
            "user_id": user_id or None,
        },
    )


# ---------------------------------------------------------------------------
# 同梱骨格の取り込み (起動時・冪等)
# ---------------------------------------------------------------------------


def import_bundled_skeletons(session) -> int:
    """カートリッジ同梱の凍結骨格を DB へ取り込む (無い版のみ・冪等)。

    以後 DB が正本。同梱ファイルは新規環境のシードとして残す。
    """
    if session is None:
        return 0
    try:
        from core import cartridges as cartridges_module

        summaries = cartridges_module.list_cartridges()
    except Exception:  # noqa: BLE001
        logger.warning("bundled cartridge listing failed for import", exc_info=True)
        return 0

    imported = 0
    for summary in summaries:
        domain_key = summary.cartridge_id
        skeleton = _bundled_skeleton(domain_key)
        if skeleton is None or not skeleton.version:
            continue
        exists = session.execute(
            sa_text(
                "SELECT 1 FROM atlas_skeletons "
                "WHERE domain_key = :domain_key AND status = 'frozen' "
                "AND version = :version LIMIT 1"
            ),
            {"domain_key": domain_key, "version": skeleton.version},
        ).fetchone()
        if exists:
            continue
        insert_frozen(
            session, domain_key, skeleton, generated_by="bundled_import"
        )
        imported += 1
        logger.info(
            "bundled atlas skeleton imported: %s %s", domain_key, skeleton.version
        )
    return imported
