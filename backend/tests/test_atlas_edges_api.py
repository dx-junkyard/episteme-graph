"""分野マップの関係表示（RE層）— 管理 API 層のテスト。

対象:
  - ``backend/api/routes/atlas_edges.py``（辺候補のレビューキュー / 判断 /
    次版下書きへの反映プレビュー・刻印）
  - ``backend/api/routes/atlas.py`` の追加分（freeze の公開前チェックと刻印）
  - ``backend/api/routes/atlas_view.py`` の追加分（``/api/atlas`` への糸の fail-soft
    マージ。振る舞いの検査は ``tests/api/test_atlas_view_api.py`` 側にある）

正本: ``docs/features/atlas_relation_edges_design.md`` §5 / §6 / §9（不変条項は §2）。

流儀は ``tests/test_atlas_gaps_api.py``（実 app + TestClient + フェイクセッション）と
同じ:

- 判断の SQL 面は本ファイルの :class:`FakeEdgeSession`（SQL 本文で分岐するインメモリ
  実装）に当て、**store の実装を通して**検証する（route が store の契約を守っているかを
  見たいため）。骨格の読み出し（``atlas_store``）だけを monkeypatch する。
- 骨格の書き込みを伴う freeze は ``AtlasSkeletonTableFake`` を使う。
"""

from __future__ import annotations

import itertools
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core import atlas  # noqa: E402
from core.atlas_edges import schema as edge_schema  # noqa: E402
from core.schema import AUDIT_ENTITY_ATLAS_EDGE  # noqa: E402
from tests.fixtures.atlas_skeletons_fake import (  # noqa: E402
    AtlasSkeletonTableFake,
    make_session_factory,
)
from tests.guardrail_helpers import (  # noqa: E402
    collect_route_pairs,
    extract_function_source,
)

_DOMAIN = "astrophysics"
_DOC1 = "11111111-1111-1111-1111-111111111111"
_DOC2 = "22222222-2222-2222-2222-222222222222"
_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_STUDENT_ID = "88888888-8888-8888-8888-888888888888"

#: 候補になる無向ペア（別領域の概念同士）。
_FROM, _TO = "cmb", "agn"
_EDGE = edge_schema.build_edge_key(_DOMAIN, _FROM, _TO)

#: 結線の静的検査用（ルーターの実ソース）。
_ATLAS_SRC = (BACKEND / "api" / "routes" / "atlas.py").read_text(encoding="utf-8")
_ATLAS_VIEW_SRC = (BACKEND / "api" / "routes" / "atlas_view.py").read_text(
    encoding="utf-8"
)

