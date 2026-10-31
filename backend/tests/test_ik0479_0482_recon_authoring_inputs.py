"""再構成ループの item オーサリング入力の是正（IK-0479〜IK-0482・ペルソナ通し受講）。

- IK-0479 live 行の空欄（出典文・節見出し・式・親の概念）を同じ文書の構造から補う
- IK-0480 同じ claim・同じ本文・親子の claim に item を二重に作らない
         （文書単位の直列化 + INSERT 時の advisory lock 下の再確認 + atomic child 優先）
- IK-0481 PDF の行末ハイフネーション（distri-\\nbution）を畳む
- IK-0482 claim_type='unknown' と短い否定文をオーサリング対象から外す（理由を件数で残す）

DB・実 LLM には触れない（境界 monkeypatch）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.reconstruction import worker  # noqa: E402
from core.reconstruction.claim_context import (  # noqa: E402
    SKIP_DUPLICATE_TEXT,
    SKIP_FAMILY_ALREADY_AUTHORED,
    SKIP_SHORT_NEGATION,
    SKIP_UNKNOWN_CLAIM_TYPE,
    authoring_skip_reason,
    clean_section_title,
    enrich_claim,
    is_short_negation,
    normalize_claim_text,
    pick_equation,
    pick_evidence_text,
    select_authoring_targets,
)
from core.reconstruction.input_builder import build_user_content  # noqa: E402
from core.reconstruction.schema import ItemAuthoringResult  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_WORKER_SRC = (BACKEND / "core" / "reconstruction" / "worker.py").read_text(encoding="utf-8")


def _claim(cid, text, *, claim_type="result", parent="", **extra):
    return {"id": cid, "text": text, "normalized_text": text, "claim_type": claim_type,
            "parent_claim_id": parent, **extra}


# --- IK-0481 ---------------------------------------------------------------


class TestHyphenationNormalization:
    def test_joins_line_break_hyphen_and_collapses_whitespace(self):
        raw = "encoded in the distri-\nbution of time delays, along with\ntheir source"
        assert normalize_claim_text(raw) == "encoded in the distribution of time delays, along with their source"

    def test_keeps_inline_hyphen(self):
        assert normalize_claim_text("binary-black-hole  channels") == "binary-black-hole channels"

    def test_prompt_input_is_normalized(self):
        claim = _claim("c1", "the distri-\nbution of delays")
        claim["evidence_text"] = "The distri-\n bution\n of delays."
        payload = json.loads(build_user_content(claim).split("\n", 1)[1])
        assert payload["text"] == "the distribution of delays"
        assert payload["normalized_text"] == "the distribution of delays"
        assert payload["evidence_text"] == "The distribution of delays."

    def test_persisted_prompt_is_normalized(self):
        body = extract_function_source(_WORKER_SRC, "_persist_item")
        assert "normalize_claim_text(result.prompt)" in body


# --- IK-0482 ---------------------------------------------------------------


class TestExclusion:
    def test_unknown_claim_type_is_excluded(self):
        assert authoring_skip_reason(_claim("c", "MAPVARS mode calculated uncertainty.", claim_type="unknown")) \
            == SKIP_UNKNOWN_CLAIM_TYPE
        assert authoring_skip_reason(_claim("c", "Some claim.", claim_type="")) == SKIP_UNKNOWN_CLAIM_TYPE

    def test_short_negations(self):
        for text in (
            "The method does not require posterior sampling.",
            "does not require an external ODE solver",
            "The EOS is not parametric.",
            "The method cannot resolve the core.",
            "この手法は事後分布のサンプリングを必要としない。",
        ):
            assert is_short_negation(text), text

    def test_long_or_positive_statements_are_kept(self):
        assert not is_short_negation("The analysis does not assume specific binary-black-hole formation channels.")
        assert not is_short_negation("Inference uses gradient-based optimization instead of MCMC.")
        assert authoring_skip_reason(_claim("c", "Inference uses gradient-based optimization.")) is None

    def test_selection_counts_reasons_without_dropping_rows(self):
        claims = [
            _claim("a", "The method does not require posterior sampling.", claim_type="method_choice"),
            _claim("b", "Phase 3 training stops when the error falls below a threshold.", claim_type="unknown"),
            _claim("c", "The delay-time distribution differs between the two mass bins.", claim_type="comparison"),
        ]
        picked, skipped = select_authoring_targets(claims, limit=10)
        assert [c["id"] for c in picked] == ["c"]
        assert skipped == {SKIP_SHORT_NEGATION: 1, SKIP_UNKNOWN_CLAIM_TYPE: 1}
        assert len(claims) == 3  # 入力は変わらない


# --- IK-0480 ---------------------------------------------------------------


class TestDeduplication:
    def test_same_text_is_authored_once_and_prefers_atomic_child(self):
        claims = [
            _claim("parent", "Inference uses gradient-based optimization."),
            _claim("child", "Inference uses gradient-based optimization", parent="other"),
        ]
        picked, skipped = select_authoring_targets(claims, limit=10)
        assert [c["id"] for c in picked] == ["child"]
        assert skipped == {SKIP_DUPLICATE_TEXT: 1}

    def test_parent_is_skipped_when_child_is_picked(self):
        claims = [
            _claim("p", "Like GP methods, the representation carries no functional form and needs no solver."),
            _claim("k", "The EOS representation carries no assumed functional form in this method.", parent="p"),
        ]
        picked, skipped = select_authoring_targets(claims, limit=10)
        assert [c["id"] for c in picked] == ["k"]
        assert skipped == {SKIP_FAMILY_ALREADY_AUTHORED: 1}

    def test_existing_items_block_text_and_family(self):
        claims = [
            _claim("dup", "Inference uses gradient-based optimization."),
            _claim("kid", "Physical knowledge enters as differentiable penalties on the loss.", parent="p1"),
            _claim("p2", "Low-density consistency emerges from saturation constraints alone."),
        ]
        authored = [
            {"id": "x", "parent_claim_id": "", "text": "inference uses gradient-based  optimization"},
            {"id": "p1", "parent_claim_id": "", "text": "parent text"},
            {"id": "y", "parent_claim_id": "p2", "text": "child of p2"},
        ]
        picked, skipped = select_authoring_targets(claims, authored=authored, limit=10)
        assert picked == []
        assert skipped == {SKIP_DUPLICATE_TEXT: 1, SKIP_FAMILY_ALREADY_AUTHORED: 2}

    def test_limit_applies_after_exclusion_and_keeps_input_order(self):
        claims = [_claim(f"c{i}", f"Distinct positive statement number {i} about the model.") for i in range(5)]
        claims.insert(0, _claim("u", "Something.", claim_type="unknown"))
        picked, _ = select_authoring_targets(claims, limit=3)
        assert [c["id"] for c in picked] == ["c0", "c1", "c2"]

    def test_persist_rechecks_existence_under_advisory_lock(self):
        body = extract_function_source(_WORKER_SRC, "_persist_item")
        assert "pg_advisory_xact_lock" in body
        assert "WHERE NOT EXISTS" in body
        assert "elicit_mode NOT IN ('symbol', 'regime', 'next_step')" in body

    def test_concurrent_runs_for_one_document_author_once(self, monkeypatch):
        """承認フックが同じ文書に thread を 2 本起こしても、同じ claim に 1 件だけ作る。"""
        authored_ids: set[str] = set()
        store_lock = threading.Lock()
        llm_calls: list[str] = []

        class _Session:
            def execute(self, *_a, **_k):
                class _R:
                    def scalar(self_inner):
                        return len(authored_ids)
                return _R()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        def fake_fetch(_session, _doc, _limit):
            with store_lock:
                return [] if "c1" in authored_ids else [_claim("c1", "Inference uses gradient-based optimization.")]

        def fake_author(claim):
            llm_calls.append(claim["id"])
            time.sleep(0.05)  # 並走させる
            return ItemAuthoringResult(elicit_mode="restate", prompt="あなたの言葉で述べてください。")

        def fake_persist(_session, claim, _result):
            with store_lock:
                if claim["id"] in authored_ids:
                    return None
                authored_ids.add(claim["id"])
                return "item-" + claim["id"]

        monkeypatch.setattr(worker, "_pg_session", lambda: _Session())
        monkeypatch.setattr(worker, "_fetch_authorable_claims", fake_fetch)
        monkeypatch.setattr(worker, "_fetch_authored_claims", lambda _s, _d: [])
        monkeypatch.setattr(worker, "_enrich_claims", lambda _s, _d, claims: claims)
        monkeypatch.setattr(worker, "_check_and_count_llm_call", lambda: True)
        monkeypatch.setattr(worker, "author_item_for_claim", fake_author)
        monkeypatch.setattr(worker, "_persist_item", fake_persist)
        monkeypatch.setattr(worker, "_record_item_event", lambda *a, **k: None)
        monkeypatch.setattr(worker, "bind_usage_context", lambda *a, **k: None)

        results: list[int] = []
        threads = [
            threading.Thread(target=lambda: results.append(worker._run_llm_item_authoring_for_document("docX")))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [0, 1]
        assert llm_calls == ["c1"]  # 2 本目は候補を読み直して LLM を呼ばない
        report = worker.last_authoring_report("docX")
        assert report["created"] in (0, 1)


# --- IK-0479 ---------------------------------------------------------------


class TestEnrichment:
    def test_evidence_prefers_containing_quote_then_parent(self):
        evidences = [
            {"evidence_role": "source_quote", "evidence_text": "Block. Inference uses gradient-based optimization. More."},
            {"evidence_role": "sentence_quote", "evidence_text": "Inference uses gradient-based optimization."},
        ]
        assert pick_evidence_text("Inference uses gradient-based optimization.", evidences, containing_only=True) \
            == "Inference uses gradient-based optimization."
        claim = _claim("k", "The method does not need posterior samples.", parent="p")
        out = enrich_claim(claim, parent={"id": "p", "text": "Unlike them, it requires no posterior sampling."},
                           evidences=evidences)
        assert out["evidence_text"] == "Unlike them, it requires no posterior sampling."
        assert "evidence_text" in out["enriched_fields"]

    def test_overlap_fallback_ignores_whole_block_quote(self):
        evidences = [{"evidence_role": "source_quote", "evidence_text": "delay time distribution differs mass bins"}]
        assert pick_evidence_text("The delay-time distribution differs between mass bins.", evidences) == ""

    def test_fills_section_title_and_parent_concepts_without_overwriting(self):
        claim = _claim("k", "A statement.", parent="p", concepts=[], equation={},
                       source_scope={"section_id": "sec_2", "block_id": "b1"}, evidence_text="kept")
        out = enrich_claim(claim, parent={"id": "p", "concepts": [{"name": "delay time"}, {"name": "mass"}]},
                           section_title="GWTC-4 of LIGO")
        assert out["concepts"] == [{"name": "delay time"}, {"name": "mass"}]
        assert out["source_scope"]["section_title"] == "GWTC-4 of LIGO"
        assert out["source_scope"]["section_id"] == "sec_2"
        assert out["evidence_text"] == "kept"
        assert claim["concepts"] == []  # 入力を mutate しない

    def test_garbled_section_titles_are_dropped(self):
        assert clean_section_title("z") == ""
        assert clean_section_title("K\nDE\nK-essence-like\nK\nB") == ""
        assert clean_section_title("V.\nCONCLUSIONS") == "V. CONCLUSIONS"

    def test_equation_linked_by_agent_claim_id(self):
        equations = [
            {"agent_equation_id": "eq_2", "label": "(2)", "latex": "a=b", "defined_symbols": ["a"],
             "linked_claim_ids": ["claim_span_003"]},
            {"agent_equation_id": "eq_9", "label": "(9)", "latex": "c=d", "linked_claim_ids": ["other"]},
        ]
        claim = _claim("uuid-1", "A relation.", agent_claim_id="claim_span_003", equation={})
        assert pick_equation(claim, equations)["latex"] == "a=b"
        out = enrich_claim(claim, equations=equations)
        assert out["equation"]["label"] == "(2)"
        assert "equation" in out["enriched_fields"]
        assert pick_equation(_claim("u2", "x"), equations) == {}

    def test_fetch_selects_parent_agent_and_chunk_columns(self):
        body = extract_function_source(_WORKER_SRC, "_fetch_authorable_claims")
        for col in ("c.parent_claim_id::text", "c.agent_claim_id", "c.chunk_id::text"):
            assert col in body

    def test_enrichment_reads_only_live_rows_of_the_same_document(self):
        body = extract_function_source(_WORKER_SRC, "_enrich_claims")
        assert "FROM theory_claims_live" in body
        assert body.count("superseded_at IS NULL") == 2  # knowledge_evidence / knowledge_equations
        assert body.count("document_id = :doc") == 2

    def test_enrichment_failure_returns_original_claims(self):
        class _Broken:
            def execute(self, *_a, **_k):
                raise RuntimeError("db down")

            def rollback(self):
                pass

        claims = [_claim("k", "A.", parent="p")]
        assert worker._enrich_claims(_Broken(), "doc", claims) is claims
