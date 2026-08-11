"""カテゴリギャップ候補 — 語彙とキー導出の正本（設計書 §5.2）。

正本: ``docs/features/category_gap_candidates_design.md``（裁定は §4、不変条項の
写像は §2）。

DB にも FastAPI にも LLM SDK にも依存しない純粋な語彙定義・純粋関数だけを置く
（``core/landscape/schema.py`` / ``core/teaching_figures/schema.py`` と同じ立場）。

ここの語彙は **migration 066 の CHECK 制約と一対一で一致させること**
（``backend/tests/test_atlas_gaps_guardrails.py`` が SQL 本文をパースして完全一致を
検査する）。

LS5 / PN-4「数値を見せない」: ``confidence`` の生値を返す関数はここに置かない。
表示に出せるのは段階ラベル（:func:`confidence_label`）と層ラベル
（:func:`layer_label`）・状態ラベル（:func:`decision_status_label`）だけである。
支持論文も件数ではなくタイトル列挙で示すため、件数の導出関数も置かない。
"""

from __future__ import annotations

import unicodedata

# ---------------------------------------------------------------------------
# 層（layer）— 地図の上位=領域 / 下位=概念（設計書 §5.1）
# ---------------------------------------------------------------------------

GAP_LAYER_REGION = "region"
GAP_LAYER_CONCEPT = "concept"

GAP_LAYERS = (GAP_LAYER_REGION, GAP_LAYER_CONCEPT)

GAP_LAYER_LABELS: dict[str, str] = {
    GAP_LAYER_REGION: "領域",
    GAP_LAYER_CONCEPT: "概念",
}


def is_valid_layer(value: str) -> bool:
    return value in GAP_LAYERS


def layer_label(value: str) -> str:
    """層の日本語表示名（未登録の値はそのまま返す）。"""
    return GAP_LAYER_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 信号の状態（landscape_gap_signals.status）— LS3 同型の再解析セマンティクス
# ---------------------------------------------------------------------------

#: 生きている信号（読み時導出の母集団）。
SIGNAL_STATUS_ACTIVE = "active"
#: 再解析で置き換えられた旧信号（履歴。行は消さない = P4）。
SIGNAL_STATUS_SUPERSEDED = "superseded"

SIGNAL_STATUSES = (SIGNAL_STATUS_ACTIVE, SIGNAL_STATUS_SUPERSEDED)

#: 再解析で ``superseded`` へ落として良い状態（``core/landscape/schema.py`` の
#: ``SUPERSEDABLE_STATUSES`` と同じ思想。教員の判断は別テーブルなので、信号側は
#: AI 由来の active だけを置き換える）。
SUPERSEDABLE_SIGNAL_STATUSES = (SIGNAL_STATUS_ACTIVE,)


def is_valid_signal_status(value: str) -> bool:
    return value in SIGNAL_STATUSES


# ---------------------------------------------------------------------------
# 教員の判断（atlas_gap_decisions.status）— 確定は人間のみ（KN-3 / AB4）
# ---------------------------------------------------------------------------

#: まだ判断していない / 見送りを取り消した（restore の着地点）。
#:
#: 設計書 §5.2 の CHECK 語彙は ('accepted','dismissed','merged') だが、見送りを
#: 「見送り済みフィルタから戻す」restore（§5.4）を**行削除なしで**実現するには
#: 判断を取り消した状態を表す語彙が1つ必要になる（AB3 / P4）。意図的な最小逸脱。
DECISION_STATUS_CANDIDATE = "candidate"
#: 「カテゴリとして妥当」の判断のみ（骨格 draft はまだ変わらない）。
DECISION_STATUS_ACCEPTED = "accepted"
#: 見送り（理由必須。同名の候補は以降レビューキューに出さない）。
DECISION_STATUS_DISMISSED = "dismissed"
#: 別の候補へ統合した（``merged_into`` に統合先 cluster_key）。
DECISION_STATUS_MERGED = "merged"

