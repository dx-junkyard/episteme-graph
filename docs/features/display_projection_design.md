# 表示投影層（Display Projection）

> **状態:** 実装済み（正本）— 2026-09-30

**正本**: 本ドキュメント（`backend/core/display_projection.py` / `backend/api/display_route.py` の正本。以後の変更は §6 実装記録への追記で行う）。
**親文書**: [`docs/vision.md`](../vision.md) §6（改訂原則4 — 数値の用途と粒度を統治する）/
[要素文脈の提示再設計](element_context_presentation_redesign.md)（LE4 / EC3 の遮断層）/
[理論モジュール層](theory_module_layer_design.md)（TM6 / TM10）/ [グラフの論文層](graph_paper_layer_design.md)（PL4 / PL7）。
**関連 migration**: なし。

---

## 1. 背景

解析層の内部 ID（`eq_blk_*` / `eq_tex_*` / `claim_span_*` / `theory_op_*` / `eq_op_*` / `ev_*` /
`synth_claim_*` / `sym_N` / `k1:` 安定キー / UUID をラベルにしたもの）・生の数値（confidence /
weight / score / 件数）・英語の生成ラベル文（"Logical progression through 41 claims …"）が学習者向け
DTO に漏れていた。遮断は画面ごとに別々の遮断器（`learner_context_common` / `deliberation.labels` /
`theory_modules.schema` / `graph_paper_layer.schema` / `course_content_builder` / `reference_health` /
`discuss.opening` / `doubt.seminar_brief` …）が持ち、語彙は 4 系統で互いに食い違っていた
（`eq_blk_` / `eq_tex_` / `sym_` / `k1:` をどれも持たない系統がある・論文の式番号 `eq_2_7` を
内部 ID と誤認する系統がある）。遮断器の無い面（学習者向け台帳の支持線の事実文・出典ポップアップの式・
主張参照・再構成の出題）もあった。

本層は**語彙を 1 箇所に集め**、学習者向けルートの戻り値が**必ず 1 回**それを通る構造にする。

## 2. 不変条項

| ID | 条項 |
|---|---|
| DP1 | **全学習者ルートが通る**。`/api/learning` / `/api/me` / `/api/atlas` のルートは `route_class=LearnerDisplayRoute` のルーターに置き、戻り値（dict / list / pydantic モデル）は `project_for_learner` を通ってから直列化される。SSE の `final` フレームも同じ射影を通す。 |
| DP2 | **語彙は 1 箇所**。内部 ID の正規表現・禁止キー集合・一般ラベル表の正本は `core/display_projection.py`。旧遮断器は定義を持たず、同じオブジェクトを参照する（ガードレールが `is` で検査）。系統ごとに意図的に違う判定（論文の式番号を例外にするか等）は系統別の定数として並べて残す。 |
| DP3 | **番地キーは遮断しない**。`id` / `*_id` / `*_ids` / `*_key` / `*_ref` / `source` / `target` / `from` / `to` / `value` の文字列値は画面遷移・再取得に要るので残す。表示文のキー以外で値全体が 1 トークンの内部 ID のもの（`chain` / `edges` / `footprints` のような番地の列）も番地として残す。 |
| DP4 | **英語生成文の置換は LABEL_KEYS のみ**。`label` / `sublabel` / `narrative_role` / `operation_line` / `display_label` / `role_label` の英語の生成文（4 語以上かつ解析層の定型の目印を持つもの）だけを一般ラベルへ置き換える。`title` / `text` / `passage` / `quote` / `summary` は論文本文（正当に英語）なので置き換えない。論文タイトルのような人間の英語は定型の目印を持たないので置き換えない。 |
| DP5 | **教員向けは段階ラベルを残す**。`project_for_teacher` は TM6 ∪ PL4 の数値キーだけを落とし、`*_label` の段階ラベルと件数キー `n` 等は残す。英語生成文の置換もしない。 |
| DP6 | **fail-soft で応答を止めない**。射影が例外を出したら警告を残して元の値を返す（学習者の操作を止めない）。 |
| DP7 | **教員向けの名指しの面は `project_for_teacher` を通る**（2026-10-01）。グラフレビュー（component-graph）・論文の順（paper-layer）・理論モジュール（theory-modules / related）・ゼミ前ブリーフ・W層の要素文脈（overview / context）・グラフ全体対話（graph-sessions）。専用ルーター（ゼミ前ブリーフ）は `route_class=TeacherDisplayRoute`、大きな admin ルーターに同居する面はエンドポイント本体で射影の入口（`project_teacher_dto` / `_teacher_view`）を明示的に呼ぶ。admin ルーター全体には掛けない（件数を正当に返す API が同居する）。 |

