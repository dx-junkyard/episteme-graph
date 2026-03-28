"""PDF text extraction and hypothesis-driven sequential LLM structure extraction.

Architecture (GROBID ベース構造事前マッピング):
1. GROBID:     PDF を GROBID API に送信し TEI XML を取得。
2. Parse:      XML から論理セクション（Abstract / Body の <div>）を抽出。
               References・Acknowledgments は除外。
3. Hypothesis: 最初のセクション（Abstract 等）から初期仮説を生成。
4. Refine:     後続セクションで逐次精錬。
5. Finalize:   最終 PaperStructure を出力。

Embedding runs concurrently in a background thread using ThreadPoolExecutor.

Notes on Reasoning models
--------------------------
o1 / o3-mini / gpt-5.2 等の reasoning モデルは以下の制約がある:
- ``system`` ロールは使用不可。``developer`` または ``user`` ロールのみ。
- ``temperature`` / ``max_tokens`` は非推奨のため指定しない。
  （必要なら ``max_completion_tokens`` を使う）
ここではすべてのプロンプトを ``user`` ロールで送信し、
temperature 等のパラメータは一切指定しないことで制約に対応している。
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from core.config import get_settings as get_app_settings
from core.llm import generate_text, generate_text_with_structured_output, generate_embeddings
from core.schema import AbstractionPattern, FieldDiff, MergeResult, PaperStructure

logger = logging.getLogger(__name__)

# セクション分割フォールバック用の閾値
_MAX_SECTION_CHARS = 8_000
_FALLBACK_CHUNK_SIZE = 4_000


# ---------------------------------------------------------------------------
# 内部データクラス
# ---------------------------------------------------------------------------

@dataclass
class _AnalysisState:
    """論文を逐次読み込む際に蓄積する分析状態。"""

    draft: dict[str, Any] = field(default_factory=dict)
    confirmed: list[str] = field(default_factory=list)
    revised: list[str] = field(default_factory=list)
    new_info: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    chunk_summaries: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public: GROBID API を使った PDF → TEI XML 変換
# ---------------------------------------------------------------------------

def extract_tei_xml_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """PDF バイナリを GROBID の processFulltextDocument API に送信し TEI XML を返す。"""
    grobid_url = get_app_settings().grobid_url
    url = f"{grobid_url}/api/processFulltextDocument"
    resp = requests.post(
        url,
        files={"input": ("paper.pdf", pdf_bytes, "application/pdf")},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


def _split_long_section(text: str, max_len: int = _MAX_SECTION_CHARS) -> list[str]:
    """長すぎるセクションをセンテンス境界で分割するフォールバック。"""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        if current_len + len(sent) > _FALLBACK_CHUNK_SIZE and current_len > 0:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sent)
        current_len += len(sent)
    if current:
        chunks.append(" ".join(current))
    return chunks or [text[:_FALLBACK_CHUNK_SIZE]]


# ---------------------------------------------------------------------------
# Public: TEI XML → 論理チャンク（セクション単位）
# ---------------------------------------------------------------------------

_EXCLUDED_HEADINGS = re.compile(
    r"(references?|bibliography|acknowledgm|funding|competing\s+interest)",
    re.IGNORECASE,
)


def parse_tei_to_logical_chunks(tei_xml: str) -> list[str]:
    """GROBID が返す TEI XML を解析し、セクション単位の論理チャンクを返す。

    - ``<teiHeader>`` 内の Abstract を最初のチャンクとして取得。
    - ``<text><body>`` 内の各 ``<div>`` を 1 セクション = 1 チャンクとして抽出。
    - ``<back>`` / ``<listBibl>``（References）や Acknowledgments は除外。
    - 8 000 文字を超えるセクションはセンテンス境界でさらに分割。
    """
    soup = BeautifulSoup(tei_xml, "xml")
    chunks: list[str] = []

    # --- Abstract の抽出 ---
    abstract_tag = soup.find("abstract")
    if abstract_tag:
        abstract_text = abstract_tag.get_text(separator=" ", strip=True)
        if abstract_text:
            chunks.append(f"[Abstract]\n{abstract_text}")

    # --- Body セクションの抽出 ---
    body = soup.find("body")
    if body:
        for div in body.find_all("div", recursive=False):
            head_tag = div.find("head")
            heading = head_tag.get_text(strip=True) if head_tag else ""

            # References / Acknowledgments を除外
            if heading and _EXCLUDED_HEADINGS.search(heading):
                continue

            paragraphs: list[str] = []
            for p_tag in div.find_all("p"):
                p_text = p_tag.get_text(separator=" ", strip=True)
                if p_text:
                    paragraphs.append(p_text)

            if not paragraphs:
                continue

            section_text = (
                f"[{heading}]\n" + "\n".join(paragraphs)
                if heading
                else "\n".join(paragraphs)
            )

            # 長いセクションはフォールバック分割
            chunks.extend(_split_long_section(section_text))

    if not chunks:
        # XML パースで何も取れなかった場合: テキスト全体をフォールバック
        fallback = soup.get_text(separator=" ", strip=True)
        if fallback:
            chunks = _split_long_section(fallback)

    return chunks or [""]


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """後方互換: PDF バイナリからプレーンテキストを返す。

    内部で GROBID API を呼び出し、TEI XML からテキストを抽出する。
    GROBID 未起動などでエラーが発生した場合は PyMuPDF へフォールバックする。
    """
    try:
        tei_xml = extract_tei_xml_from_pdf_bytes(pdf_bytes)
        chunks = parse_tei_to_logical_chunks(tei_xml)
        return "\n\n".join(chunks)
    except Exception:
        logger.warning(
            "GROBID extraction failed, falling back to PyMuPDF",
            exc_info=True,
        )
        import fitz  # PyMuPDF — フォールバック用に遅延 import

        parts: list[str] = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                parts.append(page.get_text())
        return "\n".join(parts).replace("\x00", "")


def chunk_text(text: str, chunk_size: int = _FALLBACK_CHUNK_SIZE) -> list[str]:
    """後方互換: テキストをチャンク分割する。

    GROBID ベースの論理チャンク生成（parse_tei_to_logical_chunks）を優先して
    使用するため、この関数は extract_paper_structure のフォールバック経路のみで使用。
    """
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)

        if current_len + para_len > chunk_size and current_len >= 500:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        if para_len > chunk_size:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            sub: list[str] = []
            sub_len = 0
            for sent in sentences:
                if sub_len + len(sent) > chunk_size and sub_len >= 500:
                    combined = ("\n\n".join(current) + "\n" if current else "") + " ".join(sub)
                    chunks.append(combined.strip())
                    current = []
                    current_len = 0
                    sub = []
                    sub_len = 0
                sub.append(sent)
                sub_len += len(sent)
            if sub:
                remainder = " ".join(sub)
                current.append(remainder)
                current_len += len(remainder)
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text[:chunk_size]]


# ---------------------------------------------------------------------------
# Private: LLM ヘルパー
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict[str, Any]:
    """LLM レスポンスから最初の JSON オブジェクトを抽出する。"""
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _generate_hypothesis(first_chunk: str, paper_id: str) -> dict[str, Any]:
    """最初のチャンク（Abstract等）から初期仮説ドラフトを生成する。

    LLM に渡すプロンプトはコスト削減のため最小限にする。
    返却 JSON のキー: problem / hypothesis / methodology / contributions
    """
    prompt = (
        f"[paper_id: {paper_id}] 以下は論文の冒頭です。\n"
        "全体像の初期仮説ドラフトを簡潔に作成してください。\n"
        '以下のJSONのみで回答 (キー: "problem", "hypothesis", "methodology", "contributions"):\n\n'
        f"{first_chunk}"
    )

    raw = generate_text(messages=[{"role": "user", "content": prompt}])
    result = _parse_json(raw)
    if not result:
        # JSON 抽出失敗時のフォールバック
        result = {
            "problem": raw[:300],
            "hypothesis": "",
            "methodology": "",
            "contributions": "",
        }
    logger.debug("Hypothesis draft generated for %s", paper_id)
    return result


def _to_str(item: object) -> str:
    """Coerce an item to a string — handles dicts returned by the LLM."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def _refine_with_chunk(state: _AnalysisState, chunk: str, chunk_idx: int) -> None:
    """チャンクを読み込んで分析状態をインプレースで更新する。"""
    draft_str = (
        f"Problem: {state.draft.get('problem', '')[:200]}\n"
        f"Hypothesis: {state.draft.get('hypothesis', '')[:200]}"
    )
    confirmed_str = "; ".join(_to_str(c) for c in state.confirmed[-5:]) if state.confirmed else "none"

    prompt = (
        f"[Chunk {chunk_idx}]\n"
        f"Initial draft:\n{draft_str}\n"
        f"Confirmed so far: {confirmed_str}\n\n"
        f"New chunk:\n{chunk}\n\n"
        "=== Structure Extraction Rules ===\n"
        "1. When you extract new elements or causal relationships, ALWAYS identify where they sit "
        "within the paper's overall structure (macro context).\n"
        "2. If an element is a constituent of a larger concept, identify the parent node and note "
        "a CONTAINS relationship (parent contains child) for later DSL encoding.\n"
        "3. If elements have prerequisite/dependency or contrast/equivalence relationships, note them "
        "as REQUIRES (dependency) or EQUIVALENT (equivalence/contrast) lateral relationships.\n\n"
        "=== Polarity Rules ===\n"
        "For causal polarity, do NOT force '+' or '-' when direction is ambiguous:\n"
        "- Use '+/-' when the effect is bidirectional or context-dependent.\n"
        "- Use '?' when impact exists but polarity direction is unknown.\n\n"
        "Return JSON only "
        '(keys: "confirmed"[], "revised"[], "new_info"[], "pending"[], "summary" str):'
    )

    raw = generate_text(messages=[{"role": "user", "content": prompt}])
    update = _parse_json(raw)
    if update:
        state.confirmed.extend(update.get("confirmed") or [])
        state.revised.extend(update.get("revised") or [])
        state.new_info.extend(update.get("new_info") or [])
        if update.get("pending") is not None:
            state.pending = update["pending"]
        if update.get("summary"):
            state.chunk_summaries.append(update["summary"])
    logger.debug("Chunk %d refined for state", chunk_idx)


