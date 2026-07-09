"""分野の地図 — Atlas API (Issue E-3) の結合テスト。

- GET /api/atlas がフィクスチャ (issue B/C) 互換の形 + §11 のトップレベル配列を返す
  (受け入れ条件5: フロントの差し替えが設定切替で済むこと)
- 個人層 (いまここ・足跡・隣接の光) の実行時合成
- L1 応答 p95 < 300ms (キャッシュヒット時。個人層合成を含めた計測。受け入れ条件4)
- 応答テキストに評価語が含まれない (受け入れ条件6 の e2e)

DB は FakeSession (core.postgres.get_session を monkeypatch) で代替する。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

STUDENT_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client():
    backend_dir = str(Path(__file__).resolve().parents[2])
    api_dir = str(Path(__file__).resolve().parents[2] / "api")
    src_dir = str(Path(__file__).resolve().parents[3] / "src")
    for p in (backend_dir, api_dir, src_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _headers(role: str = "STUDENT", sub: str = STUDENT_ID):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    token = jwt.encode(payload, "test-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sandbox_cartridges(tmp_path, monkeypatch):
    from core import cartridges as cartridges_module

    src = cartridges_module._cartridges_root() / "particle_physics"
    dst = tmp_path / "particle_physics"
    shutil.copytree(src, dst)
    monkeypatch.setattr(cartridges_module, "_cartridges_root", lambda: tmp_path)
    cartridges_module.clear_cache()
    yield tmp_path
    cartridges_module.clear_cache()


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, router):
        self.calls = []
        self.router = router

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return self.router(sql, params or {})

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    class Factory:
        def __init__(self):
            self.sessions = []
            self.route = lambda sql, params: FakeResult()

        def __call__(self):
            session = FakeSession(lambda sql, p: self.route(sql, p))
            self.sessions.append(session)
            return session

        def all_calls(self):
            return [call for s in self.sessions for call in s.calls]

    factory = Factory()
    import core.postgres as postgres_module

    monkeypatch.setattr(postgres_module, "get_session", factory)
    return factory


# ---------------------------------------------------------------------------
# キャッシュ行の準備 (E-1 の compute_overlay_rows を実データ相当スナップショットに適用)
# ---------------------------------------------------------------------------


def _corpus_snapshot():
    from core import atlas_state as st

    def claim(cid, ctype, concepts, eq_no=None, section=None, review="teacher_review_required"):
        return {
            "id": cid,
            "document_id": "doc-1",
            "claim_type": ctype,
            "concepts": concepts,
            "equation": {"eq_no": eq_no} if eq_no else {},
            "support_status": "source_backed",
            "evidence_text": "verbatim quote ...",
            "review_status": review,
            "source_scope": {"section": section} if section else {},
        }

    return st.CorpusSnapshot(
        components=[
            {
                "id": "comp-ope",
                "name": "演算子積展開",
                "document_id": "doc-1",
                "review_status": "teacher_review_required",
                "status": "draft",
                "evidence_claims": ["cl-ope"],
                "source_scope": {"scope": "高エネルギー・摂動領域"},
            },
            {
                "id": "comp-bc",
                "name": "B→c 和則",
                "document_id": "doc-1",
                "review_status": "teacher_review_required",
                "status": "draft",
                "evidence_claims": ["cl-bc1", "cl-bc2", "cl-bc3"],
                "source_scope": {},
            },
            {
                "id": "comp-vcb",
                "name": "Vcb の測定間の不一致",
                "document_id": "doc-1",
                "review_status": "teacher_review_required",
                "status": "draft",
                "evidence_claims": ["cl-vcb"],
                "source_scope": {},
            },
            {
                "id": "comp-extra",
                "name": "ボレル変換の応用",
                "document_id": "doc-1",
                "review_status": "teacher_review_required",
                "status": "draft",
                "evidence_claims": [],
                "source_scope": {},
            },
        ],
        claims=[
            claim("cl-ope", "equation_definition", ["演算子積展開"], eq_no="5", section="2"),
            claim("cl-bc1", "equation_relation", ["B→c 和則"], eq_no="12", section="3.2"),
            claim("cl-bc2", "derivation_step", ["B→c 和則"], eq_no="14"),
            claim("cl-bc3", "result", ["B→c 和則"]),
            claim("cl-vcb", "diagnostic_claim", ["Vcb の測定間の不一致"]),
            claim("cl-dual", "assumption", ["クォーク・ハドロン双対性"], review="teacher_approved"),
        ],
        graphs=[
            {
                "document_id": "doc-1",
                "graph": {
                    "nodes": [
                        {"id": "d1", "graph_layer": "main", "label": "相関関数を定義",
                         "stage": "theory_basis", "source_backing_status": "source_backed",
                         "linked_equation_ids": ["eq_3"]},
                        {"id": "d2", "graph_layer": "main", "label": "OPE で展開",
                         "stage": "equation_system", "source_backing_status": "source_backed",
                         "linked_equation_ids": ["eq_5", "eq_8"]},
                        {"id": "gy", "graph_layer": "main", "label": "高次項の抑制",
                         "stage": "elimination", "source_backing_status": "inferred"},
                        {"id": "d4", "graph_layer": "main", "label": "和則（整合関係）",
                         "stage": "consistency_relation",
                         "source_backing_status": "source_backed",
                         "linked_equation_ids": ["eq_12"]},
                        {"id": "d5", "graph_layer": "main", "label": "Vcb の抽出へ",
                         "stage": "diagnostic_application",
                         "source_backing_status": "partially_source_backed"},
                    ]
                },
            }
        ],
        explanations=[
            {
                "component_id": "comp-bc",
                "explanation_id": "exp-bc",
                "kind": "standard",
                "shared": False,
                "review_status": "teacher_review_required",
                "author_name": "",
                "endorser_count": 3,
                "strong_count": 1,
                "expertise_breadth": 2,
            },
            {
                "component_id": "comp-vcb",
                "explanation_id": "exp-vcb-a",
                "kind": "personal",
                "shared": True,
                "review_status": "teacher_review_required",
                "author_name": "A先生",
                "endorser_count": 0,
                "strong_count": 0,
                "expertise_breadth": 0,
            },
            {
                "component_id": "comp-vcb",
                "explanation_id": "exp-vcb-b",
                "kind": "personal",
                "shared": True,
                "review_status": "teacher_review_required",
                "author_name": "B先生",
                "endorser_count": 0,
                "strong_count": 0,
                "expertise_breadth": 0,
            },
        ],
        vectors={"comp-ope": [1.0, 0.0], "comp-extra": [0.95, 0.05]},
    )


_SIGNATURE = "||||"  # 署名クエリを ('', '', '', '', '') で応答した場合の合成値


def _cache_rows():
    """バンドル骨格 + 実データ相当スナップショット → キャッシュ行タプル。"""
    from core import atlas_state as st
    from core import cartridges as cartridges_module

    cartridge = cartridges_module.load_cartridge("particle_physics")
    skeleton = cartridge.learner_atlas_skeleton
    rows = st.compute_overlay_rows(
        skeleton, _corpus_snapshot(), cartridge_id="particle_physics"
    )
    tuples = []
    for r in rows:
        tuples.append(
            (
                r["entry_type"], r["entry_id"], r["region_id"], r["label"], r["status"],
                r["status_source"], r["verify_line"], r["endorse_line"],
                r["learn_enabled"], r["evid_enabled"],
                r["layout"], r["placement"], r["evidence"],
            )
        )
    return tuples


def _traces_rows():
    # (node_id, last_seen, first_seen): ope → disp → bc_sumrule (最新 = いまここ)
    return [
        ("ope", "2026-07-01T09:10:00", "2026-07-01T09:00:00"),
        ("disp", "2026-07-01T09:20:00", "2026-07-01T09:15:00"),
        ("bc_sumrule", "2026-07-01T09:40:00", "2026-07-01T09:30:00"),
        ("not_in_map", "2026-07-01T09:50:00", "2026-07-01T09:45:00"),
    ]


@pytest.fixture
def warm_cache(fake_db, sandbox_cartridges):
    """キャッシュヒット経路: cache 行あり・署名一致・dirty なし。"""
    rows = _cache_rows()
    meta = json.dumps({"signature": _SIGNATURE})

    def route(sql, params):
        if "atlas_overlay_dirty" in sql and sql.startswith("SELECT"):
            return FakeResult()
        if "entry_type = 'meta'" in sql:
            return FakeResult([(meta,)])
        if "FROM atlas_overlay_cache" in sql:
            return FakeResult(rows)
        if "FROM interest_traces" in sql:
            return FakeResult(_traces_rows())
        if "document_analysis_runs" in sql:  # 署名クエリ
            return FakeResult([("", "", "", "", "")])
        return FakeResult()

    fake_db.route = route
    return fake_db


# ---------------------------------------------------------------------------
# GET /api/atlas
# ---------------------------------------------------------------------------


class TestGetAtlas:
    def test_requires_auth(self, client, warm_cache):
        res = client.get("/api/atlas?cartridge=particle_physics")
        assert res.status_code in (401, 403)

    def test_unknown_cartridge_is_404(self, client, warm_cache):
        res = client.get("/api/atlas?cartridge=nope", headers=_headers())
        assert res.status_code == 404

    def test_invalid_level_is_422(self, client, warm_cache):
        res = client.get("/api/atlas?cartridge=particle_physics&level=5", headers=_headers())
        assert res.status_code == 422

    def test_fixture_compatible_shape(self, client, warm_cache):
        """受け入れ条件5: フィクスチャ (window.ATLAS_FIXTURE) と同じ形で描画できる。"""
        res = client.get("/api/atlas?cartridge=particle_physics", headers=_headers())
        assert res.status_code == 200
        data = res.json()
        # フィクスチャ互換キー
        for key in ("skeleton_version", "cartridge", "provenance", "crumbs",
                    "initial_selection", "nodes", "levels"):
            assert key in data, key
        assert data["skeleton_version"] == "2026.1"
        # §11 のトップレベル配列
        for key in ("regions", "nodes_list", "edges", "me"):
            assert key in data, key
        # ノード辞書の項目 (atlas-overlay.js が参照するフィールド)
        node = data["nodes"]["ope"]
        for key in ("label", "pill", "status", "verify", "endorse", "learn", "evid"):
            assert key in node, key
        # レベル別データ
        l1 = data["levels"]["1"]
        assert l1["viewBox"] == [680, 370]
        region_kinds = {r["id"]: r["kind"] for r in l1["regions"]}
        assert region_kinds["eft_sumrules"] == "lit"
        assert region_kinds["lattice_qcd"] == "fog"
        fog = next(r for r in l1["regions"] if r["id"] == "lattice_qcd")
        assert len(fog["dots"]) == 3
        assert data["levels"]["3"]["chain"] == [
            "step_d1", "step_d2", "step_gy", "step_d4", "step_d5"
        ]

    def test_skeleton_concept_coords_are_region_relative(self, client, warm_cache):
        """骨格概念の layout は領域内相対 (0-1)。L1/L2 では所属領域 box の offset/size を
        適用したキャンバス絶対座標で返る (seed yaml / atlas-fixture.js /
        atlas-draft-preview.js の conceptAbs() と一致)。

        回帰: 以前 atlas_view.py は相対座標を絶対として scale していたため、領域の
        右下寄りにある概念 (dual: 相対 0.72,0.8) が領域ボックスの外へはみ出していた。
        """
        from core import cartridges as cartridges_module

        data = client.get(
            "/api/atlas?cartridge=particle_physics", headers=_headers()
        ).json()
        skeleton = cartridges_module.load_cartridge(
            "particle_physics"
        ).learner_atlas_skeleton

        l1 = data["levels"]["1"]
        w1, h1 = l1["viewBox"]
        l1_by_id = {n["id"]: n for n in l1["nodes"]}

        # ope: 相対 (0.22,0.39), 領域 eft_sumrules (0.06,0.1,0.53,0.62)
        # → 絶対 x = 0.06 + 0.22*0.53 = 0.1766 → round(0.1766*680) = 120 (fixture と一致)
        assert l1_by_id["ope"]["x"] == round((0.06 + 0.22 * 0.53) * w1)  # == 120

        # 全骨格概念が「所属領域のボックス内」に収まる (相対解釈なら常に真、
        # 絶対誤解釈だと dual のような概念が領域外へ出る)
        region_box = {}
        for region in skeleton.regions:
            lo = region.layout
            if lo is not None:
                region_box[region.id] = (
                    round(lo.x * w1), round(lo.y * h1),
                    round(lo.w * w1), round(lo.h * h1),
                )
        concept_region = {
            c.id: r.id for r in skeleton.regions for c in r.concepts
        }
        for node in l1["nodes"]:
            box = region_box.get(concept_region.get(node["id"]))
            if box is None:
                continue
            rx, ry, rw, rh = box
            assert rx - 1 <= node["x"] <= rx + rw + 1, f"{node['id']} が領域 x 範囲外"
            assert ry - 1 <= node["y"] <= ry + rh + 1, f"{node['id']} が領域 y 範囲外"

        # L2 でも同じ絶対化が効く (dual の絶対 x を明示検証)
        l2 = data["levels"]["2"]
        w2, _ = l2["viewBox"]
        l2_by_id = {n["id"]: n for n in l2["nodes"]}
        assert l2_by_id["dual"]["x"] == round((0.06 + 0.72 * 0.53) * w2)  # 領域内に収まる

        # nodes_list (§11 構造化出力) も絶対正規化座標で一貫する
        nodes_list = {n["id"]: n for n in data["nodes_list"]}
        assert nodes_list["ope"]["layout"]["x"] == pytest.approx(0.06 + 0.22 * 0.53)

    def test_derived_statuses_over_api(self, client, warm_cache):
        data = client.get(
            "/api/atlas?cartridge=particle_physics", headers=_headers()
        ).json()
        nodes = data["nodes"]
        assert nodes["dual"]["status"] == "assumed"
        assert nodes["vcb"]["status"] == "contested"
        assert nodes["ope"]["status"] == "verified"  # 訪問済み → 台帳状態のまま
        assert nodes["step_gy"]["status"] == "gap"
        assert nodes["step_gy"]["learn"] is False and nodes["step_gy"]["evid"] is False
        # 霧領域も詳細パネル用に選択できる (概念ラベルは持たない)
        assert nodes["lattice_qcd"]["status"] == "fog"
        assert nodes["lattice_qcd"]["learn"] is False

    def test_personal_layer_synthesis(self, client, warm_cache):
        """個人層のみ実行時合成: いまここ・足跡・隣接の光・未訪問の減衰。"""
        data = client.get(
            "/api/atlas?cartridge=particle_physics", headers=_headers()
        ).json()
        me = data["me"]
        assert me["now"] == "bc_sumrule"
        assert me["footprints"] == ["ope", "disp", "bc_sumrule"]
        # glow: 未訪問 ∧ いまここと概念グラフ上で隣接 (bc_sumrule — vcb)
        assert me["glow"] == ["vcb"]
        assert data["nodes"]["vcb"].get("glow") is True
        # いまここの表示
        now_node = data["nodes"]["bc_sumrule"]
        assert now_node["status"] == "now"
        assert now_node["verify"].endswith("あなたの学習の現在地。")
        # 初期選択: L1/L2 = いまここ、L3 = 行間ステップ
        assert data["initial_selection"]["1"] == "bc_sumrule"
        assert data["initial_selection"]["3"] == "step_gy"
        # 未訪問 verified はグレー表示に減衰 (検証行は台帳状態のまま)
        assert data["nodes"]["disp"]["status"] == "verified"  # 訪問済み

    def test_focus_overrides_initial_selection(self, client, warm_cache):
        data = client.get(
            "/api/atlas?cartridge=particle_physics&focus=dual", headers=_headers()
        ).json()
        assert data["initial_selection"]["1"] == "dual"
        assert data["initial_selection"]["2"] == "dual"

    def test_no_evaluative_language_over_api(self, client, warm_cache):
        """受け入れ条件6: API 応答の検証行・承認行に評価語が無い。"""
        from core import atlas_state as st

        data = client.get(
            "/api/atlas?cartridge=particle_physics", headers=_headers()
        ).json()
        for node in data["nodes"].values():
            assert st.find_evaluative_language(node["verify"]) == [], node["verify"]
            assert st.find_evaluative_language(node["endorse"]) == [], node["endorse"]

    def test_cold_start_triggers_sync_refresh(self, client, fake_db, sandbox_cartridges):
        """キャッシュ未構築 (cold) では同期リフレッシュが走り、骨格は霧で返る。"""

        def route(sql, params):
            return FakeResult()  # すべて空 = コーパスなし・キャッシュなし

        fake_db.route = route
        res = client.get("/api/atlas?cartridge=particle_physics", headers=_headers())
        assert res.status_code == 200
        data = res.json()
        # 取り込み前: 全領域が霧
        kinds = {r["id"]: r["kind"] for r in data["levels"]["1"]["regions"]}
        assert set(kinds.values()) == {"fog"}
        # 同期リフレッシュ (INSERT) が実行された
        assert any(
            "INSERT INTO atlas_overlay_cache" in sql for sql, _ in fake_db.all_calls()
        )


# ---------------------------------------------------------------------------
# GET /api/atlas/node/{id}
# ---------------------------------------------------------------------------


class TestGetAtlasNode:
    def test_detail_panel_payload(self, client, warm_cache):
        res = client.get(
            "/api/atlas/node/bc_sumrule?cartridge=particle_physics", headers=_headers()
        )
        assert res.status_code == 200
        data = res.json()
        assert data["label"] == "B→c 和則"
        assert data["status"] == "verified"
        assert "原文3本に裏付け" in data["verify_line"]
        assert "式(12)" in data["evidence_refs"]
        assert data["endorsements"][0]["endorser_count"] == 3
        assert data["skeleton_version"] == "2026.1"

    def test_assumed_node_detail(self, client, warm_cache):
        data = client.get(
            "/api/atlas/node/dual?cartridge=particle_physics", headers=_headers()
        ).json()
        assert data["status"] == "assumed"
        assert "記帳された直接検証なし" in data["verify_line"]
        assert data["endorsements"] == []

    def test_unknown_node_is_404(self, client, warm_cache):
        res = client.get(
            "/api/atlas/node/nope?cartridge=particle_physics", headers=_headers()
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/cartridges/{id}/atlas/overlay/refresh (E-1 明示実行。routes/atlas.py)
# ---------------------------------------------------------------------------


class TestOverlayRefreshEndpoint:
    def test_teacher_can_refresh(self, client, fake_db, sandbox_cartridges):
        res = client.post(
            "/api/admin/cartridges/particle_physics/atlas/overlay/refresh",
            headers=_headers("TEACHER"),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["skeleton_version"] == "2026.1"
        assert data["regions"] == 4
        # キャッシュの差し替えが実行された
        calls = [sql for sql, _ in fake_db.all_calls()]
        assert any("DELETE FROM atlas_overlay_cache" in s for s in calls)
        assert any("INSERT INTO atlas_overlay_cache" in s for s in calls)

    def test_student_is_forbidden(self, client, fake_db, sandbox_cartridges):
        res = client.post(
            "/api/admin/cartridges/particle_physics/atlas/overlay/refresh",
            headers=_headers("STUDENT"),
        )
        assert res.status_code == 403

    def test_requires_auth(self, client, fake_db, sandbox_cartridges):
        res = client.post(
            "/api/admin/cartridges/particle_physics/atlas/overlay/refresh"
        )
        assert res.status_code in (401, 403)

    def test_without_frozen_skeleton_is_404(self, client, fake_db, sandbox_cartridges):
        (sandbox_cartridges / "particle_physics" / "atlas" / "skeleton.yaml").unlink()
        from core import cartridges as cartridges_module

        cartridges_module.clear_cache()
        res = client.post(
            "/api/admin/cartridges/particle_physics/atlas/overlay/refresh",
            headers=_headers("TEACHER"),
        )
        assert res.status_code == 404


class TestGetAtlasNodeAuth:
    def test_node_detail_requires_auth(self, client, warm_cache):
        res = client.get("/api/atlas/node/dual?cartridge=particle_physics")
        assert res.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 性能 (受け入れ条件4): キャッシュヒット時の L1 応答 p95 < 300ms
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_l1_response_p95_under_300ms_on_cache_hit(self, client, warm_cache):
        """個人層合成を含めた p95 計測 (E-3 チェックリストの CI 計測)。"""
        headers = _headers()
        # ウォームアップ (アプリ初期化コストを除外)
        client.get("/api/atlas?cartridge=particle_physics", headers=headers)
        samples = []
        for _ in range(40):
            start = time.perf_counter()
            res = client.get("/api/atlas?cartridge=particle_physics", headers=headers)
            samples.append(time.perf_counter() - start)
            assert res.status_code == 200
        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        assert p95 < 0.3, f"p95={p95 * 1000:.1f}ms"


# ---------------------------------------------------------------------------
# gap4: データソース既定 (runtime-config)
# ---------------------------------------------------------------------------


class TestRuntimeConfig:
    def test_default_is_api(self, client):
        """本番でモック地図が出ないよう、既定は api (認証不要の軽量フラグ)。"""
        res = client.get("/api/atlas/runtime-config")
        assert res.status_code == 200
        assert res.json()["data_source"] == "api"


# ---------------------------------------------------------------------------
# gap2/gap3: get_atlas の course/topic 対応
# ---------------------------------------------------------------------------


class TestCourseAware:
    def test_course_resolves_cartridge_and_topic_focus(self, client, warm_cache, monkeypatch):
        import services

        course_data = {
            "cartridge_id": "particle_physics",
            "sources": [],
            "topics": [{"id": "t1", "title": "演算子積展開"}],
        }
        monkeypatch.setattr(services, "get_course_data", lambda uid, cid: course_data)
        res = client.get("/api/atlas?course=c1&topic=t1", headers=_headers())
        assert res.status_code == 200
        data = res.json()
        # gap3: コースからカートリッジが解決される
        assert data["cartridge"] == "particle_physics"
        # gap2: topic → 骨格概念の focus がサーバ側で解決される
        assert data["focus"] == "ope"
        assert data["initial_selection"]["1"] == "ope"

    def test_missing_cartridge_and_course_is_422(self, client, warm_cache):
        res = client.get("/api/atlas", headers=_headers())
        assert res.status_code == 422


class TestDerivedCartridgeGate:
    """gap3 hardening: 導出カートリッジの妥当性ゲート。

    解析パイプラインは既定カートリッジで走るため、`document_analysis_runs` 由来の
    導出だけでは別分野のコース (例: 修正重力理論) にも particle_physics の地図が
    返ってしまう。コースが骨格へ足がかりを持たない場合は 404 (骨格なし扱い) に
    縮退し、フロントは地図領域ごと非表示にする (issue F 受け入れ条件6 と同じ縮退)。
    """

    def test_derived_cartridge_without_anchor_is_404(self, client, warm_cache, monkeypatch):
        """明示指定なし + どのトピックも骨格概念に対応しない → 404。"""
        import services

        course_data = {
            "sources": [{"material_id": "m-dhost"}],
            "topics": [
                {"id": "t1", "title": "修正重力理論のテストとしての大規模構造"},
                {"id": "t2", "title": "局所バイアス模型の導入"},
            ],
        }
        monkeypatch.setattr(services, "get_course_data", lambda uid, cid: course_data)
        res = client.get("/api/atlas?course=c-dhost&topic=t1", headers=_headers())
        assert res.status_code == 404

    def test_derived_cartridge_with_label_anchor_is_200(self, client, warm_cache, monkeypatch):
        """明示指定なしでも topic が骨格概念にラベル一致すれば地図を返す。"""
        import services

        course_data = {
            "sources": [{"material_id": "m-bc"}],
            "topics": [{"id": "t1", "title": "演算子積展開"}],
        }
        monkeypatch.setattr(services, "get_course_data", lambda uid, cid: course_data)
        res = client.get("/api/atlas?course=c-bc&topic=t1", headers=_headers())
        assert res.status_code == 200
        assert res.json()["cartridge"] == "particle_physics"

    def test_explicit_course_cartridge_skips_gate(self, client, warm_cache, monkeypatch):
        """course_data.cartridge_id の明示指定 (authoring-time の意思) はゲートを通さない。"""
        import services

        course_data = {
            "cartridge_id": "particle_physics",
            "sources": [],
            "topics": [{"id": "t1", "title": "骨格と無関係な題目zzz"}],
        }
        monkeypatch.setattr(services, "get_course_data", lambda uid, cid: course_data)
        res = client.get("/api/atlas?course=c1", headers=_headers())
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# gap1: 通常学習で「いまここ」を動かす帰属解決
# ---------------------------------------------------------------------------


class TestTopicAttributionGap1:
    def test_resolves_now_from_topic(self, sandbox_cartridges, monkeypatch):
        from api.routes import learning
        from core import atlas_state
        from core import cartridges as cartridges_module

        skeleton = cartridges_module.load_cartridge("particle_physics").learner_atlas_skeleton
        concept_id = skeleton.concept_ids()[0]

        class _S:
            def close(self):
                pass

        monkeypatch.setattr(learning, "_pg_session", lambda: _S())
        monkeypatch.setattr(atlas_state, "resolve_course_cartridge", lambda s, cd: "particle_physics")

        out = learning._atlas_topic_attribution(
            {"cartridge_id": "particle_physics"},
            {"id": "t1", "title": "x", "atlas_node_id": concept_id},
        )
        assert out is not None
        assert out["node_id"] == concept_id
        assert out["action"] == "study"
        assert out["level"] == 1
        assert out["skeleton_version"] == skeleton.version

    def test_returns_none_when_no_match(self, sandbox_cartridges, monkeypatch):
        from api.routes import learning
        from core import atlas_state

        class _S:
            def close(self):
                pass

        monkeypatch.setattr(learning, "_pg_session", lambda: _S())
        monkeypatch.setattr(atlas_state, "resolve_course_cartridge", lambda s, cd: "particle_physics")

        out = learning._atlas_topic_attribution(
            {}, {"id": "t", "title": "全く無関係な題目zzz9"}
        )
        assert out is None
