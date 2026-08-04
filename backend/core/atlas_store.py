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
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text as sa_text

from core import atlas as atlas_module
from core import revision_store

logger = logging.getLogger(__name__)

# 骨格専用バンドルドメイン (knowledge_landscape_design.md §6.1)。
# カートリッジ一式 (ontology.json 等) を持たない「骨格だけのドメイン」を
# `backend/atlas_domains/<domain_key>/skeleton.yaml` (+ 任意 `domain.json`) で同梱する
# ためのディレクトリ。既存のカートリッジ同梱経路 (cartridges/<id>/atlas/skeleton.yaml) は
# 不変で、こちらは見つからなかった場合のフォールバック経路として追加する。
# core/atlas_store.py → backend/
ATLAS_BUNDLED_DOMAINS_DIR = Path(__file__).resolve().parent.parent / "atlas_domains"
_BUNDLED_DOMAIN_SKELETON_FILENAME = "skeleton.yaml"
_BUNDLED_DOMAIN_META_FILENAME = "domain.json"


class DraftRevisionConflict(revision_store.RevisionConflictError):
    """楽観ロックの衝突。current_revision に最新値を持つ (API 層で 409)。"""


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


def _cartridge_bundled_skeleton(domain_key: str) -> atlas_module.AtlasSkeleton | None:
    """カートリッジ同梱の凍結骨格 (`cartridges/<id>/atlas/skeleton.yaml`)。"""
    try:
        from core import cartridges as cartridges_module

        return cartridges_module.load_cartridge(domain_key).learner_atlas_skeleton
    except FileNotFoundError:
        return None
    except ValueError:
        logger.error("invalid bundled atlas skeleton for %s", domain_key, exc_info=True)
        return None


def _domain_dir_skeleton(domain_key: str) -> atlas_module.AtlasSkeleton | None:
    """骨格専用バンドルドメインの凍結骨格 (`atlas_domains/<key>/skeleton.yaml`)。

    ファイルが無ければ None (カートリッジ経路と同じ扱い)。YAML/スキーマが壊れている
    場合も None + error ログに留める (fail-soft。既存の `_cartridge_bundled_skeleton` と
    同じ流儀で、シード不能でも起動を止めない)。
    """
    if not domain_key:
        return None
    path = ATLAS_BUNDLED_DOMAINS_DIR / domain_key / _BUNDLED_DOMAIN_SKELETON_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.error("cannot read bundled domain skeleton for %s", domain_key, exc_info=True)
        return None
    try:
        skeleton = atlas_module.parse_skeleton(yaml.safe_load(raw))
    except Exception:  # noqa: BLE001
        logger.error(
            "invalid bundled domain atlas skeleton for %s", domain_key, exc_info=True
        )
        return None
    # 同梱ドメインのファイルは「凍結版のシード」であることが前提 (§6.1)。draft や
    # レビュー帰属の無い骨格をここから読ませない (LS6: 読む骨格は凍結版のみ)。
    if not skeleton.is_learner_visible:
        logger.warning(
            "bundled domain skeleton for %s is not a reviewed frozen version; ignored",
            domain_key,
        )
        return None
    return skeleton


