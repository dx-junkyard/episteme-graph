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


class TestTexIngestKeepsReferencesAndFootnotes:
    """IK-0697 / IK-0698（2026-10-16）: 参照・引用を空文字にせず、脚注を本文の語へ密着させない。"""

    LABELS = {
        "eq:a": ta.TexLabelTarget("equation", "1"),
        "eq:b": ta.TexLabelTarget("equation", "2"),
        "sec:res": ta.TexLabelTarget("section", "3"),
        "tab:n": ta.TexLabelTarget("table", "2"),
        "fig:p": ta.TexLabelTarget("figure", "1"),
    }

    @pytest.mark.parametrize(
        "source, expected",
        [
            (r"In \ref{sec:res}, we show", "In Sec. 3, we show"),
            (r"Eq.~\eqref{eq:a}, and Eqs.~\eqref{eq:a} and \eqref{eq:b}", "Eq. (1), and Eqs. (1) and (2)"),
            (r"Eq.~(\ref{eq:b})", "Eq. (2)"),
            (r"follows from \eqref{eq:b}.", "follows from Eq. (2)."),
            (r"Table~\ref{tab:n}.", "Table 2."),
            (r"shown in \ref{fig:p}", "shown in Fig. 1"),
            (r"by authors of \cite{x,y},", "by authors of [ref],"),
            (r"see Refs.~\citep[p.~3]{x}.", "see Refs. [ref]."),
            # 番号を数えられない参照はラベルを残す（空にしない・違う番号を作らない）。
            (r"from \eqref{eq:missing} and \ref{sec:missing}", "from Eq. (eq:missing) and Sec. sec:missing"),
            (r"\cref{eq:a} gives", "Eq. (1) gives"),
        ],
    )
    def test_reference_commands_leave_readable_marks(self, source, expected):
        assert ta._clean_prose(source, labels=self.LABELS) == expected

    @pytest.mark.parametrize(
        "source, expected",
        [
            # O0（2610.19001）: 範囲・列挙の 2 つ目以降に接頭辞を付けない（「B--Sec. F」にしない）。
            (r"see Appendices~\ref{app:B}--\ref{app:F}.", "see Appendices B--F."),
            (r"Eqs.~(\ref{eq:a})--(\ref{eq:b})", "Eqs. (1)--(2)"),
            (r"Figs.~\ref{fig:p}, \ref{fig:q} and \ref{fig:r} show", "Figs. 1, 2 and 3 show"),
            (r"Secs.~\ref{sec:res} and \ref{app:B}", "Secs. 3 and B"),
            (r"Eqs.~\eqref{eq:a}--\eqref{eq:b}", "Eqs. (1)--(2)"),
            # 先頭の語が無い並びは従来どおり各参照に前置きを付ける。
            (r"in \ref{sec:res} and \ref{app:B}", "in Sec. 3 and Sec. B"),
        ],
    )
    def test_reference_ranges_and_lists_number_only_after_the_lead_word(self, source, expected):
        labels = dict(self.LABELS)
        labels.update({
            "app:B": ta.TexLabelTarget("section", "B"),
            "app:F": ta.TexLabelTarget("section", "F"),
            "fig:q": ta.TexLabelTarget("figure", "2"),
            "fig:r": ta.TexLabelTarget("figure", "3"),
        })
        assert ta._clean_prose(source, labels=labels) == expected

    def test_references_without_label_map_keep_the_label(self):
        assert ta._clean_prose(r"In \ref{sec:intro}, see Eq.~\eqref{eq:x}.") == "In Sec. sec:intro, see Eq. (eq:x)."
        assert ta._clean_text(r"Title after \cite{k}") == "Title after [ref]"

    def test_no_empty_reference_residue(self):
        out = ta._clean_prose(r"In \cite{a}, we use Eq.~\eqref{e}, Table~\ref{t}.")
        for residue in ("In ,", "Eq.,", "Table ."):
            assert residue not in out

    def test_footnote_is_delimited_not_glued(self):
        out = ta._clean_prose(r"the theorem\footnote{We remind the reader that $x>0$.} holds")
        assert out == "the theorem（脚注: We remind the reader that $x>0$.） holds"
        assert "theoremWe" not in out
        assert ta._clean_text(r"write\footnote{The same.}") == "write（脚注: The same.）"

    def test_layout_and_environment_residue_is_dropped(self):
        assert ta._clean_prose(r"\begin{center} hi \\[10pt] there \end{center}") == "hi there"
        assert ta._clean_prose(r"\begin{subequations}") == ""
        assert ta._clean_prose(r"\begin{tabular}{@{}p{0.24\linewidth}p{0.70\linewidth}@{}} a \end{tabular}") == "a"
        assert ta._clean_prose(r"\vspace*{10pt} x \, y") == "x y"

    def test_centre_is_not_rewritten(self):
        # IK-0698: 「centre」→「node」の化けは置換規則からは起きない（語はそのまま残る）。
        macros = ta._extract_simple_macros(r"\newcommand{\cm}{\vec{G}}")
        assert ta._clean_prose(r"the centre of inertia \cm moves", macros=macros) == (
            r"the centre of inertia $\vec{G}$ moves"
        )


