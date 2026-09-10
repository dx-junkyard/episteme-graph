"""教材入口の分野指定（提案 C1）と概念正規化の分野係留（提案 C2）。

正本: `docs/architecture/six_lenses_2026-09-10/06_coldstart.md` 提案1 / 提案2
（統合は `docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #7）。

守る性質:

- **C1-1** 出荷既定の分野は空（`.env.example`）。素粒子物理として立ち上がらない。
- **C1-2** 分野の解決順は 引数 > env > None。引数の空文字は「指定しない」で、
  env へフォールバックしない（画面が「指定しない」と言っているのに env の分野が
  黙って効く経路を作らない）。
- **C1-3** アップロード / URL取得 / 再解析が分野を run へ通す（既存列
  `document_analysis_runs.cartridge_id`。migration 不要）。再解析は `models` と
  同じ流儀で前回 run から継承し、空文字で解除できる。
- **C1-4** 選択肢に無い分野キーは 422（fail-closed）。
- **C2-1** `normalize_concepts` は分野が空ならカートリッジを読まない
  （既定カートリッジの別名表で概念名を書き換えない）。
- **C2-2** 正規化は置換ではなく追加（`name` を上書きせず `canonical_name` を併記）。
- **C2-3** 教員が確定した別名（VA層 `atlas_anchor_aliases`）が第2供給源として効き、
  出所は `teacher_alias` として区別される（ファイル由来の `ontology_alias` と
  混ぜない）。
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

ENV_EXAMPLE = ROOT / ".env.example"
ORCHESTRATOR = BACKEND / "core" / "document_pipeline" / "orchestrator.py"
ADMIN_ROUTES = BACKEND / "api" / "routes" / "admin.py"
THEORY_ROUTES = BACKEND / "api" / "routes" / "theory_components.py"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_function(js: str, name: str) -> str:
    """admin.js（1つの大きな IIFE）内の ``function <name>(`` 本体を粗く切り出す。

    トップレベル関数は2スペースインデントなので、次の ``\n  function `` の直前
    までを本体とみなす（ネストした関数はより深いインデントなので巻き込まれる）。
    """
    start = js.index("function " + name + "(")
    rest = js[start:]
    end = rest.find("\n  function ", 1)
    return rest if end == -1 else rest[:end]


# ---------------------------------------------------------------------------
# C1: 教材の入口に分野を置く
# ---------------------------------------------------------------------------


class TestShippedDefaultIsFieldNeutral:
    """C1-1: 出荷既定は分野中立（素粒子物理として立ち上がらない）。"""

    def test_env_example_default_cartridge_is_empty(self):
        src = _read(ENV_EXAMPLE)
        lines = [
            line for line in src.splitlines()
            if line.strip().startswith("EPISTEME_DEFAULT_CARTRIDGE_ID=")
        ]
        assert lines, ".env.example に EPISTEME_DEFAULT_CARTRIDGE_ID の行が無い"
        for line in lines:
            value = line.split("=", 1)[1].strip()
            assert value == "", (
                "出荷既定の分野は空にする（他分野・混在コーパスで全論文が"
                f"同じ分野として刻まれる）。実際の値: {value!r}"
            )

    def test_env_example_explains_when_to_fill_it(self):
        """空である理由（検証用・分野中立）をコメントで明示する。"""
        src = _read(ENV_EXAMPLE)
        block = src.split("EPISTEME_DEFAULT_CARTRIDGE_ID=")[0][-600:]
        assert "particle_physics" in block
        assert "分野中立" in block


class TestOrchestratorCartridgeResolution:
    """C1-2: 引数 > env > None。空文字は env へ戻さない。"""

    def test_env_is_read_only_when_argument_is_absent(self):
        body = extract_function_source(_read(ORCHESTRATOR), "run_document_pipeline")
        head = body.split("if target_stage is not None")[0]
        assert 'os.getenv("EPISTEME_DEFAULT_CARTRIDGE_ID")' in head
        # 未指定のときだけ env を見る（空文字は None へ畳む = 分野中立）。
        assert "if cartridge_id is None:" in head
        assert 'or "").strip() or None' in head
        # 明示指定（空文字を含む）は env へフォールバックしない。
        assert "cartridge_id.strip() or None" in head

    def test_orchestrator_does_not_call_load_cartridge_with_none(self):
        """パイプラインは cartridge を自前で load しない（agent 側が None で縮退する）。

        `core.cartridges.load_cartridge(None)` は既定カートリッジへ縮退するため、
        orchestrator から呼ぶと分野中立経路が塞がれる。
        """
        src = _read(ORCHESTRATOR)
        assert_source_forbids(
            src,
            [
                "from core.cartridges import",
                "import core.cartridges",
                "load_cartridge(None)",
                "load_cartridge()",
            ],
            context="orchestrator.py",
        )
        # 唯一の cartridge 読み出しは agent 側のメソッド（None で綺麗に縮退する）。
        for match in re.finditer(r"[\w.]*load_cartridge\(", src):
            assert match.group(0).startswith("agent._load_cartridge("), match.group(0)

    def test_agents_degrade_when_cartridge_id_is_absent(self):
        """A層 agent は cartridge_id なしで単独動作する（非改変の前提の固定）。"""
        agents_dir = ROOT / "src" / "episteme_graph" / "agents"
        checked = 0
        for agent_path in sorted(agents_dir.glob("*/agent.py")):
            src = _read(agent_path)
            if "_load_cartridge" not in src:
                continue
            body = extract_function_source(src, "_load_cartridge")
            assert "if not cartridge_id:" in body and "return None" in body, (
                f"{agent_path}: cartridge_id 未指定で縮退しない"
            )
            checked += 1
        assert checked >= 5


class TestUploadPassesDomainToRun:
    """C1-3: アップロード / URL取得 / 再解析が分野を run へ通す。"""

    def test_upload_material_accepts_cartridge_id_form_field(self):
        import routes.admin as admin_routes

        params = inspect.signature(admin_routes.upload_material).parameters
        assert "cartridge_id" in params

    def test_accept_material_source_has_the_keyword_and_passes_it_through(self):
        import routes.admin as admin_routes

        params = inspect.signature(admin_routes._accept_material_source).parameters
        assert "cartridge_id" in params
        assert params["cartridge_id"].default is None

        body = inspect.getsource(admin_routes._accept_material_source)
        # process_material_background の 6番目の位置引数（cartridge_id）に None を
        # 固定で渡していた経路を塞ぐ。
        assert "task_id, cartridge_id, source_kind" in body

    def test_url_upload_request_and_route_pass_cartridge_id(self):
        import routes.admin as admin_routes

        assert "cartridge_id" in admin_routes.UploadFromUrlRequest.model_fields
        body = inspect.getsource(admin_routes.upload_material_from_url)
        assert "_validate_cartridge_option(body.cartridge_id)" in body
        assert "cartridge_id=cartridge_option" in body

    def test_reanalyze_request_inherits_previous_run_cartridge(self):
        import routes.admin as admin_routes

        assert "cartridge_id" in admin_routes.ReanalyzeRequest.model_fields
        assert admin_routes.ReanalyzeRequest.model_fields["cartridge_id"].default is None

        body = inspect.getsource(admin_routes.reanalyze_document)
        assert "_validate_cartridge_option(body.cartridge_id)" in body
        # 未指定なら前回 run の分野を継承する（models と同じ流儀）。
        assert "_previous_run_cartridge_id(document_id, material_id)" in body
        # 継承値は process_material_background へ渡る（None 固定に戻さない）。
        assert "cartridge_option, source_kind or \"pdf\"" in body

    def test_previous_run_cartridge_distinguishes_no_run_from_neutral_run(self):
        """前回 run なし（None）と前回が分野中立（""）を区別する。

        両方を None にすると、分野中立で解析した教材が再解析で env の分野を
        黙って着せられる。
        """
        import routes.admin as admin_routes

        body = inspect.getsource(admin_routes._previous_run_cartridge_id)
        assert "if not previous_run:" in body
        assert "return None" in body
        assert 'str(previous_run.get("cartridge_id") or "")' in body

    def test_material_out_exposes_latest_run_cartridge(self):
        from schemas import MaterialOut

        assert "analysis_cartridge_id" in MaterialOut.model_fields


class TestCartridgeOptionValidation:
    """C1-4: 選択肢に無い分野キーは 422（fail-closed）。"""

    @pytest.fixture()
    def validate(self, monkeypatch):
        import routes.admin as admin_routes

        monkeypatch.setattr(
            admin_routes, "_known_domain_keys", lambda: {"particle_physics", "astrophysics"}
        )
        return admin_routes._validate_cartridge_option

    def test_none_means_unspecified(self, validate):
        assert validate(None) is None

    def test_blank_means_explicitly_unspecified(self, validate):
        assert validate("") == ""
        assert validate("   ") == ""

    def test_known_key_passes(self, validate):
        assert validate(" astrophysics ") == "astrophysics"

    def test_unknown_key_is_rejected(self, validate):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            validate("condensed_matter")
        assert exc.value.status_code == 422

    def test_malformed_key_is_rejected(self, validate):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            validate("../etc/passwd")
        assert exc.value.status_code == 422

    def test_known_keys_compose_cartridges_and_atlas_domains(self):
        """母集合はフロントの選択肢（カートリッジ + atlas ドメイン）と同じ合成。"""
        import routes.admin as admin_routes

        body = inspect.getsource(admin_routes._known_domain_keys)
        assert "list_cartridges" in body
        assert "atlas_store.list_domains" in body


class TestUploadEndpointWiring:
    """multipart の Form 配線を実際に叩いて確かめる（422 経路込み）。"""

    @pytest.fixture()
    def env(self, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from api.main import app
        from dependencies import ROLE_TEACHER, _create_token
        import routes.admin as admin_routes

        accepted: list[dict] = []

        def _accept(**kwargs):
            accepted.append(kwargs)
            return {
                "task_id": "t1", "material_id": "m1", "filename": kwargs["filename"],
                "title": "x", "source_kind": kwargs["source_kind"], "status": "pending",
                "uploaded_at": "2026-09-10T00:00:00",
                "analyze_images": bool(kwargs["analyze_images"]),
            }

        monkeypatch.setattr(admin_routes, "_accept_material_source", _accept)
        monkeypatch.setattr(
            admin_routes, "_known_domain_keys", lambda: {"particle_physics", "astrophysics"}
        )
        token = _create_token(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "kyoin", "kyoin@x", ROLE_TEACHER
        )
        return {
            "client": TestClient(app),
            "headers": {"Authorization": f"Bearer {token}"},
            "accepted": accepted,
        }

    def _post(self, env, data):
        return env["client"].post(
            "/api/admin/materials/upload",
            files={"file": ("p.pdf", b"%PDF-1.7\nhello", "application/pdf")},
            data=data,
            headers=env["headers"],
        )

    def test_selected_domain_reaches_the_run(self, env):
        resp = self._post(env, {"cartridge_id": "astrophysics"})
        assert resp.status_code == 202, resp.text
        assert env["accepted"][0]["cartridge_id"] == "astrophysics"

    def test_omitted_domain_is_unspecified(self, env):
        resp = self._post(env, {})
        assert resp.status_code == 202, resp.text
        assert env["accepted"][0]["cartridge_id"] is None

    def test_blank_domain_is_unspecified(self, env):
        resp = self._post(env, {"cartridge_id": ""})
        assert resp.status_code == 202, resp.text
        assert env["accepted"][0]["cartridge_id"] is None

    def test_unknown_domain_is_422_and_nothing_is_accepted(self, env):
        resp = self._post(env, {"cartridge_id": "condensed_matter"})
        assert resp.status_code == 422
        assert env["accepted"] == []


class TestUploadDomainUi:
    """管理UI: 分野1行（既定は「指定しない」）と再解析モーダルの前回値。"""

    def test_upload_row_exists_with_anchor_and_default_label(self):
        html = _read(ADMIN_HTML)
        assert 'data-ui-anchor="materials.upload-domain"' in html
        assert 'id="upload-domain-value"' in html
        assert "指定しない" in html

    def test_upload_sends_cartridge_id_only_when_chosen(self):
        js = _read(ADMIN_JS)
        assert 'formData.append("cartridge_id", uploadCartridgeId)' in js
        assert "payload.cartridge_id = urlCartridgeId" in js
        # 「指定しない」は送らない（サーバ既定 = 分野中立に委ねる）。
        body = _js_function(js, "getUploadCartridgeId")
        assert "_uploadDomain.value" in body

    def test_domain_option_composition_is_shared_not_duplicated(self):
        """選択肢の合成は loadDomainOptions() の1本だけ（二重実装しない）。"""
        js = _read(ADMIN_JS)
        assert js.count('apiFetch("/admin/atlas/domains")') == 1
        assert js.count("function loadDomainOptions(") == 1
        # 分野の地図タブの分野セレクタも同じ合成を使う。
        assert "loadDomainOptions()" in _js_function(js, "initAtlas")

    def test_reanalyze_modal_shows_previous_domain_and_allows_change(self):
        js = _read(ADMIN_JS)
        assert 'data-ui-anchor="materials.reanalyze-domain"' in js
        assert "前回の解析の分野: 指定しない（分野固有の語彙を使わずに解析）" in js
        # 触っていなければ送らない（サーバ側で前回値を継承）。
        body = _js_function(js, "getReanalyzeCartridgeId")
        assert "if (!_reanalyzeDomain.touched) return null;" in body

    def test_reanalyze_does_not_send_cartridge_when_untouched(self):
        js = _read(ADMIN_JS)
        body = _js_function(js, "performReanalyze")
        assert "if (cartridgeId !== null && cartridgeId !== undefined) body.cartridge_id" in body


# ---------------------------------------------------------------------------
# C2: 概念正規化の分野係留と教員別名の還流
# ---------------------------------------------------------------------------


class TestConceptNormalizerAnchoring:
    """C2-1 / C2-2 / C2-3。"""

    def test_core_module_stays_free_of_fastapi_and_db(self):
        assert_module_tree_does_not_import(
            [BACKEND / "core" / "concept_normalizer.py"],
            ["fastapi", "sqlalchemy"],
        )

    def test_no_normalization_without_a_field(self, monkeypatch):
        """分野が空ならカートリッジを読まない（既定カートリッジへ縮退しない）。"""
        from core import concept_normalizer as cn

        def _boom(*_a, **_kw):  # pragma: no cover — 呼ばれたら失敗
            raise AssertionError("load_cartridge must not be called without a cartridge_id")

        monkeypatch.setattr(cn, "load_cartridge", _boom)
        out = cn.normalize_concepts([{"name": "HQET"}])
        assert out[0]["name"] == "HQET"
        assert out[0]["canonical"] == "HQET"
        assert out[0]["normalization_source"] == cn.SOURCE_STRING_NORMALIZED

    def test_alias_match_adds_canonical_without_replacing_name(self):
        """正規化は追加であって置換ではない（vision §2.5 / 原則3）。"""
        from core import concept_normalizer as cn

        out = cn.normalize_concepts([{"name": "HQET"}], "particle_physics")
        assert out[0]["name"] == "HQET", "元の名前を上書きしてはならない"
        assert out[0]["canonical"] == "Heavy Quark Effective Theory"
        assert out[0]["canonical_name"] == "Heavy Quark Effective Theory"
        assert out[0]["normalization_source"] == cn.SOURCE_ONTOLOGY_ALIAS

    def test_teacher_alias_is_a_second_source_with_its_own_label(self):
        from core import concept_normalizer as cn

        out = cn.normalize_concepts(
            [{"name": "SM"}], "particle_physics",
            teacher_aliases={"SM": "標準模型"},
        )
        assert out[0]["name"] == "SM"
        assert out[0]["canonical"] == "標準模型"
        assert out[0]["normalization_source"] == cn.SOURCE_TEACHER_ALIAS

    def test_teacher_alias_wins_over_file_alias(self):
        """人間が確定した別名を機械の表より先に見る（原則1）。"""
        from core import concept_normalizer as cn

        out = cn.normalize_concepts(
            [{"name": "HQET"}], "particle_physics",
            teacher_aliases={"HQET": "教員が決めた名前"},
        )
        assert out[0]["canonical"] == "教員が決めた名前"
        assert out[0]["normalization_source"] == cn.SOURCE_TEACHER_ALIAS

    def test_teacher_alias_applies_even_without_a_cartridge_file(self):
        """カートリッジファイルの無い分野でも教員別名は効く。"""
        from core import concept_normalizer as cn

        out = cn.normalize_concepts(
            [{"name": "SFR"}], "astrophysics",
            teacher_aliases={"SFR": "星形成率"},
        )
        assert out[0]["normalization_source"] == cn.SOURCE_TEACHER_ALIAS
        assert out[0]["normalized"], "非ASCII の正規形で normalized が空になってはならない"

    def test_claim_routes_pass_the_field_and_teacher_aliases(self):
        src = _read(THEORY_ROUTES)
        # 引数なしの素の呼び出し（既定カートリッジへの縮退）を残さない。
        assert "normalize_concepts(normalized_concepts)" not in src
        assert "normalize_concepts(payload.get(\"concepts\") or [])" not in src
        assert "_concept_normalization_context(" in src
        assert "teacher_alias_canonical_map" in src

    def test_document_field_comes_from_the_analysis_run(self):
        src = _read(THEORY_ROUTES)
        body = extract_function_source(src, "_document_cartridge_id")
        assert "get_latest_analysis_run(document_id=doc_id)" in body
        assert 'get("cartridge_id")' in body


class TestTeacherAliasCanonicalMap:
    """VA層の確定別名 → 正規化への還流（読みだけ・埋め込みも LLM も呼ばない）。"""

    class _FakeConcept:
        def __init__(self, node_id, label):
            self.id = node_id
            self.label = label

    class _FakeRegion:
        def __init__(self, region_id, label, concepts):
            self.id = region_id
            self.label = label
            self.concepts = concepts

    class _FakeSkeleton:
        version = "v1"

        def __init__(self, regions):
            self.regions = regions

    def _skeleton(self):
        return self._FakeSkeleton([
            self._FakeRegion("r1", "領域1", [self._FakeConcept("c1", "星形成率")]),
        ])

    def test_maps_confirmed_aliases_to_node_labels(self, monkeypatch):
        from core import atlas_store
        from core.atlas_vectors import builder, store

        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: self._skeleton())
        monkeypatch.setattr(
            store, "confirmed_aliases_by_node", lambda s, d: {"c1": ["SFR", "star formation rate"]}
        )
        out = builder.teacher_alias_canonical_map(object(), "astrophysics")
        assert out == {"SFR": "星形成率", "star formation rate": "星形成率"}

    def test_nodes_absent_from_the_frozen_skeleton_are_dropped(self, monkeypatch):
        from core import atlas_store
        from core.atlas_vectors import builder, store

        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: self._skeleton())
        monkeypatch.setattr(
            store, "confirmed_aliases_by_node", lambda s, d: {"gone": ["X"]}
        )
        assert builder.teacher_alias_canonical_map(object(), "astrophysics") == {}

    def test_no_skeleton_or_no_domain_is_fail_soft(self, monkeypatch):
        from core import atlas_store
        from core.atlas_vectors import builder

        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: None)
        assert builder.teacher_alias_canonical_map(object(), "astrophysics") == {}
        assert builder.teacher_alias_canonical_map(object(), "") == {}

    def test_reads_only_no_embedding_call(self):
        from core.atlas_vectors import builder

        body = inspect.getsource(builder.teacher_alias_canonical_map)
        assert_source_forbids(
            body,
            ["embed_texts", "generate_embeddings", "check_daily_gate", "session.commit"],
            context="teacher_alias_canonical_map",
        )


def test_no_migration_was_added_for_this_change():
    """分野は既存列（document_analysis_runs.cartridge_id）に入る（migration 不要）。"""
    sql = (BACKEND / "db" / "015_document_pipeline.sql").read_text(encoding="utf-8")
    assert re.search(r"cartridge_id\s+TEXT", sql)
