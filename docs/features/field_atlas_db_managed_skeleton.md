# 分野の地図 — 骨格の DB 管理化（設計と決定事項）

> **ステータス: S1〜S3 実装済み（2026-07-05, migration 027）— 正本・凍結**
>
> 追補（2026-09-03 コード照合）: `core/atlas_store.py` の骨格ストア・generate /
> draft PUT（`revision` 楽観ロック）/ freeze / `GET /api/admin/atlas/domains`・
> バインディング API は現存し、本文の記述と一致する。以下 2 点だけ現行と表記が異なる。
> ①migration の適用は `main.py` ではなく **`backend/core/migrations.py` のランナー**
> （毎起動・番号順に冪等再実行。Tier 3-13）。`atlas_store.import_bundled_skeletons()` の
> 起動時取込は `main.py` の lifespan のままで正しい。②同梱骨格の探索先は
> カートリッジ配下に加えて **`backend/atlas_domains/<domain_key>/skeleton.yaml`**
> （カートリッジ一式を持たない骨格専用ドメイン。`knowledge_landscape_design.md`）。
> その後の拡張（ドメイン retire/restore = migration 057 / ベクトル係留 = 074 /
> 辺候補 = 076）はそれぞれ専用設計書が正本。
> 関連: `field_atlas_binding.md` / `field_atlas_skeleton.md` / `field_atlas_overlay_spec.md`
>
> 注記 (2026-08-14): `field_atlas_overlay_spec.md` の原本は消失している。現存するのは
> 2026-08-14 の**再構成版**で、**旧§番号との対応は保証されない**。

## 実装サマリ

- **migration 027** `atlas_skeletons`（`backend/db/027_atlas_skeletons.sql` / main.py 起動時）。
  同梱骨格は起動時に `atlas_store.import_bundled_skeletons()` で一度だけ DB へ取込（冪等）
- **`core/atlas_store.py`**: 骨格 DB ストア。`load_learner_skeleton()`（DB 優先・同梱ファイル
  フォールバック）/ `save_draft()`（楽観ロック `DraftRevisionConflict` → API 409）/
  `insert_frozen()`（版履歴保持・現行版は created_at 降順先頭）/ `list_domains()`
- **読み経路の差替え**（5箇所）: `atlas_view._load_learner_skeleton` /
  `routes/atlas.py` 学習者向け・overlay refresh・報告一覧 / `learning.py` gap1 帰属 /
  `atlas_state._run_background_refresh`
- **教員フロー**: generate（`body.domain` で新分野メタデータ → カートリッジファイル不要）/
  draft PUT（`body.revision` 楽観ロック）/ freeze（DB 保存・(domain,version) 一意・
  凍結後 overlay refresh 予約）/ `GET /api/admin/atlas/domains`
- **S2 バインディング API**: `POST /api/admin/courses/{id}/atlas-binding/propose`
  （決定論的提案・LLM 不使用）/ `PUT /api/admin/courses/{id}/atlas-binding`
  （オーナー教員 or SYSTEM_ADMIN。`theory_review_events` に `entity_type='atlas_binding'` で監査）
- **admin UI**: 骨格エディタの revision/409 対応・「新しい分野を追加」フォーム・
  「学習マップ編集」セクション・コースビルダー登録直後の配置提案カード
- **S3 実施済み**: `modified_gravity` 骨格（LLM 生成 → 凍結 2026.1）、
  DHOST コース (5969a478) へ binding 保存（cartridge_id + topic 14 件）
- **既知の無害な競合**: 凍結直後の refresh 予約と GET の cold refresh が重なると
  `atlas_overlay_cache` の一意制約で片方が rollback する（警告ログのみ・自己回復）

## 0. 決定事項（教員レビュー済み）

- **スコープ**: S1（DB化）+ S2（コース⇄地図バインディング）+ S3（修正重力骨格）をまとめて実施
- **同時編集**: 楽観ロック（`revision` 照合、衝突は 409）で開始。提案型（S4）は将来
- **バインディング UI**: コース作成（コースビルダー承認）時に地図中の配置を**提案**し
  教員が承認。既存コースは管理画面の「学習マップ編集」から