class TestTexLabelMap:
    BODY = r"""
\section{Intro}\label{sec:intro}
\begin{equation} a=b \label{eq:a} \end{equation}
\begin{align} x&=1 \label{eq:x} \\ y&=2 \nonumber \\ z&=3 \label{eq:z} \end{align}
\begin{subequations}\label{eq:sub}
\begin{align} p&=1 \label{eq:p} \\ q&=2 \label{eq:q} \end{align}
\end{subequations}
\begin{equation*} w \tag{T} \label{eq:t} \end{equation*}
\begin{figure}\caption{F}\label{fig:f}\end{figure}
\begin{table}\caption{T}\label{tab:t}\end{table}
\subsection{Sub}\label{sec:sub}
\begin{theorem}\label{thm:main} T \end{theorem}
\appendix
\section{Extra}\label{app:a}
\begin{equation} c \label{eq:c} \end{equation}
"""

    def test_numbers_follow_latex_rules(self):
        labels = ta._build_label_map(self.BODY)
        numbers = {key: (target.kind, target.number) for key, target in labels.items()}
        assert numbers["sec:intro"] == ("section", "1")
        assert numbers["eq:a"] == ("equation", "1")
        assert numbers["eq:x"] == ("equation", "2")
        assert numbers["eq:z"] == ("equation", "3")
        assert numbers["eq:sub"] == ("equation", "4")
        assert numbers["eq:p"] == ("equation", "4a")
        assert numbers["eq:q"] == ("equation", "4b")
        assert numbers["eq:t"] == ("equation", "T")
        assert numbers["fig:f"] == ("figure", "1")
        assert numbers["tab:t"] == ("table", "1")
        assert numbers["sec:sub"] == ("section", "1.1")
        assert numbers["thm:main"] == ("other", None)
        assert numbers["app:a"] == ("section", "A")
        # 付録の式番号は文書クラスで変わるので数えない（ラベルを残す）。
        assert numbers["eq:c"] == ("equation", None)

    def test_custom_equation_numbering_is_not_guessed(self):
        labels = ta._build_label_map(r"\renewcommand{\theequation}{\roman{equation}}" + self.BODY)
        assert labels["eq:a"].number is None
        assert labels["sec:intro"].number == "1"

    @pytest.mark.parametrize(
        "declaration",
        [
            r"\numberwithin{equation}{section}",
            # JHEP 系の同梱 .sty の書き方（2610.08908）と、@addtoreset の古い書き方。
            r"\renewcommand{\theequation}{\thesection.\arabic{equation}}\numberwithin{equation}{section}",
            r"\renewcommand{\theequation}{" "\n" r"\thesection.\arabic{equation}}\@addtoreset{equation}{section}",
        ],
    )
    def test_section_wise_equation_numbering_is_counted(self, declaration):
        # ``\numberwithin{equation}{section}`` は決定論で数えられる（「節.通し番号」・付録は「A.1」）。
        labels = ta._build_label_map(self.BODY, preamble=declaration)
        numbers = {key: target.number for key, target in labels.items()}
        assert numbers["eq:a"] == "1.1"
        assert numbers["eq:z"] == "1.3"
        assert numbers["eq:p"] == "1.4a"
        assert numbers["eq:c"] == "A.1"


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


