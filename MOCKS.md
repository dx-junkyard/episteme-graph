# MOCKS 台帳 — 学習者体験レイヤー(B層)

> **位置づけ注記（2026-08-14）**: 本台帳は B層初期（Stage M〜4）の歴史的記録である。
> 以降の全レイヤーは candidate/status 遷移パターンに発展的統合されており、新機能はこの
> 台帳への追記を要しない。

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
| （なし） | — | — | open な Mock はありません（全 Stage 完了） | — | — |

## 状態の意味
- `open`: まだ Mock。本物に未置換。
- `replaced`: 本物に置換済み（行を残す場合の記録用。原則は削除）。

## 完了記録（履歴）

Stage M で導入した全 Mock（M01〜M10）は Stage 1〜4 で本実装へ置換済み。

| Stage | 解消した Mock | 内容 |
|---|---|---|
| Stage 1 | M04, M10 | PositionAnchor をセグメント精度の実データ化／件数配色の検証用Mock撤去 |
| Stage 2 | M01, M02, M03, M07 | tier 判定を承認(teacher_reviewed)由来の実データ化・OutOfSourceGuard 順序ゲート |
| Stage 3 | M05 | interest_traces テーブル＋安価記録＋実データ読み取り |
| Stage 4 | M06, M08, M09 | InterestDashboard 実集計・Internalization Prompt 配線 |

**取り残しゼロの確認:**
```bash
grep -rn "EPISTEME_MOCK" backend/ frontend/   # → 0 件
grep -n "| open " MOCKS.md                      # → 0 行
```

## 今後の任意拡張（Mock ではない・未実装の追加機能）
仕様書 Phase 4 が挙げる以下は「Mock」ではなく未着手の発展機能。必要時に追加する。
- **TraceAnalyzer**: 痕跡の interest_kind 一括分類（LLM）
- **DecayPolicy**: 再訪タイミングの本格的な間隔最適化（現状は最古の未解決を選ぶ簡易版）
- **MaterialSuggester**: 関心集中領域 →(A) course_mapping/blueprint と突き合わせた教材拡張提案
