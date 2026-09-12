"""G層ルール ``course.delivered_unreviewed``（学ぶ単位の一級化 P2-5）の検証。

正本: ``docs/features/learning_units_design.md`` §7（オーナー判断 O-5・LU5 / LU8）。

fake session（実 DB なし）で評価器の分岐だけを固定する:
  1. 承認が1件でもあれば出ない（G1: 実施すれば自動消滅）
  2. 束ねが0件のコースには出ない（承認対象が無いのは別の事実）
  3. 承認0 + 束ねあり + 公開済みで1件出る
  4. 非公開コースは対象外（配信されていない）
  5. 事実文に件数・督促語彙が無い（LU5 / G6）・道案内は既存 capability の再利用（G3）
  6. 読むのは live ビュー（KO5）で、承認状態の書き込みはしない
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as ns  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")

_UID = "u-teacher"


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    """SQL の中身で応答を出し分ける最小のフェイク（実 DB には接続しない）。"""

    def __init__(self, *, courses, approved_components=(), approved_claims=()):
        self.courses = courses
        self.approved_components = list(approved_components)
        self.approved_claims = list(approved_claims)
        self.queries: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        if "learning_courses" in sql:
            return _FakeResult(self.courses)
        if "theory_components_live" in sql:
            refs = set(params.get("refs") or [])
            return _FakeResult([{"ref": r} for r in self.approved_components if r in refs])
        if "theory_claims_live" in sql:
            refs = set(params.get("refs") or [])
            return _FakeResult([{"ref": r} for r in self.approved_claims if r in refs])
        raise AssertionError("想定外のクエリ: " + sql)


def _course(cid="c1", title="量子力学入門", *, components=(), claims=(), units=(), sources=("mat_1",)):
    topic = {"id": "t0", "title": "波動関数"}
    if components:
        topic["linked_component_ids"] = list(components)
    if claims:
        topic["linked_claim_ids"] = list(claims)
    if units:
        topic["units"] = [{"kind": "section_block", "unit_id": u} for u in units]
    return {
        "id": cid,
        "title": title,
        "data": {
            "topics": [topic],
            "sources": [{"material_id": m} for m in sources],
        },
        "created_at": "2026-09-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# 1. カタログ登録（G3）
# ---------------------------------------------------------------------------


class TestRuleRegistration:
    def test_rule_is_recommended_guidance_reusing_graph_review(self):
        rule = ns.RULE_CATALOG[ns.RULE_COURSE_DELIVERED_UNREVIEWED]
        assert rule["severity"] == ns.SEVERITY_RECOMMENDED
        assert rule["capability_id"] == "materials.graph_review"
        cap = caps.get_capability("materials.graph_review")
        assert cap is not None and cap.kind == "guidance_only"

    def test_evaluator_is_registered(self):
        assert ns._RULE_EVALUATORS[ns.RULE_COURSE_DELIVERED_UNREVIEWED] is ns._eval_course_delivered_unreviewed


# ---------------------------------------------------------------------------
# 2. 分岐
# ---------------------------------------------------------------------------


class TestEvaluator:
    def test_emits_when_nothing_is_approved(self):
        session = _FakeSession(courses=[_course(components=["comp-1"], claims=["claim-1"])])
        out = ns._eval_course_delivered_unreviewed(session, _UID)
        assert len(out) == 1
        step, sort_ts = out[0]
        assert step.rule_id == ns.RULE_COURSE_DELIVERED_UNREVIEWED
        assert step.step_key == "course.delivered_unreviewed:c1"
        assert step.target == {"course_id": "c1", "material_id": "mat_1"}
        assert sort_ts == "2026-09-01T00:00:00+00:00"
        assert step.reason == "コース『量子力学入門』は、解析結果の確認（承認）を経ずに配信されています。"

    def test_silent_when_a_component_is_approved(self):
        session = _FakeSession(
            courses=[_course(components=["comp-1", "comp-2"])],
            approved_components=["comp-2"],
        )
        assert ns._eval_course_delivered_unreviewed(session, _UID) == []

    def test_silent_when_a_claim_is_approved(self):
        session = _FakeSession(
            courses=[_course(claims=["claim-1"])],
            approved_claims=["claim-1"],
        )
        assert ns._eval_course_delivered_unreviewed(session, _UID) == []

    def test_silent_when_nothing_is_bundled(self):
        session = _FakeSession(courses=[_course()])
        assert ns._eval_course_delivered_unreviewed(session, _UID) == []
        # 束ねが無ければ承認状態を引きにも行かない（無駄な SQL を出さない）。
        assert all("theory_" not in q for q in session.queries)

    def test_units_alone_do_not_light_the_rule(self):
        """`learning_units` 表を JOIN しない — unit_id だけでは承認状態を判定できない。"""
        session = _FakeSession(courses=[_course(units=["u-1"])])
        assert ns._eval_course_delivered_unreviewed(session, _UID) == []

    def test_only_published_courses_are_queried(self):
        session = _FakeSession(courses=[])
        ns._eval_course_delivered_unreviewed(session, _UID)
        assert "is_published = TRUE" in session.queries[0]
        assert "user_id = CAST(:uid AS uuid)" in session.queries[0]

    def test_course_without_sources_still_reports_the_fact(self):
        session = _FakeSession(courses=[_course(components=["comp-1"], sources=())])
        step, _ = ns._eval_course_delivered_unreviewed(session, _UID)[0]
        assert step.target == {"course_id": "c1"}

    def test_multiple_courses_are_evaluated_independently(self):
        session = _FakeSession(
            courses=[
                _course(cid="c1", title="A", components=["comp-1"]),
                _course(cid="c2", title="B", components=["comp-2"]),
            ],
            approved_components=["comp-1"],
        )
        out = ns._eval_course_delivered_unreviewed(session, _UID)
        assert [s.target["course_id"] for s, _ in out] == ["c2"]


# ---------------------------------------------------------------------------
# 3. 参照の突合（DB UUID / agent ID の両方）
# ---------------------------------------------------------------------------


class TestRefResolution:
    def test_query_matches_both_uuid_and_legacy_ids(self):
        src = extract_function_source(_SRC, "_approved_refs")
        assert "t.id::text = ANY(:refs)" in src
        assert "source_scope->'legacy_ids'" in src
        assert "review_status = 'teacher_approved'" in src

    def test_reads_live_views_only(self):
        src = extract_function_source(_SRC, "_eval_course_delivered_unreviewed")
        assert "theory_components_live" in src
        assert "theory_claims_live" in src
        assert "FROM theory_components " not in src
        assert "FROM theory_claims " not in src

    def test_empty_refs_issue_no_sql(self):
        session = _FakeSession(courses=[])
        assert ns._approved_refs(session, "theory_components_live", []) == set()
        assert session.queries == []


# ---------------------------------------------------------------------------
# 4. 文言（LU5 / LU8 / G6）
# ---------------------------------------------------------------------------


class TestWording:
    _FORBIDDEN = ("！", "今すぐ", "急いで", "必ず", "早く", "至急", "件", "%")

    def test_reason_has_no_counts_or_pushy_words(self):
        session = _FakeSession(courses=[_course(components=["comp-1"])])
        step, _ = ns._eval_course_delivered_unreviewed(session, _UID)[0]
        for word in self._FORBIDDEN:
            assert word not in step.reason, word
        assert not any(ch.isdigit() for ch in step.reason)

    def test_evaluator_does_not_write(self):
        src = extract_function_source(_SRC, "_eval_course_delivered_unreviewed")
        for token in ("INSERT", "UPDATE", "DELETE", "commit("):
            assert token not in src, f"{token} が評価器に現れる（G7: 読み取りのみ）"
