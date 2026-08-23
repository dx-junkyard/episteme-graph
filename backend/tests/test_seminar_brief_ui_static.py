"""ゼミ前ブリーフ（Seminar Brief, 提案1 v1）の教員フロントエンド静的ガードレール。

正本: docs/features/seminar_brief_mirroring_design.md §1（SB1〜SB4）+ §3 精査記録。
  SB1 新テーブル・新LLMゼロ（read-only 合成 — フロントも読むだけ。書き込み API を呼ばない）
  SB2 数値を見せない（件数・人数の生値なし。段階ラベル・レンジ・事実文のみ）
  SB3 第4区画「学習者からの問い」は空欄で予約（警告色・催促文にしない）
  SB4 学習者個人・学習者別件数への導線を作らない

バックエンド（`core/doubt/seminar_brief.py` + `routes/seminar_brief.py`）は
`test_seminar_brief_api.py` 側が担当。ここでは admin.js のモーダル + アンカー2件の
3点セット（anchor 表 / data-ui-anchor 担体 / teacher マニュアル節）の静的契約のみを
検証する（`test_landscape_admin_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
LECTURE_STUDIO_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
TEACHER_MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import label_vocab  # noqa: E402
from core.doubt import schema as doubt_schema  # noqa: E402
from core.doubt import seminar_brief as sb_mod  # noqa: E402
from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

SEMINAR_ANCHOR_IDS = (
    "materials.row-seminar-brief",
    "materials.seminar-brief-modal",
)

_JS_TABLE_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    # 他テストが合成ツリー・DB 状態で温めたモジュールキャッシュを持ち込まない／持ち出さない。
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _brief_segment() -> str:
    """admin.js に追加したゼミ前ブリーフ節だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── ゼミ前ブリーフ（seminar_brief_mirroring_design.md §1）")
    end = src.index("// ── File Upload ─", start)
    return src[start:end]


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n  }", start)]


def _js_table(segment: str, name: str) -> dict[str, str]:
    marker = "var " + name + " = {"
    assert marker in segment, "admin.js に " + name + " が無い"
    start = segment.index(marker)
    end = segment.index("};", start)
    block = segment[start + len(marker) : end]
    out: dict[str, str] = {}
    for quoted, bare, value in _JS_TABLE_ENTRY_RE.findall(block):
        out[quoted or bare] = value
    return out


# ===========================================================================
# 1. 教材一覧の ⋯ メニュー項目
# ===========================================================================


class TestMaterialsRowMenuItem:
    def test_menu_item_has_class_anchor_and_label(self):
        src = _read(ADMIN_JS)
        assert 'class="ls-menu-item admin-seminar-brief-btn"' in src
        assert 'data-ui-anchor="materials.row-seminar-brief"' in src
        assert "ゼミ前ブリーフ…</button>" in src

    def test_menu_item_requires_document_id(self):
        """document_id の無い教材（解析未到達）には出さない（landscape 等と同条件）。"""
        src = _read(ADMIN_JS)
        start = src.index("var seminarBriefBtn = m.document_id")
        block = src[start : src.index("\n      //", start)]
        assert ': ""' in block, "document_id が無いときは空文字に縮退すること"

    def test_menu_item_is_placed_after_landscape(self):
        src = _read(ADMIN_JS)
        panel = src[src.index('<div class="ls-menu material-more-panel" hidden>') :]
        panel = panel[: panel.index("</div>")]
        for name in ("landscapeBtn", "seminarBriefBtn", "estimateBtn"):
            assert name in panel, name
        assert panel.index("landscapeBtn") < panel.index("seminarBriefBtn") < panel.index("estimateBtn")

    def test_menu_item_is_bound_after_render(self):
        src = _read(ADMIN_JS)
        assert 'tbody.querySelectorAll(".admin-seminar-brief-btn").forEach(function (btn) {' in src
        block = src[src.index('tbody.querySelectorAll(".admin-seminar-brief-btn")') :][:600]
        assert 'openSeminarBriefModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));' in block

    def test_locate_anchor_resolver_registered(self):
        """Copilot 道案内（G8）: registerUiAnchors に行ボタンの解決を登録する。"""
        src = _read(ADMIN_JS)
        assert 'seminar_brief_button: function (id) {' in src
        block = src[src.index("seminar_brief_button: function (id) {") :][:200]
        assert '_matRowActionAnchor(id, ".admin-seminar-brief-btn")' in block


