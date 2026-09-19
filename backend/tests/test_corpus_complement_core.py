"""コーパスを補う論文 — レンズA / レンズB の core（``core/paper_discovery/complement.py``
+ ``ranking.rank_candidates(complement_context=...)``）。

正本: ``docs/features/corpus_complement_design.md`` §5.1 / §5.2（不変条項 CC1〜CC8）。
構造の検査（import 禁止・禁止キー・denylist 語彙）は
``test_corpus_complement_guardrails.py`` 側で、ここは**振る舞い**を固定する。

検証観点（設計書 §7 の core 項目）:

1. ``thin_node_ids`` — 閾値の境界（``<=``）・未配置を含む・region を除外・fail-soft
2. ``sky_statements`` — 人間が確定した前提 / 承認済み主張だけを使う（AI 候補を根拠に
   しない = CC1）・空 document で SQL を撃たない・本文の切り詰め
3. ``fills_for_vector`` / ``skies_for_vector`` — 閾値・上限・未測定は不一致扱い
4. ``build_complement_context`` — レンズごとの独立縮退と事実文
5. ``order_complement_first`` — 補完ありが先頭・元の順序を保つ安定ソート
6. ``rank_candidates`` — ``complement_context=None`` で**完全不変**／渡したときも
   ``generate_embeddings`` は**1回**（同一バッチ相乗り）・fail-soft でレンズも縮退
7. CC4 — 返り値に cosine・件数の生値が出ない
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.atlas_vectors.store import AnchorVector  # noqa: E402
from core.label_vocab import (  # noqa: E402
    ANCHOR_LANDING_THRESHOLD_NEAR,
    COMPLEMENT_SKY_THRESHOLD,
)
from core.paper_discovery import complement, ranking  # noqa: E402
from core.reconstruction.schema import APPROVED_REVIEW_STATUSES  # noqa: E402


# ---------------------------------------------------------------------------
# フェイクセッション（DB へ行かない）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """発見層 core が読む表の最小フェイク。

    ``learning_courses`` / ``documents`` / ``chunks`` は
    ``test_paper_discovery_ranking.py`` の FakeSession と同型で、本層が足すのは
    ``landscape_placements`` / ``epistemic_ledger``（+ 本文表）の3つ。
    """

    def __init__(
        self,
        *,
        courses=(),
        documents=(),
        chunks=(),
        placements=(),
        ledger=(),
        assumptions=(),
        claims=(),
        fail_on: str = "",
    ):
        self.courses = list(courses)
        self.documents = list(documents)
        self.chunks = list(chunks)
        self.placements = list(placements)
        self.ledger = list(ledger)
        self.assumptions = {a["id"]: a for a in assumptions}
        self.claims = {c["id"]: c for c in claims}
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []

    # -- 下請け ------------------------------------------------------------

    def _ledger_rows(self, target_type, table, params):
        wanted_docs = set(params.get("document_ids") or [])
        statuses = set(params.get("statuses") or [])
        limit = int(params.get("limit") or 0)
        rows = []
        for entry in self.ledger:
            if entry.get("target_type") != target_type:
                continue
            if entry.get("verification_status") != "untested":
                continue
            if entry.get("verification_scopes") or []:
                continue
            if entry.get("document_id") not in wanted_docs:
                continue
            body = table.get(entry["target_id"])
            if not body:
                continue
            status_key = "status" if target_type == "assumption" else "review_status"
            text_key = "statement" if target_type == "assumption" else "text"
            if body.get(status_key) not in statuses:
                continue
            if not (body.get(text_key) or ""):
                continue
            rows.append((entry["target_id"], entry["document_id"], body[text_key]))
        rows.sort(key=lambda row: (row[1], row[0]))
        return rows[:limit]

    # -- SQLAlchemy 風インターフェース --------------------------------------

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))

        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("table unavailable (test)")

        if "FROM learning_courses" in sql:
            return _Result(
                [
                    (c["data"],)
                    for c in self.courses
                    if (c.get("data") or {}).get("cartridge_id") == p.get("domain_key")
                ]
            )
        if "FROM landscape_placements" in sql:
            wanted = set(p.get("node_ids") or [])
            counts: dict[str, set] = {}
            for row in self.placements:
                if row.get("domain_key") != p.get("domain_key"):
                    continue
                if row.get("node_id") not in wanted:
                    continue
                if row.get("status") in ("superseded", "rejected"):
                    continue
                counts.setdefault(row["node_id"], set()).add(row["document_id"])
            return _Result(sorted((k, len(v)) for k, v in counts.items()))
        if "JOIN assumption_nodes" in sql:
            return _Result(self._ledger_rows("assumption", self.assumptions, p))
        if "JOIN theory_claims" in sql:
            return _Result(self._ledger_rows("claim", self.claims, p))
        if "FROM documents" in sql and "document_ids" in p:
            wanted = set(p.get("document_ids") or [])
            return _Result(
                [(d["id"], d.get("title") or "") for d in self.documents if d["id"] in wanted]
            )
        if "FROM documents" in sql:
            wanted = set(p.get("material_ids") or [])
            return _Result(
                [
                    (d["id"], d.get("title") or "", d.get("source_url") or "")
                    for d in self.documents
                    if d.get("source_path") in wanted
                ]
            )
        if "FROM chunks" in sql:
            wanted = set(p.get("document_ids") or [])
            limit = int(p.get("per_document") or 0)
            return _Result(
                [
                    (c["document_id"], c["embedding"])
                    for c in self.chunks
                    if c["document_id"] in wanted and int(c.get("chunk_index", 0)) < limit
                ]
            )
        return _Result()

    def commit(self):  # pragma: no cover — core は commit しない
        raise AssertionError("core must not commit")

    def close(self):
        pass

    @property
    def sql_log(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


DOMAIN = "astrophysics"


def _course_session(**kwargs) -> FakeSession:
    """分野 → material → document の連鎖が引ける最小構成。"""
    kwargs.setdefault(
        "courses",
        [{"data": {"cartridge_id": DOMAIN, "sources": [{"material_id": "m1"}]}}],
    )
    kwargs.setdefault(
        "documents",
        [{"id": "doc-1", "source_path": "m1", "title": "重力波の論文"}],
    )
    return FakeSession(**kwargs)


def _anchor(node_id, *, kind="concept", label="", region="", vector=None) -> AnchorVector:
    return AnchorVector(
        node_id=node_id,
        node_kind=kind,
        label=label or node_id,
        region_label=region,
        vector=vector,
    )


# ---------------------------------------------------------------------------
# 1. レンズA — 薄いノードの判定
# ---------------------------------------------------------------------------


class TestThinNodeIds:
    def _anchors(self):
        return [
            _anchor("r1", kind="region", label="宇宙論"),
            _anchor("c1", label="CMB"),
            _anchor("c2", label="大規模構造"),
            _anchor("c3", label="重力波"),
        ]

    def _session(self):
        return _course_session(
            placements=[
                # c1 は2論文（厚い）、c2 は1論文（境界）、c3 は未配置（0）。
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d1", "status": "inferred"},
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d2", "status": "confirmed"},
                {"domain_key": DOMAIN, "node_id": "c2", "document_id": "d1", "status": "inferred"},
                # region も配置され得るが対象外。
                {"domain_key": DOMAIN, "node_id": "r1", "document_id": "d1", "status": "inferred"},
            ]
        )

    def test_threshold_is_inclusive_and_counts_unplaced(self):
        thin = complement.thin_node_ids(
            self._session(), DOMAIN, self._anchors(), max_documents=1
        )
        # c2（配置1 = 境界）と c3（未配置）が薄い。c1（2件）は薄くない。
        assert thin == {"c2", "c3"}

    def test_zero_threshold_keeps_only_unplaced(self):
        thin = complement.thin_node_ids(
            self._session(), DOMAIN, self._anchors(), max_documents=0
        )
        assert thin == {"c3"}

    def test_regions_are_never_thin(self):
        thin = complement.thin_node_ids(
            self._session(), DOMAIN, self._anchors(), max_documents=5
        )
        assert "r1" not in thin
        assert thin == {"c1", "c2", "c3"}

    def test_dead_placements_do_not_count_as_coverage(self):
        session = _course_session(
            placements=[
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d1", "status": "superseded"},
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d2", "status": "rejected"},
            ]
        )
        thin = complement.thin_node_ids(
            session, DOMAIN, [_anchor("c1")], max_documents=0
        )
        assert thin == {"c1"}

    def test_other_domains_are_ignored(self):
        session = _course_session(
            placements=[
                {"domain_key": "other", "node_id": "c1", "document_id": "d1", "status": "inferred"},
            ]
        )
        assert complement.thin_node_ids(
            session, DOMAIN, [_anchor("c1")], max_documents=0
        ) == {"c1"}

    def test_no_anchors_does_not_touch_the_database(self):
        session = _course_session()
        assert complement.thin_node_ids(session, DOMAIN, [], max_documents=1) == set()
        assert session.calls == []

    def test_no_domain_does_not_touch_the_database(self):
        session = _course_session()
        assert complement.thin_node_ids(session, "  ", [_anchor("c1")], max_documents=1) == set()
        assert session.calls == []

    def test_database_failure_degrades_to_empty(self):
        session = _course_session(fail_on="landscape_placements")
        assert complement.thin_node_ids(
            session, DOMAIN, [_anchor("c1")], max_documents=1
        ) == set()


# ---------------------------------------------------------------------------
# 2. レンズB — 台帳から前提文へ
# ---------------------------------------------------------------------------


def _ledger_session(*, assumptions=(), claims=(), ledger=(), **kwargs) -> FakeSession:
    return _course_session(
        assumptions=assumptions, claims=claims, ledger=ledger, **kwargs
    )


class TestSkyStatements:
    def test_uses_confirmed_assumptions_and_approved_claims_only(self):
        session = _ledger_session(
            assumptions=[
                {"id": "a-ok", "statement": "測定は等方である", "status": "confirmed"},
                {"id": "a-cand", "statement": "AI が出した候補", "status": "candidate"},
                {"id": "a-dismissed", "statement": "却下された前提", "status": "dismissed"},
            ],
            claims=[
                {"id": "c-ok", "text": "赤方偏移は距離に比例する",
                 "review_status": APPROVED_REVIEW_STATUSES[0]},
                {"id": "c-pending", "text": "未承認の主張",
                 "review_status": "teacher_review_required"},
            ],
            ledger=[
                {"target_id": "a-ok", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
                {"target_id": "a-cand", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
                {"target_id": "a-dismissed", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
                {"target_id": "c-ok", "target_type": "claim", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
                {"target_id": "c-pending", "target_type": "claim", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
            ],
        )
        rows = complement.sky_statements(session, DOMAIN)
        assert [r["target_id"] for r in rows] == ["a-ok", "c-ok"]
        assert [r["target_type"] for r in rows] == ["assumption", "claim"]
        assert rows[0]["statement"] == "測定は等方である"
        assert rows[0]["document_title"] == "重力波の論文"

    def test_scoped_or_verified_rows_are_excluded(self):
        session = _ledger_session(
            assumptions=[
                {"id": "a-scoped", "statement": "スコープあり", "status": "confirmed"},
                {"id": "a-verified", "statement": "検証済み", "status": "confirmed"},
            ],
            ledger=[
                {"target_id": "a-scoped", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": [{"scope_id": "s1"}]},
                {"target_id": "a-verified", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "directly_verified", "verification_scopes": []},
            ],
        )
        assert complement.sky_statements(session, DOMAIN) == []

    def test_documents_outside_the_domain_are_excluded(self):
        session = _ledger_session(
            assumptions=[{"id": "a1", "statement": "他分野の前提", "status": "confirmed"}],
            ledger=[
                {"target_id": "a1", "target_type": "assumption", "document_id": "doc-other",
                 "verification_status": "untested", "verification_scopes": []},
            ],
        )
        assert complement.sky_statements(session, DOMAIN) == []

    def test_no_documents_does_not_query_the_ledger(self):
        session = FakeSession()  # コースが無い = 分野に document が無い
        assert complement.sky_statements(session, DOMAIN) == []
        assert "epistemic_ledger" not in session.sql_log

    def test_statement_is_truncated_and_whitespace_normalised(self):
        long_text = "あ" * (complement.MAX_SKY_STATEMENT_CHARS + 50)
        session = _ledger_session(
            assumptions=[{"id": "a1", "statement": "  前提\n  です  ", "status": "operationalized"},
                         {"id": "a2", "statement": long_text, "status": "confirmed"}],
            ledger=[
                {"target_id": "a1", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
                {"target_id": "a2", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
            ],
        )
        rows = complement.sky_statements(session, DOMAIN)
        bodies = {r["target_id"]: r["statement"] for r in rows}
        assert bodies["a1"] == "前提 です"
        assert len(bodies["a2"]) == complement.MAX_SKY_STATEMENT_CHARS

    def test_limit_is_respected(self):
        assumptions = [
            {"id": f"a{i}", "statement": f"前提{i}", "status": "confirmed"} for i in range(5)
        ]
        ledger = [
            {"target_id": f"a{i}", "target_type": "assumption", "document_id": "doc-1",
             "verification_status": "untested", "verification_scopes": []}
            for i in range(5)
        ]
        session = _ledger_session(assumptions=assumptions, ledger=ledger)
        assert len(complement.sky_statements(session, DOMAIN, limit=2)) == 2
        assert complement.sky_statements(session, DOMAIN, limit=0) == []

    def test_ledger_failure_degrades_to_empty(self):
        session = _ledger_session(fail_on="epistemic_ledger")
        assert complement.sky_statements(session, DOMAIN) == []


# ---------------------------------------------------------------------------
# 3. 純関数（fills / skies）
# ---------------------------------------------------------------------------


class TestFillsForVector:
    def _anchors(self):
        return [
            # [1, 0] との cosine: near=1.0 / mid=0.447 / far=0.316（閾値 0.36 の下）
            _anchor("c-near", label="重力波", region="高エネルギー", vector=[1.0, 0.0]),
            _anchor("c-mid", label="中間", region="宇宙論", vector=[1.0, 2.0]),
            _anchor("c-far", label="遠い", region="宇宙論", vector=[1.0, 3.0]),
            _anchor("c-blind", label="ベクトルなし", vector=None),
            _anchor("r1", kind="region", label="領域", vector=[1.0, 0.0]),
        ]

    def test_returns_labels_only_for_thin_concepts_in_the_top_band(self):
        fills = complement.fills_for_vector(
            [1.0, 0.0], self._anchors(), {"c-near", "c-mid", "c-far", "r1"}
        )
        assert fills == [
            {"node_label": "重力波", "region_label": "高エネルギー"},
            {"node_label": "中間", "region_label": "宇宙論"},
        ]

    def test_below_the_band_is_dropped(self):
        # c-far だけを薄いノードにしても、帯の外なので出ない。
        assert complement.fills_for_vector([1.0, 0.0], self._anchors(), {"c-far"}) == []
        # 帯の境界の確認（実装が正本のしきい値を使っていること）。
        assert 1.0 / (10 ** 0.5) < ANCHOR_LANDING_THRESHOLD_NEAR <= 1.0 / (5 ** 0.5)

    def test_regions_are_not_fills(self):
        assert complement.fills_for_vector([1.0, 0.0], self._anchors(), {"r1"}) == []

    def test_thick_nodes_are_dropped(self):
        assert complement.fills_for_vector([1.0, 0.0], self._anchors(), set()) == []

    def test_limit_and_missing_inputs(self):
        one = complement.fills_for_vector(
            [1.0, 0.0], self._anchors(), {"c-near", "c-mid"}, limit=1
        )
        assert [f["node_label"] for f in one] == ["重力波"]
        assert complement.fills_for_vector(None, self._anchors(), {"c-near"}) == []
        assert complement.fills_for_vector([1.0, 0.0], [], {"c-near"}) == []
        assert complement.fills_for_vector([1.0, 0.0], self._anchors(), {"c-near"}, limit=0) == []

    def test_no_numeric_values_leak(self):
        for fill in complement.fills_for_vector([1.0, 0.0], self._anchors(), {"c-near"}):
            assert set(fill) == {"node_label", "region_label"}
            assert not [v for v in fill.values() if isinstance(v, (int, float))]


class TestSkiesForVector:
    def _statements(self):
        return [
            {"statement": "遠い前提", "document_title": "論文A"},
            {"statement": "近い前提", "document_title": "論文B"},
            {"statement": "測れない前提", "document_title": "論文C"},
            {"statement": "とても近い前提", "document_title": "論文D"},
        ]

    def _vectors(self):
        # [1, 0] との cosine: 0.316（閾値 0.45 の下）/ 0.707 / 未測定 / 1.0
        return [[1.0, 3.0], [1.0, 1.0], None, [1.0, 0.0]]

    def test_orders_by_nearness_and_attaches_the_closed_world_note(self):
        skies = complement.skies_for_vector(
            [1.0, 0.0], self._vectors(), self._statements()
        )
        assert [s["statement"] for s in skies] == ["とても近い前提", "近い前提"]
        assert [s["document_title"] for s in skies] == ["論文D", "論文B"]
        assert all(s["closed_world_note"] == complement.CLOSED_WORLD_SKY_NOTE for s in skies)

    def test_unmeasured_counts_as_a_mismatch(self):
        skies = complement.skies_for_vector(
            [1.0, 0.0], [None], [{"statement": "測れない", "document_title": ""}]
        )
        assert skies == []

    def test_below_the_threshold_is_dropped(self):
        skies = complement.skies_for_vector(
            [1.0, 0.0], [[1.0, 3.0]], [{"statement": "遠い", "document_title": ""}]
        )
        assert skies == []
        assert 1.0 / (10 ** 0.5) < COMPLEMENT_SKY_THRESHOLD <= 1.0 / (2 ** 0.5)

    def test_limit_and_missing_inputs(self):
        one = complement.skies_for_vector(
            [1.0, 0.0], self._vectors(), self._statements(), limit=1
        )
        assert [s["statement"] for s in one] == ["とても近い前提"]
        assert complement.skies_for_vector(None, self._vectors(), self._statements()) == []
        assert complement.skies_for_vector([1.0, 0.0], [], self._statements()) == []
        assert complement.skies_for_vector([1.0, 0.0], self._vectors(), []) == []

    def test_shorter_vector_list_does_not_raise(self):
        skies = complement.skies_for_vector([1.0, 0.0], [[1.0, 0.0]], self._statements())
        assert [s["statement"] for s in skies] == ["遠い前提"]

    def test_no_numeric_values_leak(self):
        for sky in complement.skies_for_vector([1.0, 0.0], self._vectors(), self._statements()):
            assert set(sky) == {"statement", "document_title", "closed_world_note"}
            assert not [v for v in sky.values() if isinstance(v, (int, float))]


# ---------------------------------------------------------------------------
# 4. 材料の組み立て（レンズごとに独立して縮退する — CC8）
# ---------------------------------------------------------------------------


class TestBuildComplementContext:
    def _full_session(self):
        return _ledger_session(
            placements=[
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d1", "status": "inferred"},
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d2", "status": "inferred"},
            ],
            assumptions=[{"id": "a1", "statement": "前提", "status": "confirmed"}],
            ledger=[
                {"target_id": "a1", "target_type": "assumption", "document_id": "doc-1",
                 "verification_status": "untested", "verification_scopes": []},
            ],
        )

    def _context(self):
        return {
            "anchors": [_anchor("c1", vector=[1.0, 0.0]), _anchor("c2", vector=[0.0, 1.0])],
            "skeleton_version": "v3",
        }

    def test_both_lenses_available(self):
        built = complement.build_complement_context(
            self._full_session(), DOMAIN, self._context(), thin_max_documents=1
        )
        assert built["thin_node_ids"] == {"c2"}
        assert [s["statement"] for s in built["sky_statements"]] == ["前提"]
        assert built["facts"] == {"coverage": {"available": True}, "skies": {"available": True}}

    def test_no_anchor_context_reports_the_missing_skeleton(self):
        built = complement.build_complement_context(
            self._full_session(), DOMAIN, None, thin_max_documents=1
        )
        assert built["thin_node_ids"] == set()
        assert built["facts"]["coverage"] == {
            "available": False, "note": complement.NOTE_NO_SKELETON,
        }
        # レンズB は独立して成立する。
        assert built["facts"]["skies"]["available"] is True

    def test_empty_anchors_report_the_missing_index(self):
        built = complement.build_complement_context(
            self._full_session(), DOMAIN, {"anchors": [], "skeleton_version": "v3"},
            thin_max_documents=1,
        )
        assert built["facts"]["coverage"] == {
            "available": False, "note": complement.NOTE_NO_ANCHORS,
        }

    def test_no_thin_nodes_is_a_normal_state(self):
        built = complement.build_complement_context(
            self._full_session(), DOMAIN, self._context(), thin_max_documents=-1
        )
        assert built["thin_node_ids"] == set()
        assert built["facts"]["coverage"] == {
            "available": True, "note": complement.NOTE_NO_THIN_NODES,
        }

    def test_no_ledger_entries_report_the_empty_ledger(self):
        session = _course_session(
            placements=[
                {"domain_key": DOMAIN, "node_id": "c1", "document_id": "d1", "status": "inferred"},
            ]
        )
        built = complement.build_complement_context(
            session, DOMAIN, self._context(), thin_max_documents=0
        )
        assert built["sky_statements"] == []
        assert built["facts"]["skies"] == {
            "available": False, "note": complement.NOTE_NO_SKIES,
        }
        assert built["facts"]["coverage"]["available"] is True

    def test_degraded_facts_use_the_embedding_note(self):
        facts = complement.degraded_facts()
        assert facts == {
            "coverage": {"available": False, "note": complement.NOTE_EMBEDDING_UNAVAILABLE},
            "skies": {"available": False, "note": complement.NOTE_EMBEDDING_UNAVAILABLE},
        }


# ---------------------------------------------------------------------------
# 5. 並べ替え（補完ありが先頭・安定）
# ---------------------------------------------------------------------------


class TestOrderComplementFirst:
    def test_complement_first_keeps_the_original_order_within_groups(self):
        candidates = [
            {"arxiv_id": "1"},
            {"arxiv_id": "2", "complement": {"fills": [{"node_label": "X"}]}},
            {"arxiv_id": "3"},
            {"arxiv_id": "4", "complement": {"skies": [{"statement": "Y"}]}},
        ]
        ordered = complement.order_complement_first(candidates)
        assert [c["arxiv_id"] for c in ordered] == ["2", "4", "1", "3"]

    def test_no_candidate_is_dropped(self):
        candidates = [{"arxiv_id": str(i)} for i in range(5)]
        assert len(complement.order_complement_first(candidates)) == 5
        assert complement.order_complement_first([]) == []

    def test_empty_complement_block_is_not_promoted(self):
        ordered = complement.order_complement_first(
            [{"arxiv_id": "1"}, {"arxiv_id": "2", "complement": {}}]
        )
        assert [c["arxiv_id"] for c in ordered] == ["1", "2"]


# ---------------------------------------------------------------------------
# 6. ranking への相乗り
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_counter():
    ranking.reset_daily_counter()
    yield
    ranking.reset_daily_counter()


def _stub_embeddings(monkeypatch, vectors, *, recorder=None):
    """``core.llm.generate_embeddings`` を差し替える（呼び出し回数も記録する）。"""
    import core.llm as llm_module

    def _fake(texts, **kwargs):
        if recorder is not None:
            recorder.append(list(texts))
        if callable(vectors):
            return vectors(texts)
        return list(vectors)

    monkeypatch.setattr(llm_module, "generate_embeddings", _fake)


def _ranking_session(**kwargs) -> FakeSession:
    """重心（chunks）まで引ける構成。"""
    kwargs.setdefault(
        "chunks", [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
    )
    return _ledger_session(**kwargs)


def _candidates():
    return [
        {"arxiv_id": "2608.00001", "title": "far", "summary": "unrelated"},
        {"arxiv_id": "2608.00002", "title": "near", "summary": "close"},
    ]


class TestRankCandidatesWithoutComplement:
    """``complement_context=None`` は完全不変（後方互換）。"""

    def test_no_complement_keys_and_a_single_batch_of_candidate_texts(self, monkeypatch):
        calls: list[list[str]] = []
        _stub_embeddings(monkeypatch, [[0.0, 1.0], [1.0, 0.0]], recorder=calls)
        session = _ranking_session()

        result = ranking.rank_candidates(session, DOMAIN, _candidates())

        assert result["available"] is True
        assert "complement_facts" not in result
        assert [c["arxiv_id"] for c in result["ordered"]] == ["2608.00002", "2608.00001"]
        assert all("complement" not in c for c in result["ordered"])
        assert len(calls) == 1
        assert calls[0] == ["far\nunrelated", "near\nclose"]

    def test_fail_soft_paths_stay_shaped_as_before(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0]])
        empty = ranking.rank_candidates(FakeSession(), DOMAIN, _candidates())
        assert empty == {
            "available": False,
            "note": ranking.NOTE_NO_CORPUS,
            "ordered": _candidates(),
        }


class TestRankCandidatesWithComplement:
    def _context(self):
        return {
            "anchors": [
                _anchor("c-thin", label="重力波", region="高エネルギー", vector=[1.0, 0.0]),
                _anchor("c-thick", label="CMB", vector=[1.0, 0.0]),
            ],
            "skeleton_version": "v3",
        }

    def _complement_context(self):
        return {
            "thin_node_ids": {"c-thin"},
            "sky_statements": [
                {"statement": "この装置は線形である", "document_title": "論文A"},
            ],
            "facts": {"coverage": {"available": True}, "skies": {"available": True}},
        }

    def test_statements_ride_the_same_single_batch(self, monkeypatch):
        calls: list[list[str]] = []
        _stub_embeddings(
            monkeypatch, [[0.0, 1.0], [1.0, 0.0], [1.0, 0.0]], recorder=calls
        )
        session = _ranking_session()

        result = ranking.rank_candidates(
            session,
            DOMAIN,
            _candidates(),
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )

        # 追加の embedding コールは無い（1バッチ・末尾に前提文が載るだけ）。
        assert len(calls) == 1
        assert calls[0] == ["far\nunrelated", "near\nclose", "この装置は線形である"]
        assert result["available"] is True
        assert result["complement_facts"] == self._complement_context()["facts"]

    def test_attaches_fills_and_skies_only_to_matching_candidates(self, monkeypatch):
        # 候補1は直交（何にも近くない）、候補2はアンカー・前提文と一致。
        _stub_embeddings(monkeypatch, [[0.0, 1.0], [1.0, 0.0], [1.0, 0.0]])
        session = _ranking_session()

        result = ranking.rank_candidates(
            session,
            DOMAIN,
            _candidates(),
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )
        by_id = {c["arxiv_id"]: c for c in result["ordered"]}

        assert "complement" not in by_id["2608.00001"]
        block = by_id["2608.00002"]["complement"]
        assert block["fills"] == [{"node_label": "重力波", "region_label": "高エネルギー"}]
        assert block["skies"][0]["statement"] == "この装置は線形である"
        assert block["skies"][0]["closed_world_note"] == complement.CLOSED_WORLD_SKY_NOTE

    def test_lens_a_needs_the_anchor_context(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        result = ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            _candidates(),
            complement_context=self._complement_context(),
        )
        for candidate in result["ordered"]:
            assert "fills" not in candidate.get("complement", {})
            assert candidate["complement"]["skies"]

    def test_empty_block_is_not_attached(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[0.0, 1.0], [0.0, 1.0], [1.0, 0.0]])
        result = ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            _candidates(),
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )
        assert all("complement" not in c for c in result["ordered"])

    def test_no_raw_numbers_in_the_complement_block(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        result = ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            _candidates(),
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )
        for candidate in result["ordered"]:
            block = candidate.get("complement") or {}
            for row in list(block.get("fills") or []) + list(block.get("skies") or []):
                assert not [v for v in row.values() if isinstance(v, (int, float))]

    def test_input_candidates_are_not_mutated(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        original = _candidates()
        ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            original,
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )
        assert all("complement" not in c for c in original)

    def test_embedding_failure_degrades_both_lenses(self, monkeypatch):
        import core.llm as llm_module

        def _boom(texts, **kwargs):
            raise RuntimeError("embedding unavailable (test)")

        monkeypatch.setattr(llm_module, "generate_embeddings", _boom)

        result = ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            _candidates(),
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_UNAVAILABLE
        assert [c["arxiv_id"] for c in result["ordered"]] == [
            "2608.00001", "2608.00002",
        ]
        assert result["complement_facts"] == complement.degraded_facts()

    def test_daily_limit_degrades_both_lenses(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        result = ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            _candidates(),
            daily_limit=0,
            complement_context=self._complement_context(),
        )
        assert result["note"] == ranking.NOTE_LIMIT_REACHED
        assert result["complement_facts"] == complement.degraded_facts()

    def test_count_mismatch_degrades_instead_of_misaligning(self, monkeypatch):
        # 前提文の分が返らない = バッチ長の不一致（黙って候補にずれたベクトルを当てない）。
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0]])
        result = ranking.rank_candidates(
            _ranking_session(),
            DOMAIN,
            _candidates(),
            anchor_context=self._context(),
            complement_context=self._complement_context(),
        )
        assert result["available"] is False
        assert result["complement_facts"] == complement.degraded_facts()
