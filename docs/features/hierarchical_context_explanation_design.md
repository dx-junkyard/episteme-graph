# 上位・下位概念を活用した説明付与と図のコース流通 — 設計

状態: **Phase 1〜4 実装済み（2026-07-19、ura-dev 未コミット。§11 の決定事項と §12 の実装記録を参照）**。
追補は §13「説明レビューキュー + 一括承認/却下」（2026-07-22 設計確定、実装は別途進行）を参照。

前提調査: 2026-07-19 実施の現状調査（図⇄概念接続 / W層コンテキストレンズ / 要素説明の生成入力 /
汎用部品×論文文脈 / コース作成への図取込、の5系統）。

関連正本:
- `figure_concept_linking_design.md`（図⇄claim リンクの正本は `FigureRecord.linked_claim_ids` — 本設計はこの決定を維持する）
- `element_context_lens_design.md`（W層 #498 — 上位/下位の読み時導出。本設計はこれを構造の正本として維持する）
- `image_pipeline_knowledge_library_design.md`（L層 — 学習者向け図表示は v1 非スコープとされていた。本設計 Phase 4 がその後続）
- `contextual_figure_analysis_iterative_verification.md`（#499 — 確証バイアス遮断。本設計はこの遮断を壊さない）
- `exposition_layer_design.md`（E層・未実装 — 本設計 Phase 2 の格納庫は E層の供給源を兼ねる）

---

## 0. 現状の要約とギャップ

調査で確定した事実（詳細は各設計書・調査記録を参照）:

| # | ギャップ | 根拠 |
|---|---|---|
| (a) | 数式の説明は局所文脈のみ。equation_semantics(Stage 8) が thesis_reconstruction(Stage 9) より前のため、論文全体での位置づけを原理的に持てない | `equation_semantics/input_builder.py`（隣接ブロックのみ） |
| (b) | component は thesis 文脈が LLM 入力に届くが「40語・操作記述専念」制約で summary 本文に展開されず、`role_in_thesis` / `supports_thesis_node_ids` / `support_role` は **persistence.py の INSERT に含まれず DB 非永続**（artifact のみ） | `component_assembly/prompt.py:71-74` / `document_pipeline/persistence.py:642-668` |
| (c) | 図の説明生成（apparatus vision / teaching_takeaway）は caption＋周辺本文＋略語のみが入力。上位概念（claim/thesis）の**解決済みテキスト**は注入されない（`linked_claim_ids` は不透明 ID のままプロンプトに入る）。`interpretation` は enricher 未配線で常に空 | `figure_context.py` / `apparatus_semantics/prompt.py` / `figure_table_semantics/agent.py:276` |
| (d) | 上位/下位の投影は W層コンテキストレンズの読み時導出のみで、figures API・学習者 API には出ない。context_lens は `identity_links` / `library` を import せず、**汎用部品の説明と論文内役割を同時提示する画面が無い**。claim→thesis の back-link も無い（前方向のみ） | `context_lens.py` / `deliberation.js:1817`（リンクは生 UUID 表示） |
| (e) | 学習者が見る説明は C層承認済み explanation とチャット時ローカル生成のみ。共通部品説明・図には到達不可（E層未実装） | `learning.py:2344` / `exposition_layer_design.md` |
| (f) | **コース作成に図が一切流れない**: CourseMappingAgent は source_scope.figure_id を落とす / コースビルダー SQL に document_figures 無し / course_data.py に figure フィールド無し / `![[figure:id]]` は予約記法のみ（供給・解決・描画とも未実装）/ 学習者向け図配信 API 無し（figures 系は全て `_require_teacher`） | `course_mapping/agent.py:124-231` / `routes/admin.py:1480-1609` / `lecture_studio/topics.py:306,424-441` / `admin-lecture-studio.js:2659-2714` |

---

## 1. 理想像

読み手体験で定義する。

**教員**: 任意の要素（図 / theory_component / claim / 数式）を開くと、二層の説明が並んで読める。

1. **汎用説明（generic）** — 「この要素は一般に何か」。L層ライブラリ（分野共通の教員共同財）に
   リンク済みなら、その凍結版の説明が出典付きで引用される。
2. **文脈説明（contextual）** — 「この論文で何の役割か」。上位（どの中心命題・理論段階・主張を
   支えるか）と下位（何から構成されるか — パーツ・記号・部分主張・入力式）を**解決済みテキスト**
   で織り込んだ 2〜5 文。AI 生成は常に候補で、教員が確認して確定する。

