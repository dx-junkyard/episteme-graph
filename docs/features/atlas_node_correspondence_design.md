# 分野マップのノード版間対応（Atlas Node Correspondence — 地図を改訂しても論文の位置づけが切れないようにする）

> **状態: 実装済み（正本・凍結）**（2026-09-13 起票・同日実装。migration なし。実装記録は §11。以後は §11 の追記のみ）。
> 親文書は [知識構造の見直し提案 2026-09-12](../architecture/knowledge_structure_review_2026-09-12.md) の D4 / 付属調査
> [E 概念同一性](../architecture/knowledge_structure_review_2026-09-12/E_concepts.md) **K-6**。
> 前提は [分野マップの該当なし UX + ドメインライフサイクル](atlas_binding_lifecycle_design.md) §4.4（`freeze-impact`）と
> [知識ランドスケープ](knowledge_landscape_design.md)（LS1〜LS10）、[概念レジストリ](concept_registry_design.md)（KR9 版非依存キー）。

**オーナー判断（本書の前提・2026-09-13・推奨案を採用）**

| # | 判断 | 採用 |
|---|---|---|
| NC-O1 | 版間の対応表を持つか | **持つ**。持たない限り、地図を改訂するたびに配置の確認をやり直すことになる |
| NC-O2 | 確定の置き場所 | **凍結前の影響確認（`freeze-impact`）に候補を並べ、教員が確認してから凍結する**。凍結後の別画面で直すより判断の文脈が揃う |
| NC-O3 | 対応が付かないノードの扱い | **旧版の配置行はそのまま残し、現行版では「前の版で確認済み・現行版に対応ノードなし」の事実文で見せる**。自動で近いノードに付け替えない・消さない |

---

## 1. 問題（K-6）

分野の地図（atlas 骨格）は改訂のたびに版を凍結するが、実データでは版どうしのノード ID の重なりが **0**（modified_gravity 2 版・49 ノード。他 2 分野も同様）。
改訂が「前の版の編集」ではなく「AI が下書きを生成し直す」流れなので、同じ概念でも ID が振り直される。

論文の配置（`landscape_placements`）と アンカーベクトル（`atlas_anchor_embeddings`）は **`skeleton_version` 付きの node_id** を指すため、新版から
見ると教員が確認済みにした位置づけが消えたように見える。「地図を直すと積み上げたものが消える」ので改訂が抑止される。一方、別名レジストリ・
ギャップ判断・辺判断・Phase 3 のレジストリ ↔ node リンクは版非依存キーで設計されており、**配置だけが版に縛られている**非対称が正体。

骨格の内容には既に `id_migrations`（`{from, to, version}`・§16-4「改版時の concept id 移行規則」）というスロットがあり、修正報告
（`atlas_reports`）の付け替えに使われている。**本層はこの既存スロットを対応表の格納庫にする**（新表を作らない）。

## 2. 不変条項（NC1〜NC8）

