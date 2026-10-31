# 概念レジストリ（Concept Registry — `library_entries` を軸に、概念を SKOS 語彙で「リンク」する層）

> **状態: 実装済み（正本・凍結）**（2026-09-13 起票・同日実装。migration **082** `concept_registry`。実装記録は §13。以後は §13 の追記のみ）。
> 親文書は [知識構造の見直し提案 2026-09-12](../architecture/knowledge_structure_review_2026-09-12.md)
> の **Phase 3**（§4 P3-1〜P3-7）。前提は Phase 1 = [知識オブジェクト層](knowledge_objects_design.md)（KO1〜KO10）と
> Phase 2 = [学ぶ単位の一級化](learning_units_design.md)（LU1〜LU9）。判断材料は付属調査
> [E 概念同一性](../architecture/knowledge_structure_review_2026-09-12/E_concepts.md)（K-1〜K-12・⑦）。

**オーナー判断（本書の前提）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| O-4 | 概念レジストリの軸 | **(b) `library_entries` を拡張**（atlas 骨格は「座標系」として残し、レジストリ ↔ node は版非依存のリンク表で結ぶ） | 親文書 §6 の推奨。atlas は座標系で語彙ではなく AI が書けない（LS7 / AB4）。(c) 新設は 11 系統目で診断（分裂）を悪化させる。(b) は SKOS と一対一で「確定は人間・行削除なし・candidate 始まり」を既に実装済み（E ⑦）。オーナーの着手指示（2026-09-13「Phase 3 を実施せよ」）を推奨案の採用と解し、冒頭に固定する |
| — | 「概念の統合」の意味 | **リンクであってマージではない**（KN-2 / 原則7） | `exact_match` は 2 つの行を並存させたまま「同じと言える」を記録する。`owl:sameAs` 的な統合・`name` の書き換え・行削除は作らない |
| — | AI が概念を「作る」か | **candidate 行までは作る・確定は教員**（KN-3 / 原則1） | 候補は `library_entries.review_status='candidate'` の行として置く。凍結（= パイプラインから可視）は `confirmed` のみ可（409）。したがって AI 由来の概念が学習者・パイプラインに届く経路は教員確定を必ず通る |
| **O-6** | L層の不変条項「**昇格は人間の操作のみ**」（`image_pipeline_knowledge_library_design.md` §6 の条項 2）の読み替え | **オーナー裁定待ち。裁定まで実装は現状を維持** | 本層は同条項を「**candidate 行はパイプラインが作る・可視化（凍結）は人間のみ**」と読み替えて実装している（P3-R1）。L層の原文は「LLM がライブラリへ直接書き込む経路を作らない」で、行の作成そのものを禁じているようにも読める。**可視化の弁は凍結の 409 ゲート**（`review_status='confirmed'` でなければ凍結できず、候補行はパイプライン retrieval にも学習者にも届かない）。裁定で読み替えが否とされた場合は、候補を `library_entries` の行にせず別表に置く設計へ差し替える（影響は `atlas_links` / `identity_candidates` の保存先のみ） |

**正本**: 本ドキュメント。**関連**: [画像パイプライン + ナレッジライブラリ（L層）](image_pipeline_knowledge_library_design.md) §6 /
[知識ネットワークビジョン](knowledge_network_vision.md)（KN-1〜4）/ [要素検討ワークスペース（W層）](element_deliberation_workspace_design.md) §5.5 /
[分野マップのベクトル係留（VA層）](atlas_vector_anchoring_design.md) / [分野マップの関係表示（RE追補）](atlas_relation_edges_design.md) /
[カテゴリギャップ候補](category_gap_candidates_design.md) / [知識ランドスケープ](knowledge_landscape_design.md) /
[候補→確定の共通制御](candidate_flow_design.md) / [段階ラベル・共有語彙表](label_vocab_design.md) /
[確定文脈の記帳](decision_context_design.md)。

---

## 1. 目的 — 概念の同一性が無く、10 系統が FK 0 本で並んでいる

親文書 D4 の診断（E 節）:

- 概念を保持する系統が 10（cartridge ontology / atlas 骨格 / anchor aliases / library_entries / theory_components / symbol_registry / dsl ノード / course concepts / chunks.variables / keyphrase）。系統間 FK は 0 本（E ②）。
- 型語彙が 5 セット（Phase 1 で `core/schema.py` に正本化・DB は語彙表 FK。**concept 層の受け皿**はまだ無い — K-1 の残り）。
- claim の `concepts` は全件 LaTeX 記号（K-2）。論文横断の唯一の一致が alias 部分一致の誤爆（K-3 は Phase 0 で是正）。
- L層 `library_entries` は Phase 0 で初版凍結され可視になったが、**同一性候補を作る経路が無い**（K-5・`duplicate_candidates` は常に `[]`）。identity links 0 / aliases 0（C-12）。
- atlas node_id は版ごとに総取り替え（K-6）で、ドメイン跨ぎの同一概念（`large_scale_structure` が 2 ドメインに同 node_id・cos 0.89）に結合手段が無い（K-7）。
- SKOS 相当の語彙（`altLabel` / `hidden` / `exactMatch` / `closeMatch` / `broader`）がどこにも揃っていない（K-12）。
- 「なぜ同じと言えたか」（mapping_justification）を記録する列がどこにも無い。却下の反復判断（同じ候補を毎回見送る）が止まらない。
- 記号（symbol_registry）は Phase 1 で `knowledge_symbols` 行になったが、**学習者に届く読み手が無い**（F-10）。
- cartridge `particle_physics` は実質フレーバー物理用で、名前と実内容が乖離（F-8）。分野との適合は入口で提示されない。

本層は **`library_entries` を概念レジストリに拡張**し、①型語彙の受け皿（entry_type）②SKOS の label 3 種 + 関係 4 種
③mapping_justification ④レジストリ ↔ atlas node の版非依存リンク ⑤記号 → 概念の参照と学習者向け「直前の定義」
⑥同一性候補の自動生成（決定論）⑦cartridge の「形の宣言」と適合事実、の 7 件を **A層非改変・LLM 0 回・確定は人間**で積む。

## 2. 不変条項（KR1〜KR10）

| # | 条項 | 具体 |
|---|---|---|
| KR1 | **A層非改変** | `src/episteme_graph/agents/` のコード・dataclass を触らない。P3-5 の「`SymbolRecord` に `concept_ref`」は agent 側ではなく**読み時の join**（確定済み同一性リンク → DTO の `concept_ref`）で実現する（§7）。cartridge の形の宣言は backend 側の読み手（`core/cartridge_shape.py`）が読む |
| KR2 | **確定は人間・AI は candidate まで** | 概念の生成・別名・関係・node リンク・同一性リンクはすべて `candidate` 始まり。`library_entries.review_status='candidate'` の行は**凍結できない（409）**ので、パイプラインの retrieval（凍結版のみ）・学習者・keyphrase 供給には届かない。`atlas_skeletons` への書き込み経路は増やさない（LS7 / AB4） |
| KR3 | **リンクであってマージではない** | `exact_match` / `close_match` は 2 行を並存させる関係の記録。`name` の書き換え・行の統合・削除は作らない。論文側の局所表現（`local_expression`）は識別側に残す（KN-2） |
| KR4 | **mapping_justification 必須** | 候補・確定の書き込みは `mapping_justification`（§5 語彙）を伴う。未指定は `ValueError`（route は 422）。既存行は NULL = 「記録なし」で正直に残す（推測で埋めない） |
| KR5 | **決定論・非LLM・embedding 呼び出しゼロ** | 候補導出は正規化ラベル一致（`atlas_gaps.schema.normalize_label` 正本）・**保存済み**ベクトル（`atlas_anchor_embeddings` / `library_entry_versions.embedding` / `chunks.embedding`）の cosine・配置共起だけ。本層のコードは `core.llm` を import しない（発見層の allowlist を増やさない） |
| KR6 | **数値を見せない** | cosine・一致件数・候補数を教員にも出さない。段階ラベル（`label_vocab.ANCHOR_NEARNESS_SCALE`）か名前の列挙、または「一致した / 近い」の事実文のみ。`confidence` は DB のみ |
| KR7 | **情報を落とさない** | 行削除 API なし。却下は `dismissed` 遷移・見送りは理由必須。`hidden` ラベルは OCR ノイズ・旧表記を**捨てずに検索から隠す**ための器 |
| KR8 | **閉世界の正直さ** | 「この概念は他の論文に無い」と言わない。言えるのは「このコーパスの中では…」だけ（SL1）。形の宣言の適合は「この論文の解析で、この分野の地図に配置があった / 無かった」の事実文 |
| KR9 | **版非依存キー** | レジストリ ↔ atlas node のリンクは `(entry_id, domain_key, node_id)`（`skeleton_version` を持たない）。`atlas_anchor_aliases` / `cluster_key` / `edge_key` の 3 前例に揃える。凍結で node_id が変わった場合はリンクの `node_id` が現行版に無い事実を読み時に付ける（`node_in_current_version: false`）だけで、行は消さない |
| KR10 | **権限 fail-closed** | エントリ・ラベル・関係・node リンクは TEACHER 以上（L層と同じ）。同一性候補の一覧は閲覧不可 document 由来を除外し `hidden_count` を正直に返す（W-β と同じ）。学習者向けは記号の「直前の定義」1 本のみで、コース sources 由来の document に `ANY(:doc_ids)` で強制する |

## 3. 全体像

```
theory_components_live / knowledge_symbols_live / learning_units_live（Phase 1・2）
  ↓ 決定論の候補導出（正規化ラベル一致・保存済みベクトル・配置共起。LLM / embedding 0 回）   ← P3-6 / P3-4
library_entries（review_status: candidate → confirmed / dismissed。entry_type は語彙表 FK）    ← P3-1
  ├ library_entry_labels（preferred = name を正本とし、alternate / hidden を行に）              ← P3-2
  ├ library_entry_relations（broader / related / exact_match / close_match。ドメイン跨ぎ可）    ← P3-2
  ├ library_atlas_node_links（entry ↔ (domain_key, node_id)。版非依存・exact/close）           ← P3-4
  └ element_identity_links（instance ↔ entry。'symbol' を instance 型に追加）                   ← P3-5 / P3-6
       ↑ すべての候補・確定に mapping_justification                                            ← P3-3
学習者: 記号タップ → 直前の定義（knowledge_symbols_live）+ concept_ref（confirmed link のみ）    ← P3-5
教員: ナレッジライブラリタブに「同一性の候補」「別名」「関係」「地図の対応」区画                   ← UI
cartridge: shape.json（形の宣言）→ 再解析モーダルに「分野の適合」事実文                          ← P3-7
```

## 4. DB 設計（migration 1 本・採番は `ls backend/db/` で確認 → **082**）

Phase 1 の作法（語彙表 FK・部分 UNIQUE・`DELETE FROM` なし・末尾で live ビュー再作成）を継承する。

### 4.1 語彙表（KO7 と同型・コードと同じ列挙を `ON CONFLICT DO NOTHING` でシード）

| 表 | 列挙の正本 | 値 |
|---|---|---|
| `knowledge_entry_types` | `core/schema.py::LIBRARY_ENTRY_TYPES` | `apparatus` / `theory_component`（既存）+ `concept` / `theory` / `method` / `observable` / `assumption` / `quantity` / `process` |
| `knowledge_label_kinds` | `core/schema.py::CONCEPT_LABEL_KINDS` | `preferred` / `alternate` / `hidden` |
| `knowledge_relation_kinds` | `core/schema.py::CONCEPT_RELATION_KINDS` | `broader` / `related` / `exact_match` / `close_match` |
| `knowledge_mapping_justifications` | `core/schema.py::MAPPING_JUSTIFICATIONS` | `manual_curation` / `lexical_match` / `vector_similarity` / `cartridge_declared` / `corpus_cooccurrence` / `llm_candidate` |

