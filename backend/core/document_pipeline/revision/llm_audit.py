"""Real LLM source re-audit client for the production path (#410 P1-6).

The deterministic audit (audit.py) only keys verdicts off the checkpoint
``trigger_reason``. This client actually compares the existing artifact entity
against the retrieved source text using an LLM, keeping source-derived evidence
separate from LLM analysis notes, and never confirming a revision when the source
could not be retrieved or the LLM output is unusable.

The LLM call is injected (``generate``) so it is fully testable without network;
in production it defaults to ``core.llm.generate_text``.
"""
from __future__ import annotations

import json
from typing import Any, Callable

# checkpoint target_type -> inventory entity bucket
_BUCKET = {
    "claim": "claims", "equation": "equations", "evidence": "evidence",
    "derivation": "derivations", "component": "components",
    "graph_node": "graph_nodes", "graph_edge": "graph_edges", "thesis": "thesis",
}


def _default_generate(messages: list[dict]) -> str:
    from core.llm import generate_text
    return generate_text(messages)


def llm_enabled() -> bool:
    """True only when a *real* (non-test) LLM key is configured.

    Keeps unit tests on the deterministic path (no network) while the production
    path uses the real client.
    """
    try:
        from core.config import get_settings
        key = (get_settings().llm_api_key or "").strip()
    except Exception:
        return False
    return bool(key) and not key.lower().startswith("sk-test")


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.replace("json", "", 1).strip("` \n")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in LLM audit response")
    return json.loads(text[start:end + 1])


class LLMAuditClient:
    """Callable ``(checkpoint, retrieval) -> audit_dict`` backed by an LLM."""

    def __init__(self, inventory: dict | None, *, generate: Callable[[list[dict]], str] | None = None):
        self._entities = (inventory or {}).get("entities", {}) or {}
        self._generate = generate or _default_generate

    def _entity(self, checkpoint: dict) -> dict:
        bucket = _BUCKET.get(checkpoint.get("target_type"))
        if not bucket:
            return {}
        return (self._entities.get(bucket, {}) or {}).get(checkpoint.get("target_id"), {})

    def _related(self, entity: dict) -> dict:
        related: dict[str, list] = {"equations": [], "evidence": []}
        for eid in entity.get("equation_ids", []) or []:
            eq = (self._entities.get("equations", {}) or {}).get(eid)
            if eq:
                related["equations"].append({"id": eid, "latex": eq.get("latex"),
                                             "raw_text": eq.get("raw_text")})
        for vid in entity.get("evidence_ids", []) or []:
            ev = (self._entities.get("evidence", {}) or {}).get(vid)
            if ev:
                related["evidence"].append({"id": vid, "text": ev.get("evidence_text")})
        return related

    def _messages(self, checkpoint: dict, retrieval: dict, entity: dict, related: dict) -> list[dict]:
        source_blocks = [
            {"chunk_id": r.get("chunk_id"), "text": r.get("text"),
             "match_reason": r.get("match_reason")}
            for r in (retrieval.get("retrieved") or [])
        ]
        instruction = (
            "あなたは学術論文の校閲者です。既存の抽出成果物(entity)を、取得した原文(source)と"
            "比較し、妥当性を判定してください。原文に根拠が無い場合は valid にしないこと。"
            "LLMの推測(analysis_notes)と原文由来の根拠(evidence_refs/source由来)を必ず分けること。"
            "出力は次のJSONのみ: {\"verdict\": one of [valid, incorrect, incomplete, "
            "unsupported, ambiguous], \"findings\": [{\"kind\":..,\"detail\":..}], "
            "\"evidence_refs\": [evidence_id|chunk_id ...], \"analysis_notes\": [..], "
            "\"confidence\": 0.0-1.0, \"requires_revision\": bool, \"review_required\": bool}"
        )
        payload = {
            "checkpoint": {
                "question": checkpoint.get("question"),
                "trigger_reason": checkpoint.get("trigger_reason"),
                "target_type": checkpoint.get("target_type"),
                "target_id": checkpoint.get("target_id"),
            },
            "entity": entity,
            "related": related,
            "source": source_blocks,
        }
        return [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def __call__(self, checkpoint: dict, retrieval: dict) -> dict:
        # No source retrieved → never auto-confirm a revision (#410 P1-6).
        if not (retrieval.get("retrieved") or []):
            return {
                "verdict": "ambiguous", "findings": [{"kind": "no_source_retrieved"}],
                "evidence_refs": [], "analysis_notes": ["原文を取得できなかったため判定保留"],
                "confidence": 0.0, "requires_revision": False, "review_required": True,
            }
        entity = self._entity(checkpoint)
        related = self._related(entity)
        raw = self._generate(self._messages(checkpoint, retrieval, entity, related))
        return _extract_json(raw)  # audit._coerce_llm_result enforces the safety rules


def build_default_audit_client(inventory: dict | None) -> LLMAuditClient | None:
    """Return a production LLM audit client when a real key is configured, else None."""
    if not llm_enabled():
        return None
    return LLMAuditClient(inventory)
