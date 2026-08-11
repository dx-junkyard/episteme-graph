"""カテゴリギャップ候補 — ``core/atlas_gaps/store.py`` の単体テスト。

設計書: ``docs/features/category_gap_candidates_design.md`` §4.1（反復閾値）/
§4.2（却下の永続性）/ §4.3（2層分離・読み時導出）/ §5.2（データモデル）/ §5.4（凍結の刻印）。

``tests/test_landscape_store.py`` と同じ流儀で、``landscape_gap_signals`` /
``atlas_gap_decisions`` / ``theory_review_events`` の SQL 面だけを模倣した
インメモリのフェイクセッションを本ファイル内に置いて検証する（実 DB 接続なし）。
``atlas_gap_decisions.cluster_key`` の一意制約もフェイク側で再現する。

検証観点:
  1. ``record_signals``: 再解析で active のみ supersede / 空入力は SQL 非発行 /
     上限と重複畳み / 不正な1件だけ drop（配置の保存を巻き込まない）
  2. ``derive_candidates``: 反復閾値2 / 解消済みの自然消滅 / dismissed の抑止と復帰 /
     version_mismatch / **生 confidence を返さない・件数フィールドを作らない**
  3. 判断: upsert（dismiss は理由必須）/ restore は行削除でなく遷移 /
     mark_incorporated は accepted 限定 / stamp_applied_versions は実在 node のみ /
     list_pending_for_freeze はラベル列挙
"""

from __future__ import annotations

import itertools
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import atlas  # noqa: E402
from core.atlas_gaps import schema, store  # noqa: E402

_DOC1 = "11111111-1111-1111-1111-111111111111"
_DOC2 = "22222222-2222-2222-2222-222222222222"
_DOC3 = "33333333-3333-3333-3333-333333333333"
_RUN = "44444444-4444-4444-4444-444444444444"
_TEACHER = "99999999-9999-9999-9999-999999999999"
_DOMAIN = "astrophysics"


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