- **修正重力骨格**: LLM 生成フロー（generate エンドポイント拡張: cartridge ファイルの
  無い新分野は domain メタデータをリクエストで受ける）→ 教員レビュー → 凍結
- **コース紐づけ**: `learning_courses.data.cartridge_id` + `topics[].atlas_node_id`。
  設定はバインディング API 経由（SQL 直更新と同効果 + topic binding + 監査記録）
- **domain_key**: 現行 `cartridge_id` と同一の名前空間。ただし骨格ローダは
  **カートリッジファイルが無い domain でも DB 骨格があれば動く**（新分野に
  ファイルデプロイ不要）。解析パイプラインのカートリッジとは当面独立に運用

## 1. このドキュメントの目的

「DHOST 重力理論コースに、別分野（素粒子物理）の地図が表示される」不具合の
恒久対策として、**修正重力理論ドメインの atlas 骨格を用意する**必要がある。
その骨格を **カートリッジ（ファイル）に追加するのではなく DB で管理したい**、
という要望（複数教員が編集するとファイルはマージできない）を受けての設計整理。

## 2. 背景（前段の不具合と現在地）

前段で以下を修正済み（`learning-ux` ブランチ）:

- nginx に `/api/atlas` の proxy が無く SPA フォールバックが index.html を返していた → proxy 追加
- `atlas-data.js` が API 失敗時にフィクスチャへ退避していた → fail-closed 化（`null`＝非表示）
- `atlas_view.py` の `from api import services` が実コンテナで `ModuleNotFoundError` → 修正
- **導出カートリッジの妥当性ゲート**を追加。コースが骨格へ足がかりを持たなければ
  `GET /api/atlas` は 404（地図領域ごと非表示）

結果、**DHOST コースでは地図が「誤り」ではなく「非表示」になった**（正直な縮退）。
このコースに正しい地図を出すには、対応する分野の骨格が必要 = 本ドキュメントの主題。

## 3. 現状アーキテクチャ（どこがファイル / どこが DB か）

分野の地図は3層モデル（S=骨格 / C=状態キャッシュ / P=個人層）。
**S 層（骨格）だけがファイルで、他はすべて DB 化済み。**

| データ | 保管先 | 実体 |
|---|---|---|
| S: 骨格 **凍結版** | **ファイル** | `backend/cartridges/<id>/atlas/skeleton.yaml` |
| S: 骨格 **draft** | **ファイル** | `backend/cartridges/<id>/atlas/skeleton.draft.yaml` |
| C: 状態導出キャッシュ | DB | `atlas_overlay_cache`（migration 024） |
| P: 個人層（いまここ・足跡） | DB | `interest_traces`（migration 020） |
| 修正報告 | DB | `atlas_correction_reports`（migration 023） |
| 導線計測・初回表示フラグ | DB | `atlas_cue_events`（migration 026） |
| カートリッジのドメイン語彙 | ファイル | `ontology.json` / `component_types.json` ほか |

読み書きの経路:

- **読み**: `atlas_view._load_learner_skeleton` → `cartridges.load_cartridge(id).learner_atlas_skeleton`
  → **凍結ファイルをパース**
- **書き（教員フロー, `routes/atlas.py`）**:
  - 生成 `POST /cartridges/{id}/atlas/skeleton/generate` → `_draft_path` に **ファイル書き込み**
  - 編集 `PUT  /cartridges/{id}/atlas/skeleton/draft`    → `_draft_path` に **ファイル書き込み**
  - 凍結 `POST /cartridges/{id}/atlas/skeleton/freeze`   → `_frozen_path` に **ファイル書き込み** + `clear_cache()`

## 4. 現状の問題点（ファイル管理の限界）

### A. 複数教員の同時編集がマージできない（要望の主旨）