`core/library/schema.py` の `ENTRY_TYPES` は `core/schema.py` からの再エクスポートに置き換える（二重定義しない）。
entry_type と既存型語彙の**決定論写像**は `core/library/schema.py::entry_type_for_component_type()`
（`COMPONENT_TYPES` → entry_type。`Domain*Component` はその語幹、`Paper*Component` / `law` / `mechanism` / `unknown` は `concept`、
`apparatus` / `instrument` / `part` は `apparatus`、`theory` / `DomainTheoryComponent` は `theory`、
`operator` / `Parameter` 相当は `quantity`、`observation` / `DomainObservableComponent` は `observable`、
`DomainMethodComponent` は `method`、`DomainAssumptionComponent` は `assumption`）。**分野語を書かない**。

### 4.2 `library_entries` の変更（既存行不変）

- `entry_type` の CHECK を落とし `REFERENCES knowledge_entry_types(entry_type)` に（既存値は語彙表に含む）。
- `review_status TEXT NOT NULL DEFAULT 'confirmed'` を追加（語彙 `core/schema.py::CONCEPT_REVIEW_STATUSES` = `candidate` / `confirmed` / `dismissed`。CHECK で可）。
  既存行 = 人間が作った行なので DEFAULT `confirmed` で意味不変。**`dismissed` は `status='retired'` とは別軸**（retired = 教員が公開を止めた確定済み概念 / dismissed = 候補を見送った）。
- `review_note TEXT NOT NULL DEFAULT ''`（見送り理由・必須は store が強制）/ `mapping_justification TEXT REFERENCES knowledge_mapping_justifications` NULL 可（候補生成が書く。手動作成は `manual_curation`）/
  `candidate_key TEXT`（`cand|{domain_key}|{normalize_label(name)}`。候補の再提案を同一行に畳む。部分 UNIQUE `WHERE candidate_key IS NOT NULL`）/
  `decided_by UUID` / `decided_at TIMESTAMPTZ`。
- `freeze_entry` は `review_status <> 'confirmed'` を `LibraryConflictError`（route 409）で拒否する（KR2）。
- `UPDATABLE_FIELDS` に `review_status` を**入れない**（ガバナンス列。遷移は `core/library/registry.py` の専用関数 = `candidate_flow` 経由）。
- 一覧 API `GET /entries` は既定で `review_status='confirmed'` のみ（後方互換）。`include_candidates=true` で candidate も返す。retrieval（`search_frozen_entries`）は凍結版だけを読むので変更不要（candidate は凍結できない）。

### 4.3 `library_entry_labels`（P3-2 ラベル）

```
id UUID PK, entry_id UUID FK library_entries ON DELETE CASCADE,
kind TEXT NOT NULL REFERENCES knowledge_label_kinds(kind),      -- alternate / hidden（preferred は name が正本・行にしない）
label TEXT NOT NULL, normalized_label TEXT NOT NULL,             -- normalize は atlas_gaps.schema.normalize_label
language TEXT NOT NULL DEFAULT '',
status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','dismissed')),
mapping_justification TEXT REFERENCES knowledge_mapping_justifications(justification),
evidence JSONB NOT NULL DEFAULT '[]', created_by UUID, decided_by UUID, created_at, updated_at,
UNIQUE (entry_id, kind, normalized_label)
```

- **`aliases` JSONB は残す**（編集面・後方互換）。`store.create_entry` / `update_entry` が同一トランザクションで `aliases` を
  `kind='alternate'` / `justification='manual_curation'` の行へ**片方向ミラー**（upsert・既存 dismissed は復帰させない）。
  読み手（別名検索 `_ilike_search` / keyphrase 供給 `paper_discovery/vocab.py` / 候補導出）は**ラベル表**を読む
  （`hidden` は検索ヒットに使うが表示しない — SKOS hiddenLabel の意味）。
- preferred ラベルは `library_entries.name` 1 つ（SKOS: 言語ごとに 1 prefLabel）。行にしないので二重管理が無い。

### 4.4 `library_entry_relations`（P3-2 関係）

```
id UUID PK,
relation_key TEXT NOT NULL UNIQUE,   -- 対称 kind: rel|{kind}|{min(a,b)}|{max(a,b)} / broader: rel|broader|{narrower}|{broader}
subject_entry_id UUID FK, object_entry_id UUID FK（両方 ON DELETE CASCADE。subject <> object を CHECK）,
kind TEXT NOT NULL REFERENCES knowledge_relation_kinds(kind),
status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','dismissed')),
mapping_justification TEXT NOT NULL REFERENCES knowledge_mapping_justifications(justification),
reason TEXT NOT NULL DEFAULT '', evidence JSONB NOT NULL DEFAULT '[]', review_note TEXT NOT NULL DEFAULT '',
confidence REAL,                     -- DB のみ
created_by UUID, decided_by UUID, decided_at, created_at, updated_at
```

`exact_match` / `close_match` / `related` は無向（`relation_key` で A—B と B—A を同一行に）。`broader` は有向。
**ドメイン跨ぎ可**（`domain_key` は属性であって座標系ではない）。遷移は `candidate_flow.CandidateFlow`。

### 4.5 `library_atlas_node_links`（P3-4 レジストリ ↔ 骨格 node。版非依存）

```
id UUID PK,
link_key TEXT NOT NULL UNIQUE,       -- anode|{entry_id}|{domain_key}|{node_id}
entry_id UUID FK library_entries ON DELETE CASCADE,
domain_key TEXT NOT NULL, node_id TEXT NOT NULL, node_kind TEXT NOT NULL DEFAULT 'concept' CHECK (node_kind IN ('region','concept')),
kind TEXT NOT NULL REFERENCES knowledge_relation_kinds(kind),   -- exact_match / close_match のみ（コードで強制）
status candidate/confirmed/dismissed, mapping_justification NOT NULL FK, reason, evidence, review_note, confidence REAL,
created_by, decided_by, decided_at, created_at, updated_at
```

- `atlas_skeletons` への FK・書き込みは無い（KR2 / LS7）。**ドメイン跨ぎの exact_match** は「別ドメインの 2 node が同じ entry に
  confirmed で繋がっている」状態として表れる（ハブ経由。node—node の直接リンクは作らない = W-β の未決 2 と同じ判断）。
- 読み時に現行凍結版へ node が実在するかを `node_in_current_version` で付ける（KR9）。

### 4.6 `mapping_justification` の additive 追加（P3-3）

`element_identity_links` / `atlas_anchor_aliases` / `atlas_gap_decisions` / `atlas_edge_decisions` / `landscape_placements` に
`mapping_justification TEXT REFERENCES knowledge_mapping_justifications(justification)`（NULL 可）を追加。
**既存行のバックフィルは既存列から決定論的に導ける場合のみ**（migration 内 `UPDATE ... WHERE mapping_justification IS NULL`・冪等）:

| 表 | 導出 |
|---|---|
| `landscape_placements` | `provenance='llm'` → `llm_candidate` / `'human'` → `manual_curation` / `'deterministic'` → `lexical_match` |
| `atlas_anchor_aliases` | 全行 `manual_curation`（教員確定の別名。`source` は登録導線であって根拠ではない） |
| `element_identity_links` / `atlas_gap_decisions` / `atlas_edge_decisions` | 導出不能 → NULL のまま（推測で埋めない・KR4） |

書き込み側（新規行）: `identity_links.create_candidate(..., mapping_justification=)` 必須 kwarg（W層 UI からの手動作成は
`manual_curation`）/ `atlas_vectors.store.upsert_alias(..., mapping_justification="manual_curation")` 既定 /
`atlas_gaps.store.upsert_decision(..., mapping_justification=)`（accept は `llm_candidate` = gap 信号の出所、UI 経由の既定）/
`atlas_edges.store.decide(..., mapping_justification=)`（候補の `origin` から `vector` → `vector_similarity` / `co_occurrence` →
`corpus_cooccurrence`。route が変換）/ `landscape.store.supersede_and_insert_candidates`（provenance から導出）・
`update_status`（人間の遷移 → `manual_curation`）。

### 4.7 `element_identity_links` の instance 型に `symbol`（P3-5）

CHECK を `('figure','theory_component','theory_claim','equation','symbol')` に（DO $$ で旧制約を落として再作成・冪等）。
`instance_element_id` は `knowledge_symbols.agent_symbol_id`（`sym_{document_id}_{symbol}`。論文横断では衝突しないが 4 列 UNIQUE の規律は同じ）。
`core/deliberation/refs.py::IDENTITY_LINKABLE_ELEMENT_TYPES` に `symbol` を足す（ElementRef の解決は v1 では `symbol_records` で
実在検査するだけ。W層モーダルの対象化は非スコープ）。

### 4.8 `knowledge_symbols_live` ビュー

`CREATE OR REPLACE VIEW knowledge_symbols_live AS SELECT * FROM knowledge_symbols WHERE superseded_at IS NULL;`
読み手（§7）はこれを読む。`core/knowledge_objects/schema.py::VIEW_SYMBOLS_LIVE` を足す。

## 5. コア（`backend/core/library/`。FastAPI / sqlalchemy 以外の外部・LLM 非 import）

| ファイル | 責務 |
|---|---|
| `schema.py`（既存・拡張） | `ENTRY_TYPES` 再エクスポート・`entry_type_for_component_type()`・`REVIEW_STATUSES`・ラベル / 関係 / 正当化語彙の再エクスポート・`build_relation_key` / `build_node_link_key` / `build_candidate_key`（`normalize_label` は atlas_gaps から import）・`ENTRY_TYPE_LABELS` / `LABEL_KIND_LABELS` / `RELATION_KIND_LABELS` / `JUSTIFICATION_LABELS`（日本語。**フロントの表は逐語ミラー**で `test_library_vocab_mirror.py` に追加） |
| `store.py`（既存・拡張） | `create_entry(..., review_status=, mapping_justification=, candidate_key=)`・aliases → labels ミラー・`freeze_entry` の candidate 拒否・`list_entries(include_candidates=)`・`_row_to_entry` に新列 |
| `registry.py`（新設） | `library_entry_labels` / `library_entry_relations` / `library_atlas_node_links` の CRUD（すべて状態遷移・`DELETE FROM` なし）と `decide_entry_review`（`candidate_flow` 経由・`dismiss` は理由必須・監査 `AUDIT_ENTITY_LIBRARY_ENTRY` に action 語彙 `label_add` / `label_dismiss` / `relation_*` / `node_link_*` / `review_confirm` / `review_dismiss` / `review_restore`） |
| `atlas_links.py`（新設） | P3-4 候補導出（§6.1）。読み時導出・保存は判断のみ |
| `identity_candidates.py`（新設） | P3-6 候補導出（§6.2）。パイプラインステージ本体 `run_identity_candidates(document_id, run_id)` |
| `symbol_lookup.py`（新設・`core/` 直下） | P3-5 学習者向け「直前の定義」（§7） |
| `cartridge_shape.py`（新設・`core/` 直下） | P3-7 形の宣言の読み手と適合事実（§8） |

## 6. 候補導出（決定論・LLM / embedding 0 回）

### 6.1 レジストリ ↔ atlas node（P3-4・`atlas_links.derive_node_link_candidates(session, domain_key)`）

入力: 現行凍結骨格（`atlas_store.load_frozen_skeleton`）の concept node / 保存済みアンカーベクトル（`atlas_vectors.store.load_anchor_vectors`）/
`review_status='confirmed'` の entries とその凍結版 embedding（`library_entry_versions` の最新版）/ ラベル表（alternate・hidden も照合に使う）。

1. **語彙一致**: `normalize_label(node.label)` または教員確定別名（`atlas_anchor_aliases` confirmed）が entry の `name` / alternate / hidden の
   `normalized_label` と一致 → `exact_match` 候補（`lexical_match`）。
2. **ベクトル近傍**: アンカーベクトル × 凍結版 embedding の cosine が `label_vocab.ANCHOR_NEARNESS_THRESHOLD_NEAR` 以上 → `close_match` 候補
   （`vector_similarity`。ラベル×ラベル体制なので VA2 の `ANCHOR_NEARNESS_SCALE` を使う）。同一 (entry, node) に 1 と 2 が両方立てば 1 を採る。
