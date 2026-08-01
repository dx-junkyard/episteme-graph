# 数式ホバーの表示内容の是正（Equation Hover Content）

- 起票: 2026-08-01
- 対象: 学習画面 教材区画の**数式ブロック**をホバーしたときのツールチップ内容
- 親文書: `docs/features/learning_ui_inspect_hover_design.md`（IH1〜IH10）、
  `docs/features/learner_element_context_design.md`（LE1〜LE8）
- 状態: **実装済み（2026-08-01）** — §9 実装記録を参照

---

## 0. 課題

数式ブロックにホバーすると、ツールチップに **その数式の生 LaTeX が未レンダリングのまま**
表示される。すぐ上に KaTeX で整形済みの同じ数式が出ているため、**情報量ゼロの劣化コピー**に
なっている。タイトルも内部 ID（`eq_tex_b14`）がそのまま出る。

観測例（教材「修正重力理論のテストとしての大規模構造」）:

```
📄 教材
eq_tex_b14
\begin{aligned} \delta(t, {\bm{x}}) {} \overset{\mathrm{}}{{}:={}}{} \frac{\rho(t,{\bm{x}})-
\bar\rho(t)}{\bar\rho(t)}. \end{aligned}
```

---

## 1. 原因（現状の実装）

### 1.1 ツールチップ本文の選択規則

`frontend/public/js/app.js:3678` `materialAnchorTooltipContent()`:

```js
var label = item.title || item.label || item.caption || item.figure_id || item.id || "";
var body  = item.explanation || item.summary || item.plain_text || item.caption || item.raw_text || "";
```

- `body` の**最終フォールバックが `raw_text`（生 TeX）**。
- ツールチップは `escHtml()` するだけで KaTeX を通さない（`showMaterialTooltip`,
  `app.js:3697`）。したがって `raw_text` に落ちた瞬間、**必ず**崩れた TeX が出る。
- `label` は `item.title` を素通しするため、title が内部 ID 形でもそのまま出る。

### 1.2 数式アイテムに説明フィールドが無い

`backend/core/course_content_builder.py:906-920`（`build_topic_evidence_items` の
equation 分岐）が返すのは:

| フィールド | 実際の値（今回のケース） |
|---|---|
| `title` | `link.label` / `formula.label` が無いため正規化 ID `eq_tex_b14` |
| `summary` | `link.summary`（通常空） |
| `latex` | 表示用 LaTeX |
| `plain_text` | 未生成のため空 |
| `raw_text` | 生 TeX |

→ `explanation` / `summary` / `plain_text` が全て空 → `raw_text` に落ちる。

### 1.3 上流には説明材料があるのに投影されていない

`equations.json`（`src/episteme_graph/agents/equation_semantics/schema.py:762-820`
`to_equations_export()`）は以下を持っている:

- `role_in_argument`（統制語彙）/ `equation_type` / `secondary_types`
- `semantic_kind`（= `sem.summary`）
- `symbols[]`（#439。symbol → meaning → source を self-describing に保持）
- `symbol_definitions` / `assumptions` / `derivation_links`（from/to equations）
- `linked_claim_ids`

ところがコーススナップショットへ落とす `course_content_builder.py:1271-1283` の
`equation_items` は **`equation_id` / `label` / `latex` / `plain_text` / `raw_text` の
5つしか拾っていない**。`_topic_content_block_formulas`（同 783-805）も同じ5フィールドで、
`build_topic_evidence_items` に説明材料が一切届かない。

**これが本質的な欠落**である。フロントの問題は、届かなかったときに生 TeX へ落ちる
フォールバック設計にある。

---

## 2. 設計判断: 数式に同じ数式を出す意味は無い

ホバー + ラッチの目的は **「この要素について質問する」ための係留**（IH3/IH5）であり、
要素の再掲ではない。式そのものは本文で既に整形表示されている。学習者がその瞬間に
必要としているのは **「この式は何で、なぜここにあるのか」** である。

したがって:

> **数式ホバーは数式を再掲しない。式を*読むための*情報だけを出す。**

---

## 3. 出すべき内容（優先度順）

ホバーは API を呼ばない（IH2）。したがって以下は **freeze 時のコーススナップショットに
載っていること**が前提になる。

### 3.1 ホバー（即時・無フェッチ）

