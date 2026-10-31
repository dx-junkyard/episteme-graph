"""第 25 周 第 5 波: 面を跨ぐ学習チャット / RAG の是正（IK-1006 / 0996 / 0987 / 0997 / 1009 / 0986）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "backend", ROOT / "backend" / "api", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.text_hygiene import (  # noqa: E402
    CARRIED_CITATION_REFERENT,
    CARRIED_CITATION_REFERENT_AFTER_GENITIVE,
    UNCONFIRMED_CITATION_REFERENT,
    remove_citation_markers,
)
from core.source_versions import (  # noqa: E402
    PAPER_VERSION_NOTE,
    collapse_paper_versions,
    is_tex_ingest,
    same_passage,
)
from core.formula_facts import (  # noqa: E402
    ORIGINAL_FORMULAS_NOTE,
    RECONSTRUCTED_FORMULA_INSTRUCTION,
    formula_fact_line,
)
from core.label_vocab import RECONSTRUCTED_EQUATION_MARK, RECONSTRUCTED_EQUATION_NOTE  # noqa: E402


MARK = f"［{RECONSTRUCTED_EQUATION_MARK}］"


class TestCitationMarkerRemoval:
    """IK-1006: 名詞の位置の目印を空にしない。"""

    def test_quoted_marker_becomes_a_referent_phrase(self):
        out = remove_citation_markers("前の回答の「[出典3]」は誤りでした。")
        assert "「」" not in out
        assert out == f"前の回答の{UNCONFIRMED_CITATION_REFERENT}は誤りでした。"

    def test_marker_after_genitive_before_particle_is_a_noun(self):
        out = remove_citation_markers("前の回答の[出典3]が指す箇所")
        assert out == f"前の回答の{UNCONFIRMED_CITATION_REFERENT}が指す箇所"

    def test_appended_marker_is_removed_with_its_spaces(self):
        assert remove_citation_markers("TeX 由来の抽出 [出典5] では整っています。") == (
            "TeX 由来の抽出では整っています。"
        )
        assert remove_citation_markers("これは事実です[出典2]。") == "これは事実です。"
        assert remove_citation_markers("これは事実です（[出典2]）。") == "これは事実です。"

    def test_valid_markers_are_normalized_and_kept(self):
        out = remove_citation_markers("事実です［出典１］。前の回答の「[出典3]」は", keep_indices={1})
        assert "[出典1]" in out
        assert UNCONFIRMED_CITATION_REFERENT in out

    def test_carried_correction_uses_the_carry_phrase(self):
        out = remove_citation_markers(
            "前の回答の「[出典3]」は誤り。[出典3]を見ると",
            referent=CARRIED_CITATION_REFERENT,
            referent_after_genitive=CARRIED_CITATION_REFERENT_AFTER_GENITIVE,
        )
        assert out == f"前の回答の{CARRIED_CITATION_REFERENT_AFTER_GENITIVE}は誤り。{CARRIED_CITATION_REFERENT}を見ると"

    def test_reconcile_uses_the_canonical_remover(self):
        import routes.learning as learning_mod

        out = learning_mod._reconcile_citation_markers("前の回答の「[出典3]」は誤り [出典1]。", {1})
        assert "「」" not in out and "[出典1]" in out

    def test_previous_correction_carry_does_not_empty_the_referent(self):
        import routes.learning as learning_mod

        cleaned = learning_mod._correction_source_text("訂正します。前の回答の「[出典3]」は誤りでした。")
        assert "「」" not in cleaned
        assert CARRIED_CITATION_REFERENT_AFTER_GENITIVE in cleaned


class TestPaperVersionCollapse:
    """IK-0996: 同じ論文の別の版の同じ箇所を 1 行に畳む。"""

    META = {
        "tex": {"title": "Neutron Star EOS via PINN", "filename": "2605.12345.tar.gz",
                "source_url": "https://arxiv.org/src/2605.12345", "source_path": ""},
        "pdf": {"title": "Neutron Star EOS via PINN", "filename": "2605.12345v1.pdf",
                "source_url": "https://arxiv.org/pdf/2605.12345v1", "source_path": ""},
        "other": {"title": "Delay-time distribution of binary black holes", "filename": "x.pdf",
                  "source_url": "", "source_path": ""},
    }
    PASSAGE = "Phase 4 re-trains the networks. Reproducibility is assessed across N=15 random seeds."

    def _rows(self):
        return [
            {"id": "c-pdf", "document_id": "pdf", "raw_text": self.PASSAGE.replace(" ", "  "),
             "score": 0.71, "rc": 2},
            {"id": "c-other", "document_id": "other", "raw_text": "Something else entirely.", "score": 0.6, "rc": 0},
            {"id": "c-tex", "document_id": "tex", "raw_text": self.PASSAGE + " [[FORMULA_0]]",
             "score": 0.65, "rc": 0},
        ]

    def test_collapses_to_the_tex_version_with_fewer_reconstructed_formulas(self):
        out = collapse_paper_versions(self._rows(), self.META, reconstructed_key="rc")
        ids = [r["id"] for r in out]
        assert ids == ["c-tex", "c-other"]  # 群の先頭の位置に置く
        assert out[0]["score"] == 0.71  # 群の最も高い類似度を引き継ぐ
        assert out[0]["version_note"] == PAPER_VERSION_NOTE
        assert "version_note" not in out[1]

    def test_focus_version_wins_first(self):
        out = collapse_paper_versions(
            self._rows(), self.META, prefer_document_ids=("pdf",), reconstructed_key="rc"
        )
        assert out[0]["id"] == "c-pdf"

    def test_different_passages_of_the_same_paper_are_not_collapsed(self):
        rows = self._rows()
        rows[2]["raw_text"] = "The TOV residual loss is averaged over collocation points in the star."
        out = collapse_paper_versions(rows, self.META, reconstructed_key="rc")
        assert len(out) == 3

    def test_missing_meta_never_collapses(self):
        assert len(collapse_paper_versions(self._rows(), {}, reconstructed_key="rc")) == 3

    def test_helpers(self):
        assert same_passage("a  b, c [[FORMULA_1]] d" * 5, "a b c d" * 5)
        assert is_tex_ingest(self.META["tex"]) and not is_tex_ingest(self.META["pdf"])

    def test_source_entry_carries_the_note_and_dto_accepts_it(self):
        import routes.learning as learning_mod
        from schemas import SourceTierItem

        numbers = learning_mod._SessionCitationNumbers([])
        entry = learning_mod._adopted_source_entry(
            numbers,
            {"id": "c", "text": "x", "source_title": "T", "version_note": PAPER_VERSION_NOTE},
            set(),
        )
        assert entry["version_note"] == PAPER_VERSION_NOTE
        assert SourceTierItem(**entry).version_note == PAPER_VERSION_NOTE
        assert learning_mod._history_source_meta([entry])[0]["version_note"] == PAPER_VERSION_NOTE


class TestPaperOutlineParagraph:
    """IK-0987: 論文の構成を述べる定型段落を検索の採用から外す。"""

    def test_outline_with_unresolved_section_refs_is_excluded(self):
        from services import non_content_chunk_reason

        text = (
            "This paper is organized as follows: we explain the method used in the analysis in "
            "Sec. . Followed by that, we explain the results and discussion in Sec. and respectively. "
            "Finally, in Sec. we discuss the summary of the findings and future prospects."
        )
        assert non_content_chunk_reason(text) == "outline"

    def test_long_intro_with_one_outline_sentence_is_kept(self):
        from services import non_content_chunk_reason

        text = "We study neutron stars with physics informed networks. " * 12 + (
            "The rest of this paper is organized as follows."
        )
        assert non_content_chunk_reason(text) is None


class TestFormulaFactsPerFormula:
    """IK-0997 / IK-1009: 式の事実は式単位で名指し、原文と一致する式には印を付けない。"""

    def test_unnumbered_reconstructed_formulas_are_named_individually(self):
        line = formula_fact_line([
            {"id": "a", "latex": "E=mc^2", "reconstructed": True},
            {"id": "b", "latex": "P(\\varepsilon)", "reconstructed": True},
        ])
        assert f"$E=mc^2$ の式{MARK}" in line
        assert "P(\\varepsilon)" in line
        assert "番号のない式" not in line

    def test_tex_source_match_is_not_marked(self):
        line = formula_fact_line([
            {"id": "a", "latex": "\\mathcal{N}_\\mathrm{EOS}", "reconstructed": True,
             "source_latex": "\\mathcal{N}_\\mathrm{EOS}"},
        ])
        assert line == ""

    def test_mixed_excerpt_says_unmarked_formulas_are_original(self):
        line = formula_fact_line([
            {"id": "a", "label": "(3)", "latex": "x", "reconstructed": True},
            {"id": "b", "latex": "y"},
        ])
        assert f"式 (3){MARK}" in line
        assert ORIGINAL_FORMULAS_NOTE in line

    def test_note_lives_once_in_the_system_instruction(self):
        line = formula_fact_line([{"id": "a", "label": "(3)", "latex": "x", "reconstructed": True}])
        assert RECONSTRUCTED_EQUATION_NOTE not in line
        assert RECONSTRUCTED_EQUATION_NOTE in RECONSTRUCTED_FORMULA_INSTRUCTION

    def test_withheld_broken_reconstruction_falls_back_without_raw_tex(self):
        line = formula_fact_line([{"id": "a", "latex": "\\frac{a}{", "reconstructed": True}])
        assert MARK in line

    def test_no_internal_ids_and_no_counts(self):
        line = formula_fact_line(json.dumps([
            {"id": "eq_tex_b14", "label": "eq_tex_b14", "latex": "z", "reconstructed": True},
        ]))
        assert "eq_tex" not in line


class TestSearchCollapsesVersions:
    """IK-0996 / IK-0987: 検索の後処理 1 箇所で版を畳み、構成の段落を落とす。"""

    def test_search_post_processing(self, monkeypatch):
        import services
        from core import learning_experience

        passage = "Phase 4 re-trains the networks. Reproducibility is assessed across N=15 random seeds. " * 2
        rows = [
            # id, text, title, filename, score, material_id, section, formulas, document_id, source_url, source_path
            ("c1", passage, "Neutron Star EOS via PINN", "2605.12345v1.pdf", 0.8, "m-pdf", "Method",
             json.dumps([{"id": "f", "latex": "x", "reconstructed": True}]), "d-pdf",
             "https://arxiv.org/pdf/2605.12345v1", ""),
            ("c2", "This paper is organized as follows: in Sec. . we explain; in Sec. we conclude.",
             "Delay-time distribution", "y.pdf", 0.75, "m-o", "", None, "d-other", "", ""),
            ("c3", passage + " [[FORMULA_0]]", "Neutron Star EOS via PINN", "2605.12345.tar.gz", 0.7,
             "m-tex", "Method", json.dumps([{"id": "f", "latex": "x"}]), "d-tex",
             "https://arxiv.org/src/2605.12345", ""),
        ]

        class _S:
            def execute(self, sql, params=None):
                class _R:
                    def fetchall(self_inner):
                        return list(rows)
                return _R()

            def close(self):
                pass

        monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
        monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
        monkeypatch.setattr(services, "_pg_session", lambda: _S())
        monkeypatch.setattr(learning_experience, "approved_chunk_ids", lambda _s, _ids: set())
        results = services.search_chunks_with_metadata(
            "q", top_k=8, allowed_document_ids={"d-pdf", "d-tex", "d-other"}
        )
        assert [r["id"] for r in results] == ["c3"]
        assert results[0]["version_note"] == PAPER_VERSION_NOTE
        assert results[0]["score"] == 0.8
        assert "_reconstructed_formula_count" not in results[0]
