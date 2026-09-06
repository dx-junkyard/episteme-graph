"""分野マップのベクトル係留層（VA層）の教員フロント静的ガードレール。

正本: docs/features/atlas_vector_anchoring_design.md
  §5  索引の状態 (status) と手動 refresh（起動時の自動構築はしない）
  §7  別名レジストリ（ギャップ候補からの登録 / 手動登録 / 見送り）と近傍注記
  §8  着地予測（ディスカバリー検索の候補行に1行の事実文）
  §9  段階ラベルの正本は `core/label_vocab.py` のみ
  VA1 ベクトルは候補生成器 — 確定は常に人間（登録・却下は教員の明示操作）
  VA2 数値非表示（cosine / 類似度 / スコアを描かない。索引済みの件数は運用状態の事実）
  VA4 fail-soft（骨格なし・索引なし・日次上限は事実文へ縮退し、既存操作を止めない）
  VA6 情報を落とさない（別名の見送りは行削除ではなく状態遷移）
  VA8 閉世界の正直さ（近さの言明には骨格の版を添える）
  VA9 骨格へ書かない（別名の登録から draft/freeze を触らない）

バックエンド（`core/atlas_vectors/` / `routes/atlas_vectors.py`）は
`test_atlas_vectors_{core,api,guardrails}.py` が担当する。本ファイルは admin フロント
（`frontend/public/js/admin.js` / `admin-paper-discovery.js`）とアンカー3点セットの
静的契約のみを検証する（`test_atlas_gaps_admin_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
DISCOVERY_JS = ROOT / "frontend" / "public" / "js" / "admin-paper-discovery.js"
ATLAS_MANUAL = ROOT / "docs" / "manual" / "teacher" / "17-admin-atlas.md"
MATERIALS_MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import label_vocab  # noqa: E402
from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

VECTOR_ANCHOR_IDS = (
    "atlas.vector-refresh",
    "atlas.aliases",
    "atlas.gap-alias-register",
)

#: §9 の段階ラベル（正本は label_vocab）。フロントに写して分裂させない（VA2 の写像規律）。
#: 正本から引く — ラベルを変えたら denylist も自動で追随する。
NEARNESS_SCALE_LABELS = tuple(label_vocab.ANCHOR_NEARNESS_SCALE.labels)

#: 却下理由の固定文（ギャップ候補 → 別名の2段動作。教員に打ち直させない）。
GAP_ALIAS_NOTE_HEAD = "既存概念『"
GAP_ALIAS_NOTE_TAIL = "』の別名として登録"


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _vector_segment() -> str:
    """admin.js に追加した VA層（ベクトル索引 + 別名レジストリ）の節だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── ベクトル索引と別名レジストリ（VA層, migration 074）")
    end = src.index(
        "// ── 論文の解析から見つかった候補（カテゴリギャップ候補, migration 066）", start
    )
    return src[start:end]


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n    }", start)]


def _strip_comment_lines(src: str) -> str:
    """行頭コメント（`// …`）を落とす（設計条項の引用で禁止語彙検査が落ちないように）。"""
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


# ===========================================================================
# 1. ベクトル索引の区画（§5: status / refresh）
# ===========================================================================


