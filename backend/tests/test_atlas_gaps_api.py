"""カテゴリギャップ候補 — 管理 API 層のテスト。

対象:
  - ``backend/api/routes/atlas_gaps.py``（レビューキュー / 判断 / 取り込みプレビュー・刻印）
  - ``backend/api/routes/atlas.py`` の追加分
    （``POST .../atlas/skeleton/draft/from-frozen`` と freeze の公開前チェック・刻印）
  - ``backend/api/routes/landscape.py`` の ``gap_signals_recorded``（案内一行の材料）

正本: ``docs/features/category_gap_candidates_design.md`` §5.4 / §5.5 / §5.7。

流儀は ``tests/test_landscape_api.py``（実 app + TestClient + フェイクセッション）と
``tests/test_atlas_freeze_impact.py``（``AtlasSkeletonTableFake``）の合成:

- gap の SQL 面は ``tests/test_atlas_gaps_store.py::FakeGapSession`` をそのまま使い、
  **store の実装を通して**検証する（route が store の契約を守っているかを見たいため）。
  骨格の読み出し（``atlas_store``）だけを monkeypatch する。
- 骨格の書き込みを伴う from-frozen / freeze は ``AtlasSkeletonTableFake`` を使う。
"""

from __future__ import annotations

import sys
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
from core.atlas_gaps import schema as gap_schema  # noqa: E402
from tests.fixtures.atlas_skeletons_fake import (  # noqa: E402
    AtlasSkeletonTableFake,
    make_session_factory,
)
from tests.test_atlas_gaps_store import FakeGapSession  # noqa: E402

_DOMAIN = "astrophysics"
_DOC1 = "11111111-1111-1111-1111-111111111111"
_DOC2 = "22222222-2222-2222-2222-222222222222"
_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_STUDENT_ID = "88888888-8888-8888-8888-888888888888"

_CLUSTER = gap_schema.build_cluster_key(_DOMAIN, "cosmology", "Cosmic Web")
_REGION_CLUSTER = gap_schema.build_cluster_key(_DOMAIN, "", "重力波天文学")

_QUEUE_PATH = f"/api/admin/cartridges/{_DOMAIN}/atlas/gap-candidates"
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


