"""W層（要素検討ワークスペース）Phase 0/1/2 フロント統合の静的ガードレール。

正本: docs/features/element_deliberation_workspace_design.md（§8 API / §9 フロント）。
frontend/public/js/deliberation.js は既存の atlas / personal-map 静的テストと同様に
ソースの静的検証で受け入れ条件を固定する（test_personal_map_ui_guardrails.py と同型）。

Phase 0/1: overview の統合表示のみ（読み取り専用）。
Phase 2（本ファイルの改訂対象）: 右ペインに面③対話的検討（sessions/messages）+
候補注釈カード（annotations の一覧・commit・dismiss）を追加。書き込み系の fetch が
初めて登場するため、従来の「POST/PUT/PATCH/DELETE の fetch が一切無いこと」という
検査を「POST は /admin/deliberation/ 配下の sessions・messages・commit・dismiss に
限り許可し、PUT/PATCH/DELETE は引き続き使わない」に改訂する。

受け入れ条件との対応:
1. deliberation.js: ポーリング禁止 / 禁止語彙なし（踏破・達成率・ランキング）/
   fetch 先が "/admin/deliberation/" のみ / PUT・PATCH・DELETE の fetch が無い /
   POST は sessions・messages・commit・dismiss のみに使う /
   window.Deliberation のエクスポートがある
2. admin.html: deliberation.js の script タグが admin.js より前にある
3. admin.js: window.Deliberation への参照はすべてガード形（素の Deliberation. 直呼びなし）
4. 面③固有: セッションは最初の送信時にだけ作成する（モーダルを開いただけでは
   POST /sessions を呼ばない）/ kind ラベル辞書が6種類（meaning/decomposition/
   positioning_note/interpretation/identity/standardization）そろっている /
   429（対話上限）・degraded（AI応答生成不可）を事実文として処理する /
   対話・候補注釈カードの動的テキスト描画に escHtml を使う（XSS対策）

vision_ux_gap_survey_2026-07.md G2-W への対応（本ファイル追記分）: Phase W-β の
identity-links（`GET/POST/PATCH/DELETE .../identity-links` のうち実際に使うのは
GET・POST のみ）と Phase S の standardization/assess は API 実装済みだったが UI
未接続だった。追加した `_loadIdentityLinks` / `_bindStandardizationAssessButton`
の受け入れ条件:
5. 同一性リンク一覧が candidate/confirmed/rejected を区別するラベルを持つ
   （IDENTITY_LINK_STATUS_LABELS）/ confirm・reject は POST のみ（PUT/PATCH/DELETE
   を増やさない）/ instance 要素は `/elements/{type}/{id}/identity-links`、
   shared_part は `/shared-parts/{id}/identity-links` を使い分ける
6. 標準化度は shared_part 要素にのみ「標準化度を評価」ボタンを表示し、
   `POST /shared-parts/{id}/standardization/assess` を呼ぶ。結果は既存の
   候補注釈カード（commit/dismiss）に現れ、自動確定はしない
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

DELIBERATION_JS = ROOT / "frontend" / "public" / "js" / "deliberation.js"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
LECTURE_STUDIO_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_deliberation_calls_guarded(src: str, label: str) -> None:
    """window.Deliberation.<method>(...) の呼び出しがすべてガードされていることを検証する。

    許容形:
      - 同一行に "if (window.Deliberation)" または "window.Deliberation &&" を含む
      - `if (window.Deliberation) { ... }` ブロックの内側にある
    加えて、window. を伴わない素の "Deliberation." 直呼びが無いことも検証する
    （test_personal_map_ui_guardrails.py の _assert_personal_map_calls_guarded と同型）。
    """
    naked = re.search(r"(?<!window\.)\bDeliberation\.\w+\s*\(", src)
    assert naked is None, (
        f"{label}: window. を伴わない Deliberation の直呼びがあります: {naked.group(0)!r}"
    )

    calls = list(re.finditer(r"window\.Deliberation\.\w+\s*\(", src))
    assert calls, f"{label}: window.Deliberation の API 呼び出しが見つかりません"

    guarded_spans = [
        (m.start(1), m.end(1))
        for m in re.finditer(r"if\s*\(window\.Deliberation\)\s*\{(.*?)\}", src, re.S)
    ]

    lines = src.splitlines(keepends=True)
    line_starts = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line)

    def _line_of(offset: int) -> str:
        idx = 0
        for i, start in enumerate(line_starts):
            if start <= offset:
                idx = i
            else:
                break
        return lines[idx]

    for m in calls:
        call_line = _line_of(m.start())
        same_line_guard = (
            "if (window.Deliberation)" in call_line or "window.Deliberation &&" in call_line
        )
        in_block = any(start <= m.start() < end for start, end in guarded_spans)
        assert same_line_guard or in_block, (
            f"{label}: window.Deliberation 呼び出しがガードされていません: {call_line.strip()!r}"
        )


class TestDeliberationModule:
    """deliberation.js 単体の受け入れ条件。"""

    def test_file_exists(self):
        assert DELIBERATION_JS.exists(), "frontend/public/js/deliberation.js が存在しません。"

    def test_no_polling(self):
        """§9: 地図・アシスタント系と同様ポーリング禁止。開くたび fetch する。
        対話送信・注釈コミット/却下も setInterval のような定期実行は使わない。"""
        src = _read(DELIBERATION_JS)
        assert "setInterval" not in src

    def test_no_forbidden_vocabulary(self):
        """W8: 数値を見せない。踏破率・達成率・ランキング等の煽り語彙を出さない。"""
        src = _read(DELIBERATION_JS)
        for word in ("踏破", "達成率", "ランキング"):
            assert word not in src

    def test_exports_window_deliberation(self):
        src = _read(DELIBERATION_JS)
        assert "window.Deliberation" in src
        assert "init:" in src
        assert "openElement:" in src

    def test_fetch_targets_use_exact_allowlist(self):
        """Issue #496: deliberation API に加え、図画像・分類レビュー・再解析だけを許可する。"""
        src = _read(DELIBERATION_JS)
        assert "/admin/deliberation/" in src
        # document API は image_url fallback・presentation-mode・reanalyze の3箇所だけ。
        assert src.count('"/admin/documents/"') == 3
        assert '"/image"' in src
        assert '"/presentation-mode"' in src
        assert '"/reanalyze"' in src
        idx = 0
        while True:
            idx = src.find("/admin/", idx)
            if idx == -1:
                break
            allowed = (
                src.startswith("/admin/deliberation", idx)
                or src.startswith("/admin/documents/", idx)
                # _figureImagePath の外部URL拒否ガード文字列そのもの。
                or (
                    src.startswith("/admin/", idx)
                    and src[idx + len("/admin/")] in ('"', "'")
                )
            )
            assert allowed, (
                "deliberation.js が許可外の /admin/ パスを参照しています: "
                + src[idx : idx + 80]
            )
            idx += 1

    def test_no_raw_fetch_calls(self):
        """認証・エラー処理を持つ apiFetch 経由のみを使い、生の fetch(...) を呼ばない。"""
        src = _read(DELIBERATION_JS)
        assert re.search(r"(?<!api)fetch\(", src) is None

    def test_equation_requires_document_id(self):
        """設計書 §2: equation は document_id で一意化するため必須。"""
        src = _read(DELIBERATION_JS)
        assert 'elementType === "equation" && !opts.documentId' in src

    def test_cross_corpus_lens_is_registered(self):
        """Phase 1（§4.2）: cross_corpus レンズが LENS_LABELS / LENS_ORDER の両方にあること。
        既存レンダラ（_lensSectionHtml/_kvTableHtml）は item.label/item.value しか読まない
        ため、cross_corpus の document_id は自然と非表示になる（別途の描画コードは不要）。
        """
        src = _read(DELIBERATION_JS)
        assert "cross_corpus:" in src
        assert '"cross_corpus"' in src


