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

### 8.1 第 17 周の追補（2026-10-01）

- RS3 の問いの検出に「か。」で終わる日本語の問い（`…でしょうか。` / `…ますか。` / `…ですか。` / `…かな。`）を加えた
  （最終文 80 字以内・`ことか` / `どうか` / `確か` 等の非疑問の語尾は除く = 保守側）。何も落とさないときのバイト一致（RS5）は不変。
- **詰まりの区画**: `is_stuck_reply`（「わからない」「むずい」「まだわからない」。「はい」等の相づちは含まない）が真で casual /
  discuss / 楽屋でない往復は `assemble(..., stuck=True)` が本文の最初の段落の足場（3 文まで）だけを残し、問い返しを置かない。
  前提の逆質問はその往復には添えず次の往復へ回す（提示の記帳 `record_prerequisite_gate_presented` もしない）。tutor
  プロンプトにルール 6【詰まり】を 1 文。テストは `test_dialogue_shape_stuck.py`。
- **第 18 周（ゲート後の詰まり・早期 return の骨格）**: 逆質問が出ている間の素の詰まり（「むずい」）は `_prerequisite_followup` が
  「いいえ」と読まず `after_gate` に倒し、通常の RAG + `assemble(stuck=True)` で足場 1 つ。逆質問は詰まりの往復を飛ばして保留
  （`_previous_turn_was_prerequisite_gate`）。「はい、…」＋中身は `ack_content`（肯定を記帳し中身に RAG で答える）。
  学習相談・前提説明の早期 return も `_shape_advice_answer` で骨格に通す（学習相談は見出しなし・5 文以内）。テストは `test_wave18_gate_stuck_advice.py`。

### 8.2 早期 return の骨格と逆質問の言語（2026-10-02, IK-0594 / IK-0574）

`_learning_chat_core` と委譲先の早期 return を全件列挙し、区画から外れる経路だけを直した（問い返しの無い経路に問いは足さない）。

| 経路 | 本文 | 外れ方（是正前） | 適用 |
|---|---|---|---|
| 学習パスに戻る（`return_to_learning_path`） | 固定文 | shape なし（問い返しなし） | `verbatim_shape_dto` |
| 要素タップの説明（`EXPLAIN_GRAPH_ELEMENT`・LLM 経路） | 生成 | 「最後に確認文を1つ」を LLM の自制に任せ、問い返しが連なり得る / shape なし | `assemble` + `render_answer_text`（保存文も同じ）+ `verbatim_shape_dto` |
| 要素タップの説明（承認済み説明・graph_description 由来・degraded） | 固定・承認済み | shape なし | `verbatim_shape_dto`（degraded は None のまま） |
| HELP（テキスト経路・マニュアル本文素通し） | マニュアル本文 | shape なし | `verbatim_shape_dto` のみ（**本文は切り詰めない・言い換えない**） |
| HELP（無ヒット固定文） | 固定文 | shape なし | `verbatim_shape_dto` |
| HELP（音声 / casual の会話調整形） | 生成 | 問い返しが連なり得る / shape なし | 整形本文だけ `assemble`（footer は後ろ）。整形失敗の素通しは組み替えない |
| 点数・割合の要求 / 締めくくり | 固定文 | shape なし | `verbatim_shape_dto` |
| 地図 ↗ mind / learn | 固定文 | shape なし | `verbatim_shape_dto` |
| 学習相談・前提の説明 | 生成 | 第 19 波で `_shape_advice_answer` 済み / shape なし | `verbatim_shape_dto`（最終本文を読むだけ） |
| 回答済みの逆質問に「理解している」だけ | 固定文 | shape なし | `verbatim_shape_dto` |

- `core/dialogue_shape.py::verbatim_shape_dto(text)`: `assemble` と同じ規則で区画を読むだけで本文は変えない。末尾の問いが
  1 つだけのときだけ `closing_question` に報告し、連なる問いがあれば None（組み替えていないので問い返しとは言わない）。
  `has_gate` / `mirror_kept` は常に False（早期経路に逆質問・鏡は無い）。
- ガードレール: 4 関数（`_learning_chat_core` / `_usage_help_response` / `_atlas_action_response` /
  `_generate_graph_element_explanation`）の `LearningChatResponse(` 呼び出しすべてに `shape=` があることを AST で固定
  （`test_early_return_shape.py`）。
- **逆質問の言語（IK-0574）**: 当該発話の言語（既存の `_is_mostly_latin` / `_is_kana_kanji_free` の和 = `_fixed_text_language`）を
  逆質問の本文・回答済みの目印・選択肢ボタンまで運ぶ。英語は `label_vocab` の `PREREQUISITE_GATE_FULL_EN` / `_MARKER_EN` /
  `_FIRST_EN` / `_ANSWERED_MARKER_EN` / `PREREQUISITE_CHOICE_*_EN`。RS5 の形（本文 → `\n\n---\n\n` + 目印 + 逆質問）は不変で、
  判定器は `_is_prerequisite_gate_for_topic` / `_prerequisite_gate_first_name` 経由で日英両方を読む。日本語の発話の逆質問は
  バイト一致。テストは `test_ik0574_prerequisite_gate_language.py`。
