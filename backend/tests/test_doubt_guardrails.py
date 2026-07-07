"""DX-1 — D層ガードレールの自動テスト（構想 §8「守るべき一線」を構造的に守る）。

- AI断定禁止: 全 LLM 出力経路が candidate 系 status 以外に直接書けない
- 匿名疑義なし: challenger_id / reason の NOT NULL 制約
- 数値非表示: API レスポンスに load_score 生値・confidence 生値が含まれない
- 監視にしない: naive-signals の k-匿名（n<3 非含有）
- P4: dismiss / withdraw 系がすべて行保持
- 文言リント: フロント資産に煽り語彙が含まれない
"""

from __future__ import annotations

import re
from pathlib import Path

from core.doubt.naive_signal import aggregate_entries
from core.doubt.schema import ScopeCandidate

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_ROUTES_SRC = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
_MAIN_SRC = (_BACKEND / "api" / "main.py").read_text(encoding="utf-8")


class TestAINeverAsserts:
    """AIに疑わせない: LLM 出力は常に candidate。確定は人間のみ。"""

    def test_scope_candidate_model_defaults_to_candidate(self):
        assert ScopeCandidate().status == "candidate"

    def test_scope_candidate_worker_never_writes_scopes(self):
        """worker は scope_candidates 列にのみ書き、verification_scopes 本体に書かない。"""
        src = (_BACKEND / "core" / "doubt" / "scope_candidates" / "worker.py").read_text(encoding="utf-8")
        writes = re.findall(r"SET\s+([\s\S]+?)WHERE", src)
        for write in writes:
            assert "verification_scopes" not in write, (
                "scope candidate worker must never touch verification_scopes"
            )

    def test_scope_candidate_worker_never_writes_verification_status(self):
        src = (_BACKEND / "core" / "doubt" / "scope_candidates" / "worker.py").read_text(encoding="utf-8")
        assert "verification_status" not in src

    def test_assumption_worker_only_updates_candidates(self):
        """正規化はstatus='candidate' の行にのみ statement を書く（確定しない）。"""
        src = (_BACKEND / "core" / "doubt" / "assumption_mining" / "worker.py").read_text(encoding="utf-8")
        update = re.search(r"UPDATE assumption_nodes\s+SET statement[\s\S]+?WHERE[\s\S]+?\"\"\"", src)
        assert update and "status = 'candidate'" in update.group(0)
        assert "SET status" not in src  # worker は status 遷移を行わない

    def test_ledger_builder_never_writes_human_only_statuses(self):
        from core.doubt.ledger_builder import _BUILDER_ALLOWED_STATUSES
        assert "directly_verified" not in _BUILDER_ALLOWED_STATUSES


class TestNoAnonymousChallenge:
    """匿名疑義なし: 帰属と理由のない疑義は構造的に作れない。"""

    def test_migration_constraints(self):
        table = re.search(r"CREATE TABLE IF NOT EXISTS challenges[\s\S]+?\)\s*\"\"\"", _MAIN_SRC).group(0)
        assert "challenger_id  UUID NOT NULL" in table
        assert re.search(r"reason\s+TEXT NOT NULL CHECK \(reason <> ''\)", table)

    def test_reference_sql_constraints(self):
        sql = (_BACKEND / "db" / "031_challenges.sql").read_text(encoding="utf-8")
        assert "challenger_id  UUID NOT NULL" in sql
        assert "CHECK (reason <> '')" in sql

    def test_api_rejects_empty_reason(self):
        block = re.search(r"def create_challenge[\s\S]+?\n@admin_router", _ROUTES_SRC).group(0)
        assert re.search(r"if not body\.reason\.strip\(\):\s*\n\s*raise HTTPException\(status_code=422", block)


class TestNoRawNumbersExposed:
    """数値非表示: レスポンスに load_score 生値・confidence 生値を含めない。"""

    def test_scope_serializer_has_no_confidence(self):
        block = re.search(r"def _scope_out[\s\S]+?\n\ndef ", _ROUTES_SRC).group(0)
        assert '"confidence"' not in block

    def test_candidate_serializer_has_no_confidence(self):
        block = re.search(r"def _candidate_out[\s\S]+?\n\ndef ", _ROUTES_SRC).group(0)
        assert '"confidence"' not in block

    def test_no_load_score_key_in_responses(self):
        """レスポンス dict のキーとして load_score を返さない（SELECT はよい）。"""
        assert '"load_score"' not in _ROUTES_SRC
        assert "'load_score'" not in _ROUTES_SRC

    def test_assumption_serializer_has_no_confidence(self):
        block = re.search(r"def _assumption_out[\s\S]+?\n\n", _ROUTES_SRC).group(0)
        assert '"confidence"' not in block

    def test_load_level_labels_only(self):
        assert '"load_level"' in _ROUTES_SRC