def _skeleton(
    *,
    status: str = "frozen",
    version: str = "2026.1",
    concepts=("cmb",),
    regions=("cosmology", "galaxies"),
) -> atlas.AtlasSkeleton:
    labels = {"cosmology": "宇宙論・大規模構造", "galaxies": "銀河・銀河団"}
    built = []
    for i, region_id in enumerate(regions):
        built.append(
            atlas.SkeletonRegion(
                id=region_id,
                label=labels.get(region_id, region_id),
                layout=atlas.RegionLayout(x=0.02 + 0.3 * i, y=0.03, w=0.23, h=0.28),
                concepts=tuple(
                    atlas.SkeletonConcept(
                        id=c, label=c.upper(), layout=atlas.ConceptLayout(x=0.25, y=0.2)
                    )
                    for c in concepts
                )
                if region_id == "cosmology"
                else (),
            )
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
        regions=tuple(built),
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
    """gap ルート用の共通差し替え（DB は FakeGapSession・骨格は monkeypatch）。"""
    import routes.atlas_gaps as routes
    import services as services_module
    from core import atlas_store

    session = FakeGapSession()
    monkeypatch.setattr(routes, "_session", lambda: session)
    monkeypatch.setattr(
        atlas_store,
        "load_learner_skeleton",
        lambda domain_key, _session=None: _skeleton() if domain_key == _DOMAIN else None,
    )
    monkeypatch.setattr(atlas_store, "load_draft", lambda _s, domain_key: None)
    events: list[tuple] = []
    monkeypatch.setattr(
        services_module,
        "record_review_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    return {
        "routes": routes,
        "session": session,
        "events": events,
        "monkeypatch": monkeypatch,
    }


def _seed_repeated_signals(session, *, documents=(_DOC1, _DOC2), **overrides) -> None:
    """同一 cluster に distinct document を複数積む（反復閾値を満たす形）。"""
    for document_id in documents:
        session.seed_signal(document_id=document_id, **overrides)
        session.titles[document_id] = f"論文 {document_id[:4]}"


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
# 1. レビューキュー（読み時導出・§4.1 反復閾値 / §4.2 却下の永続）
# ---------------------------------------------------------------------------


class TestGapCandidateQueue:
    def test_single_document_signal_does_not_surface(self, client_and_tokens, env):
        """1論文の主題は分野のカテゴリではない（信号は残るがキューに出ない）。"""
        client, _student, teacher = client_and_tokens
        env["session"].seed_signal(document_id=_DOC1)

        body = client.get(_QUEUE_PATH, headers=_auth(teacher)).json()
        assert body["candidates"] == []
        assert body["skeleton_version"] == "2026.1"
        assert body["draft_exists"] is False
        assert body["draft_revision"] is None

    def test_repeated_signal_surfaces_with_supporting_documents(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])

        response = client.get(_QUEUE_PATH, headers=_auth(teacher))
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "cartridge_id",
            "candidates",
            "skeleton_version",
            "draft_exists",
            "draft_revision",
        }
        assert len(body["candidates"]) == 1
        candidate = body["candidates"][0]
        assert candidate["cluster_key"] == _CLUSTER
        assert candidate["layer"] == "concept"
        assert candidate["layer_label"] == "概念"
        assert candidate["parent_region_label"] == "宇宙論・大規模構造"
        assert candidate["proposed_label"] == "Cosmic Web"
        # 支持論文はタイトル列挙（件数フィールドを作らない — LS5）。
        assert {d["document_id"] for d in candidate["documents"]} == {_DOC1, _DOC2}
        assert all(d["title"] for d in candidate["documents"])

    def test_no_numeric_confidence_or_count_fields_are_exposed(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])

        body = client.get(_QUEUE_PATH, headers=_auth(teacher)).json()
        keys = _keys_recursive(body)
        for forbidden in ("confidence", "weight", "document_count", "count"):
            assert forbidden not in keys
        assert "confidence_label" in keys  # 段階ラベルは出す

    def test_dismissed_cluster_is_suppressed_until_requested(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])
        env["session"].seed_decision(
            cluster_key=_CLUSTER,
            status=gap_schema.DECISION_STATUS_DISMISSED,
            review_note="既存概念で足りる",
        )

        assert client.get(_QUEUE_PATH, headers=_auth(teacher)).json()["candidates"] == []
        with_dismissed = client.get(
            _QUEUE_PATH + "?include_dismissed=true", headers=_auth(teacher)
        ).json()
        assert len(with_dismissed["candidates"]) == 1
        assert (
            with_dismissed["candidates"][0]["decision"]["status"]
            == gap_schema.DECISION_STATUS_DISMISSED
        )

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
            "/api/admin/cartridges/no_such_domain/atlas/gap-candidates",
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        assert client.get(_QUEUE_PATH).status_code in (401, 403)
        assert client.get(_QUEUE_PATH, headers=_auth(student)).status_code == 403


class TestNearAnchorAnnotation:
    """VA層 §7: 近傍注記は読み時導出で、出ないときは既存レスポンスと完全に同じ形。"""

    def _annotate(self, env, fake):
        env["monkeypatch"].setattr(
            "core.atlas_vectors.annotate.annotate_gap_clusters", fake
        )

    def test_near_anchor_is_attached_with_the_skeleton_version(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])
        calls: list[tuple] = []

        def _fake(session, domain_key, skeleton_version, clusters, **kwargs):
            calls.append((domain_key, skeleton_version, len(clusters)))
            return [
                dict(
                    c,
                    near_anchor={
                        "node_id": "cmb",
                        "node_label": "CMB",
                        "region_label": "宇宙論・大規模構造",
                        "nearness_label": "かなり近い",
                        "skeleton_version": skeleton_version,
                    },
                )
                for c in clusters
            ]

        self._annotate(env, _fake)
        body = client.get(_QUEUE_PATH, headers=_auth(teacher)).json()

        assert calls == [(_DOMAIN, "2026.1", 1)]
        annotation = body["candidates"][0]["near_anchor"]
        assert annotation["node_label"] == "CMB"
        assert annotation["skeleton_version"] == "2026.1"
        # 生スコアは載らない（VA2）。
        assert "similarity" not in _keys_recursive(body)
        assert "score" not in _keys_recursive(body)

    def test_annotation_failure_keeps_the_queue(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])

        def _boom(*_args, **_kwargs):
            raise RuntimeError("vector layer down")

        self._annotate(env, _boom)
        response = client.get(_QUEUE_PATH, headers=_auth(teacher))

        assert response.status_code == 200
        body = response.json()
        assert len(body["candidates"]) == 1
        assert "near_anchor" not in body["candidates"][0]

    def test_no_annotation_keeps_the_response_backward_compatible(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])
        self._annotate(env, lambda _s, _d, _v, clusters, **_k: [dict(c) for c in clusters])

        body = client.get(_QUEUE_PATH, headers=_auth(teacher)).json()
        assert set(body) == {
            "cartridge_id",
            "candidates",
            "skeleton_version",
            "draft_exists",
            "draft_revision",
        }
        assert "near_anchor" not in body["candidates"][0]


