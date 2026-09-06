"""分野マップのベクトル係留層（VA層）— 管理 API 層のテスト。

対象:
  - ``backend/api/routes/atlas_vectors.py``（索引の状態 / 手動 refresh / 別名レジストリ）
  - ``backend/api/routes/atlas.py`` の freeze フック（凍結後の best-effort 再構築）

正本: ``docs/features/atlas_vector_anchoring_design.md`` §5 / §7（不変条項は §2）。

流儀は ``tests/test_atlas_gaps_api.py``（実 app + TestClient + フェイクセッション）を
踏襲する:

- 別名の SQL 面は **store の実装を通して**検証する（route が store の契約 —
  status 遷移のみ・帰属必須 — を守っているかを見たいため）。フェイクセッションは
  ``atlas_anchor_aliases`` / ``atlas_anchor_embeddings`` の SQL をマーカーで捌く。
- 骨格の読み出し（``atlas_store``）と埋め込み構築（``builder``）だけを monkeypatch する
  （テストから外部 API を呼ばない）。
- 別名登録後の再構築は daemon thread なので、``threading`` を同期実行のフェイクに
  差し替えて「呼ばれたこと」と「失敗しても登録は成功のまま」を決定論的に見る。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
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
from core.atlas_vectors import schema as vector_schema  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    collect_route_pairs,
    extract_function_source,
)

_DOMAIN = "astrophysics"
_OTHER_DOMAIN = "particle_physics"
_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_STUDENT_ID = "88888888-8888-8888-8888-888888888888"
_ALIAS_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

_BASE = f"/api/admin/cartridges/{_DOMAIN}/atlas"
_STATUS_PATH = _BASE + "/vectors/status"
_REFRESH_PATH = _BASE + "/vectors/refresh"
_ALIASES_PATH = _BASE + "/aliases"
_DISMISS_PATH = _ALIASES_PATH + "/" + _ALIAS_ID + "/dismiss"

_BUILT_AT = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def _skeleton(*, version: str = "2026.1") -> atlas.AtlasSkeleton:
    """region 2 + concept 1 = 3 ノードの凍結骨格。"""
    regions = (
        atlas.SkeletonRegion(
            id="cosmology",
            label="宇宙論・大規模構造",
            layout=atlas.RegionLayout(x=0.02, y=0.03, w=0.23, h=0.28),
            concepts=(
                atlas.SkeletonConcept(
                    id="cmb",
                    label="宇宙マイクロ波背景放射",
                    layout=atlas.ConceptLayout(x=0.25, y=0.2),
                ),
            ),
        ),
        atlas.SkeletonRegion(
            id="galaxies",
            label="銀河・銀河団",
            layout=atlas.RegionLayout(x=0.32, y=0.03, w=0.23, h=0.28),
            concepts=(),
        ),
    )
    return atlas.AtlasSkeleton(
        cartridge=_DOMAIN,
        status="frozen",
        version=version,
        generated_by="reference_map:test",
        reviewed_by=("faculty:t",),
        changelog=(atlas.ChangelogEntry(version=version, note="t"),),
        regions=regions,
    )


# ---------------------------------------------------------------------------
# フェイクセッション（アンカー索引の件数照会 + 別名テーブル）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeVectorSession:
    """``atlas_anchor_*`` の SQL をマーカーで捌く duck-typed セッション。"""

    def __init__(self, *, coverage=(0, 0, None), any_embedding_rows: bool = False):
        self.coverage = coverage
        self.any_embedding_rows = any_embedding_rows
        self.aliases: dict[tuple[str, str, str], dict] = {}
        self.calls: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.probe_fails = False

    # -- seed ---------------------------------------------------------------

    def seed_alias(
        self,
        *,
        alias_id: str = _ALIAS_ID,
        domain_key: str = _DOMAIN,
        node_id: str = "cmb",
        alias: str = "CMB",
        status: str = "confirmed",
        source: str = "manual",
    ) -> dict:
        row = {
            "id": alias_id,
            "domain_key": domain_key,
            "node_id": node_id,
            "alias": alias,
            "normalized_alias": vector_schema.normalize_label(alias),
            "status": status,
            "source": source,
            "evidence": {},
            "created_by": _TEACHER_ID,
            "decided_by": _TEACHER_ID,
            "created_at": _BUILT_AT,
            "updated_at": _BUILT_AT,
        }
        self.aliases[(domain_key, node_id, row["normalized_alias"])] = row
        return row

    # -- duck-typed session -------------------------------------------------

    @staticmethod
    def _tuple(row: dict) -> tuple:
        return (
            row["id"], row["domain_key"], row["node_id"], row["alias"],
            row["normalized_alias"], row["status"], row["source"], row["evidence"],
            row["created_by"], row["decided_by"], row["created_at"], row["updated_at"],
        )

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append(sql)
        params = params or {}

        if "FROM atlas_anchor_embeddings" in sql and "COUNT(*)" in sql:
            return _Result([self.coverage])
        if "FROM atlas_anchor_embeddings" in sql and "LIMIT 1" in sql:
            if self.probe_fails:
                raise RuntimeError("embeddings table unavailable")
            return _Result([(1,)] if self.any_embedding_rows else [])

        if "INSERT INTO atlas_anchor_aliases" in sql:
            return _Result([self._tuple(self._upsert(params))])
        if "UPDATE atlas_anchor_aliases" in sql:
            row = self._by_id(params.get("id"))
            if row is None:
                return _Result([])
            row["status"] = "dismissed"
            row["decided_by"] = params.get("user_id") or ""
            return _Result([self._tuple(row)])
        if "FROM atlas_anchor_aliases" in sql and "WHERE id =" in sql:
            row = self._by_id(params.get("id"))
            return _Result([self._tuple(row)] if row else [])
        if "FROM atlas_anchor_aliases" in sql:
            wanted_status = params.get("status")
            rows = [
                r
                for r in self.aliases.values()
                if r["domain_key"] == params.get("domain_key")
                and (wanted_status is None or r["status"] == wanted_status)
            ]
            rows.sort(key=lambda r: (r["node_id"], r["normalized_alias"]))
            return _Result([self._tuple(r) for r in rows])

        raise AssertionError(f"unexpected SQL: {sql}")

    def _by_id(self, alias_id):
        for row in self.aliases.values():
            if row["id"] == str(alias_id or ""):
                return row
        return None

    def _upsert(self, params: dict) -> dict:
        key = (
            params["domain_key"],
            params["node_id"],
            params["normalized_alias"],
        )
        row = self.aliases.get(key)
        if row is None:
            row = {
                "id": _ALIAS_ID,
                "domain_key": params["domain_key"],
                "node_id": params["node_id"],
                "normalized_alias": params["normalized_alias"],
                "evidence": {},
                "created_by": params["user_id"],
                "created_at": _BUILT_AT,
            }
            self.aliases[key] = row
        row["alias"] = params["alias"]
        row["status"] = "confirmed"
        row["source"] = params["source"]
        row["decided_by"] = params["user_id"]
        row["updated_at"] = _BUILT_AT
        return row

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class _SyncThread:
    """``threading.Thread`` の同期実行フェイク（best-effort 経路の決定論化）。"""

    def __init__(self, *, target=None, name="", daemon=False):
        self.target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        if self.target is not None:
            self.target()


class _FakeThreading:
    def __init__(self):
        self.started: list[str] = []

    def Thread(self, *, target=None, name="", daemon=False):  # noqa: N802
        self.started.append(name)
        return _SyncThread(target=target, name=name, daemon=daemon)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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
    """VA ルート用の共通差し替え（DB はフェイク・骨格と builder は monkeypatch）。"""
    import routes.atlas_vectors as routes
    import services as services_module
    from core import atlas_store

    session = FakeVectorSession()
    monkeypatch.setattr(routes, "_session", lambda: session)
    monkeypatch.setattr(
        atlas_store,
        "load_frozen_skeleton",
        lambda _s, domain_key: _skeleton() if domain_key == _DOMAIN else None,
    )
    monkeypatch.setattr(atlas_store, "domain_lifecycle", lambda _s, _d: "active")

    events: list[tuple] = []
    monkeypatch.setattr(
        services_module,
        "record_review_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    builds: list[dict] = []

    def _build(domain_key, **kwargs):
        builds.append({"domain_key": domain_key, **kwargs})
        return {
            "status": "completed",
            "domain_key": domain_key,
            "skeleton_version": "2026.1",
            "total_nodes": 3,
            "embedded": 3,
            "reused": 0,
        }

    monkeypatch.setattr(routes.vector_builder, "build_anchor_embeddings", _build)

    fake_threading = _FakeThreading()
    monkeypatch.setattr(routes, "threading", fake_threading)

    return {
        "routes": routes,
        "session": session,
        "events": events,
        "builds": builds,
        "threading": fake_threading,
        "monkeypatch": monkeypatch,
    }


# ---------------------------------------------------------------------------
# 1. 索引の状態（§5 status。骨格なしは available:false）
# ---------------------------------------------------------------------------


class TestVectorStatus:
    def test_no_frozen_skeleton_reports_unavailable(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.get(
            f"/api/admin/cartridges/{_OTHER_DOMAIN}/atlas/vectors/status",
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"available": False}

    def test_reports_coverage_for_the_current_version(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].coverage = (3, 3, _BUILT_AT)

        body = client.get(_STATUS_PATH, headers=_auth(teacher)).json()
        assert body == {
            "available": True,
            "domain_key": _DOMAIN,
            "skeleton_version": "2026.1",
            "total_nodes": 3,  # region 2 + concept 1
            "embedded_nodes": 3,
            "built_at": _BUILT_AT.isoformat(),
            "stale": False,
        }

    def test_partial_coverage_is_reported_honestly(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].coverage = (3, 1, _BUILT_AT)

        body = client.get(_STATUS_PATH, headers=_auth(teacher)).json()
        assert body["total_nodes"] == 3
        assert body["embedded_nodes"] == 1
        assert body["stale"] is False

    def test_stale_when_only_another_version_is_indexed(self, client_and_tokens, env):
        """現行版の索引が無く、別の版の行だけがある = 骨格が更新された状態。"""
        client, _student, teacher = client_and_tokens
        env["session"].coverage = (0, 0, None)
        env["session"].any_embedding_rows = True

        body = client.get(_STATUS_PATH, headers=_auth(teacher)).json()
        assert body["stale"] is True
        assert body["embedded_nodes"] == 0
        assert body["built_at"] is None

    def test_never_stale_when_nothing_was_ever_indexed(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].coverage = (0, 0, None)
        assert client.get(_STATUS_PATH, headers=_auth(teacher)).json()["stale"] is False

    def test_probe_failure_falls_back_to_not_stale(self, client_and_tokens, env):
        """補助照会が読めなくても状態表示は成立する（VA4 fail-soft）。"""
        client, _student, teacher = client_and_tokens
        env["session"].coverage = (0, 0, None)
        env["session"].probe_fails = True

        response = client.get(_STATUS_PATH, headers=_auth(teacher))
        assert response.status_code == 200, response.text
        assert response.json()["stale"] is False

    def test_no_similarity_numbers_are_exposed(self, client_and_tokens, env):
        """VA2 — 出す数値は索引カバレッジだけ（cosine / score を返さない）。"""
        client, _student, teacher = client_and_tokens
        env["session"].coverage = (3, 3, _BUILT_AT)
        body = client.get(_STATUS_PATH, headers=_auth(teacher)).json()
        for forbidden in ("similarity", "cosine", "score", "confidence", "nearness"):
            assert forbidden not in body

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        assert client.get(_STATUS_PATH).status_code in (401, 403)
        assert client.get(_STATUS_PATH, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 2. 手動 refresh（§5。retired は 409・失敗は事実文の 422）
# ---------------------------------------------------------------------------


class TestVectorRefresh:
    def test_returns_the_builder_summary_and_records_audit(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        from core.schema import AUDIT_ENTITY_ATLAS_VECTOR

        response = client.post(_REFRESH_PATH, headers=_auth(teacher))
        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "completed",
            "domain_key": _DOMAIN,
            "skeleton_version": "2026.1",
            "total_nodes": 3,
            "embedded": 3,
            "reused": 0,
        }
        assert env["builds"] == [{"domain_key": _DOMAIN}]

        assert len(env["events"]) == 1
        args, _kwargs = env["events"][0]
        assert args[0] == AUDIT_ENTITY_ATLAS_VECTOR
        assert args[1] == _DOMAIN
        assert args[3] == "completed"
        assert args[4] == _TEACHER_ID
        assert args[5]["action"] == "vectors_refresh"
        assert args[5]["action"] in vector_schema.AUDIT_ACTIONS
        assert args[5]["result"]["embedded"] == 3

    def test_skip_reason_is_passed_through_unchanged(self, client_and_tokens, env):
        """スキップの理由を隠さない（上限・骨格なしをそのまま返す）。"""
        client, _student, teacher = client_and_tokens
        from core.atlas_vectors import builder as builder_module

        env["monkeypatch"].setattr(
            env["routes"].vector_builder,
            "build_anchor_embeddings",
            lambda *a, **k: {
                "status": "skipped",
                "skipped_reason": builder_module.SKIP_DAILY_LIMIT,
            },
        )
        body = client.post(_REFRESH_PATH, headers=_auth(teacher)).json()
        assert body == {
            "status": "skipped",
            "skipped_reason": builder_module.SKIP_DAILY_LIMIT,
        }
        assert env["events"][0][0][3] == "skipped"

    def test_retired_domain_is_409_without_building(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        from core import atlas_store

        env["monkeypatch"].setattr(
            atlas_store, "domain_lifecycle", lambda _s, _d: "retired"
        )
        response = client.post(_REFRESH_PATH, headers=_auth(teacher))
        assert response.status_code == 409
        assert "廃止" in response.json()["detail"]
        assert env["builds"] == []
        assert env["events"] == []

    def test_builder_failure_becomes_a_factual_422(self, client_and_tokens, env):
        """内部の例外文言・スタックを detail に載せない（数値も出さない）。"""
        client, _student, teacher = client_and_tokens

        def _boom(*_a, **_k):
            raise RuntimeError("connection refused to 10.0.0.5:5432")

        env["monkeypatch"].setattr(
            env["routes"].vector_builder, "build_anchor_embeddings", _boom
        )
        response = client.post(_REFRESH_PATH, headers=_auth(teacher))
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "索引" in detail
        for leaked in ("Traceback", "RuntimeError", "10.0.0.5", "5432", "connection"):
            assert leaked not in detail
        assert not any(ch.isdigit() for ch in detail)
        # 失敗は記帳しない（起きなかった構築を監査に残さない）
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        assert client.post(_REFRESH_PATH).status_code in (401, 403)
        assert client.post(_REFRESH_PATH, headers=_auth(student)).status_code == 403
        assert env["builds"] == []


# ---------------------------------------------------------------------------
# 3. 別名の一覧（§7。node_label は骨格から補う）
# ---------------------------------------------------------------------------


class TestListAliases:
    def test_confirmed_only_by_default(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_alias(alias="CMB")
        env["session"].seed_alias(
            alias_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias="背景放射",
            status="dismissed",
        )

        body = client.get(_ALIASES_PATH, headers=_auth(teacher)).json()
        assert body["cartridge_id"] == _DOMAIN
        assert [a["alias"] for a in body["aliases"]] == ["CMB"]
        assert body["aliases"][0]["node_label"] == "宇宙マイクロ波背景放射"

    def test_include_dismissed_is_passed_through(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_alias(alias="CMB")
        env["session"].seed_alias(
            alias_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias="背景放射",
            status="dismissed",
        )

        body = client.get(
            _ALIASES_PATH + "?include_dismissed=true", headers=_auth(teacher)
        ).json()
        assert {a["alias"] for a in body["aliases"]} == {"CMB", "背景放射"}
        assert {a["status"] for a in body["aliases"]} == {"confirmed", "dismissed"}

    def test_node_label_is_empty_when_the_node_is_gone(self, client_and_tokens, env):
        """骨格から消えたノードの別名は残るが、名前を捏造しない（VA8）。"""
        client, _student, teacher = client_and_tokens
        env["session"].seed_alias(node_id="removed_node", alias="旧称")

        body = client.get(_ALIASES_PATH, headers=_auth(teacher)).json()
        assert body["aliases"][0]["node_label"] == ""

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        assert client.get(_ALIASES_PATH).status_code in (401, 403)
        assert client.get(_ALIASES_PATH, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 4. 別名の登録（§7。確定は教員の明示操作 — VA1）
# ---------------------------------------------------------------------------


class TestRegisterAlias:
    def _post(self, client, teacher, **overrides):
        payload = {"node_id": "cmb", "alias": "CMB", "source": "manual"}
        payload.update(overrides)
        return client.post(_ALIASES_PATH, json=payload, headers=_auth(teacher))

    def test_registers_and_records_audit(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        from core.schema import AUDIT_ENTITY_ATLAS_VECTOR

        response = self._post(client, teacher)
        assert response.status_code == 200, response.text
        row = response.json()
        assert row["alias"] == "CMB"
        assert row["node_id"] == "cmb"
        assert row["status"] == "confirmed"
        assert row["node_label"] == "宇宙マイクロ波背景放射"
        assert env["session"].commits == 1

        args, _kwargs = env["events"][0]
        assert args[0] == AUDIT_ENTITY_ATLAS_VECTOR
        assert args[1] == _DOMAIN
        assert args[3] == "confirmed"
        assert args[4] == _TEACHER_ID
        assert args[5]["action"] == "alias_register"
        assert args[5]["node_id"] == "cmb"
        assert args[5]["alias"] == "CMB"
        assert args[5]["source"] == "manual"

    def test_dismissed_alias_comes_back_without_a_new_row(self, client_and_tokens, env):
        """VA6 — 見送りは行削除ではないので、同じ表記の再登録が復帰になる。"""
        client, _student, teacher = client_and_tokens
        env["session"].seed_alias(alias="CMB", status="dismissed")

        response = self._post(client, teacher)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "confirmed"
        assert len(env["session"].aliases) == 1

    def test_triggers_a_background_single_node_rebuild(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        assert self._post(client, teacher).status_code == 200
        assert env["builds"] == [{"domain_key": _DOMAIN, "node_ids": ["cmb"]}]
        assert env["threading"].started == ["atlas-anchor-embed-alias"]

    def test_rebuild_failure_does_not_fail_the_registration(self, client_and_tokens, env):
        """VA4 — 登録は教員の確定操作であって、埋め込みの成否に依存しない。"""
        client, _student, teacher = client_and_tokens

        def _boom(*_a, **_k):
            raise RuntimeError("embedding API unavailable")

        env["monkeypatch"].setattr(
            env["routes"].vector_builder, "build_anchor_embeddings", _boom
        )
        response = self._post(client, teacher)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "confirmed"
        assert env["events"][0][0][5]["action"] == "alias_register"

    def test_unknown_node_id_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = self._post(client, teacher, node_id="no_such_node")
        assert response.status_code == 422
        assert "骨格" in response.json()["detail"]
        assert env["session"].aliases == {}
        assert env["events"] == []
        assert env["builds"] == []

    def test_empty_alias_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        for empty in ("", "   ", "　\t"):
            response = self._post(client, teacher, alias=empty)
            assert response.status_code == 422, empty
        assert env["session"].aliases == {}
        assert env["events"] == []

    def test_unknown_source_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = self._post(client, teacher, source="llm_candidate")
        assert response.status_code == 422
        assert env["session"].aliases == {}
        assert env["events"] == []

    def test_gap_signal_source_is_accepted(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = self._post(
            client,
            teacher,
            source="gap_signal",
            evidence={"cluster_key": "gap|astrophysics||Cosmic Web"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["source"] == "gap_signal"

    def test_domain_without_a_frozen_skeleton_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"/api/admin/cartridges/{_OTHER_DOMAIN}/atlas/aliases",
            json={"node_id": "cmb", "alias": "CMB"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"node_id": "cmb", "alias": "CMB"}
        assert client.post(_ALIASES_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(
                _ALIASES_PATH, json=payload, headers=_auth(student)
            ).status_code
            == 403
        )
        assert env["session"].aliases == {}


# ---------------------------------------------------------------------------
# 5. 別名の見送り（§7。status 遷移のみ — VA6）
# ---------------------------------------------------------------------------


class TestDismissAlias:
    def test_transitions_the_status_without_deleting(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_alias(alias="CMB")

        response = client.post(_DISMISS_PATH, headers=_auth(teacher))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "dismissed"
        assert len(env["session"].aliases) == 1
        assert env["session"].commits == 1

        args, _kwargs = env["events"][0]
        assert args[2] == "confirmed"
        assert args[3] == "dismissed"
        assert args[5]["action"] == "alias_dismiss"

    def test_alias_from_another_domain_is_404(self, client_and_tokens, env):
        """別分野の別名は不在と同じ応答（他分野の登録内容を漏らさない）。"""
        client, _student, teacher = client_and_tokens
        env["session"].seed_alias(domain_key=_OTHER_DOMAIN, alias="CMB")

        response = client.post(_DISMISS_PATH, headers=_auth(teacher))
        assert response.status_code == 404
        assert env["session"].aliases[(_OTHER_DOMAIN, "cmb", "cmb")]["status"] == (
            "confirmed"
        )
        assert env["events"] == []

    def test_unknown_alias_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        assert client.post(_DISMISS_PATH, headers=_auth(teacher)).status_code == 404
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        env["session"].seed_alias(alias="CMB")
        assert client.post(_DISMISS_PATH).status_code in (401, 403)
        assert client.post(_DISMISS_PATH, headers=_auth(student)).status_code == 403
        assert env["session"].aliases[(_DOMAIN, "cmb", "cmb")]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# 6. ルータ登録・VA6（削除 API を作らない）
# ---------------------------------------------------------------------------


class TestRouterRegistration:
    def test_five_routes_are_mounted(self):
        from api.main import app

        pairs = collect_route_pairs(app)
        base = "/api/admin/cartridges/{cartridge_id}/atlas"
        expected = {
            (base + "/vectors/status", "GET"),
            (base + "/vectors/refresh", "POST"),
            (base + "/aliases", "GET"),
            (base + "/aliases", "POST"),
            (base + "/aliases/{alias_id}/dismiss", "POST"),
        }
        assert expected <= pairs

    def test_no_delete_route_exists(self):
        import routes.atlas_vectors as routes

        for route in routes.router.routes:
            methods = getattr(route, "methods", set()) or set()
            assert "DELETE" not in methods, getattr(route, "path", "")


# ---------------------------------------------------------------------------
# 7. freeze フック（凍結後の best-effort 再構築。VA3 ① / VA4）
# ---------------------------------------------------------------------------


_ATLAS_ROUTE_SRC = (BACKEND / "api" / "routes" / "atlas.py").read_text(encoding="utf-8")
_FREEZE_SRC = extract_function_source(_ATLAS_ROUTE_SRC, "freeze_atlas_skeleton")


class TestFreezeHook:
    def test_freeze_schedules_the_anchor_rebuild(self):
        assert "build_anchor_embeddings" in _FREEZE_SRC
        assert "atlas-anchor-embed" in _FREEZE_SRC

    def test_rebuild_runs_in_a_daemon_thread(self):
        """凍結の応答を埋め込み API の待ち時間で引き延ばさない。"""
        assert "threading.Thread(" in _FREEZE_SRC
        assert "daemon=True" in _FREEZE_SRC

    def test_rebuild_failure_cannot_break_the_freeze(self):
        """VA4 — スレッド内も、スレッド起動自体も try/except で包む。"""
        tail = _FREEZE_SRC[_FREEZE_SRC.index("build_anchor_embeddings") - 400:]
        assert tail.count("except Exception:") >= 2
        assert "logger.warning" in tail
        # 凍結レスポンスの形は変えない（この区画に return を足していない）
        hook = _FREEZE_SRC[_FREEZE_SRC.index("_atlas_anchor_embed"):]
        assert "raise" not in hook.split("threading.Thread(")[0]

    def test_builder_is_imported_lazily(self):
        """入口の import を重くしない（freeze フックは関数内 import）。"""
        assert "from core.atlas_vectors" not in _ATLAS_ROUTE_SRC.split("def ")[0]
        assert "from core.atlas_vectors.builder import build_anchor_embeddings" in (
            _FREEZE_SRC
        )