| # | 内容 | データ源 | 表示例 |
|---|---|---|---|
| 1 | **役割の一行** | `role_in_argument` / `equation_type` / `semantic_kind` | 「この節での役割: 定義」 |
| 2 | **記号の意味 2〜4個** | `symbols[]`（meaning のみ） | 「δ: 密度揺らぎ / ρ̄: 平均密度」 |
| 3 | **読み下し** | `plain_text`（音声用に既存の枠） | 「デルタは、ロー引くローバーをローバーで割ったもの」 |

- ラベルは `label` を優先し、**内部 ID 形（`eq_tex_b14` 等）なら一般ラベル「数式」へ置換**する。
  置換規則は `backend/core/element_context.py` に既にある（LE6 の「裸の内部 ID を出さない」）ため、
  同じ規則を踏襲する（正本をフロントに再実装しない — 投影側で解決してから配る）。
- 1〜3 のいずれも無ければ **IH8 の固定文**「この部分の説明はまだ用意されていません」。
  **生 TeX へは絶対に落ちない。**
- confidence 等の生数値は出さない（IH7 / W8）。

### 3.2 「文脈を見る」パネル（オンデマンド・API）

導出リンク（from/to equations）・依存 claim・上位/下位関係は**ホバーの担当ではない**。
既存の学習者向け文脈 API（`GET /api/learning/courses/{course_id}/elements/equation/{id}/context`、
`backend/core/element_context.py`）が既にこの役割を持っている。役割分担を固定する:

| 面 | 問い | 実装 |
|---|---|---|
| ホバー | これは何か | スナップショット投影のみ・LLM/API 0回（IH2） |
| 文脈を見る | どこから来てどこへ行くか | `element_context` API（既存） |

---

## 4. 不変条項（EH1〜EH5）

- **EH1 数式を再掲しない**: ツールチップ本文に `latex` / `raw_text` を出さない。
  数式の描画責務は本文カード（KaTeX）のみが持つ。
- **EH2 未整備は事実文（IH8 継承）**: 説明材料が無い数式は固定文へ落とす。
  生 TeX・内部 ID をフォールバック表示に使わない。
- **EH3 ホバーは無フェッチ（IH2 継承）**: ホバー内容はコーススナップショットに
  既にある値のみ。API・LLM を呼ばない。
- **EH4 数値を見せない（IH7 / W8 継承）**: confidence・スコアの生数値を出さない。
- **EH5 A層非改変**: `src/episteme_graph/agents/` は読むだけ。`equations.json` の
  スキーマは変更しない（投影側だけを直す）。

---

## 5. 実装（本筋 = 投影の拡張まで行う）

フロントのフォールバック除去だけでは「劣化コピーが固定文に変わる」だけで、
**学習者の得る情報は増えない**。したがって投影の拡張まで含めて1つの変更とする。

### 5.1 バックエンド — 説明材料の投影

`backend/core/course_content_builder.py`:

1. `_equation_semantic_projection(eq)` を新設。`semantics`（`EquationSemantics`）配下と
   equations.json export 形の両方から `role_in_argument` / `semantic_kind` / `symbols`
   を平坦化する。役割語彙と導出規則は **A層（`ROLE_IN_ARGUMENT_VOCAB` /
   `derive_role_in_argument`）を import して使う**（EH5。`core/figure_presentation.py`
   が `agents/figure_modes` を import しているのと同じ前例）。
2. `_equation_symbol_meanings(eq)` を新設。`defined_symbols[].meaning` が解決済みの
   記号だけを `{symbol, meaning}` で最大6件返す。**meaning 未解決の記号は落とす**。
3. `_equation_display_title(label, normalized_id)` を新設。ラベル → 論文式番号
   （`eq_2_7`）→ 一般ラベル「数式」の順（EH2）。
4. `_content_blocks` の `equation_items` に投影結果を展開。fallback formula
   （チャンク由来）も同じ関数を通す（説明材料が無いので空になる）。
5. `_topic_evidence_links` の equation リンクに `extra=` で同フィールドを載せる。
6. `_topic_content_block_formulas` / `build_topic_evidence_items` の両読み出し経路で
   透過。**`summary` に latex を入れる経路（旧 branch 3）を撤去**する。

### 5.2 フロントエンド — 表示

- `frontend/public/js/element-vocab.js`: `equationRoleLabel()` を追加
  （`premise/definition/derived/result/constraint` → 前提/定義/導出結果/結果/制約）。
  **訳語表はここが唯一** — スナップショットには語彙キーのまま載せる。
