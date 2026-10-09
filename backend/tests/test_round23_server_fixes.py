"""第 23 周・第 3 波（サーバ側の根）の回帰テスト。

- 書き直し・取り消しで、元の往復から生まれた誤解候補を message_id で supersede する
- 問いの軌跡で質問の行に「誤答」という評価語を付けない
- コース目次: 会話したトピックを「学習中」に重ね、locked の表示語を付ける
- discuss 開幕: 学習者 DTO のバックボーンから解析の内部語・英語の中間文を落とす
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import label_vocab  # noqa: E402
from core.personal_graph import graph_data  # noqa: E402


# ---------------------------------------------------------------------------
# 2. 誤解候補の supersede
# ---------------------------------------------------------------------------


def _data_with(entries):
    return graph_data.parse_personal_graph({"misconceptions_by_topic": {"t1": entries}})


class TestSupersedeMisconceptions:
    def test_candidate_from_removed_message_is_superseded_with_reason(self):
        cand = graph_data.new_misconception_entry(label="x", wrong="音速を自由に", message_id="m1")
        other = graph_data.new_misconception_entry(label="y", wrong="別", message_id="m2")
        updated, changed = graph_data.supersede_misconceptions_for_messages(
            _data_with([cand, other]), ["m1"], reason="rewrite"
        )
        assert changed == 1
        rows = graph_data.misconceptions_for_topic(updated, "t1")
        by_msg = {r["message_id"]: r for r in rows}
        assert by_msg["m1"]["status"] == "superseded"
        assert by_msg["m1"]["superseded_reason"] == "rewrite"
        assert by_msg["m2"]["status"] == "candidate"
        # 行は消さない（P4）
        assert len(rows) == 2

    def test_human_decisions_and_legacy_rows_are_not_touched(self):
        confirmed = dict(graph_data.new_misconception_entry(label="c", wrong="a", message_id="m1"))
        confirmed["status"] = "confirmed"
        legacy = {"label": "旧", "wrong": "b"}  # message_id なし
        updated, changed = graph_data.supersede_misconceptions_for_messages(
            _data_with([confirmed, legacy]), ["m1"], reason="delete"
        )
        assert changed == 0
        statuses = [r["status"] for r in graph_data.misconceptions_for_topic(updated, "t1")]
        assert statuses == ["confirmed", "candidate"]

    def test_empty_ids_is_noop(self):
        cand = graph_data.new_misconception_entry(label="x", wrong="w", message_id="m1")
        _, changed = graph_data.supersede_misconceptions_for_messages(
            _data_with([cand]), [], reason="rewrite"
        )
        assert changed == 0


class _Res:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Session:
    def __init__(self, personal):
        self.personal = personal
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if "SELECT personal_graph" in sql:
            return _Res((self.personal,) if self.personal is not None else None)
        return _Res()


class TestSupersedeInSession:
    def test_updates_learning_states_only_when_changed(self):
        import services

        cand = graph_data.new_misconception_entry(label="x", wrong="w", message_id="m1")
        session = _Session({"misconceptions_by_topic": {"t1": [cand]}})
        n = services._supersede_misconceptions_in_session(
            session, "u", "c", ["m1"], reason="delete"
        )
        assert n == 1
        updates = [p for sql, p in session.calls if "UPDATE learning_states" in sql]
        assert len(updates) == 1
        stored = json.loads(updates[0]["personal"])
        assert stored["misconceptions_by_topic"]["t1"][0]["superseded_reason"] == "delete"

    def test_no_row_no_update(self):
        import services

        session = _Session(None)
        assert services._supersede_misconceptions_in_session(
            session, "u", "c", ["m1"], reason="rewrite"
        ) == 0
        assert not any("UPDATE learning_states" in sql for sql, _ in session.calls)

    def test_truncate_calls_the_misconception_supersede(self):
        from tests.guardrail_helpers import extract_function_source

        src = extract_function_source(
            (BACKEND / "api" / "services.py").read_text(encoding="utf-8"),
            "truncate_chat_and_supersede",
        )
        assert "_supersede_misconceptions_in_session(" in src
        assert "reason=reason" in src


# ---------------------------------------------------------------------------
# 3. 問いの軌跡の種別ラベル
# ---------------------------------------------------------------------------


class TestTrajectoryKindLabels:
    def test_misconception_row_is_not_called_wrong_answer(self):
        assert label_vocab.TRACE_TRAJECTORY_KIND_LABELS["misconception"] == "訂正あり"
        for table in (
            label_vocab.TRACE_TRAJECTORY_KIND_LABELS,
            label_vocab.TRACE_TRAJECTORY_KIND_WORDS,
        ):
            assert all("誤答" not in v for v in table.values())

    def test_learning_experience_has_no_wrong_answer_word(self):
        src = (BACKEND / "core" / "learning_experience.py").read_text(encoding="utf-8")
        assert '"誤答"' not in src


# ---------------------------------------------------------------------------
# 4. コース目次の状態
# ---------------------------------------------------------------------------


class TestTopicStatusOverlay:
    DATA = {
        "topics": [
            {"id": "t0", "title": "A", "chapter_index": 0, "status": "in_progress"},
            {"id": "t1", "title": "B", "chapter_index": 0, "status": "locked"},
            {"id": "t18", "title": "C", "chapter_index": 0, "status": "locked"},
        ],
    }

    def test_touched_topic_becomes_in_progress_not_completed(self):
        from api.routes import learning as learning_mod

        out = learning_mod._overlay_learner_progress(self.DATA, [], ["t18"])
        assert [t["status"] for t in out["topics"]] == ["in_progress", "locked", "in_progress"]
        assert self.DATA["topics"][2]["status"] == "locked"

    def test_status_label_is_attached_for_learner(self):
        from api.routes import learning as learning_mod

        out = learning_mod._project_topics_for_learner(self.DATA)
        labels = {t["id"]: t["status_label"] for t in out["topics"]}
        assert labels["t1"] == label_vocab.TOPIC_STATUS_LABELS["locked"]
        assert "開けます" in labels["t1"]
        assert labels["t0"] == "学習中"

    def test_touched_query_excludes_pseudo_topics(self):
        import services

        src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        body = src.split("def list_learner_touched_topic_ids")[1].split("\ndef ")[0]
        assert 'startswith("_")' in body
        assert "completed" not in body.split('"""')[2]  # 完了には使わない
        assert hasattr(services, "list_learner_touched_topic_ids")


