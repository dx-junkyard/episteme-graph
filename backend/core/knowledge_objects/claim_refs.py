"""主張参照（claim ref）を DB の主張へ解決する正本（IK-0930・読み時・非LLM）。

理論操作グラフ（``theory_component_graphs.graph_json``）と DSL の主張参照は、A層が生成時に
付けた**局所 ID**（``synth_claim_0003`` / ``claim_span_001_142`` / ``claim:{block}:{span}`` /
``{block}:{span}``）か、永続化後の**大域 ID**（``theory_claims.id`` の UUID）のどちらかで
書かれている。D層の読み手（支える主張・観測の選択肢・支持線の根・表示名）が UUID の形だけを
主張とみなすと、局所 ID の参照は黙って捨てられ、一覧が空のまま正常に終わる（IK-0930）。

規則（ここだけに置く。読み手は ``ClaimRefResolver`` を通す）:
  1. UUID はそのまま（その論文の live 行にあれば本文も引く）。
  2. 局所 ID は ``theory_claims_live`` の ``(document_id, agent_claim_id)``（migration 078 の
     索引）と ``source_scope.legacy_ids`` で UUID へ。綴りの揺れ（``claim:`` 接頭辞）は
     ``core/knowledge_objects/references.py`` の正規化を使う（二重に持たない）。
  3. どちらにも無ければ、採用 run の ``claim_object_builder`` artifact の本文で**名前だけ**
     返す（``claim_id`` は空 = 台帳は開けない。内部 ID は名前に使わない — PL7）。

局所 ID は**論文の中でしか一意でない**ので、解決器は必ず 1 本の論文に束ねる（document_id 必須）。
DB は渡された session で読む。FastAPI / LLM 非 import。失敗は fail-soft（解決できない = None）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.knowledge_objects.references import claim_ref_variants, normalize_claim_ref

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: 表示名の丸め（語の途中で切らない）。
_LABEL_MAX_CHARS = 160

_LIVE_CLAIMS_SQL = """
    SELECT id::text, COALESCE(agent_claim_id, ''),
           COALESCE(NULLIF(normalized_text, ''), text, ''), source_scope
    FROM theory_claims_live
    WHERE document_id = CAST(:doc AS uuid)
