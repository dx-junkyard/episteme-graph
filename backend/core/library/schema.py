"""L層（ナレッジライブラリ）の dataclass と語彙の正本。

DB は migration 042 (`library_entries` / `library_entry_versions`)。設計は
docs/features/image_pipeline_knowledge_library_design.md §6 を正本とする。

- ライブラリは「分野ごとの教員共同財」。document に縛られない知識エントリ
  （まず apparatus、次いで theory_component）を蓄積・精錬する。
- draft (`library_entries`) が正本、`library_entry_versions` は不変の凍結版履歴
  （`core/atlas_store.py` の draft/凍結パターンを踏襲）。
- 削除しない（P4）。`status='retired'` 遷移のみで行削除 API は作らない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 語彙（DB CHECK 制約と対応）
# ---------------------------------------------------------------------------

ENTRY_TYPE_APPARATUS = "apparatus"
ENTRY_TYPE_THEORY_COMPONENT = "theory_component"
ENTRY_TYPES = (ENTRY_TYPE_APPARATUS, ENTRY_TYPE_THEORY_COMPONENT)

STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
ENTRY_STATUSES = (STATUS_ACTIVE, STATUS_RETIRED)

# 標準化判定（Phase S・知識ネットワークビジョン §3 修正③・migration 050）の判定語彙。
# 三角測量（LLM 事前知識 + 本モジュールの凍結版類似 + コーパス内反復）で決定論的に導出される
# `core/deliberation/standardization/aggregate.py` の出力語彙と、この列の CHECK 制約が
# 同じ5値を使う（正本はこちら。deliberation.standardization 側は読み取り専用で参照する）。
# ガバナンス列であり、draft 本文編集（UPDATABLE_FIELDS）とは別経路
# （core.deliberation.annotations の standardization commit ルーティング）でのみ更新する。
STANDARDIZATION_STATUS_STANDARD = "standard"
STANDARDIZATION_STATUS_FIELD_STANDARD = "field_standard"
STANDARDIZATION_STATUS_EMERGING_COMMON = "emerging_common"
STANDARDIZATION_STATUS_NOVEL = "novel"
STANDARDIZATION_STATUS_UNKNOWN = "unknown"
STANDARDIZATION_STATUSES = (
    STANDARDIZATION_STATUS_STANDARD,
    STANDARDIZATION_STATUS_FIELD_STANDARD,
    STANDARDIZATION_STATUS_EMERGING_COMMON,
    STANDARDIZATION_STATUS_NOVEL,
    STANDARDIZATION_STATUS_UNKNOWN,
)


def is_valid_standardization_status(value: str) -> bool:
    return value in STANDARDIZATION_STATUSES

# apparatus body の型別ペイロードとして推奨されるキー（§6-2）。
# domain-specific な値をここに書くことはしない（キー名の一覧のみ）。
APPARATUS_BODY_KEY_TYPICAL_PARTS = "typical_parts"
APPARATUS_BODY_KEY_VISUAL_CUES = "visual_cues"
APPARATUS_BODY_KEY_TYPICAL_CONFIGURATIONS = "typical_configurations"
APPARATUS_BODY_KEY_MEASUREMENT_TARGETS = "measurement_targets"
# 装置候補（apparatus_semantics）の connections（from_part/to_part/relation/reason 等）の
# 受け皿（N28）。昇格モーダルが候補から転記する — 受け皿キーが無いと candidate の接続情報が
# 昇格で構造的に落ちる（P4 違反）ため正式キーとして持つ。
APPARATUS_BODY_KEY_CONNECTIONS = "connections"
APPARATUS_BODY_KEYS = (
    APPARATUS_BODY_KEY_TYPICAL_PARTS,
    APPARATUS_BODY_KEY_VISUAL_CUES,
    APPARATUS_BODY_KEY_TYPICAL_CONFIGURATIONS,
    APPARATUS_BODY_KEY_MEASUREMENT_TARGETS,
    APPARATUS_BODY_KEY_CONNECTIONS,
)

# update_entry() で編集可能なフィールドのホワイトリスト（§6-3 draft 編集）。
UPDATABLE_FIELDS = (
    "name",
    "aliases",
    "summary",
    "body",
    "exemplar_images",
    "source_component_ids",
    "source_document_ids",
)

# JSONB 列として保存するフィールド（store.py の SET/INSERT 組み立てで共有する）。
JSON_FIELDS = (
    "aliases",
    "body",
    "exemplar_images",
    "source_component_ids",
    "source_document_ids",
)


def is_valid_entry_type(entry_type: str) -> bool:
    return entry_type in ENTRY_TYPES


def is_valid_status(status: str) -> bool:
    return status in ENTRY_STATUSES


# ---------------------------------------------------------------------------
# dataclass
# ---------------------------------------------------------------------------


@dataclass
class LibraryEntry:
    """``library_entries`` 行の dataclass 表現（draft 正本）。"""

    id: str = ""
    domain_key: str = ""
    entry_type: str = ENTRY_TYPE_APPARATUS
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    body: dict[str, Any] = field(default_factory=dict)
    exemplar_images: list[dict] = field(default_factory=list)
    source_component_ids: list[str] = field(default_factory=list)
    source_document_ids: list[str] = field(default_factory=list)
    status: str = STATUS_ACTIVE
    # ガバナンス列（migration 050）。draft 本文編集（UPDATABLE_FIELDS）とは独立に
    # standardization commit ルーティングのみが更新する（revision は変更しない）。
    standardization_status: str = STANDARDIZATION_STATUS_UNKNOWN
    revision: int = 1
    latest_version_no: int = 0
    created_by: str | None = None
    updated_by: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain_key": self.domain_key,
            "entry_type": self.entry_type,
            "name": self.name,
            "aliases": list(self.aliases),
            "summary": self.summary,
            "body": dict(self.body),
            "exemplar_images": list(self.exemplar_images),
            "source_component_ids": list(self.source_component_ids),
            "source_document_ids": list(self.source_document_ids),
            "status": self.status,
            "standardization_status": self.standardization_status,
            "revision": self.revision,
            "latest_version_no": self.latest_version_no,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class LibraryEntryVersion:
    """``library_entry_versions`` 行の dataclass 表現（凍結版・不変）。

    embedding はベクトルであり検索専用のため ``to_dict()`` には含めない
    （store.list_versions / get_version は embedding を返さない、§6-2）。
    """

    id: str = ""
    entry_id: str = ""
    version_no: int = 0
    content: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    note: str = ""
    published_by: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "version_no": self.version_no,
            "content": dict(self.content),
            "note": self.note,
            "published_by": self.published_by,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# JSONB ⇄ Python 変換ヘルパ（psycopg2 は通常 dict/list を返すが、防御的に扱う）
# ---------------------------------------------------------------------------


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def embedding_source_text(entry: dict[str, Any]) -> str:
    """凍結時の embedding 対象テキスト（name + aliases + summary + body.visual_cues の連結、§6-2）。"""
    parts: list[str] = [str(entry.get("name") or "")]
    aliases = entry.get("aliases") or []
    if isinstance(aliases, list):
        parts.extend(str(a) for a in aliases if a)
    parts.append(str(entry.get("summary") or ""))
    body = entry.get("body") or {}
    if isinstance(body, dict):
        visual_cues = body.get(APPARATUS_BODY_KEY_VISUAL_CUES)
        if isinstance(visual_cues, list):
            parts.extend(str(v) for v in visual_cues if v)
        elif visual_cues:
            parts.append(str(visual_cues))
    return "\n".join(p for p in parts if p)
