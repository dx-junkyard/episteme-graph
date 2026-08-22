# わたしの地図「いまここの周り」（近傍関係ビュー, PM-N層）

> **状態:** 実装済み（正本）— 2026-08-18 起案・同日 v1 実装。2026-08-21 に**範囲モード
> （§10、topic 縮退痕跡の事実ベース粗表示）**、2026-08-22 に**縮退是正（§11、コース範囲
> フォールバック + has_touch 撤廃 + 出口案内 + topic チップの題名表示）**を追加実装。
> migration 不要（既存テーブルの読みのみ）。以後は §9 / §10.6 / §11.4 実装記録の追記のみ

親文書は [personal_knowledge_network_design.md](personal_knowledge_network_design.md)（PN-1〜PN-7）と
[knowledge_network_vision.md](knowledge_network_vision.md)（KN-1〜4）。本書はその上に、
**「わたしの地図」に周囲との関係を持ち込む**ための追加ビューを定義する。既存の3タブ
（いまの地図 / 問いからの旅 / 振り返り）と `/api/me/personal-network` は非改変で、
読み取り専用のタブと読み取り専用のエンドポイントを1つ足すだけ。

---

## §0 不変条項

親文書の PN-1〜PN-7 を全て継承する。本ビュー固有の条項を加える。

| # | 条項 | 理由 |
|---|---|---|
| **PMN-1** | **位置に意味の無い配置をしない** | 「地図」を名乗る画面で、並び順・距離・隣接が何も表さないのは出所の不正直。本ビューの縦軸は**依存の向き**という実在の関係のみを表し、それ以外の座標的意味を主張しない |
| **PMN-2** | **推測の辺を描かない** | 辺は `source_backing_status ∈ {source_backed, partially_source_backed}` のものだけを描く。`inferred` / `review_required` / 未分類の辺は描かず、描けなかったことを事実文で言う（KN-3） |
| **PMN-3** | **閉世界語彙**（SL1 継承） | 検証の不在について言えるのは「このコーパスの中では検証記録がありません」だけ。「この分野では未検証」「誰も検証していない」は書かない |
| **PMN-4** | **数値を見せない** | confidence / load_score / 支持経路の本数 / 件数 / 割合を返さない・描かない（PN-4・UC9・SL4 継承） |
| **PMN-5** | **助言・評価をしない** | 事実文のみ。「安心して進んでよい」「ここを先に固めるべき」のような指示・評価は書かない（D層の煽り語彙禁止を継承） |
| **PMN-6** | **非LLM・同期・DB 非変更** | 導出は決定論。LLM を呼ばない。書き込み系エンドポイントを作らない（PN-2/PN-5） |
| **PMN-7** | **fail-closed / 段階的縮退** | 権限が無ければ 404。データが無ければ 200 + `available:false` + 事実文（欠落を異常として演出しない、P4） |

---

## §1 背景 — 何が足りなかったか

Phase P-3 の最上位パネル（`personal-map-home.js`）は3タブすべてがテキストのリストで、
図形描画が1つも無い。図が出るのはコースビュー（`personal-map.js`）が Field Atlas
オーバーレイに重ねるドットだけで、しかも「凍結骨格のあるコース」かつ「オーバーレイを
開いたとき」に限られる。

検討の過程で、単に痕跡を円周に並べる案（アンカー星図）を作ったが破棄した。**区画
（`anchor_type` の束）までしか位置に意味が無く、区画内の並び順は ID 順・中心からの距離は
全ハブ同一・隣接に関係が無いのに、円形の見た目が「配置に意味がある地図」を誤って約束する**。
地図とは周囲との関係であって分類の配置ではない、という指摘（2026-08-18）を受けて、
「何と何の関係を見せるか」を先に決める形に設計を差し替えた。それが本書。

---

## §2 見せる関係

### 採用する2つ

