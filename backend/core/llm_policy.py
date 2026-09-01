"""LLM モデル選択（M層: Model Policy）— モデル決定の正本。

正本ドキュメント: docs/features/llm_model_selection_design.md

**Phase 0 のスコープ**（本ファイルはこの段階の実装）: 「場面（scene）→ 実モデル名」の
解決ロジックを1箇所に集約するだけで、DB ポリシー（migration 061 想定の
``llm_model_policies``）・運用タブ UI は後続 Phase。:class:`NullPolicyBackend` が
既定のため、③ユーザー別ポリシー・④システムポリシーの層は常にスキップされ、
⑤環境変数・⑥tier 既定のみが効く — つまり**環境変数だけの環境では従来と完全に
同じモデルが選ばれる**（設計書 §11-6 の不変条件）。

呼び出し側（agent・worker・route）のコードは変えない。U層の
``core.llm_usage.usage_context(feature=...)`` が既に contextvars で場面を運んでいる
ので、同じ feature 文字列を scene キーの入力として再利用する（新しい伝搬経路を作らない）。

FastAPI 非 import・LLM SDK 非 import（開発ルール2 / M1）。
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol

from core.config import get_settings
from core.llm_usage.context import current_usage_context
from core.llm_usage.schema import KNOWN_FEATURES, UNATTRIBUTED

logger = logging.getLogger(__name__)

# 同梱モデルカタログ（設計書 §5）。``LLM_MODEL_CATALOG_PATH`` 未設定時の既定。
# このファイル（backend/core/llm_policy.py）から相対解決するため、ローカル実行
# （backend/config/llm_models.json）と docker（/app/config/llm_models.json、
# Dockerfile が backend/config/ を /app/config/ に COPY）の両方で解決できる。
_BUNDLED_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "llm_models.json"

# ---------------------------------------------------------------------------
# 場面カタログ（設計書 §2）
# ---------------------------------------------------------------------------

SCENE_PIPELINE = "pipeline"
SCENE_PIPELINE_VISION = "pipeline.vision"
SCENE_COURSE_BUILDER = "course_builder"
SCENE_LECTURE_STUDIO = "lecture_studio"
SCENE_LEARNING_CHAT = "learning_chat"
SCENE_LEARNING_VOICE = "learning_voice"
SCENE_LEARNING_BACKGROUND = "learning_background"
SCENE_ATLAS = "atlas"
SCENE_DOUBT = "doubt"
SCENE_DELIBERATION = "deliberation"
SCENE_ASSISTANT = "assistant"
SCENE_FIGURE_STUDIO = "figure_studio"
SCENE_DISCOVERY_COMPARE = "discovery_compare"

_SCENE_LABELS: dict[str, str] = {
    SCENE_PIPELINE: "教材の解析",
    SCENE_PIPELINE_VISION: "教材の図の解析",
    SCENE_COURSE_BUILDER: "コース構築チャット",
    SCENE_LECTURE_STUDIO: "原稿の生成・書き換え",
    SCENE_LEARNING_CHAT: "受講中のチャット",
    SCENE_LEARNING_VOICE: "音声会話",
    SCENE_LEARNING_BACKGROUND: "学習の裏方（痕跡解析）",
    SCENE_ATLAS: "分野の地図の生成",
    SCENE_DOUBT: "前提の地図",
    SCENE_DELIBERATION: "要素検討ワークスペース",
    SCENE_ASSISTANT: "管理アシスタント",
    SCENE_FIGURE_STUDIO: "教材図スタジオ",
    SCENE_DISCOVERY_COMPARE: "類似論文の比較分析",
}


def scene_for_feature(feature: str) -> str | None:
    """``KNOWN_FEATURES`` の feature 文字列を UI 表示粒度の scene_key へ束ねる。

    None を返すのは次の場合のみ:
      - ``unattributed`` / 未知の feature（policy 非適用 = 従来挙動）
      - ``embedding:*`` / ``admin:help_kb_embed`` / ``discovery:ranking``
        （いずれも実体は embedding 呼び出し。embedding は選択対象外、M5）

    ``admin:component_candidates``（C層の質問→候補生成、正本
    ``core/component_candidates.py``）は設計書 §2 の場面表に明示の行を持たないが、
    運用タブに畳む「管理画面の小粒度 AI 支援」という性質が Admin Copilot
    （``assistant`` scene）と同型のため、便宜的に ``assistant`` へ束ねる
    （Phase 0 では scene はまだ何も分岐に使われないため実害はない。Phase 1 で
    独立 scene が必要になれば ``SCENES`` を拡張すること）。
    """
    if not feature or feature == UNATTRIBUTED:
        return None
    if (
        feature.startswith("embedding:")
        or feature == "admin:help_kb_embed"
        # 論文ディスカバリーの関連度ランキング（設計書 paper_discovery §6）も実体は
        # embedding 呼び出しなので、embedding と同じくモデル選択の対象外（M5）。
        or feature == "discovery:ranking"
    ):
        return None
    if feature in ("pipeline:apparatus_semantics", "deliberation:figure_reanalysis"):
        # 教員指示付き図再解析（deliberation:figure_reanalysis）は W層の導線から
        # 呼ばれるが、実体は apparatus vision エンジンそのもの
        # （core/figure_reanalysis.py → ApparatusSemanticsAgent →
        # generate_structured_with_images(model=None)）。従来の解決先も
        # apparatus_llm_model だったため、scene も env も pipeline.vision 側に
        # 帰属させる（非 vision モデルへ落ちる回帰を防ぐ。M5）。
        return SCENE_PIPELINE_VISION
    if feature == "pipeline" or feature.startswith("pipeline:"):
        return SCENE_PIPELINE
    if feature in (
        "learning:chat",
        "learning:chat_casual",
        "learning:chat_discuss",
        "learning:cycle_elicit",
        "learning:cycle_diff",
        "learning:understanding_check",
        "learning:help_usage",
    ):
        return SCENE_LEARNING_CHAT
    if feature.startswith("learning:voice_") or feature.startswith("deliberation:voice_"):
        # 音声（STT/TTS）は呼び出し元が学習側か管理画面（グラフレビューの音声対話）かに
        # 関わらず policy の解決経路を通らない（STT は ``settings.llm_transcribe_model``
        # 直参照、TTS は provider 固定）。設定できるのに何も起きない場面を増やさない
        # ため、書き込み可能な `deliberation` scene ではなく読み取り専用の音声場面へ
        # 束ねる（`READ_ONLY_SCENE_KEYS`・M4/M5）。**この分岐は
        # `deliberation:` prefix の分岐より前に置くこと。**
        return SCENE_LEARNING_VOICE
    if feature in (
        "learning:tension",
        "learning:structure_anchor",
        "admin:reconstruction_authoring",
    ):
        return SCENE_LEARNING_BACKGROUND
    if feature == "admin:course_builder":
        return SCENE_COURSE_BUILDER
    if feature in ("admin:lecture_rewrite", "admin:lecture_generate", "admin:lecture_tts"):
        return SCENE_LECTURE_STUDIO
    if feature in ("admin:atlas_skeleton", "admin:atlas_assist"):
        return SCENE_ATLAS
    if feature.startswith("doubt:"):
        return SCENE_DOUBT
    if feature.startswith("deliberation:"):
        return SCENE_DELIBERATION
    if feature in ("admin:figure_studio", "admin:figure_suggest"):
        # 教材図スタジオ（設計書 teaching_figure_studio_design.md §4.3）。図の対話生成と
        # ギャップ提案は教員から見て同じ場面なので、1つの scene に束ねる（vision 不要 —
        # SVG はテキスト生成なので _VISION_REQUIRED_SCENE_KEYS には入れない）。
        return SCENE_FIGURE_STUDIO
    if feature == "discovery:compare":
        # 論文レーダーの比較分析（設計書 paper_radar_design.md §5.3）。同じ発見層でも
        # ``discovery:ranking`` は embedding なので scene を持たない（上の分岐で None）—
        # モデルを選べるのはテキスト生成のこの1本だけ。
        return SCENE_DISCOVERY_COMPARE
    if feature == "admin:assistant":
        return SCENE_ASSISTANT
    if feature == "admin:component_candidates":
        return SCENE_ASSISTANT
    return None


# ---------------------------------------------------------------------------
# ステージ別（pipeline:<stage>）の表示名（設計書 §6.1「ステージ別に指定する（詳細）」・
# §6.6「ステージ別の指定（N件）」・§10 Phase 4）。
#
# キー集合は `core.document_pipeline.orchestrator.LLM_STAGE_NAMES`（M層のステージ別
# モデル選択の対象ステージ。集合の正本は `_PIPELINE_STEPS` の各 `PipelineStageDef` が
# 宣言する `model_policy=True` で、そこからの導出。「LLM を呼ぶ事実」= `llm_kind` とは
# 別の意味論で、LLM を呼ぶが対象外のステージ = component_graph はここにも入らない）と
# 完全一致でなければならない — 一致は
# `backend/tests/test_llm_model_phase4.py` が構造テストで固定する。
# **本モジュールは orchestrator を import しない**（依存方向は
# orchestrator → llm_policy のまま。M1 / 開発ルール2）。ステージが増減したら、
# orchestrator 側の集合とこの辞書の両方を同時に更新すること。
# ---------------------------------------------------------------------------

# 文言は進捗表示（routes/lecture_studio/pipeline.py::DOCUMENT_PIPELINE_STAGE_LABELS =
# admin.js の再実行メニューと同語）に揃える — 同一ステージが画面によって別名になる
# 訳語分裂を作らない（共有キーの一致は test_lecture_studio.py が固定）。
# apparatus の vision 情報はラベルでなく pipeline-stages API の vision フラグと
# 専用 UI 行（admin-llm-models.js「図の解析（vision）」）が担う。
PIPELINE_STAGE_LABELS: dict[str, str] = {
    "paper_skeleton": "論文アウトラインの推定",
    "rhetorical_role": "論述の役割分類",
    "claim_qualification": "主張の抽出・分類",
    "equation_semantics": "数式の意味付け",
    "apparatus_semantics": "図の装置・パーツ解析",
    "thesis_reconstruction": "中心命題の再構成",
    "dsl_linking": "概念グラフへの接続",
    "component_assembly": "理論コンポーネントの組み立て",
    "narrative_annotator": "説明注釈の付与",
    "contextual_explanation": "要素の二層説明の生成",
    "discuss_opening": "議論のきっかけの生成",
    "landscape_placement": "分野マップ配置候補の生成",
}


def _compute_scene_features() -> dict[str, tuple[str, ...]]:
    membership: dict[str, list[str]] = {}
    for feature in KNOWN_FEATURES:
        scene_key = scene_for_feature(feature)
        if scene_key is None:
            continue
        membership.setdefault(scene_key, []).append(feature)
    return {key: tuple(feats) for key, feats in membership.items()}


_SCENE_FEATURES = _compute_scene_features()

# 正本カタログ。UI（Phase 1+）はここから scene 一覧・表示名・含む feature を読む。
SCENES: dict[str, dict[str, Any]] = {
    scene_key: {"label": label, "features": _SCENE_FEATURES.get(scene_key, ())}
    for scene_key, label in _SCENE_LABELS.items()
}


# ---------------------------------------------------------------------------
# scene の代表 feature（レビュー指摘 m1 の是正）
#
# 運用タブ・catalog API が「この場面の現在のモデル」を1件だけ表示するときに使う。
# かつては `SCENES[scene]["features"][0]`（= KNOWN_FEATURES の登場順の先頭）を
# 使っていたため、`deliberation` → `deliberation:cross_corpus`（embedding 用の
# feature で env マッピングを持たない）、`assistant` → `admin:component_candidates`
# のように**その場面の主たる操作ではない feature** が代表になり、実際には fast tier
# で動く場面に analysis tier のモデル名を表示していた。
#
# 場面ごとに「その場面の主たる操作の feature」を明示宣言する（順序依存を排した
# 決定論的規則）。1つの scene に tier の異なる feature が混在する場合、単一の値で
# 全部を正しく表すことは原理的にできないため、**主たる操作を選ぶ**という規則にする。
# 新しい scene を追加したらここにも1行足すこと（テスト
# `test_llm_policy.py::TestRepresentativeFeature` が網羅を機械的に固定する）。
# ---------------------------------------------------------------------------

_SCENE_REPRESENTATIVE_FEATURE: dict[str, str] = {
    SCENE_PIPELINE: "pipeline",  # 汎用ステージ（大多数）の既定
    SCENE_PIPELINE_VISION: "pipeline:apparatus_semantics",
    SCENE_COURSE_BUILDER: "admin:course_builder",
    SCENE_LECTURE_STUDIO: "admin:lecture_rewrite",
    SCENE_LEARNING_CHAT: "learning:chat",
    SCENE_LEARNING_VOICE: "learning:voice_stt",
    SCENE_LEARNING_BACKGROUND: "learning:tension",
    SCENE_ATLAS: "admin:atlas_skeleton",
    SCENE_DOUBT: "doubt:scope_candidates",
    SCENE_DELIBERATION: "deliberation:chat",
    SCENE_ASSISTANT: "admin:assistant",
    SCENE_FIGURE_STUDIO: "admin:figure_studio",
    SCENE_DISCOVERY_COMPARE: "discovery:compare",
}


def representative_feature_for_scene(scene_key: str) -> str:
    """scene の「現在のモデル」表示に使う代表 feature を決定論的に返す。

    scene_key が scene 名ではない（``pipeline:<stage>`` のような feature 単位キー）
    場合は、それ自身が feature なのでそのまま返す。
    """
    entry = SCENES.get(scene_key)
    if entry is None:
        return scene_key
    explicit = _SCENE_REPRESENTATIVE_FEATURE.get(scene_key)
    features = tuple(entry.get("features") or ())
    if explicit and explicit in features:
        return explicit
    return features[0] if features else scene_key


# ---------------------------------------------------------------------------
# 読み取り専用の場面（レビュー指摘 J5 の是正・設計書 §6.3 の差分）
#
# `learning_voice` は STT（``core/llm.py::transcribe_audio`` が
# ``settings.llm_transcribe_model`` を直参照）と TTS（``core/tts.py`` が
# provider 別にモデルを固定）で構成され、**モデル選択の解決経路（policy）を通らない**。
# カタログにも音声モデル（whisper 系 / tts-1）は載っていないため、設定可能にしても
# 選べる有効値が存在しない。settable-but-inert（設定できるのに何も起きない）は
# M4/M5（捏造しない・fail-closed）に反するので、v1 は**読み取り専用**として扱い、
# 書き込み API は 422 + 事実文で拒否する。表示は実際に使われる値を正直に出す。
# ---------------------------------------------------------------------------

# ``core/tts.py`` の OpenAI TTS モデル（同ファイルにハードコードされている値。
# 表示を正直にするためだけに参照する。ずれ検出は
# `test_llm_policy.py::TestVoiceSceneFacts` が core/tts.py のソースを見て固定する）。
_OPENAI_TTS_MODEL = "tts-1"
_GOOGLE_TTS_MODEL_LABEL = "google-tts"
_VOICE_UNSUPPORTED_LABEL = "（このプロバイダでは未対応）"

READ_ONLY_SCENE_KEYS = frozenset({SCENE_LEARNING_VOICE})

_READ_ONLY_SCENE_REASONS: dict[str, str] = {
    SCENE_LEARNING_VOICE: (
        "音声モデルの変更は v1 では対応していません（表示のみ）。"
        "音声認識は LLM_TRANSCRIBE_MODEL、読み上げは音声プロバイダ側の固定モデルを使います。"
    ),
}


def is_read_only_scene(scene_key: str) -> bool:
    """scene_key が読み取り専用（ポリシー行を書いても効かない場面）かを返す。"""
    return scene_key in READ_ONLY_SCENE_KEYS


def read_only_scene_reason(scene_key: str) -> str | None:
    """読み取り専用の理由（事実文）を返す。書き込み可能な scene は None。"""
    return _READ_ONLY_SCENE_REASONS.get(scene_key)


def voice_model_facts() -> list[dict[str, str]]:
    """音声会話（``learning_voice``）で実際に使われるモデルの事実を返す。

    ``[{"label": "音声認識", "model": ...}, {"label": "読み上げ", "model": ...}]``。
    provider が音声をサポートしない組み合わせでは model に事実文を入れる
    （架空のモデル名を出さない、M4）。
    """
    settings = get_settings()
    provider = settings.llm_provider
    if provider == "openai":
        stt = (getattr(settings, "llm_transcribe_model", "") or "").strip() or _VOICE_UNSUPPORTED_LABEL
        tts = _OPENAI_TTS_MODEL
    elif provider in ("google", "gemini-vertex"):
        # transcribe_audio は openai 以外で RuntimeError を投げる（= 未対応）。
        stt = _VOICE_UNSUPPORTED_LABEL
        tts = _GOOGLE_TTS_MODEL_LABEL
    else:
        stt = _VOICE_UNSUPPORTED_LABEL
        tts = _VOICE_UNSUPPORTED_LABEL
    return [{"label": "音声認識", "model": stt}, {"label": "読み上げ", "model": tts}]


def voice_display_model() -> str:
    """運用タブの「現在のモデル」セル用の1行表示（``whisper-1（音声認識） / tts-1（読み上げ）``）。"""
    return " / ".join(f"{fact['model']}（{fact['label']}）" for fact in voice_model_facts())


# ---------------------------------------------------------------------------
# feature → env 設定のマッピング（設計書 §3 手順⑤）
# ---------------------------------------------------------------------------

# feature -> (Settings 属性名, フォールバック tier)。
# 値が空文字の Settings 属性は「未設定」とみなし tier 既定へフォールバックする
# （既存 core/llm_worker/client.py::resolve_model と同じ規約）。
_FEATURE_ENV_SETTINGS: dict[str, tuple[str, str]] = {
    "learning:tension": ("tension_llm_model", "fast"),
    "learning:structure_anchor": ("anchor_llm_model", "fast"),
    "admin:reconstruction_authoring": ("recon_llm_model", "fast"),
    "doubt:scope_candidates": ("doubt_scope_llm_model", "fast"),
    "doubt:assumption_normalize": ("doubt_assumption_llm_model", "fast"),
    "doubt:falsification_conditions": ("doubt_falsification_llm_model", "fast"),
    "admin:assistant": ("assistant_llm_model", "fast"),
    "admin:atlas_skeleton": ("atlas_assist_llm_model", "analysis"),
    "admin:atlas_assist": ("atlas_assist_llm_model", "analysis"),
    "deliberation:chat": ("deliberation_llm_model", "fast"),
    "deliberation:vision": ("deliberation_llm_model", "fast"),
    # グラフ対話レビューの「グラフ全体対話」（graph_dialogue_review_design.md §5）。
    # scene は deliberation:* prefix で SCENE_DELIBERATION に束ねられる。
    "deliberation:graph_chat": ("deliberation_llm_model", "fast"),
    # 図再解析は vision 経路（scene_for_feature の注記参照）— deliberation では
    # なく apparatus のモデル設定に従う（従来挙動の保存）。
    "deliberation:figure_reanalysis": ("apparatus_llm_model", "analysis"),
    "deliberation:standardization": ("stdpart_llm_model", "fast"),
    "learning:chat": ("learning_chat_llm_model", "analysis"),
    "learning:chat_casual": ("learning_chat_llm_model", "analysis"),
    "learning:chat_discuss": ("learning_chat_llm_model", "analysis"),
    # 理解サイクル Phase 2（設計 §8）: 既存 learning_chat の1コール地点に相乗りするため
    # モデル解決も同じ env キーを共有する。
    "learning:cycle_elicit": ("learning_chat_llm_model", "analysis"),
    "learning:cycle_diff": ("learning_chat_llm_model", "analysis"),
    "learning:understanding_check": ("learning_chat_llm_model", "analysis"),
    "learning:help_usage": ("learning_chat_llm_model", "analysis"),
    "admin:course_builder": ("course_builder_llm_model", "analysis"),
    "pipeline:apparatus_semantics": ("apparatus_llm_model", "analysis"),
    # 教材図スタジオ: 対話生成とギャップ提案で同一の設定キーを共有する
    # （deliberation の chat/vision と同じパターン）。
    "admin:figure_studio": ("figure_studio_llm_model", "fast"),
    "admin:figure_suggest": ("figure_studio_llm_model", "fast"),
    # 論文レーダーの比較分析（paper_radar_design.md §5.6）。
    "discovery:compare": ("discovery_compare_llm_model", "fast"),
}

# 特例: Settings に専用フィールドが無く、呼び出し元が os.getenv で直接読んでいる feature
# （正本: core/document_pipeline/orchestrator.py::_ctxexpl_model）。値は
# (環境変数名, フォールバック tier)。
_FEATURE_DIRECT_ENV: dict[str, tuple[str, str]] = {
    "pipeline:contextual_explanation": ("CTXEXPL_LLM_MODEL", "fast"),
    # discuss 開幕素材の生成（discuss_opening_authoring_design.md §4.1）。専用の
    # Settings フィールドを持たず env 直読みなのは contextual_explanation と同じ。
    "pipeline:discuss_opening": ("DISCUSS_OPENING_LLM_MODEL", "fast"),
    # 知識ランドスケープの配置候補生成（knowledge_landscape_design.md §7.4）。専用の
    # Settings フィールドを持たず env 直読みなのは contextual_explanation と同じ。
    "pipeline:landscape_placement": ("LANDSCAPE_PLACEMENT_LLM_MODEL", "fast"),
}

# 専用の env 設定を持たないが、**歴史的な既定 tier が汎用の analysis tier ではない**
# feature（レビュー指摘 J3 の是正）。原稿スタジオの原稿生成・AI 書き換えは
# `get_llm_params("fast")` 固定で呼ばれていたため、policy 経由に切り替えた後も
# 「ポリシー行も env も無い環境では fast tier」でなければ Phase 0 の挙動不変
# （設計書 §11-6）が壊れる。env 変数は持たないので `_FEATURE_ENV_SETTINGS` には
# 載せられず、tier だけを宣言する専用マップに置く。
_FEATURE_TIER_ONLY: dict[str, str] = {
    "admin:lecture_rewrite": "fast",
    "admin:lecture_generate": "fast",
}

# 上記マッピングに無い feature（その他 pipeline:* 全般・unattributed 等）の既定 tier。
# generate_text() 系の既存の暗黙フォールバック（``model or settings.llm_analysis_model``）
# と一致させる。
_DEFAULT_FALLBACK_TIER = "analysis"

_TIER_ATTR = {"fast": "llm_fast_model", "analysis": "llm_analysis_model"}


def _tier_default_model(tier: str) -> str:
    settings = get_settings()
    attr = _TIER_ATTR.get(tier)
    if attr is None:
        raise ValueError(f"unknown fallback tier: {tier!r}")
    return getattr(settings, attr)


def _env_model_and_tier(feature: str) -> tuple[str | None, str]:
    """feature に対応する env 由来のモデル名（未設定なら None）と fallback tier を返す。"""
    direct = _FEATURE_DIRECT_ENV.get(feature)
    if direct is not None:
        env_name, tier = direct
        value = (os.getenv(env_name, "") or "").strip()
        return (value or None, tier)

    mapping = _FEATURE_ENV_SETTINGS.get(feature)
    if mapping is None:
        return (None, _FEATURE_TIER_ONLY.get(feature, _DEFAULT_FALLBACK_TIER))

    attr, tier = mapping
    settings = get_settings()
    value = (getattr(settings, attr, "") or "").strip()
    return (value or None, tier)


def env_setting_for_feature(feature: str) -> tuple[str, str] | None:
    """feature の env 由来設定を ``(env 表示名, 現在値)`` で返す（未設定・非対応は None）。

    ``env 表示名`` は Settings 属性なら大文字化した環境変数名、``_FEATURE_DIRECT_ENV``
    ならその環境変数名そのもの。値が空文字（= 未設定）の場合は None を返す。
    ``core/llm_policy_store.py`` のシード取込（:func:`iter_env_seeds`）と
    運用タブの「env 由来の事実併記」が使う。
    """
    direct = _FEATURE_DIRECT_ENV.get(feature)
    if direct is not None:
        env_name, _tier = direct
        value = (os.getenv(env_name, "") or "").strip()
        return (env_name, value) if value else None

    mapping = _FEATURE_ENV_SETTINGS.get(feature)
    if mapping is None:
        return None
    attr, _tier = mapping
    value = (getattr(get_settings(), attr, "") or "").strip()
    return (attr.upper(), value) if value else None


# 逆引き（設定キー → feature）。core/llm_worker/client.py::resolve_model の委譲先
# resolve_for_setting() が使う。複数 feature が同じ設定キーを共有することは無い
# （各 *_llm_model は単一の場面専用）ので最初の一致を採用すれば十分。
_SETTING_KEY_TO_FEATURE: dict[str, str] = {}
for _feature, (_attr, _tier) in _FEATURE_ENV_SETTINGS.items():
    _SETTING_KEY_TO_FEATURE.setdefault(_attr, _feature)


# ---------------------------------------------------------------------------
# 解決結果
# ---------------------------------------------------------------------------

# resolve_scene_model() の source 語彙（設計書 §3）。
SOURCE_CALL_ARGUMENT = "call_argument"
SOURCE_RUN_OVERRIDE = "run_override"
SOURCE_COURSE_OVERRIDE = "course_override"
SOURCE_USER_POLICY = "user_policy"
SOURCE_SYSTEM_POLICY = "system_policy"
SOURCE_ENV = "env"
SOURCE_TIER_DEFAULT = "tier_default"

# UI 表示用ラベル（M3: tier 名を出さない。日本語の出所ラベルのみ使う）。
_SOURCE_LABELS: dict[str, str] = {
    SOURCE_CALL_ARGUMENT: "呼び出し時指定",
    SOURCE_RUN_OVERRIDE: "この実行のみ",
    SOURCE_COURSE_OVERRIDE: "このコースの設定",
    SOURCE_USER_POLICY: "あなたの既定",
    SOURCE_SYSTEM_POLICY: "システム既定",
    SOURCE_ENV: "システム既定",
    SOURCE_TIER_DEFAULT: "システム既定",
}

# ユーザー別/実行時オーバーライドの層と扱われる source 集合（reasoning_effort 注入対象）。
_POLICY_LIKE_SOURCES = frozenset(
    {SOURCE_RUN_OVERRIDE, SOURCE_COURSE_OVERRIDE, SOURCE_USER_POLICY, SOURCE_SYSTEM_POLICY}
)


@dataclass(frozen=True)
class ResolvedModel:
    """``resolve_scene_model()`` の解決結果。"""

    model: str
    source: str
    source_label: str
    reasoning_effort: str | None = None


# ---------------------------------------------------------------------------
# PolicyBackend シーム（Phase 0 は Null 実装のみ。Phase 1 で DB 実装に差し替える）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyRow:
    """``llm_model_policies``（migration 061 想定）の1行に対応する読み取り専用ビュー。"""

    model: str
    scope: str  # "user" | "system"
    reasoning_effort: str | None = None


class PolicyBackend(Protocol):
    def lookup(self, scene_key: str, feature: str, user_id: str | None) -> PolicyRow | None:
        """指定の scene（+ feature 単位の詳細指定があれば feature）・user_id に対する
        ポリシー行を返す。無ければ None。"""
        ...


class NullPolicyBackend:
    """Phase 0 の既定バックエンド。常に「ポリシー行なし」を返す（= env/tier 既定に委ねる）。"""

    def lookup(self, scene_key: str, feature: str, user_id: str | None) -> PolicyRow | None:
        return None


_policy_backend: PolicyBackend = NullPolicyBackend()


def get_policy_backend() -> PolicyBackend:
    """現在の PolicyBackend を返す。既定は :class:`NullPolicyBackend`。"""
    return _policy_backend


def set_policy_backend(backend: PolicyBackend) -> None:
    """PolicyBackend を差し替える（Phase 1 の DB 実装・テストのモック差し替え用）。"""
    global _policy_backend
    _policy_backend = backend


def reset_policy_backend_for_tests() -> None:
    """テスト後始末用: PolicyBackend を Null 実装に戻す。"""
    global _policy_backend
    _policy_backend = NullPolicyBackend()


# ---------------------------------------------------------------------------
# 実行時オーバーライド（contextvars。core/llm_usage/context.py と同型）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OverrideEntry:
    model: str
    source: str
    reasoning_effort: str | None = None


_model_override_var: ContextVar[_OverrideEntry | None] = ContextVar(
    "llm_policy_model_override", default=None
)


@contextmanager
def model_override(
    model: str,
    *,
    reasoning_effort: str | None = None,
    source: str = SOURCE_RUN_OVERRIDE,
) -> Iterator[None]:
    """「この実行だけ」のモデル上書きをセットする（設計書 §3 の階層②）。

    ``core.llm_usage.context.usage_context`` と同型の contextvars ベース実装。
    **スレッドをまたがない** — pipeline orchestrator や各 worker が専用スレッドを
    起こす場合は、スレッド起動側で文脈を取得しスレッド内で再セットすること。

    ネストは内側が勝ち、``with`` を抜けると外側（無ければ未設定）へ戻る。
    """
    token = _model_override_var.set(
        _OverrideEntry(model=model, source=source, reasoning_effort=reasoning_effort)
    )
    try:
        yield
    finally:
        _model_override_var.reset(token)


def current_model_override() -> ResolvedModel | None:
    """現在有効な ``model_override`` を ``ResolvedModel`` として返す（無ければ None）。"""
    entry = _model_override_var.get()
    if entry is None:
        return None
    return ResolvedModel(
        model=entry.model,
        source=entry.source,
        source_label=_SOURCE_LABELS.get(entry.source, entry.source),
        reasoning_effort=entry.reasoning_effort,
    )


# ---------------------------------------------------------------------------
# 解決本体（設計書 §3）
# ---------------------------------------------------------------------------


def resolve_scene_model(
    feature: str,
    *,
    requested: str | None = None,
    capability: str | None = None,
) -> ResolvedModel:
    """設計書 §3 の解決順序で1件のモデルを決定する。

    優先順:
      1. 呼び出し側の明示引数（``requested``）
      2. 実行時オーバーライド（:func:`model_override`）
      3. ユーザー別ポリシー（``scope='user'``。Phase 0 は :class:`NullPolicyBackend` により常にスキップ）
      4. システムポリシー（``scope='system'``。同上）
      5. 環境変数（既存 ``*_LLM_MODEL``）
      6. tier 既定（``LLM_FAST_MODEL`` / ``LLM_ANALYSIS_MODEL``）

    Phase 0 では③④が常にスキップされるため、DB ポリシー行が無い環境（= 現行の
    全環境）では⑤⑥のみが効き、**従来のモデル決定と完全に一致する**
    （設計書 §11-6 の不変条件）。

    ``capability``（既定は feature / scene から導出）を満たさないポリシー行は
    **その層をスキップして下位の層へ落ちる**（M5 の fail-closed。scene 単位の既定に
    非 vision モデルが入っていても、vision が必要な feature を非 vision モデルへ
    落とさない）。env / tier 既定の層は既存設定なので capability で無効化しない
    （カタログに載っていない既存モデルを使えなくしてしまう害が大きい）。
    """
    if requested:
        return ResolvedModel(
            model=requested,
            source=SOURCE_CALL_ARGUMENT,
            source_label=_SOURCE_LABELS[SOURCE_CALL_ARGUMENT],
            reasoning_effort=None,
        )

    override = current_model_override()
    if override is not None:
        return override

    scene_key = scene_for_feature(feature)
    if scene_key is not None:
        required = capability or required_capability_for_feature(feature)
        backend = get_policy_backend()
        ctx = current_usage_context()
        user_id = ctx.user_id
        if user_id:
            user_row = backend.lookup(scene_key, feature, user_id)
            if (
                user_row is not None
                and user_row.scope == "user"
                and _policy_row_satisfies_capability(user_row, required, feature=feature, scope="user")
            ):
                return ResolvedModel(
                    model=user_row.model,
                    source=SOURCE_USER_POLICY,
                    source_label=_SOURCE_LABELS[SOURCE_USER_POLICY],
                    reasoning_effort=user_row.reasoning_effort,
                )
        system_row = backend.lookup(scene_key, feature, None)
        if (
            system_row is not None
            and system_row.scope == "system"
            and _policy_row_satisfies_capability(system_row, required, feature=feature, scope="system")
        ):
            return ResolvedModel(
                model=system_row.model,
                source=SOURCE_SYSTEM_POLICY,
                source_label=_SOURCE_LABELS[SOURCE_SYSTEM_POLICY],
                reasoning_effort=system_row.reasoning_effort,
            )

    env_model, fallback_tier = _env_model_and_tier(feature)
    if env_model:
        return ResolvedModel(
            model=env_model,
            source=SOURCE_ENV,
            source_label=_SOURCE_LABELS[SOURCE_ENV],
            reasoning_effort=None,
        )

    return ResolvedModel(
        model=_tier_default_model(fallback_tier),
        source=SOURCE_TIER_DEFAULT,
        source_label=_SOURCE_LABELS[SOURCE_TIER_DEFAULT],
        reasoning_effort=None,
    )


def effort_for_call(resolved: ResolvedModel, *, requested_effort: str | None) -> str | None:
    """呼び出し側の ``reasoning_effort`` 引数と解決結果を合成する。

    呼び出し側が明示指定していればそれを常に優先する。未指定のときは、解決結果の
    source がユーザー/システムポリシーまたは実行時オーバーライド由来
    （:data:`_POLICY_LIKE_SOURCES`）で、かつ ``reasoning_effort`` が設定されている
    場合のみ注入する。env / tier 既定由来では注入しない
    （Phase 0 の挙動不変条件 — 既存 ``generate_text`` は tier の effort を
    自動適用していないため、ここで新規に適用しない）。
    """
    if requested_effort is not None:
        return requested_effort
    if resolved.source in _POLICY_LIKE_SOURCES and resolved.reasoning_effort is not None:
        return resolved.reasoning_effort
    return None


# ---------------------------------------------------------------------------
# 既存 resolve_model() の委譲先（core/llm_worker/client.py 互換）
# ---------------------------------------------------------------------------


def resolve_for_setting(model_setting_key: str, *, fallback: str = "fast") -> str:
    """``core/llm_worker/client.py::resolve_model`` の実体。

    ``model_setting_key`` が :data:`_FEATURE_ENV_SETTINGS` の逆引きで feature を
    特定できれば :func:`resolve_scene_model` を通す。特定できない（新しい呼び出し元が
    未登録の設定キーを渡した等の）場合は、**従来ロジックのみ**にフォールバックする:
    ``getattr(settings, model_setting_key, "") or getattr(settings, fallback_attr)``。

    Phase 0 は :class:`NullPolicyBackend` のため、既知キーでも未知キーでも
    結果は従来のモデル決定と完全に一致する。
    """
    if fallback not in _TIER_ATTR:
        raise ValueError(f"unknown fallback tier: {fallback!r}")

    settings = get_settings()
    feature = _SETTING_KEY_TO_FEATURE.get(model_setting_key)
    if feature is None:
        fallback_attr = _TIER_ATTR[fallback]
        return getattr(settings, model_setting_key, "") or getattr(settings, fallback_attr)

    return resolve_scene_model(feature).model


# ---------------------------------------------------------------------------
# モデルカタログ（設計書 §5）
# ---------------------------------------------------------------------------


def load_catalog() -> dict | None:
    """``settings.llm_model_catalog_path`` の JSON をロードする。

    **パス未設定（空文字）のときは同梱カタログ** ``backend/config/llm_models.json``
    を使う（設計書 §5「既定同梱」/ CLAUDE.md M層節）。既定を空 = カタログ無しに
    すると、選択肢が1件も無い＝ UI の [変更] が押せない状態が既定になってしまうため
    （2026-07-28 に実機で発覚した不整合の是正）。同梱パスは **このファイルの位置から
    解決する** ので、cwd にも docker（``/app/core/config.py`` → ``/app/config/``）にも
    依存しない。

    カタログを明示的に無効化したい場合は、存在しないパスを ``LLM_MODEL_CATALOG_PATH``
    に指定する（下記のとおりファイル不在は None）。

    ``core.llm_usage.pricing.load_price_table`` と同型: ファイル不在・JSON パース失敗は
    すべて None（例外を投げない・架空の候補を捏造しない、M4）。
    """
    try:
        settings = get_settings()
        path = (settings.llm_model_catalog_path or "").strip() or str(_BUNDLED_CATALOG_PATH)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return None
        return data
    except Exception:
        logger.debug("llm_policy: failed to load model catalog", exc_info=True)
        return None


def catalog_models(*, capability: str | None = None) -> list[dict]:
    """現在の provider（+ 任意で capability）に絞ったカタログのモデル一覧を返す。

    カタログが無い/読めない環境では空リスト（呼び出し側が「現在の設定値」1件だけを
    表示するのは Phase 1 の UI 側の責務。ここでは捏造しない）。
    """
    catalog = load_catalog()
    if not catalog:
        return []
    settings = get_settings()
    provider = settings.llm_provider
    result: list[dict] = []
    for entry in catalog.get("models") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("provider") != provider:
            continue
        if capability is not None and capability not in (entry.get("capabilities") or []):
            continue
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# fail-closed 検証（設計書 §7・§11-2）— routes/llm_models.py と各 Phase 3 呼び出し側の
# 単一の真実源。scene_key（または feature 単独指定）に対して model が選択可能かを検証する。
# ---------------------------------------------------------------------------

# scene_key（または feature 単位のステージ別上書き）が vision capability を必須とする集合
# （M5）。scene 単位では pipeline.vision のみだが、pipeline:apparatus_semantics（ステージ別
# 上書き）・deliberation の vision 系（対話の figure 要素・教員指示付き図再解析）も同じ扱い。
_VISION_REQUIRED_SCENE_KEYS = frozenset(
    {
        SCENE_PIPELINE_VISION,
        "pipeline:apparatus_semantics",
        "deliberation:vision",
        "deliberation:figure_reanalysis",
    }
)


def required_capability_for_scene(scene_key: str) -> str | None:
    """scene_key が必須とする capability（現状 ``"vision"`` のみ）を返す。無ければ None。"""
    return "vision" if scene_key in _VISION_REQUIRED_SCENE_KEYS else None


def required_capability_for_feature(feature: str) -> str | None:
    """feature が必須とする capability を返す（feature 自身 → 所属 scene の順で判定）。

    ``deliberation:vision`` のように **feature は vision 必須だが所属 scene
    （``deliberation``）は必須でない**組み合わせがあるため、feature 側を先に見る
    （レビュー指摘 m2: scene 既定の継承で画像付きコールが text モデルへ行く経路を塞ぐ）。
    """
    direct = required_capability_for_scene(feature)
    if direct is not None:
        return direct
    scene_key = scene_for_feature(feature)
    return required_capability_for_scene(scene_key) if scene_key else None


def model_has_capability(model: str, capability: str) -> bool:
    """カタログ上で ``model`` が ``capability`` を持つかを返す。

    カタログが読めない環境では判定不能なので ``True``（fail-open）を返す — ポリシー行は
    カタログ検証を通ってしか作られない（``validate_model_for_scene`` はカタログ未設定を
    拒否する）ため、後からカタログが読めなくなったケースで既存の設定を無効化する方が
    害が大きい。
    """
    catalog = load_catalog()
    if catalog is None:
        return True
    valid_ids = {entry.get("id") for entry in catalog_models(capability=capability)}
    return model in valid_ids


def _policy_row_satisfies_capability(
    row: PolicyRow, required: str | None, *, feature: str, scope: str
) -> bool:
    """ポリシー行が feature の必須 capability を満たすか（満たさなければ warning + スキップ）。"""
    if not required:
        return True
    if model_has_capability(row.model, required):
        return True
    logger.warning(
        "llm_policy: skipping %s policy row for feature=%r — model %r lacks capability %r",
        scope,
        feature,
        row.model,
        required,
    )
    return False


def is_known_scene_key(scene_key: str) -> bool:
    """scene_key が :data:`SCENES` のキー、または ``KNOWN_FEATURES`` のいずれかであるかを返す
    （pipeline scene のステージ別任意上書き、設計書 §2 末尾）。"""
    return scene_key in SCENES or scene_key in KNOWN_FEATURES


def validate_model_for_scene(scene_key: str, model: str) -> str | None:
    """scene_key に対して model が選択可能かを検証する。

    問題が無ければ ``None``、問題があれば人間可読のエラー理由文字列を返す
    （例外は投げない — 呼び出し側の route が ``HTTPException(422, detail=reason)`` に
    変換する。fail-closed: カタログ未設定・未知 scene・カタログ外 model・capability
    不足・provider 不一致はすべてエラーを返す。M4/M5）。
    """
    if not is_known_scene_key(scene_key):
        return f"unknown scene_key: {scene_key!r}"
    if not model or not model.strip():
        return "model is required"

    catalog = load_catalog()
    if catalog is None:
        return "モデルカタログが未設定のため検証できません（LLM_MODEL_CATALOG_PATH 未設定）"

    capability = required_capability_for_scene(scene_key)
    candidates = catalog_models(capability=capability)
    valid_ids = {entry.get("id") for entry in candidates}
    if model not in valid_ids:
        return (
            f"model {model!r} is not a valid choice for scene {scene_key!r}"
            + (f" (requires capability={capability!r})" if capability else "")
        )
    return None


def pipeline_stage_label(stage: str) -> str:
    """ステージ名の日本語表示名を返す（未登録のステージは stage 名をそのまま返す）。"""
    return PIPELINE_STAGE_LABELS.get(stage, stage)


# ---------------------------------------------------------------------------
# env → DB シードの計画（レビュー指摘 J2 の是正）
#
# 旧実装（core/llm_policy_store.seed_env_policies）は env を **feature キー**
# （`pipeline:apparatus_semantics` 等）で書き込んでいたが、UI/API が編集するのは
# **scene キー**（`pipeline.vision` 等）で、`_pick_priority_row` の優先順は
# system+feature > system+scene。その結果「運用タブで保存しても効かず表示も古いまま」
# という恒久シャドウが起きていた（`APPARATUS_LLM_MODEL` は既定 "gpt-4o" = 非空なので
# 全新規環境で必ず発生）。
#
# 是正方針: **シードの書き込みキーを「UI が編集するキー」に揃える**。
#   - scene の代表 feature（`representative_feature_for_scene`）が持つ env は
#     **scene キー**でシードする（その env を共有する feature 群を1行でカバー）。
#   - 同じ scene に**別の env 変数**を持つ feature がある場合（例: 学習の裏方 =
#     TENSION / ANCHOR / RECON、前提の地図 = DOUBT_SCOPE / DOUBT_ASSUMPTION、
#     要素検討 = DELIBERATION / STDPART）は、その feature の値を落とさないために
#     従来どおり **feature キー**でシードする（env が feature 単位に分かれている、
#     という事実をそのまま写す。運用タブでは「ステージ別の指定」節に現れ、変更・解除できる）。
#   - `_FEATURE_DIRECT_ENV`（CTXEXPL / DISCUSS_OPENING）は運用タブのステージ別指定で
#     編集できるキーそのものなので、従来どおり feature キー。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvSeed:
    """1件の env → ``scope='system'`` 行シード計画。

    Attributes
    ----------
    scene_key : str
        書き込み先のキー（scene キー、または feature キー）。
    env_label : str
        note に残す環境変数名（例 ``"APPARATUS_LLM_MODEL"``）。
    model : str
        現在の env 値（非空のものだけが列挙される）。
    legacy_scene_keys : tuple[str, ...]
        同じ env から**旧実装が書いていた** feature キー群。同じ env・同じ値の
        シード行（= 人が触っていない行）に限り、移行時に削除して恒久シャドウを解消する。
    """

    scene_key: str
    env_label: str
    model: str
    legacy_scene_keys: tuple[str, ...] = ()


def iter_env_seeds() -> list[EnvSeed]:
    """現在の env から作るべき ``scope='system'`` 行の計画を返す（DB 非接触・純関数）。

    順序は :data:`SCENES` の宣言順 → scene 内 feature 順 → ``_FEATURE_DIRECT_ENV`` の
    宣言順で決定論的。値が空（未設定）の env は列挙しない。
    """
    seeds: list[EnvSeed] = []

    for scene_key, entry in SCENES.items():
        if is_read_only_scene(scene_key):
            # 読み取り専用の場面（learning_voice）はポリシー行を作らない（J5）。
            continue
        features = tuple(entry.get("features") or ())
        seeded_attrs: set[str] = set()

        representative = representative_feature_for_scene(scene_key)
        rep_mapping = _FEATURE_ENV_SETTINGS.get(representative)
        if rep_mapping is not None:
            attr, _tier = rep_mapping
            fact = env_setting_for_feature(representative)
            seeded_attrs.add(attr)
            if fact is not None:
                env_label, value = fact
                legacy = tuple(
                    f for f in features
                    if (_FEATURE_ENV_SETTINGS.get(f) or (None, None))[0] == attr
                )
                seeds.append(
                    EnvSeed(
                        scene_key=scene_key,
                        env_label=env_label,
                        model=value,
                        legacy_scene_keys=legacy,
                    )
                )

        for feature in features:
            mapping = _FEATURE_ENV_SETTINGS.get(feature)
            if mapping is None:
                continue
            attr, _tier = mapping
            if attr in seeded_attrs:
                continue
            seeded_attrs.add(attr)
            fact = env_setting_for_feature(feature)
            if fact is None:
                continue
            env_label, value = fact
            seeds.append(EnvSeed(scene_key=feature, env_label=env_label, model=value))

    for feature in _FEATURE_DIRECT_ENV:
        fact = env_setting_for_feature(feature)
        if fact is None:
            continue
        env_label, value = fact
        seeds.append(EnvSeed(scene_key=feature, env_label=env_label, model=value))

    return seeds


__all__ = [
    "PIPELINE_STAGE_LABELS",
    "pipeline_stage_label",
    "SCENES",
    "SCENE_PIPELINE",
    "SCENE_PIPELINE_VISION",
    "SCENE_COURSE_BUILDER",
    "SCENE_LECTURE_STUDIO",
    "SCENE_LEARNING_CHAT",
    "SCENE_LEARNING_VOICE",
    "SCENE_LEARNING_BACKGROUND",
    "SCENE_ATLAS",
    "SCENE_DOUBT",
    "SCENE_DELIBERATION",
    "SCENE_ASSISTANT",
    "SCENE_FIGURE_STUDIO",
    "scene_for_feature",
    "representative_feature_for_scene",
    "READ_ONLY_SCENE_KEYS",
    "is_read_only_scene",
    "read_only_scene_reason",
    "voice_model_facts",
    "voice_display_model",
    "env_setting_for_feature",
    "EnvSeed",
    "iter_env_seeds",
    "ResolvedModel",
    "PolicyRow",
    "PolicyBackend",
    "NullPolicyBackend",
    "get_policy_backend",
    "set_policy_backend",
    "reset_policy_backend_for_tests",
    "model_override",
    "current_model_override",
    "resolve_scene_model",
    "effort_for_call",
    "resolve_for_setting",
    "load_catalog",
    "catalog_models",
    "required_capability_for_scene",
    "required_capability_for_feature",
    "model_has_capability",
    "is_known_scene_key",
    "validate_model_for_scene",
    "SOURCE_CALL_ARGUMENT",
    "SOURCE_RUN_OVERRIDE",
    "SOURCE_COURSE_OVERRIDE",
    "SOURCE_USER_POLICY",
    "SOURCE_SYSTEM_POLICY",
    "SOURCE_ENV",
    "SOURCE_TIER_DEFAULT",
]
