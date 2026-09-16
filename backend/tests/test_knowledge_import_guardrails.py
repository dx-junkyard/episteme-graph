"""知識の転用層 P4-1 の不変条項ガードレール（knowledge_transfer_design.md §2 / §11）。

構造的に固定するのは:

- **KT3**: ``core/knowledge_import/`` は FastAPI / ``core.llm`` / embedding を import しない
  （取り込みは決定論・LLM 0 回・embedding 0 回）。``bundle.py`` / ``rows.py`` は
  sqlalchemy も触らない純関数。
- **KT5**: 取り込み経路に ``DELETE FROM`` が無い（supersede 同期だけ）。行削除 API も無い。
- **KT2 / T-1**: 取り込み行の ``review_status`` / ``status`` が固定値で、束の値を
  引き継ぐ代入が無い。
- **KT4**: stable_key は ``core/knowledge_objects/stable_key.py`` の関数で作り、
  束の ``stable_key`` をそのまま同一性に使わない。
- **KT8**: 監査は ``AUDIT_ENTITY_IMPORT``（カタログ定数）を使い、束の中に人を書かない。
- **KT6**: import の権限ゲートは **編集**（``can_edit``）で、export の閲覧ゲートと混ぜない。
- **KO5**: 同一性キーの join は live ビューを読む。
"""

from __future__ import annotations

import ast
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
    extract_function_source,
)

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

IMPORT_DIR = BACKEND / "core" / "knowledge_import"
EXPORT_ROUTE = BACKEND / "api" / "routes" / "export.py"
DESIGN_DOC = ROOT / "docs" / "features" / "knowledge_transfer_design.md"


def _export_src() -> str:
    return EXPORT_ROUTE.read_text(encoding="utf-8")


def _import_sources() -> list[Path]:
    return sorted(IMPORT_DIR.glob("*.py"))


# ---------------------------------------------------------------------------
# KT3: core は FastAPI / LLM を知らない
# ---------------------------------------------------------------------------


class TestCoreStaysPure:
    def test_core_does_not_import_fastapi_or_llm(self):
        assert_module_tree_does_not_import(
            IMPORT_DIR,
            ["fastapi", "core.llm", "openai", "sqlalchemy.orm"],
        )

    def test_core_does_not_call_an_llm_or_an_embedder(self):
        assert_module_tree_forbids(
            IMPORT_DIR,
            [
                "generate_text",
                "generate_structured",
                "generate_embeddings",
                "usage_context(",
                "CostGate",
            ],
        )

    def test_bundle_and_rows_are_pure_functions(self):
        """束の読み取りと行の組み立ては DB を知らない（テストで単体検証できる）。"""
        for name in ("bundle.py", "rows.py"):
            src = (IMPORT_DIR / name).read_text(encoding="utf-8")
            assert_source_forbids(
                src, ["import sqlalchemy", "from sqlalchemy", "sa_text(", "session."], context=name
            )

    def test_every_module_is_accounted_for(self):
        """設計書 §4.2 が挙げた 3 モジュールがある（増やすときは設計書も直す）。"""
        names = {p.name for p in _import_sources()}
        assert {"__init__.py", "bundle.py", "rows.py", "apply.py"} == names


# ---------------------------------------------------------------------------
# KT5: 情報を落とさない
# ---------------------------------------------------------------------------


class TestNothingIsDeleted:
    def test_no_delete_statement_in_the_import_path(self):
        assert_module_tree_forbids(IMPORT_DIR, ["DELETE FROM", "delete from", "DROP TABLE"])

    def test_the_import_endpoint_issues_no_delete(self):
        src = extract_function_source(_export_src(), "import_document_bundle")
        assert_source_forbids(src, ["DELETE ", "TRUNCATE"], context="import_document_bundle")

    def test_there_is_no_delete_route_for_imports(self):
        src = _export_src()
        assert "@router.delete" not in src

    def test_supersede_is_delegated_to_phase1_sync(self):
        """置き換えは Phase 1 の sync_live_rows と同じ意味論に載せる（T-2）。"""
        src = (IMPORT_DIR / "apply.py").read_text(encoding="utf-8")
        assert "from core.knowledge_objects.sync import sync_live_rows" in src