draft/凍結が単一 YAML ファイルのため、複数教員が並行編集すると
Git マージ衝突、または後勝ちの上書きになる。編集の帰属・履歴も残らない。

### B. コンテナ再ビルドで教員の編集が消える（より重大な潜在バグ）

`backend/Dockerfile` は `COPY backend/cartridges/ /app/cartridges/` で
**カートリッジをイメージに焼き込む**。api-server にカートリッジ用の
**ボリュームマウントが無い**（現状 `./.gcp:ro` のみ）。

→ 教員が API で生成・編集・**凍結した骨格はコンテナ内ファイルにしか無く、
再ビルド／再デプロイで失われる**。本番運用では致命的。DB 化はこれも同時に解決する。

### C. 新しい分野の追加にデプロイが必要

新ドメイン（例: 修正重力理論）の骨格を足すにはリポジトリにファイルを追加して
再デプロイが要る。教員が自分でドメインを立ち上げられない。

## 5. 提案：骨格（S 層）を DB 管理へ移す

**S 層だけを DB へ移す**のが最小で効果が最大。C/P 層と修正報告・導線計測は
既に DB なので、`atlas` 系で唯一ファイルに残る箇所を DB に寄せる形になり、
アーキテクチャの一貫性も上がる。

前例: `004_schema_evolution.sql` が ontology/predicate を
「提案(`schema_proposals`)→レビュー」で **DB 管理**している。骨格にも
同じ「draft→レビュー→凍結」を DB 上で行うのは既存パターンの踏襲。

### 5.1 移す範囲（最小スコープ）

- **移す**: 骨格の draft と凍結版（S 層）
- **当面ファイルのまま**: カートリッジのドメイン語彙（ontology 等）。
  骨格は語彙より変更頻度が高く、教員が触る対象。語彙の DB 化は別スコープ。

### 5.2 新テーブル案 `atlas_skeletons`（migration 027 想定）

```
atlas_skeletons
  id             UUID PK
  domain_key     TEXT NOT NULL         -- 現行 cartridge_id 相当。分野の識別子
  status         TEXT NOT NULL         -- 'draft' | 'frozen'
  version        TEXT                  -- 凍結時に付与（draft は NULL）
  content        JSONB NOT NULL        -- 骨格本体（regions/concepts/edges/bindings）
  revision       INTEGER NOT NULL      -- 楽観ロック用。編集ごとに +1
  generated_by   TEXT                  -- 来歴（model:... など）
  created_by     UUID                  -- 編集/生成した教員
  reviewed_by    UUID[]                -- 凍結を承認した教員
  changelog      JSONB                 -- 版ノート・credits（既存構造を踏襲）
  created_at     TIMESTAMPTZ
  updated_at     TIMESTAMPTZ
  UNIQUE(domain_key, status) WHERE status='draft'   -- draft は分野に1つ
  -- 凍結版は複数版を履歴として保持（version で区別）
```

- 既存の `atlas_overlay_cache` は `(cartridge_id, skeleton_version)` で引くため、
  `domain_key`/`version` をそのまま流用でき整合する。
- `content` の JSONB は既存 `atlas.skeleton_to_dict()` / `parse_skeleton()` を
  そのまま使える（YAML ではなく JSON で持つだけ。パーサは共通）。

### 5.3 同時編集の扱い（要決定）

| 方式 | 内容 | 長所 / 短所 |
|---|---|---|
| **(a) 楽観ロック**（推奨・最小） | draft は分野に1つ。保存時に `revision` を照合し、
ズレていれば 409 でリロードさせる | 実装小・後勝ち上書きを防ぐ / 自動マージはしない |
| (b) 提案・レビュー | 教員ごとに変更提案を作り承認者が統合（`schema_proposals` 型） | 真の並行編集に強い / 実装大 |
| (c) セクション別ロック | 領域・概念単位で編集ロック | 中間 / UI 複雑 |

まず **(a)** で「消えない・衝突を検知できる」を満たし、必要なら (b) へ拡張する段階案を推奨。