class TestDeliberationDialoguePhase2:
    """面③ 対話的検討（Phase 2）固有の受け入れ条件。"""

    def test_write_methods_scoped_to_deliberation_dialogue(self):
        """POST は従来経路と図再解析、PATCH は図の presentation-mode 1箇所に限定する。"""
        src = _read(DELIBERATION_JS)
        for method in ("PUT", "DELETE"):
            assert f'"{method}"' not in src
            assert f"'{method}'" not in src
        assert src.count('method: "PATCH"') == 1
        patch_pos = src.index('method: "PATCH"')
        assert "/presentation-mode" in src[max(0, patch_pos - 500) : patch_pos]
        assert '"POST"' in src or "'POST'" in src
        # POST を使う既知のエンドポイント（設計書 §8）がすべて揃っていること。
        # test_fetch_target_is_deliberation_only により、これらはすべて
        # /admin/deliberation/ 配下に限定されていることも別途保証される。
        # commit/dismiss は _decideAnnotation(id, action, ...) が共通実装のため
        # パス末尾は "/" + action で動的に組み立てる（"commit"/"dismiss" という
        # action 文字列自体は data-action 属性・呼び出し引数として存在する）。
        assert "/sessions" in src
        assert "/messages" in src
        assert '"/admin/deliberation/annotations/"' in src
        assert '"/" + action' in src
        assert '"commit"' in src
        assert '"dismiss"' in src

    def test_session_created_lazily_on_first_send(self):
        """W6/§9: モーダルを開いただけではセッションを作らない（無駄な行・コストを
        作らない）。POST /sessions は _ensureChatSession に隔離され、openElement の
        本体からは直接呼ばれない（ユーザーの最初の送信操作を経て初めて呼ばれる）。"""
        src = _read(DELIBERATION_JS)
        assert "function _ensureChatSession" in src
        assert '"/admin/deliberation/sessions"' in src

        start = src.index("function openElement")
        end = src.index("window.Deliberation = {")
        open_element_src = src[start:end]
        assert '"/admin/deliberation/sessions"' not in open_element_src, (
            "openElement 内で直接セッションを作成しています（開くだけでセッションが"
            "作られてはならない）"
        )

    def test_annotations_restored_on_reopen(self):
        """W4: モーダル再オープン時は候補/確定/却下すべての注釈を GET で復元表示する。"""
        src = _read(DELIBERATION_JS)
        assert "function _loadAnnotations" in src
        assert "/annotations" in src

    def test_annotation_kind_labels_present(self):
        """設計書 §5/§6: コミットルーティング表にある6種類の kind すべてに
        日本語ラベルがあること（数値化・件数化はしない・W8）。"""
        src = _read(DELIBERATION_JS)
        assert "ANNOTATION_KIND_LABELS" in src
        for kind in (
            "meaning",
            "decomposition",
            "positioning_note",
            "interpretation",
            "identity",
            "standardization",
        ):
            assert f"{kind}:" in src, f"kind={kind!r} の日本語ラベルが見つかりません"

    def test_annotation_commit_dismiss_wired(self):
        """候補注釈カードに [確定]/[却下] ボタンが配線されていること。"""
        src = _read(DELIBERATION_JS)
        assert 'data-action="commit"' in src
        assert 'data-action="dismiss"' in src

    def test_degraded_and_rate_limit_are_factual_notes(self):
        """W3: 断定・煽りを足さない。429（対話上限）は detail をそのまま表示し、
        degraded（AI応答が生成できなかった）は事実文の注記として添える。"""
        src = _read(DELIBERATION_JS)
        assert "degraded" in src
        assert "429" in src
        assert "err.status === 429" in src

    def test_no_numeric_confidence_rendered(self):
        """W8: confidence の生値は表示せず、API が返す confidence_label のみ描画する。"""
        src = _read(DELIBERATION_JS)
        assert "confidence_label" in src
        assert re.search(r"\bann\.confidence\b(?!_label)", src) is None

    def test_chat_rendering_uses_esc_html(self):
        """XSS対策: 対話メッセージ・候補注釈カードの動的テキストは escHtml 経由で
        描画する（innerHTML への生埋め込みをしない）。"""
        src = _read(DELIBERATION_JS)

        assert "function _appendChatMessage" in src
        msg_start = src.index("function _appendChatMessage")
        msg_end = src.index("function _appendChatNote")
        assert "escHtml(" in src[msg_start:msg_end]

        assert "function _fillAnnotationCard" in src
        card_start = src.index("function _fillAnnotationCard")
        card_end = src.index("function _buildAnnotationCard")
        assert src[card_start:card_end].count("escHtml(") >= 3


