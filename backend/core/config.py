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
    )

    # --- LLM ---
    # ベンダーニュートラルな変数名。後方互換のため OPENAI_* 環境変数も受け付ける。
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_analysis_model: str = Field(
        default="o3-mini",
        validation_alias=AliasChoices("LLM_ANALYSIS_MODEL", "OPENAI_ANALYSIS_MODEL"),
    )
    llm_embedding_model: str = Field(
        default="text-embedding-3-large",
        validation_alias=AliasChoices("LLM_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"),
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
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_public_endpoint: str = "localhost:9000"

    # --- GROBID ---
    grobid_url: str = "http://localhost:8070"

    # --- ISOM ---
    isom_output_dir: str = "output/incoming"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings のシングルトンを返す。"""
    return Settings()
