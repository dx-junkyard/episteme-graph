# 学ぶ単位の一級化（Learning Units — 論文の「教える単位」を行にし、コース topic をその並びにする）

> **状態: 実装済み（正本・凍結）**（2026-09-13 起票・同日実装。migration **081** `learning_units`。実装記録は §12。以後は §12 の追記のみ）。
> 親文書は [知識構造の見直し提案 2026-09-12](../architecture/knowledge_structure_review_2026-09-12.md)
> の **Phase 2**（§4 P2-1〜P2-7）。前提は Phase 1 = [知識オブジェクト層](knowledge_objects_design.md)
> （KO1〜KO10。stable_key / supersede / live ビュー / 語彙表 FK の作法をそのまま継承する）。

**オーナー判断（本書の前提）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| O-3 | freeze（コース登録）を「一括確定」として `decision_context` に記帳するか | **(a) 記帳する**（`basis` 定数 1 本追加 = `BASIS_COURSE_REGISTER_UNITS`） | 親文書 §6 の推奨。承認 0 でも学習者に届く唯一の経路が freeze（C-6）。オーナーの着手指示（2026-09-13「Phase 2 を実施せよ」）を推奨案の採用と解し、冒頭に固定する。撤回する場合は §12 に記し記帳を止める（記帳済みの行は残す） |
| O-5 | 承認ゼロで配信されている事実の見せ方 | **(a) 教員に事実文（G層ルール 1 本）** | 親文書 §6 の推奨。学習者へ承認制度の実態を転嫁しない（(b) 却下）・原則8 に反する (c) 却下 |
| UC5/UC7 | 前提の ID 参照化に伴う「適応評価」 | **恒久排除**（学習者の入力を前提検査に混ぜない・習熟推定を作らない） | 親文書 P2-4 / 六つのレンズ F4 の撤去判断を逆行させない |

**正本**: 本ドキュメント。**関連**: [知識オブジェクト層](knowledge_objects_design.md) / [確定文脈の記帳](decision_context_design.md) /
[ガイダンス層](guidance_layer_design.md) / [構造帰属型の問い記録](../backend/rag-chat.md) / [画面文脈アダプター](assistant_screen_adapter_design.md)
（学習チャット Phase 4 の `selection.element`）/ [component 根拠カードの引用チップ化](component_evidence_redesign.md)
（`build_topic_evidence_items` の契約）/ [E層設計](exposition_layer_design.md)（learning_unit は E層の「足場」ではなく A層側の「単位」。E層着手時は本層を翻訳の入力にできる）。

---

## 1. 目的 — 「学ぶ単位」が topic に固定され、成果と文字列で結ばれている

親文書 D3 の診断:

- 唯一の学習単位は `learning_courses.data.topics[]` で、topic ↔ 成果の結合は**タイトル文字列の重なり率**（0.18 / 0.12）。教員が自分の言葉で章立てするほど接続が切れる（C-3）。
- 文章層（skeleton の `logical_blocks` / thesis の `support_structure` / DSL ノード）が最も忠実だが**単位として永続化されず学習者に届かない**（F-14）。
- component 21 件は LLM 原案 8 個を決定論 refinement が分割した断片で、名前が `{Operation}: {親}`・同名 4 件（F-5）。
- 前提知識は名前文字列で、半順序として検査されない（S-17）。
- freeze は承認を経由せず artifact から直接教材を作る唯一の「確定の弁を通らない経路」（C-6）。
- blueprint（語りの弧）の live 消費者ゼロ（C-9）。学習者の痕跡は `seg_0` 固定で構造に着地しない（C-11）。

本層は **learning_unit を Phase 1 の知識オブジェクト層と同じ作法の一級の行**にし、topic を「unit の並び」として
additive に定義し直す。A層（`src/episteme_graph/agents/`）は非改変。既存の freeze・既存 topic の意味は変えない。

## 2. 不変条項（LU1〜LU9）

