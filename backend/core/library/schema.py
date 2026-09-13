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

from core.atlas_gaps.schema import normalize_label
from core.schema import (  # noqa: F401  (re-export)
    CONCEPT_LABEL_KINDS,
    CONCEPT_RELATION_KINDS,
    CONCEPT_REVIEW_STATUSES,
    LIBRARY_ENTRY_TYPES,
    MAPPING_JUSTIFICATIONS,
)

# ---------------------------------------------------------------------------
# 語彙（DB の語彙表 / CHECK 制約と対応）
# ---------------------------------------------------------------------------

ENTRY_TYPE_APPARATUS = "apparatus"
ENTRY_TYPE_THEORY_COMPONENT = "theory_component"
ENTRY_TYPE_CONCEPT = "concept"
ENTRY_TYPE_THEORY = "theory"
ENTRY_TYPE_METHOD = "method"
ENTRY_TYPE_OBSERVABLE = "observable"
ENTRY_TYPE_ASSUMPTION = "assumption"
ENTRY_TYPE_QUANTITY = "quantity"
ENTRY_TYPE_PROCESS = "process"

#: entry_type の語彙。正本は ``core/schema.py::LIBRARY_ENTRY_TYPES``（migration 082 の
#: 語彙表 ``knowledge_entry_types`` が同じ列挙をシードする）。ここは再エクスポートで、
#: 定数名 ``ENTRY_TYPES`` は既存呼び出し（``is_valid_entry_type`` / route の 422）の
#: ために維持する（二重定義しない = KO7 と同じ作法）。
ENTRY_TYPES = LIBRARY_ENTRY_TYPES

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


#: standardization_status の日本語ラベル（フロント3表のミラー正本）。
#:
#: `frontend/public/js/deliberation.js::STANDARDIZATION_STATUS_LABELS` /
#: `app.js::MATERIAL_LIBRARY_STATUS_LABELS` /
#: `admin.js::_libraryStandardizationLabels` の3表と逐語一致させる
#: （固定は `backend/tests/test_library_vocab_mirror.py`。**この表を削除すると
#: ミラーテストが落ちる** — 表を消すのではなく、3表ごと同時に直すこと）。
#:
#: `emerging_common` は「コーパス内で反復して現れるのに、外部の標準としては
#: 確立していない」状態を指す。これは判定不能の中間段階ではなく、本システムが
#: 見つけ出す発見的価値の在り処（knowledge_network_vision §3 修正③）なので、
#: 「普及しつつある」のように外部での定着を示唆する語には**しない**。
STANDARDIZATION_STATUS_LABELS = {
    STANDARDIZATION_STATUS_STANDARD: "標準",
    STANDARDIZATION_STATUS_FIELD_STANDARD: "分野標準",
    STANDARDIZATION_STATUS_EMERGING_COMMON: "共通化しつつある",
    STANDARDIZATION_STATUS_NOVEL: "新規",
    STANDARDIZATION_STATUS_UNKNOWN: "未評価",
}


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
# 概念レジストリ（Phase 3 / migration 082）— 語彙・キー導出・日本語ラベル
#
# 正本: docs/features/concept_registry_design.md（不変条項 KR1〜KR10 は §2）。
# 語彙そのものの正本は core/schema.py で、ここは再エクスポート + 導出関数 + 表示ラベル。
# ---------------------------------------------------------------------------

# -- review_status（候補 → 教員の確定。status='retired' とは別軸）-------------------
REVIEW_STATUS_CANDIDATE = "candidate"
REVIEW_STATUS_CONFIRMED = "confirmed"
REVIEW_STATUS_DISMISSED = "dismissed"
REVIEW_STATUSES = CONCEPT_REVIEW_STATUSES

#: 見送りに理由（``review_note``）を要求する遷移先（KR7: 却下は理由必須）。
REVIEW_NOTE_REQUIRED_STATUSES = (REVIEW_STATUS_DISMISSED,)

# -- ラベル種別（SKOS）----------------------------------------------------------
LABEL_KIND_PREFERRED = "preferred"
LABEL_KIND_ALTERNATE = "alternate"
LABEL_KIND_HIDDEN = "hidden"
LABEL_KINDS = CONCEPT_LABEL_KINDS

#: 行として持つラベル種別。``preferred`` は ``library_entries.name`` が正本なので
#: 行にしない（SKOS: 言語ごとに 1 prefLabel。二重管理を作らない — §4.3）。
LABEL_ROW_KINDS = (LABEL_KIND_ALTERNATE, LABEL_KIND_HIDDEN)

LABEL_STATUS_CONFIRMED = "confirmed"
LABEL_STATUS_DISMISSED = "dismissed"
LABEL_STATUSES = (LABEL_STATUS_CONFIRMED, LABEL_STATUS_DISMISSED)

