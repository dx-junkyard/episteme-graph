"""TensionMiningAgent Stage 2/3 — API・集計・冪等性・プライバシーの検証。

既存テスト（test_endorsement_sharing.py）と同様、SQL/ソースの静的検証 +
純粋関数のユニットテストで構成する。

観点（設計書 §11-6/7/8）:
- API: confirm/dismiss の状態遷移、本人以外アクセス不可、digest に confidence 数値が漏れない
- 集計: candidate/dismissed が教員側に出ない、k-匿名化
- 冪等性: 同一窓の再実行スキップ（analyzed_at を先に立てる）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

MAIN = ROOT / "backend" / "api" / "main.py"
MIGRATION = ROOT / "backend" / "db" / "022_tension.sql"
LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
SERVICES = ROOT / "backend" / "api" / "services.py"
WORKER = ROOT / "backend" / "core" / "tension" / "worker.py"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMigration022:
    def test_reference_sql_defines_indexes_only(self):
        sql = _read(MIGRATION)
        assert "CREATE INDEX IF NOT EXISTS idx_interest_traces_kind_status" in sql
        assert "CREATE INDEX IF NOT EXISTS idx_interest_traces_candidate" in sql
        # 新テーブル・列追加はゼロ（設計書 §3）
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE" not in sql

    def test_main_applies_migration_022(self):
        source = _read(MAIN)
        assert "Migration 022" in source
        assert "idx_interest_traces_kind_status" in source
        assert "idx_interest_traces_candidate" in source
        assert "Migrations (002-" in source  # 以降の migration 追加 (023...) でも壊れないよう prefix で確認


class TestVocabulary:
    def test_interest_kinds_and_statuses_extended(self):
        # services.py は import に neo4j 等を要するため静的検証（既存テストと同方式）
        source = _read(SERVICES)
        kinds_block = source.split("_INTEREST_KINDS = (")[1].split(")")[0]
        assert '"tension"' in kinds_block
        statuses_block = source.split("_TRACE_STATUSES = (")[1].split(")")[0]
        for status in ("candidate", "dismissed", "articulated", "connected", "abstracted",
                       "open", "revisited", "resolved"):
            assert f'"{status}"' in statuses_block, status


class TestRoutes:
    def test_tension_routes_exist(self):
        source = _read(LEARNING)
        assert '"/courses/{course_id}/tension/digest"' in source
        assert '"/tension/{trace_id}/confirm"' in source
        assert '"/tension/{trace_id}/dismiss"' in source
        assert '"/tension/{trace_id}/connect"' in source

    def test_chat_route_wires_prefilter_hint(self):
        source = _read(LEARNING)
        assert "judge_tension_hint" in source
        assert '"tension_hint": _tension_hint' in source
        assert "maybe_schedule_tension_mining" in source


class TestOwnershipAndPrivacy:
    def test_state_transitions_are_owner_scoped(self):
        """confirm/dismiss/connect の UPDATE は必ず user_id 一致条件を持つ（本人以外は 404/400）。"""
        source = _read(SERVICES)
        for fn in ("confirm_tension_trace", "dismiss_tension_trace", "connect_tension_trace"):
            body = source.split(f"def {fn}")[1].split("\ndef ")[0]
            assert "user_id = CAST(:uid AS uuid)" in body, fn

    def test_confirm_transitions_only_from_candidate(self):
        source = _read(SERVICES)
        body = source.split("def confirm_tension_trace")[1].split("\ndef ")[0]
        assert "status = 'candidate'" in body
        assert "articulated" in body  # learner_text ありなら articulated

    def test_dismiss_keeps_the_row(self):
        """dismiss は削除でなく status='dismissed'（P4: 情報を落とさない）。"""
        source = _read(SERVICES)
        body = source.split("def dismiss_tension_trace")[1].split("\ndef ")[0]
        assert "DELETE" not in body.upper() or "status = 'dismissed'" in body
        assert "status = 'dismissed'" in body

    def test_digest_does_not_expose_confidence(self):
        """digest のレスポンス item に confidence の数値を入れない。

        SQL の閾値フィルタ（payload->>'confidence'）は許容するが、
        レスポンス dict のキーとしての "confidence" は禁止。
        """
        source = _read(SERVICES)
        body = source.split("def get_tension_digest")[1].split("\ndef ")[0]
        assert '"confidence"' not in body

    def test_state_changes_are_audited(self):
        source = _read(SERVICES)
        assert "_record_tension_event" in source
        assert "'tension'" in source.split("def _record_tension_event")[1].split("\ndef ")[0]

    def test_candidates_hidden_from_trace_view(self):
        """未確定候補・棄却はダイジェスト経由でのみ提示（問いの軌跡に出さない）。"""
        source = _read(SERVICES)
        body = source.split("def get_interest_traces")[1].split("\ndef ")[0]
        assert "NOT (kind = 'tension' AND status IN ('candidate', 'dismissed'))" in body


class TestAggregation:
    def test_teacher_dashboard_counts_only_owned_statuses(self):
        """candidate/dismissed は教員集計に入れない — 本人が引き受けたものだけが違和感。"""
        source = _read(SERVICES)
        body = source.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        assert "IN ('open', 'articulated', 'connected', 'abstracted')" in body
        assert "'candidate'" not in body.split("kind = 'tension'")[1].split("GROUP BY")[0]

    def test_k_anonymity_suppresses_small_cells(self):
        source = _read(SERVICES)
        body = source.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        assert "_TENSION_K_ANONYMITY = 3" in body
        assert "learners < _TENSION_K_ANONYMITY" in body

    def test_heatmap_has_no_personal_fields(self):
        source = _read(SERVICES)
        body = source.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        heat_block = body.split("tension_heatmap.append(")[1].split(")")[0]
        for forbidden in ("user_id", "evidence", "paraphrase", "learner_text", '"text"'):
            assert forbidden not in heat_block


class TestWorkerIdempotencyAndCost:
    def test_analyzed_at_claimed_before_llm_call(self):
        """冪等性: analyzed_at を先に立ててから LLM を呼ぶ（同一窓の並行再実行をスキップ）。"""
        source = _read(WORKER)
        body = source.split("def run_tension_mining")[1]
        claim_pos = body.find("SET analyzed_at = now()")
        llm_pos = body.find("_check_and_count_llm_call")
        assert 0 < claim_pos < llm_pos
        assert "analyzed_at IS NULL" in body

    def test_cost_caps_from_settings(self):
        source = _read(WORKER)
        assert "tension_max_calls_per_session" in source
        assert "tension_max_calls_per_day" in source

    def test_repair_failure_persists_unclassified_row(self):
        """2回失敗は破棄せず unclassified / confidence=0.0 / repair_failed=true で1行残す（P4）。"""
        source = _read(WORKER)
        assert '"tension_type": "unclassified"' in source
        assert '"repair_failed": True' in source
        assert '"confidence": 0.0' in source

    def test_candidates_are_saved_as_candidate_status(self):
        """P1: LLM 出力は常に status='candidate'（本人の confirm なしに確定しない）。"""
        source = _read(WORKER)
        insert_block = source.split("def _insert_candidate")[1].split("\ndef ")[0]
        assert "'candidate'" in insert_block
        assert "'tension'" in insert_block

    def test_trigger_thresholds(self):
        source = _read(WORKER)
        assert "HINT_BATCH_THRESHOLD = 5" in source
        assert "SESSION_IDLE_MINUTES = 20" in source


class TestDigestServiceContract:
    def test_digest_threshold_and_limit(self):
        source = _read(SERVICES)
        assert "_TENSION_DIGEST_MIN_CONFIDENCE = 0.55" in source
        assert "_TENSION_DIGEST_MAX_ITEMS = 3" in source


class TestPayloadConversion:
    def test_candidate_to_payload_matches_design(self):
        from core.tension.schema import TargetRefs, TensionCandidate, candidate_to_payload

        cand = TensionCandidate(
            tension_type="definition_unease",
            evidence_quote="なんとなく腑に落ちない",
            turn_ids=["msg_0002"],
            target_refs=TargetRefs(topic_id="t_03"),
            paraphrase="引っかかりが残っているのかもしれません",
            confidence=0.74,
            reason="hedge + revisit",
        )
        payload = candidate_to_payload(cand, context_label="第1章 · トピック", overall_tier="approved")
        assert payload["text"] == "なんとなく腑に落ちない"
        assert payload["tension_type"] == "definition_unease"
        assert payload["detector_version"] == "tension-mining/v1"
        assert payload["overall_tier_at_detection"] == "approved"
        assert payload["learner_text"] == ""
        assert payload["target_refs"]["topic_id"] == "t_03"

    def test_payload_text_capped_at_500(self):
        from core.tension.schema import TargetRefs, TensionCandidate, candidate_to_payload

        cand = TensionCandidate(
            tension_type="unclassified", evidence_quote="あ" * 600, turn_ids=["m"],
            target_refs=TargetRefs(), paraphrase="のかも", confidence=0.6, reason="r",
        )
        assert len(candidate_to_payload(cand)["text"]) == 500


class TestFrontendCard:
    def test_digest_card_and_actions_exist(self):
        source = _read(APP_JS)
        assert "renderTensionDigestCard" in source
        assert "data-tension-confirm" in source
        assert "data-tension-dismiss" in source
        assert "data-tension-defer" in source
        assert "そう、これ" in source
        assert "違う" in source
        assert "あとで" in source
        # confirm 後にだけ任意の一行編集欄
        assert "自分の言葉で言い直すと?" in source

    def test_no_interruption_during_chat(self):
        """会話中の割り込み表示はしない: カードは進捗タブ（renderProblemTrails）でのみ描画。"""
        source = _read(APP_JS)
        # 出現は関数定義 + renderProblemTrails 内の呼び出し1箇所のみ
        assert source.count("renderTensionDigestCard()") == 2
        trails_body = source.split("function renderProblemTrails()")[1].split("\n  function ")[0]
        assert "renderTensionDigestCard()" in trails_body
