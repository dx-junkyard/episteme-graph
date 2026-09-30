"""学習者向けルートの表示投影（``LearnerDisplayRoute``）。

正本設計書: ``docs/features/display_projection_design.md``（DP1 / DP6）。

学習者向けのルーター（``/api/learning`` / ``/api/me`` / ``/api/atlas``）は
``APIRouter(..., route_class=LearnerDisplayRoute)`` で作る。各エンドポイントの戻り値
（dict / list / pydantic モデル）は、FastAPI が ``response_model`` で直列化する**前に**
``core.display_projection.project_for_learner`` を必ず通る。

- エンドポイント関数そのものは差し替えない（モジュール属性は元の関数のまま）。
  ルート登録時にだけ ``functools.wraps`` した薄い包みを FastAPI へ渡すので、依存解決
  （``inspect.signature`` / 注釈の解決）は元の関数のまま動く。
- ``Response`` / ``StreamingResponse`` / ``None`` / 文字列は素通し（SSE の ``final`` は
  ``_sse_frames`` の中で同じ射影を通す）。
- 射影が例外を出しても応答は止めない（DP6）。警告を残して元の値を返す。
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

from fastapi.routing import APIRoute
from starlette.responses import Response

from core.display_projection import project_for_learner

logger = logging.getLogger(__name__)


def project_learner_payload(value: Any) -> Any:
    """エンドポイントの戻り値を学習者向けに射影する（対象外の型は素通し）。"""
    if value is None or isinstance(value, (Response, str, bytes)):
        return value
    if not isinstance(value, (dict, list, tuple)) and not callable(getattr(value, "model_dump", None)):
        return value
    try:
        return project_for_learner(value)
    except Exception:  # noqa: BLE001 — DP6: 射影の失敗で応答を止めない
        logger.warning("learner display projection failed", exc_info=True)
        return value


def project_learner_model(model: Any) -> Any:
    """pydantic モデルを射影し、同じ型のモデルへ戻す（SSE の ``final`` 用・DP6 で縮退）。"""
    try:
        return type(model).model_validate(project_for_learner(model))
    except Exception:  # noqa: BLE001
        logger.warning("learner display projection (model) failed", exc_info=True)
        return model


def wrap_learner_endpoint(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """エンドポイントを射影付きの包みにする（同期 / 非同期を保つ）。"""
    if getattr(endpoint, "__learner_display_projected__", False):
        return endpoint
    if inspect.iscoroutinefunction(endpoint):

        @functools.wraps(endpoint)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return project_learner_payload(await endpoint(*args, **kwargs))

        wrapper: Callable[..., Any] = _async_wrapper
    else:

        @functools.wraps(endpoint)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return project_learner_payload(endpoint(*args, **kwargs))

        wrapper = _sync_wrapper
    wrapper.__learner_display_projected__ = True  # type: ignore[attr-defined]
    return wrapper


class LearnerDisplayRoute(APIRoute):
    """戻り値を ``project_for_learner`` に通す APIRoute（DP1）。"""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, wrap_learner_endpoint(endpoint), **kwargs)


__all__ = [
    "LearnerDisplayRoute",
    "project_learner_model",
    "project_learner_payload",
    "wrap_learner_endpoint",
]
