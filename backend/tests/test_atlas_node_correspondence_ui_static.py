"""分野マップのノード版間対応（K-6）のフロントエンド静的ガードレール。

正本: docs/features/atlas_node_correspondence_design.md
  NC3 確定は人間（候補は既定で「選択済み」に見せない）
  NC5 自動付け替えをしない（読み替えるだけ・`landscape_placements.node_id` を書かない）
  NC6 数値非表示・閉世界（一致件数・対応率・cosine を出さない）
  §6  読み手の読み替え（学習者オーバーレイは `current_node_id || node_id`）
  §7  UI（凍結の影響モーダル内「前の版のノードとの対応」区画 + 配置行の node_status チップ）

バックエンド（`core/atlas_correspondence.py` / `routes/atlas.py` / `routes/landscape.py`）は
並行実装のため、ここでは admin / 学習者フロントとアンカー3点セットの静的契約だけを検証する
（`test_landscape_admin_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
RELEASE_REVIEW_JS = ROOT / "frontend" / "public" / "js" / "admin-release-review.js"
LANDSCAPE_LAYER_JS = ROOT / "frontend" / "public" / "js" / "landscape-layer.js"
TEACHER_MANUAL = ROOT / "docs" / "manual" / "teacher" / "17-admin-atlas.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

CORRESPONDENCE_ANCHOR_IDS = (
    "atlas.freeze-correspondence",
    "atlas.freeze-correspondence-manual",
)

# NC6: 候補区画・チップに出してはならない語（件数・率・スコア・近さの生値）。
FORBIDDEN_NUMERIC_TERMS = ("件", "率", "スコア", "類似度", "信頼度", "％", "%")


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _strip_css(src: str) -> str:
    """style 属性・cssText の中身（`100%` 等）を落として、表示文言だけを残す。"""
    src = re.sub(r'style="[^"]*"', "", src)
    src = re.sub(r"cssText\s*=\s*\"[^\"]*\"", "", src)
    return src


def _js_function_body(src: str, signature: str) -> str:
    """``signature`` から対応する閉じ波括弧までを素朴な brace matching で切り出す。"""
    start = src.index(signature)
    i = start + signature.rindex("{")
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError(f"{signature} の閉じ括弧が見つかりません")


def _correspondence_segment() -> str:
    """admin.js のノード版間対応の節（定数〜影響モーダル）だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── 分野マップのノード版間対応（K-6）")
    end = src.index('document.getElementById("atlas-freeze").addEventListener', start)
    return src[start:end]


# ===========================================================================
# 1. 凍結モーダルの「前の版のノードとの対応」区画（§7）
# ===========================================================================


class TestFreezeCorrespondenceSection:
    def test_section_and_manual_select_carry_anchors(self):
        seg = _correspondence_segment()
        assert 'data-ui-anchor="atlas.freeze-correspondence"' in seg
        assert 'data-ui-anchor="atlas.freeze-correspondence-manual"' in seg

    def test_candidate_checkbox_defaults_to_unchecked(self):
        """NC3: 候補は既定で「選択済み」に見せない（checked を書かない）。"""
        seg = _correspondence_segment()
        block = _js_function_body(seg, "function _freezeCorrespondenceHtml(impact) {")
        assert 'data-role="fc-candidate-check"' in block
        assert "checked" not in block.replace("checks[i].checked", ""), (
            "候補のチェックボックスに既定値を与えています（NC3 違反）"
        )

    def test_candidate_row_shows_labels_and_justification(self):
        seg = _correspondence_segment()
        block = _js_function_body(seg, "function _freezeCorrespondenceHtml(impact) {")
        assert "c.from_label" in block and "c.to_label" in block
        assert "根拠: " in block
        just = _js_function_body(seg, "function _freezeJustificationLabel(candidate) {")
        for label in ("名前の一致", "別名", "登録概念"):
            assert label in just, f"根拠ラベル {label} がありません"

    def test_section_is_omitted_when_no_candidates_and_no_unmatched(self):
        """候補ゼロ・未対応ゼロなら区画を出さず従来どおりの確認だけ。"""
        seg = _correspondence_segment()
        block = _js_function_body(seg, "function _freezeCorrespondenceHtml(impact) {")
        assert "if (!candidates.length && !unmatched.length) return \"\";" in block

    def test_manual_select_offers_no_mapping_first(self):
        seg = _correspondence_segment()
        block = _js_function_body(seg, "function _freezeCorrespondenceHtml(impact) {")
        assert "FREEZE_CORRESPONDENCE_MANUAL_NONE" in block
        assert 'optionsHtml = \'<option value="">\'' in block, (
            "「対応づけない」が先頭の選択肢（既定）であること"
        )
        assert "added_node_ids" in seg, "手動対応の選択肢は draft の追加ノードから作ること"

    def test_no_numeric_wording_in_section(self):
        """NC6: 一致件数・対応率・近さの数値を描かない。"""
        seg = _strip_css(_strip_js_comments(_correspondence_segment()))
        for banned in FORBIDDEN_NUMERIC_TERMS:
            assert banned not in seg, f"対応区画に数値語『{banned}』が出ています（NC6 違反）"

    def test_section_is_es5(self):
        """admin.js は ES5（アロー関数 / const / let / テンプレートリテラル禁止）。"""
        seg = _strip_js_comments(_correspondence_segment())
        assert "=>" not in seg
        assert "`" not in seg
        assert not re.search(r"\bconst\s", seg)
        assert not re.search(r"\blet\s", seg)