class TestAdminHtmlIntegration:
    def test_script_tag_present_before_admin_js(self):
        html = _read(ADMIN_HTML)
        assert re.search(r'<script src="/js/deliberation\.js(\?[^"]*)?"></script>', html), (
            "admin.html に deliberation.js の script タグがありません"
        )
        assert html.index("/js/deliberation.js") < html.index("/js/admin.js")


class TestAdminJsIntegration:
    """admin.js 側の統合: window.Deliberation 参照はすべてガード付き。"""

    def test_admin_js_references_deliberation(self):
        src = _read(ADMIN_JS)
        assert "window.Deliberation" in src

    def test_deliberation_calls_are_guarded(self):
        src = _read(ADMIN_JS)
        _assert_deliberation_calls_guarded(src, "admin.js")

    def test_init_is_called_in_init_app(self):
        src = _read(ADMIN_JS)
        assert "window.Deliberation.init(" in src

    def test_figure_deliberate_button_wired(self):
        """図・画像モーダルからの「深く検討」導線（figure 要素型）。"""
        src = _read(ADMIN_JS)
        assert "figure-deliberate-btn" in src
        assert 'window.Deliberation.openElement("figure"' in src


# ---------------------------------------------------------------------------
# レビュー指摘 [P2] (2026-07-15): §1/§9 は figure/theory_component/theory_claim/
# equation の4要素型すべてへの「深く検討」入口を要求しているが、実装当初は
# figure（admin.js の図モーダル）と equation（admin.js の revisions 画面）のみで
# theory_component / theory_claim には導線が無かった。DB UUID
# （theory_components.id / theory_claims.id）が手に入る既存画面は原稿スタジオ
# （frontend/public/js/admin-lecture-studio.js、CLAUDE.md 開発ルール5により
# 原稿スタジオの UI 変更はこちらに書く）のチャンク/セクション論理要素カード・
# 「選択中コンポーネント」ビュー・チャンクの主張一覧にあるため、そこへ導線を追加した。
# ---------------------------------------------------------------------------


