"""IK-0374 / IK-0375: コース内容生成の書き戻しが、生成中に保存された値を消さない。

砂場（uxsim）で観測した事実: コース登録（14:08:39）がバックグラウンドのコース内容生成を
始め、教員が 14:09:16 に地図の割り当て（``data.cartridge_id`` + 4 トピックの
``atlas_node_id``）を保存した。生成が 14:22:04 に終わったとき、行の ``cartridge_id`` は
NULL・``atlas_node_id`` は 0 件になり、学習者の ``GET /api/atlas`` は 404 になった。
原因は ``build_course_content`` が開始時点に読んだ ``data`` を丸ごと書き戻していたこと。

ここでは DB 行を JSON 文字列で持つ偽セッションで、生成の途中に割り当てを行へ書き込み、
書き戻し後に割り当てが残り、生成結果（状態・生成項目）が入っていることを確かめる。
あわせて IK-0375（生成中 / 失敗の状態を行に残す）を確かめる。
"""

from __future__ import annotations

import ast
import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from core import course_content_builder as ccb

COURSE_ID = "course-ik0374"
USER_ID = "teacher-1"


class _FakeDb:
    """learning_courses の1行だけを持つ偽 DB。読みは毎回 JSON から作り直す（別オブジェクト）。"""

    def __init__(self, data: dict):
        self.data_json = json.dumps(data, ensure_ascii=False)
        self.title = data.get("title") or ""
        self.status_writes: list[dict] = []
        self.full_writes = 0
        self.for_update_reads = 0

    @property
    def data(self) -> dict:
        return json.loads(self.data_json)

    def write(self, data: dict) -> None:
        self.data_json = json.dumps(data, ensure_ascii=False)


class _FakeSession:
    def __init__(self, db: _FakeDb):
        self.db = db
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query, params=None):
        sql = str(query)
        params = params or {}
        result = MagicMock()
        if "SELECT data, user_id" in sql:
            result.fetchone.return_value = (self.db.data, USER_ID)
        elif "FOR UPDATE" in sql and "SELECT data" in sql:
            self.db.for_update_reads += 1
            result.fetchone.return_value = (self.db.data,)
        elif "jsonb_set" in sql:
            data = self.db.data
            status = json.loads(params["status"])
            data["course_content_status"] = status
            self.db.write(data)
            self.db.status_writes.append(status)
        elif "UPDATE learning_courses" in sql and "title = :title" in sql:
            self.db.write(json.loads(params["data"]))
            self.db.title = params["title"]
            self.db.full_writes += 1
        elif "chunk_index ASC" in sql:
            result.fetchall.return_value = []
        elif "SELECT DISTINCT c.document_id::text" in sql:
            result.fetchall.return_value = [("doc-1",)]
        elif "FROM document_figures" in sql:
            result.fetchall.return_value = []
        return result

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _registered_course() -> dict:
    return {
        "title": "Galaxy formation seminar",
        "sources": [{"material_id": "m1"}],
        "course_focus": "暗黒物質ハローの形成をめぐる議論",
        "llm_models": {"learning_chat": "gpt-x"},
        "topics": [
            {"id": "t1", "title": "Topic 1", "prerequisites": []},
            {"id": "t2", "title": "Topic 2", "prerequisites": ["Topic 1"]},
        ],
    }


def _bind_atlas(db: _FakeDb) -> None:
    """PUT /api/admin/courses/{id}/atlas-binding が行へ書く内容を再現する。"""
    data = db.data
    data["cartridge_id"] = "astrophysics"
    data.pop("atlas_binding_pending", None)
    for topic in data["topics"]:
        topic["atlas_node_id"] = f"node-{topic['id']}"
    db.write(data)


_FAKE_ARTIFACTS = {
    "doc-1": {
        "stage_outputs": {
            "_artifacts": {
                "course_mapping": {
                    "topics": [
                        {"title": "Topic 1", "description": "desc 1"},
                        {"title": "Topic 2", "description": "desc 2"},
                    ]
                },
            },
        },
    },
}

_DRAFT = {
    "key_concepts": ["k"],
    "student_material": {"source_format": "eg-markdown-v1", "source_text": "generated text"},
    "spoken_script": "generated script",
    "cautions": [],
    "check_questions": [],
}


