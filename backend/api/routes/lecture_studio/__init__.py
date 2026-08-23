"""Episteme Graph — Lecture Script Studio (/api/admin) (Issue #70)。

教員向けレクチャー原稿の事前構築・AI補正エディタ。
- バッチスクリプト生成
- 手動スクリプト保存
- AIスクリプト書き換え
- バッチ音声生成

Tier 3-17a (2026-07): 単一ファイル (3,450行) だったモジュールをパッケージ化した。
import パス (``from routes.lecture_studio import router``) は不変。構成:

- ``_shared.py`` — scripts / pipeline / topics が共有する境界横断ヘルパー
  （チャンク取得・数式プレビュー・構造抽出等。router を持たない）
- ``scripts.py`` — 原稿・音声系（設定・バッチ生成・手動保存・AI書き換え・音声生成/試聴）
- ``pipeline.py`` — Agent Pipeline・構造再解析・コース内容生成系
- ``topics.py`` — 原稿スタジオ左ペイン系（章・トピック構造、授業用ドラフト、文書構造、
  理論コンポーネント一覧）

本ファイルは上記4モジュールの router を束ねるほか、既存テスト・呼び出し元との
互換性のため、参照されているシンボルを再エクスポートする。
"""

from __future__ import annotations

from fastapi import APIRouter

# services からの再エクスポート（既存コードでは未使用のまま輸入されていたものを含め、
# 分割前の `routes.lecture_studio.get_course_data` / `.user_can_edit_course` という
# 属性参照が引き続き解決できるよう保持する: tests/test_course_data_isolation.py）。
from services import get_course_data, user_can_edit_course  # noqa: F401

from . import _shared, pipeline, scripts, topics

router = APIRouter(tags=["Lecture Script Studio"])
router.include_router(scripts.router)
router.include_router(pipeline.router)
router.include_router(topics.router)


# ---------------------------------------------------------------------------
# 後方互換の再エクスポート（分割前は単一ファイルの module-level 属性だった）。
# テストコードが `from routes.lecture_studio import X` / `routes.lecture_studio.X`
# の形で参照しているシンボルをここに集約する。
# ---------------------------------------------------------------------------

# scripts.py 由来
from .scripts import (  # noqa: E402,F401
    _batch_audio_worker,
    _batch_generate_and_audio_worker,
    _batch_generate_worker,
    _chunk_status,
    _get_course_chunks,
    _load_chunk_slide_audio_map,
    _normalize_rewrite_result,
    _pg_session,
    _save_lecture_language,
    _save_lecture_studio_settings,
    batch_generate_audio,
    batch_generate_scripts,
    create_background_task,
    generate_spoken_text_and_formulas,
    generate_tts_audio,
    get_course_scripts,
    get_lecture_studio_settings,
    get_editable_course_data,
    get_viewable_course_data,
    preview_lecture_audio,
    threading,
    time,
    update_background_task,
    update_lecture_studio_settings,
)

# pipeline.py 由来
from .pipeline import (  # noqa: E402,F401
    DOCUMENT_PIPELINE_STAGE_LABELS,
    DOCUMENT_PIPELINE_STAGES,
    _load_pipeline_source,
    get_storage_client,
)

# topics.py 由来
from .topics import _normalize_check_questions  # noqa: E402,F401
