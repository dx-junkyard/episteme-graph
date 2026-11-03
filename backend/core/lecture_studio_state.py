"""原稿スタジオが「コース内容生成の前後で何が揃っているか」を 1 つの値で持つための純関数群。

第 25 周 面 D 第 1 周（2026-10-23）の是正の正本:

- IK-0875: 根拠の状態（中央の見出し・一覧の見出し・書き換えの事前判定）を 1 つの値で返す。
  材料が何も無いときに「根拠候補はあります」と言わない（既定を「ある」側にしない）。
- IK-0876: トピックの論文（つまづき・図スタジオが読む document）をトピックの材料から決める。
- IK-0874: AI 書き換えの根拠に ``topic.units`` を解決した中身を足し、根拠が全部空なら LLM を
  呼ばない（事実文だけ返す）。応答が下書きを変えなかったことを判定する。
- IK-0877: 保存の副作用（トピック音声を消す）の事実文。

FastAPI / sqlalchemy / LLM を import しない（DB の読みは呼び出し側が渡す）。画面に出す固定文は
ここが正本で、フロントは返ってきた文を描くだけ（JS にミラーしない）。数値は返さない。
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 固定文（サーバ側の正本）
# ---------------------------------------------------------------------------

#: 根拠の状態 → 見出しの短いラベル（教員向け。英語の内部語を pill に出さない）。
EVIDENCE_STATE_LABELS: dict[str, str] = {
    "sufficient": "根拠あり",
    "weak": "要確認",
    "missing": "対応なし",
    "none": "根拠なし",
}

#: 根拠の状態 → 区画の事実文。
EVIDENCE_STATE_MESSAGES: dict[str, str] = {
    "sufficient": "授業用ドラフトを支える根拠候補があります。",
    "weak": "根拠候補はありますが、授業用ドラフトとして使う前に教員確認が必要です。",
    "missing": (
        "このトピックに対応する主張または部品の対応付けが見つかっていません。"
        "本文説明を自動生成するには確認が必要です。"
    ),
    "none": "根拠候補はありません（コース内容生成の前か、対応が見つかっていません）。",
}

#: 学ぶ単位は選ばれているが、コース内容生成の前のトピック。
EVIDENCE_UNITS_ONLY_MESSAGE = (
    "学ぶ単位は選ばれていますが、コース内容生成の前です。"
    "[コース内容生成] を行うと、単位から主張・式・図の根拠が紐づきます。"
)

#: 「対応なし」のトピックで一覧に並ぶ候補（同じ節から拾った式など）の見出し。
#: 「対応付けられていません」の直後に式が並んで矛盾に見えるのを避ける（IK-0875）。
EVIDENCE_UNMAPPED_HEADING = "対応付けられていない候補（同じ節から拾った式・根拠）"

#: 教員が加えた資料（教材図スタジオで採用した図など）の見出し（第 25 周 第 5 波・IK-1010）。
#: 解析が拾った候補と同じ区画に混ぜず、教材欄に埋め込んだ順に先頭へ並べる。
#: 解析の対応付けの状態（``status``）には数えない（判定は変えない）。
EVIDENCE_TEACHER_ADDED_HEADING = "教員が加えた資料（教材欄に埋め込んだ順）"

#: 教員が加えた資料と並ぶ、論文の解析から拾った候補の区画の見出し（第 25 周 第 6 波・IK-1010 の残り）。
#: 根拠の状態が missing 以外（unmapped_heading が出ない）でも、教員の区画があれば解析の区画に見出しを付ける。
EVIDENCE_ANALYSIS_HEADING = "論文の解析から拾った根拠（式・主張・原文抜粋・概要）"

#: AI 書き換えの根拠が全部空のとき（LLM を呼ばず、上限も消費しない — IK-0874）。
REWRITE_NO_GROUNDING_NOTICE = (
    "このトピックにはまだ根拠の材料がありません（学ぶ単位も、コース内容生成の結果もありません）。"
    "先にコースビルダーで学ぶ単位を選ぶか、[コース内容生成] を行ってから書き換えてください。"
)

#: AI 書き換えの応答が下書きを変えなかったとき（IK-0874）。
REWRITE_UNCHANGED_NOTICE = "提案による変更はありませんでした（注意点を見てください）。"

#: AI 書き換えの応答を反映したとき。
REWRITE_APPLIED_NOTICE = "提案を反映しました。保存すると確定します。"

#: 保存できない閲覧者（editor 共有）が AI 書き換えを試したとき（第 25 周 第 3 波・IK-0950）。
#: 既定の「保存すると確定します」に継ぎ足さず、この文で置き換える（成功と不成立を同じ行に並べない）。
REWRITE_PROPOSAL_UNSAVABLE_NOTICE = (
    "案を編集欄に入れました。このコースではトピックを保存できないため、案は保存されません。"
    "［案をコピー］で写して、所有者に渡せます。"
)

#: 保存できない閲覧者に原稿スタジオのコースの見出しで出す印（IK-0950。役割の語の正本は
#: label_vocab.COURSE_ROLE_LABELS）。
def studio_share_mark() -> str:
    from core.label_vocab import COURSE_ROLE_LABELS

    return f"〔{COURSE_ROLE_LABELS['editor']}〕"


#: 共有教員が［案をコピー］で写したときの知らせ（第 25 周 第 4 波・IK-0978）。``{sections}`` は
#: 写した区画の名前（画面が並べる）・``{recipient}`` は渡す相手（所有者の表示名があれば名前入り）。
PROPOSAL_COPY_NOTICE = "{sections}をクリップボードに写しました。{recipient}に渡してください。"
PROPOSAL_COPY_RECIPIENT = "所有者"
PROPOSAL_COPY_RECIPIENT_NAMED = "所有者（{name}）"
#: 共有教員の原稿スタジオの見出しに出す所有者（IK-0978。オーナー判断 §6-1 = 見せる・教員間だけ）。
COURSE_OWNER_NOTICE = "このコースの所有者: {name}"


def proposal_copy_notice(owner_display_name: str | None) -> str:
    """［案をコピー］の知らせ（``{sections}`` を残した形）。名前が無ければ「所有者」とだけ言う。"""
    name = str(owner_display_name or "").strip()
    recipient = (
        PROPOSAL_COPY_RECIPIENT_NAMED.replace("{name}", name) if name else PROPOSAL_COPY_RECIPIENT
    )
    return PROPOSAL_COPY_NOTICE.replace("{recipient}", recipient)


#: 保存でこのトピックの生成済み音声を消したとき（IK-0877）。
TOPIC_AUDIO_INVALIDATED_NOTICE = (
    "このトピックの生成済み音声を消しました。次の音声生成で作り直されます。"
)

#: 音声のあるトピックで保存ボタンの近くに常に出す事実文（IK-0877。採用直後の告知と同じ趣旨）。
TOPIC_AUDIO_SAVE_NOTICE = "保存すると、このトピックの生成済み音声は作り直しになります。"

#: トピックの論文が決まらないとき（つまづき・図スタジオ — IK-0876）。
TOPIC_DOCUMENT_UNRESOLVED_NOTICE = (
    "このトピックの論文が決まっていません（コース内容生成の前か、根拠の対応がありません）。"
)

#: コース状態（トピック教材の経路）— 音声の揃い方の事実文（IK-0877。件数を出さない）。
TOPIC_AUDIO_STATUS_MESSAGES: dict[str, str] = {
    "none": "このコースのトピック音声はまだ生成されていません。",
    "partial": "音声が生成されたトピックと、まだのトピックがあります。",
    "all": "すべてのトピックに音声があります。",
    "no_topics": "音声を生成できるトピック教材がまだありません。",
}


#: 閲覧者がトピックを保存できないとき（IK-0908。editor 共有の教員 — 保存は所有者だけ）。
#: 保存ボタンの近くと AI 書き換えの近くに出す。
TOPIC_SAVE_OWNER_ONLY_NOTICE = (
    "このコースのトピックの保存は、コースの所有者だけができます。"
    "AI の書き換えは試せますが、結果は保存されません。変更は所有者に依頼してください。"
)

#: 保存の 403 の detail（IK-0908。画面は detail をそのまま描く）。
TOPIC_SAVE_FORBIDDEN_DETAIL = (
    "このコースのトピックの保存は、コースの所有者だけができます。変更は所有者に依頼してください。"
)

#: 保存したトピックがいつ受講者に届くか（IK-0915。正本の条件は V層の版解決
#: ``services._apply_course_version_view`` — 版が無ければ live、版があれば版の写し）。
TOPIC_DELIVERY_MESSAGES: dict[str, str] = {
    "immediate": "このコースは版を発行していないので、保存した内容はすぐ受講者に届きます。",
    "release": (
        "受講者は発行済みの版（版 {version}）を見ています。"
        "保存した内容を受講者に届けるには、新しい版を発行してください。"
    ),
    # 第 25 周 第 8 波（IK-1070）: いまの内容が版と同じとき（未発行の変更なし）は指示文を出さない。
    # 指示文と事実文を同じ値（unpublished_changes）から作る。
    "release_current": "受講者は発行済みの版（版 {version}）を見ています。",
}

#: 第 25 周 第 7 波（IK-1053・オーナー判断: 発行は所有者の明示操作のまま）: 版ありのコースで、いまの
#: 内容（HEAD）が受講者の見ている版と違うかの事実文。督促・自動発行はしない（事実と入口だけ）。
TOPIC_UNPUBLISHED_CHANGES_NOTES: dict[bool, str] = {
    True: "版 {version} の発行の後に保存した変更があります。受講者はまだ版 {version} を見ています。",
    False: "いまの内容は版 {version} と同じです。発行は要りません。",
}
#: 版の管理（V層 ``Versioning.openModal``）への入口の札。発行は所有者だけ（V層の確定仕様）。
TOPIC_VERSIONS_ENTRY_LABEL = "版の管理を開く"


def course_has_unpublished_changes(release_snapshot: Any, live_snapshot: Any) -> bool | None:
    """受講者の見ている版の写しと、いまの working copy（HEAD）が違うか（IK-1053）。

    比べるのは V層の写しの形（``{title, description, data}``）そのもの。どちらかが読めなければ
    None（言わない — 推測で「変更あり / なし」を作らない）。
    """
    if not isinstance(release_snapshot, dict) or not isinstance(live_snapshot, dict):
        return None
    import json

    def _canon(snap: dict) -> str:
        return json.dumps(
            {k: snap.get(k) for k in ("title", "description", "data")},
            ensure_ascii=False, sort_keys=True, default=str,
        )

    return _canon(release_snapshot) != _canon(live_snapshot)


def course_release_comparison(course_id: str) -> tuple[int | None, bool | None]:
    """コースの受講者の版の番号と、HEAD がその版と違うか（``(版番号, 未発行の変更)``・IK-1053 / IK-1079）。

    版が無ければ ``(None, None)``、写しが読めなければ ``(版番号, None)``（比べられない — 事実文を
    出さない）。原稿スタジオの保存の届き方と V層の版の管理モーダル（``version-state``）が同じ
    比較を通す（未発行の変更の事実の正本を 1 本にする）。読み取りのみ・fail-soft。
    """
    release_version_no: int | None = None
    unpublished_changes: bool | None = None
    try:
        from core.versioning import releases as _releases  # 遅延 import（core の推移的純粋性）

        state = _releases.get_state("course", course_id) or {}
        if state.get("active_release_id"):
            release_version_no = int(state.get("latest_version_no") or 0) or None
            try:
                release = _releases.get_release(str(state["active_release_id"])) or {}
                unpublished_changes = course_has_unpublished_changes(
                    release.get("snapshot"), _releases.build_course_snapshot(course_id)
                )
            except Exception:  # noqa: BLE001 — 写しが読めなければ比べない
                unpublished_changes = None
    except Exception:  # noqa: BLE001 — 版の状態が読めなければ版なし扱い
        return None, None
    return release_version_no, unpublished_changes


def changed_topic_titles(release_snapshot: Any, live_snapshot: Any) -> list[str] | None:
    """受講者の版の写しといまの内容（HEAD）で、中身の違うトピックの題名（いまの題名・コース順）。

    第 25 周 第 10 波（studio 頭脳の所見）: 発行の前に「何が変わったか」を版の管理で言う。
    追加されたトピックはいまの題名、版にだけあるトピックは版の題名を末尾に並べる。件数は出さない。
    どちらかが読めなければ None（言わない）。純関数・決定論。
    """
    if not isinstance(release_snapshot, dict) or not isinstance(live_snapshot, dict):
        return None
    import json

    from core.course_data import iter_all_topics

    def _topics(snap: dict) -> list[dict]:
        data = snap.get("data") if isinstance(snap.get("data"), dict) else {}
        return [t for t in iter_all_topics(data) if isinstance(t, dict)]

    def _canon(topic: dict) -> str:
        return json.dumps(topic, ensure_ascii=False, sort_keys=True, default=str)

    before = {str(t.get("id") or ""): t for t in _topics(release_snapshot)}
    titles: list[str] = []
    seen: set[str] = set()
    for topic in _topics(live_snapshot):
        tid = str(topic.get("id") or "")
        seen.add(tid)
        old = before.get(tid)
        if old is None or _canon(old) != _canon(topic):
            title = str(topic.get("title") or "").strip()
            if title and title not in titles:
                titles.append(title)
    for tid, old in before.items():
        if tid not in seen:
            title = str(old.get("title") or "").strip()
            if title and title not in titles:
                titles.append(title)
    return titles


def course_release_changed_topics(course_id: str) -> list[str] | None:
    """受講者の版と HEAD で中身の違うトピックの題名（読めなければ None・fail-soft・読み取りのみ）。"""
    try:
        from core.versioning import releases as _releases  # 遅延 import

        state = _releases.get_state("course", course_id) or {}
        if not state.get("active_release_id"):
            return None
        release = _releases.get_release(str(state["active_release_id"])) or {}
        return changed_topic_titles(release.get("snapshot"), _releases.build_course_snapshot(course_id))
    except Exception:  # noqa: BLE001
        return None


def version_state_course_facts(course_id: str, *, is_owner: bool) -> dict:
    """版の管理（``version-state``）のコースの事実文（IK-1079 / IK-1127・宛先ごと）。

    - 全員: ``audience_note``（発行した版が受講者・閲覧の共有先・編集の共有先にどう届くか）。
    - 所有者: 未発行の変更の事実文（IK-1079）+ 受講者の見ている版の文（``learner_version_note`` =
      原稿スタジオのトピックの区画と同じ ``release_current``）+ 発行の後に変わったトピックの題名。
    """
    from core import label_vocab

    out: dict = {"audience_note": label_vocab.VERSION_AUDIENCE_NOTE_COURSE}
    if not is_owner:
        return out
    version, unpublished = course_release_comparison(course_id)
    out.update(unpublished_changes_facts(version, unpublished))
    try:
        version_no = int(version or 0)
    except (TypeError, ValueError):
        version_no = 0
    if version_no > 0:
        out["learner_version_note"] = TOPIC_DELIVERY_MESSAGES["release_current"].format(version=version_no)
    if unpublished:
        titles = course_release_changed_topics(course_id)
        if titles:
            out["changed_topics_label"] = label_vocab.VERSION_CHANGED_TOPICS_LABEL
            out["changed_topic_titles"] = titles
    return out


def unpublished_changes_facts(release_version_no: int | None, unpublished_changes: bool | None) -> dict:
    """未発行の変更の事実文（``{unpublished_changes, unpublished_changes_note}``・比べられなければ空）。"""
    try:
        version = int(release_version_no or 0)
    except (TypeError, ValueError):
        version = 0
    if version <= 0 or unpublished_changes is None:
        return {}
    return {
        "unpublished_changes": bool(unpublished_changes),
        "unpublished_changes_note": TOPIC_UNPUBLISHED_CHANGES_NOTES[bool(unpublished_changes)].format(version=version),
    }


#: 受講者に届く経路の無いコース（非公開・版なし）で、保存しても受講者に見えないこと（IK-0979）。
TOPIC_DELIVERY_NONE_NOTICE = "このコースは受講者に公開されていないため、保存しても受講者には見えません。"

#: 教材図スタジオで図を採用（または既存の図を挿入）した直後の知らせの先頭（IK-0979）。
FIGURE_ADOPT_LEAD_NOTICE = "図を教材欄に入れました。トピックを保存すると確定します。"


def figure_adopt_notice_parts(delivery: dict | None) -> dict:
    """図の採用の知らせの部品（IK-0979。保存側と同じ値・同じ文の正本から組む）。

    - ``lead``: 先頭の事実文（常に出す）。
    - ``audio``: 保存で音声が作り直しになる文（画面はトピックに音声があるときだけ添える）。
    - ``delivery``: 保存がいつ受講者に届くか（``topic_save_facts`` の delivery と同じ文。
      届く経路が無いコースでは「保存しても受講者には見えません」）。
    """
    message = str((delivery or {}).get("message") or "").strip()
    return {
        "lead": FIGURE_ADOPT_LEAD_NOTICE,
        "audio": TOPIC_AUDIO_SAVE_NOTICE,
        "delivery": message or TOPIC_DELIVERY_NONE_NOTICE,
    }


#: 決定論付録（「この節で使う数式」「この節で参照する図」）が、いまの根拠から作る形と違うとき
#: （IK-0872 / IK-0880 の残り。保存済みの付録は読み時に付け直さない — 教員の明示操作で付け直す）。
APPENDIX_STALE_NOTICE = (
    "このトピックの末尾の付録（この節で使う数式・参照する図）は、いまの根拠から作る形と違います。"
    "[付録を付け直す] で作り直せます。"
)
#: 付け直しの前の確認（音声とスライドの境界が動くことを事前に言う）。
APPENDIX_REATTACH_CONFIRM = (
    "末尾の付録を、いまのトピックの根拠から作り直して編集欄に入れます。"
    "保存するまで受講者には届きません。保存すると、このトピックの生成済み音声は作り直しになり、"
    "スライドの区切りが変わることがあります。"
)
APPENDIX_REATTACH_APPLIED_NOTICE = "付録を付け直して編集欄に入れました。保存すると確定します。"
APPENDIX_REATTACH_UNCHANGED_NOTICE = "付録はいまの根拠から作る形と同じです（変更はありません）。"
#: 第 25 周 第 6 波（IK-1032）: 付け直しの結果の分岐ごとの知らせ（固定文 1 つで言わない）。
APPENDIX_REATTACH_REMOVED_NOTICE = (
    "いまの根拠からは付録に載せる式・図が無いため、付録を外しました。保存すると確定します。"
)
APPENDIX_REATTACH_ADDED_NOTICE = "いまの根拠から付録を作り、編集欄の末尾に入れました。保存すると確定します。"

#: 付け直しの結果の語彙（``course_content_builder.reattach_generated_appendix`` の ``outcome``）。
APPENDIX_REATTACH_OUTCOMES = ("replaced", "removed", "added", "unchanged")


def appendix_reattach_notice(outcome: str) -> str:
    """付け直しの結果の語（``APPENDIX_REATTACH_OUTCOMES``）に対応する知らせ（IK-1032）。"""
    return {
        "removed": APPENDIX_REATTACH_REMOVED_NOTICE,
        "added": APPENDIX_REATTACH_ADDED_NOTICE,
        "unchanged": APPENDIX_REATTACH_UNCHANGED_NOTICE,
    }.get(str(outcome or ""), APPENDIX_REATTACH_APPLIED_NOTICE)


#: 第 25 周 第 6 波（オーナー判断 1）: 保存済みの付録はシステムが書き直さない。所有者への道案内は
#: 原稿スタジオのコース見出しの事実文 + 一覧の題名 + ［付録をまとめて付け直す］（所有者の明示操作）。
APPENDIX_STALE_COURSE_NOTICE = (
    "付録が古いトピックがあります（下の一覧）。"
    "末尾の付録（この節で使う数式・参照する図）が、いまの根拠から作る形と違います。"
    "保存済みの付録はシステムが書き直さないため、付け直すまで受講者には保存済みの付録が届きます。"
)
#: 第 25 周 第 7 波（IK-1054）: 対象のトピックは文に連結せず、折りたたみの一覧（1 行 1 題名）で出す。
APPENDIX_STALE_LIST_SUMMARY = "付録が古いトピックの一覧"
APPENDIX_REATTACH_ALL_CONFIRM = (
    "一覧のトピックの末尾の付録を、いまの根拠から作り直して保存します。"
    "付け直したトピックの生成済み音声は作り直しになり、スライドの区切りが変わることがあります。"
    "付録の他の本文は変えません。"
)
APPENDIX_REATTACH_ALL_OWNER_ONLY_NOTICE = (
    "付録のまとめての付け直しは、トピックの保存と同じくコースの所有者だけができます。"
)
APPENDIX_REATTACH_ALL_DONE_NOTICE = "付録を付け直して保存しました: {titles}"
APPENDIX_REATTACH_ALL_NONE_NOTICE = "付け直しが必要なトピックはありませんでした（変更はありません）。"
#: 第 25 周 第 9 波（IK-1080）: まとめての知らせも結果の分岐ごとに段を分けて言う（1 件の経路の
#: ``appendix_reattach_notice`` と同じ分岐の語彙。件数は出さず題名を列挙する）。
APPENDIX_REATTACH_ALL_LEAD = "付録を付け直して保存しました。"
APPENDIX_REATTACH_ALL_OUTCOME_LABELS: dict[str, str] = {
    "removed": "付録を外したトピック（いまの根拠からは付録に載せる式・図がありません）",
    "replaced": "付録を作り直したトピック",
    "added": "付録を新しく付けたトピック",
}


def appendix_reattach_all_outcome_groups(items: list[tuple[str, str]]) -> list[dict]:
    """``[(outcome, title), ...]`` を分岐ごとの段 ``[{outcome, label, titles}]`` にまとめる（IK-1080）。

    段の順は外した → 作り直した → 新しく付けた（受講者の見る付録が消えるものを先に言う）。
    ``unchanged`` は段にしない（保存していない）。語彙外の結果は「作り直した」に数える
    （``appendix_reattach_notice`` の既定と同じ）。
    """
    titles_by: dict[str, list[str]] = {}
    for outcome, title in items or []:
        key = str(outcome or "")
        if key == "unchanged":
            continue
        if key not in APPENDIX_REATTACH_ALL_OUTCOME_LABELS:
            key = "replaced"
        name = str(title or "").strip()
        if name:
            titles_by.setdefault(key, []).append(name)
    return [
        {"outcome": key, "label": APPENDIX_REATTACH_ALL_OUTCOME_LABELS[key], "titles": titles_by[key]}
        for key in ("removed", "replaced", "added")
        if titles_by.get(key)
    ]


def appendix_reattach_all_notice(items: list[tuple[str, str]]) -> str:
    """まとめての付け直しの知らせ（分岐ごとの段を 1 文ずつ。何も保存していなければ「無し」の文）。"""
    groups = appendix_reattach_all_outcome_groups(items)
    if not groups:
        return APPENDIX_REATTACH_ALL_NONE_NOTICE
    return APPENDIX_REATTACH_ALL_LEAD + "".join(
        f" {group['label']}: {' / '.join(group['titles'])}。" for group in groups
    )

#: 第 25 周 第 6 波（オーナー判断 2）: 音声生成はトピックの保存と同じく所有者だけ。
TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE = (
    "このコースの音声生成は、トピックの保存と同じくコースの所有者だけができます。"
    "音声の作り直しは所有者に依頼してください。"
)
TOPIC_AUDIO_GENERATE_FORBIDDEN_DETAIL = TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE

#: 第 25 周 第 8 波（IK-1071）: 音声を頼む相手を名前で言う（名前が引けなければ従来の文）。
TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE_NAMED = (
    "このコースの音声生成は、トピックの保存と同じくコースの所有者だけができます。"
    "音声の作り直しは所有者（{name}）に依頼してください。"
)
#: 音声の依頼の文を写すボタンの札と、写す文の部品（［案をコピー］と同じ受け渡し — 教員間の
#: 通知経路は作らない。数値は載せない）。
AUDIO_REQUEST_COPY_LABEL = "依頼の文をコピー"
AUDIO_REQUEST_LINE = "このコースの音声の生成（作り直し）をお願いします。"
AUDIO_REQUEST_SECTION_LABELS: dict[str, str] = {
    "course": "コース",
    "request": "お願い",
    "language": "読み上げの言語",
    "audio_state": "音声の状態",
    "topics": "音声の無いトピック",
}
AUDIO_REQUEST_LANGUAGE_LABELS: dict[str, str] = {"ja": "日本語", "en": "英語"}
AUDIO_REQUEST_COPY_NOTICE = "音声の依頼の文をクリップボードに写しました。{recipient}に渡してください。"


def audio_generate_owner_only_notice(owner_display_name: str | None) -> str:
    """音声生成が所有者だけであることの文（IK-1071。名前があれば頼む相手を名前で言う）。"""
    name = str(owner_display_name or "").strip()
    if not name:
        return TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE
    return TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE_NAMED.replace("{name}", name)


def audio_request_facts(owner_display_name: str | None) -> dict:
    """共有教員が所有者に音声を頼むときの受け渡しの部品（IK-1071）。

    宛先（所有者の表示名）・渡す中身の見出しと依頼の 1 文・写した後の知らせを返す。中身の値
    （コース名・言語・音声の状態・音声の無いトピック）は画面がいま持っている course-structure から
    組む（サーバは語だけを持つ — 文の正本はここ・JS に日本語の表を置かない）。
    """
    name = str(owner_display_name or "").strip()
    recipient = (
        PROPOSAL_COPY_RECIPIENT_NAMED.replace("{name}", name) if name else PROPOSAL_COPY_RECIPIENT
    )
    return {
        "label": AUDIO_REQUEST_COPY_LABEL,
        "recipient": recipient,
        "request_line": AUDIO_REQUEST_LINE,
        "section_labels": dict(AUDIO_REQUEST_SECTION_LABELS),
        "language_labels": dict(AUDIO_REQUEST_LANGUAGE_LABELS),
        "copy_notice": AUDIO_REQUEST_COPY_NOTICE.replace("{recipient}", recipient),
    }

#: 書き換えの根拠を予算に収めるために一部を省いたとき（IK-0909。何を省いたかを言う）。
REWRITE_GROUNDING_TRIMMED_NOTICE = (
    "根拠の材料が長いため、一部を省いて AI に渡しました（省いたもの: {keys}）。学ぶ単位は省いていません。"
)
#: 省いた材料の画面の語（IK-0909。キー名を画面に出さない）。
REWRITE_GROUNDING_KEY_LABELS: dict[str, str] = {
    "units": "学ぶ単位",
    "summary": "トピックの概要",
    "source_excerpt": "原文の抜粋",
    "figures": "図",
    "evidence_links": "根拠リンク",
    "content_blocks": "式・部品の一覧",
    "linked_component_ids": "部品の番地",
    "linked_equation_ids": "式の番地",
    "source_evidence_ids": "根拠の番地",
}


# ---------------------------------------------------------------------------
# 根拠の状態（IK-0875）
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _real_evidence_items(evidence_items: Iterable[dict] | None) -> list[dict]:
    """「トピック概要」（topic.summary を指す source）と、教員が作った説明図（IK-0949）は
    根拠の有無に数えない（論文由来の根拠ではない）。"""
    return [
        item for item in (evidence_items or [])
        if isinstance(item, dict)
        and str(item.get("role") or "") not in ("summary", "teaching_figure")
        and not item.get("teaching")
    ]


def topic_has_units(topic: dict | None) -> bool:
    return any(isinstance(u, dict) and (u.get("stable_key") or u.get("unit_id")) for u in _as_list((topic or {}).get("units")))


def topic_evidence_state(topic: dict | None, evidence_items: Iterable[dict] | None) -> dict:
    """トピックの根拠の状態を 1 つの値で返す（中央・一覧・右ペイン・書き換えが同じ値を読む）。

    戻り値 ``{status, label, message, unmapped_heading?}``。``status`` は
    ``sufficient`` / ``weak`` / ``missing`` / ``none``。保存済みの ``topic.coverage`` が
    あればそれを優先する（freeze の判定）。何も無いときの既定は ``none``（「ある」と言わない）。
    """
    topic = topic or {}
    coverage = topic.get("coverage") if isinstance(topic.get("coverage"), dict) else {}
    confidence = str(topic.get("content_confidence") or "")
    real = _real_evidence_items(evidence_items)
    message = ""
    status = str(coverage.get("status") or "")
    if status:
        message = str(coverage.get("message") or "")
    elif topic.get("content_source") == "source_excerpt" and confidence == "none":
        status = "missing"
    elif confidence == "none":
        status = "weak"
    elif confidence:
        status = "sufficient"
    elif _as_list(topic.get("linked_component_ids")):
        status = "sufficient"
    elif real:
        status = "weak"
    elif topic_has_units(topic):
        status = "weak"
        message = EVIDENCE_UNITS_ONLY_MESSAGE
    else:
        status = "none"
    if status not in EVIDENCE_STATE_MESSAGES:
        status = "weak" if real else "none"
    out = {
        "status": status,
        "label": EVIDENCE_STATE_LABELS[status],
        "message": message or EVIDENCE_STATE_MESSAGES[status],
    }
    if status == "missing" and real:
        out["unmapped_heading"] = EVIDENCE_UNMAPPED_HEADING
    # IK-1010: 教員が加えた資料があれば、その区画の見出しを別に返す（解析の候補の見出しを
    # 教員の図に掛けない）。
    if any(_is_teacher_added_item(item) for item in (evidence_items or [])):
        out["teacher_added_heading"] = EVIDENCE_TEACHER_ADDED_HEADING
        # 第 6 波（IK-1010 の残り）: 根拠ありのトピックでも、解析の候補を見出しで教員の資料と区別する
        # （missing のときは unmapped_heading がその役）。
        if "unmapped_heading" not in out and any(
            not _is_teacher_added_item(item) for item in (evidence_items or [])
            if isinstance(item, dict)
        ):
            out["analysis_heading"] = EVIDENCE_ANALYSIS_HEADING
    return out


# ---------------------------------------------------------------------------
# 書き換えの申し送りの根拠（第 25 周 第 5 波・IK-1011）
# ---------------------------------------------------------------------------

#: 申し送りが外した・取り下げた理由に、教員の画面で読める箇所の引用を添えていないとき、
#: その項目の末尾に付ける事実文（項目は落とさない — 原則3）。
HANDOFF_UNVERIFIED_BASIS_NOTE = "（根拠の表示なし: 教員の画面で読める箇所の引用がありません）"

_HANDOFF_QUOTE_RE = re.compile(r"「([^「」]{4,240})」")
#: 人の書いた記述（注意点・本文）を外す・取り下げることを言う語。
_HANDOFF_REMOVAL_RE = re.compile(r"取り下げ|取りやめ|外し|外した|削除|削っ|除い|除外|省い|省き")


def _visible_key(text: Any) -> str:
    """引用と画面の文を照らすための正規化（NFKC・空白除去。意味の比較ではない）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def teacher_visible_texts(
    topic: dict | None,
    draft: dict | None,
    evidence_items: Iterable[dict] | None,
) -> list[str]:
    """書き換えの時点で教員の原稿スタジオに出ている文（申し送りの根拠の照合先・IK-1011）。

    前の版の下書き（教材欄・読み上げ・注意点・確認問題）・トピックの題名と概要・右ペインの
    根拠の一覧（``build_topic_evidence_items`` の見出し・要約・式・図題）。grounding にだけ
    渡す材料（論理ブロックの要約・content_blocks の内部の欄）は含めない。
    """
    texts: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            texts.append(value)
        elif isinstance(value, list):
            for v in value:
                _add(v)
        elif isinstance(value, dict):
            for v in value.values():
                _add(v)

    topic = topic or {}
    _add(topic.get("title"))
    _add(topic.get("summary"))
    _add(draft or {})
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        for key in ("title", "summary", "caption", "latex", "plain_text", "raw_text", "section_label"):
            _add(item.get(key))
    return texts