# -- 関係種別（SKOS）------------------------------------------------------------
RELATION_KIND_BROADER = "broader"
RELATION_KIND_RELATED = "related"
RELATION_KIND_EXACT_MATCH = "exact_match"
RELATION_KIND_CLOSE_MATCH = "close_match"
RELATION_KINDS = CONCEPT_RELATION_KINDS

#: 無向に扱う関係（``relation_key`` で A—B と B—A を同じ行に畳む）。``broader`` は有向。
SYMMETRIC_RELATION_KINDS = (
    RELATION_KIND_RELATED,
    RELATION_KIND_EXACT_MATCH,
    RELATION_KIND_CLOSE_MATCH,
)

#: レジストリ ↔ 骨格 node のリンクで許す種別（§4.5。``broader`` / ``related`` は
#: 座標系との対応としては意味を持たないので作らせない — registry.py が ValueError）。
NODE_LINK_KINDS = (RELATION_KIND_EXACT_MATCH, RELATION_KIND_CLOSE_MATCH)

NODE_KIND_REGION = "region"
NODE_KIND_CONCEPT = "concept"
NODE_KINDS = (NODE_KIND_REGION, NODE_KIND_CONCEPT)

# -- 候補の状態（関係 / node リンク。candidate_flow の語彙）-------------------------
CANDIDATE_STATUS_CANDIDATE = "candidate"
CANDIDATE_STATUS_CONFIRMED = "confirmed"
CANDIDATE_STATUS_DISMISSED = "dismissed"
CANDIDATE_STATUSES = (
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_CONFIRMED,
    CANDIDATE_STATUS_DISMISSED,
)

# -- mapping_justification（「なぜ同じと言えたか」。KR4）-----------------------------
JUSTIFICATION_MANUAL = "manual_curation"
JUSTIFICATION_LEXICAL = "lexical_match"
JUSTIFICATION_VECTOR = "vector_similarity"
JUSTIFICATION_CARTRIDGE = "cartridge_declared"
JUSTIFICATION_COOCCURRENCE = "corpus_cooccurrence"
JUSTIFICATION_LLM = "llm_candidate"
JUSTIFICATIONS = MAPPING_JUSTIFICATIONS


def is_valid_review_status(value: str) -> bool:
    return value in REVIEW_STATUSES


def is_valid_label_kind(value: str) -> bool:
    return value in LABEL_KINDS


def is_valid_relation_kind(value: str) -> bool:
    return value in RELATION_KINDS


def is_valid_node_link_kind(value: str) -> bool:
    return value in NODE_LINK_KINDS


def is_valid_justification(value: str) -> bool:
    return value in JUSTIFICATIONS


# ---------------------------------------------------------------------------
# component_type → entry_type の決定論写像（§4.1）
# ---------------------------------------------------------------------------
#
# **分野語を書かない**（domain-independent）。写像は型語彙の語幹だけを見る:
#   Domain*Component はその語幹（Concept/Theory/Method/Assumption/Observable）
#   apparatus / instrument / part          → apparatus
#   theory                                 → theory
#   observation                            → observable
#   operator                               → quantity
#   law / mechanism / unknown / Paper*      → concept（論文固有の主張・関係は概念層で持つ）
#
# 語彙外の値は ``concept`` に倒す（情報を落とさず、最も一般的な受け皿へ置く）。

_ENTRY_TYPE_BY_COMPONENT_TYPE: dict[str, str] = {
    "apparatus": ENTRY_TYPE_APPARATUS,
    "instrument": ENTRY_TYPE_APPARATUS,
    "part": ENTRY_TYPE_APPARATUS,
    "theory": ENTRY_TYPE_THEORY,
    "DomainTheoryComponent": ENTRY_TYPE_THEORY,
    "DomainMethodComponent": ENTRY_TYPE_METHOD,
    "DomainAssumptionComponent": ENTRY_TYPE_ASSUMPTION,
    "DomainObservableComponent": ENTRY_TYPE_OBSERVABLE,
    "observation": ENTRY_TYPE_OBSERVABLE,
    "operator": ENTRY_TYPE_QUANTITY,
    "DomainConceptComponent": ENTRY_TYPE_CONCEPT,
    "concept": ENTRY_TYPE_CONCEPT,
    "law": ENTRY_TYPE_CONCEPT,
    "mechanism": ENTRY_TYPE_CONCEPT,
    "unknown": ENTRY_TYPE_CONCEPT,
}

#: 写像に無い値の落とし先（Paper*Component 群を含む）。
ENTRY_TYPE_FALLBACK = ENTRY_TYPE_CONCEPT


