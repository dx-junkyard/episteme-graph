"""誤解メモの候補化 + 本人の3択 + 5件上限撤廃（是正 F5 / 六つのレンズ 提案3）。

設計出所: docs/architecture/six_lenses_2026-09-10/01_learner.md 提案3
（誤解メモを「AI の候補」に格下げし、本人が異議・撤回できるようにする）と
docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 #2。

対象:
  - ``backend/core/personal_graph/graph_data.py``（JSONB 正本アクセサ・状態語彙）
  - ``backend/api/services.py::detect_and_record_misconception`` /
    ``review_personal_misconception`` / ``get_personal_layer`` / ``calculate_progress``
  - ``backend/api/routes/learning.py::review_misconception_route``
  - ``frontend/public/js/app.js``（候補 / 確定 / 却下の描画と3択）

受け入れ条件（ガードレール）:
  1. 検出は必ず ``status='candidate'`` で保存される（AI は候補まで・原則1）
  2. per-topic 5件の切り詰めコードが存在しない（原則3 情報を落とさない）
  3. ``dismissed`` は行を消さず状態遷移で保持する（P4）
  4. ``status`` の無い旧形式は読み時に candidate として扱う（旧行を確定扱いにしない）
  5. review API は語彙外を 422、対象なし / コース不可視を 404
  6. UI は候補に断定語彙（「誤っていた理解 → 正しい理解」等）を使わない・件数を出さない
  7. UI アンカー3点セット（KNOWN 登録・UI_ANCHORS 値・マニュアル節 + CSS）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services  # noqa: E402
import routes.learning as learning_mod  # noqa: E402
from core import candidate_flow  # noqa: E402
from core.check_review import SELF_CHECK_VALUES  # noqa: E402
from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS  # noqa: E402
from core.personal_graph import graph_data as g  # noqa: E402
from core.schema import AUDIT_ENTITY_MISCONCEPTION, AUDIT_ENTITY_TYPES  # noqa: E402

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"
GRAPH_DATA_PY = BACKEND / "core" / "personal_graph" / "graph_data.py"
SERVICES_PY = BACKEND / "api" / "services.py"
LEARNING_PY = BACKEND / "api" / "routes" / "learning.py"

ANCHOR_ID = "rightpanel.misconception-review"
MANUAL_REF = "student/02-student.md#misconception-review"

CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}
COURSE_ID = "course-1"
TOPIC_ID = "topic-1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _legacy_entry(label: str = "9/9 の訂正", correct: str = "正しい説明") -> dict:
    """旧形式のエントリ（id / status / source を持たない3キーだけの行）。"""
    return {"label": label, "wrong": "これは A だと思います", "correct": correct}


# ---------------------------------------------------------------------------
# 1. core アクセサ（core/personal_graph/graph_data.py）
# ---------------------------------------------------------------------------


class TestVocabulary:
    """状態語彙は candidate_flow の宣言として持つ（新しいワークフローを書かない）。"""

    def test_vocabulary_is_a_candidate_flow_declaration(self):
        assert isinstance(g.MISCONCEPTION_VOCAB, candidate_flow.CandidateVocabulary)
        assert g.MISCONCEPTION_VOCAB.candidate == "candidate"
        assert g.MISCONCEPTION_VOCAB.accepted == "confirmed"
        assert g.MISCONCEPTION_VOCAB.dismissed == "dismissed"
        assert g.MISCONCEPTION_VOCAB.superseded == "superseded"

    def test_statuses_expose_all_four(self):
        assert set(g.MISCONCEPTION_STATUSES) == {
            "candidate", "confirmed", "dismissed", "superseded",
        }

    def test_decision_vocabulary_matches_r_layer_self_check(self):
        """3択の語彙は R層の自己確認と共有する（同じ形の判断に別の語彙を作らない）。"""
        assert set(services.MISCONCEPTION_DECISIONS) == set(SELF_CHECK_VALUES)

    def test_audit_entity_type_is_registered_in_catalog(self):
        assert AUDIT_ENTITY_MISCONCEPTION in AUDIT_ENTITY_TYPES
        assert len(AUDIT_ENTITY_TYPES) == len(set(AUDIT_ENTITY_TYPES))


class TestCandidateOnly:
    """AI が書ける状態は candidate だけ（原則1）。"""

    def test_new_entry_is_always_candidate(self):
        entry = g.new_misconception_entry(label="L", wrong="w", correct="c")
        assert entry["status"] == "candidate"
        assert entry["source"] == g.MISCONCEPTION_SOURCE_AI
        assert entry["id"]
        assert entry["detected_at"]

    def test_legacy_entry_without_status_reads_as_candidate(self):
        data = g.parse_personal_graph(
            {"misconceptions_by_topic": {TOPIC_ID: [_legacy_entry()]}}
        )
        entries = g.misconceptions_for_topic(data, TOPIC_ID)
        assert [e["status"] for e in entries] == ["candidate"]

    def test_unknown_status_falls_back_to_candidate(self):
        """語彙外の status を「本人が確定した」側に読み替えない（fail-closed）。"""
        raw = dict(_legacy_entry(), status="teacher_approved")
        assert g.normalize_misconception(raw)["status"] == "candidate"

    def test_message_id_is_carried(self):
        entry = g.new_misconception_entry(label="L", wrong="w", message_id="msg-1")
        assert entry["message_id"] == "msg-1"


class TestMissingCorrection:
    """訂正文が取れなかった行を「あなたは間違っていた」として残さない。"""

    def test_legacy_placeholder_becomes_none(self):
        raw = _legacy_entry(correct=g.LEGACY_EMPTY_CORRECTION)
        assert g.normalize_misconception(raw)["correct"] is None

    def test_empty_correction_is_none(self):
        entry = g.new_misconception_entry(label="L", wrong="w", correct="")
        assert entry["correct"] is None
        assert entry["status"] == "candidate"  # それでも候補としては保持する

    def test_placeholder_string_is_not_written_by_detection(self):
        """検出関数が中身のない既定文を代入しない（撤去の経緯は docstring に残す）。"""
        src = extract_function_source(_read(SERVICES_PY), "detect_and_record_misconception")
        assert f'correct = "{g.LEGACY_EMPTY_CORRECTION}"' not in src
        assert "if not correct:" not in src


class TestNoFiveItemLimit:
    """per-topic 5件の上限を撤廃した（原則3 情報を落とさない）。"""

    def test_append_keeps_every_entry(self):
        data = g.parse_personal_graph({})
        for i in range(7):
            data = g.append_misconception(
                data, TOPIC_ID, g.new_misconception_entry(label=f"L{i}", wrong=f"w{i}")
            )
        assert len(data.misconceptions_by_topic[TOPIC_ID]) == 7

    def test_append_prepends_newest_first(self):
        data = g.parse_personal_graph({})
        data = g.append_misconception(data, TOPIC_ID, g.new_misconception_entry(label="old", wrong="w"))
        data = g.append_misconception(data, TOPIC_ID, g.new_misconception_entry(label="new", wrong="w"))
        labels = [e["label"] for e in g.misconceptions_for_topic(data, TOPIC_ID)]
        assert labels == ["new", "old"]

    def test_truncation_code_is_absent(self):
        """切り詰めの痕跡（上限定数・スライス）がアクセサに残っていない。"""
        src = _read(GRAPH_DATA_PY)
        assert_source_forbids(
            src,
            ["_MISCONCEPTION_LIMIT_PER_TOPIC", "[:5]", "[:_MISCONCEPTION"],
            context="graph_data.py",
        )


class TestStatusTransitions:
    """却下は行を消さず状態遷移で保持する（P4）。"""

    def _data_with_candidate(self):
        entry = g.new_misconception_entry(label="L", wrong="w", correct="c")
        data = g.append_misconception(g.parse_personal_graph({}), TOPIC_ID, entry)
        return data, entry["id"]

    def test_confirm_returns_old_status_and_keeps_row(self):
        data, entry_id = self._data_with_candidate()
        updated, old = g.set_misconception_status(
            data, TOPIC_ID, entry_id, "confirmed", decision="agreed"
        )
        assert old == "candidate"
        rows = g.misconceptions_for_topic(updated, TOPIC_ID)
        assert len(rows) == 1
        assert rows[0]["status"] == "confirmed"
        assert rows[0]["decision"] == "agreed"
        assert rows[0]["reviewed_at"]

    def test_dismiss_does_not_remove_the_row(self):
        data, entry_id = self._data_with_candidate()
        updated, _old = g.set_misconception_status(
            data, TOPIC_ID, entry_id, "dismissed", decision="disagreed"
        )
        rows = g.misconceptions_for_topic(updated, TOPIC_ID)
        assert len(rows) == 1
        assert rows[0]["status"] == "dismissed"

    def test_unknown_status_is_rejected(self):
        data, entry_id = self._data_with_candidate()
        with pytest.raises(ValueError):
            g.set_misconception_status(data, TOPIC_ID, entry_id, "teacher_approved")

    def test_missing_entry_returns_none(self):
        data, _entry_id = self._data_with_candidate()
        assert g.set_misconception_status(data, TOPIC_ID, "nope", "confirmed") is None

    def test_source_data_is_not_mutated(self):
        data, entry_id = self._data_with_candidate()
        g.set_misconception_status(data, TOPIC_ID, entry_id, "confirmed")
        assert g.misconceptions_for_topic(data, TOPIC_ID)[0]["status"] == "candidate"

    def test_legacy_row_gets_its_deterministic_id_burned_in(self):
        """旧行を遷移させると、内容依存だった ID が行へ焼き込まれる。"""
        raw = _legacy_entry()
        data = g.parse_personal_graph({"misconceptions_by_topic": {TOPIC_ID: [raw]}})
        entry_id = g.misconception_entry_id(raw)
        updated, _old = g.set_misconception_status(data, TOPIC_ID, entry_id, "confirmed")
        assert updated.misconceptions_by_topic[TOPIC_ID][0]["id"] == entry_id


class TestEntryIds:
    """旧行にも位置に依存しない安定 ID を与える。"""

    def test_legacy_id_is_deterministic(self):
        raw = _legacy_entry()
        assert g.misconception_entry_id(raw) == g.misconception_entry_id(dict(raw))

    def test_legacy_id_survives_prepending_new_entries(self):
        raw = _legacy_entry()
        data = g.parse_personal_graph({"misconceptions_by_topic": {TOPIC_ID: [raw]}})
        entry_id = g.misconception_entry_id(raw)
        data = g.append_misconception(
            data, TOPIC_ID, g.new_misconception_entry(label="new", wrong="w")
        )
        assert g.find_misconception(data, TOPIC_ID, entry_id) is not None

    def test_explicit_id_wins(self):
        assert g.misconception_entry_id({"id": "abc", "label": "L"}) == "abc"

    def test_unknown_keys_are_preserved(self):
        data = g.parse_personal_graph(
            {"misconceptions_by_topic": {}, "future_key": {"a": 1}}
        )
        assert g.to_jsonb(data)["future_key"] == {"a": 1}


# ---------------------------------------------------------------------------
# 2. services（DB はフェイクセッション）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [] if self._row is None else [self._row]


class _FakeSession:
    """``learning_states.personal_graph`` だけを保持する素朴なフェイク。"""

    def __init__(self, personal_graph: dict | None = None, *, row_exists: bool = True):
        self.personal_graph = dict(personal_graph or {})
        self.row_exists = row_exists
        self.committed = False
        self.rolled_back = False
        self.updates = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        if "SELECT personal_graph FROM learning_states" in sql:
            return _Result((self.personal_graph,) if self.row_exists else None)
        if "UPDATE learning_states" in sql and "personal_graph" in sql:
            self.updates += 1
            raw = params.get("personal")
            self.personal_graph = json.loads(raw) if isinstance(raw, str) else (raw or {})
            return _Result(None)
        return _Result(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture()
def audit_calls(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        services, "record_review_event",
        lambda *a, **k: calls.append((a, k)),
    )
    return calls


def _install_session(monkeypatch, session):
    monkeypatch.setattr(services, "_pg_session", lambda: session)
    return session


class TestServicesReview:
    """``review_personal_misconception`` は candidate だけを遷移させる。"""

    def _session_with_candidate(self, monkeypatch):
        entry = g.new_misconception_entry(label="L", wrong="w", correct="c")
        session = _FakeSession({"misconceptions_by_topic": {TOPIC_ID: [entry]}})
        _install_session(monkeypatch, session)
        return session, entry["id"]

    def test_agreed_confirms(self, monkeypatch, audit_calls):
        session, entry_id = self._session_with_candidate(monkeypatch)
        result = services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "agreed"
        )
        assert result == {"entry_id": entry_id, "status": "confirmed", "decision": "agreed"}
        stored = session.personal_graph["misconceptions_by_topic"][TOPIC_ID]
        assert stored[0]["status"] == "confirmed"
        assert session.committed

    def test_disagreed_dismisses_without_removing_the_row(self, monkeypatch, audit_calls):
        session, entry_id = self._session_with_candidate(monkeypatch)
        result = services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "disagreed"
        )
        assert result["status"] == "dismissed"
        stored = session.personal_graph["misconceptions_by_topic"][TOPIC_ID]
        assert len(stored) == 1

    def test_verdict_wrong_records_a_distinct_decision(self, monkeypatch, audit_calls):
        session, entry_id = self._session_with_candidate(monkeypatch)
        services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "verdict_wrong"
        )
        stored = session.personal_graph["misconceptions_by_topic"][TOPIC_ID]
        assert stored[0]["status"] == "dismissed"
        assert stored[0]["decision"] == "verdict_wrong"

    def test_second_review_of_the_same_entry_is_rejected(self, monkeypatch, audit_calls):
        session, entry_id = self._session_with_candidate(monkeypatch)
        services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "agreed"
        )
        assert services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "disagreed"
        ) is None

    def test_unknown_decision_raises_value_error(self, monkeypatch, audit_calls):
        _session, entry_id = self._session_with_candidate(monkeypatch)
        with pytest.raises(ValueError):
            services.review_personal_misconception(
                CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "passed"
            )

    def test_missing_entry_returns_none(self, monkeypatch, audit_calls):
        self._session_with_candidate(monkeypatch)
        assert services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, "nope", "agreed"
        ) is None

    def test_missing_learning_state_returns_none(self, monkeypatch, audit_calls):
        _install_session(monkeypatch, _FakeSession({}, row_exists=False))
        assert services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, "x", "agreed"
        ) is None

    def test_audit_is_recorded_without_verbatim_text(self, monkeypatch, audit_calls):
        _session, entry_id = self._session_with_candidate(monkeypatch)
        services.review_personal_misconception(
            CURRENT_USER["id"], COURSE_ID, TOPIC_ID, entry_id, "agreed"
        )
        assert len(audit_calls) == 1
        args, _kwargs = audit_calls[0]
        assert args[0] == AUDIT_ENTITY_MISCONCEPTION
        assert args[1] == entry_id
        assert (args[2], args[3]) == ("candidate", "confirmed")
        assert args[4] == CURRENT_USER["id"]
        metadata = args[5]
        assert metadata["decision"] == "agreed"
        # 逐語（学習者の発言・AI の訂正文）は監査に載せない。
        assert set(metadata) == {"decision", "course_id", "topic_id"}


class TestServicesProjection:
    """読み出しの正規化と、確定だけを数える進捗。"""

    def test_get_personal_layer_normalizes_legacy_rows(self, monkeypatch):
        _install_session(
            monkeypatch,
            _FakeSession({"misconceptions_by_topic": {TOPIC_ID: [_legacy_entry()]}}),
        )
        layer = services.get_personal_layer(CURRENT_USER["id"], COURSE_ID)
        row = layer["misconceptions_by_topic"][TOPIC_ID][0]
        assert row["status"] == "candidate"
        assert row["id"]

    def test_progress_counts_only_confirmed(self, monkeypatch):
        entries = [
            dict(g.new_misconception_entry(label="a", wrong="w"), status="confirmed"),
            g.new_misconception_entry(label="b", wrong="w"),
            dict(g.new_misconception_entry(label="c", wrong="w"), status="dismissed"),
        ]
        monkeypatch.setattr(
            services, "get_personal_layer",
            lambda *_a, **_k: {"misconceptions_by_topic": {TOPIC_ID: entries}},
        )
        monkeypatch.setattr(
            services, "get_course_completion",
            lambda *_a, **_k: {"completed_topic_ids": [], "course_completed": False},
        )
        _install_session(monkeypatch, _FakeSession({}))
        progress = services.calculate_progress(CURRENT_USER["id"], COURSE_ID, {"topics": []})
        assert progress["misconceptions"] == 1


class TestDetectionWritesCandidates:
    """検出は候補としてしか書かない。"""

    def test_detection_records_candidate_entry(self, monkeypatch):
        recorded: list[dict] = []
        monkeypatch.setattr(
            services, "record_personal_misconception",
            lambda _u, _c, _t, entry: recorded.append(entry),
        )
        monkeypatch.setattr(services, "get_personal_layer", lambda *_a, **_k: {})
        services.detect_and_record_misconception(
            CURRENT_USER["id"], COURSE_ID, {}, TOPIC_ID,
            "これは A だと思います", "訂正: 正しくは B です",
            message_id="msg-9",
        )
        assert len(recorded) == 1
        assert recorded[0]["status"] == "candidate"
        assert recorded[0]["correct"] == "正しくは B です"
        assert recorded[0]["message_id"] == "msg-9"

    def test_detection_keeps_correct_none_when_not_extractable(self, monkeypatch):
        recorded: list[dict] = []
        monkeypatch.setattr(
            services, "record_personal_misconception",
            lambda _u, _c, _t, entry: recorded.append(entry),
        )
        monkeypatch.setattr(services, "get_personal_layer", lambda *_a, **_k: {})
        services.detect_and_record_misconception(
            CURRENT_USER["id"], COURSE_ID, {}, TOPIC_ID,
            "これは A だと思います", "少し誤解があるかもしれません。",
        )
        assert recorded[0]["correct"] is None
        assert recorded[0]["status"] == "candidate"


# ---------------------------------------------------------------------------
# 3. route（FastAPI を経由せず直接呼ぶ。test_learning_chat_infra.py と同型）
# ---------------------------------------------------------------------------


def _call_route(monkeypatch, decision, *, course_ok=True, topic_ok=True, result=None):
    monkeypatch.setattr(
        learning_mod, "get_accessible_course_data",
        lambda *_a, **_k: ({"topics": [{"id": TOPIC_ID, "title": "T"}]} if course_ok else None),
    )
    monkeypatch.setattr(
        learning_mod, "find_course_topic",
        lambda *_a, **_k: ({"id": TOPIC_ID} if topic_ok else None),
    )
    monkeypatch.setattr(learning_mod, "review_personal_misconception", lambda *_a, **_k: result)
    monkeypatch.setattr(
        learning_mod, "get_personal_layer",
        lambda *_a, **_k: {"misconceptions_by_topic": {}, "chat_anchors": {}},
    )
    body = learning_mod.MisconceptionReviewRequest(decision=decision)
    return learning_mod.review_misconception_route(
        COURSE_ID, TOPIC_ID, "entry-1", body, current_user=CURRENT_USER,
    )


class TestReviewRoute:
    def test_invalid_decision_is_422(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            _call_route(monkeypatch, "passed")
        assert exc.value.status_code == 422

    def test_empty_decision_is_422(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            _call_route(monkeypatch, "  ")
        assert exc.value.status_code == 422

    def test_inaccessible_course_is_404(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            _call_route(monkeypatch, "agreed", course_ok=False)
        assert exc.value.status_code == 404

    def test_missing_topic_is_404(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            _call_route(monkeypatch, "agreed", topic_ok=False)
        assert exc.value.status_code == 404

    def test_missing_candidate_is_404(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            _call_route(monkeypatch, "agreed", result=None)
        assert exc.value.status_code == 404

    def test_success_returns_status_and_personal_layer(self, monkeypatch):
        payload = _call_route(
            monkeypatch, "agreed",
            result={"entry_id": "entry-1", "status": "confirmed", "decision": "agreed"},
        )
        assert payload["ok"] is True
        assert payload["status"] == "confirmed"
        assert "misconceptions_by_topic" in payload["personal_layer"]

    def test_no_delete_route_for_misconceptions(self):
        """行削除の経路を作らない（P4）。"""
        src = _read(LEARNING_PY)
        assert '@router.delete("/courses/{course_id}/topics/{topic_id}/misconceptions' not in src


# ---------------------------------------------------------------------------
# 4. ガードレール（構造）
# ---------------------------------------------------------------------------


class TestGuardrails:
    def test_core_accessor_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _read(GRAPH_DATA_PY), ["fastapi", "sqlalchemy"], context="graph_data.py",
        )

    def test_review_uses_candidate_flow(self):
        """状態遷移は共通プリミティブに接続する（新しいワークフローを書かない）。"""
        src = extract_function_source(_read(SERVICES_PY), "review_personal_misconception")
        assert "candidate_flow.CandidateFlow(" in src
        assert "MISCONCEPTION_VOCAB" in src
        assert "AUDIT_ENTITY_MISCONCEPTION" in src

    def test_review_does_not_delete_rows(self):
        src = extract_function_source(_read(SERVICES_PY), "review_personal_misconception")
        assert_source_forbids(
            src, ["DELETE FROM", ".pop(", ".remove(", "del "],
            context="review_personal_misconception",
        )

    def test_detection_passes_message_id_from_the_route(self):
        src = _read(LEARNING_PY)
        assert "message_id=body.message_id or None," in src


# ---------------------------------------------------------------------------
# 5. フロントエンド静的検証（app.js / styles.css / マニュアル）
# ---------------------------------------------------------------------------

RENDER_SIG = "function renderMisconceptionSection(topic) {"
CANDIDATE_CARD_SIG = "function misconceptionCandidateCardHtml(m) {"
BODY_SIG = "function misconceptionBodyHtml(m) {"
BIND_SIG = "function bindMisconceptionActions(el) {"
REVIEW_SIG = "async function reviewMisconception(entryId, decision) {"

# 候補（未確認）の区画で使ってはならない断定語彙。旧 UI の「誤っていた理解 → 正しい理解」
# という判決文の形式が復活していないことを固定する。
ASSERTIVE_WORDS = (
    "誤っていた理解",
    "正しい理解",
    "あなたの誤解メモ",
    "指摘された理解の誤り",
    "理解の誤りです",
)

# 数値・進捗・ゲーミフィケーション語彙（test_check_options_ui_static.py と同一集合）。
FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")


def _extract_body(src: str, signature: str) -> str:
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated body for: " + signature)


def _misconception_ui_source() -> str:
    js = _read(APP_JS)
    return "\n".join(
        _extract_body(js, sig)
        for sig in (BODY_SIG, CANDIDATE_CARD_SIG, RENDER_SIG, BIND_SIG, REVIEW_SIG)
    )


class TestUiCandidateFraming:
    """候補は仮説文体・確定にだけ断定語彙。"""

    def test_candidate_heading_is_hypothetical(self):
        body = _extract_body(_read(APP_JS), RENDER_SIG)
        assert "AI が訂正を提案した箇所（未確認）" in body
        assert "あなたが確定した誤解メモ" in body

    def test_candidate_note_leaves_the_decision_to_the_learner(self):
        body = _extract_body(_read(APP_JS), RENDER_SIG)
        assert "あなたが決めます" in body
        assert "答えなくても構いません" in body

    def test_no_assertive_verdict_wording(self):
        assert_source_forbids(
            _misconception_ui_source(), ASSERTIVE_WORDS, context="app.js misconception UI",
        )

    def test_no_gamification_wording(self):
        assert_source_forbids(
            _misconception_ui_source(), FORBIDDEN_WORDS, context="app.js misconception UI",
        )

    def test_missing_correction_is_stated_as_fact(self):
        js = _read(APP_JS)
        assert "AI が訂正を示唆しましたが、訂正文を抽出できませんでした。" in js

    def test_no_count_badge_for_candidates(self):
        """候補に件数バッジ（旧 .mc-bd）を付けない。"""
        body = _extract_body(_read(APP_JS), RENDER_SIG)
        assert "mc-bd" not in body


class TestUiThreeChoices:
    """3択 + [あとで]。決定はサーバ、[あとで] はクライアントのみ。"""

    def test_three_decisions_present(self):
        body = _extract_body(_read(APP_JS), CANDIDATE_CARD_SIG)
        for decision in ("agreed", "disagreed", "verdict_wrong"):
            assert 'data-mc-decision="' + decision + '"' in body

    def test_defer_button_present(self):
        body = _extract_body(_read(APP_JS), CANDIDATE_CARD_SIG)
        assert "data-mc-defer=" in body
        assert "あとで" in body

    def test_defer_handler_does_not_call_the_api(self):
        body = _extract_body(_read(APP_JS), BIND_SIG)
        defer = body[body.index("data-mc-defer"):]
        assert "apiFetch" not in defer
        assert "fetch(" not in defer

    def test_review_posts_to_the_review_endpoint(self):
        body = _extract_body(_read(APP_JS), REVIEW_SIG)
        assert "/misconceptions/" in body
        assert "/review" in body
        assert '"POST"' in body
        assert "decision: decision" in body

    def test_review_parses_the_response_body(self):
        """apiFetch は Response を返す（parse を忘れると personal_layer が反映されない）。"""
        body = _extract_body(_read(APP_JS), REVIEW_SIG)
        assert "res.ok" in body
        assert "await res.json()" in body
        assert "state.personalLayer = data.personal_layer" in body

    def test_review_failure_does_not_break_learning(self):
        body = _extract_body(_read(APP_JS), REVIEW_SIG)
        assert "catch (_)" in body


class TestUiKeepsEverything:
    """表示を絞るときは畳む（消さない）。却下も読み返せる。"""

    def test_older_entries_are_folded_not_dropped(self):
        body = _extract_body(_read(APP_JS), RENDER_SIG)
        assert "これより前の提案も見る" in body
        assert "これより前の記録も見る" in body
        assert "<details" in body
        assert "slice(MISCONCEPTION_VISIBLE_CANDIDATES)" in body

    def test_dismissed_entries_stay_readable(self):
        body = _extract_body(_read(APP_JS), RENDER_SIG)
        assert "受け入れなかった訂正も見る" in body
        assert "記録は消えません" in body

    def test_sidebar_badge_distinguishes_candidate_from_confirmed(self):
        js = _read(APP_JS)
        assert "mc-badge-candidate" in js
        assert "AI が訂正を提案した箇所があります（未確認）" in js


class TestLearnerHelpAnchor:
    """UI アンカー3点セット（正本 = core/help_kb/ui_anchors.py）。"""

    def test_anchor_registered_in_known_ids(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS

    def test_anchor_mapped_to_the_student_manual(self):
        assert UI_ANCHORS.get(ANCHOR_ID) == MANUAL_REF

    def test_data_ui_anchor_attribute_present(self):
        body = _extract_body(_read(APP_JS), CANDIDATE_CARD_SIG)
        assert 'data-ui-anchor="' + ANCHOR_ID + '"' in body

    def test_manual_section_exists_with_explicit_anchor(self):
        section = _manual_section()
        assert "AI が訂正を提案した箇所（未確認）" in section
        for label in (
            "そう、これは私の誤解だった", "これは誤解ではない",
            "AI の訂正のほうが違う", "あとで",
        ):
            assert label in section

    def test_manual_states_candidate_is_not_a_verdict(self):
        section = _manual_section()
        assert "判定ではありません" in section
        assert "確定するまで「誤解」とは呼びません" in section
        assert "記録は消えません" in section
        assert "成績評価に使われたり" in section

    def test_manual_section_has_no_forbidden_words(self):
        assert_source_forbids(
            _manual_section(), FORBIDDEN_WORDS, context="02-student.md#misconception-review",
        )

    def test_css_rules_exist(self):
        css = _read(STYLES_CSS)
        for cls in (".mc-candidate", ".mc-badge-candidate", ".mc-dismissed-row", ".mc-older"):
            assert cls in css, f"styles.css に {cls} がありません"


def _manual_section() -> str:
    md = _read(STUDENT_MANUAL)
    start = md.index("{#misconception-review}")
    rel_end = md[start:].find("\n### ")
    return md[start:start + rel_end] if rel_end != -1 else md[start:]
