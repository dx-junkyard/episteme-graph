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

#: 受け付ける束の上限（§4.2 の入力欄。**圧縮済みバイト列**の上限）。
MAX_BUNDLE_BYTES = 50 * 1024 * 1024

#: 展開後（非圧縮）の**合計**上限。圧縮サイズだけを見ると、高圧縮率の zip
#: （いわゆる zip bomb）が :data:`MAX_BUNDLE_BYTES` を通り抜けたあと ``read()`` で
#: 無制限に伸長する。読む前に宣言サイズを検査し、読むときも実測で打ち切る。
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

#: 展開後の**1 ファイルあたり**の上限（合計上限の内数）。
MAX_ENTRY_UNCOMPRESSED_BYTES = 64 * 1024 * 1024

#: 1 種別あたりの項目数の上限（束 1 つで作れる行数の天井）。
MAX_IMPORT_ITEMS_PER_KIND = 5000

#: manifest から読む出所テキストの上限（監査・run options へそのまま載るため）。
MAX_SOURCE_TEXT_CHARS = 200

#: manifest の ``scope.document_ids`` から読む件数の上限。
MAX_SOURCE_DOCUMENT_IDS = 50

#: manifest の ``app`` として受け取るキー（これ以外は捨てる）。
APP_KEYS: tuple[str, ...] = ("name", "version", "git_commit")

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

#: 取り込みが**読む**エントリ（名前は決め打ち。これ以外は展開しない = path traversal も
#: 持ち込めない）。展開後サイズの事前検査もこの集合に対して行う。
_KNOWN_FILES: tuple[str, ...] = (
    MANIFEST_NAME,
    CLAIMS_NAME,
    COMPONENTS_NAME,
    EQUATIONS_NAME,
    EVIDENCE_NAME,
    DERIVATIONS_NAME,
    COMPONENT_GRAPH_NAME,
)


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


def _clip(value: Any, limit: int = MAX_SOURCE_TEXT_CHARS) -> str:
    """manifest 由来のテキストを 1 行の長さ上限で切る（untrusted 入力の縮約）。"""
    return str(value or "").strip()[:limit]


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
        return _clip(self.manifest.get("export_schema_version"))

    @property
    def export_id(self) -> str:
        return _clip(self.manifest.get("export_id"))

    @property
    def exported_at(self) -> str:
        return _clip(self.manifest.get("exported_at"))

    @property
    def app(self) -> dict:
        """書き出し元アプリの名乗り（**3 つのスカラーだけ**に絞る）。

        manifest は第三者が書いた untrusted 入力で、``app`` はそのまま run options /
        監査 metadata / 画面へ渡る。入れ子の dict・配列・長大な文字列を素通しせず、
        :data:`APP_KEYS` のスカラーだけを :data:`MAX_SOURCE_TEXT_CHARS` で切って返す。
        """
        app = self.manifest.get("app")
        if not isinstance(app, dict):
            return {}
        out: dict[str, str] = {}
        for key in APP_KEYS:
            value = app.get(key)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            text = _clip(value)
            if text:
                out[key] = text
        return out

    @property
    def app_label(self) -> str:
        """画面と監査に出す 1 行の名乗り（``name（version）``。無ければ空）。"""
        app = self.app
        name = app.get("name") or ""
        version = app.get("version") or ""
        if name and version:
            return f"{name}（{version}）"
        return name or version

    @property
    def source_object_type(self) -> str:
        scope = self.manifest.get("scope")
        return _clip((scope or {}).get("type")) if isinstance(scope, dict) else ""

    @property
    def source_object_id(self) -> str:
        scope = self.manifest.get("scope") if isinstance(self.manifest.get("scope"), dict) else {}
        object_type = self.source_object_type
        if object_type:
            value = scope.get(f"{object_type}_id")
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return _clip(value)
        return ""

    @property
    def source_document_ids(self) -> list[str]:
        scope = self.manifest.get("scope") if isinstance(self.manifest.get("scope"), dict) else {}
        raw = scope.get("document_ids")
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            text = _clip(value)
            if text:
                out.append(text)
            if len(out) >= MAX_SOURCE_DOCUMENT_IDS:
                break
        return out

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


def _too_large(name: str) -> BundleError:
    """展開後サイズの上限超過（数値は出さない — 事実だけ）。"""
    return BundleError(
        "束の中のファイルが展開後の上限を超えています。",
        facts=[f"束の中の {name} が展開後の上限を超えています。"],
        report={"oversized_entry": name},
    )


class _ReadBudget:
    """展開（伸長）してよいバイト数の残高。

    ``zf.read()`` は伸長サイズを見ないので、①エントリの宣言サイズ（``file_size``）を
    読む前に検査し、②実際の読み取りは ``zf.open()`` + ``read(limit + 1)`` で打ち切って
    宣言が嘘でも伸ばさない、の 2 段で止める。
    """

    def __init__(
        self,
        total: int = MAX_UNCOMPRESSED_BYTES,
        per_entry: int = MAX_ENTRY_UNCOMPRESSED_BYTES,
    ):
        self.remaining = int(total)
        self.per_entry = int(per_entry)

    def limit(self) -> int:
        return max(0, min(self.per_entry, self.remaining))

    def spend(self, size: int) -> None:
        self.remaining = max(0, self.remaining - int(size))


