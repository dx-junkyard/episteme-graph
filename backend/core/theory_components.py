"""Source-limited extraction for Lecture Studio theory components."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

from core.llm import generate_text, get_llm_params

logger = logging.getLogger(__name__)

_EXTRACTION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="theory-extract")


def _submit_with_context(fn):
    """呼び出し元の contextvars（U層 usage_context / M層 model_override）を
    プールスレッドへ伝搬させて submit する。

    ThreadPoolExecutor はスレッドを再利用するため、素の submit では
    ①モデル選択・トークン帰属の文脈が消える ②前のリクエストの文脈が
    残留し得る。submit ごとに copy_context() のスナップショットで実行する
    （agents/llm_json_client.py の壁時計タイムアウトスレッドと同じ裁定）。
    """
    ctx = contextvars.copy_context()
    return _EXTRACTION_EXECUTOR.submit(ctx.run, fn)


_DEFAULT_TIMEOUT_SECONDS = 45
_MAX_SOURCE_CHARS = 6000
_MAX_CONTEXT_CHARS = 3000


_PROMPT = """あなたは学術教材（論文・講義資料）から、理論コンポーネント候補を抽出するアシスタントです。

重要な制約:
- 以下のソース本文に明示されている内容だけを使ってください。
- ソース本文の外側にある一般知識で補完しないでください。
- 明示的な根拠がない項目には needs_source: true を付けてください。
- 各 input/output/precondition/constraint/invalid_condition には、可能な限り source_refs を付けてください。
- quote はソース本文からの短い引用だけにしてください。
- JSONのみを出力してください。

ソース本文:
{source_text}

既存の構造情報:
smiles_dsl:
{smiles_dsl}

variables:
{variables}

ancestors:
{ancestors}

graph_elements:
{graph_elements}

出力JSON:
{{
  "components": [
    {{
      "name": "",
      "component_type": "theory | concept | law | mechanism | operator | observation",
      "summary": "",
      "inputs": [
        {{
          "label": "",
          "type": "Concept | Condition | Equation | Field | Symmetry | Observation",
          "required": true,
          "description": "",
          "needs_source": false,
          "source_refs": [
            {{"quote": ""}}
          ]
        }}
      ],
      "outputs": [],
      "preconditions": [],
      "constraints": [],
      "invalid_conditions": [],
      "dependencies": [],
      "blackbox_policy": {{
        "default_level": "summary",
        "expand_if_unlearned": true
      }}
    }}
  ]
}}
"""

_ENRICH_PROMPT = """あなたは学術教材の理論コンポーネント候補を補完するアシスタントです。

重要な制約:
- まずソース本文と既存DSLに明示されている内容を優先してください。
- ソース本文だけで不足する場合は、ソース本文が扱っている分野で一般的とされる知識で補ってください（分野は本文から判断し、書かれていない分野の話に広げないでください）。
- 一般知識で補った項目は needs_source: true とし、source_refs は空配列にしてください。
- 既存DSLの REQUIRES / CAUSES / DEFINES / EQUIVALENT などの関係は、ソース本文と同じく根拠として扱ってください。
- summary は教材・理論の意味を説明してください。「DSLから生成した候補」のような実装説明は禁止です。
- preconditions, constraints, invalid_conditions, dependencies は、一般知識で妥当に補える場合は必ず具体的に埋めてください。
- それでも推測にしかならない項目は label を「未確定」にして needs_source: true を付けてください。
- inputs と outputs は変更しないでください。
- quote はソース本文からの短い引用だけにしてください。
- JSONのみを出力してください。

ソース本文:
{source_text}

既存DSL:
{smiles_dsl}

variables:
{variables}

ancestors:
{ancestors}

既存候補:
{components}

