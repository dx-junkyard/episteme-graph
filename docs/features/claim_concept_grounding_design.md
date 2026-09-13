# 主張の概念接地（Claim Concept Grounding — 主張の `concepts` 欄に分野の言葉を供給する）

> **状態: 実装済み（正本・凍結）**（2026-09-13 起票・同日実装。migration なし。実装記録は §10。以後は §10 の追記のみ）。
> 親文書は [知識構造の見直し提案 2026-09-12](../architecture/knowledge_structure_review_2026-09-12.md) の D4 / 付属調査
> [E 概念同一性](../architecture/knowledge_structure_review_2026-09-12/E_concepts.md) **K-2**。
> [概念レジストリ](concept_registry_design.md)（Phase 3）が §12 で非スコープとした「`claim.concepts` の concept 層」の追補で、
> レジストリ（KR1〜KR10）と Phase 0 の語境界照合（P0-2）・記号判定（P0-3）を前提にする。

**オーナー判断（本書の前提・2026-09-13）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| CG-O1 | backend が合成した辞書を `ClaimObjectBuilder` の既存注入口 `concept_resolver` に渡し、A層出力の**値**を変えることは「A層非改変」の範囲か | **範囲内** | A層のコード・dataclass・出力の形は不変。変わるのは既存の差し替え口へ渡す材料だけ |
| CG-O2 | LLM による概念抽出ステージの新設（A層への agent 追加） | **実測後に判断**（本書では実装しない） | 本書の決定論経路で主張の何割に概念が付くかを見てから |

---

## 1. 問題（K-2）

主張（claim）の `concepts` 欄は「この文は何についての話か」を持つ欄だが、実データ 610 件は**すべて数式の記号**で、分野の言葉が
1 つも無い。原因は配管: `claim_object_builder` は概念を自分で決めず、外から渡される辞書（`concept_resolver` / `cartridge_ontology`）で
本文を照合する設計だが、orchestrator は `cartridge_ontology=None` で組み立てており、分野未指定の解析経路では辞書が**空**になる
（`orchestrator._build_claim_objects`）。記号だけが残るのは `equation_claim_synthesis` が式由来の合成 claim に記号を付けるため。

その結果、①論文どうしの共通概念が偽の一致（`R λ φ …`）②学習者の概念マップに 1 文字の羅列 ③概念での検索・再利用が主張のレベルで不能
④Phase 3 のレジストリに主張からの糸が来ない。

## 2. 不変条項（CG1〜CG7）

| # | 条項 | 具体 |
|---|---|---|
| CG1 | **A層非改変** | `src/episteme_graph/agents/` を触らない。使うのは既存の `concept_resolver` 注入口と、既存フックと同型の between-stage 決定論後処理（`_hook_*`） |
| CG2 | **決定論・LLM 0 回・embedding 0 回** | 辞書照合は `agents/alias_matching.text_mentions_alias`（語境界付き・P0-2）と `normalize_label` の完全一致のみ。DSL ノードは既に LLM が作った成果物の**参照**を読むだけ |
| CG3 | **概念は候補・確定は人間** | 付いた概念で `concept_assignment_status` を `source_backed` に**昇格させない**（builder が計算した値を後段フックが上書きしない）。レジストリへの糸は `element_identity_links` の candidate |
| CG4 | **出所を混ぜない**（原則8） | 概念ごとに `source ∈ {registry_label, cartridge_alias, dsl_node, dsl_reference}` と `mapping_justification` を保持する。A層 `ClaimConcept` に列を足せないので、出所は別 artifact `claim_concept_grounding` に持ち、永続化時に `theory_claims.concepts` の各要素へ additive にマージする |
| CG5 | **情報を落とさない** | 既存の記号 mention は残し追加のみ。正規化で `name` を書き換えない（`canonical` 併記 = KN-2） |
| CG6 | **記号は概念にしない** | 辞書に載せる名前・照合結果とも `is_symbol_like_concept_name`（P0-3）で記号を除く。学習者の概念マップも同じ判定で記号を出さない |
| CG7 | **数値非表示・閉世界** | 一致件数・被覆率を教員・学習者に出さない。取りこぼしは `coverage`（P0-10 の共通形式）で artifact に記録するだけ |

## 3. 全体像