| # | 条項 | 具体 |
|---|---|---|
| LU1 | **A層非改変・既存キーの意味不変** | `topic.units` / `prerequisites[].topic_id` / `topic.narrative` は additive。`units` が空の topic は従来どおり文字列一致（救済）で動く。既存 freeze の縮小はしない |
| LU2 | **unit は候補始まり・確定は人間** | `learning_units.review_status` は `candidate` 始まり。AI は候補を並べるだけで、topic への束ね（選択）は教員が「承認してコースを登録」で行う。行削除 API なし（`dismissed` 遷移） |
| LU3 | **決定論・非LLM** | unit の導出・stable_key・prerequisites の半順序検査・narrative の持ち込み・段落番号の解決はすべて非LLM。LLM が触るのはコースビルダー outline の「候補から選ぶ」だけで、**候補に無い handle は捨てる**（捏造ガード） |
| LU4 | **stable_key + supersede** | Phase 1 と同じ（`sync_live_rows` / `_live` ビュー / DELETE なし）。topic は unit を `stable_key` で参照するので、再解析後も同じ単位を指す |
| LU5 | **数値を見せない** | `content_confidence` / 一致率 / 件数 / 半順序検査の件数を教員にも出さない。「接続できた / できなかった」「A は B に依存し B は A に依存する」の事実文のみ |
| LU6 | **適応評価の恒久排除** | 前提検査は**コース構造だけ**を入力にする（学習者の痕跡・回答・履歴を読まない）。`check_prerequisites` の判定材料は本人の明示的な「理解している」記帳のみ（是正 F4 のまま） |
| LU7 | **一括確定は decision_context** | コース登録は「提示された unit 候補のうち束ねたもの」の一括確定。DC1〜DC4 に従い提示・適用・代替・再審経路を記帳する。**候補ゼロなら記帳しない**（DC3: 代替の無い確定は記帳できない） |
| LU8 | **配信は止めない** | 承認ゼロで配信されている事実は G層の事実文で教員に見せるだけ（RR7 / 原則12） |
| LU9 | **学習者の痕跡は本人可視のまま** | `learner_selected` アンカーは新 kind を作らず既存 `question` 痕跡の `structure_anchor`。登録簿（`trace_registry`）非改変 |

## 3. 全体像

```
A層 artifact（skeleton / thesis / component_assembly / dsl / figure_table）
  ↓ persistence（決定論・非LLM）                                         ← P2-1 / P2-2
learning_units（stable_key・unit_kind・teaches・出典 block・review_status）+ theory_components.parent_component_id
  ↓ コースビルダー（候補 handle U1..Un を提示 → LLM が topic ごとに選ぶ → 教員が承認して登録）   ← P2-3
learning_courses.data.topics[].units[{kind, stable_key, unit_id, label, source}]
  + prerequisites[].topic_id（題名は表示用併記）+ narrative（blueprint 由来）                    ← P2-4 / P2-6
  ↓ 登録 = 一括確定（decision_context: presented = 候補 / applied = 束ねた unit）                ← P2-5
freeze（build_course_content）: units 優先で成果を束ね、文字列一致は units 空のときの救済のみ
  ↓
学習者: 痕跡が element / 段落に着地（learner_selected・段落番号は表示区画から解決）             ← P2-7
教員: 「承認 0 のまま配信」を G層の事実文で                                                  ← P2-5
```

## 4. DB 設計（migration 1 本・採番は `ls backend/db/` で確認）

### 4.1 `learning_units`（新表）+ `knowledge_unit_kinds`（語彙表）+ `learning_units_live`

Phase 1 の `knowledge_equations` と同じ骨格（`document_id` は UUID + FK CASCADE / `stable_key` / `produced_by_run_id` /
`superseded_at` / 部分 UNIQUE `uq_learning_units_stable_key_live (document_id, stable_key) WHERE superseded_at IS NULL`）。

| 列 | 型 | 意味 |
|---|---|---|
| `id` | UUID PK | |
| `document_id` | UUID FK documents CASCADE | |
| `stable_key` | TEXT | `core/knowledge_objects/stable_key.py::learning_unit_stable_key` |
| `agent_unit_id` | TEXT | 出所 ID（`section_block`=skeleton `block_id` / `thesis_support`=`central_thesis` または `support:{section}:{idx}`（persistence `_thesis_ref_nodes` と同じ表記）/ `parent_component`=LLM 原案の agent component_id / `dsl_node`=node_id / `figure`=FigureRecord.figure_id） |
| `unit_kind` | TEXT FK `knowledge_unit_kinds(kind)` | `core/schema.py::LEARNING_UNIT_KINDS`（5 語彙） |
| `label` / `summary` | TEXT | 表示名 / 1〜2 文 |
| `teaches` | JSONB `[]` | LRMI `teaches` 相当。`[{"kind": "concept"|"claim"|"equation", "ref": str, "label": str}]`。ref は claim = DB UUID（`claim_id_map` 経由）/ equation = agent equation_id / concept = 名前 |
| `order_index` | INTEGER | 論文順（種別内） |
| `section_ids` / `source_block_ids` | JSONB `[]` | 出典の章・ブロック集合 |
| `linked_claim_ids` | JSONB `[]` | DB UUID |
| `linked_equation_ids` | JSONB `[]` | agent equation_id |
| `linked_component_ids` | JSONB `[]` | **DB UUID**（`persist_components` の id_map 経由） |
| `linked_figure_ids` | JSONB `[]` | figure_key（FigureRecord.figure_id） |
| `review_status` | TEXT | `candidate` / `confirmed` / `dismissed`（`LEARNING_UNIT_REVIEW_STATUSES`）。**人間の確定列**（preserved） |
| `teacher_notes` | TEXT | 人間の確定列 |
| `agent_payload` | JSONB `{}` | 素の record + `linked_component_agent_ids`（コース側が artifact と突合するために必須） |
| `produced_by_run_id` / `superseded_at` / `superseded_by_run_id` / `created_at` / `updated_at` | | Phase 1 と同じ |

