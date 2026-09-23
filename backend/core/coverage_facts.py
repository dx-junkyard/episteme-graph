"""被覆・欠落の事実文（``core/reference_health.py`` の隣接語彙）。

参照の健全性（P4-3）は「参照先が存在しないリンク」しか数えない。実測（2026-09-19・
12 論文）では 12/12 本が ``status: ok`` を返す一方で、同じ run の
``document_completeness`` は 11/12 本で ``complete=false``、8/11 本は
``contextual_explanation`` が日次上限で 1 件も生成されず、1 本は学ぶ単位が 0 件だった。
**「壊れている」だけを見て「そもそも無い」「上流で打ち切った」を見ないと、計器は
「壊れていない」と読める。** ここはその 3 種類の**事実文**の語彙表を置く。

置くもの:

- :data:`EMPTY_LAYER_FACTS` — 層がまるごと空であるという事実（欠落 ≠ 破断）。
- :data:`COMPLETENESS_REASON_FACTS` — 取り込みの完全性チェック（
  ``core/document_pipeline/completeness.py`` の ``review_reasons``）の日本語事実文。
- :func:`iter_stage_coverage_gaps` — run の ``stage_outputs`` から「入力を打ち切った」
  「上限で実行されなかった」段階を決定論的に拾う純関数。

不変条項:

- **T-3 数値を書かない**: すべての事実文は数字を含まない（件数・割合・残回数を出さない）。
  「何件か」は読み手が ``details`` の配列長として数えられるだけ。
- **事実であって督促ではない**: 命令調・煽り（依頼形の語尾、強調の副詞、感嘆符）を書かない
  （G6 と同型。禁止語の一覧はガードレールテスト側が持つ — ここに書くと自分で違反する）。
- **純粋**: FastAPI / sqlalchemy / LLM を import しない（``core/label_vocab.py`` と同じ立場）。
- **訳語表を増やさない**: 段階名の日本語は既存の
  ``core/llm_policy.py::PIPELINE_STAGE_LABELS`` を第一に引き、そこに無い**非 LLM 段階**
  だけを :data:`EXTRA_STAGE_LABELS` で補う（文言は
  ``routes/lecture_studio/pipeline.py::DOCUMENT_PIPELINE_STAGE_LABELS`` と同語。
  core から routes は import できないため、必要な分だけをここに持つ）。
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from core import llm_policy

__all__ = [
    "BENIGN_TRUNCATION_REASONS",
    "COMPLETENESS_REASON_FACTS",
    "EMPTY_LAYER_FACTS",
    "EXTRA_STAGE_LABELS",
    "GAP_SKIPPED",
    "GAP_TRUNCATED",
    "LAYER_KEYS",
    "LAYER_LABELS",
    "completeness_fact",
    "empty_layer_fact",
    "iter_stage_coverage_gaps",
    "skipped_stage_fact",
    "stage_label",
    "truncated_stage_fact",
]


# ---------------------------------------------------------------------------
# 1. 層がまるごと空（欠落）
# ---------------------------------------------------------------------------

#: 空かどうかを見る層のキー（表示順の正本）。
LAYER_KEYS: tuple[str, ...] = (
    "graph_main",
    "equations",
    "derivation_steps",
    "learning_units",
    "element_explanations",
    "figures",
)

#: 層 → (短い名前, 空であるという事実文)。**1 表で持つ**（短い名前と事実文を別々の
#: 日本語表に分けると「同じキー集合で値が違う表」になり、訳語表の分裂検出
#: （``test_label_vocab_guardrails.py``）が正しく警告する）。
_EMPTY_LAYERS: dict[str, tuple[str, str]] = {
    "graph_main": ("主グラフ", "この教材には理論操作グラフの主グラフがありません。"),
    "equations": ("式", "この教材には式の記録がありません。"),
    "derivation_steps": ("導出ステップ", "この教材には導出ステップの記録がありません。"),
    "learning_units": ("学ぶ単位", "この教材には学ぶ単位の記録がありません。"),
    "element_explanations": ("要素の説明", "この教材には要素の説明がありません。"),
    "figures": ("図・画像", "この教材には図・画像の記録がありません。"),
}

#: 層が空であるという事実（「壊れている」ではなく「無い」）。
EMPTY_LAYER_FACTS: dict[str, str] = {key: fact for key, (_label, fact) in _EMPTY_LAYERS.items()}

#: 空層の短い名前（``details`` の ``from_label`` に出す。内部 ID は出さない）。
LAYER_LABELS: dict[str, str] = {key: label for key, (label, _fact) in _EMPTY_LAYERS.items()}


def empty_layer_fact(layer: str) -> str:
    """層が空であるという事実文。未知の層名でも黙らない（一般形で返す）。"""
    return EMPTY_LAYER_FACTS.get(layer) or "この教材には記録が無い層があります。"


# ---------------------------------------------------------------------------
# 2. 取り込みの完全性（document_completeness.review_reasons）
# ---------------------------------------------------------------------------

#: ``core/document_pipeline/completeness.py`` が積む review_reasons の日本語事実文。
#: 「疑い」は疑いとして書き、断定しない（AI が確定しないのと同じ作法）。
COMPLETENESS_REASON_FACTS: dict[str, str] = {
    "ingest_incomplete": "原本のうち、本文として取り込めていない範囲がある可能性があります。",
    "structure_page_coverage_low": "原本のページのうち、文書構造に現れていないページがあります。",
    "terminal_section_missing": "原本の末尾にあるはずの節が、文書構造に現れていません。",
    "tail_truncation_suspected": "原本の末尾が途中で切れている可能性があります。",
    "equation_label_discontinuity": "式番号の並びが途中で飛んでいます（取りこぼした式がある可能性があります）。",
    "equation_labels_missing_from_registry": "原本にある式番号のうち、式の記録に現れていないものがあります。",
    "equation_artifact_coverage_incomplete": "本文に現れる数式のうち、式として記録されていないものがあります。",
    "tex_display_math_without_equation_records": "原本の別行立て数式に対して、式の記録がありません。",
    "accepted_candidate_not_in_registry": "採択した式の候補のうち、式の記録に現れていないものがあります。",
}


def completeness_fact(reason: str) -> str:
    """完全性チェックの理由に対応する事実文。未知の理由でも黙って落とさない。"""
    text = str(reason or "").strip()
    return COMPLETENESS_REASON_FACTS.get(text) or (
        "取り込みの完全性チェックで、確認が必要な点が記録されています。"
    )


# ---------------------------------------------------------------------------
# 3. 段階の打ち切り・未実行（stage_outputs の coverage / skip）
# ---------------------------------------------------------------------------

GAP_TRUNCATED = "truncated_stages"
GAP_SKIPPED = "skipped_stages"

#: 打ち切りのうち、**教員が明示的に選んだ**もの（欠落として報告しない）。
#: 図の解析は `analyze_images` のオプトインなので、既定の off を毎回「欠けている」と
#: 言うのは誤報になる（常時赤の計器を作らない）。
BENIGN_TRUNCATION_REASONS: frozenset[str] = frozenset({"skipped_by_option"})

#: 段階名の日本語のうち、``llm_policy.PIPELINE_STAGE_LABELS``（LLM 段階のみ）に
#: 無い**非 LLM 段階**の補い。文言は進捗表示（``DOCUMENT_PIPELINE_STAGE_LABELS``）と同語。
EXTRA_STAGE_LABELS: dict[str, str] = {
    "document_structure": "文書構造の復元",
    "figure_image_extraction": "図画像の抽出",
    "source_chunking": "チャンクの生成",
    "evidence_registry": "根拠の一元管理",
    "claim_object_builder": "主張オブジェクトの組み立て",
    "symbol_registry": "数式記号の整理",
    "derivation_chain": "導出関係の構築",
    "figure_table_semantics": "図表の意味復元",
    "component_graph": "理論操作グラフの構築",
    "theory_modules": "理論モジュールの保存",
    "identity_candidates": "共通する概念の候補づくり",
}

#: ``stage_outputs`` のうち段階ではないキー（走査から外す）。
_NON_STAGE_KEYS: frozenset[str] = frozenset(
    {"_artifacts", "_stage_models", "completed", "reference_health", "resume"}
)


def stage_label(stage: str) -> str:
    """段階の表示名。LLM 段階は既存の正本、非 LLM 段階は :data:`EXTRA_STAGE_LABELS`。

    どちらにも無ければ段階名をそのまま返す（訳語を捏造せず、黙りもしない）。
    """
    key = str(stage or "").strip()
    if not key:
        return ""
    label = llm_policy.PIPELINE_STAGE_LABELS.get(key)
    if label:
        return label
    return EXTRA_STAGE_LABELS.get(key, key)


def truncated_stage_fact(stage: str) -> str:
    return f"「{stage_label(stage)}」の段階で、入力の一部を処理していない記録があります。"


def skipped_stage_fact(stage: str) -> str:
    return f"「{stage_label(stage)}」の段階は実行されていません。"


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def iter_stage_coverage_gaps(stage_outputs: Mapping | None) -> Iterator[tuple[str, str]]:
    """run の ``stage_outputs`` から ``(gap_kind, stage)`` を決定論順に返す（純関数）。

    - :data:`GAP_TRUNCATED`: ``coverage.truncated`` が正、または ``coverage.reasons`` が
      非空。ただし理由が :data:`BENIGN_TRUNCATION_REASONS` だけのものは除く。
    - :data:`GAP_SKIPPED`: ``skipped_by_limit`` が真、または ``skipped_reason`` が非空。
      ``skipped_by_option``（教員の明示的な選択）は含めない。

    同じ段階が両方に当たるとき（上限で実行されず、その結果 population 全部が
    truncated になる）は :data:`GAP_SKIPPED` だけを返す（「実行されていません」が
    「一部を処理していません」を含む。同じ事実を二度言わない）。

    走査順は段階名の昇順（辞書順）で、入力の dict 順に依存しない。
    """
    if not isinstance(stage_outputs, Mapping):
        return
    for stage in sorted(str(k) for k in stage_outputs.keys()):
        if stage in _NON_STAGE_KEYS or stage.startswith("_"):
            continue
        payload = stage_outputs.get(stage)
        if not isinstance(payload, Mapping):
            continue
        skipped = _truthy(payload.get("skipped_by_limit")) or bool(
            str(payload.get("skipped_reason") or "").strip()
        )
        if skipped:
            yield (GAP_SKIPPED, stage)
            continue
        coverage = payload.get("coverage")
        if isinstance(coverage, Mapping):
            reasons = coverage.get("reasons")
            reasons = [str(r) for r in reasons] if isinstance(reasons, (list, tuple)) else []
            try:
                truncated = int(coverage.get("truncated") or 0)
            except (TypeError, ValueError):
                truncated = 0
            benign = bool(reasons) and set(reasons) <= BENIGN_TRUNCATION_REASONS
            if (truncated > 0 or reasons) and not benign:
                yield (GAP_TRUNCATED, stage)