| 関係 | 学習者に何が起きるか | 図の担い手 |
|---|---|---|
| **R1 依存の向き（上流／下流）** | 引っかかりの**重さ**が読める。土台でつまずいているのか末端でつまずいているのかは本人には見えない | **縦軸**（上＝これが前提にしていること／下＝これに依存していること） |
| **R2 確かめられているか** | **困り方の性質が変わる**。「分からないのは自分のせい」と思っていた場所が「このコーパスの中では検証記録がない」場所だと分かる | **枠線**（実線＝検証の記帳あり／点線＝記帳なし）＋事実文 |

補助として、本人の関与（記号）と、まだ本人が触っていない隣（淡いノード）を同じ図に載せる。

### 採用しなかった関係と理由

- **R3 分野の地図上の隣接**: 既存の Field Atlas が担う。痕跡→骨格の対応は topic binding
  経由のみで取りこぼしが多く（数式・導出ステップ・理論構成は対応づかない）、本ビューの
  主目的である「毎回役に立つ近傍」には向かない。**別タブとして残す**（§8）。
- **R4 教材の順序上の前提**（`topics[].prerequisites`）: 意味は近いが粒度がトピックで粗い。
  R1 と混ぜると「理論の依存」と「教材の並び」が同一視される。v1 非スコープ。
- **R5 同じ場所への反復**: 既存の `anchor_groups`（いまの地図タブの二重リング）が担う。
- **R6 論文をまたぐ同じ部品**: 既存の旅（journey）の [2][3] 区間が担う。

---

## §3 データ源と解決規則

すべて既存テーブル・既存モジュールの**読み**のみ。新テーブル・新カラム・migration は無い。

### 3.1 骨格 = TheoryOperationGraph の main 層

`theory_component_graphs.graph_json` を
[`queries.fetch_component_graph(document_id)`](../../backend/core/personal_graph/queries.py) で読む
（旅と同じ軽量読み）。対象は `graph_layer == "main"` のノードだけ
（`equation_detail` / `debug` 層は使わない — CLAUDE.md TheoryOperationGraph 節）。

- **ノードのラベル**は A層が付ける英語 theory stage 名（`Theory basis` 等）。
  学習者向けの日本語は [`core/element_vocab.py`](../../backend/core/element_vocab.py) の
  `theory_stage_key()` / `theory_stage_label()`（既存・訳語の正本）で解決する。
  **新しい訳語表を作らない**。
- **辺の向き**は `source_component_id` → `target_component_id` が「前提 → 結果」
  （normalizer が stage 進行順に張る）。したがって
  **上流 = center が target 側の辺の source**、**下流 = center が source 側の辺の target**。
- **PMN-2 の辺フィルタ**: `source_backing_status ∈ {source_backed, partially_source_backed}`
  のみ採用（`core/doubt/support_paths.py` の容量1エッジ条件と同じ規則）。

### 3.2 中心の解決（本人の痕跡アンカー → main ノード）

旅の [`journey._find_main_node`](../../backend/core/personal_graph/journey.py) は
`component` / `claim` の2種のみを解決する。本ビューは対応語彙を広げる（同関数は非改変で、
本ビュー側に解決規則を持つ）。

| `anchor_type` | main ノードとの突合 | document の解決 |
|---|---|---|
| `component` | `component_id` 一致 or `member_component_ids` に含む | `queries.fetch_component_document_id` |
| `claim` | `linked_claim_ids` に含む | `queries.fetch_claim_document_id` |
| `equation` | `linked_equation_ids` に含む | コース sources を決定論順に走査 |
| `derivation_step` | `linked_derivation_ids` に含む | 同上 |
| `stage` | ノードの stage キー一致 | 同上 |
| `topic` | **範囲モード（§10）**: `topics[].linked_claim_ids` → claim → main ノードの決定論解決。1点の中心を偽装せず「触れている範囲」を返す | claim ごとに `fetch_claim_document_id` |
| `concept` / `chunk` / `segment` / `graph_edge` | **v1 非対応** | — |

