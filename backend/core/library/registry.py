"""概念レジストリ — ラベル / 関係 / 骨格リンク / エントリ確定の DB プリミティブ（Phase 3）。

設計正本: ``docs/features/concept_registry_design.md`` §5（不変条項 KR1〜KR10 は §2）。
DB は migration 082（``library_entry_labels`` / ``library_entry_relations`` /
``library_atlas_node_links`` + ``library_entries`` のガバナンス列）。

規約:

- **行を消さない**（KR7）。本モジュールに ``DELETE FROM`` は無く、今後も足さない。
  見送りは ``status='dismissed'`` への遷移で表し、``restore`` で候補へ戻せる。
- **確定は人間**（KR2 / KN-3）。遷移は :class:`core.candidate_flow.CandidateFlow` を通し、
  ``actor_id``（帰属）と見送り理由を構造的に必須にする。AI が確定状態へ遷移させる経路は無い。
- **リンクであってマージではない**（KR3）。関係は2行を並存させたままの記録で、
  ``name`` の書き換え・行の統合はしない。
- **mapping_justification 必須**（KR4）。関係・node リンクの作成は語彙内の値を必ず伴う
  （未指定・語彙外は ``ValueError`` → route が 422）。
- **数値を見せない**（KR6）。``confidence`` は列として持つが、本モジュールの戻り値にも
  載せない（DB 界面で止める）。
- 本モジュールは FastAPI も ``core.llm`` も import しない（core/ 共通ルール・KR5）。
  監査記帳は呼び出し側が ``record_audit`` callable として注入する。

セッションは各公開関数が自前で開閉する（``core/library/store.py`` と同じ流儀）。
呼び出し側が既にトランザクションを開いている場合は ``session=`` を渡すと、その中で
実行し commit / close しない（パイプラインの候補生成がバッチで使う）。
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator

from sqlalchemy import text as sa_text

from core.candidate_flow import CandidateFlow, CandidateVocabulary
from core.postgres import get_session
from core.schema import AUDIT_ENTITY_LIBRARY_ENTRY

from . import schema

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    """概念レジストリ操作の基底エラー。"""


class RegistryNotFoundError(RegistryError):
    """対象の行（entry / label / relation / node link）が存在しない。"""


# ---------------------------------------------------------------------------
# 候補→確定の共通制御（core/candidate_flow.py）に渡す語彙
# ---------------------------------------------------------------------------

#: 関係 / node リンク / エントリ確定で共通の3語彙（``superseded`` は持たない —
#: 本層の候補は再解析で置き換えず、``candidate_key`` / ``relation_key`` / ``link_key``
#: で同一行に畳む）。
VOCABULARY = CandidateVocabulary(
    candidate=schema.CANDIDATE_STATUS_CANDIDATE,
    accepted=schema.CANDIDATE_STATUS_CONFIRMED,
    dismissed=schema.CANDIDATE_STATUS_DISMISSED,
)

#: API の ``status`` 指定 → :class:`CandidateFlow` のアクション名。
_ACTION_BY_TARGET_STATUS = {
    schema.CANDIDATE_STATUS_CONFIRMED: "confirm",
    schema.CANDIDATE_STATUS_DISMISSED: "dismiss",
    schema.CANDIDATE_STATUS_CANDIDATE: "restore",
}


def action_for_status(status: str) -> str:
    """遷移先 status からアクション名を引く（語彙外は ``ValueError`` → route が 422）。"""
    action = _ACTION_BY_TARGET_STATUS.get(str(status or "").strip())
    if action is None:
        raise ValueError(
            f"invalid status: {status!r} "
            f"(must be one of {tuple(_ACTION_BY_TARGET_STATUS)!r})"
        )
    return action


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _uuid_or_none(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    try:
        uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        return None
    return text


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _as_list(value: Any) -> list:
    return schema.as_list(value)


@contextmanager
def _session_scope(session: Any = None) -> Iterator[Any]:
    """``session`` が渡されればそれをそのまま使い（commit / close しない）、
    渡されなければ自前で開いて commit / rollback / close する。"""
    if session is not None:
        yield session
        return
    own = get_session()
    try:
        yield own
        own.commit()
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def _require_actor(actor_id: Any) -> str:
    actor = _clean(actor_id)
    if not actor:
        # KN-3: 匿名の確定操作を作らない（帰属必須）。
        raise ValueError("actor_id is required")
    return actor


def _require_justification(value: Any) -> str:
    justification = _clean(value)
    if not justification:
        # KR4: 「なぜ同じと言えたか」が無い候補は作らせない。
        raise ValueError("mapping_justification is required")
    if not schema.is_valid_justification(justification):
        raise ValueError(f"invalid mapping_justification: {justification!r}")
    return justification


# ---------------------------------------------------------------------------
# 1. ラベル（library_entry_labels）
# ---------------------------------------------------------------------------

_LABEL_COLUMNS_SQL = """
    id::text, entry_id::text, kind, label, normalized_label, language, status,
    mapping_justification, evidence, review_note,
    created_by::text, decided_by::text, created_at, updated_at