class FakeGapSession:
    """``landscape_gap_signals`` / ``atlas_gap_decisions`` のインメモリ実装。"""

    def __init__(self):
        self.signals: list[dict] = []
        self.decisions: list[dict] = []
        self.audits: list[dict] = []
        self.titles: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._id_seq = itertools.count(1)
        self._clock = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)

    # -- セッションインターフェース --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        if sql.startswith("UPDATE landscape_gap_signals"):
            return self._supersede_signals(params)
        if sql.startswith("INSERT INTO landscape_gap_signals"):
            return self._insert_signal(params)
        if sql.startswith("INSERT INTO theory_review_events"):
            return self._insert_audit(params)
        if sql.startswith("SELECT") and "FROM landscape_gap_signals" in sql:
            return self._select_signals(sql, params)
        if sql.startswith("INSERT INTO atlas_gap_decisions"):
            return self._upsert_decision(params)
        if sql.startswith("UPDATE atlas_gap_decisions SET status = :candidate"):
            return self._restore_decision(params)
        if sql.startswith("UPDATE atlas_gap_decisions SET draft_node_id"):
            return self._mark_incorporated(params)
        if sql.startswith("UPDATE atlas_gap_decisions SET applied_version"):
            return self._stamp(params)
        if sql.startswith("SELECT") and "FROM atlas_gap_decisions" in sql:
            return self._select_decisions(sql, params)
        raise AssertionError(f"unhandled atlas_gaps SQL: {sql!r}")

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
    def _signal_tuple(row: dict, *, with_title: str | None = None) -> tuple:
        base = (
            row["id"], row["document_id"], row["run_id"], row["domain_key"],
            row["skeleton_version"], row["layer"], row["parent_region_id"],
            row["proposed_label"], row["normalized_label"], row["reason"],
            row["evidence_quote"], row["confidence"], row["status"],
            row["created_at"], row["updated_at"],
        )
        return base + (with_title,) if with_title is not None else base

    @staticmethod
    def _decision_tuple(row: dict) -> tuple:
        return (
            row["id"], row["cluster_key"], row["status"], row["review_note"],
            row["merged_into"], row["draft_node_id"], row["applied_version"],
            row["decided_by"], row["decided_at"], row["created_at"], row["updated_at"],
        )

    def seed_signal(self, **kwargs) -> dict:
        label = kwargs.get("proposed_label", "Cosmic Web")
        row = {
            "id": kwargs.get("id") or f"sig-{next(self._id_seq)}",
            "document_id": kwargs.get("document_id", _DOC1),
            "run_id": kwargs.get("run_id"),
            "domain_key": kwargs.get("domain_key", _DOMAIN),
            "skeleton_version": kwargs.get("skeleton_version", "2026.1"),
            "layer": kwargs.get("layer", schema.GAP_LAYER_CONCEPT),
            "parent_region_id": kwargs.get("parent_region_id", "cosmology"),
            "proposed_label": label,
            "normalized_label": kwargs.get(
                "normalized_label", schema.normalize_label(label)
            ),
            "reason": kwargs.get("reason", "この論文の主題を置ける概念が見当たらない。"),
            "evidence_quote": kwargs.get("evidence_quote", "the cosmic web"),
            "confidence": kwargs.get("confidence", 0.8),
            "status": kwargs.get("status", schema.SIGNAL_STATUS_ACTIVE),
            "created_at": kwargs.get("created_at") or self._tick(),
            "updated_at": self._tick(),
        }
        self.signals.append(row)
        return row

    def seed_decision(self, **kwargs) -> dict:
        row = {
            "id": kwargs.get("id") or f"dec-{next(self._id_seq)}",
            "cluster_key": kwargs["cluster_key"],
            "status": kwargs.get("status", schema.DECISION_STATUS_CANDIDATE),
            "review_note": kwargs.get("review_note", ""),
            "merged_into": kwargs.get("merged_into", ""),
            "draft_node_id": kwargs.get("draft_node_id", ""),
            "applied_version": kwargs.get("applied_version", ""),
            "decided_by": kwargs.get("decided_by", _TEACHER),
            "decided_at": kwargs.get("decided_at") or self._tick(),
            "created_at": self._tick(),
            "updated_at": self._tick(),
        }
        self.decisions.append(row)
        return row

    # -- ディスパッチ先（signals）--
    def _supersede_signals(self, p: dict) -> _FakeResult:
        for row in self.signals:
            if row["document_id"] == p["document_id"] and row["status"] in set(
                p["supersedable"]
            ):
                row["status"] = p["superseded"]
                row["updated_at"] = self._tick()
        return _FakeResult()

    def _insert_signal(self, p: dict) -> _FakeResult:
        row = dict(p)
        row["id"] = f"sig-{next(self._id_seq)}"
        row["created_at"] = self._tick()
        row["updated_at"] = self._tick()
        self.signals.append(row)
        return _FakeResult()

    def _select_signals(self, sql: str, p: dict) -> _FakeResult:
        rows = [r for r in self.signals if r["status"] == p["active"]]
        if "domain_key" in p:
            rows = [r for r in rows if r["domain_key"] == p["domain_key"]]
        if "document_id" in p:
            rows = [r for r in rows if r["document_id"] == p["document_id"]]
        rows.sort(
            key=lambda r: (
                r["layer"],
                r["parent_region_id"],
                r["normalized_label"],
                # created_at DESC, id
                datetime.max.replace(tzinfo=timezone.utc) - r["created_at"],
                r["id"],
            )
        )
        with_titles = "LEFT JOIN documents" in sql
        return _FakeResult(
            [
                self._signal_tuple(
                    r,
                    with_title=(self.titles.get(r["document_id"], "") if with_titles else None),
                )
                for r in rows
            ]
        )

    # -- ディスパッチ先（decisions）--
    def _find_decision(self, cluster_key: str) -> dict | None:
        for row in self.decisions:
            if row["cluster_key"] == cluster_key:
                return row
        return None

    def _upsert_decision(self, p: dict) -> _FakeResult:
        existing = self._find_decision(p["cluster_key"])
        if existing is None:
            row = self.seed_decision(
                cluster_key=p["cluster_key"],
                status=p["status"],
                review_note=p["review_note"],
                merged_into=p["merged_into"],
                decided_by=p["decided_by"],
            )
            return _FakeResult([self._decision_tuple(row)])
        existing["status"] = p["status"]
        if p["review_note"]:
            existing["review_note"] = p["review_note"]
        if p["merged_into"]:
            existing["merged_into"] = p["merged_into"]
        existing["decided_by"] = p["decided_by"]
        existing["decided_at"] = self._tick()
        existing["updated_at"] = self._tick()
        return _FakeResult([self._decision_tuple(existing)])

    def _restore_decision(self, p: dict) -> _FakeResult:
        existing = self._find_decision(p["cluster_key"])
        if existing is None:
            return _FakeResult()
        existing["status"] = p["candidate"]
        existing["decided_by"] = p["decided_by"]
        existing["decided_at"] = self._tick()
        existing["updated_at"] = self._tick()
        return _FakeResult([self._decision_tuple(existing)])

    def _mark_incorporated(self, p: dict) -> _FakeResult:
        existing = self._find_decision(p["cluster_key"])
        if existing is None or existing["status"] != p["accepted"]:
            return _FakeResult()
        existing["draft_node_id"] = p["draft_node_id"]
        existing["updated_at"] = self._tick()
        return _FakeResult([self._decision_tuple(existing)])

    def _stamp(self, p: dict) -> _FakeResult:
        touched = []
        for row in self.decisions:
            if not row["cluster_key"].startswith(p["domain_prefix"]):
                continue
            if row["status"] != p["accepted"] or row["applied_version"] != "":
                continue
            if not row["draft_node_id"] or row["draft_node_id"] not in set(p["node_ids"]):
                continue
            row["applied_version"] = p["version"]
            row["updated_at"] = self._tick()
            touched.append((row["cluster_key"],))
        return _FakeResult(touched)

    def _select_decisions(self, sql: str, p: dict) -> _FakeResult:
        if "cluster_key = ANY(:keys)" in sql:
            keys = set(p["keys"])
            rows = [r for r in self.decisions if r["cluster_key"] in keys]
        elif "cluster_key = :cluster_key" in sql:
            rows = [r for r in self.decisions if r["cluster_key"] == p["cluster_key"]]
        else:
            rows = [
                r
                for r in self.decisions
                if r["cluster_key"].startswith(p["domain_prefix"])
                and r["status"] == p["accepted"]
                and r["applied_version"] == ""
            ]
            rows.sort(key=lambda r: r["cluster_key"])
        return _FakeResult([self._decision_tuple(r) for r in rows])

    def _insert_audit(self, p: dict) -> _FakeResult:
        self.audits.append(dict(p))
        return _FakeResult()


