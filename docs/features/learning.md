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

主要 state（`app.js`）: `token / role / courseId / course(マスター) / personalLayer(誤解・アンカー) / currentTopicId / chatMessages / topicMaterial / learningSupport`。

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

- 送信: `POST /api/learning/courses/{id}/topics/{tid}/chat`（`message` + `history` + 任意のアクション payload）
- 応答に含まれるもの:
  - `answer`（Markdown、末尾にドリルダウンリンク `[〇〇について詳しく聞く]`）
  - `next_actions`（「学習に戻る」「詳細を続ける」などをボタン化）
  - `support_mode` / `status_label` / `origin`
  - `course_update.personal_layer`（`misconceptions_by_topic`, `chat_anchors`）
- **誤解検出**: 回答に訂正シグナルが含まれると個人レイヤーに記録され、トピックに誤解バッジが付く。
- **前提知識チェック**: 未習得の前提があれば逆質問（`mode="prerequisite_review"`）。
- **理解度チェック**: `POST .../topics/{tid}/check-question` で習得を確認し次トピックへ。

裏側の流れの詳細は [RAG チャットフロー](../backend/rag-chat.md)。

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
- `POST .../topics/{tid}/interrupt`（再開位置を返す）。音声入力は Web Speech API。

教員側の原稿・音声の事前生成は [管理機能 — Lecture Studio](admin.md#4-lecture-studio) を参照。

---

## 5. 認証フロー

1. ログイン `POST /api/auth/login` → JWT 取得
2. `localStorage["eg_token"]` に保存
3. 以降のリクエストに `Authorization: Bearer {token}`

→ ロール・権限の詳細は [認証・権限・開示範囲](auth-visibility.md)。

---

[← 動的スキーマ進化](../pipeline/schema-evolution.md) ｜ 次へ: [管理機能 →](admin.md)