出力JSON:
{{
  "components": [
    {{
      "name": "既存候補と同じ名前",
      "summary": "",
      "preconditions": [
        {{
          "label": "",
          "description": "",
          "needs_source": false,
          "source_refs": [
            {{"quote": ""}}
          ]
        }}
      ],
      "constraints": [],
      "invalid_conditions": [],
      "dependencies": []
    }}
  ]
}}
"""

_ENRICH_FIELDS = ("preconditions", "constraints", "invalid_conditions", "dependencies")

_NODE_RE = re.compile(r"\(([^:()\s]+):([^:()]+):([^)]+)\)")
_EDGE_RE = re.compile(
    r"\(([^:()\s]+):([^:()]+):([^)]+)\)\s*[=-]*\[\s*([^:\]]+)(?::([^\]]*))?\]\s*=*>\s*\(([^:()\s]+):([^:()]+):([^)]+)\)"
)


def _clean_label(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("\\)", ")").replace("\\(", "(")


def _item(label: str, source_ref: dict[str, Any], item_type: str = "Concept", description: str = "") -> dict[str, Any]:
    return {
        "label": label,
        "type": item_type,
        "required": True,
        "description": description,
        "needs_source": False,
        "source_refs": [source_ref],
    }


def _condition(label: str, source_ref: dict[str, Any], description: str = "") -> dict[str, Any]:
    return {
        "label": label,
        "description": description,
        "needs_source": False,
        "source_refs": [source_ref],
    }


def _needs_source_condition(label: str, description: str = "") -> dict[str, Any]:
    return {
        "label": label,
        "description": description,
        "needs_source": True,
        "source_refs": [],
    }


def _missing_condition(field: str) -> dict[str, Any]:
    return _needs_source_condition(
        "未確定",
        f"{field} は一般知識でも十分に確定できませんでした。教員による確認が必要です。",
    )


def _source_ref(chunk: dict[str, Any]) -> dict[str, Any]:
    quote = (chunk.get("raw_text") or chunk.get("display_text") or chunk.get("text") or "").strip()
    return {
        "chunk_id": str(chunk.get("id") or chunk.get("chunk_id") or ""),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "quote": quote[:240],
    }


def _variable_labels(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if isinstance(item, dict):
            label = _clean_label(item.get("name") or item.get("label") or item.get("id"))
        else:
            label = _clean_label(item)
        if label and label not in labels:
            labels.append(label)
    return labels


def _graph_concepts(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    concepts: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        if kind not in ("concept", "formula"):
            continue
        label = _clean_label(item.get("label") or item.get("id"))
        if not label:
            continue
        concepts.append({
            "label": label,
            "type": "Equation" if kind == "formula" else "Concept",
            "description": str(item.get("description") or ""),
        })
    return concepts


def _dsl_edges(smiles_dsl: str) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for match in _EDGE_RE.finditer(smiles_dsl or ""):
        relation = _clean_label(match.group(4)).upper()
        edges.append({
            "source_id": _clean_label(match.group(1)),
            "source_type": _clean_label(match.group(2)),
            "source": _clean_label(match.group(3)),
            "relation": relation,
            "target_id": _clean_label(match.group(6)),
            "target_type": _clean_label(match.group(7)),
            "target": _clean_label(match.group(8)),
        })
    return edges


def _dsl_nodes(smiles_dsl: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _NODE_RE.finditer(smiles_dsl or ""):
        label = _clean_label(match.group(3))
        if not label or label in seen:
            continue
        seen.add(label)
        nodes.append({
            "id": _clean_label(match.group(1)),
            "type": _clean_label(match.group(2)),
            "label": label,
        })
    return nodes


def _component_type_from_edges(edges: list[dict[str, str]]) -> str:
    relations = {edge.get("relation") for edge in edges}
    if "MEASURES" in relations:
        return "observation"
    if "DEFINES" in relations:
        return "concept"
    if "CAUSES" in relations or "REQUIRES" in relations:
        return "mechanism"
    return "theory"


def _ancestor_dependencies(ancestors: Any, source_ref: dict[str, Any]) -> list[dict[str, Any]]:
    labels: list[str] = []
    if isinstance(ancestors, dict):
        for values in ancestors.values():
            if isinstance(values, list):
                labels.extend(_clean_label(v) for v in values)
    elif isinstance(ancestors, list):
        for item in ancestors:
            if isinstance(item, dict):
                labels.append(_clean_label(item.get("source") or item.get("from") or item.get("parent")))
            else:
                labels.append(_clean_label(item))
    unique = []
    for label in labels:
        if label and label not in unique:
            unique.append(label)
    return [_condition(label, source_ref, "DSL ancestors 由来の依存候補") for label in unique[:8]]


def extract_theory_components_from_dsl(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Build lightweight component candidates from existing chunk DSL metadata.

    This is the structural path for the MVP: no LLM call. Inputs and outputs come from
    existing DSL metadata; optional LLM enrichment may fill narrative and condition fields.
    """
    source_ref = _source_ref(chunk)
    if not source_ref["chunk_id"]:
        return []

    dsl = str(chunk.get("smiles_dsl") or "")
    edges = _dsl_edges(dsl)
    nodes = _dsl_nodes(dsl)
    variable_labels = _variable_labels(chunk.get("variables"))
    graph_concepts = _graph_concepts(chunk.get("graph_elements"))

    if not edges and not nodes and not variable_labels and not graph_concepts:
        return []

    components: list[dict[str, Any]] = []
    for edge in edges[:6]:
        relation = edge.get("relation") or "RELATED_TO"
        source = edge.get("source") or edge.get("source_id")
        target = edge.get("target") or edge.get("target_id")
        if not source or not target:
            continue
        inputs = [_item(source, source_ref, edge.get("source_type") or "Concept", f"DSL relation {relation} のsource")]
        outputs = [_item(target, source_ref, edge.get("target_type") or "Concept", f"DSL relation {relation} のtarget")]
        preconditions = []
        dependencies = []
        constraints = []
        if relation == "REQUIRES":
            preconditions.append(_condition(source, source_ref, "DSL REQUIRES 由来の成立条件候補"))
        if relation in ("REQUIRES", "CONTAINS"):
            dependencies.append(_condition(source, source_ref, f"DSL {relation} 由来の依存候補"))
        if relation == "CAUSES":
            dependencies.append(_condition(source, source_ref, "DSL CAUSES 由来の依存候補"))
        if relation == "EQUIVALENT":
            constraints.append(_condition(f"{source} と {target} の同値関係", source_ref, "DSL EQUIVALENT 由来の制約候補"))
        components.append({
            "name": f"{source} → {target}",
            "component_type": _component_type_from_edges([edge]),
            "summary": f"{source} を入力または前提として、{target} を導く理論コンポーネント候補です。",
            "source_chunks": [source_ref],
            "inputs": inputs,
            "outputs": outputs,
            "preconditions": preconditions,
            "constraints": constraints,
            "invalid_conditions": [],
            "dependencies": dependencies,
            "blackbox_policy": {"default_level": "summary", "expand_if_unlearned": True},
        })

    if not components:
        labels = [node["label"] for node in nodes] or variable_labels or [c["label"] for c in graph_concepts]
        name = labels[0]
        outputs = [_item(label, source_ref, "Concept", "DSL/variables 由来の概念候補") for label in labels[1:4]]
        if not outputs and len(graph_concepts) > 1:
            outputs = [_item(c["label"], source_ref, c["type"], c.get("description", "")) for c in graph_concepts[1:4]]
        components.append({
            "name": name,
            "component_type": "concept",
            "summary": f"{name} を中心概念として扱う理論コンポーネント候補です。",
            "source_chunks": [source_ref],
            "inputs": [_item(name, source_ref, "Concept", "DSL/variables 由来の主要概念候補")],
            "outputs": outputs,
            "preconditions": [],
            "constraints": [],
            "invalid_conditions": [],
            "dependencies": _ancestor_dependencies(chunk.get("ancestors"), source_ref),
            "blackbox_policy": {"default_level": "summary", "expand_if_unlearned": True},
        })

    return components