def qualify_handoff_basis(notes: Iterable[Any], visible_texts: Iterable[str]) -> list[str]:
    """申し送りの各項目の根拠が教員の画面で確かめられるかを決定論で検査する（IK-1011）。

    - 「…」の引用が教員の画面の文に（正規化後の部分一致で）無い項目 → 末尾に
      :data:`HANDOFF_UNVERIFIED_BASIS_NOTE`。
    - 人の書いた記述を外す・取り下げると言う項目で、画面の文と照合できる引用が 1 つも無い
      項目 → 同じく末尾に事実文。
    項目は落とさない（取り下げの提案そのものは教員が判断する）。
    """
    haystack = "\n".join(_visible_key(t) for t in visible_texts or [] if t)
    out: list[str] = []
    for raw in notes or []:
        note = str(raw or "").strip()
        if not note:
            continue
        if note.endswith(HANDOFF_UNVERIFIED_BASIS_NOTE):
            out.append(note)
            continue
        quotes = [q for q in _HANDOFF_QUOTE_RE.findall(note) if _visible_key(q)]
        verified = [q for q in quotes if _visible_key(q) in haystack]
        unverified_quote = len(verified) < len(quotes)
        removal_without_basis = bool(_HANDOFF_REMOVAL_RE.search(note)) and not verified
        if unverified_quote or removal_without_basis:
            note = note + HANDOFF_UNVERIFIED_BASIS_NOTE
        out.append(note)
    return out