"""


def _label_row_to_dict(row: Any) -> dict:
    return {
        "id": str(row[0]),
        "entry_id": str(row[1]),
        "kind": row[2] or "",
        "label": row[3] or "",
        "normalized_label": row[4] or "",
        "language": row[5] or "",
        "status": row[6] or "",
        "mapping_justification": row[7],
        "evidence": _as_list(row[8]),
        "review_note": row[9] or "",
        "created_by": row[10],
        "decided_by": row[11],
        "created_at": row[12].isoformat() if row[12] else "",
        "updated_at": row[13].isoformat() if row[13] else "",
    }


def list_labels(
    entry_id: str,
    *,
    include_dismissed: bool = False,
    session: Any = None,
) -> list[dict]:
    """1エントリのラベル行（``alternate`` / ``hidden``）を返す。

    ``preferred`` は ``library_entries.name`` が正本なので行として存在しない（§4.3）。
    既定では見送り済み（``dismissed``）を除く。
    """
    key = _uuid_or_none(entry_id)
    if key is None:
        return []
    conditions = ["entry_id = CAST(:entry_id AS uuid)"]
    params: dict[str, Any] = {"entry_id": key}
    if not include_dismissed:
        conditions.append("status = :confirmed")
        params["confirmed"] = schema.LABEL_STATUS_CONFIRMED
    where = " AND ".join(conditions)
    with _session_scope(session) as sess:
        rows = sess.execute(
            sa_text(
                f"SELECT {_LABEL_COLUMNS_SQL} FROM library_entry_labels "
                f"WHERE {where} ORDER BY kind, normalized_label"
            ),
            params,
        ).fetchall()
    return [_label_row_to_dict(row) for row in rows]


def add_label(
    entry_id: str,
    *,
    kind: str,
    label: str,
    mapping_justification: str = schema.JUSTIFICATION_MANUAL,
    language: str = "",
    evidence: Iterable | None = None,
    actor_id: str,
    record_audit: Callable[..., Any] | None = None,
    session: Any = None,
) -> dict:
    """別名（``alternate``）または隠しラベル（``hidden``）を1件足す。

    ``hidden`` は OCR ノイズ・旧表記を**捨てずに検索から隠す**ための器（SKOS
    hiddenLabel。KR7）。既存行があれば表示テキストだけ更新して返し、**見送り済みの行は
    復帰させない**（教員の判断を編集で黙って戻さない）。

    Raises:
        ValueError: ``kind`` が行にできる種別でない / ``label`` の正規化が空 /
            帰属・正当化の欠落。
        RegistryNotFoundError: ``entry_id`` が存在しない。
    """
    actor = _require_actor(actor_id)
    justification = _require_justification(mapping_justification)
    if kind not in schema.LABEL_ROW_KINDS:
        raise ValueError(
            f"invalid label kind: {kind!r} (must be one of {schema.LABEL_ROW_KINDS!r})"
        )
    text = _clean(label)
    normalized = schema.normalize_label(text)
    if not text or not normalized:
        raise ValueError("label is required")
    key = _uuid_or_none(entry_id)
    if key is None:
        raise RegistryNotFoundError(f"library entry not found: {entry_id}")

    with _session_scope(session) as sess:
        exists = sess.execute(
            sa_text("SELECT 1 FROM library_entries WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": key},
        ).fetchone()
        if exists is None:
            raise RegistryNotFoundError(f"library entry not found: {entry_id}")
        row = sess.execute(
            sa_text(
                f"""
                INSERT INTO library_entry_labels (
                    entry_id, kind, label, normalized_label, language,
                    mapping_justification, evidence, created_by
                ) VALUES (
                    CAST(:entry_id AS uuid), :kind, :label, :normalized_label, :language,
                    :justification, CAST(:evidence AS jsonb),
                    CAST(NULLIF(:actor_id, '') AS uuid)
                )
                ON CONFLICT (entry_id, kind, normalized_label) DO UPDATE
                   SET label = EXCLUDED.label,
                       language = EXCLUDED.language,
                       updated_at = now()
                 WHERE library_entry_labels.status = :confirmed
                RETURNING {_LABEL_COLUMNS_SQL}
                """
            ),
            {
                "entry_id": key,
                "kind": kind,
                "label": text,
                "normalized_label": normalized,
                "language": _clean(language),
                "justification": justification,
                "evidence": _dump_json(list(evidence or [])),
                "actor_id": _uuid_or_none(actor) or "",
                "confirmed": schema.LABEL_STATUS_CONFIRMED,
            },
        ).fetchone()
        if row is None:
            # 既存行が dismissed（復帰させない）。事実として既存行を返す。
            row = sess.execute(
                sa_text(
                    f"""
                    SELECT {_LABEL_COLUMNS_SQL} FROM library_entry_labels
                     WHERE entry_id = CAST(:entry_id AS uuid)
                       AND kind = :kind AND normalized_label = :normalized_label
                     LIMIT 1
                    """
                ),
                {"entry_id": key, "kind": kind, "normalized_label": normalized},
            ).fetchone()
        if row is None:
            raise RegistryError("failed to record the label")
        result = _label_row_to_dict(row)

    if record_audit is not None:
        record_audit(
            entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            entity_id=key,
            action=schema.AUDIT_ACTION_LABEL_ADD,
            old_status="",
            new_status=result["status"],
            actor_id=actor,
            reason="",
            metadata={"label_id": result["id"], "kind": kind},
        )
    return result


def dismiss_label(
    label_id: str,
    *,
    actor_id: str,
    review_note: str,
    record_audit: Callable[..., Any] | None = None,
    session: Any = None,
) -> dict | None:
    """ラベルを見送る（``status='dismissed'`` への遷移。**行は消さない** — KR7）。

    見送りは理由必須（空は ``ValueError`` → route が 422）。対象が無ければ ``None``
    （呼び出し側は 404）。
    """
    actor = _require_actor(actor_id)
    note = _clean(review_note)
    if not note:
        raise ValueError("review_note is required when dismissing a label")
    key = _uuid_or_none(label_id)
    if key is None:
        return None

    with _session_scope(session) as sess:
        row = sess.execute(
            sa_text(
                f"""
                UPDATE library_entry_labels
                   SET status = :dismissed,
                       review_note = :review_note,
                       decided_by = CAST(NULLIF(:actor_id, '') AS uuid),
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                RETURNING {_LABEL_COLUMNS_SQL}
                """
            ),
            {
                "id": key,
                "dismissed": schema.LABEL_STATUS_DISMISSED,
                "review_note": note,
                "actor_id": _uuid_or_none(actor) or "",
            },
        ).fetchone()
        if row is None:
            return None
        result = _label_row_to_dict(row)

    if record_audit is not None:
        record_audit(
            entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            entity_id=result["entry_id"],
            action=schema.AUDIT_ACTION_LABEL_DISMISS,
            old_status=schema.LABEL_STATUS_CONFIRMED,
            new_status=schema.LABEL_STATUS_DISMISSED,
            actor_id=actor,
            reason=note,
            metadata={"label_id": result["id"], "kind": result["kind"]},
        )
    return result


def labels_for_entries(
    entry_ids: Iterable[str],
    *,
    include_hidden: bool = True,
    session: Any = None,
) -> dict[str, list[dict]]:
    """複数エントリのラベルをまとめて引く（候補導出・別名検索の読み口）。

    空入力では **SQL を発行しない**（空集合を「全件」に転ばせない）。
    ``include_hidden=False`` は表示用（SKOS hiddenLabel は検索には使うが表示しない）。
    """
    keys = [k for k in (_uuid_or_none(e) for e in (entry_ids or [])) if k]
    if not keys:
        return {}
    kinds = list(schema.LABEL_ROW_KINDS) if include_hidden else [schema.LABEL_KIND_ALTERNATE]
    with _session_scope(session) as sess:
        rows = sess.execute(
            sa_text(
                f"""
                SELECT {_LABEL_COLUMNS_SQL} FROM library_entry_labels
                 WHERE entry_id = ANY(CAST(:entry_ids AS uuid[]))
                   AND status = :confirmed
                   AND kind = ANY(:kinds)
                 ORDER BY kind, normalized_label
                """
            ),
            {
                "entry_ids": keys,
                "confirmed": schema.LABEL_STATUS_CONFIRMED,
                "kinds": kinds,
            },
        ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        item = _label_row_to_dict(row)
        grouped.setdefault(item["entry_id"], []).append(item)
    return grouped


# ---------------------------------------------------------------------------
# 2. 関係（library_entry_relations）
# ---------------------------------------------------------------------------

_RELATION_COLUMNS_SQL = """
    id::text, relation_key, subject_entry_id::text, object_entry_id::text, kind, status,
    mapping_justification, reason, evidence, review_note,
    created_by::text, decided_by::text, decided_at, created_at, updated_at
