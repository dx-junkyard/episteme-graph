"""前提知識の ID 参照化と半順序検査（学ぶ単位の一級化 Phase 2 / P2-4）の検証。

正本: ``docs/features/learning_units_design.md`` §6.4（LU1 / LU3 / LU5 / LU6）。

観点:
  1. 解決は**正規化題名の完全一致**だけ（部分一致・曖昧一致では結ばない = 推測しない）
  2. 入力を mutate しない（呼び出し側の course_data を書き換えない）
  3. 循環 / 冗長 / 未解決 / 前方参照をコース構造だけから検出する
  4. 事実文に数字（件数・割合）が出ない（LU5）・督促語彙が無い
  5. 学習者の入力を受け取る引数が無い（LU6 / UC5 / UC7 の恒久排除を inspect で固定）
  6. FastAPI / SQLAlchemy / LLM を import しない純関数モジュール（LU3）
"""

from __future__ import annotations

import copy
import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import course_prerequisites as cp  # noqa: E402
from tests.guardrail_helpers import assert_source_does_not_import, assert_source_forbids  # noqa: E402

_SRC = (BACKEND / "core" / "course_prerequisites.py").read_text(encoding="utf-8")


def _topic(tid, title, *, chapter=0, prerequisites=None):
    return {
        "id": tid,
        "title": title,
        "chapter_index": chapter,
        "prerequisites": list(prerequisites or []),
    }


# ---------------------------------------------------------------------------
# 1. resolve_prerequisite_topic_ids
# ---------------------------------------------------------------------------


class TestResolveTopicIds:
    def test_exact_title_match_fills_topic_id(self):
        topics = [
            _topic("t0", "波動関数"),
            _topic("t1", "期待値", prerequisites=["波動関数"]),
        ]
        resolved = cp.resolve_prerequisite_topic_ids(topics)
        assert resolved[1]["prerequisites"][0]["topic_id"] == "t0"
        assert resolved[1]["prerequisites"][0]["name"] == "波動関数"

    def test_normalization_absorbs_width_case_and_spaces(self):
        topics = [
            _topic("t0", "Hilbert Space"),
            _topic("t1", "射影", prerequisites=["ｈｉｌｂｅｒｔ　space"]),
        ]
        resolved = cp.resolve_prerequisite_topic_ids(topics)
        assert resolved[1]["prerequisites"][0]["topic_id"] == "t0"

    def test_partial_match_does_not_resolve(self):
        """部分一致では結ばない（推測しない, LU3）。"""
        topics = [
            _topic("t0", "波動関数の基礎"),
            _topic("t1", "期待値", prerequisites=["波動関数"]),
        ]
        resolved = cp.resolve_prerequisite_topic_ids(topics)
        assert resolved[1]["prerequisites"][0]["topic_id"] is None

    def test_ambiguous_titles_are_not_resolved(self):
        """同じ題名のトピックが複数あるときは引かない（どちらか分からない）。"""
        topics = [
            _topic("t0", "基礎"),
            _topic("t1", "基礎"),
            _topic("t2", "応用", prerequisites=["基礎"]),
        ]
        resolved = cp.resolve_prerequisite_topic_ids(topics)
        assert resolved[2]["prerequisites"][0]["topic_id"] is None

    def test_self_reference_is_not_created(self):
        topics = [_topic("t0", "波動関数", prerequisites=["波動関数"])]
        resolved = cp.resolve_prerequisite_topic_ids(topics)
        assert resolved[0]["prerequisites"][0]["topic_id"] is None

    def test_existing_topic_id_is_preserved(self):
        topics = [
            _topic("t0", "波動関数"),
            _topic("t1", "期待値", prerequisites=[{"name": "旧名", "topic_id": "t0"}]),
        ]
        resolved = cp.resolve_prerequisite_topic_ids(topics)
        assert resolved[1]["prerequisites"][0]["topic_id"] == "t0"
        assert resolved[1]["prerequisites"][0]["name"] == "旧名"  # 名前は落とさない

    def test_plain_string_prerequisites_become_dicts(self):
        topics = [_topic("t0", "期待値", prerequisites=["波動関数"])]
        item = cp.resolve_prerequisite_topic_ids(topics)[0]["prerequisites"][0]
        assert item["name"] == "波動関数"
        assert item["status"] == cp.DEFAULT_PREREQUISITE_STATUS
        assert item["topic_id"] is None

    def test_extra_keys_are_kept(self):
        topics = [_topic("t0", "期待値", prerequisites=[{"name": "A", "status": "mastered", "memo": "x"}])]
        item = cp.resolve_prerequisite_topic_ids(topics)[0]["prerequisites"][0]
        assert item["status"] == "mastered"
        assert item["memo"] == "x"

    def test_input_is_not_mutated(self):
        topics = [
            _topic("t0", "波動関数"),
            _topic("t1", "期待値", prerequisites=["波動関数"]),
        ]
        snapshot = copy.deepcopy(topics)
        cp.resolve_prerequisite_topic_ids(topics)
        assert topics == snapshot

    def test_non_dict_topics_are_skipped(self):
        assert cp.resolve_prerequisite_topic_ids(["x", None]) == []
        assert cp.resolve_prerequisite_topic_ids(None) == []


