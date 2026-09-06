"""分野マップの関係表示（RE層）— 語彙とキー導出の正本（設計書 §4）。

正本: ``docs/features/atlas_relation_edges_design.md``（不変条項 RE1〜RE8 は §2）。

DB にも FastAPI にも LLM SDK にも依存しない純粋な語彙定義・純粋関数だけを置く
（``core/atlas_gaps/schema.py`` / ``core/atlas_vectors/schema.py`` と同じ立場）。

ここの ``DECISION_STATUSES`` は **migration 076 の CHECK 制約と一対一で一致させること**
（``backend/tests/test_atlas_edges_guardrails.py`` が SQL 本文をパースして完全一致を
検査する）。

**RE4 数値非表示**: cosine の生値・共起件数を返す関数はここに置かない。近さは段階
ラベル（正本は ``core.label_vocab.ANCHOR_NEARNESS_SCALE`` — この層はラベル文字列を
再定義しない）、共起の支持は論文タイトルの列挙で示す（件数の導出関数も置かない）。
"""

from __future__ import annotations

from core.atlas_gaps.schema import DECISION_STATUS_LABELS as _GAP_DECISION_STATUS_LABELS

# ---------------------------------------------------------------------------
# 候補の出所（origin）— RE2 出所必須
# ---------------------------------------------------------------------------

#: VA層のアンカープロトタイプ同士が最上位帯の近さにある（``ANCHOR_NEARNESS_SCALE``）。
ORIGIN_VECTOR = "vector"
#: 両ノードに live 配置を持つ論文が :data:`MIN_DOCUMENTS_FOR_EDGE` 件以上ある。
ORIGIN_CO_OCCURRENCE = "co_occurrence"

ORIGINS = (ORIGIN_VECTOR, ORIGIN_CO_OCCURRENCE)

#: 共起を候補として浮上させる distinct document の下限（§4）。
#: ``atlas_gaps.MIN_DOCUMENTS_FOR_CANDIDATE`` と同じ思想・同じ値（1論文だけの共起は
#: 分野の関係ではなく、その論文の都合である）。
MIN_DOCUMENTS_FOR_EDGE = 2

# ---------------------------------------------------------------------------
# 推定の糸の上限（RE7 ヘアボール防止）
# ---------------------------------------------------------------------------

#: 1ノードから伸ばす糸の上限（学習者・教員とも同じ）。
THREADS_MAX_PER_NODE = 2
#: 1ドメインの糸の総数上限。
THREADS_MAX_TOTAL = 30

# ---------------------------------------------------------------------------
# 教員の判断（atlas_edge_decisions.status）— 確定は人間のみ（RE3 / KN-3）
# ---------------------------------------------------------------------------

#: まだ判断していない / 見送りを取り消した（restore の着地点）。
DECISION_STATUS_CANDIDATE = "candidate"
#: 「関係として妥当」の判断（骨格 draft はまだ変わらない — 採用と反映の分離）。
DECISION_STATUS_ACCEPTED = "accepted"
#: 見送り（理由必須。以降レビューキューにも糸レイヤーにも出さない = RE8）。
DECISION_STATUS_DISMISSED = "dismissed"

DECISION_STATUSES = (
    DECISION_STATUS_CANDIDATE,
    DECISION_STATUS_ACCEPTED,
    DECISION_STATUS_DISMISSED,
)

#: 判断状態の日本語表示名。3語彙は ``core/atlas_gaps/schema.py`` の
#: ``DECISION_STATUS_LABELS``（未判断 / 採用 / 見送り）と**同じ言葉**であるべきなので、
#: 表を書き写さずそちらから引く（gap には辺に無い ``merged`` があるため、キー集合だけが
#: 違う）。gap 側に無い状態が将来増えたときは、その値だけをここで補うこと。
DECISION_STATUS_LABELS: dict[str, str] = {
    status: _GAP_DECISION_STATUS_LABELS.get(status, status)
    for status in DECISION_STATUSES
}

#: レビューキューの既定から外す状態（``include_dismissed=True`` で戻せる）。
SUPPRESSED_DECISION_STATUSES = (DECISION_STATUS_DISMISSED,)

#: 理由（``review_note``）が必須の遷移先（空は ``ValueError`` → route が 422）。
REVIEW_NOTE_REQUIRED_STATUSES = (DECISION_STATUS_DISMISSED,)

#: ``edge_kind`` が必須の遷移先（種別の無い辺を骨格へ入れない）。
EDGE_KIND_REQUIRED_STATUSES = (DECISION_STATUS_ACCEPTED,)


def is_valid_decision_status(value: str) -> bool:
    return value in DECISION_STATUSES


def decision_status_label(value: str) -> str:
    """判断状態の日本語表示名（事実文のみ。督促・煽り表現を入れない）。"""
    return DECISION_STATUS_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 監査 action（設計書 §8。entity_type は core/schema.py::AUDIT_ENTITY_ATLAS_EDGE）
# ---------------------------------------------------------------------------

AUDIT_ACTION_ACCEPT = "accept"
AUDIT_ACTION_DISMISS = "dismiss"
AUDIT_ACTION_RESTORE = "restore"
AUDIT_ACTION_MARK_INCORPORATED = "mark_incorporated"

