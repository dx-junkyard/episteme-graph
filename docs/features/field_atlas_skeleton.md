# 分野の地図 — 骨格 (skeleton) パイプライン運用ガイド (Issue A)

仕様書: `field_atlas_overlay_spec.md` §1.2 / §9 / §10 / §16

## 概要

骨格は、モデル知識からカートリッジ単位で一度だけバッチ生成し、教員レビューを経て
版として凍結し、カートリッジに同梱して配布する静的アセット。

```
LLMバッチ生成 (draft)             → backend/cartridges/<id>/atlas/skeleton.draft.yaml
  → 教員レビュー (管理画面「分野の地図」タブで修正・承認)
  → 凍結 (version 付与・reviewed_by 記録)  → backend/cartridges/<id>/atlas/skeleton.yaml
  → カートリッジに同梱して配布
```

- 同梱するのは**骨格のみ**。検証状態・承認・灯りは生きた台帳・C層から実行時に導出する
  (骨格に焼き込むと「地図が古い認識を権威化する」事故になる)
- 骨格が持てる状態情報は `seed_status` (初期ヒント) までで、`reviewed: true` のもののみ表示可
- **リアルタイムの LLM 生成は行わない** (コスト・幻覚・再現性)
- draft はいかなる学習者向け画面にも出さない (`core.atlas.learner_view` /
  `DomainCartridge.learner_atlas_skeleton` がコードで担保)

## 実装マップ

| 対象 | 場所 |
|---|---|
| スキーマ・バリデータ・凍結・学習者向けビュー | `backend/core/atlas.py` |
| LLM バッチ生成 + 後処理 (上限・座標・語彙) | `backend/core/atlas_generator.py` |
| バッチ生成 CLI | `python -m scripts.generate_atlas_skeleton --cartridge <id> [--force]` |
| レビュー・凍結 API (教員以上) | `backend/api/routes/atlas.py` (`/api/admin/cartridges/{id}/atlas/skeleton...`) |
| 学習者向け配信 (凍結版のみ) | `GET /api/learning/atlas/{cartridge_id}/skeleton` |
| レビュー UI | 管理画面「分野の地図」タブ (`admin.html` / `admin.js`) |
| カートリッジローダ統合 | `backend/core/cartridges.py` (`atlas_skeleton` / `learner_atlas_skeleton`) |
| テスト | `backend/tests/core/test_atlas.py` / `backend/tests/core/test_atlas_generator.py` / `backend/tests/core/test_cartridges.py` / `backend/tests/api/test_atlas_api.py` |

## CI での検証

`backend/tests/core/test_atlas.py::TestDeterminism::test_bundled_particle_physics_skeleton_is_reproducible`
が、同梱骨格の (1) スキーマ適合 (2) `generated_by` / `reviewed_by` の記録
(3) `dump(load(x)) == x` のバイト同一性 を検証する。カートリッジロード自体も
同梱骨格が不正 (draft 同梱・帰属欠落など) なら `ValueError` で失敗する = ビルド失敗。

## 改版契機 (運用)

次のいずれかで**次版**を作る (凍結済みの版は不変):

1. **年次改版** — 年1回、カートリッジ改版に合わせて見直す
2. **修正報告の蓄積が閾値超え** — 採用された修正報告 (D-1 のレビューキュー由来) が
   **10件**、または**3つ以上の異なる node/region** に修正が必要になった時点
   (D-1 issue と共有の暫定値。運用開始後に調整)
3. **分野の大きな動き** — 手動判断

改版手順: 現行の凍結版を draft としてコピー (`PUT .../skeleton/draft` に凍結版の内容を
渡す) → 修正 → `POST .../skeleton/freeze` で次版 (例 `2027.1`) を付与。
採用した修正報告の報告者は freeze 時の `credits` に載せ、changelog に帰属を残す。

## concept id の永続性ポリシー (§16-4)

改版時に足跡 (訪問履歴)・修正報告が迷子にならないための規則:

1. **id は版を跨いで不変。** ラベルの改名は id を変えない
2. **id の再利用禁止。** 削除した id を別概念に転用しない
3. **統合・分割は `id_migrations` に残す。** 例:
   ```yaml
   id_migrations:
     - {from: old_concept, to: new_concept, version: "2027.1"}
   ```
   足跡・修正報告の参照側は `id_migrations` を辿って新 id に解決する
   (分割は主たる後継 1 件に張る。残りは新規 id として扱う)
4. **領域 id も同じ規則** に従う

## 未決事項の決定記録

- **§16-1 (assumed と contested の併発)**: **assumed 優先**で表示する (点線が主役のため)。
  詳細パネルには両方の事実を記す (C-1 スコープ)
- **§16-3 (霧領域の匿名ドット個数)**: 当初は概念数 k の対数スケール
  `clamp(3 + floor(log10(max(k,1))), 3, 6)` と決めたが、実装
  (`backend/core/atlas_placement.py` の `fog_dots`) では「宣言しない」原則に沿って
  さらに保守的な**固定3個**に簡素化した (概念数に比例させると霧の中身の規模を
  示唆してしまうため)。個数は存在の示唆のみで、数値としては表示しない
- **修正報告の改版トリガ閾値**: 上記「改版契機」2. の暫定値 (D-1 issue と共有)
