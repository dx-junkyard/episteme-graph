"""``core/teaching_figures/store.py`` のテスト（DB 接続なし）。

store の全関数は第1引数に session を取り commit / close をしない（採用時の
topic 側登録と同一トランザクションにするため。設計書 §7.1b）。そのため、
``execute`` を記録する fake session を渡すだけで挙動を検証できる
（``tests/test_llm_policy_store.py`` / ``tests/test_llm_usage_recorder.py`` と
同型の fake-session 流儀。実 PostgreSQL 接続なし）。

観点:
  1. store は commit / rollback / close を呼ばない（トランザクションは呼び出し側）
  2. SVG 差し替え時の ``revisions`` append と上限 20 の間引き（+ 間引いた事実の保持）
  3. 提案の再生成 = 既存 candidate の ``superseded`` 遷移 → 挿入（rows 空なら DB 非接触）
  4. ``confidence`` の生値を返さず ``confidence_label`` に変換する（FG8）
  5. 語彙 fail-closed（figure_kind / status / signal_basis）と不正 id の扱い
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from core.teaching_figures import schema  # noqa: E402
from core.teaching_figures import store  # noqa: E402

_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
_FIGURE_ID = "11111111-2222-3333-4444-555555555555"
_SUGGESTION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_USER_ID = "99999999-8888-7777-6666-555555555555"


# ---------------------------------------------------------------------------
# fake session
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """execute を記録する duck-typed fake（結果は登録順に払い出す）。"""

    def __init__(self, results: list[list[tuple]] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._results = list(results or [])

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        rows = self._results.pop(0) if self._results else []
        return _FakeResult(rows)

    def commit(self):  # pragma: no cover - 呼ばれたら テストが失敗する
        self.commits += 1

    def rollback(self):  # pragma: no cover
        self.rollbacks += 1

    def close(self):  # pragma: no cover
        self.closed = True

    # --- 検査用ヘルパ -----------------------------------------------------
    def sql(self, index: int) -> str:
        return re.sub(r"\s+", " ", self.calls[index][0]).strip()

    def params(self, index: int) -> dict:
        return self.calls[index][1]

    @property
    def statements(self) -> list[str]:
        return [re.sub(r"\s+", " ", sql).strip() for sql, _ in self.calls]


def _figure_row(
    *,
    figure_id: str = _FIGURE_ID,
    course_id: str = "course-1",
    topic_id: str | None = "topic-1",
    title: str = "熱平衡の3段階",
    caption: str = "熱平衡に達する過程の模式図",
    figure_kind: str = schema.FIGURE_KIND_PROCESS_FLOW,
    svg_source: str = "<svg/>",
    minio_key: str = "teaching/course-1/fig.svg",
    status: str = schema.FIGURE_STATUS_DRAFT,
    revisions: list | None = None,
) -> tuple:
    """``_FIGURE_COLUMNS_SQL`` の並びの行タプル。"""
    return (
        figure_id,
        course_id,
        topic_id,
        _USER_ID,
        title,
        caption,
        figure_kind,
        svg_source,
        minio_key,
        schema.SVG_CONTENT_TYPE,
        status,
        None,
        revisions if revisions is not None else [],
        _NOW,
        _NOW,
    )


def _suggestion_row(
    *,
    suggestion_id: str = _SUGGESTION_ID,
    confidence: float | None = 0.82,
    status: str = schema.SUGGESTION_STATUS_CANDIDATE,
    figure_kind: str = schema.FIGURE_KIND_CONCEPT_MAP,
) -> tuple:
    """``_SUGGESTION_COLUMNS_SQL`` の並びの行タプル。"""
    return (
        suggestion_id,
        "course-1",
        "topic-1",
        "境界条件の適用によって",
        "3つの量の関係が文章だけでは追いづらい",
        schema.SIGNAL_BASIS_BOTH,
        figure_kind,
        "3量の依存関係を矢印で示す",
        confidence,
        status,
        _USER_ID,
        _NOW,
    )


# ===========================================================================
# 1. トランザクション規約
# ===========================================================================


class TestSessionContract:
    def test_create_does_not_commit_or_close(self):
        session = _FakeSession([[_figure_row()]])
        store.create_teaching_figure(
            session,
            course_id="course-1",
            topic_id="topic-1",
            created_by=_USER_ID,
            title="熱平衡の3段階",
            caption="熱平衡に達する過程の模式図",
            figure_kind=schema.FIGURE_KIND_PROCESS_FLOW,
            svg_source="<svg/>",
            minio_key="teaching/course-1/fig.svg",
            content_type=schema.SVG_CONTENT_TYPE,
            status=schema.FIGURE_STATUS_DRAFT,
        )
        assert session.commits == 0
        assert session.rollbacks == 0
        assert session.closed is False

    def test_update_does_not_commit(self):
        session = _FakeSession([[_figure_row()], [_figure_row(title="改題")]])
        store.update_teaching_figure(session, _FIGURE_ID, title="改題")
        assert session.commits == 0
        assert session.closed is False

    def test_module_does_not_open_its_own_session(self):
        source = (Path(_backend_dir) / "core" / "teaching_figures" / "store.py").read_text(
            encoding="utf-8"
        )
        assert "get_session" not in source
        assert "session.commit()" not in source


# ===========================================================================
# 2. 生成図の作成・取得・一覧
# ===========================================================================


class TestCreateTeachingFigure:
    def _create(self, session, **overrides):
        kwargs = {
            "course_id": "course-1",
            "topic_id": "topic-1",
            "created_by": _USER_ID,
            "title": "t",
            "caption": "c",
            "figure_kind": schema.FIGURE_KIND_CONCEPT_MAP,
            "svg_source": "<svg/>",
            "minio_key": "teaching/course-1/fig.svg",
            "content_type": schema.SVG_CONTENT_TYPE,
            "status": schema.FIGURE_STATUS_ADOPTED,
        }
        kwargs.update(overrides)
        return store.create_teaching_figure(session, **kwargs)

    def test_returns_row_dict(self):
        session = _FakeSession([[_figure_row(status=schema.FIGURE_STATUS_ADOPTED)]])
        row = self._create(session)
        assert row["id"] == _FIGURE_ID
        assert row["status"] == schema.FIGURE_STATUS_ADOPTED
        assert row["content_type"] == schema.SVG_CONTENT_TYPE
        assert row["revisions"] == []
        assert row["revisions_truncated"] is False

    def test_invalid_figure_kind_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="figure_kind"):
            self._create(session, figure_kind="mermaid")
        assert session.calls == []

    def test_invalid_status_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="status"):
            self._create(session, status="published")
        assert session.calls == []

    def test_new_row_cannot_start_retired(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="retired"):
            self._create(session, status=schema.FIGURE_STATUS_RETIRED)
        assert session.calls == []

    def test_required_values(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="course_id"):
            self._create(session, course_id="")
        with pytest.raises(ValueError, match="svg_source"):
            self._create(session, svg_source="  ")
        with pytest.raises(ValueError, match="minio_key"):
            self._create(session, minio_key="")

    def test_non_uuid_created_by_is_nulled_not_cast(self):
        session = _FakeSession([[_figure_row()]])
        self._create(session, created_by="not-a-uuid")
        assert session.params(0)["created_by"] is None

    def test_explicit_figure_id_is_inserted(self):
        session = _FakeSession([[_figure_row()]])
        self._create(session, figure_id=_FIGURE_ID)
        assert "INSERT INTO course_teaching_figures (id," in session.sql(0)
        assert session.params(0)["figure_id"] == _FIGURE_ID

    def test_default_id_column_is_not_listed(self):
        session = _FakeSession([[_figure_row()]])
        self._create(session)
        assert "(id," not in session.sql(0)
        assert session.params(0)["figure_id"] is None

    def test_invalid_explicit_figure_id_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="figure_id"):
            self._create(session, figure_id="nope")
        assert session.calls == []


class TestGetAndList:
    def test_get_returns_none_for_non_uuid_without_touching_db(self):
        session = _FakeSession()
        assert store.get_teaching_figure(session, "../../etc/passwd") is None
        assert session.calls == []

    def test_get_returns_none_when_missing(self):
        session = _FakeSession([[]])
        assert store.get_teaching_figure(session, _FIGURE_ID) is None

    def test_list_all_statuses_by_default(self):
        session = _FakeSession([[_figure_row(), _figure_row(figure_id=_SUGGESTION_ID)]])
        rows = store.list_teaching_figures(session, "course-1")
        assert len(rows) == 2
        assert "status = ANY" not in session.sql(0)

    def test_list_filters_by_status(self):
        session = _FakeSession([[_figure_row()]])
        store.list_teaching_figures(
            session, "course-1", statuses=[schema.FIGURE_STATUS_ADOPTED]
        )
        assert "status = ANY(:statuses)" in session.sql(0)
        assert session.params(0)["statuses"] == [schema.FIGURE_STATUS_ADOPTED]

    def test_list_rejects_unknown_status(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="status"):
            store.list_teaching_figures(session, "course-1", statuses=["published"])
        assert session.calls == []

    def test_list_with_empty_status_filter_returns_nothing(self):
        session = _FakeSession()
        assert store.list_teaching_figures(session, "course-1", statuses=[]) == []
        assert session.calls == []


class TestAdoptedFiguresMap:
    def test_maps_id_to_delivery_fields_only(self):
        session = _FakeSession(
            [[(_FIGURE_ID, "熱平衡の模式図", schema.SVG_CONTENT_TYPE, "熱平衡の3段階")]]
        )
        mapping = store.adopted_figures_map(session, "course-1")
        assert mapping == {
            _FIGURE_ID: {
                "caption": "熱平衡の模式図",
                "content_type": schema.SVG_CONTENT_TYPE,
                "title": "熱平衡の3段階",
            }
        }
        # SVG 本体は配信マップに載せない（画像は専用エンドポイント経由）。
        assert "svg_source" not in session.sql(0)

    def test_only_adopted_rows_are_queried(self):
        session = _FakeSession([[]])
        store.adopted_figures_map(session, "course-1")
        assert session.params(0)["status"] == schema.FIGURE_STATUS_ADOPTED


# ===========================================================================
# 3. revisions append（FG7）
# ===========================================================================


class TestUpdateAndRevisions:
    def test_svg_replacement_appends_previous_version(self):
        session = _FakeSession(
            [
                [_figure_row(svg_source="<svg>old</svg>", caption="旧キャプション")],
                [_figure_row(svg_source="<svg>new</svg>")],
            ]
        )
        store.update_teaching_figure(
            session, _FIGURE_ID, svg_source="<svg>new</svg>", updated_by=_USER_ID
        )
        revisions = json.loads(session.params(1)["revisions"])
        assert len(revisions) == 1
        assert revisions[0]["svg_source"] == "<svg>old</svg>"
        assert revisions[0]["caption"] == "旧キャプション"
        assert revisions[0]["updated_by"] == _USER_ID
        assert revisions[0]["updated_at"]

    def test_metadata_only_update_does_not_touch_revisions(self):
        session = _FakeSession([[_figure_row()], [_figure_row(title="改題")]])
        store.update_teaching_figure(session, _FIGURE_ID, title="改題")
        assert "revisions" not in session.params(1)
        assert "revisions =" not in session.sql(1)

    def test_revisions_are_capped_and_truncation_is_recorded(self):
        existing = [
            {"svg_source": f"<svg>{i}</svg>", "caption": "", "updated_at": "", "updated_by": None}
            for i in range(store.MAX_REVISIONS)
        ]
        session = _FakeSession(
            [
                [_figure_row(svg_source="<svg>latest</svg>", revisions=existing)],
                [_figure_row()],
            ]
        )
        store.update_teaching_figure(session, _FIGURE_ID, svg_source="<svg>newest</svg>")
        revisions = json.loads(session.params(1)["revisions"])

        assert len(revisions) == store.MAX_REVISIONS
        # 最新版（更新前の SVG）は必ず残る。
        assert revisions[-1]["svg_source"] == "<svg>latest</svg>"
        # 最古（0番）は間引かれ、間引いた事実が残る（黙って消さない）。
        assert revisions[0]["svg_source"] == "<svg>1</svg>"
        assert revisions[0]["revisions_truncated"] is True

    def test_truncation_flag_surfaces_on_row_dict(self):
        truncated = [
            {"svg_source": "<svg>0</svg>", "revisions_truncated": True},
            {"svg_source": "<svg>1</svg>"},
        ]
        session = _FakeSession([[_figure_row(revisions=truncated)]])
        row = store.get_teaching_figure(session, _FIGURE_ID)
        assert row is not None
        assert row["revisions_truncated"] is True

    def test_jsonb_revisions_delivered_as_text_are_parsed(self):
        session = _FakeSession([[_figure_row(revisions=json.dumps([{"svg_source": "<svg/>"}]))]])
        row = store.get_teaching_figure(session, _FIGURE_ID)
        assert row is not None
        assert row["revisions"] == [{"svg_source": "<svg/>"}]

    def test_status_transition_is_allowed(self):
        session = _FakeSession(
            [[_figure_row()], [_figure_row(status=schema.FIGURE_STATUS_ADOPTED)]]
        )
        row = store.update_teaching_figure(
            session, _FIGURE_ID, status=schema.FIGURE_STATUS_ADOPTED
        )
        assert row["status"] == schema.FIGURE_STATUS_ADOPTED
        assert session.params(1)["status"] == schema.FIGURE_STATUS_ADOPTED

    def test_unknown_status_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="status"):
            store.update_teaching_figure(session, _FIGURE_ID, status="published")
        assert session.calls == []

    def test_no_fields_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="no fields"):
            store.update_teaching_figure(session, _FIGURE_ID)
        assert session.calls == []

    def test_missing_row_raises_not_found(self):
        session = _FakeSession([[]])
        with pytest.raises(store.TeachingFigureNotFoundError):
            store.update_teaching_figure(session, _FIGURE_ID, title="x")

    def test_non_uuid_id_raises_not_found_without_touching_db(self):
        session = _FakeSession()
        with pytest.raises(store.TeachingFigureNotFoundError):
            store.update_teaching_figure(session, "nope", title="x")
        assert session.calls == []


# ===========================================================================
# 4. 孤児掃除（唯一の DELETE 経路）
# ===========================================================================


class TestDeleteFiguresForCourse:
    def test_returns_minio_keys_and_also_clears_suggestions(self):
        session = _FakeSession([[("teaching/course-1/a.svg",), ("teaching/course-1/b.svg",)], []])
        keys = store.delete_figures_for_course(session, "course-1")
        assert keys == ["teaching/course-1/a.svg", "teaching/course-1/b.svg"]
        assert "DELETE FROM course_teaching_figures" in session.sql(0)
        assert "DELETE FROM teaching_figure_suggestions" in session.sql(1)

    def test_empty_course_id_is_a_noop(self):
        session = _FakeSession()
        assert store.delete_figures_for_course(session, "") == []
        assert session.calls == []

    def test_no_other_delete_statements_exist_in_the_module(self):
        source = (Path(_backend_dir) / "core" / "teaching_figures" / "store.py").read_text(
            encoding="utf-8"
        )
        # FG7: 行削除はコース物理削除の孤児掃除だけ（figures + suggestions の2文）。
        assert source.count("DELETE FROM") == 2


# ===========================================================================
# 5. 提案（candidate 層）
# ===========================================================================


class TestCreateSuggestions:
    def _row(self, **overrides) -> dict:
        row = {
            "course_id": "course-1",
            "topic_id": "topic-1",
            "anchor_excerpt": "境界条件の適用によって",
            "gap_reason": "3つの量の関係が文章だけでは追いづらい",
            "signal_basis": schema.SIGNAL_BASIS_BOTH,
            "figure_kind": schema.FIGURE_KIND_CONCEPT_MAP,
            "figure_brief": "3量の依存関係を矢印で示す",
            "confidence": 0.8,
            "created_by": _USER_ID,
        }
        row.update(overrides)
        return row

    def test_supersedes_existing_candidates_before_inserting(self):
        session = _FakeSession()
        inserted = store.create_suggestions(session, [self._row(), self._row()])
        assert inserted == 2

        assert "UPDATE teaching_figure_suggestions" in session.sql(0)
        assert session.params(0)["superseded"] == schema.SUGGESTION_STATUS_SUPERSEDED
        # 遷移元は candidate のみ（accepted / dismissed は教員の判断済みなので不変）。
        assert session.params(0)["candidate"] == schema.SUGGESTION_STATUS_CANDIDATE
        assert "status = :candidate" in session.sql(0)

        assert "INSERT INTO teaching_figure_suggestions" in session.sql(1)
        assert session.params(1)["status"] == schema.SUGGESTION_STATUS_CANDIDATE
        assert session.params(2)["status"] == schema.SUGGESTION_STATUS_CANDIDATE

    def test_empty_rows_do_not_touch_db(self):
        session = _FakeSession()
        assert store.create_suggestions(session, []) == 0
        assert session.calls == []

    def test_insert_never_takes_status_from_caller(self):
        session = _FakeSession()
        store.create_suggestions(session, [self._row(status=schema.SUGGESTION_STATUS_ACCEPTED)])
        assert session.params(1)["status"] == schema.SUGGESTION_STATUS_CANDIDATE

    def test_out_of_range_confidence_becomes_null(self):
        session = _FakeSession()
        store.create_suggestions(session, [self._row(confidence=1.7)])
        assert session.params(1)["confidence"] is None

    def test_invalid_figure_kind_is_rejected_before_any_write(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="figure_kind"):
            store.create_suggestions(session, [self._row(), self._row(figure_kind="mermaid")])
        assert session.calls == []

    def test_invalid_signal_basis_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="signal_basis"):
            store.create_suggestions(session, [self._row(signal_basis="vibes")])
        assert session.calls == []

    def test_missing_topic_id_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="topic_id"):
            store.create_suggestions(session, [self._row(topic_id="")])
        assert session.calls == []


class TestSuggestionReads:
    def test_list_returns_confidence_label_not_raw_value(self):
        session = _FakeSession([[_suggestion_row(confidence=0.82)]])
        rows = store.list_suggestions(session, "course-1", "topic-1")
        assert rows[0]["confidence_label"] == schema.CONFIDENCE_LABEL_HIGH
        assert "confidence" not in rows[0]

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.9, schema.CONFIDENCE_LABEL_HIGH),
            (0.6, schema.CONFIDENCE_LABEL_MEDIUM),
            (0.2, schema.CONFIDENCE_LABEL_LOW),
            (None, schema.CONFIDENCE_LABEL_LOW),
        ],
    )
    def test_confidence_label_thresholds(self, value, expected):
        session = _FakeSession([[_suggestion_row(confidence=value)]])
        rows = store.list_suggestions(session, "course-1", "topic-1")
        assert rows[0]["confidence_label"] == expected

    def test_list_includes_kind_label_for_display(self):
        session = _FakeSession([[_suggestion_row(figure_kind=schema.FIGURE_KIND_TIMELINE)]])
        rows = store.list_suggestions(session, "course-1", "topic-1")
        assert rows[0]["figure_kind"] == schema.FIGURE_KIND_TIMELINE
        assert rows[0]["figure_kind_label"] == schema.FIGURE_KIND_LABELS[
            schema.FIGURE_KIND_TIMELINE
        ]

    def test_list_defaults_to_candidate_and_accepted(self):
        session = _FakeSession([[]])
        store.list_suggestions(session, "course-1", "topic-1")
        assert session.params(0)["statuses"] == list(schema.SUGGESTION_VISIBLE_STATUSES)

    def test_list_rejects_unknown_status(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="status"):
            store.list_suggestions(session, "course-1", "topic-1", statuses=["maybe"])
        assert session.calls == []

    def test_get_suggestion_hides_raw_confidence(self):
        session = _FakeSession([[_suggestion_row()]])
        row = store.get_suggestion(session, _SUGGESTION_ID)
        assert row is not None
        assert "confidence" not in row
        assert row["confidence_label"] in schema.CONFIDENCE_LABELS

    def test_get_suggestion_non_uuid_is_none(self):
        session = _FakeSession()
        assert store.get_suggestion(session, "nope") is None
        assert session.calls == []


class TestSetSuggestionStatus:
    @pytest.mark.parametrize(
        "status",
        [
            schema.SUGGESTION_STATUS_ACCEPTED,
            schema.SUGGESTION_STATUS_DISMISSED,
            schema.SUGGESTION_STATUS_SUPERSEDED,
        ],
    )
    def test_decidable_transitions_only_from_candidate(self, status):
        session = _FakeSession([[_suggestion_row(status=status)]])
        row = store.set_suggestion_status(session, _SUGGESTION_ID, status)
        assert row is not None
        assert row["status"] == status
        assert session.params(0)["current_status"] == schema.SUGGESTION_STATUS_CANDIDATE
        assert "status = :current_status" in session.sql(0)

    def test_cannot_move_back_to_candidate(self):
        session = _FakeSession()
        with pytest.raises(ValueError, match="candidate"):
            store.set_suggestion_status(
                session, _SUGGESTION_ID, schema.SUGGESTION_STATUS_CANDIDATE
            )
        assert session.calls == []

    def test_unknown_status_is_rejected(self):
        session = _FakeSession()
        with pytest.raises(ValueError):
            store.set_suggestion_status(session, _SUGGESTION_ID, "approved")
        assert session.calls == []

    def test_already_decided_row_returns_none(self):
        session = _FakeSession([[]])
        assert (
            store.set_suggestion_status(
                session, _SUGGESTION_ID, schema.SUGGESTION_STATUS_DISMISSED
            )
            is None
        )

    def test_status_transition_is_an_update_not_a_delete(self):
        session = _FakeSession([[_suggestion_row(status=schema.SUGGESTION_STATUS_DISMISSED)]])
        store.set_suggestion_status(
            session, _SUGGESTION_ID, schema.SUGGESTION_STATUS_DISMISSED
        )
        assert session.sql(0).startswith("UPDATE teaching_figure_suggestions")
        assert "DELETE" not in session.sql(0)
