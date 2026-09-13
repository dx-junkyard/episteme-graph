"""概念レジストリの教員フロントエンド（ナレッジライブラリタブ）静的ガードレール。

正本: docs/features/concept_registry_design.md
  KR2 確定は人間・AI は candidate まで（候補チップ・「候補のままでは届かない」の明示）
  KR3 リンクであってマージではない（統合・行削除の導線を作らない）
  KR4 mapping_justification は必ず出す（空は「記録なし」と正直に書く）
  KR6 数値を見せない（cosine / confidence / 候補数 / 一致数を描かない）
  KR7 情報を落とさない（見送りは理由必須の状態遷移。削除ボタンなし）
  §10 UI（新モーダルを作らず、タブ上部に「同一性の候補」・詳細に4区画・分野一覧に導出ボタン）

サーバ側（`core/library/` / `routes/library.py` / migration 082）は担当 A/B のテストが、
語彙の Python ⇄ JS 逐語一致は `test_library_vocab_mirror.py` が、アンカー表の整合は
`test_admin_help_ui_anchors.py` / `test_admin_help_inspect_ui_static.py` が担当する。
本ファイルは admin フロント（`admin.html` / `admin.js`）の静的契約のみを検証する
（API は呼ばない。`test_atlas_gaps_admin_ui_static.py` と同じ流儀）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
LIBRARY_MANUAL = ROOT / "docs" / "manual" / "teacher" / "19-admin-knowledge-library.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

#: 本層が追加する管理UIアンカー（3点セットの1つ目）。
REGISTRY_ANCHOR_IDS = (
    "knowledge-library.identity-candidates",
    "knowledge-library.identity-derive",
    "knowledge-library.entry-review",
    "knowledge-library.labels",
    "knowledge-library.relations",
    "knowledge-library.atlas-links",
)

_SEGMENT_START = "// ===== 概念レジストリ（Concept Registry, migration 082）— ここから"
_SEGMENT_END = "// ===== 概念レジストリ — ここまで"


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _registry_code() -> str:
    """区画から `//` 行コメントを落とした「実際に描かれる側」だけを返す。

    禁止語の検査は描画される文字列に対して行う（このファイル自身や admin.js の
    説明コメントが禁止語を引用しているのは正当なので、コメントは対象外にする）。
    """
    return "\n".join(
        line for line in _registry_segment().split("\n") if not line.strip().startswith("//")
    )


def _registry_segment() -> str:
    src = _read(ADMIN_JS)
    assert _SEGMENT_START in src, "admin.js に概念レジストリ区画の開始マーカーが無い"
    assert _SEGMENT_END in src, "admin.js に概念レジストリ区画の終了マーカーが無い"
    return src[src.index(_SEGMENT_START) : src.index(_SEGMENT_END)]


# ===========================================================================
# 1. 5区画の存在（タブ上部の同一性候補 + 詳細の4区画）
# ===========================================================================


class TestSections:
    def test_identity_candidates_container_is_in_the_tab(self):
        html = _read(ADMIN_HTML)
        assert 'id="library-identity-candidates"' in html
        assert 'data-ui-anchor="knowledge-library.identity-candidates"' in html
        assert "同一性の候補" in html

    def test_derive_button_sits_with_the_domain_list(self):
        html = _read(ADMIN_HTML)
        assert 'id="library-atlas-derive-btn"' in html
        assert 'data-ui-anchor="knowledge-library.identity-derive"' in html
        assert "地図との対応を導出" in html

    def test_detail_hosts_the_registry_sections(self):
        src = _read(ADMIN_JS)
        # 詳細のレンダラが差し込み口を持ち、描画関数を呼ぶ。
        assert 'id="library-registry-sections"' in src
        assert "renderLibraryRegistrySections(entry);" in src

    def test_four_detail_sections_are_rendered(self):
        seg = _registry_segment()
        for title in ("別名・隠しラベル", "関係", "分野の地図との対応", "この概念の扱い"):
            assert title in seg, title + " の区画が無い"

    def test_each_section_carries_its_anchor(self):
        seg = _registry_segment()
        blob = seg + _read(ADMIN_HTML)
        for anchor in REGISTRY_ANCHOR_IDS:
            assert anchor in blob, anchor + " の担体が無い"

    def test_entries_list_shows_a_candidate_chip(self):
        src = _read(ADMIN_JS)
        assert "_libraryReviewChipHtml(e.review_status)" in src
        assert "include_candidates=true" in src

    def test_lists_have_add_forms(self):
        seg = _registry_segment()
        assert 'id="library-label-add"' in seg
        assert 'id="library-relation-add"' in seg


# ===========================================================================
# 2. KR6 数値を見せない
# ===========================================================================


class TestNoNumbers:
    def test_no_cosine_or_confidence_is_rendered(self):
        code = _registry_code()
        for forbidden in ("cosine", "confidence", "スコア", "類似度", "一致度"):
            assert forbidden not in code, "概念レジストリ区画に数値表現 " + forbidden + " がある"

    def test_no_count_badges(self):
        """候補数・支持論文数・一致件数のバッジを出さない（支持はタイトルの列挙）。"""
        code = _registry_code()
        assert "件" not in code, "概念レジストリ区画に件数表示がある"
        assert ".length +" not in code
        assert "hidden_count" in code, "閲覧不可由来の存在は事実文で正直に出す（W-β 踏襲）"
        # hidden_count は真偽としてだけ使い、数を描かない。
        assert "escHtml(c.hidden_count" not in code
        assert "+ c.hidden_count" not in code

    def test_supporting_papers_are_listed_by_title(self):
        seg = _registry_segment()
        assert "supporting_titles" in seg
        assert "この候補を支持する論文" in seg


# ===========================================================================
# 3. KR7 情報を落とさない（見送りは理由必須・削除導線なし）
# ===========================================================================


class TestDismissNeedsAReason:
    def test_prompt_helper_rejects_blank_reason(self):
        seg = _registry_segment()
        start = seg.index("function _libraryPromptDismissReason(what)")
        block = seg[start : seg.index("\n  }\n", start)]
        assert "見送る理由" in block
        # 空欄・キャンセルのときは null を返して呼び出し側が送信しない。
        assert "return null;" in block
        assert "見送りには理由が必要です" in block

    def test_every_dismiss_path_goes_through_the_prompt(self):
        seg = _registry_segment()
        # 「見送り」を発火する各ハンドラが理由取得を経由する。
        for marker in (
            "library-entry-dismiss",
            "library-label-dismiss",
            "library-relation-dismiss",
            "library-atlas-link-dismiss",
        ):
            assert marker in seg, marker + " の見送りボタンが無い"
        assert seg.count("_libraryPromptDismissReason(") >= 5

    def test_no_delete_route_is_called(self):
        code = _registry_code()
        assert 'method: "DELETE"' not in code
        assert "削除" not in code


# ===========================================================================
# 4. KR2 / KR3 断りの文言（候補は届かない・リンクは統合ではない）
# ===========================================================================


class TestFactualWording:
    def test_candidate_state_is_explained_not_implied(self):
        seg = _registry_segment()
        assert "候補のままのエントリは凍結できず、解析パイプラインや学習者には届きません。" in seg

    def test_relations_are_links_not_merges(self):
        seg = _registry_segment()
        assert "関係はリンクであって統合ではありません" in seg
        assert "どちらの行も残ります" in seg

    def test_derive_confirm_says_the_skeleton_is_not_rewritten(self):
        seg = _registry_segment()
        assert "分野の地図（骨格）は書き換わりません" in seg

    def test_atlas_link_section_names_the_skeleton_version(self):
        """KR9 / VA8: どの版の地図に対する対応かを必ず明示する。"""
        seg = _registry_segment()
        assert "skeleton_version" in seg
        assert "分野の地図 版" in seg
        assert "このノードは現在の版の地図にはありません" in seg

    def test_missing_justification_is_honest(self):
        """KR4: 根拠の記録が無い行を推測で埋めない。"""
        seg = _registry_segment()
        assert "根拠の記録なし" in seg

    def test_empty_queue_does_not_claim_the_corpus_is_clean(self):
        seg = _registry_segment()
        assert "いま確認をお待ちしている同一性の候補はありません。" in seg


# ===========================================================================
# 5. fail-soft（取得失敗は区画ごと畳む・「無い」と断定しない）
# ===========================================================================


class TestFailSoft:
    def test_each_loader_has_a_catch(self):
        seg = _registry_segment()
        for fn in (
            "loadLibraryIdentityCandidates",
            "loadLibraryEntryLabels",
            "loadLibraryEntryRelations",
            "loadLibraryEntryAtlasLinks",
        ):
            start = seg.index("function " + fn + "(")
            block = seg[start : seg.index("\n  }\n", start)]
            assert ".catch(" in block, fn + " に fail-soft の catch が無い"

    def test_section_is_removed_not_labelled_empty_on_failure(self):
        seg = _registry_segment()
        assert "function _libraryHideSection(listElId)" in seg
        assert seg.count("_libraryHideSection(") >= 4

    def test_no_polling(self):
        seg = _registry_segment()
        assert "setInterval" not in seg


# ===========================================================================
# 6. ES5 準拠（開発ルール5: admin.js は ES5）
# ===========================================================================


class TestRegistrySegmentIsEs5:
    def test_no_arrow_functions(self):
        assert "=>" not in _registry_segment()

    def test_no_const_or_let(self):
        seg = _registry_segment()
        assert re.search(r"(^|[^\w.$])const\s+\w", seg) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", seg) is None

    def test_no_template_literals_or_class(self):
        seg = _registry_segment()
        assert "`" not in seg
        assert re.search(r"(^|[^\w.$])class\s+\w", seg) is None

    def test_no_promise_finally_or_object_spread(self):
        seg = _registry_segment()
        assert ".finally(" not in seg
        # 「...」は「読み込み中...」等の文言に出るので、スプレッド構文だけを弾く。
        assert re.search(r"\.\.\.[A-Za-z_{\[]", seg) is None


# ===========================================================================
# 7. アンカー3点セット（表 + マニュアル節）
# ===========================================================================


class TestAnchorTriple:
    def test_all_anchors_are_registered(self):
        for anchor in REGISTRY_ANCHOR_IDS:
            assert anchor in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor
            assert anchor in admin_anchors_mod.ADMIN_UI_ANCHORS, anchor

    def test_all_anchors_point_at_the_library_manual(self):
        for anchor in REGISTRY_ANCHOR_IDS:
            ref = admin_anchors_mod.ADMIN_UI_ANCHORS[anchor]
            assert ref.startswith("teacher/19-admin-knowledge-library.md#"), anchor

    def test_manual_sections_exist_with_explicit_anchors(self):
        text = _read(LIBRARY_MANUAL)
        for anchor in REGISTRY_ANCHOR_IDS:
            frag = admin_anchors_mod.ADMIN_UI_ANCHORS[anchor].split("#", 1)[1]
            assert re.search(r"^##+ .*\{#" + re.escape(frag) + r"\}\s*$", text, re.M), (
                anchor + " のマニュアル節（{#" + frag + "}）が無い"
            )

    def test_manual_explains_the_disabled_and_error_cases(self):
        """無効化され得る要素は「〜な場合」の記述を持つ（管理マニュアルの規律）。"""
        text = _read(LIBRARY_MANUAL)
        assert "先に左の一覧から分野を選んでください" in text
        assert "先にここで" in text and "[確定にする]" in text

    def test_manual_resolves_for_teacher_role(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor in REGISTRY_ANCHOR_IDS:
            assert anchor in resolved, anchor
            assert resolved[anchor]["title"], anchor
            assert resolved[anchor]["body"], anchor