@pytest.fixture
def db():
    return FakeGapSession()


class _Gap:
    """agent 側 ``CategoryGapRecord`` 相当（属性アクセスの契約だけを持つ）。"""

    def __init__(self, **kwargs):
        self.layer = kwargs.get("layer", schema.GAP_LAYER_CONCEPT)
        self.domain_key = kwargs.get("domain_key", _DOMAIN)
        self.parent_region_id = kwargs.get("parent_region_id", "cosmology")
        self.proposed_label = kwargs.get("proposed_label", "Cosmic Web")
        self.reason = kwargs.get("reason", "この地図では言い表せなかった主題がある。")
        self.evidence_quote = kwargs.get("evidence_quote", "the cosmic web")
        self.confidence = kwargs.get("confidence", 0.7)


def _skeleton(*, concepts=("cmb",), regions=("cosmology", "galaxies")) -> atlas.AtlasSkeleton:
    return atlas.AtlasSkeleton(
        cartridge=_DOMAIN,
        status=atlas.STATUS_FROZEN,
        version="2026.1",
        generated_by="reference_map:test",
        reviewed_by=("faculty:t",),
        changelog=(atlas.ChangelogEntry(version="2026.1", note="t"),),
        regions=tuple(
            atlas.SkeletonRegion(
                id=region_id,
                label={"cosmology": "宇宙論・大規模構造", "galaxies": "銀河・銀河団"}.get(
                    region_id, region_id
                ),
                concepts=tuple(
                    atlas.SkeletonConcept(id=c, label=c.upper()) for c in concepts
                )
                if region_id == "cosmology"
                else (),
            )
            for region_id in regions
        ),
    )


