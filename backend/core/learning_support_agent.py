"""Structured learning support orchestration.

This module keeps learning-path detours explicit: prerequisite reviews,
detail explanations, return-to-path actions, and check-question prompts are
represented as structured state instead of only prose in chat text.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from core.course_data import course_chapters


# 本文に埋め込まれたクリック候補マーカー。フロント側でのパース（\x00 センチネル方式）を
# 廃し、ここ（サーバ側）の1か所で構造化アクションへ正規化する。
_ACTION_BUTTON_RE = re.compile(r"\[ACTION_BUTTON:\s*([^\]\n]{1,120})\]")
#: IK-0477: マーカーの中に引用番号（``[98]`` / ``[17, 19–21]`` / ``[出典3]``）が1段入れ子になっても
#: 1個のマーカーとして読む（``[^\]]`` だと内側の ``]`` で切れ、ボタンが「… reference [98」、
#: 本文に「 found about μ]」が残っていた）。
_MARKER_BODY = r"(?:[^\[\]\n]|\[[^\[\]\n]{1,24}\])"
_DRILLDOWN_RE = re.compile(
    r"\[(" + _MARKER_BODY + r"{2,80}?について(?:詳しく)?(?:聞く|教えて|教えてください|知りたい))\]"
)
#: IK-0425: 英語で答えた回答のドリルダウン（``[Ask more about X]`` / ``[Ask about X]`` /
#: ``[Tell me more about X]``）。日本語形と同じく ``next_actions`` の drilldown にし、本文から除く
#: （認識しないと角括弧の行がそのまま本文に残っていた）。
_DRILLDOWN_EN_RE = re.compile(
    r"\[((?:ask(?:\s+more)?\s+about|tell\s+me\s+more\s+about)\s+" + _MARKER_BODY + r"{2,80}?)\]",
    re.IGNORECASE,
)


@dataclass
class LearningSupportOrigin:
    course_id: str
    topic_id: str
    topic_title: str
    chapter_title: str = ""
    # 位置・復帰レイヤー(L2): 寄り道に入った時点の正確な復帰位置。
    segment_id: int = 0       # レクチャー再生セグメント（テキスト時は 0）
    scroll_offset: int = 0    # 読み位置のスクロール量（px、ベストエフォート）


@dataclass
class LearningSupportAction:
    type: str
    label: str
    message: str = ""
    target_topic_id: str | None = None


@dataclass
class LearningSupportResult:
    answer: str
    mode: str = "normal"
    status_label: str = ""
    origin: LearningSupportOrigin | None = None
    next_actions: list[LearningSupportAction] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "answer": self.answer,
            "support_mode": self.mode,
            "status_label": self.status_label,
            "origin": asdict(self.origin) if self.origin else None,
            "next_actions": [asdict(action) for action in self.next_actions],
        }


class LearningSupportAgent:
    """Builds structured UX state for learning support detours."""

    def __init__(self, course_id: str, course_data: dict):
        self.course_id = course_id
        self.course_data = course_data or {}

    def origin_for_topic(
        self,
        topic_id: str,
        topic_info: dict | None,
        segment_id: int = 0,
        scroll_offset: int = 0,
    ) -> LearningSupportOrigin:
        topic = topic_info or {}
        chapter_title = ""
        chapter_index = topic.get("chapter_index")
        chapters = course_chapters(self.course_data)
        if isinstance(chapter_index, int) and 0 <= chapter_index < len(chapters):
            chapter = chapters[chapter_index] or {}
            chapter_title = str(chapter.get("title") or "")
        return LearningSupportOrigin(
            course_id=self.course_id,
            topic_id=topic_id,
            topic_title=str(topic.get("title") or topic_id),
            chapter_title=chapter_title,
            segment_id=int(segment_id or 0),
            scroll_offset=int(scroll_offset or 0),
        )

    def return_to_path_result(self, origin: dict | None) -> LearningSupportResult:
        topic_title = str((origin or {}).get("topic_title") or "元の学習トピック")
        return LearningSupportResult(
            answer=f"元の学習パス「{topic_title}」に戻ります。続きに進む前に、必要なら確認問題で理解を確認します。",
            mode="return_to_learning_path",
        )

    def with_learning_actions(
        self,
        *,
        answer: str,
        mode: str,
        origin: LearningSupportOrigin,
        include_continue: bool = True,
        continue_label: str = "前提知識をもう少し確認する",
        continue_message: str = "前提知識をもう少し確認する",
        extra_actions: list[LearningSupportAction] | None = None,
        status_label: str = "詳細説明中",
    ) -> LearningSupportResult:
        """detour（寄り道）状態の構造化レスポンスを組み立てる。

        どの入口由来でも先頭は必ず「学習パスに戻る」（復帰導線）にし、続いて任意の
        ``extra_actions``（本文由来のドリルダウン等）、必要なら継続アクション、最後に
        質問アクションを並べる。
        """
        actions = [
            LearningSupportAction(
                type="return_to_learning_path",
                label="学習パスに戻る",
                message="学習パスに戻る",
                target_topic_id=origin.topic_id,
            )
        ]
        if extra_actions:
            for action in extra_actions:
                actions.append(action)
        if include_continue:
            actions.append(
                LearningSupportAction(
                    type="continue_detail",
                    label=continue_label,
                    message=continue_message,
                )
            )
        actions.append(
            LearningSupportAction(
                type="ask_question",
                label="質問する",
                message="質問したいことがあります",
            )
        )
        return LearningSupportResult(
            answer=answer,
            mode=mode,
            status_label=status_label,
            origin=origin,
            next_actions=actions,
        )

    def advice_actions(
        self,
        origin: LearningSupportOrigin,
        topic_title: str,
        first_concept: str | None = None,
    ) -> list[LearningSupportAction]:
        """学習相談・トピック導入（パス上）の前進アクション。

        detour ではないので「学習パスに戻る」は付けず、前提確認への分岐と本トピックの
        解説開始の2つを型付きで返す。
        """
        concept_label = (first_concept or topic_title or "").strip() or "最初の概念"
        return [
            LearningSupportAction(
                type="review_prerequisite",
                label="前提知識を確認する",
                message="このコースに必要な前提知識を確認する",
                target_topic_id=origin.topic_id,
            ),
            LearningSupportAction(
                type="start_topic",
                label=f"「{concept_label}」の説明に進む",
                message=f"{concept_label}について説明してください",
                target_topic_id=origin.topic_id,
            ),
        ]

    def prerequisite_choice_actions(
        self,
        first_prerequisite: str,
    ) -> list[LearningSupportAction]:
        """前提知識チェックの介入時に提示する「はい/いいえ」選択肢（型付き）。

        ``with_learning_actions`` の ``extra_actions`` として渡される前提なので、
        先頭の「学習パスに戻る」はここには含めない。
        """
        actions = [
            LearningSupportAction(
                type="continue_detail",
                label="はい、理解しています",
                message="はい、理解しています",
            )
        ]
        first = (first_prerequisite or "").strip()
        if first:
            actions.append(
                LearningSupportAction(
                    type="drilldown",
                    label=f"いいえ、「{first}」から教えてほしい",
                    message=f"{first}について教えてください",
                )
            )
        return actions

    @staticmethod
    def is_prerequisite_request(message: str) -> bool:
        msg = (message or "").strip()
        return "前提知識" in msg and ("確認" in msg or "復習" in msg or "必要" in msg)


_MATH_DELIMITED_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.DOTALL)


#: IK-0478: 区切りの無い生の LaTeX 制御綴り（``\alpha_K>0 implies \mu\ge1``）をボタン向けの
#: 平文に直す表。表に無い綴りは名前だけ残す（``\rm`` のような書体指定は捨てる）。
_LATEX_PLAIN = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "ge": "≥", "geq": "≥", "le": "≤", "leq": "≤", "ne": "≠", "neq": "≠", "approx": "≈",
    "sim": "∼", "simeq": "≃", "propto": "∝", "times": "×", "cdot": "·", "pm": "±",
    "infty": "∞", "partial": "∂", "nabla": "∇", "to": "→", "rightarrow": "→",
}
_LATEX_DROP = frozenset({"rm", "mathrm", "text", "mathit", "mathbf", "bf", "it", "left", "right", "displaystyle"})
_LATEX_CMD_RE = re.compile(r"\\([A-Za-z]+)")
_SOURCE_MARKER_IN_LABEL_RE = re.compile(r"\s*\[出典\s*\d+\]")
_REF_NUMBER_IN_LABEL_RE = re.compile(r"\[(\d[\d,\s–\-]*)\]")


def _latex_to_plain(text: str) -> str:
    def _cmd(match: re.Match) -> str:
        name = match.group(1)
        if name in _LATEX_DROP:
            return ""
        return _LATEX_PLAIN.get(name, name)

    out = _LATEX_CMD_RE.sub(_cmd, text or "")
    out = out.replace("{", "").replace("}", "")
    out = re.sub(r"([_^]) +", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out)


def _strip_math_delimiters(text: str) -> str:
    """``$…$`` / ``$$…$$`` / ``\\(…\\)`` / ``\\[…\\]`` の区切りを外し、中身だけを残す（IK-0451）。

    IK-0478: 区切りの無い生の制御綴り（``\\alpha_K``・``\\ge``）も平文に直す。ボタンの文字と
    送信する問いの両方に使う（書き直しの往復も同じ ``extract_inline_actions`` を通る）。
    """
    unwrapped = _MATH_DELIMITED_RE.sub(
        lambda m: next(g for g in m.groups() if g is not None).strip(), text or ""
    )
    return _latex_to_plain(unwrapped)


def extract_inline_actions(text: str) -> tuple[str, list[LearningSupportAction]]:
    """LLM 本文中のクリック候補マーカーを構造化アクションへ変換し、本文から除去する。

    対象は ``[ACTION_BUTTON: 〇〇]`` と ``[〇〇について(詳しく)?聞く/教えて]`` と、英語の
    ``[Ask more about X]`` / ``[Ask about X]`` / ``[Tell me more about X]``（IK-0425）。
    本文側のフラグメント解析はここ（サーバ側・決定論的）に一元化し、フロントは
    ``next_actions`` のみを描画する（型付き送信）。日本語ラベルを再び intent 分類へ
    通さないよう、抽出したアクションは ``type="drilldown"`` を付与する。

    Returns
    -------
    (clean_text, actions)
        マーカーを除去した本文と、抽出した ``LearningSupportAction`` のリスト。
    """
    actions: list[LearningSupportAction] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        # IK-0451: ボタンの文字は数式の区切り（``$…$`` / ``\(…\)``）を外して中身だけ残す
        # （``[Ask more about $\mu$]`` のボタンに生の ``$`` を出さない）。
        label = _strip_math_delimiters(label or "").strip()
        # IK-0477: ボタンの文字に文中の引用番号（``[98]`` / ``[出典3]``）を持ち込まない。
        # 出典マーカーは落とし、文献番号（``[98]``）は角括弧だけ外して番号を残す。
        label = _SOURCE_MARKER_IN_LABEL_RE.sub("", label)
        label = _REF_NUMBER_IN_LABEL_RE.sub(lambda m: m.group(1).strip(), label).strip()
        if not label or label in seen:
            return
        seen.add(label)
        actions.append(
            LearningSupportAction(type="drilldown", label=label, message=label)
        )

    def _sub(match: re.Match) -> str:
        _add(match.group(1))
        return ""

    clean = _ACTION_BUTTON_RE.sub(_sub, text or "")
    clean = _DRILLDOWN_RE.sub(_sub, clean)
    clean = _DRILLDOWN_EN_RE.sub(_sub, clean)
    # マーカー除去で生じた空行を畳む。
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, actions

