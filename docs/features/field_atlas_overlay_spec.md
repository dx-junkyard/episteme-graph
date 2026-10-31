# 分野の地図（Field Atlas）— 仕様の正本（再構成版）

> **本書は 2026-08-14 の再構成版です。**
> 原本はリポジトリ未コミットのまま消失しました（`docs/architecture/doc_review_findings_2026-08-13.md` §1-1。
> `find` / `git log --all` のいずれでも一度もコミットされた形跡がありません）。本書は
> **実装コードと実装済み設計書群から逆補完したもので、旧§番号との対応は保証しません**。
> 既存文書が引用する「§1.2」「§6」「§9」等の番号は本書の章番号とは一致しません
> （対応の目安は §12 を参照）。**実装が一次情報です。** 本書とコードが食い違う場合は
> コードが正しく、本書の側を直してください。

- 対象: 分野の地図（Field Atlas）の全画面オーバーレイ・常設ミニマップ・骨格（skeleton）・
  状態導出・修正報告・コース⇄地図バインディング
- 状態: 実装済み（正本・凍結）— 全機能実装済み（issue A〜F + S1〜S3 + ドメインライフサイクル）
- 関連設計書: `field_atlas_skeleton.md`（骨格運用）/ `field_atlas_detail_panel.md`（詳細パネル）/
  `field_atlas_correction_reports.md`（修正報告）/ `field_atlas_binding.md`（個人層 binding）/
  `field_atlas_db_managed_skeleton.md`（骨格の DB 管理化）/
  `field_atlas_skeleton_editor_upgrade.md`（骨格エディタ）/ `atlas_binding_lifecycle_design.md`
  （該当なし UX とドメインライフサイクル）

---

## 0. この機能は何か

学習中の箇所が**分野全体のどこに該当するか**を示す、全画面オーバーレイ + 左サイドバー下部の
常設ミニマップ。分野の地形（骨格）はモデル知識からバッチ生成して教員が凍結し、その上に
コーパス由来の「灯り・状態」と本人の「いまここ・足跡」を重ねて描く。

**別機能との区別:**

| 名前 | 実体 | プレフィックス |
|---|---|---|
| 分野の地図（Field Atlas） | 本書 | `atlas-` / `core/atlas*.py` |
| 前提の地図（Assumption Atlas, D層） | 負荷度×検証度の散布図 | `doubt-` / `assumption-` |
| 知識ランドスケープ | 論文を骨格アンカーへ配置する層 | `landscape-` |
| 個人知識ネットワーク（わたしの地図） | 本人の痕跡から導出する個人グラフ | `personal-map` |

出典: `frontend/public/js/atlas-overlay.js` 冒頭コメント / `CLAUDE.md`「分野の地図（Field Atlas）」節 /
`docs/features/element_deliberation_workspace_design.md`（プレフィックス衝突回避の記述）

---

## 1. 設計原則（不変条項）

1. **宣言しない。** 地図は「あなたはここまで来た」と告げない。状態は色と形で示し、
   評価・推薦・催促の文言を付けない。詳細パネルの検証行・承認行は**台帳由来の事実のみ**を
   テンプレート合成する（`core/atlas_state.py` の `EVALUATIVE_VOCABULARY` による静的チェックを
   テストで強制。「重要」「推奨」「べき」「必須」等を禁止語彙として持つ）。
2. **煽らない。** 導線カードは提示に留め**自動で開かない**（例外は初回ログインの一度きりのみ）。
   バッジ・ランキング・ゲーミフィケーション表現を作らない。
3. **出所の正直さ。** モデル知識由来（霧・骨格）とコーパス由来（灯り）を視覚的に混ぜない。
   骨格の来歴（`generated_by`）と教員レビュー（`reviewed_by`）を記録し、応答は
   `provenance: "AI生成・教員レビュー済"` を明示する。
4. **踏破率を数値にしない。** %・スコア・件数・バッジを学習者に一切出さない。霧領域の
   匿名ドットも個数を固定にして「霧の中身の規模」を示唆しない。内部計測
   （`atlas_cue_events`）は Stage 2 のゲート判断の材料であり、**数値をユーザーに見せる
   API・UI を作らない**。
5. **リアルタイム LLM 生成をしない。** 骨格はバッチ生成 → 教員レビュー → 凍結の静的アセット。
   実行時は「キャッシュ読み出し + 個人層の合成」のみ。学習パス提案カードも決定論的ビルダー
   （`core/atlas_path.py`、LLM 不使用）。
6. **fail-closed。** 骨格が無い／取得に失敗したときはフィクスチャへ退避せず、地図領域ごと
   非表示にする（`atlas-data.js` は `null` を返す）。導出カートリッジが妥当性ゲートを
   通らなければ `GET /api/atlas` は 404。
7. **状態判定はサーバ側に一箇所。** 導出規則を `core/atlas_state.py` に隔離し、
   フロントへ複製しない。クライアントは描画のみ。
8. **draft は学習者に出さない。** `AtlasSkeleton.is_learner_visible`（frozen かつ version あり
   かつ reviewed_by 非空）で型として担保する。

出典: `backend/core/atlas.py` / `backend/core/atlas_state.py`（`EVALUATIVE_VOCABULARY`・
`find_evaluative_language`）/ `backend/core/atlas_path.py` / `frontend/public/js/atlas-overlay.js` /
`frontend/public/js/atlas-cues.js` / `frontend/public/js/atlas-data.js` /
`backend/db/024_atlas_overlay_cache.sql` / `backend/db/026_atlas_cue_events.sql`

---

## 2. 3層モデル（S / C / P）

地図は3つの層の重ね合わせで構成される。**層ごとに出所と更新契機が違う。**

| 層 | 内容 | 保管 | 更新契機 |
|---|---|---|---|
| **S: 骨格（skeleton）** | 分野の地形（領域・代表概念・エッジ）。AI 生成 → 教員レビュー → 凍結 | `atlas_skeletons`（migration 027）が正本。カートリッジ同梱 `atlas/skeleton.yaml` と `backend/atlas_domains/<key>/skeleton.yaml` は起動時に一度だけ取り込むシード兼フォールバック | 教員の generate / draft 編集 / freeze |
| **C: コーパス層（状態）** | 灯り・霧・ノード状態・検証行・承認行。既存テーブルからの近似導出 | `atlas_overlay_cache`（migration 024） | イベント駆動の差分バッチ（`refresh_overlay_cache`）。cold start のみ同期、陳腐化は非同期 |
| **P: 個人層** | 「いまここ」「足跡」「隣接の光」 | `interest_traces.payload.atlas`（migration 020） | 学習の往復ごとに best-effort 記録（`_atlas_topic_attribution`）+ 地図内アクション |

- **S 層の読みは必ず `atlas_store.load_learner_skeleton()` を使う**（`cartridge.learner_atlas_skeleton`
  の直読み禁止）。DB 凍結版が正本で、無ければ同梱ファイルへフォールバックする。
- **C 層は骨格に焼き込まない。** 骨格が持てる状態情報は `seed_status`（初期ヒント）までで、
  `reviewed: true` のもののみ表示可。検証状態・承認・灯りは実行時に導出する
  （骨格に焼き込むと「地図が古い認識を権威化する」事故になる）。
- **P 層は個人にのみ可視。** 「いまここ」は `interest_traces` の当該ユーザー行のみから合成する。

出典: `backend/core/atlas_store.py` / `backend/core/atlas_state.py` /
`backend/api/routes/atlas_view.py`（`_personal_layer` / `_ensure_overlay_rows`）/
`backend/db/024_atlas_overlay_cache.sql` / `backend/db/027_atlas_skeletons.sql` /
`docs/features/field_atlas_db_managed_skeleton.md` §3

---

## 3. 骨格スキーマと上限値

### 3.1 スキーマ

正本は `backend/core/atlas.py` の dataclass 群。YAML / JSONB のトップレベルは
`atlas_skeleton` キーで包んでもよい（`parse_skeleton` が両対応）。