# ---------------------------------------------------------------------------
# 2. analyze_prerequisite_order
# ---------------------------------------------------------------------------


class TestOrderAnalysis:
    def test_cycle_of_two(self):
        topics = [
            _topic("t0", "A", prerequisites=["B"]),
            _topic("t1", "B", prerequisites=["A"]),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert report.cycles == [["t0", "t1"]]
        assert "「A」は「B」を前提とし、「B」は「A」を前提としています。" in report.to_facts()

    def test_cycle_of_three(self):
        topics = [
            _topic("t0", "A", prerequisites=["B"]),
            _topic("t1", "B", prerequisites=["C"]),
            _topic("t2", "C", prerequisites=["A"]),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert len(report.cycles) == 1
        assert set(report.cycles[0]) == {"t0", "t1", "t2"}
        fact = report.to_facts()[0]
        assert fact.endswith("としています。")

    def test_self_loop_via_explicit_topic_id(self):
        topics = [_topic("t0", "A", prerequisites=[{"name": "A", "topic_id": "t0"}])]
        report = cp.analyze_prerequisite_order(topics)
        assert report.cycles == [["t0"]]
        assert report.to_facts() == ["「A」は、自分自身を前提としています。"]

    def test_redundant_edge_reports_the_intermediate(self):
        topics = [
            _topic("t0", "A"),
            _topic("t1", "B", prerequisites=["A"]),
            _topic("t2", "C", prerequisites=["A", "B"]),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert report.redundant == [("t2", "t0", "t1")]
        assert report.to_facts() == ["「C」の前提「A」は、「B」を経て既に含まれています。"]

    def test_direct_edge_is_not_redundant_without_another_path(self):
        topics = [_topic("t0", "A"), _topic("t1", "B", prerequisites=["A"])]
        report = cp.analyze_prerequisite_order(topics)
        assert report.redundant == []
        assert report.to_facts() == []

    def test_unresolved_prerequisite(self):
        topics = [_topic("t0", "A", prerequisites=["未登録の前提"])]
        report = cp.analyze_prerequisite_order(topics)
        assert report.unresolved == [("t0", "未登録の前提")]
        assert report.to_facts() == [
            "「A」の前提「未登録の前提」に対応するトピックがこのコースにありません。"
        ]

    def test_forward_reference_across_chapters(self):
        topics = [
            _topic("t0", "A", chapter=0, prerequisites=["B"]),
            _topic("t1", "B", chapter=1),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert report.forward_references == [("t0", "t1", True)]
        assert report.to_facts() == ["「A」は、後の章にある「B」を前提にしています。"]

    def test_forward_reference_inside_a_chapter(self):
        topics = [
            _topic("t0", "A", chapter=0, prerequisites=["B"]),
            _topic("t1", "B", chapter=0),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert report.forward_references == [("t0", "t1", False)]
        assert report.to_facts() == ["「A」は、同じ章の後ろにある「B」を前提にしています。"]

    def test_backward_reference_is_not_reported(self):
        topics = [
            _topic("t0", "A", chapter=0),
            _topic("t1", "B", chapter=1, prerequisites=["A"]),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert report.forward_references == []
        assert report.is_empty()

    def test_healthy_course_has_no_facts(self):
        topics = [
            _topic("t0", "A"),
            _topic("t1", "B", prerequisites=["A"]),
            _topic("t2", "C", prerequisites=["B"]),
        ]
        report = cp.analyze_prerequisite_order(topics)
        assert report.to_facts() == []
        assert report.is_empty()

    def test_labels_fall_back_to_topic_id(self):
        topics = [_topic("t0", "", prerequisites=["未登録"])]
        report = cp.analyze_prerequisite_order(topics)
        assert report.label("t0") == "t0"

    def test_analysis_is_deterministic(self):
        topics = [
            _topic("t0", "A", prerequisites=["B"]),
            _topic("t1", "B", prerequisites=["A"]),
            _topic("t2", "C", prerequisites=["未登録", "A", "B"]),
        ]
        first = cp.analyze_prerequisite_order(topics).to_facts()
        second = cp.analyze_prerequisite_order(copy.deepcopy(topics)).to_facts()
        assert first == second

    def test_input_is_not_mutated(self):
        topics = [
            _topic("t0", "A", prerequisites=["B"]),
            _topic("t1", "B", prerequisites=["A"]),
        ]
        snapshot = copy.deepcopy(topics)
        cp.analyze_prerequisite_order(topics)
        assert topics == snapshot


# ---------------------------------------------------------------------------
# 3. 数値非表示・煽らない（LU5 / G6）
# ---------------------------------------------------------------------------


class TestFactsAreQuietAndNumberFree:
    _FORBIDDEN = ("！", "今すぐ", "急いで", "必ず", "早く", "至急", "件", "%")

    def _report(self):
        topics = [
            _topic("t0", "アルファ", chapter=0, prerequisites=["ベータ"]),
            _topic("t1", "ベータ", chapter=0, prerequisites=["アルファ"]),
            _topic("t2", "ガンマ", chapter=0),
            _topic("t3", "デルタ", chapter=0, prerequisites=["ガンマ", "イプシロン", "未登録の前提"]),
            _topic("t4", "イプシロン", chapter=1, prerequisites=["ガンマ"]),
        ]
        return cp.analyze_prerequisite_order(topics)

    def test_facts_are_produced_for_every_category(self):
        report = self._report()
        assert report.cycles and report.redundant and report.unresolved
        assert report.forward_references
        assert len(report.to_facts()) == (
            len(report.cycles)
            + len(report.redundant)
            + len(report.unresolved)
            + len(report.forward_references)
        )

    def test_no_digits_in_facts(self):
        for fact in self._report().to_facts():
            assert not any(ch.isdigit() for ch in fact), fact

    def test_no_pushy_or_counting_words(self):
        for fact in self._report().to_facts():
            for word in self._FORBIDDEN:
                assert word not in fact, f"{word} が事実文に含まれる: {fact}"


# ---------------------------------------------------------------------------
# 4. 構造ガードレール（LU3 / LU6）
# ---------------------------------------------------------------------------


class TestCheckPrerequisitesUsesTopicId:
    """``services.check_prerequisites`` の P2-4 対応（§6.4）。

    - ``topic_id`` があれば同コース topic を引き、**表示名は現在の題名**にする
      （題名を変えても接続が切れない）。
    - 記帳・突合キーは従来どおり前提の**名前**（LU1: 既存キーの意味を変えない）。
    - 判定材料は本人の明示的な記帳だけ（是正 F4 のまま。LU6）。
    """

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Session:
        def __init__(self, rows=()):
            self._rows = rows

        def execute(self, statement, *_a, **_k):
            return TestCheckPrerequisitesUsesTopicId._Rows(self._rows)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    @staticmethod
    def _course_data(prereq):
        return {
            "topics": [
                {"id": "t0", "title": "期待値", "prerequisites": [prereq]},
                {"id": "t1", "title": "波動関数（改題）", "prerequisites": []},
            ]
        }

    def _check(self, monkeypatch, prereq, *, acknowledged=None):
        from api import services

        # get_acknowledged_prerequisites は fetchone()[0] を progress_data として読む。
        rows = []
        if acknowledged is not None:
            rows = [[{"acknowledged_prerequisites": acknowledged}]]
        monkeypatch.setattr(services, "_pg_session", lambda: self._Session(rows))
        return services.check_prerequisites(
            "user-1", "course-1", self._course_data(prereq), "期待値", "このトピックを始めたい"
        )

    def test_display_name_follows_the_current_topic_title(self, monkeypatch):
        response = self._check(monkeypatch, {"name": "波動関数", "topic_id": "t1"})
        assert response is not None
        assert response["first_prerequisite"] == "波動関数（改題）"
        assert response["unlearned"] == ["波動関数（改題）"]

    def test_unresolvable_topic_id_falls_back_to_the_name(self, monkeypatch):
        response = self._check(monkeypatch, {"name": "波動関数", "topic_id": "t-missing"})
        assert response["unlearned"] == ["波動関数"]

    def test_acknowledgement_key_stays_the_original_name(self, monkeypatch):
        """旧来の名前で記帳済みなら、表示名が変わっても再び問い返さない（互換）。"""
        response = self._check(
            monkeypatch,
            {"name": "波動関数", "topic_id": "t1"},
            acknowledged={"波動関数": "2026-09-01T00:00:00+00:00"},
        )
        assert response is None

    def test_plain_string_prerequisite_is_unchanged(self, monkeypatch):
        response = self._check(monkeypatch, "波動関数")
        assert response["unlearned"] == ["波動関数"]

    # --- P2-R12: 表示名は要素単位（名前 -> 表示名 の dict にしない）-------------

    @staticmethod
    def _course_data_multi(prereqs):
        return {
            "topics": [
                {"id": "t0", "title": "期待値", "prerequisites": list(prereqs)},
                {"id": "t1", "title": "波動関数（前編）", "prerequisites": []},
                {"id": "t2", "title": "波動関数（後編）", "prerequisites": []},
            ]
        }

    def test_same_name_pointing_at_two_topics_keeps_both_titles(self, monkeypatch):
        """同じ名前で別 topic を指す前提が2つあっても、片方の題名が消えない。"""
        from api import services

        monkeypatch.setattr(services, "_pg_session", lambda: self._Session([]))
        response = services.check_prerequisites(
            "user-1",
            "course-1",
            self._course_data_multi([
                {"name": "波動関数", "topic_id": "t1"},
                {"name": "波動関数", "topic_id": "t2"},
            ]),
            "期待値",
            "このトピックを始めたい",
        )
        assert response is not None
        assert response["unlearned"] == ["波動関数（前編）", "波動関数（後編）"]
        assert response["first_prerequisite"] == "波動関数（前編）"

    def test_duplicate_display_names_are_not_listed_twice(self, monkeypatch):
        from api import services

        monkeypatch.setattr(services, "_pg_session", lambda: self._Session([]))
        response = services.check_prerequisites(
            "user-1",
            "course-1",
            self._course_data_multi([
                {"name": "波動関数", "topic_id": "t1"},
                {"name": "wavefunction", "topic_id": "t1"},
            ]),
            "期待値",
            "このトピックを始めたい",
        )
        assert response["unlearned"] == ["波動関数（前編）"]


class TestGuardrails:
    def test_module_is_pure(self):
        assert_source_does_not_import(
            _SRC,
            ["fastapi", "sqlalchemy", "core.postgres", "openai", "episteme_graph"],
            context="core/course_prerequisites.py",
        )
        assert "get_session" not in _SRC

    def test_analysis_takes_only_course_structure(self):
        """LU6: 学習者の痕跡・回答・習得状態を受け取る引数を持たない。"""
        params = list(inspect.signature(cp.analyze_prerequisite_order).parameters)
        assert params == ["topics"]
        resolve_params = list(inspect.signature(cp.resolve_prerequisite_topic_ids).parameters)
        assert resolve_params == ["topics"]

    def test_no_adaptive_assessment_vocabulary(self):
        assert_source_forbids(
            _SRC,
            ("mastery", "proficiency", "習熟", "interest_traces", "learning_states",
             "learner_", "chat_history"),
            context="core/course_prerequisites.py",
        )
