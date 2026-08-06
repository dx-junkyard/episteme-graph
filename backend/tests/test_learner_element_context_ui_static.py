"""学習者向け claim / equation 文脈（上位・下位）UI の静的ガードレール。

対象:
- frontend/public/js/app.js
  - claim チップのポップオーバー → 「詳しく見る」→
    GET /api/learning/courses/{courseId}/elements/claim/{id}/context
  - 数式ブロックカードの「文脈を見る」→ 同 API の equation 版
  - ITEM（{id, element_type, label, relation_label, relation_status, navigable}）の
    描画・裏付けラベル・教材内ジャンプ（担体があるときだけ）
- frontend/public/css/styles.css: 裏付けラベル・数式カードの文脈ボタン

**2026-08-01 の統一パーツカード適用（admin_ux_issues_2026-08-01.md §3.3 Phase 4）で、
DTO の描画は element-card.js（`window.ElementCard`・VARIANT_READONLY）へ移った。**
本ファイルは「導線と DTO の受け渡し」を守り、レーン/バッジの描画そのものは
test_element_card_ui_static.py / test_learner_element_card_ui_static.py が守る。

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
CARD_JS = ROOT / "frontend" / "public" / "js" / "element-card.js"
VOCAB_JS = ROOT / "frontend" / "public" / "js" / "element-vocab.js"
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

    def test_status_labels_delegated_to_element_vocab(self):
        """裏付け状態（relation_status）の段階ラベルは element-vocab.js が正本。
        app.js に独自辞書を持たない（§3.2 P1）。"""
        src = _read(APP_JS)
        assert "var MATERIAL_ELEMENT_CONTEXT_STATUS_LABELS" not in src
        vocab = _read(VOCAB_JS)
        block = _block(vocab, "var STATUS_LABELS = {", "};")
        assert "source_backed:" in block
        assert "confirmed:" in block
        assert "出典に裏付け" in block
        assert "教員確定" in block

    def test_lane_titles_delegated_to_element_card(self):
        """レーン見出しは統一パーツカード（element-card.js）が正本。app.js に
        画面固有の見出し辞書を持たない（§3.2 P2「パーツ1個の描き方は同じ」）。"""
        src = _read(APP_JS)
        assert "var MATERIAL_ELEMENT_CONTEXT_LANE_LABELS" not in src
        card = _read(CARD_JS)
        block = _block(card, "var LANE_TITLES = {", "};")
        assert "この要素が支えるもの" in block
        assert "この要素を支えるもの" in block

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

    def test_panel_renders_through_the_shared_readonly_card(self):
        """描画は統一パーツカード（VARIANT_READONLY）に委譲する（§3.3 Phase 4）。"""
        src = _read(APP_JS)
        block = _block(src, "function renderElementContextPanel(pop, body, elementType, data)", "\n  }\n")
        assert "elementContextToCardDto(elementType, data)" in block
        assert "learnerElementCardOpts(" in block
        assert "learnerElementCardHtml(dto, opts)" in block
        assert "bindLearnerElementCard(body, dto, opts)" in block

    def test_focus_role_and_generic_passed_without_status_gating(self):
        """contextual_role は表示可能なときだけ両キーが来るので、存在すれば渡す。"""
        src = _read(APP_JS)
        block = _block(src, "function elementContextToCardDto(elementType, data)", "\n  }\n")
        assert "focus.contextual_role" in block
        assert "focus.contextual_role_status" in block
        assert "focus.generic" in block
        assert "focus.intrinsic_summary" in block

    def test_both_lanes_passed_to_the_card(self):
        src = _read(APP_JS)
        block = _block(src, "function elementContextToCardDto(elementType, data)", "\n  }\n")
        assert "learnerElementCardItems(data && data.upper)" in block
        assert "learnerElementCardItems(data && data.lower)" in block

    def test_card_item_carries_label_relation_label_and_status(self):
        src = _read(APP_JS)
        block = _block(src, "function learnerElementCardItems(items)", "\n  }\n")
        assert "item.label" in block
        assert "item.relation_label" in block
        assert "item.relation_status" in block
        # 内部語彙 relation / document_id は DTO に無く、参照もしない
        assert "item.relation " not in block
        assert "item.document_id" not in block

    def test_notes_are_not_rendered_to_learners(self):
        """notes には教員向け運用語彙が混ざる余地があるため学習者 UI では出さない。
        カード（element-card.js）は dto.notes をそのまま描くので、**アダプタが
        notes を詰めないこと**が唯一のガードになる。"""
        src = _read(APP_JS)
        for fn in (
            "function elementContextToCardDto(elementType, data)",
            "function componentContextToCardDto(data)",
            "function renderElementContextPanel(pop, body, elementType, data)",
        ):
            block = _block(src, fn, "\n  }\n")
            assert "notes" not in block, f"{fn} が notes を学習者カードへ渡している"

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

        # カード本体は教材本文を知らないため、mount 後に該当行の直後へ差し込む。
        # 行 → ITEM の対応はカードが全行に付ける data-element-card-itemref で引く
        # （ゾーン描画では ITEM が元のレーン順に並ばないため）。
        aug = _block(src, "function augmentLearnerElementCardJumps(container, dto)", "\n  }\n")
        assert "[data-element-card-itemref]" in aug
        assert "materialElementContextJumpRef(item)" in aug
        assert "if (!ref) continue;" in aug
        assert "buildMaterialJumpRow(ref)" in aug
        row = _block(src, "function buildMaterialJumpRow(ref)", "\n  }\n")
        assert "evidence-chip-context-jump-btn" in row
        assert "教材内で見る" in row
        assert "jumpToMaterialEvidenceRef(ref)" in row

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
            "function elementContextToCardDto(elementType, data)",
            "function componentContextToCardDto(data)",
            "function learnerElementCardItems(items)",
        ):
            block = _block(src, fn, "\n  }\n")
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

        # 取得の呼び出し元は open* の2関数（claim チップ経路 / 数式カード経路）と
        # 近傍チップの「旅」1箇所のみ。いずれも押下ハンドラからしか呼ばれない。
        assert src.count("fetchElementContextAndRender(") == 4  # 定義1 + 呼び出し3
        travel = _block(src, "function travelToElementContext(pop, body, item)", "\n  }\n")
        assert "LEARNER_CARD_TRAVEL_TYPES.indexOf(type) < 0) return;" in travel


# ---------------------------------------------------------------------------
# styles.css
# ---------------------------------------------------------------------------


class TestStyles:
    def test_entry_and_jump_styles_defined(self):
        """導線（文脈を見る / 詳しく見る / 教材内で見る）と一時ハイライトの体裁。
        レーン・裏付けバッジ・役割行の体裁は統一パーツカード側（.element-card-*）に
        移ったため、ここでは検証しない（旧 .evidence-chip-context-lane-* は未使用）。"""
        src = _read(STYLES_CSS)
        for selector in (
            ".evidence-chip-popover-context-btn {",
            ".ls-material-context-btn {",
            ".evidence-chip-context-jump-btn {",
            ".ls-material-evidence-jump {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"

    def test_card_styles_used_by_the_learner_panel_exist(self):
        """学習者パネルが借用するカード側セレクタ（近傍レーンと差し込み行）。"""
        src = _read(STYLES_CSS)
        for selector in (
            ".element-card {",
            ".element-card-lanes {",
            ".element-card-items {",
            ".element-card-item {",
            ".element-card-status {",
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
// "var NAME = { ... };" / "var NAME = [ ... ];" の両方に対応する。
function extractVar(src, name){
  const s = src.indexOf("var " + name + " = ");
  if (s<0) throw new Error("missing var "+name);
  let open=-1,oc="",cc="";
  for(let j=src.indexOf("=",s)+1;j<src.length;j++){
    const c=src[j];
    if(c===" "||c==="\n"||c==="\r"||c==="\t") continue;
    if(c==="{"){open=j;oc="{";cc="}";}
    else if(c==="["){open=j;oc="[";cc="]";}
    break;
  }
  if(open<0) throw new Error("unsupported initializer for var "+name);
  let d=0;
  for(let j=open;j<src.length;j++){const c=src[j];if(c===oc){d++;}else if(c===cc){d--;if(d===0)return src.slice(s,j+1)+";";}}
  throw new Error("unbalanced var "+name);
}
// 語彙の正本（element-vocab.js）と統一パーツカード（element-card.js）を app.js と
// 同じディレクトリから読み込む。どちらも window に生やす IIFE なので、harness 側で
// var window を宣言した後に eval すること。
function loadSibling(appPath, name){
  const pathmod=require("path");
  return require("fs").readFileSync(pathmod.join(pathmod.dirname(appPath),name),"utf8");
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_readonly_card_rendering_behaviour_node(tmp_path):
    """claim 文脈 DTO → カード DTO → 統一パーツカード（readonly）の実描画を検証する。

    - 種別チップ・関係語・裏付けラベルが日本語で出る（語彙は element-vocab.js が正本）
    - confidence は一切出ない（W8）
    - candidate 関係はアダプタ段階で落ちる（サーバ除去との二重ガード）
    - 学習者が開けない型（shared_part / figure）は navigable にしない
    - 操作行（「深く検討」等）は readonly では出ない
    """
    script = _EXTRACT + r"""
