"""分野の地図 — 骨格のLLMバッチ生成 (Issue A-2)。

カートリッジ単位で一度だけ実行するバッチ。出力は `status: draft` の骨格で、
`generated_by: "model:<id> batch:<id>"` を必ず記録する。

- draft はいかなる画面にも学習者向け表示しない (core.atlas.learner_view が担保)
- リアルタイムの LLM 生成は行わない。本モジュールは明示的なバッチ操作
  (管理APIの生成エンドポイント / scripts.generate_atlas_skeleton) からのみ呼ぶ
- LLM 出力は後処理で上限 (領域 ≤ 7・領域内代表概念 ≤ 6, §13) と座標範囲に収める
- LLM が seed_status を提案しても `reviewed` は必ず False に留める
  (maturity・review情報の最終確定禁止 — 確定は教員レビュー A-3 のみ)
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field

from core import atlas
from core.cartridges import DomainCartridge, load_cartridge
from core.config import get_settings
from core.llm_usage.context import usage_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM structured output モデル
# ---------------------------------------------------------------------------


class GeneratedConcept(BaseModel):
    id: str = Field(description="安定したスラッグID (小文字英数と_)。版を跨いで不変になる前提で付ける")
    label: str = Field(description="学習者向けの短い概念名 (日本語)")
    x: float = Field(description="領域内の正規化x座標 (0.0-1.0)")
    y: float = Field(description="領域内の正規化y座標 (0.0-1.0)")
    seed_status: str | None = Field(
        default=None,
        description="初期状態ヒント。verified / contested / assumed のいずれか。不明なら null",
    )


class GeneratedRegion(BaseModel):
    id: str = Field(description="安定したスラッグID (小文字英数と_)")
    label: str = Field(description="領域名 (日本語)")
    x: float = Field(description="キャンバス上の正規化x座標 (0.0-1.0)")
    y: float = Field(description="キャンバス上の正規化y座標 (0.0-1.0)")
    w: float = Field(description="正規化幅 (0.0-1.0)")
    h: float = Field(description="正規化高さ (0.0-1.0)")
    concepts: list[GeneratedConcept] = Field(
        default_factory=list, description="領域の代表概念 (最大6件)"
    )


class GeneratedEdge(BaseModel):
    from_id: str = Field(description="始点の領域または概念のID")
    to_id: str = Field(description="終点の領域または概念のID")
    kind: str = Field(default="adjacent", description="adjacent / depends / related")


class GeneratedSkeleton(BaseModel):
    regions: list[GeneratedRegion] = Field(description="分野の主要領域 (最大7件)")
    edges: list[GeneratedEdge] = Field(default_factory=list, description="領域間の関係")


# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------


def build_generation_prompt(cartridge: DomainCartridge) -> str:
    concept_types = ", ".join(
        str(c.get("label") or c.get("id") or "") for c in cartridge.ontology.concept_types[:20]
    )
    return build_generation_prompt_from_meta(
        name=cartridge.name,
        target_domain=list(cartridge.target_domain),
        description=cartridge.description,
        concept_vocabulary=concept_types,
    )


def build_generation_prompt_from_meta(
    *,
    name: str,
    target_domain: list[str] | tuple[str, ...] = (),
    description: str = "",
    concept_vocabulary: str = "",
) -> str:
    """分野メタデータから生成プロンプトを組み立てる。

    migration 027 (骨格の DB 管理化) で、カートリッジファイルを持たない新分野でも
    骨格を生成できるよう、プロンプト構築をカートリッジ非依存に分離した。
    """
    concept_types = concept_vocabulary or "(指定なし — 分野の標準的な語彙を使うこと)"
    domains = ", ".join(target_domain) if target_domain else name
    return (
        "あなたは大学院教育のカリキュラム設計者です。"
        "以下の分野について、学習者に見せる「分野の地図」の骨格 (領域と代表概念の配置) を設計してください。\n\n"
        f"分野: {name}\n"
        f"対象ドメイン: {domains}\n"
        f"説明: {description}\n"
        f"分野の概念タイプ語彙: {concept_types}\n\n"
        "制約:\n"
        f"- 領域 (regions) は最大 {atlas.MAX_REGIONS} 件。分野を俯瞰する主要領域に絞る\n"
        f"- 各領域の代表概念 (concepts) は最大 {atlas.MAX_CONCEPTS_PER_REGION} 件\n"
        "- id は英小文字・数字・アンダースコアのスラッグ。改版を跨いで安定に使える普遍的な名前にする\n"
        "- layout は正規化座標 (0.0〜1.0)。領域同士は重ならないように配置する\n"
        "- 概念の x/y は領域内の相対位置 (0.0〜1.0)\n"
        "- seed_status は確信がある場合のみ: verified (実験で確認) / contested (解釈が分かれる) / "
        "assumed (暗黙の前提)。不明なら null\n"
        "- edges は領域間の隣接・依存関係。from_id / to_id には領域IDを使う\n"
        "- 推薦文言・評価語は含めない。地図は事実の投影であり誘導ではない\n"
    )


# ---------------------------------------------------------------------------
# 後処理 (上限・座標・語彙を非LLMで整える)
# ---------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9_]+")


def _slugify(value: str, fallback: str) -> str:
    slug = _SLUG_STRIP.sub("_", str(value).strip().lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"{fallback}_{slug}" if slug else fallback
    return slug


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _unique_id(slug: str, seen: set[str]) -> str:
    candidate = slug
    suffix = 2
    while candidate in seen:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def normalize_generated(
    generated: GeneratedSkeleton,
    *,
    cartridge_id: str,
    generated_by: str,
) -> atlas.AtlasSkeleton:
    """LLM出力を骨格スキーマに正規化する。上限超過は切り詰め、座標はクランプする。"""
    seen_ids: set[str] = set()
    regions: list[atlas.SkeletonRegion] = []

    dropped_regions = len(generated.regions) - atlas.MAX_REGIONS
    if dropped_regions > 0:
        logger.warning(
            "atlas generation: %d region(s) over the limit were dropped (cartridge=%s)",
            dropped_regions,
            cartridge_id,
        )

    for i, region in enumerate(generated.regions[: atlas.MAX_REGIONS]):
        region_id = _unique_id(_slugify(region.id, f"region{i + 1}"), seen_ids)
        dropped_concepts = len(region.concepts) - atlas.MAX_CONCEPTS_PER_REGION
        if dropped_concepts > 0:
            logger.warning(
                "atlas generation: %d concept(s) over the limit were dropped (region=%s)",
                dropped_concepts,
                region_id,
            )
        concepts: list[atlas.SkeletonConcept] = []
        for j, concept in enumerate(region.concepts[: atlas.MAX_CONCEPTS_PER_REGION]):
            concept_id = _unique_id(_slugify(concept.id, f"{region_id}_c{j + 1}"), seen_ids)
            seed = None
            if concept.seed_status in atlas.SEED_STATUS_VALUES:
                # LLM 提案は provisional。reviewed は教員レビュー (A-3) まで必ず False
                seed = atlas.SeedStatus(value=concept.seed_status, reviewed=False)
            concepts.append(
                atlas.SkeletonConcept(
                    id=concept_id,
                    label=concept.label.strip() or concept_id,
                    layout=atlas.ConceptLayout(
                        x=_clamp(concept.x, 0.0, 1.0), y=_clamp(concept.y, 0.0, 1.0)
                    ),
                    seed_status=seed,
                )
            )
        x = _clamp(region.x, 0.0, 1.0)
        y = _clamp(region.y, 0.0, 1.0)
        w = _clamp(region.w, 0.01, 1.0 - x) if x < 1.0 else 0.01
        h = _clamp(region.h, 0.01, 1.0 - y) if y < 1.0 else 0.01
        regions.append(
            atlas.SkeletonRegion(
                id=region_id,
                label=region.label.strip() or region_id,
                layout=atlas.RegionLayout(x=x, y=y, w=w, h=h),
                concepts=tuple(concepts),
            )
        )

    edges: list[atlas.SkeletonEdge] = []
    for edge in generated.edges:
        from_id = _slugify(edge.from_id, "")
        to_id = _slugify(edge.to_id, "")
        if from_id not in seen_ids or to_id not in seen_ids:
            logger.warning(
                "atlas generation: edge references unknown id and was dropped: %s -> %s",
                edge.from_id,
                edge.to_id,
            )
            continue
        kind = edge.kind if edge.kind in atlas.EDGE_KINDS else "adjacent"
        edges.append(atlas.SkeletonEdge(from_id=from_id, to_id=to_id, kind=kind))

    skeleton = atlas.AtlasSkeleton(
        cartridge=cartridge_id,
        status=atlas.STATUS_DRAFT,
        version="",
        generated_by=generated_by,
        reviewed_by=(),
        changelog=(),
        regions=tuple(regions),
        edges=tuple(edges),
        concept_bindings=(),
    )
    report = atlas.validate_skeleton(skeleton)
    if not report.ok:
        raise ValueError(
            "生成骨格が後処理後もスキーマに適合しません: " + "; ".join(report.errors)
        )
    return skeleton


# ---------------------------------------------------------------------------
# バッチ生成 (一度だけ実行。再実行は明示操作)
# ---------------------------------------------------------------------------


def generate_skeleton_draft(
    cartridge_id: str,
    *,
    batch_id: str | None = None,
    model: str | None = None,
    domain_meta: dict | None = None,
) -> atlas.AtlasSkeleton:
    """カートリッジのメタデータ + モデル知識から draft 骨格を生成する。

    domain_meta (migration 027): カートリッジファイルを持たない新分野向けに
    {name, description, target_domain, concept_vocabulary} をリクエストから渡す。
    指定があればカートリッジは読まない (骨格の DB 管理化に伴いファイル非依存)。
    """
    # 遅延インポート: core.atlas / cartridges の単体テストを LLM SDK なしで動かすため
    from core.llm import generate_text_with_structured_output

    if domain_meta:
        prompt = build_generation_prompt_from_meta(
            name=str(domain_meta.get("name") or cartridge_id),
            target_domain=[str(d) for d in (domain_meta.get("target_domain") or []) if d],
            description=str(domain_meta.get("description") or ""),
            concept_vocabulary=str(domain_meta.get("concept_vocabulary") or ""),
        )
    else:
        cartridge = load_cartridge(cartridge_id)
        prompt = build_generation_prompt(cartridge)

    settings = get_settings()
    model_name = model or settings.llm_analysis_model
    batch = batch_id or uuid.uuid4().hex[:12]

    with usage_context("admin:atlas_skeleton"):
        generated = generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=GeneratedSkeleton,
            model=model_name,
        )
    return normalize_generated(
        generated,
        cartridge_id=cartridge_id,
        generated_by=f"model:{model_name} batch:{batch}",
    )


# ---------------------------------------------------------------------------
# AIアシスト編集 (skeleton editor upgrade P3/P4)
#
# 全体再生成 (generate_skeleton_draft) とは別に、draft の一部分を対話で修正する。
# 責務を「対象特定 (interpret)」と「編集内容生成 (propose)」の 2 関数に分離し、
# prompt を混ぜない (§4.3)。どちらも draft を書き換えない — propose は JSON Patch
# 案を返すだけで、確定は教員が admin UI の「適用」で PUT を経由して行う。
# ---------------------------------------------------------------------------


class InterpretedTarget(BaseModel):
    kind: str = Field(
        description="対象種別: region / concept / edge / region_set / unresolved"
    )
    region_id: str | None = Field(default=None, description="対象領域ID (kind=region)")
    concept_id: str | None = Field(default=None, description="対象概念ID (kind=concept)")
    edge_from: str | None = Field(default=None, description="対象エッジの始点ID (kind=edge)")
    edge_to: str | None = Field(default=None, description="対象エッジの終点ID (kind=edge)")
    region_ids: list[str] = Field(
        default_factory=list, description="対象領域IDの集合 (kind=region_set)"
    )
    label_snapshot: str | None = Field(
        default=None, description="特定時点での対象ラベル (教員確認用)"
    )


class SkeletonInterpretation(BaseModel):
    is_continuation: bool = Field(description="直前のやりとりの続きと判断したか")
    continuation_of: str | None = Field(
        default=None, description="history 中のどの発言を継続対象と見たか (turn id)"
    )
    target: InterpretedTarget = Field(description="今回の発言が指す対象")
    requested_change: str = Field(description="要望の言い直し (教員に見せる人間可読な要約)")
    ambiguous: bool = Field(description="対象・要望が曖昧で確認が必要か")
    clarifying_question: str | None = Field(
        default=None, description="ambiguous=true のとき教員に返す問い返し"
    )
    confidence: float = Field(description="解釈の確信度 0.0-1.0")


class PatchOp(BaseModel):
    op: str = Field(description="JSON Patch 操作: replace / add / remove")
    path: str = Field(
        description="RFC 6901 JSON Pointer。atlas_skeleton 直下からのパス "
        "(例: /regions/0/concepts/2/label)。配列末尾追加は /-"
    )
    value_json: str | None = Field(
        default=None,
        description="replace / add の新しい値を JSON 文字列で。ラベルなら \"\\\"新ラベル\\\"\"、"
        "概念オブジェクトなら {\"id\":...} を JSON エンコードした文字列",
    )
    before: str | None = Field(default=None, description="変更前の人間可読な値 (diff 表示用)")
    after: str | None = Field(default=None, description="変更後の人間可読な値 (diff 表示用)")


class SkeletonEditProposal(BaseModel):
    summary: str = Field(description="この編集案の一言サマリ (教員向け)")
    patch: list[PatchOp] = Field(default_factory=list, description="JSON Patch 操作の並び")


def _skeleton_outline(skeleton: dict) -> str:
    """LLM に渡す骨格の要約 (id とラベルのみ。座標や来歴は出さない)。"""
    lines: list[str] = []
    for region in skeleton.get("regions") or []:
        lines.append(f"- region [{region.get('id')}] \"{region.get('label')}\"")
        for concept in region.get("concepts") or []:
            seed = (concept.get("seed_status") or {}).get("value") or "-"
            lines.append(
                f"    - concept [{concept.get('id')}] \"{concept.get('label')}\" (seed={seed})"
            )
    edges = skeleton.get("edges") or []
    if edges:
        lines.append("edges:")
        for e in edges:
            lines.append(f"    - {e.get('from')} --{e.get('kind')}--> {e.get('to')}")
    return "\n".join(lines)


def _assist_model(model: str | None) -> str:
    settings = get_settings()
    return model or settings.atlas_assist_llm_model or settings.llm_analysis_model


def interpret_skeleton_instruction(
    skeleton: dict,
    message: str,
    history: list[dict] | None = None,
    selection_hint: dict | None = None,
    *,
    model: str | None = None,
) -> SkeletonInterpretation:
    """教員の自由文を「対象・要望・継続性」に解釈する (まだ編集しない。§4.2)。

    - skeleton: atlas_skeleton 相当の dict (regions/edges/...)
    - history: [{role: teacher|agent, content, resolved_target?}] のチャット履歴
    - selection_hint: 直前にクリックしたノード {region_id?, concept_id?} — ヒントであり強制ではない
    """
    from core.llm import generate_text_with_structured_output

    outline = _skeleton_outline(skeleton)
    history_lines: list[str] = []
    for i, turn in enumerate(history or []):
        role = "教員" if str(turn.get("role")) == "teacher" else "AI"
        rt = turn.get("resolved_target")
        rt_str = f" (対象: {json.dumps(rt, ensure_ascii=False)})" if rt else ""
        history_lines.append(f"[turn_{i}] {role}: {turn.get('content')}{rt_str}")
    history_block = "\n".join(history_lines) or "(履歴なし)"
    hint_block = (
        json.dumps(selection_hint, ensure_ascii=False)
        if selection_hint
        else "(なし)"
    )

    prompt = (
        "あなたは分野の地図の骨格を教員と一緒に編集するアシスタントです。"
        "教員の発言を実行に移す前に、まず『何を・どう変えたいか』を解釈して確認します。\n"
        "この段階では骨格を一切変更しません。対象の特定と要望の言い直しだけを行ってください。\n\n"
        f"# 現在の骨格 (id とラベル)\n{outline}\n\n"
        f"# これまでの会話\n{history_block}\n\n"
        f"# 直前にクリックしたノード (ヒント。強制ではない)\n{hint_block}\n\n"
        f"# 教員の今回の発言\n{message}\n\n"
        "# 指示\n"
        "- 発言が指す対象 (region / concept / edge / 複数領域=region_set) を、上記の id から特定する。"
        "id が特定できないときは kind=unresolved とする。\n"
        "- 指示語 (『さっきの』『そっちの』『同様に』等) や継続の言及があれば is_continuation=true とし、"
        "history 中の該当 turn を continuation_of に入れる。新しい話題なら is_continuation=false。\n"
        "- クリックヒントと発言内容が矛盾する場合、勝手に決めず ambiguous=true にして clarifying_question で問い返す。\n"
        "- requested_change には要望を教員が確認できる短い日本語で言い直す (まだ実行はしない)。\n"
        "- 断定を避ける。少しでも対象が曖昧なら ambiguous=true とし clarifying_question を必ず書く。\n"
        "- label_snapshot には特定した対象の現在のラベルを入れる。"
    )
    with usage_context("admin:atlas_assist"):
        return generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=SkeletonInterpretation,
            model=_assist_model(model),
        )


def propose_skeleton_edit(
    skeleton: dict,
    interpretation: dict,
    *,
    model: str | None = None,
) -> SkeletonEditProposal:
    """確定済みの解釈から実際の編集案 (JSON Patch) を生成する (§4.3)。

    interpretation は §4.2 で教員が確認・確定した SkeletonInterpretation の dict。
    ここでは再解釈せず、確定した対象・要望に沿って最小の patch を作る。
    """
    from core.llm import generate_text_with_structured_output

    outline = _skeleton_outline(skeleton)
    interp_block = json.dumps(interpretation, ensure_ascii=False, indent=2)
    prompt = (
        "あなたは分野の地図の骨格を編集するアシスタントです。"
        "教員が確認・確定した解釈に沿って、骨格 (atlas_skeleton) への最小の編集案を "
        "JSON Patch (RFC 6902) で作ってください。\n\n"
        f"# 現在の骨格 (id とラベル)\n{outline}\n\n"
        f"# 確定済みの解釈 (この対象・要望に忠実に。再解釈しない)\n{interp_block}\n\n"
        "# 骨格 JSON の構造\n"
        "{ regions: [ { id, label, layout:{x,y,w,h}, "
        "concepts:[ { id, label, layout:{x,y}, seed_status:{value,reviewed} } ] } ], "
        "edges:[ { from, to, kind } ] }\n\n"
        "# 指示\n"
        "- path は atlas_skeleton 直下からの JSON Pointer (例 /regions/0/concepts/2/label)。\n"
        "- 値の変更は op=replace。value_json には新しい値を JSON 文字列で入れる "
        "(ラベル変更なら value_json=\"\\\"新ラベル\\\"\")。\n"
        "- 概念やエッジの追加は op=add。配列末尾は path 末尾を /- にする。value_json に完全なオブジェクトを入れる。\n"
        "- 削除は op=remove。\n"
        "- id は原則変更しない (版を跨いで不変。§16-4)。ラベルや配置・seed_status の調整に留める。\n"
        "- 要望と無関係な箇所は変更しない。最小の patch にする。\n"
        "- before / after に変更前後の値を人間可読な文字列で入れる (教員の diff 確認用)。\n"
        "- summary に編集案の一言サマリを日本語で書く。"
    )
    with usage_context("admin:atlas_assist"):
        return generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=SkeletonEditProposal,
            model=_assist_model(model),
        )


# ---------------------------------------------------------------------------
# JSON Patch (RFC 6902) の最小適用器 — 依存を増やさず replace/add/remove を扱う
# ---------------------------------------------------------------------------


class PatchApplyError(ValueError):
    pass


def _unescape_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_parent(doc: Any, path: str):
    """JSON Pointer の親コンテナと末尾トークンを返す。"""
    if not path.startswith("/"):
        raise PatchApplyError(f"path は / で始まる必要があります: {path!r}")
    tokens = [_unescape_token(t) for t in path.split("/")[1:]]
    if not tokens:
        raise PatchApplyError("空の path は編集できません")
    parent = doc
    for token in tokens[:-1]:
        if isinstance(parent, list):
            try:
                parent = parent[int(token)]
            except (ValueError, IndexError) as exc:
                raise PatchApplyError(f"配列インデックス不正: {path!r}") from exc
        elif isinstance(parent, dict):
            if token not in parent:
                raise PatchApplyError(f"path が存在しません: {path!r}")
            parent = parent[token]
        else:
            raise PatchApplyError(f"path をたどれません: {path!r}")
    return parent, tokens[-1]


def _list_index(parent: list, token: str, path: str, *, for_insert: bool = False) -> int:
    """末尾トークンを配列インデックスとして解決する。不正は PatchApplyError。"""
    try:
        idx = int(token)
    except (ValueError, TypeError) as exc:
        raise PatchApplyError(f"配列インデックスが数値ではありません: {path!r}") from exc
    limit = len(parent) + (1 if for_insert else 0)
    if idx < 0 or idx >= limit:
        raise PatchApplyError(f"配列インデックスが範囲外です: {path!r}")
    return idx


def apply_json_patch(skeleton: dict, patch: list[dict]) -> dict:
    """atlas_skeleton dict に JSON Patch を適用した新しい dict を返す (非破壊)。

    対応 op: replace / add / remove。value は各 op の value_json を json.loads した値。
    LLM 由来の patch を教員承認前に適用してプレビュー・検証するために使う。
    LLM 生成の path は信用できないため、不正はすべて PatchApplyError に正規化する
    (呼び出し側が「提案は返すが適用不可」として扱えるように)。
    """
    doc = copy.deepcopy(skeleton)
    for i, raw in enumerate(patch):
        op = str(raw.get("op") or "")
        path = str(raw.get("path") or "")
        value = None
        if raw.get("value_json") is not None:
            try:
                value = json.loads(raw["value_json"])
            except (ValueError, TypeError) as exc:
                raise PatchApplyError(f"patch[{i}] の value_json が JSON として不正です") from exc
        parent, token = _resolve_parent(doc, path)

        if op == "replace":
            if isinstance(parent, list):
                parent[_list_index(parent, token, path)] = value
            elif isinstance(parent, dict):
                if token not in parent:
                    raise PatchApplyError(f"replace 対象が存在しません: {path!r}")
                parent[token] = value
            else:
                raise PatchApplyError(f"replace できません: {path!r}")
        elif op == "add":
            if isinstance(parent, list):
                if token == "-":
                    parent.append(value)
                else:
                    parent.insert(_list_index(parent, token, path, for_insert=True), value)
            elif isinstance(parent, dict):
                parent[token] = value
            else:
                raise PatchApplyError(f"add できません: {path!r}")
        elif op == "remove":
            if isinstance(parent, list):
                del parent[_list_index(parent, token, path)]
            elif isinstance(parent, dict):
                parent.pop(token, None)
            else:
                raise PatchApplyError(f"remove できません: {path!r}")
        else:
            raise PatchApplyError(f"未対応の op です: {op!r}")
    return doc