class TestTexProseFromRadarLoopF:
    """論文レーダー自律周回 F（2610.08908 / 2610.20589）の所見: 引用・色・図表ラベル。"""

    @pytest.mark.parametrize(
        "source, expected",
        [
            # 英語の本文に日本語の目印を混ぜない。文頭・\citet は主語として読める既定の語にする（著者名は作らない）。
            (r"\citep{a} computed the trispectrum.", "The authors of [ref] computed the trispectrum."),
            (r"Later, \citet{b} showed it.", "Later, the authors of [ref] showed it."),
            (r"Some text \citep{a}. \cite{b} found more.", "Some text [ref]. The authors of [ref] found more."),
            (r"as $x$ \cite{a} shows", "as $x$ [ref] shows"),
            (r"following \citet{a}, we", "following [ref], we"),
            (r"published in \citeyear{a}.", "published in [ref]."),
        ],
    )
    def test_citations_are_language_neutral_and_readable(self, source, expected):
        assert ta._clean_prose(source) == expected

    def test_color_argument_is_not_body_text(self):
        source = r"parameters shown in \textcolor{blue}{blue}, \textcolor[rgb]{1,0,0}{red} and {\color{magenta} magenta}."
        assert ta._clean_prose(source) == "parameters shown in blue, red and magenta."

    def test_table_label_written_before_and_after_caption_takes_the_table_number(self):
        body = r"""
\section{S}
\begin{table}\label{tab:priors}
\begin{tabular}{ll} a & b \end{tabular}
\caption{Priors.}
\label{tab:priors}
\end{table}
\begin{table}\caption{Other.}\label{tab:other}\end{table}
"""
        labels = ta._build_label_map(body)
        assert labels["tab:priors"] == ta.TexLabelTarget("table", "1")
        assert labels["tab:other"] == ta.TexLabelTarget("table", "2")
        assert ta._clean_prose(r"displayed in Tab.~\ref{tab:priors}.", labels=labels) == "displayed in Tab. 1."



class TestTexReferencePrefixFollowsBodyLanguage:
    """G2 seq 1340: 英文に「式」を混ぜない（直前の語があれば番号だけ・無ければ本文の言語の前置き）。"""

    LABELS = {
        "eq:a": ta.TexLabelTarget("equation", "5.5"),
        "sec:b": ta.TexLabelTarget("section", "2"),
        "fig:c": ta.TexLabelTarget("figure", "1"),
        "tab:d": ta.TexLabelTarget("table", "1"),
    }

    @pytest.mark.parametrize(
        "source, expected",
        [
            (r"the expression Eqn.~\eqref{eq:a} requires", "the expression Eqn. (5.5) requires"),
            (r"using \eqref{eq:a} and \ref{sec:b}, \ref{fig:c}, \ref{tab:d}",
             "using Eq. (5.5) and Sec. 2, Fig. 1, Tab. 1"),
            (r"with $x$ given by \eqref{eq:a}", "with $x$ given by Eq. (5.5)"),
            (r"これは \eqref{eq:a} と \ref{fig:c} から従う", "これは 式 (5.5) と 図 1 から従う"),
            (r"$x$ は \ref{tab:d} に示す", "$x$ は 表 1 に示す"),
        ],
    )
    def test_prefix_language(self, source, expected):
        assert ta._clean_prose(source, labels=self.LABELS) == expected

    def test_tex_quotes_become_plain_quotes(self):
        assert ta._clean_prose(r"the so-called ``tree-level'' model and `bare' terms") == (
            'the so-called "tree-level" model and \'bare\' terms'
        )