```yaml
atlas_skeleton:
  version: "2026.1"          # 凍結時に付与（draft は空）
  cartridge: particle_physics # = domain_key
  status: frozen              # draft | frozen
  generated_by: "model:..."   # 来歴。空は validation error
  reviewed_by: ["<user>"]     # 凍結には1名以上必須
  changelog:
    - {version: "2026.1", note: "...", credits: ["..."]}
  regions:
    - id: region_key          # ^[a-z][a-z0-9_]*$
      label: "領域名"
      layout: {x: 0.0, y: 0.0, w: 0.3, h: 0.4}   # 正規化キャンバス絶対座標
      concepts:
        - id: concept_key
          label: "概念名"
          layout: {x: 0.5, y: 0.5}                # ★領域ボックス内の相対座標
          seed_status: {value: verified, reviewed: true}
  edges:
    - {from: a, to: b, kind: adjacent}            # adjacent | depends | related
  concept_bindings:
    - {skeleton: concept_key, graph_concept_id: "...", reviewed: true}
  id_migrations:
    - {from: old_concept, to: new_concept, version: "2027.1"}
```

**概念座標の規約（重要）:** `concept.layout.{x,y}` は**所属領域ボックス内の相対座標**であり、
キャンバス絶対座標ではない。描画側（`atlas_view._concept_abs` / `atlas-fixture.js` /
`atlas-draft-preview.js` の `conceptAbs()`）はいずれもこの相対解釈で一致している。
一方、コーパス概念（`atlas_placement.layout_in_region` 由来）は既に絶対座標なので変換しない。

### 3.2 上限値（実装値）

| 項目 | 値 | 定義場所 |
|---|---|---|
| 領域数（L1） | **12** | `core/atlas.py` `MAX_REGIONS` |
| 1領域あたり代表概念数 | **6** | `core/atlas.py` `MAX_CONCEPTS_PER_REGION` |
| L2 ノード数 | **20** | `routes/atlas_view.py` `_MAX_L2_NODES` |
| L3 ステップ数 | **12** | `frontend/.../atlas-overlay.js` `LIMITS.l3Nodes` |
| 学習パスカードのステップ | **6** | `core/atlas_path.py` `MAX_STEPS` |

フロントの `LIMITS = { l1Regions: 12, l1ConceptsPerRegion: 6, l2Nodes: 20, l3Nodes: 12 }`
（`atlas-overlay.js`）は**サーバ側の上限と一致させること**。超過分は描画時に黙って切り捨てられる
（`console.warn` は出るが画面には出ない）ため、ズレると地図が静かに欠ける。

`MAX_REGIONS` は 2026-08 の知識ランドスケープ（`knowledge_landscape_design.md` §6.2）で
**7 → 12** に引き上げた（宇宙物理の基準地図 v0.1 が 10 領域を持つため）。
`MAX_CONCEPTS_PER_REGION` は据え置き（骨格側を 6 個以内で設計する）。

### 3.3 バリデーション（`validate_skeleton`）

**error（凍結を拒否）:** `cartridge` 未設定 / 不正な `status` / `generated_by` 欠落 /
draft 以外で `reviewed_by` が空 / 凍結版に `version` なし / changelog に当該 version の
エントリなし / 上限超過 / id の形式違反・重複（領域 id と概念 id は同一名前空間で衝突判定）/
label 欠落 / 座標が `[0,1]` を外れる・キャンバスをはみ出す / 不正な `seed_status.value` /
不正な `edge.kind` / エッジが未知 id を参照 / `concept_bindings` が未知概念を参照・
`graph_concept_id` 欠落・**凍結版に未レビュー binding** / `id_migrations` の欠損。

**warning:** 領域ボックスの重なり / 凍結版で `seed_status` が未レビュー（表示されない旨）。

`ValidationIssue` は `str` を継承しつつ `region_id` / `concept_id` / `edge` を機械可読で持ち、
骨格エディタのプレビュー上でのインラインハイライトに使う。

### 3.4 concept id の永続性

1. **id は版を跨いで不変。** ラベル改名は id を変えない。
2. **id の再利用禁止。** 削除した id を別概念へ転用しない。
3. **統合・分割は `id_migrations` に残す**（分割は主たる後継 1 件に張り、残りは新規 id）。
4. 領域 id も同じ規則。

足跡（`interest_traces`）と修正報告の参照側が `id_migrations` を辿って新 id へ解決する。

出典: `backend/core/atlas.py`（dataclass / `parse_skeleton` / `skeleton_to_dict` /
`dump_skeleton` / `validate_skeleton`）/ `backend/api/routes/atlas_view.py`（`_concept_abs` /
`_MAX_L2_NODES`）/ `frontend/public/js/atlas-overlay.js`（`LIMITS`）/ `backend/core/atlas_path.py`
（`MAX_STEPS`）/ `docs/features/field_atlas_skeleton.md`（id 永続性ポリシー）

---

## 4. 状態導出（C 層）

### 4.1 語彙

| 状態 | 意味 | 単位 |
|---|---|---|
| `fog` | コーパス被覆ゼロ | 領域 |
| `lit` | 灯りあり | 領域 |
| `gap` | 行間 — inferred かつ原文対応なし | ノード |
| `assumed` | 暗黙の前提 | ノード |
| `contested` | 解釈が分かれる | ノード |
| `verified` | ソースバッキング + evidence | ノード |
| `unknown` | 台帳に記帳なし（seed も無い） | ノード |

状態の出所は `derived`（コーパス由来）と `seed`（骨格の初期ヒント）を区別する。
表示専用の個人状態として `now`（いまここ）/ `unvisited`（未訪問）があり、これは
**認識的状態と直交**する（表示はグレーに減衰させるが、検証行には台帳状態をそのまま残す）。

### 4.2 導出規則（優先順位・上から最初の該当を採用）

```
1. fog       — 領域単位（コーパス被覆 = 0）
2. gap       — このステップ自体が inferred かつ evidence なし
3. assumed   — 直接検証なし かつ（確定済み前提候補 or 行間の蓄積 ≥ 3）
4. contested — 共有された教員独自解釈 ≥ 2 or 承認の割れ
5. verified  — evidence_count > 0
6. seed      — コーパス由来の状態が無い概念にのみ、reviewed な seed_status を弱い初期表示
7. unknown
```

- `assumed` と `contested` の併発は**`assumed` 優先**（点線＝暗黙の前提が主役のため。
  評価順で保証する）。
- 行間の蓄積が `ASSUMED_INFERRED_THRESHOLD = 3` 件に達したら `assumed` 候補へ昇格させる。
- コーパス概念の骨格領域への割当はコサイン距離 `DISTANCE_THRESHOLD = 0.5` を超えると
  **未配置**にする（誤配置より非表示。ただし行は落とさない）。割当は乱数を使わず
  `(skeleton_version, region_id, concept_key)` の安定ハッシュから導出するので、
  同一コーパス・同一骨格版なら常に同一配置になる。

### 4.3 霧のドット

霧領域には匿名ドットを**固定 3 個**描く（`atlas_placement.fog_dots(count=3)`）。概念ラベルは
持たせない。当初案は概念数 k の対数スケール
`clamp(3 + floor(log10(max(k,1))), 3, 6)` だったが、「宣言しない」原則に沿って
**霧の中身の規模を示唆しない**固定値へ簡素化した。ドット座標も安定ハッシュで決定論的。

### 4.4 検証行・承認行・アクション

- **検証行** — 状態別に台帳由来の事実のみを合成する（裏付け原文数・式/節の参照・スコープ・
  「記帳なし」）。霧・行間には定型文（`FOG_VERIFY_LINE` / `GAP_VERIFY_LINE`）。
- **承認行** — C 層由来の事実のみ（承認者数・専門分野数・解釈の並存・記録なし）。
- **アクション制御** — `actions_for(status)` が霧・行間で `learn` / `evid` を **false** にする
  （「ここから学ぶ」「根拠を見る」を出さない）。`気になる ↗` と `修正を報告` は常時表示。

### 4.5 キャッシュの更新

`atlas_overlay_cache` は `(cartridge_id, skeleton_version)` で引く。
`compute_corpus_signature` でコーパスの変化を検知し、`mark_overlay_dirty` /
`is_overlay_stale` で陳腐化を判定する。`GET /api/atlas` は行が無いときのみ同期リフレッシュし、
陳腐化しているだけなら `schedule_overlay_refresh`（既定 `ATLAS_REFRESH_DELAY_SECONDS=60` 秒後の
バックグラウンド更新）に回す。

既知の無害な競合: 凍結直後の refresh 予約と GET の cold refresh が重なると
`atlas_overlay_cache` の一意制約で片方が rollback する（警告ログのみ・自己回復）。