def _finalize_structure(state: _AnalysisState, paper_id: str, license: str = "") -> PaperStructure:
    """蓄積された分析状態から最終的な PaperStructure を生成する。"""

    def _bullets(items: list, limit: int = 8) -> str:
        return "\n".join(f"- {_to_str(x)}" for x in items[:limit]) or "none"

    state_str = (
        f"Confirmed findings:\n{_bullets(state.confirmed)}\n\n"
        f"Revised assumptions:\n{_bullets(state.revised)}\n\n"
        f"Unexpected new info:\n{_bullets(state.new_info)}\n\n"
        f"Chunk summaries:\n{_bullets(state.chunk_summaries, 12)}"
    )

    prompt = (
        f'paper_id="{paper_id}"\n\n'
        "Accumulated analysis state from sequential reading:\n"
        f"{state_str}\n\n"
        "Based on the above, extract the final paper structure.\n"
        "Use both Japanese and English in descriptions. "
        f'Set paper_id="{paper_id}".\n'
        "Also extract: authors (list of author names), year (publication year as integer), "
        "and domain (target academic domain, e.g. 'ecology', 'economics', 'computer science').\n\n"
        "=== Episteme Graph-SMILES DSL Instructions ===\n"
        "You MUST encode all extracted causal relationships (CausalEdge) and variables "
        "into the Episteme Graph-SMILES DSL and store the result in abstract_structure.smiles_dsl.\n\n"
        "=== Core vs. Peripheral Edge Classification ===\n"
        "Each CausalEdge must be classified as either CORE or PERIPHERAL:\n"
        "- CORE (is_core=true): Backbone mechanisms essential to the paper's main argument. "
        "These represent the fundamental causal chain without which the paper's thesis collapses.\n"
        "- PERIPHERAL (is_core=false): Supplementary, contextual, or domain-specific relationships "
        "that provide supporting detail but are not essential to the core mechanism.\n\n"
        "=== CorePredicate (標準述語) — MANDATORY ===\n"
        "Each CausalEdge MUST set core_predicate to ONE of the following nine standardized values:\n"
        "  CAUSES     → one variable directly produces or triggers another\n"
        "  INHIBITS   → one variable suppresses, blocks, or reduces another\n"
        "  CORRELATES → two variables co-vary without clear directionality\n"
        "  DEFINES    → one variable characterizes, specifies, or constitutes another\n"
        "  MEASURES   → one variable operationalizes or quantifies another\n"
        "  TRANSFORMS → one variable converts or changes the state of another\n"
        "  REQUIRES   → one variable depends on or presupposes another (prerequisite/dependency)\n"
        "  CONTAINS   → parent-child (macro-micro) inclusion; the source contains the target as a constituent\n"
        "  EQUIVALENT → equivalence or contrast between concepts at the same hierarchical level (lateral relation)\n\n"
        "=== Hierarchical & Lateral Relationship Rules ===\n"
        "- When extracting elements and causal relationships, ALWAYS check where each element sits "
        "in the paper's overall structure (macro context).\n"
        "- If an element is a constituent of a larger concept, identify the parent node and link "
        "them with ==[CONTAINS:contains:+]=> (parent → child).\n"
        "- If elements share prerequisite/dependency or contrast/equivalence relationships, express "
        "them as -[REQUIRES:requires:+]-> or -[EQUIVALENT:equals:+]-> lateral edges.\n"
        "- Do NOT use absolute depth levels. All hierarchy is expressed via relative CONTAINS edges.\n\n"
        "Each CausalEdge MUST also set domain_verb to the original domain-specific verb "
        "(e.g., 'operationalizes', 'structures', 'quantifies', 'induces').\n\n"
        "DSL syntax (IMPORTANT — nodes use parentheses, edges use square brackets):\n"
        "  Core edge:       (varID:OntologyType:value) ==[CorePredicate:domain_verb:polarity]=> (targetVarID:OntologyType:value)\n"
        "  Peripheral edge: (varID:OntologyType:value) -[CorePredicate:domain_verb:polarity]-> (targetVarID:OntologyType:value)\n\n"
        "=== Polarity Extended Rules ===\n"
        "polarity accepts four values: '+', '-', '+/-', '?'\n"
        "- '+' : positive / promoting effect\n"
        "- '-' : negative / inhibiting effect\n"
        "- '+/-' : bidirectional or context-dependent (can be positive or negative depending on conditions)\n"
        "- '?' : impact exists but direction is unknown\n"
        "Do NOT force '+' or '-' when the relationship is ambiguous. Use '+/-' or '?' instead.\n\n"
        "Examples:\n"
        "  Core:       (a:Agent:Toyota) ==[CAUSES:operationalizes:+]=> (r:Resource:Profit)\n"
        "  Core:       (x:Event:Stress) ==[MEASURES:quantifies:+]=> (y:Resource:CortisalLevel)\n"
        "  Peripheral: (e:Event:MarketShift) -[CORRELATES:correlates:+]-> (r:Resource:Profit)\n"
        "  Hierarchy:  (th:Resource:TheoreticalFramework) ==[CONTAINS:contains:+]=> (h:Resource:Hypothesis)\n"
        "  Lateral:    (a:Resource:ConceptA) -[EQUIVALENT:equals:+]-> (b:Resource:ConceptB)\n"
        "  Lateral:    (p:Resource:Prerequisite) -[REQUIRES:requires:+]-> (m:Event:MainMethod)\n"
        "  Ambiguous:  (x:Event:Intervention) ==[CAUSES:influences:+/-]=> (y:Resource:Outcome)\n"
        "  Unknown:    (f:Resource:Factor) -[CORRELATES:associated:?]-> (r:Resource:Result)\n\n"
        "=== [PHASE 1 — ONTOLOGY ENFORCEMENT] OntologyType Mandatory Rules ===\n"
        "CRITICAL: Every single node in the DSL string MUST include OntologyType. "
        "Nodes without OntologyType are INVALID and must be rejected.\n\n"
        "VALID OntologyType values (10 types — OSL 汎用 4 型 + 素粒子物理学拡張 6 型):\n"
        "  Agent | Event | Resource | Intentional Moment\n"
        "  MathematicalObject | PhysicalPhenomenon | TheoreticalFramework | Theorem | Symmetry | Particle\n\n"
        "ENFORCEMENT CHECKLIST — before finalizing smiles_dsl, verify EVERY node satisfies:\n"
        "  [✓] Format is (varID:OntologyType:ConcreteValue) — all three parts present\n"
        "  [✓] OntologyType is one of the ten valid values listed above\n"
        "  [✓] CRITICAL: ConcreteValue MUST NOT contain parentheses '(' or ')'. To include acronyms or extra info, use square brackets '[]' or hyphens '-' instead (e.g., Use (f:Resource:5DCR_framework_[Revised]) instead of (f:Resource:5DCR_framework_(Revised))).\n"
        "  [✓] No bare (varID:ConcreteValue) without OntologyType (this is FORBIDDEN)\n"
        "  [✓] No bare (varID:OntologyType) without ConcreteValue (this is FORBIDDEN)\n"
        "  [✓] Back-references (varID) (for cycles) are acceptable only if the variable "
        "was already declared with full (varID:OntologyType:ConcreteValue) earlier in the same DSL string\n\n"
        "Classification guide:\n"
        "  Agent              → human, organization, institution, team, government, legal entity, actor that initiates action\n"
        "  Event              → occurrence, process, phenomenon, dynamic change\n"
        "  Resource           → material, information, capital, output, artifact\n"
        "  Intentional Moment → intention, goal, belief, commitment, mental state\n"
        "  MathematicalObject → tensor, group, manifold, operator, spinor, metric, Lie algebra, Hilbert space, functional integral\n"
        "  PhysicalPhenomenon → phase transition, scattering, decay, radiative correction, confinement, symmetry breaking, Hawking radiation\n"
        "  TheoreticalFramework → QFT, QED, QCD, Standard Model, string theory, effective field theory, lattice gauge theory\n"
        "  Theorem            → Noether's theorem, Ward identity, LSZ reduction formula, CPT theorem, Goldstone theorem, optical theorem\n"
        "  Symmetry           → gauge symmetry, Lorentz symmetry, CPT symmetry, chiral symmetry, supersymmetry, conformal symmetry\n"
        "  Particle           → quark, lepton, gauge boson, Higgs field, gluon, photon, fermion field, ghost field\n\n"
        "=== DOMAIN RULE: For physics papers (domain containing 'physics', 'QFT', '場の量子論', '素粒子' etc.), ===\n"
        "STRONGLY PREFER the physics-specific OntologyTypes (MathematicalObject, PhysicalPhenomenon, "
        "TheoreticalFramework, Theorem, Symmetry, Particle) over generic types. "
        "Only fall back to Agent/Event/Resource/Intentional Moment when no physics type fits.\n\n"
        "Rules:\n"
        "1. Each variable must be assigned an OntologyType from the ten valid values. "
        "No exceptions. If uncertain, choose the closest type.\n"
        "2. Each CausalEdge must specify polarity (+, -, +/-, or ?) in both the edge's polarity field "
        "and the DSL string.\n"
        "3. Each CausalEdge must specify ontology_level with the relevant ontology relation type.\n"
        "4. Each CausalEdge must set is_core=true for core edges and is_core=false for peripheral edges. "
        "Use ==[…]=> in the DSL for core edges and -[…]-> for peripheral edges.\n"
        "5. If there is a cycle (loop) among variables, reuse the variable ID "
        "without repeating the full declaration (e.g., (a) instead of (a:Agent:Toyota)).\n"
        "6. Chain multiple edges with spaces: "
        "(a:Agent:X) ==[CAUSES:operationalizes:+]=> (r:Resource:Y) (r) -[INHIBITS:suppresses:-]-> (a)\n"
        "7. Classify all extraction targets strictly according to OntologyType.\n"
        "8. Aim for roughly 30-50%% of edges to be core. If all edges seem equally important, "
        "select only the most fundamental causal chain as core.\n"
        "9. CRITICAL: Nodes MUST use parentheses (varID:OntologyType:value) and edges MUST use square brackets "
        "[CorePredicate:domain_verb:polarity]. The old two-part edge format [relationType:polarity] is FORBIDDEN. "
        "Using square brackets for nodes is FORBIDDEN.\n\n"
        "=== [KEY EQUATIONS EXTRACTION — LaTeX & Variable Definitions] ===\n"
        "You MUST populate the key_equations field with important equations from the paper.\n"
        "For each equation:\n"
        "  1. latex: Write the equation in LaTeX notation (e.g., '$\\mathcal{L} = \\bar{\\psi}(i\\gamma^\\mu D_\\mu - m)\\psi$')\n"
        "  2. description: Explain the physical meaning/role of the equation\n"
        "  3. variables: List ALL variables/symbols with their physical meanings:\n"
        "     - symbol: LaTeX notation (e.g., '$\\psi$')\n"
        "     - description: Physical meaning (e.g., 'ディラック場 / Dirac field')\n"
        "  4. context: Where this equation appears or what it is used for\n\n"
        "CRITICAL for physics papers:\n"
        "- Extract Lagrangians, Hamiltonians, equations of motion, Feynman rules, and key identities\n"
        "- NEVER omit variable definitions — every symbol must be mapped to its physical meaning\n"
        "- Include both the mathematical structure AND the physical interpretation\n"
        "- For derived quantities, note what they are derived from (e.g., '作用からの変分 / variation of the action')\n"
    )

    structure = generate_text_with_structured_output(
        messages=[{"role": "user", "content": prompt}],
        response_format=PaperStructure,
    )
    return structure.model_copy(update={"paper_id": paper_id, "license": license})