class TestVectorIndexPanel:
    def test_panel_is_appended_into_the_atlas_tab(self):
        """admin.html を変えず、マップ本体の区画に後付けで足す（gap グループと同じ流儀）。"""
        seg = _vector_segment()
        block = _fn_block(seg, "function buildVectorGroup() {")
        assert 'document.getElementById("atlas-map-section")' in block
        assert 'group.id = "atlas-vectors-group";' in block
        assert "section.appendChild(group);" in block

    def test_status_endpoint_and_missing_skeleton_fact(self):
        seg = _vector_segment()
        assert 'vectorsPath() + "/status"' in seg
        assert '"/atlas/vectors"' in seg
        block = _fn_block(seg, "function loadVectorStatus() {")
        # 骨格なし（404 / available:false）はエラーにせず事実文へ縮退する（VA4）
        assert "if (res.status === 404) return { available: false };" in block
        assert 'var VECTOR_NO_SKELETON_TEXT = "凍結済みの骨格がありません";' in seg

    def test_coverage_fact_line_names_the_skeleton_version(self):
        """索引済みの件数は運用状態の事実（VA2 の例外）。版を必ず添える（VA8）。"""
        seg = _vector_segment()
        block = _fn_block(seg, "function renderVectorStatus(data) {")
        assert "data.embedded_nodes" in block
        assert "data.total_nodes" in block
        assert '"索引済み: " + embedded + "/" + total + " ノード（骨格 版"' in block

    def test_stale_fact_line(self):
        seg = _vector_segment()
        assert 'var VECTOR_STALE_TEXT = "骨格が更新されています。再構築してください。";' in seg
        block = _fn_block(seg, "function renderVectorStatus(data) {")
        assert "data.stale" in block

    def test_refresh_posts_and_reloads_status(self):
        seg = _vector_segment()
        assert 'data-ui-anchor="atlas.vector-refresh"' in seg
        block = _fn_block(seg, "function refreshVectors() {")
        assert 'vectorsPath() + "/refresh"' in block
        assert 'method: "POST"' in block
        assert "loadVectorStatus();" in block

    def test_daily_limit_is_reported_as_a_fact_not_an_error(self):
        seg = _vector_segment()
        assert 'var VECTOR_LIMIT_TEXT = "本日の再構築回数の上限に達しました";' in seg
        block = _fn_block(seg, "function vectorRefreshFactText(summary) {")
        assert 'summary.status === "skipped"' in block
        assert 'summary.skipped_reason === "daily_call_limit_reached"' in block
        assert "VECTOR_LIMIT_TEXT" in block

    def test_refresh_is_disabled_for_retired_domains(self):
        seg = _vector_segment()
        block = _fn_block(seg, "function renderVectorStatus(data) {")
        assert 'domainLifecycles[select.value] === "retired"' in block

    def test_panel_is_reloaded_from_load_state_only(self):
        """再取得はタブ表示（loadState）と操作の成功後だけ。ポーリングしない。"""
        src = _read(ADMIN_JS)
        load_state = _fn_block(src, "    function loadState() {")
        assert "loadVectorPanel();" in load_state
        seg = _vector_segment()
        assert "setInterval" not in seg
        assert "setTimeout" not in seg

    def test_no_similarity_numbers_are_rendered(self):
        """VA2: cosine / 類似度 / スコアを描かない（受け取っても出さない）。"""
        code = _strip_comment_lines(_vector_segment())
        for banned in ("cosine", "similarity", "類似度", "score", "confidence"):
            assert banned not in code, banned


# ===========================================================================
# 2. 別名レジストリ（§7: 一覧 / 登録 / 見送り）
# ===========================================================================


class TestAliasRegistry:
    def test_section_carries_the_anchor(self):
        seg = _vector_segment()
        assert 'data-ui-anchor="atlas.aliases"' in seg

    def test_list_endpoint_and_dismissed_filter(self):
        seg = _vector_segment()
        assert '"/atlas/aliases"' in seg
        block = _fn_block(seg, "function loadAliases() {")
        assert 'aliasIncludeDismissed ? "?include_dismissed=true" : "?include_dismissed=false"' in block
        assert "見送り済みも表示" in seg

    def test_rows_are_grouped_by_node(self):
        seg = _vector_segment()
        block = _fn_block(seg, "function renderAliases(data) {")
        assert "row.node_id" in block
        assert "rows[0].node_label" in block
        assert "row.alias" in block

    def test_manual_registration_form_posts_source_manual(self):
        seg = _vector_segment()
        assert 'id="atlas-alias-node-id"' in seg
        assert 'id="atlas-alias-text"' in seg
        assert 'registerAlias(nodeId, alias, "manual", null,' in seg
        block = _fn_block(seg, "function registerAlias(nodeId, alias, source, evidence, onDone) {")
        assert "aliasesPath()" in block
        assert 'method: "POST"' in block
        assert "node_id: nodeId" in block
        assert 'source: source || "manual"' in block

    def test_empty_inputs_are_not_sent(self):
        seg = _vector_segment()
        assert 'var ALIAS_INPUT_REQUIRED_TEXT = "地図の項目のidと表記の両方を入力してください";' in seg
        assert "if (!nodeId || !alias) { setAliasesStatus(ALIAS_INPUT_REQUIRED_TEXT, true); return; }" in seg

    def test_dismiss_is_a_status_transition_not_a_deletion(self):
        """VA6: 行削除の経路を作らない（DELETE を使わない・状態遷移の POST のみ）。"""
        seg = _vector_segment()
        block = _fn_block(seg, "function dismissAlias(aliasId) {")
        assert '"/dismiss"' in block
        assert 'method: "POST"' in block
        assert 'method: "DELETE"' not in seg
        assert (
            'var ALIAS_DISMISS_NOTE = "「見送り」は行を消す操作ではありません。'
            '同じ表記をもう一度登録すると戻ります。";'
        ) in seg

    def test_registration_refreshes_the_index_state(self):
        seg = _vector_segment()
        block = _fn_block(seg, "function registerAlias(nodeId, alias, source, evidence, onDone) {")
        assert "loadAliases();" in block
        assert "loadVectorStatus();" in block

    def test_no_skeleton_write_path_from_this_segment(self):
        """VA9: 別名の登録から骨格 draft / freeze を書かない。"""
        seg = _vector_segment()
        assert 'method: "PUT"' not in seg
        assert "/atlas/skeleton" not in seg
        assert "freeze" not in seg


