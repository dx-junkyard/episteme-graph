# 学習機能（学生UI）

[← ドキュメント目次](../README.md)

> **鮮度注記（2026-08-13）:** 本書は 2026-07-18 時点の記述で、以降に追加された学生向け主要機能 —
> **discuss モード（サイドバー二枚看板「順番に学ぶ / この論文と議論する」・開幕/着地画面）**・
> **理解サイクル**（精読モード・持ち越し問い・帰り道の景色）・**わたしの地図**の拡張・
> SL層の台帳事実行 — が未反映。現行の正本は CLAUDE.md の該当節と各設計書
> （[discussion_mode_design.md](discussion_mode_design.md) /
> [understanding_cycle_design.md](understanding_cycle_design.md) /
> [personal_knowledge_network_design.md](personal_knowledge_network_design.md)）を参照。

学生向け学習 UI の機能を、画面と裏側の API の両面から解説します。
実装: `frontend/public/index.html` + `frontend/public/js/app.js`（ES6+ SPA）。
バックエンドは `/api/learning/*`（[API](../backend/api.md)）。

---

## 1. 3 パネルレイアウト

| パネル | 内容 |
|---|---|
| 左サイドバー（260px） | 学習パス（章→トピックのツリー、進捗ドット: 完了🟢/進行中🔵/ロック⚫）、概念マップ（習得/学習中/未来） |
| 中央 | RAG チャット UI + レクチャーコンテンツ表示領域。トピック教材チャンクをチャット上部に表示 |
| 右パネル（300px） | Context / Progress / Sources タブ（前提知識・誤解・学習支援ヒント、章進捗、PDF 出典） |

主要 state（`app.js`）: `token / role / courseId / course(マスター) / personalLayer(誤解・アンカー) / currentTopicId / chatMessages / topicMaterial / learningSupport` に加え、`lastGrounding / lastSources / lastOverallTier`（出所・出典表示）、`interestTraces`（問いの軌跡）、`tensionDigest / tensionDeferred`（違和感ダイジェスト）、`topicHasAudio`（レクチャー音声の有無）。

---

## 2. コース受講と進捗

| 機能 | 仕組み | API |
|---|---|---|
| コース一覧 | 「自分のコース」と「受講可能なコース（公開テンプレート）」を分けて表示 | `GET /api/learning/courses` |
| 受講登録 | 公開テンプレートをクローンせず、`learning_states` に1行 INSERT して個人の学習状態を作成 | `POST /api/learning/courses/{id}/enroll` |
| コース読込 | マスター + 個人レイヤーを取得 | `GET /api/learning/courses/{id}` |
| 進捗 | 章ごとの完了状況、セッション履歴 | `GET /api/learning/courses/{id}/progress` |
| トピック教材 | コンテキスト用にチャンクを取得 | `GET .../topics/{tid}/material` |

