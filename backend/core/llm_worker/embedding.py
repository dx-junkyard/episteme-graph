"""U層計測つきの embedding 呼び出し（FastAPI 非 import）。

``core.llm`` の遅延 import + ``usage_context(feature=...)` + ``generate_embeddings``
という同一の3行が、埋め込みを使う層ごとにコピーされていた
（``core/help_kb/vector.py`` / ``core/atlas_vectors/builder.py`` /
``core/paper_discovery/ranking.py``）。本モジュールはその**遅延 import と
計測の張り方**だけを集約する。

遅延 import の理由（各コピーのコメントに書かれていた意図をここへ集約）:
``core.llm`` はベンダ SDK と設定の解決を引き連れるため、埋め込みを使うだけの
モジュールを import しただけでその重さが入口に持ち込まれるのを避ける。

各層に残るもの: 何を埋め込むか（合成テキストの組み立て）・バッチの切り方・
コスト上限（``CostGate``）・保存先・失敗時の縮退。
"""

from __future__ import annotations

from typing import Any


def embed_with_context(
    texts: list[str],
    *,
    feature: str,
    model: str | None = None,
    **attrs: Any,
) -> list[list[float]]:
    """``texts`` を1バッチで埋め込む（U層 feature 帰属つき）。

    Parameters
    ----------
    feature:
        U層の feature キー（``core/llm_usage/schema.py::KNOWN_FEATURES`` の要素）。
    model:
        既定（``None``）は Settings の embedding モデル。pgvector の次元と結合して
        いるため、呼び出し側が上書きするのは原則テスト用途のみ。
    **attrs:
        ``usage_context`` の帰属 ID（``user_id`` / ``document_id`` / ``course_id`` /
        ``run_id``）。未指定のものは外側の文脈をそのまま引き継ぐ。
    """
    from core.llm import generate_embeddings
    from core.llm_usage.context import usage_context

    with usage_context(feature, **attrs):
        if model is None:
            return generate_embeddings(texts)
        return generate_embeddings(texts, model=model)


__all__ = ["embed_with_context"]