```
[claim_object_builder]  ← resolver = 辞書②レジストリ confirmed entry のラベル + ③cartridge 別名   （案 B・前段）
        ↓ … symbol_registry / derivation_chain / … / thesis_reconstruction / dsl_linking
[_hook_claim_concept_grounding]（新フック・dsl_linking 直後）                                      （案 B・後段 + 案 A）
   ①DSL ノード名を辞書に加えて主張本文を語境界照合（source=dsl_node）
   ①' DSL ノードの source_refs.claim_ids で直接指された主張にそのノード名を付ける（source=dsl_reference）
   → ctx.claim_objects.claims[].concepts へ追加（重複は normalized で畳む）・claim_object_builder artifact を再保存
   → claim_concept_grounding artifact（claim_id → [{normalized, source, entry_id?, mapping_justification}] + coverage）
        ↓
[persist_claims_components_graph]  theory_claims.concepts の各要素に source / entry_id / mapping_justification をマージ
        ↓
[identity_candidates]  ④ 新規則: grounding に entry_id を持つ主張 → element_identity_links candidate（theory_claim → entry）
学習者: create_course の概念マップから記号を除く（案 E）
```

## 4. 辞書（`backend/core/library/concept_dictionary.py`・新設・FastAPI / `core.llm` 非 import）

- `build_concept_dictionary(session=None, *, cartridge_id: str | None, dsl=None) -> ConceptDictionary`
  - ② `store.list_entries(include_candidates=False)`（= `review_status='confirmed'` かつ `status='active'`）の `name` +
    `registry.labels_for_entries(...)` の alternate / hidden ラベル → `source="registry_label"`・`entry_id`・`concept_type = entry_type`。
  - ③ `cartridge_id` が非空のときだけ `load_cartridge_or_none` で ontology の `aliases` / `concept_types` → `source="cartridge_alias"`。
    **空の cartridge_id では読まない**（既定カートリッジへ縮退させない規律）。
  - ① `dsl` が渡されたとき `nodes[].node_value` → `source="dsl_node"`（`node_type` は concept_type として保持・`source_refs.claim_ids` も持つ）。
  - すべての名前に `is_symbol_like_concept_name` を掛け記号を除く（CG6）。`normalize_label` をキーに畳み、同じキーに複数 source が
    あれば **registry_label > cartridge_alias > dsl_node** の順で 1 つを代表にし、他は `also_from` に残す（CG5）。
  - DB 不達は空辞書（fail-soft・従来動作）。
- `make_concept_resolver(dictionary) -> Callable[[text, role_labels, ontology], list[ClaimConcept]]`
  - `text_mentions_alias` で照合し `ClaimConcept(name=一致した表記, normalized=normalize_label, concept_type=辞書の型, role="unknown")` を返す。
  - 戻り値は A層の型契約（`coerce_claim_concepts` が包む）。出所は resolver の外側で `dictionary.provenance(normalized)` から引く。
- `mapping_justification`: registry_label / cartridge_alias → `lexical_match`（`cartridge_declared` は別名の由来であって照合方法ではないので
  使わない）/ dsl_node → `lexical_match` / dsl_reference → `llm_candidate`（LLM が主張とノードを結んだ参照を写しただけ）。

## 5. orchestrator の配線

- `_stage_claim_object_builder` → `_build_claim_objects(..., concept_resolver=, cartridge_ontology=)`（additive kwarg・既定 None で従来どおり）。
  辞書は `build_concept_dictionary(cartridge_id=ctx.cartridge_id)`（DSL はまだ無い）。`cartridge_ontology` は cartridge が解決できたときだけ渡す
  （builder の `_concepts_are_cartridge_backed` が従来の設計どおり効く。空 cartridge では None のまま）。
- 新フック `_hook_claim_concept_grounding(ctx)` を `_PIPELINE_STEPS` の **`dsl_linking` の直後**（`dsl_embedding` の前）に
  `PipelineStageDef(None, _hook_claim_concept_grounding)` で登録（既存フック 3 件と同型: artifact ゲート無し・毎回走る・非致命）。
  本体は `core/library/claim_concept_grounding.py::ground_claims(claim_objects, dsl, dictionary) -> GroundingResult`（純関数・入力の
  claim_objects を**新しいオブジェクトで返す**か、その場で追記する — どちらでも可だが docstring に明記）。フックは結果を
  `ctx.claim_objects` に反映し `ctx.save_artifact("claim_object_builder", ctx.claim_objects)` と
  `ctx.save_artifact("claim_concept_grounding", result.to_dict())` を行う（`_attach_coverage` で `coverage` を付ける —
  母集合 = claim 数・processed = concept 層 mention が 1 つ以上付いた claim 数・理由 `no_dictionary_match`）。