# ---------------------------------------------------------------------------
# KT2 / T-1: 取り込みは候補で着地する
# ---------------------------------------------------------------------------


class TestImportLandsAsCandidate:
    def test_review_status_constants_are_fixed(self):
        from core.knowledge_import import rows as import_rows

        assert import_rows.IMPORT_REVIEW_STATUS == "teacher_review_required"
        assert import_rows.IMPORT_COMPONENT_STATUS == "candidate"

    def test_rows_never_assign_the_bundle_review_status(self):
        """``"review_status": <束の値>`` の代入が無い（AST で値の形を見る）。"""
        tree = ast.parse((IMPORT_DIR / "rows.py").read_text(encoding="utf-8"))
        offending: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value in ("review_status", "status")):
                    continue
                # 許すのは定数（"candidate"）と IMPORT_* 定数の参照だけ。
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    continue
                if isinstance(value, ast.Name) and value.id.startswith("IMPORT_"):
                    continue
                offending.append(ast.dump(value)[:120])
        assert offending == [], f"取り込み行の確定状態が束から来ている: {offending}"

    def test_the_bundle_review_status_is_kept_as_a_fact(self):
        src = (IMPORT_DIR / "rows.py").read_text(encoding="utf-8")
        assert "source_review_status" in src

    def test_no_approval_audit_is_written_on_import(self):
        """KT2: 取り込みで承認系の監査行を作らない。"""
        src = extract_function_source(_export_src(), "_record_import_audit")
        for term in ("approved", "teacher_approved", "endorse", "AUDIT_ENTITY_COMPONENT", "AUDIT_ENTITY_CLAIM"):
            assert term not in src, f"取り込みが承認を記帳している: {term}"


# ---------------------------------------------------------------------------
# KT4: stable_key は取り込み先で再計算する
# ---------------------------------------------------------------------------


class TestStableKeyIsRecomputed:
    def test_rows_use_the_phase1_key_functions(self):
        src = (IMPORT_DIR / "rows.py").read_text(encoding="utf-8")
        assert "from core.knowledge_objects import stable_key as ko_keys" in src
        for fn in (
            "ko_keys.claim_stable_key",
            "ko_keys.component_stable_key",
            "ko_keys.equation_stable_key",
            "ko_keys.evidence_stable_key",
            "ko_keys.derivation_step_stable_key",
        ):
            assert fn in src, f"Phase 1 のキー関数を使っていない: {fn}"

    def test_rows_never_use_the_bundle_key_as_the_identity(self):
        """``"stable_key": item.get("stable_key")`` のような素通しが無い。"""
        tree = ast.parse((IMPORT_DIR / "rows.py").read_text(encoding="utf-8"))
        offending: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "stable_key"):
                    continue
                dumped = ast.dump(value)
                # 許すのは ko_keys.* の呼び出しと、そこから引いた写像の参照だけ。
                if "ko_keys" in dumped or "equation_keys" in dumped or "keys" in dumped:
                    continue
                offending.append(dumped[:160])
        assert offending == [], f"束の stable_key を同一性に使っている: {offending}"

    def test_the_manifest_declares_the_recomputation(self):
        src = extract_function_source(_export_src(), "_build_manifest")
        assert "stable_key_recomputed_on_import" in src


# ---------------------------------------------------------------------------
# KT6 / KT8: 権限と監査
# ---------------------------------------------------------------------------


