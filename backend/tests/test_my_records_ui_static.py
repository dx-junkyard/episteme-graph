"""主権台帳v1「わたしの記録」フロントエンドの静的ガードレール。

設計正本: docs/features/trace_registry_sovereignty_ledger_design.md §3.1/§3.4/§3.5。
既存の test_personal_map_home_ui_static.py と同じ方式（Path 読み + 素の assert + re）で、
フロントエンドソースの静的検証により受け入れ条件を固定する
（実ブラウザ・実APIには依存しない）。

受け入れ条件との対応:
1. my-records.js: 常設注記（PRIVACY_NOTE）と封印の正直表示（SEAL_NOTE）の逐語存在 /
   ポーリング禁止 / 禁止語彙なし（TR6）/ fetch は GET のみ・書き込み経路なし（TR4）/
   window.MyRecords 公開契約（init/open/close）
2. index.html: my-records.js の script タグ + #my-records-btn +
   data-ui-anchor="topbar.my-records" が同一要素にある
3. app.js: MyRecords への参照は window. 経由でガードされている（init/open 配線）
4. UI アンカー4点セット: KNOWN_UI_ANCHOR_IDS 登録・UI_ANCHORS のマップ値・
   マニュアル節（{#my-records}）の実在・frontend の data-ui-anchor 値の網羅
   （test_landscape_ui_static.py の TestLearnerHelpAnchor 型）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS  # noqa: E402

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
MY_RECORDS_JS = ROOT / "frontend" / "public" / "js" / "my-records.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

ANCHOR_ID = "topbar.my-records"

# TR6（数値を見せない）と同族の、ゲーミフィケーション・評価を匂わせる禁止語彙
# （test_personal_map_home_ui_static.py の FORBIDDEN_WORDS と同一集合）。
FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")

PRIVACY_NOTE = "この記録はあなたにだけ表示されます。成績評価には使用されません。"
SEAL_NOTE = "封印の仕組みは、封印したという事実を残したまま内容を読めなくする形で設計中です。"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMyRecordsModule:
    """my-records.js 単体の受け入れ条件（TR4 / TR5 / TR6）。"""

    def test_file_exists(self):
        assert MY_RECORDS_JS.exists(), (
            "frontend/public/js/my-records.js が存在しません。"
            "主権台帳v1 Phase 1 の実装が着地すればこのテストは pass するようになります。"
        )

    def test_privacy_note_verbatim(self):
        """常設注記（わたしの地図と同文）が逐語で存在する。"""
        src = _read(MY_RECORDS_JS)
        assert PRIVACY_NOTE in src

    def test_privacy_note_rendered_via_textcontent(self):
        """常設注記は textContent で描画する（HTML 注入経路にしない）。"""
        src = _read(MY_RECORDS_JS)
        assert "textContent = PRIVACY_NOTE" in src

    def test_seal_note_verbatim(self):
        """封印準備中の正直な事実文が逐語で存在する（TR5: 偽のボタン・先取りの約束なし）。"""
        src = _read(MY_RECORDS_JS)
        assert SEAL_NOTE in src

    def test_seal_note_is_not_a_button(self):
        """SEAL_NOTE は事実文（textContent）であってボタンにしない。"""
        src = _read(MY_RECORDS_JS)
        assert "textContent = SEAL_NOTE" in src
        # SEAL_NOTE を担うのは div（createElement("div")）であり button ではない。
        seal_block = src[src.find("textContent = SEAL_NOTE") - 300 : src.find("textContent = SEAL_NOTE")]
        assert 'createElement("button")' not in seal_block

    def test_no_polling(self):
        """ポーリング禁止。fetch は明示操作（open() / 持ち出す）起点のみ。"""
        src = _read(MY_RECORDS_JS)
        assert "setInterval" not in src

    def test_no_localstorage_persistence(self):
        """表示状態を localStorage に永続化しない（読むのは eg_token のみ）。"""
        src = _read(MY_RECORDS_JS)
        assert "localStorage.setItem" not in src

    def test_no_user_id_in_source(self):
        """本人スコープは既存認証（JWT）で解決し、URL・クエリ・コードに user 識別子を書かない。"""
        src = _read(MY_RECORDS_JS)
        assert "user_id" not in src

    def test_no_forbidden_vocabulary(self):
        """TR6: 数値・進捗・ゲーミフィケーションを匂わせる語彙を出さない。"""
        src = _read(MY_RECORDS_JS)
        hits = [w for w in FORBIDDEN_WORDS if w in src]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_public_contract_methods_present(self):
        """公開契約: init / open / close を window.MyRecords として公開する。"""
        src = _read(MY_RECORDS_JS)
        assert re.search(r"window\.MyRecords\s*=\s*\{", src)
        for name in ("init", "open", "close"):
            assert re.search(r"\b" + name + r"\b", src), f"{name} が見つかりません"

    def test_all_fetches_are_get_only(self):
        """TR4: 台帳は読み取り専用。このファイルの fetch に method 指定（POST 等）が
        一切無いこと（= すべて既定の GET）。
        """
        src = _read(MY_RECORDS_JS)
        assert re.search(r"\bmethod\s*:", src) is None, (
            "my-records.js の fetch に method 指定があります（GET 以外の経路を作らない）"
        )

    def test_no_dismiss_or_delete(self):
        """台帳から確定・却下・行削除を行わない（訂正操作は既存画面のまま）。"""
        src = _read(MY_RECORDS_JS)
        assert "/dismiss" not in src
        assert 'method: "DELETE"' not in src

    def test_fetches_records_endpoints(self):
        """データソースは本人スコープの `/api/me/records`（+ export）のみ。"""
        src = _read(MY_RECORDS_JS)
        assert '"/me/records"' in src
        assert '"/me/records/export"' in src

    def test_loading_and_fail_wording(self):
        """読み込み中/失敗の表示は personal-map-home と同型（「いまは表示できません。」）。"""
        src = _read(MY_RECORDS_JS)
        assert "読み込み中…" in src
        assert "いまは表示できません。" in src

    def test_truncated_fact_line(self):
        """truncated は事実文で正直に言う（件数は出さない。「すべて」と断言しない —
        持ち出しにも読み出し上限がある。TR5）。"""
        src = _read(MY_RECORDS_JS)
        assert (
            "表示は最新分のみです。持ち出しには記録が新しい順に含まれます"
            "（ごく大量の記録がある場合は上限まで）。" in src
        )
        assert "持ち出しにはすべて含まれます" not in src

    def test_export_failure_fact_line(self):
        """持ち出し失敗時は事実文のみ（alert・エラーバナーにしない）。"""
        src = _read(MY_RECORDS_JS)
        assert "持ち出しに失敗しました。" in src
        assert "alert(" not in src

    def test_export_uses_blob_download(self):
        """持ち出しは fetch + blob + <a download> 方式
        （admin-discuss-observation.js の doDownload と同じ流儀）。
        """
        src = _read(MY_RECORDS_JS)
        assert "URL.createObjectURL" in src
        assert "URL.revokeObjectURL" in src
        assert "Content-Disposition" in src

    def test_map_excluded_flag_label(self):
        """map_excluded の行には「地図には出していません」の小ラベルを出す。"""
        src = _read(MY_RECORDS_JS)
        assert "map_excluded" in src
        assert "地図には出していません" in src

    def test_provenance_section_present(self):
        """provenance_note を「来歴」区画に表示する（TR5）。"""
        src = _read(MY_RECORDS_JS)
        assert "provenance_note" in src
        assert "来歴" in src


class TestIndexHtmlIntegration:
    def test_my_records_script_tag_present(self):
        html = _read(INDEX_HTML)
        assert re.search(
            r'<script src="/js/my-records\.js(\?[^"]*)?"></script>', html
        ), "index.html に my-records.js の script タグがありません"

    def test_my_records_button_present_with_anchor(self):
        """#my-records-btn が存在し、data-ui-anchor="topbar.my-records" が同一要素にある。"""
        html = _read(INDEX_HTML)
        m = re.search(r'<button[^>]*\bid="my-records-btn"[^>]*>', html)
        assert m, "index.html に #my-records-btn がありません"
        assert 'data-ui-anchor="topbar.my-records"' in m.group(0), (
            "#my-records-btn に data-ui-anchor=\"topbar.my-records\" がありません"
        )


