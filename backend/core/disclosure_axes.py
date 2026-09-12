"""可視性6軸の宣言カタログ — 「誰に見えて、どこへ送られ、いつ取り消せるか」の単一の真実源。

正本設計書: ``docs/features/disclosure_axes_design.md``。
思想の出所は ``docs/vision.md`` §5.4（**可視性を一軸に畳まない** —
「閲覧できる audience / 名前を見られる audience / 引用・再利用できる条件 /
評価に利用できるか / 外部 index や外部 AI に渡るか / いつ撤回・凍結できるか」を
独立に扱う）と原則8（出所の正直さ）。調査の出所は
``docs/architecture/six_lenses_2026-09-10/04_community.md`` §1.1 / 提案2。

このモジュールは ``core/indicator_catalog.py`` と同じ作法で書く純宣言
（FastAPI / sqlalchemy / LLM 非依存・**値を1つも持たない**）。制度指標カタログが
「計器の定義」を全当事者に公開したのと同じことを、**可視性そのもの**に対して行う。

不変条項:

- **DA1 宣言は現物から確認できることだけ** — 各 spec の事実文は実装（``basis`` に
  挙げたモジュール）を読んで書く。「そうあってほしい姿」は書かない。実装を変えたら
  同じ PR で本モジュールと設計書を直す（片方だけの変更は「同じ名前で別のことを言う」
  = 出所の不正直になる）。
- **DA2 AI 通過点の宛先は provider だけ** — 第5軸に書けるのは「外部の AI プロバイダへ
  何が送られるか」まで。モデル名・トークン数・金額は書かない（``__post_init__`` が
  :data:`FORBIDDEN_DESTINATION_TERMS` で構造強制する）。実際の provider 名は実行時の
  設定から解決するため、カタログは ``{provider}`` プレースホルダしか持たない。
- **DA3 定義は全当事者が読める** — 公開 API（``GET /api/disclosure``）に
  ロールゲートを掛けない。観察される側（学習者）が読めない可視性宣言は宣言ではない。
- **DA4 学習者に内部名を出さない** — 学習者が読む事実文に
  :data:`LEARNER_TEXT_DENYLIST`（``core/help_kb/validator.py::STUDENT_DENYLIST`` の
  ミラー。ガードレールが包含関係を固定する）の語彙を入れない。
- **DA5 同意を装わない** — 本カタログと公開 API は**告知**であって同意取得ではない。
  「同意」「承諾」のボタン・チェックボックスをこの層に作らない（初回告知カードを置く
  かどうかはオーナー判断 D4 の未決事項で、本層のスコープ外）。
- **DA6 外部 AI を通らない経路も同じ表に書く** — 「送られる」だけを並べると、送られない
  経路（使い方の質問・楽屋・再構成の構造照合）まで送られているように読める。
  各 spec は ``without_ai`` でそれを明示する。

6軸に加えて、各 spec は **保持・削除**（``retention``）/ **集約への算入**
（``aggregation``）/ **持ち出し**（``portability``）の3項目を宣言する。軸そのものを
増やさず（vision §5.4 の6軸を正とする）、6軸の事実を読むために必要な文脈だけを添える。

ガードレールは ``backend/tests/test_disclosure_axes_guardrails.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Mapping

__all__ = [
    "AXES",
    "AXIS_AUDIENCE",
    "AXIS_EVALUATION_USE",
    "AXIS_EXTERNAL_TRANSFER",
    "AXIS_IDS",
    "AXIS_NAME_DISCLOSURE",
    "AXIS_REUSE",
    "AXIS_WITHDRAWAL",
    "AiTouchpoint",
    "DATA_KINDS",
    "DISCLOSURE_NOTE",
    "DisclosureAxis",
    "DisclosureSpec",
    "FORBIDDEN_DESTINATION_TERMS",
    "GENERIC_PROVIDER_LABEL",
    "LEARNER_TEXT_DENYLIST",
    "PROVIDER_LABELS",
    "PUBLIC_VIEW_FIELDS",
    "all_data_kinds",
    "axes_public_view",
    "catalog_public_view",
    "get_data_kind",
    "note_for",
    "provider_label",
    "validate_catalog",
]


# ---------------------------------------------------------------------------
# 軸の語彙（vision §5.4 の6軸。増やさない・畳まない）
# ---------------------------------------------------------------------------

AXIS_AUDIENCE = "audience"
AXIS_NAME_DISCLOSURE = "name_disclosure"
AXIS_REUSE = "reuse"
AXIS_EVALUATION_USE = "evaluation_use"
AXIS_EXTERNAL_TRANSFER = "external_transfer"
AXIS_WITHDRAWAL = "withdrawal"


@dataclass(frozen=True)
class DisclosureAxis:
    """1つの軸の宣言（何を独立に扱うかの定義。値は持たない）。"""

    id: str
    label: str
    question: str   # 読み手にとっての問いの形（画面・マニュアルの見出しの正本）

    def public_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "question": self.question}


AXES: tuple[DisclosureAxis, ...] = (
    DisclosureAxis(
        id=AXIS_AUDIENCE,
        label="閲覧できる相手",
        question="この記録は誰が読めますか。",
    ),
    DisclosureAxis(
        id=AXIS_NAME_DISCLOSURE,
        label="名前が見える相手",
        question="読める相手に、書いた人の名前まで見えますか。",
    ),
    DisclosureAxis(
        id=AXIS_REUSE,
        label="引用・再利用の条件",
        question="他の人が引用・再利用できますか。できるとき、何が付きますか。",
    ),
    DisclosureAxis(
        id=AXIS_EVALUATION_USE,
        label="評価への利用",
        question="成績評価・順位付け・自動的な判定に使われますか。",
    ),
    DisclosureAxis(
        id=AXIS_EXTERNAL_TRANSFER,
        label="外部の AI・外部の索引への送信",
        question="外部の AI プロバイダや外部の検索索引へ送られますか。何が送られますか。",
    ),
    DisclosureAxis(
        id=AXIS_WITHDRAWAL,
        label="撤回・凍結できるとき",
        question="後から取り消し・非表示・凍結ができますか。",
    ),
)

AXIS_IDS: tuple[str, ...] = tuple(axis.id for axis in AXES)


# ---------------------------------------------------------------------------
# 宛先の語彙（DA2: provider 名だけ。モデル名・金額を書かない）
# ---------------------------------------------------------------------------

#: 設定値（``core/config.py`` の ``llm_provider``）→ 人が読む provider 名。
#: モデル名は入れない（同じ provider の中でどのモデルを使うかは運用パラメータで、
#: 「どこへ送られるか」という可視性の事実ではない）。
PROVIDER_LABELS: Mapping[str, str] = MappingProxyType({
    "openai": "OpenAI",
    "gemini": "Google",
    "google": "Google",
    "gemini-vertex": "Google",
})

#: provider が判定できないときの言い方（推測で企業名を書かない）。
GENERIC_PROVIDER_LABEL = "外部の AI プロバイダ"

#: 事実文・軸の本文に入れてはならない語（DA2）。モデル名・料金・トークン数は
#: 可視性の事実ではなく、書くと「どこへ送られるか」の話が運用の話に化ける。
FORBIDDEN_DESTINATION_TERMS: tuple[str, ...] = (
    "gpt-",
    "o3-",
    "o4-",
    "whisper",
    "text-embedding",
    "claude",
    "トークン",
    "円",
    "ドル",
    "USD",
    "$",
)

#: 学習者が読む事実文の禁止語彙（DA4）。``core/help_kb/validator.py::STUDENT_DENYLIST``
#: のミラー。**包含関係（denylist ⊆ 本タプル）はガードレールが固定する** — help_kb を
#: import して純粋性（FastAPI / sqlalchemy 非依存の推移的保証）を崩さないため、
#: label_vocab と同じ「ミラー + テストで固定」の作法を採る。
LEARNER_TEXT_DENYLIST: tuple[str, ...] = (
    "ADMIN_PASSWORD",
    "JWT_SECRET",
    "OPENAI_API_KEY",
    "admin.html",
    "localhost:8001",
    "localhost:9001",
    "/api/admin",
    "_require_",
    "MAX_CALLS_PER_DAY",
)

#: カタログ公開時に必ず添える固定の事実文（UI・API・マニュアルで同一文言）。
#: 同意を求める文言にしない（DA5）。
DISCLOSURE_NOTE = (
    "ここに書いてあるのは、それぞれの記録が誰に見えて、どこへ送られ、"
    "いつ取り消せるかの事実です。書いてある以外の使い方はしません。"
)

#: 公開ビューに載せるフィールド（**宣言のみ**。件数・数値は1つも無い）。
PUBLIC_VIEW_FIELDS: tuple[str, ...] = (
    "data_kind",
    "label",
    "what",
    "note",
    "axes",
    "ai_touchpoints",
    "without_ai",
    "retention",
    "aggregation",
    "portability",
    "source",
    "basis",
    "design_doc",
    "learner_facing",
)


# ---------------------------------------------------------------------------
# 宣言のかたち
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AiTouchpoint:
    """外部 AI プロバイダへの1つの通過点（どの操作で・何が送られるか）。

    宛先は provider だけなので、このオブジェクトは宛先を持たない（実行時に
    設定から解決して1つ添える）。``feature`` は U層の帰属キー
    （``core/llm_usage/schema.py::KNOWN_FEATURES``）で、どの呼び出しの話かを
    実装と突き合わせられるようにするための参照。
    """

    operation: str   # 画面・操作の名前（日本語。読み手が自分の操作と結び付けられる粒度）
    sends: str       # 送られるもの（事実文）
    feature: str     # U層 feature キー（KNOWN_FEATURES の要素であることをテストが固定）

    def __post_init__(self) -> None:
        for field_name in ("operation", "sends", "feature"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"AiTouchpoint.{field_name} は必須です")
        _reject_forbidden_destination_terms(f"通過点 {self.operation}", (self.operation, self.sends))

    def public_dict(self) -> dict:
        return {"operation": self.operation, "sends": self.sends, "feature": self.feature}


def _reject_forbidden_destination_terms(where: str, texts: tuple[str, ...]) -> None:
    lowered = [t.lower() for t in texts]
    for term in FORBIDDEN_DESTINATION_TERMS:
        needle = term.lower()
        if any(needle in text for text in lowered):
            raise ValueError(
                f"{where}: 宛先の説明にモデル名・料金・トークン数を書けません（{term!r}）"
                "（DA2 — 書けるのは provider 名までで、それは実行時に解決する）"
            )


@dataclass(frozen=True)
class DisclosureSpec:
    """1つのデータ種別についての可視性6軸の宣言（値は持たない）。"""

    data_kind: str                       # snake_case の識別子
    label: str                           # 日本語の呼び名（UI・マニュアルの表示の正本）
    what: str                            # 何を指すか（事実文）
    note: str                            # 対話 UI に置く1行の事実文（外部送信ありなら {provider} を含む）
    audience: str                        # 軸1
    name_disclosure: str                 # 軸2
    reuse: str                           # 軸3
    evaluation_use: str                  # 軸4
    external_transfer: str               # 軸5
    withdrawal: str                      # 軸6
    retention: str                       # 保持・削除
    aggregation: str                     # 集約への算入
    portability: str                     # 持ち出し
    source: str                          # 保存先（テーブル / ストレージ）
    basis: tuple[str, ...]               # 宣言の出所となった正本（module[::symbol]）
    design_doc: str                      # 設計書の相対パス
    ai_touchpoints: tuple[AiTouchpoint, ...] = ()
    without_ai: tuple[str, ...] = ()     # 外部 AI を通らない経路（DA6）
    learner_facing: bool = True          # 学習者が読み手か（DA4 の検査対象）

    #: 軸 id → フィールド名（公開ビューと検査の両方が使う）。
    _AXIS_FIELDS: ClassVar[Mapping[str, str]] = MappingProxyType({
        AXIS_AUDIENCE: "audience",
        AXIS_NAME_DISCLOSURE: "name_disclosure",
        AXIS_REUSE: "reuse",
        AXIS_EVALUATION_USE: "evaluation_use",
        AXIS_EXTERNAL_TRANSFER: "external_transfer",
        AXIS_WITHDRAWAL: "withdrawal",
    })

    def __post_init__(self) -> None:
        kind = self.data_kind
        if not kind or kind != kind.strip().lower() or " " in kind or "-" in kind:
            raise ValueError(f"data_kind は小文字 snake_case: {kind!r}")
        required = (
            "label", "what", "note", "audience", "name_disclosure", "reuse",
            "evaluation_use", "external_transfer", "withdrawal",
            "retention", "aggregation", "portability", "source", "design_doc",
        )
        for field_name in required:
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{kind}: {field_name} は必須です（6軸を空欄で通さない）")
        if not self.basis:
            raise ValueError(
                f"{kind}: basis は必須です（DA1 — 宣言の出所となった実装を挙げる）"
            )

        # DA2: 可視テキストにモデル名・料金・トークン数を書かない。
        _reject_forbidden_destination_terms(kind, self._visible_texts())

        # DA2: 外部送信があるなら宛先は実行時解決のプレースホルダで書く
        # （カタログに provider 名を焼き込むと、設定を変えたときに嘘になる）。
        if self.ai_touchpoints and "{provider}" not in self.note:
            raise ValueError(
                f"{kind}: 外部 AI に送るデータ種別の note には {{provider}} を含めること"
                "（宛先は実行時の設定から解決する）"
            )
        if not self.ai_touchpoints and "{provider}" in self.note:
            raise ValueError(
                f"{kind}: 外部 AI に送らないのに note が provider を名指ししています"
            )
        if not self.ai_touchpoints and "送りません" not in self.external_transfer:
            raise ValueError(
                f"{kind}: 通過点ゼロなら第5軸は「送りません」と明言すること"
                "（黙って空欄にすると『不明』と区別できない）"
            )

        # DA4: 学習者が読む事実文に内部名を出さない。
        if self.learner_facing:
            for term in LEARNER_TEXT_DENYLIST:
                if any(term in text for text in self._visible_texts()):
                    raise ValueError(
                        f"{kind}: 学習者が読む事実文に禁止語彙 {term!r} を含めません（DA4）"
                    )

        # DA5: カタログは告知であって同意取得ではない（同意を求める・記録したと
        # 装う言い回しを構造的に禁じる。V層の「取り込む」のような別語義は妨げない）。
        for term in ("同意しました", "同意して", "同意しますか", "同意が必要", "利用規約に同意"):
            if any(term in text for text in self._visible_texts()):
                raise ValueError(
                    f"{kind}: 同意を装う文言を宣言に書けません（DA5 — ここは告知の層）"
                )

    def _visible_texts(self) -> tuple[str, ...]:
        """利用者に見える本文（``source`` / ``basis`` / ``design_doc`` は技術的な出所なので除く）。"""
        texts = [
            self.label, self.what, self.note, self.audience, self.name_disclosure,
            self.reuse, self.evaluation_use, self.external_transfer, self.withdrawal,
            self.retention, self.aggregation, self.portability,
        ]
        texts.extend(self.without_ai)
        for touchpoint in self.ai_touchpoints:
            texts.extend([touchpoint.operation, touchpoint.sends])
        return tuple(texts)

    def axis_values(self) -> dict:
        """軸 id → 事実文（軸の宣言順）。"""
        return {
            axis_id: str(getattr(self, field_name))
            for axis_id, field_name in self._AXIS_FIELDS.items()
        }

    def note_text(self, provider: str) -> str:
        """対話 UI に置く1行の事実文（provider を差し込んだ確定形）。"""
        return self.note.replace("{provider}", provider)

    def public_dict(self, *, provider: str) -> dict:
        """公開ビュー1件分（宣言のみ・値なし）。"""
        return {
            "data_kind": self.data_kind,
            "label": self.label,
            "what": self.what,
            "note": self.note_text(provider),
            "axes": self.axis_values(),
            "ai_touchpoints": [t.public_dict() for t in self.ai_touchpoints],
            "without_ai": list(self.without_ai),
            "retention": self.retention,
            "aggregation": self.aggregation,
            "portability": self.portability,
            "source": self.source,
            "basis": list(self.basis),
            "design_doc": self.design_doc,
            "learner_facing": self.learner_facing,
        }


# ---------------------------------------------------------------------------
# カタログ本体（宣言順 = 公開ビュー・マニュアルの並び順の正本）
#
# 各事実文は ``basis`` の実装を読んで書いてある（DA1）。実装を変えたら、ここと
# 設計書と（学習者に見えるものなら）マニュアルを同じ PR で直す。
# ---------------------------------------------------------------------------

_SPECS: tuple[DisclosureSpec, ...] = (
    DisclosureSpec(
        data_kind="learning_chat",
        label="学習チャット・議論・音声で送った本文",
        what=(
            "学習画面で入力した質問や考え、「論文と議論する」でのやりとり、"
            "音声会話で話した内容と、それに対する回答です。"
        ),
        note=(
            "入力した文（音声のときは録音した音声）と、参照する教材の本文は、回答を作る"
            "ために外部の AI プロバイダ（{provider}）に送られます。"
            "送った内容が成績評価に使われることはありません。"
        ),
        audience=(
            "あなただけが読み返せます（担当教員が会話の本文を開く画面はありません）。"
            "例外は1つで、教材から答えられなかった質問だけは、担当教員の画面に"
            "あなたの表示名と質問の文面がそのまま並びます。"
        ),
        name_disclosure=(
            "会話の本文に名前は付きません。教材から答えられなかった質問として"
            "担当教員に並ぶときだけ、表示名が一緒に見えます。"
        ),
        reuse="他の人が引用・再利用する経路はありません。",
        evaluation_use=(
            "成績評価・順位付け・自動的な判定には使いません。"
            "回答の出所（教材か、別の資料か、AI が組み立てた説明か）の表示にだけ使います。"
        ),
        external_transfer=(
            "送られます。入力した文・直近のやりとり・検索で選ばれた教材本文が、"
            "回答を作るために外部の AI プロバイダに送られます。"
            "教材の上で範囲選択した文は、そのまま引用として一緒に送られます。"
            "画面で要素（論理要素・主張・式・図）を開いているときは、その要素について"
            "サーバが解析結果から組み立てた事実（要素の名前、関係する式や主張、"
            "検証記録の有無、分野の地図での位置）も一緒に送られます。"
            "音声会話では、録音した音声（文字にするため）と読み上げる回答文も送られます。"
        ),
        withdrawal=(
            "自分のメッセージは書き直し・以降の削除ができ、質疑応答の履歴はまとめて"
            "削除できます。書き直し・削除をすると、そこから後のやりとりは記録から外れます。"
        ),
        retention=(
            "コースとトピックごとに保存されます。アカウントが削除されるときは"
            "消える側の記録です。"
        ),
        aggregation=(
            "会話そのものは集計されません。会話から作られる痕跡（問い・引っかかり）は、"
            "人数が少ないうちは表示されない匿名の集計として担当教員に届くことがあります。"
        ),
        portability=(
            "「わたしの記録」から、あなたの痕跡（質問の文面を含みます）をまとめて"
            "持ち出せます。会話全体をそのまま書き出す機能はありません。"
        ),
        source="learning_chat_history / unanswered_query_logs（答えられなかった質問のみ）",
        basis=(
            "backend/api/routes/learning.py::learning_chat",
            "backend/api/routes/admin.py::list_unanswered_queries",
            "backend/core/assistant_context/resolvers/learning.py",
            "backend/core/llm_policy.py",
        ),
        design_doc="docs/features/disclosure_axes_design.md",
        ai_touchpoints=(
            AiTouchpoint(
                operation="学習チャットの質問",
                sends=(
                    "入力した文・直近のやりとり・検索で選ばれた教材本文と、"
                    "教材の上で範囲選択した文・画面で開いている要素についてサーバが"
                    "解析結果から組み立てた事実（要素の名前、関係する式や主張、"
                    "検証記録の有無、分野の地図での位置）"
                ),
                feature="learning:chat",
            ),
            AiTouchpoint(
                operation="気軽に話せる先生（カジュアル）",
                sends=(
                    "入力した文・直近のやりとり・検索で選ばれた教材本文と、"
                    "教材の上で範囲選択した文"
                ),
                feature="learning:chat_casual",
            ),
            AiTouchpoint(
                operation="論文と議論する（discuss）",
                sends=(
                    "入力した文・直近のやりとり・対象論文の本文と、"
                    "教材の上で範囲選択した文・画面で開いている要素についてサーバが"
                    "解析結果から組み立てた事実（要素の名前、関係する式や主張、"
                    "検証記録の有無、分野の地図での位置）"
                ),
                feature="learning:chat_discuss",
            ),
            AiTouchpoint(
                operation="予想の前の問い（予想を書く前）",
                sends=(
                    "あなたが書いた予想の文と、対象論文の本文、"
                    "教材の上で範囲選択した文"
                ),
                feature="learning:cycle_elicit",
            ),
            AiTouchpoint(
                operation="予想との違いの観点（予想を書いたあと）",
                sends=(
                    "あなたが書いた予想の文と、対象論文の本文、"
                    "教材の上で範囲選択した文・画面で開いている要素についてサーバが"
                    "解析結果から組み立てた事実（要素の名前、関係する式や主張、"
                    "検証記録の有無、分野の地図での位置）"
                ),
                feature="learning:cycle_diff",
            ),
            AiTouchpoint(
                operation="音声で話す（聞き取り）",
                sends="録音した音声（文字にするため）",
                feature="learning:voice_stt",
            ),
            AiTouchpoint(
                operation="音声で話す（読み上げ）",
                sends="読み上げる回答文",
                feature="learning:voice_tts",
            ),
        ),
        without_ai=(
            "「使い方」の質問（テキストで聞いたとき）はマニュアルの本文をそのまま返すため、"
            "外部の AI には送りません。",
            "楽屋（集計に入らない質問場所）で聞いた内容も、集計には入りません"
            "（回答を作るための送信は、通常の質問と同じように行われます）。",
        ),
    ),
    DisclosureSpec(
        data_kind="learner_traces",
        label="学習の痕跡（問い・引っかかり・意図・印）",
        what=(
            "質問したこと、引っかかったこと、次に考えたい問い、気になる箇所に付けた印など、"
            "学習の途中に残る記録です。"
        ),
        note=(
            "引っかかりの候補づくりと、問いがどこに向いているかの見立てのために、"
            "直近のやりとり（あなたの発言を含みます）が外部の AI プロバイダ（{provider}）に"
            "送られます。候補はあなたが引き受けるまで確定しません。"
        ),
        audience=(
            "あなただけが読めます（「わたしの記録」「わたしの地図」「問いの軌跡」）。"
            "担当教員には、誰の記録かがわからない形の集計としてだけ届きます。"
        ),
        name_disclosure="集計に名前は出ません。個々の記録を担当教員が開く画面もありません。",
        reuse="他の人が引用・再利用する経路はありません。",
        evaluation_use="成績評価・順位付け・自動的な判定には使いません。",
        external_transfer=(
            "送られます。引っかかりの候補づくりと、問いの帰属（どの主張・どの式について"
            "引っかかったか）の見立てのために、直近のやりとりが外部の AI プロバイダに"
            "送られます。AI が出すのは候補までで、確定はあなたの操作です。"
        ),
        withdrawal=(
            "「地図に反映しない」で地図から外せますし、候補は却下できます。"
            "どちらも記録を消すのではなく状態を変える扱いで、後から戻せます。"
        ),
        retention=(
            "行を削除せず、状態を変えて残します。アカウントが削除されるときは"
            "消える側の記録です。"
        ),
        aggregation=(
            "あなたが引き受けた記録だけが、人数が少ないうちは表示されない匿名の集計として"
            "担当教員に届きます。候補のままの記録は集計に入りません。"
            "系統ごとに「集計に入るか」が決まっていて、「わたしの記録」で確認できます。"
        ),
        portability="「わたしの記録」から、全部をまとめて持ち出せます。",
        source="interest_traces（系統ごとの宣言は core/trace_registry.py）",
        basis=(
            "backend/core/trace_registry.py",
            "backend/core/privacy.py::K_ANONYMITY",
            "backend/api/services.py::aggregate_interest_dashboard",
            "backend/core/tension/input_builder.py",
            "backend/core/account_lifecycle.py::PURGE_TABLES",
        ),
        design_doc="docs/features/disclosure_axes_design.md",
        ai_touchpoints=(
            AiTouchpoint(
                operation="引っかかりの候補づくり",
                sends="直近のやりとり（あなたの発言はそのまま）",
                feature="learning:tension",
            ),
            AiTouchpoint(
                operation="問いの帰属の見立て",
                sends="質問の文と、その場に出ていた構造（主張・式・段）の見出し",
                feature="learning:structure_anchor",
            ),
        ),
        without_ai=(
            "印を付ける・書き置きを残す・「地図に反映しない」の操作は、外部の AI を通りません。",
            "「わたしの地図」の描画と、そこから辿る道筋の計算も外部の AI を通りません。",
        ),
    ),
    DisclosureSpec(
        data_kind="check_answers",
        label="確認問題・再構成の回答",
        what=(
            "確認問題に書いた回答と、再構成（予測・言い直し）で書いた文、"
            "そのあとの自己確認の記録です。"
        ),
        note=(
            "書いた回答は、出題の要件と並べて見せるために外部の AI プロバイダ"
            "（{provider}）に送られます。合否は AI が決めず、先へ進むかどうかはあなたが決めます。"
        ),
        audience=(
            "あなただけが読めます。担当教員に届くのは、出題そのものが妥当かを見るための"
            "集計（どの問いでつまずきが多いか）だけです。"
        ),
        name_disclosure="集計に名前は出ません。",
        reuse="他の人が引用・再利用する経路はありません。",
        evaluation_use=(
            "成績評価・順位付け・自動的な判定には使いません。"
            "正答率・点数は誰にも表示しません。"
        ),
        external_transfer=(
            "送られます。確認問題では、回答を出題の要件と並べるために回答文と教材本文が"
            "外部の AI プロバイダに送られます。再構成の食い違いの指摘は構造の突き合わせで"
            "行うため、外部の AI には送りません。"
        ),
        withdrawal=(
            "回答は書き直せます（前の版も履歴として残ります）。"
            "機械の見立てに納得できないときは「判定がおかしい」と記録できます。"
        ),
        retention=(
            "行を削除せず、改訂の履歴として残します。アカウントが削除されるときは"
            "消える側の記録です。"
        ),
        aggregation=(
            "人数が少ないうちは表示されない匿名の集計として、出題の健全性の見直しに"
            "使われます。"
        ),
        portability="回答そのものを書き出す機能はありません。",
        source="learner_reconstructions / reconstruction_items（出題側）",
        basis=(
            "backend/api/routes/learning.py::check_topic_understanding",
            "backend/core/reconstruction/diff.py",
            "backend/core/reconstruction/health.py",
        ),
        design_doc="docs/features/disclosure_axes_design.md",
        ai_touchpoints=(
            AiTouchpoint(
                operation="確認問題の回答の並置",
                sends="書いた回答・確認問題の文・そのセクションの教材本文",
                feature="learning:understanding_check",
            ),
        ),
        without_ai=(
            "再構成の食い違いの指摘は、教材から作られた構造との突き合わせで行うため"
            "外部の AI を通りません。",
            "自己確認（納得した／しない）の1タップも外部の AI を通りません。",
        ),
    ),
    DisclosureSpec(
        data_kind="course_materials",
        label="教材・論文と、その解析結果",
        what=(
            "担当教員が登録した論文の本文・図、そこから作られた主張・式・グラフ・原稿など、"
            "解析パイプラインの成果です。"
        ),
        note=(
            "この入力と、対象の教材・解析結果の本文は、回答を作るために外部の AI プロバイダ"
            "（{provider}）に送られます。"
        ),
        audience=(
            "登録した教員が決めた開示範囲（本人のみ / 指定グループ / 全体）に従います。"
            "受講しているコースの資料は、そのコースの受講者が読めます。"
        ),
        name_disclosure="登録した教員の名前が、教員向けの一覧に出ます。",
        reuse=(
            "承認済みの説明は、他の教員が帰属（誰の説明か）と版を記帳したうえで"
            "引用・再利用できます。"
        ),
        evaluation_use="人の評価には使いません（評価の対象は人ではなく資料です）。",
        external_transfer=(
            "送られます。解析（構造の抽出・式や図の読み取り・原稿の下書き）と、"
            "質問への回答づくり・教員の AI 対話のたびに、本文の必要な部分が外部の"
            "AI プロバイダに送られます。外部の検索索引には登録しません。"
        ),
        withdrawal=(
            "開示範囲の変更・公開の取り下げ・猶予付きの削除予約ができます。"
            "発行した版は不変で、受け取った側は自分で取り込むまで内容が変わりません。"
        ),
        retention=(
            "教員が削除するまで保存されます。原本は S3 互換ストレージ、解析結果は"
            "データベースに残ります。"
        ),
        aggregation="教材の処理状況の統計（人に紐づかない件数）として集計されます。",
        portability=(
            "教員は解析結果を外部レビュー用に書き出せます（誰が書き出したかは記帳されます）。"
        ),
        source="documents / chunks / theory_* / document_analysis_runs / MinIO（raw-papers・figure-images）",
        basis=(
            "backend/api/services.py::resolve_document_access",
            "backend/core/document_pipeline/orchestrator.py",
            "backend/core/llm_policy.py",
            "backend/api/routes/export.py",
        ),
        design_doc="docs/features/disclosure_axes_design.md",
        ai_touchpoints=(
            AiTouchpoint(
                operation="解析パイプライン（構造・主張・式・図）",
                sends="論文の本文・図の画像・前の段階の解析結果",
                feature="pipeline:claim_qualification",
            ),
            AiTouchpoint(
                operation="コース構築チャット",
                sends="入力した要件と、選んだ教材の題名・要約",
                feature="admin:course_builder",
            ),
            AiTouchpoint(
                operation="原稿スタジオの AI 書き換え",
                sends="対象の原稿本文と、書き換えの指示",
                feature="admin:lecture_rewrite",
            ),
            AiTouchpoint(
                operation="操作アシスタントの対話",
                sends="入力した文と、いま開いている画面の名前",
                feature="admin:assistant",
            ),
            AiTouchpoint(
                operation="要素・グラフとの AI 対話",
                sends="入力した文と、対象要素の解析結果（本文の引用を含む）",
                feature="deliberation:chat",
            ),
            AiTouchpoint(
                operation="グラフ全体との AI 対話",
                sends="入力した文と、グラフの骨格・未確認の一覧",
                feature="deliberation:graph_chat",
            ),
        ),
        without_ai=(
            "開示範囲の変更・承認・却下・版の発行は、外部の AI を通りません。",
            "論文の候補探し（arXiv 検索）と図の抽出は、外部の AI に本文を送りません。",
        ),
    ),
    DisclosureSpec(
        data_kind="teacher_reviews",
        label="教員の承認・却下・疑義",
        what=(
            "説明や主張の承認・却下、疑義（challenge）、検証の記帳、配置の確認など、"
            "教員が確定した判断とその監査記録です。"
        ),
        note=(
            "判断そのものは外部の AI に送りません。対象の教材本文は、AI 対話や再解析の"
            "ときに送られます。"
        ),
        audience=(
            "教員は互いの判断を参照できます。学習者には「教員確認済み」などの段階の"
            "表示として届きます。"
        ),
        name_disclosure=(
            "帰属は必須です。匿名の承認・匿名の疑義は作りません"
            "（誰の判断かがわかる形でだけ記帳されます）。"
        ),
        reuse="他の教員が帰属と版を記帳したうえで引用・再利用できます。",
        evaluation_use=(
            "学習者の評価には使いません。教員個人の評価・順位付けにも使いません。"
        ),
        external_transfer="判断そのものは送りません。",
        withdrawal=(
            "取り消しは行を消すのではなく状態を変える扱いです"
            "（取り下げた事実も記録として残ります）。"
        ),
        retention=(
            "監査台帳は追記のみで、消しません。アカウントが削除されても"
            "「誰が何をしたか」の記録は残ります（表示は匿名化された名前になります）。"
        ),
        aggregation="件数の統計に入りますが、個人の比較には使いません。",
        portability="教員は解析結果とあわせて書き出せます。",
        source="theory_review_events（追記のみ） / component_endorsements / challenges",
        basis=(
            "backend/core/schema.py::AUDIT_ENTITY_TYPES",
            "backend/api/services.py::record_review_event",
            "backend/core/account_lifecycle.py::RETAIN_TABLES",
            "backend/core/decision_context.py",
        ),
        design_doc="docs/features/disclosure_axes_design.md",
        without_ai=(
            "承認・却下・疑義・検証の記帳は、外部の AI を通らない人の操作です。",
            "一括で確定するときは、何が提示され何が適用されたかが記帳されます。",
        ),
        learner_facing=False,
    ),
    DisclosureSpec(
        data_kind="account",
        label="アカウント（表示名・ログイン記録）",
        what=(
            "ユーザー名・表示名・役割（受講者／教員／システム管理者）と、"
            "ログインの成否の記録です。"
        ),
        note="アカウントの情報を外部の AI に送りません。",
        audience=(
            "表示名と役割は、担当教員以上の一覧に出ます。参加しているグループの詳細では、"
            "同じグループの参加者に表示名が見えます。ログインの記録を読めるのは"
            "システム管理者だけです。"
        ),
        name_disclosure="表示名はそのまま見えます（アカウントの識別に使うためです）。",
        reuse="引用・再利用の対象ではありません。",
        evaluation_use=(
            "ログインの記録と AI の利用実績は、不正な利用や使われていないアカウントを"
            "見つけるためだけに使い、学習の評価には使いません。"
        ),
        external_transfer="送りません。",
        withdrawal=(
            "利用の停止・パスワードの再発行・削除の予約は、システム管理者の操作です。"
            "本人が退出を始める操作は用意されていません。"
        ),
        retention=(
            "アカウントの行そのものは消さず、名前を匿名化した記録として残します"
            "（他の人の記録の中の「誰が」を壊さないためです）。"
            "削除のときに消すもの・残すものは、あらかじめ表で決めてあります。"
        ),
        aggregation=(
            "1アカウント単位の運用の記録で、集計でも匿名化でもありません"
            "（読めるのはシステム管理者だけです）。"
        ),
        portability="自分の記録の持ち出しは「わたしの記録」から行えます。",
        source="users / auth_events（追記のみ）",
        basis=(
            "backend/core/account_lifecycle.py",
            "backend/core/auth_events.py",
            "backend/core/account_status.py",
        ),
        design_doc="docs/features/disclosure_axes_design.md",
        without_ai=(
            "ログイン・停止・パスワードの再発行は、外部の AI を通りません。",
        ),
    ),
)


#: data_kind → spec（挿入順 = 公開ビュー・マニュアルの並び順）。
DATA_KINDS: Mapping[str, DisclosureSpec] = MappingProxyType(
    {spec.data_kind: spec for spec in _SPECS}
)


# ---------------------------------------------------------------------------
# 参照ヘルパー
# ---------------------------------------------------------------------------


def provider_label(provider_key: str | None) -> str:
    """設定値の provider キーを人が読む名前にする（未知は総称に縮退。DA2）。"""
    return PROVIDER_LABELS.get(str(provider_key or "").strip().lower(), GENERIC_PROVIDER_LABEL)


def all_data_kinds() -> tuple[DisclosureSpec, ...]:
    """宣言順の全 spec。"""
    return _SPECS


def get_data_kind(data_kind: str) -> DisclosureSpec | None:
    """data_kind で1件引く（未知は None）。"""
    return DATA_KINDS.get(str(data_kind or "").strip())


def note_for(data_kind: str, provider_key: str | None) -> str:
    """対話 UI に置く1行の事実文（未知の data_kind は空文字 = 何も描かない）。"""
    spec = get_data_kind(data_kind)
    if spec is None:
        return ""
    return spec.note_text(provider_label(provider_key))


def axes_public_view() -> list[dict]:
    """軸の宣言（6軸・宣言順）。"""
    return [axis.public_dict() for axis in AXES]


def catalog_public_view(*, provider_key: str | None = None) -> list[dict]:
    """カタログの公開表現（**宣言のみ**・値を1つも含まない）。"""
    provider = provider_label(provider_key)
    return [spec.public_dict(provider=provider) for spec in _SPECS]


def validate_catalog() -> None:
    """カタログ全体の整合を検証する（重複・軸の網羅）。

    個々の spec の検証は :meth:`DisclosureSpec.__post_init__` が行う（宣言時点で落ちる）。
    ここでは**集合としての**整合だけを見る。
    """
    seen: set[str] = set()
    for spec in _SPECS:
        if spec.data_kind in seen:
            raise ValueError(f"data_kind が重複しています: {spec.data_kind!r}")
        seen.add(spec.data_kind)
        declared = set(spec.axis_values())
        if declared != set(AXIS_IDS):
            raise ValueError(
                f"{spec.data_kind}: 軸の集合が宣言と一致しません "
                f"（不足: {sorted(set(AXIS_IDS) - declared)} / 余分: {sorted(declared - set(AXIS_IDS))}）"
            )
    if set(DATA_KINDS) != seen:
        raise ValueError("DATA_KINDS と宣言列が一致していません")

    axis_ids = [axis.id for axis in AXES]
    if len(set(axis_ids)) != len(axis_ids):
        raise ValueError(f"軸 id が重複しています: {axis_ids}")

    # DA4/DA6: 学習者が読み手の spec は、外部 AI を通らない経路も必ず1つ以上挙げる
    # （「送られる」だけを並べると、送られない経路まで送られていると読める）。
    for spec in _SPECS:
        if spec.learner_facing and not spec.without_ai:
            raise ValueError(
                f"{spec.data_kind}: 学習者が読む宣言には without_ai を1つ以上書くこと（DA6）"
            )


validate_catalog()