class TestPermissionAndAudit:
    def test_import_requires_edit_not_view(self):
        src = extract_function_source(_export_src(), "_require_editable_document_or_404")
        assert "can_edit" in src
        assert "can_view" not in src, "取り込みの境界を閲覧にしてはならない（KT6）"

    def test_import_folds_missing_and_forbidden_into_the_same_404(self):
        src = extract_function_source(_export_src(), "_require_editable_document_or_404")
        assert "status_code=404" in src
        assert "403" not in src

    def test_the_import_endpoint_requires_a_teacher(self):
        src = _export_src()
        marker = '@router.post("/api/documents/{document_id}/import-bundle")'
        assert marker in src
        after = src.split(marker, 1)[1]
        head = after.split("\n\n", 1)[0]
        assert "Depends(_require_teacher)" in head

    def test_audit_uses_the_catalog_constant(self):
        from core.schema import AUDIT_ENTITY_IMPORT, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_IMPORT in AUDIT_ENTITY_TYPES
        src = extract_function_source(_export_src(), "_record_import_audit")
        assert "AUDIT_ENTITY_IMPORT" in src
        assert "record_review_event(" in src

    def test_the_audit_records_the_bundle_not_its_content(self):
        """監査に資料本文（claim text など）を載せない。"""
        src = extract_function_source(_export_src(), "_record_import_audit")
        for term in ('"text"', "claim_text", "evidence_text", "normalized_text"):
            assert term not in src, f"監査に資料本文が載っている: {term}"


# ---------------------------------------------------------------------------
# JSON-LD（§4.1）
# ---------------------------------------------------------------------------


class TestJsonLd:
    def test_the_crate_builder_writes_no_person(self):
        src = extract_function_source(_export_src(), "_build_ro_crate")
        for term in ("author", "creator", "publisher", "Person", "user_id", "changed_by"):
            assert term not in src, f"JSON-LD に人を書いている: {term}"

    def test_the_crate_builder_has_no_counts(self):
        src = extract_function_source(_export_src(), "_build_ro_crate")
        for term in ("len(", "count", "confidence", "weight", "score"):
            assert term not in src, f"JSON-LD に数値を載せている: {term}"

    def test_the_schema_version_constant_is_the_single_source(self):
        import routes.export as export

        assert export.EXPORT_SCHEMA_VERSION == "0.3.0"
        src = _export_src()
        # manifest / import_support の両方が定数を使う（版を二重管理しない）。
        assert '"export_schema_version": EXPORT_SCHEMA_VERSION' in src
        assert '"min_schema_version": EXPORT_SCHEMA_VERSION' in src

    def test_the_import_minimum_matches_the_export_version(self):
        import routes.export as export
        from core.knowledge_import.bundle import MIN_SCHEMA_VERSION

        assert MIN_SCHEMA_VERSION == export.EXPORT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# KO5 / 検証の再利用
# ---------------------------------------------------------------------------


class TestReadersAndValidation:
    def test_identity_join_reads_live_views(self):
        src = _export_src()
        assert '"theory_claims_live"' in src
        assert '"theory_components_live"' in src

    def test_bundle_validation_reuses_the_export_checker(self):
        """``check_refs`` は route 側の既存関数を使う（core へ移さない = §4.2）。"""
        src = extract_function_source(_export_src(), "_validate_bundle_internal_references")
        assert "_validate_export_references(" in src
        # core 側は検証関数を持たない（route の既存実装を1つだけ使う）。
        for path in _import_sources():
            if path.name == "__init__.py":
                continue
            body = path.read_text(encoding="utf-8")
            assert "def _validate_export_references" not in body, path

    def test_the_upload_is_size_capped(self):
        from core.knowledge_import.bundle import MAX_BUNDLE_BYTES

        assert MAX_BUNDLE_BYTES == 50 * 1024 * 1024
        src = extract_function_source(_export_src(), "_read_upload")
        assert "MAX_BUNDLE_BYTES" in src

    def test_the_bundle_is_never_extracted_to_disk(self):
        """zip はメモリ上で名前決め打ちで読む（path traversal を作らない）。

        ``zf.open(name)`` は**メモリ上のストリーム**（伸長を read で打ち切るために使う）で、
        ディスクへの展開ではない。禁止するのは展開 API と**素の** ``open(``。
        """
        assert_module_tree_forbids(
            IMPORT_DIR, ["extractall", ".extract(", "tempfile", "NamedTemporary"]
        )
        bare_open = re.compile(r"(?<![\w.])open\(")
        for path in sorted(IMPORT_DIR.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            assert not bare_open.search(text), f"ファイルを開いている: {path}"


# ---------------------------------------------------------------------------
# 設計書との対応
# ---------------------------------------------------------------------------


class TestDesignDoc:
    def test_the_design_doc_declares_the_endpoint(self):
        text = DESIGN_DOC.read_text(encoding="utf-8")
        assert "/api/documents/{document_id}/import-bundle" in text
        assert "ro-crate-metadata.json" in text
        assert "0.3.0" in text


# ---------------------------------------------------------------------------
# 敵対的な束（P4 ⑤-11。敵対的セキュリティレビュー P4-R1 / R3 / R5 / R12）
#
# 「壊れた束」ではなく「**壊しに来た束**」を固定する。いずれも
# ``parse_bundle`` の中で落ちる = dry_run でも同じ門を通る（確認だけのつもりの
# 操作でメモリやプロセスを持って行かれない）。
# ---------------------------------------------------------------------------


def _zip(entries: dict, *, compress=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compress) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload if isinstance(payload, (bytes, str)) else json.dumps(payload))
    return buffer.getvalue()