# ---------------------------------------------------------------------------
# 2. 判断（採用 / 見送り / 復帰）
# ---------------------------------------------------------------------------


class TestDecideGapCandidate:
    def test_accept_records_decision_and_audit(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        from core.schema import AUDIT_ENTITY_CATEGORY_GAP

        response = client.post(
            _DECIDE_PATH,
            json={"cluster_key": _CLUSTER, "action": "accept"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["status"] == gap_schema.DECISION_STATUS_ACCEPTED
        assert decision["status_label"] == "採用"
        assert env["session"].commits == 1

        assert len(env["events"]) == 1
        args, _kwargs = env["events"][0]
        assert args[0] == AUDIT_ENTITY_CATEGORY_GAP
        assert args[1] == _CLUSTER
        assert args[3] == gap_schema.DECISION_STATUS_ACCEPTED
        assert args[4] == _TEACHER_ID
        assert args[5]["action"] == gap_schema.AUDIT_ACTION_ACCEPT
        assert args[5]["cartridge_id"] == _DOMAIN

    def test_dismiss_without_a_reason_is_422_and_writes_nothing(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"cluster_key": _CLUSTER, "action": "dismiss"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].decisions == []
        assert env["events"] == []
        assert env["session"].rollbacks == 1

    def test_dismiss_with_a_reason_is_persisted(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={
                "cluster_key": _CLUSTER,
                "action": "dismiss",
                "review_note": "既存の概念で言い表せる",
            },
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["status"] == gap_schema.DECISION_STATUS_DISMISSED
        assert decision["review_note"] == "既存の概念で言い表せる"
        assert env["events"][0][0][5]["action"] == gap_schema.AUDIT_ACTION_DISMISS

    def test_restore_moves_back_to_candidate_without_deleting(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            cluster_key=_CLUSTER,
            status=gap_schema.DECISION_STATUS_DISMISSED,
            review_note="一度見送った理由",
        )
        response = client.post(
            _DECIDE_PATH,
            json={"cluster_key": _CLUSTER, "action": "restore"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["status"] == gap_schema.DECISION_STATUS_CANDIDATE
        # 行は消えず、なぜ見送ったかの履歴も残る（P4 / AB3）。
        assert len(env["session"].decisions) == 1
        assert decision["review_note"] == "一度見送った理由"
        args, _kwargs = env["events"][0]
        assert args[2] == gap_schema.DECISION_STATUS_DISMISSED
        assert args[5]["action"] == gap_schema.AUDIT_ACTION_RESTORE

    def test_restore_without_a_decision_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"cluster_key": _CLUSTER, "action": "restore"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404
        assert env["events"] == []

    def test_unknown_action_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DECIDE_PATH,
            json={"cluster_key": _CLUSTER, "action": "merge"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].decisions == []

    def test_cluster_key_from_another_domain_is_rejected(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        foreign = gap_schema.build_cluster_key("particle_physics", "", "Some Region")
        response = client.post(
            _DECIDE_PATH,
            json={"cluster_key": foreign, "action": "accept"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert env["session"].decisions == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"cluster_key": _CLUSTER, "action": "accept"}
        assert client.post(_DECIDE_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(_DECIDE_PATH, json=payload, headers=_auth(student)).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# 3. 取り込みプレビュー（読み取り専用・DB を変更しない）
# ---------------------------------------------------------------------------


class TestIncorporatePreview:
    def _accept(self, env, cluster_key=_CLUSTER) -> None:
        env["session"].seed_decision(
            cluster_key=cluster_key, status=gap_schema.DECISION_STATUS_ACCEPTED
        )

    def test_preview_builds_an_add_patch_without_touching_the_database(
        self, client_and_tokens, env
    ):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])
        self._accept(env)
        _set_draft(env, _skeleton(status="draft"), revision=4)
        before_writes = [
            sql for sql, _p in env["session"].calls if sql.startswith(("INSERT", "UPDATE"))
        ]

        response = client.post(
            _PREVIEW_PATH, json={"cluster_key": _CLUSTER}, headers=_auth(teacher)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [op["op"] for op in body["patch"]] == ["add"]
        assert body["patch"][0]["path"] == "/regions/0/concepts/-"
        assert body["layer"] == "concept"
        assert body["parent_region_id"] == "cosmology"
        assert body["node_id"]
        assert body["revision"] == 4
        assert body["validation"]["errors"] == []
        # プレビューは適用後の骨格をそのまま PUT できる形で返す。
        concepts = body["patched_draft"]["regions"][0]["concepts"]
        assert [c["id"] for c in concepts][-1] == body["node_id"]
        assert concepts[-1]["label"] == "Cosmic Web"

        # DB は変更しない（書き込み SQL も commit も発生しない）。
        after_writes = [
            sql for sql, _p in env["session"].calls if sql.startswith(("INSERT", "UPDATE"))
        ]
        assert after_writes == before_writes
        assert env["session"].commits == 0
        assert env["events"] == []

    def test_label_override_is_used(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])
        self._accept(env)
        _set_draft(env, _skeleton(status="draft"))

        body = client.post(
            _PREVIEW_PATH,
            json={"cluster_key": _CLUSTER, "proposed_label": "宇宙の大規模構造"},
            headers=_auth(teacher),
        ).json()
        assert body["proposed_label"] == "宇宙の大規模構造"
        assert "宇宙の大規模構造" in body["summary"]

    def test_region_candidate_appends_a_region(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(
            env["session"],
            layer=gap_schema.GAP_LAYER_REGION,
            parent_region_id="",
            proposed_label="重力波天文学",
        )
        self._accept(env, _REGION_CLUSTER)
        _set_draft(env, _skeleton(status="draft"))

        body = client.post(
            _PREVIEW_PATH, json={"cluster_key": _REGION_CLUSTER}, headers=_auth(teacher)
        ).json()
        assert body["layer"] == "region"
        assert body["patch"][0]["path"] == "/regions/-"
        assert body["patched_draft"]["regions"][-1]["label"] == "重力波天文学"

    def test_not_accepted_candidate_is_409(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _set_draft(env, _skeleton(status="draft"))
        no_decision = client.post(
            _PREVIEW_PATH, json={"cluster_key": _CLUSTER}, headers=_auth(teacher)
        )
        assert no_decision.status_code == 409

        env["session"].seed_decision(
            cluster_key=_CLUSTER, status=gap_schema.DECISION_STATUS_CANDIDATE
        )
        undecided = client.post(
            _PREVIEW_PATH, json={"cluster_key": _CLUSTER}, headers=_auth(teacher)
        )
        assert undecided.status_code == 409

    def test_missing_draft_is_409_with_the_next_step(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        self._accept(env)
        response = client.post(
            _PREVIEW_PATH, json={"cluster_key": _CLUSTER}, headers=_auth(teacher)
        )
        assert response.status_code == 409
        assert "下書き" in response.json()["detail"]

    def test_full_region_is_422_with_a_factual_message(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _seed_repeated_signals(env["session"])
        self._accept(env)
        _set_draft(
            env,
            _skeleton(status="draft", concepts=tuple(f"c{i}" for i in range(6))),
        )
        response = client.post(
            _PREVIEW_PATH, json={"cluster_key": _CLUSTER}, headers=_auth(teacher)
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "上限" in detail
        for banned in ("穴", "不足", "未整備", "埋めましょう"):
            assert banned not in detail

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"cluster_key": _CLUSTER}
        assert client.post(_PREVIEW_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(_PREVIEW_PATH, json=payload, headers=_auth(student)).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# 4. 取り込みの刻印（教員の PUT の**後**に呼ばれる契約）
# ---------------------------------------------------------------------------


class TestMarkIncorporated:
    def test_marks_the_node_after_the_draft_was_saved(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            cluster_key=_CLUSTER, status=gap_schema.DECISION_STATUS_ACCEPTED
        )
        _set_draft(env, _skeleton(status="draft", concepts=("cmb", "cosmic_web")))

        response = client.post(
            _MARK_PATH,
            json={"cluster_key": _CLUSTER, "draft_node_id": "cosmic_web"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200, response.text
        assert response.json()["decision"]["draft_node_id"] == "cosmic_web"
        # 反映（applied_version）は凍結時に別途刻印する（採用と反映の分離）。
        assert response.json()["decision"]["applied_version"] == ""
        assert env["session"].commits == 1
        assert env["events"][0][0][5]["action"] == gap_schema.AUDIT_ACTION_INCORPORATE

    def test_wrong_order_before_the_draft_put_is_409(self, client_and_tokens, env):
        """下書きに存在しない node の刻印は拒否する（PUT より前に呼ばれた誤順序）。"""
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            cluster_key=_CLUSTER, status=gap_schema.DECISION_STATUS_ACCEPTED
        )
        _set_draft(env, _skeleton(status="draft"))

        response = client.post(
            _MARK_PATH,
            json={"cluster_key": _CLUSTER, "draft_node_id": "cosmic_web"},
            headers=_auth(teacher),
        )
        assert response.status_code == 409
        assert env["session"].decisions[0]["draft_node_id"] == ""
        assert env["events"] == []

    def test_unknown_cluster_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        _set_draft(env, _skeleton(status="draft", concepts=("cmb", "cosmic_web")))
        response = client.post(
            _MARK_PATH,
            json={"cluster_key": _CLUSTER, "draft_node_id": "cosmic_web"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_not_accepted_cluster_is_409(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        env["session"].seed_decision(
            cluster_key=_CLUSTER, status=gap_schema.DECISION_STATUS_CANDIDATE
        )
        _set_draft(env, _skeleton(status="draft", concepts=("cmb", "cosmic_web")))
        response = client.post(
            _MARK_PATH,
            json={"cluster_key": _CLUSTER, "draft_node_id": "cosmic_web"},
            headers=_auth(teacher),
        )
        assert response.status_code == 409
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        payload = {"cluster_key": _CLUSTER, "draft_node_id": "cosmic_web"}
        assert client.post(_MARK_PATH, json=payload).status_code in (401, 403)
        assert (
            client.post(_MARK_PATH, json=payload, headers=_auth(student)).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# 5. from-frozen（現行凍結版 → 次版 draft の決定論複製。§5.5）
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


_FROM_FROZEN_PATH = f"/api/admin/cartridges/{_DOMAIN}/atlas/skeleton/draft/from-frozen"
_FREEZE_PATH = f"/api/admin/cartridges/{_DOMAIN}/atlas/skeleton/freeze"


def _seed_frozen(skeleton_db, **kwargs) -> None:
    from core import atlas_store

    atlas_store.insert_frozen(skeleton_db, _DOMAIN, _skeleton(**kwargs))


def _seed_draft(skeleton_db, **kwargs) -> None:
    from core import atlas_store

    atlas_store.save_draft(
        skeleton_db, _DOMAIN, _skeleton(status="draft", **kwargs), expected_revision=None
    )


class TestDraftFromFrozen:
    def test_copies_the_current_frozen_version_into_a_draft(
        self, client_and_tokens, skeleton_db
    ):
        client, _student, teacher = client_and_tokens
        _seed_frozen(skeleton_db)

        response = client.post(_FROM_FROZEN_PATH, headers=_auth(teacher))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_version"] == "2026.1"
        draft = body["draft"]
        assert draft["revision"] == 1
        assert draft["skeleton"]["status"] == "draft"
        assert draft["skeleton"]["version"] == ""
        assert [r["id"] for r in draft["skeleton"]["regions"]] == ["cosmology", "galaxies"]
        # node id が振り直されないことが本 API の存在理由（配置・足跡を壊さない）。
        assert draft["skeleton"]["regions"][0]["concepts"][0]["id"] == "cmb"

        drafts = [r for r in skeleton_db.skeleton_rows if r["status"] == "draft"]
        assert len(drafts) == 1
        assert any(
            e.get("entity_type") == "atlas_skeleton"
            and "draft_from_frozen" in str(e.get("metadata"))
            for e in skeleton_db.review_events
        )

    def test_existing_draft_is_409(self, client_and_tokens, skeleton_db):
        client, _student, teacher = client_and_tokens
        _seed_frozen(skeleton_db)
        _seed_draft(skeleton_db)

        response = client.post(_FROM_FROZEN_PATH, headers=_auth(teacher))
        assert response.status_code == 409
        assert len([r for r in skeleton_db.skeleton_rows if r["status"] == "draft"]) == 1

    def test_retired_domain_is_409(self, client_and_tokens, skeleton_db):
        client, _student, teacher = client_and_tokens
        from core import atlas_store

        _seed_frozen(skeleton_db)
        atlas_store.retire_domain(skeleton_db, _DOMAIN, note="実験終了")

        response = client.post(_FROM_FROZEN_PATH, headers=_auth(teacher))
        assert response.status_code == 409
        assert [r for r in skeleton_db.skeleton_rows if r["status"] == "draft"] == []

    def test_domain_without_a_frozen_version_is_404(self, client_and_tokens, skeleton_db):
        client, _student, teacher = client_and_tokens
        # domain 自体が存在しない（骨格も draft も無い）
        assert (
            client.post(_FROM_FROZEN_PATH, headers=_auth(teacher)).status_code == 404
        )
        # draft だけある domain（複製元の凍結版が無い）
        _seed_draft(skeleton_db)
        assert (
            client.post(_FROM_FROZEN_PATH, headers=_auth(teacher)).status_code
            in (404, 409)
        )

    def test_requires_teacher(self, client_and_tokens, skeleton_db):
        client, student, _teacher = client_and_tokens
        _seed_frozen(skeleton_db)
        assert client.post(_FROM_FROZEN_PATH).status_code in (401, 403)
        assert client.post(_FROM_FROZEN_PATH, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 6. 公開前チェック + 反映の刻印（freeze。§5.4 / §5.5）
# ---------------------------------------------------------------------------


class TestFreezeGateAndStamp:
    def test_pending_accepted_candidates_block_the_freeze(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        _seed_draft(skeleton_db)
        monkeypatch.setattr(
            route_mod.gap_store,
            "list_pending_for_freeze",
            lambda _s, *, domain_key, draft_node_ids: [
                {
                    "cluster_key": _CLUSTER,
                    "proposed_label": "Cosmic Web",
                    "layer": "concept",
                    "parent_region_id": "cosmology",
                    "draft_node_id": "",
                    "review_note": "",
                }
            ],
        )

        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["pending_labels"] == ["Cosmic Web"]
        # 件数を出さない（ラベルの列挙のみ）
        assert not any(ch.isdigit() for ch in detail["message"])
        # 凍結は行われていない
        assert [r for r in skeleton_db.skeleton_rows if r["status"] == "frozen"] == []

    def test_freeze_gate_receives_the_current_draft_nodes(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        seen: list[tuple] = []
        _seed_draft(skeleton_db)

        def _fake_pending(_s, *, domain_key, draft_node_ids):
            seen.append((domain_key, set(draft_node_ids)))
            return []

        monkeypatch.setattr(route_mod.gap_store, "list_pending_for_freeze", _fake_pending)
        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 200, response.text
        assert seen == [(_DOMAIN, {"cmb", "cosmology", "galaxies"})]

    def test_freeze_stamps_applied_version_on_incorporated_candidates(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        _seed_draft(skeleton_db)
        monkeypatch.setattr(
            route_mod.gap_store, "list_pending_for_freeze", lambda *a, **k: []
        )
        stamped: list[dict] = []

        def _fake_stamp(_s, *, domain_key, frozen_version, frozen_node_ids):
            stamped.append(
                {
                    "domain_key": domain_key,
                    "version": frozen_version,
                    "nodes": set(frozen_node_ids),
                }
            )
            return [_CLUSTER]

        monkeypatch.setattr(route_mod.gap_store, "stamp_applied_versions", _fake_stamp)

        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.3", "note": ""}
        )
        assert response.status_code == 200, response.text
        assert stamped == [
            {
                "domain_key": _DOMAIN,
                "version": "2026.3",
                "nodes": {"cmb", "cosmology", "galaxies"},
            }
        ]
        assert any(
            "category_gaps_applied" in str(e.get("metadata"))
            for e in skeleton_db.review_events
        )

    def test_gate_failure_does_not_block_the_freeze(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        """候補機構の照会失敗で凍結という主要操作を止めない（fail-open）。"""
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod

        _seed_draft(skeleton_db)

        def _boom(*_a, **_k):
            raise RuntimeError("gap tables unavailable")

        monkeypatch.setattr(route_mod.gap_store, "list_pending_for_freeze", _boom)
        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 200, response.text

    def test_freeze_impact_carries_factual_sentences(
        self, client_and_tokens, skeleton_db, monkeypatch
    ):
        """removed node があるとき、学習者の記録についての事実文を必ず添える（§5.5）。"""
        client, _student, teacher = client_and_tokens
        import routes.atlas as route_mod
        from core import atlas_lifecycle

        monkeypatch.setattr(
            route_mod.gap_store, "list_pending_for_freeze", lambda *a, **k: []
        )
        _seed_frozen(skeleton_db)
        _seed_draft(skeleton_db, concepts=())  # cmb が消える

        response = client.post(
            _FREEZE_PATH, headers=_auth(teacher), json={"version": "2026.2", "note": ""}
        )
        assert response.status_code == 200, response.text
        impact = response.json()["impact"]
        assert impact["removed_node_ids"] == ["cmb"]
        assert atlas_lifecycle.FACT_REMOVED_NODES_HIDE_LEARNER_TRACES in impact["facts"]
        assert not any(ch.isdigit() for f in impact["facts"] for ch in f)


# ---------------------------------------------------------------------------
# 7. 教材管理の案内一行（landscape 管理 API の gap_signals_recorded）
# ---------------------------------------------------------------------------


class TestLandscapePlacementsGapFlag:
    def test_flag_is_set_per_document_and_per_unplaced_domain(
        self, client_and_tokens, monkeypatch
    ):
        client, _student, teacher = client_and_tokens
        import routes.landscape as landscape_routes
        import services as services_module
        from core import atlas_store
        from core.atlas_gaps import store as gap_store
        from core.deliberation import refs as refs_module

        class _Session:
            def execute(self, stmt, params=None):
                raise AssertionError("unexpected SQL")

            def close(self):
                pass

        monkeypatch.setattr(landscape_routes, "_session", lambda: _Session())
        monkeypatch.setattr(
            landscape_routes.landscape_store,
            "list_for_document",
            lambda *a, **k: [],
        )
        monkeypatch.setattr(landscape_routes, "_document_titles", lambda *a, **k: {})
        monkeypatch.setattr(landscape_routes, "_last_run_at", lambda *a, **k: None)
        monkeypatch.setattr(atlas_store, "list_domains", lambda _s: [])
        monkeypatch.setattr(
            atlas_store, "load_learner_skeleton", lambda key, _s=None: None
        )
        monkeypatch.setattr(
            refs_module,
            "document_run_artifacts",
            lambda document_id: {
                "landscape_placement": {
                    "unplaced_domains": [
                        {"domain_key": _DOMAIN, "reason": "対象が一致しません"},
                        {"domain_key": "particle_physics", "reason": "対象が異なります"},
                    ]
                }
            },
        )
        monkeypatch.setattr(
            services_module,
            "resolve_document_access",
            lambda user_id, ref: services_module.DocumentAccess(
                document_id=_DOC1, source_path="mat-1", can_view=True, can_edit=True
            ),
        )
        monkeypatch.setattr(
            gap_store,
            "list_active_signals",
            lambda _s, **k: [{"domain_key": _DOMAIN}],
        )

        body = client.get(
            f"/api/admin/landscape/documents/{_DOC1}/placements", headers=_auth(teacher)
        ).json()

        assert body["gap_signals_recorded"] is True
        flags = {u["domain_key"]: u["gap_signals_recorded"] for u in body["unplaced_domains"]}
        assert flags == {_DOMAIN: True, "particle_physics": False}
        # 件数は出さない（真偽値だけ — LS5）。
        keys = _keys_recursive(body)
        for forbidden in ("gap_signal_count", "signal_count", "count"):
            assert forbidden not in keys


# ---------------------------------------------------------------------------
# 8. ルータ登録・P4（削除 API を作らない）
# ---------------------------------------------------------------------------


class TestRouterRegistration:
    def test_gap_paths_are_mounted(self):
        from api.main import app

        paths = app.openapi()["paths"]
        assert set(paths["/api/admin/cartridges/{cartridge_id}/atlas/gap-candidates"]) == {
            "get"
        }
        for suffix in ("decide", "incorporate-preview", "mark-incorporated"):
            key = f"/api/admin/cartridges/{{cartridge_id}}/atlas/gap-candidates/{suffix}"
            assert set(paths[key]) == {"post"}
        assert set(
            paths["/api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft/from-frozen"]
        ) == {"post"}

    def test_no_delete_route_exists(self):
        import routes.atlas_gaps as routes

        for route in routes.router.routes:
            methods = getattr(route, "methods", set()) or set()
            assert "DELETE" not in methods, getattr(route, "path", "")
