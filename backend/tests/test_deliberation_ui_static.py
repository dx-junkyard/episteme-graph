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

説明レビューキュー（本ファイル追記分, 2026-07-22）: document 単位で
element_explanations の candidate を一覧し、要素ごとにグループ化して一括承認/却下
できる `_openExplanationReviewModal` を追加した。1件ずつ「深く検討」を開いて承認する
既存 UX（_explanationCardHtml 等）は変更せず併存させる。新規 `GET/POST
/admin/documents/{document_id}/element-explanations(...)` は `_explanationReviewBasePath`
という1つのヘルパーへ集約したため、ソース中の `"/admin/documents/"` リテラル出現数は
既存3箇所（image_url fallback・presentation-mode・reanalyze）+ 本ヘルパー1箇所の
4箇所になる（`test_fetch_targets_use_exact_allowlist` 等の該当アサーションを
3→4 に更新）。固有の受け入れ条件は `test_element_explanation_review_ui_static.py`
を参照。
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
        # document API は image_url fallback・presentation-mode・reanalyze の3箇所 +
        # 説明レビューキューの共有ヘルパー _explanationReviewBasePath の1箇所で計4箇所。
        # （bulk-review/一覧取得の両方がこの1ヘルパーを経由するため、呼び出し箇所が
        # 増えてもソース中のリテラル出現数はここでは増えない）
        assert src.count('"/admin/documents/"') == 4
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
                # 要素説明（element_explanations）の承認/却下 API（migration 056）。
                or src.startswith("/admin/element-explanations", idx)
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
        """設計書 §2 / §16: 独立テーブルを持たない要素型（equation / evidence /
        derivation）は document_id で一意化するため必須。判定は
        ``DOCUMENT_ID_REQUIRED_ELEMENT_TYPES``（backend
        ``core/deliberation/schema.py`` の同名語彙に対応）へ一本化する。"""
        src = _read(DELIBERATION_JS)
        assert "var DOCUMENT_ID_REQUIRED_ELEMENT_TYPES = {" in src
        block = src[src.index("var DOCUMENT_ID_REQUIRED_ELEMENT_TYPES = {"):]
        block = block[: block.index("}")]
        for element_type in ("equation", "evidence", "derivation"):
            assert element_type + ": true" in block, element_type
        assert "_needsDocumentId(elementType) && !opts.documentId" in src

    def test_identity_link_section_hidden_for_non_linkable_types(self):
        """§16: evidence / derivation は共通部品化の単位ではないため、同一性リンクの
        セクション自体を出さない（backend も 422 で拒否する）。"""
        src = _read(DELIBERATION_JS)
        assert "var IDENTITY_LINKABLE_ELEMENT_TYPES = {" in src
        block = src[src.index("var IDENTITY_LINKABLE_ELEMENT_TYPES = {"):]
        block = block[: block.index("}")]
        for element_type in ("figure", "theory_component", "theory_claim", "equation"):
            assert element_type + ": true" in block, element_type
        assert "evidence" not in block
        assert "derivation" not in block
        assert "!_isIdentityLinkable(elementType)" in src

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

    # PATCH を使ってよい既知の書き込み先（増やすときはここに明示して意図を残す）。
    # ① 図の提示モード訂正（#496） ② 開幕素材の本文インライン編集
    #    （discuss_opening_authoring_design.md §6.1。旧行 superseded → 教員名義の新行）
    _ALLOWED_PATCH_TARGETS = ("/presentation-mode", "/admin/element-explanations/")

    def test_write_methods_scoped_to_deliberation_dialogue(self):
        """POST は従来経路と図再解析、PATCH は上記の既知2箇所に限定する。"""
        src = _read(DELIBERATION_JS)
        for method in ("PUT", "DELETE"):
            assert f'"{method}"' not in src
            assert f"'{method}'" not in src
        patch_positions = [m.start() for m in re.finditer(r'method: "PATCH"', src)]
        assert len(patch_positions) == len(self._ALLOWED_PATCH_TARGETS)
        for pos in patch_positions:
            preceding = src[max(0, pos - 500) : pos]
            assert any(target in preceding for target in self._ALLOWED_PATCH_TARGETS), (
                "未知の PATCH 書き込み先が追加されている"
            )
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