def _is_teacher_added_item(item: Any) -> bool:
    """教員が加えた根拠の項目か（教材図スタジオの図 = ``role == "teaching_figure"`` / ``teaching``）。"""
    return isinstance(item, dict) and (
        str(item.get("role") or "") == "teaching_figure" or bool(item.get("teaching"))
    )


# ---------------------------------------------------------------------------
# トピックの論文（IK-0876）
# ---------------------------------------------------------------------------


def topic_document_ids(
    topic: dict | None,
    *,
    course_document_ids: Iterable[str] = (),
    unit_rows_by_key: dict[str, dict] | None = None,
    chunk_document_by_id: dict[str, str] | None = None,
) -> list[str]:
    """トピックの論文（document.id）を、トピックの材料から順序付きで決める。

    供給源は ①学ぶ単位（``topic.units`` → live 行の document）②content_blocks の部品の
    document ③教材チャンク（``material_chunk_ids`` / ``linked_chunk_ids`` → chunks の document）。
    どれも無く、コースの論文がちょうど 1 本ならその 1 本（推測ではなくコースの事実）。
    コースの論文の集合が分かっているときは、その外の document を返さない（範囲を広げない）。
    """
    topic = topic or {}
    course_ids = [str(d) for d in course_document_ids or [] if str(d or "").strip()]
    allowed = set(course_ids)
    out: list[str] = []

    def _add(value: Any) -> None:
        doc = str(value or "").strip()
        if not doc or doc in out:
            return
        if allowed and doc not in allowed:
            return
        out.append(doc)

    rows = unit_rows_by_key or {}
    for unit in _as_list(topic.get("units")):
        if not isinstance(unit, dict):
            continue
        row = rows.get(str(unit.get("stable_key") or ""))
        if isinstance(row, dict):
            _add(row.get("document_id"))
    for block in _as_list(topic.get("content_blocks")):
        if isinstance(block, dict) and block.get("type") == "components":
            for item in _as_list(block.get("items")):
                if isinstance(item, dict):
                    _add(item.get("document_id"))
    chunk_docs = chunk_document_by_id or {}
    for key in ("material_chunk_ids", "linked_chunk_ids"):
        for chunk_id in _as_list(topic.get(key)):
            _add(chunk_docs.get(str(chunk_id)))
    if not out and len(course_ids) == 1:
        out.append(course_ids[0])
    return out


