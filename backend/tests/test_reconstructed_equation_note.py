"""復元された数式は「復元」と言ってから見せる（2026-09-19）。

PDF から取り出した数式テキストは記号・添字が壊れているため、パイプラインは LLM に
LaTeX を**復元**させる。復元式は原文の数式と機械照合できていないので、学習者向けの
文脈 API では抽出できた式と同じ顔で出さず、1行の事実文を添える。

固定する契約:

①文言の正本は ``core/label_vocab.RECONSTRUCTED_EQUATION_NOTE`` の1箇所
②復元本文が実在するときだけ添える（復元できなかった式に「復元しました」と言わない）
③数値（confidence・一致度）は出さない — 出るのはこの1文だけ
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import component_context, element_context, learner_context_common  # noqa: E402
from core.label_vocab import RECONSTRUCTED_EQUATION_NOTE  # noqa: E402

NOTE = RECONSTRUCTED_EQUATION_NOTE


def _export_record(*, status="inferred_from_context", latex="\\delta_g = b \\delta_m"):
    """equations.json の export 形（element_context が読む形）。"""
    return {
        "equation_id": "eq_2",
        "label": "(2)",
        "latex": latex,
        "role_in_argument": "premise",
        "semantic_kind": "Linear bias relation.",
        "confidence": 0.62,
        "reconstruction": {
            "status": status,
            "latex": latex,
            "plain_text": "delta g equals b delta m" if latex else None,
            "confidence": 0.62,
        },
    }


def _artifact_record(*, status="inferred_from_context", latex="\\delta_g = b \\delta_m"):
    """equation_semantics artifact のレコード形（component_context が読む形）。"""
    return {
        "equation_id": "eq_2",
        "label": "(2)",
        "source_extraction": {"latex": None, "plain_text": None, "raw_text": "d g = b d m"},
        "reconstruction": {
            "status": status,
            "latex": latex,
            "plain_text": "delta g equals b delta m" if latex else None,
            "confidence": 0.62,
        },
    }


# ---------------------------------------------------------------------------
# 共通プリミティブ
# ---------------------------------------------------------------------------


def test_reconstructed_record_is_detected_in_both_record_shapes():
    assert learner_context_common.equation_latex_is_reconstructed(_export_record()) is True
    assert learner_context_common.equation_latex_is_reconstructed(_artifact_record()) is True


def test_extracted_record_is_not_flagged():
    assert learner_context_common.equation_latex_is_reconstructed(
        _export_record(status="none")
    ) is False


def test_empty_reconstruction_is_not_called_a_reconstruction():
    """復元できなかった式に「復元した式です」と言わない。"""
    record = _export_record(latex=None)
    record["reconstruction"]["plain_text"] = None
    assert learner_context_common.equation_latex_is_reconstructed(record) is False
    assert learner_context_common.reconstructed_equation_note(record) is None


def test_note_text_comes_from_label_vocab():
    assert learner_context_common.reconstructed_equation_note(_export_record()) == NOTE


# ---------------------------------------------------------------------------
# element_context（claim / equation の学習者向け文脈）
# ---------------------------------------------------------------------------


def test_equation_context_carries_the_note():
    fields = element_context._equation_explanatory_fields(_export_record())
    assert fields["latex_note"] == NOTE


def test_extracted_equation_context_has_no_note():
    fields = element_context._equation_explanatory_fields(_export_record(status="none"))
    assert "latex_note" not in fields


def test_equation_context_does_not_leak_numbers():
    fields = element_context._equation_explanatory_fields(_export_record())
    assert "confidence" not in fields
    assert not any(isinstance(value, float) for value in fields.values())


# ---------------------------------------------------------------------------
# component_context（component の根拠として並ぶ式）
# ---------------------------------------------------------------------------


def test_component_equation_item_carries_the_note():
    items = component_context._build_equations(
        {"linked_equation_ids": ["eq_2"]}, {"eq_2": _artifact_record()}
    )
    assert items[0]["label_note"] == NOTE
    assert "confidence" not in items[0]


def test_component_equation_item_without_reconstruction_has_no_note():
    items = component_context._build_equations(
        {"linked_equation_ids": ["eq_2"]}, {"eq_2": _artifact_record(status="none")}
    )
    assert "label_note" not in items[0]


def test_component_equation_item_for_unknown_id_has_no_note():
    items = component_context._build_equations({"linked_equation_ids": ["eq_9"]}, {})
    assert items[0]["label"] == "eq_9"
    assert "label_note" not in items[0]


# ---------------------------------------------------------------------------
# 文言はサーバ側の1箇所（JS にミラーしない）
# ---------------------------------------------------------------------------


def test_note_is_not_mirrored_into_the_frontend():
    frontend = ROOT / "frontend" / "public"
    offenders = [
        str(path.relative_to(ROOT))
        for path in frontend.rglob("*.js")
        if NOTE in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], f"文言はサーバから受け取ること: {offenders}"


# ---------------------------------------------------------------------------
# 教材本文の印（``AI復元``）もサーバ文言（2026-09-19 レビュー: JS 直書きの解消）
# ---------------------------------------------------------------------------


def test_formula_mark_and_note_are_attached_server_side():
    from core.label_vocab import RECONSTRUCTED_EQUATION_MARK
    from core.lecture import annotate_reconstructed_formulas, normalize_to_placeholder_format

    formulas = [
        {"id": "[[FORMULA_0]]", "latex": "x=y", "reconstructed": True},
        {"id": "[[FORMULA_1]]", "latex": "a=b", "reconstructed": False},
        {"id": "[[FORMULA_2]]", "latex": "c=d"},
    ]
    out = annotate_reconstructed_formulas(formulas)
    assert out[0]["reconstructed_mark"] == RECONSTRUCTED_EQUATION_MARK
    assert out[0]["reconstructed_note"] == NOTE
    assert "reconstructed_mark" not in out[1] and "reconstructed_note" not in out[1]
    assert "reconstructed_mark" not in out[2]
    assert "reconstructed_mark" not in formulas[0]  # 入力を mutate しない

    _, normalized = normalize_to_placeholder_format("see [[FORMULA_0]] and [[FORMULA_1]]", formulas)
    assert normalized[0]["reconstructed_mark"] == RECONSTRUCTED_EQUATION_MARK
    assert "reconstructed_mark" not in normalized[1]


def test_formula_mark_is_not_hardcoded_in_the_frontend():
    from core.label_vocab import RECONSTRUCTED_EQUATION_MARK

    app_js = (ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
    assert RECONSTRUCTED_EQUATION_MARK not in app_js
    assert "formula.reconstructed_mark" in app_js
    assert "formula.reconstructed_note" in app_js
