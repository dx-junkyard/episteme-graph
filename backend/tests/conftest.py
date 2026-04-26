"""共通フィクスチャ — テスト実行時に .env や外部サービスなしで動作させる。"""

from __future__ import annotations

import pytest

from core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _override_settings(monkeypatch):
    """get_settings() のキャッシュをクリアし、テスト用のダミー設定を注入する。"""
    get_settings.cache_clear()

    test_settings = Settings(
        llm_api_key="sk-test-dummy-key-for-ci",
        llm_analysis_model="gpt-4o",
        llm_embedding_model="text-embedding-3-large",
        jwt_secret="test-secret-key",
        admin_password="test-admin-password",
        database_url="postgresql://test:test@localhost:5432/test",
        neo4j_uri="bolt://localhost:7687",
        neo4j_auth="neo4j/test",
        minio_endpoint="localhost:9000",
        minio_access_key="testaccess",
        minio_secret_key="testsecret",
    )

    _getter = lambda: test_settings
    monkeypatch.setattr("core.config.get_settings", _getter)
    # dependencies.py imports get_settings as _get_settings — patch that reference too
    try:
        monkeypatch.setattr("api.dependencies._get_settings", _getter)
    except BaseException:
        pass  # api.dependencies が利用不可の環境（CI等）ではスキップ
    # test_error_logs.py がモジュールレベルで routes.error_logs をインポートするため、
    # 収集時に dependencies が top-level モジュールとして読み込まれる場合がある。
    # その場合、api.dependencies とは別オブジェクトになるため個別にパッチする。
    try:
        monkeypatch.setattr("dependencies._get_settings", _getter)
    except BaseException:
        pass  # dependencies が未インポートの場合はスキップ

    yield

    get_settings.cache_clear()
