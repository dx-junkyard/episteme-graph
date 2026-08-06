"""教材図スタジオ API（`routes/teaching_figures.py`）+ 既存経路統合のテスト。

正本: `docs/features/teaching_figure_studio_design.md` §7（埋め込み・配信）/ §8（API）。

対象と検証観点:
  - 保存 → 採用（adopt）時のトピック側登録（§7.1b の要石: ``linked_figure_ids`` +
    ``evidence_links`` + ``learning_courses.data`` の同一トランザクション更新）
  - コース所有者 / SYSTEM_ADMIN 以外は 403（§8 の権限非対称）
  - サニタイズ拒否は 422（除去ではなく保存させない・FG3）で MinIO も DB も触らない
  - 学習者向け配信の4条件 fail-closed（受講 / course_id 一致 / 本文参照 / adopted・FG4）
  - SVG 配信の ``nosniff`` + CSP sandbox ヘッダ（FG3 第3防御）
  - 提案レスポンスに confidence の生値が出ない（FG8）
  - AI 書き換え後の図 embed の決定論復元（§7.1b-3）
  - ``figures_index`` の教材図マージが document 走査の早期 return より前で効く（§7.1）
  - コース物理削除の全経路に教材図の孤児掃除がある（§3.1）

DB / MinIO / LLM は呼び出さない（境界のみモックし、判定ロジックは実データで検証する）。
test_learner_figure_delivery.py と同型の「関数直呼び + モジュール属性パッチ」方式。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException  # noqa: E402

_COURSE_ID = "course-1"
_TOPIC_ID = "topic-1"
_FIG_ID = "550e8400-e29b-41d4-a716-446655440101"
_DOC_ID = "550e8400-e29b-41d4-a716-446655440202"
_OWNER = {"id": "owner-1", "role": "TEACHER"}
_OTHER = {"id": "other-1", "role": "TEACHER"}
_ADMIN = {"id": "admin-1", "role": "SYSTEM_ADMIN"}

_VALID_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    '<rect x="10" y="10" width="80" height="40" fill="#eee"/>'
    '<text x="20" y="35">熱平衡</text></svg>'
)
_SCRIPT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    '<script>alert(1)</script></svg>'
)


# ---------------------------------------------------------------------------
# フェイク境界
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FakeSession:
    """execute された SQL を記録するだけのフェイクセッション。

    ``events`` を渡すと commit の発生順を共有リストへ追記する（MinIO アップロードとの
    前後関係を検証するため）。
    """

    def __init__(self, row=None, events=None):
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0
        self._row = row
        self._events = events if events is not None else []

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        self.params.append(params or {})
        return _FakeResult(row=self._row)

    def commit(self):
        self.committed += 1
        self._events.append("commit")

    def rollback(self):
        self.rolled_back += 1
        self._events.append("rollback")

    def close(self):
        self.closed += 1


class _FakeStorage:
    def __init__(self, payload=b"<svg/>", fail_get=False, fail_upload=False, events=None):
        self.uploads: list[tuple] = []
        self.removed: list[tuple] = []
        self._payload = payload
        self._fail_get = fail_get
        self._fail_upload = fail_upload
        self._events = events if events is not None else []

    def upload_bytes(self, bucket, key, data, *, content_type="application/octet-stream"):
        self._events.append("upload")
        if self._fail_upload:
            raise RuntimeError("minio down")
        self.uploads.append((bucket, key, data, content_type))
        return key

    def get_object(self, bucket, key):
        if self._fail_get:
            raise RuntimeError("minio down")
        return self._payload

    def remove_object(self, bucket, key):
        self.removed.append((bucket, key))


def _course_data(topics=None, chapters=None):
    data: dict = {"id": _COURSE_ID, "title": "コース", "topics": topics if topics is not None else []}
    if chapters is not None:
        data["chapters"] = chapters
    return data


def _topic(topic_id=_TOPIC_ID, **extra):
    topic = {"id": topic_id, "title": "トピック", "student_material": {"source_text": "本文"}}
    topic.update(extra)
    return topic


@pytest.fixture
def tf():
    import routes.teaching_figures as module

    return module


@pytest.fixture(autouse=True)
def _reset_cost_gates(monkeypatch):
    """プロセスローカル CostGate をテストごとに新しくする。"""
    import routes.teaching_figures as module
    from core.llm_worker.cost_gate import CostGate

    monkeypatch.setattr(module, "_figure_studio_cost_gate", CostGate())
    monkeypatch.setattr(module, "_figure_suggest_cost_gate", CostGate())


#: ``store._row_to_figure`` が返すキー。テストスタブがこれ以外のキー（例: 実テーブルに
#: 存在しない ``updated_by``）を行に混ぜると、実 DB では起きない挙動を隠してしまう。
_FIGURE_ROW_KEYS = (
    "id", "course_id", "topic_id", "created_by", "title", "caption", "figure_kind",
    "svg_source", "minio_key", "content_type", "status", "source_suggestion_id",
    "revisions", "revisions_truncated", "created_at", "updated_at",
)

#: ``store.update_teaching_figure`` が受け付けるキーワード（route が渡してよいもの）。
_UPDATE_KWARGS = ("title", "caption", "svg_source", "minio_key", "status", "updated_by")


@pytest.fixture
def stub_create(tf, monkeypatch):
    """保存経路の境界（store / MinIO / 監査）をまとめて差し替える。

    ``events`` には commit と MinIO アップロードの発生順を記録する（正本 = DB を先に
    確定させる順序をテストで固定するため）。
    """
    events: list[str] = []
    state: dict = {
        "session": _FakeSession(events=events),
        "storage": _FakeStorage(events=events),
        "created": [], "audits": [], "audit_metadata": [], "suggestion_status": [],
        "events": events,
        "suggestion": None,
    }

    def _create(session, **kw):
        state["created"].append(kw)
        row = {
            "id": kw.get("figure_id") or _FIG_ID,
            "course_id": kw["course_id"],
            "topic_id": kw.get("topic_id") or "",
            "title": kw.get("title") or "",
            # 実 store は figure_kind に応じて caption を整える（FG5）。ここでは
            # 引数をそのまま返し、route が「行の caption」を読むことだけを検証する。
            "caption": kw.get("caption") or "",
            "figure_kind": kw.get("figure_kind") or "",
            "status": kw.get("status") or "",
            "minio_key": kw.get("minio_key") or "",
            "content_type": kw.get("content_type") or "",
            "created_at": "2026-07-31T00:00:00+00:00",
        }
        assert set(row) <= set(_FIGURE_ROW_KEYS)
        return row

    def _set_status(session, sid, status, *, course_id):
        state["suggestion_status"].append((sid, status, course_id))
        return {"id": sid, "course_id": course_id, "status": status, "figure_kind": "concept_map"}

    def _record(*args, **kw):
        state["audits"].append(args)
        state["audit_metadata"].append(args[5] if len(args) > 5 else {})

    monkeypatch.setattr(tf, "_pg_session", lambda: state["session"])
    monkeypatch.setattr(tf, "get_storage_client", lambda: state["storage"])
    monkeypatch.setattr(tf, "create_teaching_figure", _create)
    monkeypatch.setattr(tf, "get_suggestion", lambda session, sid: state["suggestion"])
    monkeypatch.setattr(tf, "set_suggestion_status", _set_status)
    monkeypatch.setattr(tf, "record_review_event", _record)
    return state


# ---------------------------------------------------------------------------
# 保存 → 採用（§7.1b）
# ---------------------------------------------------------------------------


class TestCreateTeachingFigureAdoption:
    def _body(self, tf, **kw):
        payload = {
            "svg_source": _VALID_SVG,
            "title": "熱平衡の3段階",
            "caption": "熱平衡に至る過程の模式図",
            "figure_kind": "process_flow",
            "topic_id": _TOPIC_ID,
            "adopt": True,
        }
        payload.update(kw)
        return tf.TeachingFigureCreateRequest(**payload)

    def test_adopt_registers_topic_references_and_saves_course_data(self, tf, monkeypatch, stub_create):
        topic = _topic()
        course_data = _course_data([topic])
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: course_data)

        result = tf.create_course_teaching_figure(_COURSE_ID, self._body(tf), _OWNER)

        # 行は adopted で作られる
        assert stub_create["created"][0]["status"] == "adopted"
        # §7.1b-1: linked_figure_ids
        assert topic["linked_figure_ids"] == [result["figure_id"]]
        assert result["linked_figure_ids"] == [result["figure_id"]]
        # §7.1b-2: evidence_links（既存の読み手が読むトップレベル + 設計指定の extra）
        link = result["evidence_link"]
        assert link["kind"] == "figure"
        assert link["target_id"] == result["figure_id"]
        assert link["figure_id"] == result["figure_id"]
        assert link["caption"] == "熱平衡に至る過程の模式図"
        assert link["extra"]["teaching"] is True
        assert topic["evidence_links"] == [link]
        # 同一トランザクションで learning_courses.data を更新し、1回だけ commit する
        assert any("UPDATE learning_courses" in s for s in stub_create["session"].statements)
        assert stub_create["session"].committed == 1
        # MinIO キーは teaching/{course_id}/{figure_id}.svg（抽出図と prefix が衝突しない）
        bucket, key, data, content_type = stub_create["storage"].uploads[0]
        assert bucket == "figure-images"
        assert key == f"teaching/{_COURSE_ID}/{result['figure_id']}.svg"
        assert content_type == "image/svg+xml"
        assert b"<svg" in data
        # 監査に entity_type='teaching_figure' が記帳される
        assert stub_create["audits"][0][0] == "teaching_figure"

    def test_adopt_finds_topic_nested_under_chapters(self, tf, monkeypatch, stub_create):
        """章ネスト形 chapters[].topics[] のトピックにも採用できる（iter_all_topics）。"""
        topic = _topic("nested-1")
        course_data = _course_data([], chapters=[{"title": "章1", "topics": [topic]}])
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: course_data)

        result = tf.create_course_teaching_figure(
            _COURSE_ID, self._body(tf, topic_id="nested-1"), _OWNER,
        )
        assert topic["linked_figure_ids"] == [result["figure_id"]]

    def test_draft_save_does_not_touch_topic(self, tf, monkeypatch, stub_create):
        topic = _topic()
        course_data = _course_data([topic])
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: course_data)

        result = tf.create_course_teaching_figure(
            _COURSE_ID, self._body(tf, adopt=False), _OWNER,
        )

        assert stub_create["created"][0]["status"] == "draft"
        assert result["evidence_link"] is None
        assert result["linked_figure_ids"] == []
        assert "linked_figure_ids" not in topic
        assert not any("UPDATE learning_courses" in s for s in stub_create["session"].statements)

    def test_adopt_without_topic_id_is_422(self, tf, monkeypatch, stub_create):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.create_course_teaching_figure(_COURSE_ID, self._body(tf, topic_id=""), _OWNER)
        assert exc.value.status_code == 422
        assert stub_create["created"] == []

    def test_adopt_with_unknown_topic_is_404(self, tf, monkeypatch, stub_create):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.create_course_teaching_figure(
                _COURSE_ID, self._body(tf, topic_id="missing"), _OWNER,
            )
        assert exc.value.status_code == 404
        assert stub_create["created"] == []

    def test_script_svg_is_rejected_422_without_touching_storage_or_db(
        self, tf, monkeypatch, stub_create,
    ):
        """FG3: サニタイズ拒否は除去ではなく 422。MinIO も DB も触らない（fail-closed）。"""
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))

        with pytest.raises(HTTPException) as exc:
            tf.create_course_teaching_figure(
                _COURSE_ID, self._body(tf, svg_source=_SCRIPT_SVG), _OWNER,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail  # 教員に出せる事実文
        assert stub_create["storage"].uploads == []
        assert stub_create["created"] == []
        assert stub_create["session"].committed == 0

    def test_invalid_figure_kind_is_422(self, tf, monkeypatch, stub_create):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.create_course_teaching_figure(
                _COURSE_ID, self._body(tf, figure_kind="mermaid_diagram"), _OWNER,
            )
        assert exc.value.status_code == 422

    def test_accepting_suggestion_transitions_it(self, tf, monkeypatch, stub_create):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        stub_create["suggestion"] = {
            "id": "sug-1", "course_id": _COURSE_ID, "status": "candidate",
        }
        tf.create_course_teaching_figure(
            _COURSE_ID, self._body(tf, suggestion_id="sug-1"), _OWNER,
        )
        # 遷移は必ずコーススコープ付きで発行する（store が id だけで一致させない）。
        assert stub_create["suggestion_status"] == [("sug-1", "accepted", _COURSE_ID)]
        assert stub_create["created"][0]["source_suggestion_id"] == "sug-1"

    def test_other_course_suggestion_has_no_side_effect(self, tf, monkeypatch, stub_create):
        """他コースの提案 id を送られても accepted にしない（副作用ゼロ）。

        turn 側 ``_load_suggestion`` と同じ扱い（スコープ外は無かったものとして続行）。
        図の作成自体は成功するが、由来としても記録しない（帰属の捏造をしない）。
        """
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        stub_create["suggestion"] = {
            "id": "sug-x", "course_id": "another-course", "status": "candidate",
        }

        result = tf.create_course_teaching_figure(
            _COURSE_ID, self._body(tf, suggestion_id="sug-x"), _OWNER,
        )

        assert result["figure_id"]
        assert stub_create["suggestion_status"] == []
        assert stub_create["created"][0]["source_suggestion_id"] is None
        # 事実は監査に残す（黙って捨てない）。
        assert stub_create["audit_metadata"][0]["suggestion_out_of_scope"] is True
        assert stub_create["audit_metadata"][0]["suggestion_id"] == ""

    def test_unknown_suggestion_id_is_ignored(self, tf, monkeypatch, stub_create):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        stub_create["suggestion"] = None
        tf.create_course_teaching_figure(
            _COURSE_ID, self._body(tf, suggestion_id="00000000-0000-0000-0000-000000000000"), _OWNER,
        )
        assert stub_create["suggestion_status"] == []
        assert stub_create["created"][0]["source_suggestion_id"] is None

    def test_evidence_link_uses_the_stored_caption(self, tf, monkeypatch, stub_create):
        """FG5: store が caption を整える場合があるので、登録・応答は行の値を使う。"""
        topic = _topic()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        def _create(session, **kw):
            return {
                "id": _FIG_ID, "course_id": kw["course_id"], "topic_id": kw.get("topic_id") or "",
                "title": "", "caption": "傾向のイメージ（模式図）", "figure_kind": kw["figure_kind"],
                "status": kw["status"], "minio_key": kw["minio_key"], "content_type": "",
                "created_at": "",
            }

        monkeypatch.setattr(tf, "create_teaching_figure", _create)

        result = tf.create_course_teaching_figure(
            _COURSE_ID,
            self._body(tf, caption="傾向のイメージ", figure_kind="data_plot_schematic"),
            _OWNER,
        )
        assert result["evidence_link"]["caption"] == "傾向のイメージ（模式図）"
        assert result["evidence_link"]["extra"]["caption"] == "傾向のイメージ（模式図）"
        assert result["figure"]["caption"] == "傾向のイメージ（模式図）"

    def test_snapshot_upload_happens_after_the_db_commit(self, tf, monkeypatch, stub_create):
        """正本は DB。配信スナップショット（MinIO）は commit 後に上げる。

        逆順だと DB 側が失敗したときに「配信物だけ新版・正本は旧版」が残る。
        """
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        tf.create_course_teaching_figure(_COURSE_ID, self._body(tf), _OWNER)
        assert stub_create["events"] == ["commit", "upload"]

    def test_snapshot_upload_failure_still_saves_and_is_reported(self, tf, monkeypatch, stub_create):
        """MinIO 失敗で保存を巻き戻さない（DB が正本）。事実は応答と監査に残す。"""
        events = stub_create["events"]
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(
            tf, "get_storage_client", lambda: _FakeStorage(fail_upload=True, events=events),
        )

        result = tf.create_course_teaching_figure(_COURSE_ID, self._body(tf), _OWNER)

        assert result["image_snapshot_failed"] is True
        assert stub_create["session"].committed == 1
        assert stub_create["session"].rolled_back == 0
        assert stub_create["audit_metadata"][0]["image_snapshot_failed"] is True

    def test_successful_save_reports_no_snapshot_failure(self, tf, monkeypatch, stub_create):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        result = tf.create_course_teaching_figure(_COURSE_ID, self._body(tf), _OWNER)
        assert result["image_snapshot_failed"] is False
        assert "image_snapshot_failed" not in stub_create["audit_metadata"][0]

    def test_registration_is_idempotent_for_same_figure(self, tf, monkeypatch, stub_create):
        """同じ figure_id を二重登録しない（冪等）。"""
        topic = _topic()
        tf._register_figure_in_topic(topic, _FIG_ID, "説明")
        tf._register_figure_in_topic(topic, _FIG_ID, "説明")
        assert topic["linked_figure_ids"] == [_FIG_ID]
        assert len(topic["evidence_links"]) == 1


# ---------------------------------------------------------------------------
# 権限（§8: コース所有者 / SYSTEM_ADMIN のみ）
# ---------------------------------------------------------------------------


class TestCourseOwnerGate:
    def test_non_owner_teacher_gets_403(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "_pg_session", lambda: _FakeSession(row=("owner-1",)))
        with pytest.raises(HTTPException) as exc:
            shared_mod.ensure_course_owner(_COURSE_ID, _OTHER)
        assert exc.value.status_code == 403

    def test_owner_passes(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "_pg_session", lambda: _FakeSession(row=("owner-1",)))
        shared_mod.ensure_course_owner(_COURSE_ID, _OWNER)

    def test_system_admin_passes(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "_pg_session", lambda: _FakeSession(row=("owner-1",)))
        shared_mod.ensure_course_owner(_COURSE_ID, _ADMIN)

    def test_missing_course_row_is_404(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "_pg_session", lambda: _FakeSession(row=None))
        with pytest.raises(HTTPException) as exc:
            shared_mod.ensure_course_owner(_COURSE_ID, _OWNER)
        assert exc.value.status_code == 404

    def test_editor_teacher_cannot_save_figure(self, tf, monkeypatch, stub_create):
        """editor 共有の教員は course_data を引けても図の保存は 403（権限の非対称）。"""
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(
            shared_mod, "get_editable_course_data", lambda uid, cid: _course_data([_topic()]),
        )
        monkeypatch.setattr(shared_mod, "_pg_session", lambda: _FakeSession(row=("owner-1",)))

        with pytest.raises(HTTPException) as exc:
            tf.create_course_teaching_figure(
                _COURSE_ID,
                tf.TeachingFigureCreateRequest(
                    svg_source=_VALID_SVG, topic_id=_TOPIC_ID, figure_kind="concept_map",
                ),
                _OTHER,
            )
        assert exc.value.status_code == 403
        assert stub_create["created"] == []

    def test_all_endpoints_go_through_a_course_gate(self, tf):
        """全ハンドラがコース権限ゲートを必ず通す（fail-closed）。

        書き込み系（turn / 保存 / PATCH / 提案 generate・確定）は owner ゲート、
        読み取り系（一覧・画像・提案一覧）は editor も通れる studio_editable ゲート
        （§7.3 — figures_index が画像 URL を指すため owner 限定だと editor の
        プレビューが 403 で壊れる）。どちらも通らないハンドラを作らせない。
        """
        import inspect

        write_handlers = [
            tf.figure_studio_turn,
            tf.create_course_teaching_figure,
            tf.update_course_teaching_figure,
            tf.generate_course_figure_suggestions,
            tf._decide_suggestion,
        ]
        read_handlers = [
            tf.list_course_teaching_figures,
            tf.get_course_teaching_figure_image,
            tf.get_course_figure_suggestions,
        ]
        for handler in write_handlers:
            src = inspect.getsource(handler)
            assert "course_data_for_owner(" in src, handler.__name__
        for handler in read_handlers:
            src = inspect.getsource(handler)
            assert "_course_data_for_studio_editable(" in src, handler.__name__
            assert "course_data_for_owner(" not in src, handler.__name__


# ---------------------------------------------------------------------------
# PATCH（status 遷移・§7.4）
# ---------------------------------------------------------------------------


class TestPatchTeachingFigure:
    @pytest.fixture
    def stub_patch(self, tf, monkeypatch):
        events: list[str] = []
        state: dict = {"session": _FakeSession(events=events), "storage": _FakeStorage(events=events),
                       "updates": [], "audits": [], "audit_metadata": [], "events": events,
                       "row": {
                           "id": _FIG_ID, "course_id": _COURSE_ID, "topic_id": _TOPIC_ID,
                           "status": "draft", "caption": "図の説明", "title": "図",
                           "figure_kind": "concept_map", "minio_key": f"teaching/{_COURSE_ID}/{_FIG_ID}.svg",
                           "content_type": "image/svg+xml", "created_at": "",
                       }}

        def _update(session, figure_id, **fields):
            state["updates"].append(fields)
            unknown = set(fields) - set(_UPDATE_KWARGS)
            assert not unknown, f"store が受け取らないキーワード: {sorted(unknown)}"
            # ``updated_by`` は**列ではない**（migration 063）ので行に載らない。
            # スタブが載せてしまうと、SET 句に混ぜる実装バグを隠す（実 DB は 42703）。
            applied = {k: v for k, v in fields.items() if k in _FIGURE_ROW_KEYS}
            return {**state["row"], **applied}

        def _record(*args, **kw):
            state["audits"].append(args)
            state["audit_metadata"].append(args[5] if len(args) > 5 else {})

        monkeypatch.setattr(tf, "_pg_session", lambda: state["session"])
        monkeypatch.setattr(tf, "get_storage_client", lambda: state["storage"])
        monkeypatch.setattr(tf, "get_teaching_figure", lambda session, fid: state["row"])
        monkeypatch.setattr(tf, "update_teaching_figure", _update)
        monkeypatch.setattr(tf, "record_review_event", _record)
        return state

    def test_draft_to_adopted_registers_topic(self, tf, monkeypatch, stub_patch):
        topic = _topic()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(status="adopted"), _OWNER,
        )
        assert result["topic_registered"] is True
        assert topic["linked_figure_ids"] == [_FIG_ID]
        assert any("UPDATE learning_courses" in s for s in stub_patch["session"].statements)

    def test_retire_keeps_topic_registration(self, tf, monkeypatch, stub_patch):
        """§7.4: retire でトピック側登録を消さない（生 UUID 露出を防ぐため）。"""
        topic = _topic(linked_figure_ids=[_FIG_ID], evidence_links=[
            {"kind": "figure", "target_id": _FIG_ID, "figure_id": _FIG_ID, "caption": "図の説明"},
        ])
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))
        stub_patch["row"]["status"] = "adopted"

        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(status="retired"), _OWNER,
        )
        assert result["topic_registered"] is False
        assert topic["linked_figure_ids"] == [_FIG_ID]
        assert len(topic["evidence_links"]) == 1
        assert not any("UPDATE learning_courses" in s for s in stub_patch["session"].statements)

    def test_other_course_figure_is_404(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        stub_patch["row"]["course_id"] = "another-course"
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(title="x"), _OWNER,
            )
        assert exc.value.status_code == 404

    def test_invalid_status_is_422(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(status="deleted"), _OWNER,
            )
        assert exc.value.status_code == 422

    def test_svg_replacement_is_sanitized_and_reuploaded(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(svg_source=_VALID_SVG), _OWNER,
        )
        assert stub_patch["storage"].uploads[0][1] == f"teaching/{_COURSE_ID}/{_FIG_ID}.svg"
        assert "svg_source" in stub_patch["updates"][0]

    def test_snapshot_upload_happens_after_the_db_commit(self, tf, monkeypatch, stub_patch):
        """DB（正本）を確定させてから配信スナップショットを差し替える。

        逆順（旧実装）だと UPDATE 失敗時に MinIO だけ新版になり、正本は旧版のまま残る。
        """
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(svg_source=_VALID_SVG), _OWNER,
        )
        assert stub_patch["events"] == ["commit", "upload"]

    def test_db_failure_leaves_the_snapshot_untouched(self, tf, monkeypatch, stub_patch):
        """UPDATE が落ちたら MinIO は触らない（配信物だけ新版にしない）。"""
        def _boom(session, figure_id, **fields):
            raise RuntimeError("undefined column")

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "update_teaching_figure", _boom)

        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(svg_source=_VALID_SVG), _OWNER,
            )
        assert exc.value.status_code == 500
        assert stub_patch["storage"].uploads == []
        assert "upload" not in stub_patch["events"]
        assert stub_patch["session"].rolled_back == 1

    def test_snapshot_upload_failure_keeps_the_update_and_is_reported(
        self, tf, monkeypatch, stub_patch,
    ):
        events = stub_patch["events"]
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(
            tf, "get_storage_client", lambda: _FakeStorage(fail_upload=True, events=events),
        )

        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(svg_source=_VALID_SVG), _OWNER,
        )

        assert result["image_snapshot_failed"] is True
        assert stub_patch["session"].committed == 1
        assert stub_patch["audit_metadata"][0]["image_snapshot_failed"] is True

    def test_metadata_only_patch_does_not_touch_minio(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(caption="別の説明"), _OWNER,
        )
        assert stub_patch["events"] == ["commit"]
        assert result["image_snapshot_failed"] is False

    def test_updated_by_is_passed_to_the_store_but_never_becomes_a_row_field(
        self, tf, monkeypatch, stub_patch,
    ):
        """route は ``updated_by`` を store へ渡す（revisions の帰属に使われる）。

        列ではないので行（＝応答）には現れない。スタブが行に混ぜないことで、
        SET 句へ戻す改変が実 DB でしか出ない 42703 に化けるのを防ぐ。
        """
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(title="改題"), _OWNER,
        )
        assert stub_patch["updates"][0]["updated_by"] == _OWNER["id"]
        assert "updated_by" not in result["figure"]

    def test_svg_rejection_is_422_without_upload(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(svg_source=_SCRIPT_SVG), _OWNER,
            )
        assert exc.value.status_code == 422
        assert stub_patch["storage"].uploads == []
        assert stub_patch["updates"] == []

    def test_missing_row_raises_404_from_store_error(self, tf, monkeypatch, stub_patch):
        def _boom(session, figure_id, **fields):
            raise tf.TeachingFigureNotFoundError("gone")

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "update_teaching_figure", _boom)
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(title="x"), _OWNER,
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# PATCH の register_topic_id（既存図タブからの挿入。§7.1b）
# ---------------------------------------------------------------------------


class TestPatchRegisterTopic:
    """既存の生成図を「このトピックにも登録する」経路。

    本文への ``![[figure:id]]`` 挿入だけでは ``linked_figure_ids`` /
    ``evidence_links`` が空のままで、①AI 書き換えで embed が消える ②未配信時に
    学習者へ生 UUID が露出する（レビュー M3）。挿入 UI は必ずこの API を通す。
    """

    @pytest.fixture
    def stub_patch(self, tf, monkeypatch):
        events: list[str] = []
        state: dict = {"session": _FakeSession(events=events), "storage": _FakeStorage(events=events),
                       "updates": [], "audits": [], "audit_metadata": [], "events": events,
                       "row": {
                           "id": _FIG_ID, "course_id": _COURSE_ID, "topic_id": _TOPIC_ID,
                           "status": "adopted", "caption": "図の説明", "title": "図",
                           "figure_kind": "concept_map", "minio_key": f"teaching/{_COURSE_ID}/{_FIG_ID}.svg",
                           "content_type": "image/svg+xml", "created_at": "",
                       }}

        def _update(session, figure_id, **fields):
            state["updates"].append(fields)
            unknown = set(fields) - set(_UPDATE_KWARGS)
            assert not unknown, f"store が受け取らないキーワード: {sorted(unknown)}"
            applied = {k: v for k, v in fields.items() if k in _FIGURE_ROW_KEYS}
            return {**state["row"], **applied}

        def _record(*args, **kw):
            state["audits"].append(args)
            state["audit_metadata"].append(args[5] if len(args) > 5 else {})

        monkeypatch.setattr(tf, "_pg_session", lambda: state["session"])
        monkeypatch.setattr(tf, "get_storage_client", lambda: state["storage"])
        monkeypatch.setattr(tf, "get_teaching_figure", lambda session, fid: state["row"])
        monkeypatch.setattr(tf, "update_teaching_figure", _update)
        monkeypatch.setattr(tf, "record_review_event", _record)
        return state

    def test_adopted_figure_can_be_registered_into_another_topic(self, tf, monkeypatch, stub_patch):
        """採用済みのまま（status 変更なし）でもトピック登録できる。"""
        other = _topic("topic-2")
        monkeypatch.setattr(
            tf, "course_data_for_owner", lambda cid, user: _course_data([_topic(), other]),
        )

        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id="topic-2"), _OWNER,
        )

        assert result["topic_registered"] is True
        assert other["linked_figure_ids"] == [_FIG_ID]
        assert other["evidence_links"][0]["figure_id"] == _FIG_ID
        assert result["evidence_link"]["caption"] == "図の説明"
        assert any("UPDATE learning_courses" in s for s in stub_patch["session"].statements)
        # 列の更新は無いので UPDATE course_teaching_figures は発行しない
        # （store の「更新フィールドなし」ValueError を踏まない）
        assert stub_patch["updates"] == []
        assert stub_patch["audit_metadata"][0]["registered_topic_id"] == "topic-2"

    def test_registration_is_idempotent(self, tf, monkeypatch, stub_patch):
        topic = _topic(
            linked_figure_ids=[_FIG_ID],
            evidence_links=[{"kind": "figure", "target_id": _FIG_ID, "figure_id": _FIG_ID}],
        )
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
        )
        tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
        )

        assert topic["linked_figure_ids"] == [_FIG_ID]
        assert len(topic["evidence_links"]) == 1

    def test_draft_can_adopt_and_register_in_one_call(self, tf, monkeypatch, stub_patch):
        stub_patch["row"]["status"] = "draft"
        topic = _topic("topic-2")
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID,
            tf.TeachingFigurePatchRequest(status="adopted", register_topic_id="topic-2"),
            _OWNER,
        )

        assert result["topic_registered"] is True
        assert stub_patch["updates"][0]["status"] == "adopted"
        assert topic["linked_figure_ids"] == [_FIG_ID]

    def test_draft_without_adoption_cannot_be_registered(self, tf, monkeypatch, stub_patch):
        """採用済みでない図の登録は 422（配信されない図の参照だけを残さない）。"""
        stub_patch["row"]["status"] = "draft"
        topic = _topic()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
            )

        assert exc.value.status_code == 422
        assert "採用済み" in exc.value.detail
        assert stub_patch["updates"] == []
        assert "linked_figure_ids" not in topic
        assert stub_patch["session"].committed == 0

    def test_retired_figure_cannot_be_registered(self, tf, monkeypatch, stub_patch):
        stub_patch["row"]["status"] = "retired"
        topic = _topic()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
            )
        assert exc.value.status_code == 422

    def test_unknown_topic_is_404(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id="nope"), _OWNER,
            )
        assert exc.value.status_code == 404
        assert stub_patch["session"].committed == 0

    def test_other_course_figure_cannot_be_registered(self, tf, monkeypatch, stub_patch):
        """コーススコープ外の図は 404（他コースの図を自コースへ登録できない）。"""
        stub_patch["row"]["course_id"] = "another-course"
        topic = _topic()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([topic]))

        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
            )
        assert exc.value.status_code == 404
        assert "linked_figure_ids" not in topic

    def test_non_owner_cannot_register(self, tf, monkeypatch, stub_patch):
        def _deny(course_id, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        monkeypatch.setattr(tf, "course_data_for_owner", _deny)
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
            )
        assert exc.value.status_code == 403

    def test_registration_only_patch_does_not_touch_minio(self, tf, monkeypatch, stub_patch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        result = tf.update_course_teaching_figure(
            _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(register_topic_id=_TOPIC_ID), _OWNER,
        )
        assert stub_patch["events"] == ["commit"]
        assert result["image_snapshot_failed"] is False

    def test_empty_patch_body_is_still_422(self, tf, monkeypatch, stub_patch):
        """登録指示も更新フィールドも無いボディは従来どおり store 側で 422。"""
        def _no_fields(session, figure_id, **fields):
            raise ValueError("no fields to update")

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "update_teaching_figure", _no_fields)
        with pytest.raises(HTTPException) as exc:
            tf.update_course_teaching_figure(
                _COURSE_ID, _FIG_ID, tf.TeachingFigurePatchRequest(), _OWNER,
            )
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# 教員向け画像配信（SVG ヘッダ・FG3 第3防御）
# ---------------------------------------------------------------------------


class TestAdminFigureImageDelivery:
    def test_svg_response_has_nosniff_and_csp_sandbox(self, tf, monkeypatch):
        storage = _FakeStorage(payload=b"<svg/>")
        monkeypatch.setattr(tf, "_course_data_for_studio_editable", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_storage_client", lambda: storage)
        monkeypatch.setattr(tf, "get_teaching_figure_for_delivery", lambda session, fid: {
            "id": _FIG_ID, "course_id": _COURSE_ID, "status": "adopted",
            "minio_key": "teaching/k.svg",
            "content_type": "image/svg+xml", "svg_source": _VALID_SVG,
        })

        resp = tf.get_course_teaching_figure_image(_COURSE_ID, _FIG_ID, _OWNER)

        assert resp.media_type == "image/svg+xml"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in resp.headers["content-security-policy"]
        assert "default-src 'none'" in resp.headers["content-security-policy"]

    def test_minio_failure_falls_back_to_db_svg_source(self, tf, monkeypatch):
        monkeypatch.setattr(tf, "_course_data_for_studio_editable", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_storage_client", lambda: _FakeStorage(fail_get=True))
        monkeypatch.setattr(tf, "get_teaching_figure_for_delivery", lambda session, fid: {
            "id": _FIG_ID, "course_id": _COURSE_ID, "status": "draft",
            "minio_key": "teaching/k.svg",
            "content_type": "image/svg+xml", "svg_source": _VALID_SVG,
        })

        resp = tf.get_course_teaching_figure_image(_COURSE_ID, _FIG_ID, _OWNER)
        assert b"<svg" in resp.body

    def test_other_course_figure_is_404(self, tf, monkeypatch):
        monkeypatch.setattr(tf, "_course_data_for_studio_editable", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_teaching_figure_for_delivery", lambda session, fid: {
            "id": _FIG_ID, "course_id": "another", "status": "adopted", "minio_key": "k",
            "content_type": "image/svg+xml",
        })
        with pytest.raises(HTTPException) as exc:
            tf.get_course_teaching_figure_image(_COURSE_ID, _FIG_ID, _OWNER)
        assert exc.value.status_code == 404

    def test_delivery_does_not_load_the_revision_history(self, tf, monkeypatch):
        """画像1枚のために旧版 SVG（``revisions``、数MB 級）を引かない。"""
        def _unexpected(session, fid):
            raise AssertionError("画像配信は full row（revisions 込み）を引いてはいけない")

        monkeypatch.setattr(tf, "_course_data_for_studio_editable", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_storage_client", lambda: _FakeStorage(b"<svg/>"))
        monkeypatch.setattr(tf, "get_teaching_figure", _unexpected)
        monkeypatch.setattr(tf, "get_teaching_figure_for_delivery", lambda session, fid: {
            "id": _FIG_ID, "course_id": _COURSE_ID, "status": "adopted",
            "minio_key": "teaching/k.svg", "content_type": "image/svg+xml", "svg_source": "",
        })

        resp = tf.get_course_teaching_figure_image(_COURSE_ID, _FIG_ID, _OWNER)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 提案（FG8: 数値を見せない）
# ---------------------------------------------------------------------------


class TestFigureSuggestionsResponse:
    def test_raw_confidence_never_leaks(self, tf):
        row = {
            "id": "sug-1", "anchor_excerpt": "熱平衡に達する", "gap_reason": "手順が長い",
            "figure_kind": "process_flow", "figure_brief": "3段階で描く",
            "confidence": 0.82, "confidence_label": "高", "status": "candidate",
            "signal_basis": "both",
        }
        out = tf._suggestion_response(row)
        assert "confidence" not in out
        assert out["confidence_label"] == "高"
        assert out["figure_kind_label"]  # 語彙 → 表示名は store / schema 由来

    def test_get_endpoint_strips_confidence(self, tf, monkeypatch):
        monkeypatch.setattr(tf, "_course_data_for_studio_editable", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "list_suggestions", lambda session, cid, tid, statuses=None: [
            {"id": "s1", "figure_kind": "concept_map", "confidence": 0.9, "status": "candidate"},
        ])
        resp = tf.get_course_figure_suggestions(_COURSE_ID, _TOPIC_ID, _OWNER)
        assert resp["suggestions"][0].keys() >= {"id", "confidence_label"}
        assert all("confidence" not in item for item in resp["suggestions"])

    def test_generate_adds_created_by_and_reports_dropped(self, tf, monkeypatch):
        from types import SimpleNamespace

        sessions = [_FakeSession(), _FakeSession(), _FakeSession()]
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: sessions.pop(0) if sessions else _FakeSession())
        monkeypatch.setattr(tf, "topic_gap_signals", lambda session, **kw: ["主張Aは誤り率:高（6-10人）"])
        monkeypatch.setattr(tf, "_primary_course_document_id", lambda data: _DOC_ID)
        captured: dict = {}

        def _generate(session, **kw):
            captured.update(kw)
            return SimpleNamespace(suggestions=[{"course_id": _COURSE_ID, "figure_kind": "comparison"}], dropped=2)

        monkeypatch.setattr(tf, "generate_suggestions", _generate)
        created_rows: list = []
        monkeypatch.setattr(tf, "create_suggestions", lambda session, rows: created_rows.extend(rows) or len(rows))
        monkeypatch.setattr(tf, "list_suggestions", lambda session, cid, tid, statuses=None: [])
        monkeypatch.setattr(tf, "record_review_event", lambda *a, **k: None)

        resp = tf.generate_course_figure_suggestions(_COURSE_ID, _TOPIC_ID, None, _OWNER)

        assert resp["dropped"] == 2
        assert resp["degraded"] is False
        assert "note" not in resp
        assert created_rows[0]["created_by"] == _OWNER["id"]
        # 学習者信号は事実文リストとしてのみ渡る（生値・逐語質問文は渡さない・FG8）
        assert captured["signal_lines"] == ["主張Aは誤り率:高（6-10人）"]

    def test_llm_failure_is_reported_as_degraded_not_as_zero_candidates(self, tf, monkeypatch):
        """core が ``degraded=True`` を返したら、それを応答・監査に通す。

        握り潰すと「この教材に図の提案は無い」と読めてしまう（quota は消費済み）。
        """
        from types import SimpleNamespace

        audits: list = []
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "topic_gap_signals", lambda session, **kw: [])
        monkeypatch.setattr(tf, "_primary_course_document_id", lambda data: _DOC_ID)
        monkeypatch.setattr(
            tf, "generate_suggestions",
            lambda session, **kw: SimpleNamespace(suggestions=[], dropped=0, degraded=True),
        )
        monkeypatch.setattr(tf, "create_suggestions", lambda session, rows: 0)
        monkeypatch.setattr(tf, "list_suggestions", lambda session, cid, tid, statuses=None: [])
        monkeypatch.setattr(tf, "record_review_event", lambda *a, **k: audits.append(a))

        resp = tf.generate_course_figure_suggestions(_COURSE_ID, _TOPIC_ID, None, _OWNER)

        assert resp["degraded"] is True
        assert resp["suggestions"] == []
        assert resp["note"] == tf.SUGGEST_DEGRADED_NOTE
        # 事実文に数値（回数・残数）は出さない（FG8）
        assert not any(ch.isdigit() for ch in resp["note"])
        assert audits[0][5]["degraded"] is True

    def test_result_without_a_degraded_attribute_defaults_to_false(self, tf, monkeypatch):
        """生成側の結果に属性が欠けても 500 にしない（getattr 既定）。"""
        from types import SimpleNamespace

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "topic_gap_signals", lambda session, **kw: [])
        monkeypatch.setattr(tf, "_primary_course_document_id", lambda data: _DOC_ID)
        monkeypatch.setattr(
            tf, "generate_suggestions", lambda session, **kw: SimpleNamespace(suggestions=[]),
        )
        monkeypatch.setattr(tf, "create_suggestions", lambda session, rows: 0)
        monkeypatch.setattr(tf, "list_suggestions", lambda session, cid, tid, statuses=None: [])
        monkeypatch.setattr(tf, "record_review_event", lambda *a, **k: None)

        resp = tf.generate_course_figure_suggestions(_COURSE_ID, _TOPIC_ID, None, _OWNER)
        assert resp["degraded"] is False
        assert resp["dropped"] == 0

    def test_decide_rejects_other_course_suggestion_without_writing(self, tf, monkeypatch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data())
        session = _FakeSession()
        monkeypatch.setattr(tf, "_pg_session", lambda: session)
        monkeypatch.setattr(tf, "get_suggestion", lambda s, sid: {
            "id": sid, "course_id": "another-course", "status": "candidate",
        })

        def _unexpected(*a, **k):
            raise AssertionError("他コースの提案には書き込んではいけない")

        monkeypatch.setattr(tf, "set_suggestion_status", _unexpected)
        with pytest.raises(HTTPException) as exc:
            tf.dismiss_course_figure_suggestion(_COURSE_ID, "sug-1", _OWNER)
        assert exc.value.status_code == 404
        assert session.committed == 0

    def test_dismiss_records_actual_previous_status(self, tf, monkeypatch):
        """監査には（ハードコードでなく）読み取った実際の old_status を残す（P5）。"""
        audits: list = []
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_suggestion", lambda s, sid: {
            "id": sid, "course_id": _COURSE_ID, "status": "candidate", "figure_kind": "timeline",
        })
        monkeypatch.setattr(tf, "set_suggestion_status", lambda s, sid, status, *, course_id: {
            "id": sid, "course_id": course_id, "status": status, "figure_kind": "timeline",
        })
        monkeypatch.setattr(tf, "record_review_event", lambda *a, **k: audits.append(a))

        resp = tf.dismiss_course_figure_suggestion(_COURSE_ID, "sug-1", _OWNER)

        assert resp["suggestion"]["status"] == "dismissed"
        entity_type, _entity_id, old_status, new_status, _user, _meta = audits[0]
        assert (entity_type, old_status, new_status) == ("teaching_figure", "candidate", "dismissed")

    @pytest.mark.parametrize("decided", ["accepted", "dismissed", "superseded"])
    def test_already_decided_suggestion_is_409_not_404(self, tf, monkeypatch, decided):
        """判断済みの提案は書き換えない（``store.set_suggestion_status`` の遷移元は
        candidate のみ）。存在は確認できているので 404 ではなく 409 + 事実文を返す。
        """
        session = _FakeSession()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: session)
        monkeypatch.setattr(tf, "get_suggestion", lambda s, sid: {
            "id": sid, "course_id": _COURSE_ID, "status": decided,
        })

        def _unexpected(*a, **k):
            raise AssertionError("判断済みの提案には書き込んではいけない")

        monkeypatch.setattr(tf, "set_suggestion_status", _unexpected)

        with pytest.raises(HTTPException) as exc:
            tf.accept_course_figure_suggestion(_COURSE_ID, "sug-1", _OWNER)
        assert exc.value.status_code == 409
        assert "判断済み" in exc.value.detail
        assert session.committed == 0

    def test_concurrent_decision_race_is_409(self, tf, monkeypatch):
        """読み取り後に他の操作が確定させた場合（UPDATE が0行）も 409。"""
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_suggestion", lambda s, sid: {
            "id": sid, "course_id": _COURSE_ID, "status": "candidate",
        })
        monkeypatch.setattr(tf, "set_suggestion_status", lambda s, sid, status, *, course_id: None)

        with pytest.raises(HTTPException) as exc:
            tf.dismiss_course_figure_suggestion(_COURSE_ID, "sug-1", _OWNER)
        assert exc.value.status_code == 409

    def test_decide_passes_the_course_scope_to_the_store(self, tf, monkeypatch):
        """遷移 SQL をコースにスコープする（id だけの一致で他コースを書き換えない）。"""
        captured: dict = {}
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data())
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "get_suggestion", lambda s, sid: {
            "id": sid, "course_id": _COURSE_ID, "status": "candidate",
        })

        def _set(session, sid, status, *, course_id):
            captured.update({"id": sid, "status": status, "course_id": course_id})
            return {"id": sid, "course_id": course_id, "status": status}

        monkeypatch.setattr(tf, "set_suggestion_status", _set)
        monkeypatch.setattr(tf, "record_review_event", lambda *a, **k: None)

        tf.accept_course_figure_suggestion(_COURSE_ID, "sug-1", _OWNER)
        assert captured == {"id": "sug-1", "status": "accepted", "course_id": _COURSE_ID}


# ---------------------------------------------------------------------------
# 対話（turn: DB 非変更・grounding のテキスト化）
# ---------------------------------------------------------------------------


class TestFigureStudioTurn:
    def test_turn_returns_generator_result_and_does_not_write_db(self, tf, monkeypatch):
        from types import SimpleNamespace

        session = _FakeSession()
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: session)
        captured: dict = {}

        def _turn(**kw):
            captured.update(kw)
            return SimpleNamespace(
                reply="3段階のプロセス図にしました", svg_source=_VALID_SVG,
                title="熱平衡", caption="模式図", figure_kind="process_flow", degraded=False,
            )

        monkeypatch.setattr(tf, "run_figure_turn", _turn)

        resp = tf.figure_studio_turn(
            _COURSE_ID,
            tf.FigureStudioTurnRequest(
                instruction="3段階のプロセス図で", topic_id=_TOPIC_ID,
                history=[{"role": "user", "content": "前の指示"}, {"role": "bogus", "content": "x"}],
            ),
            _OWNER,
        )

        assert resp["svg_source"] == _VALID_SVG
        assert resp["degraded"] is False
        # grounding は文字列（prompt の [参考資料] セクションに入る）
        assert isinstance(captured["grounding"], str)
        assert "トピック" in captured["grounding"]
        # 履歴は正規化される（role が user/assistant 以外は落ちる）
        assert captured["history"] == [{"role": "user", "content": "前の指示"}]
        # turn は DB を変更しない
        assert session.committed == 0

    def test_llm_failure_is_a_200_degraded_response_not_500(self, tf, monkeypatch):
        """LLM 失敗は 500 にせず ``degraded=true`` + 事実文で返す（チャット型の共通規約）。

        生成側（``core.teaching_figures.generator``）が ``degraded=True`` /
        ``svg_source=""`` を返す契約なので、route はそれを素通しする（例外にしない）。
        """
        from types import SimpleNamespace

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "run_figure_turn", lambda **kw: SimpleNamespace(
            reply="いま図を生成できませんでした。少し待って再度お試しください。",
            svg_source="", title="", caption="", figure_kind="", degraded=True,
        ))

        resp = tf.figure_studio_turn(
            _COURSE_ID, tf.FigureStudioTurnRequest(instruction="描いて", topic_id=_TOPIC_ID), _OWNER,
        )

        assert resp["degraded"] is True
        assert resp["reply"]  # 事実文が入る
        assert resp["svg_source"] == ""  # フロントは前回のプレビューを保持する

    def test_sanitize_failure_turn_returns_reply_without_svg(self, tf, monkeypatch):
        """サニタイズ2回失敗のターンは ``svg_source`` 空 + ``degraded=False``。

        LLM は動いており図だけが採用されないケース（生成側の契約）。route は
        欠けたフィールドを埋めず、空文字のまま返す（前回版プレビュー維持の合図）。
        """
        from types import SimpleNamespace

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "run_figure_turn", lambda **kw: SimpleNamespace(
            reply="生成した図が安全性チェックを通らなかったため、図は更新していません。",
            svg_source="", title="", caption="", figure_kind="", degraded=False,
        ))

        resp = tf.figure_studio_turn(
            _COURSE_ID, tf.FigureStudioTurnRequest(instruction="描いて", topic_id=_TOPIC_ID), _OWNER,
        )

        assert resp["svg_source"] == ""
        assert resp["degraded"] is False
        assert resp["reply"]

    def test_missing_result_fields_do_not_crash_the_route(self, tf, monkeypatch):
        """生成側の結果に属性が欠けても 500 にしない（getattr 既定で空に落とす）。"""
        from types import SimpleNamespace

        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(tf, "run_figure_turn", lambda **kw: SimpleNamespace(reply="ok"))

        resp = tf.figure_studio_turn(
            _COURSE_ID, tf.FigureStudioTurnRequest(instruction="描いて", topic_id=_TOPIC_ID), _OWNER,
        )
        assert resp == {
            "reply": "ok", "svg_source": "", "title": "", "caption": "",
            "figure_kind": "", "degraded": False,
        }

    def test_empty_instruction_is_422(self, tf, monkeypatch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        with pytest.raises(HTTPException) as exc:
            tf.figure_studio_turn(_COURSE_ID, tf.FigureStudioTurnRequest(instruction="  "), _OWNER)
        assert exc.value.status_code == 422

    def test_quota_is_consumed_only_after_gates_pass(self, tf, monkeypatch):
        """権限ゲート・入力検証で弾かれたリクエストは quota を消費しない。"""
        consumed: list = []
        monkeypatch.setattr(tf, "consume_figure_studio_quota", lambda uid: consumed.append(uid))
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))

        with pytest.raises(HTTPException):
            tf.figure_studio_turn(_COURSE_ID, tf.FigureStudioTurnRequest(instruction=""), _OWNER)
        assert consumed == []

    def test_daily_cap_returns_429_without_numbers(self, tf, monkeypatch):
        monkeypatch.setattr(tf, "course_data_for_owner", lambda cid, user: _course_data([_topic()]))
        monkeypatch.setattr(tf, "_max_svg_bytes", lambda: 200_000)

        class _Settings:
            figure_studio_max_calls_per_day = 0

        monkeypatch.setattr(tf, "get_settings", lambda: _Settings())

        def _unexpected(**kw):
            raise AssertionError("上限超過時に LLM を呼んではいけない")

        monkeypatch.setattr(tf, "run_figure_turn", _unexpected)
        with pytest.raises(HTTPException) as exc:
            tf.figure_studio_turn(
                _COURSE_ID, tf.FigureStudioTurnRequest(instruction="描いて"), _OWNER,
            )
        assert exc.value.status_code == 429
        assert not any(ch.isdigit() for ch in exc.value.detail)

    def test_grounding_includes_equations_and_claims(self, tf):
        topic = _topic(evidence_links=[
            {"kind": "equation", "target_id": "eq_1", "label": "式(1)", "latex": "E=mc^2"},
            {"kind": "claim", "target_id": "claim_1", "summary": "エネルギーは質量に比例する"},
        ])
        grounding = tf._build_grounding(_course_data([topic]), topic)
        text = tf._grounding_to_text(grounding)
        assert "E=mc^2" in text
        assert "エネルギーは質量に比例する" in text


# ---------------------------------------------------------------------------
# 学習者向け配信（4条件 fail-closed・FG4）
# ---------------------------------------------------------------------------


class TestLearnerTeachingFigureDelivery:
    def _patch_common(self, monkeypatch, learning_mod, course_data, teaching_row, storage=None):
        monkeypatch.setattr(learning_mod, "get_accessible_course_data", lambda uid, cid: course_data)
        monkeypatch.setattr(learning_mod, "_load_figure_row_by_id", lambda fid: None)
        monkeypatch.setattr(learning_mod, "_load_teaching_figure_row", lambda cid, fid: teaching_row)
        monkeypatch.setattr(learning_mod, "get_storage_client", lambda: storage or _FakeStorage(b"<svg/>"))

    def _row(self, **kw):
        row = {
            "id": _FIG_ID, "course_id": _COURSE_ID, "status": "adopted",
            "minio_key": f"teaching/{_COURSE_ID}/{_FIG_ID}.svg", "content_type": "image/svg+xml",
        }
        row.update(kw)
        return row

    def _referencing_course(self):
        return {"topics": [_topic(student_material={"source_text": f"本文 ![[figure:{_FIG_ID}]]"})]}

    def test_all_four_conditions_satisfied_returns_svg_with_headers(self, monkeypatch):
        import routes.learning as learning_mod

        storage = _FakeStorage(b"<svg>ok</svg>")
        self._patch_common(monkeypatch, learning_mod, self._referencing_course(), self._row(), storage)

        resp = learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})

        assert resp.body == b"<svg>ok</svg>"
        assert resp.media_type == "image/svg+xml"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in resp.headers["content-security-policy"]
        assert storage.get_object  # MinIO 経由で配信

    def test_draft_status_is_404(self, monkeypatch):
        import routes.learning as learning_mod

        self._patch_common(
            monkeypatch, learning_mod, self._referencing_course(), self._row(status="draft"),
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert exc.value.status_code == 404

    def test_retired_status_is_404(self, monkeypatch):
        import routes.learning as learning_mod

        self._patch_common(
            monkeypatch, learning_mod, self._referencing_course(), self._row(status="retired"),
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert exc.value.status_code == 404

    def test_other_course_figure_is_404(self, monkeypatch):
        import routes.learning as learning_mod

        self._patch_common(
            monkeypatch, learning_mod, self._referencing_course(), self._row(course_id="another"),
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert exc.value.status_code == 404

    def test_not_referenced_by_course_content_is_404(self, monkeypatch):
        import routes.learning as learning_mod

        self._patch_common(
            monkeypatch, learning_mod, {"topics": [_topic()]}, self._row(),
        )
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert exc.value.status_code == 404

    def test_inaccessible_course_is_404(self, monkeypatch):
        import routes.learning as learning_mod

        monkeypatch.setattr(learning_mod, "get_accessible_course_data", lambda uid, cid: None)
        with pytest.raises(HTTPException) as exc:
            learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert exc.value.status_code == 404

    def test_reference_via_linked_figure_ids_is_enough(self, monkeypatch):
        import routes.learning as learning_mod

        course = {"topics": [_topic(linked_figure_ids=[_FIG_ID])]}
        self._patch_common(monkeypatch, learning_mod, course, self._row())
        resp = learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert resp.status_code == 200

    def test_reference_inside_nested_chapter_topic_is_found(self, monkeypatch):
        """既存バグ修正: ``_course_references_figure`` は章ネスト形も走査する。"""
        import routes.learning as learning_mod

        course = {"chapters": [{"topics": [_topic(linked_figure_ids=[_FIG_ID])]}]}
        self._patch_common(monkeypatch, learning_mod, course, self._row())
        resp = learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert resp.status_code == 200

    def test_course_references_figure_walks_nested_topics(self):
        import routes.learning as learning_mod

        course = {"chapters": [{"topics": [
            {"id": "t1", "student_material": {"source_text": f"![[figure:{_FIG_ID}]]"}},
        ]}]}
        assert learning_mod._course_references_figure(course, _FIG_ID) is True

    def test_extracted_figure_path_still_returns_png(self, monkeypatch):
        """抽出図経路は従来どおり PNG（SVG ヘッダは付かない）。"""
        import routes.learning as learning_mod

        monkeypatch.setattr(
            learning_mod, "get_accessible_course_data", lambda uid, cid: self._referencing_course(),
        )
        monkeypatch.setattr(learning_mod, "_load_figure_row_by_id", lambda fid: {
            "id": _FIG_ID, "document_id": _DOC_ID, "minio_key": "figures/a.png",
        })
        monkeypatch.setattr(learning_mod, "_course_document_ids", lambda data: [_DOC_ID])
        monkeypatch.setattr(learning_mod, "get_storage_client", lambda: _FakeStorage(b"PNG"))

        resp = learning_mod.get_course_figure_image(_COURSE_ID, _FIG_ID, {"id": "student-1"})
        assert resp.media_type == "image/png"
        assert "content-security-policy" not in resp.headers


# ---------------------------------------------------------------------------
# figures_index / 図マップの合流（§7.1・§7.3）
# ---------------------------------------------------------------------------


class TestCourseFiguresIndex:
    def test_teaching_figures_survive_when_course_has_no_source_documents(self, monkeypatch):
        """§7.1: 教材図のマージは document 走査の早期 return より前に行う。"""
        import routes.lecture as lecture_mod

        monkeypatch.setattr(lecture_mod, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(
            lecture_mod, "adopted_figures_map",
            lambda session, cid: {_FIG_ID: {"caption": "図の説明", "content_type": "image/svg+xml"}},
        )
        monkeypatch.setattr(lecture_mod, "_course_document_ids", lambda data: [])

        result = lecture_mod._load_course_figures_by_id(_COURSE_ID, {})

        assert _FIG_ID in result
        assert result[_FIG_ID]["teaching"] is True
        assert result[_FIG_ID]["document_id"] is None
        assert result[_FIG_ID]["image_url"] == (
            f"/api/learning/courses/{_COURSE_ID}/figures/{_FIG_ID}/image"
        )

    def test_db_failure_keeps_teaching_figures(self, monkeypatch):
        import routes.lecture as lecture_mod

        class _BoomSession(_FakeSession):
            def execute(self, stmt, params=None):
                raise RuntimeError("db down")

        monkeypatch.setattr(lecture_mod, "_pg_session", lambda: _BoomSession())
        monkeypatch.setattr(
            lecture_mod, "adopted_figures_map",
            lambda session, cid: {_FIG_ID: {"caption": "図の説明"}},
        )
        monkeypatch.setattr(lecture_mod, "_course_document_ids", lambda data: [_DOC_ID])

        result = lecture_mod._load_course_figures_by_id(_COURSE_ID, {})
        assert _FIG_ID in result

    def test_teaching_figure_load_failure_is_fail_soft(self, monkeypatch):
        import routes.lecture as lecture_mod

        def _boom(session, cid):
            raise RuntimeError("no table")

        monkeypatch.setattr(lecture_mod, "_pg_session", lambda: _FakeSession())
        monkeypatch.setattr(lecture_mod, "adopted_figures_map", _boom)
        monkeypatch.setattr(lecture_mod, "_course_document_ids", lambda data: [])

        assert lecture_mod._load_course_figures_by_id(_COURSE_ID, {}) == {}

    def test_admin_index_uses_admin_image_urls(self, monkeypatch):
        import routes.lecture as lecture_mod

        extracted_id = "550e8400-e29b-41d4-a716-446655440303"

        class _RowsSession(_FakeSession):
            def execute(self, stmt, params=None):
                self.statements.append(str(stmt))
                return _FakeResult(rows=[(extracted_id, "抽出図の caption", _DOC_ID)])

        monkeypatch.setattr(lecture_mod, "_pg_session", lambda: _RowsSession())
        monkeypatch.setattr(
            lecture_mod, "adopted_figures_map",
            lambda session, cid: {_FIG_ID: {"caption": "教材図"}},
        )
        monkeypatch.setattr(lecture_mod, "_course_document_ids", lambda data: [_DOC_ID])

        index = lecture_mod.load_course_figures_index_for_admin(_COURSE_ID, {})

        assert index[_FIG_ID]["image_url"] == (
            f"/api/admin/courses/{_COURSE_ID}/teaching-figures/{_FIG_ID}/image"
        )
        assert index[extracted_id]["image_url"] == (
            f"/api/admin/documents/{_DOC_ID}/figures/{extracted_id}/image"
        )
        assert index[extracted_id]["teaching"] is False


# ---------------------------------------------------------------------------
# AI 書き換え耐性（§7.1b-3）
# ---------------------------------------------------------------------------


class TestRewriteFigureRestoration:
    def test_required_items_include_linked_figure_ids(self):
        from core.course_content_builder import _required_figure_items

        topic = {
            "evidence_links": [{"kind": "figure", "target_id": "fig-a", "caption": "図A"}],
            "linked_figure_ids": ["fig-a", "fig-b"],
        }
        items = _required_figure_items(topic)
        ids = [i["figure_id"] for i in items]
        assert ids == ["fig-a", "fig-b"]
        assert items[0]["caption"] == "図A"
        # linked_figure_ids 単独の図は caption を捏造しない
        assert items[1]["caption"] == ""

    def test_missing_embed_is_restored_and_flagged(self):
        import routes.lecture_studio.topics as topics_mod

        topic = {"linked_figure_ids": [_FIG_ID], "evidence_links": [
            {"kind": "figure", "target_id": _FIG_ID, "caption": "熱平衡の模式図"},
        ]}
        result = {"student_material": {"source_format": "eg-markdown-v1", "source_text": "図が消えた本文"}}

        restored = topics_mod._restore_missing_figure_embeds(result, topic)

        assert restored is True
        assert f"![[figure:{_FIG_ID}]]" in result["student_material"]["source_text"]
        assert "図が消えた本文" in result["student_material"]["source_text"]

    def test_present_embed_is_left_alone(self):
        import routes.lecture_studio.topics as topics_mod

        topic = {"linked_figure_ids": [_FIG_ID]}
        text = f"本文 ![[figure:{_FIG_ID}]] 続き"
        result = {"student_material": {"source_format": "eg-markdown-v1", "source_text": text}}

        assert topics_mod._restore_missing_figure_embeds(result, topic) is False
        assert result["student_material"]["source_text"] == text

    def test_topic_without_figures_is_not_flagged(self):
        import routes.lecture_studio.topics as topics_mod

        result = {"student_material": {"source_format": "eg-markdown-v1", "source_text": "本文"}}
        assert topics_mod._restore_missing_figure_embeds(result, {}) is False

    def test_rewrite_response_reports_figures_restored(self, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        topic = _topic(linked_figure_ids=[_FIG_ID])
        monkeypatch.setattr(
            shared_mod, "get_editable_course_data", lambda uid, cid: {"topics": [topic]},
        )
        monkeypatch.setattr(topics_mod, "consume_lecture_rewrite_quota", lambda *a, **k: None)
        monkeypatch.setattr(
            topics_mod, "generate_text_with_structured_output",
            lambda **kw: kw["response_format"](
                student_material={"source_format": "eg-markdown-v1", "source_text": "図を落とした本文"},
                spoken_script="読み上げ",
            ),
        )

        result = topics_mod.rewrite_lecture_studio_course_topic(
            _COURSE_ID, _TOPIC_ID, {"prompt": "書き換えて"}, _OWNER,
        )
        assert result["figures_restored"] is True
        assert f"![[figure:{_FIG_ID}]]" in result["student_material"]["source_text"]


# ---------------------------------------------------------------------------
# 孤児掃除（§3.1: コース物理削除の全経路）
# ---------------------------------------------------------------------------


class TestOrphanCleanupPaths:
    """コースを物理削除する全経路に教材図の削除が同乗していること（構造検査）。

    設計書 §3.1 は3経路（services.delete_course_data / _purge_course /
    _purge_document のコース削除ループ）を挙げるが、実装には admin.py の
    ``delete_material``（巻き添え削除）と ``delete_course`` もあるため、
    合計5経路すべてを検査する。
    """

    def _source(self, relative: str) -> str:
        return (BACKEND / relative).read_text(encoding="utf-8")

    def _function_source(self, src: str, name: str) -> str:
        """``def name(`` から次のトップレベル定義までを切り出す。

        ``guardrail_helpers.extract_function_source`` は名前が前方一致する別関数
        （``delete_course`` → ``delete_course_group_permission``）を拾うため、
        開き括弧まで含めた厳密一致で切り出す。
        """
        import re

        m = re.search(rf"^def {re.escape(name)}\(.*?(?=^(?:def |class |@router)|\Z)",
                      src, re.DOTALL | re.MULTILINE)
        assert m, f"{name} の関数本体が抽出できない"
        return m.group(0)

    def test_services_delete_course_data(self):
        body = self._function_source(self._source("api/services.py"), "delete_course_data")
        assert "delete_figures_for_course" in body

    def test_versioning_purge_course(self):
        body = self._function_source(self._source("core/versioning/deletion.py"), "_purge_course")
        assert "_purge_teaching_figures" in body

    def test_versioning_purge_document_course_loop(self):
        body = self._function_source(self._source("core/versioning/deletion.py"), "_purge_document")
        assert "_purge_teaching_figures" in body

    def test_admin_delete_material(self):
        body = self._function_source(self._source("api/routes/admin.py"), "delete_material")
        assert "delete_figures_for_course" in body

    def test_admin_delete_course(self):
        body = self._function_source(self._source("api/routes/admin.py"), "delete_course")
        assert "delete_figures_for_course" in body

    def test_minio_objects_are_removed_best_effort(self, monkeypatch):
        import api.services as services_mod

        storage = _FakeStorage()
        monkeypatch.setattr(services_mod, "_get_storage", lambda: storage)
        services_mod.remove_teaching_figure_objects(["teaching/c/a.svg", "", "teaching/c/b.svg"])
        assert storage.removed == [
            ("figure-images", "teaching/c/a.svg"),
            ("figure-images", "teaching/c/b.svg"),
        ]

    def test_minio_failure_does_not_raise(self, monkeypatch):
        import api.services as services_mod

        class _BoomStorage(_FakeStorage):
            def remove_object(self, bucket, key):
                raise RuntimeError("minio down")

        monkeypatch.setattr(services_mod, "_get_storage", lambda: _BoomStorage())
        services_mod.remove_teaching_figure_objects(["teaching/c/a.svg"])


# ---------------------------------------------------------------------------
# 行削除 API が存在しない（FG7）
# ---------------------------------------------------------------------------


class TestNoDeleteEndpoints:
    def test_router_has_no_delete_routes(self, tf):
        for route in tf.router.routes:
            assert "DELETE" not in route.methods, route.path
