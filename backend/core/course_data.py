"""``learning_courses.data`` (JSONB) の正本スキーマとアクセサ（Tier 3-18）。

``learning_courses.data`` はスキーマレスな JSONB で、これまで15ファイル超に散在する
素の ``dict.get()``/``[]`` アクセスで読み書きされてきた（別名: ``data`` / ``course`` /
``course_row`` / ``course_info`` 等）。本モジュールはその**カタログ文書化**と
**読み取りアクセサの一本化**を目的とする。

設計方針:
- 全モデルは ``model_config = ConfigDict(extra="allow")`` とし、全フィールドは
  Optional かデフォルト付きにする。既存データ・未知キーを一切拒否・破棄しない
  （CLAUDE.md 全体の「情報を落とさない」原則）。
- 本モジュールは FastAPI を import しない（``core/`` 共通ルール）。
- 書き込みロジック（``learning_courses.data`` への UPDATE を伴う経路）はここに置かない。
  アクセサは常に dict を受け取り dict/値を返す**読み取り専用**の純関数。
- ``validate_course_data()`` は検証用の入口であり、既存の実行パス（素の dict
  アクセス）には組み込まない（JSONB を Pydantic 経由に一括変換する破壊的変更は
  本 issue のスコープ外）。

書き手不在（読み手のみ）のフィールド:
    トップレベル: ``domain`` / ``goal`` / ``target_audience`` / ``prerequisites``
    （コースビルダー・原稿スタジオ等のどの書き込み経路にも代入箇所が無い。
    LLM プロンプトの補助情報として読まれるのみ）。
    topics[]: ``linked_claim_ids`` / ``evidence_links`` / ``content_source`` /
    ``content_confidence`` / ``atlas_node_id`` は ``api/schemas.py::LearningTopic``
    （API リクエスト/レスポンス用の別スキーマ）には存在しないが、
    ``course_content_builder.py`` / ``lecture_studio`` / ``atlas.py`` が
    読み書きしている実データキー。ここでは ``CourseTopic`` に明示フィールドとして
    含めるが、API 応答スキーマ側は変更しない（本 issue の制約）。
sources[].document_id も同様に書き手が見当たらない読み取り専用フィールド。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Pydantic モデル群（スキーマの正本・カタログ文書化）
# ---------------------------------------------------------------------------


class CoursePrerequisite(BaseModel):
    """topics[].prerequisites[] の要素。素の文字列（旧フォーマット）で来ることもある
    （呼び出し側で ``p.get("name", p) if isinstance(p, dict) else str(p)`` 正規化）。
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    status: str | None = "not_started"  # mastered | partial | not_started