# ===========================================================================
# 2. 確定は body.id_migrations にだけ載る（NC1 / NC5 / NC7）
# ===========================================================================


class TestIdMigrationsWiring:
    def test_only_checked_and_manually_selected_pairs_are_collected(self):
        seg = _correspondence_segment()
        block = _js_function_body(seg, "function _freezeCollectIdMigrations(rootEl) {")
        assert "if (!checks[i].checked) continue;" in block, (
            "チェックされていない候補を送っています（NC3 違反）"
        )
        assert 'data-role="fc-manual"' in block
        # NC7: 1 旧ノード → 1 新ノード（from は一意）。
        assert "seen[from]" in block and "seen[manualFrom]" in block

    def test_freeze_post_sends_id_migrations_only_when_present(self):
        src = _read(ADMIN_JS)
        assert "if (pendingIdMigrations.length) freezeBody.id_migrations = pendingIdMigrations;" in src
        assert "body: JSON.stringify(freezeBody)," in src

    def test_impact_modal_replaces_browser_confirm_for_freeze(self):
        """従来の confirm() を小さなモーダルに置き換える（§7）。"""
        src = _read(ADMIN_JS)
        assert "function openFreezeImpactModal(summaryLines, correspondenceHtml, onProceed) {" in src
        assert 'overlay.id = "atlas-freeze-impact-modal";' in src
        assert "この対応で凍結" in src
        # 上段の既存の事実文はそのまま（removed / affected / facts）。
        assert "概念が削除され" in src and "コースの対応が外れます" in src

    def test_freeze_checklist_stays_zero_arg(self):
        """既存の凍結チェックリスト（openFreezeChecklist()）の呼び出し形を変えない。"""
        src = _read(ADMIN_JS)
        assert "function openFreezeChecklist() {" in src
        assert len(re.findall(r"openFreezeChecklist\(\);", src)) >= 2

    def test_correspondence_section_never_writes_placements(self):
        """NC5: 対応区画から配置（landscape_placements）を書き換える経路を作らない。

        対応表は読み替えにだけ使う。区画の中に配置 API への呼び出しが現れたら、
        「候補を確定したら配置も付け替える」実装に滑っている。
        """
        seg = _strip_js_comments(_correspondence_segment())
        assert "/admin/landscape" not in seg
        assert "node_id:" not in seg, "対応区画から node_id を送る経路があります（NC5 違反）"


# ===========================================================================
# 3. 配置行の node_status チップ（教員側 2 画面）
# ===========================================================================