class TestTexAppendixParagraphLabels:
    """2610.08908（G1 seq 1243）: 付録の ``\\paragraph{…}\\label{app:…}`` を「Appendix A」と読む。"""

    BODY = r"""
\section{Model}
\begin{equation} a \label{eq:a} \end{equation}
\paragraph{FoG Counterterms.}\label{sec: fog}
\appendix
\section{Kernels}
\paragraph{Real Space.}\label{app:kernels-real}
\begin{eqnarray} K_1 &=& b_1 \nonumber \\ K_2 &=& b_2 \label{eq:k2} \end{eqnarray}
\paragraph{Redshift Space.} \label{app:kernels-rsd}
\begin{eqnarray} Z_1 &=& b_1 \nonumber \end{eqnarray}
"""

    def test_paragraph_labels_point_to_enclosing_section(self):
        labels = ta._build_label_map(self.BODY)
        assert labels["app:kernels-real"] == ta.TexLabelTarget("section", "A")
        assert labels["app:kernels-rsd"] == ta.TexLabelTarget("section", "A")
        # キーの空白は除いて正規化する（``\label{sec: fog}`` → ``sec:fog`` — J0）。
        assert labels["sec:fog"] == ta.TexLabelTarget("section", "1")
        text = ta._clean_prose(r"given in Appendix~\ref{app:kernels-rsd} and Sec.~\ref{sec: fog}", labels=labels)
        assert text == "given in Appendix A and Sec. 1"

    def test_reference_glued_to_preceding_word_gets_a_space(self):
        labels = {"eq:a": ta.TexLabelTarget("equation", "3.1")}
        assert ta._clean_prose(r"action Eq.\eqref{eq:a} reducing", labels=labels) == "action Eq. (3.1) reducing"


def test_tex_accents_become_unicode_on_all_paths():
    """I2 seq 1658: ``Schr\\"odinger`` 等のアクセント記法を本文・題名・著者・参考文献で Unicode にする。"""
    import io as _io
    import tarfile as _tarfile

    prose = ta._clean_prose(
        r"Schr\"odinger, caf\'e, \`a la, Gr\"{u}ner, \^o, \~n, Fran\c{c}ois, Stra\ss e, \'{\i}, Erd\H{o}s, $\"x$"
    )
    assert prose == r"Schrödinger, café, à la, Grüner, ô, ñ, François, Straße, í, Erdős, $\"x$"
    assert ta._clean_bib_value(r"{E. Schr{\"o}dinger and P. Erd\H{o}s}") == "E. Schrödinger and P. Erdős"

    tex = (
        "\\documentclass{article}\n\\title{The Schr\\\"odinger Equation}\n\\author{Jos\\'e M\\\"uller}\n"
        "\\begin{document}\\maketitle\n\\section{Schr\\\"odinger}\nWe follow G\\\"odel.\n\\end{document}\n"
    )
    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = tex.encode("utf-8")
        info = _tarfile.TarInfo("main.tex")
        info.size = len(data)
        tf.addfile(info, _io.BytesIO(data))
    structure = ta.build_structure_from_tex_archive(buf.getvalue(), document_id="d", source_file="x.tar.gz")
    assert structure.metadata.title == "The Schrödinger Equation"
    assert any("Müller" in name and "José" in name for name in structure.metadata.authors)
    assert structure.sections[0].title == "Schrödinger"
    assert "We follow Gödel." in [b.text for b in structure.blocks]



class TestTexLabelKeysWithSpaces:
    """J0: ``\\label{eq: tri-def}`` の空白で式のラベルが落ち、ID が ``eq_tex_bNN`` になっていた。"""

    def test_label_and_reference_keys_drop_spaces(self):
        body = r"\section{S} \begin{equation} T = 1 \label{eq: tri-def} \end{equation}"
        labels = ta._build_label_map(body)
        assert labels["eq:tri-def"] == ta.TexLabelTarget("equation", "1")
        assert ta._extract_equation_label(r"T = 1 \label{eq: tri-def}") == "eq:tri-def"
        assert ta._clean_prose(r"see Eq.~\eqref{eq: tri-def}", labels=labels) == "see Eq. (1)"
        meta = ta._tex_reference_metadata(r"\label{eq: tri-def} and \ref{eq: tri-def}")
        assert meta["labels"] == ["eq:tri-def"] and meta["refs"] == [{"key": "eq:tri-def", "kind": "ref"}]

    def test_macro_expansion_keeps_control_word_boundaries(self):
        macros = {"\\gsc": ta.TexMacro("\\gsc", r"G_\text{scalar}"), "\\ra": ta.TexMacro("\\ra", r"\to")}
        assert ta._expand_simple_macros(r"\alpha\to\gsc, \ra x", macros) == r"\alpha\to G_\text{scalar}, \to x"