def _embed_and_store_chunks(chunks: list[str], paper_id: str) -> None:
    """チャンクを Embedding して PostgreSQL に保存する（ベストエフォート）。"""
    try:
        from core.embedder import embed_and_store  # 循環 import 回避のため遅延

        embed_and_store(chunks, paper_id)
    except Exception:
        logger.warning(
            "Embedding/Qdrant storage failed for %s (continuing without embeddings)",
            paper_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Public: メイン抽出関数
# ---------------------------------------------------------------------------

def extract_paper_structure(
    text: str,
    paper_id: str = "",
    skip_embedding: bool = False,
    pdf_bytes: bytes | None = None,
    license: str = "",
) -> PaperStructure:
    """仮説検証型の逐次処理で論文テキストから PaperStructure を抽出する。

    処理フロー:
    1. pdf_bytes が渡された場合 → GROBID API で論理チャンク（セクション単位）を生成。
       pdf_bytes が None の場合 → 従来の text ベースチャンク分割（フォールバック）。
    2. skip_embedding=False の場合のみ Embedding をバックグラウンド並行実行
    3. 最初のチャンクから初期仮説ドラフトを生成
    4. 2番目以降のチャンクで逐次精錬（状態を更新）
    5. 最終評価で PaperStructure を出力
    6. skip_embedding=False の場合のみ Embedding 完了を待機（最大 90 秒）

    Parameters
    ----------
    text:
        フォールバック用のプレーンテキスト。pdf_bytes が None の場合に使用。
    pdf_bytes:
        元の PDF バイナリ。渡された場合は GROBID で論理チャンクを生成する。
    skip_embedding:
        True の場合、Qdrant への Embedding 処理を完全にスキップする。
    """
    # GROBID ベースの論理チャンク生成を優先
    if pdf_bytes is not None:
        try:
            tei_xml = extract_tei_xml_from_pdf_bytes(pdf_bytes)
            chunks = parse_tei_to_logical_chunks(tei_xml)
            logger.info("GROBID logical chunks generated: %d sections", len(chunks))
        except Exception:
            logger.warning(
                "GROBID failed for %s, falling back to text chunking",
                paper_id,
                exc_info=True,
            )
            chunks = chunk_text(text)
    else:
        chunks = chunk_text(text)

    logger.info("paper=%s  total_chunks=%d  skip_embedding=%s", paper_id, len(chunks), skip_embedding)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    embed_future: concurrent.futures.Future | None = None

    if not skip_embedding:
        embed_future = executor.submit(_embed_and_store_chunks, chunks, paper_id)

    try:
        # Step 1: 初期仮説生成（最初のチャンク）
        state = _AnalysisState()
        state.draft = _generate_hypothesis(chunks[0], paper_id)
        logger.info("Hypothesis generated for %s", paper_id)

        # Step 2: 2番目以降のチャンクで逐次精錬
        for idx, chunk in enumerate(chunks[1:], start=1):
            try:
                _refine_with_chunk(state, chunk, idx)
                logger.info("Chunk %d/%d refined for %s", idx, len(chunks) - 1, paper_id)
            except Exception:
                logger.warning(
                    "Refinement failed for chunk %d of %s — skipping",
                    idx,
                    paper_id,
                    exc_info=True,
                )

        # Step 3: 最終評価
        logger.info("Finalizing structure for %s", paper_id)
        structure = _finalize_structure(state, paper_id, license=license)

    finally:
        # Embedding の完了を待つ（最大 90 秒、失敗しても続行）
        if embed_future is not None:
            try:
                embed_future.result(timeout=90)
                logger.info("Embedding completed for %s", paper_id)
            except Exception:
                logger.warning("Embedding future failed for %s", paper_id, exc_info=True)
        else:
            logger.info("Embedding skipped for %s (skip_embedding=True)", paper_id)
        executor.shutdown(wait=False)

    return structure


# ---------------------------------------------------------------------------
# Public: Diff 計算 (Gateway層)
# ---------------------------------------------------------------------------

def _flatten_value(value: Any) -> str:
    """ネストされた値を比較用の文字列に変換する。"""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value) if value is not None else ""