def _read_json(zf: zipfile.ZipFile, name: str, names: set[str], budget: _ReadBudget) -> Any:
    if name not in names:
        return None
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    declared = int(getattr(info, "file_size", 0) or 0)
    limit = budget.limit()
    if declared > limit:
        raise _too_large(name)
    try:
        with zf.open(name) as handle:
            raw = handle.read(limit + 1)
    except (KeyError, RuntimeError, OSError, zipfile.BadZipFile) as exc:  # noqa: PERF203
        raise BundleError(
            f"束の中の {name} を読めませんでした。",
            facts=[f"束の中の {name} を読めませんでした。"],
        ) from exc
    if raw is None:
        raw = b""
    if len(raw) > limit:
        # 宣言サイズ（file_size）が実体より小さい細工。実測で打ち切って捨てる。
        raise _too_large(name)
    budget.spend(len(raw))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BundleError(
            f"束の中の {name} が JSON として読めません。",
            facts=[f"束の中の {name} が JSON として読めません。"],
        ) from exc
    except RecursionError as exc:
        # 深く入れ子にした JSON（``[[[[…]]]]``）。500 にせず 422 の事実文に畳む。
        raise BundleError(
            f"束の中の {name} の入れ子が深すぎて読めません。",
            facts=[f"束の中の {name} の入れ子が深すぎて読めません。"],
        ) from exc


def _items(payload: Any, key: str) -> list[dict]:
    if isinstance(payload, dict):
        values = payload.get(key)
    else:
        values = payload
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict)]


def _limited_items(payload: Any, key: str, *, kind_label: str) -> list[dict]:
    """``_items`` + 1 種別あたりの項目数の上限（超過は取り込まず 422）。"""
    values = _items(payload, key)
    if len(values) > MAX_IMPORT_ITEMS_PER_KIND:
        raise BundleError(
            f"束の{kind_label}の項目数が上限を超えています。",
            facts=[f"束の{kind_label}の項目数が上限を超えています。"],
            report={"oversized_kind": key, "limit": MAX_IMPORT_ITEMS_PER_KIND},
        )
    return values


def parse_bundle(data: bytes) -> ImportBundle:
    """束のバイト列を読み、検証して :class:`ImportBundle` を返す。

    Raises:
        BundleError: 上限超過（圧縮サイズ / 展開後サイズ / 項目数）/ zip でない /
            必須ファイル欠落 / JSON 不正（入れ子が深すぎる場合を含む）/
            スキーマ版が :data:`MIN_SCHEMA_VERSION` 未満。

    ``dry_run`` の経路も必ずここを通るため、上限の検査は「書き込むかどうか」より
    手前で効く（確認だけのつもりの操作でメモリを持って行かれない）。
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
        budget = _ReadBudget()

        # 読む前の門: これから読むエントリの**宣言サイズの合計**が展開後の上限を
        # 超えていれば、1 バイトも伸長せずに落とす（zip bomb の入口）。
        declared_total = 0
        for name in _KNOWN_FILES:
            if name not in names:
                continue
            try:
                declared_total += int(getattr(zf.getinfo(name), "file_size", 0) or 0)
            except KeyError:  # pragma: no cover - namelist と getinfo の不一致
                continue
        if declared_total > MAX_UNCOMPRESSED_BYTES:
            raise BundleError(
                "束の展開後のサイズが上限を超えています。",
                facts=["束の展開後のサイズが上限を超えています。"],
                report={"oversized_bundle": True},
            )

        missing = [name for name in REQUIRED_FILES if name not in names]
        if missing:
            raise BundleError(
                "束に必要なファイルがありません。",
                facts=[f"束に必要なファイルがありません: {', '.join(missing)}"],
                report={"missing_files": missing},
            )

        manifest = _read_json(zf, MANIFEST_NAME, names, budget)
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
                    f"{MIN_SCHEMA_VERSION}）。"
                    "この束は取り込めません。書き出し元のインスタンスで書き出し直してください。",
                ],
                report={"export_schema_version": schema_version, "min_schema_version": MIN_SCHEMA_VERSION},
            )

        claims = _limited_items(
            _read_json(zf, CLAIMS_NAME, names, budget), "claims", kind_label="主張"
        )
        components = _limited_items(
            _read_json(zf, COMPONENTS_NAME, names, budget), "components", kind_label="部品"
        )
        equations = _limited_items(
            _read_json(zf, EQUATIONS_NAME, names, budget), "equations", kind_label="式"
        )
        evidence = _limited_items(
            _read_json(zf, EVIDENCE_NAME, names, budget), "snippets", kind_label="根拠"
        )
        derivations = _limited_items(
            _read_json(zf, DERIVATIONS_NAME, names, budget), "chains", kind_label="導出"
        )
        graph = _read_json(zf, COMPONENT_GRAPH_NAME, names, budget)
        if isinstance(graph, dict):
            _limited_items(graph, "nodes", kind_label="グラフのノード")

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
