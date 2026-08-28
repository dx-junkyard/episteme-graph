"""Episteme Graph — 設定の一元管理モジュール。

すべての環境変数をこのモジュールに集約し、他のモジュールでは
``os.environ`` を直接参照しない。pydantic-settings の ``BaseSettings``
を利用してバリデーション付きで読み込む。

Usage::

    from core.config import get_settings

    settings = get_settings()
    print(settings.database_url)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """アプリケーション全体の設定。

    環境変数または ``.env`` ファイルから自動的に読み込まれる。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- LLM ---
    # プロバイダ切替 (openai | gemini | google | gemini-vertex)。.env の LLM_PROVIDER で切り替える。
    # google: Vertex AI ADC 認証（google-cloud-aiplatform / vertexai SDK を使用）
    # gemini-vertex: Vertex AI ADC 認証 (廃止予定)
    llm_provider: Literal["openai", "gemini", "google", "gemini-vertex"] = Field(
        default="openai",
        validation_alias=AliasChoices("LLM_PROVIDER"),
    )
    # ベンダーニュートラルな変数名。後方互換のため OPENAI_* / GEMINI_* 環境変数も受け付ける。
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LLM_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"
        ),
    )
    llm_analysis_model: str = Field(
        default="o3-mini",
        validation_alias=AliasChoices("LLM_ANALYSIS_MODEL", "OPENAI_ANALYSIS_MODEL"),
    )
    llm_embedding_model: str = Field(
        default="text-embedding-3-large",
        validation_alias=AliasChoices("LLM_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"),
    )
    # 埋め込みベクトルの次元数。pgvector のスキーマと一致させる必要がある。
    # OpenAI text-embedding-3-large = 3072、Gemini text-embedding-004 = 768 など。
    llm_embedding_dim: int = Field(
        default=3072,
        validation_alias=AliasChoices("LLM_EMBEDDING_DIM"),
    )
    llm_max_retries: int = Field(
        default=3,
        validation_alias=AliasChoices("LLM_MAX_RETRIES", "EPISTEME_LLM_MAX_RETRIES"),
    )
    llm_retry_backoff_seconds: float = Field(
        default=1.5,
        validation_alias=AliasChoices("LLM_RETRY_BACKOFF_SECONDS", "EPISTEME_LLM_RETRY_BACKOFF_SECONDS"),
    )

    # --- LLM マルチモード設定 ---
    # Fast: 意図分類、フォーマット整形など軽量タスク
    llm_fast_model: str = "gpt-5.4-nano"
    llm_fast_effort: Literal["low", "medium", "high"] = "low"

    # Standard: 通常の対話、前提知識評価、論理構造抽出
    llm_standard_model: str = "gpt-5.2"
    llm_standard_effort: Literal["low", "medium", "high"] = "medium"

    # Deep: 深刻な誤解の訂正、複雑な数式展開、グラフ依存関係解決
    llm_deep_model: str = "gpt-5.2"
    llm_deep_effort: Literal["low", "medium", "high"] = "high"

    # --- モデルティアごとの最大出力トークン数 ---
    # エージェント LLM 呼び出しの ``max_tokens`` は、使用モデルのティアに応じて
    # 切り替える。Fast は 400k、それ以外（Standard / Analysis / Deep）は 1M を
    # デフォルトとする。出力切断（truncation）を避けるための上限値。
    llm_fast_model_max_tokens: int = Field(
        default=128_000,
        validation_alias=AliasChoices("LLM_FAST_MODEL_MAX_TOKENS"),
    )
    llm_standard_model_max_tokens: int = Field(
        default=128_000,
        validation_alias=AliasChoices("LLM_STANDARD_MODEL_MAX_TOKENS"),
    )
    llm_analysis_model_max_tokens: int = Field(
        default=128_000,
        validation_alias=AliasChoices("LLM_ANALYSIS_MODEL_MAX_TOKENS"),
    )
    llm_deep_model_max_tokens: int = Field(
        default=128_000,
        validation_alias=AliasChoices("LLM_DEEP_MODEL_MAX_TOKENS"),
    )

    # --- 音声文字起こし（ハンズフリー会話） ---
    # openai プロバイダのみ対応（whisper-1 / gpt-4o-mini-transcribe 等）
    llm_transcribe_model: str = Field(
        default="whisper-1",
        validation_alias=AliasChoices("LLM_TRANSCRIBE_MODEL"),
    )

    # --- レクチャースライド同期 + 音声言語切替 (migration 040) ---
    # OpenAI TTS の voice。多言語対応の音声のため言語による分岐は不要（既定 "alloy"）。
    lecture_tts_voice: str = Field(
        default="alloy",
        validation_alias=AliasChoices("LECTURE_TTS_VOICE"),
    )

    # --- TensionMiningAgent (B層) — コスト制御（設計書 §8） ---
    # 1セッション（user×course×topic×日）あたりの LLM コール上限
    tension_max_calls_per_session: int = Field(
        default=3,
        validation_alias=AliasChoices("TENSION_MAX_CALLS_PER_SESSION"),
    )
    # 1ユーザー1日あたりの LLM コール上限
    tension_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("TENSION_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    tension_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("TENSION_LLM_MODEL"),
    )

    # --- StructureAnchorAgent (B層) — コスト制御（tension とは独立のカウンタ） ---
    # 1セッション（user×course×topic×日）あたりの LLM コール上限
    anchor_max_calls_per_session: int = Field(
        default=3,
        validation_alias=AliasChoices("ANCHOR_MAX_CALLS_PER_SESSION"),
    )
    # 1ユーザー1日あたりの LLM コール上限
    anchor_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("ANCHOR_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    anchor_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("ANCHOR_LLM_MODEL"),
    )
    # 回答末尾の帰属確認プロンプト（方法C）のセッション内提示上限（P7: 毎回出さない）
    anchor_confirm_max_per_session: int = Field(
        default=3,
        validation_alias=AliasChoices("ANCHOR_CONFIRM_MAX_PER_SESSION"),
    )

    # --- D層（Doubt Layer） — コスト制御（tension / anchor とは独立のカウンタ） ---
    # 検証スコープ候補抽出（D1-4）の 1 日あたり LLM コール上限
    doubt_scope_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("DOUBT_SCOPE_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    doubt_scope_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("DOUBT_SCOPE_LLM_MODEL"),
    )
    # 暗黙前提マイニングの LLM 正規化（D2-2）の 1 日あたりコール上限
    doubt_assumption_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("DOUBT_ASSUMPTION_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    doubt_assumption_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("DOUBT_ASSUMPTION_LLM_MODEL"),
    )
    # 反証条件候補抽出（SL-1, 賭け金の台帳）の 1 日あたり LLM コール上限
    # （doubt_scope / doubt_assumption とは独立のカウンタ）
    doubt_falsification_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("DOUBT_FALSIFICATION_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    doubt_falsification_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("DOUBT_FALSIFICATION_LLM_MODEL"),
    )

    # --- 再構成ループ（Reconstruction Loop, R層） — item オーサリングのコスト制御 ---
    # （tension / anchor / doubt とは独立のカウンタ）
    # 1 ドキュメントあたりの自動生成 item 上限
    recon_max_items_per_document: int = Field(
        default=30,
        validation_alias=AliasChoices("RECON_MAX_ITEMS_PER_DOCUMENT"),
    )
    # item オーサリング worker の 1 日あたり LLM コール上限
    recon_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("RECON_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    recon_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("RECON_LLM_MODEL"),
    )

    # --- Field Atlas 骨格エディタ AIアシスト編集（interpret / propose。P3/P4） ---
    # 1教員1日あたりの assist LLM コール上限（interpret + propose の合算）
    atlas_assist_max_calls_per_day: int = Field(
        default=60,
        validation_alias=AliasChoices("ATLAS_ASSIST_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら分析 tier（llm_analysis_model）に委譲（対象特定・編集生成には精度が要る）
    atlas_assist_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("ATLAS_ASSIST_LLM_MODEL"),
    )

    # --- 横断ユーティリティ層（Admin Copilot） — コスト制御（他機能とは独立） ---
    # chat の intent 分類/応答の 1 ユーザー 1 日あたり LLM コール上限
    assistant_max_calls_per_day: int = Field(
        default=20,
        validation_alias=AliasChoices("ASSISTANT_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    assistant_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("ASSISTANT_LLM_MODEL"),
    )

    # --- 画像読み取りパイプライン（apparatus_semantics） — コスト制御（他機能とは独立） ---
    # vision 同定に使う LLM モデル（OpenAI 経路のみ対応。§5-4）
    apparatus_llm_model: str = Field(
        default="gpt-4o",
        validation_alias=AliasChoices("APPARATUS_LLM_MODEL"),
    )
    # 1 document あたり vision 対象にする図の上限（超過分は skipped_by_limit で保持, P4）
    apparatus_max_images_per_document: int = Field(
        default=20,
        validation_alias=AliasChoices("APPARATUS_MAX_IMAGES_PER_DOCUMENT"),
    )
    # vision 呼び出しの日次上限（他機能と独立のカウンタ）
    apparatus_max_calls_per_day: int = Field(
        default=30,
        validation_alias=AliasChoices("APPARATUS_MAX_CALLS_PER_DAY"),
    )
    # 例示画像の few-shot 添付（含有承認済みのみ。既定 off — コスト・権利の両面で保守的に）
    apparatus_fewshot_images: bool = Field(
        default=False,
        validation_alias=AliasChoices("APPARATUS_FEWSHOT_IMAGES"),
    )
    # ライブラリ retrieval（凍結版のみ）の候補数
    apparatus_retrieval_top_k: int = Field(
        default=5,
        validation_alias=AliasChoices("APPARATUS_RETRIEVAL_TOP_K"),
    )
    # 図ごとの周辺本文（figure_context 収集）の最大ブロック数
    apparatus_context_max_items: int = Field(
        default=12,
        validation_alias=AliasChoices("APPARATUS_CONTEXT_MAX_ITEMS"),
    )
    # 図ごとの周辺本文（figure_context 収集）の合計文字数上限
    apparatus_context_max_chars: int = Field(
        default=6000,
        validation_alias=AliasChoices("APPARATUS_CONTEXT_MAX_CHARS"),
    )
    # --- 図解析の反復照合パイプライン（#499） ---
    # "iterative"（文脈仮説→独立観察→照合→ギャップ駆動再スキャン）| "one_shot"（旧方式）
    apparatus_analysis_mode: str = Field(
        default="iterative",
        validation_alias=AliasChoices("APPARATUS_ANALYSIS_MODE"),
    )
    # バッチ解析（document pipeline）でのギャップ駆動再スキャンの最大試行回数
    apparatus_verify_max_iterations: int = Field(
        default=3,
        validation_alias=AliasChoices("APPARATUS_VERIFY_MAX_ITERATIONS"),
    )
    # 教員指示付き再解析（同期API）での最大試行回数。応答時間を守るため既定は控えめ
    apparatus_reanalyze_max_iterations: int = Field(
        default=1,
        validation_alias=AliasChoices("APPARATUS_REANALYZE_MAX_ITERATIONS"),
    )

    # --- W層（Element Deliberation Workspace, 対話的検討） — コスト制御（他機能とは独立） ---
    # 1セッションあたりの LLM コール上限
    deliberation_max_calls_per_session: int = Field(
        default=8,
        validation_alias=AliasChoices("DELIBERATION_MAX_CALLS_PER_SESSION"),
    )
    # 1ユーザー1日あたりの LLM コール上限
    deliberation_max_calls_per_day: int = Field(
        default=40,
        validation_alias=AliasChoices("DELIBERATION_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    deliberation_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("DELIBERATION_LLM_MODEL"),
    )
    # 対話 grounding へ供給する同一性候補（同分野の凍結済み library_entries）の件数上限
    # （N2: 供給は事実の一覧のみで LLM に同一視を促しすぎない・apparatus_retrieval_top_k と同型）
    deliberation_identity_candidates_top_k: int = Field(
        default=5,
        validation_alias=AliasChoices("DELIBERATION_IDENTITY_CANDIDATES_TOP_K"),
    )

    # --- 教材図スタジオ（Teaching Figure Studio, migration 063） — コスト制御・上限 ---
    # 正本: docs/features/teaching_figure_studio_design.md §4.3
    # 空文字なら fast tier（llm_fast_model）に委譲。図の対話生成とギャップ提案で共有する
    # （同一設定キーの共有は atlas / deliberation に前例あり）
    figure_studio_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("FIGURE_STUDIO_LLM_MODEL"),
    )
    # 図の対話生成（1教員1日あたり。1図あたり数ターンの往復を想定）
    figure_studio_max_calls_per_day: int = Field(
        default=60,
        validation_alias=AliasChoices("FIGURE_STUDIO_MAX_CALLS_PER_DAY"),
    )
    # ギャップ提案の生成（1教員1日あたり。対話とは独立のカウンタ）
    figure_suggest_max_calls_per_day: int = Field(
        default=20,
        validation_alias=AliasChoices("FIGURE_SUGGEST_MAX_CALLS_PER_DAY"),
    )
    # 保存できる SVG のサイズ上限（サニタイザが入力・出力の両方で検査する）
    teaching_figure_max_svg_bytes: int = Field(
        default=200_000,
        validation_alias=AliasChoices("TEACHING_FIGURE_MAX_SVG_BYTES"),
    )
    # 1トピックあたりのギャップ提案の最大件数（1コールで返させる上限）
    teaching_figure_max_suggestions: int = Field(
        default=4,
        validation_alias=AliasChoices("TEACHING_FIGURE_MAX_SUGGESTIONS"),
    )

    # --- カテゴリギャップ候補（migration 066） ---
    # 正本: docs/features/category_gap_candidates_design.md §5.1
    # 1 document あたりに保存するギャップ信号の上限（追加 LLM コールは無く、
    # landscape_placement の同一コールに相乗りする。0 で申告させない）
    landscape_gap_max_per_document: int = Field(
        default=3,
        validation_alias=AliasChoices("LANDSCAPE_GAP_MAX_PER_DOCUMENT"),
    )

    # --- 分野マップのベクトル係留層（VA層, migration 074） ---
    # 正本: docs/features/atlas_vector_anchoring_design.md §5
    # アンカーベクトル構築・ギャップ近傍注記の embedding バッチ回数の日次上限
    # （CostGate は in-memory。超過は fail-soft で従来動作へ縮退する — VA4）
    atlas_vector_max_calls_per_day: int = Field(
        default=50,
        validation_alias=AliasChoices("ATLAS_VECTOR_MAX_CALLS_PER_DAY"),
    )
    # 正本: 同 §6（配置の前段絞り込み）
    # ドメインごとに LLM へ閉世界提示する concept ノードの上限（0 で絞り込み無効。
    # region は常に全提示 — gap 検出の親 region 選択肢を狭めないため）
    landscape_vector_prefilter_topk: int = Field(
        default=32,
        validation_alias=AliasChoices("LANDSCAPE_VECTOR_PREFILTER_TOPK"),
    )

    # --- 標準化判定 worker（Phase S, 知識ネットワークビジョン §6） — コスト制御（他機能とは独立） ---
    # 三角測量の証拠①（LLM 事前知識）の 1 日あたり LLM コール上限
    stdpart_max_calls_per_day: int = Field(
        default=10,
        validation_alias=AliasChoices("STDPART_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら fast tier（llm_fast_model）に委譲
    stdpart_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("STDPART_LLM_MODEL"),
    )

    # --- チャット型 AI 支援の共通基盤整理（正本: docs/features/assistant_common_infra_design.md） ---
    # 既定はすべて現行挙動を保存する（I1）。
    # 学習チャット本体（1リクエスト=1カウント。intent 分類〜本体を含めて1）
    learning_chat_max_calls_per_day: int = Field(
        default=300,
        validation_alias=AliasChoices("LEARNING_CHAT_MAX_CALLS_PER_DAY"),
    )
    # コースビルダーチャット（他機能とは独立のカウンタ）
    course_builder_max_calls_per_day: int = Field(
        default=100,
        validation_alias=AliasChoices("COURSE_BUILDER_MAX_CALLS_PER_DAY"),
    )
    # 原稿スタジオ rewrite（scripts.py / topics.py の両経路で同一ゲートインスタンス）
    lecture_rewrite_max_calls_per_day: int = Field(
        default=100,
        validation_alias=AliasChoices("LECTURE_REWRITE_MAX_CALLS_PER_DAY"),
    )
    # 空文字なら analysis tier（llm_analysis_model）に委譲（現行の暗黙依存を明示化, I1）
    learning_chat_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("LEARNING_CHAT_LLM_MODEL"),
    )
    # 空文字なら analysis tier（llm_analysis_model）に委譲（I1）
    course_builder_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("COURSE_BUILDER_LLM_MODEL"),
    )

    # --- JWT / Auth ---
    jwt_secret: str = "episteme-dev-secret-change-in-prod"
    admin_password: str = ""

    # --- PostgreSQL ---
    database_url: str = "postgresql://episteme:episteme@postgres:5432/episteme"

    # --- MinIO ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = Field(
        default="minioadmin",
        validation_alias=AliasChoices("MINIO_ACCESS_KEY", "MINIO_ROOT_USER"),
    )
    minio_secret_key: str = Field(
        default="minioadmin",
        validation_alias=AliasChoices("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
    )
    minio_public_endpoint: str = "localhost:9000"

    # --- CORS ---
    # カンマ区切りでオリジンを指定。"*" はワイルドカード（開発用デフォルト）。
    # 例: CORS_ORIGINS=https://your-subdomain.ngrok-free.app,http://localhost:3000
    cors_origins: str = Field(
        default="*",
        validation_alias=AliasChoices("CORS_ORIGINS"),
    )

    # --- Admin error log analysis ---
    # Admin 画面で保持・返却するメモリ上のログ最大件数。
    admin_error_log_max_items: int = Field(
        default=1000,
        validation_alias=AliasChoices("ADMIN_ERROR_LOG_MAX_ITEMS"),
    )

    # --- GROBID ---
    grobid_url: str = Field(
        default="http://localhost:8070",
        validation_alias=AliasChoices("GROBID_URL"),
    )

    # --- ISOM ---
    isom_output_dir: str = "output/incoming"

    # --- Google Cloud / Vertex AI ---
    # LLM_PROVIDER=google または gemini-vertex のときに参照される。
    # ADC は gcloud auth application-default login または GOOGLE_APPLICATION_CREDENTIALS で設定すること。
    gcp_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
    )
    gcp_location: str = Field(
        default="us-central1",
        validation_alias=AliasChoices("GCP_LOCATION", "GOOGLE_CLOUD_LOCATION"),
    )
    gcp_use_vertex_ai: bool = Field(
        default=True,
        validation_alias=AliasChoices("GCP_USE_VERTEX_AI"),
    )
    # GCP 認証ファイルの絶対パス。設定されている場合は llm.py 内で
    # os.environ["GOOGLE_APPLICATION_CREDENTIALS"] に明示的にセットする。
    # Docker では /app/.gcp/credentials.json が既定値 (docker-compose.yml 参照)。
    google_application_credentials: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_APPLICATION_CREDENTIALS"),
    )

    # --- Google Cloud / Vertex AI (廃止予定) ---
    # 後方互換のため残存。新規利用には gcp_project_id / gcp_location を使用すること。
    google_cloud_project: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_CLOUD_PROJECT"),
    )
    google_cloud_location: str = Field(
        default="us-central1",
        validation_alias=AliasChoices("GOOGLE_CLOUD_LOCATION"),
    )

    # --- Domain Cartridge ---
    # 使用するデフォルトカートリッジID。未指定時は particle_physics。
    default_cartridge_id: str = Field(
        default="particle_physics",
        validation_alias=AliasChoices("EPISTEME_DEFAULT_CARTRIDGE_ID"),
    )
    # カートリッジ定義ディレクトリを上書きしたい場合のみ指定。未指定時は backend/cartridges。
    cartridges_dir: str = Field(
        default="",
        validation_alias=AliasChoices("EPISTEME_CARTRIDGES_DIR"),
    )

    # --- Field Atlas (分野の地図) ---
    # 学習UIの地図データソース。フロント (atlas-data.js) が /api/atlas/runtime-config
    # 経由で参照する。既定 "api" (本番でモック地図が全ユーザーに出るのを構造的に防ぐ)。
    # 開発でフィクスチャを使いたい場合のみ明示的に "fixture" に設定する。
    atlas_data_source: str = Field(
        default="api",
        validation_alias=AliasChoices("ATLAS_DATA_SOURCE"),
    )

    # --- U層（LLM 使用量計測, migration 043） ---
    # false で record() を no-op に（テスト・ローカル用）
    llm_usage_tracking_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("LLM_USAGE_TRACKING_ENABLED"),
    )
    # in-memory バッファ上限。超過分は最古から破棄し dropped_events に計上する（U8）
    llm_usage_buffer_max: int = Field(
        default=1000,
        validation_alias=AliasChoices("LLM_USAGE_BUFFER_MAX"),
    )
    # バックグラウンド flusher の周期（秒）
    llm_usage_flush_interval_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("LLM_USAGE_FLUSH_INTERVAL_SECONDS"),
    )
    # この件数到達で flusher を即時起こす
    llm_usage_flush_batch: int = Field(
        default=100,
        validation_alias=AliasChoices("LLM_USAGE_FLUSH_BATCH"),
    )
    # モデル単価表 (JSON) のパス。空なら cost_usd は常に null（U7: 価格をハードコードしない）
    llm_price_table_path: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_PRICE_TABLE_PATH"),
    )

    # --- 論文ディスカバリー層 Phase 3（正本: docs/features/paper_discovery_design.md §6） ---
    # 候補の関連度ランキング（embedding 1バッチ = 1コール）の1日あたり上限。
    # 上限到達は失敗ではなく「新着順で表示する」への fail-soft（検索自体は成立させる）。
    discovery_ranking_max_calls_per_day: int = Field(
        default=100,
        validation_alias=AliasChoices("DISCOVERY_RANKING_MAX_CALLS_PER_DAY"),
    )
    # 引用グラフによる第2の候補供給源（Semantic Scholar）のオプトイン。
    # 既定 off — 外部 API の追加は SYSTEM_ADMIN の明示設定で有効化する（設計書 §6）。
    discovery_citation_source_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("DISCOVERY_CITATION_SOURCE_ENABLED"),
    )

    # --- 論文レーダー（正本: docs/features/paper_radar_design.md §5.6） ---
    # 起点論文と候補の比較分析（1リクエスト = 1コール）の1日あたり上限（教員ごと）。
    # 上限到達は 429 + 事実文（数値を返さない）。検索・帯分けとは独立のカウンタで、
    # 帯分けの embedding は既存 DISCOVERY_RANKING_MAX_CALLS_PER_DAY を共有する。
    discovery_compare_max_calls_per_day: int = Field(
        default=20,
        validation_alias=AliasChoices("DISCOVERY_COMPARE_MAX_CALLS_PER_DAY"),
    )
    # 比較分析のモデル。空文字なら fast tier（llm_fast_model）に委譲。
    discovery_compare_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("DISCOVERY_COMPARE_LLM_MODEL"),
    )

    # --- M層（LLM モデル選択, core/llm_policy.py） ---
    # モデルカタログ (JSON) のパス。空/不在/パース不能なら catalog_models() は空リスト
    # を返す（M4: 選択肢を捏造しない）。正本: docs/features/llm_model_selection_design.md §5
    llm_model_catalog_path: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_MODEL_CATALOG_PATH"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings のシングルトンを返す。"""
    return Settings()
