"""学習画面「？」インスペクト・モードの UI 論理アンカー表（正本）。

設計正本: ``docs/features/learning_ui_inspect_hover_design.md`` §5.2 / §9-2/3。

トップバーの「？」（インスペクト・モード）を ON にした状態で UI 部品（教材の中身では
なくシステムの部品）にホバーしたときに出す、マニュアル節ツールチップの参照表。
値は必ず ``docs/manual/student/`` に実在する節だけを指す（audience 越境は構造的に
禁止 — teacher/system_admin ディレクトリは一切参照しない。IH1/IH9）。

対応する節がまだ無い（＝マニュアルがまだこの UI 要素を説明していない）論理アンカーは
このマップに **入れない**。未整備の発見は捏造で埋めず、no_hit 経路
（§5.3/§8、``routes/learning.py`` の ``ui-anchor-events`` / ``_usage_help_response``）に
委ね、G層 ``manual.help_gaps_pending`` の需要側計器にそのまま流す（副産物として
「未整備アンカーの発見」が自動で改善ループに乗る）。

FastAPI を import しない（core 層の規約）。
"""

from __future__ import annotations

from typing import Optional

from . import manual as _manual

# ---------------------------------------------------------------------------
# 候補アンカーID全集合（マップ済み + 未整備）。
#
# ``UI_ANCHORS`` のキーだけでは「マップ済みのみ」になってしまい、まだ節が無い
# アンカーへの no_hit 記録（§5.4）を「未知のアンカーID」として弾いてしまう。
# ``routes/learning.py`` の ``POST /help/ui-anchor-events`` はこの全集合に対して
# 検証し、既知の UI 部品からの記録であることだけを確認する（内容の有無は
# ``UI_ANCHORS``/``resolve_ui_anchors`` が別途判定する）。
# ---------------------------------------------------------------------------
KNOWN_UI_ANCHOR_IDS: frozenset[str] = frozenset(
    {
        "sidebar.mode-sequential",
        "sidebar.mode-discuss",
        "sidebar.course-tree",
        "material.next-topic",
        "material.lecture-toggle",
        "composer.input",
        "composer.send",
        "composer.voice",
        "topbar.inspect",
        "topbar.atlas",
        "topbar.my-map",
        "rightpanel.tab-context",
        "rightpanel.tab-progress",
        "rightpanel.tab-sources",
        "rightpanel.clear-history",
        "discuss.scope-toggle",
        "discuss.end",
        # 知識ランドスケープ（docs/features/knowledge_landscape_design.md §10.2）:
        # 出典タブの「分野の中の位置づけ」セクション。
        "sources.paper-placement",
    }
)