| # | 条項 | 具体 |
|---|---|---|
| NC1 | **AI が `atlas_skeletons` に書かない**（LS7 / AB4 継承） | 候補は読み時導出。骨格へ書き込むのは教員の凍結操作（既存 `freeze` API の body）だけ。凍結される内容の `id_migrations` に教員が確定した対応だけを載せる |
| NC2 | **候補は決定論・非LLM・embedding 0 回** | 旧版ノードと draft ノードの `normalize_label` 完全一致・教員確定別名（`atlas_anchor_aliases`）一致・Phase 3 レジストリ経由（同じ entry に confirmed で結ばれている）の 3 経路のみ。cosine は使わない（draft にベクトルは無く、埋め込みを増やさない） |
| NC3 | **確定は人間・一括確定は `decision_context`** | 候補は既定で「選択済み」に見せない。教員が凍結画面で確認した対応だけが body の `id_migrations` になり、凍結の review event に `presented`（候補）/ `applied`（確定）を DC1〜DC4 で記帳する。候補ゼロなら記帳しない |
| NC4 | **情報を落とさない** | 旧版の配置行・ベクトル行・確認結果は不変。対応の「取り消し」は次版の凍結で別の対応を載せることで表す（行削除・上書きなし。骨格の版は不変） |
| NC5 | **自動付け替えをしない** | 読み手は対応表を**読み替え**に使うだけで、`landscape_placements.node_id` を UPDATE しない。対応が付かない配置は現行版では位置を持たず、事実文で存在だけを示す |
| NC6 | **数値非表示・閉世界** | 一致件数・cosine・対応率を出さない。事実文は「前の版（版 X）の地図で確認された位置づけで、現行版（版 Y）に対応する場所がありません」の形 |
| NC7 | **1 旧ノード → 1 新ノード** | 旧ノード 1 つが複数の新ノードへ割れる対応（split）は v1 では受け付けない（`from` は一意）。複数の旧ノードが 1 つの新ノードへ束なる対応（merge）は可 |
| NC8 | **版の連鎖で解く** | 現行版から見た旧 node_id の解決は、その分野の**全凍結版**の `id_migrations` を版順に辿る（1 版飛ばして改訂されても切れない）。読みは `atlas_store.load_frozen_history` の 1 経路 |

## 3. 全体像

```
[draft レビュー]  GET .../atlas/freeze-impact
   → removed_node_ids × draft の node から候補を導出（normalize_label / 別名 / レジストリ経由・決定論）
   → correspondence: {candidates:[{from_id, from_label, to_id, to_label, justification}], unmatched_removed:[...], facts:[...]}
[凍結画面]  教員が候補を確認・手で追加（旧 → 新）・外す
[POST .../atlas/skeleton/freeze  body.id_migrations=[{from,to}]]
   → サーバ検証（from ∈ 現行凍結版の node、to ∈ draft の node、from 一意）
   → frozen.id_migrations に {from, to, version=新版} を追加して insert_frozen
   → review event（AUDIT_ENTITY_ATLAS_SKELETON・action node_correspondence）に decision_context
[読み手]  NodeResolver（全凍結版の id_migrations を連鎖）で旧 node_id → 現行 node_id
   landscape 配置（教員 / 学習者 / コース確認）・論文の海・Phase 3 node リンク → current_node_id / 未対応の事実文
```

## 4. コア（`backend/core/atlas_correspondence.py`・新設・FastAPI / LLM 非 import）

- `derive_correspondence_candidates(*, frozen, draft, confirmed_aliases_by_node, registry_links=None) -> dict`
  - `removed = frozen の node ids − draft の node ids`、`added = draft − frozen`（`compute_freeze_impact` と同じ集合）。
  - 経路①: `normalize_label(旧 label) == normalize_label(新 label)`（region / concept ともに・kind が一致するもの同士）→ `lexical_match`。
  - 経路②: 旧ノードの教員確定別名（`atlas_vectors.store.confirmed_aliases_by_node`）の正規形が新ノードの label と一致 → `lexical_match`（`via="alias"`）。
  - 経路③: Phase 3 の `library_atlas_node_links`（confirmed）で旧ノードと新ノードが**同じ entry**に結ばれている → `manual_curation`（人間確定のリンクの合成）。
    v1 では draft ノードにリンクは付かないので実質「旧版 → 旧々版」の再確認にしか効かないが、経路として宣言する（将来 draft へのリンクが付いたとき用）。
  - 同じ `from` に複数候補が立てば ① > ③ > ② の順で 1 つを採り、他は `alternatives` に残す（NC4）。`to` の重複（merge）は許す。
  - 戻り値に `facts`（「候補は正規化した名前の一致だけから作っています。近さや意味の推定はしていません。」等の事実文・数値なし）と
    `unmatched_removed`（候補の無い旧ノード。ラベル列挙）。
- `NodeResolver`（純データ）: `build_node_resolver(frozen_history: Sequence[AtlasSkeleton]) -> NodeResolver`。
  `resolve(node_id) -> {"status": "current" | "migrated" | "unmapped", "current_node_id": str | None, "via": [str], "last_version": str}`。
  版順（`changelog` の順 = `created_at` 順）に `id_migrations` を辿り、現行版の node ids に到達したら `current`/`migrated`。
  循環・欠落は `unmapped`（例外を出さない）。