const fs = require("fs");
const app = fs.readFileSync(process.argv[2], "utf8");
var window = {};
function escHtml(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function renderMaterialKatex(expr, display){ return '<span class="fake-katex">'+escHtml(expr)+'</span>'; }
eval(loadSibling(process.argv[2], "element-vocab.js"));
eval(loadSibling(process.argv[2], "element-card.js"));
eval(extractVar(app, "LEARNER_CARD_TRAVEL_TYPES"));
eval(extractVar(app, "LEARNER_CARD_FOCUS_TYPES"));
eval(extractMany(app, ["learnerElementCardOpts","learnerElementCardHtml",
  "learnerElementCardItems","elementContextToCardDto"]));

const data = {
  available: true,
  element_type: "claim",
  element_id: "cl_focus",
  focus: {element_type:"claim", element_id:"cl_focus", label:"焦点の主張",
          intrinsic_summary:"焦点の主張の本文", contextual_role:"観測量を定義する",
          contextual_role_status:"source_backed"},
  upper: [
    {id:"comp_1", element_type:"theory_component", label:"共鳴条件",
     relation_label:"を支持する", relation_status:"source_backed", navigable:true, confidence:0.91},
    {id:"sp_3", element_type:"shared_part", label:"共通部品",
     relation_label:"に対応する", relation_status:"confirmed", navigable:true}
  ],
  lower: [
    {id:"cl_2", element_type:"theory_claim", label:"主張2",
     relation_label:"の前提になる", relation_status:"confirmed", navigable:true},
    {id:"cand", element_type:"theory_claim", label:"AI候補の関係",
     relation_label:"に関連する", relation_status:"candidate", navigable:true}
  ],
  notes: ["教員向けの運用メモ"]
};
const dto = elementContextToCardDto("claim", data);
const opts = learnerElementCardOpts(function(){});
const html = learnerElementCardHtml(dto, opts);
process.stdout.write(JSON.stringify({
  variantIsReadonly: opts.variant === "readonly" && html.indexOf("element-card-readonly") >= 0,
  hasKindChip: html.indexOf(">主張<") >= 0,
  hasBody: html.indexOf("焦点の主張の本文") >= 0,
  hasRole: html.indexOf("観測量を定義する") >= 0,
  hasLabels: html.indexOf("共鳴条件") >= 0 && html.indexOf("主張2") >= 0,
  hasRelationLabel: html.indexOf("を支持する") >= 0,
  hasStatusLabels: html.indexOf("出典に裏付け") >= 0 && html.indexOf("教員確定") >= 0,
  noRawStatusText: html.indexOf(">source_backed<") < 0 && html.indexOf(">confirmed<") < 0,
  noConfidence: html.indexOf("0.91") < 0 && html.indexOf("confidence") < 0,
  candidateDropped: html.indexOf("AI候補の関係") < 0 && dto.lower.length === 1,
  travelOnlyForOpenableTypes:
    dto.upper[0].navigable === true && dto.upper[1].navigable === false,
  notesNotPassed: !("notes" in dto) && html.indexOf("教員向けの運用メモ") < 0,
  noActionRow: html.indexOf("element-card-actions") < 0 && html.indexOf("深く検討") < 0
}));
"""
    harness = tmp_path / "h.js"
    harness.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(APP_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["variantIsReadonly"], "学習者カードは VARIANT_READONLY で描く"
    assert out["hasKindChip"], "種別チップは日本語（ElementVocab 正本）"
    assert out["hasBody"]
    assert out["hasRole"]
    assert out["hasLabels"]
    assert out["hasRelationLabel"]
    assert out["hasStatusLabels"], "relation_status は段階ラベルで表示する"
    assert out["noRawStatusText"], "内部語彙（source_backed 等）を本文として出さない"
    assert out["noConfidence"], "confidence を学習者に出さない"
    assert out["candidateDropped"], "candidate 関係はアダプタで落とす"
    assert out["travelOnlyForOpenableTypes"], "学習者が開けない型を navigable にしない"
    assert out["notesNotPassed"], "notes を学習者カードへ渡さない"
    assert out["noActionRow"], "readonly バリアントに操作行を出さない"