# ---------------------------------------------------------------------------
# 論理アンカーID -> "student/<file>.md#<anchor>"。
#
# docs/manual/student/02-student.md に実在する節にのみマップする。2026-07-26 に
# 学習UI再編（learning_ui_inspect_hover_design.md）に追随してマニュアルへ6節を
# 追記し、KNOWN_UI_ANCHOR_IDS の全17種がここにマップ済みになった。2026-08-04 に
# 知識ランドスケープ（knowledge_landscape_design.md §10.2）の
# sources.paper-placement を追加し、全18種がマップ済みの状態を保っている。
# sidebar.mode-sequential / sidebar.mode-discuss は「順番に学ぶ」「この論文と
# 議論する」の二枚看板を1つの節で等重に説明しているため、同じ参照先を指す
# （composer.input / composer.send が同じ #ai-chat を指すのと同じパターン）。
# ---------------------------------------------------------------------------
UI_ANCHORS: dict[str, str] = {
    # 左サイドバー（学習パス＝章→トピックのツリー構造）の説明。
    "sidebar.course-tree": "student/02-student.md#screen-overview",
    # 「次のトピックへ進める」条件（理解度チェックの合格）を説明する節。
    "material.next-topic": "student/02-student.md#comprehension-check",
    # 教材ヘッダーの「📖 読む | 🎙 講義」切替（インタラクティブ・レクチャー）。
    "material.lecture-toggle": "student/02-student.md#interactive-lecture",
    # チャット入力欄（AIチャットで質問する、の手順1）。
    "composer.input": "student/02-student.md#ai-chat",
    # 「送信」ボタン / Enter 送信（同節の手順2）。
    "composer.send": "student/02-student.md#ai-chat",
    # 「🎤 音声で話す」ボタン＝ハンズフリー音声会話。
    "composer.voice": "student/02-student.md#voice-mode",
    # 「地図」ボタン＝分野の地図。
    "topbar.atlas": "student/02-student.md#field-atlas",
    # 「わたしの地図」ボタン。
    "topbar.my-map": "student/02-student.md#personal-map",
    # 「❓ 使い方」ボタン＝インスペクト・モードの ON/OFF。
    "topbar.inspect": "student/02-student.md#inspect-mode",
    # 右パネルの Context/Progress/Sources タブ全体を紹介する節
    # （Context 個別の節はまだ無いため画面概要節で代用）。
    "rightpanel.tab-context": "student/02-student.md#screen-overview",
    # 右パネル Progress タブ＝違和感ダイジェストのカード提示先として明示されている節。
    "rightpanel.tab-progress": "student/02-student.md#tension-digest",
    # 右パネル Sources タブ＝出典タブとして明示されている節。
    "rightpanel.tab-sources": "student/02-student.md#answer-origin-badge",
    # 「▸ このトピックの操作」内の「質疑応答履歴を削除」ボタン。
    "rightpanel.clear-history": "student/02-student.md#clear-chat-history",
    # サイドバー最上部の二枚看板（「順番に学ぶ」「この論文と議論する」）。
    "sidebar.mode-sequential": "student/02-student.md#learning-mode",
    "sidebar.mode-discuss": "student/02-student.md#learning-mode",
    # discuss モードのスコープ切替（このコースのソース論文 / 閲覧できる周辺資料まで）。
    "discuss.scope-toggle": "student/02-student.md#discuss-scope-toggle",
    # discuss モードの「議論を終える」ボタン。
    "discuss.end": "student/02-student.md#discuss-end",
    # 出典タブの「分野の中の位置づけ」（知識ランドスケープ・論文の位置づけ）。
    "sources.paper-placement": "student/02-student.md#paper-placement",
}


def _split_ref(ref: str) -> tuple[str, str, str]:
    """``"student/<file>.md#<anchor>"`` を ``(audience, file, anchor)`` に分解する。"""
    audience, _, rest = (ref or "").partition("/")
    file, _, anchor = rest.partition("#")
    return audience, file, anchor


def split_manual_ref(ref: str) -> tuple[str, str]:
    """``"student/<file>.md#<anchor>"`` から audience を除いた ``(file, anchor)`` を返す。

    ``routes/learning.py`` の usage_help ハンドラが、直接解決した UI アンカーの内容から
    ``manual_citations``（file/anchor/title）を組み立てるために使う。
    """
    _audience, file, anchor = _split_ref(ref)
    return file, anchor


def resolve_ui_anchors() -> dict[str, dict]:
    """``UI_ANCHORS`` の各アンカーを student 索引で解決する。

    解決できない（ファイル/アンカーが実在しない、または ``student/`` 以外を指す不正な
    マッピング）ものは結果に含めない（fail-open。起動時 validator
    （``check_ui_anchor_mappings``）が別途違反として検出する）。

    Returns:
        ``{anchor_id: {"title": .., "body": .., "manual_anchor": ..}}``。
        student audience の節のみを参照する（audience 越境は構造的に禁止 IH1/IH9）。
    """
    idx, _excluded = _manual._student_index()
    out: dict[str, dict] = {}
    for anchor_id, ref in UI_ANCHORS.items():
        audience, file, anchor = _split_ref(ref)
        if audience != "student":
            continue
        sec = idx.get(f"{file}#{anchor}")
        if sec is None:
            continue
        out[anchor_id] = {
            "title": sec.get("title", ""),
            "body": sec.get("body", ""),
            "manual_anchor": ref,
        }
    return out


def resolve_ui_anchor(anchor_id: str) -> Optional[dict]:
    """単一の UI アンカーを解決する（usage_help ハンドラの ui_anchor 優先解決用）。

    ``UI_ANCHORS`` に無い、または解決できないアンカーIDは ``None``
    （呼び出し側は従来の keyword 検索へフォールバックする、設計 §9-1）。
    """
    if anchor_id not in UI_ANCHORS:
        return None
    return resolve_ui_anchors().get(anchor_id)
