# 応答の骨格（Dialogue Response Shape）設計書

- 状態: v1 実装（2026-09-30）
- 対象: 学習チャット（`_learning_chat_core`）の RAG 応答 — tutor / casual_light / discuss / 理解サイクル / 確認問題の壁打ち
- 関連: `docs/features/discussion_mode_design.md`（DM4）/ `docs/features/discuss_dialogue_alignment_design.md`（DA4・DA5）/
  `docs/features/seminar_brief_mirroring_design.md`（§2.1 鏡）/ `docs/backend/rag-chat.md`（前提知識の逆質問）
- 課題ナレッジ: IK-0548（鏡の規則でルール 2 と 6 が矛盾）/ IK-0575（鏡が同意の前置きを復唱）/
  IK-0576（訂正が次の往復で再訂正される）/ 前例 IK-0509（逆質問のターンで質問を落とす）

## 0. 問題

対話の「形」（答えをどこに置き、確認をいくつ、問い返しをいくつ付けるか）は、LLM の自制と、
プロンプトへの規則と固定文の継ぎ足しに任されていた。その結果、ペルソナ通し受講 第 14〜15 周で:

- 1 往復に問いのような区画が最大 4 つ並ぶ（鏡の確認 + ルール 6 の末尾の問い + 前提の逆質問 + 帰属確認カード）。
- プロンプトのルール 2（鏡の確認で終える）とルール 6（末尾に問いを必ず添える）が衝突する（IK-0548）。
- 鏡が学習者の「はい、そうですね」まで逐語で映し、問いの核を外す（IK-0575）。逐語であることしか求めず、
  どの部分を映すかを定めていなかった。
- 前の往復の訂正が、次の往復の検索で抜粋が入れ替わるだけで黙って覆る（IK-0576）。

前提の逆質問が質問を落とす件（IK-0509）は、第 15 波で「回答の後ろに逆質問を添える」固定文の継ぎ足しで
解いた。本設計はそれを一般化し、応答の区画をサーバが組み立てる。

## 1. 不変条項（RS1〜RS5）

- **RS1 先に答える**: 答えの本文を必ず先頭に置く。確認・問い返しで答えを置き換えない。
- **RS2 確認は後ろ・1 つまで**: 確認（前提の逆質問・鏡の言い直し・帰属確認カード）は答えの後ろに置き、
  1 往復に確認の区画は 1 つまで。逆質問か鏡があるターンでは帰属確認カード（`anchor_confirm`）を出さない。
- **RS3 問い返しは 1 つまで**: 本文末尾に連なる問いは最後の 1 つだけ残す。確認（逆質問・鏡）のあるターンでは
  問い返しを残さない（確認そのものが唯一の問い）。本文が問いだけなら組み替えない（答えを消さない）。
- **RS4 鏡は核心語のみ**: 鏡が映すのは学習者の発話の核心の語句で、同意・挨拶・前置き（「はい」「そうですね」等）は
  映さない。長い発話の丸写しも核心を選んでいないとみなす。核心語が残らなければ鏡を出さない（偽の鏡を作らない）。
- **RS5 保存文の形は不変**: 保存する回答本文は「本文 → 問い返し → `\n\n---\n\n` + 目印 + 逆質問」の形を保つ。
  履歴の判定器（`_prerequisite_gate_asked_in_history` / `_previous_turn_was_prerequisite_gate` /
  `_question_before_prerequisite_gate` / `_history_has_gate_skipped_notice`）はこの形に依存する。
  保存文は元の回答本文の位置で切り貼りして作る（`ResponseShape.spliced_text`）。落とすもの（2つ目以降の
  問い返し・確認のあるターンの問い返し・鏡の狭め）が無ければ**元の本文とバイト一致**し、区切りを組み直さない。
  逆質問があるときだけ末尾の空白を落として `\n\n---\n\n` + 目印 + 逆質問を足す。