- コース sources 走査は `queries.fetch_course_document_ids(course_id)` をソートした先頭
  **最大 `MAX_DOCUMENTS_SCANNED = 5` 件**まで。走査した全 document は
  `can_view_document` を通す（PMN-7）。
- 解決できなければ `available:false` + 事実文（「この記録は、まだ論文の理論構成に
  結びついていません。」）。エラーにしない（P4）。

### 3.3 検証状態 = epistemic_ledger

`epistemic_ledger`（`UNIQUE(target_id, target_type)`）を `target_type='component'` /
`target_id = component_id` で引く（[`ledger_builder`](../../backend/core/doubt/ledger_builder.py)
が main 層ノードについて作る行と同じキー）。

- 表示文言は [`label_vocab.VERIFICATION_STATUS_LABELS_LEDGER`](../../backend/core/label_vocab.py)
  をそのまま使う（「直接検証の記帳あり」「間接的な支持あり」「未検証」「反証の記帳あり」
  「検証情報なし」）。**「記帳がある / ない」を主語にする表**なので PMN-3 に適合する。
  W層レンズ用の `VERIFICATION_STATUS_LABELS_LENS` は使わない（宛先が違う）。
- 台帳行が無いノードは `verification: null`。**1件も無ければ `ledger_available: false`** を返し、
  UI は枠線の区別ごと出さない（fail-closed。図は依存の向きだけを見せる）。
- `load_score` は返さない（PMN-4）。

### 3.4 支持線 = core/doubt/support_paths

**中心ノードについてのみ** `build_support_context` を1回作り
`compute_support_lines_from_context(ctx, "component", component_id)` を呼ぶ
（全ノードに対して最大流を回さない）。返すのは `fact_line` **のみ**で、
`level` / `cut_members` / `observation_roots` は返さない（PMN-4。`fact_line` は
既存の学習者向け台帳 API がすでに学習者へ出している文言と同一）。

### 3.5 本人の関与 = 既存の個人ネットワーク導出

`derive_person_network(user_id)` の結果を再利用し、各 main ノードに対して
「そのノードが束ねるアンカー（`linked_claim_ids` / `member_component_ids` /
`linked_equation_ids` / `linked_derivation_ids` / stage キー）に一致する本人ノード」を
突合する。**candidate は含まない**（導出側が既に除外している。PN-3）。

---

## §4 API

`GET /api/me/personal-network/nearby`（`me_router`、本人のみ、読み取り専用）

| クエリ | 必須 | 意味 |
|---|---|---|
| `node_id` | ✓ | 本人の痕跡ノード ID（`/api/me/personal-network` の `nodes[].id`） |
| `mode` | — | `near`（既定・前後1階層）/ `root`（土台までの道筋 + 下流1階層）。他の値は 422 |
| `center_component_id` | — | 中心の移動先。**`node_id` から解決した document の main 層に存在する ID のみ**。存在しなければ 404 |

- `node_id` が本人の個人ネットワークに無ければ 404（旅と同じ）。
- 書き込みメソッドは追加しない（`me_router` の読み取り専用性はガードレールで固定済み）。

レスポンス:

```json
{
  "available": true,
  "mode": "near",
  "ledger_available": true,
  "center": { "...node..." },
  "upstream":   [ { "...node..." } ],
  "downstream": [ { "...node..." } ],
  "root_path":  [ { "...node..." } ],
  "edges": [ {"from": "<component_id>", "to": "<component_id>"} ],
  "facts": ["..."],
  "notice": null
}
```

ノードの形:

```json
{
  "component_id": "...",
  "label": "理論の土台",
  "stage": "theory_basis",
  "verification": {"status": "untested", "label": "未検証"},
  "mine": [ {"trace_id": "...", "kind": "tension", "kind_label": "引っかかり",
             "text": "...", "course_id": "...", "created_at": "..."} ],
  "is_center": false
}
```

