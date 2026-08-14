"""教材図スタジオ — 語彙の正本（設計書 §5.2 / §3.1 / §3.2）。

DB にも FastAPI にも LLM SDK にも依存しない純粋な語彙定義に留める
（``core/deliberation/schema.py`` と同じ立場）。

図タイプ語彙（:data:`FIGURE_KINDS`）は **生成図の意図分類** であり、既存の
``src/episteme_graph/agents/figure_modes.py::FIGURE_MODES``（PDF から抽出した図の
提示モード）とは別レイヤーの別体系である（設計書 §5.2 の注記。意図的に語を重ねない）。
"""

from __future__ import annotations

import uuid

from core.label_vocab import (
    CONFIDENCE_LABEL_HIGH,
    CONFIDENCE_LABEL_LOW,
    CONFIDENCE_LABEL_MEDIUM,
    CONFIDENCE_LABELS_LOW_MED_HIGH,
    CONFIDENCE_LOW_MED_HIGH,
)

# ── 図タイプ（domain-neutral。設計書 §5.2 の表がこの語彙の説明）───────────────
FIGURE_KIND_CONCEPT_MAP = "concept_map"
FIGURE_KIND_PROCESS_FLOW = "process_flow"
FIGURE_KIND_STRUCTURE_DIAGRAM = "structure_diagram"
FIGURE_KIND_COMPARISON = "comparison"
FIGURE_KIND_TIMELINE = "timeline"
FIGURE_KIND_COORDINATE = "coordinate"
FIGURE_KIND_DATA_PLOT_SCHEMATIC = "data_plot_schematic"
FIGURE_KIND_OTHER = "other"

# migration 063 の course_teaching_figures.figure_kind /
# teaching_figure_suggestions.figure_kind の CHECK 制約と一致させること。
FIGURE_KINDS = (
    FIGURE_KIND_CONCEPT_MAP,
    FIGURE_KIND_PROCESS_FLOW,
    FIGURE_KIND_STRUCTURE_DIAGRAM,
    FIGURE_KIND_COMPARISON,
    FIGURE_KIND_TIMELINE,
    FIGURE_KIND_COORDINATE,
    FIGURE_KIND_DATA_PLOT_SCHEMATIC,
    FIGURE_KIND_OTHER,
)

FIGURE_KIND_LABELS: dict[str, str] = {
    FIGURE_KIND_CONCEPT_MAP: "概念関係図",
    FIGURE_KIND_PROCESS_FLOW: "プロセス図",
    FIGURE_KIND_STRUCTURE_DIAGRAM: "構造・構成図",
    FIGURE_KIND_COMPARISON: "対比図",
    FIGURE_KIND_TIMELINE: "時系列図",
    FIGURE_KIND_COORDINATE: "座標・幾何図",
    FIGURE_KIND_DATA_PLOT_SCHEMATIC: "模式グラフ",
    FIGURE_KIND_OTHER: "その他",
}

# 「何をどう描くか」の想定ギャップ（設計書 §5.2 の表の右列）。提案プロンプトの
# 語彙説明に使う（UI 表示はフロント側の責務）。
FIGURE_KIND_GAP_HINTS: dict[str, str] = {
    FIGURE_KIND_CONCEPT_MAP: "用語間の関係が文章で追いづらい",
    FIGURE_KIND_PROCESS_FLOW: "手順・因果の連鎖が長い",
    FIGURE_KIND_STRUCTURE_DIAGRAM: "系の構成要素と接続が想像しづらい",
    FIGURE_KIND_COMPARISON: "2つ以上の場合分け・比較が入り組む",
    FIGURE_KIND_TIMELINE: "順序・発展段階の把握",
    FIGURE_KIND_COORDINATE: "空間配置・ベクトル・角度",
    FIGURE_KIND_DATA_PLOT_SCHEMATIC: "傾向・依存関係の直観（実データは描かない）",
    FIGURE_KIND_OTHER: "上記に収まらない（理由必須）",
}

DEFAULT_FIGURE_KIND = FIGURE_KIND_CONCEPT_MAP


def is_valid_figure_kind(value: str) -> bool:
    """図タイプ語彙に含まれるかを返す（語彙外は候補から捨てる・保存を拒否する）。"""
    return value in FIGURE_KINDS


def figure_kind_label(value: str) -> str:
    """図タイプの日本語表示名（未登録の値はそのまま返す）。"""
    return FIGURE_KIND_LABELS.get(value, value)