3. **ドメイン跨ぎの双子**: 別の live 凍結ドメインに `normalize_label` が一致する node、または cosine ≥ NEAR の node があり、どちらも entry に
   結ばれていない → **candidate entry を 1 行**（`entry_type='concept'`・`name` = 語彙一致なら共通ラベル、ベクトルのみなら先勝ちの node label・
   `domain_key` = 先勝ちのドメイン・`candidate_key` で冪等・`mapping_justification` = 一致の種類）+ **node link candidate 2 行**。
   既に `dismissed` の候補（同じ `candidate_key` / `link_key`）は再提案しない（LS3 と同じ）。

出力は `{candidates: [...], skeleton_version, facts: [...]}`（数値なし。近さは段階ラベル）。保存するのは教員の判断（confirm / dismiss）だけ。
API `POST /api/admin/library/atlas-links/derive?domain_key=`（TEACHER・retired ドメインは 409・DB 書き込みは candidate 行の upsert のみ）。

### 6.2 同一性候補（P3-6・パイプラインステージ `identity_candidates`）

`_PIPELINE_STEPS` の**末尾**（`persist_claims_components_graph` の直後。component 行・stable_key・`knowledge_symbols` が確定した後）に
`PipelineStageDef("identity_candidates", _stage_identity_candidates, progress_unit="builder")`（`llm_kind=none`・非致命・
`skipped_reason` 記録）。本体は `core/library/identity_candidates.py::run_identity_candidates(document_id, run_id)`。

対象 = 当該 document の `theory_components_live` のうち **親 component**（`parent_agent_component_id IS NULL`。子の機械名は対象外 — K-8）。

1. **既存 entry への語彙一致**: `normalize_label(component.name)` が confirmed entry のラベル（name / alternate / hidden）と一致 →
   `element_identity_links` candidate（`lexical_match`）。
2. **他 document の component との語彙一致**: 他 document の live 親 component と正規化名が一致し、どちらも entry に結ばれていない →
   candidate entry（`entry_type = entry_type_for_component_type(component_type)`・`domain_key` = 当該 document の
   `corpus.document_domain_keys` の先頭 → 無ければ run の `cartridge_id` → 無ければ `unassigned`・`source_component_ids` = 2 件・
   `source_document_ids` = 2 件）+ identity link candidate 2 本（`lexical_match`）。
3. **chunk-proxy ベクトル近傍**（W層 cross_corpus と同じ下地・**追加 embedding ゼロ**）: 親 component の `primary_chunk_id` の
   `chunks.embedding` を代表ベクトルとし、`chunks` の pgvector 検索（他 document・`chunk_type` 不問・上位 `IDENTITY_CHUNK_TOPK=8`）で
   cosine ≥ `IDENTITY_CHUNK_PROXY_THRESHOLD`（`schema.py` の定数・既定 0.60）のチャンクを持つ他 document の live 親 component
   （`primary_chunk_id` または `source_chunks` に含む）を近傍とする → candidate entry + identity link 2 本（`vector_similarity`。confidence = cosine は DB のみ）。
4. `theory_components.duplicate_candidates`（既存の受け皿・常に `[]` だった）に
   `[{"component_id", "document_id", "entry_id", "mapping_justification"}]` を書く（`persistence.set_duplicate_candidates()` 経由 —
   基表 UPDATE は `persistence.py` に限る。数値なし）。
5. 上限 `IDENTITY_CANDIDATES_MAX_PER_DOCUMENT`（env・既定 20）。超過は `coverage`（Phase 0 の共通報告形式 `coverage_report`）で正直に報告。
6. 冪等: candidate entry は `candidate_key`、link は 4 列 UNIQUE で `create_candidate` が既存行を返す。`dismissed` の候補は再提案しない。

教員レビュー: `GET /api/admin/library/identity-candidates?domain_key=&include_dismissed=` が candidate entry ごとに
`{entry, links[{link_id, instance, document_title, local_expression, status}], hidden_count}` を返す（閲覧不可 document 由来の link は除外し
`hidden_count`・**件数は隠し方の事実であって指標ではない** = W-β 踏襲）。確定は `POST /api/admin/library/entries/{id}/review`
（body `{status: confirmed|dismissed|candidate, review_note?}`。dismissed は理由必須 422）。link の確定は既存
`POST /api/admin/deliberation/identity-links/{id}/confirm|reject` を再利用（新設しない）。entry を confirmed にしても link は
自動確定しない（1 リンク = 1 判断）。

## 7. 記号 → 概念と「直前の定義」（P3-5）

- **概念参照は識別リンク**: `symbol` instance ↔ entry の `element_identity_links`（§4.7）。`SymbolRecord` は不変（KR1）。
- **学習者 API** `GET /api/learning/courses/{course_id}/symbols/lookup?symbol=&equation_id=&chunk_id=`
  （`core/symbol_lookup.py::lookup_symbol_definition`。受講ゲート `get_accessible_course_data` → `list_course_source_document_ids` →
  `knowledge_symbols_live` を `document_id = ANY(:doc_ids)` で読む。**LLM 0 回・既存データのみ**）:
  1. `canonical_symbol` または `notation_variants` に一致する live 行を集める（一致は記号専用キー `symbol_lookup.symbol_key` の完全一致・部分一致なし。**大文字小文字を区別する** — `λ` と `Λ` は別の記号。TeX ⇄ Unicode・波括弧・書体指定だけを畳む。旧実装は概念名向けの `normalize_key` を流用して `λ` / `Λ` を同じキーにしていた = IK-0387）。タップ位置の論文（式またはチャンクから）の記号を先に当て、別の論文へ倒したときと、タップ位置が無いときは出所の論文タイトルを事実文で添える。「この節の中」「この式の中だけ」はタップ位置の論文の記号でだけ出す。`available: false` は必ず事実文を1つ持つ（IK-0386）。
  2. **ScholarPhi 規則 = 直前の定義**: タップ位置（`equation_id` → `knowledge_equations_live` の `block_id` / `section_id` の順序、
     無ければ `chunk_id` の `chunk_index`）より**前**にある `defining_equation_ids` / `source_evidence_ids` の定義のうち最も近いものを 1 件
     （`definition_evidence_texts` の逐語）。前に無ければ後方の最初の定義を「この位置より後で定義されています」の事実文付きで返す。
     定義が無ければ「この論文には定義の記述が見つかりませんでした」（`definition_status` のラベルは、これと食い違わない `definition_missing` =「定義なし」のときだけ添える。`used` の「定義は別の箇所」等は含意が逆になるため出さない — IK-0380）。
  3. `concept_ref`: 当該 symbol に `confirmed` の識別リンクがあれば `{entry_id, name, entry_type_label}`、無ければ `null`（candidate は出さない）。
  4. DTO に `confidence` / `stable_key` / 内部 ID を載せない（KO10 / PL7）。`unit` / `scope_label` は載せる。
- **UI（`app.js`）**: 教材の KaTeX 描画済み数式（`.katex`）内の記号トークン（`.mord.mathnormal` / `.mop` / `.mord` の textContent）クリックで
  ポップオーバー `#symbol-lookup-popover`（定義の逐語 + 出所 + 概念名。数値なし）。数式カードの `data-equation-id` / チャンクの `data-chunk-id`
  を位置として送る。自動表示なし・タップのみ。アンカー `material.symbol-lookup` + `docs/manual/student/02-student.md` に節。

## 8. cartridge の「形の宣言」と適合事実（P3-7）

- `backend/cartridges/<id>/shape.json`（任意・無ければ従来どおり）:
  ```json
  {"covers": ["..."], "does_not_cover": ["..."], "expects": {"entry_types": [...], "component_types": [...], "claim_types": [...]},
   "atlas_domain_key": "particle_physics"}
  ```
  `covers` / `does_not_cover` は自然言語の主題語（人間が書く・`cartridge_declared`）。`expects.*` は語彙表の値のみ（起動時 validator が
  語彙外を warning）。**A層は読まない**（agent の入力は不変）。
- `particle_physics/shape.json` を同梱し、`cartridge.json` の `description` を実内容（フレーバー物理・半レプトン崩壊・HQET）に合わせて訂正する
  （名前 `cartridge_id` は参照が多いため**変えない**。乖離の解消は宣言側で行う）。
- **適合事実** `GET /api/admin/cartridges/{id}/fit?document_id=`（TEACHER・`_ensure_document_viewable`・`core/cartridge_shape.py::fit_facts`）:
  事実文のみ・数値なし。材料は (a) 採用 run の `landscape_placement` artifact の `unplaced_domains`（当該 `atlas_domain_key` の reason）
  (b) `landscape_placements` live 行の有無（「この分野の地図に配置があります / ありません」）(c) `covers` の語と paper_skeleton / thesis の
  概念語との一致（両辺 `normalize_label` 正規化 + `alias_matching` の**語境界付き**一致。完全一致では文中の主題語に当たらないため。一致した**名前の列挙**）(d) `does_not_cover` に一致した語の列挙。材料が無ければ
  「この論文はまだ解析されていないため、適合の事実はありません」。
- UI: 再解析モーダル（`admin-llm-models.js` の分野選択の直下）に、選択中 cartridge の適合事実を 1 区画（`materials.reanalyze-domain-fit`）。
  アップロード時は解析前で材料が無いため出さない（KR8）。

## 9. API 一覧（`routes/library.py` に追記・すべて `_require_teacher`。適合事実は `routes/cartridge_shape.py`・学習者 1 本は `routes/learning.py`）

| メソッド・パス | 内容 |
|---|---|
| `GET /api/admin/library/entries?include_candidates=` | 既存 + candidate の可視化フラグ |
| `POST /api/admin/library/entries/{id}/review` | review_status 遷移（confirmed / dismissed(理由必須) / candidate=restore）。監査 |
| `GET|POST /api/admin/library/entries/{id}/labels` / `POST .../labels/{label_id}/dismiss` | alternate / hidden の追加・見送り |
| `GET /api/admin/library/relations?entry_id=` / `POST /api/admin/library/relations` / `POST .../relations/{id}/decide` | 関係の候補作成（手動 = manual_curation）・確定 / 見送り / 戻す |
| `GET /api/admin/library/atlas-links?domain_key=` / `POST .../atlas-links/derive` / `POST .../atlas-links/{id}/decide` | node リンク候補の一覧・導出・判断 |
| `GET /api/admin/library/identity-candidates?domain_key=` | §6.2 のレビューキュー |
| `GET /api/admin/cartridges/{id}/fit?document_id=` | §8（新設 `routes/cartridge_shape.py`・main.py 直接登録 `prefix="/api/admin"`。cartridge 単位の読み取り専用） |
| `GET /api/learning/courses/{course_id}/symbols/lookup` | §7 |

DELETE ルートは無い。すべて `detail` は数値なしの事実文。

### 9.1 DTO 契約（UI が描く形。キー名は固定・追加は自由・無いものは省略）

- `GET /entries?include_candidates=true` → 既存形 + 各 entry に `review_status` / `review_note` / `mapping_justification` / `candidate_key`
- `POST /entries/{id}/review` body `{status, review_note?}` → `{entry}`（dismissed で理由空は 422）
- `GET /entries/{id}/labels` → `{labels:[{id,kind,label,status,mapping_justification}]}` / `POST` body `{kind,label}` → 201 `{label}` / `POST .../labels/{label_id}/dismiss` → `{label}`
- `GET /relations?entry_id=` → `{relations:[{id,kind,subject_entry_id,object_entry_id,subject_name,object_name,status,mapping_justification,reason}]}` / `POST /relations` body `{subject_entry_id,object_entry_id,kind,reason?}` → 201 / `POST /relations/{id}/decide` body `{status,review_note?}`
- `GET /atlas-links?domain_key=` → `{links:[{id,entry_id,entry_name,domain_key,node_id,node_label,node_kind,kind,status,mapping_justification,node_in_current_version}],skeleton_version}` / `POST /atlas-links/derive` body `{domain_key}` → `{candidates:[同形],facts:[str]}` / `POST /atlas-links/{id}/decide` body `{status,review_note?}`
- `GET /identity-candidates?domain_key=&include_dismissed=` → `{candidates:[{entry:{id,name,entry_type,review_status,domain_key},links:[{link_id,instance:{element_type,element_id,document_id},document_title,local_expression,status,mapping_justification}],supporting_titles:[str],hidden_count}],facts:[str]}`
- `GET /api/admin/cartridges/{id}/fit?document_id=` → `{cartridge_id,available,facts:[str],covered_terms:[str],out_of_scope_terms:[str],atlas_domain_key}`（未知分野 404 / 教材未指定 422）
- `GET /api/learning/courses/{course_id}/symbols/lookup` → `{available,symbol,definition:{text,equation_label?}|null,facts:[str],source,concept_ref:{entry_id,name,entry_type,entry_type_label?}|null,unit?,scope_label?}`（記号空 422 / コース不可視 404。位置の前後関係・定義なし・位置不明は `facts` の事実文で表す）