出典: `backend/core/atlas_state.py`（`derive_node_status` / `derive_region_status` /
`verify_line_for` / `endorse_line_for` / `actions_for` / `PILL_LABELS` /
`ASSUMED_INFERRED_THRESHOLD` / `REFRESH_DELAY_SECONDS`）/ `backend/core/atlas_placement.py`
（`DISTANCE_THRESHOLD` / `fog_dots`）/ `docs/features/field_atlas_skeleton.md`（§16-3 の決定記録）

---

## 5. オーバーレイ UI と詳細パネル

### 5.1 3レベル

| レベル | 名前 | viewBox | 中身 |
|---|---|---|---|
| L1 | 分野レベル | 680×370 | 領域ボックス（灯り/霧）・代表概念ノード・足跡・引き出し線（leaders） |
| L2 | コースレベル（概念マップ） | 680×330 | 骨格概念 + 配置済みコーパス概念・概念間エッジ |
| L3 | 導出レベル | 680×400 | 理論操作グラフの導出チェーン |

L1 ⇄ L2 ⇄ L3 は ← / → キーでも移動できる。初期選択は L1/L2 が「いまここ」（focus 指定が最優先）、
L3 は行間ステップ（無ければチェーン先頭）。

### 5.2 視覚言語

- 状態は**色 + 形状の二重符号化**（破線 / 二重円 / ハッチ）で色覚に依存させない。
- `verified` = 塗り + 実線、`contested` = 塗り + 実線（暖色）、`assumed` / `gap` = 塗りなし + 破線、
  `now` = 二重円、`fog` = ハッチ + 匿名ドット、隣接の光（`glow`）はリング。
- 霧・隣接の光に理由テキストや推薦文言を付けない。

### 5.3 詳細パネルとチャット遷移

ノード選択で `atlas:nodeselect`（detail = `{node_id, level, skeleton_version}`）を発火し、
`atlas-panel.js` が `#atlas-panel-body` に**検証行・承認行・アクション行**を差し込む。

アクション（↗）の共通機構: `AtlasOverlay.close()` → `window.sendPrompt(text, {atlas_context})`。
構造化ペイロード `{node_id, level, skeleton_version, action, node_label, ...}` を必ず添付する
（自由文のみに依存しない）。

| アクション | サーバ側の扱い |
|---|---|
| `mind`（気になる ↗） | 既存 tension 記録経路（`interest_traces` `kind='tension'`）に帰属つきで `status='open'` として記録。**本人が押した宣言**なので LLM 候補の `candidate` とは区別する。応答は決定論的な記録確認文で LLM を呼ばない |
| `learn`（ここから学ぶ ↗） | 学習パス提案カード（§5.4） |
| `evid`（根拠を見る ↗）ほか | 通常の RAG フロー（意図分類・前提ゲートはバイパス）。関心痕跡に `atlas` 帰属を焼き込む |
| `report`（修正を報告） | `atlas:reportrequest` を発火するのみ（§7） |

対話履歴は継続する（新規セッションを切らない）。再オープン時は直前の選択・レベルを復元するが、
保持は**セッション内メモリのみ**（骨格が差し替わったら破棄。改版時の id 永続性問題を持ち込まない）。

### 5.4 学習パス提案カード

`core/atlas_path.build_learning_path_card()` が**決定論的に**生成する（LLM 不使用）。
入力は対象ノード + クライアント添付の地図投影（レベル別の依存順ノード列・並置候補）+
`interest_traces`（既習の痕跡）+ コーストピックの `prerequisites` + 台帳状態。

- 各ステップに出所（`教材` / `AI一般知識`）と状態ラベルを明示
- 行間（`gap`）は「先生に聞くポイント」として質問テンプレートを添付
- 暗黙の前提（`assumed`）は台帳の事実（`記帳された直接検証なし`）のみ表示（評価しない）
- 上限 6 ステップ。省いた分は `notes` に明示（silent cap 禁止）
- 三択 `[この糸で進む] [編集する] [今はやめる]` + 「自分で繋ぐ」を
  `POST /api/learning/courses/{course_id}/atlas/path-decision` で記録
  （proceed/edit → `resolved`、**dismiss → `dismissed`（却下も記録・削除しない）**、
  connect → `articulated`）

出典: `frontend/public/js/atlas-overlay.js`（`LIMITS` / `C` / `PILL_STYLES` / `RENDERERS`）/
`frontend/public/js/atlas-panel.js`（`ACTIONS`）/ `backend/api/routes/atlas_view.py`
（`_VIEWBOX` / `levels` / `initial_selection`）/ `backend/core/atlas_path.py` /
`docs/features/field_atlas_detail_panel.md`

---

## 6. 常設ミニマップ（F-1）と見晴らしの導線（F-2）

### 6.1 ミニマップ

左サイドバー（学習パスパネル）下部の切手大表示。表示するのは**次の3つだけ**:

1. 「いまここ」の位置（青の二重円の簡略形）
2. 訪問済み・状態ノードの簡略ドット（色は視覚言語準拠、**ラベルなし**）
3. 霧領域のハッチ簡略表現

数値（%・件数）・ラベル・凡例は一切描かない。縮約アルゴリズムは **L1 をそのまま縮小**
（領域ボックス・霧ハッチはそのまま、代表概念ノードは無ラベルのドットへ簡略化）。
専用エンドポイントは作らず `GET /api/atlas`（L1）の応答から描く。

**更新はトピック遷移とオーバーレイ閉時のみ**（`atlas:closed` の購読。常時ポーリングしない）。
骨格なしカートリッジ（`AtlasData.load()` が `null`）では領域ごと非表示。

### 6.2 導線 4 箇所

| # | 契機 | 実装 |
|---|---|---|
| ① | トピック完了直後 | `app.js` が `AtlasCues.showCue("topic_complete", …)` |
| ② | 章末（章の最後のトピックのみ） | 同 `"chapter_end"`（章境界を跨ぐ完了 + レクチャー完了バナー） |
| ③ | 寄り道復帰 | 同 `"detour_return"`（戻った位置を `atlas-pulse` でハイライト） |
| ④ | 初回ログイン | `AtlasCues.maybeAutoOpenFirstLogin()` の**一度きり自動表示** |

①〜③は**カード提示に留め自動で開かない**（開くのは常に本人）。カードは常に最新 1 枚。

### 6.3 抑制ルール

- **直近 10 分以内にオーバーレイを開いていたら、①②のカードを出さない**
  （`SUPPRESS_MS = 10 * 60 * 1000`）。③寄り道復帰は明示アクション直後なので抑制しない。
- 骨格なしカートリッジでは導線を出さない（`skeletonMissing()`）。
- 初回自動表示のフラグは `atlas_cue_events` の `(user_id, 'first_login', 'opened')` 行の存在で
  永続化する（再ログイン・別端末でも一度きり）。**フラグ確認に失敗したら自動表示しない**
  （押し付けない側に倒す）。
- オプトアウトは設定項目を作らず、オーバーレイ内の注記で「初回のみ」であることを明示する。

### 6.4 内部計測

`POST /api/learning/atlas/cues/events` に `shown` / `opened` / `dwell` / `learn_reached` を
best-effort で記録する（失敗しても学習フローに影響させない）。
**数値をユーザーに見せる API・UI は作らない。**

出典: `frontend/public/js/atlas-minimap.js` / `frontend/public/js/atlas-cues.js`
（`SUPPRESS_MS` / `showCue` / `maybeAutoOpenFirstLogin`）/ `frontend/public/js/app.js`
（`showCue` 呼び出し3箇所 + `maybeAutoOpenFirstLogin`）/ `backend/db/026_atlas_cue_events.sql`

---

## 7. 修正報告フロー（D）と改版契機

### 7.1 報告

地図上からワンタップで「この配置・状態のどこが実際と違うか」を送る。疑義（challenge）の
軽量版で、将来 challenge（型: 地図修正）へ昇格できるデータ構造（`kind` 列）を持つ。

- **匿名オプションは存在しない**（`reporter_id` は NOT NULL、API も要認証）。フォーム近傍に
  「あなたの名前とともに記録されます」を明示する。
- 自動添付メタ: `node_id | region_id` / `level` / `skeleton_version`。どの版への指摘かを固定する。
- 空文字送信はクライアント・サーバ双方でガード（サーバは 422）。
- 送信成功後も**オーバーレイは閉じない**（↗ チャット遷移との違い）。

### 7.2 レビュー

新規のキュー機構は作らず、管理画面「分野の地図」タブ内の「修正報告のレビュー」セクションで
処理する。レコードの正本は `atlas_correction_reports`（migration 023）1 テーブルのみで、
状態遷移の監査は `theory_review_events`（`entity_type='atlas_report'`）に記録する。