> マスター（不変）と個人進捗（可変）の分離は [データモデル](../architecture/data-model.md#重要な設計パターン) を参照。

### 2.1 受講登録の確認ダイアログ

コース選択で「受講可能なコース」を選んでも即時には受講登録しない。確認モーダル
（`app.js` の受講確認オーバーレイ。コースタイトル + `description` を提示し
「受講する / キャンセル」）を経てから `POST .../enroll` を呼ぶ。キャンセル時は
select を元の値へ戻し、失敗時はモーダル内にエラー表示して再試行できる。

### 2.2 コース完了カード（サーバー正本の完了判定）

確認問題（`POST .../topics/{tid}/check-question` → `.../check`）に合格すると、サーバーが
`services.record_topic_check_pass()` で **`learning_states.progress_data`** に永続化する:

- `progress_data.completed_topics`（topic_id → 合格時刻 ISO8601。既存タイムスタンプは上書きしない）
- 全トピック合格時に `progress_data.course_completed_at` を一度だけ設定

`/check` レスポンスの `course_completed` / `completed_topic_ids` が**サーバー正本**で、
フロント（`app.js` の `showCourseCompletionCard`）は `course_completed === true` のときだけ
「全トピックを学習しました」と断定する（「次のトピックが無い」ことだけで完走と断定しない。
未確認なら「まだ確認を終えていないトピックがあります」に縮退 — fail-closed）。カードは
事実文のみ（数値・スコア・祝祭演出なし）で、「他のコースを見る」「わたしの地図を見る」
（`PersonalMapHome.open()`）への導線を添える。

---

## 3. RAG チャット

- 送信ボタンは「質問」1 つに統合済み（旧「教材に沿って質問」「自由に質問」の 2 ボタンは廃止。
  `intent_mode` の on_path / explore は寄り道状態からフロントが自動判定して送る内部値）。
- 送信: `POST /api/learning/courses/{id}/topics/{tid}/chat`（`message` + `history` + 任意のアクション payload）
- 応答に含まれるもの:
  - `answer`（Markdown、末尾にドリルダウンリンク `[〇〇について詳しく聞く]`）
  - `next_actions`（「学習に戻る」「詳細を続ける」などをボタン化）
  - `support_mode` / `status_label` / `origin`
  - `content_grounding`（出所: 教材 / 別の資料 / モデル生成 — 下記）
  - `course_update.personal_layer`（`misconceptions_by_topic`, `chat_anchors`）
- **誤解検出**: 回答に訂正シグナルが含まれると個人レイヤーに記録され、トピックに誤解バッジが付く。
- **前提知識チェック**: 未習得の前提があれば逆質問（`mode="prerequisite_review"`）。
- **理解度チェック**: `POST .../topics/{tid}/check-question` で習得を確認し次トピックへ。

### 回答の出所表示（content_grounding）
回答バブル下部と出典タブのバナーに、回答が何に基づくかをバッジで表示します
（`app.js` の `GROUNDING_META` / `groundingBadge()`）。

| 値 | 表示 |
|---|---|
| `course_material` | 教材から回答 |
| `other_material` | 別の資料から回答 |
| `model_generated` | AI の一般知識（出典なし） |

`tier`（教員承認状況のバッジ）とは別軸です。判定ロジックは [RAG チャットフロー](../backend/rag-chat.md#4-出所判定content_grounding)。

裏側の流れの詳細は [RAG チャットフロー](../backend/rag-chat.md)。

---

## 3.5 ハンズフリー音声会話（カジュアル対話モード）

チャット入力欄の 🤖 ボタンで「気軽に話せる先生」との音声会話を開始します（`app.js`）。

1. MediaRecorder + WebAudio の無音検知（発話後 ~1.4 秒の沈黙）で発話を自動区切り
2. `POST /api/learning/voice/transcribe` で Whisper 文字起こし
3. `intent_mode='casual'` でチャット送信（雑談拒否・前提知識ゲート・誤解検出をバイパスし、
   短い会話調で応答。RAG 検索・tier・OutOfSourceGuard はそのまま → [RAG チャットフロー](../backend/rag-chat.md#3-インテントモードon_path--explore--casual)）
4. 応答を `POST /api/learning/voice/speak`（TTS, MP3）で再生（再生中はマイク停止、終了で聞き取り再開）
5. 応答の第 1 根拠チャンク（`sources[0].chunk_id`）を `GET .../source-chunk/{chunk_id}` で取得し、
   ボイスパネルに「いま話している題材」として教材表示

状態表示（「聞いています…」「文字起こし中…」「先生が話しています…」）付き。
interest_traces 記録と tension プレフィルタは通常どおり効きます（payload に `casual: true`）。

---

## 3.6 違和感（tension）ダイジェスト

対話ログから TensionMiningAgent（B層）が検出した「理解した上での引っかかり」の**候補**を、
進捗タブに「引っかかりの気配」カードとして提示します（`app.js` の `renderTensionDigestCard()`）。

- 取得: `GET /api/learning/courses/{id}/tension/digest`（本人のみ・candidate・最大 3 件。confidence 数値は見せない）
- 本人の操作: **そう、これ**（confirm → 自分の言葉で言い直すと articulated）/ **違う**（dismiss）/ あとで
- 確定した tension は「問いの軌跡」に昇格し、グラフ上の node/edge への接続（connect）もできる
- 教員・管理者は個別の tension 行にアクセスできない（k-匿名化された集計のみ）

検出パイプラインの詳細は [RAG チャットフロー](../backend/rag-chat.md#5-tension-プレフィルタと-tensionminingagentb層)。

---

## 3.7 チャットメッセージの書き直し・削除（truncate セマンティクス）

学習者は自分の入力メッセージを **書き直し（✏️）／以降削除（🗑）** できる（`app.js`。
id を持つ user バブルにのみボタンを出し、送信中は出さない）。どちらも
「そのメッセージ以降の往復を捨てる」**truncate セマンティクス**で統一されている。

- **書き直し**: ✏️ で本文を入力欄へ戻し `editingMessageId` を立てる。送信時に
  `LearningChatRequest.replace_message_id` を添えると、サーバーは
  `services.truncate_chat_and_supersede()` で正本履歴を当該メッセージ位置で切り詰めてから、
  新しい本文を同じ位置から通常フローで再処理する（誤解検出・tier・grounding は自然に再実行）。
  クライアント履歴も同位置で truncate する。トピック切替で編集状態は解除。
- **削除**: 🗑 で確認の上
  `DELETE /api/learning/courses/{id}/topics/{tid}/chat/messages/{message_id}` を呼ぶ
  （同じ truncate、再送なし。履歴が空になれば行削除）。
- **派生痕跡の後始末（P4）**: 取り除いたメッセージ由来の `interest_traces` は削除せず
  `status='superseded'` へ遷移し、以降の tension / anchor worker・ダイジェスト・
  問いの軌跡ビューから除外される。

---

## 4. インタラクティブ・レクチャーモード

論文チャンクを **セミナー形式の音声講義**に変換する没入型機能。「🎙️ レクチャー」ボタンで起動。

### シーケンス構築
- `GET /api/learning/lecture/courses/{id}/topics/{tid}/sequence`
- 返り値: `{segments: [{text, formulas: [{latex, spoken, is_display}], ...}]}`
- **適応的**: 習得済み概念のセグメントはスキップ、部分理解は要約版に変換（`lecture.py`、習得状態は `learner_mastered_concepts`）。

### 再生・カラオケ風ハイライト
- プレイヤーバー（前/再生/次、進捗バー、タイムスタンプ）
- 現在セグメント = 強調（青背景・左ボーダー）、過去 = 50% 不透明、未来 = 非表示
- 音声は `POST .../topics/{tid}/tts`（spoken_text → TTS、`lecture_audio_cache` にキャッシュ）

### 数式の扱い
- LaTeX 表示と、音声読み上げ用テキスト（例: `E=mc^2` → 「E イコール mc の二乗」）を自動生成（`lecture.py:generate_spoken_text_and_formulas`）。

### 中断チャット
- 「❓ 質問」で再生を一時停止し、別スレッドで質問 → コンテキストを保持して回答 → 「再開する」で続行。
- `POST .../topics/{tid}/interrupt`（再開位置を返す）。中断チャットの音声入力はブラウザの Web Speech API
  （ハンズフリー音声会話の Whisper 文字起こしとは別実装）。

教員側の原稿・音声の事前生成は [管理機能 — Lecture Studio](admin.md#4-lecture-studio) を参照。

---

## 5. 分野の地図（Field Atlas: オーバーレイ + ミニマップ）

学習中の箇所が**分野全体のどこに該当するか**を示す機能（CLAUDE.md「分野の地図」節が正本）。
宣言しない・煽らない・**踏破率を数値にしない**・リアルタイム LLM 生成をしない、が設計原則。

- **全画面オーバーレイ**: ヘッダの「地図」ボタン（`#atlas-btn`）や各導線カードから
  `AtlasOverlay.open()`（`atlas-overlay.js`）で開く。詳細パネルは `atlas-panel.js`、
  修正報告は `atlas-report.js`。
- **常設ミニマップ**（`atlas-minimap.js`）: 左パネル下・切手大。「いまここ + 状態ドット +
  霧ハッチ」のみで、数値・ラベル・凡例は描かない。更新はトピック遷移とオーバーレイ閉時のみ
  （ポーリング禁止）。
- **導線**（`atlas-cues.js`）: ①トピック完了直後 ②章末 ③寄り道復帰（戻った位置を
  ハイライト）④初回ログインの一度きり自動表示。①〜③はカード提示に留め自動では開かない。
  内部計測は `POST /api/learning/atlas/cues/events` / `GET .../cues/state`（数値をユーザーに
  見せる UI は無い）。
- **データ取得**: `GET /api/atlas`（`atlas-data.js`。状態判定はサーバー側のみ）。
  骨格の無いコース・カートリッジでは 404 → 地図領域ごと非表示（fail-closed。フィクスチャへの
  自動退避はしない）。

---

## 6. わたしの地図（個人知識ネットワーク）

本人の確定痕跡（tension / 帰属付き問い / 再構成成功 / connect した橋）から**毎回決定論的に
導出される**個人の知識ネットワーク（正本: `docs/features/personal_knowledge_network_design.md`）。
保存物ではなく導出・**本人のみ可視**・candidate は数えない・数値を見せない。

- **最上位パネル**（`personal-map-home.js`、ヘッダ「わたしの地図」ボタン `#my-map-btn`）:
  「いまの地図 / 問いからの旅 / 振り返り」の 3 タブ。データソースは正本 API
  `GET /api/me/personal-network`（+ `GET /api/me/personal-network/journey?node_id=`）のみ。
  常設注記「この地図はあなたにだけ表示されます。成績評価には使用されません。」
- **コースビュー**（`personal-map.js`）: Field Atlas オーバーレイに自分の記録を重ねる表示 +
  旅カード。コーススコープの旅 API は
  `GET /api/learning/courses/{id}/personal-network(...)`。別コースに同一アンカーの兄弟が
  あれば `cross_course_hint`（「以前の学習につながる道があります」）だけを返し、本人が
  開いたときのみコース横断版へ差し替える。
- **訂正操作**: `POST /api/learning/traces/{trace_id}/map-exclude` / `.../map-restore`
  （`payload.map_excluded` の状態遷移のみ。dismiss とは独立・行削除しない）。問いの軌跡の
  除外済み項目には「地図に戻す」チップが付く。

---

## 7. 再構成ループ（Reconstruction Loop）

学習者に理論の再構成（予測 / 言い直し）をさせ、A層の `theory_claims` を答えキーとして
**非LLM の構造照合**でズレを事実として返す閉ループ（R層。正本:
`docs/features/reconstruction_loop_design.md`）。

- **導線**: トピック学習ビュー下部の「🧩 再構成に挑戦」ボタン（`reconstruction.js`、
  `window.Reconstruction`）。自動では開かない。カードには「この問いは AI が自動生成した
  ものです」を常に明示。
- **フロー**: ELICIT（出題 `GET .../topics/{tid}/reconstruction/next`）→ CAPTURE（提出
  `POST /api/learning/reconstruction/{item_id}/submit` → DIFF + 出典リビール）→
  SELF-CHECK（必須 `POST .../{recon_id}/self-check`。「そのとおり / 納得できない /
  判定が間違っている」）→ 再挑戦（`POST .../{item_id}/revise`）または記号葉への降下
  （`POST .../{recon_id}/descend`）。
- 判定は「食い違いの可能性」という仮説文体で表示し、権威は出典のリビールに置く。
  数値スコア・正答率は学習者に見せない（サーバー側でも返さない）。

---

## 8. 認証フロー

1. ログイン `POST /api/auth/login` → JWT 取得
2. `localStorage["eg_token"]` に保存
3. 以降のリクエストに `Authorization: Bearer {token}`

→ ロール・権限の詳細は [認証・権限・開示範囲](auth-visibility.md)。

---

[← 動的スキーマ進化](../pipeline/schema-evolution.md) ｜ 次へ: [管理機能 →](admin.md)