def topic_chunk_ids(topic: dict | None) -> list[str]:
    topic = topic or {}
    ids: list[str] = []
    for key in ("material_chunk_ids", "linked_chunk_ids"):
        for chunk_id in _as_list(topic.get(key)):
            text = str(chunk_id or "").strip()
            if text and text not in ids:
                ids.append(text)
    return ids


# ---------------------------------------------------------------------------
# AI 書き換えの根拠（IK-0874）
# ---------------------------------------------------------------------------

#: 単位の要約の切り詰め（プロンプトの予算）。
_UNIT_SUMMARY_LIMIT = 400


def unit_grounding_for_prompt(topic: dict | None, unit_rows_by_key: dict[str, dict] | None) -> list[dict]:
    """``topic.units`` を live 行で解決した中身（種別・名前・要約）を書き換えの根拠に載せる形へ。

    ``unit_id`` / ``stable_key`` / document_id は載せない（モデルが本文に写す番地を渡さない）。
    live 行が無い単位は名前だけ（教員が選んだ事実は残す）。
    """
    from core.course_units import unit_kind_label  # 循環 import を避ける（純関数のみ）

    rows = unit_rows_by_key or {}
    out: list[dict] = []
    for unit in _as_list((topic or {}).get("units")):
        if not isinstance(unit, dict):
            continue
        row = rows.get(str(unit.get("stable_key") or "")) or {}
        label = str(row.get("label") or unit.get("label") or "").strip()
        if not label:
            continue
        entry = {"kind": unit_kind_label(str(row.get("unit_kind") or unit.get("kind") or "")), "label": label}
        summary = str(row.get("summary") or "").strip()
        if summary:
            entry["summary"] = summary[:_UNIT_SUMMARY_LIMIT]
        out.append(entry)
    return out