"""


def _is_uuid(value: Any) -> bool:
    return bool(_UUID_RE.match(str(value or "").strip()))


def _ref_text(value: Any) -> str:
    """参照値（文字列 / ``{"claim_id": ...}``）→ 綴りを揃えた文字列。"""
    if isinstance(value, dict):
        value = value.get("claim_id") or value.get("id") or ""
    return normalize_claim_ref(value)


def _legacy_ids(source_scope: Any) -> list[str]:
    scope = source_scope
    if isinstance(scope, str) and scope.strip():
        try:
            scope = json.loads(scope)
        except Exception:  # noqa: BLE001
            scope = {}
    if not isinstance(scope, dict):
        return []
    values = scope.get("legacy_ids")
    return [str(v) for v in values if str(v or "").strip()] if isinstance(values, list) else []


def _excerpt(value: Any) -> str:
    from core.text_excerpt import excerpt

    text = str(value or "").strip()
    return excerpt(text, _LABEL_MAX_CHARS) or text[:_LABEL_MAX_CHARS]


class ClaimRefResolver:
    """1 本の論文の主張参照を UUID・本文へ解決する（論文ごとの読み時辞書を 1 回だけ作る）。

    Args:
        session: SQLAlchemy session（呼び出し側が持つもの。閉じない）。
        document_id: 参照が属する論文（UUID 文字列）。空なら何も解決しない。
        artifact_claim_texts: ``claim_object_builder`` の ``{agent_id: 本文}``（省略時は必要に
            なったときに採用 run から 1 回だけ読む）。テストと、既に artifact を持つ呼び出し側用。
    """

    def __init__(
        self,
        session: Any,
        document_id: str | None,
        *,
        artifact_claim_texts: dict[str, str] | None = None,
    ) -> None:
        self.session = session
        doc = str(document_id or "").strip().lower()
        self.document_id = doc if _is_uuid(doc) else ""
        self._uuid_by_key: dict[str, str] | None = None
        self._text_by_uuid: dict[str, str] = {}
        self._artifact_texts = artifact_claim_texts

    # -- 読み込み（遅延・1 回だけ） ------------------------------------------------
    def _load_live(self) -> dict[str, str]:
        if self._uuid_by_key is not None:
            return self._uuid_by_key
        index: dict[str, str] = {}
        if self.document_id and self.session is not None:
            try:
                from sqlalchemy import text as sa_text

                rows = self.session.execute(
                    sa_text(_LIVE_CLAIMS_SQL), {"doc": self.document_id}
                ).fetchall()
            except Exception:  # noqa: BLE001 — 引けない = 解決しない（fail-soft）
                logger.debug("claim ref lookup failed for %s", self.document_id, exc_info=True)
                try:
                    self.session.rollback()
                except Exception:  # noqa: BLE001
                    pass
                rows = []
            for row in rows or []:
                try:
                    uid = str(row[0] or "").strip().lower()
                    agent_id = str(row[1] or "").strip()
                    text = str(row[2] or "")
                    legacy = _legacy_ids(row[3] if len(row) > 3 else None)
                except Exception:  # noqa: BLE001
                    continue
                if not _is_uuid(uid):
                    continue
                self._text_by_uuid[uid] = text
                index.setdefault(uid, uid)
                for key in [agent_id, *legacy]:
                    for variant in claim_ref_variants(key):
                        index.setdefault(variant, uid)
        self._uuid_by_key = index
        return index

    def _artifact(self) -> dict[str, str]:
        if self._artifact_texts is not None:
            return self._artifact_texts
        texts: dict[str, str] = {}
        if self.document_id:
            try:
                from core.document_pipeline.persistence import document_run_artifacts

                artifacts = document_run_artifacts(self.document_id, session=self.session) or {}
                payload = artifacts.get("claim_object_builder")
                payload = payload if isinstance(payload, dict) else {}
                for claim in payload.get("claims") or payload.get("claim_objects") or []:
                    if not isinstance(claim, dict):
                        continue
                    cid = str(claim.get("claim_id") or "").strip()
                    text = str(claim.get("normalized_text") or claim.get("text") or "").strip()
                    if cid and text:
                        for variant in claim_ref_variants(cid):
                            texts.setdefault(variant, text)
            except Exception:  # noqa: BLE001
                logger.debug("claim artifact texts unavailable for %s", self.document_id, exc_info=True)
        self._artifact_texts = texts
        return texts

    # -- 公開 API --------------------------------------------------------------------
    def uuid_for(self, ref: Any) -> str:
        """参照 → この論文の live 主張の UUID（無ければ空文字）。"""
        raw = _ref_text(ref)
        if not raw:
            return ""
        index = self._load_live()
        for variant in claim_ref_variants(raw) + claim_ref_variants(str(ref if isinstance(ref, str) else "")):
            key = variant.strip()
            hit = index.get(key) or index.get(key.lower())
            if hit:
                return hit
        return ""

    def label_for(self, ref: Any) -> str:
        """参照 → 主張の本文（DB → artifact の順。引けなければ空文字 — ID は返さない）。"""
        uid = self.uuid_for(ref)
        if uid:
            text = self._text_by_uuid.get(uid) or ""
            if text.strip():
                return _excerpt(text)
        raw = _ref_text(ref)
        if not raw or _is_uuid(raw):
            return ""
        texts = self._artifact()
        for variant in claim_ref_variants(raw):
            text = texts.get(variant)
            if text:
                return _excerpt(text)
        return ""

    def resolve(self, ref: Any) -> dict | None:
        """参照 → ``{"claim_id": UUID か空, "label": 本文か空, "ref": 正規化した参照}``。

        UUID にも本文にも届かなければ None。
        """
        raw = _ref_text(ref)
        if not raw:
            return None
        uid = self.uuid_for(raw)
        label = self.label_for(raw)
        if not uid and not label:
            return None
        return {"claim_id": uid, "label": label, "ref": raw}

    def aliases(self, ref: Any) -> list[str]:
        """1 つの参照が名乗り得る綴り（元の綴り・正規化・UUID。順序保持・重複なし）。"""
        out: list[str] = []
        for value in claim_ref_variants(ref if isinstance(ref, str) else _ref_text(ref)):
            if value not in out:
                out.append(value)
        uid = self.uuid_for(ref)
        if uid and uid not in out:
            out.append(uid)
        return out


class ClaimRefResolvers:
    """複数論文の解決器を論文ごとに 1 つずつ作って使い回す。"""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._by_document: dict[str, ClaimRefResolver] = {}

    def for_document(self, document_id: str | None) -> ClaimRefResolver:
        key = str(document_id or "").strip().lower()
        if key not in self._by_document:
            self._by_document[key] = ClaimRefResolver(self.session, key)
        return self._by_document[key]


__all__ = ["ClaimRefResolver", "ClaimRefResolvers"]