- `knowledge_unit_kinds(kind TEXT PK, label TEXT)` は `core/schema.py::LEARNING_UNIT_KINDS` と同じ列挙を `ON CONFLICT DO NOTHING` でシード（一致は `test_knowledge_objects_vocab.py` 型のテストで固定）。
- `CREATE OR REPLACE VIEW learning_units_live AS SELECT * FROM learning_units WHERE superseded_at IS NULL;`
- 読み手は `learning_units_live` を読む。基表を SELECT してよいのは `persistence.py` / `versioning/deletion.py` のみ（KO5 のガードレールに追加）。

### 4.2 `theory_components.parent_component_id`（P2-2・nullable 追加）

- `parent_component_id UUID NULL`（同表の live 親行 = LLM 原案。FK は張らない — supersede 遷移で親が superseded になっても子の参照を壊さない）+ `parent_agent_component_id TEXT NULL`。
- **列を足すので、migration 末尾で `theory_claims_live` / `theory_components_live` の `CREATE OR REPLACE VIEW` 2 文を再実行する**（Phase 1 の規律・`test_knowledge_objects_vocab.py::test_migrations_adding_columns_recreate_the_view`）。

## 5. unit の導出（`backend/core/knowledge_objects/learning_units.py`・純関数）

`build_learning_unit_items(document_id, *, skeleton, thesis, component_result, dsl, figures, claim_id_map, component_id_map, evidence_registry) -> list[dict]`
（`sync_live_rows` の `incoming` 形 = `{"stable_key", "agent_id", "values": {...}}`）。入力を mutate しない。素材が無い種別はスキップ。

| unit_kind | 出所 | label / summary | teaches / links | stable_key の材料 |
|---|---|---|---|---|
| `section_block` | `PaperSkeletonResult.logical_blocks[]`（`prior_work` / `meta` も落とさず保持） | `label` / `summary` | 出典 = `evidence_block_ids` / `section_ids`。claims = evidence block → claim（`claim_id_map` の block:span キーで解決できるもの） | `block_type` + 正規化 label + block 集合 |
| `thesis_support` | `central_thesis` + `support_structure[section][idx]` | text（80 字）/ reason は載せない | `claim_ids`（→ UUID）/ `equation_ids` | agent_unit_id + 正規化 text + claim/equation 集合 |
| `parent_component` | `component_refinement.component_refinement_records`（原案 1 件 = 1 unit。split の親は `refinement_report.split_actions[].parent_label`、unchanged はその component 自身） | 親 label / 親 summary | `linked_component_ids` = 子の DB UUID（`component_id_map`）、`agent_payload.linked_component_agent_ids` = 子 agent ID。teaches = concepts（`concept_name_list`）+ claims | 正規化 label + 子の block 集合 |
| `dsl_node` | `DSLLinkingResult.nodes[]` | `node_value` / node_type | teaches = concept 1 件。`agent_payload.is_thesis_anchor` | node_type + 正規化 node_value |
| `figure` | `FigureTableSemanticsResult.figures[]` | figure_label（`fig_3.3` のような内部表記は label にしない → caption 先頭）/ caption | `linked_claim_ids` → UUID、`linked_figure_ids=[figure_id]` | figure_id + 正規化 caption |

`learning_unit_stable_key(document_id, unit_kind, text, refs)` は `digest(["learning_unit", document_id, unit_kind, normalized_text, join_sorted(refs)])`。
同一 run 内の衝突は `dedupe_stable_keys`。`persist_learning_units(document_id, run_id, ...)` は `_stage_persist_claims_components_graph` の
components 保存**後**（`id_map` が要るため）に呼び、`stage_outputs` の `knowledge_objects` 要約へ `learning_units` を足す。

### 5.1 P2-2 親子構造の保存

`persist_components` は `component_result.refinement_report.split_actions` から `child agent_id → (parent agent_id, parent_label)` を組み、
子行の `parent_agent_component_id` を埋める。**LLM 原案の親は `theory_components` の行としては作らない**（学習者・グラフの主語は
子 = 実際の理論操作で、親は `learning_units(unit_kind='parent_component')` が一級に持つ）。`parent_component_id` は親が
`learning_units` 行になった後、unit の id を **書かず**、`theory_components` 内で同 stable_key を持つ live 行が無いので NULL のまま
にする — つまり v1 では `parent_agent_component_id` だけが実質の親参照で、`parent_component_id` は「親を component 行として
持つ将来」に備えた nullable 列。**子の `name` は変えない**（stable_key の材料でもあり、Phase 1 の人間確定保護と衝突する）。
「Transform representation: …」を学習者から消すのは**表示側**: 学習者向け投影（`course_content_builder._content_blocks` の
components item と `build_topic_evidence_items`）で、unit 経由で束ねた子 component は親 unit の label を `display_label` として
併記し、UI は `display_label` を優先する（内部名は `label` に残す = 情報を落とさない）。