def rewrite_grounding_is_empty(evidence: dict) -> bool:
    """書き換えに渡す根拠の辞書が全部空か（空なら LLM を呼ばない）。

    ``coverage`` / ``content_confidence`` は状態の記録であって材料ではないので数えない。
    """
    for key, value in (evidence or {}).items():
        if key in ("coverage", "content_confidence"):
            continue
        if isinstance(value, str) and value.strip():
            return False
        if isinstance(value, (list, dict)) and value:
            return False
    return True


def draft_has_body(draft: dict | None) -> bool:
    """下書きに見出し以外の本文（教材の地の文・読み上げ原稿）があるか。

    根拠が空でも、教員が書いた本文がある下書きの書き換え（言い直し・短縮）は成り立つ。
    見出しだけの下書き（登録直後）を空の根拠で書き換えると、AI が根拠の不足を言うだけになる。
    """
    draft = draft or {}
    material = draft.get("student_material") if isinstance(draft.get("student_material"), dict) else {}
    for line in str(material.get("source_text") or "").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return True
    return bool(str(draft.get("spoken_script") or "").strip())


def _draft_signature(draft: dict) -> str:
    material = draft.get("student_material") if isinstance(draft.get("student_material"), dict) else {}

    def _lines(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v.get("question") if isinstance(v, dict) else v).strip() for v in value if v]
        return [line.strip() for line in str(value or "").splitlines() if line.strip()]

    return json.dumps({
        "key_concepts": _lines(draft.get("key_concepts")),
        "material": str(material.get("source_text") or "").strip(),
        "spoken": str(draft.get("spoken_script") or "").strip(),
        "cautions": _lines(draft.get("cautions")),
        "questions": _lines(draft.get("check_questions")),
    }, ensure_ascii=False, sort_keys=True)