"""


def _relation_row_to_dict(row: Any) -> dict:
    # confidence は**返さない**（KR6: 数値は DB 界面で止める）。
    return {
        "id": str(row[0]),
        "relation_key": row[1] or "",
        "subject_entry_id": str(row[2]),
        "object_entry_id": str(row[3]),
        "kind": row[4] or "",
        "status": row[5] or "",
        "mapping_justification": row[6],
        "reason": row[7] or "",
        "evidence": _as_list(row[8]),
        "review_note": row[9] or "",
        "created_by": row[10],
        "decided_by": row[11],
        "decided_at": row[12].isoformat() if row[12] else None,
        "created_at": row[13].isoformat() if row[13] else "",
        "updated_at": row[14].isoformat() if row[14] else "",
    }


def list_relations(
    *,
    entry_id: str | None = None,
    include_dismissed: bool = False,
    session: Any = None,
) -> list[dict]:
    """関係の一覧（``entry_id`` 指定時は subject / object どちらかに現れる行）。"""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    key = _uuid_or_none(entry_id) if entry_id else None
    if entry_id and key is None:
        return []
    if key is not None:
        conditions.append(
            "(subject_entry_id = CAST(:entry_id AS uuid) "
            "OR object_entry_id = CAST(:entry_id AS uuid))"
        )
        params["entry_id"] = key
    if not include_dismissed:
        conditions.append("status <> :dismissed")
        params["dismissed"] = schema.CANDIDATE_STATUS_DISMISSED
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with _session_scope(session) as sess:
        rows = sess.execute(
            sa_text(
                f"SELECT {_RELATION_COLUMNS_SQL} FROM library_entry_relations "
                f"{where} ORDER BY kind, created_at"
            ),
            params,
        ).fetchall()
    return [_relation_row_to_dict(row) for row in rows]


def get_relation(relation_id: str, *, session: Any = None) -> dict | None:
    key = _uuid_or_none(relation_id)
    if key is None:
        return None
    with _session_scope(session) as sess:
        row = sess.execute(
            sa_text(
                f"SELECT {_RELATION_COLUMNS_SQL} FROM library_entry_relations "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": key},
        ).fetchone()
    return _relation_row_to_dict(row) if row else None


def create_relation(
    *,
    subject_entry_id: str,
    object_entry_id: str,
    kind: str,
    mapping_justification: str,
    reason: str = "",
    evidence: Iterable | None = None,
    confidence: float | None = None,
    status: str = schema.CANDIDATE_STATUS_CANDIDATE,
    actor_id: str | None = None,
    record_audit: Callable[..., Any] | None = None,
    session: Any = None,
) -> dict:
    """概念どうしの関係を1件作る（既定は ``candidate``。KR2）。

    **リンクであってマージではない**（KR3）— 2行は並存したままで、``name`` も
    ``aliases`` も書き換えない。対称な kind（``related`` / ``exact_match`` /
    ``close_match``）は ``relation_key`` で A—B と B—A を同じ行に畳む。既存行があれば
    **上書きせず**そのまま返す（教員の判断を再提案で消さない）。

    ``confidence`` は DB にだけ保存し、戻り値には載せない（KR6）。
    """
    justification = _require_justification(mapping_justification)
    if kind not in schema.RELATION_KINDS:
        raise ValueError(
            f"invalid relation kind: {kind!r} (must be one of {schema.RELATION_KINDS!r})"
        )
    if status not in schema.CANDIDATE_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    if status != schema.CANDIDATE_STATUS_CANDIDATE:
        # KR2: 生成は candidate 始まり。confirmed を直接書く経路は作らない。
        raise ValueError("relations must be created as candidates")
    subject = _uuid_or_none(subject_entry_id)
    obj = _uuid_or_none(object_entry_id)
    if subject is None or obj is None:
        raise ValueError("subject_entry_id and object_entry_id are required")
    if subject == obj:
        raise ValueError("subject_entry_id and object_entry_id must differ")

    relation_key = schema.build_relation_key(kind, subject, obj)
    with _session_scope(session) as sess:
        row = sess.execute(
            sa_text(
                f"""
                INSERT INTO library_entry_relations (
                    relation_key, subject_entry_id, object_entry_id, kind, status,
                    mapping_justification, reason, evidence, confidence, created_by
                ) VALUES (
                    :relation_key, CAST(:subject AS uuid), CAST(:object AS uuid), :kind,
                    :status, :justification, :reason, CAST(:evidence AS jsonb),
                    :confidence, CAST(NULLIF(:actor_id, '') AS uuid)
                )
                ON CONFLICT (relation_key) DO NOTHING
                RETURNING {_RELATION_COLUMNS_SQL}
                """
            ),
            {
                "relation_key": relation_key,
                "subject": subject,
                "object": obj,
                "kind": kind,
                "status": status,
                "justification": justification,
                "reason": _clean(reason),
                "evidence": _dump_json(list(evidence or [])),
                "confidence": confidence,
                "actor_id": _uuid_or_none(actor_id) or "",
            },
        ).fetchone()
        created = row is not None
        if row is None:
            row = sess.execute(
                sa_text(
                    f"SELECT {_RELATION_COLUMNS_SQL} FROM library_entry_relations "
                    "WHERE relation_key = :relation_key LIMIT 1"
                ),
                {"relation_key": relation_key},
            ).fetchone()
        if row is None:
            raise RegistryError("failed to record the relation")
        result = _relation_row_to_dict(row)

    if created and record_audit is not None:
        record_audit(
            entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            entity_id=result["subject_entry_id"],
            action=schema.AUDIT_ACTION_RELATION_ADD,
            old_status="",
            new_status=result["status"],
            actor_id=_clean(actor_id),
            reason=result["reason"],
            metadata={
                "relation_id": result["id"],
                "relation_key": result["relation_key"],
                "kind": result["kind"],
                "object_entry_id": result["object_entry_id"],
                "mapping_justification": result["mapping_justification"],
            },
        )
    return result


_RELATION_AUDIT_ACTION = {
    "confirm": schema.AUDIT_ACTION_RELATION_CONFIRM,
    "dismiss": schema.AUDIT_ACTION_RELATION_DISMISS,
    "restore": schema.AUDIT_ACTION_RELATION_RESTORE,
}


def decide_relation(
    relation_id: str,
    *,
    status: str,
    actor_id: str,
    review_note: str = "",
    record_audit: Callable[..., Any],
    session: Any = None,
) -> dict | None:
    """関係の候補を確定 / 見送り / 差し戻す（``candidate_flow`` 経由。KR2 / KR7）。

    遷移の可否・帰属必須・見送り理由必須は :class:`CandidateFlow` が判定する
    （ここで再実装しない）。対象が無ければ ``None``（呼び出し側は 404）。
    """
    action = action_for_status(status)
    actor = _require_actor(actor_id)
    current = get_relation(relation_id, session=session)
    if current is None:
        return None

    with _session_scope(session) as sess:
        def _apply(**kwargs: Any) -> dict | None:
            row = sess.execute(
                sa_text(
                    f"""
                    UPDATE library_entry_relations
                       SET status = :new_status,
                           review_note = CASE WHEN :review_note <> ''
                                THEN :review_note ELSE library_entry_relations.review_note END,
                           decided_by = CAST(NULLIF(:actor_id, '') AS uuid),
                           decided_at = now(),
                           updated_at = now()
                     WHERE id = CAST(:id AS uuid) AND status = :old_status
                    RETURNING {_RELATION_COLUMNS_SQL}
                    """
                ),
                {
                    "id": current["id"],
                    "new_status": kwargs["new_status"],
                    "old_status": kwargs["old_status"],
                    "review_note": _clean(kwargs.get("reason")),
                    "actor_id": _uuid_or_none(actor) or "",
                },
            ).fetchone()
            if row is None:
                raise ValueError("この関係の判断は、ほかの操作によって変更されています。")
            return _relation_row_to_dict(row)

        flow = CandidateFlow(
            vocab=VOCABULARY,
            audit_entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            apply_status=_apply,
            record_audit=lambda **kwargs: record_audit(
                **{
                    **kwargs,
                    "action": _RELATION_AUDIT_ACTION.get(
                        kwargs.get("action"), kwargs.get("action")
                    ),
                    "entity_id": current["subject_entry_id"],
                    "metadata": {
                        **dict(kwargs.get("metadata") or {}),
                        "relation_id": current["id"],
                        "relation_key": current["relation_key"],
                        "kind": current["kind"],
                        "object_entry_id": current["object_entry_id"],
                    },
                }
            ),
            require_dismiss_reason=True,
        )
        result = getattr(flow, action)(
            current["id"],
            current_status=current["status"],
            actor_id=actor,
            reason=_clean(review_note),
        )
    return result.get("applied")


# ---------------------------------------------------------------------------
# 3. 骨格 node リンク（library_atlas_node_links。**版非依存** = KR9）
# ---------------------------------------------------------------------------

_NODE_LINK_COLUMNS_SQL = """
    id::text, link_key, entry_id::text, domain_key, node_id, node_kind, kind, status,
    mapping_justification, reason, evidence, review_note,
    created_by::text, decided_by::text, decided_at, created_at, updated_at