## 10. UI（`admin.js` のナレッジライブラリタブに区画追加・新モーダルなし）

- エントリ詳細に「別名（alternate）」「隠しラベル（hidden）」「関係」「分野の地図との対応」区画 + 追加フォーム。
- タブ上部に「同一性の候補」区画（candidate entry カード: 名前・種別・支持する論文タイトルの列挙・各 link の [確認][見送り]（既存 identity-links API）・
  entry の [確定][見送り（理由）]）。件数バッジなし。
- 分野一覧に「地図との対応を導出」ボタン（confirm の事実文付き）。
- アンカー（3 点セット）: `knowledge-library.identity-candidates` / `knowledge-library.identity-derive` / `knowledge-library.entry-review` /
  `knowledge-library.labels` / `knowledge-library.relations` / `knowledge-library.atlas-links` / `materials.reanalyze-domain-fit` +
  学習者 `material.symbol-lookup`。**正確な件数は `test_admin_help_ui_anchors.py` が正**。マニュアルは
  `docs/manual/teacher/19-admin-knowledge-library.md` に節を足す。
- 日本語表（entry_type / label kind / relation kind / justification）はサーバ正本のミラー（`test_library_vocab_mirror.py` に追加）。

## 11. 権限・監査・数値

- 書き込みは TEACHER 以上・帰属必須（`decided_by` 空は 422）。学習者は §7 の 1 本のみ・コース sources にスコープ強制。
- 監査は既存 `AUDIT_ENTITY_LIBRARY_ENTRY`（新 entity_type を作らない。action 語彙は `core/library/schema.py::REGISTRY_AUDIT_ACTIONS`）。
  パイプラインの候補生成は run 単位の要約 1 行（`AUDIT_ENTITY_KNOWLEDGE_OBJECT`・件数のみ・Phase 1 と同じ）。
- 数値（cosine / confidence / 候補数）は API・UI に出さない。近さは `ANCHOR_NEARNESS_SCALE` のラベル。

## 12. 非スコープ（v1）

学習者向けの概念一覧・「コーパス全体の知識グラフ」画面（原則6）/ `owl:sameAs` 的な統合・行削除 / 関係の推移的閉包の自動確定 /
LLM による概念名・定義の生成（`llm_candidate` は既存 LLM 由来の候補に付ける語彙であって、本層で LLM を呼ぶことではない）/
W層モーダルでの `symbol` 要素の対象化 / cartridge shape の SHACL 検証・自動適合スコア / ~~`claim.concepts` の concept 層書き込み~~
（K-2 → **2026-09-13 同日の追補 [claim_concept_grounding_design.md](claim_concept_grounding_design.md) で解消**。§13.2）/ ~~atlas node_id の版間対応表~~（K-6 → **2026-09-13 同日の追補 [atlas_node_correspondence_design.md](atlas_node_correspondence_design.md) で解消**）/
alias 候補行の自動生成（VA層非スコープの継承）。

## 13. 実装記録

### 13.0 2026-09-13 — Phase 3 v1（Fable 5.1 指揮・Opus 5 の 4 担当）

体制: 指揮者が本書（KR1〜KR10・§4〜§9.1 の DTO 契約）を先に固定し、A（スキーマ・コア・正当化列）/ C（記号の直前定義・cartridge 形の宣言）/
D（管理 UI・マニュアル・docs 索引）を並列、B（候補導出・パイプラインステージ）を A のスキーマ確定後に実施。migration は **082** に採番
（`ls backend/db/` で確認）。オーナー判断 O-4 は (b) を推奨どおり採用（冒頭表）。

| # | 解消 | 実装先 |
|---|---|---|
| P3-1 | `entry_type` を語彙表 `knowledge_entry_types` FK に拡張（`LIBRARY_ENTRY_TYPES` 9 語彙・既存型語彙からの決定論写像 `entry_type_for_component_type`） | 082・`core/schema.py`・`core/library/schema.py` |
| P3-2 | SKOS 語彙表 `knowledge_label_kinds` / `knowledge_relation_kinds` + `library_entry_labels`（alternate / hidden。preferred は `name`・`aliases` は片方向ミラー）+ `library_entry_relations`（無向 kind は `relation_key` で畳む・ドメイン跨ぎ可）。RDF 化なし | 082・`core/library/{schema,store,registry}.py`・`routes/library.py` |
| P3-3 | `knowledge_mapping_justifications` + `mapping_justification` を `element_identity_links` / `atlas_anchor_aliases` / `atlas_gap_decisions` / `atlas_edge_decisions` / `landscape_placements` と新 3 表に additive 追加。書き込み側は必須 kwarg（未指定・語彙外は ValueError → 422）。バックフィルは landscape（provenance 由来）と aliases（manual_curation）のみ・他は NULL のまま | 082・各 store・`routes/{deliberation,atlas_edges}.py` |
| P3-4 | `library_atlas_node_links`（`link_key` 版非依存・exact_match / close_match・ハブ経由でドメイン跨ぎ）+ 候補導出 `atlas_links.derive_node_link_candidates`（語彙一致 / 保存済みアンカー × 凍結版 embedding の cosine / 別ドメインの双子 → candidate entry + link 2 本）+ `POST /atlas-links/derive` | `core/library/{registry,atlas_links}.py`・`routes/library.py` |
| P3-5 | `element_identity_links` の instance 型に `symbol`・`knowledge_symbols_live`・学習者 API `GET /api/learning/courses/{id}/symbols/lookup`（ScholarPhi 規則・LLM 0 回・quota 非消費・concept_ref は confirmed リンク + confirmed entry のみ）+ `app.js` の記号クリックポップオーバー（`material.symbol-lookup`） | `core/symbol_lookup.py`・`routes/learning.py`・`app.js` / `index.html` / `styles.css`・`docs/manual/student/02-student.md` |
| P3-6 | パイプラインステージ `identity_candidates`（末尾・非LLM・非致命）= `identity_candidates.run_identity_candidates`（live 親 component の①entry ラベル一致 ②他 document 親 component の正規化名一致 ③chunk-proxy 近傍 → candidate entry + identity link 2 本 + `duplicate_candidates`）+ `GET /identity-candidates`（hidden_count 付き）+ UI「同一性の候補」 | `core/library/identity_candidates.py`・`orchestrator.py`・`persistence.set_duplicate_candidates`・`routes/library.py`・`admin.js` |
| P3-7 | `backend/cartridges/particle_physics/shape.json`（`covers` / `does_not_cover` / `expects` / `atlas_domain_key`）+ `cartridge.json` の description / target_domain を実内容に訂正（`cartridge_id` 不変）+ `core/cartridge_shape.py`（load / validate / fit_facts。起動時 validator は fail-open）+ `GET /api/admin/cartridges/{id}/fit` + 再解析モーダルの「分野の適合」区画（`materials.reanalyze-domain-fit`） | `core/cartridge_shape.py`・`routes/cartridge_shape.py`・`admin-cartridge-fit.js`・`docs/manual/teacher/11-admin-materials.md` |

UI: ナレッジライブラリタブに「同一性の候補」区画 + 詳細の「別名 / 隠しラベル / 関係 / 分野の地図との対応」4 区画 + 「地図との対応を導出」
（`admin.js`・ES5）。日本語表 5 つはサーバ正本（`core/library/schema.py`）の逐語ミラーで `test_library_vocab_mirror.py` が固定。
アンカーは管理 7 件（`knowledge-library.{identity-candidates,identity-derive,entry-review,labels,relations,atlas-links}` +
`materials.reanalyze-domain-fit`）・学習 1 件（`material.symbol-lookup`）で 3 点セット済み（総数の正本は `test_admin_help_ui_anchors.py`）。
docs: `docs/README.md` / `layer_registry.md`（層一覧 + §3 の 082）/ `data-model.md` / `backend/api.md` / `pipeline/overview.md` /
`features/learning.md` §3.13 / CLAUDE.md「概念レジストリ層」節。

検証: backend 15,276 pass / src 1,924 pass（ベースライン 14,905 / 1,924 から赤ゼロで増分）。実 DB での migration 適用・E2E は未実施
（docker 復帰後の課題。082 は `test_migrations_runner.py` の冪等 lint を通過）。

**設計書からの差分（A / C）**:

| # | 判断 | 理由 |
|---|---|---|
| A-1 | `IDENTITY_LINKABLE_ELEMENT_TYPES` の所在は `core/deliberation/schema.py`（既存の正本）。`refs.resolve` に `_resolve_symbol` を追加 | §4.7 は `refs.py` と書いたが正本は schema 側だった |
| A-2 | `DIALOGUE_SESSION_ELEMENT_TYPES` を新設し W層セッション作成 route で `symbol` を 422 | `symbol` を document 要素型に入れた副作用で `deliberation_sessions` の CHECK に 500 で当たる経路を塞ぐ（§12「W層モーダルの対象化は非スコープ」の帰結） |
| A-3 | `create_candidate(mapping_justification=None)` は既定値付きで検証は要素型チェックの後 | 既存ガードレールが 2 引数呼び出しで `ElementResolutionError` を期待。実効は KR4 どおり |
| A-4 | `atlas_edges` の正当化は route がボディの optional `origin` から変換し、未指定・語彙外は**書かない** | KR4「推測で埋めない」 |
| C-1 | `covers` の照合は両辺 `normalize_label` + `alias_matching` の語境界付き一致 | 完全一致では文中の主題語に当たらず常に空になる。P0-2 の規律を流用 |
| C-2 | `cartridge.json` の `target_domain` 先頭に `particle_physics` を残したうえで実分野語を追加 | 既存テスト `tests/core/test_cartridges.py` の互換 |
| C-3 | DTO は §9.1 の確定形（fit = `covered_terms` / `out_of_scope_terms`、lookup = `definition.equation_label` + `facts`） | §9.1 を実装に合わせて追随済み |

### 13.1 候補導出（P3-4 / P3-6）とパイプラインステージ — 2026-09-13

**新設**: `backend/core/library/atlas_links.py`（§6.1）/ `backend/core/library/identity_candidates.py`（§6.2）。
どちらも FastAPI・`core.llm` を import せず、**保存済みベクトルの読みだけ**で動く（KR5。ガードレールが
`generate_embeddings(` / `embed_with_context(` / `build_anchor_embeddings(` の不在を固定する）。

**API 2 本**（`routes/library.py`・`_require_teacher`）:

- `POST /api/admin/library/atlas-links/derive` body `{domain_key}` → `{candidates, skeleton_version, facts}`。
  retired ドメインは 409 / 現行凍結版なしは 422（いずれも数値なしの事実文）。監査は
  `AUDIT_ENTITY_LIBRARY_ENTRY` の action `node_link_derive`（`REGISTRY_AUDIT_ACTIONS` に追加）。
  **§9.1 との差分**: core は `coverage`（母集合と処理数の件数報告）も返すが、**route は落とす** —
  KR6「候補数を教員にも出さない」に従い、取りこぼしは `facts` の事実文が担う。
- `GET /api/admin/library/identity-candidates?domain_key=&include_dismissed=` → §9.1 のとおり
  `{candidates:[{entry, links, supporting_titles, hidden_count}], facts}`。閲覧不可 document 由来の
  リンクは `services.resolve_document_access(...).can_view` で除外し `hidden_count` を正直に返す（KR10）。
  **§9.1 への追補**: リンクが 1 本も見えない候補は一覧から落とす（判断材料が無いため。
  `include_dismissed=true` のときは履歴として残す）。