**学習者**: コース教材の中に、トピックに関係する図が**画像として**現れ、キャプションと
**教員承認済みの文脈説明**が添えられる。要素タップの説明ポップアップも同じ二層構成
（承認済みのみ）。「この図は §3 の主張 X を支える測定配置で、A・B・C から構成される」が
教材の中で読める状態。

**データ**: 上位/下位の**構造接続そのものは引き続き読み時導出**（KN 原則「文脈上の役割を
固定属性に保存しない」を維持）。保存するのは (i) A層 artifact に既に存在する構造メタデータの
DB 転記と、(ii) candidate→approved のライフサイクルを持つ**説明テキスト**のみ。

---

## 2. 設計原則（不変条項）

- **E1 二層分離**: generic と contextual を別レコードで持ち、混ぜない。generic は論文非依存、
  contextual は document スコープ。
- **E2 candidate-only**: LLM 生成説明は常に `status='candidate'`。学習者に出すのは
  `approved` のみ（教員確定なしに露出しない）。
- **E3 幻覚ガード**: generic 説明は **L層凍結版へのリンク（confirmed identity link）がある場合のみ**
  引用ベースで生成・表示する。リンクが無い要素は contextual のみ（standardization の
  「LLM 単独主張は unknown」と同じ思想。LLM の一般知識だけで汎用説明を書かせない）。
- **E4 構造は導出・説明は保存**: upper/lower 構造の正本は context_lens の読み時導出のまま。
  新設するのは説明テキストの格納庫であって、関係グラフの複製ではない。
- **E5 #499 遮断の維持**: 反復照合の観察段・照合段に上位概念テキストを注入しない。
  図の文脈説明は**解析完了後**の独立ステージで生成する（確証バイアス機構の外）。
- **E6 既存共通則の継承**: evidence_quote / reason / confidence 必須、confidence 生値は
  API/UI に出さない（段階ラベル）、P4 情報を落とさない（dismiss / supersede は状態遷移）、
  権限 fail-closed、U層計測、`core/` の FastAPI 非 import、llm_worker 骨格の利用（コピペ禁止）。
- **E7 ステージ順序を変えない**: equation_semantics の位置は動かさない。thesis 確定後の
  新ステージが位置づけを補う（(a) の解法）。
- **E8 A層の既存 agent 非改変を基本とする**: component_assembly / equation_semantics /
  figure_table_semantics のプロンプト・スキーマは触らず、**追加ステージと persistence の転記**で
  実現する（既存出力の回帰リスクを避ける）。

---

## 3. 全体構成

```
基盤   Phase 1: 構造メタデータの永続化（migration 055・非LLM・小）
Track A（説明の二層化）
       Phase 2: ContextualExplanationAgent + element_explanations（migration 056）
       Phase 3: 汎用×固有の結線（context_lens ⇄ L層 / identity link UI）
Track B（図のコース流通）
       Phase 4: evidence 供給 → ![[figure:id]] 解決 → 学習者配信
```

Track B は Track A に**依存しない**（caption のみでも図の流通は成立する）。Phase 2 が入ると
図・要素に添える説明の質が上がる、という関係。

---

## 4. Phase 1: 構造メタデータの永続化（migration 055・非LLM）

artifact に既に存在するのに DB へ転記されず消えている情報を救う。LLM 追加なし・agent 非改変。

- `theory_components` に `thesis_context JSONB` を追加。`persistence.py` の INSERT で
  ComponentRecord の `role_in_thesis` / `supports_thesis_node_ids` / `support_role` /
  `support_distance_to_headline_claim` を格納する（現状 artifact のみ→ギャップ (b) の後半解消）。
- `theory_claims` に `thesis_refs JSONB` を追加。thesis_reconstruction artifact の前方向リンク
  （`central_thesis.claim_ids` / `support_structure[].claim_ids`）を persistence 時に**逆引きして**
  claim 行へ転記する（`[{"thesis_ref", "kind", "text_excerpt"}]`）。claim 単体から上位を
  辿れるようにする（ギャップ (d) の back-link 解消）。決定論・非LLM。
- `ClaimObjectRecord.figure_ids` は現状どおり populate **しない**（figure 側
  `linked_claim_ids` が正本 — figure_concept_linking_design の決定を維持）。