## 6. コース側（P2-3 / P2-4 / P2-6）

### 6.1 `topic.units`（additive）

```json
"units": [{"kind": "section_block", "stable_key": "k1:…", "unit_id": "<uuid>", "label": "…", "source": "teacher_selected"}]
```
`source ∈ {teacher_selected（コースビルダーで選んだ）, title_match（救済: 文字列一致で後付け）}`。
`core/course_data.py::CourseTopic.units: list[CourseTopicUnit]`（`extra="allow"`）+ アクセサ `topic_unit_keys(topic)`。
`api/schemas.py::LearningTopic.units`（学習者向け DTO には `stable_key` / `unit_id` を**出さない** — `label` と `kind` のみ。KO10 と同じ）。

### 6.2 候補の提示と選択（コースビルダー）

- `backend/core/course_units.py`（FastAPI 非 import・DB は session 引数）: `list_unit_candidates(session, document_ids) -> list[UnitCandidate]`
  （`learning_units_live` から `unit_kind ∈ LEARNING_UNIT_KINDS_FOR_COURSE`・`review_status <> 'dismissed'`、並びは
  (document_ids の順, kind の LEARNING_UNIT_KINDS 順, order_index, label) で**決定論**。handle は `U1..Un`）/
  `resolve_unit_handles(candidates, handles) -> list[dict]`（候補に無い handle は捨てる = LU3）/ `units_for_documents_by_key(session, document_ids, keys)`。
- `_build_material_context`（`routes/admin.py`）に区画「**学ぶ単位の候補**」（`U{n} [種別] label — summary 40 字`、上限は種別ごと
  に `section_block` 全件・`thesis_support` 全件・`parent_component` 全件・`figure` 8 件）。数値・confidence は出さない。
- `_COURSE_BUILDER_SYSTEM_PROMPT` の topic スキーマに `"units": ["U3"]` を足し、「候補リストの handle だけを使う・候補に無い
  単位を作らない・1 topic に 1〜3 unit・同じ unit を複数 topic に置いてもよい」を明示。
- `admin.js` の登録ペイロード（`draft.chapters[].topics[].units`）を `topics[].units`（handle の配列）として素通し。
- `create_course`（`routes/learning.py`）は `sources` の material → document を解決し、handle を `resolve_unit_handles` で
  `{kind, stable_key, unit_id, label, source:"teacher_selected"}` に写す（解決できない handle は落とし、`course_content_status`
  の extra に事実文）。**指揮者（Fable）が最後に配線する**（担当 B は helper と単体テストまで）。

### 6.3 freeze（`build_course_content`）での束ね

`_enrich_topics`: topic に `units` があれば **units 優先** — `learning_units_live` を document 集合で読み（`_load_learning_units`）、
`agent_payload.linked_component_agent_ids` で bundle の components を、`linked_claim_ids` / `linked_equation_ids` で claims /
equations を束ねる。`content_source="learning_units"`。units が空のときだけ従来の
`_best_mapping`（文字列一致）を救済として使い、一致した mapping の component が unit の子なら `topic.units` に
`source:"title_match"` で後付けする（教員が選んでいないことを `source` で区別）。unit 経由の component item には
`display_label`（親 unit label）を付ける（§5.1）。

**保存済みの選択は上書きしない（P2-R1）**: `topic.units` は保存順のまま全要素を残し、live に居ない
（再解析で supersede された等）要素は `source` を書き換えず `resolved: false` を足すだけ（LU2 / LU4）。
救済（`title_match`）は既存キーに無いものを**末尾へ追記**するだけで、解決がゼロのときも教員の選択を
置き換えない。解決できない `teacher_selected` があった run は `course_content_status.extra.units_note` に
事実文（件数なし）「選んだ単位のうち、いまの解析結果に見つからないものがあります。」を載せる。

**散文の出所を混ぜない（P2-R2）**: units があるトピックは、`_best_mapping` 由来の散文
（`summary` / `learning_objectives` / `assessment_prompts` / `prerequisite_concepts` /
`expected_misconceptions` / `blackbox_policy`）を**題名完全一致（`exact_title`）のときだけ**採る。
類似一致（`title_similarity`）は「タイトルの語が重なった別トピックの説明」であり、教員が選んだ単位の
説明として出す根拠にならない。採らない場合は unit / component 側の材料だけで組む（無ければ空欄 = 事実）。
`content_confidence` は実態に合わせて `unit_selection`（unit だけ）/
`unit_selection_with_title_mapping`（unit + 題名一致 mapping の散文）に分ける。