- 処理アクション: **採用**（`accepted`）/ **見送り**（理由 note 必須・`declined`）/
  **重複統合**（`merged`、`merged_into` に統合先）
- 旧版への報告は「旧版（現行 X）」ラベルで識別する
- 採用と「次版で対応済み」は分ける（migration 046 の `incorporated_at` / `incorporated_by` /
  `incorporation_note`）。公開時に反映済み扱いへ進めるのは `incorporated_at` が記録された報告だけ

### 7.3 改版契機と閾値（実装値）

**`REVISION_TRIGGER_THRESHOLD = 5`**（`backend/core/atlas_reports.py`）。
**同一対象（node/region）への未クローズ報告（pending + 未反映 accepted）が 5 件**に達したら、
レビュー画面に改版検討ヒント（`revision_hint_targets`）を表示する。
**自動改版はしない**（表示のみ。改版判断は教員）。

次版を作る契機は次の3つで、上の閾値はその材料の一つ:

1. **年次改版** — 年 1 回、カートリッジ改版に合わせて見直す
2. **修正報告の蓄積が閾値超え** — 上記 5 件
3. **分野の大きな動き** — 手動判断

改版手順: 現行凍結版 → 次版 draft（`POST .../atlas/skeleton/draft/from-frozen` の決定論複製、
または `PUT .../skeleton/draft` に凍結版の内容を渡す）→ 修正 → `POST .../skeleton/freeze` で
次版（例 `2027.1`）を付与。採用した修正報告の報告者は freeze 時の `changelog[].credits` へ
自動合流する。

### 7.4 凍結後の報告の扱い

- `accepted` → `applied_version` に新版を刻印して**自動クローズ**（報告者へ「版 X に反映」通知）
- `pending` → **新版へ引き継ぎ**。concept id が統合・分割された場合は `id_migrations` に従って
  対象 id を付け替える
- 報告者本人への通知は `GET /api/atlas/reports/mine?unacked=true` /
  `POST /api/atlas/reports/{id}/ack`（本人のみ既読化）。resolve のたびに未読へ戻る
  （採用 → 版反映で 2 度目の通知が届くのは意図どおり）

出典: `backend/core/atlas_reports.py`（`REVISION_TRIGGER_THRESHOLD = 5` / `RESOLVE_ACTIONS` /
`REPORT_KIND`）/ `backend/db/023_atlas_correction_reports.sql` /
`backend/db/046_atlas_report_incorporation.sql` / `frontend/public/js/atlas-report.js` /
`docs/features/field_atlas_correction_reports.md`

---

## 8. コース⇄地図バインディングとドメインライフサイクル

### 8.1 topic → 骨格概念の解決（決定論的・LLM 非依存）

1. **明示 binding**: `course_data.topics[].atlas_node_id`（または `atlas_concept_id`）が
   骨格の既知 id なら採用（authoring-time で最強）
2. **コーパス binding**: topic の `material_chunk_ids` → `theory_components` → 骨格の
   `concept_bindings` 経由。hot path（チャット）では使わず on-demand の経路のみ
3. **ラベル一致（fallback）**: 正規化した `topic.title` と概念 label/id の一致 → 包含。
   最後の縮退先は region ラベル

### 8.2 コース ⇄ カートリッジ

`atlas_state.resolve_course_cartridge` が ①`course_data.cartridge_id` の明示指定
②`sources[].material_id` → `document_analysis_runs.cartridge_id`（completed の最頻）
③既定カートリッジ、の順で導出する。

**導出カートリッジの妥当性ゲート:** ②③の**導出**で決まった場合、そのコースが骨格へ
少なくとも 1 つの足がかり（topic → 骨格概念対応）を持つことを
`course_has_skeleton_anchor` で検証し、どのトピックも対応しなければ `GET /api/atlas` は
**404（骨格なし扱い）**を返す。解析パイプラインは既定カートリッジで走るため、ゲートが
ないと別分野のコースに無関係な地図が出る。**明示 `cartridge_id` はゲートを免除する**
（authoring-time の意思を信頼する）。

### 8.3 明示バインディング（S2）

`POST /api/admin/courses/{id}/atlas-binding/propose` が全ドメイン骨格への topic 対応
カバレッジを**決定論的に**提案し（LLM 不使用）、教員承認で
`PUT /api/admin/courses/{id}/atlas-binding` が `learning_courses.data.cartridge_id` +
`topics[].atlas_node_id` を保存する（監査: `theory_review_events`
`entity_type='atlas_binding'`）。コースビルダーの登録直後と管理画面「学習マップ編集」から操作する。

### 8.4 該当なし UX とドメインライフサイクル

**一致ゼロは正常な状態であり発見**（AB1）。詳細は `atlas_binding_lifecycle_design.md` が正本。
要点のみ:

- propose は retired ドメインを除外し `domains_checked` / `retired_skipped` /
  `atlas_binding_pending` / `current_retired` を返す
- 0 一致時のフロント既定は「バインドしない」（`proposals[0]` への fallback は廃止）
- 出口 3 つ: 手動対応 / 後回し（`PUT .../atlas-binding/pending` = G層 To-Do）/
  コース起点の新分野作成
- ドメインは `atlas_domain_meta.lifecycle`（active / retired、migration 057）で
  `POST .../atlas/retire|restore`。retired は propose 候補から除外・generate / draft保存 /
  freeze は 409（読み取り専用）・**学習者表示は不変**。**ドメイン削除 API は無い**
- 凍結前に `GET .../atlas/freeze-impact` が draft と現行凍結版の差分 + バインド中コースの
  topic 影響を返し、フロントが事実文 confirm で提示する
- freeze / retire は `cross_layer_notify` で「バインド中コース所有者 + 骨格編集履歴のある教員」
  （actor 除外）へ best-effort 通知（学習者・draft レビューには通知しない）
- retire / restore と書き込み系は domain 単位 advisory lock（`atlas_store.lock_domain_for_write`）
  で直列化する

### 8.5 骨格エディタの AI アシスト

`POST .../atlas/skeleton/assist/interpret` / `.../assist/propose` が JSON Patch（RFC 6902）で
部分修正を提案する。**適用は教員の明示操作**（draft の `PUT` を通る）。コスト上限は
`ATLAS_ASSIST_MAX_CALLS_PER_DAY`（既定 60、interpret + propose の合算）。会話履歴は
ブラウザ内メモリのみ（draft 保存・再生成・凍結で無効化）。

出典: `backend/core/atlas.py`（`match_topic_to_concept`）/ `backend/core/atlas_state.py`
（`resolve_course_cartridge` / `course_has_skeleton_anchor` / `resolve_topic_concept_via_corpus`）/
`backend/core/atlas_store.py`（`retire_domain` / `restore_domain` / `lock_domain_for_write`）/
`backend/core/atlas_lifecycle.py`（`notify_atlas_event` / `compute_freeze_impact`）/
`backend/core/config.py`（`atlas_assist_max_calls_per_day`）/
`docs/features/atlas_binding_lifecycle_design.md` / `docs/features/field_atlas_binding.md` /
`docs/features/field_atlas_skeleton_editor_upgrade.md`

---

## 9. API 一覧

いずれも実パス。列挙は 2026-08-14 時点のルート定義から数え直したもの（計 29 本）。

### 9.1 学習者・共通（認証必須）

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/atlas/runtime-config` | データソース設定（`{"data_source": "api"\|"fixture"}`） |
| GET | `/api/atlas` | 地図データ一式（骨格 + キャッシュ + 個人層）。`cartridge` / `course` / `topic` / `level` / `focus`。骨格なし・ゲート不通過は 404 |
| GET | `/api/atlas/node/{node_id}?cartridge=` | 詳細パネル用の単一ノード情報（`cartridge` 必須） |
| POST | `/api/atlas/report` | 修正報告の送信（201 + `report_id`。匿名経路なし） |
| GET | `/api/atlas/reports/mine` | 自分の報告の処理結果（`?unacked=true`） |
| POST | `/api/atlas/reports/{report_id}/ack` | 本人のみ既読化 |
| GET | `/api/learning/atlas/{cartridge_id}/skeleton` | 学習者向け骨格（凍結版のみ） |
| GET | `/api/learning/atlas/cues/state` | 初回ログイン導線のフラグ |
| POST | `/api/learning/atlas/cues/events` | 導線の内部計測（201） |

`POST /api/learning/courses/{course_id}/atlas/path-decision`（学習パスカードの三択記録）は
`routes/learning.py` 側に実装がある。

### 9.2 教員（`_require_teacher`）— 骨格

| メソッド | パス |
|---|---|
| GET | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/generate` |
| PUT | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft`（`revision` 楽観ロック・衝突 409） |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft/from-frozen` |
| DELETE | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/freeze` |
| GET | `/api/admin/cartridges/{cartridge_id}/atlas/freeze-impact` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/retire` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/restore` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/assist/interpret` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/skeleton/assist/propose` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/overlay/refresh` |
| GET | `/api/admin/atlas/domains` |