- `mine` が空 = まだ本人が触っていないノード（UI は淡く描く）。
- `facts` はサーバが組む事実文の配列（検証・支持線・上流／下流の列挙）。
  クライアントで文言を組み立てない（文言の正本をサーバに置く）。
- 数値フィールドは**一切含めない**（`confidence` / `load_score` / `level` / 件数）。
- `notice` は `available:false` のときの事実文。

---

## §5 UI

`personal-map-home.js` に**先頭タブ**「いまここの周り」を追加する（既存3タブは順序も
挙動も不変）。

- **中心の選択**: 既に取得済みの `/api/me/personal-network` の `nodes` から
  「自分の記録がある場所」チップ行を組む（追加 fetch なし）。押すと `nearby` を取得。
- **モード切替**: 「近く（前後1階層）」/「土台までの道筋」。押したときだけ再取得。
- **図**: SVG。縦にレーン（これが前提にしていること / いまここ / これに依存していること）。
  チップの枠線は `verification.status`（`untested` / `unknown` は点線）。`mine` があれば
  kind 記号（`.personal-map-dot-*` と同じ形・色 — [atlas.css](../../frontend/public/css/atlas.css)
  の既存記号体系を共有する）。`mine` が空なら淡色。
- **中心移動**: チップのクリックで `center_component_id` を付けて再取得。
- **事実文**: 図の下にサーバの `facts` をそのまま並べる。
- **自分の記録**: 中心ノードの `mine` を既存のノード行（`.pm-home-node-row`）で描き、
  「ここから旅に出る」「地図には反映しない」の既存導線を再利用する。
- ポーリング禁止。数値・進捗・ゲーミフィケーション表示なし。常設注記は既存のまま。

---

## §6 縮退の規則（fail-closed）

| 状況 | 挙動 |
|---|---|
| `node_id` が本人のものでない | 404 |
| document が閲覧不可 | その document を走査対象から除外（結果的に解決不能なら `available:false`） |
| main 層グラフが無い / 中心が解決できない | `available:false` + 事実文（異常表示にしない） |
| 採用できる辺が無い | `upstream`/`downstream` 空 + 事実文「この場所の前後のつながりは、まだ出典から確認できていません。」 |
| 台帳行が1件も無い | `ledger_available:false`。UI は枠線の区別を出さず、依存の向きだけを見せる |
| 支持線が計算できない | `facts` から支持線の行を落とすだけ（他は出す） |
| 上流ゼロ / 下流ゼロ | 事実文で言う（「この論文の中には見つかりません」）。助言は書かない（PMN-5） |

---

## §7 ガードレール

`backend/tests/test_personal_map_nearby.py`（新規）と既存 UI 静的テストの拡張で、
次を構造的に固定する。

1. `core/personal_graph/nearby.py` が FastAPI / LLM を import しない
2. `inferred` / `review_required` / 未分類の辺を採用しない（PMN-2）
3. レスポンスに数値キー（`confidence` / `load_score` / `level` / `weight` / `count`）が
   再帰的に現れない（PMN-4）
4. 禁止語彙（「この分野では未検証」「誰も検証していない」「世界初」「未踏」「すべき」
   「安心」）が実装ソース・レスポンスに現れない（PMN-3 / PMN-5）
5. 他人の `node_id` では 404（PN-1）
6. `me_router` に書き込みメソッドが増えていない（既存ガードレールの継続）
7. 台帳ゼロで `ledger_available:false` かつ図が成立する（PMN-7）
8. 訳語は `element_vocab` / `label_vocab` からのみ引く（重複表を作らない）

---

## §8 非スコープ（v1）

- **R3 分野の地図に重ねる表示**（案1）。Field Atlas 側の既存機能として残し、本パネルの
  タブとしての統合は別 issue。
- コーススコープ版 `/api/learning/courses/{id}/personal-network/nearby`（正本 API のみで足りる）。
- `concept` / `chunk` / `segment` / `graph_edge` アンカーの中心解決
  （`topic` は §10 範囲モードで解決済み — 2026-08-21）。