# ===========================================================================
# 2. モーダル定型
# ===========================================================================


class TestSeminarBriefModalContract:
    def test_modal_functions_exist(self):
        seg = _brief_segment()
        for signature in (
            "function openSeminarBriefModal(documentId, title) {",
            "function loadSeminarBrief(documentId) {",
            "function renderSeminarBrief(data) {",
        ):
            assert signature in seg, signature

    def test_overlay_carries_id_anchor_and_standard_css(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function openSeminarBriefModal(documentId, title) {")
        assert 'overlay.id = "seminar-brief-modal";' in block
        assert 'overlay.setAttribute("data-ui-anchor", "materials.seminar-brief-modal");' in block
        assert "z-index:9999" in block
        assert "document.body.appendChild(overlay);" in block

    def test_title_is_set_via_textcontent(self):
        seg = _brief_segment()
        assert 'document.getElementById("seminar-brief-modal-title").textContent = "ゼミ前ブリーフ: " + (title || "");' in seg

    def test_background_click_and_close_button_remove_the_overlay(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function openSeminarBriefModal(documentId, title) {")
        assert 'overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });' in block
        assert 'document.getElementById("seminar-brief-modal-close")' in block

    def test_loader_is_kicked_from_open(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function openSeminarBriefModal(documentId, title) {")
        assert "loadSeminarBrief(documentId);" in block

    def test_no_polling(self):
        seg = _brief_segment()
        assert "setInterval" not in seg
        assert "setTimeout" not in seg

    def test_read_only_no_write_requests(self):
        """SB1: ブリーフは読むだけ。書き込みメソッド・確定ボタンの API を持たない。"""
        seg = _brief_segment()
        for verb in ("POST", "PATCH", "PUT", "DELETE"):
            assert '"' + verb + '"' not in seg, verb
        assert "method:" not in seg

    def test_get_path_matches_the_backend_route(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function loadSeminarBrief(documentId) {")
        assert 'apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/seminar-brief")' in block


# ===========================================================================
# 3. 4区画（見出しの逐語・順序・空区画の静かな省略・第4区画の常設）
# ===========================================================================


class TestFourSections:
    HEADINGS = ("脆い前提", "一点吊りの支持線", "晴れ間", "学習者からの問い")

    def test_headings_verbatim_and_in_order(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function renderSeminarBrief(data) {")
        positions = []
        for heading in self.HEADINGS:
            needle = '"' + heading + '"'
            assert needle in block, heading
            positions.append(block.index(needle))
        assert positions == sorted(positions), "4区画の描画順が設計と違う"

    def test_dto_keys_match_the_backend_projection(self):
        """フロントの読み出しキーが `build_seminar_brief` の返却キーと一致する。"""
        seg = _brief_segment()
        backend_src = (BACKEND / "core" / "doubt" / "seminar_brief.py").read_text(encoding="utf-8")
        for key in ("fragile_assumptions", "single_support_lines", "clear_skies", "learner_handoff"):
            assert "data." + key in seg, key
            assert '"' + key + '"' in backend_src, key + " がバックエンド投影から消えた"

    def test_empty_sections_are_silently_omitted(self):
        """空の区画は「（該当なし）」ではなく区画ごと省略する（空欄は発見の流儀）。"""
        seg = _brief_segment()
        block = _fn_block(seg, "function _sbSection(heading, rowEls) {")
        assert "if (!rowEls || !rowEls.length) return null;" in block
        assert "該当なし" not in seg

    def test_fourth_section_is_always_rendered_with_the_reserved_note(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function renderSeminarBrief(data) {")
        assert "body.appendChild(handedSection);" in block
        # note は淡色（tertiary）。警告色・danger を使わない（SB3）。
        handed = block[block.index("var handed =") :]
        assert "--color-text-tertiary" in handed
        assert "danger" not in handed
        assert "warning" not in handed

    def test_reserved_note_fallback_mirrors_the_backend_constant(self):
        """フォールバック固定文はサーバ正本（LEARNER_HANDOFF_RESERVED_NOTE）の逐語。"""
        seg = _brief_segment()
        needle = 'var SEMINAR_BRIEF_RESERVED_NOTE = "' + sb_mod.LEARNER_HANDOFF_RESERVED_NOTE + '";'
        assert needle in seg
        # サーバの note を優先し、無いときだけフォールバックする。
        assert "handed.note || SEMINAR_BRIEF_RESERVED_NOTE" in seg

    def test_unavailable_shows_only_the_reason_fact(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function renderSeminarBrief(data) {")
        assert "if (!data.available) {" in block
        assert "data.reason ||" in block
        # 縮退も警告色にしない（secondary で事実文のみ）。
        unavailable = block[block.index("if (!data.available) {") : block.index("var fragileRows")]
        assert "--color-text-secondary" in unavailable
        assert "danger" not in unavailable


# ===========================================================================
# 4. SB2 数値非漏洩（生数値キー・生数値フィールドの不参照）
# ===========================================================================


class TestNoNumericExposure:
    def test_raw_count_keys_are_never_referenced(self):
        seg = _brief_segment()
        assert "dependent_count" not in seg
        assert "n_items" not in seg
        assert "n_users" not in seg

    def test_no_raw_score_fields(self):
        seg = _brief_segment()
        assert re.search(r"\.confidence\b", seg) is None
        assert re.search(r"\.weight\b", seg) is None
        assert re.search(r"\.load_score\b", seg) is None

    def test_only_string_values_are_rendered(self):
        """段階ラベル・レンジ・事実文（文字列）以外の値は種類を問わず描画しない構造ガード。"""
        seg = _brief_segment()
        meta = _fn_block(seg, "function _sbFragileMeta(item) {")
        assert meta.count('=== "string"') >= 4
        stumble = _fn_block(seg, "function _sbStumbleFacts(stumble) {")
        assert 'typeof value === "string"' in stumble

    def test_stumble_axes_render_only_after_k_anonymity_gate(self):
        """つまづき補助は k-匿名通過分（has_data）のみ。出ないことを警告にしない（SB4）。"""
        seg = _brief_segment()
        block = _fn_block(seg, "function _sbStumbleFacts(stumble) {")
        assert "if (!stumble || !stumble.has_data) return [];" in block


# ===========================================================================
# 5. textContent / createElement 基調（本文変数を innerHTML に渡さない）
# ===========================================================================


class TestTextContentRendering:
    def test_helper_builds_nodes_with_textcontent(self):
        seg = _brief_segment()
        block = _fn_block(seg, "function _sbEl(tag, cssText, text) {")
        assert "document.createElement(tag)" in block
        assert "el.textContent = text;" in block

    def test_render_functions_never_touch_innerhtml(self):
        seg = _brief_segment()
        for signature in (
            "function renderSeminarBrief(data) {",
            "function _sbSection(heading, rowEls) {",
            "function _sbStatementRow(statement, facts) {",
            "function _sbFragileMeta(item) {",
            "function _sbStumbleFacts(stumble) {",
            "function loadSeminarBrief(documentId) {",
        ):
            assert "innerHTML" not in _fn_block(seg, signature), signature

    def test_the_only_innerhtml_is_the_static_shell(self):
        """innerHTML はモーダル骨組み（静的リテラルのみ）の1回だけ。変数を混ぜない。"""
        seg = _brief_segment()
        assert seg.count(".innerHTML") == 1
        shell = seg[seg.index("overlay.innerHTML =") : seg.index("document.body.appendChild(overlay);")]
        assert "escHtml(" not in shell
        assert "+ title" not in shell
        assert "+ data" not in shell
        assert "+ documentId" not in shell


# ===========================================================================
# 6. 語彙ミラー（正本はサーバ。片側だけ直すとここが落ちる）
# ===========================================================================


class TestVocabularyMirrors:
    def test_support_level_labels_mirror_the_doubt_schema(self):
        seg = _brief_segment()
        assert _js_table(seg, "SEMINAR_SUPPORT_LEVEL_LABELS") == dict(
            doubt_schema.SUPPORT_LEVEL_BADGE_LABELS
        )

    def test_verification_status_labels_mirror_the_ledger_vocabulary(self):
        seg = _brief_segment()
        assert _js_table(seg, "SEMINAR_VERIFICATION_STATUS_LABELS") == dict(
            label_vocab.VERIFICATION_STATUS_LABELS_LEDGER
        )

    def test_stumble_axis_ids_exist_in_the_backend_aggregation(self):
        seg = _brief_segment()
        stumble_src = (BACKEND / "core" / "reconstruction" / "stumble.py").read_text(encoding="utf-8")
        for axis in ("error_rate", "symbol_descent", "verdict_self_check_divergence", "faq"):
            assert '"' + axis + '"' in seg, axis
            assert '"' + axis + '"' in stumble_src, axis + " がバックエンド集計から消えた"

    def test_stumble_axis_headings_match_the_lecture_studio_vocabulary(self):
        """原稿スタジオの lsStumbleAxis と同じ4軸見出し（語彙を割らない）。"""
        seg = _brief_segment()
        ls = _read(LECTURE_STUDIO_JS)
        for heading in ("誤り率", "記号降下", "判定の乖離", "質問・誤解"):
            assert '"' + heading + '"' in seg, heading
            assert '"' + heading + '"' in ls, heading


# ===========================================================================
# 7. ES5 準拠（開発ルール5: admin.js は ES5）
# ===========================================================================


class TestSeminarBriefSegmentIsEs5:
    def test_no_arrow_functions(self):
        assert "=>" not in _brief_segment()

    def test_no_const_or_let(self):
        seg = _brief_segment()
        assert re.search(r"(^|[^\w.$])const\s+\w", seg) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", seg) is None

    def test_no_template_literals_or_class(self):
        seg = _brief_segment()
        assert "`" not in seg
        assert re.search(r"(^|[^\w.$])class\s+\w", seg) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in _brief_segment()


# ===========================================================================
# 8. 3点セット（anchor 表 + data-ui-anchor 担体 + マニュアル節）
# ===========================================================================


class TestAnchorThreePieceSet:
    def test_both_anchor_ids_are_registered(self):
        for anchor_id in SEMINAR_ANCHOR_IDS:
            assert anchor_id in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor_id
            assert anchor_id in admin_anchors_mod.ADMIN_UI_ANCHORS, anchor_id

    def test_anchor_values_point_at_the_materials_manual(self):
        expected = {
            "materials.row-seminar-brief": "teacher/11-admin-materials.md#seminar-brief-open",
            "materials.seminar-brief-modal": "teacher/11-admin-materials.md#seminar-brief-modal",
        }
        for anchor_id, ref in expected.items():
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id] == ref

    def test_anchors_resolve_for_teacher_role(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id in SEMINAR_ANCHOR_IDS:
            assert anchor_id in resolved, anchor_id
            section = resolved[anchor_id]
            assert section["title"], anchor_id
            assert section["body"], anchor_id

    def test_anchors_are_carried_by_admin_js(self):
        src = _read(ADMIN_JS)
        assert 'data-ui-anchor="materials.row-seminar-brief"' in src
        assert 'overlay.setAttribute("data-ui-anchor", "materials.seminar-brief-modal");' in src

    def test_manual_sections_have_explicit_anchors(self):
        doc = _read(TEACHER_MANUAL)
        assert "## ゼミ前ブリーフ {#seminar-brief}" in doc
        assert "### ゼミ前ブリーフ… {#seminar-brief-open}" in doc
        assert "### ゼミ前ブリーフモーダル {#seminar-brief-modal}" in doc

    def test_manual_lists_the_menu_item_in_the_more_menu(self):
        doc = _read(TEACHER_MANUAL)
        assert "[ゼミ前ブリーフ…](#seminar-brief-open)" in doc

    def test_manual_explains_the_four_sections_and_design_facts(self):
        """4区画の意味・数値が出ない理由・第4区画の予約・k-匿名の帰結を説明する。"""
        doc = _read(TEACHER_MANUAL)
        section = doc[doc.index("## ゼミ前ブリーフ {#seminar-brief}") :]
        section = section[: section.index("## モーダル: 解析を再開")]
        for heading in ("脆い前提", "一点吊りの支持線", "晴れ間", "学習者からの問い"):
            assert heading in section, heading
        assert "数値が出ない理由" in section
        assert "手渡しの仕組みは準備中" in section
        assert "k-匿名" in section
        assert "少人数" in section
        # 読み取り専用であることの明示（SB1）。
        assert "読み取り専用" in section