### 9.3 教員 — 修正報告・バインディング

| メソッド | パス |
|---|---|
| GET | `/api/admin/cartridges/{cartridge_id}/atlas/reports?status=` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/reports/{report_id}/resolve` |
| POST | `/api/admin/cartridges/{cartridge_id}/atlas/reports/{report_id}/incorporate` |
| POST | `/api/admin/courses/{course_id}/atlas-binding/propose` |
| PUT | `/api/admin/courses/{course_id}/atlas-binding` |
| PUT | `/api/admin/courses/{course_id}/atlas-binding/pending` |
| DELETE | `/api/admin/courses/{course_id}/atlas-binding/pending` |

**カテゴリギャップ候補**（`routes/atlas_gaps.py`、migration 066）は分野マップを論文から育てる
別層で、正本は `docs/features/category_gap_candidates_design.md`。本書の対象外。

### 9.4 nginx

`/api/atlas` は `frontend/nginx.conf` に**明示 proxy が必須**
（`location /api/atlas/` + `location = /api/atlas`）。欠落すると SPA フォールバックが
index.html を **200 で**返し、フロントの `res.ok` 判定をすり抜けて JSON パース失敗経由で事故る。

出典: `backend/api/routes/atlas.py`（`router` / `admin_atlas_router` / `binding_router` /
`learning_router` / `report_router`）/ `backend/api/routes/atlas_view.py` /
`backend/api/main.py`（ルータ登録）/ `docs/features/field_atlas_binding.md`（nginx）

---

## 10. データストア

| データ | 保管先 | migration |
|---|---|---|
| 骨格 draft / 凍結版（S 層） | `atlas_skeletons` | 027 |
| ドメインのメタデータ・ライフサイクル | `atlas_domain_meta` | 028（`lifecycle` は 057） |
| 状態導出キャッシュ（C 層） | `atlas_overlay_cache` | 024 |
| 個人層（いまここ・足跡） | `interest_traces.payload.atlas` | 020 |
| 修正報告 | `atlas_correction_reports` | 023（`incorporated_*` は 046） |
| 導線計測・初回表示フラグ | `atlas_cue_events` | 026 |
| コース⇄地図バインディング | `learning_courses.data`（`cartridge_id` / `topics[].atlas_node_id` / `atlas_binding_pending`） | — |
| 骨格シード（ファイル） | `backend/cartridges/<id>/atlas/skeleton.yaml` / `backend/atlas_domains/<key>/skeleton.yaml` | — |

出典: `backend/db/023..028,046,057_*.sql` / `backend/core/atlas_store.py`
（`ATLAS_BUNDLED_DOMAINS_DIR`）/ `docs/architecture/layer_registry.md`

---

## 11. 設定（環境変数）

| 変数 | 既定 | 意味 |
|---|---|---|
| `ATLAS_DATA_SOURCE` | `api` | フロントのデータソース。`fixture` はローカル確認用にのみ明示指定 |
| `ATLAS_REFRESH_DELAY_SECONDS` | `60` | 陳腐化キャッシュのバックグラウンド更新までの遅延 |
| `ATLAS_ASSIST_MAX_CALLS_PER_DAY` | `60` | 骨格エディタ AI アシストの 1 教員 1 日あたり上限（interpret + propose 合算） |
| `ATLAS_ASSIST_LLM_MODEL` | 空 | 空なら分析 tier に委譲 |

出典: `backend/core/config.py` / `backend/core/atlas_state.py`

---

## 12. 旧§番号について

原本が消失しているため、既存文書中の §参照は本書の章番号と対応しません。コード・設計書の
コメントから読み取れる**おおよその主題**は次のとおりです（対応は保証しません。参照側の
文書を書き換えず、この対応表を目安として残します）。

| 旧§ | 参照元 | 主題（コード・設計書のコメントから推定） | 本書の該当章 |
|---|---|---|---|
| §1.2 | `field_atlas_skeleton.md` / `atlas-overlay.js` / `atlas-cues.js` / `atlas_path.py` | 設計原則（-4 = 踏破率を数値にしない、-6 = リアルタイム LLM 生成をしない） | §1 |
| §4.3 | `atlas_view.py` | 初期選択（L1/L2 = いまここ、L3 = 行間ステップ） | §5.1 |
| §5 | `atlas-overlay.js` / `atlas-minimap.js` | 視覚言語（色・形状） | §5.2 |
| §6 / §6.1 / §6.2 | `field_atlas_detail_panel.md` / `atlas_state.py` | 詳細パネル（検証行・承認行・アクション表示規則） | §4.4 / §5.3 |
| §7 | `field_atlas_correction_reports.md` | 修正報告フロー | §7 |
| §8 | `field_atlas_detail_panel.md` | チャット遷移・学習パス提案カード | §5.3 / §5.4 |
| §9 / §9.1 / §9.2 / §9.4 | `atlas.py` / `atlas_state.py` | 骨格スキーマ・3層モデル・seed_status・YAML トップレベル | §2 / §3 |
| §10 | `atlas_state.py` / `atlas_view.py` | 状態導出規則・unvisited の直交性 | §4 |
| §11 | `field_atlas_correction_reports.md` / `atlas_view.py` | API（報告送信・トップレベル配列） | §9 |
| §12 | `atlas_state.py` / `field_atlas_skeleton.md` | フィクスチャ・状態ピル文言・行間の定型文 | §4.4 |
| §13 | `atlas.py` / `atlas_view.py` / `atlas-overlay.js` | ノード数の上限 | §3.2 |
| §14 | `atlas-overlay.js` | 守るべき設計原則（再掲） | §1 |
| §16-1 | `atlas_state.py` / `field_atlas_skeleton.md` | assumed と contested の併発 → assumed 優先 | §4.2 |
| §16-2 | `atlas_state.py` | 行間の蓄積 3 件で assumed 候補へ昇格 | §4.2 |
| §16-3 | `atlas_placement.py` / `field_atlas_skeleton.md` | 霧領域の匿名ドット個数 → 固定 3 | §4.3 |
| §16-4 | `atlas.py` / `field_atlas_skeleton.md` | concept id の永続性 | §3.4 |
| §16-6 | `atlas-cues.js` | 初回自動表示のオプトアウト → 設定項目を作らない | §6.3 |

出典: 上表「参照元」列の各ファイル（`field_atlas_overlay_spec.md` §N という形の言及を
全文検索して収集した）

---

## 13. 追補（2026-09-03 コード照合）

本書 §1〜§12 は 2026-08-14 時点の再構成である。以後に地図の**周辺**へ積まれた層を、
本文を書き換えずにここへ列挙する（いずれも骨格の地形・座標・状態導出には手を入れない）。
各層の仕様の正本は右列の設計書で、本書の対象外。

| 追加 | 現れ方 | 正本 |
|---|---|---|
| 論文の位置（配置層） | オーバーレイ L1 に 📄 マーカーを重ねる `landscape-layer.js` のトグル。出典タブの「分野の中の位置づけ」と同じデータを共有 | `knowledge_landscape_design.md`（migration 065） |
| カテゴリギャップ候補 | 管理画面「分野の地図」タブのレビュー区画に第2グループ「論文の解析から見つかった候補」 | `category_gap_candidates_design.md`（migration 066） |
| ベクトル係留（VA層） | 「ベクトル索引」「登録済みの別名」区画。配置プレフィルタ・着地予測・別名レジストリ | `atlas_vector_anchoring_design.md`（migration 074） |
| 関係（辺）候補と推定の糸 | 同レビュー区画の第3グループ + 学習者側は `GET /api/atlas` の **optional キー `threads`** を読む `atlas-threads-layer.js`（L2・点線・既定オフ） | `atlas_relation_edges_design.md`（migration 076） |
| 骨格専用ドメインの同梱 | `backend/atlas_domains/<domain_key>/skeleton.yaml`（+ `domain.json`）を起動時に冪等シード。カートリッジ一式を持たない分野でも骨格を配れる | `knowledge_landscape_design.md` |
| 該当なし UX とドメインライフサイクル | propose の retired 除外・pending バインド・retire/restore・freeze-impact | `atlas_binding_lifecycle_design.md`（migration 057） |
| G層 fail-closed の徹底 | `atlas-data.js` の既定カートリッジ（`particle_physics`）フォールバックを撤去。コース文脈も明示 `cartridge` も無ければ取得せず `null`＝地図領域ごと非表示 | `guidance_layer_design.md` Phase 0 |

§9 の API 一覧（2026-08-14 時点・計 29 本）にも、上記のうち
`routes/atlas_vectors.py`（vectors status / refresh・aliases）と
`routes/atlas_edges.py`（edge-candidates 系）が加わっている。本数を数え直す場合は
`backend/api/routes/atlas*.py` の実定義を一次情報とすること。

## 14. 追補（2026-09-28 ペルソナ通し受講 第 8 周・IK-0412）

- `GET /api/atlas` の `ledger_status` は検証行と食い違わない値にする
  （`atlas_state.learner_ledger_status`）。骨格 seed 由来（`status_source='seed'`）・
  コーパス由来の状態なし（`unknown`）・引用 0 本の `verified` は **`unrecorded`**
  （語の正本 `label_vocab.ATLAS_LEDGER_STATUS_UNRECORDED`）。表示状態 `status` とノードの色は
  フロント互換のため不変。seed の「原文に裏付け」ピルは「骨格の初期表示（台帳に記帳なし）」。
- ノードに `pill_note`（ピルの意味の1行。正本 `label_vocab.ATLAS_PILL_NOTES`、nodes_list は
  `panel.pill_note`）。**フロント（`atlas-panel.js` / `atlas-overlay.js`）はまだ描いていない**。
- パンくずは分野の表示名（`atlas_domain_meta.name` → 同梱 `domain.json`）。無ければ
  「名前が登録されていない分野」。トップレベルに `domain_name` を追加（`cartridge` は取得キー
  として互換のまま）。

## 15. 追補（2026-10-23 第 25 周 第 3 波・IK-0940 / IK-0941 / IK-0942・migration なし）

- **霧の定義の是正（IK-0940）**: L1 の霧（`kind="fog"`）は「論文の配置も解析成果もない領域」。
  `atlas_state.load_corpus_snapshot` が `landscape_placements` の生きている配置（`status ∈
  landscape_schema.LEARNER_VISIBLE_STATUSES` = confirmed / inferred / review_required。旧版の node_id は
  `atlas_correspondence.build_node_resolver` で現行版へ読み替え）を `CorpusSnapshot.placed_node_ids` に読み、
  `compute_overlay_rows` が領域そのもの・その概念のどれかに配置があれば灯す。論文の海（`corpus_view` /
  `corpus-sea.js` の `densityIndex` — IK-0847）と同じ状態集合で、霧の判定の正本はこの 1 箇所。
  - 配置は**真偽だけ**を使い、記帳の件数（`evidence.coverage`）には足さない（CR3）。配置だけで灯った領域の
    検証行は `atlas_state.REGION_PLACED_ONLY_VERIFY_LINE`「この領域には論文が置かれています（論文の位置）。
    検証の記録（台帳）はまだありません。」。
  - キャッシュの陳腐化検知（`_SIGNATURE_SQL`）に `landscape_placements.updated_at` の最大値を足した。
  - CR2（コース学習の非改変）は霧の**定義の是正**であって学習経路の変更ではない、として抵触しないと判断した
    （指揮者の判断・推奨どおり）。霧の固定文 `FOG_VERIFY_LINE` / ピル注記は意味どおりになった。
  - 残課題: L1 のキャッシュはドメイン単位のため、配置の有無は**全利用者の配置**で決まる（海は本人が閲覧できる
    論文だけで決める）。L1 が言うのは「灯り／霧」の真偽だけで題名・件数は出さないので開示の差は小さいが、
    閲覧できない論文だけが置かれた領域では L1 が灯り、海は霧になりうる。利用者別の判定にするかは実例を待つ。
- **状態を名前に焼かない（IK-0941）**: 霧の領域を詳細パネル用の `nodes[region_id]` に載せるときの `label` は
  領域名のまま（旧「〇〇（霧の領域）」）。霧であることは `pill`（「論文未投入」）が言う。論文の海の
  `anchorLabel` は領域アンカーなら `regionById` の名前を最初に引く。
- **版の呼び名（IK-0942）**: 教員・学習者とも「地図 版 X」の 1 形式。正本 `label_vocab.ATLAS_VERSION_PREFIX` /
  `atlas_version_label()`、JS は逐語ミラー（admin.js `atlasVersionText` / landscape-layer.js /
  atlas-threads-layer.js / app.js / admin-release-review.js。一致は `test_atlas_review_round25_r3.py`）。
  分野の地図タブの状態行から「学習者に公開中: 版 X」を外した（要約カードが言う）。論文ディスカバリー・
  レーダーの「骨格 版」は面 C の外として残した（残課題）。

## 16. 追補（2026-10-25 第 25 周 第 4 波・IK-0964 / IK-0942 残り・migration なし）

- 札と説明の軸を揃える: 札が個人の状態（「未訪問」「いまここ」）のとき、ピルの説明（`pill_note`）も訪問の軸で言い
  （`label_vocab.ATLAS_PILL_NOTES["unvisited" | "now"]`）、台帳の状態は検証行だけが言う。札が台帳の状態なら従来どおり台帳の状態で引く。
- 学習者に「骨格」を出さない: オーバーレイのフッターは `ATLAS_VERSION_PREFIX`（「地図 版 」）の逐語ミラー、seed 由来の検証行は
  「検証: 台帳に記帳なし。分野の地図（教員レビュー済）の初期表示。」。

## 17. 追補（2026-10-27 第 25 周 第 5 波・IK-0989〜0995 / IK-0942・0964・0967 の残り・migration なし）

凍結が初めて成立した第 5 周で見えた「版が上がった後の派生面の追随」と「確定の前後の対称性」の是正。
地形は人間・関係は離散の辺（`field_map_display_principles_2026-08-29.md`）は変えない — 描く範囲と、描かなかった事実の言い方だけを変える。

- **IK-0989 L2 の代表選出をサーバへ**: `routes/atlas_view.py::select_l2_concept_ids` が骨格概念を
  ①焦点・いまここ・コースのトピックに対応する概念（`topics[].atlas_node_id`）・足跡 → ②それらの 1 hop（骨格の関係）→
  ③骨格の並び順 で上限 `_MAX_L2_NODES`（§13 の 20）まで選ぶ（決定論）。描かなかった概念があれば
  `levels["2"].omitted_note`（正本 `label_vocab.ATLAS_L2_OMITTED_NOTE`・件数なし）、実線は描いた概念どうしの間だけ送る。
  推定の糸の端点が描かれていなければ `threads.undrawn_note`（`ATLAS_THREADS_UNDRAWN_NOTE`）。フロントは
  `#atlas-level-note` / `.threads-undrawn` に素通し（`LIMITS.l2Nodes` は安全弁として残す）。