# ===========================================================================
# 3. ギャップ候補の近傍注記 → 別名として登録の2段動作（§7 UI 導線）
# ===========================================================================


class TestGapNearAnchorFlow:
    def _gap_segment(self) -> str:
        src = _read(ADMIN_JS)
        start = src.index(
            "// ── 論文の解析から見つかった候補（カテゴリギャップ候補, migration 066）"
        )
        return src[start : src.index("    function addDomainOption(key, label) {", start)]

    def test_annotation_is_rendered_only_when_the_server_sends_it(self):
        seg = self._gap_segment()
        block = _fn_block(seg, "function _gapNearAnchorHtml(candidate) {")
        assert "candidate.near_anchor" in block
        assert 'if (!near || !near.node_label) return "";' in block

    def test_annotation_is_a_possibility_with_the_skeleton_version(self):
        """VA8: 「可能性」の注記であり、版を添える。段階ラベルはサーバの文字列をそのまま。"""
        seg = self._gap_segment()
        block = _fn_block(seg, "function _gapNearAnchorHtml(candidate) {")
        assert '"既存の『" + near.node_label + "』の別表記の可能性があります"' in block
        assert 'parts.push("骨格 版" + near.skeleton_version)' in block
        assert "near.nearness_label" in block

    def test_button_is_gated_on_the_annotation(self):
        seg = self._gap_segment()
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        assert 'data-gap-action="alias-register"' in card
        assert 'data-ui-anchor="atlas.gap-alias-register"' in card
        assert "candidate.near_anchor && candidate.near_anchor.node_id" in card

    def test_action_is_routed_from_the_existing_handler(self):
        seg = self._gap_segment()
        block = _fn_block(seg, "function handleGapAction(clusterKey, action, card) {")
        assert 'if (action === "alias-register") { gapRegisterAlias(clusterKey, card); return; }' in block

    def test_register_then_dismiss_with_a_programmatic_review_note(self):
        """理由必須の既存却下経路を再利用し、理由を自動で填める（教員に prompt を出さない）。"""
        seg = _vector_segment()
        block = _fn_block(seg, "function gapRegisterAlias(clusterKey, card) {")
        assert 'registerAlias(near.node_id, alias, "gap_signal", { cluster_key: clusterKey }' in block
        assert "gapDecide(clusterKey, \"dismiss\", GAP_ALIAS_NOTE_HEAD + nodeLabel + GAP_ALIAS_NOTE_TAIL);" in block
        assert 'var GAP_ALIAS_NOTE_HEAD = "%s";' % GAP_ALIAS_NOTE_HEAD in seg
        assert 'var GAP_ALIAS_NOTE_TAIL = "%s";' % GAP_ALIAS_NOTE_TAIL in seg
        # 登録が成功したときだけ却下する（順序の契約）
        idx_register = block.index("registerAlias(near.node_id")
        idx_dismiss = block.index("gapDecide(clusterKey")
        assert idx_register < idx_dismiss

    def test_alias_list_is_reloaded_after_the_two_step_action(self):
        seg = _vector_segment()
        register = _fn_block(seg, "function registerAlias(nodeId, alias, source, evidence, onDone) {")
        assert "loadAliases();" in register


# ===========================================================================
# 4. 着地予測の1行（§8。表示だけ・アンカーなし・ボタンなし）
# ===========================================================================


class TestDiscoveryLandingLine:
    def test_landing_line_is_rendered_from_the_server_payload(self):
        src = _read(DISCOVERY_JS)
        assert 'var LANDING_HEAD = "地図上の近い領域: ";' in src
        assert "candidate.landing" in src
        assert "landing.region_label" in src
        assert "landing.node_label" in src
        assert "landing.nearness_label" in src
        assert 'landingParts.push("骨格 版" + landing.skeleton_version)' in src

    def test_landing_line_is_display_only(self):
        """取り込みの弁は既存経路のまま（PD1）。着地予測に操作を付けない。"""
        src = _read(DISCOVERY_JS)
        start = src.index('var landing = candidate && candidate.landing;')
        block = src[start : src.index("if (matched.length) {", start)]
        assert "<button" not in block
        assert "data-ui-anchor" not in block
        assert "addEventListener" not in block

    def test_absent_landing_renders_nothing(self):
        src = _read(DISCOVERY_JS)
        assert "if (landing && landing.node_label) {" in src

    def test_scale_labels_are_never_hardcoded_in_the_frontend(self):
        """VA2 の写像規律: 段階ラベルの正本は label_vocab。JS に閾値表・語彙表を持たない。"""
        for path in (ADMIN_JS, DISCOVERY_JS):
            src = _read(path)
            for label in NEARNESS_SCALE_LABELS:
                assert label not in src, "%s に段階ラベル %s が直書きされている" % (path.name, label)

    def test_no_raw_scores_next_to_the_landing_line(self):
        src = _read(DISCOVERY_JS)
        start = src.index('var landing = candidate && candidate.landing;')
        block = src[start : src.index("if (matched.length) {", start)]
        for banned in ("cosine", "similarity", "score", "confidence"):
            assert banned not in block, banned