class TestLectureStudioDeliberationEntryPoints:
    """admin-lecture-studio.js（原稿スタジオ）の theory_component / theory_claim
    「深く検討」導線。deliberation.js 側の静的ガードレール（TestDeliberationModule /
    TestAdminJsIntegration）と同型の検査を、原稿スタジオ側にも適用する。"""

    def test_file_exists(self):
        assert LECTURE_STUDIO_JS.exists(), "frontend/public/js/admin-lecture-studio.js が存在しません。"

    def test_references_window_deliberation(self):
        src = _read(LECTURE_STUDIO_JS)
        assert "window.Deliberation" in src

    def test_deliberation_calls_are_guarded(self):
        """window.Deliberation.openElement(...) の呼び出しはすべて
        `if (window.Deliberation)` でガードされていること（admin.js と同じ規約）。"""
        src = _read(LECTURE_STUDIO_JS)
        _assert_deliberation_calls_guarded(src, "admin-lecture-studio.js")

    def test_theory_component_entry_points_wired(self):
        """チャンク/セクションの論理要素カード・「選択中コンポーネント」ビューの
        3箇所すべてに theory_component の導線があること（section-scope /
        chunk-scope / lsBindTheoryCardActions 経由の単体ビュー）。"""
        src = _read(LECTURE_STUDIO_JS)
        assert 'data-theory-action="deliberate"' in src
        occurrences = src.count('window.Deliberation.openElement("theory_component"')
        assert occurrences >= 3, (
            "theory_component の openElement 呼び出しが期待より少ない "
            f"（3箇所: section-scope / chunk-scope / 選択中コンポーネント。実際: {occurrences}）"
        )

    def test_theory_component_document_id_uses_source_scope(self):
        """document_id は TheoryComponentOut.source_scope.document_id から読む
        （呼び出し元の表示スコープに依存しない・レビュー指摘の根拠になった
        「entity_id が DB UUID でない」問題を避けるため、component.id 自体は
        常に theory_components.id の実 UUID を使う）。"""
        src = _read(LECTURE_STUDIO_JS)
        assert "function lsTheoryElementDocumentId(component)" in src
        assert "component.source_scope" in src

    def test_theory_claim_entry_point_wired(self):
        """チャンクの主張一覧（lsClaimCardHtml / lsRenderClaimsPanel）に
        theory_claim の導線があること。document_id は ClaimOut.document_id を使う
        （claim 自体に document_id フィールドがあるため、周辺の chunk/scope 状態に
        依存せず取れる）。"""
        src = _read(LECTURE_STUDIO_JS)
        assert "ls-claim-deliberate-btn" in src
        assert 'window.Deliberation.openElement("theory_claim"' in src
        assert 'data-document-id="' in src

    def test_no_forbidden_vocabulary(self):
        """W8: 数値を見せない。踏破率・達成率・ランキング等の煽り語彙を出さない。"""
        src = _read(LECTURE_STUDIO_JS)
        for word in ("踏破", "達成率", "ランキング"):
            assert word not in src


# ---------------------------------------------------------------------------
# vision_ux_gap_survey_2026-07.md G2-W: identity-links / standardization/assess は
# API 実装済みだったがフロント未接続だった。Phase W-β・Phase S の UI 配線を固定する。
# ---------------------------------------------------------------------------