- 教材の順序上の前提（`topics[].prerequisites`）との合流（R4）。
- 2階層以上の同時表示・パン／ズーム・グラフ全体表示。
- 教員向けの本ビュー（k-匿名集約の対象にしない。PN-1）。

---

## §9 実装記録（2026-08-18, v1）

### 着地したもの

| 層 | 実装先 |
|---|---|
| 導出（純関数 + DB 経路） | `backend/core/personal_graph/nearby.py`（新規。FastAPI / services / `core.llm` 非 import） |
| DB 読み | `backend/core/personal_graph/queries.py` に `fetch_component_ledger_statuses` / `fetch_center_support_fact_line` を追加（`core.doubt` は遅延 import — 台帳未導入でも動く） |
| API | `GET /api/me/personal-network/nearby`（`routes/personal_map.py` の `me_router`。読み取り専用のまま） |
| フロント | `frontend/public/js/personal-map-home.js` に先頭タブ「いまここの周り」+ SVG 描画 + `.pm-home-nb-*` スタイル（`styles.css`） |
| テスト | `backend/tests/test_personal_map_nearby.py`（47件）+ `test_personal_map_home_ui_static.py` に `TestNearbyTab`（8件） |

### 設計から変えた点（実装で判明したこと）

1. **theory stage の日本語訳は既に存在した**。検討段階では
   「`THEORY_STAGE_LABELS` が英語なので `label_vocab.py` に訳語を追加する必要がある」と
   見積もっていたが、[`core/element_vocab.py`](../../backend/core/element_vocab.py) に
   `THEORY_STAGE_LABELS`（日本語）と `theory_stage_key()`（英語表示名からの逆引き）が
   実装済みだった。**新しい訳語表は作らず**これを使う。
2. **`node_kind` の訳語表を1枚に統合した**。`journey.py` の `_NODE_KIND_JA` を
   `personal_graph/schema.py` の `NODE_KIND_LABELS` へ移し、`journey.py` はそれを参照する
   別名に変更（外部シグネチャ不変）。`nearby.py` も同じ表を引く。
3. **ノードの `description` は返さない**。main ノードの description は step reason 由来で
   内部 ID（`eq_2_7` 等）を含み得るため、学習者向け DTO から除いた（§4 のノード形も修正済み）。
   場所の同定は stage 名 + 本人の痕跡テキストで足りる（main 層は stage ごとに1ノード）。
4. **`refuted` を破線にしない**。破線は「記帳が無い」（`untested` / `unknown`）だけに使う。
   「反証の記帳あり」は記帳があるので実線 + ラベルで示す（記帳の有無と検証の結論を混ぜない）。
5. **台帳行が無いノードは中立の見た目**（`.no-ledger`）にした。実線のままだと「検証済み」に
   見えるため、細い薄枠にして検証について何も主張しない。
6. **開くたびに先頭タブへ戻す**。`open()` が中心・モードも初期化する（表示状態を保存しない）。
7. **SVG チップにキーボード操作を補った**。`<g tabindex role="button">` は `<button>` と違い
   Enter / Space が click にならないため、`keydown` ハンドラを追加した
   （`personal-map.js` のマーカーと同じ流儀）。

### 検証したこと

- backend 全スイート **10,029 passed / 25 skipped**（2026-08-18 時点。近傍関係ビュー分 55 件を含む）、
  `src/tests` **1,811 passed**。回帰なし。
- クライアント経路はスタブ API を挿したハーネスで DOM を実測확인: 既定タブ・中心の選択肢
  （解決できないアンカーの痕跡は選択肢に出ない）・モード切替（`mode=root` で「土台」レーンが
  出る）・中心移動（`center_component_id` 付きで再取得）・枠線クラスの出方
  （`is-center` / `has-mine` / `unverified` / `no-ledger`）・事実文・凡例・既存ノード行の再利用。