**単位が立たなかった章（P2-R11）**: `core/knowledge_objects/learning_units.py::uncovered_sections(structure, skeleton)`
（決定論・非LLM・推定しない）が、`paper_skeleton` の `logical_blocks` がどの `section_ids` にも張っていない章を
文書順で返す。freeze は題名だけを `course_content_status.extra.uncovered_sections` に列挙し、
`uncovered_sections_note` に事実文（件数なし）を添える。素材が無い（構造 / skeleton が読めない）ときは
空 — 「単位が立たなかった」と「素材が無い」を混同しない。

### 6.4 P2-4 前提の ID 参照

- `prerequisites[]` 要素に `topic_id`（同コース topic の id）を additive 追加（`CoursePrerequisite.topic_id: str | None`）。
  `core/course_prerequisites.py::resolve_prerequisite_topic_ids(topics) -> list[dict]`（正規化題名の完全一致で `topic_id` を
  埋める。一致しなければ None のまま = 推測しない）。`create_course` で 1 回呼ぶ（指揮者が配線）。
- `check_prerequisites`（`services.py`）は `topic_id` があればそれで同コース topic を引き、**表示名は現在の題名**を使う
  （題名変更で切れない）。判定材料は従来どおり本人の明示記帳のみ（LU6）。
- 半順序検査（非LLM・純関数）`core/course_prerequisites.py::analyze_prerequisite_order(topics) -> PrerequisiteOrderReport`:
  `cycles`（A→B→A）/ `redundant`（A→C が A→B→C で推移的に導ける）/ `unresolved`（topic_id が引けない名前）/
  `forward_references`（後の章の topic を前提にしている）。事実文は `to_facts()`（`label_vocab` の固定文・**件数なし**）。
- API `POST /api/admin/course-builder/prerequisite-check`（`_require_teacher`・body = course_draft の chapters/topics・
  DB 非変更・LLM 0 回）→ `{"facts": [...], "available": bool}`。コースビルダーの下書きプレビュー（`admin.js` 前提知識行の下）
  に事実の段落として描く（操作要素ではないので `data-ui-anchor` は付けない = `admin-indicators.js` の規律）。

### 6.5 P2-6 blueprint の持ち込み

`_collect_structured_content` が `blueprint` artifact の `narrative_arc[]` を `component_id → {role, visual_strategy}` に索引化し、
`_enrich_topics` が束ねた components から `topic.narrative = {"roles": [...distinct・弧の順], "visual_strategy": 先頭}`
を導出（無ければキー自体を足さない）。`_topic_context_for_prompt` に `narrative` を渡し、散文生成が語りの役割を参照できるようにする
（数値なし・`rationale` は載せない）。学習者 DTO には出さない（v1）。

## 7. P2-5 登録の一括確定と G層

- `create_course` は候補（`list_unit_candidates`）が 1 件以上あるときだけ
  `build_decision_context(basis=BASIS_COURSE_REGISTER_UNITS, presented_ids=候補 stable_key, applied_ids=束ねた stable_key,
  alternatives=(ALT_EDIT, ALT_DESELECT), reopen_path="/api/admin/courses/{course_id}/lecture-studio/course-topics/{topic_id}",
  reopen_statuses=("candidate",), evidence_shown=None)` を作り、`services.record_review_event(AUDIT_ENTITY_COURSE_TOPIC, course_id,
  "draft", "registered", user_id, attach_decision_context({...}, ctx))` で 1 行記帳する（新 entity_type は作らない）。
- G層ルール `course.delivered_unreviewed`（`RULE_COURSE_DELIVERED_UNREVIEWED`・recommended・capability は既存
  `materials.graph_review` を再利用・target は最初の source material）: 本人所有・`is_published` のコースで、source document の
  live component / claim のうち topic が束ねているもの（`linked_component_ids` / `units` 経由）に教員の確認
  （`next_steps.APPROVED_REVIEW_STATUSES` = `teacher_approved` / `teacher_reviewed` / `endorsed`。P2-R8）
  が 1 件も無い。事実文「コース『X』は、解析結果の確認（承認）を経ずに配信されています。」（件数なし・督促なし）。
  束ねが 0 件のコースには出さない（承認対象が無いのは別の事実）。

## 8. P2-7 痕跡の着地

- `_learner_selected_anchor(body, screen_selection=...)`（`routes/learning.py`）: 要素タップ・テキスト選択が無く、
  `screen_context.selection` に `element_type` / `element_id` があり course が一致するときは、それを `learner_selected` の
  anchor（`anchor_type_for_element`）として記帳する（AI 候補に回さない）。`element_type` は `LEARNING_ELEMENT_TYPES` に落ちる
  ものだけ・label は `element_label` か id。
