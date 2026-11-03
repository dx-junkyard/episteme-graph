"""再解析の「分野: 指定しない」が resume 経路でも run 行と全 agent に効くこと（IK-0702）。

契約（route → orchestrator → persistence で一貫）:

- route: body の ``cartridge_id`` が ``None``（未指定）なら前回 run の分野を引き継ぐ。
  ``""`` は「指定しない」への明示的な解除で、そのまま渡す。
- orchestrator: ``None`` は env 既定、``""`` は ``None``（分野中立）に正規化。resume でも
  解決済みの値を ``ctx.cartridge_id`` と run 行の両方へ渡す（run 行から読み戻さない）。
- persistence: ``cartridge_id`` は「この run の実効の分野」で、更新時も必ず書く
  （``None`` を「変更なし」と読まない — 読むと resume で解除が表現できない）。

加えて、同じ教材の解析が走っている間の再解析は 409（旧スレッドが前回の分野で並走し
続け、run 行と LLM 呼び出しに前回の分野が残るのを防ぐ）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND.parent / "src"
for _p in (str(SRC), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --- persistence -----------------------------------------------------------


def _run_update(cartridge_id):
    from core.document_pipeline import persistence

    session = MagicMock()
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.upsert_analysis_run(
            run_id="11111111-1111-1111-1111-111111111111",
            document_id="doc-1",
            material_id="mat-1",
            cartridge_id=cartridge_id,
            status="running",
            current_stage="save_pdf",
        )
    stmt, params = session.execute.call_args_list[0].args
    return str(stmt), params


@pytest.mark.parametrize("value", [None, "", "  "])
def test_update_writes_null_cartridge_for_neutral(value):
    sql, params = _run_update(value)
    assert "cartridge_id = :cartridge_id" in sql
    assert params["cartridge_id"] is None


def test_update_writes_explicit_cartridge():
    sql, params = _run_update("astrophysics")
    assert "cartridge_id = :cartridge_id" in sql
    assert params["cartridge_id"] == "astrophysics"


def test_insert_normalizes_blank_cartridge_to_null():
    from core.document_pipeline import persistence

    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("run-new",)
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.upsert_analysis_run(
            document_id="doc-1", material_id="mat-1", cartridge_id="",
            status="running", current_stage="save_pdf",
        )
    _, params = session.execute.call_args_list[0].args
    assert params["cartridge_id"] is None


# --- orchestrator ----------------------------------------------------------

_RUNNING_PREVIOUS = {
    "id": "run-prev",
    "status": "running",
    "current_stage": "rhetorical_role",
    "cartridge_id": "particle_physics",
    "stage_outputs": {},
    "options": {},
}


def _resume_with(cartridge_id, monkeypatch, env=""):
    from core.document_pipeline import orchestrator

    monkeypatch.setenv("EPISTEME_DEFAULT_CARTRIDGE_ID", env)
    upserts: list[dict] = []
    seen: dict = {}

    def fake_upsert(**kwargs):
        upserts.append(kwargs)
        return kwargs.get("run_id") or "run-new"

    def step(ctx):
        seen["ctx_cartridge_id"] = ctx.cartridge_id
        return True

    with patch.object(orchestrator, "get_latest_analysis_run",
                      MagicMock(return_value=dict(_RUNNING_PREVIOUS))), \
         patch.object(orchestrator, "upsert_analysis_run", side_effect=fake_upsert), \
         patch.object(orchestrator, "_PIPELINE_STEPS",
                      [orchestrator.PipelineStageDef("save_pdf", step)]):
        result = orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4", document_id="doc-1", material_id="mat-1",
            resume=True, cartridge_id=cartridge_id,
        )
    return upserts, seen, result


def test_resume_with_blank_clears_cartridge_on_run_row_and_agents(monkeypatch):
    upserts, seen, result = _resume_with("", monkeypatch, env="particle_physics")
    # 走行中の前回 run を同じ run id で拾う（resume）が、分野は解除される。
    assert upserts[0]["run_id"] == "run-prev"
    assert all(u["cartridge_id"] is None for u in upserts)
    assert seen["ctx_cartridge_id"] is None
    assert result.cartridge_id is None


def test_resume_with_explicit_cartridge_overrides_previous(monkeypatch):
    upserts, seen, _ = _resume_with("astrophysics", monkeypatch)
    assert upserts[0]["run_id"] == "run-prev"
    assert all(u["cartridge_id"] == "astrophysics" for u in upserts)
    assert seen["ctx_cartridge_id"] == "astrophysics"


def test_resume_does_not_read_cartridge_back_from_run_row(monkeypatch):
    """前回 run 行の分野は orchestrator では継承しない（継承は route の責務）。"""
    upserts, seen, _ = _resume_with(None, monkeypatch, env="")
    assert seen["ctx_cartridge_id"] is None
    assert all(u["cartridge_id"] is None for u in upserts)


# --- route -----------------------------------------------------------------


def _import_admin_routes():
    pytest.importorskip("fastapi")
    for _p in (str(BACKEND), str(BACKEND / "api")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import routes.admin as admin_mod
    return admin_mod


def _reanalyze(monkeypatch, body, *, previous_cartridge="particle_physics", running=False):
    admin_mod = _import_admin_routes()
    from services import DocumentAccess

    captured: dict = {}

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(
        admin_mod, "resolve_document_access",
        lambda uid, ref: DocumentAccess(
            document_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
            source_path="mat-1", uploaded_by=uid, is_owner=True,
            can_view=True, can_edit=True,
        ),
    )
    select_session = MagicMock()
    select_session.execute.return_value.fetchone.return_value = ("Title", "file.pdf")
    monkeypatch.setattr(admin_mod, "_pg_session",
                        MagicMock(side_effect=[select_session, MagicMock()]))
    storage = MagicMock()
    storage.get_object.return_value = b"%PDF-1.4"
    monkeypatch.setattr(admin_mod, "get_storage_client", lambda: storage)
    monkeypatch.setattr(admin_mod, "create_background_task", lambda *a, **k: None)
    monkeypatch.setattr(admin_mod.threading, "Thread", _FakeThread)
    monkeypatch.setattr(admin_mod, "_previous_run_cartridge_id",
                        lambda *a, **k: previous_cartridge)
    status = {"mat-1": {"status": "processing"}} if running else {}
    monkeypatch.setattr(admin_mod, "_material_status", status)

    admin_mod.reanalyze_document(
        "dddddddd-dddd-dddd-dddd-dddddddddddd",
        body=body,
        current_user={"id": "22222222-2222-2222-2222-222222222222"},
    )
    return captured


def test_route_blank_passes_blank_not_previous(monkeypatch):
    admin_mod = _import_admin_routes()
    captured = _reanalyze(monkeypatch, admin_mod.ReanalyzeRequest(cartridge_id=""))
    assert captured["args"][5] == ""


def test_route_unset_inherits_previous_run_cartridge(monkeypatch):
    admin_mod = _import_admin_routes()
    captured = _reanalyze(monkeypatch, admin_mod.ReanalyzeRequest())
    assert captured["args"][5] == "particle_physics"


def test_route_rejects_while_same_material_is_processing(monkeypatch):
    admin_mod = _import_admin_routes()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _reanalyze(monkeypatch, admin_mod.ReanalyzeRequest(cartridge_id=""), running=True)
    assert excinfo.value.status_code == 409
    assert "実行中" in excinfo.value.detail