- **未実施**: 実 DB / docker 環境での E2E（実際の `theory_component_graphs` と
  `epistemic_ledger` を使った目視確認）。ブラウザでの見た目の確認も未実施。

---

## §10 範囲モード（topic 縮退痕跡の事実ベース粗表示, 2026-08-21）

### 10.1 動機 — 「本人確定」が守っているものと、守っていないもの

v1 の中心解決は「構造帰属が本人確定済みの痕跡」に限られ、普通のチャット質問
（topic 粒度に縮退した N3）は `available:false` になっていた。しかし本人確定が
関門として守っているのは**AIによる帰属推定を本人の記録に昇格させること**であって、
「この質問をこのトピックでした」という事実そのものではない（オーナー裁定 2026-08-21）。
topic には `topics[].linked_claim_ids`（`course_content_builder` がトピック教材生成時に
書き込む決定論マッピング）があるため、**AI推定ゼロ**で「このトピックの教材が触れる
main 層ノード群」まで解決できる。これを**範囲**として見せるのが本モードである。

### 10.2 原理 — 点ではなく範囲を主張する

topic 縮退痕跡について事実として言えるのは「このトピックの教材はこの範囲に触れている」
まで。1点の中心を偽装せず、応答を `mode:"range"` の別形にする（PMN-1 の正直さの延長）。
「質問がその中のどこについてかは、まだ記録されていません」と事実文で言い、精密化の
出口（テキスト選択質問＝経路A / 帰属カード confirm＝経路B）を案内する。

### 10.3 データ経路（全部決定論・既存の読みのみ）

```
topic 痕跡ノード (anchor_type='topic', course_id)
  → queries.fetch_topic_claim_binding(course_id, topic_id)   … linked_claim_ids + topic_label
  → claim ごとに fetch_claim_document_id（document_id ソート・MAX_DOCUMENTS_SCANNED 上限・
    can_view_document フィルタ = PMN-7）
  → fetch_component_graph → main_nodes / qualified_edges（PMN-2 の辺フィルタ不変）
  → touched 判定 = main ノードの linked_claim_ids ∩ トピックの claim 集合
  → fetch_component_ledger_statuses（全 main まとめて1回）+ fetch_document_titles
```

### 10.4 API（既存エンドポイントの応答拡張のみ）

`GET /api/me/personal-network/nearby` は topic アンカーの `node_id` に対し:

- **通常時**: `mode:"range"` — `center:null` / `topic_label` /
  `range_documents: [{title, nodes(+touched), edges}]` / 共通 `facts`。
  支持線の事実文は取得しない（中心が無い）。
- **`center_component_id` 指定時**: コース sources を走査して当該 main ノードを探し、
  従来の**点ビュー**（near/root）を返す（範囲→点は本人の明示ナビゲーションであって
  帰属の主張ではない）。見つからなければ 404（既存の fail-closed と同じ）。
- **縮退**: `linked_claim_ids` 無し / claim が main ノードに突合ゼロ / 閲覧可能 document
  ゼロ → `available:false` + 事実文「このトピックの教材は、まだ理論構成に対応づけられて
  いません。」（エラーにしない・P4）。

### 10.5 偽精度の構造的禁止（本モードの要石）

topic 縮退痕跡を**特定ノードの `mine` に載せない**。`node_matches_anchor` が topic
アンカーに False を返すため、`_mine_for_node` の既存実装のまま構造的に成立する
（新しい除外ロジックを書かない）。topic 痕跡は図の下の「このトピックでの自分の記録」
リスト（既存ノード行の再利用 — 旅・訂正操作つき）にのみ現れる。点アンカー痕跡
（claim 等）は従来どおりノード上の記号として載る（それは事実の精度）。