class TestKAnonymity:
    """監視にしない: naive signals は k=3・n<3 セル非表示・個人特定不可。"""

    def test_below_k_anchor_excluded(self):
        entries = [
            ("u1", "claim", "c1", "L", "premise"),
            ("u2", "claim", "c1", "L", "premise"),
        ]
        assert aggregate_entries(entries) == []

    def test_at_k_anchor_included_with_range_only(self):
        entries = [
            ("u1", "claim", "c1", "L", "premise"),
            ("u2", "claim", "c1", "L", "premise"),
            ("u3", "claim", "c1", "L", "premise"),
        ]
        signals = aggregate_entries(entries)
        assert len(signals) == 1
        assert signals[0]["count_range"] == "3-5"
        # 個人を特定できるフィールドが存在しない
        assert "user_id" not in str(signals)
        assert "u1" not in str(signals)

    def test_same_user_repeat_does_not_inflate(self):
        entries = [("u1", "claim", "c1", "L", "premise")] * 10
        assert aggregate_entries(entries) == []

    def test_doubt_type_cells_also_k_anonymous(self):
        entries = [
            ("u1", "claim", "c1", "L", "premise"),
            ("u2", "claim", "c1", "L", "premise"),
            ("u3", "claim", "c1", "L", "scope"),  # scope は n=1 → 非表示
            ("u4", "claim", "c1", "L", "premise"),
        ]
        signals = aggregate_entries(entries)
        assert len(signals) == 1
        doubt_types = [d["doubt_type"] for d in signals[0]["doubt_types"]]
        assert "scope" not in doubt_types
        assert "premise" in doubt_types

    def test_candidates_not_collected(self):
        """candidate 状態（本人未確定）の trace が集計に入らない（P1）— SQL 検査。"""
        src = (_BACKEND / "core" / "doubt" / "naive_signal.py").read_text(encoding="utf-8")
        # structure_anchor は learner_selected / confirmed のみ
        assert "learner_selected" in src and "confirmed" in src
        assert "llm_candidate" not in src
        # tension は本人が引き受けた status のみ
        assert '"open", "articulated", "connected", "abstracted"' in src


class TestP4NothingDeleted:
    """P4: dismiss / withdraw は保持。D層のコードは行を削除しない。"""

    def test_no_delete_statements_in_doubt_layer(self):
        doubt_dir = _BACKEND / "core" / "doubt"
        for path in doubt_dir.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "DELETE FROM" not in src, f"{path} must not delete rows"
        assert "DELETE FROM" not in _ROUTES_SRC

    def test_dismiss_and_withdraw_are_status_transitions(self):
        assert "SET status = 'dismissed'" in _ROUTES_SRC
        assert "SET status = 'withdrawn'" in _ROUTES_SRC


class TestNoHypeLanguage:
    """文言リント: 禁止語（煽り語彙）がフロント・APIレスポンス文言に含まれない。"""

    BANNED = ("疑え", "ノーベル賞", "危険地帯", "要注意ゾーン", "崩壊させよ")

    def _assets(self):
        return [
            _REPO / "frontend" / "public" / "js" / "doubt-atlas.js",
            _REPO / "frontend" / "public" / "js" / "app.js",
            _REPO / "frontend" / "public" / "js" / "admin.js",
            _REPO / "frontend" / "public" / "admin.html",
            _BACKEND / "api" / "routes" / "doubt.py",
            _BACKEND / "core" / "doubt" / "open_assumptions.py",
        ]

    def test_no_banned_words(self):
        for path in self._assets():
            src = path.read_text(encoding="utf-8")
            for word in self.BANNED:
                assert word not in src, f"banned word {word!r} in {path}"

    def test_axes_are_factual_labels(self):
        """地図の軸ラベルは事実記述のみ。"""
        assert '"検証スコープの被覆"' in _ROUTES_SRC
        assert '"依存の広がり"' in _ROUTES_SRC


class TestEmptyScopeIsNormal:
    """「空欄」はエラーではなく事実（D1-1/D1-3）。"""

    def test_unscoped_flag_in_atlas(self):
        assert '"unscoped"' in _ROUTES_SRC

    def test_migration_has_unscoped_index(self):
        assert "idx_epistemic_ledger_unscoped" in _MAIN_SRC

    def test_frontend_shows_empty_as_fact(self):
        src = (_REPO / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
        assert "検証スコープの記帳なし" in src
        # 警告色クラスを空欄表示に使わない（doubt-muted の静かな表示）
        assert "doubt-muted" in src
