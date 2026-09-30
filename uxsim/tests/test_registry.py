"""行為レジストリのガードレール（PE9）: id の網羅・API の実在・affordance / manual の実在。"""
from __future__ import annotations

import importlib.util
import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from uxsim.actions.registry import REGISTRY
from uxsim.config import REPO_ROOT

REQUIRED_IDS = """
auth.login learning.course.list learning.course.enroll learning.course.open learning.topic.open learning.chat.ask
learning.chat.rewrite learning.chat.history learning.check.take learning.check.self_check learning.discuss.opening
learning.discuss.ask learning.discuss.reflection learning.cycle.intention learning.cycle.return_door
learning.cycle.todays_words learning.cycle.anchor learning.tension.digest learning.tension.confirm
learning.tension.dismiss learning.anchors.digest learning.anchors.confirm learning.anchors.dismiss
learning.reconstruction.next learning.reconstruction.submit learning.reconstruction.self_check
learning.reconstruction.descend learning.symbol.lookup learning.descent.ladder learning.chat.backstage
learning.element.context learning.source_chunk.open learning.records.mine learning.personal_network.mine
learning.personal_network.journey learning.personal_network.nearby learning.atlas.view learning.landscape.view
learning.corpus.domains learning.corpus.documents learning.corpus.discuss_ask learning.lecture.sequence
learning.lecture.audio_status learning.help.inspect learning.chat.usage_help learning.progress learning.voice.speak
learning.chat.casual admin.next_steps.list admin.materials.list admin.materials.get admin.materials.upload_url
admin.materials.task_status admin.materials.visibility admin.graph_review.open admin.graph_review.approve_component
admin.graph_review.reject_component admin.graph_review.chat admin.course_builder.session_create
admin.course_builder.chat admin.course_builder.register admin.course.visibility admin.release_review.placements
admin.release_review.accept admin.atlas_binding.propose admin.atlas_binding.save admin.lecture_studio.scripts
admin.users.create_student admin.groups.create admin.groups.add_member admin.copilot.chat admin.help.inspect
admin.discuss_opening_review.list
learning.component.context learning.component.context_hop learning.chunk.claim_refs learning.chat.ask_selection
learning.chat.cycle learning.atlas.threads learning.atlas.neighbors admin.paper_layer.view admin.theory_modules.view
admin.theory_modules.related admin.graph_review.node_chat admin.deliberation.overview
admin.graph_review.approve_claim admin.seminar_brief.view
""".split()

MANUAL_ROOT = REPO_ROOT / "docs" / "manual"
_ROUTE_DECORATOR = re.compile(r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"')


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/") or "/")


@lru_cache(maxsize=1)
def app_routes() -> frozenset[tuple[str, str]]:
    """FastAPI のルート表（import できなければ route デコレータの grep に縮退）。"""
    backend = REPO_ROOT / "backend"
    for p in (REPO_ROOT / "src", backend, backend / "api"):
        if str(p) in sys.path:
            sys.path.remove(str(p))
        sys.path.insert(0, str(p))
    try:
        from api.main import app  # noqa: WPS433
        # backend/tests を sys.path に載せると tests/api（通常パッケージ）が api を覆うので、ファイルから読む
        spec = importlib.util.spec_from_file_location("_uxsim_guardrail_helpers",
                                                      backend / "tests" / "guardrail_helpers.py")
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)  # type: ignore[union-attr]
        collect_route_pairs = helpers.collect_route_pairs

        return frozenset((m, _norm(p)) for p, m in collect_route_pairs(app))
    except Exception:  # noqa: BLE001 — 環境依存の import 失敗は grep で代える
        return frozenset(_grep_routes(backend / "api"))


def _grep_routes(api_dir: Path) -> set[tuple[str, str]]:
    """prefix を main.py / router 定義から読めないので、末尾一致で照合する用の (METHOD, path) 集合。"""
    out = set()
    for f in api_dir.rglob("*.py"):
        for m in _ROUTE_DECORATOR.finditer(f.read_text(encoding="utf-8")):
            out.add((m.group(2).upper(), "*" + _norm(m.group(3))))
    return out


def _route_exists(method: str, path: str) -> bool:
    routes = app_routes()
    key = (method, _norm(path))
    if key in routes:
        return True
    return any(r[0] == method and r[1].startswith("*") and _norm(path).endswith(r[1][1:]) for r in routes)


@lru_cache(maxsize=1)
def ui_anchor_ids() -> frozenset[str]:
    from core.help_kb.admin_ui_anchors import KNOWN_ADMIN_UI_ANCHOR_IDS
    from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS

    return frozenset(KNOWN_UI_ANCHOR_IDS) | frozenset(KNOWN_ADMIN_UI_ANCHOR_IDS)


def _manual_ref_exists(ref: str) -> bool:
    file, _, anchor = ref.partition("#")
    path = MANUAL_ROOT / file
    if not path.is_file():
        return False
    return not anchor or f"{{#{anchor}}}" in path.read_text(encoding="utf-8")


def test_all_required_ids_registered():
    missing = [i for i in REQUIRED_IDS if i not in REGISTRY]
    assert not missing, missing


@pytest.mark.parametrize("action_id", sorted(REGISTRY))
def test_api_route_exists(action_id):
    action = REGISTRY[action_id]
    if action.unsupported:
        assert action.api is None
        return
    assert action.api is not None
    for method, path in action.all_api:
        assert _route_exists(method, path), f"{action_id}: {method} {path} がルート表に無い"


@pytest.mark.parametrize("action_id", sorted(REGISTRY))
def test_affordance_and_manual_resolve(action_id):
    action = REGISTRY[action_id]
    aff = action.affordance
    if "#" in aff or aff.endswith(".md"):
        assert _manual_ref_exists(aff), f"{action_id}: manual の節が無い {aff}"
    else:
        assert aff in ui_anchor_ids(), f"{action_id}: 未知の data-ui-anchor {aff}"
    assert action.manual and _manual_ref_exists(action.manual), f"{action_id}: manual {action.manual}"
    assert action.llm_cost in ("product", "none")
    assert action.screen in ("learning", "admin")
    assert action.label and not action.label.isascii(), "label は画面の日本語"


def test_known_unsupported_list_is_empty():
    """現時点で全行為が実ルートを持つ（持たない行為を足したらここに列挙する）。"""
    assert sorted(a.id for a in REGISTRY.values() if a.unsupported) == []


STRUCTURE_IDS = REQUIRED_IDS[REQUIRED_IDS.index("learning.component.context"):]


@pytest.mark.parametrize("action_id", STRUCTURE_IDS)
def test_structure_actions_have_handlers_and_real_affordance(action_id):
    """§18.2 の行為は実行関数を持ち、affordance が実在の UI 部品かマニュアル節に解決する。"""
    from uxsim.runner.actions_exec import _HANDLERS

    assert action_id in _HANDLERS
    assert not REGISTRY[action_id].unsupported
