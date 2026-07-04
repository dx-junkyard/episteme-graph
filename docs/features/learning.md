# 学習機能（学生UI）

[← ドキュメント目次](../README.md)

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
| 受講登録 | 公開テンプレートをクローンして自分用インスタンスを作成 | `POST /api/learning/courses/{id}/enroll` |
| コース読込 | マスター + 個人レイヤーを取得 | `GET /api/learning/courses/{id}` |
| 進捗 | 章ごとの完了状況、セッション履歴 | `GET /api/learning/courses/{id}/progress` |
| トピック教材 | コンテキスト用にチャンクを取得 | `GET .../topics/{tid}/material` |

> マスター（不変）と個人進捗（可変）の分離は [データモデル](../architecture/data-model.md#重要な設計パターン) を参照。

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

## 5. 認証フロー

1. ログイン `POST /api/auth/login` → JWT 取得
2. `localStorage["eg_token"]` に保存
3. 以降のリクエストに `Authorization: Bearer {token}`

→ ロール・権限の詳細は [認証・権限・開示範囲](auth-visibility.md)。

---

[← 動的スキーマ進化](../pipeline/schema-evolution.md) ｜ 次へ: [管理機能 →](admin.md)