class TestTexInlineEquationCandidates:
    """TeX 取り込みの本文 inline 数式は ``$…$`` の原文境界で式候補になる（radar_loop Q0/Q2 所見）。

    ``_clean_prose`` が残した ``$…$`` を equation_semantics が PDF 向け正規表現で切り直し、
    ``\\mathrm{halo}} = K_{ij}`` / ``M_{\\rm min}=10`` のような途中切れの候補を
    ``inline_pdf_text_layer`` として再構成に回していた。値の指定は候補にしない。
    """

    def test_clean_prose_inline_math_becomes_tex_source_candidates(self):
        from episteme_graph.agents.document_structure.schema import (
            DocumentMetadata,
            DocumentStructureResult,
            Section,
            TypedBlock,
        )
        from episteme_graph.agents.equation_semantics.input_builder import (
            EquationSemanticsInputBuilder,
        )

        text = ta._clean_prose(
            r"The kernel \(P_{\mathrm{halo}} = K_{ij}\,x_i x_j\) is evaluated at $z=0$ "
            r"down to $M_{\rm min}=10^{11}M_\odot/h$ with $\epsilon=0.1$."
        )
        block = TypedBlock("tex_b1", 1, 0, text, "body_paragraph", raw={"parser_source": "tex_archive"})
        block.section_id = "sec_1"
        structure = DocumentStructureResult(
            document_id="doc", source_file="paper.tar.gz", cartridge_id=None,
            metadata=DocumentMetadata(title="T", pages=1),
            sections=[Section("sec_1", "Model", 1, 1, 1)], blocks=[block],
        )
        builder = EquationSemanticsInputBuilder()
        candidates = builder.build_candidates(structure)
        assert [c.raw_text for c in candidates] == [r"P_{\mathrm{halo}} = K_{ij}\,x_i x_j"]
        assert candidates[0].source_location["extraction_source"] == "tex_source"
        excluded = builder.compute_candidate_coverage(structure)["details"]["excluded_inline"]
        assert [e["text"] for e in excluded] == ["z=0", r"M_{\rm min}=10^{11}M_\odot/h", r"\epsilon=0.1"]


class TestTexIngestUnwrapsEnsuremath:
    """2026-10-16（radar_loop S1 seq 2773 / 2821 / 2833 / 2842）: ``\\ensuremath{X}`` を数式に残さない。"""

    def _macros(self):
        return ta._extract_simple_macros(
            r"\newcommand{\vev}[1]{\ensuremath{\left\langle #1 \right\rangle}}"
        )

    def test_inline_math_macro_is_unwrapped(self):
        out = ta._clean_prose(
            r"a nonzero mean, i.e. $\vev{u^{\rm FoG}_\parallel} \supset \text{const.}$ here.",
            macros=self._macros(),
        )
        assert r"\ensuremath" not in out
        assert r"$\left\langle u^{\rm FoG}_\parallel \right\rangle \supset \text{const.}$" in out

    def test_nested_display_equation_is_unwrapped(self):
        latex = ta._clean_equation(
            r"\vev{ \vev{ \prod_m e^{-i k_m u_m} }_{\rm FoG} } = 1",
            macros=self._macros(),
        )
        assert r"\ensuremath" not in latex
        assert latex.count(r"\left\langle") == 2

    def test_literal_ensuremath_and_unbalanced_form(self):
        assert ta._unwrap_ensuremath(r"x = \ensuremath{R_{D}} + 1") == r"x = R_{D} + 1"
        assert ta._unwrap_ensuremath(r"a \ensuremath b") == "a b"
        assert ta._unwrap_ensuremath(r"\ensuremathx") == r"\ensuremathx"


class TestTexIngestCitationRunOn:
    """2026-10-16（radar_loop S0 seq 2790・2609.35757 の原文 ``\\cite{…}.In the PBS``）。"""

    def test_space_is_restored_after_citation_period(self):
        out = ta._clean_prose(r"split (PBS) \cite{Mo:1995cs,Sheth1999}.In the PBS, the bias")
        assert out == "split (PBS) [ref]. In the PBS, the bias"

    def test_lowercase_and_existing_space_are_untouched(self):
        assert ta._clean_prose(r"as in \cite{x}.e.g. here") == "as in [ref].e.g. here"
        assert ta._clean_prose(r"as in \cite{x}. Then") == "as in [ref]. Then"


