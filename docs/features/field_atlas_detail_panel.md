# 分野の地図 — 詳細パネル・チャット遷移・学習パス提案カード (Issue C)

仕様書 `field_atlas_overlay_spec.md` §6 / §8 の実装。issue B (オーバーレイ UI) の
ノード選択イベントに接続し、「地図を眺める」から「そこから動く」への遷移層を実装する。

> 注記 (2026-08-14): `field_atlas_overlay_spec.md` の原本は消失している。現存するのは
> 2026-08-14 の**再構成版**で、**旧§番号との対応は保証されない**。

## 実装ファイル

| ファイル | 役割 |
|---|---|
| `frontend/public/js/atlas-panel.js` | C-1 詳細パネル (検証行・承認行・アクション行) + C-2 ↗ アクション |
| `frontend/public/js/atlas-overlay.js` | C-2 再オープン時の選択・レベル復元 / `data` getter 公開 |
| `frontend/public/js/app.js` | `sendPrompt(text, payload)` 拡張・学習パスカード描画・三択の配線 |
| `frontend/public/css/atlas.css` | パネル中身・カードのスタイル |
| `backend/core/atlas_path.py` | C-3 学習パスカードの決定論的生成 (純関数・LLM 不使用) |
| `backend/api/routes/learning.py` | チャットの atlas 分岐 (mind/learn) / `POST …/atlas/path-decision` |
| `backend/api/schemas.py` | `LearningChatRequest.atlas_context` / `LearningChatResponse.atlas_path_card` |

## C-1: 詳細パネル

- `atlas:nodeselect` イベント (`{node_id, level, skeleton_version}`) を購読して
  `#atlas-panel-body` に検証行・承認行・アクション行を差し込む。名前・状態ピルは
  issue B の実装 (破線枠ピル含む) をそのまま使う。
- アクション表示規則 (§6.2): `learn` / `evid` はノードのフラグで表示制御
  (霧・行間では非表示)。`気になる ↗` `修正を報告` は常時表示。
- `[修正を報告]` は `CustomEvent "atlas:reportrequest"` の発火のみ
  (フォームと送信は issue D)。

## C-2: チャット遷移

- ↗ の共通機構: `AtlasOverlay.close()` → `window.sendPrompt(text, {atlas_context})`。
  構造化ペイロード `{node_id, level, skeleton_version, action, node_label, ...}` を
  必ず添付する (自由文のみに依存しない)。プロンプト文言はモックと同一。
- サーバ側 (`learning_chat`):
  - `mind` (気になる ↗) → **既存 tension 記録経路** (interest_traces `kind='tension'`)
    に帰属つきで記録。学習者本人が押した宣言なので `status='open'`
    (LLM 候補の `candidate` とは区別。P1: 違和感を生成するのは人間)。応答は決定論的な
    記録確認文で、LLM を呼ばない。
  - `learn` (ここから学ぶ ↗) → 学習パス提案カード (下記 C-3)。
  - `evid` (根拠を見る ↗) ほか → 通常の RAG フロー (意図分類・前提ゲートはバイパス)。
    関心痕跡の payload に `atlas` 帰属を焼き込む。
- 対話履歴はそのまま継続する (新規セッションを切らない)。
- オーバーレイ再オープン時に直前の選択・レベルを復元する。

## C-3: 学習パス提案カード

- 生成は `core/atlas_path.build_learning_path_card()` — **決定論的** (リアルタイム
  LLM 生成をしない §1.2-6)。入力:
  対象ノード + クライアント添付の地図投影 (`related` = レベル別の依存順ノード列、
  `juxtapose` = 並置候補) + `interest_traces` (既習の痕跡・いまの糸) +
  コーストピックの `prerequisites` (概念グラフ依存の近似) + 台帳状態 (ノード status)。
- 表示規則:
  - 各ステップに出所 (`教材` / `AI一般知識`) と状態ラベルを明示
  - 行間 (`gap`) は「先生に聞くポイント」として質問テンプレートを添付
  - 暗黙の前提 (`assumed`) は台帳の事実 (`記帳された直接検証なし`) のみ表示 (評価しない)
  - 終端は可能なら並置 (2カラムの単純並置) + 「自分で繋ぐ」入力。接続の言語化はしない
  - 上限 (6ステップ) で省いた分は `notes` に明示 (silent cap 禁止)
- 三択: `[この糸で進む]` `[編集する]` `[今はやめる]` + 「自分で繋ぐ」の記録。
  すべて `POST /api/learning/courses/{course_id}/atlas/path-decision` で
  interest_traces に帰属つきで記録する:
  proceed/edit → `resolved`、**dismiss → `dismissed` (却下も記録・削除しない)**、
  connect → `articulated` (本人の言葉を主文に残す)。
- `[編集する]` はパスの文字列をチャット入力欄に展開し、本人の言葉で編集して送らせる。

## 未決事項の決定 (issue C 内で決めるとされていたもの)

1. **学習パスカードの生成場所** — 既存の対話エンドポイント
   (`POST …/topics/{topic_id}/chat`) に載せる。専用エンドポイントは増やさない
   (プロンプトが対話に載る一貫性と、履歴・関心痕跡の既存永続化を流用するため)。
   生成自体は非 LLM の決定論的ビルダー。
2. **オーバーレイ選択状態の復元の保持期間** — セッション内 (メモリ) のみ。
   骨格データが差し替わったら破棄する (骨格改版時の concept id 永続性問題 —
   仕様書 §16-4 — を持ち込まないため)。

## テスト

`backend/tests/test_atlas_detail_panel_transitions.py` — カード生成の
ユニットテスト + ソース静的検証 (受け入れ条件 1〜6 に対応。評価語・誘導文言の
不在チェックを含む)。フィクスチャ全15ノードのパネル一致と ↗ 3アクションの
「閉じる→プロンプト→構造化ペイロード」はヘッドレスブラウザの DOM ハーネスでも確認済み。