def draft_changed(before: dict, after: dict) -> bool:
    """書き換えの応答が下書きを変えたか（空白の差だけなら変えていない）。"""
    return _draft_signature(before or {}) != _draft_signature(after or {})



# ---------------------------------------------------------------------------
# 保存の可否と届き方（IK-0908 / IK-0915）
# ---------------------------------------------------------------------------


def topic_save_facts(
    *,
    is_owner: bool,
    is_system_admin: bool = False,
    delivered: bool = False,
    release_version_no: int | None = None,
    owner_display_name: str | None = None,
    unpublished_changes: bool | None = None,
) -> dict:
    """閲覧者がトピックを保存できるか・保存がいつ受講者に届くかを 1 つの値で返す。

    第 25 周 第 7 波（IK-1053）: 版ありのとき ``unpublished_changes``（HEAD と受講者の版が違うか・
    分からなければ None）を事実文 ``unpublished_changes_note`` にし、所有者にだけ版の管理への入口
    ``versions_entry`` を添える（発行は所有者の明示操作のまま・自動発行も督促もしない）。

    - ``can_save``: 保存 API（所有者 / SYSTEM_ADMIN のみ）と同じ判定。False のときは
      ``save_notice`` に所有者へ依頼する事実文を添える。
    - ``delivery``: 受講者がいるコース（公開・グループ共有・版あり）のときだけ。
      版があれば ``release``（発行するまで届かない）、無ければ ``immediate``。
      受講者のいないコース（非公開・版なし）では何も言わない（キー自体を付けない）。
    """
    can_save = bool(is_owner or is_system_admin)
    out: dict = {"can_save": can_save}
    if not can_save:
        out["save_notice"] = TOPIC_SAVE_OWNER_ONLY_NOTICE
        # IK-0950: スタジオの見出しにも共有・保存不可の印（トピックを開く前に分かる）。
        out["share_mark"] = studio_share_mark()
        # IK-0978: 案を誰に渡すか（所有者の表示名。共有教員の画面だけ — 受講者の DTO には出さない）。
        name = str(owner_display_name or "").strip()
        if name:
            out["owner_display_name"] = name
            out["owner_notice"] = COURSE_OWNER_NOTICE.replace("{name}", name)
        out["proposal_copy_notice"] = proposal_copy_notice(name)
        # 第 25 周 第 6 波（オーナー判断 2）: 音声生成もトピックの保存と同じく所有者だけ。
        # IK-1071: 頼む相手の名前と、頼む中身を写す部品（［案をコピー］と同じ受け渡し）。
        out["audio_generate_notice"] = audio_generate_owner_only_notice(name)
        out["audio_request"] = audio_request_facts(name)
    try:
        version = int(release_version_no or 0)
    except (TypeError, ValueError):
        version = 0
    if version > 0:
        # IK-1070: 「発行してください」は未発行の変更がある（True）か、比べられない（None）ときだけ。
        message_key = "release_current" if unpublished_changes is False else "release"
        delivery: dict = {
            "status": "release",
            "message": TOPIC_DELIVERY_MESSAGES[message_key].format(version=version),
        }
        facts = unpublished_changes_facts(version, unpublished_changes)
        if facts:
            delivery.update(facts)
            out["unpublished_changes_note"] = facts["unpublished_changes_note"]
        if is_owner:
            entry = {"label": TOPIC_VERSIONS_ENTRY_LABEL, "object_type": "course"}
            delivery["versions_entry"] = entry
            out["versions_entry"] = entry
        out["delivery"] = delivery
    elif delivered:
        out["delivery"] = {"status": "immediate", "message": TOPIC_DELIVERY_MESSAGES["immediate"]}
    return out


def course_is_delivered(*, visibility: str | None, is_published: bool, group_id: Any = None) -> bool:
    """受講者に届く経路があるコースか（公開テンプレート・グループ共有・公開フラグ）。"""
    vis = str(visibility or "private")
    if vis == "public" and is_published:
        return True
    if vis == "group" and group_id:
        return True
    return bool(is_published)


# ---------------------------------------------------------------------------
# AI 書き換えの根拠の予算（IK-0909）
# ---------------------------------------------------------------------------

#: 根拠を JSON にしたときの上限（プロンプトの予算。従来の ``[:8000]`` と同じ大きさ）。
REWRITE_EVIDENCE_LIMIT = 8000

#: 予算を配る順（学ぶ単位を先頭に・必ず残す）。状態の記録（coverage / content_confidence）は
#: 材料ではないので最後に小さく載せる。
_REWRITE_KEY_ORDER = (
    "units",
    "summary",
    "source_excerpt",
    "figures",
    "evidence_links",
    "content_blocks",
    "linked_component_ids",
    "linked_equation_ids",
    "source_evidence_ids",
    "coverage",
    "content_confidence",
)

#: content_blocks の各項目から落とす失敗状態の文を持ちうるキー（IK-0873 の正本で判定）。
_UNINFORMATIVE_TEXT_KEYS = ("semantic_kind", "summary", "description")


def _drop_uninformative_texts(value: Any) -> Any:
    from core.course_content_builder import is_uninformative_equation_summary

    if isinstance(value, dict):
        out: dict = {}
        for key, item in value.items():
            if key in _UNINFORMATIVE_TEXT_KEYS and isinstance(item, str) and is_uninformative_equation_summary(item):
                continue
            out[key] = _drop_uninformative_texts(item)
        return out
    if isinstance(value, list):
        return [_drop_uninformative_texts(item) for item in value]
    return value


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2))


