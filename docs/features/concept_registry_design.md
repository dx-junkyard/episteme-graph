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
  1. `canonical_symbol` または `notation_variants` に一致する live 行を集める（一致は `normalize_key`（`concept_normalizer`）の完全一致・部分一致なし）。
  2. **ScholarPhi 規則 = 直前の定義**: タップ位置（`equation_id` → `knowledge_equations_live` の `block_id` / `section_id` の順序、
     無ければ `chunk_id` の `chunk_index`）より**前**にある `defining_equation_ids` / `source_evidence_ids` の定義のうち最も近いものを 1 件
     （`definition_evidence_texts` の逐語）。前に無ければ後方の最初の定義を「この位置より後で定義されています」の事実文付きで返す。
     定義が無ければ `definition_status` のラベル（`element_vocab.DEFINITION_STATUS_LABELS`）と「この論文には定義の記述が見つかりませんでした」。
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