- migration は冪等（`ADD COLUMN IF NOT EXISTS`）。既存行は NULL のまま（再解析で埋まる）。

効果: Phase 2 の入力構築・コースビルダー・学習者 API が artifact 全読みなしに JOIN で
上位文脈を引けるようになる。

---

## 5. Phase 2: 文脈つき説明の生成（Track A 本丸、migration 056）

### 5.1 新ステージ: ContextualExplanationAgent

- 実装場所: `src/episteme_graph/agents/contextual_explanation/`（標準 agent 構成:
  agent / input_builder / prompt / llm_client / schema / validator / repair / examples）。
- 登録: orchestrator の `_PIPELINE_STEPS` に **component_graph の後・course_mapping の前**で
  `_stage_contextual_explanation(ctx)` として追加（Tier 3-19 方式）。この時点で thesis /
  derivation / symbol registry / 図の iterative_analysis がすべて揃っている（E7）。
- 入力構築（input_builder、要素型ごと。**不透明 ID を渡さず、全て解決済みテキストに展開**する）:
  - **component**: summary + evidence claims 本文 + thesis_context（Phase 1）+
    member/parent 構成 + linked equations の semantics.summary
  - **claim**: text + thesis_refs（支持先 thesis の本文抜粋）+ section_title + subclaims +
    equation semantics
  - **equation**: semantics.summary + derivation 内位置（input/output 式）+ symbols の
    meaning（SymbolRegistry）+ linked claims 本文 + thesis 抜粋 —
    **ここで初めて数式が論文全体の位置づけを得る**（ギャップ (a) の解法。ステージ順序は不変）
  - **figure**: caption + linked_claim 本文（crosslink 由来）+ thesis 抜粋 + parts
    （analysis_profile / apparatus parts、iterative_analysis の alignment 結果を含む）+
    inner_labels — **図が初めて上位概念の解決済みテキストを見る**のはこのステージ
    （#499 の状態機械の完了後・外側なので E5 と矛盾しない）
  - L層リンク（confirmed identity link）がある要素: 凍結版の name / summary /
    body 抜粋を few-shot 引用として付与
- 出力（structured output、validator/repair 付き）: 要素ごとに
  - `contextual_explanation`: この論文での位置づけ（上位=何を支えるか / 下位=何から
    構成されるか、を織り込んだ 2〜5 文。断定でなく evidence 裏付けの記述文体）
  - `generic_explanation`: **L層引用がある場合のみ**（E3）。出典 entry_id / version_no を必ず併記
  - 各説明に `evidence_quote` / `reason` / `confidence`
- 縮退規則（P4）:
  - linked_claim_ids が空の図（本文メンション無し）は contextual 生成を**スキップ**し、
    `skipped_reason='no_concept_link'` を stage_outputs に正直に記録する
    （根拠なしに位置づけを創作させない）
  - 修復 2 回失敗した要素は生成なし＋failure 記録（配信しない）
- コスト: `CTXEXPL_MAX_ELEMENTS_PER_DOCUMENT`（既定 40。優先順位は
  component → 図 → thesis 直下 claim → 主要式。切り捨ては truncated 記録）/
  `CTXEXPL_MAX_CALLS_PER_DAY`（既定 20）/ `CTXEXPL_LLM_MODEL`（fast tier 既定）。
  1 コールに複数要素をまとめてよい（要素単位リトライは validator 側で分離）。
  `core/llm_worker/` 骨格（BaseJSONLLMClient + run_with_repair + CostGate）への
  15〜20 行アダプタで接続する（7 系統目。コピペ禁止）。

### 5.2 格納庫: `element_explanations`（migration 056）

C層 `component_explanations` は component 専用かつ course 文脈前提のため流用せず、
**全要素型を受けるポリモーフィック台帳**を新設する（W層 `element_annotations` と同型の設計）。

