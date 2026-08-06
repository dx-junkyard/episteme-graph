"""知識ランドスケープ — ``core/landscape/store.py`` / ``projection.py`` の単体テスト。

設計書: ``docs/features/knowledge_landscape_design.md`` §5（再解析セマンティクス）・
§8（モジュール契約）・§9（DTO）・§0（LS3 / LS5 / LS6 / P4）。

``tests/test_element_explanations.py`` / ``tests/fixtures/atlas_skeletons_fake.py`` の流儀を
踏襲し、``landscape_placements`` の SQL 面だけを模倣するインメモリのフェイクセッションを
本ファイル内に置いて検証する（実 DB 接続なし）。部分一意インデックス
``uq_landscape_placements_live``（superseded 以外は
``(document_id, domain_key, node_id, perspective)`` で一意）もフェイク側で再現し、
store が制約違反を起こさないことを検査する。

検証観点:
  1. 再解析: ``inferred`` のみ ``superseded`` へ / ``confirmed``・``rejected``・
     ``review_required`` は不変 / 同一キーの新候補は挿入スキップ（LS3）
  2. 履歴保持: ``superseded`` 行は削除されず残る（P4）。バッチ内重複は先勝ち
  3. ``update_status``: live ↔ live のみ・``superseded`` の復帰不可・未知語彙は ValueError
  4. ``list_for_document(s)``: 既定は live のみ / 空 ids・空 statuses は SQL 非発行（LS6）
  5. ``projection``: 生 ``weight`` / ``confidence`` を DTO に出さない（LS5）・
     学習者 DTO は claim_id を落とし可視 status のみ
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas  # noqa: E402
from core.landscape import projection, schema, store  # noqa: E402

_DOC = "11111111-1111-1111-1111-111111111111"
_DOC2 = "22222222-2222-2222-2222-222222222222"
_RUN = "33333333-3333-3333-3333-333333333333"
_TEACHER = "99999999-9999-9999-9999-999999999999"


# ---------------------------------------------------------------------------
# インメモリフェイクセッション
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeLandscapeSession:
    """``landscape_placements`` のインメモリ実装（セッション兼用）。

    ``commit`` / ``rollback`` / ``close`` は呼ばれたら記録するだけ（store 側は
    トランザクションを触らない設計なので、テストは 0 回であることを検査できる）。
    """

    def __init__(self):
        self.rows: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._id_seq = itertools.count(1)
        self._clock = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

    # -- セッションインターフェース --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        if sql.startswith("UPDATE landscape_placements SET status = :superseded"):
            return self._supersede(params)
        if sql.startswith("UPDATE landscape_placements SET status = :new_status"):
            return self._update_status(params)
        if sql.startswith("SELECT domain_key, node_id, perspective FROM"):
            return self._live_keys(params)
        if sql.startswith("INSERT INTO landscape_placements"):
            return self._insert(params)
        if sql.startswith("SELECT") and "WHERE id = CAST(:id AS uuid)" in sql:
            return self._get(params)
        if sql.startswith("SELECT") and "document_id::text = ANY(:ids)" in sql:
            return self._list_many(params)
        if sql.startswith("SELECT") and "WHERE document_id = CAST(:document_id AS uuid)" in sql:
            return self._list_one(sql, params)
        raise AssertionError(f"unhandled landscape_placements SQL: {sql!r}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    # -- ヘルパ --
    def _tick(self) -> datetime:
        self._clock += timedelta(seconds=1)
        return self._clock

    @staticmethod
    def _tuple(row: dict) -> tuple:
        return (
            row["id"], row["document_id"], row["domain_key"], row["skeleton_version"],
            row["node_id"], row["node_kind"], row["perspective"], row["weight"],
            row["reason"], row["evidence"], row["status"], row["provenance"],
            row["run_id"], row["created_by"], row["reviewed_by"], row["reviewed_at"],
            row["review_note"], row["created_at"], row["updated_at"],
        )

    def seed(self, **kwargs) -> dict:
        """テスト用に直接1行入れる（教員判断済みの既存行を作るため）。"""
        row = {
            "id": kwargs.get("id") or f"pl-{next(self._id_seq)}",
            "document_id": kwargs.get("document_id", _DOC),
            "domain_key": kwargs.get("domain_key", "astrophysics"),
            "skeleton_version": kwargs.get("skeleton_version", "2026.1"),
            "node_id": kwargs.get("node_id", "cosmology"),
            "node_kind": kwargs.get("node_kind", schema.NODE_KIND_REGION),
            "perspective": kwargs.get("perspective", schema.PERSPECTIVE_SUBJECT),
            "weight": kwargs.get("weight", 0.8),
            "reason": kwargs.get("reason", "理由"),
            "evidence": kwargs.get("evidence", [{"quote": "原文", "claim_id": None}]),
            "status": kwargs.get("status", schema.STATUS_INFERRED),
            "provenance": kwargs.get("provenance", schema.PROVENANCE_LLM),
            "run_id": kwargs.get("run_id"),
            "created_by": kwargs.get("created_by", schema.PIPELINE_CREATED_BY),
            "reviewed_by": kwargs.get("reviewed_by"),
            "reviewed_at": kwargs.get("reviewed_at"),
            "review_note": kwargs.get("review_note", ""),
            "created_at": self._tick(),
            "updated_at": self._tick(),
        }
        self.rows.append(row)
        return row

    def _live(self, document_id: str) -> list[dict]:
        return [
            r
            for r in self.rows
            if r["document_id"] == document_id and r["status"] != schema.STATUS_SUPERSEDED
        ]

    # -- ディスパッチ先 --
    def _supersede(self, p: dict) -> _FakeResult:
        touched = []
        for row in self.rows:
            if (
                row["document_id"] == p["document_id"]
                and row["status"] in set(p["supersedable"])
            ):
                row["status"] = p["superseded"]
                row["updated_at"] = self._tick()
                touched.append((row["id"],))
        return _FakeResult(touched)

    def _live_keys(self, p: dict) -> _FakeResult:
        return _FakeResult(
            [
                (r["domain_key"], r["node_id"], r["perspective"])
                for r in self._live(p["document_id"])
            ]
        )

    def _insert(self, p: dict) -> _FakeResult:
        key = (p["document_id"], p["domain_key"], p["node_id"], p["perspective"])
        for row in self.rows:
            if row["status"] == schema.STATUS_SUPERSEDED:
                continue
            if (
                row["document_id"],
                row["domain_key"],
                row["node_id"],
                row["perspective"],
            ) == key:
                raise RuntimeError(
                    "duplicate key value violates unique index "
                    f"uq_landscape_placements_live: {key}"
                )
        evidence = p["evidence"]
        row = {
            "id": f"pl-{next(self._id_seq)}",
            "document_id": p["document_id"],
            "domain_key": p["domain_key"],
            "skeleton_version": p["skeleton_version"],
            "node_id": p["node_id"],
            "node_kind": p["node_kind"],
            "perspective": p["perspective"],
            "weight": p["weight"],
            "reason": p["reason"],
            "evidence": json.loads(evidence) if isinstance(evidence, str) else evidence,
            "status": p["status"],
            "provenance": p["provenance"],
            "run_id": p["run_id"],
            "created_by": p["created_by"],
            "reviewed_by": None,
            "reviewed_at": None,
            "review_note": "",
            "created_at": self._tick(),
            "updated_at": self._tick(),
        }
        self.rows.append(row)
        return _FakeResult([self._tuple(row)])

    def _get(self, p: dict) -> _FakeResult:
        for row in self.rows:
            if row["id"] == p["id"]:
                return _FakeResult([self._tuple(row)])
        return _FakeResult()

    def _update_status(self, p: dict) -> _FakeResult:
        for row in self.rows:
            if row["id"] != p["id"] or row["status"] == p["superseded"]:
                continue
            row["status"] = p["new_status"]
            row["reviewed_by"] = p["reviewer_id"]
            row["reviewed_at"] = self._tick()
            if p["note"]:
                row["review_note"] = p["note"]
            row["updated_at"] = self._tick()
            return _FakeResult([self._tuple(row)])
        return _FakeResult()

    def _list_one(self, sql: str, p: dict) -> _FakeResult:
        rows = [r for r in self.rows if r["document_id"] == p["document_id"]]
        if "status <> :superseded" in sql:
            rows = [r for r in rows if r["status"] != p["superseded"]]
        rows.sort(key=lambda r: (r["domain_key"], r["node_id"], r["perspective"]))
        return _FakeResult([self._tuple(r) for r in rows])

    def _list_many(self, p: dict) -> _FakeResult:
        ids = set(p["ids"])
        statuses = set(p["statuses"])
        rows = [
            r for r in self.rows if r["document_id"] in ids and r["status"] in statuses
        ]
        rows.sort(key=lambda r: (r["domain_key"], r["node_id"], r["perspective"]))
        return _FakeResult([self._tuple(r) for r in rows])


@pytest.fixture
def db():
    return FakeLandscapeSession()


def _candidate(**kwargs) -> dict:
    base = {
        "domain_key": "astrophysics",
        "skeleton_version": "2026.1",
        "node_id": "cosmology",
        "node_kind": schema.NODE_KIND_REGION,
        "perspective": schema.PERSPECTIVE_SUBJECT,
        "weight": 0.8,
        "reason": "この論文は宇宙膨張の測定を主題にしている",
        "evidence": [{"quote": "we measure the expansion rate", "claim_id": "claim-1"}],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# 1. 再解析セマンティクス（§5 / LS3）
# ---------------------------------------------------------------------------


class TestSupersedeAndInsert:
    def test_inserts_candidates_as_inferred_only(self, db):
        result = store.supersede_and_insert_candidates(
            db, _DOC, _RUN, [_candidate(), _candidate(node_id="galaxies")]
        )
        assert result["created"] == 2
        assert result["superseded"] == 0
        assert result["skipped_existing"] == 0
        assert {r["status"] for r in db.rows} == {schema.STATUS_INFERRED}
        assert {r["run_id"] for r in db.rows} == {_RUN}
        # store はトランザクションを触らない（呼び出し側の責務）
        assert (db.commits, db.rollbacks, db.closed) == (0, 0, False)

    def test_reanalysis_supersedes_only_inferred(self, db):
        old = db.seed(status=schema.STATUS_INFERRED, node_id="galaxies")
        confirmed = db.seed(status=schema.STATUS_CONFIRMED, node_id="stars")
        rejected = db.seed(status=schema.STATUS_REJECTED, node_id="planetary")
        review = db.seed(status=schema.STATUS_REVIEW_REQUIRED, node_id="high_energy")

        result = store.supersede_and_insert_candidates(
            db, _DOC, _RUN, [_candidate(node_id="cosmology")]
        )

        assert result["superseded"] == 1
        assert old["status"] == schema.STATUS_SUPERSEDED
        # 教員判断は AI 再解析で動かない（LS3）
        assert confirmed["status"] == schema.STATUS_CONFIRMED
        assert rejected["status"] == schema.STATUS_REJECTED
        assert review["status"] == schema.STATUS_REVIEW_REQUIRED
        # 履歴は削除されない（P4）
        assert len(db.rows) == 5

    @pytest.mark.parametrize(
        "existing_status",
        [schema.STATUS_CONFIRMED, schema.STATUS_REJECTED, schema.STATUS_REVIEW_REQUIRED],
    )
    def test_candidate_colliding_with_live_teacher_row_is_skipped(
        self, db, existing_status
    ):
        db.seed(status=existing_status, node_id="cosmology",
                perspective=schema.PERSPECTIVE_SUBJECT)

        result = store.supersede_and_insert_candidates(
            db,
            _DOC,
            _RUN,
            [
                _candidate(node_id="cosmology", perspective=schema.PERSPECTIVE_SUBJECT),
                _candidate(node_id="cosmology", perspective=schema.PERSPECTIVE_METHOD),
            ],
        )

        assert result["skipped_existing"] == 1
        assert result["created"] == 1
        # 却下済みキーが inferred として復活しない
        statuses = {(r["node_id"], r["perspective"]): r["status"] for r in db.rows}
        assert statuses[("cosmology", schema.PERSPECTIVE_SUBJECT)] == existing_status
        assert statuses[("cosmology", schema.PERSPECTIVE_METHOD)] == schema.STATUS_INFERRED

    def test_superseded_key_can_be_reinserted(self, db):
        """再解析後は同一キーの新候補を入れられる（部分一意インデックスの前提）。"""
        db.seed(status=schema.STATUS_INFERRED, node_id="cosmology")
        result = store.supersede_and_insert_candidates(db, _DOC, _RUN, [_candidate()])
        assert (result["superseded"], result["created"], result["skipped_existing"]) == (1, 1, 0)
        live = [r for r in db.rows if r["status"] != schema.STATUS_SUPERSEDED]
        assert len(live) == 1

    def test_duplicate_keys_in_batch_are_deduped_first_wins(self, db):
        result = store.supersede_and_insert_candidates(
            db,
            _DOC,
            _RUN,
            [_candidate(reason="先勝ち"), _candidate(reason="あとから")],
        )
        assert (result["created"], result["skipped_existing"]) == (1, 1)
        assert db.rows[0]["reason"] == "先勝ち"

    def test_other_documents_are_untouched(self, db):
        other = db.seed(document_id=_DOC2, status=schema.STATUS_INFERRED)
        store.supersede_and_insert_candidates(db, _DOC, _RUN, [_candidate()])
        assert other["status"] == schema.STATUS_INFERRED

    def test_empty_candidates_touch_no_sql(self, db):
        result = store.supersede_and_insert_candidates(db, _DOC, _RUN, [])
        assert result == {
            "created": 0,
            "superseded": 0,
            "skipped_existing": 0,
            "created_ids": [],
        }
        assert db.calls == []

    @pytest.mark.parametrize(
        "bad",
        [
            {"domain_key": ""},
            {"node_id": ""},
            {"perspective": "vibes"},
            {"node_kind": "galaxy"},
            {"provenance": "hearsay"},
        ],
    )
    def test_invalid_vocabulary_raises_before_any_write(self, db, bad):
        with pytest.raises(ValueError):
            store.supersede_and_insert_candidates(db, _DOC, _RUN, [_candidate(**bad)])
        assert db.calls == []

    def test_weight_is_clamped_and_evidence_normalized(self, db):
        store.supersede_and_insert_candidates(
            db,
            _DOC,
            _RUN,
            [
                _candidate(
                    weight=3.7,
                    evidence=[
                        {"quote": " q1 ", "claim_id": "c1", "confidence": 0.9},
                        "bare quote",
                        {"quote": "", "claim_id": None},
                    ],
                )
            ],
        )
        row = db.rows[0]
        assert row["weight"] == 1.0
        # confidence のような生数値は evidence から落とす（LS5）
        assert row["evidence"] == [
            {"quote": "q1", "claim_id": "c1"},
            {"quote": "bare quote", "claim_id": None},
        ]

    def test_no_delete_statements_are_issued(self, db):
        db.seed(status=schema.STATUS_INFERRED)
        store.supersede_and_insert_candidates(db, _DOC, _RUN, [_candidate(node_id="stars")])
        assert not any("DELETE" in sql.upper() for sql, _ in db.calls)


# ---------------------------------------------------------------------------
# 2. status 遷移（§8 / LS3）
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    @pytest.mark.parametrize(
        "target",
        [
            schema.STATUS_CONFIRMED,
            schema.STATUS_REJECTED,
            schema.STATUS_REVIEW_REQUIRED,
            schema.STATUS_INFERRED,
        ],
    )
    def test_live_to_live_transitions_record_reviewer(self, db, target):
        row = db.seed(status=schema.STATUS_INFERRED)
        updated = store.update_status(
            db, row["id"], target, reviewer_id=_TEACHER, note="根拠を確認した"
        )
        assert updated["status"] == target
        assert updated["reviewed_by"] == _TEACHER
        assert updated["reviewed_at"]
        assert updated["review_note"] == "根拠を確認した"
        # provenance は触らない（生成手段の記録）
        assert updated["provenance"] == schema.PROVENANCE_LLM

    def test_empty_note_keeps_previous_note(self, db):
        row = db.seed(status=schema.STATUS_INFERRED, review_note="前の理由")
        updated = store.update_status(
            db, row["id"], schema.STATUS_CONFIRMED, reviewer_id=_TEACHER
        )
        assert updated["review_note"] == "前の理由"

    def test_superseded_target_is_rejected(self, db):
        row = db.seed(status=schema.STATUS_INFERRED)
        with pytest.raises(ValueError):
            store.update_status(
                db, row["id"], schema.STATUS_SUPERSEDED, reviewer_id=_TEACHER
            )
        assert row["status"] == schema.STATUS_INFERRED

    def test_superseded_source_cannot_be_revived(self, db):
        row = db.seed(status=schema.STATUS_SUPERSEDED)
        with pytest.raises(ValueError):
            store.update_status(
                db, row["id"], schema.STATUS_CONFIRMED, reviewer_id=_TEACHER
            )
        assert row["status"] == schema.STATUS_SUPERSEDED

    def test_unknown_status_is_rejected(self, db):
        row = db.seed()
        with pytest.raises(ValueError):
            store.update_status(db, row["id"], "approved", reviewer_id=_TEACHER)

    def test_reviewer_id_is_required(self, db):
        row = db.seed()
        with pytest.raises(ValueError):
            store.update_status(db, row["id"], schema.STATUS_CONFIRMED, reviewer_id="")

    def test_missing_row_returns_none(self, db):
        assert (
            store.update_status(
                db, "pl-does-not-exist", schema.STATUS_CONFIRMED, reviewer_id=_TEACHER
            )
            is None
        )


# ---------------------------------------------------------------------------
# 3. 読み出し（fail-closed）
# ---------------------------------------------------------------------------


class TestListing:
    def test_list_for_document_hides_history_by_default(self, db):
        db.seed(status=schema.STATUS_INFERRED, node_id="cosmology")
        db.seed(status=schema.STATUS_SUPERSEDED, node_id="galaxies")
        live = store.list_for_document(db, _DOC)
        assert [r["node_id"] for r in live] == ["cosmology"]
        full = store.list_for_document(db, _DOC, include_history=True)
        assert {r["node_id"] for r in full} == {"cosmology", "galaxies"}

    def test_list_for_document_empty_id_touches_no_sql(self, db):
        assert store.list_for_document(db, "") == []
        assert db.calls == []

    def test_list_for_documents_filters_by_status(self, db):
        db.seed(document_id=_DOC, status=schema.STATUS_CONFIRMED, node_id="cosmology")
        db.seed(document_id=_DOC, status=schema.STATUS_REJECTED, node_id="galaxies")
        db.seed(document_id=_DOC2, status=schema.STATUS_INFERRED, node_id="stars")
        rows = store.list_for_documents(
            db, [_DOC, _DOC2], statuses=schema.LEARNER_VISIBLE_STATUSES
        )
        assert {r["node_id"] for r in rows} == {"cosmology", "stars"}

    def test_list_for_documents_uses_any_binding(self, db):
        db.seed(document_id=_DOC, status=schema.STATUS_CONFIRMED)
        store.list_for_documents(db, [_DOC], statuses=[schema.STATUS_CONFIRMED])
        sql = db.calls[-1][0]
        assert "ANY(:ids)" in sql and "ANY(:statuses)" in sql

    @pytest.mark.parametrize(
        "ids,statuses",
        [([], schema.LEARNER_VISIBLE_STATUSES), ([_DOC], []), ([""], [""])],
    )
    def test_list_for_documents_fail_closed_on_empty_input(self, db, ids, statuses):
        assert store.list_for_documents(db, ids, statuses=statuses) == []
        assert db.calls == []

    def test_get_placement_roundtrip(self, db):
        row = db.seed(weight=0.42, evidence=[{"quote": "q", "claim_id": "c"}])
        loaded = store.get_placement(db, row["id"])
        assert loaded["id"] == row["id"]
        assert loaded["weight"] == pytest.approx(0.42)
        assert loaded["evidence"] == [{"quote": "q", "claim_id": "c"}]
        assert store.get_placement(db, "") is None


# ---------------------------------------------------------------------------
# 4. 投影（§9 / LS5）
# ---------------------------------------------------------------------------


def _skeleton() -> atlas.AtlasSkeleton:
    return atlas.AtlasSkeleton(
        cartridge="astrophysics",
        status=atlas.STATUS_FROZEN,
        version="2026.1",
        generated_by="reference_map:test",
        reviewed_by=("faculty:t",),
        changelog=(atlas.ChangelogEntry(version="2026.1", note="t"),),
        regions=(
            atlas.SkeletonRegion(
                id="cosmology",
                label="宇宙論・大規模構造",
                concepts=(atlas.SkeletonConcept(id="cmb", label="宇宙背景放射"),),
            ),
            atlas.SkeletonRegion(id="galaxies", label="銀河・銀河団"),
        ),
    )


def _collect_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(str(k))
            keys |= _collect_keys(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            keys |= _collect_keys(v)
    return keys


class TestProjection:
    def test_node_index_maps_regions_and_concepts(self):
        index = projection.skeleton_node_index({"astrophysics": _skeleton()})
        assert index[("astrophysics", "cosmology")] == {
            "label": "宇宙論・大規模構造",
            "kind": schema.NODE_KIND_REGION,
            "region_id": "cosmology",
        }
        assert index[("astrophysics", "cmb")] == {
            "label": "宇宙背景放射",
            "kind": schema.NODE_KIND_CONCEPT,
            "region_id": "cosmology",
        }
        # 3形の入力を同じ索引に正規化する
        assert (
            projection.skeleton_node_index([("astrophysics", _skeleton())]) == index
        )
        assert (
            projection.skeleton_node_index(
                [{"domain_key": "astrophysics", "skeleton": _skeleton()}]
            )
            == index
        )

    def test_admin_dto_uses_labels_and_hides_raw_numbers(self, db):
        row = db.seed(
            status=schema.STATUS_INFERRED,
            node_id="cmb",
            weight=0.9,
            evidence=[{"quote": "q", "claim_id": "c1"}],
        )
        index = projection.skeleton_node_index({"astrophysics": _skeleton()})
        dto = projection.admin_placement_dto(
            store.get_placement(db, row["id"]),
            index,
            domain_names={"astrophysics": "宇宙物理"},
        )
        assert dto["domain_name"] == "宇宙物理"
        assert dto["node_label"] == "宇宙背景放射"
        assert dto["region_id"] == "cosmology"
        assert dto["node_kind"] == schema.NODE_KIND_CONCEPT
        assert dto["weight_label"] == "強い関連"
        assert dto["status_label"] == schema.STATUS_LABELS[schema.STATUS_INFERRED]
        assert dto["provenance_label"] == "AIによる推定（未確認）"
        # 教員にも生数値は出さない（LS5）
        assert not _collect_keys(dto) & set(schema.FORBIDDEN_NUMERIC_KEYS)
        # 教員 DTO は claim 参照を保つ（根拠へ遡れる・LS4）
        assert dto["evidence"] == [{"quote": "q", "claim_id": "c1"}]

    def test_admin_dto_keeps_unknown_node_visible(self, db):
        row = db.seed(node_id="retired_region")
        dto = projection.admin_placement_dto(
            store.get_placement(db, row["id"]),
            projection.skeleton_node_index({"astrophysics": _skeleton()}),
        )
        assert dto["node_label"] == "retired_region"
        assert dto["region_id"] == ""
        assert dto["domain_name"] == "astrophysics"

    def test_learner_dto_filters_status_and_drops_claim_ids(self, db):
        db.seed(status=schema.STATUS_CONFIRMED, node_id="cosmology", weight=0.9)
        db.seed(status=schema.STATUS_REJECTED, node_id="galaxies")
        db.seed(
            status=schema.STATUS_INFERRED,
            node_id="cmb",
            perspective=schema.PERSPECTIVE_METHOD,
            weight=0.2,
            evidence=[{"quote": "q", "claim_id": "c1"}],
        )
        db.seed(status=schema.STATUS_SUPERSEDED, node_id="cosmology",
                perspective=schema.PERSPECTIVE_QUESTION)
        db.seed(status=schema.STATUS_INFERRED, node_id="gone_in_new_version")

        rows = store.list_for_document(db, _DOC, include_history=True)
        dto = projection.learner_landscape_dto(
            rows,
            projection.skeleton_node_index({"astrophysics": _skeleton()}),
            [{"domain_key": "astrophysics", "domain_name": "宇宙物理", "frozen_version": "2026.1"}],
            "astrophysics",
            document_titles={_DOC: "論文タイトル"},
        )

        placements = dto["documents"][0]["placements"]
        # rejected / superseded / 骨格に無いノードは出ない
        assert {p["node_id"] for p in placements} == {"cosmology", "cmb"}
        assert all(p["evidence"] == [] or "claim_id" not in p["evidence"][0] for p in placements)
        labels = {p["node_id"]: p for p in placements}
        assert labels["cosmology"]["provenance_label"] == "教員確認済み"
        assert labels["cmb"]["provenance_label"] == "AIによる推定（未確認）"
        assert labels["cmb"]["weight_label"] == "弱い関連"
        assert labels["cmb"]["perspective_label"] == "方法から"
        assert dto["domains"][0]["is_course_map"] is True
        assert dto["corpus"] == {"source_document_count": 1, "placed_document_count": 1}
        assert dto["documents"][0]["title"] == "論文タイトル"
        # LS5: 学習者 DTO にも生数値のキーが無い
        assert not _collect_keys(dto) & set(schema.FORBIDDEN_NUMERIC_KEYS)

    def test_learner_dto_orders_course_domain_first(self, db):
        db.seed(status=schema.STATUS_CONFIRMED, domain_key="particle_physics",
                node_id="cosmology")
        db.seed(status=schema.STATUS_CONFIRMED, domain_key="astrophysics",
                node_id="cosmology")
        index = projection.skeleton_node_index(
            {"astrophysics": _skeleton(), "particle_physics": _skeleton()}
        )
        dto = projection.learner_landscape_dto(
            store.list_for_document(db, _DOC),
            index,
            [
                {"domain_key": "particle_physics", "frozen_version": "2026.1"},
                {"domain_key": "astrophysics", "frozen_version": "2026.1"},
                {"domain_key": "unused_domain", "frozen_version": "2026.1"},
            ],
            "astrophysics",
        )
        assert [d["domain_key"] for d in dto["domains"]] == [
            "astrophysics",
            "particle_physics",
        ]

    def test_learner_dto_empty_is_a_valid_empty_shape(self):
        dto = projection.learner_landscape_dto([], {}, [], None)
        assert dto == {
            "course_domain_key": None,
            "domains": [],
            "documents": [],
            "corpus": {"source_document_count": 0, "placed_document_count": 0},
        }


# ---------------------------------------------------------------------------
# 5. 語彙とラベル（§8）
# ---------------------------------------------------------------------------


class TestSchemaVocabulary:
    def test_status_sets_are_consistent(self):
        assert schema.STATUS_SUPERSEDED not in schema.LIVE_STATUSES
        assert set(schema.LIVE_STATUSES) < set(schema.STATUSES)
        assert set(schema.LEARNER_VISIBLE_STATUSES) < set(schema.LIVE_STATUSES)
        assert schema.STATUS_REJECTED not in schema.LEARNER_VISIBLE_STATUSES
        assert schema.SUPERSEDABLE_STATUSES == (schema.STATUS_INFERRED,)

    def test_every_vocabulary_value_has_a_label(self):
        assert set(schema.PERSPECTIVE_LABELS) == set(schema.PERSPECTIVES)
        assert set(schema.STATUS_LABELS) == set(schema.STATUSES)
        assert set(schema.PROVENANCE_LABELS) == set(schema.LEARNER_VISIBLE_STATUSES)

    @pytest.mark.parametrize(
        "weight,label",
        [
            (1.0, "強い関連"),
            (0.7, "強い関連"),
            (0.69, "関連"),
            (0.4, "関連"),
            (0.39, "弱い関連"),
            (0.0, "弱い関連"),
            (None, "弱い関連"),
            ("なんとなく", "弱い関連"),
        ],
    )
    def test_weight_label_boundaries(self, weight, label):
        assert schema.weight_label(weight) == label

    def test_provenance_label_defaults_to_ai_side(self):
        # 未知 status を「教員確認済み」に見せない（AC-005 の安全側）
        assert schema.provenance_label("nonsense") == schema.DEFAULT_PROVENANCE_LABEL
        assert "AI" in schema.DEFAULT_PROVENANCE_LABEL

    def test_labels_contain_no_urging_language(self):
        forbidden = ("疑え", "すぐに", "必ず確認せよ", "残念", "ノーベル")
        text = " ".join(
            list(schema.STATUS_LABELS.values())
            + list(schema.PROVENANCE_LABELS.values())
            + list(schema.WEIGHT_LABELS.values())
            + list(schema.PERSPECTIVE_LABELS.values())
        )
        assert not [w for w in forbidden if w in text]

    def test_normalize_weight_is_deterministic(self):
        assert schema.normalize_weight(None) == schema.DEFAULT_WEIGHT
        assert schema.normalize_weight("x") == schema.DEFAULT_WEIGHT
        assert schema.normalize_weight(-1) == 0.0
        assert schema.normalize_weight(2) == 1.0
        assert schema.normalize_weight(0.33) == pytest.approx(0.33)


# ---------------------------------------------------------------------------
# 6. 一意制約（フェイク側で再現した部分一意インデックス）
# ---------------------------------------------------------------------------


class TestUniqueIndexRespected:
    def test_store_never_violates_live_unique_index(self, db):
        """live キー衝突は store 側で事前に弾き、DB 制約違反を起こさない。"""
        db.seed(status=schema.STATUS_CONFIRMED, node_id="cosmology")
        # 例外が飛ばないこと自体が検査（フェイクは違反時に RuntimeError を投げる）
        store.supersede_and_insert_candidates(
            db, _DOC, _RUN, [_candidate(), _candidate(), _candidate(node_id="galaxies")]
        )
        live_keys = [
            (r["domain_key"], r["node_id"], r["perspective"])
            for r in db.rows
            if r["status"] != schema.STATUS_SUPERSEDED
        ]
        assert len(live_keys) == len(set(live_keys))

    def test_migration_declares_partial_unique_index(self):
        sql = (BACKEND / "db" / "065_landscape_placements.sql").read_text(encoding="utf-8")
        assert re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_landscape_placements_live\s+"
            r"ON landscape_placements \(document_id, domain_key, node_id, perspective\)\s+"
            r"WHERE status <> 'superseded'",
            sql,
        )
