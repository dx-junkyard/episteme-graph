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
- 実装状態: 全機能実装済み（issue A〜F + S1〜S3 + ドメインライフサイクル）
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