def compute_structure_diff(
    base: PaperStructure,
    proposed: PaperStructure,
) -> list[FieldDiff]:
    """base と proposed の PaperStructure を比較し、変更があるフィールドのみを返す。

    比較は ``model_dump()`` で辞書化した上で再帰的に行い、
    review_status / paper_id など自動管理フィールドは除外する。

    Parameters
    ----------
    base:
        現在の正典 PaperStructure。
    proposed:
        ユーザー提案の PaperStructure。

    Returns
    -------
    list[FieldDiff]
        変更のあるフィールドのリスト。変更なしの場合は空リスト。
    """
    _SKIP_FIELDS = {"paper_id", "review_status", "reviewer_notes"}

    def _recurse(
        base_dict: dict[str, Any],
        proposed_dict: dict[str, Any],
        prefix: str = "",
    ) -> list[FieldDiff]:
        diffs: list[FieldDiff] = []
        for key in base_dict:
            if key in _SKIP_FIELDS and not prefix:
                continue
            path = f"{prefix}.{key}" if prefix else key
            b_val = base_dict.get(key)
            p_val = proposed_dict.get(key)

            if isinstance(b_val, dict) and isinstance(p_val, dict):
                diffs.extend(_recurse(b_val, p_val, path))
            else:
                b_str = _flatten_value(b_val)
                p_str = _flatten_value(p_val)
                if b_str != p_str:
                    diffs.append(FieldDiff(
                        field_path=path,
                        base_value=b_str,
                        proposed_value=p_str,
                    ))
        return diffs

    return _recurse(base.model_dump(), proposed.model_dump())