- `seg_0` の是正: ①フロント `app.js` — 「ここについて質問」は `Session.currentAnchor()` ではなく**選択範囲を含む教材区画**
  （`data-segment-index` を持つ最も近い祖先。無ければ chunk 順）から `segment_id` を取る。②サーバ — `selection_segment_id` が
  無く `selection_text` があるときは、`core/structure_anchor/selection_segment.py::resolve_selection_segment(chunks, selection_text)`
  （非LLM・空白正規化の部分文字列一致・一意に決まるときだけ）で埋め、決まらなければ `anchor_id=""`（推測しない）。 ③**区画の粒度（P2-R3）**: 配信（`get_topic_material`）と照合（`_anchor_segment_texts`）はどちらも
  `core/lecture.py::build_topic_slides` のページ境界（`===` マーカー優先・無く長ければ段落境界の自動ページ分割。
  表示・音声・readiness と同じ決定論分割）で割る。長いトピックは複数区画で配信され、フロントの
  `data-segment-index` が 0..N-1 を取るので区画番号が意味を持つ（短いトピックは 1 区画 = `seg_0` が唯一の正解）。
  `formulas` / `figures` / `evidence_items` は**区画ごとに間引かない**（`[[FORMULA_N]]` / `[[FIGURE_N]]` の
  位置依存解決を壊さないため）。章題は先頭区画にだけ載せる。④**照合キー（P2-R13）**: `resolve_selection_segment`
  の `_match_key` は `![[figure:id]]` / `[[FIGURE_N]]` / `[[FORMULA_N]]` のような埋め込み・プレースホルダーを
  両側から落としてから比較する（配信は `resolve_figure_embeds` を通し、痕跡帰属は同期パスに DB クエリを
  足さないため通さない — 供給元の違いで「一致しない」にならないようにする）。

## 9. 権限・監査・数値

- unit の読みは document viewable。**選択された `material_id` は「読んでよい」証拠ではない**ので、
  候補表を組む経路は必ず可視集合と積を取る（P2-R5・fail-closed。可視集合が空なら SQL を発行せず空）:
  コースビルダーの `routes/admin.py::_build_material_context(..., user_id=)` と、コース登録の
  `routes/learning.py::_ordered_source_document_ids(session, material_ids, *, user_id)`。書き込みは persistence のみ。
- 教材コンテキスト（解析成果 + 原文抜粋 + 学ぶ単位の候補）は PDF 由来 = 第三者が書いた untrusted 入力なので、
  プロンプト先頭に `core/text_hygiene.py::UNTRUSTED_SOURCE_NOTICE` を置き（言い換え禁止・適用パス台帳は
  `test_pdf_trust_boundary_guardrails.py`）、候補区画の label / summary は `strip_control_sequences` を通す（P2-R6）。
- `PUT /api/learning/courses/{id}` の topics 反映は、GET 射影で参照キーが落ちた units で上書きしない
  （`_preserve_topic_units`・topic id キーで温存。P2-R4）。`concepts` は登録時と同じ記号除去の弁
  （`_split_symbol_concepts`）を通す（P3-R8）。
- 監査: unit 保存は `AUDIT_ENTITY_KNOWLEDGE_OBJECT`（run 単位要約に `learning_units` の件数）。登録は `AUDIT_ENTITY_COURSE_TOPIC`
  + `decision_context`。
- 数値非表示: `confidence` / `order_index` / 一致率 / 件数を学習者・教員の UI に出さない（DTO は label / kind / 事実文のみ）。

## 10. 検証

- core: `test_learning_units_{stable_key,derive,persist}.py`（決定論・入力非改変・種別スキップ・supersede）
- コース: `test_course_units.py` / `test_course_content_units.py`（units 優先・救済の source 区別・display_label・narrative）
- 前提: `test_course_prerequisites.py`（循環・冗長・未解決・前方参照・件数なし事実文・学習者入力ゼロ）+ API
- G層: `test_next_steps_guardrails.py` 追随 + `test_next_steps_delivered_unreviewed.py`
- 痕跡: `test_structure_anchor_selection.py`（screen selection → learner_selected / 段落解決 / 不一致は空）
- ガードレール: `test_learning_units_guardrails.py`（core 非 FastAPI・基表 allowlist・語彙一致・DTO 非漏洩・LLM 0 回・
  適応評価語彙の不在（mastery / 習熟 / proficiency）・`data-ui-anchor` 不変）

## 11. 非スコープ（v1）

- unit の教員確定 UI（`confirmed` への遷移 API・レビューキュー）— 候補と `source` の区別を先に運用で見る
- freeze スナップショットから component 投影を外して unit 参照だけにすること（既存 freeze の互換）
- 承認状態の freeze 後追随（unit → live component の承認を学習者 DTO に写す）
- `dsl_node` unit のコースビルダー提示（Phase 3 概念レジストリの材料）
- 学習者向けの `topic.narrative` 表示 / 前提の学習者側表示変更 / 適応的な前提提示（恒久排除）