class TestNodeStatusChips:
    def test_admin_landscape_modal_renders_chip(self):
        src = _read(ADMIN_JS)
        assert "LANDSCAPE_NODE_STATUS_LABELS" in src
        assert "_landscapeNodeStatusChipHtml(p) +" in src
        block = _js_function_body(src, "function _landscapeNodeStatusChipHtml(placement) {")
        assert "placement.node_status" in block
        assert 'if (!label) return "";' in block, "node_status が無ければ描かない fail-soft"

    def test_release_review_renders_chip(self):
        src = _read(RELEASE_REVIEW_JS)
        assert "function nodeStatusChipHtml(placement) {" in src
        assert "nodeStatusChipHtml(placement) +" in src
        block = _js_function_body(src, "function nodeStatusChipHtml(placement) {")
        assert 'if (!label) return "";' in block

    def test_chip_labels_are_facts_without_numbers(self):
        for path in (ADMIN_JS, RELEASE_REVIEW_JS):
            src = _read(path)
            assert "前の版から対応づけ" in src
            assert "現行版に対応する場所なし" in src
        # current は無表示（既定の状態を飾らない）
        admin_block = _js_function_body(
            _read(ADMIN_JS), "var LANDSCAPE_NODE_STATUS_LABELS = {"
        )
        assert "current:" not in admin_block

    def test_release_review_chip_is_es5(self):
        src = _strip_js_comments(_read(RELEASE_REVIEW_JS))
        block = src[src.index("var NODE_STATUS_LABELS = {") : src.index("function placementRowHtml(placement) {")]
        assert "=>" not in block and "`" not in block
        assert not re.search(r"\bconst\s", block) and not re.search(r"\blet\s", block)


# ===========================================================================
# 4. 学習者オーバーレイ（§6 / NC5 / NC6）
# ===========================================================================


class TestLearnerOverlay:
    def test_position_prefers_current_node_id(self):
        src = _read(LANDSCAPE_LAYER_JS)
        block = _js_function_body(src, "function groupByNode(data) {")
        assert "p.current_node_id || p.node_id" in block, (
            "位置解決が現行版の node_id を優先していません（§6）"
        )

    def test_unmapped_placements_are_not_positioned(self):
        src = _read(LANDSCAPE_LAYER_JS)
        block = _js_function_body(src, "function groupByNode(data) {")
        assert 'p.node_status === "unmapped"' in block

    def test_domain_facts_reuse_existing_fact_row(self):
        """新しい帯を作らず、既存の事実行（state.factEl）に足す。"""
        src = _read(LANDSCAPE_LAYER_JS)
        block = _js_function_body(src, "function domainFactLines(data) {")
        assert "domain.facts" in block
        assert "node_id" not in block, "学習者の事実文に node_id を出しています（NC6 違反）"
        visibility = _js_function_body(src, "function updateFactVisibility() {")
        assert "domainFactLines(data)" in visibility
        assert "corpusFactText(data)" in visibility

    def test_learner_layer_never_exposes_correspondence_internals(self):
        """NC6: 学習者に `via` / 旧 node_id / 対応の経路を出さない。"""
        src = _strip_js_comments(_read(LANDSCAPE_LAYER_JS))
        for banned in ("via", "id_migrations", "justification", "lexical_match", "correspondence"):
            assert banned not in src, f"学習者オーバーレイに {banned} が出ています（NC6 違反）"


# ===========================================================================
# 5. アンカー3点セット（表 / 担体 / マニュアル）
# ===========================================================================


class TestAnchorTriplet:
    def test_anchors_registered_in_table(self):
        for anchor_id in CORRESPONDENCE_ANCHOR_IDS:
            assert anchor_id in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS
            assert anchor_id in admin_anchors_mod.ADMIN_UI_ANCHORS
            ref = admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id]
            assert ref.startswith("teacher/17-admin-atlas.md#"), ref

    def test_anchors_resolve_for_teacher(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id in CORRESPONDENCE_ANCHOR_IDS:
            assert anchor_id in resolved, f"{anchor_id} が TEACHER に解決されません"
            assert resolved[anchor_id]["title"]
            assert resolved[anchor_id]["body"]

    def test_manual_sections_exist_with_explicit_anchors(self):
        md = _read(TEACHER_MANUAL)
        assert "{#freeze-correspondence}" in md
        assert "{#freeze-correspondence-manual}" in md
        # 無効化・既定オフの理由を必ず書く（管理UIマニュアルの規律）。
        assert "チェックが外れている場合" in md
        assert "対応先が選べない場合" in md
        # NC5: 読み替えであって書き換えではないことを明示する。
        assert "書き換わりません" in md