# ===========================================================================
# 5. ES5 準拠（開発ルール5: admin 系 JS は ES5）
# ===========================================================================


class TestVectorSegmentIsEs5:
    def test_no_arrow_functions(self):
        assert "=>" not in _vector_segment()

    def test_no_const_or_let(self):
        seg = _vector_segment()
        assert re.search(r"(^|[^\w.$])const\s+\w", seg) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", seg) is None

    def test_no_template_literals_or_class(self):
        seg = _vector_segment()
        assert "`" not in seg
        assert re.search(r"(^|[^\w.$])class\s+\w", seg) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in _vector_segment()


# ===========================================================================
# 6. 3点セット（anchor 表 + マニュアル節）
# ===========================================================================


class TestAnchorThreePieceSet:
    def test_all_vector_anchor_ids_are_registered(self):
        for anchor_id in VECTOR_ANCHOR_IDS:
            assert anchor_id in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor_id
            assert anchor_id in admin_anchors_mod.ADMIN_UI_ANCHORS, anchor_id

    def test_anchor_values_point_at_the_atlas_manual(self):
        expected = {
            "atlas.vector-refresh": "teacher/17-admin-atlas.md#vector-refresh",
            "atlas.aliases": "teacher/17-admin-atlas.md#aliases",
            "atlas.gap-alias-register": "teacher/17-admin-atlas.md#gap-alias-register",
        }
        for anchor_id, ref in expected.items():
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id] == ref

    def test_anchors_resolve_for_teacher_role(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id in VECTOR_ANCHOR_IDS:
            assert anchor_id in resolved, anchor_id
            assert resolved[anchor_id]["title"], anchor_id
            assert resolved[anchor_id]["body"], anchor_id

    def test_anchors_are_carried_by_admin_js(self):
        src = _read(ADMIN_JS)
        for anchor_id in VECTOR_ANCHOR_IDS:
            assert 'data-ui-anchor="%s"' % anchor_id in src, anchor_id

    def test_manual_sections_have_explicit_anchors(self):
        doc = _read(ATLAS_MANUAL)
        for heading in (
            "## ベクトル索引 {#vector-index}",
            "### 索引を再構築する {#vector-refresh}",
            "## 登録済みの別名 {#aliases}",
            "### 近い項目の注記と別名として登録 {#gap-alias-register}",
        ):
            assert heading in doc, heading

    def test_manual_vector_section_states_the_purpose_and_recovery(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("## ベクトル索引 {#vector-index}") :]
        section = section[: section.index("\n## 登録済みの別名")]
        assert "凍結済みの骨格がありません" in section
        assert "骨格が更新されています。再構築してください。" in section
        assert "本日の再構築回数の上限に達しました" in section
        assert "**ボタンが無効（グレーアウト）になっている場合" in section
        # 数値のスコアには触れない（件数は運用状態の事実として残す）
        assert "類似度" in section and "表示しません" in section

    def test_manual_alias_section_states_that_dismiss_is_not_a_deletion(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("## 登録済みの別名 {#aliases}") :]
        section = section[: section.index("\n## ")]
        assert "見送りは削除ではありません" in section
        assert "手動" in section
        assert "#gap-alias-register" in section

    def test_manual_gap_alias_section_frames_it_as_a_possibility(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("### 近い項目の注記と別名として登録 {#gap-alias-register}") :]
        section = section.split("\n### ")[0]
        assert "可能性の提示" in section
        assert "判定ではありません" in section
        assert "教員が決めます" in section
        assert "別名として登録" in section

    def test_materials_manual_documents_the_landing_line(self):
        doc = _read(MATERIALS_MANUAL)
        section = doc[doc.index("### モーダル: arXivから探す {#arxiv-discovery-modal}") :]
        section = section[: section.index("\n### ")]
        assert "地図上の近い領域" in section
        assert "確定した配置では" in section
        assert "骨格の版" in section
        assert "数値は表示されず" in section