def _all_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(str(k))
            keys |= _all_keys(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            keys |= _all_keys(v)
    return keys


# ---------------------------------------------------------------------------
# 1. 信号の投入（§5.1 / LS3 同型）
# ---------------------------------------------------------------------------


class TestRecordSignals:
    def test_inserts_active_signals(self, db):
        created = store.record_signals(
            db,
            document_id=_DOC1,
            run_id=_RUN,
            gaps=[_Gap(), _Gap(proposed_label="Void Statistics")],
            skeleton_versions={_DOMAIN: "2026.1"},
        )
        assert created == 2
        assert {r["status"] for r in db.signals} == {schema.SIGNAL_STATUS_ACTIVE}
        assert {r["skeleton_version"] for r in db.signals} == {"2026.1"}
        assert {r["run_id"] for r in db.signals} == {_RUN}
        assert db.signals[0]["normalized_label"] == "cosmic web"
        # store はトランザクションを触らない（呼び出し側の責務）
        assert (db.commits, db.rollbacks, db.closed) == (0, 0, False)

    def test_empty_input_touches_no_sql(self, db):
        assert store.record_signals(db, document_id=_DOC1, run_id=_RUN, gaps=[]) == 0
        assert db.calls == []

    def test_all_dropped_input_touches_no_sql(self, db):
        """全件 drop でも SQL を発行しない（生きた信号を消しに行かない）。"""
        assert (
            store.record_signals(
                db,
                document_id=_DOC1,
                run_id=_RUN,
                gaps=[_Gap(proposed_label=""), _Gap(layer="galaxy")],
            )
            == 0
        )
        assert db.calls == []

    def test_reanalysis_supersedes_active_rows_of_the_same_document(self, db):
        old = db.seed_signal(document_id=_DOC1, proposed_label="Old Subject")
        other = db.seed_signal(document_id=_DOC2, proposed_label="Other Subject")
        store.record_signals(
            db, document_id=_DOC1, run_id=_RUN, gaps=[_Gap()],
        )
        assert old["status"] == schema.SIGNAL_STATUS_SUPERSEDED
        # 他 document は触らない
        assert other["status"] == schema.SIGNAL_STATUS_ACTIVE
        # 履歴は削除されない（P4）
        assert len(db.signals) == 3

    def test_no_delete_statements_are_issued(self, db):
        db.seed_signal()
        store.record_signals(db, document_id=_DOC1, run_id=_RUN, gaps=[_Gap()])
        assert not any("DELETE" in sql.upper() for sql, _ in db.calls)

    def test_limit_is_applied_first_wins(self, db):
        created = store.record_signals(
            db,
            document_id=_DOC1,
            run_id=_RUN,
            gaps=[
                _Gap(proposed_label="A"),
                _Gap(proposed_label="B"),
                _Gap(proposed_label="C"),
                _Gap(proposed_label="D"),
            ],
            max_signals=2,
        )
        assert created == 2
        assert [r["proposed_label"] for r in db.signals] == ["A", "B"]

    def test_zero_limit_stores_nothing(self, db):
        assert (
            store.record_signals(
                db, document_id=_DOC1, run_id=_RUN, gaps=[_Gap()], max_signals=0
            )
            == 0
        )
        assert db.calls == []

    def test_duplicates_within_a_document_are_collapsed(self, db):
        created = store.record_signals(
            db,
            document_id=_DOC1,
            run_id=_RUN,
            gaps=[_Gap(proposed_label="Cosmic Web"), _Gap(proposed_label="cosmic  web")],
        )
        assert created == 1

    def test_invalid_entries_are_dropped_without_raising(self, db):
        """1件の語彙違反で配置の保存まで巻き戻さない（soft collector の維持）。"""
        created = store.record_signals(
            db,
            document_id=_DOC1,
            run_id=_RUN,
            gaps=[
                _Gap(),
                _Gap(domain_key="", proposed_label="No Domain"),
                _Gap(layer="", proposed_label="No Layer"),
                _Gap(layer="galaxy", proposed_label="Bad Layer"),
                _Gap(proposed_label="   "),
            ],
        )
        assert created == 1
        assert [r["proposed_label"] for r in db.signals] == ["Cosmic Web"]

    def test_region_layer_normalizes_parent_to_empty(self, db):
        store.record_signals(
            db,
            document_id=_DOC1,
            run_id=_RUN,
            gaps=[
                _Gap(
                    layer=schema.GAP_LAYER_REGION,
                    parent_region_id="cosmology",
                    proposed_label="重力波天文学",
                )
            ],
        )
        assert db.signals[0]["parent_region_id"] == ""

    def test_accepts_mapping_shaped_gaps(self, db):
        created = store.record_signals(
            db,
            document_id=_DOC1,
            run_id=_RUN,
            gaps=[
                {
                    "layer": "concept",
                    "domain_key": _DOMAIN,
                    "parent_region_id": "cosmology",
                    "proposed_label": "Cosmic Web",
                    "reason": "r",
                    "evidence_quote": "q",
                    "confidence": 0.6,
                }
            ],
        )
        assert created == 1
        assert db.signals[0]["confidence"] == pytest.approx(0.6)

    def test_unmeasured_confidence_stays_null(self, db):
        store.record_signals(
            db, document_id=_DOC1, run_id=_RUN, gaps=[_Gap(confidence=None)]
        )
        assert db.signals[0]["confidence"] is None


# ---------------------------------------------------------------------------
# 2. 監査（§5.7）
# ---------------------------------------------------------------------------


class TestDetectAudit:
    def test_records_a_detect_event_without_an_actor(self, db):
        store.record_detect_audit(
            db, document_id=_DOC1, run_id=_RUN, created=2, domain_keys=[_DOMAIN, ""]
        )
        assert len(db.audits) == 1
        event = db.audits[0]
        assert event["entity_type"] == "category_gap"
        assert event["entity_id"] == _DOC1
        assert event["changed_by"] is None
        assert event["new_status"] == schema.DECISION_STATUS_CANDIDATE
        assert '"action": "detect"' in event["metadata"]
        assert _DOMAIN in event["metadata"]


# ---------------------------------------------------------------------------
# 3. 候補の読み時導出（§4.1 / §4.3）
# ---------------------------------------------------------------------------


def _seed_repeated(db, *, label="Cosmic Web", documents=(_DOC1, _DOC2), **kwargs):
    for document_id in documents:
        db.seed_signal(document_id=document_id, proposed_label=label, **kwargs)


class TestDeriveCandidates:
    def test_single_document_signal_does_not_surface(self, db):
        db.seed_signal(document_id=_DOC1)
        assert (
            store.derive_candidates(
                db, domain_key=_DOMAIN, frozen_skeleton=_skeleton(), current_version="2026.1"
            )
            == []
        )

    def test_two_documents_surface_one_cluster(self, db):
        db.titles = {_DOC1: "論文A", _DOC2: "論文B"}
        _seed_repeated(db)
        clusters = store.derive_candidates(
            db, domain_key=_DOMAIN, frozen_skeleton=_skeleton(), current_version="2026.1"
        )
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster["cluster_key"] == schema.build_cluster_key(
            _DOMAIN, "cosmology", "Cosmic Web"
        )
        assert cluster["layer"] == schema.GAP_LAYER_CONCEPT
        assert cluster["parent_region_label"] == "宇宙論・大規模構造"
        assert cluster["parent_region_known"] is True
        assert {d["title"] for d in cluster["documents"]} == {"論文A", "論文B"}
        assert cluster["decision"] is None
        assert cluster["version_mismatch"] is False

    def test_label_variants_group_into_one_cluster(self, db):
        db.seed_signal(document_id=_DOC1, proposed_label="Cosmic  Web")
        db.seed_signal(document_id=_DOC2, proposed_label="cosmic web")
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton())
        assert len(clusters) == 1
        assert len(clusters[0]["documents"]) == 2

    def test_resolved_by_the_current_map_disappears(self, db):
        """次版に概念が入れば候補は自然消滅する（完了フラグを持たない・G1）。"""
        _seed_repeated(db, label="CMB Lensing")
        skeleton = _skeleton(concepts=("cmb_lensing",))
        # id 一致でも解消（ラベル・id の両方を突合する）
        assert (
            store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=skeleton) == []
        )

    def test_resolved_by_label_match_disappears(self, db):
        _seed_repeated(db, label="銀河・銀河団")
        assert (
            store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton()) == []
        )

    def test_dismissed_cluster_is_suppressed_but_restorable(self, db):
        _seed_repeated(db)
        key = schema.build_cluster_key(_DOMAIN, "cosmology", "Cosmic Web")
        db.seed_decision(
            cluster_key=key,
            status=schema.DECISION_STATUS_DISMISSED,
            review_note="既存概念の言い換えのため",
        )
        assert store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton()) == []
        shown = store.derive_candidates(
            db, domain_key=_DOMAIN, frozen_skeleton=_skeleton(), include_dismissed=True
        )
        assert len(shown) == 1
        assert shown[0]["decision"]["status"] == schema.DECISION_STATUS_DISMISSED
        assert shown[0]["decision"]["review_note"] == "既存概念の言い換えのため"

    def test_accepted_cluster_stays_visible(self, db):
        _seed_repeated(db)
        db.seed_decision(
            cluster_key=schema.build_cluster_key(_DOMAIN, "cosmology", "Cosmic Web"),
            status=schema.DECISION_STATUS_ACCEPTED,
        )
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton())
        assert clusters[0]["decision"]["status"] == schema.DECISION_STATUS_ACCEPTED

    def test_merged_cluster_is_suppressed_by_default(self, db):
        _seed_repeated(db)
        db.seed_decision(
            cluster_key=schema.build_cluster_key(_DOMAIN, "cosmology", "Cosmic Web"),
            status=schema.DECISION_STATUS_MERGED,
            merged_into="gap|astrophysics|cosmology|large scale structure",
        )
        assert store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton()) == []

    def test_version_mismatch_is_reported_per_document(self, db):
        db.seed_signal(document_id=_DOC1, skeleton_version="2026.1")
        db.seed_signal(document_id=_DOC2, skeleton_version="2025.4")
        clusters = store.derive_candidates(
            db, domain_key=_DOMAIN, frozen_skeleton=_skeleton(), current_version="2026.1"
        )
        assert clusters[0]["version_mismatch"] is True
        mismatches = {
            d["document_id"]: d["version_mismatch"] for d in clusters[0]["documents"]
        }
        assert mismatches == {_DOC1: False, _DOC2: True}

    def test_without_current_version_nothing_is_a_mismatch(self, db):
        db.seed_signal(document_id=_DOC1, skeleton_version="2025.4")
        db.seed_signal(document_id=_DOC2, skeleton_version="2026.1")
        clusters = store.derive_candidates(
            db, domain_key=_DOMAIN, frozen_skeleton=_skeleton(), current_version=""
        )
        assert clusters[0]["version_mismatch"] is False

    def test_full_parent_region_is_flagged(self, db):
        _seed_repeated(db)
        full = _skeleton(concepts=tuple(f"c{i}" for i in range(atlas.MAX_CONCEPTS_PER_REGION)))
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=full)
        assert clusters[0]["parent_region_at_capacity"] is True

    def test_unknown_parent_region_is_reported_without_guessing(self, db):
        _seed_repeated(db, parent_region_id="retired_region")
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton())
        assert clusters[0]["parent_region_known"] is False
        assert clusters[0]["parent_region_label"] == ""
        assert clusters[0]["parent_region_at_capacity"] is False

    def test_no_raw_confidence_and_no_count_fields(self, db):
        """LS5: 生値も「該当論文 N 件」も出さない（支持論文はリストで示す）。"""
        _seed_repeated(db)
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton())
        keys = _all_keys(clusters)
        assert not keys & set(schema.FORBIDDEN_NUMERIC_KEYS)
        assert not [k for k in keys if k.endswith("_count") or k == "count"]
        assert clusters[0]["documents"][0]["confidence_label"] in schema.CONFIDENCE_LABELS

    def test_signals_from_other_domains_are_ignored(self, db):
        _seed_repeated(db)
        db.seed_signal(document_id=_DOC3, domain_key="particle_physics")
        db.seed_signal(document_id=_DOC1, domain_key="particle_physics")
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton())
        assert {c["domain_key"] for c in clusters} == {_DOMAIN}

    def test_superseded_signals_are_not_counted(self, db):
        db.seed_signal(document_id=_DOC1)
        db.seed_signal(document_id=_DOC2, status=schema.SIGNAL_STATUS_SUPERSEDED)
        assert store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton()) == []

    def test_empty_domain_key_touches_no_sql(self, db):
        assert store.derive_candidates(db, domain_key="") == []
        assert db.calls == []

    def test_works_without_a_skeleton(self, db):
        """骨格が読めなくても候補は導出できる（解消済み判定だけが効かない）。"""
        _seed_repeated(db)
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=None)
        assert len(clusters) == 1
        assert clusters[0]["parent_region_known"] is False

    def test_accepts_dict_shaped_skeleton(self, db):
        _seed_repeated(db, label="銀河・銀河団")
        as_dict = atlas.skeleton_to_dict(_skeleton())
        assert store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=as_dict) == []

    def test_ordering_is_deterministic(self, db):
        _seed_repeated(db, label="Void Statistics")
        _seed_repeated(db, label="Cosmic Web")
        _seed_repeated(db, label="重力波天文学", layer=schema.GAP_LAYER_REGION,
                       parent_region_id="")
        clusters = store.derive_candidates(db, domain_key=_DOMAIN, frozen_skeleton=_skeleton())
        assert [c["proposed_label"] for c in clusters] == [
            "Cosmic Web",
            "Void Statistics",
            "重力波天文学",
        ]


