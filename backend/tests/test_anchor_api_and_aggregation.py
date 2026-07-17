"""Structure-Anchored Questions Stage 1/2/3 — API・集計・冪等性・プライバシーの検証。

tension の test_tension_api_and_aggregation.py と同様、SQL/ソースの静的検証 +
純粋関数のユニットテストで構成する（services.py の import は外部依存の初期化コストが
大きいため、フルスタックの結合テストではなく静的検証に留める）。

観点:
- 記録: 方法A（learner_selected）が同期・非LLMで配線されている
- API: confirm/dismiss の本人スコープ、digest に confidence 数値が漏れない
- 集計: llm_candidate が教員側に出ない、k-匿名化、行 status を触らない
- 冪等性: payload.anchor_analyzed_at を先に立てる（tension の analyzed_at 列と競合しない）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

MIGRATION = ROOT / "backend" / "db" / "025_structure_anchor.sql"
LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
SERVICES = ROOT / "backend" / "api" / "services.py"
WORKER = ROOT / "backend" / "core" / "structure_anchor" / "worker.py"
EXPERIENCE = ROOT / "backend" / "core" / "learning_experience.py"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMigration025:
    def test_reference_sql_defines_indexes_only(self):
        """正本は backend/db/025_structure_anchor.sql（main.py のインライン DDL ではない）。"""
        sql = _read(MIGRATION)
        assert "idx_interest_traces_anchor_candidate" in sql
        assert "idx_interest_traces_anchor_pending" in sql
        # 新テーブル・列追加はゼロ（payload 拡張のみ）
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE" not in sql


class TestMethodAWiring:
    def test_chat_route_records_learner_selected_anchor(self):
        source = _read(LEARNING)
        assert "_learner_selected_anchor" in source
        assert "cited_chunk_ids" in source
        assert "maybe_schedule_anchor_mining" in source
        # 明示アンカーのある問いは方法Bの対象にしない
        assert "not _sel_anchor" in source

    def test_element_tap_path_records_anchor(self):
        """EXPLAIN_GRAPH_ELEMENT 経路でも learner_selected の問いが記録される。"""
        source = _read(LEARNING)
        branch = source.split('if body.action == "EXPLAIN_GRAPH_ELEMENT":')[1].split("# カジュアル対話モード")[0]
        assert "record_interest_trace" in branch
        assert "structure_anchor" in branch

    def test_confirm_prompt_is_gated(self):
        """方法C は tension_hint / 明示アンカーのときだけ・セッション上限つきで出す（P7）。"""
        source = _read(LEARNING)
        assert "check_and_count_confirm_prompt" in source
        assert "_tension_hint or _sel_anchor is not None" in source

    def test_record_interest_trace_returns_id(self):
        """方法Cの確定操作先として trace_id を返す（RETURNING id）。"""
        source = _read(SERVICES)
        body = source.split("def record_interest_trace")[1].split("\ndef ")[0]
        assert "RETURNING id" in body
        assert "-> str | None" in body


class TestRoutes:
    def test_anchor_routes_exist(self):
        source = _read(LEARNING)
        assert '"/courses/{course_id}/anchors/digest"' in source
        assert '"/anchors/{trace_id}/confirm"' in source
        assert '"/anchors/{trace_id}/dismiss"' in source


class TestOwnershipAndPrivacy:
    def test_state_transitions_are_owner_scoped(self):
        source = _read(SERVICES)
        for fn in ("confirm_anchor_trace", "dismiss_anchor_trace"):
            body = source.split(f"def {fn}")[1].split("\ndef ")[0]
            assert "user_id = CAST(:uid AS uuid)" in body, fn

    def test_row_status_is_never_touched(self):
        """帰属の確定/棄却は payload のみ更新し、行の status は変更しない。"""
        source = _read(SERVICES)
        for fn in ("confirm_anchor_trace", "dismiss_anchor_trace"):
            body = source.split(f"def {fn}")[1].split("\ndef ")[0]
            assert "SET status" not in body, fn

    def test_dismiss_keeps_the_anchor(self):
        """dismiss は削除でなく structure_anchor.status='dismissed'（P4）。"""
        source = _read(SERVICES)
        body = source.split("def dismiss_anchor_trace")[1].split("\ndef ")[0]
        assert "DELETE" not in body.upper()
        assert '"dismissed"' in body

    def test_digest_does_not_expose_confidence(self):
        source = _read(SERVICES)
        body = source.split("def get_anchor_digest")[1].split("\ndef ")[0]
        assert '"confidence"' not in body

    def test_state_changes_are_audited(self):
        """entity_type は core/schema.py の AUDIT_ENTITY_STRUCTURE_ANCHOR カタログ定数を使う（提案7）。"""
        from core.schema import AUDIT_ENTITY_STRUCTURE_ANCHOR

        assert AUDIT_ENTITY_STRUCTURE_ANCHOR == "structure_anchor"
        source = _read(SERVICES)
        assert "_record_anchor_event" in source
        body = source.split("def _record_anchor_event")[1].split("\ndef ")[0]
        assert "AUDIT_ENTITY_STRUCTURE_ANCHOR" in body

    def test_trace_view_shows_only_owned_attributions(self):
        """問いの軌跡には learner_selected/confirmed のみ載せる（llm_candidate は digest 経由。P1）。"""
        source = _read(EXPERIENCE)
        body = source.split("def build_traces_view")[1]
        assert '("learner_selected", "confirmed")' in body


class TestAggregation:
    def test_teacher_dashboard_counts_only_confirmed_attributions(self):
        source = _read(SERVICES)
        body = source.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        assert "IN ('learner_selected', 'confirmed')" in body
        assert "'llm_candidate'" not in body

    def test_k_anonymity_suppresses_small_cells(self):
        """k=3 の閾値は core/privacy.py の共通 k-匿名ゲート（提案8）に一本化されている。"""
        from core.privacy import K_ANONYMITY

        assert K_ANONYMITY == 3
        source = _read(SERVICES)
        assert "from core.privacy import K_ANONYMITY" in source
        body = source.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        assert "learners < K_ANONYMITY" in body

    def test_heatmap_has_no_personal_fields(self):
        """集計は語彙（anchor_type/stage/doubt_type）のみ。本文・自由記述ラベルを出さない。"""
        source = _read(SERVICES)
        body = source.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        heat_block = body.split("anchor_heatmap.append(")[1].split(")")[0]
        for forbidden in ("user_id", "evidence", "anchor_label", '"text"', "question"):
            assert forbidden not in heat_block


class TestWorkerIdempotencyAndCost:
    def test_claims_via_payload_key_not_analyzed_at_column(self):
        """冪等化は payload.anchor_analyzed_at で行う（analyzed_at 列は tension が使用中）。"""
        source = _read(WORKER)
        assert "anchor_analyzed_at" in source
        fetch = source.split("def _fetch_pending_questions")[1].split("\ndef ")[0]
        assert "payload->>'anchor_analyzed_at' IS NULL" in fetch
        assert "analyzed_at IS NULL\n" not in fetch  # 列は参照しない

    def test_cost_caps_are_independent_settings(self):
        source = _read(WORKER)
        assert "anchor_max_calls_per_session" in source
        assert "anchor_max_calls_per_day" in source
        assert "tension_max_calls" not in source  # tension の上限を横取りしない

    def test_repair_failure_marks_but_does_not_write_anchor(self):
        """2回修復失敗は印（anchor_repair_failed）のみ残し、誤帰属を書かない（P4）。"""
        source = _read(WORKER)
        body = source.split("if result.repair_failed:")[1].split("else:")[0]
        assert "anchor_repair_failed" in body
        assert "jsonb_set(payload, '{structure_anchor}'" not in body

    def test_candidate_write_never_overwrites_existing_anchor(self):
        source = _read(WORKER)
        assert "payload->'structure_anchor' IS NULL\n" in source

    def test_cost_cap_releases_claim_instead_of_losing_questions(self):
        """コスト上限時は claim（anchor_analyzed_at）を解放し、問いを無言で失わない（P4）。"""
        source = _read(WORKER)
        gate = source.split("if not _check_and_count_llm_call(")[1].split("course_row = ")[0]
        assert "payload - 'anchor_analyzed_at'" in gate


class TestConfirmDefaults:
    def test_confirm_without_anchor_creates_segment_fallback(self):
        """方法Cの1タップ申告（帰属未生成）は segment 縮退の最小アンカーを作る。"""
        source = _read(SERVICES)
        body = source.split("def confirm_anchor_trace")[1].split("\ndef ")[0]
        assert 'anchor_type="segment"' in body
        assert "learner_declared_via_confirm_prompt" in body


class TestFrontend:
    def test_app_js_wires_selection_and_confirm_ui(self):
        source = _read(APP_JS)
        assert "initSelectionAnchor" in source
        assert "selection_text" in source
        assert "renderAnchorDigestCard" in source
        assert "renderAnchorConfirmPrompt" in source
        assert "/anchors/" in source

    def test_admin_js_renders_anchor_heatmap_es5(self):
        source = _read(ADMIN_JS)
        assert "anchor_heatmap" in source
        block = source.split("anchor_heatmap")[1].split("body.innerHTML = html;")[0]
        # ES5 規約: 追加ブロックに const/let/アロー関数を持ち込まない
        for token in ("const ", "let ", "=>"):
            assert token not in block