UI は範囲モードで near/root 切替を出さず（範囲に前後1階層の区別は無い）、
touched でないノードを淡色（`untouched`）にする。凡例は
「濃い枠＝この話題が触れている場所」「淡色＝この論文のその他の理論構成」の事実のみ
（数値・件数なし = PMN-4 不変）。

### 10.6 実装記録（2026-08-21, Fable 指揮 + Sonnet 2体並列）

| 層 | 実装先 |
|---|---|
| DB 読み | `queries.py` に `fetch_topic_claim_binding`（`_claim_topic_map_from_data` と同じ course_data 走査パターン） |
| 導出 | `nearby.py` に `MODE_RANGE` / `RANGE_ANCHOR_TYPES` / `NOTICE_TOPIC_NO_MAPPING` / `FACT_RANGE_*` / `build_topic_range`（純関数）+ `nearby_for_person_node` の topic 分岐 |
| フロント | `personal-map-home.js`: `NEARBY_ANCHOR_TYPES` に topic / `renderNearbyRange`（1ノード=1行 SVG・レーンラベルなし・`untouched` 淡色）/ topic 中心では near/root 切替を非表示 / 「このトピックでの自分の記録」/ `styles.css` に `.pm-home-nb-range-head` 等 |
| テスト | `test_personal_map_nearby.py` に範囲モード群（touched 判定・偽精度禁止・複数 document 決定論順・縮退3種・数値非漏洩・事実文リテラル固定）+ `test_personal_map_home_ui_static.py` に `TestNearbyRangeMode` |

- route（`personal_map.py`）は無変更（mode バリデーションは near/root のまま。
  range は応答専用語彙）。migration 不要・書き込みなし・LLM なしは不変。

---

## §11 縮退是正 — フォールバックの導入と PMN-1 の解釈補足（2026-08-22）

### 11.1 動機 — 縮退が「例外」ではなく「主経路」だった

実データの精査（2026-08-22、実受講者のスクリーンショット）で、範囲モード（§10）が
`_nearby_for_topic_anchor` の3つの early-return（①`topics[].linked_claim_ids` 空
②claim → document 解決ゼロ ③main ノードとの交差ゼロ = 旧 `has_touch` ゲート）で
`NOTICE_TOPIC_NO_MAPPING` の一行に落ち、学習者には**発話チップの列挙 + notice 1行**しか
出ないことが判明した。原因は2つ:

1. **供給側の見積もり誤り**。学習者の痕跡はほぼ全て topic 縮退（普通のチャット質問は全て
   `kind='question'` で記録される）であり、精密アンカー（N2）も `linked_claim_ids`
   （`course_content_builder._enrich_topics` が agent mapping 一致時のみ書く）も実データでは
   稀。縮退を「まれな例外」として設計したが、実際には常態だった。
2. **「偽精度の禁止」を「表示しない」に倒しすぎた**。原則が要求しているのは「粗さを正直に
   ラベルすること」であって「粗ければ何も見せないこと」ではない。

### 11.2 PMN-1 の解釈補足（本節をもって正本とする）

> **粗い対応は、隠すのではなく、粗いとラベルして見せる。** PMN-1（位置に意味の無い配置を
> しない）と KN-3（推測エッジを作らない）が禁じているのは「推測を事実の顔で見せること」で
> あり、「事実を粗い粒度のまま見せること」ではない。touched を推測で立てない限り、コース
> sources の理論構成をコース粒度で見せることは事実の提示である。

### 11.3 是正内容

- **A. コース範囲フォールバック**: ①②で claim 経路が全滅したとき、
  `_documents_for_anchor`（コース sources・決定論順・`MAX_DOCUMENTS_SCANNED` 上限・
  `can_view_document` fail-closed）で解析済み論文の main バックボーンを
  `touched_claim_ids=set()` の範囲として表示する。facts の先頭に
  `FACT_RANGE_COURSE_FALLBACK`（「このトピックと論文の対応はまだ記録されていません。
  かわりに、このコースのソース論文の理論構成を表示しています。」）。フォールバック時は
  `FACT_RANGE_UNKNOWN_POINT` を出さない（対応が存在するかのように読めるため）。
  DTO に `range_fallback: bool` を追加し、UI は見出しを「この話題が触れている範囲」から
  「コースのソース論文の理論構成」へ、凡例から「触れている場所」の項を落とす
  （**見出し・凡例が事実文より多くを主張しない**）。