# ── FG5: 模式グラフの caption に「模式図」表記を強制付与する ────────────────────
#: ``data_plot_schematic`` の caption に必須の語（実データのプロットと誤読させない）。
SCHEMATIC_CAPTION_MARKER = "模式図"


def enforce_schematic_caption(caption: str, figure_kind: str) -> str:
    """``data_plot_schematic`` の caption に「模式図」表記を付与して返す（FG5）。

    生成図の ``data_plot_schematic`` は**実測値を描かない**（プロンプト側の
    ``DATA_PLOT_CONSTRAINT`` で捏造を禁止している）。学習者が実データのグラフと
    誤読しないよう、キャプションでも必ず模式であることを示す。教員が既に
    「模式図」と書いていれば二重に付けない。

    ``figure_kind`` が他の語彙なら caption はそのまま返す（教員の文面に触らない）。
    """
    original = str(caption or "")
    if figure_kind != FIGURE_KIND_DATA_PLOT_SCHEMATIC:
        return original
    text = original.strip()
    if SCHEMATIC_CAPTION_MARKER in text:
        return original
    if not text:
        # 空 caption に「（模式図）」だけを付けると括弧が浮くので語だけを入れる。
        return SCHEMATIC_CAPTION_MARKER
    return f"{text}（{SCHEMATIC_CAPTION_MARKER}）"


# ── 生成図の状態（FG7: 削除しない。行削除 API は作らない）────────────────────
# draft   = 保存済みだが未採用（教員のストック。挿入タブに出る・学習者非配信）
# adopted = 配信対象（学習者配信ゲートの条件4）
# retired = 回収（本文の embed は消さない。配信だけ止める）
FIGURE_STATUS_DRAFT = "draft"
FIGURE_STATUS_ADOPTED = "adopted"
FIGURE_STATUS_RETIRED = "retired"
FIGURE_STATUSES = (FIGURE_STATUS_DRAFT, FIGURE_STATUS_ADOPTED, FIGURE_STATUS_RETIRED)


def is_valid_figure_status(value: str) -> bool:
    return value in FIGURE_STATUSES


# ── ギャップ候補の状態（FG2/FG7: AI 出力は常に candidate。却下も保持）──────────
SUGGESTION_STATUS_CANDIDATE = "candidate"
SUGGESTION_STATUS_ACCEPTED = "accepted"
SUGGESTION_STATUS_DISMISSED = "dismissed"
SUGGESTION_STATUS_SUPERSEDED = "superseded"
SUGGESTION_STATUSES = (
    SUGGESTION_STATUS_CANDIDATE,
    SUGGESTION_STATUS_ACCEPTED,
    SUGGESTION_STATUS_DISMISSED,
    SUGGESTION_STATUS_SUPERSEDED,
)
# candidate からのみ辿り着く終端状態（``store.set_suggestion_status`` が受理する遷移先。
# candidate へ戻す遷移は無い）。
SUGGESTION_DECIDABLE_STATUSES = (
    SUGGESTION_STATUS_ACCEPTED,
    SUGGESTION_STATUS_DISMISSED,
    SUGGESTION_STATUS_SUPERSEDED,
)
# 一覧の既定（教員が今見るべきもの。dismissed / superseded は保持するが既定では出さない）。
SUGGESTION_VISIBLE_STATUSES = (SUGGESTION_STATUS_CANDIDATE, SUGGESTION_STATUS_ACCEPTED)


def is_valid_suggestion_status(value: str) -> bool:
    return value in SUGGESTION_STATUSES


# ── ギャップ検出の根拠（migration 063 の signal_basis CHECK と一致）─────────────
SIGNAL_BASIS_TEXT_ANALYSIS = "text_analysis"
SIGNAL_BASIS_LEARNER_SIGNALS = "learner_signals"
SIGNAL_BASIS_BOTH = "both"
SIGNAL_BASES = (
    SIGNAL_BASIS_TEXT_ANALYSIS,
    SIGNAL_BASIS_LEARNER_SIGNALS,
    SIGNAL_BASIS_BOTH,
)


def is_valid_signal_basis(value: str) -> bool:
    return value in SIGNAL_BASES


