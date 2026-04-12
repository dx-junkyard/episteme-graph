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
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_public_endpoint: str = "localhost:9000"

    # --- GROBID ---
    grobid_url: str = "http://localhost:8070"

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings のシングルトンを返す。"""
    return Settings()
