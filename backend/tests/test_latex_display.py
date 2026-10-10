"""表示用 LaTeX の正本（core/latex_display.py）と、その適用面の回帰テスト（2026-10-10）。

グラフレビューの数式崩れの3つの原因をそれぞれ固定する:
  1. 式ラベルを文字数で切って描けない断片を作っていた（``truncate_latex``）
  2. 地の文に ``$`` なしの TeX が混ざり、画面が生の TeX を出していた（``delimit_bare_tex``）
  3. TeX 取り込みが本文のインライン数式を壊していた（``tex_archive._clean_prose``）
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.display_projection import project_for_learner, project_for_teacher
from core.document_pipeline import tex_archive as ta
from core.graph_paper_layer import schema as pl_schema
from core.latex_display import delimit_bare_tex, looks_like_bare_tex, truncate_latex

# 実データ（2026-10-10 の調査で KaTeX が描けなかった式の先頭）。
REAL_EQUATIONS = [
    r"\mathcal{C}_i = \begin{cases} \, 1+C_{V_L} \, C_{S_L} \, C_{S_R} \, C_T \end{cases} \text{for}~~ i= \begin{cases} V_L \end{cases}",
    r"\kappa_D^{\rm SM} = \mathcal{N} \, \bigg[ \left( 1 + \frac{1}{2} \rho^2 \right) \frac{(1+r)^2 (w^2-1)}{\hat q^2} \bigg] \, \xi(w)^2",
    r"\kappa^{(0)} = 1 , \kappa^{(1)} = \frac12 \lambda + \frac{3}{4} \beta_{K^2} \gamma\gamma(k_1,k_2,k_3)",
    r"\tilde{\delta}(t,{\bm{k}}) {} \overset{\mathrm{def}}{=} \int d^3x\, e^{-i k x}\delta(t,x)",
    r"\frac{Z_3({\bm{p}}/R,{\bm{q}}/R,{\bm{r}}/R-{\bm{q}}/R)}{3!b_1} = \frac{Z_3({\bm{p}},{\bm{q}},{\bm{r}}-{\bm{q}})}{3!b_1},",
]


def _balanced(text: str) -> bool:
    depth = 0
    index = 0
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth < 0:
            return False
        index += 1
    return depth == 0


class TestTruncateLatex:
    @pytest.mark.parametrize("latex", REAL_EQUATIONS)
    def test_cut_is_structurally_complete_at_every_limit(self, latex):
        for limit in range(1, len(latex) + 2):
            cut, truncated = truncate_latex(latex, limit)
            assert _balanced(cut), (limit, cut)
            assert cut.count(r"\begin") == cut.count(r"\end"), (limit, cut)
            assert len(re.findall(r"\\left(?![A-Za-z])", cut)) == len(re.findall(r"\\right(?![A-Za-z])", cut))
            assert not cut.endswith(("^", "_", "\\", "{", r"\overset", r"\bigg", r"\frac")), (limit, cut)
            assert not re.search(r"\\(?:bigg|Bigg|big|Big)$", cut)
            if not truncated:
                assert cut == latex.strip()

    def test_overset_keeps_both_arguments_or_drops_them(self):
        cut, truncated = truncate_latex(REAL_EQUATIONS[3], 40)
        assert truncated
        assert r"\overset" not in cut or re.search(r"\\overset\{[^{}]*(\{[^{}]*\})?[^{}]*\}\{[^{}]*\}", cut)

    def test_too_long_first_atom_returns_empty(self):
        assert truncate_latex(r"\frac{" + "a" * 200 + "}{b}", 40) == ("", True)

    def test_bare_token_arguments_stay_with_command(self):
        cut, _ = truncate_latex(r"x = \frac12 + \frac34 + yyyyyyyyyyyyyyyyyyyyyy", 14)
        assert cut in ("x = \\frac12", "x = \\frac12 +")


class TestEquationLabels:
    @pytest.mark.parametrize("latex", REAL_EQUATIONS)
    def test_unnumbered_label_is_delimited_math_or_none(self, latex):
        label = pl_schema.equation_display_label({"reconstruction": {"latex": latex}})
        assert label.startswith("番号なし")
        assert label.replace("\\$", "").count("$") % 2 == 0
        outside = re.sub(r"\$[^$]*\$", " ", label)
        assert not re.search(r"\\[A-Za-z]+", outside), label

    def test_plain_text_fallback_never_shows_raw_tex(self):
        record = {
            "reconstruction": {"latex": r"\frac{" + "a" * 200 + "}{b}", "plain_text": r"\begin{aligned} &\frac{Z_3({\bm{p}}/R"},
        }
        assert pl_schema.equation_display_label(record) == "番号なし"


class TestDelimitBareTex:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("In this limit, m_B = m_{\\Lambda_b} = m_b and m_c.", "In this limit, $m_B = m_{\\Lambda_b} = m_b$ and $m_c$."),
            ("with m_c/m_b \\to 1 and w_{\\rm max} \\to 1", "with $m_c/m_b \\to 1$ and $w_{\\rm max} \\to 1$"),
            ("Equation (eq:Hamiltonian) defines {\\cal {H}}_{\\rm{eff}}.", "Equation (eq:Hamiltonian) defines ${\\cal {H}}_{\\rm{eff}}$."),
            ("(2\\pi)^3, P_{\\rm L}(k), R", "$(2\\pi)^3$, $P_{\\rm L}(k)$, R"),
            ("In equation (eq_1), $n$ depends on a.", "In equation (eq_1), $n$ depends on a."),
        ],
    )
    def test_wraps_runs_outside_math(self, text, expected):
        assert delimit_bare_tex(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "zero_recoil_limit is an identifier, not math.",
            "Already $x_1$ delimited and \\(y_2\\) too.",
            "Broken $x_1 delimiter left as is with m_b.",
            "Unbalanced \\frac{a group stays raw",
            "日本語の文。",
        ],
    )
    def test_leaves_text_it_cannot_safely_change(self, text):
        out = delimit_bare_tex(text)
        if text.startswith(("zero_", "Already", "Broken", "日本語")):
            assert out == text
        assert out.replace("\\$", "").count("$") % 2 == text.replace("\\$", "").count("$") % 2

    def test_looks_like_bare_tex(self):
        assert looks_like_bare_tex(r"\begin{aligned} \frac{a}{b}")
        assert not looks_like_bare_tex("Defines the observable ratio.")
        assert not looks_like_bare_tex("ratio $R_{H_c}$ only")


class TestTeacherProjection:
    def test_prose_keys_are_delimited_for_teachers_only(self):
        payload = {
            "assumptions": [{"text": "with m_c/m_b \\to 1"}],
            "equations": [{"latex": "m_c/m_b \\to 1"}],
            "symbols": [{"symbol": "m_{\\Lambda_b}"}],
        }
        teacher = project_for_teacher(payload)
        assert teacher["assumptions"][0]["text"] == "with $m_c/m_b \\to 1$"
        # 画面側が ``$…$`` で包むキーは触らない。
        assert teacher["equations"][0]["latex"] == "m_c/m_b \\to 1"
        assert teacher["symbols"][0]["symbol"] == "m_{\\Lambda_b}"
        assert project_for_learner(payload)["assumptions"][0]["text"] == "with m_c/m_b \\to 1"


class TestTexIngestKeepsInlineMath:
    def test_paragraph_math_survives_with_paper_macros(self):
        macros = ta._extract_simple_macros(
            r"\newcommand{\Lc}{\Lambda_c} \newcommand{\RD}{\ensuremath{R_{D}}} \newcommand{\SM}{Standard Model}"
        )
        text = (
            r"Measurements of $R_{D^{(*)}} = {\rm BR}(\bar B\to D^{(*)}\tau\bar\nu)$ with $\ell = e,\mu$"
            r" show \emph{deviations} at $3.8\sigma$~\cite{x}. Also $R_{\Lc}$ and \RD{} in the \SM."
        )
        out = ta._clean_prose(text, macros=macros)
        assert r"$R_{D^{(*)}} = {\rm BR}(\bar B\to D^{(*)}\tau\bar\nu)$" in out
        assert r"$\ell = e,\mu$" in out and r"$3.8\sigma$" in out
        assert r"$R_{\Lambda_c}$" in out and r"$R_{D}$" in out
        assert "Standard Model" in out and "deviations" in out
        assert r"\cite" not in out and r"\emph" not in out

    def test_adjacent_inline_math_is_not_read_as_display(self):
        out = ta._clean_prose(r"defined by $h$$\equiv$$H_0/(100~\mathrm{km})$.")
        assert out == r"defined by $h$ $\equiv$ $H_0/(100~\mathrm{km})$."
        body = r"defined by $h$$\ldef$$H_0$ here.\n\nNext $$x+y$$ display."
        kinds = [segment[0] for segment in ta._segment_tex(body)]
        assert kinds == ["text", "equation", "text"]

    def test_caption_with_nested_braces_is_kept(self):
        segment = r"\centering \includegraphics{a} \caption[short]{Plot of $R_{\Lambda_c}$ vs. $w$.} \label{f}"
        assert ta._caption_text(segment) == r"Plot of $R_{\Lambda_c}$ vs. $w$."


class TestGraphReviewRendersLabelsAsMath:
    SOURCE = (Path(__file__).resolve().parents[2] / "frontend/public/js/admin-graph-review.js").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "needle",
        [
            "esc(String(item.display_label))",
            "esc(String(module.subtitle))",
            "esc(String(equation.display_label",
            "esc(inputs) + \" → \" + esc(outputs)",
            "esc(sinkDisplayLabel(sink))",
            "esc(paperNodeLabel(nodeId))",
        ],
    )
    def test_label_surfaces_do_not_escape_math(self, needle):
        assert needle not in self.SOURCE