#: 候補は読み時導出なので ``detect`` の記帳は無い（§8）。
AUDIT_ACTIONS = (
    AUDIT_ACTION_ACCEPT,
    AUDIT_ACTION_DISMISS,
    AUDIT_ACTION_RESTORE,
    AUDIT_ACTION_MARK_INCORPORATED,
)

#: API の action 語彙（``accept`` / ``dismiss`` / ``restore``）。
ACTION_ACCEPT = AUDIT_ACTION_ACCEPT
ACTION_DISMISS = AUDIT_ACTION_DISMISS
ACTION_RESTORE = AUDIT_ACTION_RESTORE

DECIDE_ACTIONS = (ACTION_ACCEPT, ACTION_DISMISS, ACTION_RESTORE)


# ---------------------------------------------------------------------------
# edge_key（無向・版非依存。migration 076 のコメントと同じ形式）
# ---------------------------------------------------------------------------

#: edge_key の名前空間プレフィックス（gap の cluster_key と混ざらないように）。
EDGE_KEY_PREFIX = "edge"

#: edge_key の区切り（骨格の node id は slug なので ``|`` を含まない）。
EDGE_KEY_SEPARATOR = "|"


def _clean(value: object) -> str:
    return str(value or "").strip()


def undirected_pair(node_a: str, node_b: str) -> tuple[str, str]:
    """無向ペアの正準形（id をソートした ``(min, max)``）。

    「A—B」と「B—A」を同じ判断・同じ候補として扱うための唯一の正規化点。
    空白は落とすが、それ以上の正規化（casefold 等）はしない — 骨格の node id は
    生成時に既に slug 化されており、大文字小文字の揺れは無いため。
    """
    left = _clean(node_a)
    right = _clean(node_b)
    return (left, right) if left <= right else (right, left)


def build_edge_key(domain_key: str, node_a: str, node_b: str) -> str:
    """判断テーブルの一意キー（**無向・版非依存**）。

    形式: ``edge|{domain_key}|{min(node_a, node_b)}|{max(node_a, node_b)}``

    ``skeleton_version`` を含めないのは gap の cluster_key と同じ理由（§4.2 裁定）:
    凍結のたびに却下済みの辺候補が蘇る「ゾンビ候補」を防ぐ。新版で辺が実際に張られた
    候補は、導出が既存辺を除外するので版キー無しでも自然に消える（RE8）。
    """
    left, right = undirected_pair(node_a, node_b)
    return EDGE_KEY_SEPARATOR.join(
        (EDGE_KEY_PREFIX, _clean(domain_key), left, right)
    )


def parse_edge_key(edge_key: str) -> tuple[str, str, str]:
    """edge_key を ``(domain_key, node_a, node_b)`` へ戻す。

    形が合わないキーは ``("", "", "")``（fail-safe。呼び出し側は表示を諦めて別の値へ
    縮退する — ``atlas_gaps.parse_cluster_key`` と同じ規約）。
    """
    parts = _clean(edge_key).split(EDGE_KEY_SEPARATOR)
    if len(parts) != 4 or parts[0] != EDGE_KEY_PREFIX:
        return ("", "", "")
    domain = parts[1]
    left, right = undirected_pair(parts[2], parts[3])
    if not left or not right:
        return ("", "", "")
    return (domain, left, right)


def edge_key_domain_prefix(domain_key: str) -> str:
    """``domain_key`` で判断行を絞るための edge_key 前方一致プレフィックス。

    ``atlas_edge_decisions`` は共同財行なので ``domain_key`` 列を持たない
    （キーに含まれている）。SQL 側は ``LIKE`` ではなく ``starts_with`` を使うこと
    （domain_key には ``_`` が含まれるため ``LIKE`` のワイルドカードと衝突する）。
    """
    return (
        EDGE_KEY_SEPARATOR.join((EDGE_KEY_PREFIX, _clean(domain_key)))
        + EDGE_KEY_SEPARATOR
    )


__all__ = [
    "ACTION_ACCEPT",
    "ACTION_DISMISS",
    "ACTION_RESTORE",
    "AUDIT_ACTIONS",
    "AUDIT_ACTION_ACCEPT",
    "AUDIT_ACTION_DISMISS",
    "AUDIT_ACTION_MARK_INCORPORATED",
    "AUDIT_ACTION_RESTORE",
    "DECIDE_ACTIONS",
    "DECISION_STATUSES",
    "DECISION_STATUS_ACCEPTED",
    "DECISION_STATUS_CANDIDATE",
    "DECISION_STATUS_DISMISSED",
    "DECISION_STATUS_LABELS",
    "EDGE_KEY_PREFIX",
    "EDGE_KEY_SEPARATOR",
    "EDGE_KIND_REQUIRED_STATUSES",
    "MIN_DOCUMENTS_FOR_EDGE",
    "ORIGINS",
    "ORIGIN_CO_OCCURRENCE",
    "ORIGIN_VECTOR",
    "REVIEW_NOTE_REQUIRED_STATUSES",
    "SUPPRESSED_DECISION_STATUSES",
    "THREADS_MAX_PER_NODE",
    "THREADS_MAX_TOTAL",
    "build_edge_key",
    "decision_status_label",
    "edge_key_domain_prefix",
    "is_valid_decision_status",
    "parse_edge_key",
    "undirected_pair",
]
