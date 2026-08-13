"""賭け金の台帳（Stakes Ledger, SL層）教員UIの静的ガードレール。

正本: `docs/features/stakes_ledger_design.md`（特に §1 不変条項・§9 UI・§12）。バックエンド
API（`backend/api/routes/doubt.py` / `backend/core/doubt/`）は並行実装中のため、本ファイルは
`frontend/public/js/doubt-atlas.js` の静的契約のみを検証する（`test_doubt_ui_wiring_static.py`
と同型のソース検査アプローチ — 関数ソースを正規表現で切り出し、その中の文字列を検査する）。

対象:
- SL-1 反証条件レジストリ（覆る条件の一覧・候補・手動記帳・AI候補再生成）
- SL-2 観測の反実仮想（「観測を仮に倒す」）
- SL-3 独立支持経路の事実文（支持線）
- SL-4 晴れ間投影の拡張（未検証合意リストの3列 + 到達可能フィルタ）+ 昇格ゲート
       （external_check 必須）+ proposal ステータス遷移
- 絶対制約: 閉世界語彙・数値非表示・doubt-muted・ES5・setInterval 禁止
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOUBT_ATLAS_JS = ROOT / "frontend" / "public" / "js" / "doubt-atlas.js"


def _read() -> str:
    return DOUBT_ATLAS_JS.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    """`function <name>` から最初の2スペース閉じ括弧までを切り出す（既存テストと同型）。"""
    m = re.search(r"function " + re.escape(name) + r"[\s\S]+?\n  \}\n", src)
    assert m, f"function {name} not found or unterminated"
    return m.group(0)


class TestFileExists:
    def test_file_exists(self):
        assert DOUBT_ATLAS_JS.exists()


class TestServerLabelTablesMirrored:
    """設計書 §9-5: 段階ラベルの二重表（サーバ/フロント）— サーバと同一の語彙を逐語で使う。"""

    def test_falsification_kind_labels_present_verbatim(self):
        src = _read()
        assert "var FALSIFICATION_KIND_LABELS = {" in src
        assert 'observation_value: "観測値そのもの"' in src
        assert 'auxiliary_hypothesis: "較正・装置などの補助仮説"' in src
        assert 'not_formulable: "反証条件を定式化できないという記帳"' in src

    def test_reachability_labels_present_verbatim(self):
        src = _read()
        assert "var REACHABILITY_LABELS = {" in src
        assert 'reachable: "現在の観測で検証可能"' in src
        assert 'next_generation: "次世代の装置・観測なら可能"' in src
        assert 'unreachable: "現状では困難"' in src
        assert 'unassessed: "未評価"' in src


class TestFalsificationSection:
    """SL-1: 「覆る条件」区画（台帳詳細ペイン）。"""

    def test_section_function_present(self):
        src = _read()
        assert "function falsificationSectionHtml(entry) {" in src

    def test_wired_from_both_ledger_render_branches(self):
        """renderLedgerInto の両分岐（台帳行あり/なし）から falsificationSectionHtml を呼ぶ。"""
        src = _read()
        assert src.count("falsificationSectionHtml(") >= 2
        assert src.count("bindFalsificationSection(container, targetType, targetId)") >= 2

    def test_empty_state_is_muted_fact_not_error(self):
        src = _read()
        block = _function_block(src, "falsificationSectionHtml")
        assert "覆る条件はまだ記帳されていません。" in block
        assert '"doubt-muted"' in block

    def test_confirmed_condition_attributed_to_teacher(self):
        src = _read()
        block = _function_block(src, "falsificationSectionHtml")
        assert "教員の記帳" in block

    def test_manual_record_endpoint_and_method(self):
        src = _read()
        block = _function_block(src, "bindFalsificationForm")
        assert "/falsification-conditions" in block
        assert 'method: "POST"' in block

    def test_manual_record_requires_reason_field(self):
        src = _read()
        block = _function_block(src, "bindFalsificationForm")
        assert 'data-f="reason"' in block

    def test_not_formulable_shows_evidence_optional_note(self):
        """kind に not_formulable を選ぶと根拠不要の説明文を表示する。"""
        src = _read()
        block = _function_block(src, "bindFalsificationForm")
        assert 'kindSelect.value === "not_formulable"' in block
        assert "根拠の引用は必須ではありません" in block

    def test_manual_record_surfaces_422_detail(self):
        src = _read()
        block = _function_block(src, "bindFalsificationForm")
        assert "res.status === 422" in block
        assert "data.detail" in block

    def test_candidate_confirm_and_dismiss_endpoints(self):
        src = _read()
        block = _function_block(src, "bindFalsificationCandidateButtons")
        assert "/falsification-candidates/" in block
        assert "/confirm" in block
        assert "/dismiss" in block

    def test_candidate_confirm_sends_reachability_selection(self):
        src = _read()
        block = _function_block(src, "bindFalsificationCandidateButtons")
        assert "data-doubt-falsification-reachability" in block
        assert "reachability" in block

    def test_candidate_buttons_prevent_double_submit(self):
        """ボタン連打防止: confirm/dismiss とも disabled ガードを持つ。"""
        src = _read()
        block = _function_block(src, "bindFalsificationCandidateButtons")
        assert block.count("if (btn.disabled) return;") >= 2
        assert block.count("btn.disabled = true;") >= 2

    def test_candidate_card_has_no_confidence_field(self):
        """API 契約: falsification_candidates は confidence を持たない（サーバ側で剥がれる）。"""
        src = _read()
        block = _function_block(src, "falsificationSectionHtml")
        assert "confidence" not in block

    def test_ai_refresh_button_calls_course_scoped_endpoint(self):
        src = _read()
        block = _function_block(src, "bindFalsificationRefresh")
        assert "/falsification-candidates/refresh" in block
        assert 'method: "POST"' in block
        assert "tabState.courseId" in block

    def test_refresh_button_label_present(self):
        src = _read()
        block = _function_block(src, "falsificationSectionHtml")
        assert "AIに候補を出してもらう" in block


class TestSupportLinesFact:
    """SL-3: 独立支持経路の事実文（数値は出さない・導出失敗はキーなしで非表示）。"""

    def test_function_present_and_reads_fact_line(self):
        src = _read()
        assert "function supportLinesFactHtml(entry) {" in src
        block = _function_block(src, "supportLinesFactHtml")
        assert "sl.fact_line" in block

    def test_missing_support_lines_renders_nothing(self):
        src = _read()
        block = _function_block(src, "supportLinesFactHtml")
        assert 'if (!sl || !sl.fact_line) return "";' in block

    def test_cut_members_only_shown_for_single_level(self):
        src = _read()
        block = _function_block(src, "supportLinesFactHtml")
        assert 'sl.level === "single"' in block
        assert "cut_members" in block

    def test_observation_roots_shown_without_identified_via_leaking_into_display(self):
        src = _read()
        block = _function_block(src, "supportLinesFactHtml")
        assert "observation_roots" in block
        assert "identified_via" not in block

    def test_wired_into_ledger_render(self):
        src = _read()
        assert "html += supportLinesFactHtml(entry);" in src


class TestCounterfactualObservationToggle:
    """SL-2: 「観測を仮に倒す」— 既存の前提トグルと併用可・崩壊語彙を使わない。"""

    def test_toggle_button_present_with_anchor(self):
        src = _read()
        block = _function_block(src, "counterfactualToolbarHtml")
        assert "観測を仮に倒す" in block
        assert 'data-ui-anchor="doubt-atlas.counterfactual-observation-toggle"' in block

    def test_aspect_labels_defined(self):
        src = _read()
        assert "var FALSIFICATION_ASPECT_LABELS = {" in src
        assert 'value: "観測値そのもの"' in src
        assert 'systematics: "較正・装置などの補助仮説"' in src

    def test_observation_targets_endpoint_used(self):
        src = _read()
        block = _function_block(src, "renderObservationToggleForm")
        assert "/observation-targets" in block

    def test_aspect_two_choice_radio(self):
        src = _read()
        block = _function_block(src, "renderObservationToggleForm")
        assert 'name="doubt-cf-obs-aspect"' in block
        assert 'value="value"' in block
        assert 'value="systematics"' in block

    def test_toggled_observations_sent_in_compute_and_sessions(self):
        src = _read()
        compute_block = _function_block(src, "recomputeCounterfactual")
        assert "toggled_observations" in compute_block
        status_block = _function_block(src, "updateCfStatus")
        assert "toggled_observations" in status_block  # セッション保存 body

    def test_no_collapse_wording_used_for_observation_toggle(self):
        src = _read()
        block = _function_block(src, "counterfactualToolbarHtml")
        assert "崩壊" not in block
        assert "この観測に依存する範囲" in block

    def test_can_combine_with_assumption_toggle(self):
        """前提トグルと観測トグルの併用可: 両方空のときのみ非破壊表示に戻す。"""
        src = _read()
        block = _function_block(src, "recomputeCounterfactual")
        assert "!cfState.toggledIds.length && !cfState.toggledObservations.length" in block


class TestOpenAssumptionsExtension:
    """SL-4: 未検証合意リストの3列追加 + 到達可能フィルタ。"""

    def test_new_fields_referenced_in_render(self):
        src = _read()
        block = _function_block(src, "renderOpenAssumptionsList")
        assert "has_falsification_condition" in block
        assert "reachability_summary" in block
        assert "support_line_level" in block

    def test_absence_is_muted_not_alarming(self):
        src = _read()
        block = _function_block(src, "renderOpenAssumptionsList")
        assert '"doubt-muted"' in block

    def test_filter_checkbox_default_unchecked(self):
        src = _read()
        assert 'id="doubt-open-reachable-filter"' in src
        assert 'data-ui-anchor="doubt-atlas.open-assumptions-reachable-filter"' in src
        assert "到達可能な反証条件がある項目だけ表示" in src
        # 既定 off: テンプレート内で checked 属性を付与していないこと
        idx = _read().index('id="doubt-open-reachable-filter"')
        window = _read()[max(0, idx - 80): idx + 80]
        assert "checked" not in window

    def test_filter_uses_client_side_render_not_refetch(self):
        """フィルタ切替は再取得しない（教員の明示操作でその場に絞り込む）。"""
        src = _read()
        assert (
            'document.getElementById("doubt-open-reachable-filter").addEventListener('
            '"change", renderOpenAssumptionsList);' in src
        )

    def test_items_are_cached_for_client_side_filtering(self):
        src = _read()
        assert "var lastOpenAssumptionItems = [];" in src
        block = _function_block(src, "renderOpenAssumptionsList")
        assert "onlyReachable" in block
        assert 'it.reachability_summary === "reachable"' in block


class TestProposalPromotionExternalCheck:
    """SL-8: challenge → proposal 昇格の external_check 必須化。"""

    def test_external_check_field_present(self):
        src = _read()
        block = _function_block(src, "bindChallengeActions")
        assert 'data-f="external_check"' in block
        assert "コーパス外で確認した文献・根拠を記録してください" in block

    def test_external_check_required_client_side(self):
        src = _read()
        block = _function_block(src, "bindChallengeActions")
        assert "if (!externalCheck)" in block

    def test_reachability_select_present_in_promotion_form(self):
        src = _read()
        block = _function_block(src, "bindChallengeActions")
        assert 'data-f="reachability"' in block
        assert "reachabilityOptionsHtml()" in block

    def test_request_body_includes_new_fields(self):
        src = _read()
        block = _function_block(src, "bindChallengeActions")
        assert "external_check: externalCheck" in block
        assert "reachability: reachability" in block

    def test_existing_proposal_text_requirement_preserved(self):
        """既存契約（test_doubt_ui_wiring_static.py）を壊さない: proposal 本文も必須。"""
        src = _read()
        block = _function_block(src, "bindChallengeActions")
        assert "if (!proposal)" in block
        assert "proposal:" in block


class TestProposalStatusTransitions:
    """proposal の status 遷移（最小: 既存カード内のボタン群）。"""

    def test_status_labels_and_buttons(self):
        src = _read()
        block = _function_block(src, "renderProposalStatusCard")
        assert "検証に着手" in block
        assert "完了を記録" in block
        assert "取り下げ" in block

    def test_uses_patch_to_proposals_endpoint(self):
        src = _read()
        block = _function_block(src, "renderProposalStatusCard")
        assert "/admin/doubt/proposals/" in block
        assert 'method: "PATCH"' in block

    def test_status_card_wired_after_successful_promotion(self):
        src = _read()
        block = _function_block(src, "bindChallengeActions")
        assert "renderProposalStatusCard(slot, proposalId, \"proposed\")" in block

    def test_button_prevents_double_submit(self):
        src = _read()
        block = _function_block(src, "renderProposalStatusCard")
        assert "if (btn.disabled) return;" in block
        assert "btn.disabled = true;" in block


class TestAbsoluteConstraints:
    """タスクの絶対制約（SL1 閉世界語彙 / 数値非表示 / doubt-muted / ES5 / ポーリング禁止）。"""

    _D_LAYER_BANNED = ("疑え", "ノーベル賞", "危険地帯", "要注意ゾーン", "崩壊させよ")
    _SL1_BANNED = ("この分野では未検証", "誰も検証していない", "世界初", "未踏")

    def test_d_layer_banned_words_absent(self):
        src = _read()
        for word in self._D_LAYER_BANNED:
            assert word not in src

    def test_sl1_closed_world_banned_words_absent(self):
        src = _read()
        for word in self._SL1_BANNED:
            assert word not in src

    def test_no_setinterval_polling(self):
        src = _read()
        assert "setInterval" not in src

    def test_no_confidence_or_path_count_numeric_leak(self):
        src = _read()
        assert "confidence" not in src
        assert "path_count" not in src

    def test_es5_style_var_and_function_only(self):
        """新規セクションが const/let/arrow-function を使わない（ES5 互換）。"""
        src = _read()
        new_fn_names = [
            "falsificationSectionHtml", "supportLinesFactHtml", "bindFalsificationSection",
            "bindFalsificationForm", "bindFalsificationCandidateButtons", "bindFalsificationRefresh",
            "renderObservationToggleForm", "addToggledObservation", "removeToggledObservation",
            "recomputeCounterfactual", "renderProposalStatusCard", "renderOpenAssumptionsList",
        ]
        for name in new_fn_names:
            block = _function_block(src, name)
            assert not re.search(r"\bconst\b", block), f"{name} uses const"
            assert not re.search(r"\blet\b", block), f"{name} uses let"
            assert "=>" not in block, f"{name} uses an arrow function"

    def test_uses_doubt_muted_for_empty_states(self):
        """空欄・記録なしは doubt-muted クラス（警告色にしない）。"""
        src = _read()
        assert "doubt-muted" in src
