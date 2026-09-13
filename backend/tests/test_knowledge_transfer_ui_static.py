"""知識の転用層（Phase 4）管理UIの静的ガードレール。

正本: `docs/features/knowledge_transfer_design.md`（§4.3 取り込み UI / §6 参照の健全性 /
§8 D層・C層の表現語彙・判断 T-2 / T-3・不変条項 KT2 / KT5 / KT7）。

検査するのは「画面が守らなければならない構造」だけで、文言の細部は追わない:

- **束の取り込み**: dry-run（確認）→ 確定（取り込む）の 2 段であること・確認を通さずに
  取り込みが押せないこと・live 行があるときは `replace` を教員が明示すること。
- **参照の健全性**: 3 状態の事実文チップがあること・件数バッジ / 比率を作らないこと・
  「切れがあります」を警告色にしないこと・詳細はモーダルに置くこと。
- **D層 / C層**: 疑義の向き・根拠の線・引用の意図の担体とアンカーがあること。
- **横断**: ポーリング（setInterval）を作らないこと・学習側 `app.js` を巻き込まないこと。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "frontend" / "public" / "js"
IMPORT_JS = JS_DIR / "admin-knowledge-import.js"
ADMIN_JS = JS_DIR / "admin.js"
DOUBT_JS = JS_DIR / "doubt-atlas.js"
STUDIO_JS = JS_DIR / "admin-lecture-studio.js"
LEARNING_JS = JS_DIR / "app.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    """`function <name>` から最初の2スペース閉じ括弧までを切り出す（既存テストと同型）。"""
    m = re.search(r"function " + re.escape(name) + r"[\s\S]+?\n  \}\n", src)
    assert m, f"function {name} not found or unterminated"
    return m.group(0)


def _strip_comments(src: str) -> str:
    """コメントを落としたコード本体（設計書の引用・記法説明を検査対象から外す）。"""
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _anchor_present(src: str, anchor: str) -> bool:
    """アンカー担体（属性直書き / setAttribute のどちらでも良い）。"""
    return f'data-ui-anchor="{anchor}"' in src or (
        '"data-ui-anchor"' in src and f'"{anchor}"' in src
    )


class TestFilesAndWiring:
    def test_import_module_exists_and_is_wired(self):
        assert IMPORT_JS.exists()
        html = _read(ADMIN_HTML)
        assert "/js/admin-knowledge-import.js" in html
        # admin.js は最後に読む（DI 注入元より前にモジュールが居ること）。
        assert html.index("/js/admin-knowledge-import.js") < html.index("/js/admin.js")

    def test_import_module_exposes_window_namespace(self):
        src = _read(IMPORT_JS)
        assert "window.KnowledgeImport" in src
        assert "function init(" in src
        assert "function openModal(" in src

    def test_admin_js_injects_dependencies(self):
        src = _read(ADMIN_JS)
        assert "window.KnowledgeImport.init(" in src
        # zip は multipart で送るので JSON 強制の apiFetch ではなく apiFetchRaw を注入する。
        m = re.search(r"window\.KnowledgeImport\.init\(\{[\s\S]{0,300}?\}\)", src)
        assert m and "apiFetchRaw" in m.group(0)
        assert "onImported" in m.group(0)

    def test_import_module_is_es5(self):
        """ES5（開発ルール5）— let / const / アロー / テンプレートリテラルを使わない。"""
        src = _strip_comments(_read(IMPORT_JS))
        assert not re.search(r"(^|[^\w.])(let|const)\s+\w", src)
        assert "=>" not in src
        assert "`" not in src


class TestImportTwoStepFlow:
    """dry-run → 確定の 2 段（§4.3）。確認なしの実行経路を作らない。"""

    def test_dry_run_and_execute_are_separate_actions(self):
        src = _read(IMPORT_JS)
        assert "function runDryRun(" in src
        assert "function runImport(" in src
        assert "dry_run=" in src

    def test_dry_run_sends_true_and_execute_sends_false(self):
        block = _function_block(_read(IMPORT_JS), "importPath")
        assert 'dry_run=" + (dryRun ? "true" : "false")' in block

    def test_execute_requires_a_completed_dry_run(self):
        src = _read(IMPORT_JS)
        run = _function_block(src, "runImport")
        assert "state.plan" in run
        can = _function_block(src, "canSubmit")
        # 確認結果（plan）が無ければ押せない。
        assert "if (!state.plan) return false;" in can

    def test_reselecting_a_bundle_clears_the_confirmation(self):
        """束を選び直したら確認をやり直す（別の束の確認で実行させない）。"""
        block = _function_block(_read(IMPORT_JS), "openModal")
        assert 'el("ki-file").addEventListener("change"' in block
        assert "state.plan = null;" in block


class TestReplaceIsExplicit:
    """T-2: live 行があるときの置き換えは教員の明示（無言で上書きしない）。"""

    def test_replace_checkbox_is_gated_on_has_live_rows(self):
        src = _read(IMPORT_JS)
        plan = _function_block(src, "renderPlan")
        assert "has_live_rows" in plan
        assert "replaceRow.hidden" in plan

    def test_submit_is_blocked_until_replace_is_confirmed(self):
        block = _function_block(_read(IMPORT_JS), "canSubmit")
        assert "target.has_live_rows && !state.replace" in block

    def test_replace_confirmation_states_the_consequence(self):
        src = _read(IMPORT_JS)
        assert "この教材には解析結果があります。" in src
        assert "再解析と同じ規則で置き換わります" in src
        assert "教員が確定した状態は保たれます" in src

    def test_replace_flag_is_sent_only_on_execution(self):
        block = _function_block(_read(IMPORT_JS), "importPath")
        assert 'replace=" + (state.replace ? "true" : "false")' in block
        # dry-run では replace を送らない（確認は常に書き込み 0）。
        assert '(dryRun ? "" :' in block


class TestImportHonestyAndNumbers:
    """KT2 / KT5 / KT7: 候補として着地する事実・サーバの事実文・数値は件数だけ。"""

    def test_landing_as_unconfirmed_candidates_is_stated(self):
        src = _read(IMPORT_JS)
        assert "未確認（教員の確認待ち）の候補" in src

    def test_server_facts_are_rendered_verbatim(self):
        block = _function_block(_read(IMPORT_JS), "renderPlan")
        assert "plan.facts" in block
        assert "plan.warnings" in block

    def test_error_details_come_from_the_server(self):
        """422 / 409 / 404 の事実文はサーバの detail をそのまま出す（言い換えない）。"""
        block = _function_block(_read(IMPORT_JS), "detailLines")
        assert "detail.facts" in block
        assert "detail.message" in block
        assert "report" in block

    def test_no_score_or_ratio_vocabulary(self):
        """画面に出る文字列に評点・比率の語彙が無いこと（コメントは対象外）。"""
        src = _strip_comments(_read(IMPORT_JS))
        for banned in ("スコア", "％", "パーセント", "達成率", "進捗率", "信頼度"):
            assert banned not in src, banned

    def test_no_polling(self):
        src = _read(IMPORT_JS)
        assert "setInterval" not in src


class TestReferenceHealthChip:
    """T-3: 教材行は状態の事実文 1 行だけ。件数バッジ・比率を作らない。"""

    def test_chip_labels_cover_three_states(self):
        src = _read(ADMIN_JS)
        block = src[src.index("var REFERENCE_HEALTH_CHIP_LABELS = {"):]
        block = block[: block.index("};")]
        assert "参照: 問題なし" in block
        assert "参照: 切れがあります" in block
        assert "参照: 未確認" in block

    def test_chip_is_rendered_from_material_reference_health(self):
        block = _function_block(_read(ADMIN_JS), "materialReferenceHealthChipHtml")
        assert "m.reference_health" in block
        assert "health.status" in block
        # 不明な状態は「未確認」に倒す（推測で言い換えない）。
        assert "REFERENCE_HEALTH_CHIP_LABELS.unchecked" in block

    def test_broken_is_not_painted_as_a_warning(self):
        """「切れがある」は運用上の事実であって失敗ではない（警告色にしない）。"""
        block = _function_block(_read(ADMIN_JS), "materialReferenceHealthChipHtml")
        assert "warning" not in block
        assert "danger" not in block

    def test_chip_shows_no_counts(self):
        block = _function_block(_read(ADMIN_JS), "materialReferenceHealthChipHtml")
        assert "length" not in block
        assert "details" not in block


class TestReferenceHealthModal:
    """詳細（列挙）はモーダルに置き、その場で検査し直す（保存しない = KT5）。"""

    def test_modal_and_recheck_exist(self):
        src = _read(ADMIN_JS)
        assert "function openReferenceHealthModal(" in src
        assert "function loadReferenceHealth(" in src
        assert "function renderReferenceHealth(" in src

    def test_modal_reads_the_read_only_endpoint(self):
        block = _function_block(_read(ADMIN_JS), "loadReferenceHealth")
        assert "/reference-health" in block
        assert "method" not in block  # GET のみ（書き込み経路を作らない）

    def test_modal_lists_broken_references(self):
        block = _function_block(_read(ADMIN_JS), "renderReferenceHealth")
        assert "data.details" in block
        assert "row.ref" in block
        assert "from_label" in block

    def test_modal_states_that_the_result_is_not_saved(self):
        src = _read(ADMIN_JS)
        assert "検査結果は保存されません" in src

    def test_recheck_reruns_the_same_load(self):
        block = _function_block(_read(ADMIN_JS), "openReferenceHealthModal")
        assert "reference-health-recheck" in block
        assert "loadReferenceHealth(documentId)" in block


class TestAnchors:
    """アンカー担体（3点セットの1つ）。表への登録は test_admin_help_ui_anchors が見る。"""

    IMPORT_ANCHORS = (
        ("materials.row-import", ADMIN_JS),
        ("materials.import-modal", IMPORT_JS),
        ("materials.import-submit", IMPORT_JS),
    )
    HEALTH_ANCHORS = (
        ("materials.row-reference-health", ADMIN_JS),
        ("materials.reference-health-modal", ADMIN_JS),
        ("materials.reference-health-recheck", ADMIN_JS),
    )
    VOCAB_ANCHORS = (
        ("doubt-atlas.challenge-mode", DOUBT_JS),
        ("doubt-atlas.evidence-lines", DOUBT_JS),
        ("doubt-atlas.evidence-line-add", DOUBT_JS),
        ("lecture-studio.cite-intent", STUDIO_JS),
    )

    def test_every_new_anchor_has_a_carrier(self):
        for anchor, path in self.IMPORT_ANCHORS + self.HEALTH_ANCHORS + self.VOCAB_ANCHORS:
            assert _anchor_present(_read(path), anchor), f"{anchor} の担体が {path.name} に無い"

    def test_anchor_ids_are_registered(self):
        from core.help_kb import admin_ui_anchors

        for anchor, _path in self.IMPORT_ANCHORS + self.HEALTH_ANCHORS + self.VOCAB_ANCHORS:
            assert anchor in admin_ui_anchors.KNOWN_ADMIN_UI_ANCHOR_IDS
            assert anchor in admin_ui_anchors.ADMIN_UI_ANCHORS


class TestDoubtLayerVocabUi:
    """P4-5: 疑義の向き・根拠の線（人間の記帳専用）。"""

    def test_challenge_form_sends_challenge_mode(self):
        block = _function_block(_read(DOUBT_JS), "bindChallengeForm")
        assert "challenge_mode" in block
        assert "CHALLENGE_MODE_LABELS" in block

    def test_challenge_card_shows_the_mode(self):
        src = _read(DOUBT_JS)
        assert "c.challenge_mode_label" in src

    def test_evidence_lines_section_exists_with_empty_fact(self):
        block = _function_block(_read(DOUBT_JS), "evidenceLinesSectionHtml")
        assert "根拠の線はまだ記帳されていません。" in block
        assert "doubt-muted" in block  # 空欄は警告色にしない

    def test_evidence_line_form_posts_the_declared_fields(self):
        block = _function_block(_read(DOUBT_JS), "bindEvidenceLineForm")
        for field in ("line_kind", "reason", "evidence_ids", "claim_ids", "equation_ids"):
            assert field in block, field
        assert "/evidence-lines" in block
        # 帰属はサーバが認証ユーザーから採る（フォームに記帳者欄を作らない）。
        assert "recorded_by" not in block

    def test_evidence_lines_have_no_ai_candidate_path(self):
        """根拠の線は人間の記帳専用 — AI 候補の生成ボタンを作らない。"""
        block = _function_block(_read(DOUBT_JS), "evidenceLinesSectionHtml")
        assert "AI" not in block
        assert "candidate" not in block

    def test_evidence_lines_have_no_delete_button(self):
        """KT5: 削除しない（訂正は PATCH）。"""
        block = _function_block(_read(DOUBT_JS), "evidenceLinesSectionHtml")
        assert "削除" not in block


class TestCitationIntentUi:
    """P4-5: 引用の意図は任意。未選択なら送らない（NULL = 記録なし）。"""

    def test_cite_selector_offers_intent(self):
        block = _function_block(_read(STUDIO_JS), "lsOpenCiteSelector")
        assert "data-cite-intent" in block
        assert "LS_CITATION_INTENT_ORDER" in block

    def test_intent_is_omitted_when_not_selected(self):
        block = _function_block(_read(STUDIO_JS), "lsOpenCiteSelector")
        assert "if (intent) body.citation_intent = intent;" in block

    def test_unselected_option_is_labelled_as_not_recorded(self):
        block = _function_block(_read(STUDIO_JS), "lsOpenCiteSelector")
        assert "記録しない" in block


class TestLearningSideUntouched:
    """KT7 の運用面: この層に学習者向け表示は無い（app.js を巻き込まない）。"""

    def test_learning_app_has_no_transfer_layer_hooks(self):
        src = _read(LEARNING_JS)
        for token in (
            "import-bundle",
            "reference-health",
            "KnowledgeImport",
            "citation_intent",
            "evidence_lines",
            "challenge_mode",
        ):
            assert token not in src, token
