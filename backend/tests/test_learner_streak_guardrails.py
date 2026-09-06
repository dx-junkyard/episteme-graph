"""連続学習日数（streak）を学習者に出さないガードレール。

正本: docs/features/understanding_cycle_design.md の不変条項 **UC4**
「セッション間は何もしない — 督促・連続日数・未消化バッジ・忘却曲線を作らない」。
「間」を埋めないこと自体が設計なので、連続した学習日数を数えて見せる計器は、
たとえ小さな一行であっても UC4 に正面から反する。

2026-09-05 の是正で以下を撤去した:

- `frontend/public/index.html` トップバーの `<span id="streak">`（「◯日連続学習中」）
- `frontend/public/js/app.js` 学習サマリの「連続学習日数」カードと、上の span の更新
- `backend/api/services.py` の `calculate_streak()` と `calculate_progress` の
  `streak_days` キー
- `backend/api/schemas.py` の `LearningProgress.streak_days`（学習者向け DTO）

このテストは「戻ってきていないこと」を構造的に固定する。再導入したくなったときは、
テストを消す前に UC4 の裁定からやり直すこと。
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend" / "public"

# 学習者が見る面（管理画面は対象外 — 教員向けの内部運用カウントとは別の話）。
_LEARNER_SOURCES = (
    FRONTEND / "index.html",
    FRONTEND / "js" / "app.js",
    FRONTEND / "js" / "discuss.js",
    FRONTEND / "js" / "personal-map.js",
    FRONTEND / "js" / "personal-map-home.js",
)

_BACKEND_SOURCES = (
    BACKEND / "api" / "services.py",
    BACKEND / "api" / "schemas.py",
    BACKEND / "api" / "routes" / "learning.py",
    BACKEND / "api" / "routes" / "cycle.py",
)

# 撤去した語彙。コメント（是正の来歴を残すために語を含む）は除いて検査する。
_FORBIDDEN = ("streak", "日連続", "連続学習", "連続日数")


def _strip_comments(text: str, *, html: bool) -> str:
    """コメントを落とした本文を返す。

    是正の来歴コメントには撤去語彙そのものが書いてあるため、コメントごと禁止すると
    「なぜ消したか」を書き残せなくなる。検査対象は実際に動く／配信される本文だけ。
    """
    if html:
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    # 行コメント（URL の "//" を巻き込まないよう、直前が ":" でない場合のみ）。
    text = re.sub(r"(?m)(?<!:)//.*$", " ", text)
    text = re.sub(r"(?m)^\s*#.*$", " ", text)  # Python の行コメント
    return text


class TestStreakRemovedFromLearnerSurfaces:
    def test_learner_frontend_has_no_streak_vocabulary(self):
        problems = []
        for path in _LEARNER_SOURCES:
            if not path.exists():
                continue
            body = _strip_comments(
                path.read_text(encoding="utf-8"), html=path.suffix == ".html"
            )
            for word in _FORBIDDEN:
                if word in body:
                    problems.append(f"{path.name}: {word!r}")
        assert problems == [], (
            "UC4: 学習者の画面に連続日数の計器を出さない（督促・未消化バッジを作らない）。"
            + " / ".join(problems)
        )

    def test_no_streak_element_id_in_learning_page(self):
        """トップバーの `<span id="streak">` は撤去済み（DOM ごと無い）。"""
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        assert 'id="streak"' not in html


class TestStreakRemovedFromBackend:
    def test_calculate_streak_is_gone(self):
        """計算関数ごと撤去する（呼ばれない関数として残さない）。"""
        src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        assert "def calculate_streak" not in src

    def test_backend_has_no_streak_vocabulary(self):
        problems = []
        for path in _BACKEND_SOURCES:
            if not path.exists():
                continue
            body = _strip_comments(path.read_text(encoding="utf-8"), html=False)
            # docstring は本文として残るため、語が出るのは実装かどうかで判断する。
            for word in _FORBIDDEN:
                if word in body:
                    problems.append(f"{path.name}: {word!r}")
        assert problems == [], " / ".join(problems)

    def test_progress_dto_has_no_streak_field(self):
        """学習者向け DTO から落ちている（UC9: 数値を見せない）。"""
        import sys

        for _p in (str(BACKEND), str(BACKEND / "api")):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from schemas import LearningProgress  # noqa: PLC0415

        assert "streak_days" not in LearningProgress.model_fields

    def test_progress_payload_has_no_streak_key(self):
        """`calculate_progress` の返す dict にキー自体が無い。"""
        src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        assert '"streak_days"' not in src