class CourseMisconception(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str | None = ""
    wrong: str | None = None
    correct: str | None = None


class CourseTopic(BaseModel):
    """``learning_courses.data.topics[]`` の要素。

    ``api/schemas.py::LearningTopic`` （API 入出力用）のフィールドに加えて、
    LearningTopic には存在しない実データキー（``content_source`` /
    ``content_confidence`` / ``linked_claim_ids`` / ``evidence_links`` /
    ``draft_source`` / ``atlas_node_id`` / ``coverage`` 等）も明示する。
    ここに無いキーも ``extra="allow"`` によって失われない。
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    chapter_index: int | None = None
    topic_index: int | None = None
    status: str | None = "locked"  # completed | in_progress | locked

    prerequisites: list[CoursePrerequisite | str] = Field(default_factory=list)
    misconceptions: list[CourseMisconception] = Field(default_factory=list)

    summary: str | None = ""
    content: str | None = ""
    content_blocks: list[dict] = Field(default_factory=list)
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisite_concepts: list[str] = Field(default_factory=list)
    blackbox_policy: dict | None = Field(default_factory=dict)
    assessment_prompts: list[str] = Field(default_factory=list)
    expected_misconceptions: list[str] = Field(default_factory=list)

    linked_component_ids: list[str] = Field(default_factory=list)
    linked_equation_ids: list[str] = Field(default_factory=list)
    # Phase 4 §7.1 (hierarchical_context_explanation_design.md): figures backing
    # this topic, derived deterministically by course_content_builder /
    # CourseMappingAgent (component.source_scope.figure_id and figure_table_semantics
    # linked_claim_ids reverse lookup). No migration needed (JSONB field).
    linked_figure_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    teaching_takeaways: list[str] = Field(default_factory=list)
    material_chunk_ids: list[str] = Field(default_factory=list)
    source_excerpt: str | None = ""
    key_concepts: list[str] | None = Field(default_factory=list)
    student_material: dict | None = Field(default_factory=dict)
    spoken_script: str | None = ""
    cautions: list[str] | None = Field(default_factory=list)
    check_questions: list[dict | str] | None = Field(default_factory=list)

    # --- LearningTopic (api/schemas.py) に無い実データキー ---
    # course_content_builder.py が生成・lecture_studio/topics.py が読み書きする。
    linked_claim_ids: list[str] = Field(default_factory=list)
    evidence_links: list[dict] = Field(default_factory=list)
    content_source: str | None = ""
    content_confidence: str | float | None = ""
    draft_source: str | None = ""
    coverage: dict | None = Field(default_factory=dict)
    linked_chunk_ids: list[str] = Field(default_factory=list)
    # atlas.py::save_course_atlas_binding が書き込む唯一の書き手（binding 保存経路）。
    atlas_node_id: str | None = None


class CourseChapter(BaseModel):
    """``learning_courses.data.chapters[]`` の要素。

    正のトピック格納形はフラット ``data.topics[]`` + ``topic.chapter_index`` だが、
    一部の防御的読み取りコード（``core/status/projector.py::course_atlas_binding_facts``
    等）は ``chapter.topics[]`` ネスト形も走査する。ここでは両立のため任意フィールド
    として ``topics`` を持たせる（正の書き込み経路はこのネスト形を書かない）。
    """

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    status: str | None = "locked"  # completed | in_progress | locked
    progress_pct: int | None = 0
    topics: list[CourseTopic] | None = None  # 防御的ネスト形のみ（正はフラット topics[]）


class CourseConcept(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    status: str | None = "future"  # mastered | learning | future
    children: list[str] = Field(default_factory=list)
    expanded: bool | None = False


class CourseSource(BaseModel):
    """``learning_courses.data.sources[]`` の要素。"""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    subtitle: str | None = ""
    license: str | None = ""
    used_section: str | None = ""
    material_id: str | None = ""
    # 読み取り専用（書き手不在）: theory_components.py が読むが、コースビルダー/
    # 原稿スタジオのどの保存経路も document_id を書かない。
    document_id: str | None = None


class CourseReferencedSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str | None = None
    section: str | None = None
    title: str | None = None
    note: str | None = ""


class LectureStudioSettings(BaseModel):
    """``learning_courses.data.lecture_studio_settings``。

    ``api/schemas.py::LectureStudioSettings``（API 入出力用）とはフィールド名は
    揃えつつ、保存形にのみ存在する ``scripts_need_regeneration``
    （migration 040: 口調/言語変更時に次回バッチ生成で全チャンク再生成させるフラグ）
    を追加で持つ、別モデル。
    """

    model_config = ConfigDict(extra="allow")

    narration_persona: str | None = ""
    response_persona: str | None = ""
    lecture_language: str | None = "ja"  # ja | en
    scripts_need_regeneration: bool | None = False


class CourseData(BaseModel):
    """``learning_courses.data`` (JSONB) 全体のスキーマ正本。

    書き手不在（読み取り専用）のトップレベルフィールド: ``domain`` / ``goal`` /
    ``target_audience`` / ``prerequisites``。``course_content_status`` は
    ``_set_content_status()`` が任意キーを足す free-form dict のため、構造化せず
    ``dict`` のまま保持する。
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    description: str | None = ""

    chapters: list[CourseChapter] = Field(default_factory=list)
    topics: list[CourseTopic] = Field(default_factory=list)
    concepts: list[CourseConcept] = Field(default_factory=list)
    sources: list[CourseSource] = Field(default_factory=list)
    referenced_sections: list[CourseReferencedSection] = Field(default_factory=list)

    cartridge_id: str | None = None
    lecture_studio_settings: LectureStudioSettings | None = None
    # M層 Phase 3（llm_model_selection_design.md §6.4）: 場面別コース単位のモデル上書き。
    # キーは scene_key（現状 "learning_chat" のみ書き手・読み手を持つ）、値はモデル id。
    # 書き手: routes/learning.py::update_course（CourseUpdateRequest.llm_models）。
    llm_models: dict[str, str] = Field(default_factory=dict)
    course_content_status: dict | None = Field(default_factory=dict)
    # discuss_opening_authoring_design.md §2 最下段 / Phase 0b: 「このコースで議論したい
    # こと」。**教員の任意入力**で AI 生成は関与しない（主語=教員）。discuss 開幕画面の
    # 先頭に表示される。書き手は routes/learning.py::update_course
    # （CourseUpdateRequest.course_focus）のみ。
    course_focus: str | None = ""
    # atlas_binding_lifecycle_design.md §2.3: コース起点で新分野を作成した際の
    # 「凍結待ち」仮予約 domain_key。書き手は routes/atlas.py の
    # PUT .../atlas-binding/pending のみ。バインド保存（PUT .../atlas-binding）成功時に
    # 自動クリアされる（意思決定がなされたため）。
    atlas_binding_pending: str | None = None

    # --- 読み取り専用（書き手不在）。LLM プロンプト補助情報として読まれるのみ ---
    domain: str | None = None
    goal: str | None = None
    target_audience: str | None = None
    prerequisites: list[Any] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# アクセサ（dict → dict/値。None・非 dict 入力に安全）
# ---------------------------------------------------------------------------


def course_topics(data: dict | None) -> list[dict]:
    """``data.topics`` をフラットなリストで返す（旧: ``data.get("topics", [])`` /
    ``data.get("topics") or []`` の置換先。非 list を返さないよう防御する）。

    章ネスト形 (``chapters[].topics[]``) は含まない。両方見る必要がある場合は
    ``iter_all_topics()`` を使うこと。

    ``course_sources()`` と ``iter_all_topics()`` の章ネスト側と同じく、**非 dict 要素は
    除外する**（``course_chapters()`` の docstring が「``course_topics()`` とは異なり
    位置保存を優先する」と述べている側の挙動）。トピックは位置ではなく ``topic_id`` /
    ``title`` で参照されるため、位置を保つ必要が無い。除外しないと ``None`` や文字列が
    そのまま呼び出し側へ流れ、``topic.get(...)`` を呼ぶ各層（``lecture`` /
    ``status.projector`` / ``next_steps`` ほか）で AttributeError になる — この
    モジュールが素の dict アクセスを引き受けている意味が失われる。
    """
    if not isinstance(data, dict):
        return []
    topics = data.get("topics")
    if not isinstance(topics, list):
        return []
    return [t for t in topics if isinstance(t, dict)]


def iter_all_topics(data: dict | None) -> list[dict]:
    """フラット ``data.topics[]`` + 章ネスト ``data.chapters[].topics[]`` の両方を
    防御的に走査して返す（旧: ``core/status/projector.py::course_atlas_binding_facts``
    や ``core/admin_assistant/next_steps.py`` に散在していた「両方見る」パターンの
    置換先）。正の格納形はフラットのみだが、ネスト形が来ても取りこぼさない。
    """
    if not isinstance(data, dict):
        return []
    result: list[dict] = []
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        for topic in chapter.get("topics") or []:
            if isinstance(topic, dict):
                result.append(topic)
    result.extend(course_topics(data))
    return result


def course_chapters(data: dict | None) -> list:
    """``data.chapters`` をリストで返す（無い/非 list なら ``[]``）。

    旧: ``data.get("chapters", [])`` / ``data.get("chapters") or []`` の置換先
    （``core/learning_support_agent.py`` / ``core/tension/worker.py`` /
    ``core/structure_anchor/worker.py`` 等に散在していた、``topic.chapter_index`` を
    添字とした章タイトル逆引きパターン）。**要素は dict にフィルタしない**
    （chapter_index はこのリストの**位置**に対応するため、非 dict 要素を除外すると
    以降の要素の添字がずれて chapter_index との対応が壊れる — ``course_sources()`` /
    ``course_topics()`` とは異なり、ここでは位置保存を優先する）。
    """
    if not isinstance(data, dict):
        return []
    chapters = data.get("chapters")
    return chapters if isinstance(chapters, list) else []


def course_sources(data: dict | None) -> list[dict]:
    """``data.sources`` をリストで返す（非 dict 要素は除外する防御込み）。

    旧: ``data.get("sources", [])`` / ``data.get("sources") if isinstance(data, dict)
    else None`` の置換先。
    """
    if not isinstance(data, dict):
        return []
    sources = data.get("sources")
    if not isinstance(sources, list):
        return []
    return [s for s in sources if isinstance(s, dict)]


def course_source_material_ids(data: dict | None) -> list[str]:
    """``data.sources[].material_id`` を順序保持・falsy スキップで返す。

    旧: ``[s.get("material_id") for s in sources if isinstance(s, dict) and
    s.get("material_id")]``（``core/status/projector.py::_course_material_ids`` /
    ``api/services.py`` 等5箇所超に散在していたパターン）の置換先。重複除去はしない
    （呼び出し側が dedup 済みの挙動を期待する場合は ``list(dict.fromkeys(...))`` を
    呼び出し側で適用すること）。
    """
    return [str(s["material_id"]) for s in course_sources(data) if s.get("material_id")]


def course_cartridge_id(data: dict | None) -> str:
    """``data.cartridge_id`` を trim 済み文字列で返す（無ければ ``""``）。

    旧: ``str(course_data.get("cartridge_id") or "").strip()`` の置換先。
    """
    if not isinstance(data, dict):
        return ""
    return str(data.get("cartridge_id") or "").strip()


def course_title(data: dict | None, default: str = "") -> str:
    """``data.title`` を返す（無ければ ``default``）。

    旧: ``course_data.get("title", "")`` / ``course_data.get("title") or default`` の
    置換先。呼び出し側でローカル変数名 ``course_title`` と衝突する場合は
    ``from core.course_data import course_title as _course_title`` のように別名 import
    すること。
    """
    if not isinstance(data, dict):
        return default
    return data.get("title") or default


def course_atlas_binding_pending(data: dict | None) -> str:
    """``data.atlas_binding_pending`` を trim 済み文字列で返す（無ければ ``""``）。

    atlas_binding_lifecycle_design.md §2.3 の「凍結待ち」仮予約 domain_key アクセサ。
    旧: ``str(course_data.get("atlas_binding_pending") or "").strip()`` の置換先。
    """
    if not isinstance(data, dict):
        return ""
    return str(data.get("atlas_binding_pending") or "").strip()


def course_focus(data: dict | None) -> str:
    """``data.course_focus`` を trim 済み文字列で返す（無ければ ``""``）。

    discuss 開幕画面の「このコースで議論したいこと」（`discuss_opening_authoring_design.md`
    §2 最下段・Phase 0b）。**教員の任意入力**で、AI 生成・候補提示は一切関与しない。
    未入力（``""``）は正常な状態で、開幕画面は区画ごと非表示にする（欠落を警告しない）。
    """
    if not isinstance(data, dict):
        return ""
    return str(data.get("course_focus") or "").strip()


def course_llm_models(data: dict | None) -> dict:
    """``data.llm_models`` を dict で返す（無い/非 dict なら ``{}``）。

    M層 Phase 3（場面別 LLM モデル選択, `docs/features/llm_model_selection_design.md` §6.4）
    のコース単位モデル上書き。キーは scene_key（現状 ``"learning_chat"`` のみ意味を持つ）、
    値はカタログ収載モデル id の文字列。
    """
    if not isinstance(data, dict):
        return {}
    models = data.get("llm_models")
    return models if isinstance(models, dict) else {}


def lecture_studio_settings(data: dict | None) -> dict:
    """``data.lecture_studio_settings`` を dict で返す（無い/非 dict なら ``{}``）。

    旧: ``course_data.get("lecture_studio_settings", {}) or {}`` /
    ``course_data.get("lecture_studio_settings") or {}`` の置換先。
    """
    if not isinstance(data, dict):
        return {}
    settings = data.get("lecture_studio_settings")
    return settings if isinstance(settings, dict) else {}


def find_course_topic(
    data: dict | None,
    topic_id: str,
    chapter_index: object = None,
    topic_index: object = None,
) -> dict | None:
    """``data.topics[]`` からトピックを検索する。

    1. まず ``id`` の文字列一致で検索する。
    2. 見つからなければ ``(chapter_index, topic_index)`` の一致で検索する
       （``topic_index`` 未設定の topic は enumerate 添字にフォールバック）。

    旧: ``api/routes/lecture_studio/topics.py::_find_course_topic`` の実装の
    置換先（同モジュールは本関数への委譲に置き換える）。フラット ``topics[]``
    のみを見る（章ネストは見ない — 元実装と同じ挙動）。
    """
    topics = course_topics(data)
    for topic in topics:
        if isinstance(topic, dict) and str(topic.get("id", "")) == str(topic_id):
            return topic
    for idx, topic in enumerate(topics):
        if not isinstance(topic, dict):
            continue
        if topic.get("chapter_index") == chapter_index and topic.get("topic_index", idx) == topic_index:
            return topic
    return None


def course_atlas_binding_facts(data: dict | None) -> tuple[bool, bool]:
    """course_data から ``(has_cartridge, has_atlas_node)`` を走査する共通プリミティブ。

    ``cartridge_id`` の有無と、``topics[].atlas_node_id``（章内・トップレベルいずれか）の
    有無を1回の走査で返すだけで、両者からどう bound/needs-binding を判定するかの
    **判定方針は呼び出し側に残す**（``core/status/projector.py`` は AND 条件、
    ``core/admin_assistant/next_steps.py`` は「cartridge_id があれば binding 不要」—
    意図的に異なる、統合しない）。

    Tier3-16 で ``core/status/projector.py`` に実装されたものを Tier3-18 で本モジュールへ
    移設した（正本はここ）。``core/status/projector.py::course_atlas_binding_facts`` は
    後方互換の再エクスポートとして残る。
    """
    if not isinstance(data, dict):
        return False, False
    has_cartridge = bool(data.get("cartridge_id"))
    has_atlas_node = False
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        for topic in chapter.get("topics") or []:
            if isinstance(topic, dict) and topic.get("atlas_node_id"):
                has_atlas_node = True
                break
        if has_atlas_node:
            break
    if not has_atlas_node:
        for topic in data.get("topics") or []:
            if isinstance(topic, dict) and topic.get("atlas_node_id"):
                has_atlas_node = True
                break
    return has_cartridge, has_atlas_node


def validate_course_data(data: dict) -> CourseData:
    """``data`` を ``CourseData`` として検証する（テスト・検証用の入口）。

    既存の実行パス（素の dict アクセス）には組み込まれていない。``extra="allow"`` の
    ため未知キーは失われず、``model_dump()`` でラウンドトリップできる。
    """
    return CourseData.model_validate(data)