class TestAppJsIntegration:
    """app.js 側の統合: MyRecords 参照はすべて window. 経由でガードされている。"""

    def test_my_records_referenced(self):
        src = _read(APP_JS)
        assert "MyRecords" in src, "app.js が MyRecords と統合されていません"

    def test_no_naked_my_records_calls(self):
        src = _read(APP_JS)
        naked = re.search(r"(?<!window\.)\bMyRecords\.\w+\s*\(", src)
        assert naked is None, (
            f"window. を伴わない MyRecords の直呼びがあります: {naked.group(0)!r}"
        )

    def test_init_hooked(self):
        src = _read(APP_JS)
        assert "MyRecords.init(" in src

    def test_my_records_btn_wired_to_open(self):
        src = _read(APP_JS)
        assert "my-records-btn" in src
        assert "MyRecords.open()" in src


class TestLearnerHelpAnchor:
    """UIアンカー4点セット（正本 = core/help_kb/ui_anchors.py。
    test_landscape_ui_static.py の TestLearnerHelpAnchor 型）。
    """

    def test_anchor_registered_in_known_ids(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS

    def test_anchor_mapped_to_student_manual_section(self):
        assert UI_ANCHORS.get(ANCHOR_ID) == "student/02-student.md#my-records"

    def test_manual_section_exists_with_explicit_anchor(self):
        md = _read(STUDENT_MANUAL)
        assert "{#my-records}" in md
        assert "わたしの記録" in md
        # 常設注記・持ち出し・封印の正直な説明がマニュアル側にもある
        assert "成績評価には使用されません" in md
        assert "持ち出す" in md
        assert "封印したという事実を残したまま内容を読めなくする形で設計中" in md

    def test_frontend_anchor_values_are_all_known(self):
        html = _read(INDEX_HTML)
        js = _read(MY_RECORDS_JS)
        used = set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', html))
        used |= set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', js))
        assert ANCHOR_ID in used
        assert not (used - set(KNOWN_UI_ANCHOR_IDS)), (
            f"KNOWN_UI_ANCHOR_IDS に無いアンカーID: {used - set(KNOWN_UI_ANCHOR_IDS)}"
        )
