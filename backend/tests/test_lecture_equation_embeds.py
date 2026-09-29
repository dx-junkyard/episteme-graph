"""Tests for ![[equation:...]] embed resolution in lecture segments.

数式埋め込みは evidence_links の `latex`（描画用 TeX）を最優先で使い、
`summary`（意味要約・散文）を LaTeX として埋め込まないこと。

N18（2026-07）: `_resolve_equation_embeds` の正本は routes/lecture.py から
core/lecture.py へ移設された（`build_topic_slides` と一体で、状態投影と分割・述語の
正本を共有するため）。core.lecture から直接 import して単体テストする。
"""

from __future__ import annotations


def _load_resolve_equation_embeds():
    from core.lecture import _resolve_equation_embeds
    return _resolve_equation_embeds


def _links():
    return [
        {
            "kind": "equation",
            "target_id": "eq_sum_rule",
            "summary": "セミレプトニック崩壊の sum rule を定義する。",
            "latex": (
                r"\begin{aligned} \frac{R_{\Lambda_c}}{R_{\Lambda_c}^{SM}} - "
                r"\alpha_R \frac{R_{D}}{R_{D}^{SM}} - \beta_R \frac{R_{D^*}}{R_{D^*}^{SM}}"
                r"=\delta_R \,. \end{aligned}"
            ),
            "plain_text": "R_Lambda_c 比から R_D 比と R_D* 比を引くと delta_R になる",
        },
        {
            "kind": "equation",
            "target_id": "eq_summary_only",
            "summary": "運動方程式の線形化。",
        },
    ]


def test_resolve_equation_embeds_prefers_link_latex_over_summary():
    resolve = _load_resolve_equation_embeds()

    text, formulas = resolve("導入 ![[equation:eq_sum_rule]] まとめ", _links(), [])

    assert "[[FORMULA_0]]" in text
    assert "![[equation:" not in text
    assert formulas[0]["latex"].startswith(r"\begin{aligned}")
    assert formulas[0]["latex"].endswith(r"\end{aligned}")
    # 読み上げには生 TeX ではなく人間向けテキストを使う。
    assert formulas[0]["spoken"] == "R_Lambda_c 比から R_D 比と R_D* 比を引くと delta_R になる"


def test_resolve_equation_embeds_does_not_render_prose_summary_as_formula():
    """IK-0455: latex の無い式の散文 summary を数式として描かない（埋め込みを外す）。"""
    resolve = _load_resolve_equation_embeds()

    text, formulas = resolve("[[equation:eq_summary_only]]", _links(), [])

    assert "[[FORMULA_" not in text
    assert "[[equation:" not in text
    assert formulas == []


def test_resolve_equation_embeds_uses_math_like_summary_when_no_latex():
    resolve = _load_resolve_equation_embeds()
    links = [{"kind": "equation", "target_id": "eq_m", "summary": r"\begin{aligned} a &= b \end{aligned}"}]

    text, formulas = resolve("![[equation:eq_m]]", links, [])

    assert "[[FORMULA_0]]" in text
    assert formulas[0]["latex"] == r"\begin{aligned} a &= b \end{aligned}"


def test_resolve_equation_embeds_removes_unresolvable_embeds():
    resolve = _load_resolve_equation_embeds()

    text, formulas = resolve("前 ![[equation:eq_unknown]] 後", _links(), [])

    assert "![[equation:" not in text
    assert "[[FORMULA_" not in text
    assert formulas == []