## 2. 方式

生成後・保存前の 1 点で、純関数 `core/dialogue_shape.py::assemble` が回答本文を区画に分け、
`render_answer_text` が保存・応答用の本文へ組み直す。**プロンプトは区画の中身を埋めるだけ**で、順序・問いの数・
鏡の範囲はサーバが決める。LLM 呼び出しは増えない。

| 関数 | 役割 |
|---|---|
| `assemble(*, answer, learner_message, is_discuss, gate_text, degraded, gate_marker)` | 区画 `ResponseShape` を返す。degraded（固定文）は組み替えない |
| `split_closing_question(body)` | 本文末尾に連なる問いの文を取り出す。末尾のアクション目印（`[ACTION_BUTTON: …]` 等）は判定から外して末尾に残す。箇条書き・引用・コード・数式の中の問いには触れない |
| `mirror_core(mirror, learner_message)` | 鏡の引用を核心語へ狭める（冪等）。核心語が残らなければ None |
| `render_answer_text(shape)` | RS5 の形で本文を組み立てる |
| `shape_dto(shape, *, mirror)` | 応答 DTO `LearningChatResponse.shape`（固定 3 キー） |
| `previous_correction_fact(history)` | 直前の assistant 往復の訂正文（逐語・200 字まで） |

### 2.1 route への配線（`api/routes/learning.py::_learning_chat_core`）

- 逆質問の継ぎ足し（旧 `answer + "\n\n---\n\n" + _prereq_gate_suffix`）を `assemble` + `render_answer_text` に置き換えた
  （位置は同じ・`persist_chat_history` の前）。逆質問の本文は `_prereq_gate_text_for_shape` で渡し、目印
  `PREREQUISITE_GATE_ANSWERED_MARKER` は `gate_marker` 引数で渡す（core から route を import しない）。
- discuss の鏡: `assemble` が保存文の 〔鏡〕…〔/鏡〕 を核心語だけの鏡に置き換える（核心語が無ければ取り除く）。
  既存の `extract_mirror`（保存・痕跡の後）はそのまま残し、直後に `mirror_core` を掛ける（冪等）。
- `anchor_confirm` の条件に `and _response_shape.allows_anchor_confirm` を足した（`check_and_count_confirm_prompt`
  の前 = 出さないターンでセッション内上限を消費しない）。
- `LearningChatResponse.shape = {"closing_question", "has_gate", "mirror_kept"}`。数値は載せない。ストリーミングの
  `final` は `model_dump()` のまま運ぶ（ST6）。delta は生成中の本文のままで、`final` が区画済みの本文で置き換える（ST1）。

### 2.2 フロント（`frontend/public/js/app.js`）

`renderAiContent` が `msg.shape.closing_question` を本文の末尾から切り分け、`.closing-question` の独立段落として描く
（本文と二重に出さない）。履歴復元で `shape` が無いときは本文をそのまま描く。

## 3. 鏡の核心語（RS4 / IK-0575）の規則

- 前置きの語（固定表）: はい / ええ / うん / そうですね / なるほど / わかりました / 了解 / ありがとう / 確かに /
  その通り / では / じゃあ / えっと / OK / Yes / Sure / Right / Thanks / I see / Got it ほか。
- 前置きは語境界でだけ剥がす: 日本語の語は後ろに区切り（読点・句点・感嘆・疑問・空白・改行など）か文末が
  続くときだけ、英字の語は語境界があるときだけ（「ではなく」「うんどう」「確かに存在する」は削らない。
  「では」の後ろに区切りの無い「ではハッブル…」も保守的に残す）。
- 前置きで始まる引用は前置きと区切りを剥がす（逐語部分文字列のまま）。前置きだけの引用は落とし、引用どうしを
  つないでいた「、」「と」も落とす。
- 前置きを除いて 40 字を超える発話を丸ごと写した引用は落とす。
- verbatim 検査（`extract_mirror`）は不変。狭めた後も引用は学習者の発話の逐語部分文字列である。

