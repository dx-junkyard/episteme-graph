"""学習画面 UI 再編 Phase 3（教材ホバー+ラッチ, 最終フェーズ）のフロントエンド静的
ガードレール。

設計正本: docs/features/learning_ui_inspect_hover_design.md
§5.1（教材の中身のホバー）/ §6（ホバー+ラッチの状態遷移）/ §7（アンカー優先ラダー）/
§8（痕跡・帰属・ルートの対応表）/ IH1〜IH10。

Phase 2（インスペクト・モード、test_learning_ui_phase2_static.py）が使うツールチップ
基盤（`#inspect-tooltip` 容器・`_positionInspectTooltip` の位置決め）を、本フェーズが
教材アンカー向けに一般化して再利用している前提で検証する（第2のツールチップ実装を
コピペしていないこと自体は明示的に検査しない — 関数の存在と挙動だけを見る）。

バックエンド（`_build_anchor_ladder_hint` / `_learner_selected_anchor` / 方法A の
`element_id`/`element_type`/`element_label`/`chunk_id`）は既に実装済み
（test_anchor_ladder_hint.py 参照）。ここでは frontend/public/{index.html,
js/app.js, css/styles.css} 側の静的契約のみを検証する:

1. IH3/IH5: アンカー添付コード（sendMessage）は「ピンが可視のまま」を唯一の真実源に
   する（`_latchState.pinned` かつ DOM 上のツールチップが非 hidden）。
2. IH2: 教材ツールチップ表示・ラッチ経路に fetch/apiFetch/sendMessage が無い。
3. ✕ ボタン / Esc キーでの手動クリアが配線されている。
4. ラッチのトリガーが「入力欄の空→非空」と「音声 VAD の発話検知」の2箇所にのみ存在する。
5. §4.1 マトリクス: インスペクト ON 中は教材ツールチップを出さない
   （showMaterialTooltip 自体のガード + インスペクトへ入る瞬間の強制クリア）。
6. §6.1: ラッチ後にマウスを動かしても値が変わらない（mouseover ハンドラが
   pinned 時に新規ツールチップ表示より前に early return する）。
7. IH8: 固定事実文「この部分の説明はまだ用意されていません」。
8. IH9: ホバー対象は data-evidence-ref 登録済み要素のみで、素の段落セレクタ
   （`.material-chunk-text p` 等）を対象にしていない。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    """`signature`（例: "function foo(...) {"）から対応する閉じ `}` までを、
    素朴な波括弧カウントで抽出する（test_learning_ui_phase2_static.py と同じ流儀）。"""
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body for: " + signature)


class TestAnchorAttachRequiresVisiblePin:
    """IH3/IH5: アンカー添付は「ピンが可視のまま」を唯一の真実源にする。
    不可視のラッチ値（pinned=true だが DOM 上は非表示）を送らせない。"""

    def test_send_message_computes_pin_visible_from_pinned_and_dom_hidden(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        assert "_latchState.pinned" in block
        assert 'document.getElementById("inspect-tooltip")' in block
        # 「_latchState.pinned かつ tip が実在しかつ hidden でない」の合成判定。
        assert "_latchState.pinned && !!_materialTip && !_materialTip.hidden" in block

    def test_anchor_fields_only_attached_when_pin_visible(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        assert "if (_pinVisible && _latchState.anchor) {" in block
        # 判定の定義がガード条件より前にあること。
        define_idx = block.index("const _pinVisible =")
        guard_idx = block.index("if (_pinVisible && _latchState.anchor) {")
        assert define_idx < guard_idx
        # 添付フィールドは既存の方法A（element_id/element_type/element_label/chunk_id）
        # のみを使い、新しいフィールド名を発明しない。
        guard_block = block[guard_idx:block.index("clearMaterialLatch();", guard_idx)]
        assert "payload.element_id = _latchState.anchor.element_id;" in guard_block
        assert "payload.element_type = _latchState.anchor.element_type;" in guard_block
        assert "payload.element_label = _latchState.anchor.element_label;" in guard_block
        assert "payload.chunk_id = _latchState.anchor.chunk_id;" in guard_block

    def test_explicit_element_id_is_not_overwritten(self):
        """EXPLAIN_GRAPH_ELEMENT 等が既に element_id を積んでいれば上書きしない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        assert "if (!payload.element_id) {" in block

    def test_latch_is_consumed_on_send_regardless_of_attach(self):
        """送信の瞬間にピンが可視であれば、使う/使わないに関わらずラッチは消費される。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        pin_block = block[block.index("if (_pinVisible && _latchState.anchor) {"):]
        assert "clearMaterialLatch();" in pin_block


class TestMaterialTooltipHasNoGenerativeCall:
    """IH2: 教材ツールチップ（ホバー・ラッチとも）は API・LLM・チャット送信を
    一切呼ばない — 既にクライアントが持っているデータだけを見せる。"""

    def test_show_material_tooltip_calls_no_api_or_chat(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "apiFetch(" not in block
        assert "fetch(" not in block
        assert "sendMessage(" not in block

    def test_latch_function_calls_no_api_or_chat(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function latchMaterialAnchorIfHovering() {")
        assert "apiFetch(" not in block
        assert "fetch(" not in block
        assert "sendMessage(" not in block

    def test_hover_delegation_calls_no_api_or_chat(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        assert "apiFetch(" not in block
        assert "fetch(" not in block
        assert "sendMessage(" not in block

    def test_tooltip_content_derived_from_registered_items_only(self):
        """内容はすでに renderMaterialChunk が持っているデータ（evidence_items /
        formulas / figures）から作るだけで、新規取得を行わない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialAnchorTooltipContent(item) {")
        assert "apiFetch(" not in block
        assert "fetch(" not in block


class TestPinClearWiring:
    """✕ ボタン / Esc キーによる手動クリアが配線されている。"""

    def test_close_button_click_clears_latch(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        assert '.closest(".inspect-tooltip-close")' in block
        assert "clearMaterialLatch();" in block
        click_idx = block.index('.closest(".inspect-tooltip-close")')
        clear_idx = block.index("clearMaterialLatch();", click_idx)
        assert click_idx < clear_idx

    def test_escape_key_clears_latch_only_when_pinned(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        assert '"Escape" && _latchState.pinned' in block
        esc_idx = block.index('"Escape" && _latchState.pinned')
        clear_idx = block.index("clearMaterialLatch();", esc_idx)
        assert esc_idx < clear_idx

    def test_clear_material_latch_resets_state_and_dom_together(self):
        """IH3: pinned を false にする瞬間、DOM 側も必ず不可視にする
        （ブール値と可視状態が乖離しないことを構造的に保証する）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function clearMaterialLatch() {")
        assert "_latchState.pinned = false;" in block
        assert "_latchState.anchor = null;" in block
        assert "tip.hidden = true;" in block
        assert 'tip.classList.remove("material", "pinned");' in block


class TestLatchTriggersExactlyTwoPlaces:
    """§6.1: ラッチのトリガーは「入力欄の最初のキーストローク（空→非空）」と
    「音声 VAD の発話検知」の2箇所のみ。"""

    def test_latch_called_exactly_twice(self):
        js = _read(APP_JS)
        assert js.count("latchMaterialAnchorIfHovering();") == 2

    def test_input_event_triggers_latch_on_empty_to_nonempty_transition(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initInput() {")
        assert 'input.addEventListener("input", function () {' in block
        input_start = block.index('input.addEventListener("input", function () {')
        input_end = block.index("});", input_start)
        input_block = block[input_start:input_end]
        assert "_chatInputWasEmpty && !isEmpty" in input_block
        assert "latchMaterialAnchorIfHovering();" in input_block
        cond_idx = input_block.index("_chatInputWasEmpty && !isEmpty")
        call_idx = input_block.index("latchMaterialAnchorIfHovering();")
        assert cond_idx < call_idx

    def test_clearing_input_does_not_clear_the_latch(self):
        """入力欄を全消去（非空→空）してもラッチは維持する — クリアは ✕/Esc/送信のみ。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initInput() {")
        input_start = block.index('input.addEventListener("input", function () {')
        input_end = block.index("});", input_start)
        input_block = block[input_start:input_end]
        assert "clearMaterialLatch" not in input_block

    def test_voice_vad_triggers_latch_on_speech_detected_transition(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function voiceVadTick() {")
        assert "if (!voiceState.speechDetected) {" in block
        branch_start = block.index("if (!voiceState.speechDetected) {")
        branch_end = block.index("\n      }", branch_start)
        branch_block = block[branch_start:branch_end]
        assert "voiceState.speechDetected = true;" in branch_block
        assert "latchMaterialAnchorIfHovering();" in branch_block


class TestInspectModeSuppressesMaterialTooltip:
    """§4.1 マトリクス: インスペクト ON 中は教材ツールチップを出さない
    （ON は UI 部品側＝マニュアル節ツールチップの担当）。"""

    def test_show_material_tooltip_returns_early_when_inspect_active(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "if (_inspectState.active) return;" in block
        # インスペクトのガードが処理本体より前にあること。
        guard_idx = block.index("if (_inspectState.active) return;")
        tip_idx = block.index('document.getElementById("inspect-tooltip");')
        assert guard_idx < tip_idx

    def test_hover_delegation_ignores_material_anchors_while_inspecting(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        mo_start = block.index('document.addEventListener("mouseover", function (e) {')
        mo_end = block.index("});", mo_start)
        mouseover_block = block[mo_start:mo_end]
        assert "if (_inspectState.active) return;" in mouseover_block

    def test_entering_inspect_mode_forcibly_clears_material_latch(self):
        """インスペクトへ入る瞬間、残っている教材ラッチを強制解除する
        （共有しているツールチップ容器の排他を保証する）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function setInspectMode(active) {")
        assert "if (active) clearMaterialLatch();" in block


class TestLatchValueDoesNotChangeOnMouseMove:
    """§6.1: ラッチ後にマウスを動かしても値は変わらない。mouseover ハンドラは
    pinned 時に新規ツールチップ表示（別要素へのホバー反映）より前に early return する。"""

    def test_mouseover_handler_returns_before_showing_new_tooltip_when_pinned(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        mo_start = block.index('document.addEventListener("mouseover", function (e) {')
        mo_end = block.index("});", mo_start)
        mouseover_block = block[mo_start:mo_end]
        assert "if (_latchState.pinned) return;" in mouseover_block
        pinned_idx = mouseover_block.index("if (_latchState.pinned) return;")
        show_idx = mouseover_block.index("showMaterialTooltip(el, ref);")
        assert pinned_idx < show_idx

    def test_mouseout_handler_does_not_hide_while_pinned(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        mo_start = block.index('document.addEventListener("mouseout", function (e) {')
        mo_end = block.index("});", mo_start)
        mouseout_block = block[mo_start:mo_end]
        assert "if (_latchState.pinned) return;" in mouseout_block

    def test_show_material_tooltip_itself_refuses_to_run_while_pinned(self):
        """showMaterialTooltip 自身も pinned 中は何もしない（二重の防御）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "if (_latchState.pinned) return;" in block

    def test_latch_function_is_noop_once_already_pinned(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function latchMaterialAnchorIfHovering() {")
        assert block.index("if (_latchState.pinned) return;") < block.index("_latchState.pinned = true;")


class TestUndocumentedFixedText:
    """IH8: 表示できる説明が何も無い要素は固定事実文。捏造しない。"""

    def test_fixed_fact_text_present_in_material_tooltip(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "この部分の説明はまだ用意されていません" in block

    def test_tooltip_content_helper_returns_null_when_nothing_to_show(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialAnchorTooltipContent(item) {")
        assert "if (!label && !body) return null;" in block


class TestHoverTargetExcludesPlainParagraphs:
    """IH9: ホバー対象は data-evidence-ref 登録済み要素のみ。素の本文段落
    （.material-chunk-text 直下のテキスト等）は対象にしない。"""

    def test_hover_delegation_targets_data_evidence_ref_only(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        assert 'closest("[data-evidence-ref]")' in block
        # 段落そのものを対象にする selector（.material-chunk-text や裸の "p"）は使わない。
        assert '.material-chunk-text' not in block
        assert 'closest("p")' not in block

    def test_hover_target_selector_is_attribute_based_not_container_based(self):
        """コンテナ（.material-chunk / .lecture-slide-text 等）そのものではなく、
        要素個々の data-evidence-ref 属性で対象を絞る。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initMaterialHoverLatch() {")
        assert 'closest(".material-chunk")' not in block
        assert 'closest(".lecture-slide-text")' not in block


class TestAnchorBearingElementsCarryDataEvidenceRef:
    """componentチップ・数式ブロック・図カード・出典カードが実際に
    data-evidence-ref を持つ（= ホバー対象になる）ことをソース上で固定する。"""

    def test_component_claim_chip_has_evidence_ref(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderMaterialEvidenceChip(kind, embedId, evidenceItem, resolver)")
        assert "data-evidence-ref=" in block

    def test_equation_embed_registers_for_hover_lookup(self):
        js = _read(APP_JS)
        assert 'registerMaterialEvidenceChipEntry("equation:" + embedId, evidenceItem || formulaObj, null);' in js

    def test_figure_evidence_card_registers_for_hover_lookup(self):
        js = _read(APP_JS)
        assert 'registerMaterialEvidenceChipEntry("figure:" + embedId, evidenceItem, null);' in js

    def test_generic_source_card_registers_for_hover_lookup(self):
        js = _read(APP_JS)
        assert 'registerMaterialEvidenceChipEntry(embed.kind + ":" + embedId, evidenceItem, null);' in js

    def test_image_figure_card_gets_a_data_evidence_ref_and_registers(self):
        """[[FIGURE_N]] 経由の画像付き図カード（renderMaterialFigureCard）は
        figure_id が分かれば data-evidence-ref を持ち、ホバー用に登録される
        （不足していた data-* を Phase 3 で最小追加した部分）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderMaterialFigureCard(figure) {")
        assert 'var ref = figure.figure_id ? ("figure:" + normalizeMaterialEvidenceId(figure.figure_id)) : "";' in block
        assert "if (ref) registerMaterialEvidenceChipEntry(ref, figure, null);" in block
        assert "data-evidence-ref" in block

    def test_material_chunk_wrapper_carries_chunk_id_for_latch_lookup(self):
        """ラッチ時に「どのチャンク内で注目したか」を拾うための data-chunk-id
        （method A の chunk_id フィールドに使う）。"""
        js = _read(APP_JS)
        assert 'html += \'<div class="material-chunk" data-chunk-id="\' + escHtml(chunk.id || "") + \'">\';' in js

    def test_lecture_slide_wrapper_carries_chunk_id_for_latch_lookup(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderLectureStage() {")
        assert 'data-chunk-id="' in block
        assert "slide.chunk_id" in block


class TestElementTypeVocabularyReusesMethodA:
    """既存の方法A（element_type 語彙・anchor_type_for_element）をそのまま使い、
    新しい backend フィールドを発明しない。equation は既存の "formula" 語彙
    （anchor_type_for_element が "equation" にマップする）を使う。"""

    def test_equation_kind_maps_to_existing_formula_vocabulary(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialAnchorElementType(kind) {")
        assert 'return kind === "equation" ? "formula" : "concept";' in block


class TestInitWiring:
    def test_material_hover_latch_initialized_on_app_start(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function initApp() {")
        assert "initMaterialHoverLatch();" in block

    def test_registered_after_inspect_mode_init(self):
        """排他な機能なので、初期化順自体に強い意味は無いが、既存の
        initInspectMode() 呼び出しの近傍に配線されていることを確認する。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function initApp() {")
        inspect_idx = block.index("initInspectMode();")
        latch_idx = block.index("initMaterialHoverLatch();")
        assert inspect_idx < latch_idx


class TestStylesCss:
    def test_pinned_and_close_button_classes_defined(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".inspect-tooltip.material .inspect-tooltip-source {",
            ".inspect-tooltip.pinned {",
            ".inspect-tooltip-close {",
            ".inspect-tooltip.pinned .inspect-tooltip-close {",
        ):
            assert selector in css, f"{selector} が styles.css に見つかりません"

    def test_close_button_hidden_unless_pinned(self):
        css = _read(STYLES_CSS)
        start = css.index(".inspect-tooltip-close {")
        end = css.index("}", start)
        assert "display: none;" in css[start:end]

    def test_close_button_recovers_pointer_events_when_pinned(self):
        """容器全体は pointer-events:none のままだが、✕ だけはクリックできる。"""
        css = _read(STYLES_CSS)
        start = css.index(".inspect-tooltip.pinned .inspect-tooltip-close {")
        end = css.index("}", start)
        assert "pointer-events: auto;" in css[start:end]


class TestMaterialTooltipSourceChip:
    """IH1: 出所チップは「📄 教材」（インスペクトの「📖 使い方」と混同しない）。"""

    def test_material_source_chip_wording(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "📄 教材" in block

    def test_no_confidence_numbers_leak_into_tooltip(self):
        """IH7: ツールチップに confidence 等の生数値を出さない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "confidence" not in block


class TestEquationTooltipNeverRepeatsTheEquation:
    """EH1/EH2/EH5: 数式ホバーは式を再掲しない。

    設計正本: docs/features/equation_hover_content_design.md。
    数式は本文カードが KaTeX で整形表示しているため、ツールチップ（escHtml のみ）に
    latex / raw_text を入れると「未レンダリングの同じ式」という劣化コピーになる。
    """

    def test_tooltip_body_does_not_use_latex_or_raw_text(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialAnchorTooltipContent(item) {")
        assert "item.latex" not in block
        assert "item.raw_text" not in block

    def test_equation_without_explanatory_lines_falls_back_to_ih8(self):
        """IH8/§3.1: 説明材料が1つも無い数式は「数式」ラベル単独の空箱を出さず、
        固定文（この部分の説明はまだ用意されていません）へ落とす。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialAnchorTooltipContent(item) {")
        assert 'if (item.kind === "equation" && !body) return null;' in block, (
            "equation で body が空なら null を返す契約が失われている"
        )
        show = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "この部分の説明はまだ用意されていません" in show

    def test_tooltip_uses_role_symbols_and_reading(self):
        """出すのは役割 / 記号の意味 / 読み下し（設計書 §3.1）。組み立ては
        ホバーと「文脈を見る」パネルで共有する単一実装（EC5）。"""
        js = _read(APP_JS)
        tooltip = _extract_function_body(js, "function materialAnchorTooltipContent(item) {")
        assert "equationExplanatoryLines(" in tooltip
        shared = _extract_function_body(js, "function equationExplanatoryLines(src, skipText) {")
        assert "equationRoleLabel" in shared
        assert "src.symbols" in shared
        assert "src.plain_text" in shared
        assert "src.latex" not in shared
        assert "src.raw_text" not in shared

    def test_tooltip_includes_assumptions_and_section(self):
        """§6 S1（element_context_presentation_redesign.md）: ホバーの行構成は
        役割 → 意味 → 記号 → 読み → 成立条件1件 → 掲載節。成立条件は snapshot の
        assumptions（focus 側は intrinsic.conditions）先頭1件を原文のまま出す。"""
        js = _read(APP_JS)
        shared = _extract_function_body(js, "function equationExplanatoryLines(src, skipText) {")
        assert "src.assumptions" in shared
        assert "src.conditions" in shared
        assert '"成立条件: "' in shared
        assert "src.section_label" in shared
        assert '"掲載: "' in shared
        # 順序: 成立条件は読み（plain_text）の後・掲載節の前。
        assert shared.index("src.plain_text") < shared.index('"成立条件: "') < shared.index('"掲載: "')

    def test_context_panel_reuses_the_same_lines(self):
        """EC5: パネル本文もホバーと同じ材料・同じ順序で組む。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function elementContextToCardDto(elementType, data) {")
        assert "equationExplanatoryLines(" in block

    def test_card_math_rendering_is_gated(self):
        """EC2: KaTeX(throwOnError:false) に平文・壊れた TeX を渡さない
        （渡すと赤いエラーがそのまま学習者に見える）。

        ゲートの実装は **element-card.js 内製**（element_context_presentation_redesign.md
        §6 S3-5）。app.js は素のレンダラを渡すだけで、判定を二重実装しない。"""
        js = _read(APP_JS)
        opts = _extract_function_body(js, "function learnerElementCardOpts(onCenter) {")
        assert "renderMath: renderMaterialKatex," in opts
        assert "looksLikeRenderableTex" not in js
        card = _read(ROOT / "frontend" / "public" / "js" / "element-card.js")
        gate = _extract_function_body(card, "function looksLikeRenderableTex(text, symbolMode) {")
        assert "opens === closes" in gate  # 切り詰めで壊れた TeX を弾く
        # 波括弧カウントはエスケープ除去後の単純カウントで行う。
        # 「直前が非バックスラッシュの閉じ括弧」の /g 走査は連続する閉じ括弧を
        # 数え落とし、{\bm{x}} 形の正当な TeX を不均衡と誤判定して弾く
        # （2026-08-03 実機で発覚した回帰）。
        assert 't.replace(/\\\\[{}]/g, "")' in gate
        assert "(^|[^\\\\])" not in gate
        assert "if (!looksLikeRenderableTex(expr, symbolMode)) return \"\";" in card

    def test_katex_error_output_falls_back_to_plain_text(self):
        """EC2: KaTeX は throwOnError:false のとき例外を投げず「赤い表示の HTML」を
        正常返却する。経路は2つ — ①式全体のパース失敗（class="katex-error"）
        ②未知コマンドのインライン回復（errorColor #cc0000 の赤テキストのみ・
        katex-error クラス無し。論文独自マクロ \\bmx がこれ）。形のゲートでは
        どちらも検出できないため、出力側で両方の痕跡を検査し素のテキストへ落とす。"""
        card = _read(ROOT / "frontend" / "public" / "js" / "element-card.js")
        rendered = _extract_function_body(card, "function renderMathGated(ctx, expr, display, symbolMode) {")
        assert "KATEX_ERROR_MARK.test(rendered)" in rendered
        assert "var KATEX_ERROR_MARK = /katex-error|#cc0000/;" in card

    def test_context_panel_title_has_no_dangling_separator(self):
        """EC1: 見出しが空のときに「数式 ・ 」を作らない（生 TeX も出さない）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function evidenceChipPopoverHead(kind, title) {")
        assert "text ? kindLabel" in block

    def test_role_labels_come_from_element_vocab_only(self):
        """EH5: 訳語表は ElementVocab が唯一。app.js に日本語の役割辞書を作らない。"""
        vocab = _read(ROOT / "frontend" / "public" / "js" / "element-vocab.js")
        assert "equationRoleLabel: equationRoleLabel" in vocab
        for role in ("premise", "definition", "derived", "result", "constraint"):
            assert role + ":" in vocab
        block = _extract_function_body(vocab, "function equationRoleLabel(role) {")
        assert 'return "";' in block  # 未知キーで内部語彙を漏らさない


class TestHoverContextExit:
    """§6 S1（element_context_presentation_redesign.md）: ホバーは①「これは何か」で
    完結するため、残り3問（位置づけ / 構成 / 導出）への出口を最終行に明示する。
    EH3（無フェッチ）は維持 — snapshot に載っている値だけを見せ、押されるまで
    何も取得しない。"""

    def test_section_line_is_added_only_when_the_snapshot_has_it(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function equationExplanatoryLines(src, skipText) {")
        assert 'if (src.section_label) lines.push("掲載: " + src.section_label);' in block
        assert "apiFetch(" not in block
        assert "fetch(" not in block

    def test_context_cta_is_fail_closed_to_openable_types(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialAnchorTooltipContent(item) {")
        assert "MATERIAL_ELEMENT_CONTEXT_TYPES.indexOf(item.kind) >= 0" in block
        assert "cta = { type: item.kind, id: String(item.id) };" in block
        assert "cta: cta" in block

    def test_context_cta_reuses_the_existing_delegation(self):
        """クリック処理は既存の .ls-material-context-btn 委譲リスナー
        （openElementContextPopover）を再利用する。第2の配線を作らない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function materialTooltipContextCtaHtml(cta) {")
        assert "ls-material-context-btn" in block
        assert "data-element-context-type" in block
        assert "data-element-context-id" in block
        assert "MATERIAL_HOVER_CONTEXT_CTA" in block
        assert 'var MATERIAL_HOVER_CONTEXT_CTA = "▸ 文脈を見る（どこから来て、どこへ行くか）";' in js
        show = _extract_function_body(js, "function showMaterialTooltip(anchorEl, ref) {")
        assert "materialTooltipContextCtaHtml(content.cta)" in show
        # ホバー自体は依然として何も取得しない（IH2 / EH3）。
        assert "apiFetch(" not in show
        assert "fetch(" not in show

    def test_cta_is_clickable_even_though_the_tooltip_is_pointer_events_none(self):
        css = _read(STYLES_CSS)
        start = css.index(".inspect-tooltip-context-btn {")
        block = css[start:css.index("}", start)]
        assert "pointer-events: auto;" in block


class TestContextPanelUsesTheHeadline:
    def test_panel_title_prefers_the_headline(self):
        """DTO v2 はラベルラダーの結果を headline に載せる（旧サーバは label のみ）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderElementContextPanel(pop, body, elementType, data) {")
        assert "focus.headline" in block
        assert "focus.label" in block  # 旧応答へのフォールバックを残す
