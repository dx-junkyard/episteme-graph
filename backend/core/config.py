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

    # --- JWT / Auth ---
    jwt_secret: str = "episteme-dev-secret-change-in-prod"
    admin_password: str = ""

    # --- PostgreSQL ---
    database_url: str = "postgresql://episteme:episteme@postgres:5432/episteme"

    # --- Neo4j ---
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_auth: str = "neo4j/password"

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings のシングルトンを返す。"""
    return Settings()