def entry_type_for_component_type(component_type: str | None) -> str:
    """``theory_components.component_type`` → ``library_entries.entry_type`` の決定論写像。

    同一性候補の生成（§6.2）が、component から concept 候補行を立てるときに使う。
    語彙外・未知の値は :data:`ENTRY_TYPE_FALLBACK`（``concept``）に倒す
    （分野語をここに書かない = domain-independent）。
    """
    return _ENTRY_TYPE_BY_COMPONENT_TYPE.get(
        str(component_type or "").strip(), ENTRY_TYPE_FALLBACK
    )


# ---------------------------------------------------------------------------
# キー導出（版非依存 = KR9。normalize_label は core/atlas_gaps/schema.py が正本）
# ---------------------------------------------------------------------------

KEY_SEPARATOR = "|"
RELATION_KEY_PREFIX = "rel"
NODE_LINK_KEY_PREFIX = "anode"
CANDIDATE_KEY_PREFIX = "cand"


def build_relation_key(kind: str, subject_entry_id: str, object_entry_id: str) -> str:
    """``library_entry_relations.relation_key`` を導出する（§4.4）。

    対称な kind（``related`` / ``exact_match`` / ``close_match``）は端点を辞書順に
    並べ替えて A—B と B—A を同じキーに畳む。``broader`` は有向なので
    ``rel|broader|{narrower}|{broader}`` の順序を保つ。
    """
    left = str(subject_entry_id or "").strip()
    right = str(object_entry_id or "").strip()
    if kind in SYMMETRIC_RELATION_KINDS:
        left, right = sorted((left, right))
    return KEY_SEPARATOR.join((RELATION_KEY_PREFIX, str(kind or "").strip(), left, right))


def build_node_link_key(entry_id: str, domain_key: str, node_id: str) -> str:
    """``library_atlas_node_links.link_key`` を導出する（**版非依存** = KR9）。

    ``skeleton_version`` を含めない。凍結のたびに同じ判断を要求しないため
    （``atlas_anchor_aliases`` / ``cluster_key`` / ``edge_key`` の3前例と同じ）。
    """
    return KEY_SEPARATOR.join(
        (
            NODE_LINK_KEY_PREFIX,
            str(entry_id or "").strip(),
            str(domain_key or "").strip(),
            str(node_id or "").strip(),
        )
    )


def build_candidate_key(domain_key: str, name: str) -> str:
    """``library_entries.candidate_key`` を導出する（候補の再提案を同一行に畳む）。

    形式: ``cand|{domain_key}|{normalize_label(name)}``。手動作成の行は
    ``candidate_key=None`` のまま（畳む対象ではない）。
    """
    return KEY_SEPARATOR.join(
        (
            CANDIDATE_KEY_PREFIX,
            str(domain_key or "").strip(),
            normalize_label(name),
        )
    )


# ---------------------------------------------------------------------------
# 日本語ラベル（フロント3表と同じく admin.js のミラー正本）
#
# `admin.js::_libraryEntryTypeLabels` / `_libraryLabelKindLabels` /
# `_libraryRelationKindLabels` / `_libraryJustificationLabels` /
# `_libraryReviewStatusLabels` と逐語一致させる（固定は
# backend/tests/test_library_vocab_mirror.py。**この表を削除するとミラーテストが落ちる**
# — 表を消すのではなく両側を同時に直すこと）。
#
# KR6「数値を見せない」: ラベルに件数・確度・割合を書かない。KR8「閉世界の正直さ」:
# 「唯一」「初」のような分野全体への言明を書かない。
# ---------------------------------------------------------------------------

ENTRY_TYPE_LABELS = {
    "apparatus": "装置",
    "theory_component": "理論コンポーネント",
    "concept": "概念",
    "theory": "理論",
    "method": "方法",
    "observable": "観測量",
    "assumption": "前提",
    "quantity": "量",
    "process": "過程",
}

LABEL_KIND_LABELS = {
    "preferred": "主ラベル",
    "alternate": "別名",
    "hidden": "隠しラベル",
}

RELATION_KIND_LABELS = {
    "broader": "上位",
    "related": "関連",
    "exact_match": "同じ",
    "close_match": "近い",
}

JUSTIFICATION_LABELS = {
    "manual_curation": "教員の判断",
    "lexical_match": "表記の一致",
    "vector_similarity": "意味の近さ",
    "cartridge_declared": "分野の宣言",
    "corpus_cooccurrence": "コーパス内の共起",
    "llm_candidate": "AI の候補",
}

REVIEW_STATUS_LABELS = {
    "candidate": "候補",
    "confirmed": "確定",
    "dismissed": "見送り",
}


def entry_type_label(value: str) -> str:
    return ENTRY_TYPE_LABELS.get(value, value)


