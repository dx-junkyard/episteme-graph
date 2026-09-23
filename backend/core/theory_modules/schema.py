"""理論モジュール層 — 定数・事実文・表示ラベルの正本（純データ + 純関数）。

設計: ``docs/features/theory_module_layer_design.md``（TM1〜TM10・§5 導出規則・§8.1 DTO）。

このモジュールは FastAPI / sqlalchemy / LLM / embedding を import しない。
表示に関わる規律は3つ:

- TM6 数値非表示: 閾値 k・接点の本数・工程の型の多重度・構造の指紋・モジュールの数を
  DTO に載せない。``FORBIDDEN_KEYS`` をガードレールが DTO 全体に再帰走査する。
- TM10 内部 ID 非表示: 式は印字番号（「式 (12)」）か「番号なし: 要約の先頭」で示し、
  ``eq_tex_*`` / ``eq_op_*`` / ``theory_op_*`` をラベルに使わない。
- TM5 分野中立: ラベルの語は ``core/element_vocab.py`` の既存訳語表と論文データからだけ
  取る。新しい訳語表をここに作らない。

**閾値はコード定数であって環境変数から読まない**（§5.4 / SA7 と同じ扱い）。値を変える
ときは設計書 §3 に実測を足して変え、規則を変えたら ``RULE_VERSION`` を上げる。

モジュールの読み時キー ``module_key``（§5.7）について: 本来の材料は「成員 step が生む式の
**equation stable_key**（KO2・内容由来）の昇順列」である。Phase 0 の入力（採用 run の
artifact）には式の stable_key が載っていないため、**Phase 0 は ``equation_id`` の昇順列を
材料にする**。Phase 1 で理論モジュールを knowledge object として保存する（設計書 §9 O-1 (c)）
ときに、式の stable_key（``knowledge_equations`` の ``stable_key``）に差し替える。差し替えは
規則の変更なので ``RULE_VERSION`` を上げる（Phase 0 は保存しないので旧キーとの対応は取らない）。
"""

from __future__ import annotations

import re
from typing import Any

from core.text_hygiene import strip_control_sequences

# ---------------------------------------------------------------------------
# 規則の版と閾値（env から読まない）
# ---------------------------------------------------------------------------

#: 導出規則の版（§5.7）。§5.2〜5.5 の規則や閾値を変えたら上げる。
RULE_VERSION = "m1"

#: 外枠モジュールの接点の上限 k（§5.4）。
OUTER_INTERFACE_LIMIT = 3

#: 内側モジュール（外枠の再分割）の接点の上限 k（§5.4）。
INNER_INTERFACE_LIMIT = 2

#: 共有の基礎とみなす「消費する成員 step の数」の下限（§5.3。入次数は問わない）。
SHARED_FOUNDATION_MIN_CONSUMERS = 4

# ---------------------------------------------------------------------------
# 語彙（DTO の列挙値）
# ---------------------------------------------------------------------------

LEVEL_OUTER = "outer"
LEVEL_INNER = "inner"

ISOLATED_CYCLE = "cycle"
ISOLATED_INTERFACE_TOO_WIDE = "interface_too_wide"

#: 成員にする step を持つチェーンの種別（§5.2-1）。
MEMBER_CHAIN_TYPES: tuple[str, ...] = ("equation_chain", "mixed_chain")
#: 成員にせず sink として持つチェーンの種別（§5.2-3）。
SINK_CHAIN_TYPES: tuple[str, ...] = ("system_level",)
#: 主張の列（依存ではない。§5.2-2 / §7）。
CLAIM_CHAIN_TYPES: tuple[str, ...] = ("claim_chain",)

#: 詳細ノードとして読む graph_layer（main は集約なので step と対応付けない）。
DETAIL_GRAPH_LAYERS: tuple[str, ...] = ("equation_detail", "debug")
GRAPH_LAYER_DEBUG = "debug"

#: 裏付けの強さの順（弱いほど後ろ）。モジュールの裏付けは成員の最も弱いもの（§5.6）。
BACKING_ORDER: tuple[str, ...] = (
    "source_backed",
    "partially_source_backed",
    "review_required",
)
BACKING_INFERRED = "inferred"

#: 本文スニペットの上限（論文層の reference index と同じ 200 字）。
TEXT_SNIPPET_MAX = 200
#: 「番号なし: 要約の先頭」の上限（TM10）。
LABEL_SNIPPET_MAX = 40
#: 理論対象の上限（normalizer の ``_theory_object`` と同じ 120 字）。
THEORY_OBJECT_MAX = 120
#: 理論対象に印字番号を並べるときの上限。
THEORY_OBJECT_LABELS_MAX = 3

#: TM6: DTO に出してはならないキー（ガードレールが再帰走査する）。
FORBIDDEN_KEYS: tuple[str, ...] = (
    "confidence",
    "weight",
    "score",
    "candidate_score",
    "fingerprint",
    "structure_fingerprint",
    "interface_width",
    "interface_size",
    "k",
    "limit",
    "count",
    "counts",
    "module_count",
    "member_count",
    "consumer_count",
    "multiplicity",
)