def _component_enrichment_seed(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seed = []
    for component in components[:6]:
        seed.append({
            "name": component.get("name") or "",
            "component_type": component.get("component_type") or "theory",
            "inputs": [
                {"label": item.get("label"), "type": item.get("type")}
                for item in (component.get("inputs") or [])
                if isinstance(item, dict)
            ],
            "outputs": [
                {"label": item.get("label"), "type": item.get("type")}
                for item in (component.get("outputs") or [])
                if isinstance(item, dict)
            ],
        })
    return seed


def _component_with_missing_markers(component: dict[str, Any]) -> dict[str, Any]:
    updated = dict(component)
    for field in _ENRICH_FIELDS:
        if not updated.get(field):
            updated[field] = [_missing_condition(field)]
    warnings = list(updated.get("validation_warnings") or [])
    warnings.append({
        "field": "llm_enrichment",
        "message": "空欄の代わりに未確定・要出典項目を追加しました。承認前に根拠を確認してください。",
    })
    updated["validation_warnings"] = warnings
    return updated


def _condition_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = _clean_label(item.get("label"))
        if not label:
            continue
        refs = item.get("source_refs") if isinstance(item.get("source_refs"), list) else []
        cleaned.append({
            "label": label,
            "description": str(item.get("description") or ""),
            "needs_source": bool(item.get("needs_source")) or not refs,
            "source_refs": refs,
        })
    return cleaned


def enrich_theory_components_with_llm(
    chunk: dict[str, Any],
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use LLM only to enrich summary and condition fields for DSL-derived candidates.

    The structural fields inputs/outputs remain DSL-derived. If the provider fails or
    times out, missing enrichment fields are explicitly marked as needs_source.
    """
    if not components:
        return components
    source_text = (chunk.get("raw_text") or chunk.get("display_text") or chunk.get("text") or "").strip()
    if not source_text:
        return [_component_with_missing_markers(component) for component in components]

    # M層: model は渡さず（M1）、core/llm.py 入口の resolve_scene_model に委ねる。
    # feature は呼び出し元（routes/theory_components.py）が張る usage_context
    # （admin:component_extract）。ポリシー行も env も無い環境では
    # llm_policy._FEATURE_TIER_ONLY により従来と同じ fast tier に解決される。
    params = get_llm_params("fast")
    prompt = _ENRICH_PROMPT.format(
        source_text=_clip(source_text, _MAX_SOURCE_CHARS),
        smiles_dsl=_clip(str(chunk.get("smiles_dsl") or ""), _MAX_CONTEXT_CHARS),
        variables=_clip(_json_text(chunk.get("variables")), _MAX_CONTEXT_CHARS),
        ancestors=_clip(_json_text(chunk.get("ancestors")), _MAX_CONTEXT_CHARS),
        components=_clip(json.dumps(_component_enrichment_seed(components), ensure_ascii=False, indent=2), _MAX_CONTEXT_CHARS),
    )
    timeout_seconds = int(os.getenv("THEORY_EXTRACTION_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS)))

    def _call_llm() -> str:
        return generate_text(
            messages=[{"role": "user", "content": prompt}],
            reasoning_effort=params.get("reasoning_effort"),
            temperature=0.0,
            max_tokens=1600,
        )

    try:
        future = _submit_with_context(_call_llm)
        raw = future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        logger.warning("Theory component enrichment timed out after %ss", timeout_seconds)
        return [_component_with_missing_markers(component) for component in components]
    except Exception:
        logger.warning("Theory component enrichment failed", exc_info=True)
        return [_component_with_missing_markers(component) for component in components]

    parsed = _parse_json_object(raw)
    enriched_items = parsed.get("components")
    if not isinstance(enriched_items, list):
        return [_component_with_missing_markers(component) for component in components]

    enriched_by_name = {
        str(item.get("name") or "").strip(): item
        for item in enriched_items
        if isinstance(item, dict)
    }
    merged: list[dict[str, Any]] = []
    for idx, component in enumerate(components):
        enriched = enriched_by_name.get(str(component.get("name") or "").strip())
        if not enriched and idx < len(enriched_items) and isinstance(enriched_items[idx], dict):
            enriched = enriched_items[idx]
        if not enriched:
            merged.append(_component_with_missing_markers(component))
            continue
        updated = dict(component)
        if isinstance(enriched.get("summary"), str) and enriched["summary"].strip():
            updated["summary"] = enriched["summary"].strip()
        for field in _ENRICH_FIELDS:
            items = _condition_list(enriched.get(field))
            if items:
                updated[field] = items
        merged.append(_component_with_missing_markers(updated))
    return merged


def _json_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned, strict=False)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(), strict=False)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


def extract_theory_components_from_chunk(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract theory component candidates using only the selected chunk context.

    The caller is responsible for adding course_id, primary_chunk_id, and status before
    persistence. On provider errors or malformed JSON this returns an empty list so the
    existing editor flow remains usable.
    """
    source_text = (chunk.get("raw_text") or chunk.get("display_text") or chunk.get("text") or "").strip()
    if not source_text:
        return []

    params = get_llm_params("fast")
    prompt = _PROMPT.format(
        source_text=_clip(source_text, _MAX_SOURCE_CHARS),
        smiles_dsl=chunk.get("smiles_dsl") or "",
        variables=_clip(_json_text(chunk.get("variables")), _MAX_CONTEXT_CHARS),
        ancestors=_clip(_json_text(chunk.get("ancestors")), _MAX_CONTEXT_CHARS),
        graph_elements=_clip(_json_text(chunk.get("graph_elements")), _MAX_CONTEXT_CHARS),
    )

    timeout_seconds = int(os.getenv("THEORY_EXTRACTION_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS)))

    def _call_llm() -> str:
        return generate_text(
            messages=[{"role": "user", "content": prompt}],
            reasoning_effort=params.get("reasoning_effort"),
            temperature=0.0,
            max_tokens=1800,
        )

    try:
        future = _submit_with_context(_call_llm)
        raw = future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        logger.warning("Theory component extraction timed out after %ss", timeout_seconds)
        return []
    except Exception:
        logger.warning("Theory component extraction failed", exc_info=True)
        return []

    parsed = _parse_json_object(raw)
    components = parsed.get("components")
    if not isinstance(components, list):
        return []
    return [c for c in components if isinstance(c, dict) and str(c.get("name") or "").strip()]