- 事実文の正本: `FACT_UNMAPPED_PLACEMENT = "前の版（版 {old}）の地図で確認された位置づけで、現行版（版 {new}）に対応する場所がありません。"` /
  `FACT_MIGRATED = "版 {old} のノード「{from_label}」は版 {new} の「{to_label}」に対応づけられています。"`（教員向け詳細のみ。学習者には
  前者だけ）。
- `validate_migrations(body_pairs, *, frozen, draft) -> list[IdMigration]`（`from` が現行凍結版に無い / `to` が draft に無い / `from` 重複 →
  `ValueError` → route 422 事実文）。

## 5. API 変更（`routes/atlas.py`・既存 2 本の additive 拡張）

- `GET /api/admin/cartridges/{id}/atlas/freeze-impact` → 既存キーに **`correspondence`** を追加
  （`{candidates, unmatched_removed, already_declared, facts}`。`already_declared` = draft 自身の `id_migrations` に既にある対応）。
- `POST /api/admin/cartridges/{id}/atlas/skeleton/freeze` → `FreezeSkeletonRequest.id_migrations: list[{from,to}] = []`（additive・未指定は従来どおり）。
  検証は §4 の `validate_migrations`。`atlas.freeze_skeleton` の戻りに `id_migrations` を **追加**（既存の draft 由来分は保持）してから
  `insert_frozen`。既存の `stamp_applied_versions` / 修正報告の付け替え（`atlas_reports.carry_over...(id_migrations=frozen.id_migrations)`）は
  そのまま新しい対応も使う（既存機構が自然に効く）。
- 監査: 候補提示が 1 件以上あるときだけ、既存の凍結 review event と**別に** `AUDIT_ENTITY_ATLAS_SKELETON` / `action="node_correspondence"` の
  1 行を `decision_context`（新 basis `BASIS_ATLAS_NODE_CORRESPONDENCE = "atlas_skeleton.node_correspondence"`・`presented` = 候補の
  `from|to` キー・`applied` = 確定した対を同形・alternatives = `ALT_EDIT` / `ALT_SKIP_STEP`）付きで記帳する（DC1〜DC4。候補ゼロなら記帳しない）。
- レスポンス `impact.correspondence.applied` に確定した対応（ラベル付き）を載せる。
- `atlas_store.load_frozen_history(session, domain_key) -> list[AtlasSkeleton]`（新設・`created_at ASC, version ASC`・壊れた行はスキップ）。

## 6. 読み手の読み替え（`node_id` を UPDATE しない・NC5）

| 読み手 | 変更 |
|---|---|
| `routes/landscape.py`（教員: document / course の配置一覧・学習者: `learner_landscape_for_documents`・overview） | `_load_skeletons` の隣で `NodeResolver` を作り、各配置 DTO に `current_node_id` / `node_status ∈ {current, migrated, unmapped}` を additive 追加。`node_label` は**現行版のラベル**（migrated なら to 側）。unmapped は学習者向けでは位置に置かず、domain DTO の `facts` に `FACT_UNMAPPED_PLACEMENT`（版番号入り）を 1 行。教員向けは行を残し `node_status` チップ |
| `frontend/public/js/landscape-layer.js`（学習者オーバーレイ） | 位置解決を `current_node_id || node_id` に。`facts` の描画は既存の事実行の仕組みに乗せる |
| `core/corpus_view.py`（論文の海） | 「現行骨格に無い node_id は落とす」を「resolver で読み替え → それでも無ければ落とす」に。落とした事実は既存の縁の事実文の作法で 1 行 |
| `core/library/registry.py::annotate_node_links`（Phase 3） | `node_in_current_version` の判定を resolver 経由にし `current_node_id` を付ける |
| コース binding（`topics[].atlas_node_id`） | **v1 非改変**。既存 G層 `course.atlas_binding_stale` が事実を出し、教員が propose / 保存で直す（自動付け替えをしない NC5 と整合） |
| アンカーベクトル | 非改変（凍結後の best-effort 再構築で新版分が作られる。旧版の行は残る） |