def _bundled_domain_keys() -> list[str]:
    """`atlas_domains/` 配下の骨格を持つ domain_key 一覧 (ソート済み)。"""
    try:
        entries = sorted(p for p in ATLAS_BUNDLED_DOMAINS_DIR.iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []
    return [
        p.name
        for p in entries
        if (p / _BUNDLED_DOMAIN_SKELETON_FILENAME).is_file()
    ]


def _bundled_domain_meta(domain_key: str) -> dict | None:
    """`atlas_domains/<key>/domain.json` (任意)。無い・壊れていれば None。"""
    path = ATLAS_BUNDLED_DOMAINS_DIR / domain_key / _BUNDLED_DOMAIN_META_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.error("invalid bundled domain meta for %s", domain_key, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    return {
        "name": str(data.get("name") or domain_key),
        "description": str(data.get("description") or ""),
    }


def _bundled_skeleton(domain_key: str) -> atlas_module.AtlasSkeleton | None:
    """同梱の凍結骨格 (フォールバック / シード)。

    ① カートリッジ同梱 (`cartridges/<id>/atlas/skeleton.yaml`) を優先し、
    ② 見つからなければ骨格専用バンドルドメイン (`atlas_domains/<key>/skeleton.yaml`)
    を読む (knowledge_landscape_design.md §6.1)。
    """
    skeleton = _cartridge_bundled_skeleton(domain_key)
    if skeleton is not None:
        return skeleton
    return _domain_dir_skeleton(domain_key)


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

    返り値: {domain_key, frozen_version, has_draft, draft_revision, source, lifecycle}
    source は 'db' / 'bundled' (DB に凍結版がある domain は 'db')。
    lifecycle は 'active' / 'retired' (migration 057。meta 行が無ければ 'active')。
    """
    domains: dict[str, dict] = {}
    # domain_key -> (name, lifecycle)。atlas_skeletons 行の有無に関わらず適用するため
    # (同梱カートリッジのみで DB 骨格行が無い domain も retire しうる)、DB 行の走査より
    # 先に読み切っておく。
    meta_by_key: dict[str, tuple[str, str]] = {}
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
                    "domain_name": "",
                    "frozen_version": "",
                    "has_draft": False,
                    "draft_revision": None,
                    "source": "db",
                    "lifecycle": "active",
                },
            )
            if status == "frozen":
                entry["frozen_version"] = str(version or "")
            elif status == "draft":
                entry["has_draft"] = True
                entry["draft_revision"] = int(revision or 1)

        try:
            meta_rows = session.execute(
                sa_text("SELECT domain_key, name, lifecycle FROM atlas_domain_meta")
            ).fetchall()
            for domain_key, name, lifecycle in meta_rows:
                key = str(domain_key)
                meta_by_key[key] = (str(name or ""), str(lifecycle or "active"))
                if key in domains:
                    if name:
                        domains[key]["domain_name"] = str(name)
                    domains[key]["lifecycle"] = str(lifecycle or "active")
        except Exception:  # noqa: BLE001
            logger.warning("atlas domain_meta listing failed", exc_info=True)

    def _add_bundled(key: str) -> None:
        if not key or key in domains:
            return
        skeleton = _bundled_skeleton(key)
        if skeleton is None:
            return
        name, lifecycle = meta_by_key.get(key, ("", "active"))
        domains[key] = {
            "domain_key": key,
            "domain_name": name,
            "frozen_version": skeleton.version,
            "has_draft": False,
            "draft_revision": None,
            "source": "bundled",
            "lifecycle": lifecycle,
        }

    # 同梱カートリッジ (未取込環境のフォールバック表示)
    try:
        from core import cartridges as cartridges_module

        for summary in cartridges_module.list_cartridges():
            _add_bundled(summary.cartridge_id)
    except Exception:  # noqa: BLE001
        logger.warning("bundled cartridge listing failed", exc_info=True)

    # 骨格専用バンドルドメイン (atlas_domains/<key>/skeleton.yaml。§6.1)
    try:
        for key in _bundled_domain_keys():
            _add_bundled(key)
    except Exception:  # noqa: BLE001
        logger.warning("bundled domain listing failed", exc_info=True)

    return sorted(domains.values(), key=lambda d: d["domain_key"])


# ---------------------------------------------------------------------------
# ドメインライフサイクル (migration 057, §3.1)
# ---------------------------------------------------------------------------


def domain_lifecycle(session, domain_key: str) -> str:
    """domain の lifecycle ('active' | 'retired') を返す。

    `atlas_domain_meta` に行が無い、または問い合わせ自体に失敗した場合は
    'active' とみなす (fail-open: retired は明示 `retire_domain()` 呼び出しの
    結果のみが持つ状態であり、未知の状態を retired 扱いにしてはならない)。
    """
    if session is None or not domain_key:
        return "active"
    try:
        row = session.execute(
            sa_text(
                "SELECT lifecycle FROM atlas_domain_meta WHERE domain_key = :domain_key"
            ),
            {"domain_key": domain_key},
        ).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("atlas domain lifecycle query failed for %s", domain_key, exc_info=True)
        return "active"
    if not row or not row[0]:
        return "active"
    return str(row[0])


def retired_domain_keys(session) -> set[str]:
    """lifecycle='retired' の domain_key 集合を1クエリで返す (propose の照合フィルタ用)。"""
    if session is None:
        return set()
    try:
        rows = session.execute(
            sa_text("SELECT domain_key FROM atlas_domain_meta WHERE lifecycle = 'retired'")
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("atlas retired domain listing failed", exc_info=True)
        return set()
    return {str(r[0]) for r in rows}


def retire_domain(
    session, domain_key: str, *, user_id: str | None = None, note: str = ""
) -> None:
    """domain を retired にする (meta 行が無ければ name=domain_key で新規作成)。

    コミットは呼び出し側の責務 (他の書き込み関数と同じ規約)。
    """
    session.execute(
        sa_text(
            """
            INSERT INTO atlas_domain_meta
                (domain_key, name, lifecycle, retired_at, retired_by, retire_note, created_by)
            VALUES
                (:domain_key, :domain_key, 'retired', now(), CAST(:user_id AS uuid),
                 :note, CAST(:user_id AS uuid))
            ON CONFLICT (domain_key) DO UPDATE SET
                lifecycle = 'retired',
                retired_at = now(),
                retired_by = CAST(:user_id AS uuid),
                retire_note = :note,
                updated_at = now()
            """
        ),
        {
            "domain_key": domain_key,
            "user_id": user_id or None,
            "note": note or "",
        },
    )


def restore_domain(session, domain_key: str, *, user_id: str | None = None) -> None:
    """domain を active に戻す (retired_at/retired_by をクリア)。

    `retire_note` は履歴として残す (上書きしない)。`user_id` は呼び出し側の監査記録
    (`theory_review_events`) との呼び出し規約を揃えるために受け取るが、
    v1 に "restored_by" 列は無いため本関数の SQL 自体では使用しない。
    コミットは呼び出し側の責務。
    """
    del user_id  # v1: restored_by 列なし。将来拡張時の呼び出し規約のため引数のみ保持。
    session.execute(
        sa_text(
            """
            UPDATE atlas_domain_meta
               SET lifecycle = 'active',
                   retired_at = NULL,
                   retired_by = NULL,
                   updated_at = now()
             WHERE domain_key = :domain_key
            """
        ),
        {"domain_key": domain_key},
    )


def load_domain_meta(session, domain_key: str) -> dict | None:
    """カートリッジファイルの無い新分野向けに永続化した domain_meta (migration 028)。

    generate API で body.domain が省略された場合のフォールバックに使う
    (フロントの画面内メモリ pendingDomainMeta はリロードで消えるため)。
    """
    if session is None or not domain_key:
        return None
    row = session.execute(
        sa_text(
            """
            SELECT name, description, target_domain, concept_vocabulary
              FROM atlas_domain_meta
             WHERE domain_key = :domain_key
            """
        ),
        {"domain_key": domain_key},
    ).fetchone()
    if not row:
        return None
    target_domain = row[2]
    if isinstance(target_domain, str):
        target_domain = json.loads(target_domain)
    return {
        "name": str(row[0] or ""),
        "description": str(row[1] or ""),
        "target_domain": list(target_domain or []),
        "concept_vocabulary": str(row[3] or ""),
    }


def save_domain_meta(session, domain_key: str, meta: dict, *, user_id: str | None = None) -> None:
    """domain_meta を upsert する (次回以降の generate で body.domain 省略を許す)。"""
    session.execute(
        sa_text(
            """
            INSERT INTO atlas_domain_meta
                (domain_key, name, description, target_domain, concept_vocabulary, created_by)
            VALUES
                (:domain_key, :name, :description, CAST(:target_domain AS JSONB), :concept_vocabulary, :created_by)
            ON CONFLICT (domain_key) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                target_domain = EXCLUDED.target_domain,
                concept_vocabulary = EXCLUDED.concept_vocabulary,
                updated_at = now()
            """
        ),
        {
            "domain_key": domain_key,
            "name": str(meta.get("name") or domain_key),
            "description": str(meta.get("description") or ""),
            "target_domain": json.dumps([str(d) for d in (meta.get("target_domain") or []) if d]),
            "concept_vocabulary": str(meta.get("concept_vocabulary") or ""),
            "created_by": user_id or None,
        },
    )


# ---------------------------------------------------------------------------
# 書き込み (教員フロー)
# ---------------------------------------------------------------------------


def lock_domain_for_write(session, domain_key: str) -> None:
    """domain 単位の書き込み直列化 (トランザクションスコープの advisory lock)。

    retire / restore と draft・凍結版の書き込みは check-then-write のため、
    素朴に並行すると「active 確認 → (別教員が retire) → retired ドメインへ書き込み」の
    競合が起きる (特に generate は LLM 生成を挟むため窓が大きい)。lifecycle の確認より
    前に本ロックを取得することで、同一 domain の状態遷移と書き込みを直列化する。
    ロックはトランザクション終了 (commit / rollback) で自動解放される。
    seed の 57 は本ライフサイクルを導入した migration 番号 (名前空間の衝突回避)。
    """
    session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 57))"),
        {"key": f"atlas_domain:{domain_key}"},
    )


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

    revision_store.update_with_revision_lock(
        session,
        update_sql="""
            UPDATE atlas_skeletons
               SET content = CAST(:content AS jsonb),
                   revision = revision + 1,
                   generated_by = CASE WHEN :generated_by <> '' THEN :generated_by
                                       ELSE generated_by END,
                   updated_by = CAST(:user_id AS uuid),
                   updated_at = now()
             WHERE domain_key = :domain_key AND status = 'draft'
               AND revision = :expected_revision
            """,
        update_params={
            "domain_key": domain_key,
            "content": content,
            "generated_by": generated_by,
            "user_id": user_id or None,
            "expected_revision": int(expected_revision),
        },
        select_sql=(
            "SELECT revision FROM atlas_skeletons "
            "WHERE domain_key = :domain_key AND status = 'draft' LIMIT 1"
        ),
        select_params={"domain_key": domain_key},
        conflict_exc=DraftRevisionConflict,
        conflict_message="draft が他の編集で更新されています。最新を読み込み直してください",
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


def _seed_bundled_domain_meta(session, domain_key: str) -> None:
    """`domain.json` の name/description を `atlas_domain_meta` へ **行が無いときだけ** 入れる。

    既存行は上書きしない (教員が管理画面で付けた表示名・説明を同梱ファイルで
    巻き戻さない。§6.1-3)。失敗しても骨格の取り込み自体は続行する (fail-soft)。
    """
    meta = _bundled_domain_meta(domain_key)
    if not meta:
        return
    try:
        if load_domain_meta(session, domain_key) is not None:
            return
        save_domain_meta(session, domain_key, meta)
    except Exception:  # noqa: BLE001
        logger.warning(
            "bundled domain meta seed failed for %s", domain_key, exc_info=True
        )


def import_bundled_skeletons(session) -> int:
    """同梱の凍結骨格を DB へ取り込む (無い版のみ・冪等)。

    対象は ① カートリッジ同梱 (`cartridges/<id>/atlas/skeleton.yaml`) と
    ② 骨格専用バンドルドメイン (`atlas_domains/<key>/skeleton.yaml`。§6.1)。
    以後 DB が正本。同梱ファイルは新規環境のシードとして残す。
    """
    if session is None:
        return 0
    try:
        from core import cartridges as cartridges_module

        summaries = cartridges_module.list_cartridges()
    except Exception:  # noqa: BLE001
        logger.warning("bundled cartridge listing failed for import", exc_info=True)
        summaries = []

    domain_keys: list[str] = [s.cartridge_id for s in summaries]
    try:
        for key in _bundled_domain_keys():
            if key not in domain_keys:
                domain_keys.append(key)
    except Exception:  # noqa: BLE001
        logger.warning("bundled domain listing failed for import", exc_info=True)

    candidates: list[tuple[str, atlas_module.AtlasSkeleton]] = []
    for domain_key in domain_keys:
        skeleton = _bundled_skeleton(domain_key)
        if skeleton is None or not skeleton.version:
            continue
        candidates.append((domain_key, skeleton))
        _seed_bundled_domain_meta(session, domain_key)

    def _exists(candidate: tuple[str, atlas_module.AtlasSkeleton]) -> bool:
        domain_key, skeleton = candidate
        row = session.execute(
            sa_text(
                "SELECT 1 FROM atlas_skeletons "
                "WHERE domain_key = :domain_key AND status = 'frozen' "
                "AND version = :version LIMIT 1"
            ),
            {"domain_key": domain_key, "version": skeleton.version},
        ).fetchone()
        return row is not None

    def _create(candidate: tuple[str, atlas_module.AtlasSkeleton]) -> None:
        domain_key, skeleton = candidate
        insert_frozen(session, domain_key, skeleton, generated_by="bundled_import")

    def _on_created(candidate: tuple[str, atlas_module.AtlasSkeleton]) -> None:
        domain_key, skeleton = candidate
        logger.info(
            "bundled atlas skeleton imported: %s %s", domain_key, skeleton.version
        )

    result = revision_store.idempotent_seed_import(
        candidates,
        exists_fn=_exists,
        create_fn=_create,
        # 同梱データは信頼できる前提で fail-fast する (library と異なり握って続行しない)。
        catch_create_errors=False,
        on_created=_on_created,
    )
    return result["imported"]