def test_delimit_does_not_cross_japanese_punctuation():
    """judge-deep-07 ②: ``R_δ(λ)、定義:`` を 1 語にして和文ごと ``$…$`` で囲んでいた。"""
    from core.latex_display import delimit_bare_tex

    text = "照合の対象にしていません: 定義: R_δ(λ)、定義: η、結果を導出: T_0ほか"
    out = delimit_bare_tex(text)
    assert "$R_δ(λ)$、定義" in out
    for chunk in out.split("、"):
        assert chunk.count("$") % 2 == 0, out
    assert delimit_bare_tex(r"質量 m_{\Lambda_b}の質量") == r"質量 $m_{\Lambda_b}$の質量"


# ---------------------------------------------------------------------------
# TeX 取り込みのマクロ・略語・float・節一覧（2026-10-17, U1 seq 3019 / 3022 / 3076 / 3091 / 3094 / 3100 / 3112）
# ---------------------------------------------------------------------------

_U1_PREAMBLE = r"""
\documentclass{aa}
\usepackage{glossaries}
\usepackage{xspace}
\newcommand{\lcdm}{$\Lambda$CDM\xspace}
\newcommand{\comet}{{\tt COMET}\xspace}
\newacronym{pve}{PVE}{prior-volume effects}
\newacronym{rsd}{RSD}{redshift-space distortions}
\newglossaryentry{bao}{name={BAO}, description={baryon acoustic oscillations}}
\newcommand{\pve}{\gls{pve}\xspace}
\newcommand{\rsd}{\gls{rsd}\xspace}
"""


def _u1_structure(body: str):
    import io
    import tarfile

    from core.document_pipeline.tex_archive import build_structure_from_tex_archive

    data = (_U1_PREAMBLE + "\\begin{document}\n" + body + "\n\\end{document}\n").encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("main.tex")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return build_structure_from_tex_archive(buf.getvalue(), document_id="d", source_file="u1.tar.gz")


def test_text_macro_with_own_math_is_not_nested_in_dollars():
    """``\\lcdm`` → ``$\\Lambda$CDM\\xspace`` を ``$…$`` で包むと ``$$\\Lambda$CDM$`` になり、先頭の
    ``$$`` が display math の開きと読まれて数百字が 1 数式になっていた（U1 seq 3076）。"""
    structure = _u1_structure(
        "\\section{Results}\nIn the \\lcdm case the shift is mild; the \\lcdm, $w$\\lcdm and $x$ runs agree.\n"
    )
    text = next(b.text for b in structure.blocks if b.block_type == "body_paragraph")
    assert "$$" not in text
    assert "xspace" not in text
    assert "In the $\\Lambda$CDM case" in text
    assert "$w$ $\\Lambda$CDM" in text  # 隣り合う数式は空白で離す


def test_text_macro_with_math_inside_math_mode_uses_text_wrapper():
    """数式の中では本体の ``$`` を剥がし、外側の語は ``\\text{}`` に包む（``\\ensuremath`` と同じ扱い）。"""
    macros = ta._extract_simple_macros(_U1_PREAMBLE)
    latex = ta._clean_inline_math(r"\lcdm + x", macros)
    assert "$" not in latex
    assert latex.startswith(r"\Lambda\text{CDM}")
    assert "xspace" not in latex


def test_text_font_macro_is_not_wrapped_as_math():
    structure = _u1_structure("\\section{Code}\nWe use \\comet for the fits.\n")
    text = next(b.text for b in structure.blocks if b.block_type == "body_paragraph")
    assert "$" not in text
    assert "COMET for the fits" in text


