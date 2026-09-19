"""束の取り込み API（knowledge_transfer_design.md §4.2 / P4-1）。

``POST /api/documents/{document_id}/import-bundle`` の route 関数を直接呼び、
DB は monkeypatch で差し替える（``test_graph_paper_layer_api.py`` と同じ流儀）。

固定するのは:

- **KT6**: 権限は**編集**（``resolve_document_access.can_edit``）。不在・権限なしは同じ 404。
- **検証 422**: zip でない / 必須ファイル欠落 / スキーマ版が古い / 束の内部参照が切れている。
- **dry-run**: 既定で書き込み 0、事実と件数だけを返す。
- **T-2 409**: live 行があって ``replace`` が明示されていなければ書き込まない。
- **実行**: 取り込み run を 1 行作り、同期し、監査する。**DELETE を発行しない**（KT5）。
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import routes.export as export  # noqa: E402
import services  # noqa: E402

_TEACHER = {"id": "33333333-3333-3333-3333-333333333333", "role": "TEACHER"}
_DOC = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# 束（テスト用の最小・自己整合な束）
# ---------------------------------------------------------------------------


def _bundle_bytes(*, schema_version="0.3.0", drop=(), dangling=False, raw=None) -> bytes:
    if raw is not None:
        return raw
    claims = [{
        "claim_id": "claim_1",
        "document_id": "doc-source",
        "text": "A defines B.",
        "normalized_text": "a defines b",
        "claim_type": "definition",
        "support_status": "source_backed",
        "review_status": "teacher_approved",
        "source_evidence_ids": ["ev_1"],
    }]
    components = [{
        "component_id": "comp_1",
        "name": "Theory basis",
        "component_type": "theory",
        "primary_operation": "define_quantity",
        "linked_claim_ids": ["claim_9" if dangling else "claim_1"],
        "evidence_claims": [],
    }]
    payloads = {
        "manifest.json": {
            "export_schema_version": schema_version,
            "export_id": "export_api_test",
            "exported_at": "2026-09-13T00:00:00Z",
            "app": {"name": "episteme-graph"},
            "scope": {
                "type": "document",
                "document_id": "doc-source",
                "document_ids": ["doc-source"],
            },
        },
        "claims/claims.json": {"claims": claims},
        "components/components.json": {"components": components},
        "evidence/evidence_snippets.json": {"snippets": [{
            "evidence_id": "ev_1", "block_id": "blk_1", "evidence_text": "a defines b",
        }]},
        "graph/component_graph.json": {
            "nodes": [{"component_id": "comp_1"}],
            "edges": [],
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in payloads.items():
            if name in drop:
                continue
            zf.writestr(name, json.dumps(payload, ensure_ascii=False))
    return buffer.getvalue()


class _Upload:
    """FastAPI の UploadFile のうち route が使う ``.file`` だけを模擬する。"""

    def __init__(self, data: bytes):
        self.file = io.BytesIO(data)
        self.filename = "bundle.zip"


# ---------------------------------------------------------------------------
# fake session
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    @property
    def rowcount(self):
        return len(self._rows)


class _FakeSession:
    """取り込み経路が発行する SQL を受け取り、統計を取れる最小セッション。"""

    def __init__(self, *, live_claims=0, live_components=0, adopted_run=""):
        self.live_counts = [live_claims, live_components]
        self.adopted_run = adopted_run
        self.statements: list[str] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0
        self._insert_seq = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        upper = sql.upper()
        if "SELECT COUNT(*)" in upper:
            value = self.live_counts.pop(0) if self.live_counts else 0
            return _Result([(value,)])
        if upper.startswith("UPDATE DOCUMENTS"):
            return _Result([(self.adopted_run,)] if self.adopted_run else [])
        if "INSERT INTO DOCUMENT_ANALYSIS_RUNS" in upper:
            return _Result([("run-0001",)])
        if upper.startswith("SELECT"):
            # sync_live_rows の live 行 SELECT（この教材には live 行が無い前提）。
            return _Result([])
        if "RETURNING ID" in upper:
            self._insert_seq += 1
            return _Result([(f"uuid-{self._insert_seq:04d}",)])
        return _Result([])

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1

    # -- 検査用 ---------------------------------------------------------

    def writes(self) -> list[str]:
        return [
            s for s in self.statements
            if s.upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]


@pytest.fixture
def env(monkeypatch):
    """権限・セッション・監査を差し替え、検査対象を返す。"""
    state = {"session": _FakeSession(), "audits": [], "knowledge_audits": []}

    def _access(user_id, ref):
        return services.DocumentAccess(
            document_id=_DOC, source_path="material-1", uploaded_by=user_id,
            is_owner=True, can_view=True, can_edit=state.get("can_edit", True),
        )

    monkeypatch.setattr(services, "resolve_document_access", _access)
    monkeypatch.setattr(export, "_pg_session", lambda: state["session"])
    monkeypatch.setattr(
        export, "record_knowledge_audit",
        lambda session, **kw: state["knowledge_audits"].append(kw),
    )
    monkeypatch.setattr(
        export, "_record_import_audit",
        lambda **kw: state["audits"].append(kw),
    )
    return state


def _call(bundle_bytes, *, dry_run=True, replace=False, user=None, bundle_sha256=None):
    """取り込み API の直接呼び出し。

    確定（``dry_run=False``）は確認で返されたハッシュを必須で受ける（P4-R4）。
    既定では「確認した束をそのまま確定した」= 実際のハッシュを送る。TOCTOU の
    検査は ``bundle_sha256`` を明示して行う。
    """
    if bundle_sha256 is None:
        bundle_sha256 = "" if dry_run else hashlib.sha256(bundle_bytes).hexdigest()
    return export.import_document_bundle(
        _DOC,
        bundle=_Upload(bundle_bytes),
        dry_run=dry_run,
        replace=replace,
        bundle_sha256=bundle_sha256,
        current_user=user or _TEACHER,
    )


# ---------------------------------------------------------------------------
# KT6: 権限は編集・不在と権限なしは同じ 404
# ---------------------------------------------------------------------------


class TestPermission:
    def test_view_only_is_404(self, env):
        env["can_edit"] = False
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes())
        assert exc.value.status_code == 404
        assert exc.value.detail == export._DOCUMENT_NOT_FOUND_DETAIL

    def test_missing_document_is_the_same_404(self, monkeypatch, env):
        monkeypatch.setattr(
            services, "resolve_document_access",
            lambda user_id, ref: services.DocumentAccess(document_id=None),
        )
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes())
        assert exc.value.status_code == 404
        assert exc.value.detail == export._DOCUMENT_NOT_FOUND_DETAIL

    def test_permission_is_checked_before_reading_the_bundle(self, env):
        """副作用（zip 読み・DB 読み）より先に認可する。"""
        env["can_edit"] = False
        with pytest.raises(HTTPException):
            _call(b"not a zip")
        assert env["session"].statements == []

    def test_system_admin_may_import(self, monkeypatch, env):
        monkeypatch.setattr(
            services, "resolve_document_access",
            lambda user_id, ref: services.DocumentAccess(
                document_id=_DOC, can_view=True, can_edit=False
            ),
        )
        out = _call(_bundle_bytes(), user={"id": "admin-1", "role": "SYSTEM_ADMIN"})
        assert out["dry_run"] is True


# ---------------------------------------------------------------------------
# 検証 422
# ---------------------------------------------------------------------------


class TestValidation:
    def test_not_a_zip_is_422(self, env):
        with pytest.raises(HTTPException) as exc:
            _call(b"definitely not a zip")
        assert exc.value.status_code == 422
        assert "ZIP" in " ".join(exc.value.detail["facts"])

    def test_missing_manifest_is_422(self, env):
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes(drop=("manifest.json",)))
        assert exc.value.status_code == 422
        assert exc.value.detail["report"]["missing_files"] == ["manifest.json"]

    def test_old_schema_version_is_422(self, env):
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes(schema_version="0.2.0"))
        assert exc.value.status_code == 422
        assert exc.value.detail["report"]["min_schema_version"] == "0.3.0"

    def test_dangling_internal_reference_is_422(self, env):
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes(dangling=True))
        assert exc.value.status_code == 422
        errors = exc.value.detail["report"]["errors"]
        assert errors and any("claim_9" in json.dumps(e) for e in errors)

    def test_validation_failures_write_nothing(self, env):
        with pytest.raises(HTTPException):
            _call(_bundle_bytes(dangling=True), dry_run=False, replace=True)
        assert env["session"].writes() == []
        assert env["audits"] == []

    def test_validation_detail_is_a_japanese_fact(self, env):
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes(schema_version="0.1.0"))
        facts = exc.value.detail["facts"]
        assert facts and all(isinstance(f, str) and f.endswith("。") for f in facts)


# ---------------------------------------------------------------------------
# dry-run（既定）
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_is_the_default_and_writes_nothing(self, env):
        out = _call(_bundle_bytes())
        assert out["dry_run"] is True
        assert env["session"].writes() == []
        assert env["session"].committed == 0
        assert env["audits"] == []

    def test_dry_run_dto_shape(self, env):
        out = _call(_bundle_bytes())
        assert set(out) >= {
            "dry_run", "source", "counts", "target", "would_supersede", "facts",
        }
        assert out["source"]["export_id"] == "export_api_test"
        assert out["source"]["object_type"] == "document"
        assert out["source"]["document_ids"] == ["doc-source"]
        assert out["counts"] == {
            "claims": 1, "components": 1, "equations": 0,
            "evidence": 1, "derivation_steps": 0, "graph_nodes": 1,
        }
        assert out["target"] == {"document_id": _DOC, "has_live_rows": False}
        assert out["would_supersede"] is False

    def test_dry_run_reports_existing_rows(self, monkeypatch, env):
        env["session"] = _FakeSession(live_claims=4)
        monkeypatch.setattr(export, "_pg_session", lambda: env["session"])
        out = _call(_bundle_bytes())
        assert out["target"]["has_live_rows"] is True
        assert out["would_supersede"] is True
        # 置き換えを明示していないので「取り込みは行わない」と正直に言う（T-2）。
        assert any("置き換えを明示しない限り" in f for f in out["facts"])

    def test_session_is_always_closed(self, env):
        _call(_bundle_bytes())
        assert env["session"].closed == 1


# ---------------------------------------------------------------------------
# T-2: live 行のある教材への取り込み
# ---------------------------------------------------------------------------


class TestReplaceGate:
    def test_live_rows_without_replace_is_409(self, monkeypatch, env):
        env["session"] = _FakeSession(live_claims=2)
        monkeypatch.setattr(export, "_pg_session", lambda: env["session"])
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes(), dry_run=False)
        assert exc.value.status_code == 409
        assert "既に解析結果があります" in exc.value.detail["message"]
        assert env["session"].writes() == []
        assert env["session"].committed == 0

    def test_live_rows_with_replace_proceeds(self, monkeypatch, env):
        env["session"] = _FakeSession(live_claims=2)
        monkeypatch.setattr(export, "_pg_session", lambda: env["session"])
        out = _call(_bundle_bytes(), dry_run=False, replace=True)
        assert out["imported"] is True
        assert env["session"].committed == 1


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


class TestApply:
    def test_import_creates_a_run_and_commits(self, env):
        out = _call(_bundle_bytes(), dry_run=False)
        assert out["imported"] is True
        assert out["run_id"] == "run-0001"
        joined = " ".join(env["session"].statements).upper()
        assert "INSERT INTO DOCUMENT_ANALYSIS_RUNS" in joined
        assert env["session"].committed == 1

    def test_the_import_run_is_marked_as_an_import_stage(self, env):
        _call(_bundle_bytes(), dry_run=False)
        run_insert = next(
            s for s in env["session"].statements
            if "document_analysis_runs" in s and s.upper().startswith("INSERT")
        )
        assert "'completed'" in run_insert
        assert ":stage" in run_insert

    def test_import_never_deletes(self, env):
        """KT5: 取り込みは DELETE を発行しない（supersede 同期だけ）。"""
        _call(_bundle_bytes(), dry_run=False, replace=True)
        assert not any(s.upper().startswith("DELETE") for s in env["session"].statements)

    def test_graph_is_upserted(self, env):
        _call(_bundle_bytes(), dry_run=False)
        assert any("theory_component_graphs" in s for s in env["session"].statements)

    def test_audit_is_recorded_with_the_bundle_facts(self, env):
        _call(_bundle_bytes(), dry_run=False)
        assert len(env["audits"]) == 1
        audit = env["audits"][0]
        assert audit["document_id"] == _DOC
        assert audit["run_id"] == "run-0001"
        assert audit["bundle"].export_id == "export_api_test"
        assert audit["replace"] is False
        assert audit["user_id"] == _TEACHER["id"]
        assert env["knowledge_audits"] and env["knowledge_audits"][0]["run_id"] == "run-0001"

    def test_stats_and_facts_are_returned(self, env):
        out = _call(_bundle_bytes(), dry_run=False)
        assert set(out["stats"]) >= {
            "claims", "components", "equations", "evidence", "derivation_steps", "graph",
        }
        assert out["facts"]

    def test_the_adopted_run_is_pinned_before_the_import_run_is_created(self, env):
        """取り込み run が採用 run を横取りしないことの担保。"""
        _call(_bundle_bytes(), dry_run=False)
        statements = env["session"].statements
        pin = next(i for i, s in enumerate(statements) if s.upper().startswith("UPDATE DOCUMENTS"))
        run = next(
            i for i, s in enumerate(statements)
            if "document_analysis_runs" in s and s.upper().startswith("INSERT")
        )
        assert pin < run
        assert "active_analysis_run_id IS NULL" in statements[pin]

    def test_a_failure_rolls_back(self, monkeypatch, env):
        class _Boom(_FakeSession):
            def execute(self, stmt, params=None):
                if "INSERT INTO document_analysis_runs" in str(stmt):
                    raise RuntimeError("boom")
                return super().execute(stmt, params)

        env["session"] = _Boom()
        monkeypatch.setattr(export, "_pg_session", lambda: env["session"])
        with pytest.raises(RuntimeError):
            _call(_bundle_bytes(), dry_run=False)
        assert env["session"].rolled_back == 1
        assert env["session"].committed == 0
        assert env["audits"] == []


# ---------------------------------------------------------------------------
# 往復（この system が書き出した束が、この system の検証を通る）
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def _real_bundle(self) -> bytes:
        claims = [{
            "claim_id": "claim_1",
            "document_id": "doc-source",
            "source_scope": {"document_id": "doc-source", "section_id": "sec_1"},
            "claim_type": "definition",
            "text": "A defines B.",
            "normalized_text": "a defines b",
            "concepts": [],
            "equation": {},
            "support_status": "source_backed",
            "evidence_text": "",
            "review_status": "teacher_review_required",
            "source_evidence_ids": ["ev_1"],
            "atomicity": "atomic",
            "is_atomic": True,
        }]
        components = [{
            "component_id": "comp_1",
            "document_id": "doc-source",
            "name": "Theory basis",
            "component_type": "theory",
            "source_scope": {"document_id": "doc-source"},
            "evidence_claims": ["claim_1"],
            "linked_claim_ids": ["claim_1"],
            "review_status": "teacher_review_required",
            "primary_operation": "define_quantity",
        }]
        evidence = [{
            "evidence_id": "ev_1",
            "document_id": "doc-source",
            "block_id": "blk_1",
            "evidence_text": "a defines b",
            "evidence_role": "source_quote",
        }]
        graph = {"graph_schema_version": "0.1.0", "nodes": [{"component_id": "comp_1"}], "edges": []}
        manifest = export._build_manifest(
            export_id="export_round_trip",
            scope_type="document",
            scope_id="doc-source",
            material_ids=[],
            document_ids=["doc-source"],
            claims=claims,
            dsl_graph={"nodes": [], "edges": []},
            components=components,
            component_graph=graph,
            evidence_snippets=evidence,
            options={},
        )
        return export._build_zip(
            manifest=manifest,
            claims=claims,
            dsl_graph={"nodes": [], "edges": []},
            components=components,
            component_graph=graph,
            evidence_snippets=evidence,
            ro_crate=export._build_ro_crate(manifest),
        )

    def test_a_bundle_this_system_produced_passes_validation(self, env):
        out = _call(self._real_bundle())
        assert out["dry_run"] is True
        assert out["source"]["export_id"] == "export_round_trip"
        assert out["counts"]["claims"] == 1
        assert out["counts"]["components"] == 1

    def test_the_round_trip_bundle_can_be_applied(self, env):
        out = _call(self._real_bundle(), dry_run=False)
        assert out["imported"] is True
        assert env["session"].committed == 1
        assert not any(s.upper().startswith("DELETE") for s in env["session"].statements)


# ---------------------------------------------------------------------------
# P4-R4: 確認した束と、確定される束が同じであること（TOCTOU）
# ---------------------------------------------------------------------------


class TestBundleHashHandshake:
    def test_dry_run_returns_the_hash(self, env):
        out = _call(_bundle_bytes(), dry_run=True)
        assert out["bundle_sha256"] == hashlib.sha256(_bundle_bytes()).hexdigest()

    def test_execution_without_the_hash_is_422_and_writes_nothing(self, env):
        with pytest.raises(HTTPException) as exc:
            _call(_bundle_bytes(), dry_run=False, bundle_sha256="")
        assert exc.value.status_code == 422
        assert "確認していない束は取り込めません。" in exc.value.detail["facts"][0]
        assert env["session"].writes() == []
        assert env["session"].committed == 0

    def test_a_different_bundle_than_the_confirmed_one_is_409(self, env):
        """確認と確定の間で束が差し替えられたら書き込まない。"""
        confirmed = hashlib.sha256(_bundle_bytes()).hexdigest()
        swapped = _bundle_bytes(schema_version="0.3.1")  # 別の中身（検証は通る）
        with pytest.raises(HTTPException) as exc:
            _call(swapped, dry_run=False, bundle_sha256=confirmed)
        assert exc.value.status_code == 409
        assert "確認した束と、いま送られた束が違います。" in exc.value.detail["message"]
        assert env["session"].writes() == []
        assert env["session"].committed == 0

    def test_the_hash_is_compared_case_insensitively(self, env):
        sha = hashlib.sha256(_bundle_bytes()).hexdigest().upper()
        out = _call(_bundle_bytes(), dry_run=False, bundle_sha256=sha)
        assert out["imported"] is True


# ---------------------------------------------------------------------------
# P4-R2: 人間が確定した行は replace でも表示対象から外さない
# ---------------------------------------------------------------------------


class _LiveRowSession(_FakeSession):
    """live 行を持つ教材を模擬する（保護スキャンと sync の SELECT に同じ行を返す）。"""

    def __init__(self, rows):
        super().__init__(live_claims=len(rows), live_components=0)
        self._rows = rows
        self.supersede_params: list[dict] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        upper = sql.upper()
        if "SELECT COUNT(*)" in upper:
            return super().execute(stmt, params)
        if "SUPERSEDED_AT = NOW()" in upper:
            self.statements.append(sql)
            self.supersede_params.append(dict(params or {}))
            return _Result([])
        if upper.startswith("SELECT") and (
            "THEORY_CLAIMS_LIVE" in upper or "FROM THEORY_CLAIMS " in upper
        ):
            self.statements.append(sql)
            return _Result(self._rows)
        return super().execute(stmt, params)

    def updates_for(self, row_id: str) -> list[str]:
        return [s for s in self.statements if row_id in str(s) or row_id in ""]


def _approved_row(stable_key="k1:approved-locally"):
    return {
        "id": "aaaaaaaa-0000-0000-0000-000000000001",
        "stable_key": stable_key,
        "agent_id": "claim_local",
        "review_status": "teacher_approved",
        "label": "承認済みの主張",
        "created_by": "u-teacher",
    }


class TestApprovedRowsSurviveReplace:
    def test_an_approved_row_absent_from_the_bundle_is_not_superseded(self, monkeypatch, env):
        row = _approved_row()
        session = _LiveRowSession([row])
        monkeypatch.setattr(export, "_pg_session", lambda: session)

        _call(_bundle_bytes(), dry_run=False, replace=True)

        superseded_ids = {
            value for params in session.supersede_params for value in params.values()
        }
        assert row["id"] not in superseded_ids, "承認済みの行が supersede された"

    def test_a_candidate_row_absent_from_the_bundle_is_superseded(self, monkeypatch, env):
        row = _approved_row()
        row["review_status"] = "teacher_review_required"  # 人間が触っていない
        session = _LiveRowSession([row])
        monkeypatch.setattr(export, "_pg_session", lambda: session)

        _call(_bundle_bytes(), dry_run=False, replace=True)

        superseded_ids = {
            value for params in session.supersede_params for value in params.values()
        }
        assert row["id"] in superseded_ids

    def test_the_dry_run_says_what_drops_out_and_what_stays(self, monkeypatch, env):
        session = _LiveRowSession([_approved_row()])
        monkeypatch.setattr(export, "_pg_session", lambda: session)

        out = _call(_bundle_bytes(), dry_run=True)

        counts = out["would_supersede_counts"]["claims"]
        assert counts["kept_human_decided"] == 1
        assert counts["superseded"] == 0
        assert counts["kept_labels"] == ["承認済みの主張"]
        assert session.committed == 0
        assert session.writes() == []