- `concept_assignment_status` は触らない（CG3）。`is_atomic` / `support_status` も不変。

## 6. 永続化（`persistence.persist_qualified_claims`）

- `claim_concept_grounding` artifact（`ctx.artifact(...)` 経由で渡す additive kwarg `concept_grounding=None`）があれば、
  `theory_claims.concepts` の各要素（dict）に `source` / `entry_id` / `mapping_justification` / `canonical` を additive にマージ
  （`normalized` キーで突合・無い要素はそのまま）。**既存行の意味は変わらない**（キー追加のみ・KO7 の型語彙 FK には触れない）。
- `theory_claims` に列は足さない（migration なし）。

## 7. レジストリへの糸（`identity_candidates` 規則 ④）

`core/library/identity_candidates.py::run_identity_candidates` に規則 ④ を追加: 当該 document の `theory_claims_live` のうち
`concepts` 要素に `entry_id` を持つ行 → `element_identity_links` candidate（instance = `theory_claim`（DB UUID）・
`mapping_justification` は要素の値・`local_expression = {"name": 一致した表記}`・`evidence = [{"claim_text": 先頭 200 字}]`）。
上限は既存 `IDENTITY_CANDIDATES_MAX_PER_DOCUMENT` を共有（規則 ①〜③ と合算・超過は coverage）。dismissed は再提案しない（既存規律）。

## 8. 学習者の概念マップ（案 E・`routes/admin.py`）

- `create_course` の `data.concepts` 組み立てで、`name` / `children[]` を `is_symbol_like_concept_name`（+ 1 文字）で除く。
  除いた名前は捨てずに `topic`/course_data の `excluded_symbol_concepts` に残す（CG5・学習者には出さない）。
- コースビルダーの材料文脈（`_build_material_context` / 教材一覧 `top_concepts`）の概念供給も同じ判定で記号を除き、
  Phase 2 の `learning_units.teaches` があればそちらを先に並べる。
- course_mapping agent（A層）の `introduced_concepts` フォールバックは触らない（CG1）。

## 9. ガードレール・検証・非スコープ

- `test_claim_concept_grounding_{dictionary,hook,persist,course}.py` + `test_claim_concept_grounding_guardrails.py`
  （`core/library/{concept_dictionary,claim_concept_grounding}.py` が fastapi / `core.llm` を import しない・`text_mentions_alias` 以外の
  部分文字列一致（`in text`）を書かない・記号名を辞書に入れない・`concept_assignment_status` を書き換えない・空 cartridge で
  `load_cartridge` を呼ばない・フックが `dsl_linking` 直後にある・`persist_qualified_claims` の既存キー不変）。
- 実測: 開発 DB の 2 論文で「concept 層 mention が付いた claim / 全 claim」を artifact の `coverage` で確認し、§10 に**事実として**記す
  （数値は設計記録にのみ。UI に出さない）。→ CG-O2 の判断材料。
- **非スコープ**: LLM 抽出ステージ（CG-O2）/ component `_fill_concepts` の辞書拡張（A層）/ `claim.concepts` の学習者向け直接表示 /
  `theory_claims` への列追加 / 記号 mention の削除。

## 10. 実装記録

**2026-09-13 実装（担当 X: §4〜§7。§8 の案 E は担当 Y のコース側実装）。migration なし・新 API なし・
LLM 呼び出し 0 回・embedding 0 回。**

### 10.1 新設ファイル

| ファイル | 中身 |
|---|---|
| `backend/core/library/concept_dictionary.py` | `ConceptDictionary`（`entries: {normalized → {canonical, names, concept_type, source, entry_id, also_from, claim_ids}}` / `add()` / `match()` / `provenance()`）・`build_concept_dictionary(session=None, *, cartridge_id, dsl=None)`・`make_concept_resolver(dictionary)`・`cartridge_ontology_for(cartridge_id)`・出所 4 語彙（`SOURCE_REGISTRY_LABEL` / `SOURCE_CARTRIDGE_ALIAS` / `SOURCE_DSL_NODE` / `SOURCE_DSL_REFERENCE`）と `justification_for_source()`・`MAX_CONCEPTS_PER_CLAIM=8` |
| `backend/core/library/claim_concept_grounding.py` | `ground_claims(claim_objects, dsl, dictionary) -> GroundingResult`（**`claim_objects` をその場で変更**）・`GroundingResult.to_dict()`・`merge_grounding_into_concepts(concepts, grounded)`（§6 の純関数）・`REASON_NO_MATCH="no_dictionary_match"` |