# ---------------------------------------------------------------------------
# 5. discuss 開幕のバックボーン
# ---------------------------------------------------------------------------


class TestLearnerBackbone:
    def test_internal_words_are_dropped(self):
        from core.discuss.opening import learner_backbone_node, project_backbone

        projected, _ = project_backbone([
            {
                "component_id": "theory_op_0001",
                "label": "Theory basis",
                "graph_layer": "main",
                "source_backing_status": "source_backed",
                "review_status": "source_backed",
                "review_reasons": ["missing_atomic_claim"],
                "description": "Defines the basic action of the model.",
            },
            {
                "component_id": "theory_op_0002",
                "label": "Equation system",
                "graph_layer": "main",
                "source_backing_status": "review_required",
            },
        ])
        nodes = [learner_backbone_node(n) for n in projected]
        assert set(nodes[0]) == {"node_id", "label", "stage_label", "backed"}
        assert nodes[0]["backed"] is True and nodes[1]["backed"] is False
        text = json.dumps(nodes, ensure_ascii=False)
        for word in ("theory_basis", "source_backed", "missing_atomic_claim", "Defines"):
            assert word not in text

    def test_build_opening_applies_the_learner_projection(self):
        from tests.guardrail_helpers import extract_function_source

        src = extract_function_source(
            (BACKEND / "core" / "discuss" / "opening.py").read_text(encoding="utf-8"),
            "build_opening",
        )
        fragile = src.index("project_fragile_points(")
        learner = src.index("learner_backbone_node(")
        # 脆い箇所の導出（内部語が要る）の後で絞る。
        assert fragile < learner
