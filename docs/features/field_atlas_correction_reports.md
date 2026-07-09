# 分野の地図 — 修正報告フロー (Issue D)

仕様書 `field_atlas_overlay_spec.md` §7 / §11 に基づく。骨格(モデル生成の地形)が
誤っているリスクへの応答として、**地図上からワンタップで修正を報告できる導線**を提供する。
疑義(challenge)の軽量版であり、Stage 4 でそのまま challenge(型: 地図修正)へ昇格する
前提のデータ構造を持つ。

## D-1: 報告フォームと送信

- `frontend/public/js/atlas-report.js`(新規): atlas-panel.js (Issue C) の接続点イベント
  `atlas:reportrequest` を購読し、詳細パネル内にインラインフォームをトグル表示する
  - textarea placeholder「この配置・状態のどこが実際と違うか」+ `[帰属つきで送信 ↗]`
  - 送信ボタンの近傍に「あなたの名前とともに記録されます（匿名にはできません）」を明示。
    **匿名オプションは存在しない**
  - 自動添付メタ: `node_id | region_id` / `level` / `skeleton_version` を選択イベント
    (Issue B の `atlas:nodeselect` detail)から取得。対象が領域(levels[*].regions に載る id)
    なら `region_id`、そうでなければ `node_id` として送る
  - 空文字送信のガード(内容未記入では送信不可。サーバ側でも 422)
  - 送信成功 → トースト「修正報告を記録しました」→ フォームを閉じてパネルへ復帰
    (**オーバーレイは閉じない**。↗ チャット遷移アクションとの違い)
- `POST /api/atlas/report`(§11): 要認証。body =
  `{ text, node_id | region_id, level, skeleton_version, cartridge_id, node_label }`。
  201 + `report_id`。帰属は JWT から取り、**匿名での送信経路は存在しない**

## D-2: レビューキュー投入と処理

新規のキュー機構は作らず、既存の C層教員レビュー導線を流用する:

- レコードの正本は `atlas_correction_reports`(migration 023)1テーブルのみ。
  状態遷移の監査は既存 `theory_review_events` に `entity_type='atlas_report'` で記録
  (`_record_review_event` を entity_type 引数で汎用化)
- レビュー画面は管理画面「分野の地図」タブ(Issue A の骨格レビュー画面)内の
  「修正報告のレビュー」セクション。表示: **報告本文 + 対象ノード/領域 + 骨格バージョン +
  報告者**、旧版への報告は「旧版（現行 X）」ラベルで識別(受け入れ条件5)
- API(教員以上):
  - `GET /api/admin/cartridges/{cartridge_id}/atlas/reports?status=` — キュー一覧
    (`version_mismatch` / `target_counts` / `revision_hint_targets` 付き)
  - `POST /api/admin/cartridges/{cartridge_id}/atlas/reports/{report_id}/resolve` —
    body `{action: accept|decline|merge, note, merge_into}`
- 処理アクション: **採用**(次版へ反映予定に積む=`accepted`)/ **見送り**(理由 note 必須=
  `declined`)/ **重複統合**(`merged`、`merged_into` に統合先)
- 同一対象への未クローズ報告(pending + 未反映 accepted)の件数を表示し、
  閾値到達で改版検討ヒントを出す

## D-3: 採用時の帰属反映

- 骨格の凍結(`POST .../atlas/skeleton/freeze`)時に、採用済み・未反映の報告の報告者を
  `changelog[].credits`(Issue A のスキーマ)へ自動合流する(明示指定の credits と重複なく統合)。
  管理画面の凍結情報に changelog と「修正報告の帰属」を表示する(受け入れ条件3)
- 凍結後の報告の扱い:
  - `accepted` → `applied_version` に新版を刻印して**自動クローズ**(報告者に「版 X に反映」通知)
  - `pending` → **新版へ引き継ぎ**。改版で concept id が統合・分割された場合は
    骨格の `id_migrations`(当該版のエントリ)に従って対象 `node_id` を付け替える(§16-4)
- 報告者本人への通知:
  - `GET /api/atlas/reports/mine?unacked=true` — 処理済みで未読の結果(採用/見送り理由/統合)
  - `POST /api/atlas/reports/{id}/ack` — 本人のみ既読化
  - フロントは atlas-report.js がログイン済みセッションの起動時に未読結果を取得し、
    画面右下の通知カード(確認ボタンで既読化)として表示する。resolve のたびに
    `notified_at` は未読に戻る(採用→版反映で2度目の通知が届くのは意図どおり)

## データ (migration 023: `atlas_correction_reports`)

```
id, kind('map_correction'), cartridge_id, skeleton_version(必須・不変),
node_id | region_id(どちらか必須), level(1..3), node_label(報告時点のラベル),
report_text, reporter_id(NOT NULL=匿名不可),
status(pending|accepted|declined|merged), resolution_note, resolved_by, resolved_at,
merged_into, applied_version(''=未反映), notified_at(NULL=未読), created_at, updated_at
```

正本リファレンス: `backend/db/023_atlas_correction_reports.sql`
(適用は `backend/api/main.py` の `_run_migrations()`)。

## 未決事項の決定

| 事項 | 決定 |
|---|---|
| 改版トリガとなる報告蓄積の閾値 | **同一対象への未クローズ報告 5件**(`core/atlas_reports.py` の `REVISION_TRIGGER_THRESHOLD`)。到達でレビュー画面に改版検討ヒントを表示するのみで、**自動改版はしない**(改版判断は教員。Issue A の年次改版・手動契機と並ぶ材料の一つ) |
| 見送り理由の定型分類 | **自由記述のみ**(必須)。初期は報告の量が少なく分類の妥当な語彙が定まらないため。蓄積後に `theory_review_events` の監査ログから分類を帰納する(Stage 4 の challenge 型設計と合わせて再検討) |

## 非スコープ (issue D のまま)

- challenge の一級市民化・型システム(Stage 4)。ただし `kind` 列で昇格を妨げない
- 報告への議論スレッド、k-匿名集計、自動クラスタリング

## テスト

- `backend/tests/core/test_atlas_reports.py` — 入力検証・キュー整形・credits・
  id 移行・SQL 実行(FakeSession)
- `backend/tests/api/test_atlas_report_api.py` — 受け入れ条件1〜5の API 結合
  (DB は `core.postgres.get_session` の monkeypatch)
- `backend/tests/test_atlas_correction_reports_static.py` — フロント/SQL の静的検証
  (匿名オプション不在・空文字ガード・旧版識別・changelog credits 表示ほか)