- `frontend/public/js/app.js` `materialAnchorTooltipContent()`: 本文を行の配列で組み立て、
  `latex` / `raw_text` を一切参照しない。行は「役割 → 意味の要約 → 記号 → 読み」の順。
  `.inspect-tooltip-body` は既に `white-space: pre-wrap` なので改行で足りる。

### 5.3 後方互換と副作用

- **既存コースは再生成まで欠落したまま**（V層のスナップショット不変性を守る）。
  欠落時は IH8 の固定文へ落ちる（劣化許容 — `component_evidence_redesign.md` Phase 1
  と同じ方針）。
- `symbols` は投影側で6件に切り、表示は4件まで。
- `role_in_argument` は equation_type が無い / `unknown` の式では**導出しない**。
  `derive_role_in_argument` の既定値「premise」を貼ると、解析されていない式に
  「前提」という嘘のラベルが付くため（EH2）。
- ツールチップから `raw_text` を外す変更は equation 以外の kind にも効くが、
  `raw_text` を持つのは equation アイテムだけなので実害は無い。DTO 自体は
  `raw_text` を保持する（本文カードが「原文（未整形）」表示に使う）。

---

## 6. ガードレール

新規テストファイルは作らず、同じ面を既に守っている2ファイルへ追加する。

`backend/tests/test_learning_ui_phase3_static.py`
（`TestEquationTooltipNeverRepeatsTheEquation`）:

- **EH1**: `materialAnchorTooltipContent` が `item.latex` / `item.raw_text` を参照しない。
- **§3.1**: 役割 / 記号 / 読み下しの3材料を使っている。
- **EH5**: 訳語は `ElementVocab.equationRoleLabel` 経由で、未知キーは `""`。
- IH8 固定文へ落ちる契約（`if (!label && !body) return null;`）を維持。
- IH2（無フェッチ）は既存の同ファイル内テストが継続して担保。

`backend/tests/test_topic_material_evidence_items.py`
（`TestEquationExplanatoryProjection` / `TestEquationDisplayTitle`）:

- 投影が `semantics` 配下から役割 / 意味の要約 / 記号を平坦化すること。
- **EH2**: meaning 未解決の記号を落とす / equation_type 無しで役割を捏造しない /
  合成 ID（`eq_tex_b14`）が「数式」になり式番号（`eq_2_7`）は残ること。
- **EH4**: 投影に `confidence` が混ざらないこと。
- **EH1**: content_blocks 経路の `summary` に latex が入らないこと。
- `_topic_evidence_links` が空の説明材料でキーを増やさないこと。

---

## 7. 非スコープ

- ツールチップ内での KaTeX レンダリング（EH1 により数式自体を出さないので不要）。
- ホバー内容のリアルタイム LLM 生成（IH2）。
- 図・claim・component のホバー内容の見直し（本文書は数式のみ。
  ただし `materialAnchorTooltipContent` は共通関数なので、Phase 1 の
  `raw_text` 除去は全種別に効く）。
- `equations.json` スキーマの変更（EH5）。

---

## 8. 参照

| 対象 | 場所 |
|---|---|
| ツールチップ本文の選択 | `frontend/public/js/app.js:3678` |
| ツールチップ描画 | `frontend/public/js/app.js:3697` |
| equation evidence item | `backend/core/course_content_builder.py:906-920` |
| formulas 投影 | `backend/core/course_content_builder.py:783-805` |
| equation_items 生成 | `backend/core/course_content_builder.py:1271-1283` |
| equations.json export | `src/episteme_graph/agents/equation_semantics/schema.py:684-820` |
| 学習者向け文脈 API | `backend/core/element_context.py` |
| 内部 ID → 一般ラベル置換 | `backend/core/element_context.py`（LE6） |

---

## 9. 実装記録（2026-08-01）

### 変更ファイル