_QUEUE_PATH = f"/api/admin/cartridges/{_DOMAIN}/atlas/edge-candidates"
_DECIDE_PATH = _QUEUE_PATH + "/decide"
_PREVIEW_PATH = _QUEUE_PATH + "/incorporate-preview"
_MARK_PATH = _QUEUE_PATH + "/mark-incorporated"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def _keys_recursive(payload) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _keys_recursive(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys_recursive(item)
    return found


# ---------------------------------------------------------------------------
# テストダブル
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeEdgeSession:
    """``atlas_edge_decisions`` / ``landscape_placements`` のインメモリ実装。"""

    def __init__(self):
        self.decisions: list[dict] = []
        self.placements: list[dict] = []
        self.titles: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._id_seq = itertools.count(1)
        self._clock = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)

    # -- セッションインターフェース --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        if "FROM landscape_placements" in sql:
            return self._select_placements(params)
        if sql.startswith("INSERT INTO atlas_edge_decisions"):
            return self._insert_decision(params)
        if sql.startswith("UPDATE atlas_edge_decisions SET status"):
            return self._transition(params)
        if sql.startswith("UPDATE atlas_edge_decisions SET applied_version"):
            return self._stamp(params)
        if sql.startswith("SELECT") and "FROM atlas_edge_decisions" in sql:
            return self._select_decisions(sql, params)
        raise AssertionError(f"unhandled atlas_edges SQL: {sql!r}")

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
    def _decision_tuple(row: dict) -> tuple:
        return (
            row["id"], row["edge_key"], row["status"], row["edge_kind"],
            row["review_note"], row["applied_version"], row["decided_by"],
            row["decided_at"], row["created_at"], row["updated_at"],
        )

    def seed_decision(self, **kwargs) -> dict:
        row = {
            "id": kwargs.get("id") or f"dec-{next(self._id_seq)}",
            "edge_key": kwargs.get("edge_key", _EDGE),
            "status": kwargs.get("status", edge_schema.DECISION_STATUS_CANDIDATE),
            "edge_kind": kwargs.get("edge_kind", ""),
            "review_note": kwargs.get("review_note", ""),
            "applied_version": kwargs.get("applied_version", ""),
            "decided_by": kwargs.get("decided_by", _TEACHER_ID),
            "decided_at": kwargs.get("decided_at") or self._tick(),
            "created_at": self._tick(),
            "updated_at": self._tick(),
        }
        self.decisions.append(row)
        return row

    def seed_placement(self, *, node_id: str, document_id: str, title: str = "") -> None:
        self.placements.append(
            {
                "node_id": node_id,
                "document_id": document_id,
                "title": title or f"論文 {document_id[:4]}",
            }
        )

    def write_calls(self) -> list[str]:
        return [sql for sql, _p in self.calls if sql.startswith(("INSERT", "UPDATE"))]

    # -- クエリ --
    def _select_placements(self, params) -> FakeResult:
        rows = [
            (p["document_id"], p["title"], p["node_id"])
            for p in self.placements
            if p.get("domain_key", _DOMAIN) == params.get("domain_key")
        ]
        return FakeResult(sorted(rows, key=lambda r: (r[2], r[1], r[0])))

    def _select_decisions(self, sql: str, params) -> FakeResult:
        if "edge_key = ANY(:keys)" in sql:
            keys = set(params.get("keys") or [])
            rows = [r for r in self.decisions if r["edge_key"] in keys]
        elif "edge_key = :edge_key" in sql:
            rows = [r for r in self.decisions if r["edge_key"] == params.get("edge_key")]
        else:  # list_pending_for_freeze / dismissed_edge_keys
            prefix = params.get("domain_prefix") or ""
            rows = [
                r
                for r in self.decisions
                if r["edge_key"].startswith(prefix)
                and (
                    "status" not in params
                    or r["status"] == params.get("status")
                )
                and (
                    "accepted" not in params
                    or (
                        r["status"] == params["accepted"]
                        and r["applied_version"] == ""
                    )
                )
            ]
        return FakeResult([self._decision_tuple(r) for r in rows])

    def _insert_decision(self, params) -> FakeResult:
        key = params["edge_key"]
        if not any(r["edge_key"] == key for r in self.decisions):
            self.seed_decision(edge_key=key, status=params["candidate"], decided_by=None)
        return FakeResult()

    def _transition(self, params) -> FakeResult:
        for row in self.decisions:
            if (
                row["edge_key"] == params["edge_key"]
                and row["status"] == params["old_status"]
            ):
                row["status"] = params["new_status"]
                if params.get("edge_kind"):
                    row["edge_kind"] = params["edge_kind"]
                if params.get("review_note"):
                    row["review_note"] = params["review_note"]
                row["decided_by"] = params.get("actor_id") or None
                row["decided_at"] = self._tick()
                row["updated_at"] = self._tick()
                return FakeResult([self._decision_tuple(row)])
        return FakeResult()

    def _stamp(self, params) -> FakeResult:
        keys = set(params.get("keys") or [])
        stamped = []
        for row in self.decisions:
            if (
                row["edge_key"] in keys
                and row["status"] == params["accepted"]
                and row["applied_version"] == ""
            ):
                row["applied_version"] = params["version"]
                stamped.append((row["edge_key"],))
        return FakeResult(stamped)


