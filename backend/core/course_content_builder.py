"""Build course topic content from document pipeline artifacts."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from core.course_data import course_chapters, course_source_material_ids, course_title, course_topics
from core.deliberation import labels as labels_mod
from core.document_pipeline.figure_images import normalize_figure_join_key
from core.llm import generate_text, generate_text_with_structured_output, get_llm_params
from core.postgres import get_session as _pg_session
from core.text_excerpt import excerpt, looks_like_tex_math
from episteme_graph.agents.equation_semantics.schema import (
    LINK_STATUSES,
    ROLE_IN_ARGUMENT_VOCAB,
    derive_role_in_argument,
)

logger = logging.getLogger(__name__)


def _strip_nuls(value: Any) -> Any:
    """Postgres jsonb cannot store \u0000; remove NULs from generated content."""
    if isinstance(value, str):
        return value.replace("\x00", "").replace("\\u0000", "")
    if isinstance(value, list):
        return [_strip_nuls(item) for item in value]
    if isinstance(value, dict):
        return {str(key).replace("\x00", "").replace("\\u0000", ""): _strip_nuls(item) for key, item in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_strip_nuls(value), ensure_ascii=False)


def build_course_content_background(user_id: str, course_id: str) -> None:
    """Best-effort background entrypoint for course registration."""
    try:
        build_course_content(user_id, course_id)
    except Exception:
        logger.exception("Course content build failed: course_id=%s user_id=%s", course_id, user_id)


def build_course_content(user_id: str, course_id: str) -> dict:
    """Populate learning_courses.data.topics with structured pipeline content.

    The course builder creates the outline first. This function enriches that
    outline from the latest document pipeline artifacts tied to the course's
    source materials.
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT data, user_id
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not row or not row[0]:
            return {"status": "not_found", "updated_topics": 0}
        if str(row[1]) != str(user_id):
            return {"status": "forbidden", "updated_topics": 0}

        course = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        _set_content_status(course, "processing")

        material_ids = _course_material_ids(course)
        document_ids = _load_document_ids(session, material_ids)
        if not document_ids:
            _set_content_status(
                course,
                "waiting_for_pipeline",
                "コースの教材に紐づく解析済みドキュメントが見つかりません。",
            )
            _save_course(session, course_id, course)
            return {"status": "waiting_for_pipeline", "updated_topics": 0}

        artifacts_by_doc = _load_latest_artifacts(session, document_ids)
        bundle = _collect_structured_content(artifacts_by_doc)
        if not bundle["mapping_topics"] and not bundle["components"]:
            _set_content_status(
                course,
                "waiting_for_pipeline",
                "CourseMappingAgent または ComponentAssemblyAgent の成果物がまだありません。",
            )
            _save_course(session, course_id, course)
            return {"status": "waiting_for_pipeline", "updated_topics": 0}

        chunks_by_material = _load_chunks(session, material_ids)
        figures_index = _load_document_figures_index(session, document_ids)
        enriched_topics = _enrich_topics(course_topics(course), bundle, chunks_by_material, figures_index)
        draft_result = _generate_course_topic_drafts(course, enriched_topics)
        course["topics"] = enriched_topics
        # referenced_sections は生成しない（2026-07-26）。トピック→component_id の内部対応表を
        # そのまま学習者の出典タブに出しており、内部 ID（comp_001）と agent クラス名だけが
        # 並ぶ「学習者に何も伝えない」表示になっていた。根拠となる論理要素は教材本文の
        # ⚓ チップ（evidence_items + component context API）が正本で、こちらは重複かつ劣化。
        # 既存コースの保存済み `referenced_sections` は消さない（P4。UI が読まなくなるだけ）。
        _set_content_status(
            course,
            "completed",
            "",
            {
                "document_ids": document_ids,
                "updated_topics": len(enriched_topics),
                "mapping_topics": len(bundle["mapping_topics"]),
                "components": len(bundle["components"]),
                "equations": len(bundle["equations"]),
                "drafted_topics": draft_result["drafted_topics"],
                "draft_errors": draft_result["draft_errors"],
            },
        )
        _invalidate_topic_lecture_audio_cache(session, course_id)
        _save_course(session, course_id, course)
        return {
            "status": "completed",
            "updated_topics": len(enriched_topics),
            "drafted_topics": draft_result["drafted_topics"],
            "draft_errors": draft_result["draft_errors"],
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _invalidate_topic_lecture_audio_cache(session, course_id: str) -> None:
    """コース内容生成が全トピックの student_material/spoken_script を無条件上書き

    （``_generate_course_topic_drafts`` / ``_apply_deterministic_topic_draft_fallback``）
    するため、生成済みのトピック音声キャッシュ (``topic_lecture_audio_cache``) を
    無効化する。個別トピック編集時の DELETE
    （``routes/lecture_studio/topics.py::save_lecture_studio_course_topic``）と同じ方針で、
    次回の音声生成で作り直される。全トピックが無条件上書きされるため、トピック単位
    ではなくコース単位で削除する。呼び出し元 ``build_course_content`` の
    try/except/finally（rollback・close）にそのまま乗るよう、commit はしない
    （``_save_course`` の commit と同一トランザクションでまとめて確定する）。
    """
    session.execute(
        sa_text("""
            DELETE FROM topic_lecture_audio_cache
            WHERE course_id = :course_id
        """),
        {"course_id": course_id},
    )


def _course_material_ids(course: dict) -> list[str]:
    return list(dict.fromkeys(course_source_material_ids(course)))


def _load_document_ids(session, material_ids: list[str]) -> list[str]:
    if not material_ids:
        return []
    params = {f"mid_{idx}": mid for idx, mid in enumerate(material_ids)}
    placeholders = ", ".join(f":mid_{idx}" for idx in range(len(material_ids)))
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT c.document_id::text
            FROM chunks c
            WHERE c.material_id IN ({placeholders})
              AND c.document_id IS NOT NULL
        """),
        params,
    ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def _load_latest_artifacts(session, document_ids: list[str]) -> dict[str, dict]:
    """Load each document's adopted (active) run artifacts for course content (#408).

    Prefers documents.active_analysis_run_id; falls back to the latest *completed*
    run. A rejected candidate or in-flight latest run never feeds course content.
    """
    from core.document_pipeline.persistence import resolve_artifact_runs

    artifacts: dict[str, dict] = {}
    resolved = resolve_artifact_runs(session, document_ids)
    for document_id, info in resolved.items():
        stage_outputs = info.get("stage_outputs")
        doc_artifacts = stage_outputs.get("_artifacts") if isinstance(stage_outputs, dict) else None
        if isinstance(doc_artifacts, dict):
            artifacts[document_id] = doc_artifacts
    return artifacts


def _equation_display_math(equation: dict) -> tuple[str | None, str | None]:
    """ネストされた equation_semantics 生成物から表示用 latex / plain_text を導出する。

    EquationSemanticsResult.to_equations_export() と同じ規則:
    - 信頼できる source_extraction を基本とするが、needs_math_review の PDF 由来数式は
      監査用テキストであり表示用数式にしない（None）。
    - reconstruction があればそれを優先する。
    - prose 再構成（latex_is_prose）は監査専用なので表示しない（None）。
    既にトップレベルへフラット化済み（latex/plain_text を持つ）の生成物はそのまま尊重する。
    """
    src = equation.get("source_extraction") if isinstance(equation.get("source_extraction"), dict) else {}
    rec = equation.get("reconstruction") if isinstance(equation.get("reconstruction"), dict) else {}

    needs_review = bool(src.get("needs_math_review"))
    latex = None if needs_review else src.get("latex")
    plain_text = None if needs_review else src.get("plain_text")

    if str(rec.get("status") or "none") != "none":
        latex = rec.get("latex")
        plain_text = rec.get("plain_text")

    if "latex_is_prose" in (rec.get("review_reason") or []):
        latex = None
        plain_text = None

    return latex, plain_text


# ── 数式の説明材料（equation_hover_content_design.md §3.1 / EH1〜EH5）─────────
# equation_semantics の生成物は役割・記号の意味を ``semantics``（EquationSemantics）
# 配下に持つのに、コーススナップショットへは latex / plain_text / raw_text しか
# 落ちていなかった。そのため学習画面の数式ホバーが「生 TeX の再掲」しかできず、
# 情報量ゼロの劣化コピーになっていた（設計書 §1.3）。ここで役割・意味要約・記号の
# 意味を平坦化して snapshot に載せる。役割語彙とその導出規則は A層が正本なので
# import して使い（EH5: A層は読むだけ）、日本語表示名への変換は
# ``frontend/public/js/element-vocab.js`` が正本（スナップショットに訳語を焼かない）。
_EQUATION_SYMBOL_LIMIT = 6

_GENERIC_EQUATION_TITLE = "数式"

# 掲載節・成立条件（element_context_presentation_redesign.md §5.1 / §8 Phase 2）。
# 節見出しは短いが、壊れた長文が入っていても snapshot を汚さないよう上限を置く。
_EQUATION_SECTION_LABEL_LIMIT = 80
_EQUATION_ASSUMPTION_LIMIT = 2
_EQUATION_ASSUMPTION_TEXT_LIMIT = 120


def _defined_symbol_names(eq: dict) -> set[str]:
    """この式が**定義する**記号名の集合（決定論。推測しない）。

    供給元は3系統:
      * ``semantics.defined_symbols[]``（asdict 形。``definition_status`` を持つ）
      * equations.json export 形の ``defined_symbols``（記号名の list[str]）
      * ``symbols[]`` 側が既に ``defined_here`` / ``definition_status`` を持つ場合
    """
    semantics = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
    defined: set[str] = set()
    for raw in _as_list(semantics.get("defined_symbols")) + _as_list(eq.get("defined_symbols")):
        if isinstance(raw, str):
            name = raw.strip()
            if name:
                defined.add(name)
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("symbol") or "").strip()
        if not name:
            continue
        if raw.get("defined_here") or str(raw.get("definition_status") or "") in ("defined", "redefined"):
            defined.add(name)
    for raw in _as_list(eq.get("symbols")):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("symbol") or "").strip()
        if not name:
            continue
        if raw.get("defined_here") or str(raw.get("definition_status") or "") in ("defined", "redefined"):
            defined.add(name)
    return defined


def _equation_symbol_meanings(eq: dict) -> list[dict]:
    """式の記号 → 意味を ``[{"symbol", "meaning"}]`` にまとめる。

    供給元は ``semantics.defined_symbols[].meaning``（issue #439 で SymbolRegistry
    から投影済み）と equations.json export 形の ``symbols[]`` の両方。**意味が解決
    できていない記号は推測で埋めずに落とす**（EH2 捏造禁止）。

    この式が定義する記号には ``defined_here: True`` を**その場合だけ**足す
    （element_context_presentation_redesign.md §5.1 のラベルラダー③「記号 + 役割の
    決定論合成」が読み取り時にも効くようにするための材料。定義でない記号にキーを
    足さないので、既存スナップショットとの差分は純増のみ）。
    """
    semantics = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
    defined_names = _defined_symbol_names(eq)
    out: list[dict] = []
    seen: set[str] = set()
    for raw in _as_list(eq.get("symbols")) + _as_list(semantics.get("defined_symbols")):
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip()
        meaning = str(raw.get("meaning") or "").strip()
        if not symbol or not meaning or symbol in seen:
            continue
        seen.add(symbol)
        entry = {"symbol": symbol, "meaning": _short_excerpt(meaning, limit=60)}
        if symbol in defined_names:
            entry["defined_here"] = True
        out.append(entry)
        if len(out) >= _EQUATION_SYMBOL_LIMIT:
            break
    return out


def _explanatory_text(value) -> str:
    """「意味の要約」として表示してよいテキストだけを返す（EH1）。

    ``semantics.summary`` は自由文のはずだが、TeX ソース由来の論文では式そのものが
    入っていることがある（実機 2026-08-02 の eq_tex_b14）。ホバー・外殻カードは
    ``semantic_kind`` を意味の一行として表示するため、TeX とみなせる値は空へ落とす
    （``_spoken_plain_text`` と同じ方針。表示用の latex / raw_text は温存する）。
    """
    text = str(value or "").strip()
    if not text or _looks_like_tex_math(text):
        return ""
    return text


def _equation_link_status(eq: dict) -> str:
    """式の前段リンク状態（``LINK_STATUSES`` の統制語彙キーのみ）。

    「もとになる式」が空欄である**理由**の材料（CP10「空は沈黙ではない」）。
    表示文への変換は ``core/element_vocab.py`` の ``link_status_fact`` が正本なので、
    スナップショットにはキーだけを載せる（訳語を焼かない, CP4）。未知値は空
    （fail-closed。内部語彙を画面へ漏らさない）。
    """
    semantics = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
    value = str(semantics.get("link_status") or eq.get("link_status") or "").strip()
    return value if value in LINK_STATUSES else ""


def _equation_assumptions(eq: dict) -> list[str]:
    """式の成立条件（``semantics.assumptions``）を先頭2件だけ自然文で運ぶ。

    生 TeX の条件式は表示に使わない（EH1。数式の再掲になる）。
    """
    semantics = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
    raw_items = (
        _as_list(semantics.get("assumptions"))
        or _as_list(eq.get("assumptions"))
        or _as_list(eq.get("local_assumptions"))
    )
    out: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if not text or _looks_like_tex_math(text):
            continue
        cut = _short_excerpt(text, limit=_EQUATION_ASSUMPTION_TEXT_LIMIT)
        if cut:
            out.append(cut)
        if len(out) >= _EQUATION_ASSUMPTION_LIMIT:
            break
    return out


def _equation_semantic_projection(eq: dict) -> dict:
    """式の「役割 / 意味の要約 / 記号の意味 / 掲載節 / 成立条件」を平坦化する。

    ``role_in_argument`` は A層の統制語彙（``ROLE_IN_ARGUMENT_VOCAB``）のまま運ぶ。
    上流が空のときは equation_type から A層と同じ規則で導出するが、equation_type
    自体が無い / unknown な式（チャンク由来の fallback formula 等）では **導出せず
    空にする** — 既定値の「前提」を勝手に貼らない（EH2 捏造禁止）。

    ``section_label`` / ``link_status`` / ``assumptions`` は
    element_context_presentation_redesign.md §8 Phase 2 の追加分で、**事実が無い
    ときはキー自体を載せない**（section_id の生値のような内部 ID は出さないし、
    空欄のキーで snapshot を膨らませない）。``section_label`` は
    ``_collect_structured_content`` が document_structure の節見出しへ解決済みの
    値だけを使う（ここで再解決はしない — 解決できなければ黙って省く）。
    """
    semantics = eq.get("semantics") if isinstance(eq.get("semantics"), dict) else {}
    role = str(eq.get("role_in_argument") or semantics.get("role_in_argument") or "").strip()
    if role not in ROLE_IN_ARGUMENT_VOCAB:
        equation_type = str(semantics.get("equation_type") or eq.get("equation_type") or "").strip()
        if equation_type and equation_type != "unknown":
            role = derive_role_in_argument(
                equation_type,
                input_equation_ids=[
                    str(i) for i in _as_list(
                        semantics.get("input_equation_ids") or eq.get("input_equation_ids")
                    )
                ],
                output_equation_ids=[
                    str(i) for i in _as_list(
                        semantics.get("output_equation_ids") or eq.get("output_equation_ids")
                    )
                ],
            )
        else:
            role = ""
    semantic_kind = _explanatory_text(eq.get("semantic_kind") or semantics.get("summary"))
    projection = {
        "role_in_argument": role if role in ROLE_IN_ARGUMENT_VOCAB else "",
        "semantic_kind": _short_excerpt(semantic_kind, limit=120) if semantic_kind else "",
        "symbols": _equation_symbol_meanings(eq),
    }
    section_label = _short_excerpt(
        str(eq.get("section_label") or ""), limit=_EQUATION_SECTION_LABEL_LIMIT
    )
    if section_label:
        projection["section_label"] = section_label
    link_status = _equation_link_status(eq)
    if link_status:
        projection["link_status"] = link_status
    assumptions = _equation_assumptions(eq)
    if assumptions:
        projection["assumptions"] = assumptions
    return projection


def _equation_title_record(link: dict | None, formula: dict | None, normalized_id: str) -> dict:
    """既存スナップショットの平坦フィールドから**ラベルラダー用の最小レコード**を作る。

    ``build_topic_evidence_items`` は artifact ではなく freeze 済みの
    evidence_link / content_blocks から読むため、``labels.equation_label`` に渡せる
    形（role_in_argument / semantic_kind / symbols）へ組み直す。数式そのもの
    （latex / plain_text / raw_text）も渡すが、これは**候補から除外させるための
    安全網**であって表示候補ではない（EH1）。
    """
    link = link if isinstance(link, dict) else {}
    formula = formula if isinstance(formula, dict) else {}
    symbols = _as_list(link.get("symbols")) or _as_list(formula.get("symbols"))
    return {
        "equation_id": str(normalized_id or ""),
        "role_in_argument": link.get("role_in_argument") or formula.get("role_in_argument") or "",
        # 読み取り時にも TeX を落とす（投影時ガード導入前に freeze された
        # スナップショットは semantic_kind に生 TeX を持ち得る, EH1）。
        "semantic_kind": _explanatory_text(link.get("semantic_kind") or formula.get("semantic_kind")),
        "symbols": [s for s in symbols if isinstance(s, dict)],
        "link_status": link.get("link_status") or formula.get("link_status") or "",
        "latex": link.get("latex") or formula.get("latex") or "",
        "plain_text": link.get("plain_text") or formula.get("plain_text") or "",
        "raw_text": formula.get("raw_text") or "",
    }


def _equation_item_assumptions(link: dict | None, formula: dict | None) -> list[str]:
    """読み取り時の成立条件（先頭2件・自然文・生 TeX は落とす）。

    freeze 済みスナップショットは投影時点で既に2件へ絞られているが、旧データや
    手編集で長文・TeX が入っていても表示側へ流さないよう、読み取り時にも同じ
    フィルタを通す（``_spoken_plain_text`` と同じ「読み取り時の防衛」方針）。
    """
    link = link if isinstance(link, dict) else {}
    formula = formula if isinstance(formula, dict) else {}
    raw_items = _as_list(link.get("assumptions")) or _as_list(formula.get("assumptions"))
    out: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if not text or _looks_like_tex_math(text):
            continue
        cut = _short_excerpt(text, limit=_EQUATION_ASSUMPTION_TEXT_LIMIT)
        if cut:
            out.append(cut)
        if len(out) >= _EQUATION_ASSUMPTION_LIMIT:
            break
    return out


def _equation_display_title(
    label: str | None,
    normalized_id: str,
    *,
    record: dict | None = None,
) -> str:
    """数式アイテムの表示タイトル（EH2: 裸の内部 ID を出さない）。

    ラベル生成の正本は ``core/deliberation/labels.py`` のラベルラダー
    （element_context_presentation_redesign.md §5.1）で、ここは**その委譲**である
    — 第2のラベル生成器を持たない（CP1 / LE6′）。ラダーは
    ① 論文の式番号（``eq_2_7`` → 「式 (2.7)」。合成 ID ``eq_tex_b14`` は式番号では
    ないので採らない）② 記号 + 役割の決定論合成（「δ(t,x) を定義する式」）
    ③ ``semantic_kind`` の第1文 ④ 役割訳 + 「式」 ⑤ 一般ラベル「数式」の順。

    ``record`` は ``_equation_title_record`` が作る最小レコード（省略可）。
    ラダーが尽きた（一般ラベルに落ちた）ときに限り、人間可読な明示ラベル
    （内部 ID でも生 TeX でもないもの）を見出しに使う — 教員・A層が付けた読める
    ラベルを「数式」に潰さないため（P4）。
    """
    text = str(label or "").strip()
    data = dict(record) if isinstance(record, dict) else {}
    if text:
        data["label"] = text
    norm = str(normalized_id or "").strip()
    if norm and not data.get("equation_id"):
        data["equation_id"] = norm
    resolved = labels_mod.equation_label(data)
    if resolved.text and not resolved.unresolved:
        return resolved.text
    if text and not _looks_like_tex_math(text) and not labels_mod.is_internal_id_like(text):
        return text
    return _GENERIC_EQUATION_TITLE


def _spoken_plain_text(value) -> str:
    """読み下し（音声用テキスト）として使える plain_text だけを返す。

    チャンク由来の fallback formula は読み上げ原稿を持たず、freeze 時に
    plain_text へ原文 TeX がそのまま入っていることがある。数式ホバーは
    plain_text を「意味の要約 / 読み」として表示するため、TeX とみなせる
    値は空へ落とす（EH1/EH2 — 表示用の latex / raw_text は温存する）。
    """
    text = str(value or "")
    if _looks_like_tex_math(text):
        return ""
    return text


def _fill_equation_display_math(eq: dict) -> None:
    """equation dict にトップレベル latex / plain_text / raw_text を補完する（in-place）。

    既にトップレベルに非空の値があれば尊重し、無い場合のみネスト構造から導出して埋める。
    raw_text は表示用数式（latex/plain_text）が無い needs_math_review な式でも UI が
    「原文（未整形）」として表示できるよう、source_extraction.raw_text から補完する。
    """
    latex, plain_text = _equation_display_math(eq)
    if latex and not eq.get("latex"):
        eq["latex"] = latex
    if plain_text and not eq.get("plain_text"):
        eq["plain_text"] = plain_text
    if not eq.get("raw_text"):
        src = eq.get("source_extraction") if isinstance(eq.get("source_extraction"), dict) else {}
        raw = src.get("raw_text")
        if raw:
            eq["raw_text"] = raw


def _document_sections_by_id(artifacts: dict) -> dict[str, dict]:
    """``document_structure`` の sections を section_id で索引化する。

    節見出しの供給元はここだけ（``core/deliberation/context_lens.py`` の
    ``_sections_by_id`` と同じ規則）。artifact が無い / 形が違う場合は空 dict を
    返し、呼び出し側は掲載節を出さない（fail-soft）。
    """
    structure = _as_dict(artifacts.get("document_structure"))
    index: dict[str, dict] = {}
    for section in _as_list(structure.get("sections")):
        if isinstance(section, dict) and section.get("section_id"):
            index[str(section["section_id"])] = section
    return index


def _section_title(section: dict | None) -> str:
    if not isinstance(section, dict):
        return ""
    return str(section.get("title") or "").strip()


def _equation_section_id(eq: dict) -> str:
    """式の掲載 section_id（asdict 形 / equations.json export 形の両方に対応）。"""
    source_extraction = eq.get("source_extraction") if isinstance(eq.get("source_extraction"), dict) else {}
    for holder in (source_extraction, eq):
        location = holder.get("source_location")
        if isinstance(location, dict):
            section_id = str(location.get("section_id") or "").strip()
            if section_id:
                return section_id
    return str(eq.get("section_id") or "").strip()


def _collect_structured_content(artifacts_by_doc: dict[str, dict]) -> dict:
    mapping_topics: list[dict] = []
    components: dict[str, dict] = {}
    equations: dict[str, dict] = {}
    claims: dict[str, dict] = {}
    evidence: dict[str, dict] = {}
    # claim_id -> [{"document_id", "figure_key", "caption"}] の逆引き索引。図⇄claim
    # リンクの正本は FigureRecord.linked_claim_ids の一箇所（figure_concept_linking_design
    # の決定）であり、claim 側には figure_ids が populate されないため、ここで明示的に
    # 逆方向へたどる（Phase 4 §7.1）。
    figure_claim_links: dict[str, list[dict]] = {}

    for document_id, artifacts in artifacts_by_doc.items():
        mapping = _as_dict(artifacts.get("course_mapping"))
        for topic in _as_list(mapping.get("topics")):
            if isinstance(topic, dict):
                topic = dict(topic)
                topic.setdefault("document_id", document_id)
                mapping_topics.append(topic)

        # narrative_annotator（#360）は TheoryOperationGraph 主グラフノードの
        # narrative_role（この段階が論文の主張に何を寄与するか、1〜2文）を
        # component_id 名前空間で component_assembly と共有する
        # （component_evidence_redesign.md Phase 1）。この接続の説明文を
        # components 投影に持ち込み、チップ展開時の「この論文の中での位置づけ」に
        # 使う。artifact が dict でない/欠落時は _as_dict/_as_list が空を返すため
        # 静かにスキップされる（防御的）。narrative の confidence（生値）はここでは
        # 使わない（投影に混ぜない）。
        narrative_artifact = _as_dict(artifacts.get("narrative_annotator"))
        narrative_role_by_component_id: dict[str, str] = {}
        for node in _as_list(narrative_artifact.get("node_narratives")):
            if not isinstance(node, dict):
                continue
            comp_id = str(node.get("component_id") or "").strip()
            role = str(node.get("narrative_role") or "").strip()
            if comp_id and role:
                narrative_role_by_component_id[comp_id] = role

        assembly = _as_dict(artifacts.get("component_assembly"))
        for component in _as_list(assembly.get("components")):
            if isinstance(component, dict) and component.get("component_id"):
                item = dict(component)
                item.setdefault("document_id", document_id)
                comp_id = str(item["component_id"])
                narrative_role = narrative_role_by_component_id.get(comp_id)
                if narrative_role:
                    item["narrative_role"] = narrative_role
                components[comp_id] = item

        # 掲載節（element_context_presentation_redesign.md §5.1 / RC12）。式は
        # source_location.section_id を持つが、節**見出し**は document_structure に
        # しかない。ここで解決できたものだけを ``section_label`` として equation に
        # 載せ、解決できない section_id は生値を出さずに黙って落とす。
        sections_by_id = _document_sections_by_id(artifacts)

        eq_artifact = _as_dict(artifacts.get("equation_semantics"))
        for equation in _as_list(eq_artifact.get("equations")):
            if not isinstance(equation, dict):
                continue
            eq = dict(equation)
            eq.setdefault("document_id", document_id)
            section_label = _section_title(sections_by_id.get(_equation_section_id(eq)))
            if section_label:
                eq["section_label"] = section_label
            # equation_semantics の生成物はネスト構造（source_extraction / reconstruction）で
            # 保存されており、トップレベルに latex / plain_text を持たない。後段の
            # _topic_evidence_links は equation.get("latex") を参照するため、ここで
            # to_equations_export() と同じ規則でフラット化して埋める（欠落していると
            # 根拠リンクの本文が空になり ![[equation:id]] が「未解決」になる）。
            _fill_equation_display_math(eq)
            eq_id = str(eq.get("equation_id") or eq.get("id") or "")
            if eq_id:
                equations[eq_id] = eq

        # claims.json (ClaimObjectBuilder) を取り込み、claim を根拠アイテム化できるようにする。
        claim_artifact = _as_dict(artifacts.get("claim_object_builder"))
        for claim in _as_list(claim_artifact.get("claims")):
            if isinstance(claim, dict) and claim.get("claim_id"):
                item = dict(claim)
                item.setdefault("document_id", document_id)
                claims[str(item["claim_id"])] = item

        # evidence_registry (EvidenceRegistryBuilder) を取り込み、PDF 原文スパンを
        # kind=source の根拠アイテム化できるようにする。
        evidence_artifact = _as_dict(artifacts.get("evidence_registry"))
        for record in _as_list(evidence_artifact.get("records")):
            if isinstance(record, dict) and record.get("evidence_id"):
                item = dict(record)
                item.setdefault("document_id", document_id)
                evidence[str(item["evidence_id"])] = item

        # figure_table_semantics (FigureRecord) を claim_id → 図 の逆引き索引にする。
        # FigureRecord.figure_id は caption ラベルをそのまま使う表記（例 'fig_3.3'、
        # ピリオド保持）で振られる一方、document_figures.figure_key は
        # _normalize_figure_key により非英数字→アンダースコア正規化された表記
        # （例 'fig_3_3'）になる（バグB）。両者は素朴な文字列一致では章番号付き
        # ラベルで一致しないため、ここでは document_figures.id (UUID) の解決を先送り
        # し figure_key のまま保持しておいて、_topic_evidence_links 側で
        # figures_index（_resolve_figure_ref 経由、normalize_figure_join_key で
        # 両辺を正規化してから突合）を使って解決する。
        fig_tbl_artifact = _as_dict(artifacts.get("figure_table_semantics"))
        for fig in _as_list(fig_tbl_artifact.get("figures")):
            if not isinstance(fig, dict):
                continue
            figure_key = str(fig.get("figure_id") or "").strip()
            if not figure_key:
                continue
            caption = str(fig.get("caption") or "")
            for claim_id in _as_list(fig.get("linked_claim_ids")):
                claim_id = str(claim_id or "").strip()
                if not claim_id:
                    continue
                figure_claim_links.setdefault(claim_id, []).append({
                    "document_id": document_id,
                    "figure_key": figure_key,
                    "caption": caption,
                })

    return {
        "mapping_topics": mapping_topics,
        "components": components,
        "equations": equations,
        "claims": claims,
        "evidence": evidence,
        "figure_claim_links": figure_claim_links,
    }


def _load_chunks(session, material_ids: list[str]) -> dict[str, list[dict]]:
    if not material_ids:
        return {}
    params = {f"mid_{idx}": mid for idx, mid in enumerate(material_ids)}
    placeholders = ", ".join(f":mid_{idx}" for idx in range(len(material_ids)))
    rows = session.execute(
        sa_text(f"""
            SELECT id::text, material_id, chunk_index, display_text, text, formulas, chapter, section
            FROM chunks
            WHERE material_id IN ({placeholders})
            ORDER BY chunk_index ASC
        """),
        params,
    ).fetchall()
    chunks: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        material_id = row[1] or ""
        chunks[material_id].append({
            "id": row[0],
            "material_id": material_id,
            "chunk_index": row[2],
            "text": (row[3] or row[4] or "").strip(),
            "formulas": row[5] if isinstance(row[5], list) else [],
            "chapter": row[6],
            "section": row[7],
        })
    return chunks


def _load_document_figures_index(session, document_ids: list[str]) -> dict[str, dict]:
    """図のコース流通（Phase 4 §7.1）向けに ``document_figures`` を軽量索引化する。

    ``figure_images.load_document_figures`` は図配信 API 向けの重い列
    （bbox / inner_labels / analysis_profile 等）まで読むため、根拠リンク生成のような
    id 解決だけの用途には使わず、専用の軽量クエリにする。索引は2通りのキーを持つ:

    - ``figure_id``（``document_figures.id`` の UUID 文字列）: component の
      ``source_scope.figure_id`` から直接解決できる経路用
    - ``"{document_id}::{normalize_figure_join_key(figure_key)}"``: figure_table_semantics の
      ``FigureRecord.linked_claim_ids`` 逆引き（figure_key しか分からない）経路用。
      ``document_figures.figure_key`` は既に ``normalize_figure_join_key`` と同じ規則
      （非英数字→アンダースコア）で生成されているはずだが、突合側（呼び出し元）が
      ``fig_3.3`` のようなピリオド保持表記を渡してくることがあるため、ここでも
      正規化してから索引キーを合成し、突合を冪等にする（バグB修正）。
    """
    if not document_ids:
        return {}
    params = {f"did_{idx}": did for idx, did in enumerate(document_ids)}
    placeholders = ", ".join(f":did_{idx}" for idx in range(len(document_ids)))
    rows = session.execute(
        sa_text(f"""
            SELECT id::text, document_id, figure_key, caption_text
            FROM document_figures
            WHERE document_id IN ({placeholders})
              AND status = 'extracted'
        """),
        params,
    ).fetchall()
    index: dict[str, dict] = {}
    for row in rows:
        figure_id = str(row[0] or "")
        document_id = str(row[1] or "")
        figure_key = str(row[2] or "")
        caption = str(row[3] or "")
        if not figure_id:
            continue
        item = {
            "figure_id": figure_id,
            "figure_key": figure_key,
            "document_id": document_id,
            "caption": caption,
        }
        index[figure_id] = item
        normalized_key = normalize_figure_join_key(figure_key)
        if document_id and normalized_key:
            index[f"{document_id}::{normalized_key}"] = item
    return index


def _resolve_figure_ref(
    figures_index: dict[str, dict],
    *,
    figure_id: str | None = None,
    document_id: str | None = None,
    figure_key: str | None = None,
) -> dict | None:
    """figures_index から図参照を解決する（figure_id 優先、無ければ document_id+figure_key）。

    figure_key は ``FigureRecord.figure_id``（caption ラベル生。ピリオド保持、
    例 ``fig_3.3``）と ``document_figures.figure_key``（非英数字→アンダースコア正規化、
    例 ``fig_3_3``）とで表記が異なるため（バグB）、``normalize_figure_join_key`` で
    正規化してから ``_load_document_figures_index`` と同じキー規則で lookup する。
    """
    figure_id = str(figure_id or "").strip()
    if figure_id and figure_id in figures_index:
        return figures_index[figure_id]
    document_id = str(document_id or "").strip()
    normalized_key = normalize_figure_join_key(figure_key)
    if document_id and normalized_key:
        return figures_index.get(f"{document_id}::{normalized_key}")
    return None


def _enrich_topics(
    topics: list[dict],
    bundle: dict,
    chunks_by_material: dict[str, list[dict]],
    figures_index: dict[str, dict] | None = None,
) -> list[dict]:
    enriched: list[dict] = []
    all_chunks = [chunk for chunks in chunks_by_material.values() for chunk in chunks]
    figures_index = figures_index or {}
    for index, raw_topic in enumerate(topics):
        topic = dict(raw_topic) if isinstance(raw_topic, dict) else {"title": str(raw_topic)}
        mapping, mapping_confidence = _best_mapping(topic, bundle["mapping_topics"], index)
        component_ids = _component_ids_for_topic(topic, mapping, bundle["components"])
        components = [bundle["components"][cid] for cid in component_ids if cid in bundle["components"]]
        equations = _equations_for_components(components, bundle["equations"])
        fallback_chunk = _fallback_chunk_for_topic(all_chunks, index, len(topics))

        summary = _topic_summary(mapping, components, fallback_chunk)
        learning_objectives = _as_str_list(mapping.get("learning_objectives") if mapping else [])
        assessment_prompts = _as_str_list(mapping.get("assessment_prompts") if mapping else [])
        prerequisite_concepts = _as_str_list(mapping.get("prerequisite_concepts") if mapping else [])
        teaching_takeaways = _as_str_list([c.get("teaching_takeaway") for c in components if c.get("teaching_takeaway")])
        evidence_ids = _linked_ids(components, "linked_evidence_ids")
        evidence_links = _topic_evidence_links(
            components,
            equations,
            bundle.get("claims") or {},
            bundle.get("evidence") or {},
            mapping_confidence,
            figures_index=figures_index,
            figure_claim_links=bundle.get("figure_claim_links") or {},
        )

        fallback_formulas = _fallback_formulas(fallback_chunk)

        topic.update({
            "summary": summary,
            "content": _compose_topic_content(
                summary,
                learning_objectives,
                components,
                equations,
                assessment_prompts,
            ),
            "content_blocks": _content_blocks(
                summary,
                learning_objectives,
                components,
                equations,
                assessment_prompts,
                fallback_formulas,
            ),
            "learning_objectives": learning_objectives,
            "prerequisite_concepts": prerequisite_concepts,
            "blackbox_policy": mapping.get("blackbox_policy") if isinstance(mapping, dict) else {},
            "assessment_prompts": assessment_prompts,
            "expected_misconceptions": _as_str_list(mapping.get("expected_misconceptions") if mapping else []),
            "linked_component_ids": component_ids,
            "linked_equation_ids": [str(e.get("equation_id") or e.get("id")) for e in equations if e.get("equation_id") or e.get("id")],
            "linked_claim_ids": _linked_ids(components, "linked_claim_ids"),
            "source_evidence_ids": evidence_ids,
            "evidence_links": evidence_links,
            "teaching_takeaways": teaching_takeaways,
            "material_chunk_ids": [fallback_chunk["id"]] if fallback_chunk else [],
            "source_excerpt": _short_excerpt(fallback_chunk.get("text", "")) if fallback_chunk else "",
            "content_source": "agent_mapping" if mapping_confidence != "none" or components else "source_excerpt",
            "content_confidence": mapping_confidence,
        })
        enriched.append(topic)
    return enriched


# TeX 判定の実装は ``core/text_excerpt.py`` へ移設した（切り詰め正本 ``excerpt`` と
# 同じ場所に置き、``core/deliberation/labels.py`` から course_content_builder への
# 相互参照を避けるため。element_context_presentation_redesign.md Phase 0）。
# 本モジュールは公開名 ``looks_like_tex_math`` を再エクスポートするだけで、import 面
# （``from core.course_content_builder import looks_like_tex_math``）は不変。
#
# 旧称（本モジュール内の呼び出し・既存テスト用）。判定規則の正本は
# ``core/text_excerpt.looks_like_tex_math`` で、学習者向け射影
# （``core/element_context.py``）もこれを import して使う — TeX 判定を第2実装で
# コピペしない（equation_context_panel_display_design.md §5.1）。
_looks_like_tex_math = looks_like_tex_math


def _topic_evidence_links(
    components: list[dict],
    equations: list[dict],
    claims_by_id: dict[str, dict],
    evidence_by_id: dict[str, dict],
    confidence: str,
    *,
    figures_index: dict[str, dict] | None = None,
    figure_claim_links: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Build the authoritative 根拠リンク list consumed by the lecture studio UI.

    Surfaces component / equation / claim / source / figure references with
    summaries so the frontend can resolve `![[component:id]]` /
    `![[equation:id]]` / `![[claim:id]]` / `![[source:id]]` /
    `![[figure:id]]` embeds. Without this, `topic.evidence_links` stayed empty
    and any claim/source/figure reference rendered as "未解決".

    Figures (kind='figure', Phase 4 §7.1) are derived deterministically via two
    routes, never invented: (1) components whose `source_scope.figure_id` /
    `figure_key` point at a `document_figures` row (apparatus/device candidate
    components), and (2) claims linked to a figure through
    `FigureRecord.linked_claim_ids` (figure_concept_linking_design's single
    source of truth), resolved to the concrete `document_figures.id` UUID via
    `figures_index`. `figure_claim_links` is the claim_id -> figure reverse
    index built by `_collect_structured_content`.
    """
    figures_index = figures_index or {}
    figure_claim_links = figure_claim_links or {}
    links: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(
        kind: str,
        target_id: str,
        summary: str,
        support_role: str,
        *,
        latex: str | None = None,
        plain_text: str | None = None,
        label: str | None = None,
        extra: dict | None = None,
    ) -> None:
        target_id = str(target_id or "").strip()
        if not target_id:
            return
        key = (kind, target_id)
        if key in seen:
            return
        seen.add(key)
        # 生 TeX の summary を 220 字で機械的に切ると数式が途中で壊れて描画不能に
        # なる（例: "\end{a..."）。TeX とみなせる summary は latex の有無に関わらず
        # summary から除去し（EH1: 説明欄に生 TeX を漏らさない）、latex が未指定の
        # ときだけ latex へ移して UI に数式として描画させる（equation_quote に
        # 限らず全 kind に効く汎用ガード）。
        if _looks_like_tex_math(summary):
            if not latex:
                latex = summary.strip()
            summary = ""
        link = {
            "kind": kind,
            "target_id": target_id,
            "summary": _short_excerpt(summary, limit=220) if summary else "",
            "support_role": support_role,
            "confidence": confidence,
        }
        # 数式は latex / label を持たせ、UI が生 LaTeX 文字列ではなく数式として
        # 描画できるようにする（summary はあくまで人間向けの説明にする）。
        if latex:
            link["latex"] = latex
        if plain_text:
            link["plain_text"] = plain_text
        if label:
            link["label"] = label
        if extra:
            for extra_key, extra_value in extra.items():
                if extra_value not in (None, ""):
                    link[extra_key] = extra_value
        links.append(link)

    for component in components:
        add(
            "component",
            component.get("component_id") or component.get("id") or "",
            component.get("summary") or component.get("teaching_takeaway") or "",
            "support",
            label=component.get("label") or "",
        )

    for equation in equations:
        semantics = equation.get("semantics") if isinstance(equation.get("semantics"), dict) else {}
        # summary は生 LaTeX ではなく、意味要約 / ラベルなど人間向けの説明にする。
        eq_summary = semantics.get("summary") or equation.get("label") or ""
        # 役割 / 意味の要約 / 記号の意味も根拠リンクに載せる（equation_hover_content_design.md
        # §5 Phase 2）。content_blocks 側と同じ投影を使い、空の値は add() が落とす。
        eq_semantics = _equation_semantic_projection(equation)
        add(
            "equation",
            equation.get("equation_id") or equation.get("id") or "",
            eq_summary,
            "equation",
            latex=equation.get("latex") or equation.get("latex_canonical") or equation.get("normalized_latex") or "",
            plain_text=equation.get("plain_text") or "",
            label=equation.get("label") or "",
            extra={
                "role_in_argument": eq_semantics["role_in_argument"],
                "semantic_kind": eq_semantics["semantic_kind"],
                "symbols": eq_semantics["symbols"] or None,
                # §8 Phase 2: 掲載節・前段リンク状態・成立条件。事実が無いキーは
                # add() が落とす（空欄のキーを snapshot に増やさない）。
                "section_label": eq_semantics.get("section_label") or None,
                "link_status": eq_semantics.get("link_status") or None,
                "assumptions": eq_semantics.get("assumptions") or None,
            },
        )

    # claim は component の linked_claim_ids 経由で参照される。claims.json に実体が
    # あるものだけを根拠化する（存在しない id はリンクにしない）。
    for claim_id in _linked_ids(components, "linked_claim_ids"):
        claim = claims_by_id.get(str(claim_id))
        if not claim:
            continue
        add(
            "claim",
            claim_id,
            claim.get("normalized_text") or claim.get("text") or "",
            str(claim.get("support_status") or "claim"),
        )

    # source span は component の linked_evidence_ids 経由で参照される。
    # evidence_registry に実体がある PDF 原文引用だけを kind=source で根拠化する。
    for evidence_id in _linked_ids(components, "linked_evidence_ids"):
        record = evidence_by_id.get(str(evidence_id))
        if not record:
            continue
        evidence_text = str(record.get("evidence_text") or "")
        evidence_role = str(record.get("evidence_role") or "source_quote")
        # equation_quote の evidence_text は生 TeX（TeX アーカイブ由来では
        # \begin{aligned}...\end{aligned} 全体）。summary 経由にすると 220 字で
        # 切り詰められて TeX が壊れるため、全文を latex として渡し UI に数式
        # 描画させる。role が別でも本文が TeX なら同様に扱う。
        if evidence_role == "equation_quote" or _looks_like_tex_math(evidence_text):
            add("source", evidence_id, "", evidence_role, latex=evidence_text)
        else:
            add("source", evidence_id, evidence_text, evidence_role)

    def add_figure(figure_ref: dict | None) -> None:
        if not figure_ref:
            return
        figure_id = str(figure_ref.get("figure_id") or "")
        if not figure_id:
            return
        add(
            "figure",
            figure_id,
            figure_ref.get("caption") or "",
            "figure",
            extra={
                "figure_id": figure_id,
                "figure_key": figure_ref.get("figure_key") or "",
                "document_id": figure_ref.get("document_id") or "",
                "caption": figure_ref.get("caption") or "",
            },
        )

    # 経路1: 装置候補コンポーネントは source_scope.figure_id / figure_key で図に
    # 直接紐づく（apparatus_components.py が付与）。figures_index で
    # document_figures.id (UUID) / caption を解決する。
    for component in components:
        source_scope = component.get("source_scope")
        if not isinstance(source_scope, dict):
            continue
        comp_figure_id = source_scope.get("figure_id")
        comp_figure_key = source_scope.get("figure_key")
        if not comp_figure_id and not comp_figure_key:
            continue
        add_figure(_resolve_figure_ref(
            figures_index,
            figure_id=comp_figure_id,
            document_id=component.get("document_id"),
            figure_key=comp_figure_key,
        ))

    # 経路2: component の linked_claim_ids から、その claim を参照している図
    # （FigureRecord.linked_claim_ids の逆引き、figure_claim_links）を辿る。
    # claim → 図の対応が無い（本文メンション無し）図は正直にスキップする（P4）。
    for claim_id in _linked_ids(components, "linked_claim_ids"):
        for figure_link in figure_claim_links.get(str(claim_id), []):
            resolved = _resolve_figure_ref(
                figures_index,
                document_id=figure_link.get("document_id"),
                figure_key=figure_link.get("figure_key"),
            )
            if resolved and not resolved.get("caption") and figure_link.get("caption"):
                resolved = {**resolved, "caption": figure_link["caption"]}
            add_figure(resolved)

    return links


# ---------------------------------------------------------------------------
# 教材埋め込み ``![[kind:id]]`` の学習画面向け解決 DTO（evidence_items）
# ---------------------------------------------------------------------------
#
# 授業用ドラフト（admin-lecture-studio.js の lsRenderCourseMaterialPreview /
# lsTopicEvidenceItems）は topic の evidence_links / content_blocks /
# linked_component_ids / source_excerpt を使って全 kind の ``![[kind:id]]`` を
# クライアント側で解決していた。一方 get_topic_material は本文と数式・図しか渡さず、
# 学習画面レンダラ（app.js renderMaterialChunk）は equation / figure 以外を常に
# 「未解決」表示にしていた（同じ教材 DSL に対し解決コンテキストが画面ごとに違う不整合）。
#
# build_topic_evidence_items は admin と同一の抽出・正規化規則で、学習者へ公開して
# よい参照だけから読み取り専用 DTO を組み立てる。DB 上の任意 ID をクライアント入力
# から自由に解決する経路は作らない（ここに現れる参照＝そのトピックで公開済みの参照）。

_EVIDENCE_ID_EQ_PREFIX_RE = re.compile(r"^(?:eq_){2,}", re.IGNORECASE)


def normalize_evidence_id(value: object) -> str:
    """教材埋め込み ``![[kind:id]]`` の id を正規化する（両画面共通の正本規則）。

    frontend の ``lsNormalizeEvidenceId``（admin-lecture-studio.js）と
    ``normalizeMaterialEvidenceId``（app.js）と**同一仕様**にする:
    空白除去 → 先頭 ``[[`` / 末尾 ``]]`` を最大2回剥がす → 旧二重 ``eq_``
    プレフィックス（``eq_eq_F2`` → ``eq_F2``）を畳み込む。これにより同じ ID が
    両画面で同じ解決キーになる（差分は test_topic_material_evidence_items が固定する）。
    """
    s = str(value if value is not None else "").strip()
    for _ in range(2):
        if s.startswith("[["):
            s = s[2:]
        if s.endswith("]]"):
            s = s[:-2]
    s = s.strip()
    s = _EVIDENCE_ID_EQ_PREFIX_RE.sub("eq_", s)
    return s


def _topic_content_block_formulas(topic: dict) -> list[dict]:
    """``topic.content_blocks`` の equations を UI 数式アイテムへ変換する。

    admin ``lsTopicFormulas`` / routes の ``_topic_formulas_from_content_blocks`` と
    同じ規則（latex が無くても plain_text / raw_text があれば残す）。
    """
    formulas: list[dict] = []
    for block in (topic or {}).get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "equations":
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            if not (item.get("latex") or item.get("plain_text") or item.get("raw_text")):
                continue
            formulas.append({
                "id": item.get("equation_id") or f"TOPIC_FORMULA_{len(formulas)}",
                "label": item.get("label") or "",
                "latex": item.get("latex") or "",
                "plain_text": item.get("plain_text") or "",
                "raw_text": item.get("raw_text") or "",
                # equation_hover_content_design.md §5 Phase 2: 説明材料を透過する。
                # 再生成前のコース（旧スナップショット）はキーを持たないので空になる。
                "role_in_argument": item.get("role_in_argument") or "",
                "semantic_kind": item.get("semantic_kind") or "",
                "symbols": [
                    s for s in _as_list(item.get("symbols")) if isinstance(s, dict)
                ][:_EQUATION_SYMBOL_LIMIT],
                # element_context_presentation_redesign.md §8 Phase 2 の追加分。
                "section_label": item.get("section_label") or "",
                "link_status": item.get("link_status") or "",
                "assumptions": [
                    str(a).strip() for a in _as_list(item.get("assumptions")) if str(a or "").strip()
                ][:_EQUATION_ASSUMPTION_LIMIT],
            })
    return formulas


def _topic_component_block_index(topic: dict) -> dict[str, dict]:
    """``content_blocks`` の components ブロックを component_id（正規化後）で索引化する。

    ``build_topic_evidence_items`` が evidence_links 経由 / linked_component_ids
    フォールバックの両経路で rich な component 投影
    （label / narrative_role / document_id / supports）をマージするための共通索引。
    """
    index: dict[str, dict] = {}
    for block in (topic or {}).get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "components":
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            norm = normalize_evidence_id(item.get("component_id"))
            if norm:
                index[norm] = item
    return index


def _topic_component_block_item(topic: dict, component_id: object) -> dict:
    """``content_blocks`` の components ブロックから component_id 一致アイテムを引く
    （admin ``lsTopicComponentById`` と同じ探索）。無ければ空 dict。"""
    return _topic_component_block_index(topic).get(normalize_evidence_id(component_id), {})


def _merge_component_rich_projection(item: dict, rich: dict | None) -> None:
    """``_content_blocks`` の components 投影（rich item）を evidence item にマージする
    （component_evidence_redesign.md Phase 1 §4）。

    ``label`` / ``narrative_role`` / ``document_id`` と、下位接続の説明文付き投影
    ``supports``（preconditions/inputs/outputs/cautions/equations/claims/dependencies）
    を追加する。索引に無い component（``rich`` が falsy）には何も付けない —
    ``content_blocks`` に rich 投影が無い旧データでも壊れない後方互換。
    既存フィールド（kind/id/title/summary/role/confidence）は変更しない。
    """
    if not rich:
        return
    if rich.get("label"):
        item["label"] = rich["label"]
    if rich.get("narrative_role"):
        item["narrative_role"] = rich["narrative_role"]
    if rich.get("document_id"):
        item["document_id"] = rich["document_id"]
    item["supports"] = {
        "preconditions": rich.get("preconditions") or [],
        "inputs": rich.get("inputs") or [],
        "outputs": rich.get("outputs") or [],
        "cautions": rich.get("cautions") or [],
        "equations": rich.get("equations") or [],
        "claims": rich.get("claims") or [],
        "dependencies": rich.get("dependencies") or [],
    }


def build_topic_evidence_items(topic: dict) -> list[dict]:
    """学習画面向けの読み取り専用 evidence DTO を、トピックに公開済みの参照だけから
    決定論的に組み立てる（admin ``lsTopicEvidenceItems`` と同一規則）。

    学習画面が ``![[component:id]]`` / ``![[claim:id]]`` / ``![[source:id]]`` /
    ``![[equation:id]]`` / ``![[figure:id]]`` を解決するための材料。供給元は
    ``learning_courses.data`` に保存済みの ``topic.evidence_links`` /
    ``content_blocks`` / ``linked_component_ids`` / ``source_excerpt`` /
    ``summary`` のみ（コース再生成不要）。**DB 上の任意 ID をクライアント入力から
    解決しない** — ここに現れる参照＝そのトピックで公開してよい参照。

    各アイテムの共通フィールド: ``kind`` / ``id``（正規化済み）/ ``title`` /
    ``summary`` / ``role`` / ``confidence``。種別固有: equation は
    ``latex`` / ``plain_text`` / ``raw_text``、figure は ``figure_id`` /
    ``figure_key`` / ``caption``、latex を持つ source は ``latex``。component は
    ``content_blocks`` の rich 投影が解決できた場合に限り ``label`` /
    ``narrative_role`` / ``document_id`` / ``supports``（preconditions/inputs/
    outputs/cautions/equations/claims/dependencies）を追加で持つ
    （component_evidence_redesign.md Phase 1）。title は summary を流用せず、
    label（無ければ「論理コンポーネント」）にする。
    """
    topic = topic or {}
    items: list[dict] = []

    formula_by_norm: dict[str, dict] = {}
    for formula in _topic_content_block_formulas(topic):
        formula_by_norm[normalize_evidence_id(formula.get("id"))] = formula

    # content_blocks の components 投影（rich item: label / narrative_role /
    # document_id / preconditions・inputs・outputs・cautions / dependencies /
    # equations / claims）を component_id（正規化後）で索引化する。evidence_links
    # 経由 / linked_component_ids フォールバックの両経路がここから同じ規則でマージする
    # （component_evidence_redesign.md Phase 1 §4）。
    component_index = _topic_component_block_index(topic)

    confidence = str(topic.get("content_confidence") or "")

    # 1) evidence_links（component / equation / claim / source / figure）— 正本の根拠リンク。
    for link in topic.get("evidence_links") or []:
        if not isinstance(link, dict):
            continue
        kind = str(link.get("kind") or "source")
        raw_id = link.get("target_id") or link.get("id") or ""
        if kind == "equation":
            norm = normalize_evidence_id(raw_id)
            formula = formula_by_norm.get(norm) or {}
            # リンク生成時の TeX ガード導入前に freeze された既存コースの
            # evidence_link には TeX 混じりの summary が保存され得る。読み取り時にも
            # 落とし、ホバーの「意味の要約」行に生 TeX を出さない（EH1/EH2 —
            # 再 freeze なしで既存スナップショットに効かせる防衛）。
            summary = link.get("summary") or ""
            if _looks_like_tex_math(summary):
                summary = ""
            title_record = _equation_title_record(link, formula, norm)
            items.append({
                "kind": "equation",
                "id": norm,
                # 生 LaTeX をタイトルに出さない。裸の内部 ID も出さない（EH2）。
                # 見出しはラベルラダー（labels.equation_label）へ委譲する。
                "title": _equation_display_title(
                    link.get("label") or formula.get("label"), norm, record=title_record
                ),
                "summary": summary,
                "latex": link.get("latex") or formula.get("latex") or "",
                "plain_text": _spoken_plain_text(link.get("plain_text") or formula.get("plain_text")),
                "raw_text": formula.get("raw_text") or "",
                "role": link.get("support_role") or "equation",
                # equation_hover_content_design.md §3.1: 式を*読むための*材料。
                # ホバーはこれだけを見せ、式そのものを再掲しない（EH1）。
                "role_in_argument": title_record["role_in_argument"],
                "semantic_kind": title_record["semantic_kind"],
                "symbols": title_record["symbols"],
                # element_context_presentation_redesign.md §6 S1: 掲載節（1行）と、
                # 成立条件 / 「前段が無い理由」の材料（表示文への変換は
                # ElementVocab.link_status_fact が正本）。
                "section_label": link.get("section_label") or formula.get("section_label") or "",
                "link_status": title_record["link_status"],
                "assumptions": _equation_item_assumptions(link, formula),
                "confidence": link.get("confidence") or "",
            })
            continue
        if kind == "figure":
            fig_id = str(link.get("figure_id") or raw_id or "")
            caption = link.get("caption") or ""
            items.append({
                "kind": "figure",
                "id": normalize_evidence_id(fig_id),
                "figure_id": fig_id,
                "figure_key": link.get("figure_key") or "",
                "caption": caption,
                "title": "図: " + _short_excerpt(caption or fig_id or "図", limit=40),
                "summary": caption,
                "role": link.get("support_role") or "figure",
                "confidence": link.get("confidence") or "",
            })
            continue
        # source / claim / component。equation_quote など生 TeX を summary に持つ source は
        # latex に移して数式描画させる（admin と同じ切り詰め回避ガード）。
        latex = link.get("latex") or ""
        summary = link.get("summary") or ""
        if not latex and _looks_like_tex_math(summary):
            latex = summary
            summary = ""
        norm_id = normalize_evidence_id(raw_id)
        rich_component = component_index.get(norm_id) if kind == "component" else None
        if latex:
            title = link.get("label") or ("数式引用" if link.get("support_role") == "equation_quote" else "数式")
        elif kind == "component":
            # component_evidence_redesign.md Phase 1 §4: title に summary を流用
            # しない（同じ文が title/summary に二重表示される症状の解消）。
            title = link.get("label") or (rich_component or {}).get("label") or "論理コンポーネント"
        else:
            title = summary or str(raw_id) or kind
        item = {
            "kind": kind,
            "id": norm_id,
            "title": title,
            "summary": summary,
            "role": link.get("support_role") or "",
            "confidence": link.get("confidence") or "",
        }
        if latex:
            item["latex"] = latex
        if kind == "component":
            _merge_component_rich_projection(item, rich_component)
        items.append(item)

    # 2) linked_component_ids: evidence_links に無い component を content_blocks から補う。
    for cid in topic.get("linked_component_ids") or []:
        norm_cid = normalize_evidence_id(cid)
        rich_component = component_index.get(norm_cid)
        block_component = rich_component or {}
        title = block_component.get("label") or block_component.get("component_id") or ""
        summary = block_component.get("teaching_takeaway") or block_component.get("summary") or ""
        item = {
            "kind": "component",
            "id": norm_cid,
            "title": title or str(cid),
            "summary": summary or "このトピックに関連付けられた論理コンポーネントです。",
            "role": "support",
            "confidence": confidence,
        }
        _merge_component_rich_projection(item, rich_component)
        items.append(item)

    # 3) content_blocks の equations（本文が式を直接埋め込むケース）。
    for formula in _topic_content_block_formulas(topic):
        norm = normalize_evidence_id(formula.get("id"))
        spoken = _spoken_plain_text(formula.get("plain_text"))
        title_record = _equation_title_record(None, formula, norm)
        items.append({
            "kind": "equation",
            "id": norm,
            "title": _equation_display_title(formula.get("label"), norm, record=title_record),
            # summary に latex を入れない（EH1: 数式の再掲を作らない。かつて
            # ここが生 TeX の供給源になっていた）。意味の要約か読み下しだけを使う。
            "summary": title_record["semantic_kind"] or spoken,
            "latex": formula.get("latex") or "",
            "plain_text": spoken,
            "raw_text": formula.get("raw_text") or "",
            "role": "equation",
            "role_in_argument": title_record["role_in_argument"],
            "semantic_kind": title_record["semantic_kind"],
            "symbols": title_record["symbols"],
            "section_label": formula.get("section_label") or "",
            "link_status": title_record["link_status"],
            "assumptions": _equation_item_assumptions(None, formula),
            "confidence": confidence,
        })

    # 4) source_excerpt（原文抜粋）。
    if topic.get("source_excerpt"):
        chunk_ids = topic.get("linked_chunk_ids") or []
        excerpt_id = str(chunk_ids[0]) if chunk_ids else "excerpt"
        items.append({
            "kind": "source",
            "id": normalize_evidence_id(excerpt_id),
            "title": "原文抜粋",
            "summary": topic.get("source_excerpt") or "",
            "role": "source_span",
            "confidence": confidence,
        })

    # 5) トピック概要（``![[source:topic_summary]]`` / ``![[source:summary]]``）。
    #    course_content_builder のプロンプトが明示的に許可する参照（本文が概要を指す）。
    if topic.get("summary"):
        summary_text = _short_excerpt(str(topic.get("summary") or ""), limit=260)
        for sid in ("topic_summary", "summary"):
            items.append({
                "kind": "source",
                "id": sid,
                "title": "トピック概要",
                "summary": summary_text,
                "role": "summary",
                "confidence": confidence,
            })

    # dedup（``kind:normalized_id``、先勝ち — evidence_links を content_blocks より優先）。
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        key = f"{item['kind']}:{item['id']}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _best_mapping(topic: dict, mapping_topics: list[dict], index: int) -> tuple[dict, str]:
    if not mapping_topics:
        return {}, "none"
    title = str(topic.get("title") or "")
    for mapping in mapping_topics:
        if _norm_title(mapping.get("title")) == _norm_title(title):
            return mapping, "exact_title"
    scored = sorted(
        ((_overlap_score(title, f"{m.get('title', '')} {m.get('description', '')}"), m) for m in mapping_topics),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.18:
        return scored[0][1], "title_similarity"
    return {}, "none"


def _component_ids_for_topic(topic: dict, mapping: dict, components: dict[str, dict]) -> list[str]:
    ids = [str(cid) for cid in _as_list(mapping.get("linked_component_ids") if mapping else []) if cid]
    if ids:
        return list(dict.fromkeys(ids))
    title = str(topic.get("title") or "")
    scored = sorted(
        (
            (_overlap_score(title, f"{c.get('label', '')} {c.get('summary', '')} {c.get('teaching_takeaway', '')}"), cid)
            for cid, c in components.items()
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    return [cid for score, cid in scored[:3] if score >= 0.12]


def _equations_for_components(components: list[dict], equations: dict[str, dict]) -> list[dict]:
    ids = _linked_ids(components, "linked_equation_ids")
    for component in components:
        evidence_refs = component.get("evidence_refs") if isinstance(component.get("evidence_refs"), dict) else {}
        ids.extend(str(eid) for eid in _as_list(evidence_refs.get("equation_ids")) if eid)
    seen: set[str] = set()
    out: list[dict] = []
    for eq_id in ids:
        if eq_id in seen or eq_id not in equations:
            continue
        seen.add(eq_id)
        out.append(equations[eq_id])
    return out[:5]


def _topic_summary(mapping: dict, components: list[dict], fallback_chunk: dict | None) -> str:
    if isinstance(mapping, dict) and mapping.get("description"):
        return str(mapping["description"]).strip()
    for component in components:
        if component.get("summary"):
            return str(component["summary"]).strip()
    if fallback_chunk and fallback_chunk.get("text"):
        return _short_excerpt(fallback_chunk["text"], limit=420)
    return ""


def _fallback_chunk_for_topic(chunks: list[dict], topic_index: int, topic_count: int) -> dict | None:
    if not chunks:
        return None
    if topic_index < len(chunks):
        return chunks[topic_index]
    mapped_index = min(int(topic_index * len(chunks) / max(topic_count, 1)), len(chunks) - 1)
    return chunks[mapped_index] if mapped_index >= 0 else None


def _fallback_formulas(fallback_chunk: dict | None) -> list[dict]:
    if not fallback_chunk:
        return []
    formulas = fallback_chunk.get("formulas")
    return [dict(f) for f in formulas if isinstance(f, dict)] if isinstance(formulas, list) else []


def _compose_topic_content(
    summary: str,
    learning_objectives: list[str],
    components: list[dict],
    equations: list[dict],
    assessment_prompts: list[str],
) -> str:
    lines: list[str] = []
    if summary:
        lines.extend(["概要", summary, ""])
    if learning_objectives:
        lines.append("学習目標")
        lines.extend(f"- {item}" for item in learning_objectives)
        lines.append("")
    if components:
        lines.append("論理要素")
        for component in components[:5]:
            label = component.get("label") or component.get("component_id") or ""
            text = component.get("teaching_takeaway") or component.get("summary") or ""
            lines.append(f"- {label}: {text}" if text else f"- {label}")
        lines.append("")
    if equations:
        lines.append("重要な数式")
        for equation in equations:
            label = equation.get("label") or equation.get("equation_id") or equation.get("id") or ""
            latex = equation.get("latex") or equation.get("latex_canonical") or equation.get("normalized_latex") or ""
            lines.append(f"- {label}: {latex}" if label else f"- {latex}")
        lines.append("")
    if assessment_prompts:
        lines.append("確認問題")
        lines.extend(f"- {item}" for item in assessment_prompts)
    return "\n".join(line for line in lines if line is not None).strip()


def _component_field_refs(value: Any, limit: int = 8) -> list[dict]:
    """ComponentFieldRef のリスト（preconditions/inputs/outputs/cautions）を
    components 投影用に正規化する（component_evidence_redesign.md Phase 1 §5.1）。

    ``text`` が空の要素は落とす（説明文の無い裸参照を投影に持ち込まない）。
    ``claim_ids`` / ``equation_ids`` は文字列リストへ正規化する。
    """
    out: list[dict] = []
    for raw in _as_list(value):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "text": text,
            "claim_ids": [str(cid) for cid in _as_list(raw.get("claim_ids")) if str(cid).strip()],
            "equation_ids": [str(eid) for eid in _as_list(raw.get("equation_ids")) if str(eid).strip()],
        })
        if len(out) >= limit:
            break
    return out


def _component_dependency_refs(value: Any, limit: int = 8) -> list[dict]:
    """ComponentDependency のリストを components 投影用に正規化する。

    ``reason`` と ``targets``(component_refs) のどちらも空の要素は落とす
    （説明文の無いエッジを投影に持ち込まない）。
    """
    out: list[dict] = []
    for raw in _as_list(value):
        if not isinstance(raw, dict):
            continue
        targets = [str(t) for t in _as_list(raw.get("component_refs")) if str(t).strip()]
        reason = str(raw.get("reason") or "").strip()
        if not targets and not reason:
            continue
        out.append({
            "type": str(raw.get("dependency_type") or ""),
            "targets": targets,
            "reason": reason,
        })
        if len(out) >= limit:
            break
    return out


# ComponentRecord の役割別 equation id フィールド → role 語彙（順序が dedupe の優先順位）。
_COMPONENT_EQUATION_ROLE_FIELDS = (
    ("input_equation_ids", "input"),
    ("intermediate_equation_ids", "intermediate"),
    ("output_equation_ids", "output"),
    ("constraint_equation_ids", "constraint"),
    ("definition_equation_ids", "definition"),
)


def _component_equations_with_roles(component: dict, limit: int = 12) -> list[dict]:
    """ComponentRecord の役割別 equation id リストを role 付きでまとめる（初出優先で dedupe）。

    ``linked_equation_ids`` にあってどの役割リストにも無い id は role="linked" で
    末尾に追加する。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for field, role in _COMPONENT_EQUATION_ROLE_FIELDS:
        for eq_id in _as_list(component.get(field)):
            eq_id = str(eq_id or "").strip()
            if not eq_id or eq_id in seen:
                continue
            seen.add(eq_id)
            out.append({"id": eq_id, "role": role})
    for eq_id in _as_list(component.get("linked_equation_ids")):
        eq_id = str(eq_id or "").strip()
        if not eq_id or eq_id in seen:
            continue
        seen.add(eq_id)
        out.append({"id": eq_id, "role": "linked"})
    return out[:limit]


def _content_blocks(
    summary: str,
    learning_objectives: list[str],
    components: list[dict],
    equations: list[dict],
    assessment_prompts: list[str],
    fallback_formulas: list[dict] | None = None,
) -> list[dict]:
    blocks: list[dict] = []
    if summary:
        blocks.append({"type": "summary", "text": summary})
    if learning_objectives:
        blocks.append({"type": "learning_objectives", "items": learning_objectives})
    if components:
        blocks.append({
            "type": "components",
            "items": [
                {
                    "component_id": c.get("component_id"),
                    "label": c.get("label"),
                    "summary": c.get("summary"),
                    "teaching_takeaway": c.get("teaching_takeaway"),
                    # component_evidence_redesign.md Phase 1: 裸IDではなく接続の
                    # 説明文を運ぶ（narrative_role / 下位接続の text・reason 付き
                    # 投影 / 数式の役割分類）。
                    "narrative_role": c.get("narrative_role") or "",
                    "document_id": c.get("document_id") or "",
                    "preconditions": _component_field_refs(c.get("preconditions")),
                    "inputs": _component_field_refs(c.get("inputs")),
                    "outputs": _component_field_refs(c.get("outputs")),
                    "cautions": _component_field_refs(c.get("cautions")),
                    "dependencies": _component_dependency_refs(c.get("dependencies")),
                    "equations": _component_equations_with_roles(c),
                    "claims": [
                        str(cid) for cid in _as_list(c.get("linked_claim_ids")) if str(cid).strip()
                    ][:12],
                }
                for c in components[:5]
            ],
        })
    equation_items = [
        {
            "equation_id": e.get("equation_id") or e.get("id"),
            "label": e.get("label"),
            "latex": e.get("latex") or e.get("latex_canonical") or e.get("normalized_latex"),
            "plain_text": e.get("plain_text") or e.get("reading"),
            # Issue: 未解決の数式. Carry the raw extracted text so the UI can still
            # show a reading when LaTeX is absent (e.g. needs_math_review), instead
            # of an empty "LaTeX を取得できませんでした" box.
            "raw_text": e.get("raw_text") or e.get("text"),
            # equation_hover_content_design.md §5 Phase 2: 式を*読むための*材料
            # （役割 / 意味の要約 / 記号の意味）。無ければ空のまま運ぶ（UI 側が
            # IH8 の固定文へ落ちる）。
            **_equation_semantic_projection(e),
        }
        for e in equations
    ]
    existing_ids = {str(item.get("equation_id") or "") for item in equation_items if item.get("equation_id")}
    for f in fallback_formulas or []:
        formula_id = str(f.get("id") or f.get("equation_id") or "")
        latex = f.get("latex") or f.get("latex_canonical") or f.get("normalized_latex") or ""
        if not formula_id or not latex or formula_id in existing_ids:
            continue
        existing_ids.add(formula_id)
        equation_items.append({
            "equation_id": formula_id,
            "label": f.get("label") or "",
            "latex": latex,
            "plain_text": f.get("plain_text") or f.get("spoken") or f.get("reading") or "",
            "raw_text": f.get("raw_text") or f.get("text") or "",
            # チャンク由来の fallback formula は equation_semantics を通っていないため
            # 説明材料を持たない。空で運ぶ（推測で埋めない, EH2）。
            **_equation_semantic_projection(f),
        })
    if equation_items:
        blocks.append({
            "type": "equations",
            "items": equation_items,
        })
    if assessment_prompts:
        blocks.append({"type": "assessment_prompts", "items": assessment_prompts})
    return blocks


_COURSE_CONTENT_DRAFT_PROMPT = """あなたは大学教員の授業用ドラフト作成を支援するアシスタントです。

目的:
- コース全体の章立て、前後の説明順序、現在セクションが果たす教育上の役割を考慮する
- 現在セクションだけで閉じた説明にせず、前のセクションから何を受け取り、次へ何を渡すかを明確にする
- Claim / コンポーネント / 数式 / 原文抜粋は根拠として扱いつつ、理解に必須の数式は教材欄で明示的に使う
- 教材欄と本文説明を分離する

教材欄の表記:
- Markdown風の軽量表記を使う
- インライン数式は `$...$`
- ブロック数式は `$$...$$`
- `\(...\)` や `\[...\]` は使わず、必ず `$...$` / `$$...$$` を使う
- 埋め込みは `![[equation:id]]`, `![[component:id]]`, `![[claim:id]]`, `![[source:id]]`, `![[figure:id]]` の形式を使う
- 埋め込みに使ってよい kind と id の組み合わせは、根拠候補の `available_references` に列挙されたものだけ
- `available_references` に無い id を発明してはならない。また id 本来の kind を変えて埋め込んではならない（例: component の id を `claim:` や `equation:` で埋め込まない）
- 該当する根拠が `available_references` に無い場合は埋め込みを使わず、本文の言葉だけで説明する
- `![[source:id]]` は `available_references` にある source span の id、または原文抜粋(source_excerpt)を指す `![[source:topic_summary]]` のみを使う
- `![[figure:id]]` は `available_references` にある kind='figure' の id（供給された figure の id）のみ使用可。一覧に無い id を発明しない
- 根拠候補の `content_blocks` に equations がある場合、トピック理解に必須の式を `![[equation:id]]` で教材欄に埋め込む
- 数式を埋め込む前後には、その式が何を定義・変換・制約しているかを短く説明する
- 数式を単に列挙せず、授業の流れの中で使う

出力は必ずJSONのみ。
JSON文字列内のLaTeXバックスラッシュは必ず `\\Lambda` のように二重化してください。
形式:
{{
  "key_concepts": ["重要概念"],
  "student_material": {{"source_format": "eg-markdown-v1", "source_text": "学生に見せる教材"}},
  "spoken_script": "教員が話せる自然文。音声読み上げ対象。",
  "cautions": ["注意点"],
  "check_questions": [
    {{
      "question": "確認問題",
      "model_answer": "模範解答",
      "answer_requirements": ["回答に含めるべき要素"],
      "explanation": "難しい問題では、なぜそうなるかの解説"
    }}
  ]
}}

コース全体:
{course_json}

現在のセクション:
{topic_json}

前後関係:
{sequence_json}

根拠候補:
{evidence_json}

現在の下書き:
{draft_json}

依頼:
授業用ドラフトを作成してください。
"""


class _CourseContentStudentMaterialDraft(BaseModel):
    source_format: str = "eg-markdown-v1"
    source_text: str = ""


class _CourseContentCheckQuestionDraft(BaseModel):
    question: str = ""
    model_answer: str = ""
    answer_requirements: list[str] = Field(default_factory=list)
    explanation: str = ""


class _CourseContentDraftResponse(BaseModel):
    key_concepts: list[str] = Field(default_factory=list)
    student_material: _CourseContentStudentMaterialDraft = Field(default_factory=_CourseContentStudentMaterialDraft)
    spoken_script: str = ""
    cautions: list[str] = Field(default_factory=list)
    check_questions: list[_CourseContentCheckQuestionDraft] = Field(default_factory=list)


def _generate_course_topic_drafts(course: dict, topics: list[dict]) -> dict:
    if not topics:
        return {"drafted_topics": 0, "draft_errors": 0}
    course_context = _course_context_for_prompt(course, topics)
    params = get_llm_params("fast")
    drafted = 0
    errors = 0
    for index, topic in enumerate(topics):
        try:
            result = _generate_single_topic_draft(
                course_context=course_context,
                topics=topics,
                topic=topic,
                index=index,
                model=params["model"],
                reasoning_effort=params["reasoning_effort"],
            )
            topic["key_concepts"] = result["key_concepts"]
            topic["student_material"] = result["student_material"]
            topic["spoken_script"] = result["spoken_script"]
            topic["cautions"] = result["cautions"]
            topic["check_questions"] = result["check_questions"]
            topic["draft_source"] = "course_content_generation"
            drafted += 1
        except Exception:
            errors += 1
            logger.exception(
                "Failed to generate course topic draft: course=%s topic=%s",
                course.get("id") or course.get("title"),
                topic.get("id") or topic.get("title"),
            )
            _apply_deterministic_topic_draft_fallback(topic)
    return {"drafted_topics": drafted, "draft_errors": errors}


def _generate_single_topic_draft(
    *,
    course_context: dict,
    topics: list[dict],
    topic: dict,
    index: int,
    model: str,
    reasoning_effort: str | None,
) -> dict:
    prompt = _COURSE_CONTENT_DRAFT_PROMPT.format(
        course_json=json.dumps(course_context, ensure_ascii=False, indent=2)[:8000],
        topic_json=json.dumps(_topic_context_for_prompt(topic), ensure_ascii=False, indent=2)[:4000],
        sequence_json=json.dumps(_topic_sequence_context(topics, index), ensure_ascii=False, indent=2)[:3000],
        evidence_json=json.dumps(_topic_evidence_for_prompt(topic), ensure_ascii=False, indent=2)[:8000],
        draft_json=json.dumps(_topic_existing_draft(topic), ensure_ascii=False, indent=2)[:6000],
    )
    parsed: object
    try:
        parsed = generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=_CourseContentDraftResponse,
            model=model,
        )
    except Exception:
        raw = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            reasoning_effort=reasoning_effort,
        )
        parsed = _parse_topic_draft_json(raw)
    result = _normalize_topic_draft_response(parsed)
    _ensure_required_equations_in_material(result, topic)
    _ensure_required_figures_in_material(result, topic)
    _ensure_check_question_details(result, topic)
    if not any([
        result["key_concepts"],
        result["student_material"]["source_text"].strip(),
        result["spoken_script"].strip(),
        result["cautions"],
        result["check_questions"],
    ]):
        raise ValueError("empty draft response")
    return result


def _course_context_for_prompt(course: dict, topics: list[dict]) -> dict:
    chapters = course_chapters(course)
    grouped: dict[int, list[dict]] = defaultdict(list)
    for idx, topic in enumerate(topics):
        grouped[int(topic.get("chapter_index") or 0)].append({
            "order": idx + 1,
            "id": topic.get("id") or "",
            "title": topic.get("title") or "",
            "summary": topic.get("summary") or "",
            "prerequisites": topic.get("prerequisites") or [],
        })
    return {
        "title": course_title(course),
        "goal": course.get("goal") or course.get("description") or "",
        "target_audience": course.get("target_audience") or "",
        "chapters": [
            {
                "chapter_index": idx,
                "title": ch.get("title") if isinstance(ch, dict) else str(ch),
                "topics": grouped.get(idx, []),
            }
            for idx, ch in enumerate(chapters)
        ] or [{"chapter_index": 0, "title": "コース", "topics": grouped.get(0, [])}],
    }


def _topic_context_for_prompt(topic: dict) -> dict:
    return {
        "id": topic.get("id") or "",
        "title": topic.get("title") or "",
        "chapter_index": topic.get("chapter_index", 0),
        "prerequisites": topic.get("prerequisites") or [],
        "learning_objectives": topic.get("learning_objectives") or [],
        "expected_misconceptions": topic.get("expected_misconceptions") or [],
        "content_confidence": topic.get("content_confidence") or "",
    }


def _topic_sequence_context(topics: list[dict], index: int) -> dict:
    def compact(topic: dict | None) -> dict | None:
        if not topic:
            return None
        return {
            "id": topic.get("id") or "",
            "title": topic.get("title") or "",
            "summary": topic.get("summary") or "",
            "key_concepts": topic.get("key_concepts") or [],
        }

    return {
        "current_order": index + 1,
        "total_sections": len(topics),
        "previous": compact(topics[index - 1] if index > 0 else None),
        "current": compact(topics[index]),
        "next": compact(topics[index + 1] if index + 1 < len(topics) else None),
    }


def _topic_evidence_for_prompt(topic: dict) -> dict:
    # 埋め込みに使ってよい (kind, id) の組み合わせは、実際に解決可能な evidence_links
    # からそのまま導出する。これにより LLM が kind を取り違えたり存在しない id を
    # 発明したりするのを防ぐ（提示する候補＝解決できる候補、を保証する）。
    available_references = [
        {"kind": link.get("kind"), "id": link.get("target_id")}
        for link in (topic.get("evidence_links") or [])
        if isinstance(link, dict) and link.get("kind") and link.get("target_id")
    ]
    if topic.get("source_excerpt"):
        available_references.append({"kind": "source", "id": "topic_summary"})
    return {
        "summary": topic.get("summary") or "",
        "content": topic.get("content") or "",
        "content_blocks": topic.get("content_blocks") or [],
        "source_excerpt": topic.get("source_excerpt") or "",
        "linked_component_ids": topic.get("linked_component_ids") or [],
        "linked_equation_ids": topic.get("linked_equation_ids") or [],
        "linked_claim_ids": topic.get("linked_claim_ids") or [],
        "source_evidence_ids": topic.get("source_evidence_ids") or [],
        "available_references": available_references,
        "assessment_prompts": topic.get("assessment_prompts") or [],
        "teaching_takeaways": topic.get("teaching_takeaways") or [],
    }


def _topic_existing_draft(topic: dict) -> dict:
    return {
        "key_concepts": topic.get("key_concepts") or [],
        "student_material": topic.get("student_material") or {},
        "spoken_script": topic.get("spoken_script") or topic.get("content") or "",
        "cautions": topic.get("cautions") or [],
        "check_questions": topic.get("check_questions") or topic.get("assessment_prompts") or [],
    }


def _apply_deterministic_topic_draft_fallback(topic: dict) -> None:
    topic["key_concepts"] = _as_str_list(topic.get("learning_objectives"))[:6] or _tokens_as_list(topic.get("title") or "")
    topic["student_material"] = {
        "source_format": "eg-markdown-v1",
        "source_text": _fallback_student_material(topic),
    }
    topic["spoken_script"] = topic.get("content") or topic.get("summary") or ""
    topic["cautions"] = _as_str_list(topic.get("expected_misconceptions"))[:4]
    topic["check_questions"] = _detailed_check_questions(topic.get("assessment_prompts"), topic)
    topic["draft_source"] = "course_content_generation_fallback"


def _fallback_student_material(topic: dict) -> str:
    lines = []
    if topic.get("title"):
        lines.append("## " + str(topic["title"]))
    if topic.get("summary"):
        lines.extend(["", str(topic["summary"])])
    for eq_id in topic.get("linked_equation_ids") or []:
        lines.extend(["", f"![[equation:{eq_id}]]"])
    return "\n".join(lines).strip()


def _tokens_as_list(text: str) -> list[str]:
    return list(_tokens(text))[:6]


def _parse_topic_draft_json(raw: str) -> dict:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    candidates = [cleaned]
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match and match.group() != cleaned:
        candidates.append(match.group())
    for candidate in list(candidates):
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", candidate)
        if repaired != candidate:
            candidates.append(repaired)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


def _normalize_topic_draft_response(parsed: object) -> dict:
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump()
    if not isinstance(parsed, dict):
        parsed = {}
    student_material = parsed.get("student_material")
    if isinstance(student_material, BaseModel):
        student_material = student_material.model_dump()
    if not isinstance(student_material, dict):
        student_material = {"source_format": "eg-markdown-v1", "source_text": str(student_material or "")}
    return {
        "key_concepts": _clean_str_list(parsed.get("key_concepts")),
        "student_material": {
            "source_format": student_material.get("source_format") or "eg-markdown-v1",
            "source_text": str(student_material.get("source_text") or ""),
        },
        "spoken_script": str(parsed.get("spoken_script") or ""),
        "cautions": _clean_str_list(parsed.get("cautions")),
        "check_questions": _normalize_check_question_items(parsed.get("check_questions")),
    }


def _ensure_required_equations_in_material(result: dict, topic: dict) -> None:
    material = result.setdefault("student_material", {})
    if not isinstance(material, dict):
        material = {"source_format": "eg-markdown-v1", "source_text": str(material or "")}
        result["student_material"] = material
    material["source_format"] = material.get("source_format") or "eg-markdown-v1"
    source_text = str(material.get("source_text") or "").strip()
    required = _required_equation_items(topic)
    missing = [
        item for item in required
        if item.get("equation_id") and f"![[equation:{item['equation_id']}]]" not in source_text
    ]
    if not missing:
        material["source_text"] = source_text
        return
    lines = [source_text] if source_text else []
    lines.extend(["", "### この節で使う数式"])
    for item in missing:
        eq_id = str(item.get("equation_id") or "")
        label = str(item.get("label") or eq_id)
        description = _equation_material_description(item)
        lines.append(f"- {label}: {description}")
        # 未解決の数式 fix: 描画できる本体（LaTeX / reading / 原文）を持つ式だけ
        # `![[equation:id]]` を埋め込む。本体の無い式（linked_equation_ids だけの
        # 裸ID 等）に埋め込みを出すと、必ず「未解決の数式」になるため埋め込まない。
        if _equation_has_renderable_body(item):
            lines.append(f"![[equation:{eq_id}]]")
    material["source_text"] = "\n".join(line for line in lines if line is not None).strip()


def _equation_has_renderable_body(item: dict) -> bool:
    """数式項目が UI で描画可能な本体（LaTeX / reading / 原文）を持つか。"""
    if not isinstance(item, dict):
        return False
    return bool(
        (item.get("latex") or item.get("latex_canonical") or item.get("normalized_latex"))
        or (item.get("plain_text") or item.get("reading"))
        or (item.get("raw_text") or item.get("text"))
    )


def _equation_material_description(item: dict) -> str:
    for key in ("summary", "description", "semantic_summary", "role", "meaning", "plain_text", "reading"):
        text = str(item.get(key) or "").strip()
        if text and not _looks_like_tex_source(text):
            return _short_excerpt(text, limit=120)
    return "この節の説明で参照する中心的な関係式です。"


def _looks_like_tex_source(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    tex_markers = (
        "\\begin",
        "\\end",
        "\\frac",
        "\\sum",
        "\\int",
        "\\left",
        "\\right",
        "\\bm",
        "\\mathbf",
        "\\tilde",
        "\\delta",
        "\\rho",
        "\\alpha",
        "\\gamma",
        "\\sigma",
        "\\overset",
        "_{",
        "^{",
    )
    return any(marker in stripped for marker in tex_markers)


def _required_equation_items(topic: dict, limit: int = 5) -> list[dict]:
    by_id: dict[str, dict] = {}
    for block in topic.get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "equations":
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            eq_id = str(item.get("equation_id") or item.get("id") or "").strip()
            if eq_id and eq_id not in by_id:
                normalized = dict(item)
                normalized["equation_id"] = eq_id
                by_id[eq_id] = normalized
    ordered_ids = [str(eq_id) for eq_id in topic.get("linked_equation_ids") or [] if str(eq_id)]
    for eq_id in ordered_ids:
        by_id.setdefault(eq_id, {"equation_id": eq_id, "label": eq_id})
    ordered_ids.extend(eq_id for eq_id in by_id if eq_id not in ordered_ids)
    required = [by_id[eq_id] for eq_id in ordered_ids if eq_id in by_id]
    return required[:limit]


def _required_figure_items(topic: dict, limit: int = 5) -> list[dict]:
    """topic の ``evidence_links``（kind='figure'）∪ ``linked_figure_ids`` から本文へ
    注入すべき図一覧を導出する。

    数式版 ``_required_equation_items`` と同じ発想: 既に解決済みの evidence
    （``document_figures.id`` / ``course_teaching_figures.id`` が判明しているもの）だけを
    対象にし、出現順・重複排除で返す。id を発明しない（存在しない figure_id を作らない）。

    ``linked_figure_ids`` も引くのは教材図スタジオ設計書 §7.1b-3（AI 書き換え耐性）:
    採用時の参照登録は ``evidence_links`` と ``linked_figure_ids`` の両方に書くが、
    片方だけが残っている（旧データ・部分的な編集）場合にも本文復元が効くようにする。
    caption は ``evidence_links`` 側にしか無いため、``linked_figure_ids`` 単独の図は
    caption 空で返す（キャプションを捏造しない）。
    """
    items: list[dict] = []
    seen: set[str] = set()
    for link in topic.get("evidence_links") or []:
        if not isinstance(link, dict) or link.get("kind") != "figure":
            continue
        figure_id = str(link.get("target_id") or link.get("figure_id") or "").strip()
        if not figure_id or figure_id in seen:
            continue
        seen.add(figure_id)
        extra = link.get("extra") if isinstance(link.get("extra"), dict) else {}
        items.append({
            "figure_id": figure_id,
            "caption": str(
                link.get("caption") or link.get("summary") or extra.get("caption") or ""
            ),
        })
    for raw_id in topic.get("linked_figure_ids") or []:
        figure_id = str(raw_id or "").strip()
        if not figure_id or figure_id in seen:
            continue
        seen.add(figure_id)
        items.append({"figure_id": figure_id, "caption": ""})
    return items[:limit]


def _ensure_required_figures_in_material(result: dict, topic: dict) -> None:
    """トピックに紐づく図が本文に埋め込まれていなければ末尾へ決定論的に追記する。

    数式の ``_ensure_required_equations_in_material`` と同格の決定論注入
    （hierarchical_context_explanation_design.md Phase 4 §7.2）。LLM が
    ``![[figure:id]]`` を書き漏らすと図が学習者に一切配信されない構造的弱点
    （学習者向け配信の条件3が本文参照に依存するため）を埋める。``spoken_script``
    は変更しない（v1 設計: 図は読み上げない）。
    """
    material = result.setdefault("student_material", {})
    if not isinstance(material, dict):
        material = {"source_format": "eg-markdown-v1", "source_text": str(material or "")}
        result["student_material"] = material
    material["source_format"] = material.get("source_format") or "eg-markdown-v1"
    source_text = str(material.get("source_text") or "").strip()
    required = _required_figure_items(topic)
    missing = [
        item for item in required
        if item.get("figure_id") and f"![[figure:{item['figure_id']}]]" not in source_text
    ]
    if not missing:
        material["source_text"] = source_text
        return
    lines = [source_text] if source_text else []
    lines.extend(["", "### この節で参照する図"])
    for item in missing:
        figure_id = str(item.get("figure_id") or "")
        caption = str(item.get("caption") or "").strip()
        if caption:
            lines.append(f"- {_short_excerpt(caption, limit=120)}")
        lines.append(f"![[figure:{figure_id}]]")
    material["source_text"] = "\n".join(line for line in lines if line is not None).strip()


def _ensure_check_question_details(result: dict, topic: dict) -> None:
    result["check_questions"] = _detailed_check_questions(result.get("check_questions"), topic)


def _detailed_check_questions(value: object, topic: dict) -> list[dict]:
    questions = _normalize_check_question_items(value)
    for item in questions:
        _fill_check_question_detail(item, topic)
    if not questions:
        fallback_question = _fallback_check_question(topic)
        if fallback_question:
            item = {
                "question": fallback_question,
                "model_answer": "",
                "answer_requirements": [],
                "explanation": "",
            }
            _fill_check_question_detail(item, topic)
            questions.append(item)
    return questions[:4]


def _normalize_check_question_items(value: object) -> list[dict]:
    raw_items = value if isinstance(value, list) else ([value] if value else [])
    questions: list[dict] = []
    for raw in raw_items:
        if isinstance(raw, BaseModel):
            raw = raw.model_dump()
        if isinstance(raw, str):
            item = {
                "question": raw,
                "model_answer": "",
                "answer_requirements": [],
                "explanation": "",
            }
        elif isinstance(raw, dict):
            item = {
                "question": str(raw.get("question") or raw.get("text") or "").strip(),
                "model_answer": str(raw.get("model_answer") or raw.get("answer") or "").strip(),
                "answer_requirements": _clean_str_list(raw.get("answer_requirements") or raw.get("requirements")),
                "explanation": str(raw.get("explanation") or raw.get("reason") or "").strip(),
            }
        else:
            continue
        if not item["question"]:
            continue
        questions.append(item)
    return questions[:4]


def _fill_check_question_detail(item: dict, topic: dict) -> None:
    summary = str(topic.get("summary") or topic.get("content") or "").strip()
    objectives = _as_str_list(topic.get("learning_objectives"))[:3]
    concepts = _as_str_list(topic.get("key_concepts"))[:3]
    equation_items = _required_equation_items(topic, limit=3)
    equation_labels = [
        str(eq.get("label") or eq.get("equation_id") or "").strip()
        for eq in equation_items
        if eq.get("label") or eq.get("equation_id")
    ]
    requirements = item.get("answer_requirements") or []
    for value in concepts + objectives:
        if value and value not in requirements:
            requirements.append(value)
    for label in equation_labels:
        req = f"数式 {label} の意味または役割に触れる"
        if req not in requirements:
            requirements.append(req)
    if not requirements:
        requirements.append("この節の中心概念を自分の言葉で説明する")
    item["answer_requirements"] = requirements[:5]
    if not item.get("model_answer"):
        if summary:
            item["model_answer"] = _short_excerpt(summary, limit=260)
        elif objectives:
            item["model_answer"] = "、".join(objectives)
        else:
            item["model_answer"] = "この節で扱った定義・関係式・前提を結び付けて説明する。"
    if not item.get("explanation"):
        if equation_labels:
            item["explanation"] = (
                "根拠となる数式を単独で読むのではなく、各記号が何を表し、"
                "その式が次の議論にどのように使われるかを確認する。"
            )
        else:
            item["explanation"] = "用語の暗記ではなく、前提、中心概念、結論のつながりを確認する。"


def _fallback_check_question(topic: dict) -> str:
    prompts = _as_str_list(topic.get("assessment_prompts"))
    if prompts:
        return prompts[0]
    title = str(topic.get("title") or "この節").strip()
    return f"{title}で扱った中心概念と数式の役割を説明してください。"


def _clean_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [line.strip("- ・\t ") for line in value.splitlines() if line.strip("- ・\t ")]
    return []


def _set_content_status(course: dict, status: str, message: str = "", extra: dict | None = None) -> None:
    payload = dict(extra or {})
    payload.update({
        "status": status,
        "message": message,
        "updated_at": _dt.datetime.now(_dt.UTC).isoformat(),
    })
    course["course_content_status"] = payload


def _save_course(session, course_id: str, course: dict) -> None:
    clean_course = _strip_nuls(course)
    session.execute(
        sa_text("""
            UPDATE learning_courses
            SET data = CAST(:data AS jsonb),
                title = :title,
                updated_at = now()
            WHERE id = :course_id
        """),
        {
            "course_id": course_id,
            "title": str(clean_course.get("title") or course_id).replace("\x00", "").replace("\\u0000", ""),
            "data": _json_dumps(clean_course),
        },
    )
    session.commit()


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_str_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _linked_ids(components: list[dict], field: str) -> list[str]:
    ids: list[str] = []
    for component in components:
        ids.extend(str(item) for item in _as_list(component.get(field)) if item)
    return list(dict.fromkeys(ids))


def _short_excerpt(text: str, limit: int = 280) -> str:
    """切り詰めの委譲（CP5「切り詰めは1実装」）。

    正本は ``core/text_excerpt.py`` の ``excerpt``（文境界 → 語境界 → 文字数の順で
    切り、常に省略記号を付け、TeX コマンドの途中では切らない）。本モジュールは
    素スライス（``text[:limit]``）を持たない — 英文が単語途中で切れる
    （``…spatial mea``）事故と、TeX が壊れて描画不能になる事故の再発防止。
    シグネチャ（``text`` / ``limit``）と「切ったら省略記号を付ける」挙動は不変。
    """
    return excerpt(text, limit)


def _norm_title(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").casefold())


def _tokens(text: str) -> set[str]:
    ascii_tokens = {tok.casefold() for tok in re.findall(r"[A-Za-z0-9]{3,}", text or "")}
    jp_tokens = set(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]{2,}", text or ""))
    return ascii_tokens | jp_tokens


def _overlap_score(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), 1)
