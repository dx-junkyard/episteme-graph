# MOCKS 台帳 — 学習者体験レイヤー(B層)

このファイルは Mock 主導実装の**唯一の真実の台帳**です。
すべての Mock はここに1行登録し、本物の実装に置き換えたら行を削除（または `状態` を `replaced` に）します。

## 目印規約（取り残しゼロを機械的に確認する）

1. **コードタグ**: すべての Mock に統一トークン `EPISTEME_MOCK` をコメントで付ける。
   - Python: `# EPISTEME_MOCK[<id>] <層>: <説明> — replace in Stage N`
   - JS: `// EPISTEME_MOCK[<id>] ...`
2. **データ印**: Mock データを返す API レスポンスは `"_mock": true`（必要なら `"_mock_fields": [...]`）を含める。
3. **UI印**: フロントは `_mock` を検知して 🚧MOCK バッジ（CSS クラス `mock-flag`）を必ず描画する。
4. **完了確認**:
   ```bash
   grep -rn "EPISTEME_MOCK" backend/ frontend/   # → 空
   grep -n "| open " MOCKS.md                      # → 空
   ```
   両方が空なら Mock 取り残しゼロ。

## 台帳

| ID | 層/コンポーネント | 場所 (file) | 内容 | 置換Stage | 状態 |
|----|------------------|------------|------|----------|------|
| M01 | L1 信頼性 / SourceTierJudge | backend/core/learning_experience.py: `judge_source_tier()` | 承認フラグ未読。score 閾値だけの簡易 tier 判定（approved は出さず安全側） | Stage 2 | open |
| M02 | L1 信頼性 / OutOfSourceGuard | backend/core/learning_experience.py: `out_of_source_notice()` / student_graph retrieval | 暫定の未踏判定とガード定型文 | Stage 2 | open |
| M03 | L1 信頼性 / search_chunks tier | backend/core/chat.py: `search_chunks()` | dormant 経路の tier も簡易判定で埋める | Stage 2 | open |
| M04 | L2 位置・復帰 / PositionAnchor | backend/core/learning_experience.py: `build_position_anchor()` | segment_id/scroll_offset は仮値（DB永続化なし） | Stage 1 | open |
| M05 | L3 資産化 / 問いの軌跡 + 再訪のころ合い | backend/core/learning_experience.py: `mock_interest_traces()` ＋ GET interest-traces | interest_traces テーブル未作成。curated mock(status主役)＋revisit_cue を返す | Stage 3/4 | open |
| M06 | L4 可視化 / InterestDashboard | backend/core/learning_experience.py: `mock_interest_dashboard()` ＋ GET /api/admin/interest-dashboard | 集団集計を固定 mock データで返す | Stage 4 | open |
| M07 | UI / tier 表示（全体格バナー・根拠の合流・answer tier bar） | frontend/public/js/app.js: `renderSourcesTab` / `renderAiContent` | tier 値は mock データ駆動 | Stage 2 | open |
| M08 | UI / 問いの軌跡（進捗タブ） | frontend/public/js/app.js: `renderProblemTrails` ＋ trace アクション | interest-traces を mock 表示。「解決済み/なぜ気になった?」は未配線 | Stage 3/4 | open |
| M09 | UI / 教員 InterestDashboard | frontend/public/js/admin.js, admin.html | dashboard を mock 表示 | Stage 4 | open |

## 状態の意味
- `open`: まだ Mock。本物に未置換。
- `replaced`: 本物に置換済み（行を残す場合の記録用。原則は削除）。
