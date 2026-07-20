"""コース⇄地図バインディングのライフサイクル API のルートレベル結合テスト。

対象: `backend/api/routes/atlas.py`
  - `PUT/DELETE /api/admin/courses/{course_id}/atlas-binding/pending` (§4.2, 新設)
  - `POST /api/admin/courses/{course_id}/atlas-binding/propose` の追加フィールド
    (`domains_checked` / `retired_skipped` / `atlas_binding_pending`, §4.1)
  - `PUT /api/admin/courses/{course_id}/atlas-binding` の pending 自動クリア + retired 422
  - `POST /api/admin/cartridges/{cartridge_id}/atlas/retire` / `.../restore` (§4.3)
  - retired ドメインの読み取り専用ガード (generate/draft/freeze の 409)

正本: docs/features/atlas_binding_lifecycle_design.md。

core 層 (atlas_store.py / atlas_lifecycle.py / course_data.py) は実装済み・変更不可のため、
既存 `tests/api/test_atlas_api.py` と同じ方式 (TestClient + `AtlasSkeletonTableFake` の
monkeypatch) でルート層のみを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas  # noqa: E402
from tests.fixtures.atlas_skeletons_fake import (  # noqa: E402
    AtlasSkeletonTableFake,
    make_session_factory,
)

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    backend_dir = str(Path(__file__).resolve().parent.parent)
    api_dir = str(Path(__file__).resolve().parent.parent / "api")
    src_dir = str(Path(__file__).resolve().parent.parent.parent / "src")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _headers(role: str, sub: str = "11111111-1111-1111-1111-111111111111"):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    token = jwt.encode(payload, "test-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def teacher_headers():
    return _headers("TEACHER")


@pytest.fixture
def other_teacher_headers():
    return _headers("TEACHER", sub=OTHER_TEACHER_ID)


@pytest.fixture
def student_headers():
    return _headers("STUDENT")


@pytest.fixture(autouse=True)
def skeleton_db(monkeypatch):
    """migration 027/057: atlas_skeletons + atlas_domain_meta のインメモリフェイクを DB として注入する。"""
    import core.postgres as postgres_module

    fake = AtlasSkeletonTableFake()
    monkeypatch.setattr(postgres_module, "get_session", make_session_factory(fake))
    return fake


@pytest.fixture(autouse=True)
def _no_bundled_cartridges(monkeypatch):
    """実カートリッジ (particle_physics 等) の同梱骨格をテストの対象外にする。

    list_domains() / propose の domains_checked は同梱カートリッジも合成対象にするため、
    これを外さないとリポジトリの実ファイルの有無でテストの期待値が変わってしまう。
    """
    import core.cartridges as cartridges_module

    monkeypatch.setattr(cartridges_module, "list_cartridges", lambda: [])
    cartridges_module.clear_cache()
    yield
    cartridges_module.clear_cache()


def _review_event_action(event: dict) -> str:
    """theory_review_events フェイク行から metadata.action を取り出す (JSON 文字列格納)。"""
    import json as _json

    metadata = event.get("metadata")
    if not metadata:
        return ""
    try:
        return _json.loads(metadata).get("action", "")
    except (TypeError, ValueError):
        return ""


TEACHER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TEACHER_ID = "33333333-3333-3333-3333-333333333333"


def _skeleton(
    domain_key: str = "test_domain",
    status: str = "frozen",
    version: str = "2026.1",
    concept_ids: tuple[str, ...] = ("c1", "c2"),
) -> atlas.AtlasSkeleton:
    concepts = tuple(
        atlas.SkeletonConcept(id=cid, label=cid, layout=atlas.ConceptLayout(x=0.5, y=0.5))
        for cid in concept_ids
    )
    return atlas.AtlasSkeleton(
        cartridge=domain_key,
        status=status,
        version=version if status == "frozen" else "",
        generated_by="model:test",
        reviewed_by=("faculty:t",) if status == "frozen" else (),
        changelog=(atlas.ChangelogEntry(version=version, note="t"),) if status == "frozen" else (),
        regions=(
            atlas.SkeletonRegion(
                id="r1",
                label="領域1",
                layout=atlas.RegionLayout(x=0.1, y=0.1, w=0.4, h=0.4),
                concepts=concepts,
            ),
        ),
        edges=(),
        concept_bindings=(),
    )


def _seed_frozen_domain(skeleton_db, domain_key: str = "test_domain", **kwargs) -> None:
    from core import atlas_store

    atlas_store.insert_frozen(skeleton_db, domain_key, _skeleton(domain_key, status="frozen", **kwargs))


def _seed_course(skeleton_db, course_id="c1", owner=TEACHER_ID, data=None) -> None:
    skeleton_db.courses[course_id] = {
        "user_id": owner,
        "data": data if data is not None else {"title": "テストコース", "topics": []},
    }


@_skip_no_fastapi
class TestSetPending:
    def test_requires_teacher(self, client, student_headers, skeleton_db):
        _seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding/pending",
            headers=student_headers,
            json={"domain_key": "modified_gravity"},
        )
        assert resp.status_code == 403

    def test_requires_owner_or_admin(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db, owner=OTHER_TEACHER_ID)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding/pending",
            headers=teacher_headers,
            json={"domain_key": "modified_gravity"},
        )
        assert resp.status_code == 403

    def test_course_not_found_is_404(self, client, teacher_headers, skeleton_db):
        resp = client.put(
            "/api/admin/courses/no_such_course/atlas-binding/pending",
            headers=teacher_headers,
            json={"domain_key": "modified_gravity"},
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "domain_key",
        ["", "Modified_Gravity", "modified-gravity", "modified gravity", "modified.gravity"],
    )
    def test_invalid_slug_is_422(self, client, teacher_headers, skeleton_db, domain_key):
        _seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding/pending",
            headers=teacher_headers,
            json={"domain_key": domain_key},
        )
        assert resp.status_code == 422

    def test_retired_domain_is_422(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db)
        _seed_frozen_domain(skeleton_db, "retired_domain")
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "retired_domain", note="実験終了")
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding/pending",
            headers=teacher_headers,
            json={"domain_key": "retired_domain"},
        )
        assert resp.status_code == 422

    def test_saves_and_audits(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding/pending",
            headers=teacher_headers,
            json={"domain_key": "modified_gravity"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"course_id": "c1", "atlas_binding_pending": "modified_gravity"}
        assert skeleton_db.courses["c1"]["data"]["atlas_binding_pending"] == "modified_gravity"
        events = [e for e in skeleton_db.review_events if e.get("entity_type") == "atlas_binding"]
        assert any(e.get("new_status") == "modified_gravity" for e in events)
        # 監査 action は設計書 §4.2 の契約値 (entity_type で既にスコープ済みのため短命名)
        assert any(_review_event_action(e) == "pending_set" for e in events)

    def test_new_domain_without_frozen_skeleton_is_allowed(self, client, teacher_headers, skeleton_db):
        """凍結骨格がまだ無い (これから generate する) 新分野の指定は許可される。"""
        _seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding/pending",
            headers=teacher_headers,
            json={"domain_key": "brand_new_domain"},
        )
        assert resp.status_code == 200, resp.text


@_skip_no_fastapi
class TestClearPending:
    def test_requires_teacher(self, client, student_headers, skeleton_db):
        _seed_course(skeleton_db, data={"atlas_binding_pending": "modified_gravity", "topics": []})
        resp = client.delete(
            "/api/admin/courses/c1/atlas-binding/pending", headers=student_headers
        )
        assert resp.status_code == 403

    def test_clears_existing_pending(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db, data={"atlas_binding_pending": "modified_gravity", "topics": []})
        resp = client.delete(
            "/api/admin/courses/c1/atlas-binding/pending", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"course_id": "c1", "atlas_binding_pending": ""}
        assert "atlas_binding_pending" not in skeleton_db.courses["c1"]["data"]

    def test_idempotent_when_absent(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db, data={"topics": []})
        resp = client.delete(
            "/api/admin/courses/c1/atlas-binding/pending", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["atlas_binding_pending"] == ""

    def test_audits_clear_action(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db, data={"atlas_binding_pending": "modified_gravity", "topics": []})
        client.delete("/api/admin/courses/c1/atlas-binding/pending", headers=teacher_headers)
        events = [e for e in skeleton_db.review_events if e.get("entity_type") == "atlas_binding"]
        assert any(_review_event_action(e) == "pending_clear" for e in events)


@_skip_no_fastapi
class TestProposeNewFields:
    def test_domains_checked_and_retired_skipped(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db)
        _seed_frozen_domain(skeleton_db, "domain_a")
        _seed_frozen_domain(skeleton_db, "domain_b")
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "domain_b", note="")
        resp = client.post("/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["domains_checked"] == 1
        assert data["retired_skipped"] == 1
        domain_keys = {p["domain_key"] for p in data["proposals"]}
        assert domain_keys == {"domain_a"}

    def test_retired_domain_without_frozen_skeleton_not_counted(
        self, client, teacher_headers, skeleton_db
    ):
        """凍結骨格を持たない retired ドメインは retired_skipped に数えない
        (照合対象にそもそもならなかったもの)。"""
        _seed_course(skeleton_db)
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "empty_retired_domain", note="")
        resp = client.post("/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["retired_skipped"] == 0

    def test_includes_atlas_binding_pending(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db, data={"atlas_binding_pending": "modified_gravity", "topics": []})
        resp = client.post("/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["atlas_binding_pending"] == "modified_gravity"

    def test_atlas_binding_pending_defaults_to_empty(self, client, teacher_headers, skeleton_db):
        _seed_course(skeleton_db)
        resp = client.post("/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["atlas_binding_pending"] == ""

    def test_current_retired_flag(self, client, teacher_headers, skeleton_db):
        """現行バインド先が retired のとき current_retired=True を返す
        (候補に現れないため、フロントが「維持される・解除は明示操作」を出す材料)。"""
        _seed_frozen_domain(skeleton_db, "domain_a")
        _seed_frozen_domain(skeleton_db, "domain_b")
        _seed_course(skeleton_db, data={"cartridge_id": "domain_b", "topics": []})
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "domain_b", note="")
        resp = client.post("/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["current_cartridge_id"] == "domain_b"
        assert data["current_retired"] is True
        assert "domain_b" not in {p["domain_key"] for p in data["proposals"]}

    def test_current_retired_false_for_active_binding(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        _seed_course(skeleton_db, data={"cartridge_id": "domain_a", "topics": []})
        resp = client.post("/api/admin/courses/c1/atlas-binding/propose", headers=teacher_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["current_retired"] is False


@_skip_no_fastapi
class TestSaveBindingLifecycleInteractions:
    def test_save_binding_clears_pending(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        _seed_course(
            skeleton_db,
            data={
                "atlas_binding_pending": "some_other_domain",
                "topics": [{"id": "t0", "title": "topic0"}],
            },
        )
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding",
            headers=teacher_headers,
            json={"cartridge_id": "domain_a", "topic_bindings": []},
        )
        assert resp.status_code == 200, resp.text
        assert "atlas_binding_pending" not in skeleton_db.courses["c1"]["data"]

    def test_unbind_also_clears_pending(self, client, teacher_headers, skeleton_db):
        """解除保存 (cartridge_id="") でも pending はクリアされる (§2.3)。"""
        _seed_course(
            skeleton_db,
            data={"atlas_binding_pending": "some_other_domain", "topics": []},
        )
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding",
            headers=teacher_headers,
            json={"cartridge_id": "", "topic_bindings": []},
        )
        assert resp.status_code == 200, resp.text
        assert "atlas_binding_pending" not in skeleton_db.courses["c1"]["data"]

    def test_save_binding_retired_domain_is_422(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "retired_domain")
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "retired_domain", note="")
        _seed_course(skeleton_db)
        resp = client.put(
            "/api/admin/courses/c1/atlas-binding",
            headers=teacher_headers,
            json={"cartridge_id": "retired_domain", "topic_bindings": []},
        )
        assert resp.status_code == 422


@_skip_no_fastapi
class TestRetireRestoreRoutes:
    def test_retire_requires_teacher(self, client, student_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/retire",
            headers=student_headers,
            json={"note": ""},
        )
        assert resp.status_code == 403

    def test_retire_unknown_domain_is_404(self, client, teacher_headers, skeleton_db):
        resp = client.post(
            "/api/admin/cartridges/no_such_domain/atlas/retire",
            headers=teacher_headers,
            json={"note": ""},
        )
        assert resp.status_code == 404

    def test_retire_succeeds_and_audits(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/retire",
            headers=teacher_headers,
            json={"note": "実験的骨格のため"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"cartridge_id": "domain_a", "lifecycle": "retired", "notified": 0}
        assert skeleton_db.domain_meta["domain_a"]["lifecycle"] == "retired"
        events = [e for e in skeleton_db.review_events if e.get("entity_type") == "atlas_skeleton"]
        assert any(_review_event_action(e) == "retire" for e in events)

    def test_retire_notifies_editor_excluding_actor(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """関係教員 (骨格編集履歴のある教員) へ通知され、actor 本人は除外される (§3.4)。

        `cross_layer_notify.notify_user` は現在 ``core.postgres.get_session()``
        (モジュール属性経由) でセッションを取るため monkeypatch が効くが、
        本テストの関心は recipient 解決と route の配線なので、配送本体
        (`notify_user`) を直接 monkeypatch して検証する。
        """
        from core import atlas_store
        from core.status import cross_layer_notify as cross_layer_notify_module

        delivered_to: list[str] = []

        def _fake_notify_user(recipient_id, kind, entity_type, entity_id, payload=None):
            delivered_to.append(recipient_id)
            return True

        monkeypatch.setattr(cross_layer_notify_module, "notify_user", _fake_notify_user)

        atlas_store.insert_frozen(
            skeleton_db, "domain_a", _skeleton("domain_a"), user_id=OTHER_TEACHER_ID
        )
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/retire",
            headers=teacher_headers,  # actor = TEACHER_ID (異なる編集者 OTHER_TEACHER_ID へ配送)
            json={"note": ""},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["notified"] == 1
        assert delivered_to == [OTHER_TEACHER_ID]

    def test_retire_already_retired_is_409(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "domain_a", note="")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/retire",
            headers=teacher_headers,
            json={"note": ""},
        )
        assert resp.status_code == 409

    def test_restore_requires_teacher(self, client, student_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "domain_a", note="")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/restore", headers=student_headers
        )
        assert resp.status_code == 403

    def test_restore_not_retired_is_409(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/restore", headers=teacher_headers
        )
        assert resp.status_code == 409

    def test_restore_succeeds_and_audits(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, "domain_a", note="")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/restore", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cartridge_id": "domain_a", "lifecycle": "active"}
        assert skeleton_db.domain_meta["domain_a"]["lifecycle"] == "active"
        events = [e for e in skeleton_db.review_events if e.get("entity_type") == "atlas_skeleton"]
        assert any(_review_event_action(e) == "restore" for e in events)


@_skip_no_fastapi
class TestRetiredReadOnlyGuard:
    """retired ドメインは generate / draft 保存 / freeze が 409 (読み取り専用)。"""

    def _retire(self, skeleton_db, domain_key="domain_a"):
        _seed_frozen_domain(skeleton_db, domain_key)
        from core import atlas_store

        atlas_store.retire_domain(skeleton_db, domain_key, note="")

    def test_generate_on_retired_domain_is_409(self, client, teacher_headers, skeleton_db):
        self._retire(skeleton_db)
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/generate",
            headers=teacher_headers,
            json={"force": True, "domain": {"name": "domain_a"}},
        )
        assert resp.status_code == 409

    def test_save_draft_on_retired_domain_is_409(self, client, teacher_headers, skeleton_db):
        self._retire(skeleton_db)
        skeleton_dict = {
            "atlas_skeleton": {
                "version": "",
                "cartridge": "domain_a",
                "status": "draft",
                "generated_by": "model:test",
                "reviewed_by": [],
                "regions": [
                    {
                        "id": "region_a",
                        "label": "領域A",
                        "layout": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
                        "concepts": [
                            {
                                "id": "concept_a",
                                "label": "概念A",
                                "layout": {"x": 0.5, "y": 0.5},
                            }
                        ],
                    }
                ],
                "edges": [],
                "concept_bindings": [],
            }
        }
        resp = client.put(
            "/api/admin/cartridges/domain_a/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": skeleton_dict},
        )
        assert resp.status_code == 409

    def test_freeze_on_retired_domain_is_409(self, client, teacher_headers, skeleton_db):
        """retired なら freeze は 409 (lifecycle ガードが draft 有無の判定より先に効く)。"""
        from core import atlas_store

        _seed_frozen_domain(skeleton_db, "domain_a")
        atlas_store.save_draft(
            skeleton_db,
            "domain_a",
            _skeleton("domain_a", status="draft"),
            expected_revision=None,
        )
        atlas_store.retire_domain(skeleton_db, "domain_a", note="")
        resp = client.post(
            "/api/admin/cartridges/domain_a/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.2", "note": ""},
        )
        assert resp.status_code == 409

    def test_learner_visibility_is_unaffected_by_retire(self, client, student_headers, skeleton_db):
        """AB3: retired 後もバインド済みコースの学習者向け骨格表示は不変。"""
        self._retire(skeleton_db)
        resp = client.get(
            "/api/learning/atlas/domain_a/skeleton", headers=student_headers
        )
        assert resp.status_code == 200


@_skip_no_fastapi
class TestDiscardDraft:
    """DELETE /api/admin/cartridges/{id}/atlas/skeleton/draft (§3.1 後始末)。

    draft は共有物・履歴ではなく作業コピーのため破棄を許可する。ポイントは
    **retired ドメインでも許可される**こと (retire 後に残った draft の唯一の後始末経路)。
    """

    def _seed_draft(self, skeleton_db, domain_key="domain_a"):
        from core import atlas_store

        atlas_store.save_draft(
            skeleton_db,
            domain_key,
            _skeleton(domain_key, status="draft"),
            expected_revision=None,
        )

    def test_requires_teacher(self, client, student_headers, skeleton_db):
        self._seed_draft(skeleton_db)
        resp = client.delete(
            "/api/admin/cartridges/domain_a/atlas/skeleton/draft", headers=student_headers
        )
        assert resp.status_code == 403

    def test_discards_active_domain_draft(self, client, teacher_headers, skeleton_db):
        self._seed_draft(skeleton_db)
        resp = client.delete(
            "/api/admin/cartridges/domain_a/atlas/skeleton/draft", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cartridge_id": "domain_a", "discarded": True}
        assert skeleton_db._drafts("domain_a") == []

    def test_discards_retired_domain_draft(self, client, teacher_headers, skeleton_db):
        """retired でも破棄は許可 (generate/保存/freeze の 409 とは対照的な唯一の書き込み)。"""
        from core import atlas_store

        _seed_frozen_domain(skeleton_db, "domain_a")
        self._seed_draft(skeleton_db)
        atlas_store.retire_domain(skeleton_db, "domain_a", note="実験終了")
        resp = client.delete(
            "/api/admin/cartridges/domain_a/atlas/skeleton/draft", headers=teacher_headers
        )
        assert resp.status_code == 200, resp.text
        assert skeleton_db._drafts("domain_a") == []
        # 凍結版履歴は不変 (AB3)
        assert skeleton_db._frozen("domain_a")

    def test_no_draft_is_404(self, client, teacher_headers, skeleton_db):
        _seed_frozen_domain(skeleton_db, "domain_a")
        resp = client.delete(
            "/api/admin/cartridges/domain_a/atlas/skeleton/draft", headers=teacher_headers
        )
        assert resp.status_code == 404

    def test_audits_discard(self, client, teacher_headers, skeleton_db):
        self._seed_draft(skeleton_db)
        client.delete(
            "/api/admin/cartridges/domain_a/atlas/skeleton/draft", headers=teacher_headers
        )
        events = [e for e in skeleton_db.review_events if e.get("entity_type") == "atlas_skeleton"]
        assert any(_review_event_action(e) == "draft_discard" for e in events)


@_skip_no_fastapi
class TestLifecycleWriteSerialization:
    """check-then-write 競合の防止 (レビュー v2 P1-2)。

    lifecycle 確認と書き込みは domain 単位の advisory lock で直列化し、
    セッションを跨ぐ generate / freeze は書き込みトランザクション内で再確認する。
    """

    def test_generate_rechecks_lifecycle_after_llm(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """LLM 生成中に別教員が retire したら、draft を保存せず 409 を返す。"""
        from core import atlas_generator, atlas_store

        def _generate_and_retire(cartridge_id, model=None, domain_meta=None):
            # LLM 生成に相当する時間の間に retire が割り込んだ状況を決定論的に再現する
            atlas_store.retire_domain(skeleton_db, cartridge_id, note="生成中に廃止")
            return _skeleton(cartridge_id, status="draft")

        monkeypatch.setattr(atlas_generator, "generate_skeleton_draft", _generate_and_retire)
        resp = client.post(
            "/api/admin/cartridges/race_domain/atlas/skeleton/generate",
            headers=teacher_headers,
            json={"domain": {"name": "競合テスト", "description": ""}},
        )
        assert resp.status_code == 409, resp.text
        assert skeleton_db._drafts("race_domain") == []

    def test_freeze_rechecks_lifecycle_before_insert(
        self, client, teacher_headers, skeleton_db, monkeypatch
    ):
        """入口チェックと書き込みの間に retire されたら、凍結版を挿入せず 409 を返す。"""
        import api.routes.atlas as atlas_routes
        from core import atlas_store

        atlas_store.save_draft(
            skeleton_db,
            "race_domain",
            _skeleton("race_domain", status="draft"),
            expected_revision=None,
        )

        def _retire_then_no_reports(session, cartridge_id):
            atlas_store.retire_domain(skeleton_db, cartridge_id, note="凍結処理中に廃止")
            return []

        monkeypatch.setattr(
            atlas_routes.atlas_reports, "accepted_unapplied_reports", _retire_then_no_reports
        )
        resp = client.post(
            "/api/admin/cartridges/race_domain/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2026.1", "note": ""},
        )
        assert resp.status_code == 409, resp.text
        assert skeleton_db._frozen("race_domain") == []
        # draft は失われない (P4: rollback で保持)
        assert skeleton_db._drafts("race_domain")

    def test_write_paths_take_domain_lock(self, client, teacher_headers, skeleton_db):
        """retire が advisory lock を取得している (書き込み系との直列化の実在確認)。"""
        _seed_frozen_domain(skeleton_db, "domain_a")
        client.post("/api/admin/cartridges/domain_a/atlas/retire", headers=teacher_headers, json={})
        assert any("pg_advisory_xact_lock" in sql for sql, _ in skeleton_db.calls)