DECISION_STATUSES = (
    DECISION_STATUS_CANDIDATE,
    DECISION_STATUS_ACCEPTED,
    DECISION_STATUS_DISMISSED,
    DECISION_STATUS_MERGED,
)

DECISION_STATUS_LABELS: dict[str, str] = {
    DECISION_STATUS_CANDIDATE: "未判断",
    DECISION_STATUS_ACCEPTED: "採用",
    DECISION_STATUS_DISMISSED: "見送り",
    DECISION_STATUS_MERGED: "統合済み",
}

#: レビューキューの既定から外す状態（``include_dismissed=True`` で戻せる）。
#: ``merged`` も統合先の候補で代表されるため既定では出さない。
SUPPRESSED_DECISION_STATUSES = (DECISION_STATUS_DISMISSED, DECISION_STATUS_MERGED)

#: 理由（``review_note``）が必須の遷移先（空は ``ValueError`` → route が 422）。
REVIEW_NOTE_REQUIRED_STATUSES = (DECISION_STATUS_DISMISSED,)

#: ``merged_into`` が必須の遷移先（統合先の無い統合は作らない）。
MERGED_INTO_REQUIRED_STATUSES = (DECISION_STATUS_MERGED,)


def is_valid_decision_status(value: str) -> bool:
    return value in DECISION_STATUSES


def decision_status_label(value: str) -> str:
    """判断状態の日本語表示名（事実文のみ。督促・煽り表現を入れない — LS1）。"""
    return DECISION_STATUS_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 反復閾値（§4.1 裁定）
# ---------------------------------------------------------------------------

#: 同一 cluster に distinct document がこの数以上あるときだけレビューキューへ浮上させる。
#: 信号自体は1論文目から保存する（P4）。D層 ``assumption_mining`` の
#: ``MIN_DOCUMENTS_FOR_CANDIDATE`` と同じ思想・同じ値（1論文の主題は分野のカテゴリでは
#: ないという判断。AB2 共同財の保護）。
MIN_DOCUMENTS_FOR_CANDIDATE = 2


# ---------------------------------------------------------------------------
# ラベル正規化と cluster_key（§4.2 裁定: 版非依存キー）
# ---------------------------------------------------------------------------

#: cluster_key の名前空間プレフィックス（他の cluster_key 系と混ざらないように）。
CLUSTER_KEY_PREFIX = "gap"

#: cluster_key の区切り（ラベル中に現れても壊れないよう、区切りは正規化で潰さない）。
CLUSTER_KEY_SEPARATOR = "|"


def normalize_label(label: str) -> str:
    """ラベル比較用の正規化（決定論・純粋関数）。

    手順は次の4段で固定する:

    1. Unicode NFKC 正規化（全角英数・互換文字を統一する）
    2. ``casefold()``（Unicode 対応の小文字化）
    3. 連続する任意の空白（全角空白・タブ・改行を含む）を**半角スペース1個**へ畳む
    4. 前後の空白を落とす

    この関数の用途は2つあり、**どちらも同じ規則でなければならない**:

    - :func:`build_cluster_key` の構成要素（同一主題の信号を論文横断で束ねる）
    - 現行凍結骨格の region / concept ラベル・id との突合（解消済み候補の除外）

    ``core/atlas.py::normalize_label`` とは意図的に別実装である（あちらは
    topic ⇄ concept のあいまい照合用に空白と「・」を全部落とす。こちらは
    「Cosmic  Web」と「cosmic web」を同一視しつつ「宇宙論・大規模構造」の
    区切りは保つ）。
    """
    normalized = unicodedata.normalize("NFKC", str(label or "")).casefold()
    return " ".join(normalized.split())


