"""k-匿名ゲートの共通実装（FastAPI 非依存, 提案8）。

D層（``core/doubt/naive_signal.py`` 経由の ``core/doubt/schema.py``）/
R層（``core/reconstruction/health.py`` → ``stumble.py``）/ B層集計
（``api/services.py`` の tension/anchor ヒートマップ）で、k=3 の閾値判定と
「3-5 / 6-10 / 11+」の件数レンジ導出がそれぞれ独立実装されていた。ここに集約する
のは **閾値そのもの** と **レンジ境界ロジック（<=5 / <=10 / それ以外）** だけ。

閾値未満のときの表現（空文字 / "まだデータなし" / 行ごと省略 等）や、レスポンスの
キー名・形式は呼び出し側ごとに異なり、意図的にそれぞれへ残す（1つも変えない）。
このモジュールはそれらの表現を決め打ちしない。
"""

from __future__ import annotations

# 学習者由来シグナルを教員向けに集計する全レイヤー共通の k-匿名しきい値。
# n（人数・件数）がこれ未満のセルは非表示 / 非開示にする（個人特定不可能にする）。
K_ANONYMITY = 3


def meets_k_anonymity(n: int, k: int = K_ANONYMITY) -> bool:
    """人数（または件数）が k-匿名の閾値を満たすか。"""
    return n >= k


def bucket_count_range(n: int) -> str:
    """件数を "3-5" / "6-10" / "11+" のレンジ文字列へバケット化する。

    n が閾値未満のときの意味は保証しない（呼び出し側で先に
    ``meets_k_anonymity`` 相当の判定を行うこと）。
    """
    if n <= 5:
        return "3-5"
    if n <= 10:
        return "6-10"
    return "11+"


def count_range_gated(n: int, *, below_label: str, k: int = K_ANONYMITY) -> str:
    """件数レンジを k-匿名ゲート付きで返す（n < k は ``below_label``）。"""
    if n < k:
        return below_label
    return bucket_count_range(n)


def gate_label(label: str, n: int, *, below_label: str, k: int = K_ANONYMITY) -> str:
    """人数が k 未満なら ``below_label``、以上なら ``label`` を返す。"""
    return label if meets_k_anonymity(n, k) else below_label
