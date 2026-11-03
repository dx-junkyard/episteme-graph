"""第 25 周 面 C（分野の地図の運用）の是正（IK-0846〜IK-0851）。

- IK-0846: 論文の同一性キー（同じ arXiv 論文の重複取り込みを 1 本に数える）。
- IK-0848: 判断の帰属は表示名・日付・出所 → 判断の 1 行（UUID を出さない）。
- IK-0849: 辺の共起候補に両端の配置の理由と逐語（数値は運ばない）。
- IK-0850: 確認文・版の出所に内部 ID / 取込経路のトークンを出さない。
- IK-0851: 「該当なし」と「調べていない」を事実文で分ける。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas as atlas_module  # noqa: E402
from core import atlas_lifecycle  # noqa: E402
from core import atlas_review_display as display  # noqa: E402
from core import corpus_view  # noqa: E402
from core import document_refs  # noqa: E402
from core.atlas_edges import derive  # noqa: E402
from core.atlas_gaps import store as gap_store  # noqa: E402
from core.display_projection import INTERNAL_ID_TOKEN_RE  # noqa: E402
from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402

PDF_DOC = "aaaaaaaa-0000-0000-0000-000000000001"
TEX_DOC = "bbbbbbbb-0000-0000-0000-000000000002"
OTHER_DOC = "cccccccc-0000-0000-0000-000000000003"
ACTOR = "11111111-1111-1111-1111-111111111111"


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, handlers):
        self.handlers = list(handlers)

    def execute(self, statement, params=None):
        sql = str(statement)
        for needle, rows in self.handlers:
            if needle in sql:
                return FakeResult(rows)
        return FakeResult([])

    def rollback(self):
        pass


# ---------------------------------------------------------------------------
# IK-0846 論文の同一性キー
# ---------------------------------------------------------------------------


class TestPaperIdentity:
    def test_pdf_and_tex_imports_of_one_arxiv_paper_share_a_key(self):
        pdf = document_refs.paper_identity_key(PDF_DOC, filename="2605.26810v1.pdf")
        tex = document_refs.paper_identity_key(
            TEX_DOC, source_url="https://arxiv.org/src/2605.26810v2"
        )
        assert pdf == tex == "arxiv:2605.26810"

    def test_without_arxiv_material_the_document_id_is_the_key(self):
        assert document_refs.paper_identity_key(OTHER_DOC, filename="notes.pdf") == (
            "document:" + OTHER_DOC
        )

    def test_non_arxiv_url_is_not_read_as_arxiv(self):
        key = document_refs.paper_identity_key(
            OTHER_DOC, source_url="https://example.org/2605.26810.pdf"
        )
        assert key == "document:" + OTHER_DOC

    def test_representative_prefers_the_readable_title(self):
        reps = document_refs.representative_document_ids(
            [
                {"document_id": PDF_DOC, "title": "2605.26810v1", "filename": "2605.26810v1.pdf"},
                {
                    "document_id": TEX_DOC,
                    "title": "Cepheus B star formation",
                    "source_url": "https://arxiv.org/abs/2605.26810",
                },
                {"document_id": OTHER_DOC, "title": "Other paper"},
            ]
        )
        assert reps[PDF_DOC] == TEX_DOC
        assert reps[TEX_DOC] == TEX_DOC
        assert reps[OTHER_DOC] == OTHER_DOC


def _signal_row(signal_id, document_id, title, *, filename="", source_url=""):
    # _SIGNAL_COLUMNS_SQL の列順 + title / source_url / filename
    return (
        signal_id, document_id, None, "astro", "v1", "concept", "r1",
        "Interstellar magnetic field", "interstellar magnetic field",
        "reason " + signal_id, "quote " + signal_id, 0.8, "active",
        "2026-10-22T00:00:0" + signal_id[-1], "2026-10-22T00:00:00",
        title, source_url, filename,
    )


class TestGapThresholdCountsPapers:
    def _session(self, rows):
        return FakeSession([("FROM landscape_gap_signals", rows)])

    def test_two_imports_of_one_paper_do_not_make_a_candidate(self):
        session = self._session(
            [
                _signal_row("s1", PDF_DOC, "2605.26810v1", filename="2605.26810v1.pdf"),
                _signal_row("s2", TEX_DOC, "Cepheus B", source_url="https://arxiv.org/abs/2605.26810"),
            ]
        )
        assert gap_store.derive_candidates(session, domain_key="astro") == []

    def test_two_distinct_papers_still_make_a_candidate_with_one_row_each(self):
        session = self._session(
            [
                _signal_row("s1", PDF_DOC, "2605.26810v1", filename="2605.26810v1.pdf"),
                _signal_row("s2", TEX_DOC, "Cepheus B", source_url="https://arxiv.org/abs/2605.26810"),
                _signal_row("s3", OTHER_DOC, "Other paper"),
            ]
        )
        out = gap_store.derive_candidates(session, domain_key="astro")
        assert len(out) == 1
        titles = sorted(d["title"] for d in out[0]["documents"])
        assert titles == ["Cepheus B", "Other paper"]
        for d in out[0]["documents"]:
            assert not any(k.startswith("_") for k in d)


# ---------------------------------------------------------------------------
# IK-0846 / IK-0849 辺の共起
# ---------------------------------------------------------------------------

SKELETON = atlas_module.AtlasSkeleton(
    cartridge="astro",
    status="frozen",
    version="v1",
    regions=(
        atlas_module.SkeletonRegion(
            id="r1", label="R1",
            concepts=(atlas_module.SkeletonConcept(id="a", label="A"),),
        ),
        atlas_module.SkeletonRegion(
            id="r2", label="R2",
            concepts=(atlas_module.SkeletonConcept(id="c", label="C"),),
        ),
    ),
    edges=(),
)


def _placement(doc, title, node, *, source_url="", filename="", reason="", evidence=None):
    return (doc, title, node, source_url, filename, reason, evidence)


class TestEdgeCoOccurrence:
    def test_duplicate_imports_do_not_satisfy_the_threshold(self):
        session = FakeSession(
            [
                (
                    "landscape_placements",
                    [
                        _placement(PDF_DOC, "2605.26810v1", "a", filename="2605.26810v1.pdf"),
                        _placement(PDF_DOC, "2605.26810v1", "c", filename="2605.26810v1.pdf"),
                        _placement(TEX_DOC, "Cepheus B", "a", source_url="https://arxiv.org/abs/2605.26810"),
                        _placement(TEX_DOC, "Cepheus B", "c", source_url="https://arxiv.org/abs/2605.26810"),
                    ],
                )
            ]
        )
        assert derive.derive_co_occurrence_pairs(session, domain_key="astro", skeleton=SKELETON) == []

    def test_endpoints_carry_reason_and_quote_without_numbers(self):
        session = FakeSession(
            [
                (
                    "landscape_placements",
                    [
                        _placement(PDF_DOC, "Alpha", "a", reason="ra", evidence=[{"quote": "qa", "claim_id": "x"}]),
                        _placement(PDF_DOC, "Alpha", "c", reason="rc", evidence='[{"quote": "qc"}]'),
                        _placement(OTHER_DOC, "Beta", "a"),
                        _placement(OTHER_DOC, "Beta", "c"),
                    ],
                )
            ]
        )
        pairs = derive.derive_co_occurrence_pairs(session, domain_key="astro", skeleton=SKELETON)
        assert len(pairs) == 1
        alpha, beta = pairs[0]["documents"]
        assert alpha["endpoints"] == [
            {"node_id": "a", "node_label": "A", "reason": "ra", "evidence_quote": "qa"},
            {"node_id": "c", "node_label": "C", "reason": "rc", "evidence_quote": "qc"},
        ]
        assert "endpoints" not in beta
        for e in alpha["endpoints"]:
            assert "weight" not in e and "confidence" not in e
            assert "claim_id" not in e

    def test_placement_sql_does_not_select_numbers(self):
        import inspect

        src = inspect.getsource(derive._fetch_placements)
        sql = src.split('"""', 3)[3]
        assert "p.weight" not in sql and "p.confidence" not in sql