def _run_build(db: _FakeDb, *, during_draft=None, draft_error: Exception | None = None):
    session = _FakeSession(db)
    calls = {"n": 0}

    def _generate(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1 and during_draft is not None:
            during_draft(db)
        return dict(_DRAFT)

    patches = [
        patch("core.course_content_builder._pg_session", return_value=session),
        patch("core.document_pipeline.persistence.resolve_artifact_runs", return_value=_FAKE_ARTIFACTS),
        patch("core.course_content_builder.generate_text_with_structured_output", side_effect=_generate),
    ]
    if draft_error is not None:
        patches.append(
            patch("core.course_content_builder._generate_course_topic_drafts", side_effect=draft_error)
        )
    for p in patches:
        p.start()
    try:
        return ccb.build_course_content(USER_ID, COURSE_ID), session
    finally:
        for p in reversed(patches):
            p.stop()


# ---------------------------------------------------------------------------
# IK-0374: 生成中に保存された割り当てが書き戻しで消えない
# ---------------------------------------------------------------------------


def test_atlas_binding_saved_during_build_survives_writeback():
    db = _FakeDb(_registered_course())
    result, session = _run_build(db, during_draft=_bind_atlas)

    assert result["status"] == "completed"
    data = db.data
    # 生成中に保存された地図の割り当てが残っている
    assert data["cartridge_id"] == "astrophysics"
    assert [t.get("atlas_node_id") for t in data["topics"]] == ["node-t1", "node-t2"]
    # builder の持ち物ではないコース直下のキーも残っている
    assert data["course_focus"] == "暗黒物質ハローの形成をめぐる議論"
    assert data["llm_models"] == {"learning_chat": "gpt-x"}
    # 生成結果は入っている
    assert data["course_content_status"]["status"] == "completed"
    assert data["course_content_status"]["updated_topics"] == 2
    for topic in data["topics"]:
        assert topic["student_material"]["source_text"].startswith("generated text")
        assert topic["spoken_script"] == "generated script"
        assert topic["draft_source"] == "course_content_generation"
        assert "content_blocks" in topic
        assert "content_source" in topic
    # 書き戻しは行を読み直してから行う
    assert db.for_update_reads == 1
    assert db.full_writes == 1
    # 割り当ては生成項目と衝突しないので「残した」注記は出ない
    assert "concurrent_edits_note" not in data["course_content_status"]


def test_topics_are_matched_by_id_not_position():
    """生成中にトピックが並べ替え・追加・削除されても、id で突き合わせる。"""

    def _reorder(db: _FakeDb) -> None:
        data = db.data
        t1, t2 = data["topics"]
        t2["atlas_node_id"] = "node-t2"
        data["topics"] = [t2, {"id": "t3", "title": "Added later"}]  # t1 は削除された
        db.write(data)

    db = _FakeDb(_registered_course())
    _run_build(db, during_draft=_reorder)
    topics = db.data["topics"]
    assert [t["id"] for t in topics] == ["t2", "t3"]  # 消された t1 は復活しない
    assert topics[0]["atlas_node_id"] == "node-t2"
    assert topics[0]["spoken_script"] == "generated script"
    assert "spoken_script" not in topics[1]  # 生成対象でなかったトピックは触らない


def test_teacher_edit_to_generated_field_during_build_is_kept_with_note():
    def _edit_material(db: _FakeDb) -> None:
        data = db.data
        data["topics"][0]["student_material"] = {"source_format": "eg-markdown-v1", "source_text": "教員が書いた本文"}
        db.write(data)

    db = _FakeDb(_registered_course())
    _run_build(db, during_draft=_edit_material)
    data = db.data
    assert data["topics"][0]["student_material"]["source_text"] == "教員が書いた本文"
    assert data["topics"][1]["student_material"]["source_text"].startswith("generated text")
    assert data["course_content_status"]["concurrent_edits_note"] == ccb.CONCURRENT_EDITS_KEPT_NOTE


def test_merge_is_pure_and_only_takes_builder_owned_keys():
    snapshot = {"title": "c", "topics": [{"id": "t1", "title": "T"}]}
    built = {
        "title": "c",
        "cartridge_id": None,
        "course_content_status": {"status": "completed"},
        "topics": [{"id": "t1", "title": "T", "summary": "s", "atlas_node_id": None}],
    }
    live = {
        "title": "renamed",
        "cartridge_id": "astrophysics",
        "atlas_binding_pending": "",
        "topics": [{"id": "t1", "title": "T", "atlas_node_id": "n1", "prerequisites": ["x"]}, "junk"],
    }
    live_before = json.dumps(live)
    merged = ccb.merge_built_course_into_live(live, snapshot, built)
    assert json.dumps(live) == live_before  # 入力を変えない
    assert merged["title"] == "renamed"
    assert merged["cartridge_id"] == "astrophysics"
    assert merged["atlas_binding_pending"] == ""
    assert merged["course_content_status"] == {"status": "completed"}
    assert merged["topics"][0] == {
        "id": "t1", "title": "T", "atlas_node_id": "n1", "prerequisites": ["x"], "summary": "s",
    }
    assert merged["topics"][1] == "junk"


def test_waiting_for_pipeline_write_also_preserves_binding():
    db = _FakeDb(_registered_course())
    session = _FakeSession(db)

    orig_execute = session.execute

    def _execute(query, params=None):
        sql = str(query)
        if "document_id::text" in sql and "chunk_index ASC" not in sql:
            # 解析済み document が無い → waiting_for_pipeline。その直前に割り当てが保存される。
            _bind_atlas(db)
            result = MagicMock()
            result.fetchall.return_value = []
            return result
        return orig_execute(query, params)

    session.execute = _execute
    with patch("core.course_content_builder._pg_session", return_value=session):
        result = ccb.build_course_content(USER_ID, COURSE_ID)
    assert result["status"] == "waiting_for_pipeline"
    data = db.data
    assert data["course_content_status"]["status"] == "waiting_for_pipeline"
    assert data["cartridge_id"] == "astrophysics"
    assert all(t.get("atlas_node_id") for t in data["topics"])


# ---------------------------------------------------------------------------
# IK-0375: 生成中・失敗の状態を行に残す
# ---------------------------------------------------------------------------


def test_processing_status_is_persisted_before_generation():
    seen: dict = {}

    def _observe(db: _FakeDb) -> None:
        seen["status"] = db.data["course_content_status"]["status"]

    db = _FakeDb(_registered_course())
    _run_build(db, during_draft=_observe)
    assert seen["status"] == "processing"
    assert db.status_writes and db.status_writes[0]["status"] == "processing"
    assert "updated_at" in db.status_writes[0]
    assert db.data["course_content_status"]["status"] == "completed"


def test_processing_write_touches_only_the_status_key():
    """状態の記帳は jsonb_set の1文（他のキーを読み直して書き戻さない）。"""
    session = MagicMock()
    ccb._persist_content_status(session, COURSE_ID, {"status": "processing"})
    sql = str(session.execute.call_args.args[0])
    assert "jsonb_set" in sql and "{course_content_status}" in sql
    assert "title" not in sql
    session.commit.assert_called_once()


def test_failed_status_is_persisted_when_build_raises():
    db = _FakeDb(_registered_course())
    with pytest.raises(RuntimeError):
        _run_build(db, draft_error=RuntimeError("llm down"))
    status = db.data["course_content_status"]
    assert status["status"] == "failed"
    assert status["message"] == ccb.CONTENT_BUILD_FAILED_MESSAGE
    assert "llm down" not in json.dumps(status, ensure_ascii=False)
    assert not any(ch.isdigit() for ch in status["message"])
    # 失敗の記帳は他のキーを消さない
    assert db.data["course_focus"] == "暗黒物質ハローの形成をめぐる議論"


def test_background_entrypoint_swallows_and_records_failure():
    db = _FakeDb(_registered_course())
    session = _FakeSession(db)
    with patch("core.course_content_builder._pg_session", return_value=session), \
         patch("core.document_pipeline.persistence.resolve_artifact_runs", return_value=_FAKE_ARTIFACTS), \
         patch("core.course_content_builder._generate_course_topic_drafts", side_effect=RuntimeError("x")):
        ccb.build_course_content_background(USER_ID, COURSE_ID)  # 例外を外に出さない
    assert db.data["course_content_status"]["status"] == "failed"


def test_not_found_course_writes_no_status():
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = None
    with patch("core.course_content_builder._pg_session", return_value=session):
        assert ccb.build_course_content(USER_ID, COURSE_ID)["status"] == "not_found"
    for call in session.execute.call_args_list:
        assert "jsonb_set" not in str(call.args[0])


# ---------------------------------------------------------------------------
# ガードレール: builder がトピックに書くキーは BUILDER_OWNED_TOPIC_KEYS に入っている
# ---------------------------------------------------------------------------


def _topic_keys_written(func) -> set[str]:
    tree = ast.parse(inspect.getsource(func).lstrip())
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "topic"
                    and isinstance(target.slice, ast.Constant)
                ):
                    keys.add(target.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "topic"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            keys.update(k.value for k in node.args[0].keys if isinstance(k, ast.Constant))
    return keys


def test_every_topic_key_the_builder_writes_is_declared_as_owned():
    written = set()
    for func in (
        ccb._enrich_topics,
        ccb._generate_course_topic_drafts,
        ccb._apply_deterministic_topic_draft_fallback,
    ):
        written |= _topic_keys_written(func)
    assert written, "AST walk found no writes — the guard would be vacuous"
    assert written <= ccb.BUILDER_OWNED_TOPIC_KEYS, sorted(written - ccb.BUILDER_OWNED_TOPIC_KEYS)


def test_non_builder_keys_are_not_owned():
    for key in ("atlas_node_id", "id", "title", "prerequisites", "chapter_index"):
        assert key not in ccb.BUILDER_OWNED_TOPIC_KEYS
    for key in ("cartridge_id", "course_focus", "llm_models", "atlas_binding_pending", "topics", "title"):
        assert key not in ccb.BUILDER_OWNED_COURSE_KEYS
