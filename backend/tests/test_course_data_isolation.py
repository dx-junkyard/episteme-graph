"""Issue #111: コース間データ隔離の結合テスト。

複数の教材・コースが存在する環境下で、あるコースのデータを取得した際に
別のコースや教材のデータが混入していないことを検証する。

対象エンドポイント:
    - GET  /api/admin/courses/{course_id}/draft-format         (教員: コース構築画面向け)
    - GET  /api/admin/courses/{course_id}/lecture-scripts      (教員: 原稿スタジオ)
    - GET  /api/learning/courses/{course_id}                   (学生: 学習画面)
    - GET  /api/learning/courses/{cid}/topics/{tid}/chat       (学生: チャット履歴)

テスト戦略:
    FastAPI TestClient を使用し、認証依存関係 (_get_current_user / _require_teacher)
    を app.dependency_overrides で差し替える。DB 層 (get_course_data / _get_course_chunks
    / _pg_session) は教材A/コースA と 教材B/コースB の 2 セットの in-memory データを
    返すようモンキーパッチし、エンドポイントを純粋にルーティング・フィルタリングの観点で検証する。
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

# tests/conftest.py と同様に api/ を sys.path に追加する
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))


# ---------------------------------------------------------------------------
# Fixture 用 in-memory データセット: 教材A/コースA, 教材B/コースB
# ---------------------------------------------------------------------------

TEACHER_USER = {
    "id": "00000000-0000-0000-0000-00000000aaaa",
    "username": "teacher",
    "email": "teacher@test.local",
    "role": "TEACHER",
}

STUDENT_USER = {
    "id": "00000000-0000-0000-0000-00000000bbbb",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}

COURSE_A_ID = "course-aaa"
COURSE_B_ID = "course-bbb"
MATERIAL_A_ID = "mat-A-001"
MATERIAL_B_ID = "mat-B-001"

# 教材Aのチャンク (3件)
CHUNKS_MATERIAL_A = [
    {
        "id": "00000000-a000-0000-0000-000000000001",
        "chunk_index": 0,
        "text": "教材A: イントロダクション",
        "spoken_text": "教材Aの第一チャンクです",
        "formulas": [],
    },
    {
        "id": "00000000-a000-0000-0000-000000000002",
        "chunk_index": 1,
        "text": "教材A: 本文1",
        "spoken_text": "",
        "formulas": [],
    },
    {
        "id": "00000000-a000-0000-0000-000000000003",
        "chunk_index": 2,
        "text": "教材A: 結論",
        "spoken_text": "",
        "formulas": [],
    },
]

# 教材Bのチャンク (2件)
CHUNKS_MATERIAL_B = [
    {
        "id": "00000000-b000-0000-0000-000000000001",
        "chunk_index": 0,
        "text": "教材B: 前提",
        "spoken_text": "教材Bの第一チャンクです",
        "formulas": [],
    },
    {
        "id": "00000000-b000-0000-0000-000000000002",
        "chunk_index": 1,
        "text": "教材B: 応用",
        "spoken_text": "",
        "formulas": [],
    },
]

COURSE_DATA_A = {
    "id": COURSE_A_ID,
    "title": "コースA",
    "chapters": [{"title": "第1章 (コースA)", "status": "locked", "progress_pct": 0}],
    "topics": [
        {
            "id": "topic-A-1",
            "title": "コースAトピック1",
            "chapter_index": 0,
            "status": "locked",
            "prerequisites": [],
            "misconceptions": [],
        }
    ],
    "concepts": [{"name": "概念A1", "status": "future", "children": [], "expanded": False}],
    "sources": [
        {
            "title": "教材A",
            "subtitle": "",
            "license": "",
            "used_section": "",
            "material_id": MATERIAL_A_ID,
        }
    ],
    "referenced_sections": [],
}

COURSE_DATA_B = {
    "id": COURSE_B_ID,
    "title": "コースB",
    "chapters": [{"title": "第1章 (コースB)", "status": "locked", "progress_pct": 0}],
    "topics": [
        {
            "id": "topic-B-1",
            "title": "コースBトピック1",
            "chapter_index": 0,
            "status": "locked",
            "prerequisites": [],
            "misconceptions": [],
        }
    ],
    "concepts": [{"name": "概念B1", "status": "future", "children": [], "expanded": False}],
    "sources": [
        {
            "title": "教材B",
            "subtitle": "",
            "license": "",
            "used_section": "",
            "material_id": MATERIAL_B_ID,
        }
    ],
    "referenced_sections": [],
}


# ---------------------------------------------------------------------------
# 軽量な PG セッション/Result スタブ
# ---------------------------------------------------------------------------


class _FakeResult:
    """SQLAlchemy の Result オブジェクト互換スタブ。"""

    def __init__(self, rows):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """routes.admin._pg_session() が返すセッションのスタブ。

    draft-format エンドポイントが発行する `SELECT data, title FROM learning_courses ...`
    と、学習チャット履歴用の `SELECT history FROM learning_chat_history ...` を
    解釈して in-memory データを返す。それ以外のクエリは空のレコードを返す。
    """

    def execute(self, query, params=None):
        sql = str(query)
        params = params or {}

        if "FROM learning_courses" in sql and "data, title" in sql:
            cid = params.get("course_id")
            if cid == COURSE_A_ID:
                return _FakeResult([(copy.deepcopy(COURSE_DATA_A), "コースA")])
            if cid == COURSE_B_ID:
                return _FakeResult([(copy.deepcopy(COURSE_DATA_B), "コースB")])
            return _FakeResult([])

        if "FROM learning_chat_history" in sql:
            # 履歴はすべて空 (テストシナリオでは未回答状態)
            return _FakeResult([])

        return _FakeResult([])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_users(monkeypatch):
    """TestClient と現在ユーザー切替ハンドルを返す統合フィクスチャ。

    - 認証依存関係を差し替え
    - get_course_data / _get_course_chunks をモンキーパッチ
    - 管理者/学習ルーターの _pg_session を _FakeSession に置換
    """
    from fastapi.testclient import TestClient

    # import は fixture 内で実施（conftest の _override_settings 後である必要があるため）
    from main import app
    from dependencies import _get_current_user, _require_teacher

    # 現在ユーザーを保持するホルダー (テスト内で切り替え可能)
    current = {"user": TEACHER_USER}

    def _override_current_user():
        return current["user"]

    def _override_require_teacher():
        # ロール検査を省略してそのまま返す
        return current["user"]

    app.dependency_overrides[_get_current_user] = _override_current_user
    app.dependency_overrides[_require_teacher] = _override_require_teacher

    # --- get_course_data の差し替え -------------------------------------------------
    def _fake_get_course_data(user_id, course_id):
        if course_id == COURSE_A_ID:
            return copy.deepcopy(COURSE_DATA_A)
        if course_id == COURSE_B_ID:
            return copy.deepcopy(COURSE_DATA_B)
        return None

    # `from services import get_course_data` で取り込まれた名前を各モジュールで差し替える
    monkeypatch.setattr("routes.learning.get_course_data", _fake_get_course_data)
    monkeypatch.setattr("routes.lecture_studio.get_course_data", _fake_get_course_data)
    monkeypatch.setattr("routes.lecture.get_course_data", _fake_get_course_data)
    monkeypatch.setattr("services.get_course_data", _fake_get_course_data)

    # --- _get_course_chunks の差し替え ---------------------------------------------
    def _fake_get_course_chunks(course_data):
        """course_data["sources"][*]["material_id"] を解釈して対応チャンクだけを返す。"""
        material_ids = {
            s.get("material_id")
            for s in course_data.get("sources", [])
            if s.get("material_id")
        }
        chunks = []
        if MATERIAL_A_ID in material_ids:
            chunks.extend(copy.deepcopy(CHUNKS_MATERIAL_A))
        if MATERIAL_B_ID in material_ids:
            chunks.extend(copy.deepcopy(CHUNKS_MATERIAL_B))
        return chunks

    monkeypatch.setattr("routes.lecture_studio._get_course_chunks", _fake_get_course_chunks)

    # _chunk_status は lecture_audio_cache テーブルを参照するため、シンプルなスタブに
    monkeypatch.setattr(
        "routes.lecture_studio._chunk_status",
        lambda chunk: "generated" if chunk.get("spoken_text") else "ungenerated",
    )

    # --- _pg_session の差し替え -----------------------------------------------------
    # admin.draft-format / learning.chat-history が直接 SQL を実行するため
    monkeypatch.setattr("routes.admin._pg_session", lambda: _FakeSession())
    monkeypatch.setattr("routes.learning._pg_session", lambda: _FakeSession())

    # lifespan を発火させないため `with` ブロックを使わずに TestClient を生成する
    client = TestClient(app)

    try:
        yield client, current
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# ヘルパー: IDセットを抽出
# ---------------------------------------------------------------------------


def _material_a_chunk_ids() -> set[str]:
    return {c["id"] for c in CHUNKS_MATERIAL_A}


def _material_b_chunk_ids() -> set[str]:
    return {c["id"] for c in CHUNKS_MATERIAL_B}


# ---------------------------------------------------------------------------
# テスト1: 教材管理とコース構築の整合性検証
# ---------------------------------------------------------------------------


class TestCourseBuilderIsolation:
    """教員権限でコース構築情報を取得した際、他コースの教材データが混入しないことを検証する。

    対象エンドポイント: GET /api/admin/courses/{course_id}/draft-format
    """

    def test_course_a_draft_only_references_material_a(self, client_and_users):
        client, current = client_and_users
        current["user"] = TEACHER_USER

        resp = client.get(
            f"/api/admin/courses/{COURSE_A_ID}/draft-format",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert body["course_id"] == COURSE_A_ID
        assert body["course_title"] == "コースA"

        draft = body["course_draft"]
        source_material_ids = {s.get("material_id", "") for s in draft.get("sources", [])}

        # コースAの material_id のみが含まれること
        assert MATERIAL_A_ID in source_material_ids
        # コースBの material_id が一切混入していないこと
        assert MATERIAL_B_ID not in source_material_ids

        # チャプター・概念のラベルにもコースBの痕跡が無いこと
        all_text_blob = str(draft)
        assert "コースB" not in all_text_blob
        assert "教材B" not in all_text_blob

    def test_course_b_draft_only_references_material_b(self, client_and_users):
        client, current = client_and_users
        current["user"] = TEACHER_USER

        resp = client.get(
            f"/api/admin/courses/{COURSE_B_ID}/draft-format",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 200, resp.text

        draft = resp.json()["course_draft"]
        source_material_ids = {s.get("material_id", "") for s in draft.get("sources", [])}

        assert MATERIAL_B_ID in source_material_ids
        assert MATERIAL_A_ID not in source_material_ids

        all_text_blob = str(draft)
        assert "コースA" not in all_text_blob
        assert "教材A" not in all_text_blob


# ---------------------------------------------------------------------------
# テスト2: 原稿スタジオのデータ隔離検証
# ---------------------------------------------------------------------------


class TestLectureStudioIsolation:
    """原稿スタジオ API で、他コース由来のチャンクが一切混入しないことを検証する。

    対象エンドポイント: GET /api/admin/courses/{course_id}/lecture-scripts
    """

    def test_course_a_lecture_scripts_only_contain_material_a_chunks(self, client_and_users):
        client, current = client_and_users
        current["user"] = TEACHER_USER

        resp = client.get(
            f"/api/admin/courses/{COURSE_A_ID}/lecture-scripts",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 200, resp.text

        chunks = resp.json()
        assert isinstance(chunks, list)

        # 件数が教材Aのチャンク数と一致
        assert len(chunks) == len(CHUNKS_MATERIAL_A)

        returned_ids = {c["chunk_id"] for c in chunks}
        # すべて教材A由来
        assert returned_ids == _material_a_chunk_ids()
        # 教材Bのチャンクが一切含まれないこと (厳格な not-in チェック)
        for bid in _material_b_chunk_ids():
            assert bid not in returned_ids

        # テキスト本文にも教材Bの痕跡がないこと
        joined_text = "\n".join(c.get("text", "") for c in chunks)
        assert "教材B" not in joined_text

    def test_course_b_lecture_scripts_only_contain_material_b_chunks(self, client_and_users):
        client, current = client_and_users
        current["user"] = TEACHER_USER

        resp = client.get(
            f"/api/admin/courses/{COURSE_B_ID}/lecture-scripts",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 200, resp.text

        chunks = resp.json()
        assert len(chunks) == len(CHUNKS_MATERIAL_B)

        returned_ids = {c["chunk_id"] for c in chunks}
        assert returned_ids == _material_b_chunk_ids()
        for aid in _material_a_chunk_ids():
            assert aid not in returned_ids

        joined_text = "\n".join(c.get("text", "") for c in chunks)
        assert "教材A" not in joined_text

    def test_lecture_scripts_do_not_cross_contaminate_between_calls(self, client_and_users):
        """A→B の順で連続アクセスした際、Bのレスポンスに A のチャンクIDが残らないこと。"""
        client, current = client_and_users
        current["user"] = TEACHER_USER

        resp_a = client.get(
            f"/api/admin/courses/{COURSE_A_ID}/lecture-scripts",
            headers={"Authorization": "Bearer dummy"},
        )
        resp_b = client.get(
            f"/api/admin/courses/{COURSE_B_ID}/lecture-scripts",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        ids_a = {c["chunk_id"] for c in resp_a.json()}
        ids_b = {c["chunk_id"] for c in resp_b.json()}

        # 積集合は空 (完全分離)
        assert ids_a.isdisjoint(ids_b)


# ---------------------------------------------------------------------------
# テスト3: 学生向け学習画面のデータ隔離検証
# ---------------------------------------------------------------------------


class TestLearningCourseIsolation:
    """学生権限で学習画面 API を叩いた際、他コースのデータが混入しないことを検証する。

    対象エンドポイント:
        - GET /api/learning/courses/{course_id}
        - GET /api/learning/courses/{cid}/topics/{tid}/chat
    """

    def test_student_course_a_detail_excludes_course_b_data(self, client_and_users):
        client, current = client_and_users
        current["user"] = STUDENT_USER

        resp = client.get(
            f"/api/learning/courses/{COURSE_A_ID}",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 200, resp.text

        detail = resp.json()
        assert detail["id"] == COURSE_A_ID
        assert detail["title"] == "コースA"

        # トピック / 概念 / ソースすべて コースA由来のみ
        topic_ids = {t["id"] for t in detail.get("topics", [])}
        concept_names = {c["name"] for c in detail.get("concepts", [])}
        source_material_ids = {s.get("material_id") for s in detail.get("sources", [])}

        assert topic_ids == {"topic-A-1"}
        assert "topic-B-1" not in topic_ids
        assert concept_names == {"概念A1"}
        assert "概念B1" not in concept_names
        assert source_material_ids == {MATERIAL_A_ID}
        assert MATERIAL_B_ID not in source_material_ids

        # 念のためレスポンス全体を文字列化し、コースBの文字列が一切出現しないこと
        blob = str(detail)
        assert COURSE_B_ID not in blob
        assert "コースB" not in blob
        assert "教材B" not in blob

    def test_student_course_b_detail_excludes_course_a_data(self, client_and_users):
        client, current = client_and_users
        current["user"] = STUDENT_USER

        resp = client.get(
            f"/api/learning/courses/{COURSE_B_ID}",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 200, resp.text

        detail = resp.json()
        assert detail["id"] == COURSE_B_ID
        assert detail["title"] == "コースB"

        topic_ids = {t["id"] for t in detail.get("topics", [])}
        concept_names = {c["name"] for c in detail.get("concepts", [])}
        source_material_ids = {s.get("material_id") for s in detail.get("sources", [])}

        assert topic_ids == {"topic-B-1"}
        assert "topic-A-1" not in topic_ids
        assert concept_names == {"概念B1"}
        assert "概念A1" not in concept_names
        assert source_material_ids == {MATERIAL_B_ID}
        assert MATERIAL_A_ID not in source_material_ids

        blob = str(detail)
        assert COURSE_A_ID not in blob
        assert "コースA" not in blob
        assert "教材A" not in blob

    def test_student_chat_history_isolated_by_course(self, client_and_users):
        """チャット履歴取得時、コース/トピックが一致しない場合でも他コースの履歴が漏れないこと。

        _FakeSession は履歴を空で返すため 200 + history=[] が返ることを検証する。
        """
        client, current = client_and_users
        current["user"] = STUDENT_USER

        resp_a = client.get(
            f"/api/learning/courses/{COURSE_A_ID}/topics/topic-A-1/chat",
            headers={"Authorization": "Bearer dummy"},
        )
        resp_b = client.get(
            f"/api/learning/courses/{COURSE_B_ID}/topics/topic-B-1/chat",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        # 履歴はいずれも空 (他コースの履歴が混入していないこと)
        assert resp_a.json() == {"history": []}
        assert resp_b.json() == {"history": []}

    def test_student_requests_switching_courses_never_mix_data(self, client_and_users):
        """コースA→コースB の順で連続アクセスしてもクロスコンタミが発生しないこと。"""
        client, current = client_and_users
        current["user"] = STUDENT_USER

        # コースAを取得
        first = client.get(
            f"/api/learning/courses/{COURSE_A_ID}",
            headers={"Authorization": "Bearer dummy"},
        )
        # 直後にコースBを取得
        second = client.get(
            f"/api/learning/courses/{COURSE_B_ID}",
            headers={"Authorization": "Bearer dummy"},
        )
        assert first.status_code == 200
        assert second.status_code == 200

        a_topics = {t["id"] for t in first.json().get("topics", [])}
        b_topics = {t["id"] for t in second.json().get("topics", [])}
        a_materials = {s["material_id"] for s in first.json().get("sources", [])}
        b_materials = {s["material_id"] for s in second.json().get("sources", [])}

        assert a_topics.isdisjoint(b_topics)
        assert a_materials.isdisjoint(b_materials)