```sql
CREATE TABLE IF NOT EXISTS element_explanations (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL,           -- 権限継承の単位（実体 FK は documents に準拠）
  element_type TEXT NOT NULL CHECK (element_type IN
    ('figure','theory_component','theory_claim','equation')),
  element_id TEXT NOT NULL,            -- ポリモーフィック（FK なし）
  kind TEXT NOT NULL CHECK (kind IN ('generic','contextual')),
  body TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,  -- evidence_quote/reason/confidence/引用元(L層 entry+version)
  status TEXT NOT NULL CHECK (status IN ('candidate','approved','dismissed','superseded')),
  created_by TEXT NOT NULL,            -- 'pipeline' | user_id（教員手書きも可）
  reviewed_by TEXT, reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **再解析時**: 既存 candidate は `superseded` に遷移して保持（行削除しない）。
  `approved` は消さない（AI 再解析が教員確定を消さない — migration 053 と同じ原則）。
- **行削除 API は作らない**。document 削除経路（`_purge_document` 等）の明示 DELETE に同乗。
- **C層との関係**: 正本の二重化を避けるため、approved contextual を C層へ**転記しない**。
  読み手向け表示が両者を併記する（C層=承認・共有レイヤーの説明バージョン、
  element_explanations=論文文脈の二層説明、と役割が異なる）。
  `_ensure_standard_explanation` が approved contextual を standard body の初期値に使う拡張は
  任意（v1.1、未決事項参照）。
- **承認 API**（`/api/admin/...`、`_require_teacher` + `_ensure_document_editable`）:
  `GET /documents/{id}/element-explanations`（フィルタ: element_type/status）/
  `POST /element-explanations/{id}/approve` / `POST .../dismiss` /
  `PATCH .../{id}`（body 編集 — 編集は新 revision 行 + 旧行 superseded で履歴保持）。
  監査は `theory_review_events` に **`entity_type='element_explanation'`**
  （`AUDIT_ENTITY_*` カタログへ追加、`services.record_review_event` 経由）。
- **UI**: W層「深く検討」モーダルの候補注釈カードと同じパターン（confirm/dismiss）。
  要素インベントリ・図モーダルにも説明カードを表示。
- **U層**: `feature='pipeline:contextual_explanation'` で計測。

### 5.3 W層・学習者への露出

- W層 dialogue の grounding に approved / candidate 説明を注入（candidate は
  「AI候補」ラベル付き — 既存の `_CONTEXT_STATUS_LABELS` 語彙を流用）。
- 学習者: 既存のグラフ要素説明生成（`learning.py::_generate_graph_element_explanation`）が
  **approved の element_explanations を優先**して返し、無ければ従来のローカル生成に縮退する。
  candidate は学習者に出さない（E2）。confidence 数値は返さない。
- E層本体（見取り図 UI）は本設計の非スコープだが、このテーブルが E層の供給源になる。

---

## 6. Phase 3: 汎用×固有の結線（Track A 後半）

- **context_lens に generic ブロックを追加**: focus 直下に、confirmed identity link 先の
  L層エントリ（active のみ）の `name` / `summary` / `standardization_status`（段階ラベル）を
  出典付きで返す（`focus.generic`）。DB 読みは既存方針どおり読み取り専用・fail-soft。
  逆方向（shared_part が focus のとき）は「この部品のインスタンス一覧＋各論文での
  approved contextual 説明」を返す（閲覧不可 document は既存どおり除外し hidden_count を正直に返す）。
- **identity link UI の脱 UUID**: `deliberation.js` のリンク行で、生 UUID の代わりに
  エントリ name + summary 冒頭を表示（API がエントリ概要を同梱する）。
- **dialogue grounding**: generic ブロックも `[文脈: 共通部品]` として注入。
- **journey（任意・要 UX 判断）**: 共通部品 hop の事実文に summary 冒頭 1 文を添える。
  journey の「事実文のみ・数値なし」原則の範囲内でのみ行う。v1 では見送り可。

これで「汎用部品としての説明」と「この論文での上位・下位との結び付き」が
**同一画面（W層モーダル）で初めて同時に読める**。

---

## 7. Phase 4: 図のコース流通（Track B）

### 7.1 供給（非LLM・決定論）

- `course_content_builder._topic_evidence_links()` に **kind='figure'** を追加:
  トピックの `linked_component_ids` → `theory_components.source_scope.figure_id/figure_key`、
  および `linked_claim_ids` → `FigureRecord.linked_claim_ids` の逆引き、の 2 経路で
  図を決定論導出し、`{figure_id, figure_key, caption, document_id}` を evidence item 化する。
- `CourseMappingAgent`: `CourseTopicMapping` に `linked_figure_ids` を追加。
  component の source_scope から**決定論的に**導出（LLM 変更なし）。
- `course_data.py`: `CourseTopic` に `linked_figure_ids` フィールド＋アクセサを追加
  （JSONB 内なので migration 不要。素の dict アクセス禁止の原則どおりアクセサ経由）。
- 原稿スタジオのトピック下書きプロンプト（`lecture_studio/topics.py`）: evidence dict に
  figure（id + caption + 説明。approved 優先、candidate は「AI候補」ラベル付き —
  教員向け下書きなので candidate 供給可）を追加。
  **これで予約記法 `![[figure:id]]` に初めて実データが供給される。**

### 7.2 記法解決（正本は Python 側）

- `core/lecture.py` に、equation の `[[FORMULA_N]]` 解決と同格の **figure 解決**を追加:
  `![[figure:id]]` → 図ブロック（画像 URL + caption + approved contextual 説明）。
  受講表示・スタジオプレビュー・スライド分割が**同じ関数を通る**
  （プレビューと配信のレンダラ共有の既存原則。クライアント側に解決ロジックを再実装しない）。
- スライド分割の文字数換算: 図 1 個 = N 字換算（数式=60 字と同様に既定値を置く。
  既定 200 字を提案、`===` 明示分割優先は不変）。読み上げ（spoken）には図を含めない
  （v1 は caption を読まない。音声なしスライドのタイマー送り縮退と整合）。
- フロント（`admin-lecture-studio.js` の埋め込み解決・`app.js` のスライド描画）に
  figure 分岐と `<img>` 描画を追加。未解決 id は既存の「未解決」フォールバックカードのまま。

### 7.3 学習者配信（fail-closed）

- 新 endpoint: `GET /api/learning/courses/{course_id}/figures/{figure_id}/image`
  （admin endpoint は `_require_teacher` のまま流用しない）。ゲートは 3 条件の AND:
  1. 受講ゲート（`get_accessible_course_data` — 本人が当該コースを閲覧できる）
  2. 図の document がコースの `sources[].document_id / material_id` に含まれる
  3. 図がコース content（topics[].linked_figure_ids または student_material 内の
     figure 参照）から実際に参照されている
  いずれか欠ければ 404（fail-closed）。
- 図メタ（caption + **approved** contextual 説明のみ）は教材レスポンス
  （`get_topic_material` / lecture sequence の slide payload）へ埋め込む
  （独立 JSON endpoint は作らない — 呼び出し回数と権限判定面を増やさない）。
- bbox・パーツオーバーレイは学習者 v1 非スコープ（教員レビュー用のまま）。

### 7.4 コースビルダー（任意・v1.1）

- コースビルダーのコンテキスト SQL に document_figures の件数と caption 一覧を追加し、
  LLM がトピック設計時に図の存在を知れるようにする。

---

## 8. ガードレール（`backend/tests/`、guardrail_helpers 使用）

`test_contextual_explanation_guardrails.py` / `test_course_figure_guardrails.py`:

- pipeline が `status='approved'` を書かない（candidate-only）
- 学習者 API が candidate / dismissed / superseded を返さない、confidence 生値を返さない
- generic 説明が L層リンク無しで生成されない（E3）
- linked_claim_ids 空の図に contextual が生成されず skipped_reason が記録される
- element_explanations に行削除 API が無い / 再解析が approved を消さない
- 学習者図画像 endpoint の 3 条件 fail-closed（未受講 403/404・source 外 document 404・
  未参照図 404）
- #499 の観察段・照合段プロンプトに thesis / claim 本文が渡らない（既存遮断テストへ追加）
- `contextual_explanation` agent が cartridge 無しで単独動作する / domain 語彙を
  ハードコードしない
- core 追加分（説明読み出しユーティリティ等）が FastAPI を import しない
- 監査 entity_type がカタログ定数経由である

---

## 9. 非スコープ（v1）

- E層本体（学習者向け見取り図・翻訳 UI）— 別設計書のまま
- 学習者への bbox オーバーレイ / L層ライブラリ本文の学習者開示
- TheoryOperationGraph への図ノード組み込み（式 backing 無しの原則は不変）
- journey での説明本文運搬（Phase 3 の任意項目、UX 判断待ち）
- 音声（TTS）での図キャプション読み上げ
- 図メンションが無い図への上位接続の推測補完（linked_claim_ids の正直な縮退を維持。
  改善するなら crosslink の検出強化という別課題）

---

## 10. 実装順序と規模感

| 順 | 内容 | 規模 | 依存 |
|---|---|---|---|
| 1 | Phase 1 永続化（migration 055 + persistence 転記 + テスト） | 小 | なし |
| 2 | Phase 4 図のコース流通（供給→記法解決→学習者配信） | 中 | Phase 1 不要（caption のみで成立） |
| 3 | Phase 2 ContextualExplanationAgent + migration 056 + 承認 API/UI | 大（本丸） | Phase 1 |
| 4 | Phase 3 レンズ結線 + identity link UI | 小〜中 | Phase 2（generic 表示に element_explanations は不要だが、逆方向表示に使う） |

Phase 4 を Phase 2 より先に出せば「教材に図が出る」体感価値が先に立ち、
Phase 2 完了時に図へ説明が自動で添わる、という段階投入になる。

---

## 11. 未決事項 → 実装時の確定（2026-07-19）

1. `_ensure_standard_explanation` は**変更しない**（C層との併記のみ。転記による正本の二重化を回避）
2. 学習者ポップアップの優先順位は **approved contextual → C層承認済み → ローカル生成** で確定
3. スライドでの図の文字数換算 = **200字**（未解決の `![[figure:...]]` も同じ換算にし、
   figures_by_id 無しで呼ぶ readiness / 音声生成経路と `slide_index` を一致させる）
4. journey への summary 添付は **v1 見送り**
5. `CTXEXPL_MAX_ELEMENTS_PER_DOCUMENT=40` / 優先順位 component → figure → claim → equation で確定

## 12. 実装記録（2026-07-19、ura-dev 未コミット）

Fable 5 指揮 + Sonnet 5 サブエージェント10体（3波）で全 Phase を実装。全テスト
（backend + src）6,475+ pass / 0 fail。主な実装上の確定・逸脱:

- **thesis_ref 文字列**は設計例の `"central"` ではなく既存コードと JOIN 可能な
  `"central_thesis"` / `"support:<section>:<idx>"`（`ComponentRecord.supports_thesis_node_ids`
  と同一語彙）に統一。
- **element_explanations の element_id**: `_stage_contextual_explanation` は
  `persist_claims_components_graph` より前に走るため、theory_component / theory_claim は
  **agent 側 ID**（component_id / claim_{span_id}）で保存される。agent ID は再解析を跨いで
  決定論的（DB UUID は再解析で変わる）ため保存形式としてはこれが正で、**読み出し側**
  （`decomposition.explanations_for_element`）が legacy_ids / span キーで DB UUID ⇄ agent ID を
  突合する（figure = document_figures.id、equation = artifact equation_id は正準のまま）。
- **学習者ポップアップの型対応**: legacy `documents.knowledge_graph` 由来の
  `concept` / `relationship` は theory 要素と ID 空間を共有せず対応不能（誤マッチ回避のため
  意図的に未マップ）。確実に対応するのは `formula` → `equation`。
- **原稿スタジオのプレビュー図描画**はサーバ側 `[[FIGURE_N]]` ではなくスタジオ既存の
  埋め込みカード機構（`@@EG_COURSE_EMBED@@`）のクライアント解決に乗せた（equation の
  既存プレビュー方式と同型）。受講側は サーバ解決（`core/lecture.py`）+ `figures` payload。
- **コスト制御**は `figure_reanalysis.py` と同じ CostGate（daily）+ 事後計上パターン。
  U層 feature は `pipeline:contextual_explanation`。
- migration は **055**（thesis_context / thesis_refs）と **056**（element_explanations）。
  document 削除経路（`versioning/deletion.py::_purge_document` / `admin.py::delete_material`）に
  element_explanations の明示 DELETE を同乗済み。

---

## 13. 追補: 説明レビューキュー + 一括承認/却下（2026-07-22）

状態: **設計確定（本節）。実装は別エージェントが並行作業中**。§5.2 の承認 API
（1件ずつの `approve`/`dismiss`）を置き換えず、その上にバッチ経路を追加する追補。

### 13.0 決定の記録

- **問題**: §5.2 の「深く検討」モーダルは説明候補を1件ずつ確認・承認する UX しか持たない。
  ContextualExplanationAgent は document 1本あたり最大 `CTXEXPL_MAX_ELEMENTS_PER_DOCUMENT`
  （既定40）件の候補を生成しうるため、1件ずつの承認はレビュー負荷が高すぎ、実運用では
  ゲート（E2 candidate-only）が事実上機能しない（承認が進まない→学習者に何も出ない状態が
  常態化する）。
- **検討した選択肢**:
  1. 一括承認/レビューキュー — candidate-only ゲート（E2）は維持したまま「1件ずつ」という
     操作上の摩擦だけを取り除く。
  2. 出所ラベル付き既定表示 + 事後修正（opt-out） — candidate をラベル付きで先に学習者へ出し、
     教員は誤りだけを事後に取り消す。
  3. 要素種別による段階化 — 図など高リスク種別のみゲートし、他は自動承認に近づける。
- **決定**: (1) を採用する。E2（candidate-only。確定は必ず人間）は不変のまま、
  「1件ずつ」の操作コストのみを下げる。(2) は学習者への露出タイミングが AI 生成直後になり
  E2 の精神（教員確定なしに露出しない）と衝突するため不採用。(3) は要素型ごとの信頼度に
  優劣をつける根拠が現時点で無く、運用観察後に再検討する（本追補の非スコープ）。

### 13.1 API

`POST /api/admin/documents/{document_id}/element-explanations/bulk-review`

- **body**: `{"action": "approve" | "dismiss", "explanation_ids": ["...", ...]}`
  （1〜200件。上限は `BULK_REVIEW_MAX_ITEMS`、既定200。超過は 422）。
- **権限**: TEACHER 以上 + `_ensure_document_editable`（§5.2 の承認 API と同じ document 単位
  fail-closed ゲート。他 document 所有の explanation_id は下記のとおり not_found 扱いにし、
  存在有無を漏らさない）。
- **セマンティクス**: 部分成功を許容する。`status='candidate'` の行だけを遷移させ、
  それ以外は失敗させず `skipped: [{"id", "status", "reason": "conflict" | "not_found"}]` に
  積んで正直に返す（1件の競合や1件の権限外指定でバッチ全体を失敗させない）。
  `document_id` に属さない・存在しない `explanation_id` は `reason="not_found"` に統一し、
  他 document の存在を推測させない。
- **監査**: 遷移した行は **1行ずつ** `theory_review_events`
  （`entity_type='element_explanation'`、`services.record_review_event` 経由、
  `payload` に `"bulk": true` を含める）。`skipped` の行は監査記帳しない
  （状態が変わっていないため）。
- **一覧**: 新規リスト API は作らない。既存
  `GET /documents/{id}/element-explanations?status=candidate` をそのまま再利用する。
- **削除 API は引き続き作らない**（P4。§5.2 の方針を継承）。

### 13.2 UI（`deliberation.js`）

- **入口**: 要素インベントリモーダルのツールバーに「説明レビュー (N)」ボタンを追加する。
  `N` = 当該 document の `status='candidate'` 件数。モーダルを開いたときに1回取得し、
  ポーリングはしない（既存 UI 規約を継承）。
- **キュー画面**: candidate を要素ごとにグループ化したカード一覧 + 各カードのチェックボックス
  + 「すべて選択」「選択解除」+「選択した N 件を承認」「選択した N 件を却下」ボタン。
  実行前に事実文で確認する（例:「承認すると学習者に表示されます」— 煽り文言・数値スコアは
  出さない、E6 継承）。実行後、応答の `skipped` があれば「N 件は状態が変わっていたため
  スキップされました」等の事実文で表示する。
- **単件操作との併存**: §5.2 の1件ずつの承認/却下カード（「深く検討」モーダル内）は
  そのまま残す。レビューキューは追加の入口であり、既存導線を置き換えない。
- **表示規約**: confidence は生値を出さず段階ラベルのみ（E6 継承）。

### 13.3 不変条項との整合

- **E2（candidate-only）は不変**: 一括であっても、`candidate → approved/dismissed` の遷移は
  常に教員の明示操作（バッチ実行ボタンの押下）が起点であり、AI が自動で確定させることはない。
- **P4（情報を落とさない）は不変**: 却下は行削除ではなく `dismissed` への状態遷移として保持する。
  `skipped` の行も状態を変えずにそのまま残る。
- **帰属必須**: 遷移した各行の `reviewed_by` / `reviewed_at` に操作者・時刻を記録し、
  監査は1行ずつ `theory_review_events` に記帳する（バッチ実行であることは `payload.bulk` で
  区別できるが、監査行自体をまとめて集約しない）。
- **権限 fail-closed（E6 継承）**: `_ensure_document_editable` を通らないリクエストは
  そもそもバッチの対象にならない。