### 5.4 読み取り経路の変更

- `cartridges.load_cartridge().learner_atlas_skeleton`（ファイル読み）を、
  **DB 優先・ファイルはフォールバック**に変更する薄いローダーへ差し替える。
- 影響は `atlas_view._load_learner_skeleton` と `routes/atlas.py` の
  `_draft_path`/`_frozen_path`/`_load_optional`。書き込み3エンドポイントも
  DB 読み書きへ。**A 層（生成パイプライン）は不変。**

### 5.5 コース⇄骨格の紐づけ（前段の縮退問題との統合）

現状はコース→カートリッジを `document_analysis_runs.cartridge_id` から**導出**し、
外れると既定カートリッジへ縮退していた（今回の不具合の根本）。DB 化に合わせて
**コースに骨格（domain_key）を明示参照させる**と、導出依存を断てる:

- `learning_courses.data.cartridge_id`（または `atlas_domain_key`）を教員が設定
- 前段で入れた妥当性ゲートは「明示参照があればゲート免除」で既に対応済み

これにより「解析は既定カートリッジで走る → 地図が別分野」の構造的問題が解消する。

### 5.6 既存カートリッジ（particle_physics）の移行

- 起動時マイグレーションで `atlas/skeleton.yaml` を読み `atlas_skeletons` に
  凍結版として1回だけ取り込む（冪等）。以後 DB が正本、ファイルは種として残置。

## 6. スコープの選択肢（段階）

| 段階 | 内容 | 解決する問題 |
|---|---|---|
| **S1（最小・推奨）** | `atlas_skeletons` 追加 + 読み書きを DB 化 + 楽観ロック + particle_physics 取込 | A（衝突検知）/ B（揮発性）/ C（デプロイ不要） |
| S2 | コースに `atlas_domain_key` を明示参照させる UI/API | 前段の導出縮退を根絶 |
| S3 | 修正重力理論ドメインの骨格 draft を作成→レビュー→凍結（新フローで実施） | DHOST コースに地図が出る |
| S4（任意） | 提案・レビュー型の並行編集（`schema_proposals` 型） | 真の同時編集 |
| S5（任意） | カートリッジのドメイン語彙も DB 化 | 語彙のファイル依存も解消 |

## 7. 決めてほしいこと

1. **スコープ**: S1〜S3 をまとめてやるか、S1 だけ先に入れて地図表示は後段か。
2. **同時編集**: 5.3 の (a) 楽観ロックで始めてよいか、(b) 提案型が要るか。
3. **骨格 draft の作り方**: 修正重力理論ドメインを
   - 手書き draft（私がコース構成＋論文から素案 → あなたがレビュー）
   - LLM 生成フロー（既存 generate エンドポイント。※ LLM 認証設定の有無は未確認。
     確認が要る場合は `.env` は読まず、あなたに直接伺うか動作確認で判断する）
4. **コース紐づけ手段**: SQL 直更新（最小）か、設定用 admin API/UI の新設か。
5. **ドメインと cartridge_id の関係**: 骨格の `domain_key` を現行 `cartridge_id` と
   同一に保つか、解析用カートリッジと地図用ドメインを分離するか。

## 付録：関連ファイル

- 読み: `backend/api/routes/atlas_view.py`（`_load_learner_skeleton`）
- 書き（教員フロー）: `backend/api/routes/atlas.py`（generate/draft/freeze, `_draft_path`/`_frozen_path`）
- 骨格スキーマ・パーサ: `backend/core/atlas.py`（`parse_skeleton` / `skeleton_to_dict` / `freeze_skeleton`）
- 骨格生成: `backend/core/atlas_generator.py`
- カートリッジ読み込み: `backend/core/cartridges.py`（`load_cartridge` / `learner_atlas_skeleton`）
- 既存 DB 前例: `backend/db/004_schema_evolution.sql`（提案→レビュー型の語彙管理）
- 焼き込み: `backend/Dockerfile`（`COPY backend/cartridges/`。ボリューム未マウント）
```
