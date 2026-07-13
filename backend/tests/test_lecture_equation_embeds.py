"""Tests for ![[equation:...]] embed resolution in lecture segments.

数式埋め込みは evidence_links の `latex`（描画用 TeX）を最優先で使い、
`summary`（意味要約・散文）を LaTeX として埋め込まないこと。

`routes/lecture.py` は fastapi 経由の重い依存チェーンを持つため、
既存の `test_lecture_audio_availability.py` と同様に、対象の純関数
`_resolve_equation_embeds` をソースから直接 exec して単体テストする。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LECTURE_PY = ROOT / "backend" / "api" / "routes" / "lecture.py"


def _load_resolve_equation_embeds():
    """依存モジュール（fastapi 等）を経由せず、対象関数だけをソースから抽出して実行する。"""
    source = LECTURE_PY.read_text(encoding="utf-8")
    start = source.index("def _resolve_equation_embeds")
    end = source.index("\ndef ", start + 10)
    func_src = source[start:end]
    ns: dict = {"_re": re}
    exec(func_src, ns)  # noqa: S102 - テスト専用の純関数抽出
    return ns["_resolve_equation_embeds"]


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


def test_resolve_equation_embeds_falls_back_to_summary_when_no_latex():
    resolve = _load_resolve_equation_embeds()

    text, formulas = resolve("[[equation:eq_summary_only]]", _links(), [])

    assert "[[FORMULA_0]]" in text
    assert formulas[0]["latex"] == "運動方程式の線形化。"
    assert formulas[0]["spoken"] == "運動方程式の線形化。"


def test_resolve_equation_embeds_removes_unresolvable_embeds():
    resolve = _load_resolve_equation_embeds()

    text, formulas = resolve("前 ![[equation:eq_unknown]] 後", _links(), [])

    assert "![[equation:" not in text
    assert "[[FORMULA_" not in text
    assert formulas == []