# ---------------------------------------------------------------------------
# IK-0846 論文の海の一覧・厚み
# ---------------------------------------------------------------------------


class TestCorpusSeaFoldsDuplicates:
    def _session(self, list_rows):
        identities = [
            (PDF_DOC, "2605.26810v1", "", "2605.26810v1.pdf"),
            (TEX_DOC, "Cepheus B", "https://arxiv.org/abs/2605.26810", ""),
            (OTHER_DOC, "Other", "", ""),
        ]
        return FakeSession(
            [
                ("COALESCE(d.source_url, '') AS source_url", identities),
                ("d.authors", list_rows),
            ]
        )

    def test_list_shows_one_row_per_paper_with_the_readable_title(self):
        session = self._session(
            [
                (PDF_DOC, "2605.26810v1", [], 2026, True),
                (TEX_DOC, "Cepheus B", ["X"], 2026, False),
                (OTHER_DOC, "Other", [], 2025, True),
            ]
        )
        out = corpus_view.list_corpus_documents(
            session, "astro", [PDF_DOC, TEX_DOC, OTHER_DOC]
        )
        assert [d["document_id"] for d in out] == [TEX_DOC, OTHER_DOC]
        assert out[0]["title"] == "Cepheus B"
        assert out[0]["placed"] is True


