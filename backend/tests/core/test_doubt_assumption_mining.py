"""D2-2 / D3-5 — 暗黙前提マイニングのテスト。"""

from __future__ import annotations

import re
from pathlib import Path

from core.doubt.assumption_mining.detector import _cluster_key, _is_gap_node
from core.doubt.assumption_mining.schema import (
    MAX_STATEMENT_CHARS,
    MIN_CORPUS_DOCUMENTS,
    GapCluster,
)
from core.doubt.assumption_mining.validator import validate_output

_BACKEND = Path(__file__).resolve().parents[2]


class TestGapCluster:
    def test_single_derivation_not_candidate(self):
        """単一導出・単一論文にしか現れない補完は候補化しない（受け入れ条件）。"""
        cluster = GapCluster(
            cluster_key="gap|x|", operation="x",
            document_ids=["d1"], derivation_ids=["deriv1"],
        )
        assert not cluster.is_candidate()

    def test_two_documents_is_candidate(self):
        cluster = GapCluster(
            cluster_key="gap|x|", operation="x",
            document_ids=["d1", "d2"], derivation_ids=["deriv1"],
        )
        assert cluster.is_candidate()

    def test_two_derivations_is_candidate(self):
        cluster = GapCluster(
            cluster_key="gap|x|", operation="x",
            document_ids=["d1"], derivation_ids=["deriv1", "deriv2"],
        )
        assert cluster.is_candidate()

    def test_placeholder_statement_is_neutral(self):
        cluster = GapCluster(cluster_key="gap|linearize_x|", operation="linearize_x",
                             document_ids=["d1", "d2"], derivation_ids=["v1"])
        text = cluster.placeholder_statement()
        assert "linearize_x" in text
        assert "正規化待ち" in text


class TestDetectorHelpers:
    def test_gap_node_detection(self):
        assert _is_gap_node({"source_backing_status": "inferred"})
        assert _is_gap_node({"source_backing_status": "review_required"})
        assert _is_gap_node({"origin": "deterministic_fallback"})
        assert not _is_gap_node({"source_backing_status": "source_backed", "origin": "paper"})

    def test_cluster_key_is_stable(self):
        key1 = _cluster_key("Linearize_X", ["b_reason", "a_reason"])
        key2 = _cluster_key("linearize_x", ["a_reason", "b_reason"])
        assert key1 == key2


class TestNormalizationValidator:
    def test_valid_statement_passes(self):
        sources = ["摂動展開の高次項を無視する補完です"]
        result, errors, _ = validate_output({
            "statement": "摂動展開は高次項を無視しても十分な精度を保つ",
            "evidence_quote": "摂動展開の高次項を無視する補完です",
            "reason": "補完の説明文に依存が明示されている",
            "confidence": 0.6,
        }, sources)
        assert errors == []
        assert result["statement"]

    def test_empty_statement_is_valid_no_forcing(self):
        """言語化できない場合の空 statement は正常出力（無理に作らない）。"""
        result, errors, _ = validate_output(
            {"statement": "", "evidence_quote": "", "reason": "", "confidence": 0.0}, ["x"],
        )
        assert errors == []
        assert result["statement"] == ""

    def test_non_verbatim_quote_fails(self):
        result, errors, _ = validate_output({
            "statement": "何らかの前提",
            "evidence_quote": "出典に無い文",
            "reason": "r",
            "confidence": 0.5,
        }, ["まったく別のテキスト"])
        assert result is None

    def test_too_long_statement_fails(self):
        result, errors, _ = validate_output({
            "statement": "あ" * (MAX_STATEMENT_CHARS + 1),
            "evidence_quote": "x",
            "reason": "r",
            "confidence": 0.5,
        }, ["x"])
        assert result is None


class TestCandidateOnlyInvariant:
    """マイニング出力が candidate 以外で直接書かれないことの構造テスト（DX-1）。"""

    def test_detector_inserts_candidate_only(self):
        src = (_BACKEND / "core" / "doubt" / "assumption_mining" / "detector.py").read_text(encoding="utf-8")
        inserts = re.findall(r"INSERT INTO assumption_nodes[\s\S]+?VALUES[\s\S]+?\)", src)
        assert inserts, "detector must insert assumption_nodes"
        for stmt in inserts:
            assert "'candidate'" in stmt
            assert "'confirmed'" not in stmt

    def test_corpus_audit_inserts_candidate_only(self):
        src = (_BACKEND / "core" / "doubt" / "assumption_mining" / "corpus_audit.py").read_text(encoding="utf-8")
        assert "'mined_corpus', 'candidate'" in src

    def test_corpus_audit_min_corpus_guard(self):
        """3 論文未満のコーパスでは経路B を実行しない（偽陽性防止）。"""
        assert MIN_CORPUS_DOCUMENTS == 3
        src = (_BACKEND / "core" / "doubt" / "assumption_mining" / "corpus_audit.py").read_text(encoding="utf-8")
        assert "MIN_CORPUS_DOCUMENTS" in src
        assert re.search(r"len\(document_ids\)\s*<\s*MIN_CORPUS_DOCUMENTS", src)

    def test_cluster_key_conflict_prevents_recandidacy(self):
        """confirmed / dismissed 済みの同一前提が再候補化されない（ON CONFLICT DO NOTHING）。"""
        for name in ("detector.py", "corpus_audit.py"):
            src = (_BACKEND / "core" / "doubt" / "assumption_mining" / name).read_text(encoding="utf-8")
            assert "ON CONFLICT (cluster_key)" in src
            assert "DO NOTHING" in src

    def test_normalization_failure_keeps_row(self):
        """LLM 正規化失敗時も検出クラスタが行として保持される（P4）。"""
        src = (_BACKEND / "core" / "doubt" / "assumption_mining" / "worker.py").read_text(encoding="utf-8")
        assert "normalization_failed" in src
        assert "DELETE" not in src.upper() or "DELETE FROM assumption_nodes" not in src