# ---------------------------------------------------------------------------
# 4. 教員の判断
# ---------------------------------------------------------------------------


_KEY = schema.build_cluster_key(_DOMAIN, "cosmology", "Cosmic Web")


class TestDecisions:
    def test_accept_creates_a_row(self, db):
        decision = store.upsert_decision(
            db,
            cluster_key=_KEY,
            status=schema.DECISION_STATUS_ACCEPTED,
            decided_by=_TEACHER,
        )
        assert decision["status"] == schema.DECISION_STATUS_ACCEPTED
        assert decision["decided_by"] == _TEACHER
        assert decision["applied_version"] == ""
        assert decision["draft_node_id"] == ""
        assert decision["status_label"] == "採用"

    def test_upsert_is_idempotent_on_cluster_key(self, db):
        store.upsert_decision(
            db, cluster_key=_KEY, status=schema.DECISION_STATUS_ACCEPTED,
            decided_by=_TEACHER,
        )
        store.upsert_decision(
            db, cluster_key=_KEY, status=schema.DECISION_STATUS_DISMISSED,
            decided_by=_TEACHER, review_note="やはり既存概念で表せる",
        )
        assert len(db.decisions) == 1
        assert db.decisions[0]["status"] == schema.DECISION_STATUS_DISMISSED

    def test_dismiss_requires_a_reason(self, db):
        with pytest.raises(ValueError):
            store.upsert_decision(
                db, cluster_key=_KEY, status=schema.DECISION_STATUS_DISMISSED,
                decided_by=_TEACHER,
            )
        with pytest.raises(ValueError):
            store.upsert_decision(
                db, cluster_key=_KEY, status=schema.DECISION_STATUS_DISMISSED,
                decided_by=_TEACHER, review_note="   ",
            )
        assert db.decisions == []
        assert db.calls == []

    def test_merge_requires_a_target(self, db):
        with pytest.raises(ValueError):
            store.upsert_decision(
                db, cluster_key=_KEY, status=schema.DECISION_STATUS_MERGED,
                decided_by=_TEACHER,
            )
        assert db.calls == []

    @pytest.mark.parametrize("bad", ["approved", "", "APPROVED", "candidate "])
    def test_unknown_status_is_rejected(self, db, bad):
        with pytest.raises(ValueError):
            store.upsert_decision(
                db, cluster_key=_KEY, status=bad, decided_by=_TEACHER
            )
        assert db.calls == []

    def test_attribution_is_required(self, db):
        with pytest.raises(ValueError):
            store.upsert_decision(
                db, cluster_key=_KEY, status=schema.DECISION_STATUS_ACCEPTED,
                decided_by="",
            )
        with pytest.raises(ValueError):
            store.upsert_decision(
                db, cluster_key="", status=schema.DECISION_STATUS_ACCEPTED,
                decided_by=_TEACHER,
            )
        assert db.calls == []

    def test_empty_note_keeps_the_previous_reason(self, db):
        db.seed_decision(
            cluster_key=_KEY,
            status=schema.DECISION_STATUS_DISMISSED,
            review_note="前の理由",
        )
        updated = store.upsert_decision(
            db, cluster_key=_KEY, status=schema.DECISION_STATUS_ACCEPTED,
            decided_by=_TEACHER,
        )
        assert updated["review_note"] == "前の理由"

    def test_restore_is_a_transition_not_a_deletion(self, db):
        db.seed_decision(
            cluster_key=_KEY,
            status=schema.DECISION_STATUS_DISMISSED,
            review_note="一度見送った理由",
        )
        restored = store.restore_decision(db, cluster_key=_KEY, decided_by=_TEACHER)
        assert restored["status"] == schema.DECISION_STATUS_CANDIDATE
        # 見送った理由は履歴として残す（P4）
        assert restored["review_note"] == "一度見送った理由"
        assert len(db.decisions) == 1
        assert not any("DELETE" in sql.upper() for sql, _ in db.calls)

    def test_restore_of_a_missing_row_returns_none(self, db):
        assert store.restore_decision(db, cluster_key=_KEY, decided_by=_TEACHER) is None

    def test_restore_requires_attribution(self, db):
        db.seed_decision(cluster_key=_KEY, status=schema.DECISION_STATUS_DISMISSED)
        with pytest.raises(ValueError):
            store.restore_decision(db, cluster_key=_KEY, decided_by="")

    def test_get_decision(self, db):
        db.seed_decision(cluster_key=_KEY, status=schema.DECISION_STATUS_ACCEPTED)
        assert store.get_decision(db, _KEY)["status"] == schema.DECISION_STATUS_ACCEPTED
        assert store.get_decision(db, "gap|x|y|z") is None
        assert store.get_decision(db, "") is None