## 12. 実装記録

### 12.1 2026-09-13 — Phase 2 v1（Fable 5.1 指揮・Opus 5 の 4 担当）

migration は **081** `081_learning_units.sql`。分担は A（スキーマ・導出・永続化）/ B（コース側）/ C（前提・G層）/
D（痕跡の着地）。`create_course` の配線（handle 解決・`prerequisites[].topic_id`・`decision_context` 記帳・学習者 DTO 射影）と
docs 一式は指揮者。検証: backend 14,905 pass / src 1,924 pass。空 DB に init〜081 の 79 ファイルを 2 回適用して無変更（冪等・
`knowledge_unit_kinds` 5 行・`learning_units_live` 23 列・`theory_components_live` に親参照 2 列）。`persist_learning_units` の
実 PG 往復で「同キー = 同 UUID 更新（updated）/ 人間の確定列 `review_status` `teacher_notes` 保護 / 不一致 = `superseded_at`
刻印 + 新行 / documents 削除で CASCADE」を確認。

**設計からの逸脱・判断（実装で確定した仕様）**

| 箇所 | 判断 |
|---|---|
| §5 persist | 5 種別とも素材 `None` のときだけ SQL 非発行。一部の種別が欠けた run では欠けた種別の live 行が supersede される（行は残る = P4。v1 は unit の UUID を外部参照しないため許容。`skipped_kinds` に正直に載せる） |
| §5 remap | `element_id_remap` への再係留はしない（unit の agent ID を参照する表が無く、`object_kind` の CHECK 語彙を増やさない） |
| §5 parent_component | split 親の `summary` は空のまま（artifact に親要約が無い。子の要約で埋めない）。`dsl_node` にも `linked_claim_ids` / `linked_equation_ids` を additive に持たせる |
| §5 label | 内部 ID（`fig_3.3` / `eq_op_*` / `theory_op_*`）は label にしない。候補が全部内部 ID なら空文字で出す |
| §5.1 | `parent_component_id`（UUID 列）は書かない。実質の親参照は `parent_agent_component_id` のみ。`knowledge_unit_kinds.label` は `label_vocab.LEARNING_UNIT_KIND_LABELS` と逐語一致をガードレールで固定 |
| §6.1 | `LearningTopic.units` は `list[dict | str]`（登録ペイロードが handle 文字列配列で来るため）。解決は `create_course` |
| §6.2 | 候補区画は教材ごとではなくコンテキスト末尾に 1 区画（handle は document を跨ぐ通し番号）。`figure` の上限 8 は document ごと。`create_course` の document 順は `_build_material_context` と同じ material_ids 順 |
| §6.3 | `_load_learning_units` は keys ではなく document 集合で全 live unit を読む（救済の逆引きに必要）。unit の claim は `agent_payload` の agent 側 ID を優先し bundle に実在するものだけ束ねる（DB UUID を `![[claim:id]]` の名前空間に流さない）。`topic.narrative.visual_strategy` は弧の先頭の非 `none` 値 |
| §6.4 | 事実文は `course_prerequisites.py` 内の f-string（dict の訳語表を作らない）。前方参照は「後の章 / 同じ章の後ろ」の 2 文。循環の中では冗長・前方参照を二重に言わない。同名 topic が複数なら解決しない |
| §7 G層 | `learning_units` を JOIN せず、束ねは `linked_component_ids` / `linked_claim_ids` のみ（units だけのコースには出さない = 偽陽性回避）。ソース教材の無い公開コースは `material_id` を落として事実だけ出す |
| §7 記帳 | 落とした handle の事実は監査 metadata の `unit_handles_unresolved`（真偽のみ）に残す（`course_content_status` は freeze が上書きするため） |
| §8 | 区画の粒度は「配信された教材区画」（`.material-chunk`）で段落ではない。`anchor_type_for_element` が claim を concept に丸める点は `ANCHOR_TYPES` に同名があればそれを使う 1 段で補う |

**残課題（v1 非スコープに追加）**: unit 経由の `display_label` を⚓チップ / 原稿スタジオで優先描画する配線（DTO には載っている）/
`PUT /api/learning/courses/{id}` で topics を差し替えたときの handle 再解決（P2-R4 で**参照キーの温存**は入ったが、
射影された units から handle を引き直す経路は無いまま）。

### 12.2 2026-09-13 — 敵対的レビューの是正（F2 班）

Phase 2 の敵対的レビューで確定した 13 件（P2-R1〜R6 / R8〜R13 / P3-R8）の修正。**migration なし・新エンドポイントなし・
LLM 呼び出し回数は不変**。