- **B. `has_touch` ゲートの撤廃**: claim が document に解決すればグラフを表示し、
  `touched` は実際に交差したノードにだけ点く（交差ゼロ＝マーク無しで表示）。このとき
  `FACT_RANGE_COURSE_FALLBACK` は出さない（claim 解決済みはより特定的な事実であり、
  フォールバック文を混ぜると出所が不正直になる）。`NOTICE_TOPIC_NO_MAPPING` に落ちるのは
  「フォールバックまで試してもグラフのある閲覧可能な document がゼロ」の場合のみ。
- **C. topic チップの題名表示**: `derive.py` の topic 縮退分岐（`_topic_anchor` に集約）で
  `anchor_label` にトピック題名を入れる（`queries.fetch_topic_labels{,_for_courses}` —
  `iter_all_topics` 経由・章ネスト対応・題名が引けなければ従来どおり空 = 捏造しない）。
  中心選択チップは `anchor_label` 優先の既存ロジックのまま題名表示になり、発話の生テキスト
  （「うん、塗ってみる。」等）は入口から消える（痕跡自体は P4 で保持、チップ選択後の
  「自分の記録」欄には従来どおり出る）。`build_network` / `build_person_network` は kw-only
  引数（既定 None）で完全後方互換。N2 の anchor_label は上書きしない。
- **D. unavailable に出口案内**: `unavailable(mode, notice, facts=())` に facts を追加し、
  全呼び出しで `FACT_RANGE_SHARPEN`（精密化の出口）を渡す。UI は unavailable 分岐でも
  facts を「この記録について」見出しで描画する（欠落を行き止まりにしない）。
- 空列挙の是正: 「触れている理論構成：」の事実文は列挙が非空のときだけ出す。

### 11.4 実装記録（2026-08-22, Fable 指揮 + Opus 2体並列）

| 層 | 実装先 |
|---|---|
| 導出 | `nearby.py`: `FACT_RANGE_COURSE_FALLBACK` / `_nearby_for_topic_anchor` の3 early-return 撤廃とフォールバック / `build_topic_range(fallback_fact=)` + `range_fallback` キー / `unavailable(facts=)` |
| DB 読み | `queries.py`: `_topic_labels_from_data`（純関数）+ `fetch_topic_labels` / `fetch_topic_labels_for_courses` |
| 導出（C） | `derive.py`: `_topic_anchor` ヘルパー新設・`build_network(topic_labels=)` / `build_person_network(topic_labels_by_course=)`（既定 None・後方互換） |
| フロント | `personal-map-home.js`: unavailable 分岐の facts 描画（見出し「この記録について」）/ フォールバック時の見出し・凡例切替 |
| テスト | `test_personal_map_nearby.py`（フォールバック群・unavailable 形・空列挙抑制・`range_fallback`）+ `test_personal_graph_derive.py` / `test_personal_graph_person_scope.py`（題名付与・非捏造・N2 非上書き・コース間非混入） |

- 検証: backend 全スイート 10,222 passed / 25 skipped（唯一の fail はユーザー WIP 由来の
  既知の先行 fail `test_learner_ux_static` で本変更と無関係）。スタブ API ハーネスで DOM 実測
  （チップの題名表示・フォールバック範囲の描画・unavailable の出口案内・見出し/凡例の切替）。
  実 DB / docker E2E は未実施。
- 供給側の根治（G層 To-Do `course.topics_unlinked` — 全トピックで結線が空のコースを教員に
  提示しコース内容生成の再実行を促す）は**別 issue** として切り出し済み。