## 3. 語彙

- **統合語彙** `INTERNAL_ID_TOKEN_RE`（語境界付き）: 直後が数字のプレフィックス
  （`theory_op_` / `ev_` / `evidence_` / `comp_` / `node_` / `span_` / `step_` / `sys_N_step_` /
  `blk_` / `section_` / `sec_` / `figure_` / `fig_` / `sym_`）、本体のどこかに数字があるプレフィックス
  （`derivation_` / `system_derivation_` / `synth_` / `claim_span_` / `claim_` / `eqcand_` /
  汎用の式 ID `eq_` / `eq-` / TeX ブロック `tex_`）、`k1:` + 16 進 8 桁以上、`support:…`、UUID。
  汎用の式 ID は**論文の式番号を除外**する（`eq_12` / `eq_2_7` は捕まえず、`eq_tex_b14` /
  `eq_blk_004_0084` / `eq_op_7` は捕まえる。`eq_(3.1)` / `eq.3.14` は形として当たらない）。
  数字を要求するので `claim_type` / `derivation_in` / `node_id` のような語彙語・キー名は当たらない。
- **禁止キー** `FORBIDDEN_KEYS_LEARNER`（値ごと落とす）: confidence / weight / score /
  candidate_score / load_score / qualification_reason / stable_key / produced_by_run_id /
  superseded_at / superseded_by_run_id / fingerprint / structure_fingerprint / interface_width /
  interface_size / k / n / count / counts / n_users / n_items / dependent_count / member_count /
  module_count / consumer_count / multiplicity / cosine / similarity / distance。
  k-匿名のレンジ（`"3-5"` 等、`core/privacy.py`）は文字列値なので落ちない。
  `FORBIDDEN_KEYS_TEACHER` = TM6 ∪ PL4。
- **表示文のキー** `DISPLAY_KEYS` + 接尾辞 `_label` / `_text` / `_title` / `_note` / `_line` /
  `_fact` / `_message` / `_summary`。値全体が 1 トークンの内部 ID でも置き換える。
- **置き換えの語**: `UNIDENTIFIED_ELEMENT_TEXT`「（本文を特定できない要素）」（旧
  `theory_modules.schema` から移設）。`LABEL_KEYS` の値全体が内部 ID で `element_type` が
  分かるときは element_type 別の一般ラベル（`GENERIC_ITEM_LABELS`、旧
  `learner_context_common` から移設）。

## 4. 対象外

- `auth.py` / `groups.py`（`/api/me/invitations` を含む）/ `export.py` / `indicators.py` /
  `disclosure.py`: 認証・運用・カタログで、それぞれのガードレールがキーを走査している。
- 管理画面のルート: 名指しの面（DP7）以外は各層の射影（PL / TM / W層）がそのまま正本。
  admin ルーター全体へは適用しない（要素インベントリの `counts` など、件数を正当に返す API が同居する）。
- 画像・音声・SSE の途中フレーム（`delta` は衛生済みの本文のみ）。

## 5. 新しいルートを足すとき

- 学習者向けのルートは `route_class=LearnerDisplayRoute` を持つルーターに置く
  （横断ガードレール `test_display_projection_guardrails.py` が全ルートを検査する）。
- 内部 ID の正規表現・禁止キー集合を**再定義しない**。判定が要るときは
  `core.display_projection` の関数（`contains_internal_id` / `mask_internal_ids` /
  `is_internal_id_token` / `strip_keys`）を使う。
- 番地として返す値は番地キー（`*_id` 等）に置く。表示文に ID を埋め込まない。

## 6. 実装記録

### 2026-09-30 初版

- `backend/core/display_projection.py`（新設・純関数）: 統合語彙・系統別語彙・禁止キー・
  表示キー・`project` / `project_for_learner` / `project_for_teacher` / `strip_keys`。
- `backend/api/display_route.py`（新設）: `LearnerDisplayRoute`（エンドポイントを
  `functools.wraps` で包み、同期 / 非同期を保つ）・`project_learner_payload`・
  `project_learner_model`（SSE `final` 用）。
- ルーター 15 本に `route_class=LearnerDisplayRoute`: `learning.router` / `atlas.learning_router` /
  `atlas.report_router` / `atlas_view.router` / `corpus.learning_router` / `cycle.learning_router` /
  `descent.learning_router` / `discuss_observation.learning_router` / `doubt.learning_router` /
  `landscape.learning_router` / `lecture.router` / `personal_map.router` / `personal_map.me_router` /
  `my_records.me_router` / `reconstruction.learning_router`。