def test_glossary_references_expand_first_use_then_short():
    """``\\gls`` / 略語マクロは初出がフル表記 (略語)、以後は略語。節題は略語だけ・初出を消費しない
    （U1 seq 3019 / 3094 — 節題に ``$\\gls{rsd}\\xspace$RSD`` が二重に出た）。"""
    structure = _u1_structure(
        "\\section{Comparison between different \\texorpdfstring{\\rsd}{RSD} models}\n"
        "These \\pve matter. Again \\gls{pve} and \\pve. \\rsd and \\Gls{bao}; then \\gls{bao} and \\glspl{rsd}.\n"
        "Unknown \\gls{zzz} stays readable.\n"
    )
    heading = structure.sections[0].title
    assert heading == "Comparison between different RSD models"
    text = "\n".join(b.text for b in structure.blocks if b.block_type == "body_paragraph")
    assert "These prior-volume effects (PVE) matter. Again PVE and PVE." in text
    assert "redshift-space distortions (RSD) and Baryon acoustic oscillations (BAO); then BAO and RSDs." in text
    assert "Unknown zzz stays readable." in text
    assert "gls" not in text and "xspace" not in text and "$" not in text


def test_table_float_moves_to_section_that_references_it():
    """結論節に書かれた付録の表は、参照する付録節の段落の直後へ寄せる（U1 seq 3100）。表本体は
    表由来（``raw.in_table``）のまま — 役割判定・主張採否の母集合に入らない（R-4）。"""
    structure = _u1_structure(
        "\\section{Conclusions}\nWe conclude here.\n\n"
        "\\begin{table}\\caption{Fiducial values}\\label{tab:fid}\n"
        "\\begin{tabular}{ll} $b_1$ & 1.4 \\\\ \\end{tabular}\\end{table}\n\n"
        "More conclusions.\n"
        "\\appendix\n\\section{Fiducial values}\nTable~\\ref{tab:fid} lists the values.\n"
    )
    sec = {s.title: s.section_id for s in structure.sections}
    table_blocks = [b for b in structure.blocks if b.block_type == "table_caption" or b.raw.get("in_table")]
    assert {b.section_id for b in table_blocks} == {sec["Fiducial values"]}
    assert all(b.raw.get("relocated_from_section_id") == sec["Conclusions"] for b in table_blocks)
    assert all(b.block_type == "body_paragraph" for b in table_blocks if b.raw.get("in_table"))
    order = [b.text for b in structure.blocks]
    assert order.index("More conclusions.") < order.index("Fiducial values", order.index("More conclusions."))
    assert [b.block_id for b in structure.blocks] == [f"tex_b{i + 1}" for i in range(len(structure.blocks))]


def test_paper_skeleton_section_list_keeps_subsections_and_omits_tex_pages():
    """paper_skeleton の節一覧に下位節を入れ子で添え、TeX 経路（頁なし）では「(page 1)」を出さない
    （IK-0799 / U1 seq 3019・3034）。表本体は代表ブロックに使わない。"""
    from episteme_graph.agents.paper_skeleton.input_builder import SkeletonInputBuilder
    from episteme_graph.agents.paper_skeleton.prompt import PaperSkeletonPromptFactory

    structure = _u1_structure(
        "\\section{Prior volume effects}\nIntro text.\n\\subsection{Formalism}\nFormal text.\n"
        "\\begin{table}\\caption{T}\\label{tab:t}\\begin{tabular}{ll} a & b \\\\ \\end{tabular}\\end{table}\n"
        "Table~\\ref{tab:t} shows it.\n"
        "\\section{Results}\nDone.\n"
    )
    llm_input = SkeletonInputBuilder().build(structure)
    headers = llm_input.section_headers
    assert [h["title"] for h in headers] == ["Prior volume effects", "Results"]
    assert [s["title"] for s in headers[0]["subsections"]] == ["Formalism"]
    assert headers[0]["subsections"][0]["section_number"] == "1.1"
    assert all(h["page_start"] is None for h in headers)
    rep = [b["text"] for b in llm_input.representative_blocks]
    assert "Formal text." in rep or "Table 1 shows it." in rep  # 下位節の本文も代表に入る
    assert not any("|" in t for t in rep)
    prompt = PaperSkeletonPromptFactory().build_messages(llm_input)[1]["content"]
    assert "(page" not in prompt
    assert "  - [" in prompt and "1.1 Formalism" in prompt