# ── confidence の段階ラベル（FG8: 生値を出さない）─────────────────────────────
# **生値を返す関数はこのモジュールに置かない**。API 応答へ confidence を載せる経路は
# 段階ラベルのみを通す（store.list_suggestions が変換の責務を負う）。
# 段階の境界・語彙・「未測定は最も慎重な段階へ倒す」規則の正本は core/label_vocab.py。
CONFIDENCE_LABELS = CONFIDENCE_LABELS_LOW_MED_HIGH


def confidence_label(value: float | None) -> str:
    """confidence の生値を段階ラベル（低 / 中 / 高）へ変換する（FG8）。

    未測定（``None``）・数値化できない値は最も慎重な「低」に倒す
    （情報が無いことを高確度に見せない。``core/deliberation/identity_links.py::
    confidence_label`` と同じ思想）。
    """
    return CONFIDENCE_LOW_MED_HIGH.label_for(value)


def normalize_confidence(value: object) -> float | None:
    """LLM 出力の confidence を ``[0.0, 1.0]`` の float か ``None`` に正規化する。

    範囲外・数値化不能は ``None``（= 未測定）にする。捏造した高確度を通さないため、
    クリップではなく破棄に倒す。
    """
    try:
        if value is None:
            return None
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numeric < 0.0 or numeric > 1.0:
        return None
    return numeric


# MinIO 配信スナップショットのキー規約（既存の抽出図 ``figures/{document_id}/...`` と
# prefix で衝突させない。設計書 §3.1）。
FIGURE_IMAGES_BUCKET = "figure-images"
SVG_CONTENT_TYPE = "image/svg+xml"


def minio_key_for(course_id: str, figure_id: str) -> str:
    """生成図の MinIO オブジェクトキー ``teaching/{course_id}/{figure_id}.svg``。"""
    return f"teaching/{course_id}/{figure_id}.svg"


def new_figure_id() -> str:
    """生成図の id を先に決めるための UUID 文字列。

    MinIO キーが id 依存（:func:`minio_key_for`）なので、「キーを決める → 画像を上げる →
    行を作る」の順に進めたい呼び出し側が使う（``store.create_teaching_figure`` の
    ``figure_id`` 引数へ渡す）。省略すれば DB 既定の ``gen_random_uuid()`` が採番する。
    """
    return str(uuid.uuid4())


__all__ = [
    "CONFIDENCE_LABELS",
    "CONFIDENCE_LABEL_HIGH",
    "CONFIDENCE_LABEL_LOW",
    "CONFIDENCE_LABEL_MEDIUM",
    "DEFAULT_FIGURE_KIND",
    "FIGURE_IMAGES_BUCKET",
    "FIGURE_KINDS",
    "FIGURE_KIND_GAP_HINTS",
    "FIGURE_KIND_LABELS",
    "FIGURE_KIND_COMPARISON",
    "FIGURE_KIND_CONCEPT_MAP",
    "FIGURE_KIND_COORDINATE",
    "FIGURE_KIND_DATA_PLOT_SCHEMATIC",
    "FIGURE_KIND_OTHER",
    "FIGURE_KIND_PROCESS_FLOW",
    "FIGURE_KIND_STRUCTURE_DIAGRAM",
    "FIGURE_KIND_TIMELINE",
    "FIGURE_STATUSES",
    "FIGURE_STATUS_ADOPTED",
    "FIGURE_STATUS_DRAFT",
    "FIGURE_STATUS_RETIRED",
    "SCHEMATIC_CAPTION_MARKER",
    "SIGNAL_BASES",
    "SIGNAL_BASIS_BOTH",
    "SIGNAL_BASIS_LEARNER_SIGNALS",
    "SIGNAL_BASIS_TEXT_ANALYSIS",
    "SUGGESTION_DECIDABLE_STATUSES",
    "SUGGESTION_STATUSES",
    "SUGGESTION_STATUS_ACCEPTED",
    "SUGGESTION_STATUS_CANDIDATE",
    "SUGGESTION_STATUS_DISMISSED",
    "SUGGESTION_STATUS_SUPERSEDED",
    "SUGGESTION_VISIBLE_STATUSES",
    "SVG_CONTENT_TYPE",
    "confidence_label",
    "enforce_schematic_caption",
    "figure_kind_label",
    "is_valid_figure_kind",
    "is_valid_figure_status",
    "is_valid_signal_basis",
    "is_valid_suggestion_status",
    "minio_key_for",
    "new_figure_id",
    "normalize_confidence",
]
