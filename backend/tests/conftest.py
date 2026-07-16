"""共通フィクスチャ — テスト実行時に .env や外部サービスなしで動作させる。"""

from __future__ import annotations

import sys
from pathlib import Path

# core/document_pipeline/tex_archive.py 等が src/ 配下の episteme_graph を import する。
# src/tests/ 側テストの収集順に依存せず import できるよう、ここで決定論的にパスへ載せる。
_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

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


@pytest.fixture(autouse=True)
def _reset_llm_usage_context():
    """bind_usage_context を使う worker 関数をテストがメインスレッドで直接呼ぶと
    contextvar が後続テストへ漏れるため、テストごとに既定値へ戻す。"""
    yield
    try:
        from core.llm_usage import context as _llm_usage_context
        _llm_usage_context._current_context.set(_llm_usage_context.UsageContext())
    except Exception:
        pass