## 7. UI（`admin.js` 分野の地図タブ・ES5）

- 凍結ボタン → `freeze-impact` 取得 → 既存の `confirm()` を**小さなモーダル**に置き換える: 上段に既存の事実文（removed / affected / facts）、中段に
  「前の版のノードとの対応」区画: 候補行 = `旧ラベル → 新ラベル（根拠: 名前の一致 / 別名 / 登録概念）` + チェックボックス（**既定オフ** —
  NC3「選択済みに見せない」）、`unmatched_removed` の行 = 旧ラベル + 「対応先」select（draft の追加ノード）で手動対応、下段に
  「この対応で凍結」。件数を描かない。
- アンカー（3 点セット）: `atlas.freeze-correspondence`（区画）/ `atlas.freeze-correspondence-manual`（手動対応の select）。マニュアルは
  `docs/manual/teacher/17-admin-atlas.md` に節。学習者側は新しい操作要素なし（事実文のみ）。
- 教材管理の landscape レビューモーダル（`admin.js`）と コース確認ウィザード（`admin-release-review.js`）の配置行に `node_status` チップ
  （migrated: 「版 X → 版 Y」/ unmapped: 事実文）。

## 8. 権限・監査・数値

- freeze-impact / freeze は既存どおり TEACHER・retired は 409。学習者 API は既存の受講ゲートのまま。
- 監査は既存 `AUDIT_ENTITY_ATLAS_SKELETON`（新 entity_type なし）。`decision_context` の basis 定数を 1 本追加。
- 一致件数・対応率・cosine を出さない。`impact.removed_node_ids` 等の既存キーは不変。

## 9. ガードレール

`test_atlas_node_correspondence_{core,api,readthrough,ui_static}.py`: core が fastapi / `core.llm` / embedding 生成を import しない・
`atlas_skeletons` への INSERT/UPDATE が `atlas_store` 以外に無い・`landscape_placements` の `node_id` を UPDATE する文が無い・候補が
`lexical_match` / `manual_curation` 以外の justification を持たない・`from` 重複と骨格外 ID で 422・候補ゼロで decision_context を記帳しない・
UI のチェックボックス既定オフ・数値語の不在・学習者 DTO に `via` / 旧 node_id を出さない（現行 node_id と事実文だけ）。

## 10. 非スコープ（v1）

1 旧ノード → 複数新ノードの split / 凍結後の対応の追加・訂正 UI（次版の凍結で載せる）/ cosine による候補（draft を埋め込まない）/
コース binding の自動読み替え / アンカーベクトルの版間継承 / 対応表の学習者向け表示（学習者には事実文のみ）。

## 11. 実装記録

### 11.1 2026-09-13 — v1（Fable 5.1 指揮・Opus 5 の 2 担当）

体制: 指揮者が本書（NC1〜NC8・§5 の API 契約）を先に固定し、P（core・API・読み手の読み替え）と Q（凍結モーダル・配置行のチップ・
学習者オーバーレイ・マニュアル・docs 索引）を並列実施。migration なし・LLM 0 回・embedding 0 回。