class TestIdentityLinksWiring:
    """Phase W-β: 同一性リンク（element_identity_links）一覧・確定・却下の配線。"""

    def _function_block(self, src: str, name: str) -> str:
        m = re.search(r"function " + name + r"\([\s\S]+?\n  \}\n", src)
        assert m, f"function {name} が見つかりません"
        return m.group(0)

    def test_load_identity_links_function_present(self):
        src = _read(DELIBERATION_JS)
        assert "function _loadIdentityLinks" in src

    def test_document_scoped_endpoint_used_for_instance_elements(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_loadIdentityLinks")
        assert '"/admin/deliberation/elements/"' in block
        assert '"/identity-links"' in block

    def test_domain_scoped_endpoint_used_for_shared_part(self):
        """shared_part は L層の開示方針が異なるため別エンドポイントを使う（§5 W5）。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_loadIdentityLinks")
        assert '"/admin/deliberation/shared-parts/"' in block
        assert 'ref.elementType === "shared_part"' in block

    def test_status_labels_distinguish_candidate_confirmed_rejected(self):
        """G2-W: candidate/confirmed の別を明示する（設計書 KN-3: 確定は人間のみ）。"""
        src = _read(DELIBERATION_JS)
        assert "IDENTITY_LINK_STATUS_LABELS" in src
        for status in ("candidate", "confirmed", "rejected"):
            assert f"{status}:" in src

    def test_confirm_and_reject_actions_wired(self):
        src = _read(DELIBERATION_JS)
        assert 'data-identity-action="confirm"' in src
        assert 'data-identity-action="reject"' in src

    def test_decide_uses_post_only(self):
        """確定・却下は POST のみ（PUT/PATCH/DELETE を新たに増やさない・W4 削除API不在）。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_decideIdentityLink")
        assert '"/admin/deliberation/identity-links/"' in block
        assert 'method: "POST"' in block

    def test_pending_only_shows_confirm_reject_buttons(self):
        """確定・却下済みのリンクには操作ボタンを出さない（教員の再確定を促さない）。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_identityLinkRowHtml")
        assert 'link.status === "candidate"' in block

    def test_instance_side_local_expression_not_rewritten(self):
        """KN-2: インスタンス側の表記は書き換えないという説明文をUIに残す。"""
        src = _read(DELIBERATION_JS)
        assert "既存の表記は書き換えません" in src

    def test_hidden_count_shown_when_present(self):
        """shared_part 経由の一覧は閲覧不可 document 由来のリンクを隠しうる。
        件数を黙って欠落させず正直に表示する（P4 / 出所の正直さ）。"""
        src = _read(DELIBERATION_JS)
        assert "hidden_count" in src

    def test_no_raw_confidence_rendered_for_identity_links(self):
        """W8: identity link のカードも confidence_label のみ描画する。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_identityLinkRowHtml")
        assert "confidence_label" in block
        assert re.search(r"\blink\.confidence\b(?!_label)", block) is None

    def test_identity_link_error_class_matches_between_render_and_catch(self):
        """レビュー指摘6: 描画側のエラー要素クラスと catch 側の querySelector が一致すること。
        不一致だと確定・却下失敗時に errEl が常に null になり失敗理由が表示されない。"""
        src = _read(DELIBERATION_JS)
        row_block = self._function_block(src, "_identityLinkRowHtml")
        decide_block = self._function_block(src, "_decideIdentityLink")
        assert "deliberation-identity-link-error" in row_block
        assert '".deliberation-identity-link-error"' in decide_block


class TestStandardizationAssessWiring:
    """Phase S: 標準化度の評価ボタン（三角測量 worker の手動起動）。"""

    def test_assess_button_only_for_shared_part(self):
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _standardizationSectionHtml[\s\S]+?\n  \}\n", src)
        assert m
        block = m.group(0)
        assert 'elementType !== "shared_part"' in block
        assert 'return ""' in block

    def test_assess_calls_existing_endpoint_with_post(self):
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _bindStandardizationAssessButton[\s\S]+?\n  \}\n", src)
        assert m
        block = m.group(0)
        assert '"/standardization/assess"' in block
        assert 'method: "POST"' in block

    def test_assess_reuses_existing_annotation_loading(self):
        """自動確定しない: 評価結果は既存の候補注釈一覧（commit/dismiss）に現れるだけ。"""
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _bindStandardizationAssessButton[\s\S]+?\n  \}\n", src)
        assert m
        assert "_loadAnnotations(" in m.group(0)

    def test_assess_note_is_factual_server_text(self):
        """事実文（サーバの note）をそのまま表示し、断定・煽りを足さない。"""
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _bindStandardizationAssessButton[\s\S]+?\n  \}\n", src)
        assert m
        assert "data.note" in m.group(0)