- 旧遮断器の語彙を委譲: `learner_context_common` / `deliberation/labels` /
  `theory_modules/schema` / `graph_paper_layer/schema` / `course_content_builder` /
  `reference_health` / `discuss/opening`（`_strip_numeric_keys`）/ `doubt/seminar_brief`。
  判定の中身はそれぞれ従来と同一。
- 遮断の無かった面を埋めた: 支持線の `cut_members` のラベルが引けないとき生のノード ID に
  縮退していた（学習者向け台帳の事実文に届く）→ 読める語へ / 出典ポップアップの式の `label` が
  内部 ID なら出さない / 主張参照に `claim_type_label`（`element_vocab`）を追加し本文の内部 ID を
  置換 / 再構成の出題から `section_id` を落とし `claim_type_label` を追加。
- ガードレール: `test_display_projection_guardrails.py`（全学習者ルートの route class・
  response_model のフィールド名・語彙・純粋性・委譲の同一性・リーク走査）+
  `test_display_projection_core.py`。

#### 指示からの意図的な差分

- 「番地キー以外の文字列値はすべて表示文として遮断する」を、値全体が 1 トークンの内部 ID の場合に
  限り「表示文のキーでだけ置き換える」に狭めた。分野の地図の `chain` / `edges` /
  `footprints` / `initial_selection` や導出ステップの列など、`_id` 接尾辞の無いキーに番地の列を
  持つ応答が複数あり、置き換えると画面遷移が壊れるため（`test_atlas_view_api.py` が実際に検出）。
  文中に埋め込まれた内部 ID はキーを問わず置き換える。
- 英語生成文の判定は「4 語以上の英語文」に加えて解析層の定型の目印（件数入り・操作動詞 + コロン・
  `Define eq…`）を要求した。個人の旅の `ref.label` などに論文タイトル（英語・4 語以上）が入るため。

### 2026-10-01 レビュー是正（番地の規則・選択肢のラベル）

- **埋め込み参照は番地**: `mask_internal_ids` は本文中の `![[kind:…]]`（1 段の入れ子
  `![[equation: [[eq_x]] ]]` を含む）と `[[FORMULA_N]]`（`EMBED_MARKER_RE`）の内側を置き換えない。
  クライアントが `evidence_items[].id` / `formulas` に照合して描くため、中の ID を隠すと教材の
  埋め込みが全部解決できなくなっていた。
- **URL は番地**: 番地キーに `href` / `src` / `urls` と接尾辞 `_url` / `_urls` / `_href` / `_src` /
  `_path` を足し、キーを問わず `/api/` か `http(s)://` で始まる値も遮断しない（`figures[].image_url`
  の UUID が隠れて図が全部読めなくなっていた）。
- **英語生成文の置換を狭めた（DP4 の補足）**: 既知の `element_type`（`GENERIC_ITEM_LABELS` のキー）を
  持つ項目だけを置き換え、`response_space` / `next_actions` / `sources` / `options` / `choices` の列の中
  （`OPTION_LIST_KEYS`）では置き換えない。別々の選択肢が同じ一般ラベルに畳まれて選べなくなっていたため。
- **語彙の追加**: `figure_N` / `section_N` / 裸の `tex_bN` を内部 ID に加えた（`Figure 3` /
  `latex_…` / 論文の式番号は当たらない）。`blk_N` は既存の `blk_` で当たる。
- **履歴の出典メタ**: 応答本文の内部 ID が置き換わると、送り返された本文が保存本文と一致せず
  `_rehydrate_history_sources` が出典メタ（IK-0432 の番号の対応）を戻せなかった。突き合わせを
  assistant `id` → `reply_to_id`（直前の user `id`）→ 本文の順にした。

### 2026-10-01 教員向けの名指しの面（DP7）

第 17 周の教員レーンで、グラフレビュー初期表示・論文の順・理論モジュール・ゼミ前ブリーフに内部 ID が
残っていた。多くは番地キー（`node_ids` / `section_id` / `module_key` / `target_id` …）の下で画面遷移に
要る値だったので、まず fixture（実論文 3 本 `backend/tests/fixtures/theory_modules/`）から 3 つの DTO を
組み、**番地キー以外の表示文**に残る内部 ID を測った。射影前: 論文の順のノード `label`（`Define eq_tex_b78`
等・式の詳細層で 1 論文 52 件）・主張本文（`Equation (eq_tex_b14) defines …`）・理論モジュールの前提文
（`… summarized in tex_b92`）。component-graph は既存の読み時ラベル置換で 0 件。射影後: 3 DTO × 3 論文で 0 件。

- `core/display_projection.py`: `mask_internal_ids` / `project` / `project_for_teacher` に任意の
  `token_replacer`（トークン → 読める語。`None` を返せば既定語）。本モジュールは式の記録を読まない（純関数のまま）。