| ファイル | 変更 |
|---|---|
| `backend/core/course_content_builder.py` | `_equation_semantic_projection` / `_equation_symbol_meanings` / `_equation_display_title` 新設。`_content_blocks` / `_topic_evidence_links` / `_topic_content_block_formulas` / `build_topic_evidence_items` の4経路へ配線。A層から `ROLE_IN_ARGUMENT_VOCAB` / `derive_role_in_argument` を import |
| `frontend/public/js/element-vocab.js` | `equationRoleLabel()` + `EQUATION_ROLE_LABELS`（訳語の正本） |
| `frontend/public/js/app.js` | `materialAnchorTooltipContent()` を行組み立てに変更。`latex` / `raw_text` 参照を撤去 |
| `frontend/public/{index,admin}.html` | element-vocab.js のキャッシュバスター `-1` → `-2` |
| `backend/tests/test_learning_ui_phase3_static.py` | `TestEquationTooltipNeverRepeatsTheEquation`（7件） |
| `backend/tests/test_topic_material_evidence_items.py` | `TestEquationExplanatoryProjection`（9件）/ `TestEquationDisplayTitle`（5件） |

テスト: backend 全体 **7,694 passed / 24 skipped**（回帰なし）。

### 実装中に見つかった追加の生 TeX 供給源

`build_topic_evidence_items` の content_blocks 経路（旧 branch 3）が
`"summary": formula.get("plain_text") or formula.get("latex")` としており、
**`raw_text` を塞いでも `summary` 経由で生 TeX がツールチップに出る**第2の穴だった。
`semantic_kind` → `plain_text` の順に変更して塞いだ（EH1）。

### レビューフォローアップ（2026-08-01、同日）

実装レビューで見つかった2件を追加修正した。

1. **説明材料ゼロの数式が IH8 固定文に到達できなかった（仕様未達, §3.1）** —
   `materialAnchorTooltipContent()` は body が空でも `_equation_display_title` 由来の
   `item.title`（最悪「数式」）が必ず非空のため `if (!label && !body) return null;` が
   成立せず、「📄 教材 / 数式」だけの空箱ツールチップになっていた。
   `item.kind === "equation"` かつ body が空なら `null` を返すようにし、
   `showMaterialTooltip()` の既存 `content === null` 分岐（IH8 固定文
   「この部分の説明はまだ用意されていません」）に乗せた。equation 以外の kind は
   従来どおりタイトルのみの content を許容する（本設計書のスコープは数式のみ, §7）。
2. **equation リンクの TeX 風 summary が漏れる経路が残っていた（EH1）** —
   `_topic_evidence_links` の `add()` の TeX ガードが
   `if not latex and _looks_like_tex_math(summary):` だったため、必ず `latex=` を渡す
   equation 分岐ではガードが素通りし、`semantics.summary` が TeX 混じりだと
   `link.summary` → `equationExplanatoryLines` の `src.summary` 経由で生 TeX が
   ツールチップ本文に出た。**TeX 風 summary は latex の有無にかかわらず summary から
   除去**し、latex が未指定のときだけ latex へ移す（既存挙動は維持）よう変更した。

ガードレール（新規ファイルは作らず既存クラスに追加）:
`TestEquationTooltipNeverRepeatsTheEquation::test_equation_without_explanatory_lines_falls_back_to_ih8` /
`TestEquationExplanatoryProjection::test_tex_like_summary_never_leaks_into_equation_link`。

### 実機確認フォローアップ（2026-08-02）

実機で「ホバーに生 TeX が出続ける」症状を確認。原因は上記2とは別の**読み取り時の穴**:
リンク生成時の TeX ガード（上記2）導入**前**に freeze された既存コースの
`evidence_links` には TeX 混じり `summary` が保存済みで、`build_topic_evidence_items`
の equation 分岐が `link.get("summary")` を無フィルタで item に載せていた。
読み取り時にも `_looks_like_tex_math` で落とすようにし、**コースを再構築しなくても**
既存スナップショットが IH8 固定文（説明材料なし）に縮退するようにした（EH1/EH2）。
ガードレール: `TestEquationExplanatoryProjection::test_stored_tex_summary_is_dropped_at_read_time`。

なお役割・記号・読み下しの**説明材料**は freeze 時スナップショット由来のため、
表示にはコース再構築（トピック再生成）が必要（§5.3 のとおり）。また frontend は
volume mount 無しのビルド配信 + `app.js` にキャッシュバスターが無いため、反映には
`docker compose up -d --build frontend` とブラウザのハードリロードが必要。

### 残作業

- docker 実機での目視確認（トピック再生成後に役割・記号の意味が出ること）。
- 既存コースの再生成タイミング（教員操作）は運用判断。再生成前は IH8 固定文。