**パイプライン**: `_PIPELINE_STEPS` の**末尾**（`persist_claims_components_graph` の直後）に
`PipelineStageDef("identity_candidates", _stage_identity_candidates, progress_unit="builder")`
（`llm_kind=none` / `model_policy=False` = M層の対象外・非致命）。`PIPELINE_STAGES` は 31 要素
（名前付き 30 ステージ）になり、`DOCUMENT_PIPELINE_STAGE_LABELS`（「共通する概念の候補づくり」）と
`llm_usage.schema.KNOWN_FEATURES`（`pipeline:identity_candidates`）に追随した。
`docs/pipeline/overview.md` §2 の表と §4 の coverage 一覧も更新済み。

**env**: `IDENTITY_CANDIDATES_MAX_PER_DOCUMENT`（既定 20・`core/config.py` + `.env.example`）。
超過は `coverage`（Phase 0 の共通報告形式・理由コード `candidate_limit`）で正直に報告する。

**実装上の判断（設計書からの明示的な差分）**:

| # | 判断 | 理由 |
|---|---|---|
| 1 | §6.1 の 1 / 2 の照合対象エントリを**ドメインで絞らない** | `domain_key` は属性であって座標系ではない（§4.4 / §4.5）。同じエントリが複数ドメインの node に繋がるハブ状態こそ本層が作りたいもの。誤爆は正規化ラベルの完全一致と最上位帯の cosine が抑える |
| 2 | §6.1 の 3 の「entry に結ばれていない」は **`dismissed` 以外のリンク行が無い**ことと定義 | 見送られた対応を「結ばれている」と数えると、教員が別の相手を選び直せなくなる |
| 3 | §6.2 で ElementRef を `refs.resolve()` ではなく **dataclass 直組み**（`validate()` は呼ぶ） | 直前に `theory_components_live` から読んだ行を渡すので実在は確認済み。1 document あたり親 component 数ぶんの DB 往復が純粋な重複になる |
| 4 | 候補エントリ（`store.create_entry`）と同一性リンク（`identity_links.create_candidate`）は**既存の公開面のまま**自前トランザクションで書く | 公開面を変えずに済ませるため。`candidate_key` と 4 列 UNIQUE で再実行が安全（冪等）で、途中で落ちても作れたところまでが残る（P4） |
| 5 | 分野が引けない候補エントリの `domain_key` に `unassigned` を使う（`schema.DOMAIN_KEY_UNASSIGNED`） | 「この論文を参照するコースがまだ無い」は正常な状態（`corpus.document_domain_keys` の規約）。分野名を推測で作らない |

**小さな追随**: `core/symbol_lookup.py` の `concept_ref` ゲートに `library_entries.review_status`
が confirmed である条件を追加した（同一性候補が未確定のエントリ行を作るようになったため、
AI が立てた候補の名前を学習者に「概念」として見せない = KR2）。

**ガードレール**: `test_concept_registry_{atlas_links,identity_candidates,stage,candidates_api}.py` +
`test_concept_registry_guardrails.py` の §9（候補導出の純粋性・骨格非書き込み・live ビュー・
基表 UPDATE の所在・段階ラベル）。

### 13.2 追補 — 主張の概念接地（K-2）— 2026-09-13

§12 で非スコープとした `claim.concepts` の concept 層は、オーナー判断（CG-O1: 既存 `concept_resolver` 注入口への辞書供給は
A層非改変の範囲内 / CG-O2: LLM 抽出は実測後）を受け、専用設計書 [claim_concept_grounding_design.md](claim_concept_grounding_design.md)
（CG1〜CG7・migration なし）で同日実装した。本層との接点: 辞書②はレジストリの confirmed entry の name + alternate / hidden ラベル、
`identity_candidates` に規則 ④（`entry_id` 付き主張 → `theory_claim` の identity link candidate）を追加、`mapping_justification` の
語彙をそのまま使う。

### 13.3 追補 — 敵対的レビュー是正（F3 班）— 2026-09-13

Phase 3 の敵対的レビューで挙がった P3-R1〜R14 のうち、本層が所有する 13 件を同日是正した
（`claim_concept_grounding.py` の A層 hook = F1 / `orchestrator.py` の resume 語彙 = F2 は別班）。

| # | 是正 | 要点 |
|---|---|---|
| P3-R1 | L層の不変条項「昇格は人間の操作のみ」の読み替えを**裁定待ちとして明記** | 冒頭のオーナー判断表に **O-6** を追加し、`image_pipeline_knowledge_library_design.md` §6 の条項 2 にも注記。裁定まで実装は現状維持（可視化の弁は凍結の 409 ゲート） |
| P3-R2 | `GET /entries` に**候補限定の document 可視性**（KR10） | `_apply_candidate_visibility`（route 層）が `review_status='candidate'` の行だけ `source_document_ids` を `services.resolve_document_access(...).can_view` で絞り、出所が 1 件も残らない候補を落として `hidden_count` を返す。確定済みは従来どおり全教員に見える（分野の共同財）。判定失敗は fail-closed。core は FastAPI 非 import のまま |
| P3-R3 | 候補の事実文から**相手 component 名を外す** | `identity_candidates._twin_reason` は「別の論文の記述と表記が一致しました。」。相手の素性は可視性を通った `links[]` / `supporting_titles` でのみ示す |
| P3-R4 | 学習者 DTO `concept_ref` から内部 ID と生の語彙キーを落とす | `symbol_lookup._load_concept_ref` は `name` / `entry_type_label` のみ（フロントも `name` しか読んでいない） |
| P3-R5 | 候補リンク書き込みを **SAVEPOINT** で包む | `atlas_links._savepoint`（`begin_nested` が無いセッションでは素通し）。PostgreSQL は 1 文の失敗でトランザクションが abort するので、`except` だけでは「1 件失敗 → 導出ごと 500」になっていた。`_ensure_candidate_entry` は store 側が自前セッションを持つので対象外（docstring に明記） |
| P3-R6 | **retired は読み取り専用**をレジストリ側にも適用 | `registry._require_not_retired` を `add_label` / `create_relation`（両端）/ `create_node_link` / `decide_entry_review` に置き、`LibraryRetiredError` → route で 409（既存 `update_entry` / `freeze_entry` と同型） |
| P3-R7 | `q` 検索が**ラベル表**にも当たる | `store.list_entries` の条件に `EXISTS (... library_entry_labels ...)` を OR で追加。ラベル側は**正規化の完全一致**のみ（部分一致は `SM` が `cosmological` に当たる F-7 の再発源）。`hidden` ラベルは検索に当たるが、返るのはエントリ行なので表示テキストには現れない（SKOS hiddenLabel） |
| P3-R9 | live に解決できない候補リンクを落とす | `_live_component_ids` / `_link_resolves_to_live_component`（`theory_components_live` 実在で絞る。DB 不達は fail-open）。落としたことは `hidden_unresolved`（真偽）+ 事実文で報告し、**件数は書かない**（KR6） |
| P3-R10 | 概念辞書の**分野スコープ**と照合コスト | `build_concept_dictionary(..., domain_key=)` を追加し、既定は `domain_key or cartridge_id`（`cartridge_id` と `domain_key` は同一名前空間なので、現行の orchestrator 呼び出しは無改変でそのまま絞られる）。絞りは「当該分野 + `unassigned`」で、引けなければ従来どおり全件。`ConceptDictionary.match()` は `distinct_surfaces()` を 1 回作って**相異なる表記の数**だけ本文を走査する（出力の順序・内容は不変） |
| P3-R11 | 同一性候補一覧の N+1 解消 | `identity_links.list_for_shared_parts(ids)`（`shared_part_id = ANY(...)` の 1 クエリ・`{id: [link]}`）を追加し route が使う。既存 `list_for_shared_part` は非改変 |
| P3-R12 | **見送り**（`pipeline:identity_candidates` は `KNOWN_FEATURES` に残す） | 指摘の前提「LLM を呼ばないステージは載せない」が成り立たない。`orchestrator.report_start(stage)` はステージ種別を問わず `set_current_feature("pipeline:{stage}")` を呼ぶので feature は実行時に必ず立ち、`KNOWN_FEATURES` はその**参照用の語彙表**（U3 の帰属先カタログ）である。非LLM ステージは既に10件載っており（`source_chunking` / `evidence_registry` / `symbol_registry` / `derivation_chain` / `persist_claims_components_graph` 等）、削ると `test_llm_usage_attribution.py::test_pipeline_stage_features_all_registered` が守る「全ステージ網羅」の規約が破れる |
| P3-R13 | 骨格非書き込み検査を **glob 化** | `test_atlas_node_correspondence_guardrails.py` の `_LAYER_PATHS` ハードコードを廃し、`backend/core` + `backend/api` の全 `.py`（`atlas_store.py` を除く）を走査。後から増えた層（`core/library/`）が検査から漏れない |
| P3-R14 | migration 082 の entry_type CHECK **総なめ DROP を FK 未作成時だけに限定** | 毎起動・番号順に全ファイルを再実行する方式なので、無条件 DROP は「冪等」ではなく「毎回壊す」（後続 migration が正当な CHECK を足しても黙って落ちる）。FK 存在チェックで早期 `RETURN` し、置き換え済み環境では丸ごと no-op |

**テスト**: `test_concept_registry_dictionary.py`（新設・P3-R10）+
`test_concept_registry_{store,api,candidates_api,guardrails,vocab,stage}.py` /
`test_symbol_lookup_core.py` / `test_atlas_node_correspondence_guardrails.py` への追随。
ガードレールは `test_concept_registry_guardrails.py` の
`TestVisibilityAndDisclosure` / `TestRetiredIsReadOnly` に集約した。

**所有外への申し送り**: `build_concept_dictionary` の `domain_key` は
`cartridge_id` を既定にするので orchestrator は無改変で効く。`cartridge_id` が空の run でも
分野で絞りたい場合（`corpus.document_domain_keys` から解決）は、orchestrator 側で
`domain_key=` を明示的に渡す 1 行の追加が要る（本班の所有外）。

**追加是正（V-6 — scratch DB 実データ検証, 2026-09-13）**: 既存 run から永続化した
`theory_claims.concepts` は概念名 261 種のうち 244 種が生の数式記号
（`E_{L/R}(z,t)` / `F_3(t,{\bm{k}}_1,…)` / `0.62 \lesssim ? \lesssim 1.41`）。
`claim_concept_grounding` のフックは当該 run では未実行で、DB には生のまま残る。
**読み時の遮断**を `core/learner_context_common.py` に 1 箇所だけ置いた:

