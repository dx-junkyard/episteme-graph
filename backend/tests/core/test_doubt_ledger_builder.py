"""D1-2 — LedgerBuilder（決定論的バックフィル）のテスト。"""

from __future__ import annotations

import re
from pathlib import Path

from core.doubt.ledger_builder import (
    _BUILDER_ALLOWED_STATUSES,
    _backing_status,
    _claim_status,
)

_BACKEND = Path(__file__).resolve().parents[2]
_SRC = (_BACKEND / "core" / "doubt" / "ledger_builder.py").read_text(encoding="utf-8")


class TestConservativeMapping:
    def test_source_backed_claim_with_evidence_is_indirect(self):
        """「原文に書いてある」≠「検証されている」— directly_verified にはしない。"""
        assert _claim_status("source_backed", "evidence text") == "indirectly_supported"

    def test_source_backed_claim_without_evidence_is_unknown(self):
        assert _claim_status("source_backed", "   ") == "unknown"

    def test_other_claims_are_unknown(self):
        assert _claim_status("llm_generated", "evidence") == "unknown"
        assert _claim_status("", "") == "unknown"

    def test_inferred_backing_maps_to_unknown(self):
        """inferred → unknown（保守的マップ, D1-2）。"""
        assert _backing_status("inferred") == "unknown"
        assert _backing_status("review_required") == "unknown"
        assert _backing_status("partially_source_backed") == "unknown"
        assert _backing_status("") == "unknown"
        assert _backing_status("source_backed") == "indirectly_supported"


class TestHumanOnlyVocabularyGuard:
    def test_builder_cannot_write_directly_verified(self):
        """directly_verified の行が自動生成されない（受け入れ条件・人間専用語彙）。"""
        assert "directly_verified" not in _BUILDER_ALLOWED_STATUSES
        assert "refuted" not in _BUILDER_ALLOWED_STATUSES

    def test_upsert_preserves_human_entries(self):
        """人間が触った行の verification_status を builder が上書きしない。"""
        # UPSERT の CASE 条件: unknown かつ scopes 空のときのみ EXCLUDED を採用
        assert re.search(
            r"verification_status\s*=\s*CASE[\s\S]+?'unknown'[\s\S]+?"
            r"verification_scopes\s*=\s*'\[\]'::jsonb[\s\S]+?"
            r"ELSE epistemic_ledger\.verification_status",
            _SRC,
        )

    def test_upsert_is_idempotent(self):
        """再実行しても行が重複しない（ON CONFLICT による UPSERT）。"""
        assert "ON CONFLICT (target_id, target_type) DO UPDATE" in _SRC

    def test_debug_layer_nodes_excluded(self):
        assert re.search(r'if layer == "debug":\s*\n\s*continue', _SRC)