class TestTexReferenceDoublePrefixAndFootnotePlacement:
    """IK-0723 / IK-0713（2026-10-17・2610.08751 の再解析 run 40ecf8df で残った 2 件）。"""

    LABELS = {
        "sec:a": ta.TexLabelTarget("section", "2.1"),
        "sec:b": ta.TexLabelTarget("section", "4"),
        "fig:a": ta.TexLabelTarget("figure", "1"),
        "eq:a": ta.TexLabelTarget("equation", "3"),
        "tab:a": ta.TexLabelTarget("table", "2"),
    }

    @pytest.mark.parametrize(
        "source, expected",
        [
            # 直前の語がすでに種類を言っていれば、\cref 系でも種類名を重ねない。
            (r"in Sect.~\cref{sec:a}, we", "in Sect. 2.1, we"),
            (r"(see Sect.~\Cref{sec:b})", "(see Sect. 4)"),
            (r"Sects.~\cref{sec:a} and \cref{sec:b}", "Sects. 2.1 and 4"),
            (r"Section~\autoref{sec:b}", "Section 4"),
            (r"see Fig~\ref{fig:a}", "see Fig 1"),
            (r"Fig.~\cref{fig:a}", "Fig. 1"),
            (r"Eq.~\cref{eq:a}", "Eq. (3)"),
            (r"Table~\cref{tab:a}", "Table 2"),
            # 直前に種類の語が無ければ従来どおり種類名を付ける。
            (r"as in \cref{sec:a}", "as in Sec. 2.1"),
            (r"second \cref{eq:a}", "second Eq. (3)"),
        ],
    )
    def test_reference_does_not_repeat_the_kind_word(self, source, expected):
        assert ta._clean_prose(source, labels=self.LABELS) == expected

    def test_inline_footnote_moves_to_the_end_of_its_sentence(self):
        structure = _u1_structure(
            "\\section{Intro}\n"
            "We fit $w_0w_a$CDM\\footnote{We adopt the CPL form.} cosmologies, assuming a flat Universe. "
            "Next, we see Fig. 2 and more.\n\n"
            "the Hessian $F$,\\footnote{For the Gaussian case.} leading to a simple result. Then it ends.\n\n"
            "Thanks to all.\\footnote{We thank the referee.} The end.\n"
        )
        texts = [b for b in structure.blocks if b.block_type == "body_paragraph"]
        first, second, third = texts[0], texts[1], texts[2]
        assert first.text == (
            "We fit $w_0w_a$CDM cosmologies, assuming a flat Universe.（脚注: We adopt the CPL form.） "
            "Next, we see Fig. 2 and more."
        )
        assert first.raw["footnotes"] == ["We adopt the CPL form."]
        assert second.text.startswith("the Hessian $F$, leading to a simple result.（脚注: For the Gaussian case.） Then")
        # 文末に書かれた脚注はその場に残る（次の文の終わりへ送らない）。
        assert third.text == "Thanks to all.（脚注: We thank the referee.） The end."
        assert third.raw["footnotes"] == ["We thank the referee."]

    def test_footnote_without_sentence_end_goes_to_block_end_and_is_not_lost(self):
        out, notes = ta._relocate_footnotes_to_sentence_end(
            "a（脚注: one） b（脚注: two） c, leading to"
        )
        assert out == "a b c, leading to（脚注: one）（脚注: two）"
        assert notes == ["one", "two"]
        # 略語（Fig. / et al.）の句点では文を終えない。
        out, _ = ta._relocate_footnotes_to_sentence_end("as in Smith（脚注: n） et al. (2020) and Fig. 3 shows. Then")
        assert out == "as in Smith et al. (2020) and Fig. 3 shows.（脚注: n） Then"

    def test_table_body_keeps_body_paragraph_type_with_in_table_flag(self):
        """IK-0806 の未是正範囲: 表本体の種別は GROBID 経路と同じ body_paragraph + raw.in_table のまま
        （本文でない種別を TeX 経路だけで作ると、in_table を見る 4 つの後段の契約が割れる）。"""
        structure = _u1_structure(
            "\\section{A}\nText.\n"
            "\\begin{table}\\caption{T}\\begin{tabular}{ll} a & b \\\\ \\end{tabular}\\end{table}\n"
        )
        table = [b for b in structure.blocks if b.raw.get("in_table")]
        assert table and all(b.block_type == "body_paragraph" for b in table)