def _minimal(**overrides) -> dict:
    entries = {
        "manifest.json": {
            "export_schema_version": "0.3.0",
            "export_id": "export_x",
            "scope": {"type": "document", "document_id": "doc-source"},
        },
        "claims/claims.json": {"claims": []},
        "components/components.json": {"components": []},
    }
    entries.update(overrides)
    return entries


class TestAdversarialBundles:
    def test_a_zip_bomb_is_rejected_before_it_is_expanded(self):
        """P4-R1: 圧縮サイズは小さく、展開すると巨大な束を受け付けない。"""
        from core.knowledge_import.bundle import (
            MAX_UNCOMPRESSED_BYTES,
            BundleError,
            parse_bundle,
        )

        data = _zip(_minimal(**{"claims/claims.json": b"0" * (MAX_UNCOMPRESSED_BYTES + 1024)}))
        # 圧縮後は上限（50MB）を軽く通り抜ける大きさ。
        assert len(data) < 50 * 1024 * 1024
        with pytest.raises(BundleError) as exc:
            parse_bundle(data)
        assert "上限を超えています" in " ".join(exc.value.facts)
        # 事実文に数値（上限の実数）を書かない。
        assert not any(ch.isdigit() for ch in " ".join(exc.value.facts))

    def test_a_lying_file_size_is_cut_off_by_the_measured_read(self):
        """P4-R1: 宣言サイズ（file_size）が嘘でも、read で打ち切る。"""
        from unittest.mock import patch

        from core.knowledge_import.bundle import (
            MAX_ENTRY_UNCOMPRESSED_BYTES,
            BundleError,
            parse_bundle,
        )

        data = _zip(_minimal(**{
            "claims/claims.json": b"0" * (MAX_ENTRY_UNCOMPRESSED_BYTES + 1024),
        }))
        real_getinfo = zipfile.ZipFile.getinfo

        def _lying_getinfo(self, name):
            info = real_getinfo(self, name)
            if name == "claims/claims.json":
                info.file_size = 1  # 「1 バイトです」と名乗る
            return info

        with patch.object(zipfile.ZipFile, "getinfo", _lying_getinfo):
            with pytest.raises(BundleError):
                # 宣言が嘘でも束は通らない（読み取りは limit + 1 で打ち切られ、
                # 展開されたバイト列が上限を超えたままメモリに載ることはない）。
                parse_bundle(data)

    def test_the_read_is_cut_off_at_the_budget(self):
        """P4-R1: 実測の打ち切り（宣言ではなく読んだ量で落とす）。"""
        from core.knowledge_import import bundle as bundle_mod

        budget = bundle_mod._ReadBudget(total=10, per_entry=10)
        data = _zip({"manifest.json": b"0" * 64})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            with pytest.raises(bundle_mod.BundleError) as exc:
                bundle_mod._read_json(zf, "manifest.json", {"manifest.json"}, budget)
        assert "上限を超えています" in " ".join(exc.value.facts)

    def test_deeply_nested_json_is_a_422_not_a_500(self):
        """P4-R12: RecursionError を束の事実文に畳む（500 を出さない）。"""
        from core.knowledge_import.bundle import BundleError, parse_bundle

        deep = "[" * 120000 + "]" * 120000
        data = _zip(_minimal(**{"claims/claims.json": deep}))
        with pytest.raises(BundleError) as exc:
            parse_bundle(data)
        assert "読めません" in " ".join(exc.value.facts)

    def test_too_many_items_in_one_kind_is_rejected(self):
        """P4-R3: 1 つの束で作れる行数に天井がある。"""
        from core.knowledge_import.bundle import (
            MAX_IMPORT_ITEMS_PER_KIND,
            BundleError,
            parse_bundle,
        )

        claims = [{"claim_id": f"c{i}", "text": "x"} for i in range(MAX_IMPORT_ITEMS_PER_KIND + 1)]
        data = _zip(_minimal(**{"claims/claims.json": {"claims": claims}}))
        with pytest.raises(BundleError) as exc:
            parse_bundle(data)
        assert "項目数が上限を超えています" in " ".join(exc.value.facts)

    def test_path_traversal_entries_are_never_read(self):
        """束の中に ``../`` の名前があっても、読むのは決め打ちの名前だけ。"""
        from core.knowledge_import.bundle import parse_bundle

        data = _zip(_minimal(**{
            "../../etc/passwd": "root:x:0:0",
            "/absolute/evil.json": "{}",
            "claims/../../../escape.json": "{}",
        }))
        bundle = parse_bundle(data)
        # 名前は「束に入っていた事実」として残るが、読まれてはいない。
        assert bundle.counts()["claims"] == 0
        assert bundle.manifest.get("export_id") == "export_x"

    def test_manifest_strings_are_clipped_and_the_app_is_three_scalars(self):
        """P4-R5: manifest は untrusted。run options / 監査へ素通ししない。"""
        from core.knowledge_import.bundle import (
            MAX_SOURCE_DOCUMENT_IDS,
            MAX_SOURCE_TEXT_CHARS,
            parse_bundle,
        )

        data = _zip(_minimal(**{
            "manifest.json": {
                "export_schema_version": "0.3.0",
                "export_id": "E" * 5000,
                "exported_at": "T" * 5000,
                "app": {
                    "name": "N" * 5000,
                    "version": "1.0",
                    "git_commit": "abc",
                    "nested": {"deep": ["payload"] * 100},
                    "extra": "x" * 5000,
                },
                "scope": {
                    "type": "document",
                    "document_id": "D" * 5000,
                    "document_ids": [f"doc-{i}" for i in range(500)],
                },
            },
        }))
        bundle = parse_bundle(data)
        assert set(bundle.app) <= {"name", "version", "git_commit"}
        assert len(bundle.app["name"]) == MAX_SOURCE_TEXT_CHARS
        assert len(bundle.export_id) == MAX_SOURCE_TEXT_CHARS
        assert len(bundle.exported_at) == MAX_SOURCE_TEXT_CHARS
        assert len(bundle.source_object_id) == MAX_SOURCE_TEXT_CHARS
        assert len(bundle.source_document_ids) == MAX_SOURCE_DOCUMENT_IDS
        # source() は run options / 監査に載る。ここに入れ子は残らない。
        assert set(bundle.source()["app"]) <= {"name", "version", "git_commit"}

    def test_the_minimum_schema_version_message_says_to_export_again(self):
        """P4-R11: 「対応していない」で終わらせず、次の一手を書く。"""
        from core.knowledge_import.bundle import BundleError, parse_bundle

        data = _zip(_minimal(**{
            "manifest.json": {"export_schema_version": "0.2.0", "export_id": "e"},
        }))
        with pytest.raises(BundleError) as exc:
            parse_bundle(data)
        assert "書き出し直して" in " ".join(exc.value.facts)