プロンプト側はルール 2 に「引用は学生の発話の核心の語句に限り、同意・挨拶・前置き（「はい」「そうですね」など）は
引用しないでください」を 1 文足した（サーバの規則と同じことを言う）。

## 4. ルール 2 と 6 の矛盾（IK-0548）の解き方

ルール 2（鏡の確認）が優先する。ルール 6 の F4 文（「確認の問い自体がこの必須要素を満たします」）は残し、
「末尾の問いは1つだけ…サーバは末尾に連なる問いを1つだけ残し、確認のあるターンでは問い返しを残しません」を足した。
モデルが両方を出しても、サーバが鏡のあるターンの末尾の問い返しを落とす（RS3）ので、学習者に届く問いは 1 つ。

## 5. DM4 との関係

DM4「応答末尾の生成プロンプトは構造的必須」は維持する。サーバは問い返しを**削って 1 つに**するだけで、
足さない。確認（鏡・逆質問）のあるターンでは確認が生成プロンプトの役を担う（DA4 の F4 と同じ扱い）。

## 6. 訂正の持ち越し（IK-0576）

原因: 毎回の検索で出典の抜粋が入れ替わり、前の往復の訂正は誤解の候補（`detect_and_record_misconception`）に
記録されるだけで次の往復に戻らない。前の往復の結論を持ち越す経路が無かった。

方式: 直前の assistant 往復が訂正の語（訂正 / より正確です / 誤解）を含むとき、その文を逐語（200 字まで）で
`[前の往復での訂正]` ブロックとして system の末尾に足し、「前の回答の訂正を撤回する場合は撤回だと明示してください
（検索結果の違いだけで結論を変えないでください）」と添える。撤回は禁じない（誤った訂正を直せなくしない）。
casual と `cycle_mode=elicit` では持ち越さない。**読む履歴はサーバに保存された履歴**（書き直しなら切り詰め済みの
サーバ履歴）で、クライアントが送った `body.history` の本文を system に持ち上げない。読む前に制御文字・
`[ACTION_BUTTON: …]`・`[〇〇について詳しく聞く]`・`[出典N]`・〔鏡〕…〔/鏡〕・鏡の移設注記を剥がす。**注入の位置**: `_learning_chat_core` の `_anchor_ladder_hint` を
system に足す直後（`messages: list[dict] = [` の前）。保存文・痕跡は不変。

## 7. 非スコープ（v1）

- 問い返しの内容の評価（uptake の質・汎用文の検出）— プロンプト（DA4）のまま。
- 訂正の構造化（訂正対象の主張 ID への紐づけ・コースを跨ぐ持ち越し）— 直前 1 往復の逐語だけ。
- 早期 return（HELP / 学習相談 / 地図 / 要素説明）の区画化 — 最終 return の RAG 応答だけが対象。
- 出典本文と引用の照合（IK-0545）— 別件。

## 8. 実装記録（2026-09-30）

- `backend/core/dialogue_shape.py`（新設・FastAPI / sqlalchemy / LLM 非 import）。
- `backend/api/routes/learning.py`: 逆質問の継ぎ足しを `assemble` に置換 / `anchor_confirm` の条件 / 鏡の `mirror_core` /
  `shape=` / `_previous_correction_block` と注入 / discuss プロンプトのルール 2・6 に各 1 文。
- `backend/api/schemas.py`: `LearningChatResponse.shape`。
- `frontend/public/js/app.js`: `splitClosingQuestion` / `renderAiContent` の `.closing-question`・`shape` の保持。
  `index.html` の `?v=` を更新。`css/styles.css` に `.closing-question`。
- テスト: `backend/tests/test_dialogue_shape_{core,guardrails,route}.py`。既存の鏡・discuss・前提の逆質問・
  ストリーミング・様相のガードレールは不変で通る。
