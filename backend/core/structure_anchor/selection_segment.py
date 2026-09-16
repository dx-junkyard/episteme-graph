"""選択逐語 → 教材区画番号の決定論的解決（学ぶ単位 P2-7・設計 §8）。

`structure_anchor` 付き痕跡の `anchor_id` が `seg_0` に固定される問題（親文書 C-11）の
サーバ側の受け皿。クライアントが区画番号を申告できないとき（＝レクチャー非再生中で
選択範囲の属する区画が取れないとき）に、**教材区画の本文と選択逐語の突き合わせ**だけで
番号を埋める。

規律:

- **非LLM・決定論**（LU3）。FastAPI / sqlalchemy / `core.llm` を import しない。
- **推測しない**（P1 / LU3 の捏造ガード）: 一致が 0 件のときも、複数区画に一致する
  ときも ``None`` を返す。呼び出し側はそのとき ``anchor_id=""``（＝場所は不明のまま）に
  縮退させる。`seg_0` を既定値として埋め戻さない。
- **情報を落とさない**（P4）: 解決できなくても痕跡そのもの（逐語 = ``evidence_quote``）は
  記録される。本モジュールは番号だけを扱う。
- **例外を外へ出さない**（fail-soft）。
"""

from __future__ import annotations

import re

from core.text_hygiene import strip_control_sequences

#: 一致検査の正規化。空白・改行・全角空白の違いで「不一致」にしないための比較キーを
#: 作るだけで、本文そのものは書き換えない（`core/assistant_context/selection.py` の
#: 選択逐語ブロックと同じ規則 — 片方だけが厳しいと「ブロックには載るのに区画は
#: 決まらない」というちぐはぐが起きる）。
_WHITESPACE_RE = re.compile(r"[\s　]+")

#: 埋め込み・プレースホルダーの綴り（``![[figure:xxx]]`` / ``[[FIGURE_1]]`` /
#: ``[[FORMULA_3]]`` / ``![[component:id]]``）。区画本文は、供給元によって
#: **解決済み**（``[[FIGURE_1]]``）と**未解決**（``![[figure:xxx]]``）のどちらの綴りにも
#: なり得る（配信は ``resolve_figure_embeds`` を通し、痕跡帰属は同期パスに DB クエリを
#: 足さないため通さない）。学習者の選択逐語にはどちらの綴りも現れない（画面では画像・
#: 数式として描かれている）ので、**両側から落として比較する** = 供給元の違いで
#: 「一致しない」にならないようにする（P2-R13）。
_EMBED_TOKEN_RE = re.compile(r"!?\[\[[^\]\n]{0,200}\]\]")


def _match_key(text: str) -> str:
    cleaned = _EMBED_TOKEN_RE.sub("", strip_control_sequences(str(text or "")))
    return _WHITESPACE_RE.sub("", cleaned)


def resolve_selection_segment(
    segments: list[str] | tuple[str, ...] | None, selection_text: str | None
) -> int | None:
    """選択逐語を含む教材区画の index を返す。決まらなければ ``None``。

    ``segments`` は学習者に配信された教材区画の本文を**表示順**に並べたもの
    （``get_topic_material`` が返す ``chunks[].text`` と同じ並び。フロントの
    ``data-segment-index`` と同じ単位でなければならない）。

    一意に決まるとき**だけ** index を返す:

    - 0 件一致（教材の外からの選択・引用の貼り付け・表示の書き換え後）→ ``None``
    - 複数区画に一致（同じ文が複数区画にある）→ ``None``（どちらかを選ばない）
    """
    try:
        key = _match_key(selection_text)
        if not key:
            return None
        hits = [
            index
            for index, segment in enumerate(segments or [])
            if key in _match_key(segment)
        ]
        if len(hits) != 1:
            return None
        return hits[0]
    except Exception:  # pragma: no cover - 防御的（fail-soft）
        return None