- `api/display_route.py::TeacherDisplayRoute`（学習者向けと同じ包み方・Response 素通し・DP6）。ゼミ前ブリーフの
  `admin_router` が持つ。
- `routes/theory_components.py::project_teacher_dto`: 式の記録から「式 (N)」/「番号なしの式」へ置き換える
  replacer を組んで `project_for_teacher` に通し、数字を含まない TeX ラベル由来の式 ID（`eq_delta_b_flamingo`）も
  記録にあるものに限って表示文から置き換える。component-graph / paper-layer / theory-modules / related の 4 本と、
  `routes/deliberation.py::_teacher_view` 経由で overview / context / graph-sessions（開始・発話）の 4 本。
  paper-layer / theory-modules は builder が読んだ artifact を `artifacts_sink` で受け取り二度読まない。
- 番地キーは残す（`module_key` / `parent_module_key` / `*_module_keys` はグラフレビューの UI がモジュールを引く鍵 —
  TM10/TM12 の「表示しない」は画面に描かないことで、DTO から消すことではない）。落ちるのは TM6 ∪ PL4 の数値キー
  （`structure_fingerprint` / `confidence` / `weight` / `score` / `count` …）。`confidence_label` 等の段階ラベルは残す。
  component-graph の辺の `confidence` は応答モデルの既定値（0.5）に戻るため、原稿スタジオの辺の太さは一様になる（GR3）。

射影では直せない作り込みの漏れ:

- **ゼミ前ブリーフの対象名**: 依存グラフのノード名は A層の `visual_label`（`Infer intermediate claim\nsynth_claim_0013 → clai`
  — 2 行目は 30 字で切られる）だったため、隠すと「Infer intermediate claim（本文を特定できない要素） → clai」になり、
  脆い前提 8 件と一点吊りの支持線の崩れる対象がすべて中身ゼロだった。`DependencyGraph` に表示用の
  `node_claim_ids` / `node_operations` を足し、`_ReadableLabels` が「操作の日本語名（`element_vocab.operation_label`）:
  根拠の主張本文（`claim_object_builder`・語の途中で切らない）」で組み直す。主張が引けなければ操作の日本語名だけ。
  支持線の `cut_members` も同じ名前で組む（`ctx.graph.node_labels` を上書き）。`target_label` の丸めは
  `text_excerpt.excerpt`（語の途中で切らない）。
- **グラフの `visual_label`**: 2 行目（入力 → 出力）に内部 ID か切れ端が残るなら 2 行目を落として動詞だけにする
  （式 ID だけなら「式 (2) → 式 (3)」に置き換えて残す）。
- **主グラフの `display_label` の生 TeX**（「消去: D, M_{\mathrm{PISN}}, N」）: 理論対象に TeX があれば理論モジュールと
  同じ `plain_math` で読める形へ落とす。`plain_math` はエスケープした波括弧 `\{ \}` と `\_ \%` を畳み、名前の付かない
  残りのバックスラッシュを出さない（「\Θ^2_GW\」の解消）。
- 次にやること（G層）の `locate_plan.steps[].anchor_id` は UI の論理アンカー（番地キー `*_id`）なので変えない。

ガードレール: `test_display_projection_guardrails.py`（名指しの 8 エンドポイントが射影の入口を呼ぶ・ゼミ前ブリーフの
ルーターが `TeacherDisplayRoute`・admin ルーター全体には掛けない・教員 UI が落とすキーを読まない・fixture 3 本の
DTO の表示文に内部 ID が残らない）+ `test_display_projection_teacher.py`。

### 2026-10-23 原稿スタジオの読み取り専用区画（DP7 の範囲の読み替え・第 25 周 面 D）

- 原稿スタジオ（`GET .../lecture-studio/course-structure`）は**編集欄の本文には射影を掛けない**（教員が
  `![[kind:id]]` の埋め込み記法を編集するため）。読み取り専用の区画 — 根拠リンクの一覧（`evidence_items`）と
  注意点のプレビュー（新キー `cautions_display`）— だけを `project_for_teacher` に通し、式の内部 ID は付録と同じ
  名前の段（印字番号 → 人が付けた名前 → 「この節の式」。`course_content_builder._appendix_equation_label`）へ
  写す（`routes/lecture_studio/topics.py::_topic_studio_view`）。編集欄の `cautions` はそのまま返す。
- 指揮者の判断（オーナー判断の推奨どおり）。分野の地図タブ（IK-0850）も同じ方式で揃える。課題 IK-0873。
- ガードレール: `test_lecture_studio_round25.py::TestStudioTeacherView`。