"""


def _node_link_row_to_dict(row: Any) -> dict:
    # confidence は返さない（KR6）。
    return {
        "id": str(row[0]),
        "link_key": row[1] or "",
        "entry_id": str(row[2]),
        "domain_key": row[3] or "",
        "node_id": row[4] or "",
        "node_kind": row[5] or "",
        "kind": row[6] or "",
        "status": row[7] or "",
        "mapping_justification": row[8],
        "reason": row[9] or "",
        "evidence": _as_list(row[10]),
        "review_note": row[11] or "",
        "created_by": row[12],
        "decided_by": row[13],
        "decided_at": row[14].isoformat() if row[14] else None,
        "created_at": row[15].isoformat() if row[15] else "",
        "updated_at": row[16].isoformat() if row[16] else "",
    }


def list_node_links(
    *,
    domain_key: str | None = None,
    entry_id: str | None = None,
    include_dismissed: bool = False,
    session: Any = None,
) -> list[dict]:
    """レジストリ ↔ 骨格 node のリンク一覧（版非依存なので骨格の版で絞らない）。"""
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if domain_key:
        conditions.append("domain_key = :domain_key")
        params["domain_key"] = _clean(domain_key)
    if entry_id:
        key = _uuid_or_none(entry_id)
        if key is None:
            return []
        conditions.append("entry_id = CAST(:entry_id AS uuid)")
        params["entry_id"] = key
    if not include_dismissed:
        conditions.append("status <> :dismissed")
        params["dismissed"] = schema.CANDIDATE_STATUS_DISMISSED
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with _session_scope(session) as sess:
        rows = sess.execute(
            sa_text(
                f"SELECT {_NODE_LINK_COLUMNS_SQL} FROM library_atlas_node_links "
                f"{where} ORDER BY domain_key, node_id, created_at"
            ),
            params,
        ).fetchall()
    return [_node_link_row_to_dict(row) for row in rows]


def get_node_link(link_id: str, *, session: Any = None) -> dict | None:
    key = _uuid_or_none(link_id)
    if key is None:
        return None
    with _session_scope(session) as sess:
        row = sess.execute(
            sa_text(
                f"SELECT {_NODE_LINK_COLUMNS_SQL} FROM library_atlas_node_links "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": key},
        ).fetchone()
    return _node_link_row_to_dict(row) if row else None


def create_node_link(
    *,
    entry_id: str,
    domain_key: str,
    node_id: str,
    kind: str,
    mapping_justification: str,
    node_kind: str = schema.NODE_KIND_CONCEPT,
    reason: str = "",
    evidence: Iterable | None = None,
    confidence: float | None = None,
    actor_id: str | None = None,
    record_audit: Callable[..., Any] | None = None,
    session: Any = None,
) -> dict:
    """概念 ↔ 骨格 node の対応候補を1件作る（常に ``candidate``。KR2）。

    ``kind`` は ``exact_match`` / ``close_match`` のみ（``broader`` / ``related`` は
    座標系との対応としては意味を持たないので作らせない — §4.5）。
    ``atlas_skeletons`` へは**一切書かない**（KR2 / LS7 / AB4）。既存行は上書きせず
    そのまま返す（``link_key`` は版非依存 = KR9）。
    """
    justification = _require_justification(mapping_justification)
    if not schema.is_valid_node_link_kind(kind):
        raise ValueError(
            f"invalid node link kind: {kind!r} "
            f"(must be one of {schema.NODE_LINK_KINDS!r})"
        )
    if node_kind not in schema.NODE_KINDS:
        raise ValueError(f"invalid node kind: {node_kind!r}")
    key = _uuid_or_none(entry_id)
    domain = _clean(domain_key)
    node = _clean(node_id)
    if key is None:
        raise ValueError("entry_id is required")
    if not domain:
        raise ValueError("domain_key is required")
    if not node:
        raise ValueError("node_id is required")

    link_key = schema.build_node_link_key(key, domain, node)
    with _session_scope(session) as sess:
        row = sess.execute(
            sa_text(
                f"""
                INSERT INTO library_atlas_node_links (
                    link_key, entry_id, domain_key, node_id, node_kind, kind, status,
                    mapping_justification, reason, evidence, confidence, created_by
                ) VALUES (
                    :link_key, CAST(:entry_id AS uuid), :domain_key, :node_id, :node_kind,
                    :kind, :status, :justification, :reason, CAST(:evidence AS jsonb),
                    :confidence, CAST(NULLIF(:actor_id, '') AS uuid)
                )
                ON CONFLICT (link_key) DO NOTHING
                RETURNING {_NODE_LINK_COLUMNS_SQL}
                """
            ),
            {
                "link_key": link_key,
                "entry_id": key,
                "domain_key": domain,
                "node_id": node,
                "node_kind": node_kind,
                "kind": kind,
                "status": schema.CANDIDATE_STATUS_CANDIDATE,
                "justification": justification,
                "reason": _clean(reason),
                "evidence": _dump_json(list(evidence or [])),
                "confidence": confidence,
                "actor_id": _uuid_or_none(actor_id) or "",
            },
        ).fetchone()
        created = row is not None
        if row is None:
            row = sess.execute(
                sa_text(
                    f"SELECT {_NODE_LINK_COLUMNS_SQL} FROM library_atlas_node_links "
                    "WHERE link_key = :link_key LIMIT 1"
                ),
                {"link_key": link_key},
            ).fetchone()
        if row is None:
            raise RegistryError("failed to record the node link")
        result = _node_link_row_to_dict(row)

    if created and record_audit is not None:
        record_audit(
            entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            entity_id=result["entry_id"],
            action=schema.AUDIT_ACTION_NODE_LINK_ADD,
            old_status="",
            new_status=result["status"],
            actor_id=_clean(actor_id),
            reason=result["reason"],
            metadata={
                "link_id": result["id"],
                "link_key": result["link_key"],
                "domain_key": result["domain_key"],
                "node_id": result["node_id"],
                "kind": result["kind"],
                "mapping_justification": result["mapping_justification"],
            },
        )
    return result


_NODE_LINK_AUDIT_ACTION = {
    "confirm": schema.AUDIT_ACTION_NODE_LINK_CONFIRM,
    "dismiss": schema.AUDIT_ACTION_NODE_LINK_DISMISS,
    "restore": schema.AUDIT_ACTION_NODE_LINK_RESTORE,
}


def decide_node_link(
    link_id: str,
    *,
    status: str,
    actor_id: str,
    review_note: str = "",
    record_audit: Callable[..., Any],
    session: Any = None,
) -> dict | None:
    """node リンク候補を確定 / 見送り / 差し戻す（``candidate_flow`` 経由）。

    確定しても ``atlas_skeletons`` は変わらない（KR2 / KR9: 対応の記録であって
    座標系の書き換えではない）。対象が無ければ ``None``（呼び出し側は 404）。
    """
    action = action_for_status(status)
    actor = _require_actor(actor_id)
    current = get_node_link(link_id, session=session)
    if current is None:
        return None

    with _session_scope(session) as sess:
        def _apply(**kwargs: Any) -> dict | None:
            row = sess.execute(
                sa_text(
                    f"""
                    UPDATE library_atlas_node_links
                       SET status = :new_status,
                           review_note = CASE WHEN :review_note <> ''
                                THEN :review_note ELSE library_atlas_node_links.review_note END,
                           decided_by = CAST(NULLIF(:actor_id, '') AS uuid),
                           decided_at = now(),
                           updated_at = now()
                     WHERE id = CAST(:id AS uuid) AND status = :old_status
                    RETURNING {_NODE_LINK_COLUMNS_SQL}
                    """
                ),
                {
                    "id": current["id"],
                    "new_status": kwargs["new_status"],
                    "old_status": kwargs["old_status"],
                    "review_note": _clean(kwargs.get("reason")),
                    "actor_id": _uuid_or_none(actor) or "",
                },
            ).fetchone()
            if row is None:
                raise ValueError("この対応の判断は、ほかの操作によって変更されています。")
            return _node_link_row_to_dict(row)

        flow = CandidateFlow(
            vocab=VOCABULARY,
            audit_entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            apply_status=_apply,
            record_audit=lambda **kwargs: record_audit(
                **{
                    **kwargs,
                    "action": _NODE_LINK_AUDIT_ACTION.get(
                        kwargs.get("action"), kwargs.get("action")
                    ),
                    "entity_id": current["entry_id"],
                    "metadata": {
                        **dict(kwargs.get("metadata") or {}),
                        "link_id": current["id"],
                        "link_key": current["link_key"],
                        "domain_key": current["domain_key"],
                        "node_id": current["node_id"],
                    },
                }
            ),
            require_dismiss_reason=True,
        )
        result = getattr(flow, action)(
            current["id"],
            current_status=current["status"],
            actor_id=actor,
            reason=_clean(review_note),
        )
    return result.get("applied")


def annotate_node_links(
    links: Iterable[dict],
    frozen_skeleton: Any,
    *,
    resolve: Any = None,
) -> list[dict]:
    """リンク行に「その node が現行凍結版に実在するか」を付ける（純関数・KR9）。

    版が変わって node_id が消えても**行は消さない**ので、読み時にこの事実
    （``node_in_current_version``）だけを添える。骨格が読めないときは ``None``
    （「分からない」を false と偽らない）。実在する場合はラベルも添える
    （UI が内部 ID を表示に使わないため — PL7 と同じ規律）。

    ``resolve`` は ``node_id -> {"current_node_id", ...}`` の読み替え器
    （``core.atlas_correspondence.NodeResolver.resolve``）。渡すと、骨格の改版で
    id が振り直されたリンクも現行版の node へ読み替えて実在を判定する
    （ノード版間対応 §6・NC5: リンク行の ``node_id`` は書き換えない）。
    ``current_node_id`` は読み替え先（実在しなければ空文字）。
    """
    items = [dict(link) for link in (links or [])]
    if frozen_skeleton is None:
        for item in items:
            item["node_in_current_version"] = None
            item["current_node_id"] = ""
            item.setdefault("node_label", "")
        return items

    labels: dict[str, str] = {}
    regions = getattr(frozen_skeleton, "regions", None)
    if regions is None and isinstance(frozen_skeleton, dict):
        regions = frozen_skeleton.get("regions") or []
    for region in regions or []:
        region_id = str(
            getattr(region, "id", None)
            if not isinstance(region, dict)
            else region.get("id") or ""
        )
        region_label = str(
            getattr(region, "label", "")
            if not isinstance(region, dict)
            else region.get("label") or ""
        )
        if region_id:
            labels[region_id] = region_label
        concepts = (
            getattr(region, "concepts", None)
            if not isinstance(region, dict)
            else region.get("concepts")
        )
        for concept in concepts or []:
            concept_id = str(
                getattr(concept, "id", None)
                if not isinstance(concept, dict)
                else concept.get("id") or ""
            )
            concept_label = str(
                getattr(concept, "label", "")
                if not isinstance(concept, dict)
                else concept.get("label") or ""
            )
            if concept_id:
                labels[concept_id] = concept_label

    for item in items:
        node_id = str(item.get("node_id") or "")
        target = node_id
        if resolve is not None and node_id not in labels:
            try:
                resolved = resolve(node_id) or {}
            except Exception:  # noqa: BLE001 — 読み替えの失敗で一覧を落とさない
                resolved = {}
            target = str(resolved.get("current_node_id") or "") or node_id
        present = target in labels
        item["node_in_current_version"] = present
        item["current_node_id"] = target if present else ""
        item["node_label"] = labels.get(target, "")
    return items


# ---------------------------------------------------------------------------
# 4. エントリの確定（library_entries.review_status）
# ---------------------------------------------------------------------------

_ENTRY_REVIEW_AUDIT_ACTION = {
    "confirm": schema.AUDIT_ACTION_REVIEW_CONFIRM,
    "dismiss": schema.AUDIT_ACTION_REVIEW_DISMISS,
    "restore": schema.AUDIT_ACTION_REVIEW_RESTORE,
}


def decide_entry_review(
    entry_id: str,
    *,
    status: str,
    actor_id: str,
    review_note: str = "",
    record_audit: Callable[..., Any],
    session: Any = None,
) -> dict | None:
    """概念（``library_entries``）の候補を確定 / 見送り / 差し戻す。

    ``review_status`` は ``UPDATABLE_FIELDS`` に入っていないガバナンス列で、遷移は
    この関数（= :class:`CandidateFlow`）だけが行う（§4.2）。``confirmed`` になって
    初めて凍結でき、凍結して初めてパイプライン retrieval と学習者に届く（KR2）。

    ``status='retired'``（公開を止める）とは**別軸**なので、本関数は ``status`` 列に
    触れない。対象が無ければ ``None``（呼び出し側は 404）。
    """
    action = action_for_status(status)
    actor = _require_actor(actor_id)

    from . import store as library_store  # 循環 import を避けるため関数内で読む

    current = library_store.get_entry(entry_id)
    if current is None:
        return None

    with _session_scope(session) as sess:
        def _apply(**kwargs: Any) -> dict | None:
            row = sess.execute(
                sa_text(
                    f"""
                    UPDATE library_entries
                       SET review_status = :new_status,
                           review_note = CASE WHEN :review_note <> ''
                                THEN :review_note ELSE library_entries.review_note END,
                           decided_by = CAST(NULLIF(:actor_id, '') AS uuid),
                           decided_at = now(),
                           updated_at = now()
                     WHERE id = CAST(:id AS uuid) AND review_status = :old_status
                    RETURNING {library_store._ENTRY_COLUMNS_SQL}
                    """
                ),
                {
                    "id": current["id"],
                    "new_status": kwargs["new_status"],
                    "old_status": kwargs["old_status"],
                    "review_note": _clean(kwargs.get("reason")),
                    "actor_id": _uuid_or_none(actor) or "",
                },
            ).fetchone()
            if row is None:
                raise ValueError("この概念の判断は、ほかの操作によって変更されています。")
            return library_store._row_to_entry(row)

        flow = CandidateFlow(
            vocab=VOCABULARY,
            audit_entity_type=AUDIT_ENTITY_LIBRARY_ENTRY,
            apply_status=_apply,
            record_audit=lambda **kwargs: record_audit(
                **{
                    **kwargs,
                    "action": _ENTRY_REVIEW_AUDIT_ACTION.get(
                        kwargs.get("action"), kwargs.get("action")
                    ),
                    "metadata": {
                        **dict(kwargs.get("metadata") or {}),
                        "domain_key": current["domain_key"],
                        "entry_type": current["entry_type"],
                    },
                }
            ),
            require_dismiss_reason=True,
        )
        result = getattr(flow, action)(
            current["id"],
            current_status=current["review_status"],
            actor_id=actor,
            reason=_clean(review_note),
        )
    return result.get("applied")