class TestIncorporationAndFreeze:
    def test_mark_incorporated_only_touches_accepted_rows(self, db):
        db.seed_decision(cluster_key=_KEY, status=schema.DECISION_STATUS_CANDIDATE)
        assert store.mark_incorporated(db, cluster_key=_KEY, draft_node_id="cosmic_web") is None
        db.decisions[0]["status"] = schema.DECISION_STATUS_ACCEPTED
        updated = store.mark_incorporated(db, cluster_key=_KEY, draft_node_id="cosmic_web")
        assert updated["draft_node_id"] == "cosmic_web"
        # 取り込みだけでは反映（版の刻印）にならない
        assert updated["applied_version"] == ""

    def test_mark_incorporated_requires_a_node_id(self, db):
        with pytest.raises(ValueError):
            store.mark_incorporated(db, cluster_key=_KEY, draft_node_id="")

    def test_stamp_applied_versions_only_for_nodes_that_exist(self, db):
        db.seed_decision(
            cluster_key=_KEY,
            status=schema.DECISION_STATUS_ACCEPTED,
            draft_node_id="cosmic_web",
        )
        other = schema.build_cluster_key(_DOMAIN, "cosmology", "Void Statistics")
        db.seed_decision(
            cluster_key=other,
            status=schema.DECISION_STATUS_ACCEPTED,
            draft_node_id="void_statistics",
        )
        not_incorporated = schema.build_cluster_key(_DOMAIN, "cosmology", "Filaments")
        db.seed_decision(
            cluster_key=not_incorporated, status=schema.DECISION_STATUS_ACCEPTED
        )

        stamped = store.stamp_applied_versions(
            db,
            domain_key=_DOMAIN,
            frozen_version="2026.2",
            frozen_node_ids=["cosmic_web", "cmb"],
        )
        assert stamped == [_KEY]
        by_key = {d["cluster_key"]: d for d in db.decisions}
        assert by_key[_KEY]["applied_version"] == "2026.2"
        assert by_key[other]["applied_version"] == ""
        assert by_key[not_incorporated]["applied_version"] == ""

    def test_stamp_never_touches_other_domains(self, db):
        foreign = schema.build_cluster_key("astrophysics_extra", "cosmology", "Cosmic Web")
        db.seed_decision(
            cluster_key=foreign,
            status=schema.DECISION_STATUS_ACCEPTED,
            draft_node_id="cosmic_web",
        )
        assert (
            store.stamp_applied_versions(
                db, domain_key=_DOMAIN, frozen_version="2026.2",
                frozen_node_ids=["cosmic_web"],
            )
            == []
        )
        assert db.decisions[0]["applied_version"] == ""

    @pytest.mark.parametrize(
        "domain,version,nodes",
        [("", "2026.2", ["a"]), (_DOMAIN, "", ["a"]), (_DOMAIN, "2026.2", []),
         (_DOMAIN, "2026.2", ["", "  "])],
    )
    def test_stamp_is_fail_closed_on_empty_input(self, db, domain, version, nodes):
        db.seed_decision(
            cluster_key=_KEY, status=schema.DECISION_STATUS_ACCEPTED,
            draft_node_id="cosmic_web",
        )
        assert (
            store.stamp_applied_versions(
                db, domain_key=domain, frozen_version=version, frozen_node_ids=nodes
            )
            == []
        )
        assert db.calls == []
        assert db.decisions[0]["applied_version"] == ""

    def test_pending_for_freeze_lists_labels(self, db):
        _seed_repeated(db)
        db.seed_decision(
            cluster_key=_KEY,
            status=schema.DECISION_STATUS_ACCEPTED,
            draft_node_id="cosmic_web",
        )
        # draft に node がまだ無い = 未反映
        pending = store.list_pending_for_freeze(db, domain_key=_DOMAIN, draft_node_ids=[])
        assert [p["proposed_label"] for p in pending] == ["Cosmic Web"]
        assert pending[0]["layer"] == schema.GAP_LAYER_CONCEPT
        assert pending[0]["draft_node_id"] == "cosmic_web"
        # 件数フィールドは作らない（ラベル列挙で示す）
        assert not [k for k in _all_keys(pending) if k.endswith("_count")]

        # draft に入っていれば未反映ではない
        assert (
            store.list_pending_for_freeze(
                db, domain_key=_DOMAIN, draft_node_ids=["cosmic_web"]
            )
            == []
        )

    def test_pending_includes_accepted_without_incorporation(self, db):
        db.seed_decision(cluster_key=_KEY, status=schema.DECISION_STATUS_ACCEPTED)
        pending = store.list_pending_for_freeze(
            db, domain_key=_DOMAIN, draft_node_ids=["anything"]
        )
        assert len(pending) == 1
        # 生きた信号が無ければ cluster_key から復元した正規化ラベルへ縮退する
        assert pending[0]["proposed_label"] == "cosmic web"

    def test_pending_excludes_dismissed_and_applied(self, db):
        db.seed_decision(
            cluster_key=_KEY, status=schema.DECISION_STATUS_DISMISSED,
            review_note="見送り",
        )
        db.seed_decision(
            cluster_key=schema.build_cluster_key(_DOMAIN, "cosmology", "Void"),
            status=schema.DECISION_STATUS_ACCEPTED,
            draft_node_id="void",
            applied_version="2026.2",
        )
        assert store.list_pending_for_freeze(db, domain_key=_DOMAIN, draft_node_ids=[]) == []

    def test_pending_empty_domain_touches_no_sql(self, db):
        assert store.list_pending_for_freeze(db, domain_key="", draft_node_ids=[]) == []
        assert db.calls == []


# ---------------------------------------------------------------------------
# 5. 信号の読み出し
# ---------------------------------------------------------------------------


class TestListActiveSignals:
    def test_returns_only_active_rows(self, db):
        db.seed_signal(document_id=_DOC1)
        db.seed_signal(document_id=_DOC2, status=schema.SIGNAL_STATUS_SUPERSEDED)
        rows = store.list_active_signals(db, domain_key=_DOMAIN)
        assert [r["document_id"] for r in rows] == [_DOC1]

    def test_filters_by_document(self, db):
        db.seed_signal(document_id=_DOC1)
        db.seed_signal(document_id=_DOC2, proposed_label="Void Statistics")
        rows = store.list_active_signals(db, document_id=_DOC2)
        assert [r["proposed_label"] for r in rows] == ["Void Statistics"]