- `is_symbol_like_concept(name)` — **判定表を新設せず**既存 2 正本の OR。
  ①`component_assembly.schema.is_symbol_like_concept_name`（P0-3。LaTeX 制御記法・
  添字記法・短すぎる名前）②`text_excerpt.looks_like_tex_math`（散文に埋もれた TeX。
  `0.62 \lesssim …` のように先頭が `\` でも `{}$` も含まない式片は ① では落ちない）。
- `visible_concept_names(concepts)` — `claim.concepts`（文字列 / `{"name": ...}` の
  両形）の射影。記号様・内部 ID（`safe_text` と同じ遮断）・重複を落とし順序は保つ。
- `core/component_context.py` / `core/element_context.py` の**双方**から再エクスポート
  （片方だけに実装しない = 本モジュール新設の趣旨）。

**行は消さない**（P4）— 出さないだけで DB の値も symbol_registry からの辿りも失われない。
既存行の接地バックフィルは orchestrator / grounding hook 側の担当（本班の所有外）。
ガードレールは `test_concept_registry_dictionary.py::TestLearnerConceptGate`。

## 追補（2026-09-30・ペルソナ通し受講 第 16 波）

- **P3-5 記号 lookup は「この論文」に限る**（IK-0552）: 探索範囲は chunk の document、無ければ表示中トピックの論文（`topic_id` を受ける）。
  コースの別論文の定義は持ってこない。別論文に同じ記号があるときは `FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS`、この論文に無いときは
  `FACT_SYMBOL_NOT_IN_THIS_DOCUMENT` の事実文を返す。式本文は返さない（位置の事実文まで・IK-0578 で未決）。

## 追補（2026-10-23・第 25 周 面 A 第 1 周の是正）

- 同一性の候補の空の状態の文言はサーバの `facts` が正本（見送り済みを含めたかで文を分け、候補の出所と
  作り直す道を添える）。地図との対応の導出結果の `facts` は画面がボタンの下に描く（IK-0864）。
- 一覧・詳細の表示語は route 層で足す: `updated_by_label`（シードは「同梱の初期データ」・帰属の解決は
  `core.atlas_review_display.fetch_actor_labels` を共有）/ `domain_label` / `version_line`（「凍結版 N」
  「まだ凍結していません」・revision は出さない）/ `review_status_note`（確定と凍結は別）（IK-0866）。
- 別名の正本はラベル区画。詳細の編集面から別名の欄を外し、保存は aliases を送らない（IK-0867）。
- 関係の相手は名前・別名で検索して選ぶ（`GET /entries?q=` の結果が空なら `facts` で事実文）（IK-0868）。
- 記号の照会: 式だけで定義された記号は「式 (3)で定義されています。」+ 定義式の本文 / 登録の行に定義が無くても
  配信済みの式から探す / 但し書きは取り込み形式で分ける / 別の論文の同じ字形は意味の注記つき / 画面が上付きを
  添字に結合して送った記号は、完全一致の後に `symbol_key_fallbacks` で 1 段ずつ照合し直す（IK-0869 / IK-0870）。
- Copilot の capability に概念レジストリの 4 項目（案内専用）を足した（IK-0865）。

## 追補（2026-10-23・第 25 周 面 A 第 2 周の是正）

- **共通部品の検索の分野**（IK-0890）: 分野は `core/deliberation/dialogue.py::resolve_document_domains` の 1 関数が
  段順（採用解析 run の `cartridge_id` → 教材を sources に含むコースの `cartridge_id`（`corpus.document_domain_keys`）→
  live な `landscape_placements.domain_key`）で決める。深く検討の検索 route（`GET /api/admin/deliberation/elements/
  {type}/{id}/shared-part-candidates`）と W層の対話の同一性候補の両方がここを通る。route は教員の選択（クエリ
  `domain_key`）を最優先し、1 つに決まらない（複数・どこにも無い）ときは**サーバが黙って選ばず**、表示名付きの
  `domains`（`{domain_key, domain_label}`。当たった分野 → ライブラリにエントリがある分野）と事実文を返す
  （「カートリッジ」の語は出さない）。応答に `domain_label` / `domain_source`（`analysis_run` / `course` /
  `placement` / `selected`）を添える。表示名の正本は `atlas_state.domain_display_name`、共通の分野は
  `core/library/schema.DOMAIN_UNASSIGNED_LABEL`。
- **ラベル・関係の追加の結果**（IK-0897）: `registry.add_label` / `create_relation` は戻り値に `outcome`
  （`created` / `existing` / `dismissed`）を添える。route は新規だけ 201・それ以外は 200 で、固定文の `facts`
  （`LABEL_OUTCOME_FACTS` / `RELATION_OUTCOME_FACTS`）を返し、画面は区画の一覧の直前に描く。見送り済みの行は
  追加し直しても復帰させない（KR7 は不変）。
- **記号の照会の第 3 の供給源**（IK-0893）: 登録簿に定義の文が無いときだけ、同じ論文の主張本文での使われ方
  （`mentions`・最大 2 件・数式区間を割らない）を固定文 `FACT_CLAIM_MENTIONS`（「定義の文ではありません」）付きで
  添える。式の左辺による定義の推定は `P = …` / `P(ε) = …` に限る（`P(R_*) = 0` の値の代入を定義に数えない）。
- 導出の確認ダイアログの分野名は分野一覧の `domain_label`（IK-0866 の残り）。

### pre-mortem（第 2 周の是正の設計時の検査）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 入口で任意になった分野を後段が唯一の材料として要求 | `available-but-unwired` | 同じ値を持つ別の正本へ段順で後退し、最後は教員に選ばせる |
| 同じ問い（記号の意味）に画面ごとに別の材料で答える | `duplicate-canonical-sources` | 部品の文脈と同じ主張本文を第 3 の供給源にし、定義と断定しない |
| 3 つの結果を 1 つの成功で表す | 該当なし（表現の欠落。`outcome` の一級化で解消） | — |

## 追補（2026-10-23・第 25 周 面 A 第 3 周の是正）

- **同一性リンクの行**（IK-0916 / IK-0917）: `_identity_link_response` が部品側を `identity_links.describe_instance`
  （名前・要約・入出力・論文の題名。live ビューのみ・fail-soft）で同梱し、内部の要素型名・UUID は画面の本文に出さない。
  確信度の段階（`confidence_label`）は AI・照合由来（`MACHINE_JUSTIFICATIONS`）の未確定の行だけに付け、それ以外は
  `None`。人の判断の状態は別キー `provenance_label`（教員が作成 / 教員が確定 / 教員が却下 / 照合による候補）。
  未確定の行には確定の直前に読む `confirm_note`（同じものとして扱う・学生の画面に何が出る・使う関係なら確定しない）。
  **リンクの種類は足さない**（指揮者の判断・KN-2 の「同じもの」を保つ。使う・満たす関係は v1 で記録しない）。
- **作成の結果**（IK-0918）: `create_candidate` が `outcome`（`created` / `existing_candidate` /
  `existing_confirmed` / `existing_rejected`）を返し、route は新規だけ 201 + 監査、既存は 200 + `facts`
  （`LINK_OUTCOME_FACTS`）。IK-0897 と同じ作法。
- **同じ論文の別の版**（IK-0919）: `identity_candidates` に規則②b。arXiv ID（`source_url` → `filename` →
  `source_path`）か題名の正規化完全一致で別の版の組を先に解き、その中で根拠の式の正規化 LaTeX・主張の正規化本文の
  一致から部品の対を選ぶ（名前を使わない・`lexical_match`・`SAME_PAPER_REASON_*`）。規則③の起点は代表チャンク →
  出典チャンク → 根拠の主張の `chunk_id`（最大 4）。LLM 0 回・embedding 0 回（KR5 不変）。
- **確定リンクの読み手**（IK-0920）: ライブラリのエントリ詳細「結びついた教材の部品」・学生の部品の文脈（他の論文は
  確定リンク ∩ 読める論文の**題名**の列挙。件数キー `other_documents_count` は廃止）・学習チャットの element 解決器の
  事実 1 行・旅（区画に一意の部品が埋め込まれているときだけ [2] へ）。
- **地図との対応**（IK-0927）: 導出の事実文に「凍結していないエントリは名前・別名の一致だけで照合」と手動の入口。
  `GET /api/admin/library/atlas-links/nodes`（現行凍結骨格の概念を名前で検索）+
  `POST /api/admin/library/entries/{id}/atlas-links`（`manual_curation` の候補・現行版に無い node は 422・骨格に
  書かない = KR2）。教員の手動作成を即確定にするかは決めていない（候補で入り、既存の確定で確定する）。
- **検索の帰属**（IK-0928）: `library.search` は `feature` を受けると `embed_with_context` で帰属を張る
  （`embedding:library_search` / `embedding:shared_part_search`）。
- **記号の使用例**（IK-0922 / IK-0898）: 論文の文を持たない主張の出所の述語の正本 `core.schema.NON_SOURCE_TEXT_CLAIM_ORIGINS`
  で合成主張を除き、区切りの無い TeX は切り詰めの前に `delimit_bare_tex` で区切る。

### pre-mortem（第 3 周の是正の設計時の検査）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 確定リンクを作る側だけがあり読む側が無い | `available-but-unwired` | 読み手 4 面を既存 API・既存の解決器・既存の旅の hop に配線（新しい kind・新しい API を作らない） |
| 意図の語表が登録簿と別にある | `duplicate-canonical-sources` | 語を `Capability.keywords` に移し intent は読むだけ |
| 合成主張を論文の文として扱う消費者が増える | `rule-enforced-per-surface` | 述語を `core/schema.py` に 1 つ置き 2 消費者が共有 |
| 作り直しで既存行を返して「作成しました」 | `failure-reported-as-success` | `outcome` の一級化 |
| 他の論文の題名を学生に出す | `condition-not-propagated` | 読める論文の集合を呼び出し側が組んで渡し、その積だけを列挙（未指定はコースの sources = fail-closed） |

## 第 4 波の追補（2026-10-25・第 25 周 面 A 第 4 周の是正）

- **確定の取り消し**（IK-0953・migration **093**）: 同一性リンクの状態に `revoked`（教員が確定を取り消した）を足した。
  遷移は `confirmed → revoked` だけで、`identity_links.revoke(link_id, revoked_by=, reason=)` が理由・帰属を必須にし
  （空は `ValueError`）、確定者 `decided_by` / `decided_at` は上書きせず `revoked_by` / `revoked_at` / `revoke_reason` に
  別に残す（行は消さない = P4 / W4）。route `POST /api/admin/deliberation/identity-links/{id}/revoke`（`{reason}`・
  理由空は 422・確定でない行は 409・権限は確定と同じインスタンス側の論文の編集）、監査 `AUDIT_ENTITY_DELIBERATION` の
  `action="identity_link.revoked"`（確定者も metadata に残す）。学生側・旅・ライブラリの読み手は `status='confirmed'`
  だけを読む既存の述語のままで、取り消した結び付きは追加の変更なしに消える。同じ組の作り直しは `existing_revoked`
  （新しくは作らない）。行の応答は `describe_actors` で作成者・確定者・取り消した人の**表示名**（引けなければ
  「表示名を確認できない利用者」）と、作成者 = 確定者のときの `self_confirmed_note`「作成者が確定しました。」
  （自己確定は禁じない — オーナー判断の推奨どおり事実で並べる）、確定の行に `revoke_note`。UI は deliberation.js の
  確定の行とライブラリの「結びついた教材の部品」の確定の行に［確定を取り消す］（理由を問う・アンカー
  `deliberation.identity-link-revoke`）。KB `library.md#revoke-identity-link`。
- **粒度の確認**（IK-0917 残り）: 未確定の行に `confirm_question`（両側を名指しして「同じものですか（一方が他方を
  使う・満たすだけではありませんか）」）と、部品の種別（`entry_type_for_component_type(component_type)`）と共通部品の
  `entry_type` が違うときだけ `granularity_note`。UI は種別が違う対の［確定］でもう一度確かめるダイアログ。
  **リンクの種類は引き続き足さない**（使う・満たす関係は v1 で記録しない）。
- **学生は凍結版だけを読む**（IK-0954・オーナー判断の推奨で実装）: `library.store.get_frozen_entry_for_learner` が
  active かつ凍結版を持つエントリの最新凍結版の content を返し、`component_context._build_shared_part` と
  `symbol_lookup._load_concept_ref` はこれ（または凍結版との JOIN）だけを読む。凍結版が無いエントリは確定リンクが
  あっても学生の「共通部品として」・記号の概念名に出さない（fail-closed）。L層の「パイプラインは凍結版だけ」と
  同じ規律を学生にも。標準化の評価は `standardization_status`（ガバナンス列・版を持たない）を読む。
- **学生向けの標準化の文**（IK-0959）: `label_vocab.learner_standardization_note` が教員の確定した 4 値だけを 1 文にし、
  未評価は何も出さない（学生 DTO から生の `standardization_status` を外した）。
- **版の手がかり**（IK-0956）: `describe_instance` が同じ題名の document が 2 本以上のときだけ
  `instance_document_version_label`（`document_version_hint` =「TeX 版・ファイル名」「PDF 版・ファイル名」）を返す。
  学生の「ほかの論文」は同じ題名が並ぶときだけ形式（TeX 版 / PDF 版）を括弧で添える（ファイル名は出さない）。
- **規則の版**（IK-0957）: `identity_candidates.IDENTITY_RULES_VERSION`（`r6-same-paper`）を段の artifact に
  `rules_version` として残す。候補一覧の空状態は、閲覧できる document の採用 run（completed）の artifact を照らし、
  いまの規則で探していない教材を題名で列挙して「パイプラインを実行 ▼」の道を言うか、すべて探したことを言う
  （判定できなければ何も足さない）。規則を足す・変えるときは版を上げる。