#: TM10: 表示ラベル・本文に出してはならない内部 ID の形（テスト兼用）。
INTERNAL_ID_RE = re.compile(
    r"\b(?:eq_op|theory_op|eq|synth_claim|claim_span|claim|ev|comp|derivation|system_derivation)_[0-9A-Za-z_.:\-]*[0-9A-Za-z]",
)
#: 式 ID の形（本文中の式 ID を印字番号へ置き換えるための抽出）。
EQUATION_ID_TOKEN_RE = re.compile(r"\beq_[0-9A-Za-z_.\-]*[0-9A-Za-z]")

# ---------------------------------------------------------------------------
# 事実文（TM8）。語彙表ではなく固定文として個別に置く。
# ---------------------------------------------------------------------------

FACT_NO_DERIVATIONS = "導出の解析結果が無いため、理論モジュールを組めません。"
FACT_NO_EQUATION_STEPS = "この教材では式の導出が再現されていないため、理論モジュールを組めません。"
FACT_CLAIM_CHAINS_ONLY = (
    "導出の解析結果は主張の並びだけで、式の手順を含みません。主張の並びは「論文の順」で見られます。"
)
FACT_NO_EQUATIONS = "式の解析結果が無いため、式は印字番号ではなく「番号なし」で示します。"
FACT_NO_CLAIMS = "主張の解析結果が無いため、理論対象は式の印字番号で示します。"
FACT_NO_COMPONENTS = "コンポーネントの解析結果が無いため、部品との照合は表示できません。"
FACT_NO_GRAPH = "理論操作グラフが無いため、式の詳細ノードとの対応と裏付けは表示できません。"
FACT_INFERRED_STEPS_EXCLUDED = "推定扱いの手順は理論モジュールに含めていません。"
#: 循環（§5.5 の読み替え = 実装記録 §12 の逸脱 1）。末尾に式の表示ラベルを「、」で並べる。
FACT_CYCLE_PREFIX = "導出のつながりに循環があるため、次の式を含む手順はひとまとまりとして扱っています: "
#: 接点が単独で上限を超え、隣と併合できなかった手順（§5.5）。
FACT_INTERFACE_TOO_WIDE = "受け渡す式が多く、隣の手順とまとめられなかった手順を単独のモジュールとして残しています。"
FACT_SINKS = "論文全体をまとめる手順は、モジュールではなく結果の吸い込み口として別に示します。"
FACT_BUILD_FAILED = "理論モジュールの導出に失敗したため表示できません。"

# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------


def clean_text(value: Any) -> str:
    """制御文字を除き、前後の空白を落とす。"""
    return strip_control_sequences(str(value or "")).strip()


def truncate_snippet(value: Any, limit: int = TEXT_SNIPPET_MAX) -> str:
    """本文の丸め。``$...$`` の途中で切らない（論文層と同じ規則）。"""
    text = clean_text(value)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if cut.count("$") % 2 == 1:
        marker = cut.rfind("$")
        cut = cut[:marker] if marker >= 0 else cut
    return cut.rstrip()


def contains_internal_id(value: Any) -> bool:
    """文字列に内部 ID が混ざっているか（TM10）。"""
    return bool(INTERNAL_ID_RE.search(str(value or "")))


def equation_summary(record: Any) -> str:
    """式レコードの要約（``semantics.summary``）→ 本文（plain_text / latex）の順。"""
    rec = record if isinstance(record, dict) else {}
    semantics = rec.get("semantics") if isinstance(rec.get("semantics"), dict) else {}
    reconstruction = rec.get("reconstruction") if isinstance(rec.get("reconstruction"), dict) else {}
    extraction = rec.get("source_extraction") if isinstance(rec.get("source_extraction"), dict) else {}
    for candidate in (
        semantics.get("summary"),
        reconstruction.get("plain_text"),
        extraction.get("plain_text"),
        reconstruction.get("latex"),
        extraction.get("latex"),
    ):
        text = clean_text(candidate)
        if text and not contains_internal_id(text):
            return text
    return ""


def equation_display_label(record: Any) -> str:
    """式の表示ラベル（TM10）。``label`` があれば「式 (N)」、無ければ「番号なし: 要約の先頭」。

    ``equation_id`` は**決して**ラベルに使わない。レコードが無い式は「番号なし」。
    """
    rec = record if isinstance(record, dict) else {}
    label = clean_text(rec.get("label")).strip("()").strip()
    if label and not contains_internal_id(label):
        return f"式 ({label})"
    summary = equation_summary(rec)
    if summary:
        return f"番号なし: {summary[:LABEL_SNIPPET_MAX]}"
    return "番号なし"


def has_printed_label(record: Any) -> bool:
    rec = record if isinstance(record, dict) else {}
    label = clean_text(rec.get("label")).strip("()").strip()
    return bool(label) and not contains_internal_id(label)