def _is_empty_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _fit_value(value: Any, budget: int) -> Any:
    """値を JSON の長さ ``budget`` 以内に収める（リストは項目単位・入れ子は再帰・文字列は文字単位）。

    収まらないときは中身を減らした値を返し、何も残せなければ None。入れ子のリストの先頭項目が
    大きすぎても、その項目の中身を削って 1 つは残す（式の一覧の塊を丸ごと落とさない — IK-0909）。
    """
    if budget <= 0:
        return None
    if _json_len(value) <= budget:
        return value
    if isinstance(value, str):
        n = max(0, min(len(value), budget - 8))
        while n > 0 and _json_len(value[:n]) > budget:
            n = int(n * 0.8)
        return value[:n] if n > 0 else None
    if isinstance(value, list):
        kept: list = []
        for item in value:
            if _json_len(kept + [item]) <= budget:
                kept.append(item)
                continue
            room = budget - _json_len(kept + [""])
            # 入れ子は字下げの分だけ長くなるので、収まるまで取り分を縮めて測り直す。
            while room > 40:
                sub = _fit_value(item, room)
                if not _is_empty_value(sub) and _json_len(kept + [sub]) <= budget:
                    kept.append(sub)
                    break
                room = int(room * 0.8)
            break
        return kept or None
    if isinstance(value, dict):
        out: dict = {}
        for key, item in value.items():
            candidate = dict(out)
            candidate[key] = item
            if _json_len(candidate) <= budget:
                out[key] = item
                continue
            room = budget - _json_len({**out, key: ""})
            while room > 40:
                sub = _fit_value(item, room)
                if not _is_empty_value(sub) and _json_len({**out, key: sub}) <= budget:
                    out[key] = sub
                    break
                room = int(room * 0.8)
        return out or None
    return None


def compact_rewrite_evidence(evidence: dict, *, limit: int = REWRITE_EVIDENCE_LIMIT) -> tuple[dict, list[str]]:
    """書き換えの根拠を、JSON を壊さずに予算へ収める（IK-0909）。

    直列化してから頭から切ると末尾のキー（学ぶ単位）が先に落ち、JSON も壊れる。ここでは
    ①失敗状態の文（「Equation semantics could not be inferred.」）を材料から外し
    ②学ぶ単位を先頭に必ず残し ③残りの**キーごとに予算を配る**（第 25 周 第 3 波: 先着順では
    前のキーが予算を使い切り、根拠リンク・式・部品の一覧が丸ごと落ちていた）。各キーはまず
    均等な取り分の中で項目単位・入れ子・文字単位に縮め、余った予算を順に削ったキーへ戻す。
    省いたキー（全部または一部）を 2 つ目の戻り値で返す。
    """
    evidence = _drop_uninformative_texts(dict(evidence or {}))
    keys = [k for k in _REWRITE_KEY_ORDER if k in evidence] + [k for k in evidence if k not in _REWRITE_KEY_ORDER]
    out: dict = {}
    if "units" in evidence:
        out["units"] = evidence["units"]  # 必ず残す（単位の要約は unit_grounding_for_prompt が既に切っている）
    rest = [k for k in keys if k != "units" and not _is_empty_value(evidence.get(k))]
    trimmed: list[str] = []

    def _assemble(values: dict) -> dict:
        assembled: dict = {}
        for key in keys:
            if key == "units" and "units" in out:
                assembled[key] = out["units"]
            elif key in values:
                assembled[key] = values[key]
        return assembled

    # 1 周目: 均等な取り分（使い残しは後ろのキーへ回る）。
    placed: dict = {}
    for index, key in enumerate(rest):
        remaining = limit - _json_len(_assemble(placed))
        share = remaining // max(1, len(rest) - index)
        value = evidence[key]
        overhead = _json_len(_assemble({**placed, key: ""})) - _json_len(_assemble(placed))
        fitted = _fit_value(value, max(0, share - overhead))
        if not _is_empty_value(fitted):
            placed[key] = fitted
        if _is_empty_value(fitted) or fitted != value:
            trimmed.append(key)
    # 2 周目: 余った予算を、削ったキーへ順に戻す。
    for key in list(trimmed):
        others = {k: v for k, v in placed.items() if k != key}
        room = limit - _json_len(_assemble({**others, key: ""}))
        fitted = _fit_value(evidence[key], room + 2)
        if not _is_empty_value(fitted) and _json_len(_assemble({**others, key: fitted})) <= limit:
            placed[key] = fitted
            if fitted == evidence[key]:
                trimmed.remove(key)
    # 入れ子の字下げで上限を超えたら、いちばん大きいキーから縮める（JSON は壊さない）。
    for _ in range(50):
        if _json_len(_assemble(placed)) <= limit or not placed:
            break
        biggest = max(placed, key=lambda k: _json_len(placed[k]))
        shrunk = _fit_value(placed[biggest], int(_json_len(placed[biggest]) * 0.8))
        if _is_empty_value(shrunk):
            placed.pop(biggest)
        else:
            placed[biggest] = shrunk
        if biggest not in trimmed:
            trimmed.append(biggest)
    out = _assemble(placed)
    # 値の無いキーを「省いた」と言わない（空リスト・空文字は元から材料ではない）。
    trimmed = [k for k in trimmed if evidence.get(k)]
    return out, trimmed


def rewrite_grounding_trimmed_notice(trimmed: Iterable[str]) -> str:
    labels = [REWRITE_GROUNDING_KEY_LABELS.get(k, "") for k in trimmed]
    labels = [label for label in labels if label]
    if not labels:
        return ""
    return REWRITE_GROUNDING_TRIMMED_NOTICE.format(keys="・".join(dict.fromkeys(labels)))


# ---------------------------------------------------------------------------
# 根拠リンクの式の見出し（教員向け — IK-0873 の残り）
# ---------------------------------------------------------------------------

_JAPANESE_RE = None


def _has_japanese(text: str) -> bool:
    global _JAPANESE_RE
    if _JAPANESE_RE is None:
        import re

        _JAPANESE_RE = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF]")
    return bool(_JAPANESE_RE.search(text or ""))


def studio_equation_titles(items: Iterable[dict] | None) -> list[dict]:
    """原稿スタジオの根拠リンクで、式の見出しを教員が見分けられる形にする（読み取り専用の区画のみ）。

    - 英語の要約（A層の ``semantic_kind`` 第1文「Fragment of …」）が見出しになった式は、
      役割の訳（「前提式」）か「数式」に戻す（要約は ``summary`` として残る）。
    - 同じ見出し（「前提式」×4）が並ぶときは、掲載節 → 読み下しの順で区別を括弧で添える。
      区別の材料が無ければそのまま（作らない）。
    学習画面の evidence DTO（``build_topic_evidence_items``）は変えない。入力を mutate しない。
    """
    from core.element_vocab import equation_role_label
    from core.text_excerpt import excerpt

    out = [dict(item) if isinstance(item, dict) else item for item in (items or [])]
    equations = [item for item in out if isinstance(item, dict) and item.get("kind") == "equation"]
    for item in equations:
        title = str(item.get("title") or "").strip()
        semantic = str(item.get("semantic_kind") or item.get("summary") or "").strip()
        # 見出しが A層の英語の要約（semantic_kind の第1文）から来たときだけ戻す
        # （教員・A層が付けた読める名前は変えない）。
        from_summary = bool(title and semantic and title.rstrip("…").strip() and semantic.startswith(title.rstrip("…").strip()))
        if title and from_summary and not _has_japanese(title):
            role_ja = equation_role_label(item.get("role_in_argument"))
            item["title"] = (role_ja + "式") if role_ja else "数式"
    groups: dict[str, list[dict]] = {}
    for item in equations:
        groups.setdefault(str(item.get("title") or ""), []).append(item)
    for title, group in groups.items():
        if len(group) < 2:
            continue
        # 区別の材料は、グループの中で値が分かれるものだけを使う（全部同じ節なら節は添えない）。
        for field in ("section_label", "plain_text"):
            hints = [excerpt(str(it.get(field) or "").strip(), 24) for it in group]
            if len({h for h in hints if h}) < 2:
                continue
            for it, hint in zip(group, hints):
                if hint:
                    it["title"] = f"{title}（{hint}）"
            break
    return out


# ---------------------------------------------------------------------------
# 決定論付録の付け直し（IK-0872 / IK-0880 の残り）
# ---------------------------------------------------------------------------


