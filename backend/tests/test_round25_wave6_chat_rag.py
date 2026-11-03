"""第 25 周 第 6 波: 面を跨ぐ学習チャット / RAG の是正（焦点の版・出典番号・式の印の版またぎ・構造ブロック・分類の画面文脈・文中の埋め込み）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "backend", ROOT / "backend" / "api", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.focus_document import (  # noqa: E402
    FocusDocument,
    prefer_focus,
    with_paper_versions,
)
from core.formula_facts import formula_fact_line  # noqa: E402
from core.label_vocab import RECONSTRUCTED_EQUATION_MARK  # noqa: E402
from core.source_versions import collapse_paper_versions  # noqa: E402

MARK = f"［{RECONSTRUCTED_EQUATION_MARK}］"


class TestFocusIncludesPaperVersions:
    """焦点が PDF 版のとき TeX 版の行も焦点として数え、無関係な論文の行を落とす。"""

    def test_with_paper_versions_appends_siblings_and_keeps_source(self):
        focus = FocusDocument(document_ids=("pdf",), source="topic")
        out = with_paper_versions(focus, {"pdf": {"tex"}})
        assert out.document_ids == ("pdf", "tex")
        assert out.source == "topic"
        assert with_paper_versions(focus, {}) is focus

    def test_off_topic_paper_is_dropped_when_tex_version_is_in_focus(self):
        rows = [
            {"id": "t1", "document_id": "tex", "score": 0.62},
            {"id": "cep", "document_id": "cepb", "score": 0.41},
            {"id": "p1", "document_id": "pdf", "score": 0.28},  # PDF 版は採用の下限未満
        ]
        before = prefer_focus(rows, FocusDocument(document_ids=("pdf",), source="topic"), limit=8)
        assert "cep" in [r["id"] for r in before]  # 是正前の挙動（焦点内に採用できる行が無い）
        focus = with_paper_versions(FocusDocument(document_ids=("pdf",), source="topic"), {"pdf": {"tex"}})
        after = prefer_focus(rows, focus, limit=8)
        assert "cep" not in [r["id"] for r in after]
        assert "t1" in [r["id"] for r in after]

    def test_route_extends_focus_with_versions_once(self):
        src = (ROOT / "backend" / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert src.count("resolve_focus_document(") == 1
        assert "with_paper_versions(" in src
        assert "paper_version_siblings(_focus.document_ids, allowed_document_ids=allowed_document_ids)" in src


class TestCitationNumberStableAcrossVersions:
    """前の往復で番号を振った版のチャンクを残し、番号が往復ごとに変わらないようにする。"""

    META = {
        "tex": {"title": "Neutron Star EOS via PINN", "filename": "2605.12345.tar.gz",
                "source_url": "https://arxiv.org/src/2605.12345", "source_path": ""},
        "pdf": {"title": "Neutron Star EOS via PINN", "filename": "2605.12345v1.pdf",
                "source_url": "https://arxiv.org/pdf/2605.12345v1", "source_path": ""},
    }
    PASSAGE = "NICER posteriors are used as training data for the radius of the star in this work."

    def test_numbered_chunk_wins(self):
        rows = [
            {"id": "c-tex", "document_id": "tex", "raw_text": self.PASSAGE, "score": 0.7, "rc": 0},
            {"id": "c-pdf", "document_id": "pdf", "raw_text": self.PASSAGE, "score": 0.6, "rc": 3},
        ]
        assert collapse_paper_versions(rows, self.META, reconstructed_key="rc")[0]["id"] == "c-tex"
        out = collapse_paper_versions(
            rows, self.META, prefer_chunk_ids=("c-pdf",), reconstructed_key="rc"
        )
        assert [r["id"] for r in out] == ["c-pdf"]

    def test_route_passes_numbered_chunks(self):
        src = (ROOT / "backend" / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert "prefer_chunk_ids=tuple(_citation_numbers.mapping())" in src


class TestFormulaMarkAcrossVersions:
    """同じ式が TeX 版で原文なら、PDF 版の抜粋でも［AI復元］を付けない。"""

    TOV = "L_{\\mathrm{TOV}} = \\frac{1}{N_c} \\sum_{i=1}^{N_c} r_i"

    def test_confirmed_latex_removes_the_mark(self):
        formulas = [{"id": "f4", "label": "(4)", "latex": self.TOV, "reconstructed": True}]
        assert f"式 (4){MARK}" in formula_fact_line(formulas)
        line = formula_fact_line(formulas, confirmed_latex=[self.TOV.replace(" ", "")])
        assert MARK not in line
        assert "式 (4)" in line

    def test_unconfirmed_formula_keeps_its_mark(self):
        formulas = [
            {"id": "f4", "label": "(4)", "latex": self.TOV, "reconstructed": True},
            {"id": "f5", "label": "(5)", "latex": "y=1", "reconstructed": True},
        ]
        line = formula_fact_line(formulas, confirmed_latex=[self.TOV])
        assert f"式 (5){MARK}" in line
        assert f"式 (4){MARK}" not in line

    def test_search_rows_rechecked_and_internal_key_dropped(self, monkeypatch):
        import services

        rows = [
            {"id": "c1", "document_id": "pdf", "formula_facts": formula_fact_line(
                [{"id": "f4", "label": "(4)", "latex": self.TOV, "reconstructed": True}]),
             "_formulas": [{"id": "f4", "label": "(4)", "latex": self.TOV, "reconstructed": True}]},
            {"id": "c2", "document_id": "other", "formula_facts": "", "_formulas": None},
        ]
        monkeypatch.setattr(
            services, "paper_original_formula_latex",
            lambda ids, allowed_document_ids: {"pdf": [self.TOV]} if "pdf" in ids else {},
        )
        out = services.confirm_formula_facts_across_versions(rows, allowed_document_ids={"pdf", "tex"})
        assert MARK not in out[0]["formula_facts"]
        assert all("_formulas" not in r for r in out)

    def test_siblings_fail_closed_without_scope(self):
        import services

        assert services.paper_version_siblings(["pdf"], allowed_document_ids=None) == {}
        assert services.paper_version_siblings(["pdf"], allowed_document_ids=set()) == {}


class TestRetrievedStructureEnglishDetail:
    """構造ブロックの段階の括弧に英語の生成文（途中切り）を添えない。"""

    def _fact(self, display_label: str) -> str:
        from core.assistant_context.resolvers.learning import _retrieved_node_fact

        return _retrieved_node_fact({"node": {"label": "Consistency relation", "display_label": display_label}})

    def test_english_sentence_detail_is_dropped(self):
        fact = self._fact(
            "Consistency relation: One neural network represents the neutron-star equation of state P(ε) as a continuous function"
        )
        assert "One neural network" not in fact
        assert fact.endswith("段階に置かれています")

    def test_short_object_name_is_kept(self):
        assert "（TOV equations）" in self._fact("Consistency relation: TOV equations")


class TestIntentScreenHint:
    """意図分類に画面で開いているものの名前を 1 行だけ渡す。"""

    def _body(self, view=None, selection=None):
        payload = SimpleNamespace(model_dump=lambda: {
            "screen": "learning", "selection": selection or {}, "view": view or {},
        })
        return SimpleNamespace(screen_context=payload)

    def test_atlas_node_label(self):
        from routes.learning import _intent_screen_hint

        hint = _intent_screen_hint(self._body(view={"atlas_node_label": "暗黒エネルギー"}), course_id="c1")
        assert "暗黒エネルギー" in hint and "この項目" in hint

    def test_other_course_is_ignored(self):
        from routes.learning import _intent_screen_hint

        body = self._body(view={"atlas_node_label": "x"}, selection={"course_id": "other"})
        assert _intent_screen_hint(body, course_id="c1") == ""
        assert _intent_screen_hint(SimpleNamespace(screen_context=None), course_id="c1") == ""

    def test_prompt_carries_the_hint(self, monkeypatch):
        import routes.learning as learning_mod

        seen: dict = {}

        def _fake(messages, **_kw):
            seen["prompt"] = messages[0]["content"]
            return "DOMAIN_RAG"

        monkeypatch.setattr(learning_mod, "generate_text", _fake)
        monkeypatch.setattr(learning_mod, "get_llm_params", lambda _t: {"model": "m", "reasoning_effort": None})
        assert learning_mod._classify_intent(
            "この項目はどこまで確かめられている？", "c", screen_hint="学習者は分野の地図で「暗黒エネルギー」を開いています"
        ) == "DOMAIN_RAG"
        assert "画面の文脈: 学習者は分野の地図で「暗黒エネルギー」" in seen["prompt"]


class TestInlineEmbedInPromptMaterial:
    """文中の主張の埋め込みを外して値の抜けた文にしない。"""

    def test_inline_claim_is_quoted_and_block_claim_is_removed(self):
        from core.course_content_builder import material_text_for_prompt

        topic = {"evidence_links": [
            {"kind": "claim", "target_id": "claim_x", "summary": "R1.4 = 12.85 km"},
        ]}
        text = "この論文は半径を ![[claim:claim_x]] と報告しています。\n\n![[claim:claim_x]]\n\n次へ。"
        out = material_text_for_prompt(topic, text)
        assert "半径を 「R1.4 = 12.85 km」 と報告" in out
        assert "半径を  と報告" not in out
        assert "![[" not in out