- **Copilot**（IK-0958）: `renderMarkdown` が節内リンクを見出しの文字に落とす。登録簿に `library.what_students_see` /
  `library.revoke_identity_link`。
- **エントリの扱いの操作**（IK-0955）: 状態ごとに出し分け（候補 = 確定 / 見送り、見送り済み = 候補に戻す、確定済み = なし）。

### pre-mortem（第 4 周の是正の設計時の検査）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 人の確定に撤回の遷移が無い | `negative-decision-second-class` | 撤回を状態遷移で足し、確定者を上書きせず別の列に取り消しを記録（行は残す） |
| 学生の読み口が凍結の弁を通らない | `stale-derivative-served` | 学生向けの読み口を凍結版の 1 関数にし、凍結版が無ければ面ごと出さない |
| 規則を足しても既存の成果は古い規則のまま | `completion-defined-by-proxy` | 規則の版を artifact に残し、空状態が版を照らして「まだ探していない」を言う |
| 同じ題名の 2 版が区別できない | `same-name-different-referents` | 同題名のときだけ版の手がかりを運ぶ（常時は出さない） |
| 内部の管理値を学生へ | `projection-mistaken-for-source` | 学生向けの文の表を label_vocab に置き、未評価はキーを作らない |

## 第 5 波の追補（2026-10-27・第 25 周 面 A 第 5 周の是正）

審判 `uxsim/runs/round25/judge_share_r5.md`（§3 是正案・§5 オーナー判断の候補）を根で束ねて是正した。
オーナー判断 2 件は推奨どおりに決定し、ここに記す。

**オーナー判断（推奨どおり）**

| # | 判断 | 採用 | 実装 |
|---|---|---|---|
| IK-0984 | 著者のいない（AI が作った）標準の説明を、誰が学生への公開を査読できるか | **その部品の論文を編集できる教員**が査読・共有を設定できる。査読者は既存の監査（`AUDIT_ENTITY_EXPLANATION` の `changed_by`）に残す。査読した教員を「著者に準じる者」とし、1 人の査読ではその教員のコース（`learning_courses` の所有者）の学生にだけ届く。**この部品を使うほかのコースへ広げるには、いまの承認の期間に別の教員の査読がもう 1 件要る**（AI の要約を 1 人の判断で全コースへ配らない） | `core/explanation_governance.py` の `STANDARD_REVIEWERS_SQL` / `STANDARD_DELIVERY_SQL`（`LEARNER_EXPLANATION_SCOPE_SQL` が使う）・`STANDARD_ADDITIONAL_REVIEW_ACTION`。`routes/theory_components.py` の `_can_review_standard`（管理者 or `_ensure_component_editable`）・`ExplanationOut.can_review` / `second_review_available` / `outcome_fact`。PATCH は標準の説明の本文・題名・根拠の主張を管理者だけに残す（403 `EXPLANATION_STANDARD_BODY_ADMIN_ONLY`）。承認済みの標準の説明への 2 人目の査読は状態を変えず `action="additional_review"` の記帳だけ（同じ人は 409） |
| IK-0980 | 同じ論文の 2 版の部品の同一性を、文の近さで候補にしてよいか | **しない**。同じ論文の別の版どうし（`resolve_same_paper_documents` が真）では規則 ③（chunk-proxy）を使わず、規則 ②b（式・主張の本文の一致）だけで対を作る。既に立った誤った候補は消さず（KR7）、段の再実行で作り直す | `identity_candidates._run_component_and_claim_rules` が別の版の部品を `others_by_chunk` に入れない。未決の chunk-proxy 候補（`status='candidate'` ∧ `vector_similarity`）しか持たない部品は再実行で ②b にかけ直し、新しい対は `candidate_key` に接尾辞 `same-paper` を付けた別エントリに置く（旧エントリに相乗りさせない）。規則の版 `IDENTITY_RULES_VERSION` を `r7-same-paper-symbols` に上げた |

**判断の補足（IK-0984）**: いまの承認の期間に説明カードからの査読の記帳が 1 件も無い承認（部品の承認から
引き継いだ標準の説明・この規則より前の承認）は、従来どおり部品を使うコースへ届く。記帳の無い承認の承認者を
推測で決めない（`changed_by` の無い承認を 1 人とも 0 人とも数えない）ための縮退で、部品の承認（グラフレビュー）
から生まれた標準の説明をこの規則の対象にするかは**次の判断の材料**として残す（下の「残した課題」①）。
標準の説明は共有の ON / OFF を届く範囲に使わない（査読者の人数で決まる）ので、カードに共有の切り替えを出さない。

**是正（根ごと）**

- **同一性の候補の材料**（IK-0980・IK-0981）: 上の判断に加え、②b の理由に一致した材料（式は
  `graph_paper_layer.schema.equation_display_label` の表示ラベル「式 (12)」、主張は冒頭 40 字の引用・上限 3 件）を
  `SAME_PAPER_MATCHED_PREFIX`「一致したもの: 」に続けて書き、リンクの `evidence` に
  `{"basis": "same_paper_version", "matched": [...]}` を残す。**語彙表（`mapping_justification`）に値を足さず**
  `lexical_match` のまま、一覧の行に `basis_label`「同じ論文の別の版での一致」を添える（migration を足さない —
  他面と並行する波で採番が衝突しないため。語彙表に一級の値を足すかは次の判断）。一覧の各リンク行に `reason`・
  部品の要約 `instance_summary`。以前の規則で別の版どうしを文の近さで結んだ候補エントリには
  `stale_rule_note`（`STALE_SAME_PAPER_PROXY_NOTE`）。
- **判断の単位**（IK-0982・IK-0917 のエントリ単位）: キューは「candidate のエントリ」∪「candidate のリンクを持つ
  確定済みエントリ」。確定済みのカードは未決のリンクだけを並べ `links_pending_note` を添え、エントリの操作を出さない。
  候補のエントリには `confirm_question`（`ENTRY_CONFIRM_QUESTION`：確定するのは概念を置くことだけで結び付きは別）、
  エントリ詳細には `review_confirm_question`。`POST /entries/{id}/review` は `facts`（確定・見送り・候補に戻す）を
  返す。詳細の「結びついた教材の部品」の候補の行に［確認］［見送り］（既存の deliberation の confirm / reject・
  確定の前に `confirm_question` / `granularity_note` を並べた確認）。一覧の［確認］も同じ確認を通る。
- **書き手の無い読み手**（IK-0983）: 規則 ⑥ `core/library/symbol_concepts.py::derive_symbol_concept_candidates`
  （`knowledge_symbols_live` の `definition_evidence_texts` × 確定・公開中のエントリの `name` / `alternate` の
  `text_mentions_alias` 語境界一致・記号のような語は除く・hidden は使わない → `element_identity_links`
  `instance_element_type='symbol'` の candidate。LLM 0 回・embedding 0 回・DELETE なし・1 document 20 本・1 記号 2 エントリ）。
  パイプラインの `identity_candidates` 段と、ライブラリの `POST /api/admin/library/entries/{id}/symbol-candidates/derive`
  （確定済みエントリだけ・閲覧できる document だけ・409 は事実文）の 2 つの入口。詳細に「結びついた記号」区画
  （`describe_instance` が記号の表記と定義の文を返す）。確定は既存のリンクの確定の弁だけ（KR2）。
- **状態と操作**（IK-0985）: 査読ボタンは未査読のときだけ。PATCH の応答 `outcome_fact` が「共有を ON / OFF に
  しました。」「学生に公開してよいと査読しました。」「もう一人の査読者として査読しました。」+ いまの届く先を言い、
  JS はこれを優先して出す。
- **一覧の版の手がかり**（IK-0956 の一覧側）: 同じ題名の論文が 2 本以上のときだけ `document_version_label`。

**未評価のまま残すもの（製品は変えない）**: IK-0953（確定の取り消し）・IK-0920（確定リンクの読み手）は確定リンクが
作られず実地で評価されていない（H35）。

**残した課題**: ①部品の承認（グラフレビュー）から承認済みで生まれた標準の説明は、説明カードの査読の記帳が無いので
従来どおり部品を使うコースへ届く（1 人の判断で全コースへ配らない、を部品の承認にも当てるかは次の判断）。
②`mapping_justification` の語彙表に「同じ論文の別の版での一致」を一級の値として足すか（いまは evidence の印と
表示の呼び名）。③学生の記号の吹き出しで、概念との対応が無いときに「まだ記録されていません」と言う事実文は
入れていない（学習画面は面を跨ぐ共有ファイルのため・次の波）。④標準の説明の査読を取り消す（査読待ちに戻す）
操作はカードに無い（C層の状態遷移の語彙と合わせて決める）。

### pre-mortem（第 5 周の是正の設計時の検査）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 文の近さで同一性を決める | `extent-decided-by-proxy-signal` | 同じ論文の別の版どうしでは代理の手がかりを使わず、本文の一致だけで対を作る |
| 版の規則の根拠を既存語彙に寄せて別の名で見せる | `wording-mismatch` | 印を evidence に残し、一覧が根拠の呼び名と一致した材料を出す |
| 親（エントリ）の確定で子（リンク）の判断の置き場を閉じる | `completion-defined-by-proxy` | 済み = 親と子の両方が決まったこと。確定済みエントリも未決リンクがあればキューに残す |
| 確定済みの記録だけを読む表示に書き手が無い | `last-mile-missing` | 記号の候補づくり（規則 ⑥）と詳細の区画・入口を作り、確定は既存の弁 |
| 著者に結んだ権限で、機械生成物の判断者が宙に浮く | `permission-and-affordance-asymmetric` | 論文の編集者を査読者にし、広げるにはもう一人。画面の可否（`can_review`）と PATCH の 403 は同じ判定 |
| 既存の判断（却下・確定）を作り直しで上書きする | `negative-decision-second-class` | 作り直すのは未決の chunk-proxy 候補だけの部品だけ。人の判断が 1 本でもあれば触らない |

## 第 6 波の追補（2026-10-27・第 25 周 面 A 第 6 周の是正）

審判 `uxsim/runs/round25/judge_share_r6.md`（§3 是正案・§5 オーナー判断の候補）を根で束ねて是正した。
オーナー判断 2 件は推奨どおりに決定し、ここと `knowledge_objects_design.md` §12.5 に記す。

**オーナー判断（推奨どおり）**

| # | 判断 | 採用 | 実装 |
|---|---|---|---|
| IK-1012 / IK-1020 / IK-1021 | 再解析で名前が変わった部品に付いた人の記録と参照を、新しい部品へ付け替えるか | **出典（block）の重なりで旧部品 ⇄ 新部品が 1 対 1 に決まるときだけ**付け替え、`element_id_remap` に `remap_kind="superseded_component"` で記録する。決まらなければ旧行に残し、教員・学生の画面で「この記録は再解析前の部品に付いています」と事実文で見せる（消さない）。凍結したコースの ⚓ は新しい凍結から DB UUID でも部品を指し、旧い凍結は読み時に記録を辿る | `knowledge_objects_design.md` §12.5（`remap.reanchor_superseded_components` / `core/frozen_component_refs.py`） |
| IK-1023 | 教員の説明を他コースへ広げる「著者以外の査読者」は誰か | **標準の説明と同じく、その部品の論文を編集できる教員**（著者以外）。著者が査読して共有 ON にした説明にだけ「もう一人の査読者として査読する」を記帳でき（状態は変えず `action="additional_review"`・`changed_by` が査読者 = 監査に残る）、既存の `NON_AUTHOR_REVIEWED_SQL` がそのまま「著者以外の査読承認」として読む。本文・題名・共有は著者のまま。**引用は届く理由にしない** — 引用の応答は引用先の学生に届くかを `LEARNER_EXPLANATION_SCOPE_SQL` で評価して言う | `core/explanation_governance.py`（`EXPLANATION_PERSONAL_SECOND_REVIEW_ONLY` / `citation_delivery_fact`）・`routes/theory_components.py`（`_row_to_explanation_out(..., personal_reviewers=)`・`patch_explanation` の `personal_second_review`・`cite_explanation` の `outcome_fact`） |

**是正（根ごと）**