- **IK-0964 残り**: 札「未訪問」の説明は「まだこの場所から学んでいません（地図で選ぶだけでは数えず…）」— 訪問は
  `interest_traces` の atlas 痕跡で数えることを言う。
- 他面の是正（索引の構築中・公開中の版の読み返し・作成方法・辺の結果文・置けなかった主題の所在・縁の解消・配置の理由）は
  各設計書の第 5 波の節（`atlas_vector_anchoring_design.md` §13.3 / `atlas_relation_edges_design.md` §11.z4 /
  `category_gap_candidates_design.md` §10-9 / `corpus_roaming_design.md` §14.3 / `knowledge_landscape_design.md` §15.y）。
- ガードレール: `backend/tests/test_atlas_review_round25_r5.py`。

### 17.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い（採る部品・見送る派生形） |
|---|---|---|
| 描画上限で母集合を切る | `information-dropped-as-unrepresentable` | 切る判断をサーバの 1 箇所に寄せ、描かなかった事実を事実文で返す（件数なし）。上限を撤廃して全 50 概念を描く派生形は §13 の目安と可読性から見送る |
| 縁と候補キューが同じ信号から別々に導出 | `rule-enforced-per-surface` / `duplicate-canonical-sources` | 解消済みの判定を `atlas_gaps.store.resolved_label_set` 1 つに寄せ、縁はそれを呼ぶだけ（新しい判定を書かない）。教員の判断（decisions）は縁で読まない（§5.6） |
| 凍結の後処理（索引）が非同期 | `stale-derivative-served` | 構築中の印（プロセス内・失敗しても必ず外す）で状態表示と関係の候補が「進行中」を名乗る。凍結の応答の形は変えない（VA4） |
| 版ごとの来歴欄 | `same-name-different-referents` | 次版の下書きの `generated_by` を `revision_of:{前の版}` にし、系列の起点は履歴行に任せる |
| 確定の後に結果を読み返す面・否定の判断の結果 | `last-mile-missing` / `negative-decision-second-class` | 公開中の版を既存のプレビュー部品で読み取り専用に・判断の結果文を組の名前で |