def build_cluster_key(domain_key: str, parent_region_id: str, proposed_label: str) -> str:
    """判断テーブルの一意キー（**版非依存**。§4.2 裁定）。

    形式: ``gap|{domain_key}|{parent_region_id}|{normalize_label(proposed_label)}``

    ``skeleton_version`` を含めないのは、凍結のたびに却下済み候補が蘇る
    「ゾンビ候補」を防ぐため（同じ判断を版ごとに要求しない = G4）。新版で解消された
    候補の自然消滅は、読み時に現行凍結版のラベル集合と突合すれば版キー無しで
    実現できる（:func:`core.atlas_gaps.store.derive_candidates`）。
    """
    return CLUSTER_KEY_SEPARATOR.join(
        (
            CLUSTER_KEY_PREFIX,
            str(domain_key or "").strip(),
            str(parent_region_id or "").strip(),
            normalize_label(proposed_label),
        )
    )


def parse_cluster_key(cluster_key: str) -> tuple[str, str, str]:
    """cluster_key を ``(domain_key, parent_region_id, normalized_label)`` へ戻す。

    ラベル側に区切り文字が含まれていても壊れないよう ``maxsplit=3`` で分解する
    （ラベルは最後の要素なので、残り全部がラベル）。形が合わないキーは
    ``("", "", "")`` を返す（fail-safe。呼び出し側は表示を諦めて別の値へ縮退する）。
    """
    parts = str(cluster_key or "").split(CLUSTER_KEY_SEPARATOR, 3)
    if len(parts) != 4 or parts[0] != CLUSTER_KEY_PREFIX:
        return ("", "", "")
    return (parts[1], parts[2], parts[3])


def cluster_key_domain_prefix(domain_key: str) -> str:
    """``domain_key`` で判断行を絞るための cluster_key 前方一致プレフィックス。

    ``atlas_gap_decisions`` は共同財行なので ``domain_key`` 列を持たない
    （キーに含まれている）。SQL 側は ``LIKE`` ではなく ``starts_with`` を使うこと
    （domain_key には ``_`` が含まれるため ``LIKE`` のワイルドカードと衝突する）。
    """
    return (
        CLUSTER_KEY_SEPARATOR.join(
            (CLUSTER_KEY_PREFIX, str(domain_key or "").strip())
        )
        + CLUSTER_KEY_SEPARATOR
    )


# ---------------------------------------------------------------------------
# confidence の段階ラベル（LS5: 生値を出さない）
# ---------------------------------------------------------------------------
# ``core/landscape/schema.py`` は weight の段階ラベルしか持たない（confidence の
# 変換関数が無い）ため、``core/deliberation/identity_links.py`` /
# ``core/teaching_figures/schema.py`` と**同型**の実装をここに置く。

CONFIDENCE_LABEL_LOW = "低"
CONFIDENCE_LABEL_MEDIUM = "中"
CONFIDENCE_LABEL_HIGH = "高"
CONFIDENCE_LABELS = (
    CONFIDENCE_LABEL_LOW,
    CONFIDENCE_LABEL_MEDIUM,
    CONFIDENCE_LABEL_HIGH,
)

CONFIDENCE_THRESHOLD_HIGH = 0.75
CONFIDENCE_THRESHOLD_MEDIUM = 0.5


def confidence_label(value: object) -> str:
    """confidence の生値を段階ラベル（低 / 中 / 高）へ変換する（LS5）。

    未測定（``None``）・数値化できない値は最も慎重な「低」に倒す
    （情報が無いことを高確度に見せない）。
    """
    try:
        if value is None:
            return CONFIDENCE_LABEL_LOW
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return CONFIDENCE_LABEL_LOW
    if numeric >= CONFIDENCE_THRESHOLD_HIGH:
        return CONFIDENCE_LABEL_HIGH
    if numeric >= CONFIDENCE_THRESHOLD_MEDIUM:
        return CONFIDENCE_LABEL_MEDIUM
    return CONFIDENCE_LABEL_LOW


def normalize_confidence(value: object) -> float | None:
    """LLM / 呼び出し側の confidence を ``[0.0, 1.0]`` へ正規化する（None 許容）。

    数値化できない値は ``None``（未測定）として保持する — 0.0 に丸めると
    「測っていない」と「低い」が区別できなくなる（P4 情報を落とさない）。
    """
    if value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