def _skeleton(
    *,
    status: str = "frozen",
    version: str = "2026.1",
    edges: tuple = (),
) -> atlas.AtlasSkeleton:
    """概念 cmb（宇宙論）と agn（銀河）を持つ最小骨格（別領域 = 候補になれる）。"""
    regions = (
        atlas.SkeletonRegion(
            id="cosmology",
            label="宇宙論・大規模構造",
            layout=atlas.RegionLayout(x=0.02, y=0.03, w=0.23, h=0.28),
            concepts=(
                atlas.SkeletonConcept(
                    id="cmb", label="宇宙マイクロ波背景放射",
                    layout=atlas.ConceptLayout(x=0.25, y=0.2),
                ),
            ),
        ),
        atlas.SkeletonRegion(
            id="galaxies",
            label="銀河・銀河団",
            layout=atlas.RegionLayout(x=0.32, y=0.03, w=0.23, h=0.28),
            concepts=(
                atlas.SkeletonConcept(
                    id="agn", label="活動銀河核",
                    layout=atlas.ConceptLayout(x=0.25, y=0.2),
                ),
            ),
        ),
    )
    return atlas.AtlasSkeleton(
        cartridge=_DOMAIN,
        status=status,
        version=version if status == "frozen" else "",
        generated_by="reference_map:test",
        reviewed_by=("faculty:t",) if status == "frozen" else (),
        changelog=(atlas.ChangelogEntry(version=version, note="t"),)
        if status == "frozen"
        else (),
        regions=regions,
        edges=tuple(
            atlas.SkeletonEdge(from_id=a, to_id=b, kind=k) for a, b, k in edges
        ),
    )


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token

    client = TestClient(app)
    student = _create_token(_STUDENT_ID, "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


@pytest.fixture
def env(monkeypatch):
    """辺候補ルート用の共通差し替え（DB はフェイク・骨格とアンカーは monkeypatch）。"""
    import routes.atlas_edges as routes
    import services as services_module
    from core import atlas_store
    from core.atlas_vectors import builder as vector_builder

    session = FakeEdgeSession()
    monkeypatch.setattr(routes, "_session", lambda: session)
    monkeypatch.setattr(
        atlas_store,
        "load_learner_skeleton",
        lambda domain_key, _session=None: _skeleton() if domain_key == _DOMAIN else None,
    )
    monkeypatch.setattr(atlas_store, "load_draft", lambda _s, domain_key: None)
    # v1 のアンカーは既定で「未構築」（候補は共起由来のみ）。
    monkeypatch.setattr(
        vector_builder, "anchors_with_labels", lambda *a, **k: ([], "")
    )
    events: list[tuple] = []

    def _record(*args, **kwargs):
        # 監査がコミット後に行われることを見るため、記帳時点の commit 回数も残す。
        events.append((args, kwargs, session.commits))

    monkeypatch.setattr(services_module, "record_review_event", _record)
    return {
        "routes": routes,
        "session": session,
        "events": events,
        "monkeypatch": monkeypatch,
    }


def _seed_co_occurrence(session, *, documents=(_DOC1, _DOC2)) -> None:
    """両端に同じ論文が配置されている状態（共起の反復閾値を満たす形）。"""
    for document_id in documents:
        session.seed_placement(node_id=_FROM, document_id=document_id)
        session.seed_placement(node_id=_TO, document_id=document_id)


def _set_draft(env, skeleton: atlas.AtlasSkeleton | None, revision: int = 3) -> None:
    from core import atlas_store

    env["monkeypatch"].setattr(
        atlas_store,
        "load_draft",
        lambda _s, domain_key: (
            None if skeleton is None else {"skeleton": skeleton, "revision": revision}
        ),
    )


# ---------------------------------------------------------------------------
# 1. レビューキュー（読み時導出。RE6 / RE4）
# ---------------------------------------------------------------------------


class TestEdgeCandidateQueue:
    def test_co_occurrence_candidate_surfaces_with_titles(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _seed_co_occurrence(env["session"])

        response = client.get(_QUEUE_PATH, headers=_auth(teacher))
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "cartridge_id",
            "candidates",
            "skeleton_version",
            "draft_exists",
            "draft_revision",
        }
        assert body["skeleton_version"] == "2026.1"
        assert len(body["candidates"]) == 1
        candidate = body["candidates"][0]
        assert candidate["edge_key"] == _EDGE
        assert candidate["origins"] == ["co_occurrence"]
        # 近さの段階ラベルはベクトル由来のときだけ（未測定はキー自体を付けない）。
        assert "nearness_label" not in candidate
        assert {d["document_id"] for d in candidate["documents"]} == {_DOC1, _DOC2}
        assert all(d["title"] for d in candidate["documents"])

    def test_no_numeric_similarity_or_count_fields_are_exposed(
        self, client_and_tokens, env
    ):
        """RE4: cosine の生値・共起件数を出さない。"""
        client, _student, teacher = client_and_tokens
        _seed_co_occurrence(env["session"])

        keys = _keys_recursive(client.get(_QUEUE_PATH, headers=_auth(teacher)).json())
        for forbidden in (
            "similarity",
            "_similarity",
            "score",
            "cosine",
            "confidence",
            "weight",
            "document_count",
            "count",
        ):
            assert forbidden not in keys

    def test_dismissed_candidate_is_suppressed_until_requested(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_co_occurrence(env["session"])
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_DISMISSED,
            review_note="同じ領域の話にすぎない",
        )

        assert client.get(_QUEUE_PATH, headers=_auth(teacher)).json()["candidates"] == []
        with_dismissed = client.get(
            _QUEUE_PATH + "?include_dismissed=true", headers=_auth(teacher)
        ).json()
        assert len(with_dismissed["candidates"]) == 1
        assert (
            with_dismissed["candidates"][0]["decision"]["status"]
            == edge_schema.DECISION_STATUS_DISMISSED
        )

    def test_accepted_decision_is_merged_into_the_candidate(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_co_occurrence(env["session"])
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_ACCEPTED,
            edge_kind="related",
        )

        candidate = client.get(_QUEUE_PATH, headers=_auth(teacher)).json()["candidates"][0]
        assert candidate["decision"]["status"] == edge_schema.DECISION_STATUS_ACCEPTED
        assert candidate["decision"]["edge_kind"] == "related"

    def test_anchor_failure_keeps_the_queue(self, client_and_tokens, env):
        """VA層が使えなくても共起由来の候補は出る（fail-soft = RE6）。"""
        client, _student, teacher = client_and_tokens
        from core.atlas_vectors import builder as vector_builder

        _seed_co_occurrence(env["session"])

        def _boom(*_args, **_kwargs):
            raise RuntimeError("vector index unavailable")

        env["monkeypatch"].setattr(vector_builder, "anchors_with_labels", _boom)
        response = client.get(_QUEUE_PATH, headers=_auth(teacher))

        assert response.status_code == 200
        assert len(response.json()["candidates"]) == 1

    def test_draft_state_is_reported_for_the_incorporate_button(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _set_draft(env, _skeleton(status="draft"), revision=7)
        body = client.get(_QUEUE_PATH, headers=_auth(teacher)).json()
        assert body["draft_exists"] is True
        assert body["draft_revision"] == 7

    def test_domain_without_frozen_skeleton_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.get(
            "/api/admin/cartridges/no_such_domain/atlas/edge-candidates",
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        assert client.get(_QUEUE_PATH).status_code in (401, 403)
        assert client.get(_QUEUE_PATH, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 2. 判断（採用 / 見送り / 復帰。確定は人間のみ — RE3）
# ---------------------------------------------------------------------------


class TestDecideEdgeCandidate:
    def test_accept_with_a_kind_records_decision_and_audit(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "accept", "kind": "depends"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["status"] == edge_schema.DECISION_STATUS_ACCEPTED
        assert decision["edge_kind"] == "depends"
        assert env["session"].commits == 1

        assert len(env["events"]) == 1
        args, _kwargs, commits_at_record = env["events"][0]
        assert args[0] == AUDIT_ENTITY_ATLAS_EDGE
        assert args[1] == _EDGE
        assert args[3] == edge_schema.DECISION_STATUS_ACCEPTED
        assert args[4] == _TEACHER_ID
        assert args[5]["action"] == edge_schema.AUDIT_ACTION_ACCEPT
        assert args[5]["cartridge_id"] == _DOMAIN
        # 監査はコミットの**後**（書き込めていない遷移を監査に載せない）。
        assert commits_at_record == 1

    def test_accept_without_a_kind_is_422_and_writes_nothing(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "accept"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].decisions == []
        assert env["session"].commits == 0
        assert env["session"].rollbacks == 1
        assert env["events"] == []

    def test_unknown_kind_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "accept", "kind": "causes"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].decisions == []

    def test_dismiss_without_a_reason_is_422_and_writes_nothing(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "dismiss"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["events"] == []
        assert env["session"].commits == 0
        assert env["session"].rollbacks == 1
        # 行は遅延生成されるが、遷移していない（candidate のまま）。
        assert [r["status"] for r in env["session"].decisions] in (
            [],
            [edge_schema.DECISION_STATUS_CANDIDATE],
        )

    def test_dismiss_with_a_reason_is_persisted(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={
                "edge_key": _EDGE,
                "action": "dismiss",
                "review_note": "同じ論文に出るだけで関係ではない",
            },
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["status"] == edge_schema.DECISION_STATUS_DISMISSED
        assert decision["review_note"] == "同じ論文に出るだけで関係ではない"
        assert env["events"][0][0][5]["action"] == edge_schema.AUDIT_ACTION_DISMISS

    def test_restore_moves_back_to_candidate_without_deleting(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_DISMISSED,
            review_note="一度見送った理由",
        )
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "restore"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["status"] == edge_schema.DECISION_STATUS_CANDIDATE
        # 行は消えず、なぜ見送ったかの履歴も残る（RE5 / P4）。
        assert len(env["session"].decisions) == 1
        assert decision["review_note"] == "一度見送った理由"
        args, _kwargs, _commits = env["events"][0]
        assert args[2] == edge_schema.DECISION_STATUS_DISMISSED
        assert args[5]["action"] == edge_schema.AUDIT_ACTION_RESTORE

    def test_restore_without_a_decision_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "restore"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404
        assert env["events"] == []
        assert env["session"].decisions == []

    def test_unknown_action_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": _EDGE, "action": "merge"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].calls == []

    def test_edge_key_from_another_domain_is_rejected(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        foreign = edge_schema.build_edge_key("particle_physics", "a", "b")
        response = client.post(
            _DECIDE_PATH,
            json={"edge_key": foreign, "action": "accept", "kind": "related"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].calls == []
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"edge_key": _EDGE, "action": "accept", "kind": "related"}
        assert client.post(_DECIDE_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(_DECIDE_PATH, json=payload, headers=_auth(student)).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# 3. 反映プレビュー（読み取り専用・DB を変更しない — RE3）
# ---------------------------------------------------------------------------


class TestIncorporatePreview:
    def _accept(self, env, *, kind: str = "related") -> None:
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_ACCEPTED,
            edge_kind=kind,
        )

    def test_preview_builds_an_add_patch_without_touching_the_database(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        self._accept(env, kind="depends")
        _set_draft(env, _skeleton(status="draft"), revision=4)

        response = client.post(
            _PREVIEW_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [op["op"] for op in body["patch"]] == ["add"]
        assert body["patch"][0]["path"] == "/edges/-"
        assert {body["from_id"], body["to_id"]} == {_FROM, _TO}
        assert body["kind"] == "depends"
        assert body["revision"] == 4
        assert body["validation"]["errors"] == []
        # 適用後の骨格はそのまま PUT できる形。
        edges = body["patched_draft"]["edges"]
        assert edges[-1]["kind"] == "depends"
        assert {edges[-1]["from"], edges[-1]["to"]} == {_FROM, _TO}
        # DB は変更しない（書き込み SQL も commit も監査も発生しない）。
        assert env["session"].write_calls() == []
        assert env["session"].commits == 0
        assert env["events"] == []

    def test_not_accepted_candidate_is_409(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _set_draft(env, _skeleton(status="draft"))
        no_decision = client.post(
            _PREVIEW_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert no_decision.status_code == 409

        env["session"].seed_decision(
            edge_key=_EDGE, status=edge_schema.DECISION_STATUS_CANDIDATE
        )
        undecided = client.post(
            _PREVIEW_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert undecided.status_code == 409

    def test_missing_draft_is_409_with_the_next_step(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        self._accept(env)
        response = client.post(
            _PREVIEW_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 409
        assert "下書き" in response.json()["detail"]

    def test_duplicate_edge_is_422_with_a_factual_message(self, client_and_tokens, env):
        """同じ無向ペアが既に下書きにあるときは patch を提示しない（patching が防波堤）。"""
        client, _student, teacher = client_and_tokens
        self._accept(env)
        _set_draft(env, _skeleton(status="draft", edges=((_TO, _FROM, "adjacent"),)))

        response = client.post(
            _PREVIEW_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "すでに次版の下書きにあります" in detail
        for banned in ("穴", "不足", "未整備", "埋めましょう"):
            assert banned not in detail
        assert not any(ch.isdigit() for ch in detail)

    def test_edge_key_from_another_domain_is_rejected(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        foreign = edge_schema.build_edge_key("particle_physics", "a", "b")
        response = client.post(
            _PREVIEW_PATH, json={"edge_key": foreign}, headers=_auth(teacher)
        )
        assert response.status_code == 422
        assert env["session"].calls == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"edge_key": _EDGE}
        assert client.post(_PREVIEW_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(_PREVIEW_PATH, json=payload, headers=_auth(student)).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# 4. 反映の記録（教員の PUT の**後**に呼ばれる契約）
# ---------------------------------------------------------------------------


class TestMarkIncorporated:
    def test_records_the_edge_after_the_draft_was_saved(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_ACCEPTED,
            edge_kind="related",
        )
        _set_draft(env, _skeleton(status="draft", edges=((_TO, _FROM, "related"),)))

        response = client.post(
            _MARK_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        # 状態は変えない（採用のまま）。反映は凍結時に applied_version として刻印する。
        assert decision["status"] == edge_schema.DECISION_STATUS_ACCEPTED
        assert decision["applied_version"] == ""
        assert env["session"].write_calls() == []
        assert (
            env["events"][0][0][5]["action"]
            == edge_schema.AUDIT_ACTION_MARK_INCORPORATED
        )

    def test_wrong_order_before_the_draft_put_is_409(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_ACCEPTED,
            edge_kind="related",
        )
        _set_draft(env, _skeleton(status="draft"))  # 辺がまだ下書きに無い

        response = client.post(
            _MARK_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 409
        assert "下書き" in response.json()["detail"]
        assert env["events"] == []

    def test_missing_draft_is_409(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            edge_key=_EDGE,
            status=edge_schema.DECISION_STATUS_ACCEPTED,
            edge_kind="related",
        )
        response = client.post(
            _MARK_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 409

    def test_unknown_decision_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _set_draft(env, _skeleton(status="draft", edges=((_FROM, _TO, "related"),)))
        response = client.post(
            _MARK_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 404

    def test_not_accepted_decision_is_409(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            edge_key=_EDGE, status=edge_schema.DECISION_STATUS_CANDIDATE
        )
        _set_draft(env, _skeleton(status="draft", edges=((_FROM, _TO, "related"),)))
        response = client.post(
            _MARK_PATH, json={"edge_key": _EDGE}, headers=_auth(teacher)
        )
        assert response.status_code == 409
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"edge_key": _EDGE}
        assert client.post(_MARK_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(_MARK_PATH, json=payload, headers=_auth(student)).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# 5. 公開前チェック + 反映の刻印（freeze。§5）
# ---------------------------------------------------------------------------


@pytest.fixture
def skeleton_db(monkeypatch):
    import core.postgres as postgres_module

    fake = AtlasSkeletonTableFake()
    monkeypatch.setattr(postgres_module, "get_session", make_session_factory(fake))
    return fake


@pytest.fixture(autouse=True)
def _no_bundled_cartridges(monkeypatch):
    """同梱カートリッジ・骨格専用バンドルドメインをテストの対象外にする。"""
    import core.atlas_store as atlas_store_module
    import core.cartridges as cartridges_module

    monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [])
    monkeypatch.setattr(atlas_store_module, "_bundled_domain_keys", lambda: [])
    cartridges_module.clear_cache()
    yield
    cartridges_module.clear_cache()


_FREEZE_PATH = f"/api/admin/cartridges/{_DOMAIN}/atlas/skeleton/freeze"


def _seed_draft(skeleton_db, **kwargs) -> None:
    from core import atlas_store

    atlas_store.save_draft(
        skeleton_db, _DOMAIN, _skeleton(status="draft", **kwargs), expected_revision=None
    )


class TestFreezeGateAndStamp:
    def test_pending_accepted_edges_block_the_freeze(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        _seed_draft(skeleton_db)
        monkeypatch.setattr(
            route_mod.gap_store, "list_pending_for_freeze", lambda *a, **k: []
        )
        monkeypatch.setattr(
            route_mod.edge_store,
            "list_pending_for_freeze",
            lambda _s, *, domain_key, draft_edge_pairs: [
                {
                    "edge_key": _EDGE,
                    "from_id": _FROM,
                    "to_id": _TO,
                    "edge_kind": "related",
                    "review_note": "",
                }
            ],
        )

        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        # ラベルの列挙で示す（件数を書かない = RE4）。
        assert detail["pending_edges"] == ["宇宙マイクロ波背景放射 — 活動銀河核"]
        assert not any(ch.isdigit() for ch in detail["message"])
        assert [r for r in skeleton_db.skeleton_rows if r["status"] == "frozen"] == []

    def test_gate_receives_the_current_draft_edges(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        seen: list[tuple] = []
        _seed_draft(skeleton_db, edges=((_FROM, _TO, "related"),))
        monkeypatch.setattr(
            route_mod.gap_store, "list_pending_for_freeze", lambda *a, **k: []
        )

        def _fake_pending(_s, *, domain_key, draft_edge_pairs):
            seen.append((domain_key, {tuple(p) for p in draft_edge_pairs}))
            return []

        monkeypatch.setattr(
            route_mod.edge_store, "list_pending_for_freeze", _fake_pending
        )
        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 200, response.text
        assert seen == [(_DOMAIN, {(_FROM, _TO)})]

    def test_freeze_stamps_applied_version_on_incorporated_edges(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        _seed_draft(skeleton_db, edges=((_FROM, _TO, "related"),))
        for store_mod in (route_mod.gap_store, route_mod.edge_store):
            monkeypatch.setattr(store_mod, "list_pending_for_freeze", lambda *a, **k: [])
        monkeypatch.setattr(
            route_mod.gap_store, "stamp_applied_versions", lambda *a, **k: []
        )
        stamped: list[dict] = []

        def _fake_stamp(_s, *, domain_key, frozen_version, frozen_edge_pairs):
            stamped.append(
                {
                    "domain_key": domain_key,
                    "version": frozen_version,
                    "pairs": {tuple(p) for p in frozen_edge_pairs},
                }
            )
            return [_EDGE]

        monkeypatch.setattr(route_mod.edge_store, "stamp_applied_versions", _fake_stamp)

        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.3", "note": ""}
        )
        assert response.status_code == 200, response.text
        assert stamped == [
            {"domain_key": _DOMAIN, "version": "2026.3", "pairs": {(_FROM, _TO)}}
        ]
        assert any(
            "relation_edges_applied" in str(e.get("metadata"))
            for e in skeleton_db.review_events
        )

    def test_gate_failure_does_not_block_the_freeze(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        """候補機構の照会失敗で凍結という主要操作を止めない（fail-open）。"""
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        _seed_draft(skeleton_db)
        monkeypatch.setattr(
            route_mod.gap_store, "list_pending_for_freeze", lambda *a, **k: []
        )

        def _boom(*_a, **_k):
            raise RuntimeError("edge decision table unavailable")

        monkeypatch.setattr(route_mod.edge_store, "list_pending_for_freeze", _boom)
        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 200, response.text


class TestFreezeWiringStatic:
    """freeze の結線が「ゲートは fail-open・刻印はトランザクション内」であること。"""

    def _freeze_source(self) -> str:
        return extract_function_source(_ATLAS_SRC, "freeze_atlas_skeleton")

    def test_freeze_raises_409_with_pending_edges(self):
        source = self._freeze_source()
        assert "pending_edges" in source
        assert "_pending_edge_candidates" in source

    def test_stamping_happens_inside_the_freeze_transaction(self):
        source = self._freeze_source()
        stamp_at = source.index("edge_store.stamp_applied_versions")
        commit_at = source.index("session.commit()")
        insert_at = source.index("atlas_store.insert_frozen")
        assert insert_at < stamp_at < commit_at

    def test_pending_edge_collection_is_fail_open(self):
        source = extract_function_source(_ATLAS_SRC, "_pending_edge_candidates")
        assert "except Exception" in source
        assert "return []" in source


# ---------------------------------------------------------------------------
# 6. 学習者向け配信（/api/atlas への糸の fail-soft マージ。§6）
# ---------------------------------------------------------------------------


class TestAtlasViewThreadsWiring:
    """静的検査。振る舞いは ``tests/api/test_atlas_view_api.py`` 側にある。"""

    def _get_atlas_source(self) -> str:
        return extract_function_source(_ATLAS_VIEW_SRC, "get_atlas")

    def test_threads_are_merged_from_the_edges_layer(self):
        source = self._get_atlas_source()
        assert "threads_for_domain" in source
        assert "core.atlas_edges.threads" in source

    def test_threads_key_is_conditional_on_available(self):
        source = self._get_atlas_source()
        assert 'relation_threads.get("available")' in source
        assert 'payload["threads"] = relation_threads' in source

    def test_merge_never_breaks_the_map(self):
        source = self._get_atlas_source()
        assert "except Exception" in source


# ---------------------------------------------------------------------------
# 7. ルータ登録・RE5（削除 API を作らない）
# ---------------------------------------------------------------------------


class TestRouterRegistration:
    def test_edge_paths_are_mounted(self):
        from api.main import app

        base = "/api/admin/cartridges/{cartridge_id}/atlas/edge-candidates"
        pairs = collect_route_pairs(app)
        assert (base, "GET") in pairs
        for suffix in ("decide", "incorporate-preview", "mark-incorporated"):
            assert (f"{base}/{suffix}", "POST") in pairs
        assert len({p for p in pairs if p[0].startswith(base)}) == 4

    def test_no_delete_route_exists(self):
        import routes.atlas_edges as routes

        for route in routes.router.routes:
            methods = getattr(route, "methods", set()) or set()
            assert "DELETE" not in methods, getattr(route, "path", "")
