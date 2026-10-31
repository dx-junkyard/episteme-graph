"""第 25 周 面 D 第 10 波（IK-1127 / IK-1081 の残り / IK-0571 記号 / 書き換えの材料 / 頭脳所見）の再現テスト。

- IK-1127: 版の管理（version-state）がコースでは宛先ごとの届き方（受講者 = 同意なしに新しい版 /
  閲覧の共有先 = 取り込むまで自分の版 / 編集の共有先 = いまの内容）を言い、所有者には受講者の見ている版
  （原稿スタジオの ``release_current`` と同じ文）と、発行の後に変わったトピックの題名（件数なし）を返す。
  versioning.js はサーバの文を素通しにし、旧い「共有先は同意するまで」の 1 文を言わない。
- IK-1081: 「誤って式として取り込まれた…教材には載せていません」の注は作業メモ（地の文は外さない）。
  一覧は残っている場所（本文の注 / 注意点だけ）で分ける。注意点の「埋め込んでいません」がいまの本文の
  埋め込みと食い違うときは事実文。
- IK-0571: 定義の文が原文の言語なら「（原文）」、定義の文が無い記号は「定義の記述が見つかりませんでした」。
- 書き換えの材料（「現在の授業用ドラフト」）に内部 ID・作業メモが残らない。
- 学生の ⚓ の摘要行に件数を出さない。記号の「別の論文」は論文の呼び方の正本を通す。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import course_content_builder as ccb  # noqa: E402
from core import label_vocab  # noqa: E402
from core import lecture  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402
from core.learner_context_common import contains_internal_id  # noqa: E402

FRONTEND = ROOT / "frontend" / "public"
STUDIO_JS = (FRONTEND / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
VERSIONING_JS = (FRONTEND / "js" / "versioning.js").read_text(encoding="utf-8")
VERSIONING_SRC = (BACKEND / "api" / "routes" / "versioning.py").read_text(encoding="utf-8")
TOPICS_SRC = (BACKEND / "api" / "routes" / "lecture_studio" / "topics.py").read_text(encoding="utf-8")
SYMBOL_SRC = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")


def _js_fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n  function [A-Za-z0-9_]+\(", src[start + 10:])
    return src[start: start + 10 + nxt.start()] if nxt else src[start:]


# ---------------------------------------------------------------------------
# IK-1127
# ---------------------------------------------------------------------------


class TestVersionAudience:
    def test_audience_note_names_learners_and_subscribers(self):
        note = label_vocab.VERSION_AUDIENCE_NOTE_COURSE
        assert "受講者" in note and "同意は要りません" in note
        assert "閲覧の共有先" in note and "編集の共有先" in note
        assert not re.search(r"\d", note)

    def test_non_owner_gets_only_audience(self, monkeypatch):
        monkeypatch.setattr(studio_state, "course_release_comparison", lambda cid: (7, True))
        out = studio_state.version_state_course_facts("c1", is_owner=False)
        assert out == {"audience_note": label_vocab.VERSION_AUDIENCE_NOTE_COURSE}

    def test_owner_after_release_sees_learner_version(self, monkeypatch):
        monkeypatch.setattr(studio_state, "course_release_comparison", lambda cid: (7, False))
        out = studio_state.version_state_course_facts("c1", is_owner=True)
        assert out["learner_version_note"] == studio_state.TOPIC_DELIVERY_MESSAGES["release_current"].format(version=7)
        assert out["unpublished_changes_note"] == studio_state.TOPIC_UNPUBLISHED_CHANGES_NOTES[False].format(version=7)
        assert "changed_topic_titles" not in out

    def test_owner_with_changes_lists_titles(self, monkeypatch):
        monkeypatch.setattr(studio_state, "course_release_comparison", lambda cid: (6, True))
        monkeypatch.setattr(studio_state, "course_release_changed_topics", lambda cid: ["フィラメントの磁場"])
        out = studio_state.version_state_course_facts("c1", is_owner=True)
        assert out["changed_topic_titles"] == ["フィラメントの磁場"]
        assert out["changed_topics_label"] == label_vocab.VERSION_CHANGED_TOPICS_LABEL

    def test_no_release_no_learner_version(self, monkeypatch):
        monkeypatch.setattr(studio_state, "course_release_comparison", lambda cid: (None, None))
        out = studio_state.version_state_course_facts("c1", is_owner=True)
        assert set(out) == {"audience_note"}

    def test_changed_topic_titles_pure(self):
        before = {"data": {"topics": [
            {"id": "t1", "title": "A", "student_material": {"source_text": "x"}},
            {"id": "t2", "title": "B"},
            {"id": "t9", "title": "消えた"},
        ]}}
        after = {"data": {"topics": [
            {"id": "t1", "title": "A", "student_material": {"source_text": "x\n\n===\n\ny"}},
            {"id": "t2", "title": "B"},
            {"id": "t3", "title": "新しい"},
        ]}}
        assert studio_state.changed_topic_titles(before, after) == ["A", "新しい", "消えた"]
        assert studio_state.changed_topic_titles(before, before) == []
        assert studio_state.changed_topic_titles(None, after) is None

    def test_route_and_modal_pass_through(self):
        assert "version_state_course_facts(canonical" in VERSIONING_SRC
        assert "共有先は同意するまで自分の版に留まります" not in VERSIONING_JS
        for key in ("state.audience_note", "state.learner_version_note", "state.changed_topic_titles"):
            assert key in VERSIONING_JS
        # 文はサーバの素通し（JS に宛先の文を焼き込まない）。
        assert "受講者の同意" not in VERSIONING_JS


# ---------------------------------------------------------------------------
# IK-1081 の残り
# ---------------------------------------------------------------------------

_T1_NOTE = (
    "> 注: このトピックに紐づく「式」は図の座標目盛り（赤経・赤緯）が誤って式として取り込まれたもので、"
    "数式として使えないため教材には載せていません。"
)


class TestWorkNoteGenerationProcessWords:
    def test_t1_note_is_work_note(self):
        assert ccb.is_work_note_text(_T1_NOTE)
        body = "## 観測\n\n磁場の向きを偏光から読みます。\n\n" + _T1_NOTE + "\n\n## 次のトピックへ\n\n次へ。"
        text = lecture.topic_student_material({"student_material": {"source_text": body}})
        assert "取り込まれた" not in text and "次へ。" in text

    def test_learner_prose_is_not_work_note(self):
        for prose in (
            "観測データは 13CO の輝線で取り込みます。",
            "偏光の角度から磁場の向きを推定します。教材では図 1 を使います。",
            "この近似は低温でのみ成り立つため、高温の領域には載せられません。",
            "電子が取り込まれた後の状態を考えます。",
        ):
            assert not ccb.is_work_note_text(prose), prose

    def test_list_split_by_place(self):
        body_topic = {"student_material": {"source_text": "本文。\n\n" + _T1_NOTE}}
        caution_topic = {"cautions": ["claim の id は8件ありますが本文が供給されていないため埋め込んでいません。"]}
        assert studio_state.topic_work_note_state(body_topic)["place_keys"] == ["body"]
        assert studio_state.topic_work_note_state(caution_topic)["place_keys"] == ["cautions"]
        assert set(studio_state.work_note_list_summaries()) == {"body", "cautions"}
        assert '"places"' in TOPICS_SRC and "work_note_list_summaries" in TOPICS_SRC
        assert "s.work_note_list_summaries" in STUDIO_JS

    def test_embed_conflict(self):
        uuid = "d687f713-e9fe-4950-93df-cd6b7b88136d"
        topic = {
            "student_material": {"source_text": "本文。\n\n### この節で参照する図\n\n![[figure:%s]]" % uuid},
            "cautions": [
                "図 %s は caption が供給されておらず、確認できないため埋め込んでいません。" % uuid,
                "figure 97fbef7c-… は根拠候補に説明が載っておらず埋め込んでいません。",
            ],
        }
        conflicts = ccb.work_note_embed_conflicts(topic)
        assert len(conflicts) == 1 and uuid in conflicts[0]
        state = studio_state.topic_work_note_state(topic)
        assert state["embed_conflict_note"] == studio_state.WORK_NOTE_EMBED_CONFLICT_NOTICE
        # 省略記号付きの先頭 8 桁でも突き合わせる。
        topic["student_material"]["source_text"] = "![[figure:97fbef7c-1111-2222-3333-444455556666]]"
        assert len(ccb.work_note_embed_conflicts(topic)) == 1
        # 本文に埋め込みが無ければ言わない。
        topic["student_material"]["source_text"] = "本文だけ。"
        assert ccb.work_note_embed_conflicts(topic) == []
        assert "embed_conflict_note" in STUDIO_JS


# ---------------------------------------------------------------------------
# 書き換えの材料（製品頭脳 A の前周所見）
# ---------------------------------------------------------------------------


class TestRewriteMaterialHasNoInternalIds:
    def test_prompt_draft_strips_ids_and_notes(self):
        from routes.lecture_studio.topics import _rewrite_prompt_draft

        draft = {
            "student_material": {"source_text": "磁場を見積もります。\n\n" + _T1_NOTE},
            "cautions": [
                "低温でのみ成り立ちます。",
                "図 d687f713-e9fe-4950-93df-cd6b7b88136d は caption が無いため埋め込んでいません。",
                "eq_blk_003_0065 と eq_blk_003_0066 は同じ式です。",
                "claim_span_006_sub01 は本文が読めません。",
            ],
        }
        out = _rewrite_prompt_draft({}, draft)
        text = out["student_material"]["source_text"]
        assert "取り込まれた" not in text
        assert out["cautions"] == ["低温でのみ成り立ちます。"]
        for item in [text, *out["cautions"]]:
            assert not contains_internal_id(item), item


# ---------------------------------------------------------------------------
# IK-0571 記号の定義
# ---------------------------------------------------------------------------


class _NoSession:
    def execute(self, *a, **k):  # concept_ref の読みは fail-soft で None
        raise RuntimeError("no db")

    def rollback(self):
        pass


class TestSymbolDefinitionMarks:
    def test_original_mark_on_english_definition(self):
        from core import symbol_lookup

        res = symbol_lookup._build_result(
            _NoSession(), {"document_id": "d1", "agent_symbol_id": "s1"},
            chosen={"text": "P is the gas pressure inside the clump."},
            fact="", titles={}, display_symbol="P",
        )
        assert res["definition"]["original_mark"] == label_vocab.ORIGINAL_TEXT_MARK

    def test_japanese_definition_has_no_mark(self):
        from core import symbol_lookup

        res = symbol_lookup._build_result(
            _NoSession(), {"document_id": "d1", "agent_symbol_id": "s1"},
            chosen={"text": "P はクランプ内部の圧力です。"},
            fact="", titles={}, display_symbol="P",
        )
        assert "original_mark" not in res["definition"]

    def test_no_definition_says_so(self):
        assert symbol_lookup_fact() in SYMBOL_SRC
        fn = _js_fn(APP_JS, "renderSymbolLookupPopover")
        assert "facts" in fn

    def test_other_document_titles_use_paper_title_canon(self):
        assert "resolve_paper_titles" in SYMBOL_SRC


def symbol_lookup_fact() -> str:
    from core import symbol_lookup

    return symbol_lookup.FACT_NO_DEFINITION


# ---------------------------------------------------------------------------
# 学生の ⚓ の摘要行（件数なし）
# ---------------------------------------------------------------------------


class TestSupportsSummaryNoCounts:
    def test_no_count_in_summary_line(self):
        fn = _js_fn(APP_JS, "renderSupportsSummaryLine")
        assert '"件"' not in fn and "length + " not in fn
