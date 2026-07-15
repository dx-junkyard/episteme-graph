"""W層（要素検討ワークスペース）Phase 0 フロント統合の静的ガードレール。

正本: docs/features/element_deliberation_workspace_design.md（§8 API / §9 フロント）。
frontend/public/js/deliberation.js は Phase 0（overview の統合表示のみ・読み取り専用）の
実装で、既存の atlas / personal-map 静的テストと同様にソースの静的検証で受け入れ条件を
固定する（test_personal_map_ui_guardrails.py と同型）。

受け入れ条件との対応:
1. deliberation.js: ポーリング禁止 / 禁止語彙なし（踏破・達成率・ランキング）/
   fetch 先が "/admin/deliberation/" のみ / POST・PUT・PATCH・DELETE の fetch が無い
   （Phase 0 は読み取り専用）/ window.Deliberation のエクスポートがある
2. admin.html: deliberation.js の script タグが admin.js より前にある
3. admin.js: window.Deliberation への参照はすべてガード形（素の Deliberation. 直呼びなし）
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
        """§9: 地図・アシスタント系と同様ポーリング禁止。開くたび fetch する。"""
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

    def test_fetch_target_is_deliberation_only(self):
        """§8: overview API 以外の admin API パスを直接参照しない。"""
        src = _read(DELIBERATION_JS)
        assert "/admin/deliberation/" in src
        idx = 0
        while True:
            idx = src.find("/admin/", idx)
            if idx == -1:
                break
            fragment = src[idx : idx + len("/admin/deliberation")]
            assert fragment == "/admin/deliberation", (
                "deliberation.js が deliberation 以外の /admin/ パスを参照しています: "
                + src[idx : idx + 60]
            )
            idx += 1

    def test_no_raw_fetch_calls(self):
        """認証・エラー処理を持つ apiFetch 経由のみを使い、生の fetch(...) を呼ばない。"""
        src = _read(DELIBERATION_JS)
        assert re.search(r"(?<!api)fetch\(", src) is None

    def test_no_write_methods(self):
        """Phase 0 は overview の読み取りのみ（面③対話・注釈コミットは Phase 2）。"""
        src = _read(DELIBERATION_JS)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'"{method}"' not in src
            assert f"'{method}'" not in src

    def test_equation_requires_document_id(self):
        """設計書 §2: equation は document_id で一意化するため必須。"""
        src = _read(DELIBERATION_JS)
        assert 'elementType === "equation" && !opts.documentId' in src


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
