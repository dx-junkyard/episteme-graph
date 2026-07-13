"""D1-3 / D2-4 / D3-1〜D3-3 / DX-2 — doubt API の契約テスト（ソース検査方式）。

routes/doubt.py は `from dependencies import ...` の flat import を含むため、
既存テストの流儀（test_issue_128 等）に合わせてソーステキストの契約検査で守る。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_SRC = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
_MAIN = (_BACKEND / "api" / "main.py").read_text(encoding="utf-8")
_ADMIN = (_BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from tests.guardrail_helpers import read_migration_sql  # noqa: E402

# migration 029〜033（D層）の番号 → テーブル名。正本は backend/db/0NN_*.sql
# （main.py のインライン DDL ではない, 2026-07 migration 正本一本化）。
_D_LAYER_MIGRATION_TABLES = {
    29: "epistemic_ledger",
    30: "assumption_nodes",
    31: "challenges",
    32: "verification_proposals",
    33: "counterfactual_sessions",
}


class TestRouterRegistration:
    def test_admin_router_included(self):
        # Tier 3-17c: admin.py 経由の二段ネストではなく main.py から
        # `/api/admin` prefix で直接マウントするフラット構造。
        assert "from routes import doubt as doubt_routes" in _MAIN
        assert 'app.include_router(doubt_routes.admin_router, prefix="/api/admin")' in _MAIN

    def test_learning_router_registered(self):
        assert "doubt_routes.learning_router" in _MAIN

    def test_migrations_029_to_033_present(self):
        for number, name in _D_LAYER_MIGRATION_TABLES.items():
            sql = read_migration_sql(_BACKEND, number)
            assert f"CREATE TABLE IF NOT EXISTS {name}" in sql


class TestLedgerEndpoints:
    def test_endpoints_exist(self):
        for path in (
            '"/ledger/{target_type}/{target_id}"',
            '"/ledger/{target_type}/{target_id}/scopes"',
            '"/ledger/{target_type}/{target_id}/scopes/{scope_id}"',
            '"/ledger/{target_type}/{target_id}/verification-status"',
            '"/courses/{course_id}/ledger-summary"',
            '"/courses/{course_id}/naive-signals"',
            '"/courses/{course_id}/assumption-atlas"',
            '"/courses/{course_id}/open-assumptions"',
        ):
            assert path in _SRC, f"missing endpoint {path}"

    def test_directly_verified_requires_scope(self):
        """スコープなしの directly_verified 昇格は 422（全称検証の構造的禁止）。"""
        block = re.search(
            r"def put_verification_status[\s\S]+?def ", _SRC
        ).group(0)
        assert "DIRECTLY_VERIFIED" in block
        assert "len(scopes) < 1" in block
        assert "422" in block

    def test_scope_requires_axis_evidence_reason(self):
        block = re.search(r"def add_ledger_scope[\s\S]+?def ", _SRC).group(0)
        assert "422" in block
        assert "any(axes)" in block
        assert "reason" in block

    def test_teacher_role_required_for_admin_writes(self):
        """STUDENT ロールは書き込み不可（_require_teacher 依存）。"""
        for fn in ("add_ledger_scope", "put_verification_status", "confirm_assumption",
                   "create_challenge", "create_verification_proposal",
                   "save_counterfactual_session"):
            block = re.search(r"def " + fn + r"\([\s\S]+?\) -> dict", _SRC)
            assert block, f"missing {fn}"
            assert "_require_teacher" in block.group(0), f"{fn} must require teacher"

    def test_all_writes_audited(self):
        """全書き込みが theory_review_events に記録される（_record_doubt_event）。

        entity_type は core/schema.py の AUDIT_ENTITY_TYPES カタログ定数を使う
        （提案7: 監査 entity_type の一本化）。call site が catalog 定数を参照して
        いること・カタログ側の値が期待どおりであることの双方を検証する。
        """
        from core.schema import (
            AUDIT_ENTITY_ASSUMPTION,
            AUDIT_ENTITY_CHALLENGE,
            AUDIT_ENTITY_COUNTERFACTUAL_SESSION,
            AUDIT_ENTITY_LEDGER,
            AUDIT_ENTITY_VERIFICATION_PROPOSAL,
        )

        assert _SRC.count("_record_doubt_event(") >= 10
        expected = {
            "AUDIT_ENTITY_LEDGER": ("ledger", AUDIT_ENTITY_LEDGER),
            "AUDIT_ENTITY_ASSUMPTION": ("assumption", AUDIT_ENTITY_ASSUMPTION),
            "AUDIT_ENTITY_CHALLENGE": ("challenge", AUDIT_ENTITY_CHALLENGE),
            "AUDIT_ENTITY_VERIFICATION_PROPOSAL": ("verification_proposal", AUDIT_ENTITY_VERIFICATION_PROPOSAL),
            "AUDIT_ENTITY_COUNTERFACTUAL_SESSION": ("counterfactual_session", AUDIT_ENTITY_COUNTERFACTUAL_SESSION),
        }
        for const_name, (literal, value) in expected.items():
            assert const_name in _SRC, f"{const_name} must be referenced in doubt.py"
            assert value == literal, f"{const_name} catalog value drifted from {literal!r}"


class TestCandidateConfirmationFlow:
    def test_candidate_confirm_assigns_teacher_attribution(self):
        block = re.search(r"def confirm_scope_candidate[\s\S]+?\n@admin_router", _SRC).group(0)
        # 確定時に帰属が教員に付く
        assert '"recorded_by": str(current_user.get("id")' in block

    def test_candidate_dismiss_keeps_row(self):
        """却下は dismissed で保持（P4）— 配列から要素を削除しない。"""
        block = re.search(r"def dismiss_scope_candidate[\s\S]+?\n@admin_router", _SRC).group(0)
        assert '"dismissed"' in block
        assert ".remove(" not in block and "del " not in block

    def test_assumption_confirm_creates_ledger_row_untested(self):
        block = re.search(r"def confirm_assumption[\s\S]+?\n@admin_router", _SRC).group(0)
        assert "INSERT INTO epistemic_ledger" in block
        assert "'untested'" in block

    def test_assumption_confirm_requires_reason(self):
        block = re.search(r"def confirm_assumption[\s\S]+?\n@admin_router", _SRC).group(0)
        assert "reason" in block and "422" in block


class TestChallengeEndpoints:
    def test_challenge_requires_type_and_reason(self):
        block = re.search(r"def create_challenge[\s\S]+?\n@admin_router", _SRC).group(0)
        assert "422" in block
        assert "challenge_type" in block
        assert "reason" in block

    def test_withdraw_is_status_transition(self):
        """withdraw は行削除ではなく status 遷移（P4）。"""
        block = re.search(r"def withdraw_challenge[\s\S]+?\n#", _SRC).group(0)
        assert "'withdrawn'" in block
        assert "DELETE FROM" not in block

    def test_proposal_promotes_challenge(self):
        block = re.search(r"def create_verification_proposal[\s\S]+?\n@admin_router", _SRC).group(0)
        assert "'led_to_verification'" in block


class TestCounterfactualEndpoints:
    def test_group_visibility_respected(self):
        block = re.search(r"def list_counterfactual_sessions[\s\S]+?\n@admin_router", _SRC).group(0)
        assert "group_members" in block
        assert "'public'" in block

    def test_patch_owner_only(self):
        block = re.search(r"def patch_counterfactual_session[\s\S]+?\n#", _SRC).group(0)
        assert "403" in block


class TestMetricsEndpoint:
    def test_system_admin_only(self):
        block = re.search(r"def get_doubt_metrics[\s\S]+?return", _SRC).group(0)
        assert "_require_system_admin" in block


class TestLearnerEndpoints:
    def test_learner_endpoints_are_read_only(self):
        """学習者向け router に書き込み系エンドポイントが存在しない。"""
        learner_part = _SRC[_SRC.index("learning_router.get"):] if "learning_router.get" in _SRC else ""
        assert "@learning_router.post" not in _SRC
        assert "@learning_router.put" not in _SRC
        assert "@learning_router.delete" not in _SRC
        assert "@learning_router.patch" not in _SRC

    def test_learner_ledger_fails_closed_on_missing_row(self):
        block = re.search(r"def get_learner_ledger_line[\s\S]+?def ", _SRC).group(0)
        assert "404" in block

    def test_learner_scopes_hide_attribution(self):
        """学習者へは記帳者 ID を出さない。"""
        block = re.search(r"def get_learner_ledger_line[\s\S]+?def ", _SRC).group(0)
        assert '"condition", "domain", "precision", "system"' in block

    def test_learner_open_assumptions_hide_names(self):
        block = re.search(r"def get_learner_open_assumptions[\s\S]+$", _SRC).group(0)
        assert "include_challenger_names=False" in block