class TestFigurePresentationWorkspace:
    """Issue #496: 原図を証拠として分類別に検討するUIの静的契約。"""

    def test_authenticated_blob_image_lifecycle(self):
        src = _read(DELIBERATION_JS)
        assert "apiFetchRaw(path, { _noJson: true })" in src
        assert "res.blob()" in src
        assert "URL.createObjectURL" in src
        assert "URL.revokeObjectURL" in src
        assert 'path.indexOf("/api/") === 0' in src
        assert 'path.indexOf("/admin/") !== 0' in src

    def test_admin_injects_raw_fetch(self):
        src = _read(ADMIN_JS)
        assert "window.Deliberation.init({ apiFetch: apiFetch, apiFetchRaw: apiFetchRaw" in src

    def test_all_presentation_modes_have_dedicated_fail_soft_rendering(self):
        src = _read(DELIBERATION_JS)
        for mode in (
            "functional_diagram",
            "data_plot",
            "descriptive_image",
            "mixed",
            "unknown",
        ):
            assert f"{mode}:" in src
        for renderer in (
            "_functionalDiagramHtml",
            "_dataPlotHtml",
            "_descriptiveImageHtml",
        ):
            assert f"function {renderer}" in src
        assert "原図と周辺本文を確認しながら質問できます" in src
        assert "図の種類を判定できませんでした" in src
        assert "panel.analysis || panel.analysis_profile" in src
        assert "パネルの解析を見る" in src

    def test_flat_and_legacy_analysis_contracts_are_supported(self):
        src = _read(DELIBERATION_JS)
        assert "fields.analysis_profile" in src
        for key in ("functional_analysis", "data_plot_analysis", "descriptive_analysis"):
            assert key in src
        for key in ("from_function_id", "from_output_id", "to_function_id", "to_input_id"):
            assert key in src
        assert "analysis.y_axes" in src
        assert "function _functionLookup" in src
        assert "_connectionText(connection, functionLookup)" in src

    def test_plot_observation_meaning_and_highlight_contract(self):
        src = _read(DELIBERATION_JS)
        assert "analysis.interpretations" in src
        assert "candidate.observation_id" in src
        assert "analysis.highlights" in src
        assert "意味候補" in src
        assert "注目箇所" in src

    def test_raw_metadata_is_collapsed(self):
        src = _read(DELIBERATION_JS)
        assert '<details class="deliberation-figure-raw">' in src
        assert "抽出データ・根拠を見る" in src

    def test_selected_visual_context_is_sent_separately(self):
        src = _read(DELIBERATION_JS)
        assert "chatState.selectedContext" in src
        assert "messageBody.selected_context" in src
        for kind in ("part", "observation", "subject"):
            assert f'_contextAttrs("{kind}"' in src

    def test_teacher_mode_override_is_exactly_scoped(self):
        src = _read(DELIBERATION_JS)
        assert "/presentation-mode" in src
        assert 'method: "PATCH"' in src
        assert "presentation_mode: select.value || null" in src
        assert "function _reloadOverview" in src

    def test_teacher_can_reanalyze_and_only_supported_candidates_show_confirm(self):
        src = _read(DELIBERATION_JS)
        assert "function _bindFigureReanalysis" in src
        assert "function _structuredFigureCandidateHtml" in src
        assert "再解析で検出した構成を確認" in src
        assert '"/reanalyze"' in src
        assert 'method: "POST"' in src
        assert "ann.commit_supported !== false" in src
        assert "ann.commit_note" in src
        assert "body.text" in src

    def test_figure_layout_is_responsive_and_zoomable(self):
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        assert ".deliberation-figure-lightbox.is-open" in css
        assert ".deliberation-figure-image-card" in css
        assert "@media (max-width: 820px)" in css

    def test_bbox_overlays_and_related_connections_are_fail_soft(self):
        src = _read(DELIBERATION_JS)
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        assert "function _relativeBbox" in src
        assert "function _renderFigureOverlays" in src
        assert 'data-figure-bbox="' in src
        assert "if (!figureBbox) return null" in src
        assert "data-deliberation-connection" in src
        assert ".deliberation-figure-overlay.is-selected" in css
        assert ".deliberation-connection-list li.is-related" in css