# ---------------------------------------------------------------------------
# その他の規約
# ---------------------------------------------------------------------------

#: 教員向け・学習者向けの DTO から必ず落とすキー（LS5。route / 投影側が参照する）。
FORBIDDEN_NUMERIC_KEYS = ("confidence", "weight")

#: 監査 metadata の action 語彙（設計書 §5.7）。
AUDIT_ACTION_DETECT = "detect"
AUDIT_ACTION_ACCEPT = "accept"
AUDIT_ACTION_DISMISS = "dismiss"
AUDIT_ACTION_RESTORE = "restore"
AUDIT_ACTION_MERGE = "merge"
AUDIT_ACTION_INCORPORATE = "incorporate"

AUDIT_ACTIONS = (
    AUDIT_ACTION_DETECT,
    AUDIT_ACTION_ACCEPT,
    AUDIT_ACTION_DISMISS,
    AUDIT_ACTION_RESTORE,
    AUDIT_ACTION_MERGE,
    AUDIT_ACTION_INCORPORATE,
)

#: 判断状態 → 監査 action（route / store がそのまま使える対応表）。
DECISION_STATUS_AUDIT_ACTIONS: dict[str, str] = {
    DECISION_STATUS_CANDIDATE: AUDIT_ACTION_RESTORE,
    DECISION_STATUS_ACCEPTED: AUDIT_ACTION_ACCEPT,
    DECISION_STATUS_DISMISSED: AUDIT_ACTION_DISMISS,
    DECISION_STATUS_MERGED: AUDIT_ACTION_MERGE,
}


def audit_action_for_status(status: str) -> str:
    """判断状態に対応する監査 action（未知は ``''`` を返す）。"""
    return DECISION_STATUS_AUDIT_ACTIONS.get(status, "")


__all__ = [
    "AUDIT_ACTIONS",
    "AUDIT_ACTION_ACCEPT",
    "AUDIT_ACTION_DETECT",
    "AUDIT_ACTION_DISMISS",
    "AUDIT_ACTION_INCORPORATE",
    "AUDIT_ACTION_MERGE",
    "AUDIT_ACTION_RESTORE",
    "CLUSTER_KEY_PREFIX",
    "CLUSTER_KEY_SEPARATOR",
    "CONFIDENCE_LABELS",
    "CONFIDENCE_LABEL_HIGH",
    "CONFIDENCE_LABEL_LOW",
    "CONFIDENCE_LABEL_MEDIUM",
    "CONFIDENCE_THRESHOLD_HIGH",
    "CONFIDENCE_THRESHOLD_MEDIUM",
    "DECISION_STATUSES",
    "DECISION_STATUS_ACCEPTED",
    "DECISION_STATUS_AUDIT_ACTIONS",
    "DECISION_STATUS_CANDIDATE",
    "DECISION_STATUS_DISMISSED",
    "DECISION_STATUS_LABELS",
    "DECISION_STATUS_MERGED",
    "FORBIDDEN_NUMERIC_KEYS",
    "GAP_LAYERS",
    "GAP_LAYER_CONCEPT",
    "GAP_LAYER_LABELS",
    "GAP_LAYER_REGION",
    "MERGED_INTO_REQUIRED_STATUSES",
    "MIN_DOCUMENTS_FOR_CANDIDATE",
    "REVIEW_NOTE_REQUIRED_STATUSES",
    "SIGNAL_STATUSES",
    "SIGNAL_STATUS_ACTIVE",
    "SIGNAL_STATUS_SUPERSEDED",
    "SUPERSEDABLE_SIGNAL_STATUSES",
    "SUPPRESSED_DECISION_STATUSES",
    "audit_action_for_status",
    "build_cluster_key",
    "cluster_key_domain_prefix",
    "confidence_label",
    "decision_status_label",
    "is_valid_decision_status",
    "is_valid_layer",
    "is_valid_signal_status",
    "layer_label",
    "normalize_confidence",
    "normalize_label",
    "parse_cluster_key",
]