| 項 | 実装先 |
|---|---|
| 候補導出 / `NodeResolver` / `validate_migrations` / 事実文の正本 | `backend/core/atlas_correspondence.py`（FastAPI・sqlalchemy・`core.llm` 非 import・SQL ゼロ） |
| 全凍結版の読み | `atlas_store.load_frozen_history(session, domain_key)`（`created_at ASC, version ASC`・壊れた行はその行だけスキップ） |
| `freeze-impact` の `correspondence` / freeze body `id_migrations` / 422 / `decision_context` | `routes/atlas.py`（`IdMigrationIn`・`_correspondence_preview`）・`decision_context.BASIS_ATLAS_NODE_CORRESPONDENCE` |
| 読み替え | `core/landscape/projection.py`（`NodeResolve`・教員 / 学習者 DTO の `current_node_id` / `node_status`・学習者 domain DTO の `facts`）・`routes/landscape.py`（document / PATCH / course / 学習者 / overview）・`core/corpus_view.py`（読み替え後 `anchor_node_id` + `facts`）・`core/library/registry.py::annotate_node_links(resolve=)` |
| UI | `admin.js`（`confirm()` → `#atlas-freeze-impact-modal`。候補チェック**既定オフ**・手動対応 select・`openFreezeChecklist()` はゼロ引数維持・landscape モーダルの `node_status` チップ）/ `admin-release-review.js`（ステップ 2 のチップ）/ `landscape-layer.js`（`current_node_id || node_id`・unmapped は置かない・`domains[].facts` を既存の事実行へ） |
| アンカー・マニュアル | `atlas.freeze-correspondence` / `atlas.freeze-correspondence-manual`（総数の正本は `test_admin_help_ui_anchors.py`）+ `docs/manual/teacher/17-admin-atlas.md` |
| docs | `docs/README.md` / `layer_registry.md`（S層・知識ランドスケープ行に追補併記）/ `backend/api.md` / `features/learning.md` / CLAUDE.md「分野マップのノード版間対応」節 |

**実際の DTO**（§5 からの additive 差分を含む）:

- `correspondence = {candidates:[{from_id, from_label, from_kind, to_id, to_label, to_kind, via:"label"|"alias"|"registry", justification}],
  alternatives:[同形], unmatched_removed:[{node_id, label, kind}], already_declared:[{from, to, from_label, to_label, version}],
  added_nodes:[{node_id, label, kind}], facts:[str]}`。`added_nodes` は手動対応 select の選択肢を draft 無しの画面からも作るために追加。
- freeze body `id_migrations: [{from, to}]`（`from_id` / `to_id` も受ける）。422 は文字列の事実文で、検証は `freeze_skeleton` の**前**（版も draft も動かない）。
  レスポンス `impact.correspondence.applied: [{from_id, from_label, to_id, to_label}]`。
- 配置 DTO: 教員は `node_id`（行のまま）+ `current_node_id` + `node_status`、`node_label` は現行版。学習者は `node_id` = 読み替え後（旧 id を出さない）+
  `current_node_id` + `node_status`、unmapped は位置に置かず domain DTO の `facts`（常在・無ければ `[]`）。overview は `node_status` のみ
  （グルーピングキーが現行 node なので `current_node_id` は省略）。`NodeResolver.resolve` の `via`（辿った node 列）はどの DTO にも出さない。

**実装上の判断（設計書との差分）**:

| # | 判断 | 理由 |
|---|---|---|
| 1 | `last_version` = その node が実在した**最後の凍結版** | `FACT_UNMAPPED_PLACEMENT` の `{old}` を推測せず事実から出す。旧版が特定できないときは版数なしの `FACT_UNMAPPED_PLACEMENT_NO_OLD_VERSION` |
| 2 | 同一 `from` が複数版で再宣言されていたら**古い版の宣言が勝つ** | id が消えた時点の対応が正。鎖を切らない |
| 3 | 別名 / レジストリの照会失敗時に `session.rollback()` | 実 Postgres では失敗文がトランザクションを中断し後続の読みを巻き添えにする。全経路読み取り専用。migration 082 未適用環境でも freeze が落ちない |
| 4 | `validate_migrations(..., version=)` の任意キーワード | `IdMigration.version` はサーバが新版で刻印する |
| 5 | Q: 凍結モーダルは既存 `openFreezeChecklist()` のゼロ引数を維持し、確定対はクロージャ変数で body へ | 既存 `test_atlas_binding_ux_static` の構造アサートを壊さない |

検証: backend 15,515 pass / src 1,924 pass（2026-09-13・赤ゼロ）。実 DB での凍結 → 読み替えの E2E は docker 復帰後。