# ---------------------------------------------------------------------------
# Public: LLM提案評価・マージ関数 (Gateway層)
# ---------------------------------------------------------------------------

def evaluate_and_merge_proposals(
    base_structure: PaperStructure,
    proposed_structure: PaperStructure,
) -> MergeResult:
    """Reasoningモデルを使って正典構造とユーザー提案をマージ・評価する。

    Diff ベースの最適化:
    1. base と proposed の差分を計算し、変更がなければ早期リターン。
    2. 変更があるフィールドのみを LLM に送信しトークン消費を削減。
    3. LLM が承認した変更のみを base 構造に適用し、未変更フィールドの
       ハルシネーションによる破損を防止する。

    方針: 「ジャンクの中の宝石」を最大限に拾い上げる。
    粗削りな提案であっても有用な洞察・補足・修正を積極的に取り込み、
    正典構造をより良いものに育てる。

    Parameters
    ----------
    base_structure:
        現在の正典 PaperStructure（マージのベースライン）。
    proposed_structure:
        ユーザーが提出した提案 PaperStructure。

    Returns
    -------
    MergeResult
        ``merged_structure`` (更新後の正典) と
        ``evaluation_reasoning`` (マージ方針・却下理由のテキスト) を含む。

    Notes on Reasoning models
    -------------------------
    gpt-5.2 等の Reasoning モデルは ``system`` ロールをサポートしないため、
    すべてのプロンプトを ``user`` ロールで送信する。
    temperature 等のパラメータも指定しない。
    """
    # --- Step 1: Diff 計算 ---
    diffs = compute_structure_diff(base_structure, proposed_structure)

    if not diffs:
        logger.info("No diff detected — returning base structure as-is")
        return MergeResult(
            merged_structure=base_structure.model_copy(),
            evaluation_reasoning="提案構造と正典構造の間に差分がないため、変更なし。",
        )

    logger.info("Diff detected: %d field(s) changed", len(diffs))

    # --- Step 2: 差分のみを LLM に送信 ---
    diff_lines: list[str] = []
    for d in diffs:
        diff_lines.append(
            f"- field: {d.field_path}\n"
            f"  base:     {d.base_value}\n"
            f"  proposed: {d.proposed_value}"
        )
    diff_text = "\n".join(diff_lines)

    prompt = (
        "あなたは論文構造レビュアーです。\n"
        "以下に、正典構造 (base) とユーザー提案 (proposed) の「差分のみ」を示します。\n"
        "差分に含まれないフィールドは変更されていないため、一切触れないでください。\n\n"
        "【マージ方針】\n"
        "提案はジャンクを含む可能性がありますが、その中にある「宝石」（有用な洞察・"
        "補足・修正・新しい視点）を最大限に拾い上げてください。\n"
        "たとえ粗削りな提案であっても、正典構造をより正確・豊かにする部分があれば"
        "積極的に取り込んでください。\n"
        "一方、誤り・無関係・冗長な部分は却下し、その理由を明記してください。\n\n"
        f"--- 差分フィールド ({len(diffs)} 件) ---\n{diff_text}\n\n"
        "【出力形式】\n"
        "各差分フィールドについて、以下の JSON 配列で回答してください:\n"
        '[\n  {"field_path": "...", "action": "accept" or "reject", "final_value": "...", "reason": "..."}\n]\n'
        "action が accept の場合: final_value に採用する値を入れてください（proposed そのままでも base との折衷でも可）。\n"
        "action が reject の場合: final_value は空文字で構いません。base の値が維持されます。\n"
        "JSON 配列のみで回答してください。"
    )

    raw = generate_text(messages=[{"role": "user", "content": prompt}])

    # --- Step 3: LLM 結果をパースして base に差分適用 ---
    decisions = _parse_diff_decisions(raw)
    merged_dict = base_structure.model_dump()
    reasoning_parts: list[str] = []

    for d in diffs:
        decision = decisions.get(d.field_path)
        if decision and decision.get("action") == "accept":
            final_value = decision.get("final_value", d.proposed_value)
            _set_nested_value(merged_dict, d.field_path, final_value)
            reason = decision.get("reason", "")
            reasoning_parts.append(f"[ACCEPT] {d.field_path}: {reason}")
        else:
            reason = decision.get("reason", "差分なし or LLM 未回答") if decision else "LLM 未回答"
            reasoning_parts.append(f"[REJECT] {d.field_path}: {reason}")

    # paper_id は正典のものを必ず引き継ぐ
    merged_dict["paper_id"] = base_structure.paper_id
    merged_structure = PaperStructure.model_validate(merged_dict)

    return MergeResult(
        merged_structure=merged_structure,
        evaluation_reasoning="\n".join(reasoning_parts),
    )


