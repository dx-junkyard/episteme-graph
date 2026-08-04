"""知識ランドスケープ — 語彙とラベルの正本（設計書 §8）。

正本: ``docs/features/knowledge_landscape_design.md``（不変条項 LS1〜LS10 は §0）。

DB にも FastAPI にも LLM SDK にも依存しない純粋な語彙定義に留める
（``core/teaching_figures/schema.py`` / ``core/deliberation/schema.py`` と同じ立場）。

ここの語彙は **migration 065 の CHECK 制約と一対一で一致させること**
（ガードレールテスト ``backend/tests/test_landscape_guardrails.py`` が SQL 本文を
パースして完全一致を検査する）。

LS5「数値を見せない」: weight / confidence の生値を返す関数はここに置かない。
表示に出せるのは段階ラベル（:func:`weight_label`）と出所ラベル
（:func:`provenance_label`）・状態ラベル（:func:`status_label`）だけである。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 観点（perspective）— 1論文が複数領域へ異なる観点で配置されるのが既定（LS1）
# ---------------------------------------------------------------------------

PERSPECTIVE_SUBJECT = "subject"
PERSPECTIVE_QUESTION = "question"
PERSPECTIVE_METHOD = "method"
PERSPECTIVE_THEORY = "theory"
PERSPECTIVE_OBSERVATION = "observation"
PERSPECTIVE_APPLICATION = "application"

PERSPECTIVES = (
    PERSPECTIVE_SUBJECT,
    PERSPECTIVE_QUESTION,
    PERSPECTIVE_METHOD,
    PERSPECTIVE_THEORY,
    PERSPECTIVE_OBSERVATION,
    PERSPECTIVE_APPLICATION,
)

PERSPECTIVE_LABELS: dict[str, str] = {
    PERSPECTIVE_SUBJECT: "対象から",
    PERSPECTIVE_QUESTION: "問いから",
    PERSPECTIVE_METHOD: "方法から",
    PERSPECTIVE_THEORY: "理論から",
    PERSPECTIVE_OBSERVATION: "観測から",
    PERSPECTIVE_APPLICATION: "応用から",
}


def is_valid_perspective(value: str) -> bool:
    return value in PERSPECTIVES


def perspective_label(value: str) -> str:
    """観点の日本語表示名（未登録の値はそのまま返す）。"""
    return PERSPECTIVE_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 状態（status）— LS3: AI 配置は inferred 止まり、確定は教員のみ
# ---------------------------------------------------------------------------

#: AI（LLM / 決定論アルゴリズム）由来の候補。教員未確認。
STATUS_INFERRED = "inferred"
#: 教員が明示的に確認した配置。
STATUS_CONFIRMED = "confirmed"
#: 教員が却下した配置（行は保持する = P4。学習者には出さない）。
STATUS_REJECTED = "rejected"
#: 教員が再検討に回した配置（学習者には「確認待ち（AI推定）」で見える）。
STATUS_REVIEW_REQUIRED = "review_required"
#: 再解析で置き換えられた旧候補（履歴。学習者にも一覧の既定にも出さない）。
STATUS_SUPERSEDED = "superseded"

STATUSES = (
    STATUS_INFERRED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_SUPERSEDED,
)

#: 「生きている」状態（superseded 以外）。migration 065 の部分一意インデックス
#: ``uq_landscape_placements_live`` の条件と対応する。手動の status 遷移
#: （``store.update_status``）が許すのはこの集合の内側だけ。
LIVE_STATUSES = (
    STATUS_INFERRED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
)

#: 学習者に見せてよい状態（§4.2）。rejected / superseded は見えない。
LEARNER_VISIBLE_STATUSES = (
    STATUS_CONFIRMED,
    STATUS_INFERRED,
    STATUS_REVIEW_REQUIRED,
)

#: 再解析で ``superseded`` へ落として良い状態（LS3: 教員の判断は AI が上書きしない）。
SUPERSEDABLE_STATUSES = (STATUS_INFERRED,)

STATUS_LABELS: dict[str, str] = {
    STATUS_INFERRED: "AI推定（未確認）",
    STATUS_CONFIRMED: "教員確認済み",
    STATUS_REJECTED: "却下",
    STATUS_REVIEW_REQUIRED: "再検討中",
    STATUS_SUPERSEDED: "再解析により置換",
}


def is_valid_status(value: str) -> bool:
    return value in STATUSES


def status_label(value: str) -> str:
    """状態の日本語表示名（事実文のみ。督促・煽り表現を入れない — LS1/LS8）。"""
    return STATUS_LABELS.get(value, value)


# ---------------------------------------------------------------------------
# 出所ラベル（§4.2 / LS8 コーパスの正直さ・AC-005）
# ---------------------------------------------------------------------------

#: status → 学習者・教員の両画面で必ず併記する出所ラベル。
PROVENANCE_LABELS: dict[str, str] = {
    STATUS_CONFIRMED: "教員確認済み",
    STATUS_INFERRED: "AIによる推定（未確認）",
    STATUS_REVIEW_REQUIRED: "確認待ち（AI推定）",
}

#: 未知・非表示状態に対する既定ラベル。最も慎重な「AI推定」に倒す
#: （教員確認済みに見せる方向へは絶対に倒さない）。
DEFAULT_PROVENANCE_LABEL = PROVENANCE_LABELS[STATUS_INFERRED]


def provenance_label(status: str) -> str:
    """**status** から出所ラベルを引く（AC-005: AI推定と教員確認を必ず区別する）。

    引数が status なのは設計書 §8 の契約どおり — 表示上の「出所」は
    ``provenance``（llm/deterministic/human = 生成手段）ではなく
    「教員が確認したかどうか」で決まるため。未知の status は
    :data:`DEFAULT_PROVENANCE_LABEL`（AI推定側）へ倒す。
    """
    return PROVENANCE_LABELS.get(status, DEFAULT_PROVENANCE_LABEL)


# ---------------------------------------------------------------------------
# 生成手段（provenance 列）
# ---------------------------------------------------------------------------

PROVENANCE_LLM = "llm"
PROVENANCE_DETERMINISTIC = "deterministic"
PROVENANCE_HUMAN = "human"
PROVENANCES = (PROVENANCE_LLM, PROVENANCE_DETERMINISTIC, PROVENANCE_HUMAN)


def is_valid_provenance(value: str) -> bool:
    return value in PROVENANCES


# ---------------------------------------------------------------------------
# ノード種別（骨格の region / concept）
# ---------------------------------------------------------------------------

NODE_KIND_REGION = "region"
NODE_KIND_CONCEPT = "concept"
NODE_KINDS = (NODE_KIND_REGION, NODE_KIND_CONCEPT)


def is_valid_node_kind(value: str) -> bool:
    return value in NODE_KINDS


# ---------------------------------------------------------------------------
# 関連の段階ラベル（LS5: weight の生値を出さない）
# ---------------------------------------------------------------------------

WEIGHT_LEVEL_STRONG = "strong"
WEIGHT_LEVEL_MEDIUM = "medium"
WEIGHT_LEVEL_WEAK = "weak"
WEIGHT_LEVELS = (WEIGHT_LEVEL_STRONG, WEIGHT_LEVEL_MEDIUM, WEIGHT_LEVEL_WEAK)

WEIGHT_LABELS: dict[str, str] = {
    WEIGHT_LEVEL_STRONG: "強い関連",
    WEIGHT_LEVEL_MEDIUM: "関連",
    WEIGHT_LEVEL_WEAK: "弱い関連",
}

#: 段階の境界（設計書 §8: >=0.7 strong / >=0.4 medium / else weak）。
WEIGHT_THRESHOLD_STRONG = 0.7
WEIGHT_THRESHOLD_MEDIUM = 0.4

DEFAULT_WEIGHT = 0.5


def weight_level(value: object) -> str:
    """weight の生値を段階キー（strong / medium / weak）へ変換する。

    数値化できない・未測定（``None``）は最も慎重な ``weak`` に倒す
    （情報が無いことを強い関連に見せない）。
    """
    try:
        if value is None:
            return WEIGHT_LEVEL_WEAK
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return WEIGHT_LEVEL_WEAK
    if numeric >= WEIGHT_THRESHOLD_STRONG:
        return WEIGHT_LEVEL_STRONG
    if numeric >= WEIGHT_THRESHOLD_MEDIUM:
        return WEIGHT_LEVEL_MEDIUM
    return WEIGHT_LEVEL_WEAK


def weight_label(value: object) -> str:
    """weight の生値を段階ラベル（強い関連 / 関連 / 弱い関連）へ変換する（LS5）。"""
    return WEIGHT_LABELS[weight_level(value)]


def normalize_weight(value: object) -> float:
    """LLM / 呼び出し側の weight を ``[0.0, 1.0]`` の float に正規化する。

    数値化できない値は :data:`DEFAULT_WEIGHT`、範囲外は端へクランプする
    （DB の ``weight REAL NOT NULL DEFAULT 0.5`` と揃える。生値は DB 内部にしか
    出ないため、破棄よりも決定論的なクランプが扱いやすい）。
    """
    try:
        if value is None:
            return DEFAULT_WEIGHT
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


# ---------------------------------------------------------------------------
# その他の規約
# ---------------------------------------------------------------------------

#: パイプライン由来の created_by 既定値（教員の手動再提案は user_id を入れる）。
PIPELINE_CREATED_BY = "pipeline"

#: 学習者 DTO から必ず落とすキー（LS5。projection 側の実装が参照する）。
FORBIDDEN_NUMERIC_KEYS = ("weight", "confidence")


__all__ = [
    "DEFAULT_PROVENANCE_LABEL",
    "DEFAULT_WEIGHT",
    "FORBIDDEN_NUMERIC_KEYS",
    "LEARNER_VISIBLE_STATUSES",
    "LIVE_STATUSES",
    "NODE_KINDS",
    "NODE_KIND_CONCEPT",
    "NODE_KIND_REGION",
    "PERSPECTIVES",
    "PERSPECTIVE_APPLICATION",
    "PERSPECTIVE_LABELS",
    "PERSPECTIVE_METHOD",
    "PERSPECTIVE_OBSERVATION",
    "PERSPECTIVE_QUESTION",
    "PERSPECTIVE_SUBJECT",
    "PERSPECTIVE_THEORY",
    "PIPELINE_CREATED_BY",
    "PROVENANCES",
    "PROVENANCE_DETERMINISTIC",
    "PROVENANCE_HUMAN",
    "PROVENANCE_LABELS",
    "PROVENANCE_LLM",
    "STATUSES",
    "STATUS_CONFIRMED",
    "STATUS_INFERRED",
    "STATUS_LABELS",
    "STATUS_REJECTED",
    "STATUS_REVIEW_REQUIRED",
    "STATUS_SUPERSEDED",
    "SUPERSEDABLE_STATUSES",
    "WEIGHT_LABELS",
    "WEIGHT_LEVELS",
    "WEIGHT_LEVEL_MEDIUM",
    "WEIGHT_LEVEL_STRONG",
    "WEIGHT_LEVEL_WEAK",
    "WEIGHT_THRESHOLD_MEDIUM",
    "WEIGHT_THRESHOLD_STRONG",
    "is_valid_node_kind",
    "is_valid_perspective",
    "is_valid_provenance",
    "is_valid_status",
    "normalize_weight",
    "perspective_label",
    "provenance_label",
    "status_label",
    "weight_label",
    "weight_level",
]