def topic_appendix_state(topic: dict | None) -> dict | None:
    """保存済みの付録がいまの根拠から作る形と違うときだけ ``{stale: True, message}``。"""
    from core.course_content_builder import topic_appendix_is_stale

    try:
        stale = topic_appendix_is_stale(topic or {})
    except Exception:  # noqa: BLE001 — 判定できないときは何も言わない（fail-soft）
        return None
    if not stale:
        return None
    return {"stale": True, "message": APPENDIX_STALE_NOTICE}


# ---------------------------------------------------------------------------
# 旧い生成規則の作業メモの名残（IK-1081）
# ---------------------------------------------------------------------------

#: 名残のある場所の語（本文の注の段落 / 注意点）。
WORK_NOTE_PLACE_LABELS: dict[str, str] = {"body": "本文の「注」の段落", "cautions": "注意点"}
WORK_NOTE_TOPIC_NOTICE = (
    "このトピックの{places}に、古い生成規則の作業メモ（教員宛ての注・内部の番号）が残っています。"
    "本文の注の段落は受講者には表示していません。保存済みの本文・注意点はシステムが書き直さないため、"
    "外すか書き直して保存してください。"
)
WORK_NOTE_COURSE_NOTICE = (
    "古い生成規則の作業メモ（教員宛ての注・内部の番号）が残るトピックがあります（下の一覧・残っている場所ごと）。"
    "本文の注の段落は受講者には表示していません。注意点は受講者の画面に出ない欄です。"
    "システムは保存済みの本文・注意点を書き直さないため、外すには各トピックを開いて編集し、保存してください。"
)
WORK_NOTE_LIST_SUMMARY = "作業メモが残るトピックの一覧"
#: 第 25 周 第 10 波（IK-1081 の残り）: 一覧を残っている場所で分ける（件数は出さない）。
WORK_NOTE_LIST_SUMMARY_BODY = "本文の「注」の段落に作業メモが残るトピック"
WORK_NOTE_LIST_SUMMARY_CAUTIONS = "注意点だけに作業メモが残るトピック"


def work_note_list_summaries() -> dict[str, str]:
    """一覧の見出し（残っている場所のキー → 見出し）。"""
    return {"body": WORK_NOTE_LIST_SUMMARY_BODY, "cautions": WORK_NOTE_LIST_SUMMARY_CAUTIONS}
#: 注意点の作業メモが「埋め込んでいません」と言う図・式が、いまの本文に入っているときの事実文。
WORK_NOTE_EMBED_CONFLICT_NOTICE = (
    "注意点の作業メモに「埋め込んでいません」とある図・式は、いまの本文（付録を含む）には埋め込まれています。"
    "このメモは本文より前の生成時の記録で、いまの本文とは食い違っています。"
)


def topic_work_note_state(topic: dict | None) -> dict | None:
    """保存済みの本文・注意点に作業メモの名残があるときだけ ``{message, places}``（IK-1081）。"""
    from core.course_content_builder import topic_work_note_residue

    try:
        residue = topic_work_note_residue(topic or {})
    except Exception:  # noqa: BLE001 — 判定できないときは何も言わない（fail-soft）
        return None
    place_keys = [
        key for key in ("body", "cautions")
        if residue.get("body_notes" if key == "body" else "caution_notes")
    ]
    if not place_keys:
        return None
    places = [WORK_NOTE_PLACE_LABELS[key] for key in place_keys]
    state = {
        "message": WORK_NOTE_TOPIC_NOTICE.format(places="と".join(places)),
        "places": places,
        "place_keys": place_keys,
    }
    try:
        from core.course_content_builder import work_note_embed_conflicts

        if work_note_embed_conflicts(topic or {}):
            state["embed_conflict_note"] = WORK_NOTE_EMBED_CONFLICT_NOTICE
    except Exception:  # noqa: BLE001 — 突き合わせられなければ何も足さない（fail-soft）
        pass
    return state


__all__ = [
    "WORK_NOTE_COURSE_NOTICE",
    "changed_topic_titles",
    "course_release_changed_topics",
    "version_state_course_facts",
    "WORK_NOTE_EMBED_CONFLICT_NOTICE",
    "WORK_NOTE_LIST_SUMMARY_BODY",
    "WORK_NOTE_LIST_SUMMARY_CAUTIONS",
    "work_note_list_summaries",
    "WORK_NOTE_LIST_SUMMARY",
    "WORK_NOTE_PLACE_LABELS",
    "WORK_NOTE_TOPIC_NOTICE",
    "topic_work_note_state",
    "course_release_comparison",
    "unpublished_changes_facts",
    "APPENDIX_REATTACH_ALL_LEAD",
    "APPENDIX_REATTACH_ALL_OUTCOME_LABELS",
    "appendix_reattach_all_outcome_groups",
    "appendix_reattach_all_notice",
    "APPENDIX_REATTACH_ADDED_NOTICE",
    "APPENDIX_REATTACH_ALL_CONFIRM",
    "APPENDIX_REATTACH_ALL_DONE_NOTICE",
    "APPENDIX_REATTACH_ALL_NONE_NOTICE",
    "APPENDIX_REATTACH_ALL_OWNER_ONLY_NOTICE",
    "APPENDIX_REATTACH_APPLIED_NOTICE",
    "APPENDIX_REATTACH_CONFIRM",
    "APPENDIX_REATTACH_OUTCOMES",
    "APPENDIX_REATTACH_REMOVED_NOTICE",
    "APPENDIX_REATTACH_UNCHANGED_NOTICE",
    "APPENDIX_STALE_COURSE_NOTICE",
    "APPENDIX_STALE_LIST_SUMMARY",
    "APPENDIX_STALE_NOTICE",
    "REWRITE_EVIDENCE_LIMIT",
    "REWRITE_GROUNDING_KEY_LABELS",
    "REWRITE_GROUNDING_TRIMMED_NOTICE",
    "TOPIC_DELIVERY_MESSAGES",
    "TOPIC_SAVE_FORBIDDEN_DETAIL",
    "TOPIC_SAVE_OWNER_ONLY_NOTICE",
    "compact_rewrite_evidence",
    "course_is_delivered",
    "rewrite_grounding_trimmed_notice",
    "studio_equation_titles",
    "appendix_reattach_notice",
    "topic_appendix_state",
    "topic_save_facts",
    "EVIDENCE_STATE_LABELS",
    "EVIDENCE_STATE_MESSAGES",
    "EVIDENCE_UNITS_ONLY_MESSAGE",
    "EVIDENCE_UNMAPPED_HEADING",
    "EVIDENCE_TEACHER_ADDED_HEADING",
    "EVIDENCE_ANALYSIS_HEADING",
    "HANDOFF_UNVERIFIED_BASIS_NOTE",
    "qualify_handoff_basis",
    "teacher_visible_texts",
    "REWRITE_APPLIED_NOTICE",
    "REWRITE_PROPOSAL_UNSAVABLE_NOTICE",
    "REWRITE_NO_GROUNDING_NOTICE",
    "REWRITE_UNCHANGED_NOTICE",
    "TOPIC_AUDIO_GENERATE_FORBIDDEN_DETAIL",
    "TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE",
    "TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE_NAMED",
    "AUDIO_REQUEST_COPY_LABEL",
    "audio_generate_owner_only_notice",
    "audio_request_facts",
    "TOPIC_AUDIO_INVALIDATED_NOTICE",
    "TOPIC_AUDIO_SAVE_NOTICE",
    "TOPIC_AUDIO_STATUS_MESSAGES",
    "TOPIC_DOCUMENT_UNRESOLVED_NOTICE",
    "draft_changed",
    "draft_has_body",
    "rewrite_grounding_is_empty",
    "topic_chunk_ids",
    "topic_document_ids",
    "topic_evidence_state",
    "topic_has_units",
    "unit_grounding_for_prompt",
]