どちらも FastAPI / `core.llm` / sqlalchemy の直接依存なし（`core/library/` の既存ガードレール
`test_concept_registry_guardrails.py` の `DELETE FROM` 不在検査・FastAPI 非 import 検査もそのまま掛かる）。

### 10.2 配線

- **前段（案 B）**: `orchestrator._claim_concept_inputs(ctx)` が辞書（DSL なし）から
  `(concept_resolver, cartridge_ontology)` を作り、`_stage_claim_object_builder` →
  `_build_claim_objects(..., concept_resolver=, cartridge_ontology=)`（additive kwarg・既定 None）で
  **既存の注入口**へ渡す。辞書が空なら `(None, None)` = 従来動作。`cartridge_ontology` は cartridge が
  解決できたときだけで、空 `cartridge_id` では読まない。
- **後段（案 B + 案 A）**: `_hook_claim_concept_grounding(ctx)` を `_PIPELINE_STEPS` の
  **`dsl_linking` の直後・`dsl_embedding` の前**に `PipelineStageDef(None, ...)` で登録
  （フックは 3 件 → **4 件**、`_PIPELINE_STEPS` は 33 → **34 要素**。`docs/pipeline/overview.md` §2 に追随）。
  フックは辞書を DSL 込みで組み直し → `ground_claims` → 変化があれば `claim_object_builder` を保存し直し →
  `claim_concept_grounding` artifact を保存。非致命（例外は warning のみ・`return False`）。
- **artifact の形**: `{"claims": {claim_id: [{normalized, name, canonical, source, entry_id,
  mapping_justification}]}, "population", "processed", "reasons", "coverage"}`。`coverage` は
  `_attach_coverage`（P0-10 の共通形式・`unit="claims"`・母集合 = claim 数・processed = 概念層 mention が
  1 つ以上付いた claim 数・理由 `no_dictionary_match`）で組み立てる（自前 dict を書かない）。
- **永続化**: `persist_qualified_claims(..., concept_grounding=None)`（additive kwarg）→
  `_build_claim_items` → `merge_concept_grounding()`。`concepts` の各要素に `source` / `entry_id` /
  `mapping_justification` / `canonical` を `normalized` キーで additive にマージする。**既にある値は
  上書きしない**・当たらない要素はそのまま・列は増やさない。
- **レジストリへの糸（§7 規則 ④）**: `identity_candidates.load_grounded_claims()` +
  `_claim_concept_links()`。`theory_claims_live`（基表は FROM しない = KO5）の `concepts` 要素に
  `entry_id` を持つ行 → `element_identity_links` の candidate（instance = `theory_claim` の DB UUID・
  `local_expression={"name": 一致した表記}`・`evidence=[{"claim_text": 先頭 200 字}]`・
  `mapping_justification` は接地が記録した値）。**entry は作らない**（既に確定済みのエントリを指す）。
  上限は既存 `IDENTITY_CANDIDATES_MAX_PER_DOCUMENT` を規則 ①〜③ と合算し、確定・公開中でない
  エントリは指さず、既にリンクのある `(claim, entry)` の組は再提案しない。component が 0 件の
  document でも規則 ④ は走る（主張は別に在るため、`not parents` の早期 return をやめた）。

### 10.3 実装上の判断（設計書との差分）

1. **別名も記号判定を通る（CG6 の適用範囲）**: `SM` のような 2 文字の略号は
   `is_symbol_like_concept_name` で記号側になるため照合表に入らない。「名前・照合結果とも記号を
   除く」（CG6）の素直な適用で、短い別名の誤爆（F-7）をそもそも作らない側に倒した。
2. **`cartridge_ontology` の `concept_types` は `examples` から導く**: カートリッジの
   `ontology.json` は `concept_types` を `{id, label, examples}` のリストで持つ一方、builder は
   `{canonical: type}` の写像を期待する。A層は非改変なので backend 側で決定論的に写像する
   （`examples` に載っている名前 → その型 id）。