- **版をまたぐ同一性の候補**（IK-1020）: 片側の部品が superseded でリンクが一覧から隠れた候補は、残ったリンクに
  「別の論文の記述と…」の保存値を言わせず `PARTNER_REPLACED_REASON` に差し替え（元の文は `recorded_reason`）、
  カードに `partner_replaced_note`。旧規則の注記（`stale_rule_note`）の判定は**隠したリンクも含めたリンク全体**で行う。
  付け替えが決まった組は remap でリンクが新部品へ移り、そもそも半端にならない。
- **対の判断の材料**（IK-1026・IK-0953 の一覧）: 候補の行の確認の問いは、同じエントリの他の行を相手として
  「部品「A」（TeX 版『題名』）と部品「B」（PDF 版『題名』）は、同じものですか（…）」と名指しする
  （`identity_links.pair_confirm_question`・版は `document_format_label` を常に運ぶ・相手が無ければ従来の問い）。
  決めた行に `decision_line`「〔確定〕 確定: 名前・日付」（見送りも同型）、リンクの確定・見送りの応答に
  `outcome_fact`（判断した人・日付・学生に出る条件）。エントリ詳細の確定者にも日付。
- **否定の判断の読み手**（IK-1024）: ラベルの見送りは行を消さず、詳細の区画の折りたたみ「見送り済みのラベル」に
  「見送り: 理由（見送った人・日付）」と［見送りを取り消す］（`POST /entries/{id}/labels/{label_id}/restore`・
  理由必須・監査 `label_restore`・行は 1 本のまま・retired は 409）。見送りの応答に `facts`。
- **照合キーの正規化**（IK-1022）: 記号の照合キーで TeX の間隔命令（`\!` `\,` `\;` `\:` `\>` `\ ` と `\quad` `\qquad`
  ほか）を落とす。正本は `core/theory_modules/schema.py::strip_tex_spacing`、`symbol_match_key` / `clean_symbol_name` /
  `symbol_lookup.symbol_key` が使う。
- **出口の案内の契約**（IK-1025）: テキスト選択で帰属した痕跡（segment / chunk）の旅の出口は、帰属カード
  （AI の帰属候補だけを並べる）を勧めず、式・主張の文だけを選び直して問う手だけを言う
  （`personal_graph.nearby.FACT_SELECTED_SHARPEN`）。
- **注記の粗さ**（IK-0954 の残り）: 「結びついた教材の部品」の区画注記に凍結の条件
  （この共通部品に凍結した版があるとき・無いうちは学生に出ない）を足した。

**新しいアンカー**: `knowledge-library.labels-dismissed` / `knowledge-library.label-restore`（マニュアル
`teacher/19-admin-knowledge-library.md#labels-dismissed` / `#label-restore`）。

**残した課題**: ①旧い凍結で、凍結時の部品名が live・superseded のどの行とも一致しない ⚓ は従来の解決のまま
（再凍結で `db_id` が付く）。②付け替え先が決まらなかった説明・リンクを教員が手で新しい部品へ移す操作は無い
（事実文で見せるだけ — 手で移す入口は次の判断）。③段の再実行で古い対の候補を作り直す（superseded にする）
処理は入れていない（付け替えで足りない残りは事実文）。④教員の説明の査読を取り消す遷移は C層の状態語彙と合わせて決める。

### pre-mortem（第 6 周の是正の設計時の検査）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 再解析で人の記録が取り残される | `reexecution-overwrites-human-decision` | 出典の重なりで 1 対 1 に決まる組だけ運び、決まらなければ事実文 |
| 凍結の参照が run ごとの番号 | `id-not-stable-across-versions` | 凍結に UUID を足し、旧い凍結は凍結時の名前と記録だけで辿る |
| 部品名の写しの共通部品と比べる問い | `same-name-different-referents` | 対の相手（版・題名・部品名）を名指しして問う |
| 規則はあるが操作する人がいない（著者以外の査読） | `permission-and-affordance-asymmetric` | 論文の編集者に入口を出し、PATCH の 403 と画面の可否を同じ判定にする |
| 見送りが見えない・戻せない | `negative-decision-second-class` | 見送り済みを理由・判断者付きで残し、戻すのも理由必須の遷移 |
| 案内先が空 | `last-mile-missing` | 痕跡の出所で実行できる出口だけを言う |
| 照合キーの正規化が二重 | `duplicate-canonical-sources` | 間隔命令の除去は theory_modules の 1 関数を両方の照合キーが使う |

原理 `sync-by-stable-key-and-protect-human-decisions` を読んだ（consulted — 参照の結び直しを出典の重なりへ広げた）。

## 第 7 波の追補（2026-10-28・第 25 周 面 A 第 7 周の是正）

審判 `uxsim/runs/round25/judge_share_r7.md`（§3 是正案・§5 オーナー判断の候補 1）を根で束ねて是正した。
オーナー判断 1 件は推奨どおりに決定し、ここと `knowledge_objects_design.md` §12.6 に記す。

**オーナー判断（推奨どおり）**

| # | 判断 | 採用 | 実装 |
|---|---|---|---|
| IK-1051 / IK-1012 | 再解析より前に凍結したコースの ⚓ を、再解析の後どう扱うか | **凍結した本文は変えない**。⚓ の参照だけを、出典（block）の重なりで 1 対 1 に決まるときに限り後継へつなぐ（新しい凍結・再解析時の付け替えと**同じ規則** `remap.match_superseded_components` を読み時に当てる）。決まらなければ学生には凍結時の部品名 + 事実文（1 対 1 に決まらない =「後継が決まっていません」/ 重なる新しい部品が 0 件 =「見つかりません」）、所有者には原稿スタジオの見出しで凍結し直しの案内（G層 To-Do は作らない・**自動で凍結し直さない**） | `knowledge_objects_design.md` §12.6（`remap.read_time_superseded_successor` / `frozen_component_refs.stale_frozen_component_labels` / `frozen_anchor_stale_notice`） |

**是正（根ごと）**

- **判断の後の読み手**（IK-1050・IK-0953 の残り・IK-1024 の残り）: 同一性リンクの［見送り］は**理由必須**
  （`POST /api/admin/deliberation/identity-links/{id}/reject` が body `{"reason": "…"}` を取り、空は 422 —
  KR7 をエントリの見送り・確定の取り消しとそろえた）。理由は行の `revoke_reason` 列に入れ、監査 metadata にも残す
  （**列の読み替え**: `revoke_reason` は「否定の判断（見送り・取り消し）の理由」の列。見送った行は取り消せない
  ＝ 2 つの理由が同じ行に並ぶことは無い。migration を増やさない判断）。判断した行は「片側の置き換わり
  （`PARTNER_REPLACED_REASON` — 判断した行にも保存値より先に出す・元の文は `recorded_reason`）→ 判断の印
  （`decision_line`）→ 理由（新キー `decision_reason_line`「見送りの理由: …」・理由の無い旧い行は「記録なし」）」の
  順に出る。札の訳語はサーバの `identity_links.IDENTITY_LINK_STATUS_LABELS`（新キー `status_label` — candidate /
  confirmed / rejected「見送り」/ revoked「確定を取り消し」まで覆う）で、admin.js の一覧も詳細もこれを優先する
  （英語の「[rejected]」は出ない）。取り消しの行は新キー `revoke_line`「〔取り消し済み〕 取り消し: 名前・日付（元の
  確定: 名前・日付）」（一覧では `decision_line` に入る）。見送った概念のカード（`include_dismissed=true`）は
  `decision_line`「〔見送り〕 見送り: 名前・日付」/ `decision_reason_line` / `restore_note`（戻すのは詳細の
  ［候補に戻す］）を出し、**判断のボタンは候補のときだけ**。ラベルの足し直しの文は戻す手（「見送り済みのラベル」の
  ［見送りを取り消す］）を指す。
- **候補の規則の粒度**（IK-1052・`core/library/symbol_concepts.py`）: ①1 文がほかの記号も定義するときは、
  照らす範囲を**その記号を述べる部分**（直前のほかの記号の後ろ〜直後のほかの記号の前）に絞り、記号の名前（自分・
  ほか）は伏せてから語境界一致を見る（`_symbol_clause`）。②別名がほかの記号名の一部（「TOV」⊂ `N_TOV`）なら、
  その別名では候補を立てない（`_alias_inside_other_symbol` — 正式名は対象外）。③同じ論文の別の版（`same_paper`）で
  照合キー（`symbol_lookup.symbol_key`）の同じ記号には、一方の版の候補・確定の結び付きを、もう一方の版に **candidate**
  として写す（`_carry_over_same_paper_versions`・理由「同じ論文の別の版の同じ記号「…」が、概念「…」と結び付いています
  （確定済み）。…」・確定は 1 本ずつ教員 = KR2・見送り・取り消しは写さない）。複数の記号を定義する文から立った候補の
  理由には「この定義の文はほかの記号（…）も定義しています。照らしたのは、この記号を述べる部分だけです。」を足す。
  ASCII の 1 文字の記号（`r`）は区切りの手がかりにしない（本文の語と紛れるため）。
- **査読の帰属**（IK-1023 の残り）: 説明カードに新キー `review_line`「査読: 名前・日付（著者）／ 名前・日付」
  （いまの承認の期間の記帳 = `STANDARD_REVIEWERS_SQL` と同じ材料・名前は profile_name）。
- **取り残された説明の置き場**（IK-1012 の残り）: `GET /api/admin/theory-components/{id}/stranded-explanations`
  （読み取り専用・UUID を返さない・`facts` + 各行 `kind_label` / `title` / `body` / `origin_line` / `rewrite_note`）。
  作成者の行には「新しい部品の［追加］から書き直してください（元の記録は残ります）」、ほかの行には「書き直せるのは作成者
  だけです」、標準の説明には「新しい部品の要約から作り直されます」。原稿スタジオの説明一覧に
  ［再解析前の説明を開く（読み取り専用）］。
- **TeX のラベル**（IK-0681 の学生の吹き出しの面）: `symbol_lookup` は式の表示名・`definition.equation_label` に
  TeX の label キー（`eq:tov_residual`）を出さない（判定の正本 `display_projection.is_tex_label_key`）。

**新しいアンカー**: `lecture-studio.explanation-stranded-open`（マニュアル `teacher/14-admin-lecture-studio.md#explanation-stranded-open`）。

**残した課題**: ①凍結した教材の**主張**の ⚓（`![[claim:…]]`）の版をまたぐ解決は本波の対象外（部品の ⚓ だけ）。
②読み時の付け替えは `element_id_remap` に記帳しない（書かない読み時の導出）— 凍結し直すと新しい凍結の `db_id` で直接つながる。
③見送った同一性リンクを候補に戻す遷移は無い（見送りは理由付きで残るが、戻すときは別の組として作り直すしかない）。
④IK-0681 の本体（TeX 経路の式に印字番号が無い）は残る。

### pre-mortem（第 7 周の是正の設計時の検査）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 判断前の行だけ整え、判断後の行が保存値・生の状態語に戻る | `negative-decision-second-class` | 判断した行も片側の置き換わり → 印 → 理由の順に出し、札は状態語彙を全部覆うサーバの表で引く。リンクの見送りも理由必須 |
| 旧い凍結の参照が再解析で一斉に切れる | `id-not-stable-across-versions` | 新しい凍結と同じ規則を読み時に当て、決まらないときは原因（0 件 / 決まらない）を言い、所有者に凍結し直しを案内（自動で凍結し直さない） |
| 1 文の語の出現を複数の記号の性質とみなす | `substring-match-false-positive` | その記号を述べる部分だけを照らし、記号名を伏せ、記号名の一部の別名を使わない |
| 同じ論文の別の版で記号リンクが届かない | `id-namespace-conflated` | 照合キーの同じ記号へ candidate として写す（確定は人） |
| 学生の画面に内部の TeX label キー | `contract-changed-one-side` | 判定の正本 `is_tex_label_key` をサーバの DTO で通す（JS は素通しのまま） |

原理 `sync-by-stable-key-and-protect-human-decisions` を読んだ（consulted — 「参照を結び直す」部品を書き込みの無い読み時の
導出へ広げた。非適用: 読み時に `element_id_remap` へ記帳する派生形 — 読み手が書くと閲覧が記録を変える）。