# ---------------------------------------------------------------------------
# IK-0848 判断の帰属
# ---------------------------------------------------------------------------


class TestDecisionAttribution:
    def test_uuid_is_replaced_by_a_display_name_and_a_line(self):
        decision = {"status": "accepted", "decided_by": ACTOR, "decided_at": "2026-10-22T12:00:00+00:00"}
        out = display.project_decision(
            decision, {ACTOR: "浦川"}, origin_prefix=display.ORIGIN_PREFIX_GAP
        )
        assert "decided_by" not in out
        assert out["decided_by_label"] == "浦川"
        assert out["decision_line"] == "「浦川」が 2026-10-22 に採用しました"
        assert out["provenance_line"] == "AIによる検出 → 「浦川」が 2026-10-22 に採用しました"
        assert ACTOR not in str(out.values())

    def test_restored_candidate_keeps_the_unconfirmed_origin(self):
        decision = {"status": "candidate", "decided_by": ACTOR, "decided_at": "2026-10-22"}
        out = display.project_decision(
            decision, {ACTOR: "浦川"}, origin_prefix=display.ORIGIN_PREFIX_EDGE
        )
        assert "provenance_line" not in out
        assert out["decision_line"] == "「浦川」が 2026-10-22 に見送りから戻しました"

    def test_missing_actor_is_said_as_such(self):
        out = display.project_decision({"status": "dismissed", "decided_at": ""}, {})
        assert out["decided_by_label"] == display.DECISION_ACTOR_UNRECORDED

    def test_deleted_account_tombstone_name_is_not_shown(self):
        session = FakeSession([("FROM users", [(ACTOR, "deleted-1234abcd", "deleted")])])
        assert display.fetch_actor_labels(session, [ACTOR]) == {
            ACTOR: display.DECISION_ACTOR_DELETED
        }


# ---------------------------------------------------------------------------
# IK-0850 内部 ID / トークン
# ---------------------------------------------------------------------------


class _Skel:
    generated_by = "reference_map:astrophysics_v0.1_spec_2026-08-04"
    reviewed_by = ("faculty:reference_import", ACTOR)


class TestProvenanceLabels:
    def test_tokens_become_words(self):
        session = FakeSession([("FROM users", [(ACTOR, "浦川", "active")])])
        out = display.skeleton_provenance(session, _Skel())
        assert out == {
            "generated_by_label": display.GENERATED_BY_REFERENCE_MAP,
            "reviewed_by_labels": [display.REVIEWER_REFERENCE_IMPORT, "浦川"],
        }
        text = str(out)
        assert "reference_map:" not in text and "faculty:" not in text
        assert not INTERNAL_ID_TOKEN_RE.search(text)