class TestManualIdentityLinkCreation:
    """N2: 手動リンク作成 UI（「共通部品と結びつける」→ 候補検索 → candidate 作成）。

    受け入れ条件:
    - 作成導線はインスタンス要素にのみ表示する（shared_part 自身には出さない —
      identity link の source は常に document-scoped インスタンス）。
    - 候補検索は GET .../shared-part-candidates（domain 解決はサーバ側）。
    - 作成は既存の POST /admin/deliberation/identity-links（常に candidate。
      確定・却下は既存の確定/却下ボタンがそのまま担う・KN-3）。
    - 検索0件は事実文（煽らない・数値を出さない）。
    - 作成成功後は同一性リンク一覧を再読込する。
    """

    def _function_block(self, src: str, name: str) -> str:
        m = re.search(r"function " + name + r"\([\s\S]+?\n  \}\n", src)
        assert m, f"function {name} が見つかりません"
        return m.group(0)

    def test_create_button_only_for_instance_elements(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_identityLinksSectionHtml")
        assert "deliberation-identity-link-open-search" in block
        assert 'elementType !== "shared_part"' in block
        # bind 側も shared_part では何もしない（二重の防御）。
        bind_block = self._function_block(src, "_bindIdentityLinkSearch")
        assert 'ref.elementType === "shared_part"' in bind_block

    def test_candidate_search_uses_server_side_domain_resolution_endpoint(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_searchSharedPartCandidates")
        assert '"/shared-part-candidates"' in block
        assert '"/admin/deliberation/elements/"' in block
        # equation / evidence / derivation は document_id で一意化する（既存の一覧・
        # 注釈と同じ規約。判定は _needsDocumentId に一本化した — §16）。
        assert "_needsDocumentId(ref.elementType) && ref.documentId" in block

    def test_create_posts_existing_identity_links_endpoint(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_createIdentityLink")
        assert '"/admin/deliberation/identity-links"' in block
        assert 'method: "POST"' in block

    def test_creation_is_candidate_only_messaging(self):
        """KN-3: 作成されるのは候補であり、確定は別操作であることを UI 文言で明示する。"""
        src = _read(DELIBERATION_JS)
        assert "候補を作成" in src
        block = self._function_block(src, "_createIdentityLink")
        assert "確定・却下できます" in block

    def test_zero_results_show_factual_message(self):
        src = _read(DELIBERATION_JS)
        assert "同分野の共通部品が見つかりません" in src

    def test_identity_links_reloaded_after_creation(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_createIdentityLink")
        assert "_loadIdentityLinks(" in block

    def test_search_and_create_render_through_esc_html(self):
        """XSS対策: 候補行の name/aliases/summary・エラー文言は escHtml 経由で描画する。"""
        src = _read(DELIBERATION_JS)
        row_block = self._function_block(src, "_sharedPartCandidateRowHtml")
        assert "escHtml(entry.name" in row_block
        assert "escHtml(entry.summary)" in row_block
        create_block = self._function_block(src, "_createIdentityLink")
        assert "escHtml(" in create_block


class TestIdentityLinkDeUuid:
    """脱UUID（`hierarchical_context_explanation_design.md` §6）: 同一性リンク行が
    生 UUID の代わりにエントリ name/summary を表示すること（UUID は tooltip に残す）。
    """

    def _function_block(self, src: str, name: str) -> str:
        m = re.search(r"function " + name + r"\([\s\S]+?\n  \}\n", src)
        assert m, f"function {name} が見つかりません"
        return m.group(0)

    def test_row_prefers_entry_name_over_raw_uuid(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_identityLinkRowHtml")
        assert "link.shared_part_name || link.shared_part_id" in block

    def test_raw_uuid_kept_as_tooltip_not_dropped(self):
        """P4: UUID 自体は情報として失わない（title 属性に残す）。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_identityLinkRowHtml")
        assert "title=\"" in block and "escHtml(link.shared_part_id)" in block

    def test_summary_rendered_through_esc_html(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_identityLinkRowHtml")
        assert "escHtml(link.shared_part_summary)" in block


# ---------------------------------------------------------------------------
# 説明カード（element_explanations, migration 056）。
# 正本: docs/features/hierarchical_context_explanation_design.md §5.2/§5.3。
# ---------------------------------------------------------------------------


class TestExplanationCardsWiring:
    """overview.explanations（candidate + approved）を面③注釈カードと同じ流儀で表示し、
    candidate には承認/却下ボタンを出す（approved はバッジ表示のみ）。"""

    def _function_block(self, src: str, name: str) -> str:
        m = re.search(r"function " + name + r"\([\s\S]+?\n  \}\n", src)
        assert m, f"function {name} が見つかりません"
        return m.group(0)

    def test_rendering_and_binding_functions_present(self):
        src = _read(DELIBERATION_JS)
        for name in (
            "_explanationCardHtml",
            "_explanationsSectionHtml",
            "_bindExplanationActions",
            "_decideExplanation",
        ):
            assert f"function {name}" in src, f"function {name} が見つかりません"

    def test_kind_and_status_label_dictionaries_present(self):
        src = _read(DELIBERATION_JS)
        assert "EXPLANATION_KIND_LABELS" in src
        for kind in ("generic", "contextual"):
            assert f"{kind}:" in src
        assert "EXPLANATION_STATUS_LABELS" in src
        for status in ("candidate", "approved"):
            assert f"{status}:" in src

    def test_unavailable_explanations_render_nothing(self):
        """overview.explanations.available が false のときはセクション自体を出さない。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_explanationsSectionHtml")
        assert "!explanations.available" in block or "!explanations || !explanations.available" in block

    def test_approve_and_dismiss_actions_wired_to_element_explanations_endpoint(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_decideExplanation")
        assert '"/admin/element-explanations/"' in block
        assert 'method: "POST"' in block

    def test_pending_only_shows_approve_dismiss_buttons(self):
        """承認済みには操作ボタンを出さない（教員の再承認を促さない）。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_explanationCardHtml")
        assert 'exp.status === "candidate"' in block
        assert 'data-explanation-action="approve"' in block
        assert 'data-explanation-action="dismiss"' in block

    def test_no_raw_confidence_rendered(self):
        """W8: confidence の生値ではなく confidence_label のみ描画する。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_explanationCardHtml")
        assert "confidence_label" in block
        assert re.search(r"\bevidence\.confidence\b(?!_label)", block) is None

    def test_render_modal_body_wires_explanations_section_and_binds_actions(self):
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_renderModalBody")
        assert "_explanationsSectionHtml(data.explanations)" in block
        assert "_bindExplanationActions()" in block

    def test_card_rendering_uses_esc_html(self):
        """XSS対策: 説明本文・reason・evidence_quote は escHtml 経由で描画する。"""
        src = _read(DELIBERATION_JS)
        block = self._function_block(src, "_explanationCardHtml")
        assert "escHtml(exp.body" in block


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


# ---------------------------------------------------------------------------
# 教員指示付き図再解析（Guided Figure Re-analysis）。
# 正本: docs/features/guided_figure_reanalysis_design.md §7 フロントエンド / §8 テスト計画。
# ---------------------------------------------------------------------------


def _function_block(src: str, name: str) -> str:
    """モジュールレベル関数（2スペースインデント）本体をソースから切り出す。

    TestIdentityLinksWiring._function_block と同型（本ファイル内の他クラスとの
    重複を避けるためモジュールレベルへ寄せる。引数リストは `_bindFigureReanalysis
    (decomposition)` のように空でない場合もあるため `[^)]*` で受ける）。
    """
    m = re.search(r"function " + name + r"\([^)]*\)\s*\{[\s\S]+?\n  \}\n", src)
    assert m, f"function {name} が見つかりません"
    return m.group(0)


class TestGuidedFigureReanalysis:
    """教員指示付き図再解析（focus_bbox による領域指定 + hint_text による言葉の指示）。"""

    def test_focus_toggle_and_hint_controls_present(self):
        """設計書 §7-1: トグルボタン・クリアボタン・指示欄（2000字まで）。"""
        src = _read(DELIBERATION_JS)
        assert 'id="deliberation-focus-toggle"' in src
        assert 'id="deliberation-focus-clear"' in src
        assert 'id="deliberation-reanalyze-hint"' in src
        assert 'maxlength="2000"' in src

    def test_focus_controls_placed_right_after_mode_review_row(self):
        """設計書 §7-1: `.deliberation-mode-review`（既存の再解析ボタン等を含む行）
        の直下に配置する。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_figureModeHtml")
        assert "_figureFocusControlsHtml()" in block
        mode_review_idx = block.index('deliberation-mode-review')
        focus_controls_idx = block.index("_figureFocusControlsHtml()")
        assert mode_review_idx < focus_controls_idx, (
            "focus controls が .deliberation-mode-review より前に置かれています"
        )

    def test_focus_layer_is_sibling_of_read_only_overlays(self):
        """設計書 §7-2: 既存 #deliberation-figure-overlays（読み取り専用マーカー）
        との干渉を避けるため、独立した描画レイヤーを兄弟として追加する。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_figureWorkspaceHtml")
        assert 'id="deliberation-figure-overlays"' in block
        assert 'id="deliberation-figure-focus-layer"' in block
        assert block.index('id="deliberation-figure-overlays"') < block.index(
            'id="deliberation-figure-focus-layer"'
        )

    def test_focus_mode_gates_pointer_events_via_css_class(self):
        """focus モード ON のときのみドラッグ描画レイヤーが操作可能になる。"""
        src = _read(DELIBERATION_JS)
        assert "function _setFigureFocusLayerActive" in src
        assert '"is-drawable"' in src

    def test_mouse_and_touch_drag_handlers_bound_on_focus_layer(self):
        """設計書 §7-2: mousedown/mousemove/mouseup + touch 系を同ハンドラに束ねる
        （タブレットでのレビューを想定）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bindFigureFocusDrawing")
        for evt in ("mousedown", "mousemove", "mouseup", "touchstart", "touchmove", "touchend"):
            assert f'"{evt}"' in block, f"{evt} のハンドラ登録が見つかりません"

    def test_micro_drag_is_ignored_matching_server_threshold(self):
        """設計書 §4-1/§7-2: 幅または高さ 0.02 未満のドラッグは誤クリックとして
        無視する（サーバ側 422 の閾値と整合）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bindFigureFocusDrawing")
        assert "0.02" in block

    def test_focus_bbox_state_field_present(self):
        """設計書 §7-2: 描画結果は figureImageState.focusBbox（画像内相対座標 0..1）
        に保持する。"""
        src = _read(DELIBERATION_JS)
        assert "focusBbox" in src
        assert "hintText" in src
        assert "focusMode" in src

    def test_guidance_state_persists_across_reload_overview(self):
        """設計書 §7-3: 送信した guidance は成功・失敗にかかわらず消さない。
        _reloadOverview（再解析成功時に自動で呼ばれる）は guidance をリセットしない
        —— _resetFigureImageState は画像 blob のライフサイクルのみをリセットし、
        focusBbox/hintText/focusMode には触れない。"""
        src = _read(DELIBERATION_JS)
        reset_image = _function_block(src, "_resetFigureImageState")
        assert "focusBbox" not in reset_image
        assert "hintText" not in reset_image

        reload_overview = _function_block(src, "_reloadOverview")
        assert "_resetFigureGuidanceState" not in reload_overview, (
            "_reloadOverview が guidance をリセットしています（再解析成功直後の"
            "自動リロードで教員の指示が消えてしまいます）"
        )

    def test_guidance_state_resets_only_when_opening_a_different_element(self):
        """設計書 §7-2: 別の要素でモーダルを開いたとき（_closeModal 経由）のみ
        focus 状態を完全リセットする。"""
        src = _read(DELIBERATION_JS)
        assert "function _resetFigureGuidanceState" in src
        reset_guidance = _function_block(src, "_resetFigureGuidanceState")
        assert "focusBbox" in reset_guidance
        assert "hintText" in reset_guidance
        assert "focusMode" in reset_guidance

        close_modal = _function_block(src, "_closeModal")
        assert "_resetFigureGuidanceState()" in close_modal

    def test_drawn_rect_and_hint_restored_after_rerender(self):
        """モーダル再描画後もテキストエリアの値・矩形描画を復元する。"""
        src = _read(DELIBERATION_JS)
        controls = _function_block(src, "_figureFocusControlsHtml")
        assert "figureImageState.hintText" in controls
        assert "figureImageState.focusBbox" in controls

        drawing = _function_block(src, "_bindFigureFocusDrawing")
        assert "_renderFigureFocusRect()" in drawing

    def test_reanalyze_sends_no_body_when_guidance_is_empty(self):
        """設計書 §4-1: body なし / 両フィールド null = 従来動作（後方互換）。
        hint_text も focus_bbox も無ければ _figureReanalyzeGuidancePayload は null を
        返し、呼び出し側は body キー自体を省略した従来どおりのリクエストを送る。"""
        src = _read(DELIBERATION_JS)
        payload_fn = _function_block(src, "_figureReanalyzeGuidancePayload")
        assert "return hasGuidance ? payload : null" in payload_fn
        assert "payload.hint_text" in payload_fn
        assert "payload.focus_bbox" in payload_fn

        bind_fn = _function_block(src, "_bindFigureReanalysis")
        assert "guidance ?" in bind_fn
        assert '{ method: "POST" }' in bind_fn

    def test_guidance_note_surfaced_as_factual_status_text(self):
        """GF3/GF5: guidance_note が返れば AI の応答を status に表示する
        （指示された要素が見つからなかった場合も教員はここで正直に知れる）。"""
        src = _read(DELIBERATION_JS)
        bind_fn = _function_block(src, "_bindFigureReanalysis")
        assert "data.guidance_note" in bind_fn
        assert "AIの応答" in bind_fn
        assert "note.substring(0, 120)" in bind_fn

    def test_focus_layer_and_rect_have_dedicated_css(self):
        """CSS: 描画レイヤー（focus-layer）と矩形（focus-rect）、トグルの ON 状態、
        指示欄の見た目が定義されていること。"""
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        assert ".deliberation-figure-focus-layer" in css
        assert ".deliberation-figure-focus-layer.is-drawable" in css
        assert ".deliberation-figure-focus-rect" in css
        assert ".deliberation-focus-toggle.is-active" in css
        assert ".deliberation-reanalyze-hint" in css


class TestDeliberationJsEs5RegressionGuard:
    """deliberation.js は ES5 準拠（開発ルール5）。test_apparatus_overlay_ui_static.py
    の admin.js 用リグレッションガード（TestAdminJsEs5RegressionGuard）と同型。
    教員指示付き図再解析の追加でこの既存規約を崩していないことを固定する。"""

    def test_no_arrow_functions_anywhere_in_file(self):
        src = _read(DELIBERATION_JS)
        assert "=>" not in src

    def test_no_const_or_let_declarations_anywhere_in_file(self):
        src = _read(DELIBERATION_JS)
        assert re.search(r"\bconst\s+\w", src) is None
        assert re.search(r"\blet\s+\w", src) is None

    def test_no_template_literals_anywhere_in_file(self):
        src = _read(DELIBERATION_JS)
        assert "`" not in src

    def test_only_var_declarations_used(self):
        src = _read(DELIBERATION_JS)
        assert "var " in src


# ---------------------------------------------------------------------------
# 要素インベントリ（Element Inventory / 検出要素の一覧）。
# 正本: docs/features/element_inventory_design.md §4/§5/§6/§7/§9。
# I-2（フロント）: deliberation.js に openInventory を追加 + admin.js/
# admin-lecture-studio.js に導線を追加する（バックエンドの
# GET /api/admin/deliberation/documents/{document_id}/elements は別issueで実装）。
# ---------------------------------------------------------------------------


class TestElementInventoryDeliberationJs:
    """openInventory 本体（deliberation.js）の受け入れ条件。"""

    def test_openinventory_exported(self):
        src = _read(DELIBERATION_JS)
        assert "window.Deliberation" in src
        assert "openInventory:" in src

    def test_modal_dom_ids_present(self):
        """§9: 独立 DOM id（既存の深く検討モーダル #deliberation-modal とは別ID）。"""
        src = _read(DELIBERATION_JS)
        assert 'overlay.id = "deliberation-inventory-modal"' in src
        for dom_id in (
            'id="deliberation-inventory-toolbar"',
            'id="deliberation-inventory-list"',
            'id="deliberation-inventory-truncated-note"',
            'id="deliberation-inventory-keyword"',
            'id="deliberation-inventory-reload"',
        ):
            assert dom_id in src, f"{dom_id} が見つかりません"

    def test_fetch_endpoint_matches_design_contract(self):
        """§5: GET /api/admin/deliberation/documents/{document_id}/elements。"""
        src = _read(DELIBERATION_JS)
        assert '"/admin/deliberation/documents/"' in src
        assert '"/elements"' in src

    def test_fetch_targets_still_within_allowlist(self):
        """既存の許可リスト検査（test_fetch_targets_use_exact_allowlist）と同じ
        許容ルールに新エンドポイントが収まること（/admin/deliberation 配下）。"""
        src = _read(DELIBERATION_JS)
        idx = src.index('"/admin/deliberation/documents/"')
        assert src.startswith("/admin/deliberation", idx + 1)
        # 既存契約（/admin/documents/ は4箇所固定。説明レビューキュー追加分は
        # test_fetch_targets_use_exact_allowlist 参照）を増やしていないこと。
        assert src.count('"/admin/documents/"') == 4

    def test_keyword_input_does_not_trigger_fetch(self):
        """§6: フィルタはクライアントサイド。キー入力ごとに再フェッチしない。"""
        src = _read(DELIBERATION_JS)
        m = re.search(
            r'keywordInput\.addEventListener\("input",\s*function\s*\(\)\s*\{([\s\S]*?)\}\);',
            src,
        )
        assert m, "キーワード入力のイベントハンドラが見つかりません"
        handler_body = m.group(1)
        assert "apiFetch(" not in handler_body
        assert "fetch(" not in handler_body
        assert "_renderInventoryList()" in handler_body

    def test_reload_button_is_the_only_manual_refetch(self):
        """§9: 「再読込」ボタンだけが再フェッチする（深く検討を閉じても再フェッチしない）。"""
        src = _read(DELIBERATION_JS)
        assert 'document.getElementById("deliberation-inventory-reload")' in src
        assert "reloadBtn.addEventListener(\"click\", _loadInventory)" in src

    def test_deliberate_button_reuses_open_element_without_refetch(self):
        """§9: 「深く検討」は既存 openElement をそのまま呼ぶ。openElement 自体は
        非改変（_bindInventoryDeliberateButtons からの直接呼び出しのみを追加）。"""
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _bindInventoryDeliberateButtons[\s\S]+?\n  \}\n", src)
        assert m
        block = m.group(0)
        assert "openElement(elementType, elementId" in block
        assert "documentId: inventoryState.documentId" in block

    def test_modal_z_index_lower_than_element_modal(self):
        """§9: インベントリの上に既存の深く検討モーダル（z-index:9999）が重なる。"""
        src = _read(DELIBERATION_JS)
        element_modal_z = re.search(
            r'overlay\.id = "deliberation-modal";[\s\S]{0,400}?z-index:(\d+)', src
        )
        inventory_modal_z = re.search(
            r'overlay\.id = "deliberation-inventory-modal";[\s\S]{0,400}?z-index:(\d+)', src
        )
        assert element_modal_z and inventory_modal_z
        assert int(inventory_modal_z.group(1)) < int(element_modal_z.group(1))

    def test_type_filter_chips_match_design(self):
        """§6: すべて / 論理要素 / 主張 / 数式 / 図 の5チップ。

        種別の訳語は element-vocab.js（window.ElementVocab）が正本になったため
        （admin_ux_issues_2026-08-01.md §3.3 Phase 0）、deliberation.js 側は
        語彙外の "すべて" だけを持ち、種別は正本へ委譲する。figure の表示名は
        他画面と統一され「図・画像」→「図」になった。"""
        src = _read(DELIBERATION_JS)
        assert "var INVENTORY_TYPE_CHIP_LABELS" not in src
        assert "var ELEMENT_TYPE_LABELS" not in src
        assert "function inventoryTypeLabel(type) {" in src
        assert "vocab.elementTypeLabel(elementType)" in src
        assert '"すべて"' in src
        assert "INVENTORY_TYPE_ORDER" in src

    def test_deliberation_status_badge_three_levels_only(self):
        """§7: 検討済み/候補あり/未検討の3段階のみ。dismissed 件数は判定材料に
        使わない（API では返すが表示は抑制する。既存 .dismissed CSS クラスを
        「未検討」のスタイル流用に使うのは可）。"""
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _inventoryStatusBadgeHtml[\s\S]+?\n  \}\n", src)
        assert m
        block = m.group(0)
        assert "検討済み" in block
        assert "候補あり" in block
        assert "未検討" in block
        assert re.search(r"\.dismissed\b", block) is None

    def test_truncated_types_note_is_factual(self):
        """§6/I4: 上限到達時は正直に事実文で伝える。"""
        src = _read(DELIBERATION_JS)
        assert "truncated_types" in src
        assert "は500件で省略されています" in src

    def test_no_confidence_numeric_value_rendered_for_inventory_cards(self):
        """I3: equations.json 由来の confidence 生数値をカードに含めない。"""
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _inventoryCardHtml[\s\S]+?\n  \}\n", src)
        assert m
        assert re.search(r"\bel\.confidence\b", m.group(0)) is None

    def test_inventory_uses_esc_html_for_dynamic_text(self):
        """XSS対策: label/snippet/badges はすべて escHtml 経由で描画する。"""
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _inventoryCardHtml[\s\S]+?\n  \}\n", src)
        assert m
        assert m.group(0).count("escHtml(") >= 3

    def test_inventory_rows_are_not_element_cards(self):
        """§3.3 Phase 3 の決定: 一覧行は統一パーツカードにしない。

        一覧は文脈 DTO を持たず、N 件分の文脈フェッチは「自動では開かない」原則に
        反するため（学習者側の出典タブと同じ判断）。P2 は、行から開く検討モーダル側が
        カード表示であることで満たす。行の種別ラベルは正本（ElementVocab）へ委譲済み。
        """
        src = _read(DELIBERATION_JS)
        m = re.search(r"function _inventoryCardHtml[\s\S]+?\n  \}\n", src)
        assert m
        assert "ElementCard" not in m.group(0)
        assert "elementTypeLabel(el.element_type)" in m.group(0)


class TestElementInventoryAdminJsIntegration:
    """admin.js: 教材管理行の「検出要素」ボタン導線。"""

    def test_inventory_button_class_present(self):
        src = _read(ADMIN_JS)
        assert "admin-inventory-btn" in src

    def test_button_gated_on_document_id_like_figures_button(self):
        """§2/§9: document_id がある教材のみ活性（図・画像ボタンと同条件）。"""
        src = _read(ADMIN_JS)
        assert 'var figuresBtn = m.document_id' in src
        assert 'var inventoryBtn = m.document_id' in src

    def test_inventory_button_placed_next_to_figures_button(self):
        """§2: 「図・画像」の隣に配置する。"""
        src = _read(ADMIN_JS)
        assert re.search(r"figuresBtn\s*\+\s*\n\s*inventoryBtn\s*\+", src)

    def test_click_calls_open_inventory_guarded(self):
        src = _read(ADMIN_JS)
        assert "window.Deliberation.openInventory(" in src
        _assert_deliberation_calls_guarded(src, "admin.js")

    def test_inventory_click_binding_uses_admin_inventory_btn_class(self):
        src = _read(ADMIN_JS)
        m = re.search(r'tbody\.querySelectorAll\("\.admin-inventory-btn"\)[\s\S]+?\}\);\s*\n\s*\}\);', src)
        assert m, "admin-inventory-btn の click バインディングが見つかりません"
        assert "window.Deliberation.openInventory(" in m.group(0)


class TestElementInventoryLectureStudioIntegration:
    """admin-lecture-studio.js: コース構造タブのコースヘッダの「検出要素」入口。"""

    def test_button_html_function_present(self):
        src = _read(LECTURE_STUDIO_JS)
        assert "function lsCourseInventoryButtonHtml" in src
        assert "function lsCourseInventorySources" in src
        assert "function lsBindCourseInventoryButtons" in src

    def test_sources_read_pattern_matches_primary_document_id(self):
        """§9-3: sources の読み方は lsCoursePrimaryDocumentId() と同じにする。"""
        src = _read(LECTURE_STUDIO_JS)
        pattern = "course.sources || (course.data && course.data.sources) || []"
        assert src.count(pattern) >= 2

    def test_button_hidden_without_sources_or_without_deliberation(self):
        """§9-3: sources が0件、または window.Deliberation が無いときはボタンを描画しない。"""
        src = _read(LECTURE_STUDIO_JS)
        m = re.search(r"function lsCourseInventoryButtonHtml[\s\S]+?\n  \}\n", src)
        assert m
        block = m.group(0)
        assert 'if (!window.Deliberation) return ""' in block
        assert 'if (!sources.length) return ""' in block

    def test_multiple_sources_use_menu_pattern(self):
        """§9-3: 複数件は既存 ls-menu パターンの小メニューで教材を選ばせる。"""
        src = _read(LECTURE_STUDIO_JS)
        m = re.search(r"function lsCourseInventoryButtonHtml[\s\S]+?\n  \}\n", src)
        assert m
        block = m.group(0)
        assert '"ls-menu ls-course-inventory-menu"' in block
        assert '"ls-menu-item ls-course-inventory-item"' in block

    def test_material_id_passed_through_without_reshaping(self):
        """§9-3: material_id をそのまま渡してよい（API側が正規化する）。"""
        src = _read(LECTURE_STUDIO_JS)
        m = re.search(r"function lsCourseInventorySources[\s\S]+?\n  \}\n", src)
        assert m
        assert "src.material_id" in m.group(0)

    def test_calls_open_inventory_guarded(self):
        src = _read(LECTURE_STUDIO_JS)
        assert "window.Deliberation.openInventory(" in src
        _assert_deliberation_calls_guarded(src, "admin-lecture-studio.js")

    def test_render_course_structure_binds_inventory_buttons(self):
        """コース構造タブの再描画（lsRenderCourseStructure）のあらゆる分岐で
        lsBindCourseInventoryButtons が呼ばれること（章構造なし早期returnを含む）。"""
        src = _read(LECTURE_STUDIO_JS)
        start = src.index("function lsRenderCourseStructure")
        end = src.index("function lsRenderComponentsTab")
        block = src[start:end]
        assert block.count("lsBindCourseInventoryButtons()") >= 2


class TestElementInventoryLectureStudioEs5Guard:
    """追加した検出要素導線の ES5 準拠（開発ルール5）。"""

    def _added_region(self):
        src = _read(LECTURE_STUDIO_JS)
        start = src.index("function lsCourseInventorySources")
        end = src.index("function lsRenderComponentsTab")
        return src[start:end]

    def test_no_arrow_functions(self):
        assert "=>" not in self._added_region()

    def test_no_const_or_let(self):
        region = self._added_region()
        assert re.search(r"\bconst\s+\w", region) is None
        assert re.search(r"\blet\s+\w", region) is None

    def test_no_template_literals(self):
        assert "`" not in self._added_region()

    def test_only_var_used(self):
        assert "var " in self._added_region()


# ---------------------------------------------------------------------------
# 要素中心コンテキストビュー（Element-Centered Context Lens, Issue #498）。
# 正本: docs/features/element_context_lens_design.md §2.3（読解の開始点を失わせ
# ない・中心移動）/ §3（上位構造Why・選択要素What・下位構造How の共通UI）/
# §6 Phase 2（deliberation.js への統合）/ §8（受け入れ条件）。
# overview.context の統合表示と、隣接ノード選択・パンくずによる中心移動の
# 静的ガードレール（backend/core/deliberation/context_lens.py 側は別ファイル対応）。
# ---------------------------------------------------------------------------


class TestElementContextLens:
    """要素中心コンテキストビュー（上位構造Why / 選択要素What / 下位構造How）と
    中心移動（隣接ノード選択・パンくず）の受け入れ条件。"""

    def test_rendering_functions_present(self):
        """§3.3 Phase 3 以降、中心カード・レーン・バッジの描画は統一パーツカード
        （element-card.js）が担う。deliberation.js が持つのはシェル・補足欄・配線のみ。"""
        src = _read(DELIBERATION_JS)
        for name in (
            "_contextLensHtml",
            "_contextLensShellHtml",
            "_contextLensOpts",
            "_contextSupplementHtml",
        ):
            assert f"function {name}" in src, f"function {name} が見つかりません"

    def test_bespoke_lane_renderers_are_gone(self):
        """独自レーン HTML（旧 deliberation-context-lane / -item / -focus / -nav /
        -status 系）を復活させないこと（P2: パーツ1個の描き方は1つ）。"""
        src = _read(DELIBERATION_JS)
        for name in (
            "function _contextLaneHtml",
            "function _contextLaneItemHtml",
            "function _contextFocusHtml",
            "function _contextGenericHtml",
            "function _contextStatusBadgeHtml",
        ):
            assert name not in src, name
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        for marker in (
            "deliberation-context-lane",
            "deliberation-context-item",
            "deliberation-context-nav",
            "deliberation-context-status",
            "CONTEXT_STATUS_LABELS",
        ):
            assert marker not in code, marker

    def test_context_lens_renders_and_binds_through_element_card(self):
        """render と bind に同一の dto / opts を渡す（近傍チップは onCenter が
        設定されているときだけ描かれるため、食い違うと配線が消える）。"""
        src = _read(DELIBERATION_JS)
        render = _function_block(src, "_contextLensHtml")
        assert "card.render(dto, opts)" in render
        assert "contextLensRender = { dto: dto, opts: opts };" in render
        bind = _function_block(src, "_bindContextNavigation")
        assert "card.bind(container, contextLensRender.dto, contextLensRender.opts)" in bind

    def test_element_card_missing_degrades_to_a_factual_line(self):
        """部品未読み込みは事実文へ縮退する（独自レーン HTML の二重実装は持たない）。"""
        src = _read(DELIBERATION_JS)
        render = _function_block(src, "_contextLensHtml")
        assert "if (!card || !card.render) {" in render
        assert "deliberation-context-lens-unavailable" in render

    def test_evidence_refs_are_rendered_by_the_card_itself(self):
        """ITEM の evidence_refs はカード本体（element-card.js の itemRefsHtml、
        editable 限定の折りたたみ）が描く。旧 DOM 後付け
        （_augmentContextEvidenceRefs）を復活させないこと。"""
        src = _read(DELIBERATION_JS)
        # 関数定義・呼び出しとしては存在しない（撤去の経緯コメントには名前が残る）。
        assert "function _augmentContextEvidenceRefs" not in src
        assert "_augmentContextEvidenceRefs(" not in src
        assert "function _contextEvidenceRefsNode" not in src
        assert "_contextEvidenceRefsNode(" not in src
        card_src = _read(DELIBERATION_JS.parent / "element-card.js")
        assert "function itemRefsHtml" in card_src
        assert "element-card-item-refs" in card_src

    def test_provenance_is_kept_outside_the_card(self):
        """focus.provenance はカードの表示契約（§3.2）に無いので補足欄に残す。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_contextSupplementHtml")
        assert "deliberation-context-provenance" in block
        assert "出典情報はありません" in block
        assert "STANDARDIZATION_STATUS_LABELS" in block

    def test_navigation_functions_present(self):
        """設計書 §2.3/§6 Phase 2: 中心移動・パンくずの本体関数。"""
        src = _read(DELIBERATION_JS)
        for name in ("_navigateToElement", "_renderBreadcrumb"):
            assert f"function {name}" in src, f"function {name} が見つかりません"

    def test_dom_ids_and_classes_present(self):
        src = _read(DELIBERATION_JS)
        assert 'var CONTEXT_LENS_ID = "deliberation-context-lens";' in src
        assert 'id="\' + CONTEXT_LENS_ID + \'"' in src
        assert 'id="deliberation-breadcrumb"' in src

    def test_lane_titles_are_why_and_how(self):
        """設計書 §3: 上位構造（Why）/ 下位構造（How）の見出し文言を維持する
        （カードの laneTitles で上書きする）。"""
        src = _read(DELIBERATION_JS)
        assert '{ upper: "上位構造（Why）", lower: "下位構造（How）" }' in src

    def test_empty_lane_is_a_factual_line_not_hidden(self):
        """設計書 §2.2/§7: 関係が得られない場合も非表示にせず事実文で保持する
        （推測で穴埋めしない）。文言の正本は統一パーツカード側。"""
        card = _read(ROOT / "frontend" / "public" / "js" / "element-card.js")
        assert "この要素が支える上位の構造は、まだ同定されていません。" in card
        assert "この要素を支える下位の構造は見つかりませんでした。" in card

    def test_unidentified_role_is_stated_as_a_fact(self):
        """役割が未同定のときカードは role 行を描かない（推測で埋めない）。
        「この文脈での役割 = 未同定」の事実文は補足欄が持つ。"""
        src = _read(DELIBERATION_JS)
        assert 'var CONTEXT_STATUS_UNIDENTIFIED_LABEL = "未同定";' in src
        block = _function_block(src, "_contextSupplementHtml")
        assert "roleUnidentified" in block
        assert "この文脈での役割" in block

    def test_status_badge_labels_come_from_the_single_vocabulary(self):
        """W8: 数値・スコアを見せない。状態は段階ラベルのみで、語彙の正本は
        element-vocab.js（§3.3 Phase 0）。deliberation.js に独自辞書を持たない。"""
        src = _read(DELIBERATION_JS)
        assert "CONTEXT_STATUS_LABELS" not in src
        assert "CONTEXT_KNOWN_STATUSES" in src
        vocab = _read(ROOT / "frontend" / "public" / "js" / "element-vocab.js")
        for label in ("出典に裏付け", "教員確定", "AI候補"):
            assert label in vocab, label

    def test_relation_label_is_the_rendered_relation_text(self):
        """設計書 §3.2: 関係は動詞（relation_label、主語は焦点要素）で示す。
        サーバ契約の relation_label をそのまま描画に使うこと（正本はカード側）。"""
        card = _read(ROOT / "frontend" / "public" / "js" / "element-card.js")
        assert "item.relation_label" in card

    def test_esc_html_used_in_lens_shell_and_supplement(self):
        """XSS対策: deliberation.js 側が組む動的テキスト（note・補足欄）は escHtml 経由。
        カード本体は opts.escapeHtml として同じ escHtml を受け取る。"""
        src = _read(DELIBERATION_JS)
        lens_block = _function_block(src, "_contextLensHtml")
        assert "escHtml(" in lens_block
        supplement_block = _function_block(src, "_contextSupplementHtml")
        assert "escHtml(" in supplement_block
        opts_block = _function_block(src, "_contextLensOpts")
        assert "escapeHtml: escHtml" in opts_block

    def test_navigate_does_not_close_modal(self):
        """設計書 §2.3: 隣接ノード選択はモーダルを破棄せず中心だけを差し替える。
        パンくず履歴を保持するため _navigateToElement は _closeModal を呼んではならない
        （_closeModal は面③状態・パンくず履歴ごとリセットしてしまう）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_navigateToElement")
        assert "_closeModal" not in block

    def test_context_lens_skips_intra_document_lens_when_available(self):
        """設計書 §6 Phase 0: context が利用可能なとき、論文内レンズ
        （intra_document）は上位/下位構造投影に再構成されるため二重表示しない。
        旧run・context 不在時は従来どおり intra_document を表示する
        （_positioningHtml が opts 省略可能な後方互換シグネチャであること）。"""
        src = _read(DELIBERATION_JS)
        assert "skipIntraDocument" in src
        assert "function _positioningHtml(positioning, opts)" in src

    def test_context_lens_integrated_into_render_modal_body(self):
        """§6 Phase 2: _renderModalBody が data.context を読み、figure は
        図ワークスペースの後（画像を主役に保つ）、非 figure は内訳より前に
        コンテキストレンズを配置すること。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_renderModalBody")
        assert "_contextLensHtml(data.context)" in block
        assert "data.context && data.context.available" in block

    def test_css_classes_defined(self):
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        for selector in (
            ".deliberation-context-lens",
            ".deliberation-context-card.element-card",
            ".deliberation-context-focus",
            ".element-card-item-refs",
            ".element-card-lane-title",
            ".deliberation-breadcrumb",
        ):
            assert selector in css, f"{selector} が styles.css に見つかりません"
        # 撤去した独自レーン CSS が復活していないこと（申し送りコメントは残るので
        # 行頭のルール宣言として現れないことを見る）。
        for selector in (
            ".deliberation-context-lane",
            ".deliberation-context-item",
            ".deliberation-context-nav",
            ".deliberation-context-status",
            ".deliberation-context-evidence",
        ):
            assert not re.search(
                r"^\s*" + re.escape(selector) + r"[\s,{:-]*[,{]", css, re.M
            ), f"{selector} は撤去済みのはず"

    def test_css_status_modifier_classes_distinguish_candidate_from_confirmed(self):
        """W-層の既存視覚言語（candidate=点線・淡色 / confirmed=通常 / 未同定=点）を
        統一パーツカードのバッジ CSS で維持する。"""
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        assert ".element-card-status-confirmed" in css
        assert ".element-card-status-unidentified" in css
        candidate_idx = css.index(".element-card-status-candidate")
        candidate_block = css[candidate_idx : css.index("}", candidate_idx)]
        assert "dashed" in candidate_block
        unidentified_idx = css.index(".element-card-status-unidentified")
        unidentified_block = css[unidentified_idx : css.index("}", unidentified_idx)]
        assert "dotted" in unidentified_block

    def test_no_new_admin_documents_fetch_literal_introduced(self):
        """既存の許可リスト（test_fetch_targets_use_exact_allowlist）を壊していない
        ことの補強確認: /admin/documents/ の出現回数は既存契約どおり4のまま
        （中心移動は既存の overview/annotations エンドポイントを再利用し、
        新規 fetch 先を追加しない。4という基準値自体は説明レビューキュー追加分の
        test_fetch_targets_use_exact_allowlist で説明済み）。"""
        src = _read(DELIBERATION_JS)
        assert src.count('"/admin/documents/"') == 4

    def test_no_raw_fetch_or_new_http_methods_introduced(self):
        src = _read(DELIBERATION_JS)
        assert re.search(r"(?<!api)fetch\(", src) is None
        for method in ("PUT", "DELETE"):
            assert f'"{method}"' not in src
            assert f"'{method}'" not in src

    def test_no_polling_or_forbidden_vocabulary_introduced(self):
        src = _read(DELIBERATION_JS)
        assert "setInterval" not in src
        for word in ("踏破", "達成率", "ランキング"):
            assert word not in src

    def test_no_raw_confidence_field_rendered(self):
        """W8: confidence の生値は表示しない（本機能に confidence フィールドは
        契約上存在しないが、他要素と同じ規約として念のため固定する）。"""
        src = _read(DELIBERATION_JS)
        assert re.search(r"\bfocus\.confidence\b(?!_label)", src) is None
        assert re.search(r"\bitem\.confidence\b(?!_label)", src) is None

    def test_generic_block_is_a_separate_section_from_contextual_role(self):
        """§2.1: 汎用説明（「一般には」）と文脈上の役割を混ぜない。統一パーツカードが
        generic 行と role 行を別行として描く。"""
        card = _read(ROOT / "frontend" / "public" / "js" / "element-card.js")
        assert "function genericHtml(focus, ctx)" in card
        assert "function roleHtml(focus, ctx)" in card
        assert 'GENERIC_PREFIX = "一般には: "' in card

    def test_generic_block_omitted_when_null(self):
        card = _read(ROOT / "frontend" / "public" / "js" / "element-card.js")
        block = _function_block(card, "genericHtml")
        assert 'if (!generic) return "";' in block

    def test_math_renderer_is_injected_into_the_card(self):
        """RC8 / §6 S4: W層モーダルだけ renderMath 未注入で生 TeX が出ていた問題の解消。
        TeX 判定のゲートは element-card.js が内製するので、ここは素の描画関数を渡す
        （deliberation.js 側に第2のゲートを実装しない）。"""
        src = _read(DELIBERATION_JS)
        opts = _function_block(src, "_contextLensOpts")
        assert "renderMath: _renderMath" in opts
        render = _function_block(src, "_renderMath")
        assert "window.katex" in render
        assert "throwOnError: false" in render
        assert "try {" in render  # レンダラの例外でモーダルを壊さない
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        assert "looksLikeRenderableTex" not in code, "TeX 判定の二重実装は置かない"

    def test_derivations_are_forwarded_to_the_card(self):
        """DTO v2 の導出ストーリー。focus 配下で来る場合はカード側が拾う。"""
        src = _read(DELIBERATION_JS)
        render = _function_block(src, "_contextLensHtml")
        assert "derivations: context.derivations || []" in render

    def test_generic_standardization_status_kept_in_supplement(self):
        """カードは L層の標準化判定を描かないので、補足欄で落とさず保持する。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_contextSupplementHtml")
        assert "standardization_status" in block
        assert "共通部品の標準化判定" in block
        assert "escHtml(" in block


# ---------------------------------------------------------------------------
# 照合解析（Contextual Figure Analysis Iterative Verification, #499 Wave 4）。
# 正本: docs/features/contextual_figure_analysis_iterative_verification.md
# 「実装記録（2026-07-18 実装決定事項）」節。バックエンド（別 Wave）が figures GET /
# deliberation overview の figure fields に投影する fields.iterative_analysis
# （confidence 生値を含まず confidence_label のみの契約）の表示と、レビュー質問
# カード「この箇所を再解析」の配線を固定する。
# ---------------------------------------------------------------------------


class TestIterativeAnalysisUi:
    """照合解析セクション（deliberation.js）の受け入れ条件。"""

    def test_rendering_functions_present(self):
        src = _read(DELIBERATION_JS)
        for name in ("_iterativeAnalysisHtml", "_alignmentItemsHtml", "_reviewQuestionsHtml"):
            assert f"function {name}" in src, f"function {name} が見つかりません"

    def test_alignment_status_labels_cover_all_five_states(self):
        src = _read(DELIBERATION_JS)
        assert "ALIGNMENT_STATUS_LABELS" in src
        for status in (
            "supported_by_both",
            "visual_only",
            "text_only",
            "contradicted",
            "unresolved",
        ):
            assert f"{status}:" in src, f"status={status!r} のラベルが見つかりません"

    def test_convergence_labels_cover_all_six_states(self):
        src = _read(DELIBERATION_JS)
        assert "ITERATIVE_CONVERGENCE_LABELS" in src
        for status in (
            "converged",
            "max_iterations_reached",
            "no_progress",
            "aborted_error",
            "aborted_cost_limit",
            "not_run",
        ):
            assert f"{status}:" in src, f"convergence_status={status!r} のラベルが見つかりません"

    def test_unavailable_state_is_a_factual_note_only(self):
        """available が falsy/空なら「まだ実行されていません」の事実文のみを出す
        （W4: 存在しない解析結果を捏造しない）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_iterativeAnalysisHtml")
        assert "反復照合解析はまだ実行されていません" in block
        assert "if (!available)" in block

    def test_iterative_analysis_html_uses_esc_html_repeatedly(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_iterativeAnalysisHtml")
        assert block.count("escHtml(") >= 2

    def test_alignment_and_review_renderers_use_esc_html(self):
        """XSS対策: 照合項目・レビュー質問カードの動的テキストは escHtml 経由で描画する。"""
        src = _read(DELIBERATION_JS)
        row_block = _function_block(src, "_alignmentItemRowHtml")
        assert "escHtml(" in row_block
        card_block = _function_block(src, "_reviewQuestionCardHtml")
        assert "escHtml(" in card_block

    def test_no_raw_confidence_rendered_for_iterative_variables(self):
        """W8: confidence の生値は表示しない。本セクションで使う変数名すべてに
        ついて固定する（ia=iterative_analysis / hyp=context_hypothesis /
        obs=visual_observations / alt=alternative_hypotheses / item=alignment_item /
        rec=verification_iteration / el=expected_element）。"""
        src = _read(DELIBERATION_JS)
        for var in ("ia", "hyp", "obs", "alt", "item", "rec", "el"):
            assert re.search(r"\b" + var + r"\.confidence\b(?!_label)", src) is None, (
                f"{var}.confidence の生値描画を検出しました"
            )
        assert "confidence_label" in src

    def test_unresolved_item_ids_present_in_reanalyze_payload_builder(self):
        """レビュー質問カードが積んだ unresolved_item_ids は reanalyze の body 構築部
        （_figureReanalyzeGuidancePayload）で組み立てる。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_figureReanalyzeGuidancePayload")
        assert "unresolved_item_ids" in block
        assert "unresolvedItemIds" in block

    def test_reverify_button_carries_question_and_region_hint_data_attrs(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_reviewQuestionCardHtml")
        assert "data-question-id=" in block
        assert "data-region-hint=" in block
        assert "deliberation-iterative-reverify" in block
        assert "この箇所を再解析" in block

    def test_reverify_reuses_existing_reanalyze_button_without_new_fetch(self):
        """既存の許可リスト（test_fetch_targets_use_exact_allowlist: /admin/documents/
        は4箇所固定。説明レビューキュー追加分で3→4に更新済み）を壊さないこと。
        送信処理は新規 fetch を書かず、既存の「AIで図を再解析」ボタン
        （_bindFigureReanalysis の click ハンドラ）をプログラム的にクリックして
        完全共有する。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bindIterativeReverify")
        assert 'getElementById("deliberation-figure-reanalyze")' in block
        assert "apiFetch(" not in block
        assert "fetch(" not in block
        assert src.count('"/admin/documents/"') == 4

    def test_reverify_connects_to_candidate_flow_via_shared_button(self):
        """「この箇所を再解析」はプログラム的クリックで既存ボタンの処理を起動する。
        その既存処理（_bindFigureReanalysis）が候補注釈の再読込（_loadAnnotations）を
        呼ぶことは既存テスト（TestFigurePresentationWorkspace）で別途保証されている
        ため、ここでは共有関係そのものを固定する。"""
        src = _read(DELIBERATION_JS)
        reverify_block = _function_block(src, "_bindIterativeReverify")
        assert "reanalyzeBtn.click()" in reverify_block
        reanalyze_block = _function_block(src, "_bindFigureReanalysis")
        assert "_loadAnnotations(" in reanalyze_block

    def test_bind_called_in_render_modal_body_figure_branch(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_renderModalBody")
        assert "_bindIterativeReverify()" in block

    def test_inserted_right_after_figure_mode_container(self):
        """§9: 図の分類ペイン（_figureModeHtml の描画結果）の直後に挿入する。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_figureWorkspaceHtml")
        mode_idx = block.index("deliberation-figure-mode-container")
        iterative_idx = block.index("_iterativeAnalysisHtml(fields)")
        assert mode_idx < iterative_idx

    def test_alignment_items_grouped_by_status_with_caution_styling(self):
        """区分別 alignment 表示。text_only/contradicted/unresolved は注意色。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_alignmentItemsHtml")
        assert "ALIGNMENT_STATUS_ORDER" in block
        row_block = _function_block(src, "_alignmentItemRowHtml")
        assert "is-caution" in row_block
        assert "ALIGNMENT_CAUTION_STATUSES" in src

    def test_evidence_four_way_note_present(self):
        """根拠の4区分注記（画像で直接確認/本文の記述/推論/未確認）を固定文言で表示する。"""
        src = _read(DELIBERATION_JS)
        for phrase in ("画像で直接確認", "本文の記述", "推論", "未確認"):
            assert phrase in src, f"{phrase!r} が見つかりません"

    def test_no_forbidden_vocabulary_in_iterative_section(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_iterativeAnalysisHtml")
        for word in ("踏破", "達成率", "ランキング"):
            assert word not in block

    def test_css_classes_defined(self):
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        for selector in (
            ".deliberation-iterative-analysis",
            ".deliberation-iterative-status",
            ".deliberation-iterative-alignment-item",
            ".deliberation-iterative-alignment-item.is-caution",
            ".deliberation-iterative-review-card",
            ".deliberation-iterative-reverify",
            ".deliberation-iterative-details",
        ):
            assert selector in css, f"{selector} が styles.css に見つかりません"

    def test_no_raw_fetch_or_new_http_methods_introduced(self):
        src = _read(DELIBERATION_JS)
        assert re.search(r"(?<!api)fetch\(", src) is None
        for method in ("PUT", "DELETE"):
            assert f'"{method}"' not in src
            assert f"'{method}'" not in src

    def test_no_polling_introduced(self):
        src = _read(DELIBERATION_JS)
        assert "setInterval" not in src

    def test_es5_regression_not_reintroduced(self):
        """開発ルール5: ES5 準拠を本追加分でも維持する
        （TestDeliberationJsEs5RegressionGuard の全文検査と同じ観点の補強確認）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_iterativeAnalysisHtml")
        assert "=>" not in block
        assert re.search(r"\bconst\s+\w", block) is None
        assert re.search(r"\blet\s+\w", block) is None
        assert "`" not in block


# ---------------------------------------------------------------------------
# 要素解決の誤表示是正（2026-09、正本: graph_dialogue_review_design.md §11）。
#
# グラフ対話レビューの「深く検討」は理論操作グラフの集約ノードから代表要素の
# agent 側 ID（comp_003 等）を渡す。deliberation.js 側の受け入れ条件:
#   - agent 側 ID のときは document_id を必ずクエリに載せる（backend は
#     document スコープが無いと解決しない fail-closed）
#   - overview が返す正準 ref（DB UUID）で以降の呼び出しを行う
#   - エラー表示はサーバの detail（事実文）を出し、原因と無関係の固定文言
#     （「equation は document_id が必要です」）を 422 に被せない
# ---------------------------------------------------------------------------


class TestElementResolutionErrorMessaging:
    def test_stale_422_fixed_message_is_gone(self):
        src = _read(DELIBERATION_JS)
        assert "この要素の指定が不正です" not in src

    def test_render_error_prefers_server_detail(self):
        src = _read(DELIBERATION_JS)
        assert "function _renderError(status, detail)" in src
        block = src[src.index("function _renderError(status, detail)"):]
        block = block[: block.index("\n  }\n") + 4]
        assert 'typeof detail === "string"' in block
        # detail が無いときだけ status 由来の汎用文言へ縮退する。
        assert "この要素は見つかりませんでした" in block
        assert "内訳の読み込みに失敗しました" in block

    def test_overview_failure_passes_detail_to_render_error(self):
        src = _read(DELIBERATION_JS)
        block = src[src.index("function _loadAndRenderElement()"):]
        block = block[: block.index("\n  }\n") + 4]
        assert "_parseJsonResponse" in block  # detail を保持した Error を投げる共通処理
        assert "_renderError(err && err.status, err && err.detail)" in block


class TestAgentSideElementIdScoping:
    def test_agent_id_resolvable_types_declared(self):
        src = _read(DELIBERATION_JS)
        assert "AGENT_ID_RESOLVABLE_ELEMENT_TYPES" in src
        block = src[src.index("var AGENT_ID_RESOLVABLE_ELEMENT_TYPES"):]
        block = block[: block.index("};") + 2]
        assert "theory_component: true" in block
        assert "theory_claim: true" in block

    def test_document_id_is_sent_for_non_uuid_ids(self):
        src = _read(DELIBERATION_JS)
        block = src[src.index("function _refNeedsDocumentId(ref)"):]
        block = block[: block.index("\n  }\n") + 4]
        assert "_isDbUuid(ref.elementId)" in block
        query = src[src.index("function _documentIdQuery(ref)"):]
        query = query[: query.index("\n  }\n") + 4]
        assert "_refNeedsDocumentId(ref)" in query

    def test_overview_ref_is_canonicalized_for_later_calls(self):
        src = _read(DELIBERATION_JS)
        assert "function _syncRefFromOverview(data)" in src
        block = src[src.index("function _syncRefFromOverview(data)"):]
        block = block[: block.index("\n  }\n") + 4]
        assert "resolved.element_id" in block
        assert "chatState.ref.elementId = canonicalId" in block
        for caller in ("function _reloadOverview()", "function _loadAndRenderElement()"):
            body = src[src.index(caller):]
            body = body[: body.index("\n  }\n") + 4]
            assert "_syncRefFromOverview(data)" in body, caller
