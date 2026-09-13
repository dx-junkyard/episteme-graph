"""束（ZIP）の読み取りと検証（knowledge_transfer_design.md §4.2）。

zip をディスクへ展開せず、**名前を決め打ちで読む**だけ（path traversal を作らない）。
検証に落ちたときは :class:`BundleError` を投げ、route 層が 422 の事実文に変換する。

純関数（stdlib のみ）。FastAPI / sqlalchemy / LLM は import しない。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Any

#: 受け付ける束の上限（§4.2 の入力欄）。
MAX_BUNDLE_BYTES = 50 * 1024 * 1024

#: 取り込みが前提にするキーが揃った最小スキーマ版（§4.1）。
MIN_SCHEMA_VERSION = "0.3.0"

MANIFEST_NAME = "manifest.json"
CLAIMS_NAME = "claims/claims.json"
COMPONENTS_NAME = "components/components.json"
EQUATIONS_NAME = "equations/equations.json"
EVIDENCE_NAME = "evidence/evidence_snippets.json"
DERIVATIONS_NAME = "derivations/derivation_chains.json"
COMPONENT_GRAPH_NAME = "graph/component_graph.json"

#: 欠けていたら取り込めない束の中身（§4.2 の検証 422）。
REQUIRED_FILES: tuple[str, ...] = (MANIFEST_NAME, CLAIMS_NAME, COMPONENTS_NAME)


class BundleError(Exception):
    """束を取り込めない理由（422 に畳む）。

    ``facts`` は学習者ではなく教員に見せる日本語の事実文、``report`` は
    ``_validate_export_references`` の errors / warnings のような機械可読な内訳。
    """

    def __init__(self, message: str, *, facts: list[str] | None = None, report: dict | None = None):
        super().__init__(message)
        self.message = message
        self.facts = list(facts or [message])
        self.report = dict(report or {})


@dataclass
class ImportBundle:
    """束の中身（読み取り済み・検証済み）。"""

    manifest: dict = field(default_factory=dict)
    claims: list[dict] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    equations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    derivation_chains: list[dict] = field(default_factory=list)
    component_graph: dict = field(default_factory=dict)
    sha256: str = ""
    names: list[str] = field(default_factory=list)

    # -- manifest の投影（束の中の事実だけを読む。人は載っていない） --------

    @property
    def schema_version(self) -> str:
        return str(self.manifest.get("export_schema_version") or "")

    @property
    def export_id(self) -> str:
        return str(self.manifest.get("export_id") or "")

    @property
    def exported_at(self) -> str:
        return str(self.manifest.get("exported_at") or "")

    @property
    def app(self) -> dict:
        app = self.manifest.get("app")
        return dict(app) if isinstance(app, dict) else {}

    @property
    def source_object_type(self) -> str:
        scope = self.manifest.get("scope")
        return str((scope or {}).get("type") or "") if isinstance(scope, dict) else ""

    @property
    def source_object_id(self) -> str:
        scope = self.manifest.get("scope") if isinstance(self.manifest.get("scope"), dict) else {}
        object_type = self.source_object_type
        if object_type:
            value = scope.get(f"{object_type}_id")
            if value:
                return str(value)
        return ""

    @property
    def source_document_ids(self) -> list[str]:
        scope = self.manifest.get("scope") if isinstance(self.manifest.get("scope"), dict) else {}
        return [str(v) for v in (scope.get("document_ids") or []) if v]

    @property
    def graph_nodes(self) -> list[dict]:
        nodes = self.component_graph.get("nodes")
        return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []

    def counts(self) -> dict:
        """dry-run / 監査に載せる件数（§4.2）。"""
        return {
            "claims": len(self.claims),
            "components": len(self.components),
            "equations": len(self.equations),
            "evidence": len(self.evidence),
            "derivation_steps": sum(
                len([s for s in (c.get("steps") or []) if isinstance(s, dict)])
                for c in self.derivation_chains
            ),
            "graph_nodes": len(self.graph_nodes),
        }

    def source(self) -> dict:
        """束の出所（取り込み行の payload と監査 metadata に載せる事実）。"""
        return {
            "export_id": self.export_id,
            "object_type": self.source_object_type,
            "object_id": self.source_object_id,
            "document_ids": self.source_document_ids,
            "exported_at": self.exported_at,
            "app": self.app,
            "bundle_sha256": self.sha256,
            "schema_version": self.schema_version,
        }


# ---------------------------------------------------------------------------
# 版の比較
# ---------------------------------------------------------------------------


def version_tuple(value: str) -> tuple[int, ...]:
    """``"0.3.0"`` → ``(0, 3, 0)``。数字でない部分があれば :class:`ValueError`。"""
    parts = str(value or "").strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"invalid schema version: {value!r}")
    return tuple(int(p) for p in parts)


def _meets_min_version(value: str) -> bool:
    try:
        return version_tuple(value) >= version_tuple(MIN_SCHEMA_VERSION)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------


def _read_json(zf: zipfile.ZipFile, name: str, names: set[str]) -> Any:
    if name not in names:
        return None
    try:
        raw = zf.read(name)
    except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:  # noqa: PERF203
        raise BundleError(
            f"束の中の {name} を読めませんでした。",
            facts=[f"束の中の {name} を読めませんでした。"],
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BundleError(
            f"束の中の {name} が JSON として読めません。",
            facts=[f"束の中の {name} が JSON として読めません。"],
        ) from exc


def _items(payload: Any, key: str) -> list[dict]:
    if isinstance(payload, dict):
        values = payload.get(key)
    else:
        values = payload
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict)]


def parse_bundle(data: bytes) -> ImportBundle:
    """束のバイト列を読み、検証して :class:`ImportBundle` を返す。

    Raises:
        BundleError: 上限超過 / zip でない / 必須ファイル欠落 / JSON 不正 /
            スキーマ版が :data:`MIN_SCHEMA_VERSION` 未満。
    """
    if not data:
        raise BundleError("束が空です。", facts=["束が空です。"])
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(
            "束のサイズが上限を超えています。",
            facts=["束のサイズが上限を超えています。"],
        )

    sha256 = hashlib.sha256(data).hexdigest()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleError(
            "束が ZIP として読めません。",
            facts=["束が ZIP として読めません。"],
        ) from exc

    with zf:
        names = set(zf.namelist())
        missing = [name for name in REQUIRED_FILES if name not in names]
        if missing:
            raise BundleError(
                "束に必要なファイルがありません。",
                facts=[f"束に必要なファイルがありません: {', '.join(missing)}"],
                report={"missing_files": missing},
            )

        manifest = _read_json(zf, MANIFEST_NAME, names)
        if not isinstance(manifest, dict):
            raise BundleError(
                "束の manifest.json が読めません。",
                facts=["束の manifest.json が読めません。"],
            )
        schema_version = str(manifest.get("export_schema_version") or "")
        if not _meets_min_version(schema_version):
            raise BundleError(
                "束のスキーマ版が取り込みに対応していません。",
                facts=[
                    "束のスキーマ版が取り込みに対応していません"
                    f"（束: {schema_version or '未記載'} / 取り込みが必要とする最小版: "
                    f"{MIN_SCHEMA_VERSION}）。",
                ],
                report={"export_schema_version": schema_version, "min_schema_version": MIN_SCHEMA_VERSION},
            )

        claims = _items(_read_json(zf, CLAIMS_NAME, names), "claims")
        components = _items(_read_json(zf, COMPONENTS_NAME, names), "components")
        equations = _items(_read_json(zf, EQUATIONS_NAME, names), "equations")
        evidence = _items(_read_json(zf, EVIDENCE_NAME, names), "snippets")
        derivations = _items(_read_json(zf, DERIVATIONS_NAME, names), "chains")
        graph = _read_json(zf, COMPONENT_GRAPH_NAME, names)

    return ImportBundle(
        manifest=manifest,
        claims=claims,
        components=components,
        equations=equations,
        evidence=evidence,
        derivation_chains=derivations,
        component_graph=graph if isinstance(graph, dict) else {},
        sha256=sha256,
        names=sorted(names),
    )