def label_kind_label(value: str) -> str:
    return LABEL_KIND_LABELS.get(value, value)


def relation_kind_label(value: str) -> str:
    return RELATION_KIND_LABELS.get(value, value)


def justification_label(value: str) -> str:
    return JUSTIFICATION_LABELS.get(value, value)


def review_status_label(value: str) -> str:
    return REVIEW_STATUS_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 監査 action 語彙（§11。新しい entity_type は作らない — AUDIT_ENTITY_LIBRARY_ENTRY を流用）
# ---------------------------------------------------------------------------

AUDIT_ACTION_REVIEW_CONFIRM = "review_confirm"
AUDIT_ACTION_REVIEW_DISMISS = "review_dismiss"
AUDIT_ACTION_REVIEW_RESTORE = "review_restore"
AUDIT_ACTION_LABEL_ADD = "label_add"
AUDIT_ACTION_LABEL_DISMISS = "label_dismiss"
AUDIT_ACTION_RELATION_ADD = "relation_add"
AUDIT_ACTION_RELATION_CONFIRM = "relation_confirm"
AUDIT_ACTION_RELATION_DISMISS = "relation_dismiss"
AUDIT_ACTION_RELATION_RESTORE = "relation_restore"
AUDIT_ACTION_NODE_LINK_ADD = "node_link_add"
AUDIT_ACTION_NODE_LINK_CONFIRM = "node_link_confirm"
AUDIT_ACTION_NODE_LINK_DISMISS = "node_link_dismiss"
AUDIT_ACTION_NODE_LINK_RESTORE = "node_link_restore"
#: 地図との対応候補の決定論導出（§6.1・``core/library/atlas_links.py``）。分野単位の
#: 1 行で、作られた候補の件数は記帳しない（KR6）。
AUDIT_ACTION_NODE_LINK_DERIVE = "node_link_derive"

REGISTRY_AUDIT_ACTIONS = (
    AUDIT_ACTION_REVIEW_CONFIRM,
    AUDIT_ACTION_REVIEW_DISMISS,
    AUDIT_ACTION_REVIEW_RESTORE,
    AUDIT_ACTION_LABEL_ADD,
    AUDIT_ACTION_LABEL_DISMISS,
    AUDIT_ACTION_RELATION_ADD,
    AUDIT_ACTION_RELATION_CONFIRM,
    AUDIT_ACTION_RELATION_DISMISS,
    AUDIT_ACTION_RELATION_RESTORE,
    AUDIT_ACTION_NODE_LINK_ADD,
    AUDIT_ACTION_NODE_LINK_CONFIRM,
    AUDIT_ACTION_NODE_LINK_DISMISS,
    AUDIT_ACTION_NODE_LINK_RESTORE,
    AUDIT_ACTION_NODE_LINK_DERIVE,
)


# ---------------------------------------------------------------------------
# 同一性候補の導出パラメータ（§6.2。数値は DB 界面までで API / UI へは出さない = KR6）
# ---------------------------------------------------------------------------

#: chunk-proxy ベクトル近傍の足切り（W層 cross_corpus と同じ下地・**追加 embedding ゼロ**）。
IDENTITY_CHUNK_PROXY_THRESHOLD = 0.60

#: 代表チャンク1件あたりの pgvector 近傍取得件数。
IDENTITY_CHUNK_TOPK = 8

#: 分野が引けなかった候補エントリの ``domain_key``（§6.2 の 3 段解決の最後）。
#:
#: 「この論文を参照するコースがまだ無い / コースに分野が設定されていない」は正常な状態
#: なので（``corpus.document_domain_keys`` の規約）、分野を推測で埋めず**分からないと
#: いう事実**をこのキーで持つ。候補導出（``atlas_links`` / ``identity_candidates``）は
#: この値を実在する分野名として扱わない。
DOMAIN_KEY_UNASSIGNED = "unassigned"


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
    # 概念レジストリのガバナンス列（migration 082）。UPDATABLE_FIELDS には**入れない**
    # （遷移は core/library/registry.py::decide_entry_review = candidate_flow 経由）。
    # 既定が confirmed なのは、既存行も手動作成も「人間が作った行」だから（KR2）。
    review_status: str = REVIEW_STATUS_CONFIRMED
    review_note: str = ""
    #: 「なぜこの概念を立てられたか」。記録が無い行は None のまま（推測で埋めない = KR4）。
    mapping_justification: str | None = None
    #: 候補の再提案を同一行に畳むキー。手動作成は None。
    candidate_key: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
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
            "review_status": self.review_status,
            "review_note": self.review_note,
            "mapping_justification": self.mapping_justification,
            "candidate_key": self.candidate_key,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
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
