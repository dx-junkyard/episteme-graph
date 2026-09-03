"""グラフ対話レビュー — フロントエンドの静的検証。

正本: ``docs/features/graph_dialogue_review_design.md`` §3/§6/§7。
admin-graph-review.js（ES5・GraphReview）・admin.js の導線・admin.html の読み込み順・
CSS の存在を、Node 実行なしのソース検査で確認する。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_SRC = (ROOT / "frontend" / "public" / "js" / "admin-graph-review.js").read_text(encoding="utf-8")
VOICE_SRC = (ROOT / "frontend" / "public" / "js" / "admin-voice-chat.js").read_text(encoding="utf-8")
ADMIN_SRC = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
STUDIO_SRC = (ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
HTML_SRC = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
CSS_SRC = (ROOT / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8")


class TestEs5Compat:
    def test_no_es6_syntax(self):
        # 管理画面 JS は ES5 互換（開発ルール5）。
        assert "=>" not in JS_SRC
        assert not re.search(r"\bconst\s", JS_SRC)
        assert not re.search(r"\blet\s", JS_SRC)
        assert "`" not in JS_SRC  # テンプレートリテラル禁止

    def test_iife_and_public_api(self):
        assert "window.GraphReview" in JS_SRC
        assert '"use strict"' in JS_SRC


class TestAnchors:
    def test_all_screen_anchors_present(self):
        for anchor in (
            "graph-review.modal",
            "graph-review.layer",
            "graph-review.filter-unreviewed",
            "graph-review.next-unreviewed",
            "graph-review.approve",
            "graph-review.reject",
            "graph-review.claim-approve",
            "graph-review.chat",
            "graph-review.graph-chat",
            "graph-review.open-deliberation",
            "graph-review.new-chat",
            "graph-review.voice",
        ):
            assert 'data-ui-anchor="' + anchor + '"' in JS_SRC, anchor

    def test_row_button_anchor_in_admin_js(self):
        assert 'data-ui-anchor="materials.row-graph-review"' in ADMIN_SRC


class TestAdminWiring:
    def test_row_button_requires_document_id(self):
        # 検出要素ボタンと同条件: document_id を持つ行のみメニューに出す。
        idx = ADMIN_SRC.index("admin-graph-review-btn")
        assert "m.document_id" in ADMIN_SRC[max(0, idx - 600):idx]

    def test_click_opens_graph_review(self):
        assert "window.GraphReview.open(" in ADMIN_SRC

    def test_init_injected(self):
        assert (
            "window.GraphReview.init({ apiFetch: apiFetch, escHtml: escHtml, getToken: getAuthToken })"
            in ADMIN_SRC
        )

    def test_assistant_anchor_resolver_registered(self):
        assert "material_graph_review_button" in ADMIN_SRC


class TestGraphViewDelegation:
    def test_uses_lecture_studio_graph_view(self):
        assert "window.LectureStudio && window.LectureStudio.graphView" in JS_SRC
        for api in ("filterByLayer", "layerOptions", "layoutPositions", "displayEdges",
                    "visNodeSpec", "visEdgeSpec", "networkOptions", "detailHeading",
                    "sourceBackingLabel", "reviewReasonLabel"):
            assert api in JS_SRC, api

    def test_studio_delegates_layer_filter(self):
        # lsGraphForCurrentLayer は純粋版への薄い委譲（挙動不変のリファクタ）。
        idx = STUDIO_SRC.index("function lsGraphForCurrentLayer")
        body = STUDIO_SRC[idx: STUDIO_SRC.index("}", idx) + 1]
        assert "lsGraphFilterByLayer(graph, lsState.graphLayerFilter" in body


class TestReviewSemantics:
    def test_unreviewed_filter_keeps_context(self):
        # 未レビュー強調は非該当ノードを消さず薄くする（構造の文脈を保つ）。
        assert "opacity = 0.22" in JS_SRC

    def test_component_actions_present(self):
        assert ">承認</button>" in JS_SRC
        assert ">却下</button>" in JS_SRC
        assert "深く検討" in JS_SRC

    def test_claim_approve_uses_transition_api(self):
        assert '"/review"' in JS_SRC or "/review\"" in JS_SRC
        assert "teacher_approved" in JS_SRC

    def test_component_actions_use_transition_endpoints(self):
        # フル PUT を使わない（同時編集の巻き戻し防止 — 設計書 §4）。
        assert '"/admin/theory-components/" + encodeURIComponent(componentId) + "/" + action' in JS_SRC
        assert 'method: "PUT"' not in JS_SRC

    def test_reload_after_decision(self):
        # 承認・却下後はサーバの状態で再描画（楽観更新でズレを残さない）。
        assert "loadGraph(true)" in JS_SRC


class TestChat:
    def test_graph_chat_uses_graph_sessions_api(self):
        assert "/graph-sessions" in JS_SRC

    def test_node_chat_uses_w_layer_sessions(self):
        assert '"/admin/deliberation/sessions"' in JS_SRC
        assert '"theory_component"' in JS_SRC or "element_type: \"theory_component\"" in JS_SRC

    def test_degraded_reply_is_labeled(self):
        assert "縮退応答" in JS_SRC


class TestVoiceChat:
    """音声対話追補（設計書 §12）— エンジンの独立性と GR1 の維持を静的に固定する。"""

    def _voice_section(self) -> str:
        # 音声配線の区画（ハンズフリー音声対話 〜 公開 API の直前）。
        start = JS_SRC.index("ハンズフリー音声対話")
        end = JS_SRC.index("公開 API", start)
        return JS_SRC[start:end]

    def test_engine_is_es5(self):
        # 管理画面 JS は ES5 互換（開発ルール5）。
        assert "=>" not in VOICE_SRC
        assert not re.search(r"\bconst\s", VOICE_SRC)
        assert not re.search(r"\blet\s", VOICE_SRC)
        assert "`" not in VOICE_SRC

    def test_engine_public_api(self):
        assert "window.AdminVoiceChat" in VOICE_SRC
        assert '"use strict"' in VOICE_SRC

    def test_engine_is_dom_independent(self):
        # エンジンは画面を知らない（アンカー担体は admin-graph-review.js 側）。
        assert "data-ui-anchor" not in VOICE_SRC

    def test_script_order_engine_before_review(self):
        voice = HTML_SRC.index('src="/js/admin-voice-chat.js')
        review = HTML_SRC.index('src="/js/admin-graph-review.js')
        assert voice < review

    def test_voice_section_never_calls_decision_api(self):
        # GR1: 音声は対話の入出力手段のみ。承認・却下 API を呼ぶ経路を作らない。
        section = self._voice_section()
        assert "/approve" not in section
        assert "/reject" not in section

    def test_voice_loop_stops_on_rate_limit(self):
        # 上限（429）に達したら回し続けない。
        idx = JS_SRC.index("function voiceUtterance")
        body = JS_SRC[idx: JS_SRC.index("function voiceTranscribe", idx)]
        assert "429" in body
        assert "stopVoice(" in body

    def test_close_stops_voice(self):
        # 画面を閉じたらマイクを解放する（見えない場所で録音を続けない）。
        idx = JS_SRC.index("function close()")
        assert "stopVoice()" in JS_SRC[idx: idx + 600]


class TestHtmlAndCss:
    def test_script_loaded_between_studio_and_admin(self):
        studio = HTML_SRC.index("admin-lecture-studio.js")
        review = HTML_SRC.index("admin-graph-review.js")
        admin = HTML_SRC.index("/js/admin.js")
        assert studio < review < admin

    def test_css_defined(self):
        assert ".graph-review-modal" in CSS_SRC
        assert ".graph-review-modal[hidden]" in CSS_SRC


class TestDeliberationTargetResolution:
    """「深く検討」・ノード対話の要素解決（2026-09 是正、設計書 §11）。

    理論操作グラフの main / equation_detail ノードは graph-native ID
    （theory_op_0001 / eq_op_0001）で theory_components の行を持たない。ノード ID を
    そのまま渡すとサーバ 422 になるため、代表要素（representative_component_id =
    component_assembly の agent 側 ID）へ解決し、解決できないノードではボタンを
    出さずに事実文で案内する（GR3: 数値を出さない・原因を偽らない）。
    """

    def _block(self, name: str) -> str:
        start = JS_SRC.index("function " + name + "(")
        return JS_SRC[start: JS_SRC.index("\n  }\n", start) + 4]

    def test_target_resolver_prefers_db_uuid_then_representative(self):
        block = self._block("deliberationTargetId")
        assert "isDbUuid(nodeId)" in block
        assert "representative_component_id" in block
        assert "linked_component_ids" in block

    def test_open_deliberation_uses_resolved_target(self):
        block = self._block("openDeliberation")
        assert "deliberationTargetId(node)" in block
        assert 'openElement("theory_component", targetId' in block
        # 生のノード ID を要素 ID として渡さない（422 の原因）。
        assert 'openElement("theory_component", g.nodeId(node)' not in JS_SRC

    def test_button_hidden_when_node_has_no_resolvable_element(self):
        # 解決できないノードは 422 の裏に隠さず、事実文で案内する。
        assert "集約元の要素を特定できないため" in JS_SRC
        assert "「深く検討」は集約元の代表要素を開きます。" in JS_SRC
        assert "var deliberateBtn = deliberationTarget" in JS_SRC

    def test_node_chat_uses_same_target(self):
        block = self._block("ensureSession")
        assert "deliberationTargetId(node)" in block
        assert "element_id: componentId" in block


class TestReviewReasonPresentation:
    """review_reasons の表示是正（2026-09-01）。

    サーバの読み時射影（theory_components.py::_normalize_stored_component_graph）が
    ①承認済みノードの理由を review_reasons_at_analysis へ移し、②source_backed の
    warning を review_reasons_advisory と宣言する。UI はレビュー要求（要確認の理由）と
    参考メモ・解析時点メモを見出しと色で区別し、承認済みノードにレビューを促す
    表示を残さない。
    """

    def test_advisory_reasons_use_non_review_heading(self):
        assert "review_reasons_advisory" in JS_SRC
        assert "解析メモ（参考）: " in JS_SRC
        assert "要確認の理由: " in JS_SRC

    def test_archived_reasons_rendered_without_review_prompt(self):
        assert "review_reasons_at_analysis" in JS_SRC
        assert "解析時点のメモ（承認済みのため確認は不要です）: " in JS_SRC

    def test_advisory_and_archived_styles_are_not_warning_colored(self):
        assert ".graph-review-detail-reasons-advisory" in CSS_SRC
        assert ".graph-review-detail-reasons-archived" in CSS_SRC

    def test_graph_updated_at_fact_line(self):
        # いつの解析結果を見ているかを隠さない（焼き込みグラフの鮮度の事実文）。
        assert "graph_updated_at" in JS_SRC
        assert "の解析結果を表示しています" in JS_SRC
        assert ".graph-review-graph-updated" in CSS_SRC


class TestArtifactResolvedClaims:
    """解析結果由来の根拠 claim の表示（2026-09-02 是正）。

    atomic rewrite の細分化 claim / 式から合成した claim は theory_claims の行を
    持たないため、従来は本文ごと「未解決の根拠（本文を取得できません）」に落ちていた。
    バックエンドが reference_index を artifact 解決へ拡張したので、UI は本文を出し、
    承認行が無いことを「未承認（解析結果）」と明示する（隠さない・偽らない）。
    """

    def test_artifact_labels_present(self):
        assert "未承認（解析結果）" in JS_SRC
        assert '"式から合成"' in JS_SRC
        assert '"主張の細分化"' in JS_SRC
        assert "元の主張: " in JS_SRC
        assert ">元の主張を承認</button>" in JS_SRC
        assert "解析結果のみの根拠で、承認対象の行はありません。" in JS_SRC

    def test_unresolved_fallback_kept(self):
        # 参照インデックスに無い ID は従来どおり正直に未解決と告げる。
        assert "未解決の根拠（本文を取得できません）" in JS_SRC
        assert '"未解決"' in JS_SRC

    def test_parent_approval_reuses_existing_anchor_and_attribute(self):
        # 新しいアンカー ID を作らない（ADMIN_UI_ANCHORS の網羅テストと二重管理にしない）。
        block = JS_SRC[JS_SRC.index("function claimRowHtml("):]
        block = block[: block.index("\n  function ")]
        assert 'data-ui-anchor="graph-review.claim-approve"' in block
        assert 'data-graph-review-claim="' in block
        assert "isApproved(claim.parent_review_status)" in block

    def test_agent_ids_never_interpolated_into_row(self):
        # 内部 ID（synth_claim_0001 等）は教員 UI に出さない。
        block = JS_SRC[JS_SRC.index("function claimRowHtml("):]
        block = block[: block.index("\n  function ")]
        assert "agent_id" not in block

    def test_es5_in_new_block(self):
        block = JS_SRC[JS_SRC.index("function claimRowHtml("):]
        block = block[: block.index("\n  function ")]
        assert "=>" not in block
        assert not re.search(r"\bconst\s", block)
        assert not re.search(r"\blet\s", block)
        assert "`" not in block


_CLAIM_ROW_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extractFrom(s0, name){
  const s = s0.indexOf("function " + name + "(");
  if (s<0) throw new Error("missing "+name);
  let d=0, started=false;
  for(let j=s0.indexOf("{",s); j<s0.length; j++){
    const c=s0[j];
    if(c==="{"){d++;started=true;}
    else if(c==="}"){d--;if(started&&d===0)return s0.slice(s,j+1);}
  }
  throw new Error("unbalanced "+name);
}
function extractObjVar(s0, name){
  const s = s0.indexOf("var " + name + " = {");
  if (s<0) throw new Error("missing var "+name);
  const e = s0.indexOf("\n  };\n", s);
  if (e<0) throw new Error("unbalanced var "+name);
  return s0.slice(s, e + 6);
}
function extractLineVar(s0, name){
  const m = new RegExp("var " + name + " = \\{[^\\n]*\\};").exec(s0);
  if (!m) throw new Error("missing line var "+name);
  return m[0];
}
var deps = { escHtml: function (t) {
  return String(t == null ? "" : t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
} };
// 原稿スタジオ未ロードの縮退経路（richText → esc）を検証するため window は空にする。
var window = {};
var state = { graph: { reference_index: { claims: {
  "claim_uuid_ref": { claim_id: "11111111-2222-3333-4444-555555555555",
                      text: "DB 由来の主張", review_status: "teacher_review_required",
                      resolution: "db" },
  "synth_claim_0001": { claim_id: "", text: "式から合成された主張", review_status: "",
                        resolution: "artifact", origin: "equation_synthesis",
                        support_status: "source_backed", is_atomic: true,
                        parent_claim_id: "", parent_review_status: "" },
  "claim_span_001_13_sub04": { claim_id: "", text: "細分化された主張", review_status: "",
                               resolution: "artifact", origin: "atomic_rewrite",
                               support_status: "source_backed", is_atomic: true,
                               parent_claim_id: "99999999-8888-7777-6666-555555555555",
                               parent_review_status: "teacher_review_required" },
  "legacy_ref": { claim_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", text: "旧グラフの主張",
                  review_status: "teacher_approved" }
} } } };
eval(extractObjVar(src, "REVIEW_STATUS_LABELS"));
eval(extractLineVar(src, "APPROVED_STATUSES"));
eval(extractObjVar(src, "CLAIM_ORIGIN_LABELS"));
eval(extractFrom(src, "esc"));
eval(extractFrom(src, "gv"));
eval(extractFrom(src, "richText"));
eval(extractFrom(src, "reviewStatusLabel"));
eval(extractFrom(src, "isApproved"));
eval(extractFrom(src, "collectClaimRefs"));
eval(extractFrom(src, "claimRowHtml"));

const refs = collectClaimRefs({ linked_claim_ids: [
  "claim_uuid_ref", "synth_claim_0001", "claim_span_001_13_sub04", "legacy_ref", "ghost_claim_42"
] });
const byAgent = {};
refs.forEach(function (r) { byAgent[r.agent_id] = r; });
const rows = {};
refs.forEach(function (r) { rows[r.agent_id] = claimRowHtml(r); });
const all = refs.map(function (r) { return rows[r.agent_id]; }).join("");

process.stdout.write(JSON.stringify({
  dbResolution: byAgent["claim_uuid_ref"].resolution,
  legacyInferredDb: byAgent["legacy_ref"].resolution,
  artifactResolution: byAgent["synth_claim_0001"].resolution,
  missingResolution: byAgent["ghost_claim_42"].resolution,
  synthShowsText: rows["synth_claim_0001"].indexOf("式から合成された主張") >= 0,
  synthUnapprovedChip: rows["synth_claim_0001"].indexOf("未承認（解析結果）") >= 0,
  synthOriginChip: rows["synth_claim_0001"].indexOf("式から合成") >= 0,
  synthHasNoButton: rows["synth_claim_0001"].indexOf("<button") < 0,
  synthHasNote: rows["synth_claim_0001"].indexOf("承認対象の行はありません") >= 0,
  subShowsText: rows["claim_span_001_13_sub04"].indexOf("細分化された主張") >= 0,
  subOriginChip: rows["claim_span_001_13_sub04"].indexOf("主張の細分化") >= 0,
  subParentChip: rows["claim_span_001_13_sub04"].indexOf("元の主張: 未レビュー") >= 0,
  subParentButton:
    rows["claim_span_001_13_sub04"].indexOf(
      'data-graph-review-claim="99999999-8888-7777-6666-555555555555"') >= 0 &&
    rows["claim_span_001_13_sub04"].indexOf(">元の主張を承認</button>") >= 0,
  subParentButtonEnabled: rows["claim_span_001_13_sub04"].indexOf("disabled") < 0,
  subHasNoNote: rows["claim_span_001_13_sub04"].indexOf("承認対象の行はありません") < 0,
  dbRowUnchanged:
    rows["claim_uuid_ref"].indexOf(">承認</button>") >= 0 &&
    rows["claim_uuid_ref"].indexOf("未レビュー") >= 0 &&
    rows["claim_uuid_ref"].indexOf("未承認（解析結果）") < 0,
  legacyApprovedDisabled: rows["legacy_ref"].indexOf("disabled") >= 0,
  missingRowFallback:
    rows["ghost_claim_42"].indexOf("未解決の根拠（本文を取得できません）") >= 0 &&
    rows["ghost_claim_42"].indexOf("<button") < 0,
  noAgentIdLeak:
    all.indexOf("synth_claim_0001") < 0 &&
    all.indexOf("claim_span_001_13_sub04") < 0 &&
    all.indexOf("ghost_claim_42") < 0
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_claim_row_rendering_behaviour_node(tmp_path):
    """DB 由来 / 解析結果由来 / 未解決 の3系統が期待どおり描き分けられること。"""
    harness = tmp_path / "claim_row.js"
    harness.write_text(_CLAIM_ROW_HARNESS, encoding="utf-8")
    js_path = ROOT / "frontend" / "public" / "js" / "admin-graph-review.js"
    proc = subprocess.run(["node", str(harness), str(js_path)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["dbResolution"] == "db"
    assert out["legacyInferredDb"] == "db", "旧グラフ（resolution 無し）は claim_id で DB 判定"
    assert out["artifactResolution"] == "artifact"
    assert out["missingResolution"] == ""
    assert out["synthShowsText"], "解析結果由来でも本文を隠さない"
    assert out["synthUnapprovedChip"]
    assert out["synthOriginChip"]
    assert out["synthHasNoButton"], "式から合成した claim は単体では承認できない"
    assert out["synthHasNote"]
    assert out["subShowsText"]
    assert out["subOriginChip"]
    assert out["subParentChip"]
    assert out["subParentButton"], "元の主張へ承認を導く"
    assert out["subParentButtonEnabled"]
    assert out["subHasNoNote"]
    assert out["dbRowUnchanged"], "DB 由来の行は従来どおり"
    assert out["legacyApprovedDisabled"], "承認済みは再承認させない"
    assert out["missingRowFallback"], "本当に解決できない参照は正直に未解決と告げる"
    assert out["noAgentIdLeak"], "内部 ID を教員 UI に出さない"


class TestInlineMathInClaimText:
    """根拠 claim 本文・ノード説明の数式描画（2026-09-03）。

    claim 本文はインライン数式を ``$…$`` で持つ（バックエンドの合成・修復とも同規約）。
    生の ``$P_{\\rm L}(k)$`` を教員に読ませないため KaTeX で描画するが、描画の実装は
    原稿スタジオの正本に一本化する（GR8: 画面ごとに数式パイプラインを増やさない）。
    """

    def _claim_row_block(self) -> str:
        block = JS_SRC[JS_SRC.index("function claimRowHtml("):]
        return block[: block.index("\n  function ")]

    def _studio_helper_block(self) -> str:
        block = STUDIO_SRC[STUDIO_SRC.index("function lsInlineMathHtml("):]
        return block[: block.index("\n  function lsRenderKatex(")]

    def test_claim_text_rendered_through_inline_math_helper(self):
        block = self._claim_row_block()
        assert "richText(claim.text)" in block
        assert "esc(claim.text)" not in block
        # 未解決の正直な事実文は残す。
        assert "未解決の根拠（本文を取得できません）" in block

    def test_rich_text_delegates_to_graph_view_with_esc_fallback(self):
        block = JS_SRC[JS_SRC.index("function richText("):]
        block = block[: block.index("\n  function ")]
        assert "gv()" in block
        assert "view.inlineMathHtml" in block
        assert "return esc(text);" in block, "graphView 未ロード時は素のエスケープへ縮退"

    def test_node_description_uses_same_helper(self):
        assert 'graph-review-detail-desc">\' + richText(node.description)' in JS_SRC

    def test_review_js_has_no_own_math_pipeline(self):
        # 4本目の preserveMath / KaTeX 直呼びを作らない。
        assert "katex" not in JS_SRC.replace("inlineMathHtml", "")
        assert "preserveMath" not in JS_SRC

    def test_helper_defined_and_exported_on_graph_view(self):
        assert "function lsInlineMathHtml(" in STUDIO_SRC
        assert "inlineMathHtml: lsInlineMathHtml" in STUDIO_SRC
        # graphView オブジェクトの中に載っていること（既存キーの隣）。
        view_block = STUDIO_SRC[STUDIO_SRC.index("graphView: {"):]
        view_block = view_block[: view_block.index("}")]
        assert "inlineMathHtml: lsInlineMathHtml" in view_block
        assert "nodeId: lsGraphNodeId" in view_block, "既存キーを落とさない"

    def test_helper_reuses_ls_render_katex(self):
        block = self._studio_helper_block()
        assert "lsRenderKatex(block.expr, block.display)" in block
        assert "renderToString" not in block, "KaTeX 直呼びを増やさない"
        assert "escHtml(" in block, "地の文は必ずエスケープする"

    def test_helper_is_es5(self):
        block = self._studio_helper_block()
        assert "=>" not in block
        assert not re.search(r"\bconst\s", block)
        assert not re.search(r"\blet\s", block)
        assert "`" not in block

    def test_studio_claim_labels_strip_math_delimiters(self):
        # ElementCard は symbol 以外のラベルを数式描画しない（element-card.js の
        # renderMathGated は element_type=symbol のみ）。ラベルでは区切りだけ外す。
        assert "function lsGraphStripMathDelimiters(" in STUDIO_SRC
        assert "lsGraphSnippet(lsGraphStripMathDelimiters(claim.text" in STUDIO_SRC
        assert "lsGraphSnippet(lsGraphStripMathDelimiters(refClaim.text" in STUDIO_SRC

    def test_css_aligns_inline_math_with_row_text(self):
        assert ".graph-review-claim-text .katex" in CSS_SRC
        assert ".graph-review-detail-desc .katex" in CSS_SRC


_INLINE_MATH_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extractFrom(s0, name){
  const s = s0.indexOf("function " + name + "(");
  if (s<0) throw new Error("missing "+name);
  let d=0, started=false;
  for(let j=s0.indexOf("{",s); j<s0.length; j++){
    const c=s0[j];
    if(c==="{"){d++;started=true;}
    else if(c==="}"){d--;if(started&&d===0)return s0.slice(s,j+1);}
  }
  throw new Error("unbalanced "+name);
}
var deps = { escHtml: function (t) {
  return String(t == null ? "" : t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
} };
var window = { katex: { renderToString: function (formula, opts) {
  return '<span class="katex-fake" data-display="' + (opts.displayMode ? "1" : "0") + '">' +
    String(formula) + "</span>";
} } };
eval(extractFrom(src, "escHtml"));
eval(extractFrom(src, "lsNormalizeKatexFormula"));
eval(extractFrom(src, "lsRenderKatex"));
eval(extractFrom(src, "lsInlineMathHtml"));
eval(extractFrom(src, "lsGraphStripMathDelimiters"));

const inline = lsInlineMathHtml("Equation defines $P_{\\rm L}(k)$ here.");
const display = lsInlineMathHtml("before $$a = b$$ after");
const paren = lsInlineMathHtml("value \\(x_i\\) end");
const unbalanced = lsInlineMathHtml("costs $5 per <unit> & more");
const noKatex = (function () {
  const saved = window.katex; window.katex = null;
  const out = lsInlineMathHtml("see $\\alpha$ now");
  window.katex = saved; return out;
})();

process.stdout.write(JSON.stringify({
  inlineWrapped: inline.indexOf('class="lecture-formula visible"') >= 0,
  inlineKeepsTex: inline.indexOf("P_{\\rm L}(k)") >= 0,
  inlineNoDollar: inline.indexOf("$") < 0,
  inlineProseKept: inline.indexOf("Equation defines ") >= 0 && inline.indexOf(" here.") >= 0,
  displayWrapped: display.indexOf('class="lecture-formula-block visible"') >= 0,
  parenWrapped: paren.indexOf('class="lecture-formula visible"') >= 0 &&
    paren.indexOf("x_i") >= 0 && paren.indexOf("\\(") < 0,
  unbalancedLiteral: unbalanced.indexOf("$5 per") >= 0,
  unbalancedEscaped: unbalanced.indexOf("&lt;unit&gt;") >= 0 &&
    unbalanced.indexOf("&amp;") >= 0,
  unbalancedNoMath: unbalanced.indexOf("lecture-formula") < 0,
  noKatexChip: noKatex.indexOf("ls-formula-chip") >= 0 &&
    noKatex.indexOf("\\alpha") >= 0,
  emptyStays: lsInlineMathHtml("") === "",
  stripKeepsTex: lsGraphStripMathDelimiters("defines $P_{\\rm L}(k)$ here") ===
    "defines P_{\\rm L}(k) here"
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_inline_math_helper_behaviour_node(tmp_path):
    """$…$ / \\(…\\) / $$…$$ の描画と、閉じない $ の literal 維持。"""
    harness = tmp_path / "inline_math.js"
    harness.write_text(_INLINE_MATH_HARNESS, encoding="utf-8")
    js_path = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
    proc = subprocess.run(["node", str(harness), str(js_path)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["inlineWrapped"], "$…$ は lecture-formula ラッパで描画"
    assert out["inlineKeepsTex"]
    assert out["inlineNoDollar"], "区切りの $ は出力に残さない"
    assert out["inlineProseKept"]
    assert out["displayWrapped"], "$$…$$ はブロック"
    assert out["parenWrapped"]
    assert out["unbalancedLiteral"], "閉じない $ は数式にしない"
    assert out["unbalancedEscaped"], "地の文は必ずエスケープ"
    assert out["unbalancedNoMath"]
    assert out["noKatexChip"], "window.katex 不在時は <code> チップへ縮退"
    assert out["emptyStays"]
    assert out["stripKeepsTex"], "ラベルでは区切りだけ外して TeX ソースを残す"


class TestStudioDeliberationTargetResolution:
    """原稿スタジオのグラフ詳細も同じ要素解決規則を使う（同根の 422 の再発防止）。"""

    def test_studio_resolves_target_before_open_element(self):
        assert "function lsGraphDeliberationTargetId(" in STUDIO_SRC
        assert 'openElement("theory_component", deliberationTarget' in STUDIO_SRC
        # 生のグラフノード ID を要素 ID として渡す旧形が存在しない。
        assert 'openElement("theory_component", nodeId' not in STUDIO_SRC

    def test_studio_resolver_order(self):
        start = STUDIO_SRC.index("function lsGraphDeliberationTargetId(")
        block = STUDIO_SRC[start: STUDIO_SRC.index("\n  }\n", start) + 4]
        assert "representative_component_id" in block
        assert "linked_component_ids" in block


class TestPaperLayer:
    """グラフの論文層（graph_paper_layer_design.md §3/§4.1/§7）。

    フレーム（理論操作グラフ）を触らず、論文側の骨格と各ノードの「論文での対応」を
    読み時射影で足す層。UI 側の不変条項は PL2（LLM を呼ばない = 生成しない）・
    PL3（リンクの無いものに位置を推定しない）・PL4（数値・件数を出さない）・
    PL7（内部 ID を描かない）・PL8（欠落は事実文で明示）。
    """

    MANUAL_SRC = (ROOT / "docs" / "manual" / "teacher" / "26-admin-graph-review.md").read_text(
        encoding="utf-8"
    )

    def test_anchors_present(self):
        for anchor in ("graph-review.paper-view", "graph-review.paper-facing"):
            assert 'data-ui-anchor="' + anchor + '"' in JS_SRC, anchor

    def test_paper_layer_fetch_path_and_stale_guard(self):
        start = JS_SRC.index("function loadPaperLayer(")
        block = JS_SRC[start: JS_SRC.index("\n  }\n", start) + 4]
        assert '"/admin/documents/" + encodeURIComponent(documentId) + "/paper-layer"' in block
        # グラフ取得と同型の stale-response ガード（別教材へ切替済みの応答は破棄）。
        assert "state.documentId !== documentId" in block
        # 取得失敗はグラフ・レビュー操作を止めず、事実文だけを残す（PL8）。
        assert "state.paperLayerError" in block

    def test_view_toggle_attribute_and_state(self):
        assert 'data-graph-review-view="graph"' in JS_SRC
        assert 'data-graph-review-view="paper"' in JS_SRC
        assert "function setView(" in JS_SRC
        # 切替では network インスタンスを捨てず、包みの hidden を切り替える。
        assert "graph-review-network-wrap" in JS_SRC

    def test_outline_and_facing_class_names(self):
        assert "graph-review-paper-section" in JS_SRC
        assert "graph-review-paper-facing" in JS_SRC
        assert "graph-review-paper-chip" in JS_SRC

    def test_fact_sentences_present(self):
        # PL3: 位置を推定せず、特定できない事実をそのまま出す。
        assert "論文上の位置を特定できませんでした（式・根拠・claim へのリンクがありません）" in JS_SRC
        # PL8: 掛かっていない章・取得失敗の事実文。
        assert "このフレームには掛かっていません" in JS_SRC
        assert "論文層を取得できませんでした。" in JS_SRC

    def test_coverage_block_has_no_counts_or_warning(self):
        start = JS_SRC.index("function paperCoverageHtml(")
        block = JS_SRC[start: JS_SRC.index("\n  function ", start)]
        assert "フレームに掛かっていない要素" in block
        # 件数バッジ・警告色を作らない（PL4 / 設計書 §3.2）。
        assert ".length +" not in block
        assert "警告" not in block
        assert "is-error" not in block

    def test_node_chips_never_print_internal_ids(self):
        start = JS_SRC.index("function paperNodeLabel(")
        block = JS_SRC[start: JS_SRC.index("\n  function ", start)]
        # detailHeading が nodeId へ縮退したら DTO の label、それも無ければ表示名なし。
        assert "detailHeading" in block
        assert "heading !== nodeId" in block
        assert "（表示名なし）" in block

    def test_no_local_math_pipeline(self):
        # 数式は共通の richText（graphView.inlineMathHtml）経由のみ（GR8）。
        assert "katex" not in JS_SRC.lower()
        assert 'richText("$" + latex + "$")' in JS_SRC

    def test_paper_layer_block_is_es5(self):
        block = JS_SRC[JS_SRC.index("function renderPaperOutline("):]
        block = block[: block.index("\n  function markSelectedPaperChips(")]
        assert "=>" not in block
        assert not re.search(r"\bconst\s", block)
        assert not re.search(r"\blet\s", block)

    def test_css_defined(self):
        assert ".graph-review-paper-" in CSS_SRC
        assert ".graph-review-paper[hidden]" in CSS_SRC

    def test_manual_sections_exist(self):
        assert "{#paper-view}" in self.MANUAL_SRC
        assert "{#paper-facing}" in self.MANUAL_SRC