当たらない族: 再実行と削除（行を消さない・書き込み経路を増やさない）/ 順序と予算（外部 API・LLM 呼び出しを増やさない）。

## 18. 追補（2026-10-27 第 25 周 第 6 波・IK-1017 / IK-1018 / IK-1019 / IK-0992 の残り・migration なし）

第 6 周で凍結 2026.3 が成立し、確定した関係が学生の L2 に届くかを観察した。第 5 波は「黙って切る」を直したが「何を選ぶか」が残った。

- **IK-1017 L2 の手がかりにコースの論文の配置と凍結で加わった関係を足す**: `select_l2_concept_ids(..., course_hints=)` の順は
  ①焦点・いまここ・トピックの対応（`topics[].atlas_node_id`）・足跡 → ②コースの論文が置かれている概念
  （`course_placement_concept_ids`: コース sources の `landscape_placements` のうち学習者に見せてよい状態 = 学習者の
  「論文の位置」レイヤーと同じ母集合・confirmed 優先・旧版の node_id は `atlas_correspondence.build_node_resolver` で読み替える
  だけ = NC5）→ 凍結で加わった関係の両端（`added_relation_pairs`: `atlas_store.load_frozen_history` の末尾 2 版の無向差）→
  ③それらの 1 hop → ④骨格の並び順。読みは `_l2_course_hints` に寄せ、失敗は空（従来動作へ縮退・fail-soft）。
  事実文は `label_vocab.atlas_l2_omitted_note(hints)` が**描いた概念に実際に効いた手がかり**の語
  （`ATLAS_L2_HINT_LEARNING` / `_PLACEMENTS` / `_ADDED_RELATIONS`）を並べて言い、手がかりが無いときは
  「このコースには地図の上の手がかりがまだ無いため、地図の並び順で先頭の項目を描いています」と言う（優先が働いたように言わない）。
  学習の手がかりだけのときは第 5 波の `ATLAS_L2_OMITTED_NOTE` と同文。数値・件数は言わない。
- **IK-1018 承認の行は承認だけを言う**: 「暗黙の前提」で承認の記録が無いときの行は
  `label_vocab.ATLAS_ENDORSE_NONE_ASSUMED_LINE`「承認: このコーパスの中では、教員の承認の記録がありません」（SL1 の閉世界）。
  状態キャッシュに残る旧文は `atlas_state.learner_endorse_line` が読み時に置き換える（キャッシュを書き換えない）。
  学習者のパネルの文に出さない内部の欄名は `ATLAS_LEARNER_PANEL_DENYLIST`。
- **IK-1019 ［止めている候補を見る］はカードへ**: `readiness.pending_items[]` に表示しない番地 `target_key`
  （関係 = `edge_key` / 論文由来の候補 = `cluster_key`）を足し、`admin.js::scrollToFreezeBlocker` が先頭の理由のカード
  （`[data-edge-key]` / `[data-cluster-key]`）へ着地して枠を一時強調する。カードが無ければその種類のグループ、それも無ければ区画。
- **IK-0992 の残り**: 下書きがある間も `#atlas-published-view` を出す。見出しは
  「公開中の版（地図 版 X）を見る（読み取り専用 — 下で編集している次版の下書きとは別です）」で閉じた状態。
- ガードレール: `backend/tests/test_atlas_review_round25_r6.py`。

### 18.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い（採る部品・見送る派生形） |
|---|---|---|
| 優先順位の関数に手元の手がかりを足す | `available-but-unwired` | 学習者の「論文の位置」と同じ母集合（`LEARNER_VISIBLE_STATUSES`・コース sources）を同じ store 関数で読む。L2 専用の新しい配置判定を書かない |
| 手がかりの有無で文を変える | `wording-mismatch` / `rule-enforced-per-surface` | 文は `label_vocab` 1 箇所の関数で組み、route は「実際に描いた概念に効いた」手がかりだけを渡す（入力したが効かなかった手がかりを言わない） |
| 版をまたぐ node_id | `same-name-different-referents` | 旧版の配置は読み替えるだけ（`landscape_placements` を書き換えない）。読み替えできない旧ノードは手がかりにしない |
| 学習者向け固定文が内部の欄名を持つ | `wording-mismatch` | 文の正本を `label_vocab` に移し、キャッシュの旧文は読み時に置き換える（キャッシュの再計算を待たない） |
| 確認画面の「そこへ行く」 | `last-mile-missing` | 番地は表示しない `target_key` だけをサーバが添え、着地先の解決はフロントの既存カード属性で行う |

当たらない族: 再実行と削除（行を消さない・`atlas_skeletons` への書き込み経路を増やさない）/ 順序と予算（外部 API・LLM・
embedding 呼び出しを増やさない）。

## 19. 追補（2026-10-28 第 25 周 第 7 波・IK-1044〜1047 + IK-0942 / IK-1018 / IK-0664 の残り・migration なし）

第 7 周で凍結 2026.4（関係だけの版上げ）が成立し、学生の L2 に「確定済みの関係」が残るかを観察した。

- **IK-1045 L2 の手がかりは改訂で加わった全部の関係**: `added_relation_pairs` は末尾 2 版の差ではなく、
  `load_frozen_history` の全版を遡り、現行凍結版の各関係が**最後に加わった版**を決めて「直近の改訂 → 古い改訂」の順に並べる
  （系列の最初の版から在る関係は含めない・同じ版の中は現行版の並び順）。直近の差だけだと、版上げ 1 回で前の改訂で確定した
  関係が手がかりから外れていた。手がかりの語 `ATLAS_L2_HINT_ADDED_RELATIONS` は「地図の版の改訂で加わった関係」（選出と一致）。
- **IK-1018 残り 学習者の地図の文に「台帳」「記帳」を出さない**: 正本の文（`atlas_state` の検証行・`label_vocab.ATLAS_PILL_*` /
  `ATLAS_PILL_NOTES` — 教員側・ガードレールが読む）は変えず、学習者ルート `routes/atlas_view.py` が `_apply_learner_wording` で
  `pill` / `pill_note` / `verify` / `endorse`（`/node` は `verify_line` / `endorse_line`）にだけ
  `label_vocab.learner_atlas_wording`（置換表 `ATLAS_LEARNER_WORDING`・長い句から順・決定論）を掛ける。名前（label）は触らない。
  `atlas-panel.js` の既定文は「検証の記録がありません。」、`atlas-overlay.js` の案内は「記録された根拠」。
- **IK-0942 残り 論文の海も「地図 版 X」**: `corpus_view.build_corpus_landscape` が `skeleton_version_label`
  （`label_vocab.atlas_version_label`・空なら空文字）を返し、`corpus-sea.js::versionLineHtml` が凡例の下に素通しで描く。
- **IK-0664 論文の呼び方は題名を正本・無ければ arXiv 番号**: `core/document_refs.choose_paper_titles`（純関数）/
  `resolve_paper_titles`（SQL）が ①その取り込みの読める題名 → ②閲覧できる同じ論文の別の取り込みの読める題名 → ③arXiv メタデータの写し
  （`paper_discovery_arxiv_metadata_cache` の読みだけ・外部 API は呼ばない）→ ④保存値（番号）の順で決める。学習者の「論文の位置」
  （`learner_landscape_for_documents(..., viewer_id=)`・兄弟の母集合は `list_visible_document_ids`）と「登録済み教材」
  （`resolve_course_source_titles` は写しだけ・`_with_resolved_source_titles` は arXiv 番号の写しを仮の値と見なす。教員の読める題名は
  上書きしない）が同じ規則を通る。画面文脈アダプターの経路（viewer なし）は写しまで。
