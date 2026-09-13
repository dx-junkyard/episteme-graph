"""知識ランドスケープ: 配置候補の生成と保存（設計書 §7 / §8）。

正本: ``docs/features/knowledge_landscape_design.md``。このモジュールは
``landscape_placement`` ステージの**実体**であり、教員の手動再提案
（``POST /api/admin/landscape/documents/{ref}/placements/propose``）とも
**同一コードパス・同一日次予算**を共有する（§7.1）。

流れ:

1. 凍結骨格を持つ **active** ドメインを列挙する（retired 除外・draft は読まない, LS6）
2. artifact（paper_skeleton / thesis_reconstruction / claim_object_builder）から
   **解決済みテキスト**の素材を組む（LS2: A層は読むだけ）
3. 日次コスト上限を CostGate で事前チェック（残数を見て、実行後に実測を計上する
   ``contextual_explanation`` / ``discuss_opening`` と同じ会計）
4. agent を 1 document = 1 コールで実行する（全ドメインを相対評価）
5. 結果を ``landscape_placements`` に ``status='inferred'`` で保存する
   （再解析セマンティクスは ``store.supersede_and_insert_candidates``。教員の
   confirmed / rejected は AI が上書きできない, LS3）
6. 同じ LLM コールに相乗りしたカテゴリギャップ信号を ``landscape_gap_signals`` へ
   保存する（``docs/features/category_gap_candidates_design.md`` §5.1 / §5.3。
   **配置と同一トランザクション**。追加ステージ・追加 LLM コールは無い）

このモジュールは **status を渡さない**（store 側の既定 ``inferred`` のみ）。
FastAPI は import しない（設計書 §8）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from core.llm_worker.cost_gate import CostGate, today_str

logger = logging.getLogger(__name__)

# パイプラインと手動再提案が共有する、プロセス寿命の日次コストゲート（設計書 §7.4）。
_landscape_cost_gate = CostGate()

DEFAULT_MAX_CALLS_PER_DAY = 20
DEFAULT_MAX_PLACEMENTS_PER_DOCUMENT = 8
# カテゴリギャップ候補の1 document 上限（category_gap_candidates_design.md §5.1）。
# 正本は ``core.config.Settings.landscape_gap_max_per_document``。
DEFAULT_MAX_GAPS_PER_DOCUMENT = 3

# ステージ skip 語彙（設計書 §7.2。``_stage_artifact_indicates_llm_skip`` 準拠）。
SKIPPED_REASON_NO_SKELETON = "no_frozen_skeleton"
SKIPPED_REASON_NO_MATERIAL = "no_source_material"
SKIPPED_REASON_DAILY_LIMIT = "daily_call_limit_reached"

# 素材の防波堤（設計書 §7.3: source_backed かつ atomic な claim 上位20件）。
MAX_CLAIM_SUMMARIES = 20

_SOURCE_BACKED = "source_backed"


# ---------------------------------------------------------------------------
# 設定（env 読み。max(0, int(...)) の try/except は既存ステージと同じ流儀）
# ---------------------------------------------------------------------------


def _max_calls_per_day() -> int:
    try:
        return max(
            0, int(os.getenv("LANDSCAPE_MAX_CALLS_PER_DAY", str(DEFAULT_MAX_CALLS_PER_DAY)))
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_CALLS_PER_DAY


def _max_placements_per_document() -> int:
    try:
        return max(
            0,
            int(
                os.getenv(
                    "LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT",
                    str(DEFAULT_MAX_PLACEMENTS_PER_DOCUMENT),
                )
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_PLACEMENTS_PER_DOCUMENT


def _max_gaps_per_document() -> int:
    """1 document あたりのカテゴリギャップ候補の上限（``core.config`` が正本）。

    設定を読めないときは既定値へ fail-open する（gap は配置の付随情報なので、
    設定不達で配置の生成まで止めない）。
    """
    try:
        from core.config import get_settings

        return max(
            0,
            int(
                getattr(
                    get_settings(),
                    "landscape_gap_max_per_document",
                    DEFAULT_MAX_GAPS_PER_DOCUMENT,
                )
                or 0
            ),
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to read landscape_gap_max_per_document", exc_info=True)
        return DEFAULT_MAX_GAPS_PER_DOCUMENT


def _vector_prefilter_topk() -> int:
    """配置の前段絞り込みの上位件数（``core.config`` が正本。0 = 無効）。

    正本: ``docs/features/atlas_vector_anchoring_design.md`` §6。設定を読めないときは
    **0（無効）へ倒す** — ベクトル層が使えないなら従来どおり骨格を全提示する（VA4）。
    """
    try:
        from core.config import get_settings

        return max(
            0, int(getattr(get_settings(), "landscape_vector_prefilter_topk", 0) or 0)
        )
    except Exception:  # noqa: BLE001
        logger.debug("failed to read landscape_vector_prefilter_topk", exc_info=True)
        return 0


def _apply_vector_prefilter(document_id: str, domains: list[dict]) -> tuple[list[dict], dict]:
    """骨格ノードを論文重心との近さで上位 top_k に絞る（VA層 §6）。

    論文重心は既存チャンク埋め込みの平均（``paper_discovery.ranking.document_centroid``）
    なので、**追加の embedding 呼び出しはゼロ**。判定そのものは純関数
    （``atlas_vectors.query.prefilter_domains``）が持ち、ここは DB からアンカーを
    読んで渡す配線だけを行う。

    どこで失敗しても**元の domains をそのまま返す**（VA4 fail-soft）。返す facts は
    間引き件数の正直な記録で、絞り込まなかった場合も ``applied: False`` を返す（VA7）。
    """
    facts: dict[str, Any] = {"applied": False, "omitted": 0, "domains": {}}
    top_k = _vector_prefilter_topk()
    if top_k <= 0 or not domains:
        return domains, facts

    session = None
    try:
        from core.atlas_vectors import store as vector_store
        from core.atlas_vectors.query import prefilter_domains
        from core.paper_discovery.ranking import document_centroid
        from core.postgres import get_session

        session = get_session()
        centroid = document_centroid(session, str(document_id or ""))
        if centroid is None:
            # チャンク埋め込みが無い document は測れない = 絞り込まない（慎重側）。
            return domains, facts
        anchors_by_domain = vector_store.anchors_for_domains(session, domains)
        if not anchors_by_domain:
            return domains, facts
        return prefilter_domains(centroid, domains, anchors_by_domain, top_k=top_k)
    except Exception:  # noqa: BLE001 — 絞り込めないだけで配置は従来どおり成立させる
        logger.warning(
            "landscape_placement: vector prefilter unavailable (non-fatal): document=%s",
            document_id, exc_info=True,
        )
        return domains, {"applied": False, "omitted": 0, "domains": {}}
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                logger.debug("landscape session close failed", exc_info=True)


def _landscape_model() -> str:
    """M層の正本（``core.llm_policy.resolve_scene_model``）でモデルを決める。

    ``LandscapePlacementAgent`` はモデル名を construction 引数で受けるため、
    パイプラインのランナーループが張る ``model_override`` の contextvar
    （解決順②）を素通ししてしまう。ここで **同じ正本関数**を呼んで解決してから
    渡すことで、run override → user/system ポリシー →
    ``LANDSCAPE_PLACEMENT_LLM_MODEL``（``llm_policy`` の ``_FEATURE_DIRECT_ENV``）
    → fast tier の順が効く（``_discuss_opening_model`` と同型）。
    """
    try:
        from core.llm_policy import SCENE_PIPELINE, resolve_scene_model

        return resolve_scene_model(f"{SCENE_PIPELINE}:landscape_placement").model
    except Exception:
        logger.debug("failed to resolve landscape_placement model", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# 汎用アクセサ（artifact は dict、in-run のパイプライン成果は dataclass で来る）
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _artifact(artifacts: Any, stage: str) -> Any:
    value = _get(artifacts, stage)
    return value if value is not None else {}


def _entry_text(container: Any, key: str) -> str:
    """``{"text": ..., "reason": ..., "confidence": ...}`` 形のエントリから本文だけ。

    素の文字列で来る形にも耐える（``core/discuss/authoring.py::_skeleton_entry_text``
    と同型。規則そのものは相互 import せず小さく持つ）。
    """
    entry = _get(container, key)
    if isinstance(entry, Mapping):
        return _clean(entry.get("text"))
    return _clean(entry)


# ---------------------------------------------------------------------------
# 骨格列挙（凍結版のみ・retired 除外, LS6 / LS7）
# ---------------------------------------------------------------------------


def collect_placement_domains() -> list[dict]:
    """配置先候補になるドメイン（凍結骨格を持つ active ドメイン）の一覧。

    返り値の各要素:
    ``{domain_key, domain_name, skeleton_version, nodes: [{node_id, label, kind,
    region_id}]}``。骨格の読みは必ず ``atlas_store.load_learner_skeleton``
    （``cartridge.learner_atlas_skeleton`` 直読み禁止）を通し、draft は読まない。
    DB 不達・骨格なしは空リスト（fail-closed で「配置しない」に縮退する）。
    """
    from core import atlas_store
    from core.postgres import get_session

    session = None
    try:
        session = get_session()
        retired = set(atlas_store.retired_domain_keys(session) or set())
        listed = list(atlas_store.list_domains(session) or [])
        out: list[dict] = []
        for entry in listed:
            domain_key = _clean(_get(entry, "domain_key"))
            if not domain_key:
                continue
            if domain_key in retired or _clean(_get(entry, "lifecycle")) == "retired":
                # retired ドメインは配置提案の候補から除外する（既存 propose と同じ方針）。
                continue
            skeleton = atlas_store.load_learner_skeleton(domain_key, session)
            if skeleton is None or not getattr(skeleton, "is_learner_visible", False):
                # 凍結・レビュー済みでない骨格には配置しない（LS6 の fail-closed）。
                continue
            nodes = _skeleton_nodes(skeleton)
            if not nodes:
                continue
            out.append(
                {
                    "domain_key": domain_key,
                    "domain_name": _clean(_get(entry, "domain_name")),
                    "skeleton_version": _clean(getattr(skeleton, "version", ""))
                    or _clean(_get(entry, "frozen_version")),
                    "nodes": nodes,
                }
            )
        return out
    except Exception:
        logger.warning(
            "landscape_placement: failed to enumerate frozen skeletons (non-fatal)",
            exc_info=True,
        )
        return []
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                logger.debug("landscape session close failed", exc_info=True)


def _skeleton_nodes(skeleton: Any) -> list[dict]:
    """凍結骨格 → 配置先ノード一覧（region が先、その配下 concept が続く）。"""
    nodes: list[dict] = []
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = _clean(getattr(region, "id", ""))
        if not region_id:
            continue
        nodes.append(
            {
                "node_id": region_id,
                "label": _clean(getattr(region, "label", "")) or region_id,
                "kind": "region",
                "region_id": "",
            }
        )
        for concept in getattr(region, "concepts", ()) or ():
            concept_id = _clean(getattr(concept, "id", ""))
            if not concept_id:
                continue
            nodes.append(
                {
                    "node_id": concept_id,
                    "label": _clean(getattr(concept, "label", "")) or concept_id,
                    "kind": "concept",
                    "region_id": region_id,
                }
            )
    return nodes


# ---------------------------------------------------------------------------
# 素材（artifact から解決済みテキストへ, LS2）
# ---------------------------------------------------------------------------


def build_placement_input(
    *,
    document_id: str,
    cartridge_id: str | None,
    artifacts: Any,
    domains: list[dict],
    max_placements: int,
    max_gaps: int = DEFAULT_MAX_GAPS_PER_DOCUMENT,
) -> dict:
    """agent 入力（``LandscapePlacementInput.from_dict`` が読める dict）を組む。

    不透明 ID は渡さない（設計書 §7.3）。``paper_goal`` / ``central_question`` /
    ``headline_claim`` は ``paper_skeleton`` artifact が正本で、``central_thesis`` は
    ``thesis_reconstruction`` artifact（``central_thesis.text``）。``central_question``
    は thesis 側にもあるため thesis 優先で skeleton へ縮退する（discuss 開幕の投影と
    同じ縮退規則）。
    """
    skeleton = _artifact(artifacts, "paper_skeleton")
    thesis = _artifact(artifacts, "thesis_reconstruction")
    structure = _artifact(artifacts, "document_structure")

    central = _get(thesis, "central_thesis") or {}
    central_question = _clean(_get(thesis, "central_question")) or _entry_text(
        skeleton, "central_question"
    )
    headline_claim = _entry_text(skeleton, "headline_claim") or _clean(
        _get(thesis, "headline_claim")
    )

    return {
        "document_id": str(document_id or ""),
        "cartridge_id": cartridge_id,
        "paper_title": _clean(_get(_get(structure, "metadata") or {}, "title")),
        "central_question": central_question,
        "central_thesis": _clean(_get(central, "text")),
        "paper_goal": _entry_text(skeleton, "paper_goal"),
        "headline_claim": headline_claim,
        "claim_summaries": collect_claim_summaries(artifacts),
        "domains": domains,
        "max_placements": max_placements,
        # カテゴリギャップ候補の上限（0 なら申告させない）。placements 最優先・
        # gap は最後・任意という契約は agent 側プロンプトが持つ（§5.1）。
        "max_gaps_per_document": max(0, int(max_gaps or 0)),
    }


def collect_claim_summaries(artifacts: Any, limit: int = MAX_CLAIM_SUMMARIES) -> list[dict]:
    """``claim_object_builder`` artifact → ``[{claim_id, text}]``（上位 limit 件）。

    対象は ``support_status == 'source_backed'`` かつ ``is_atomic``（設計書 §7.3）。
    非 atomic / split_pending の claim を配置の根拠にしない（A層の原則と同じ）。
    """
    result = _artifact(artifacts, "claim_object_builder")
    raw_claims = _get(result, "claims") or []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in raw_claims:
        claim_id = _clean(_get(raw, "claim_id"))
        if not claim_id or claim_id in seen:
            continue
        if _clean(_get(raw, "support_status")) != _SOURCE_BACKED:
            continue
        if not bool(_get(raw, "is_atomic")):
            continue
        text = _clean(_get(raw, "normalized_text")) or _clean(_get(raw, "text"))
        if not text:
            continue
        seen.add(claim_id)
        out.append({"claim_id": claim_id, "text": text})
        if len(out) >= max(0, int(limit or 0)):
            break
    return out


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def build_and_store_placements(
    document_id: str,
    *,
    artifacts: Any = None,
    run_id: str | None = None,
    created_by: str = "pipeline",
) -> dict:
    """配置候補を生成して ``landscape_placements`` へ保存する（§7 / §8）。

    戻り値はステージ payload（§7.2 の skip 語彙を含む）:
    ``{status, created, superseded, skipped_existing, placements_total,
    unplaced_domains, llm_calls, [truncated], [skipped_reason],
    [skipped_by_limit], [review_notes]}``。

    生成できなかった場合も **status='completed'** を返す（非致命ステージ）。理由は
    ``skipped_reason`` に正直に残す（P4）。
    """
    max_placements = _max_placements_per_document()
    max_gaps = _max_gaps_per_document()

    payload: dict[str, Any] = {
        "status": "completed",
        "created": 0,
        "superseded": 0,
        "skipped_existing": 0,
        "placements_total": 0,
        "unplaced_domains": [],
        "llm_calls": 0,
        "created_by": created_by,
        "max_placements": max_placements,
    }

    domains = collect_placement_domains()
    payload["domains_checked"] = [d["domain_key"] for d in domains]
    if not domains:
        payload["skipped_reason"] = SKIPPED_REASON_NO_SKELETON
        logger.info(
            "landscape_placement: no active domain with a frozen skeleton; "
            "skipping generation for document=%s",
            document_id,
        )
        return payload

    # 配置先の前段絞り込み（VA層 §6）。間引いた件数は**必ず**記録する（VA7 —
    # 絞り込まなかった場合も applied:false を残し、silent truncation を作らない）。
    domains, prefilter_facts = _apply_vector_prefilter(document_id, domains)
    payload["vector_prefilter"] = prefilter_facts

    if artifacts is None:
        try:
            from core.deliberation.refs import document_run_artifacts

            artifacts = document_run_artifacts(str(document_id or ""))
        except Exception:
            logger.warning(
                "landscape_placement: failed to load run artifacts (non-fatal): "
                "document=%s",
                document_id, exc_info=True,
            )
            artifacts = {}

    cartridge_id = _resolve_cartridge_id(artifacts)
    agent_input = build_placement_input(
        document_id=str(document_id or ""),
        cartridge_id=cartridge_id,
        artifacts=artifacts,
        domains=domains,
        max_placements=max_placements or DEFAULT_MAX_PLACEMENTS_PER_DOCUMENT,
        max_gaps=max_gaps,
    )
    payload["claim_count"] = len(agent_input["claim_summaries"])

    if max_placements <= 0:
        payload["skipped_reason"] = "placement_limit_is_zero"
        return payload

    if not _input_has_material(agent_input):
        payload["skipped_reason"] = SKIPPED_REASON_NO_MATERIAL
        logger.info(
            "landscape_placement: no thesis / goal / claim material for document=%s; "
            "skipping generation",
            document_id,
        )
        return payload

    daily_limit = _max_calls_per_day()
    daily_key = today_str()
    remaining = _landscape_cost_gate.daily_remaining(
        daily_limit=daily_limit, daily_key=daily_key
    )
    if remaining <= 0:
        payload["skipped_by_limit"] = True
        payload["skipped_reason"] = SKIPPED_REASON_DAILY_LIMIT
        logger.info(
            "landscape_placement: daily call limit reached (limit=%d), skipping "
            "generation for document=%s",
            daily_limit, document_id,
        )
        return payload

    from episteme_graph.agents.landscape_placement.agent import LandscapePlacementAgent

    agent = LandscapePlacementAgent(llm_model=_landscape_model() or None)
    result = agent.run(agent_input, cartridge_id=cartridge_id)
    # 実測の事後計上（contextual_explanation / discuss_opening と同じ会計）。
    _landscape_cost_gate.count_extra_daily(
        daily_key=daily_key, amount=int(getattr(result, "llm_call_count", 0) or 0)
    )

    payload["llm_calls"] = int(getattr(result, "llm_call_count", 0) or 0)
    payload["placements_total"] = len(result.placements)
    payload["unplaced_domains"] = [
        {"domain_key": u.domain_key, "reason": u.reason} for u in result.unplaced_domains
    ]
    if result.truncated:
        payload["truncated"] = True
        payload["truncated_count"] = int(result.truncated_count or 0)
    if result.skipped_reason:
        payload["skipped_reason"] = result.skipped_reason
    if result.review_notes:
        payload["review_notes"] = list(result.review_notes)

    # カテゴリギャップ信号（設計書 §5.1）。並行実装中でも壊れないよう防御アクセスで
    # 読む。配置とは独立で、**配置が空でも保存する**（「置けなかった」ことこそ信号）。
    gaps = list(getattr(result, "category_gaps", None) or [])
    payload["category_gaps_total"] = len(gaps)

    candidates = (
        _to_store_candidates(result, domains, created_by=created_by)
        if result.placements
        else []
    )
    if not candidates and not gaps:
        return payload

    payload.update(
        _persist(
            document_id,
            run_id,
            candidates,
            gaps=gaps,
            skeleton_versions={
                d["domain_key"]: d.get("skeleton_version", "") for d in domains
            },
            max_gaps=max_gaps,
        )
    )
    return payload


def _resolve_cartridge_id(artifacts: Any) -> str | None:
    """artifact に記録されている cartridge_id（無ければ None で単独動作）。"""
    for stage in ("paper_skeleton", "thesis_reconstruction", "component_assembly"):
        value = _clean(_get(_artifact(artifacts, stage), "cartridge_id"))
        if value:
            return value
    return None


def _input_has_material(agent_input: Mapping[str, Any]) -> bool:
    """agent 側 ``has_material()`` と同じ判定（LLM を呼ぶ前に builder 側で縮退する）。"""
    if any(
        _clean(agent_input.get(key))
        for key in ("central_thesis", "paper_goal", "central_question", "headline_claim")
    ):
        return True
    return any(_clean(c.get("text")) for c in agent_input.get("claim_summaries") or [])


def _to_store_candidates(
    result: Any,
    domains: list[dict],
    *,
    created_by: str,
) -> list[dict]:
    """agent の配置候補 → ``store.supersede_and_insert_candidates`` の入力行。

    ``status`` は**渡さない**（store 既定の ``inferred`` のみ。LS3: AI は confirmed を
    書けない）。``skeleton_version`` はドメイン単位、``node_kind`` は骨格ノード索引から
    決定論的に付ける（LLM 出力からは取らない）。
    """
    version_by_domain = {d["domain_key"]: d.get("skeleton_version", "") for d in domains}
    kind_by_node = {
        (d["domain_key"], node["node_id"]): node.get("kind", "region")
        for d in domains
        for node in d.get("nodes") or []
    }

    candidates: list[dict] = []
    for placement in result.placements:
        evidence: list[dict] = []
        if placement.evidence_quote:
            evidence.append(
                {
                    "quote": placement.evidence_quote,
                    "claim_id": placement.claim_id or None,
                }
            )
        candidates.append(
            {
                "domain_key": placement.domain_key,
                "skeleton_version": version_by_domain.get(placement.domain_key, ""),
                "node_id": placement.node_id,
                "node_kind": kind_by_node.get(
                    (placement.domain_key, placement.node_id), "region"
                ),
                "perspective": placement.perspective,
                "weight": float(placement.weight),
                "reason": placement.reason,
                "evidence": evidence,
                "provenance": "llm",
                "created_by": created_by,
            }
        )
    return candidates


def _persist_gaps(
    session: Any,
    document_id: str,
    run_id: str | None,
    gaps: list[Any],
    *,
    skeleton_versions: Mapping[str, str] | None = None,
    max_gaps: int = DEFAULT_MAX_GAPS_PER_DOCUMENT,
) -> dict:
    """カテゴリギャップ信号を保存し、検出を監査へ記帳する（呼び出し側の tx に同乗）。

    正本: ``docs/features/category_gap_candidates_design.md`` §5.2 / §5.7。
    再解析セマンティクス（active のみ supersede）と上限は
    ``core/atlas_gaps/store.py::record_signals`` が持つ。ここは配線だけを行う。

    監査は **1 run 1 event**（``entity_type='category_gap'`` /
    ``metadata.action='detect'``）。1件も保存されなかった run では記帳しない
    （何も起きていない run の空記録を積まない）。
    """
    from core.atlas_gaps.store import record_detect_audit, record_signals

    created = record_signals(
        session,
        document_id=str(document_id or ""),
        run_id=run_id,
        gaps=gaps,
        skeleton_versions=skeleton_versions or {},
        max_signals=max_gaps,
    )
    if created:
        record_detect_audit(
            session,
            document_id=str(document_id or ""),
            run_id=run_id,
            created=created,
            domain_keys=[_clean(_get(g, "domain_key")) for g in gaps],
        )
    return {"gap_signals_created": int(created)}


def _persist(
    document_id: str,
    run_id: str | None,
    candidates: list[dict],
    *,
    gaps: list[Any] | None = None,
    skeleton_versions: Mapping[str, str] | None = None,
    max_gaps: int = DEFAULT_MAX_GAPS_PER_DOCUMENT,
) -> dict:
    """1トランザクションで supersede + insert（失敗は非致命・生成記録は残す）。

    カテゴリギャップ信号（``landscape_gap_signals``）も**同じトランザクション**で
    保存する（設計書 §3-3: 保存は ``_persist`` と同一トランザクション）。gap の保存に
    失敗した場合は配置の保存も巻き戻る（部分適用で「配置だけ新しい」状態を作らない）。
    """
    from core.postgres import get_session

    session = get_session()
    try:
        from core.landscape.store import supersede_and_insert_candidates

        stats = supersede_and_insert_candidates(
            session, document_id, run_id, candidates
        )
        out = {
            "created": int((stats or {}).get("created") or 0),
            "superseded": int((stats or {}).get("superseded") or 0),
            "skipped_existing": int((stats or {}).get("skipped_existing") or 0),
        }
        if gaps:
            out.update(
                _persist_gaps(
                    session,
                    document_id,
                    run_id,
                    gaps,
                    skeleton_versions=skeleton_versions,
                    max_gaps=max_gaps,
                )
            )
        session.commit()
        return out
    except Exception:
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("landscape rollback failed", exc_info=True)
        logger.warning(
            "landscape_placement: failed to persist placement candidates "
            "(non-fatal): document=%s",
            document_id, exc_info=True,
        )
        return {"persist_failed": True}
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            logger.debug("landscape session close failed", exc_info=True)