def _parse_diff_decisions(raw: str) -> dict[str, dict[str, str]]:
    """LLM の差分判定レスポンスを field_path → decision dict にパースする。"""
    match = re.search(r"\[[\s\S]*\]", raw)
    if not match:
        return {}
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return {}
    result: dict[str, dict[str, str]] = {}
    for item in items:
        if isinstance(item, dict) and "field_path" in item:
            result[item["field_path"]] = item
    return result


def _set_nested_value(d: dict[str, Any], path: str, value: Any) -> None:
    """ドット区切りパスで辞書のネストされた値を設定する。

    値の型を元の構造に合わせて復元する（list/dict は JSON パース試行）。
    """
    keys = path.split(".")
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    last_key = keys[-1]
    original = d.get(last_key)

    # 元の値が list/dict で、新しい値が str の場合は JSON パースを試行
    if isinstance(original, (list, dict)) and isinstance(value, str):
        try:
            parsed = json.loads(value)
            d[last_key] = parsed
            return
        except (json.JSONDecodeError, TypeError):
            pass
    d[last_key] = value


# ---------------------------------------------------------------------------
# Public: 抽象化パターン抽出 (Public層)
# ---------------------------------------------------------------------------

def extract_abstraction_pattern(structure: PaperStructure) -> AbstractionPattern:
    """承認済み PaperStructure から汎用的な抽象化パターンを LLM で抽出する。

    Evo-DKD アプローチに基づき、具体的事象を変数（X, Y, Z 等）に置換して
    分野横断で適用可能な「問題解決の型」を生成する。

    Parameters
    ----------
    structure:
        パターン抽出対象の PaperStructure（review_status が approved 推奨）。

    Returns
    -------
    AbstractionPattern
        抽出された抽象化パターン。source_arxiv_id には元論文のIDがセットされる。
    """
    # Core edges のみを優先して抽出データを構築
    core_edges = [e for e in structure.abstract_structure.edges if e.is_core]
    peripheral_edges = [e for e in structure.abstract_structure.edges if not e.is_core]

    core_edges_desc = "\n".join(
        f"  - {e.source} {e.core_predicate.value}:{e.domain_verb}({e.polarity}) {e.target}" for e in core_edges
    ) or "  (none)"
    peripheral_edges_desc = "\n".join(
        f"  - {e.source} {e.core_predicate.value}:{e.domain_verb}({e.polarity}) {e.target}" for e in peripheral_edges
    ) or "  (none)"

    prompt = (
        "あなたはメタ構造転写エンジンの一部です。\n"
        "以下の論文構造データから、具体的な事象を「変数（X, Y, Z等）」に置き換え、\n"
        "分野横断で適用可能な汎用的な「問題解決の型（Abstraction Pattern）」を抽出してください。\n\n"
        "【重要：Core Edge 優先】\n"
        "論文の因果グラフには Core（骨格メカニズム）と Peripheral（補足要素）の区別があります。\n"
        "パターン抽出では Core edges を最優先で structural_rules に反映し、\n"
        "Peripheral edges は補足情報として参考にしつつ、パターンの本質に含めないでください。\n\n"
        f"--- Core Edges (骨格メカニズム) ---\n{core_edges_desc}\n\n"
        f"--- Peripheral Edges (補足要素) ---\n{peripheral_edges_desc}\n\n"
        "【ルール】\n"
        "- name: パターンの簡潔な名称（英語、20語以内）\n"
        "- description: パターンの説明（具体的なドメイン用語は使わず、抽象変数で記述）\n"
        "- variables_template: パターン内で使われる抽象変数のリスト（例: [\"X\", \"Y\", \"Z\"]）\n"
        "- structural_rules: 変数間の関係ルール（例: [\"X inhibits Y\", \"Y enables Z\"]）。\n"
        "  Core edges のみから structural_rules を構成してください。\n\n"
        "=== [PHASE 2 — VARIABLE SUBSTITUTION FAILSAFE] ===\n"
        "structural_rules に含まれる表現は完全に抽象化されていなければなりません。\n"
        "出力前に以下のフェイルセーフチェックを必ず実行してください:\n\n"
        "【FORBIDDEN（ドメイン語汚染の例 — 出力してはならない）】\n"
        "  ✗ 'Toyota causes Profit'         → 固有名詞・具体的エンティティが残存\n"
        "  ✗ 'cells synthesize ATP'          → 生物学ドメイン固有の語彙が残存\n"
        "  ✗ 'interest rate affects GDP'     → 経済学ドメイン固有の語彙が残存\n"
        "  ✗ 'X causes Toyota'               → 片方だけ抽象化されており不十分\n\n"
        "【REQUIRED（完全抽象化の例 — このように出力すること）】\n"
        "  ✓ 'X causes Y'                    → 両変数が完全に抽象変数\n"
        "  ✓ 'X inhibits Y when Z increases' → 変数のみで構成\n"
        "  ✓ 'X enables Y through Z'         → ドメイン語なし\n\n"
        "【フェイルセーフ手順】\n"
        "1. structural_rules の各ルール文字列を検査する\n"
        "2. 固有名詞・専門用語・ドメイン固有語が残存していないか確認する\n"
        "3. variables_template に含まれない識別子（X, Y, Z 以外の具体的単語）が\n"
        "   ルール中に存在する場合、そのルールを変数に置き換えるか破棄する\n"
        "4. description においても同様に、具体的ドメイン用語を使わず\n"
        "   抽象変数（X, Y, Z 等）で記述されていることを確認する\n\n"
        f"--- 論文構造データ ---\n{structure.model_dump_json(indent=2)}\n\n"
        f'source_arxiv_id は "{structure.paper_id}" を設定してください。\n'
        "JSONスキーマに厳格に従って出力してください。"
    )

    pattern = generate_text_with_structured_output(
        messages=[{"role": "user", "content": prompt}],
        response_format=AbstractionPattern,
    )
    return pattern.model_copy(update={"source_arxiv_id": structure.paper_id})