# ---------------------------------------------------------------------------
# IK-0851 空の結果の種類
# ---------------------------------------------------------------------------


class TestEmptyResultsAreDistinguished:
    def test_near_anchor_states(self):
        assert display.near_anchor_check("no_index")["state"] == "no_index"
        assert "ベクトル索引" in display.near_anchor_check("no_index")["note"]
        assert display.near_anchor_check("") is None

    def test_annotate_reports_no_index_without_a_current_version(self):
        from core.atlas_vectors import annotate

        report: dict = {}
        annotate.annotate_gap_clusters(
            FakeSession([]), "astro", "", [{"proposed_label": "x"}], report=report
        )
        assert report["state"] == "no_index"

    def test_dismissed_none_note_only_when_filter_is_on_and_none_dismissed(self):
        cands = [{"decision": {"status": "accepted"}}]
        assert display.dismissed_none_note(cands, include_dismissed=False, suppressed={"dismissed"}) is None
        assert display.dismissed_none_note(cands, include_dismissed=True, suppressed={"dismissed"}) == display.DISMISSED_NONE_NOTE
        cands.append({"decision": {"status": "dismissed"}})
        assert display.dismissed_none_note(cands, include_dismissed=True, suppressed={"dismissed"}) is None

    def test_freeze_impact_says_nothing_is_removed(self):
        facts = atlas_lifecycle._freeze_impact_facts(set(), set(), has_frozen=True)
        assert facts == [atlas_lifecycle.FACT_NO_REMOVED_NODES, atlas_lifecycle.FACT_SAME_NODE_SET]
        assert atlas_lifecycle._freeze_impact_facts(set(), set(), has_frozen=False) == []
        assert atlas_lifecycle._freeze_impact_facts({"x"}, set()) == [
            atlas_lifecycle.FACT_REMOVED_NODES_HIDE_LEARNER_TRACES
        ]


def test_atlas_review_display_does_not_import_fastapi():
    src = (BACKEND / "core" / "atlas_review_display.py").read_text(encoding="utf-8")
    assert_source_does_not_import(src, ["fastapi"], context="atlas_review_display.py")


# ---------------------------------------------------------------------------
# 管理画面の描画（静的検査）
# ---------------------------------------------------------------------------

ADMIN_JS = BACKEND.parent / "frontend" / "public" / "js" / "admin.js"


def _admin_js() -> str:
    return ADMIN_JS.read_text(encoding="utf-8")


class TestAdminAtlasReviewUi:
    def test_origin_line_is_replaced_by_the_decision_line(self):
        js = _admin_js()
        assert "escHtml(_reviewOriginText(decision, GAP_ORIGIN_LABEL))" in js
        assert "escHtml(_reviewOriginText(decision, EDGE_ORIGIN_NOTE))" in js
        assert "decision.provenance_line" in js
        # 判断者の UUID を描かない
        assert "decision.decided_by)" not in js and "decision.decided_by +" not in js

    def test_accepted_edge_cannot_be_accepted_again(self):
        js = _admin_js()
        assert "(accepted || retired ? \" disabled\" : \"\") + '>採用</button>';" in js
        assert "_edgeKindSelectHtml(edgeKey, decision, !!retired || accepted)" in js

    def test_edge_documents_show_endpoint_reason_and_quote(self):
        js = _admin_js()
        assert "d.endpoints" in js
        assert "e.evidence_quote" in js and "e.reason" in js

    def test_list_notes_are_server_sentences(self):
        js = _admin_js()
        assert "data.near_anchor_check" in js and "data.dismissed_note" in js
        assert "ベクトル索引がないため" not in js  # 文言はサーバが持つ

    def test_provenance_tokens_are_not_rendered(self):
        js = _admin_js()
        assert "frozen.generated_by" not in js
        assert "frozen.reviewed_by" not in js
        assert "provenance.generated_by_label" in js