| 発見 | 是正 |
|---|---|
| P2-R1 再解析後の freeze が `teacher_selected` を `title_match` で上書き | `_merge_topic_units`: 保存順を保って全要素を残し、未解決は `resolved:false`（`source` は不変）。救済は追記のみ。`course_content_status.extra.units_note` に事実文（件数なし） |
| P2-R2 units があるトピックに類似一致 mapping の散文が入る | `_mapping_prose_allowed`: units があるときは `exact_title` のときだけ mapping 由来を採る。`content_confidence` を `unit_selection` / `unit_selection_with_title_mapping` に分ける |
| P2-R3 `seg_0` 固定（配信 chunks が常に 1 件） | 配信・照合の両方を `build_topic_slides` のページ境界に揃える（`_topic_material_segment_texts` が共通正本）。索引（formulas / figures / evidence_items）は区画ごとに間引かない。章題は先頭区画のみ |
| P2-R4 `PUT /courses/{id}` の topics 素通しで units が往復消失 | `_preserve_topic_units`: 参照キーを持たない incoming は既存 units を topic id キーで温存 |
| P2-R5 候補表の可視性ゲート無し | `_ordered_source_document_ids(..., user_id=)` / `_build_material_context(..., user_id=)` が `list_visible_document_ids` と積を取る（fail-closed・空なら SQL 非発行） |
| P2-R6 untrusted 入力の明示が無い | `_build_material_context` 先頭に `UNTRUSTED_SOURCE_NOTICE`（適用パス台帳に登録）。候補区画の label / summary を `strip_control_sequences` に通す |
| P2-R8 承認語彙が `teacher_approved` だけ | `next_steps.APPROVED_REVIEW_STATUSES`（3 語彙）に。SQL は `= ANY(:approved)` |
| P2-R9 `BASIS_COURSE_REGISTER_UNITS` の call site 検査が無い | `test_course_register_units.py` に逐語検査 2 件（learning.py 1 箇所・他モジュールゼロ） |
| P2-R10 プレビューが handle（`U3`）を表示名にしていた | `course-builder/chat` レスポンスに `unit_candidates`（handle / kind / kind_label / label）を足し、`cbUnitDisplayName` が名前に直す（候補表に無い handle は handle のまま = 捏造しない） |
| P2-R11 単位が立たなかった章が黙って消える | `learning_units.uncovered_sections`（決定論・推定しない）+ freeze の `extra.uncovered_sections{,_note}` |
| P2-R12 `prereq_display` が名前キーの dict で後勝ち | 要素単位の `prereq_entries: list[tuple[記帳キー, 表示名]]` に |
| P2-R13 照合本文と表示本文のずれ | `selection_segment._match_key` が埋め込み・プレースホルダーを両側から落とす |
| P3-R8 `PUT /courses/{id}` の `concepts` に記号除去が掛からない | `update_course` も `_split_symbol_concepts` を通す（除いた名前は `excluded_symbol_concepts` に残す） |

**判断（実装で確定した仕様）**

- **P2-R3 は「完全分割」を採った**（縮退案は不採用）。フロント（`app.js`）は既に
  `state.topicMaterial.forEach(function (chunk, segmentIndex) {...})` で配信配列の位置を `data-segment-index` に
  書いているため、配信を割るだけで区画番号が自然に 0..N-1 になる。UI 契約テスト
  （`test_learning_ui_phase3_static.py` / `test_learning_layout_static.py`）は無改変で通る。
  各区画の `id` は `topic:{topic_id}` のまま（`chunk` アンカーの意味を変えないため。重複は許容）。
- **P2-R13 は `figures_by_id` を痕跡帰属経路に持ち込まなかった**。`build_topic_slides` は解決済み `[[FIGURE_N]]` と
  未解決 `![[figure:id]]` を同じ 200 字に換算するので**ページ境界は一致**し、残る差はプレースホルダーの綴りだけ。
  それを `_match_key` から落とすことで、同期パスに DB クエリを増やさずに照合を揃えた。
- **P2-R10 は新 API を作らず** `_build_material_context` の出力パラメータ（`unit_candidates_out`）+ chat レスポンスの
  additive フィールドにした（戻り値の型を変えないので既存のパッチ面・テストが壊れない）。
- **P2-R11 の出力先は freeze の `course_content_status.extra`**（`persist_learning_units` は F1 所有のため触っていない）。
  判定そのものは `core/knowledge_objects/learning_units.py` の純関数で、保存側から呼び直すこともできる。
- **P2-R1 の `resolved` は未解決要素にだけ足す**（解決できた要素の形は従来どおり）。学習者向け射影
  （`learner_topic_units_projection`）は `kind` / `label` だけなので `resolved` は漏れない。

検証: 新規 `test_learning_units_segments.py`（8 件）+ `test_course_content_units.py` / `test_course_units.py` /
`test_course_register_units.py` / `test_learning_units_derive.py` / `test_course_prerequisites.py` /
`test_next_steps_delivered_unreviewed.py` / `test_pdf_trust_boundary_guardrails.py` への追加。

---