3. **`concept_assignment_status` は builder の計算のまま・ただし registry だけの辞書では昇格を塞ぐ（CG3）**: フックは
   概念を足すだけで status を触らない。A層 builder の既存規則 `_concepts_are_cartridge_backed` は「ontology が空で
   resolver がある」と無条件に True を返すため、cartridge が解決できない run では atomic × `source_backed` の claim が
   `source_backed` に上がり得た。これを A層に触らずに塞ぐため、orchestrator は cartridge 不在時に
   `concept_dictionary.REGISTRY_ONLY_ONTOLOGY`（別名・型が空で `provenance="concept_registry"` だけを持つ**非空**の dict。
   既知集合が空なので `inferred` に留まる）を deepcopy して渡す（指揮者の追記。`test_claim_concept_grounding_hook.py::
   TestRegistryOnlyOntologyKeepsInferred`）。cartridge が解決できた run では従来どおり cartridge 由来の概念だけが
   `source_backed` の根拠になる。
4. **resume との関係**: `claim_concept_grounding` は `PIPELINE_STAGES` に無いキーなので、
   `start_stage` 指定の部分再実行では前回 run の artifact 絞り込み（`stage_order` に無いキーは
   落とす）から外れる。フック自体は毎回走るため、`dsl_linking` より後ろから再開した run では
   接地の出所が付かないまま永続化される（概念そのものは `claim_object_builder` artifact に
   残っているので落ちない）。前回値を無言で流用しない側に倒した結果。
5. **規則 ④ の打ち切り理由**: `run_identity_candidates` の `coverage` は単位が `components` なので、
   主張側だけが上限で打ち切られた場合の理由コードは（`truncated == 0` のとき共通形式が理由を
   落とすため）表に出ないことがある。件数を出さない規律を優先し、`coverage` の単位は変えていない。

### 10.4 実測（CG-O2 の材料）

**未実測**（この作業環境では Docker デーモンが起動しておらず、開発 DB の採用 run の artifact を
読めなかった）。決定論・非LLM の経路なので、`claim_concept_grounding` artifact の `coverage`
（`population` / `processed`）を 2 論文で読めば「概念層 mention が付いた claim / 全 claim」がそのまま
分かる。**数値は本節にのみ記し、UI には出さない**（CG7）。

### 10.5 テスト

`backend/tests/test_claim_concept_grounding_{dictionary,hook,persist,guardrails}.py`（新設）+
`test_concept_registry_identity_candidates.py::TestGroundedClaimLinks`（規則 ④）+
`docs/pipeline/overview.md` §2 に追随（`test_docs_registry_guardrails.py` / `test_pipeline_stage_registry.py` /
`test_pipeline_coverage_report.py` は既存のまま通る）。§9 の `test_claim_concept_grounding_course.py`
（案 E = 学習者の概念マップ）は担当 Y の範囲。

### 10.6 案 E（担当 Y）と訂正

- **`create_course` の所在は `backend/api/routes/learning.py`**（`POST /api/learning/courses`。§8 の「`routes/admin.py`」は誤り —
  admin 側の `# Build concepts` は登録済みコース → draft 変換で、登録時に除去済みのため触っていない）。
- 記号判定の共通述語は `core/course_data.py::is_symbol_concept_name`（A層 `is_symbol_like_concept_name` に委譲 + 1 文字。第 2 の
  正規表現を書かない）。`create_course` は `data["concepts"]` の `name` / `children[]` から記号を除き、外した名前を
  `learning_courses.data.excluded_symbol_concepts`（トップレベル・出現順・重複除去・空ならキー自体を足さない）に残す。`name` が記号なら
  `children` ごと、`name` が概念なら記号の `children` だけを外す。学習者向け `LearningCourseDetail` はホワイトリスト射影なので漏れない
  （テストで固定）。
- コースビルダーの材料文脈 `_build_material_context` と教材一覧 `top_concepts` に同じ判定を掛け、
  `core/course_units.py::unit_concept_terms_by_document`（`learning_units_live.teaches` の concept 項・`dismissed` 除外・fail-soft）の
  言葉を先に並べる。上限は既存のまま。
- docs: `docs/README.md` / `layer_registry.md`（概念レジストリ層の行に追補として併記）/ `docs/features/learning.md` §1.2。
- テスト: `test_claim_concept_grounding_course.py`（36 件）。

検証（追補全体）: backend 15,389 pass / src 1,924 pass（2026-09-13・赤ゼロ）。実 DB での実測（§10.4）は docker 復帰後。