- IK-1044 / IK-1046 / IK-1047 は `atlas_relation_edges_design.md` §11.z5 と `knowledge_landscape_design.md`（配置の理由の注記）。
- ガードレール: `backend/tests/test_atlas_review_round25_r7.py`。

### 19.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い（採る部品・見送る派生形） |
|---|---|---|
| 「確定済み」を「最近変わった」で代えていた | `stale-derivative-served` | 全版の差から「最後に加わった版」を導出し直す（関係を保存しない・読み時導出のまま） |
| 学習者向けの語の置換 | `wording-mismatch` / `rule-enforced-per-surface` | 置換表は `label_vocab` 1 箇所、route は 1 つの関数で文のキーにだけ掛ける。正本の文・キャッシュは書き換えない |
| 呼び方の判定が面ごとに別 | `rule-enforced-per-surface` | 読める題名の判定は `document_refs.title_is_readable`（論文の海の代表選出と同じ `title_is_arxiv_number`）に寄せる |
| 他の取り込みから題名を借りる | `scope-widened-silently` | 兄弟は閲覧できる document だけ（呼び出し側の `list_visible_document_ids`）。viewer が無い経路は兄弟を引かない |

当たらない族: 再実行と削除（行を消さない・`atlas_skeletons` への書き込み経路なし）/ 順序と予算（外部 API・LLM・embedding を増やさない）。

## 20. 追補（2026-10-28 第 25 周 第 8 波・IK-1045 / IK-1055 / IK-1056 + IK-1046・IK-1044 の残り・migration なし）

第 8 周（固定コース = 8 トピックの binding あり）で、トピックの対応と配置だけで L2 の上限が埋まり、改訂で加わった関係の両端が
列の最後で押し出された（IK-1045 STILL）。

- **IK-1045 確定済みの関係に枠を先に配る**: `select_l2_concept_ids` に `leading`（焦点・いまここ）/ `relation_pairs`（改訂で加わった
  関係。直近 → 古い順）/ `relation_reserve`（`_MAX_L2_NODES // 2`）を足し、順を ⓪leading → ⓪'関係（**組ごとに両端そろえて**・枠内）→
  ①トピックの対応・足跡 → ②配置 → ②'枠に入らなかった関係（組ごと）→ ③1 hop → ④骨格順 にした。片端だけを入れない（原則①′
  「関係は離散の辺」— 線の無い点を増やさない）。それでも描けなかった確定済みの関係は `omitted_note` の末尾に
  `label_vocab.atlas_l2_undrawn_relations_line` で**名前で言う**（「A — B」・件数なし・挙げきれなければ「ほか」）。
- **IK-1055 手がかりの語を入力で分ける**: `ATLAS_L2_HINT_LEARNING`「いま学んでいる場所」は学習者本人の状態（焦点・いまここ・足跡）が
  描いた項目に効いたときだけ。コースのトピックの `atlas_node_id` は `ATLAS_L2_HINT_COURSE_TOPICS`「このコースのトピックが結ばれている場所」。
- **IK-1056 承認の記録なしは 1 つの文**: `atlas_state.endorse_line_for` の記録なしの枝は状態に依らず `label_vocab.ATLAS_ENDORSE_NONE_LINE`
  （閉世界「このコーパスの中では、教員の承認の記録がありません」）。状態キャッシュに残る旧い「承認: 承認記録なし」は
  `learner_endorse_line` が読み時に置き換える。
- **IK-1046 残り 切り詰めた略**: `core/landscape/schema.truncation_matched_in`（大文字始まり・英字のみ・3 文字以上で、短い方が長い方の
  先頭と一致 — Cep ⇔ Cepheus）を `reason_beyond_quote_note` の除外に足した（A層の照合の正本は不変・表示の段で絞る）。
- **IK-1044 残り（管理）**: 見送った判断のメモは見出し「見送りの理由」（採用のメモは「判断のメモ」のまま）。下書きの無いときの未採用カードは
  `EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT`「[採用] を押し、この区画の先頭の［現在の版から次版の下書きを作る］で下書きを作ると反映できます。」。

pre-mortem: 当たる型は `extent-decided-by-proxy-signal`（確定済みを「優先の列の後ろ」で代えていた）・`wording-mismatch`・
`rule-enforced-per-surface`・`substring-match-false-positive`。当たらない族: 再実行と削除 / 順序と予算（外部 API・LLM・embedding を増やさない）。

## 21. 追補（2026-10-31 第 25 周 第 10 波・IK-1118 / IK-1119 + H54・te-01 の軽 2 件・migration なし）

第 10 周（凍結 2026.7・確定済みの関係 7 本・うち有向 4 本）で、描けなかった有向の 1 本と教員の反映の知らせから向きが落ち（IK-1118）、
学生は「改訂で加わった関係」を直近の版の差と読んだ（IK-1119）。

- **IK-1118 関係を名前で言う全ての出口を句に通す（同じ型の 4 回目）**: 面を足していく直し方をやめ、出口を列挙した。L2 の
  「ここには描いていないもの」（`l2_undrawn_relations` を (kind, from_label, to_label) に・`atlas_l2_undrawn_relations_line` が
  `edge_relation_phrase`）/ 反映の知らせ（`atlas_review_display.incorporated_edge_result_line` — 下書きに保存された辺の種別と記載順）/
  採用の結果文（`edge_decide_result_line(..., kind)`）/ 凍結を止めている採用の一覧（`routes/atlas.py::_edge_pair_label` →
  `atlas_lifecycle.edge_change_label`）/ JSON Patch の `after`。既にあった面（カード・反映の確認・凍結の影響・L2 の実線と
  `relations_note`・右パネル）と合わせ、「A — B」を f-string で組む関数は同じ関数で句の関数を通すことを AST で固定した
  （`test_atlas_review_round25_r10.py::test_no_dash_join_outside_phrase_functions`。種別が引けないときだけ「A — B」の fallback）。
- **IK-1119 版の範囲の語と版の事実（オーナー判断 = 推奨どおり）**: L2 の手がかりの語と描いていない行の前置きは「**これまでの**版の
  改訂で加わった関係」（全改訂の集合であることを言う）。版の事実は**右パネルの「地図の関係:」の行にだけ**「（版 2026.7 で加わった）」
  （`label_vocab.ATLAS_PANEL_RELATION_ADDED_IN`。判定は `added_relation_pairs` と同じ内部関数の `added_relation_versions`。最初の版から
  在る関係には添えない）。**地図の上には印・NEW バッジ・告知を付けない**（category_gap_candidates_design §5.6 の「版更新の告知を作らない」を
  維持 — 学生が選んだ概念について尋ねたときだけ見える属性は告知に当たらない、と読む）。
- **H54 反映済みの採用は見送れない**: `POST …/edge-candidates/decide` の `dismiss` は、採用済みの関係が次版の下書きに入っていれば
  （下書きが無ければ公開中の版に入っていれば）409 の事実文（`EDGE_DISMISS_LOCKED_IN_DRAFT` / `_IN_FROZEN`）。判断と次の公開版の食い違いを
  作らない。下書きから外して保存すれば見送れる。カードは `dismiss_locked` / `dismiss_locked_note` を受けて［見送り…］を出さない。
- **te-01 対応の要らない凍結**: 凍結で消えるノードが無く対応の区画が出ないときは、`freeze-impact` の `facts` に
  `atlas_correspondence.FACT_NO_CORRESPONDENCE_NEEDED`「この凍結で消えるノードはないため、前の版のノードとの対応を付ける必要はありません。」。
- **右パネルの置かれた論文**: `panel.papers` / `nodes[*].papers` =「ここに置かれているこのコースの論文: 『…』（教員確認済み）、『…』（AIによる
  推定（未確認））。」（名前の列挙・件数なし・出所ラベル必須 = LS8。母集合は L2 の手がかりの配置と同じ・`course_papers_by_concept`）。

### 21.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 同じ事実（関係の向き）を言う面が複数 | `rule-enforced-per-surface` | 正本の句 1 つ + 出口の AST 走査（原理 `route-all-surfaces-through-one-point` を consulted） |
| 集計範囲を広げた集合の呼び名 | `same-name-different-referents` | 語に範囲（「これまでの」）を入れ、版はパネルの属性として別に言う |
| 判断と公開物の 2 つの状態 | `contract-changed-one-side` | 判断の遷移側で版の状態を見て断る（UI の非表示は補助） |

当たらない族: 再実行と削除（行削除・migration なし）/ 順序と予算（外部 API・LLM・embedding を増やさない）。
