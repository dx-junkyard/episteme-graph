# AI アシスタント UX ロードマップ（2026-09-12）— 入口統合 → 構造 grounding → ストリーミング

[← アーキテクチャ概要](overview.md) ｜ 関連: [AI エージェント全件棚卸し（2026-09-10）](agent_inventory_and_refactoring_2026-09-10.md) /
[チャット型 AI の共通規約](../features/assistant_common_infra_design.md) / [ビジョン §6](../vision.md#§6-横断設計原則カタログ)

> **状態: 設計中**（Phase 1 = 入口統合・Phase 2 = 構造 grounding は **いずれも 2026-09-12
> 実装済み**。**次は Phase 3 = ストリーミング**（未着手）。3段の順序と依存を確定した計画文書で、
> 各段の正本設計書は §3 の表。実装が済んだ段は各設計書側に §実装記録があり、本書は §6 の
> 判定表だけを更新する）

## 1. 問い — 「自然な知識へのアクセス」と「滑らかさ」の何が欠けているか

2026-09-10 の全件棚卸しでは、エージェントの**中身**（LLM 呼び出しの骨格）を共通化した。
その結果を「学習者が知識に自然に到達できるか」「体験が滑らかか」で読み直すと、欠けているのは
中身ではなく **入口・grounding・待ち時間** の3点である（2026-09-11 に file:line で再確認）。

| # | 欠落 | 事実（正本は各設計書） | 利用者が受ける影響 |
|---|---|---|---|
| 1 | **話し方を学習者が UI 語彙で選ぶ** | `intent_mode`（質問=on_path/explore / casual / discuss）・`cycle_mode`・`discuss_scope`・精読トグル・再構成・楽屋が別入口。サーバの4分類器 `_classify_intent` を casual は迂回し、**casual はテキストから入れない**（ハンズフリー音声のみ、`app.js` §ハンズフリー） | 「軽く聞く」と「論文と議論する」を学習者が事前に選ぶ。UI 語彙の学習コスト |
| 2 | **会話の grounding が chunk 止まり** | 学習チャットの文脈 = 表示中教材 先頭5000字 + pgvector 上位8チャンク。パイプラインが作った claim / component / 理論操作グラフ / 台帳 / 配置は回答プロンプトに入らない。`selection_text` も帰属記録専用で回答に効かない。画面文脈アダプター（SA層）Phase 4 が未実装（→ **2026-09-12 に解消**: SA層 §11.15。選択箇所と選択要素の構造事実が回答プロンプトに載る） | 構造の答えは別タブにあり、会話はチャンク文面の再構成になる。「ここについて質問」の期待とずれる |
| 3 | **待ち時間が無音** | 意図分類 → 埋め込み検索 → 本体生成（+ advice / scaffold）が直列で、`core/llm.py` にストリーム経路が無い。`StreamingResponse` は export のみ | 長い回答ほど無音が伸びる。滑らかさへの影響が最大 |

副次的な観察（本ロードマップの対象外・別件）: degraded 固定文の系統差（6種）、Copilot の応答が
KB テンプレートのみ、教員 AI モーダルの履歴保存先の不統一、候補レビュー場所の分散、
`learning.py` の `get_llm_params(tier)` 明示渡し（M層迂回）。棚卸し記録 §6.6 と本書 §7 に残す。

## 2. 順序の根拠

**入口統合（Phase 1）→ 構造 grounding（Phase 2）→ ストリーミング（Phase 3）** の順にする。

1. **Phase 1 が Phase 2 の前提**: grounding に何を足すかは「その発話がどの様相か」で決まる
   （casual では構造ブロックを省く、cycle では伏せフィールドを出さない、discuss は `_doc:` 文書
   スコープ）。様相判定が UI ボタンに分散したままだと、Phase 2 の解決器は入口ごとに分岐を持つ
   ことになる。先に「様相はサーバが1箇所で決める」に寄せ、Phase 2 はその1箇所の出力を読む。
2. **Phase 2 が Phase 3 の前提**: ストリーミングは `_learning_chat_core` を「前処理 / 生成 /
   後処理」に分ける改修を伴う。grounding の合流点（前処理の末尾）を先に確定しておかないと、
   分割後に前処理へ差し戻す二重作業になる。また Phase 2 は LLM 回数ゼロ・migration なしで
   最も安全に価値を出せる。
3. **Phase 3 は最後**: 効果は最大だが、後処理（引用整合・誤解検出・鏡・ドリルダウン・hygiene・
   U層計測）を完成テキストで行う契約を崩さないための設計量が最も多い。Phase 1/2 で入口と
   前処理が固定された後に着手する方が、非ストリーム経路との byte 一致を保ちやすい。

各段は独立に出荷可能で、前段を飛ばしても後段が壊れることはない（依存は「設計の手戻り」で
あって「動作の前提」ではない）。ただし順序を変えるときは本書 §6 に理由を書く。

## 3. 各段の正本と範囲

| Phase | 正本設計書 | 範囲 | migration | LLM 回数 |
|---|---|---|---|---|
| 1 入口統合 | [learning_chat_entry_unification_design.md](../features/learning_chat_entry_unification_design.md) | 単一 composer・様相はサーバ推定（HELP 非LLM pre-route の後）・明示 UI は override・casual のテキスト入口・`stance_source` の記録 | なし | +0〜1/ターン（既存分類器に吸収） |
| 2 構造 grounding | [assistant_screen_adapter_design.md §11](../features/assistant_screen_adapter_design.md)（SA層 Phase 4） | `screen="learning"` の参照渡し・`learner_context_common` 射影のみで解決・選択テキストの注入・段階導入（**4-a / 4-b / 4-d 実装済み・4-c は保留**） | なし | +0（SA3） |
| 3 ストリーミング | [llm_response_streaming_design.md](../features/llm_response_streaming_design.md) | `generate_text_stream`・`/chat/stream`（前処理 / 生成 / 後処理の分離）・終端イベントでメタ一括・U層の stream 計測 | なし | 不変 |

## 4. 3段に共通する不変条項（各設計書はこれを継承し、例外は設計書に明記する）

- **RM1 骨格は非LLM・同期**（vision 原則9・UC8）: 様相推定の一次判定・grounding 解決・ストリーム
  の前後処理は決定論。LLM が落ちても degraded で体験が閉じる。
- **RM2 無断でスコープを変えない**（DM1）: 様相推定も grounding も、検索範囲・文書スコープを
  学習者の明示なしに広げも狭めもしない。
- **RM3 沈黙適応しない**（原則12・UC5）: 推定するのは「会話の様相」であって「学習者の能力」では
  ない。提示内容・提示順・対話方針を能力推定で暗黙に変えない。推定結果は事実として見せ、
  訂正は学習者の1タップ。
- **RM4 数値・内部 ID を学習者に出さない**（原則4・PL7・W8）: 様相の確信度、トークン数、所要時間、
  `eq_op_*` 等の内部 ID は UI にも事実文にも出さない。
- **RM5 出所の正直さ**（原則8）: 構造 grounding の事実文は出所ラベル（AI推定（未確認）/ 教員確認済み）
  と閉世界語彙（SL1）を保つ。ストリームの部分テキストを正本にしない。
- **RM6 保存・監査・痕跡は完成テキストと確定操作だけ**（原則14・SA6）: `screen_context` と
  ストリーム途中状態は保存しない。`stance_source` は痕跡 payload に事実として残す。
- **RM7 既存 seam を壊さない**: `window_history(body.history, max_messages=20, max_chars=2000)`
  の逐語、`patch("api.routes.learning.generate_text")` の patch 対象、HELP pre-route の位置、
  CostGate の消費位置（LLM 呼び出し前・ストリーム開始前）は不変。source-text ガードレールが固定する。
- **RM8 後方互換**: 新フィールドは optional。`screen_context` 無し・ストリーム無しの要求は
  現行と byte 一致。

## 5. vision §6 14原則への照合（ロードマップ全体）

| 原則 | 触れ方 | 例外 |
|---|---|---|
| 1 AIは候補まで・確定は人間 | 様相推定は候補提示（訂正チップ）。grounding は読み取り。ストリームは表示先行 | なし |
| 2 evidence-based | 構造 grounding は逐語引用・出所付きの事実文のみ | なし |
| 3 情報を落とさない | 推定様相・訂正は痕跡 payload に保持（行削除なし） | なし |
| 4 数値の用途と粒度 | RM4。教員向け集約（誤ルーティング率等）は k-匿名・定義公開（indicator catalog に追加） | なし |
| 5 監視しない | `stance_source` の教員向け集約は k=3 集約のみ・評価利用禁止 | なし |
| 6 egocentric | 学習者の grounding は本人可視範囲のみ | なし |
| 7 リンクであってマージではない | 入口統合は UI 語彙の統合であり、内部様相（casual/discuss/cycle）の語彙・痕跡区分はマージしない | なし |
| 8 出所の正直さ | RM5。様相推定も「推定」と表示 | なし |
| 9 同期パスに LLM を入れない | RM1。様相推定の LLM 精緻化は既存分類器の1コールに吸収（曖昧時のみ） | Phase 1 は既存の同期 1 コールを流用（新規増ではない） |
| 10 完了フラグを持たない | 導出のみ | なし |
| 11 fail-closed | grounding の解決は権限ゲート付き core 経由のみ・不在は黙って落とす | なし |
| 12 押し付けない | RM3。cycle は推定で**入らない**（提案まで） | なし |
| 13 層は積層 | 3段とも既存層を読む側。`_learning_chat_core` の分割は同一契約の再配置 | なし |
| 14 監査必須 | 状態変更なし（読みと表示のみ）。訂正操作は痕跡に帰属付き | なし |

## 6. 受け入れ条件と判定表（実装のたびに更新する）

| Phase | 受け入れ条件（体験） | 受け入れ条件（構造） | 状態 |
|---|---|---|---|
| 1 | 学習者が composer 1つで「軽い質問」「論文との議論」を書き分けずに済み、推定が外れたら1タップで直せる。casual にテキストから入れる | HELP pre-route の順序不変・DM1 のスコープ不変・discuss 中に casual を推定しない・cycle を推定で入らない・`stance_source` が痕跡に残る・LLM 回数 +0〜1 | **実装済み（2026-09-12）** — 記録は [入口統合設計書 §13](../features/learning_chat_entry_unification_design.md) |
| 2 | 「ここについて質問」が選択箇所を踏まえて答える。表示中のチップ（claim / component / 式）に触れた質問が構造の事実で答えられる | `screen_context` 無しで byte 一致・解決器は学習者射影のみ経由・数値/内部ID/伏せフィールド非漏洩・LLM 回数 +0・`screen_context` 非保存 | **実装済み（2026-09-12）** — 4-a / 4-b / 4-d。4-c（表示中トピックの主張要約）は §11.13-3 のとおり保留。記録は [SA層設計書 §11.15](../features/assistant_screen_adapter_design.md) |
| 3 | 最初の文字が数秒以内に出る。途中失敗でも同じ吹き出しが degraded に置き換わる | 非ストリーム API 不変・quota は最初のバイト前・終端イベントに DTO 全項目・hygiene は chunk と完成後・U層 usage を1回・reported/estimated を混ぜない | 設計中（**次はこの段**） |

## 7. 本ロードマップの範囲外（別件として記録）

- degraded 文言の統一・回復導線の提示（棚卸し記録 §2.3 の 6 種）。
- Admin Copilot の応答生成（テンプレート → 文脈を受けた自然文）と画面文脈の全タブ化（SA層 Phase 3）。
- 教員 AI モーダルの履歴保存先の統一（ブラウザ内 / DB の混在）。
- 候補レビュー場所の一望化（G層 To-Do の拡張）。
- `learning.py` の `get_llm_params(tier)` 明示渡し（M層解決順序の迂回）。
- 音声（casual / 教員グラフレビュー）の文単位 TTS（Phase 3 の設計書で 3-c として検討のみ）。
