"""学習者向け claim / equation 文脈（上位・下位）UI の静的ガードレール。

対象:
- frontend/public/js/app.js
  - claim チップのポップオーバー → 「詳しく見る」→
    GET /api/learning/courses/{courseId}/elements/claim/{id}/context
  - 数式ブロックカードの「文脈を見る」→ 同 API の equation 版
  - ITEM（{id, element_type, label, relation_label, relation_status, navigable}）の
    レーン描画・裏付けラベル・教材内ジャンプ（担体があるときだけ）
- frontend/public/css/styles.css: レーン・裏付けラベル・数式カードの文脈ボタン

不変条項の構造的検証:
- source / figure / component からこの API を呼ばない（component は専用の
  /components/{id}/context のまま）
- confidence 等の生数値を描かない・段階ラベルのみ
- 自動では開かない（ページ描画パスから文脈 API を呼ばない。押下ハンドラのみ）
- 学習者に notes（教員向け運用語彙が混ざる余地がある）を出さない
- 煽り語・スコア・進捗数値を新規文言に入れない

バックエンド API は backend/api/routes/learning.py::get_course_element_context
（実装済み）。ここでは JS/CSS 側の静的契約のみを検証する（node 依存の実評価は
他の UI 静的テストと同じ方針で、node が無い環境では skip）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _block(src: str, start_marker: str, end_marker: str = "\n  }") -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# ---------------------------------------------------------------------------
# 語彙・定数
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_only_claim_and_equation_are_supported_element_types(self):
        src = _read(APP_JS)
        assert 'var MATERIAL_ELEMENT_CONTEXT_TYPES = ["claim", "equation"];' in src

    def test_api_path_segments_whitelisted(self):
        """パスは claim / equation のホワイトリストからのみ組む（fail-closed）。"""
        src = _read(APP_JS)
        block = _block(src, "var MATERIAL_ELEMENT_CONTEXT_PATHS = {", "};")
        assert '"/elements/claim/"' in block
        assert '"/elements/equation/"' in block
        for forbidden in ("/elements/source/", "/elements/figure/", "/elements/component/"):
            assert forbidden not in src, f"{forbidden} を叩く経路があってはならない"

    def test_relation_status_labels_are_two_values_only(self):
        """relation_status は source_backed / confirmed の2値のみ（candidate は API 側で除外）。"""
        src = _read(APP_JS)
        block = _block(src, "var MATERIAL_ELEMENT_CONTEXT_STATUS_LABELS = {", "};")
        assert "source_backed:" in block
        assert "confirmed:" in block
        assert "出典に裏付け" in block
        assert "教員確定" in block
        assert "candidate" not in block

    def test_lane_labels_use_subject_specific_factual_wording(self):
        src = _read(APP_JS)
        block = _block(src, "var MATERIAL_ELEMENT_CONTEXT_LANE_LABELS = {", "};")
        assert "この主張が支えるもの" in block
        assert "この主張の根拠" in block
        assert "この数式が支えるもの" in block
        assert "この数式の根拠" in block

    def test_ref_kind_reverse_map_excludes_shared_part(self):
        """ITEM.element_type → 教材本文の data-evidence-ref kind の逆写像。
        shared_part は教材本文に担体を持たないので含めない。"""
        src = _read(APP_JS)
        block = _block(src, "var MATERIAL_ELEMENT_CONTEXT_REF_KINDS = {", "};")
        assert "theory_component:" in block
        assert "theory_claim:" in block
        assert "equation:" in block
        assert "figure:" in block
        assert "shared_part" not in block


# ---------------------------------------------------------------------------
# claim チップ導線
# ---------------------------------------------------------------------------


class TestClaimChipEntry:
    def test_claim_popover_offers_context_more_button(self):
        src = _read(APP_JS)
        block = _block(src, "function renderClaimChipPopoverBody(item)")
        assert "elementContextMoreButton(" in block
        assert '"claim"' in block

    def test_context_more_button_markup_carries_type_and_id(self):
        src = _read(APP_JS)
        block = _block(src, "function elementContextMoreButton(elementType, elementId)")
        assert "詳しく見る" in block
        assert "evidence-chip-popover-context-btn" in block
        assert "data-element-context-type" in block
        assert "data-element-context-id" in block
        # fail-closed: 許可された型以外はボタン自体を出さない
        assert "MATERIAL_ELEMENT_CONTEXT_TYPES.indexOf(elementType) < 0" in block

    def test_claim_button_wired_to_element_context_panel_not_component_api(self):
        src = _read(APP_JS)
        block = _block(src, "function openEvidenceChipPopover(anchor, entry)", "\n  }\n")
        assert ".evidence-chip-popover-context-btn" in block
        assert "openElementContextPanel(" in block
        # component 用の既存ボタン（別 API）も引き続き別配線で残っている
        assert ".evidence-chip-popover-more-btn" in block
        assert "openEvidenceChipContextPanel(" in block


# ---------------------------------------------------------------------------
# equation カード導線
# ---------------------------------------------------------------------------


class TestEquationCardEntry:
    def test_equation_block_card_gets_context_toggle(self):
        src = _read(APP_JS)
        start = src.index('ls-material-embed ls-material-formula-only" data-evidence-ref="equation:')
        block = src[start:start + 400]
        assert 'renderMaterialElementContextButton("equation", embedId)' in block

    def test_context_toggle_markup(self):
        src = _read(APP_JS)
        block = _block(src, "function renderMaterialElementContextButton(elementType, elementId)")
        assert "ls-material-context-btn" in block
        assert "文脈を見る" in block
        assert "data-element-context-type" in block
        assert "data-element-context-id" in block
        assert "MATERIAL_ELEMENT_CONTEXT_TYPES.indexOf(elementType) < 0" in block

    def test_toggle_click_delegated_to_element_context_popover(self):
        src = _read(APP_JS)
        block = _block(src, "function initMaterialEvidenceChipDelegation()", "\n  }\n")
        assert '.ls-material-context-btn' in block
        assert "openElementContextPopover(" in block
        # チップ用ポップオーバー（component/claim）の配線も維持
        assert "openEvidenceChipPopover(" in block

    def test_pending_equation_card_has_no_context_toggle(self):
        """本文が無い「数式は準備中です」カードには文脈トグルを出さない。"""
        src = _read(APP_JS)
        start = src.index("ls-material-embed ls-material-formula-pending")
        block = src[start:start + 400]
        assert "renderMaterialElementContextButton" not in block


# ---------------------------------------------------------------------------
# 取得・描画
# ---------------------------------------------------------------------------


class TestFetchAndRender:
    def test_api_path_builder_uses_course_scoped_learning_path(self):
        src = _read(APP_JS)
        block = _block(src, "function elementContextApiPath(elementType, elementId)")
        assert '"/learning/courses/" + state.courseId + seg' in block
        assert 'encodeURIComponent(elementId) + "/context"' in block
        assert "if (!seg" in block  # 未知の型はパスを組まない

    def test_failure_and_404_degrade_to_factual_sentence(self):
        src = _read(APP_JS)
        block = _block(src, "async function fetchElementContextAndRender(pop, body, elementType, elementId)")
        assert "if (!res.ok)" in block
        assert block.count("MATERIAL_ELEMENT_CONTEXT_UNAVAILABLE") >= 3  # path 不成立 / !ok / catch
        assert "文脈情報はまだありません。" in src

    def test_available_false_renders_server_note_or_fallback(self):
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "data.available !== true" in block
        assert "data.note" in block
        assert "MATERIAL_ELEMENT_CONTEXT_UNAVAILABLE" in block

    def test_focus_role_and_generic_rendered_without_status_gating(self):
        """contextual_role は表示可能なときだけ両キーが来るので、存在すれば出す。"""
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "focus.contextual_role" in block
        assert "この論文での役割" in block
        assert "elementContextStatusBadge(focus.contextual_role_status)" in block
        assert "focus.generic" in block
        assert "一般には: " in block
        assert "focus.intrinsic_summary" in block

    def test_both_lanes_rendered(self):
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "renderElementContextLane(lanes.upper, data.upper)" in block
        assert "renderElementContextLane(lanes.lower, data.lower)" in block

    def test_lane_row_uses_label_relation_label_and_status_badge(self):
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextLane(title, items)", "\n  }\n")
        assert "item.label" in block
        assert "item.relation_label" in block
        assert "elementContextStatusBadge(item.relation_status)" in block
        # 内部語彙 relation / document_id は DTO に無く、参照もしない
        assert "item.relation " not in block
        assert "item.document_id" not in block

    def test_notes_are_not_rendered_to_learners(self):
        """notes には教員向け運用語彙が混ざる余地があるため学習者 UI では出さない。"""
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "data.notes" not in block
        assert ".notes" not in block

    def test_topic_scoped_memory_cache_cleared_on_topic_switch(self):
        src = _read(APP_JS)
        assert "var materialElementContextCache = {};" in src
        assert "function clearMaterialElementContextCache()" in src
        select_topic = _block(src, "async function selectTopic(topicId, opts)", "\n  }\n")
        assert "clearMaterialElementContextCache();" in select_topic
        fetch_block = _block(src, "async function fetchElementContextAndRender(pop, body, elementType, elementId)")
        assert "materialElementContextCache[cacheKey]" in fetch_block


# ---------------------------------------------------------------------------
# 教材内ジャンプ
# ---------------------------------------------------------------------------


class TestInMaterialJump:
    def test_jump_button_only_when_carrier_exists_in_dom(self):
        src = _read(APP_JS)
        block = _block(src, "function materialElementContextJumpRef(item)")
        assert "MATERIAL_ELEMENT_CONTEXT_REF_KINDS[item.element_type]" in block
        assert "normalizeMaterialEvidenceId(item.id)" in block
        assert 'document.querySelector(\'[data-evidence-ref="\' + ref + \'"]\')' in block
        assert 'return ""' in block  # 担体が無ければ ref を返さない = ボタンを出さない

        lane = _block(src, "function renderElementContextLane(title, items)", "\n  }\n")
        assert "jumpRef" in lane
        assert "evidence-chip-context-jump-btn" in lane
        assert "教材内で見る" in lane

    def test_jump_closes_popover_scrolls_and_highlights_temporarily(self):
        src = _read(APP_JS)
        block = _block(src, "function jumpToMaterialEvidenceRef(ref)")
        assert "closeSourcePopup();" in block
        assert "scrollIntoView(" in block
        assert "ls-material-evidence-jump" in block
        assert "setTimeout(" in block  # 一時ハイライト（自動解除）

    def test_no_learner_facing_deliberation_entry(self):
        """学習者 UI から W層（教員向け「深く検討」）は開かない。"""
        src = _read(APP_JS)
        assert "深く検討" not in src
        assert "window.Deliberation" not in src


# ---------------------------------------------------------------------------
# 数値・煽り語の非表示 / 自動オープン禁止
# ---------------------------------------------------------------------------


class TestNoNumbersNoAutoOpen:
    def test_confidence_never_rendered(self):
        src = _read(APP_JS)
        for fn in (
            "function renderElementContextPanel(pop, body, elementType, data)",
            "function renderElementContextLane(title, items)",
            "function elementContextStatusBadge(status)",
        ):
            block = _block(src, fn, "\n  }\n") if fn.endswith("data)") or fn.endswith("items)") else _block(src, fn)
            assert "confidence" not in block, f"{fn} が confidence を描いている"
            assert "score" not in block

    def test_new_wording_has_no_hype_or_score_vocabulary(self):
        src = _read(APP_JS)
        start = src.index("// ── claim / equation の上位・下位文脈パネル")
        end = src.index("// ── 1-hop 近傍グラフ", start)
        block = src[start:end]
        for banned in ("寄り道", "スコア", "正答率", "達成率", "％", "ランキング", "おすすめ", "すごい"):
            assert banned not in block, f"新規文言に {banned} が混ざっている"

    def test_context_fetch_not_called_from_render_paths(self):
        """自動では開かない: 教材描画・トピック描画から文脈 API を呼ばない
        （呼び出しは押下ハンドラ経由の open* 関数のみ）。"""
        src = _read(APP_JS)
        for fn, end in (
            ("function renderMaterialChunk(chunk) {", "\n  function renderMaterialMissingEmbed"),
            ("function renderRightPanel()", "\n  }\n"),
        ):
            block = _block(src, fn, end)
            assert "fetchElementContextAndRender" not in block
            assert "openElementContextPopover" not in block
            assert "openElementContextPanel" not in block

        # 取得の呼び出し元は open* の2関数のみ（claim チップ経路 / 数式カード経路）。
        # どちらも押下ハンドラからしか呼ばれない。
        assert src.count("fetchElementContextAndRender(") == 3  # 定義1 + 呼び出し2


# ---------------------------------------------------------------------------
# styles.css
# ---------------------------------------------------------------------------


class TestStyles:
    def test_lane_and_badge_and_toggle_styles_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".evidence-chip-popover-context-btn {",
            ".ls-material-context-btn {",
            ".evidence-chip-context-role {",
            ".evidence-chip-context-generic {",
            ".evidence-chip-context-status {",
            ".evidence-chip-context-lane-item {",
            ".evidence-chip-context-lane-label {",
            ".evidence-chip-context-lane-relation {",
            ".evidence-chip-context-jump-btn {",
            ".ls-material-evidence-jump {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"

    def test_existing_popover_styles_untouched(self):
        src = _read(STYLES_CSS)
        assert ".evidence-chip-popover-more-btn {" in src
        assert ".evidence-chip-context-tabs {" in src

    def test_highlight_does_not_blink(self):
        """一時ハイライトは点滅アニメーションにしない（煽らない・注意を奪わない）。"""
        src = _read(STYLES_CSS)
        block = _block(src, ".ls-material-evidence-jump {", "}")
        assert "animation" not in block


# ---------------------------------------------------------------------------
# 実行時の振る舞い（node が使える環境のみ）
# ---------------------------------------------------------------------------

_EXTRACT = r"""
function extractFrom(src, name){
  const s = src.indexOf("function " + name + "(");
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function extractMany(src, names){ return names.map(function(n){ return extractFrom(src,n); }).join("\n"); }
function extractVar(src, name){
  const s = src.indexOf("var " + name + " = {");
  if (s<0) throw new Error("missing var "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1)+";";}}
  throw new Error("unbalanced var "+name);
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_lane_rendering_behaviour_node(tmp_path):
    """レーン描画を実評価し、ラベル+relation_label+裏付けラベルが出て、担体の無い
    ITEM には「教材内で見る」が付かないこと、confidence が漏れないことを確認する。"""
    script = _EXTRACT + r"""
const fs = require("fs");
const app = fs.readFileSync(process.argv[2], "utf8");
function escHtml(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
// 担体は「存在しない」= どの ref も見つからない DOM スタブ。
var document = { querySelector: function(){ return null; } };
eval(extractVar(app, "MATERIAL_ELEMENT_CONTEXT_STATUS_LABELS"));
eval(extractVar(app, "MATERIAL_ELEMENT_CONTEXT_REF_KINDS"));
eval(extractMany(app, ["normalizeMaterialEvidenceId","elementContextStatusBadge",
  "materialElementContextJumpRef","renderElementContextLane"]));

const html = renderElementContextLane("この主張が支えるもの", [
  {id:"comp_1", element_type:"theory_component", label:"共鳴条件",
   relation_label:"を支持する", relation_status:"source_backed", navigable:true, confidence:0.91},
  {id:"cl_2", element_type:"theory_claim", label:"主張2",
   relation_label:"の前提になる", relation_status:"confirmed", navigable:false},
  {id:"sp_3", element_type:"shared_part", label:"共通部品", relation_label:"に対応する",
   relation_status:"confirmed", navigable:true}
]);
const empty = renderElementContextLane("この主張の根拠", []);
process.stdout.write(JSON.stringify({
  hasTitle: html.indexOf("この主張が支えるもの") >= 0,
  hasLabels: html.indexOf("共鳴条件") >= 0 && html.indexOf("主張2") >= 0,
  hasRelationLabel: html.indexOf("を支持する") >= 0,
  hasStatusLabels: html.indexOf("出典に裏付け") >= 0 && html.indexOf("教員確定") >= 0,
  noRawStatus: html.indexOf("source_backed") < 0 && html.indexOf("confirmed") < 0,
  noConfidence: html.indexOf("0.91") < 0 && html.indexOf("confidence") < 0,
  noJumpButtonWithoutCarrier: html.indexOf("教材内で見る") < 0,
  emptyLaneIsEmpty: empty === ""
}));
"""
    harness = tmp_path / "h.js"
    harness.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(APP_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hasTitle"]
    assert out["hasLabels"]
    assert out["hasRelationLabel"]
    assert out["hasStatusLabels"], "relation_status は段階ラベルで表示する"
    assert out["noRawStatus"], "内部語彙（source_backed 等）を素で出さない"
    assert out["noConfidence"], "confidence を学習者に出さない"
    assert out["noJumpButtonWithoutCarrier"], "担体が無い ITEM にジャンプボタンを出さない"
    assert out["emptyLaneIsEmpty"], "空レーンは見出しごと出さない"
