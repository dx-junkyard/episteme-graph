# 数式「文脈を見る」パネルの表示是正（Equation Context Panel Display）

- 起票: 2026-08-01
- 対象: 学習画面 教材区画の数式カード「文脈を見る」→ 要素文脈パネル（`element_context`）
- 姉妹文書: `docs/features/equation_hover_content_design.md`（EH1〜EH5。ホバー側の同種問題）
- 親文書: `docs/features/learner_element_context_design.md`（LE1〜LE8）
- 状態: **実装済み（2026-08-01）** — §7 実装記録を参照

---

## 0. 課題

受講画面で数式の「文脈を見る」を押すと、

1. ダイアログのタイトルが **切り詰められた生 TeX**
   （`数式 ・ \begin{aligned} \delta(t, {\bm{x}}) {} \overset{\ma…`）
2. カード本文にも同じ生 TeX が **赤字**で出る
   （`\begin{aligned} \delta(t, {\bm{x}}) {} \overset{\mathrm{}}{{}:={}}{} \frac{\rho(`）
3. 上位・下位レーンの事実文に **内部 ID** が露出する
   （`導出「derivation_eq_tex_b16」のステップ「step_001」` /
   `導出「system_derivation_0001」のステップ「sys_001_step_1」`）

数式そのものはパネルのすぐ背後の教材本文に KaTeX で整形表示されている。
つまりパネルは「読めない形の、同じ式の写し」を2箇所に出したうえで、内部 ID を
学習者に見せている。

---

## 1. 原因

### 1.1 赤字は「KaTeX が失敗した結果」であって未変換ではない

カード本文は KaTeX を通っている。`element-card.js:223` が equation focus のとき
`ctx.renderMath(summary, true)` を呼び、その実体は `app.js` の
`renderMaterialKatex()`（`throwOnError: false`）である。KaTeX は
`throwOnError:false` のとき **エラー箇所を赤字で出力する**（既定 `errorColor`）。

したがって赤字＝「TeX エンジンを通していない」ではなく
**「壊れた TeX を渡したので、KaTeX がエラーとして赤く描いた」**。

### 1.2 壊れる原因は W層ラベルの 80 字切り詰め

`backend/core/deliberation/context_lens.py:470` `_equation_label()`:

```python
text = (rec.plain_text or rec.latex or src.plain_text or src.latex
        or record.label or record.equation_id or "")
return str(text)[:80]
```

- `plain_text`（読み下し）が生成されていない式では **latex が採用**される。
- そこへ **80 字の機械的な切り詰め**が入るため、`\frac{\rho(` のように
  コマンド途中で切れた TeX 断片になる。KaTeX が通るはずがない。

`_build_equation()`（同 1455 付近）はこの1つの文字列を
**`label` と `intrinsic_summary` の両方**に入れる。

```python
label = _equation_label(record) or ref.element_id
focus = {..., "label": label, "intrinsic_summary": label, ...}
```

### 1.3 学習者射影は TeX を素通しする

`backend/core/element_context.py` の LE4 ラベル遮断は
**「裸の内部 ID 形」だけ**を一般ラベルへ置換する（`_is_internal_id_label`）。
TeX 文字列はこの検査に引っかからないため、`label` / `intrinsic_summary` とも
そのまま学習者 DTO に乗る。

### 1.4 タイトルは label をそのまま連結している

`app.js` `renderElementContextPanel()`:

```js
headTitle.textContent = kindLabel + " ・ " + focus.label;
```

`focus.label` が TeX ならタイトルも TeX になる（こちらは escape されるだけで
KaTeX を通らないため、生文字列がそのまま見える）。

### 1.5 内部 ID の露出は別経路（レーンの事実文）

`context_lens.py:693`:

```python
label = f"導出「{derivation_id}」のステップ「{step_hit}」"
```

ラベル**全体**が内部 ID ではなく「内部 ID を含む文」なので、
`_is_internal_id_label`（先頭一致の検査）が効かない。LE4 の穴。

---

## 2. 設計判断

### 2.1 パネルは数式を再掲しない（姉妹文書 EH1 の踏襲）

パネルの入口は教材本文の数式カードであり、式は画面上に整形表示されている。
**パネルの価値は「この式がどこにつながっているか」（上位・下位レーン）**であって、
式の写しではない。

### 2.2 切り詰めた TeX は、レンダリングしてもしなくても表示してはならない

- 完全な TeX なら KaTeX で描ける。ただし §2.1 より再掲する意味がない。
- **不完全な TeX は情報ではない**。赤字エラーで出すのも、生文字列で出すのも、
  学習者には「壊れている」以上の意味を持たない。よって**出さない**。
- 「表示できるものが無い」ことは事実文で言う（LE の既存縮退と同じ）。捏造しない。

### 2.3 直すのは学習者射影の層（W層は変更しない）

LE6（W層非改変）を守り、`core/element_context.py` と frontend で遮る。
W層 `_equation_label` の 80 字切り詰めは**教員向け W層 UI にも同じ赤字を出している
はず**だが、本文書のスコープ外とし §8 に申し送る。

---

## 3. 出すべき内容

数式 focus のパネル冒頭は、ホバー（EH 設計）と**同じ材料・同じ順序**にする。
2つの導線で違うことを言わない。

| 位置 | 内容 | データ源 |
|---|---|---|
| タイトル | `数式 ・ eq_2_7`（式番号があるときだけ）/ 無ければ `数式` のみ | element_id |
| カード見出し | 同上（種別チップ + 式番号） | focus.label |
| カード本文 | 役割の一行 / 意味の要約 / 記号の意味 | equations.json（`role_in_argument` / `semantic_kind` / `symbols`） |
| 上位・下位 | 既存のレーン（本命） | context_lens |

- 本文に出せる材料が何も無ければ、カード本文は既存の事実文
  （`element-card.js` の `FACT_NO_BODY`）に落とす。**TeX は出さない。**
- 数値（confidence）は出さない（LE / W8 継承）。

---

## 4. 不変条項（EC1〜EC5）

- **EC1 パネルは数式を再掲しない**: `focus.label` / `focus.intrinsic_summary` に
  TeX を載せない。タイトルにも出さない。
- **EC2 壊れた TeX を表示しない**: 切り詰め・不完全な TeX は空へ縮退させる。
  KaTeX の赤字エラー出力を学習者に見せない。
- **EC3 内部 ID を出さない（LE4 の拡張）**: 裸の ID だけでなく、
  **ID を埋め込んだ事実文**も遮る。関係の意味（「導出に属する」）は残す。
- **EC4 W層非改変（LE6 継承）**: 直すのは `core/element_context.py` と frontend。
- **EC5 ホバーと同じことを言う**: 役割 / 意味の要約 / 記号の意味は
  `equation_hover_content_design.md` §3.1 と同一の材料・同一の語彙
  （表示名は `element-vocab.js` が正本）。

---

## 5. 実装方針

### 5.1 バックエンド `backend/core/element_context.py`

1. **TeX 検出**を追加（`_looks_like_tex_math` 相当の判定。
   `\begin{` / `\frac` / `\overset` 等のコマンド出現、または波括弧の不均衡）。
2. `_project_focus` に element_type 別の後処理を入れる:
   - equation の `label`: TeX 形なら捨て、`element_id` が論文式番号形
     （`_EQUATION_NUMBER_LABEL_RE`）ならそれ、でなければ空。
   - equation の `intrinsic_summary`: TeX 形なら捨てる。
     代わりに equations.json から `semantic_kind` / `role_in_argument` /
     `symbols` を読んで本文を組み立てる（`_resolve_equation` が既に
     `equation_records(doc, artifacts=...)` を読んでいるので追加 I/O は不要）。
3. `_project_item`: レーン項目にも同じ TeX 遮断をかける
   （`leads_to` / `derives_from` の相手式ラベルも `_equation_label` 由来）。
4. `_is_internal_id_label` を拡張し、**文中に内部 ID を含むラベル**
   （`_ROLE_INTERNAL_TOKEN_RE` と同じ発想の全文検索）も内部 ID とみなす。
   `derivation_*` / `step_*` / `sys_*_step_*` / `system_derivation_*` を語彙に追加。
   置換後は `_GENERIC_ITEM_LABELS["derivation"]`（「導出の流れ」）になり、
   `relation_label`（「の導出に属する」）は保持される。

### 5.2 フロントエンド `frontend/public/js/app.js`

5. `renderElementContextPanel`: `focus.label` が空ならタイトルは種別ラベルのみ
   （`数式 ・ ` の宙ぶらりんを出さない）。
6. `elementContextToCardDto`: 既存の「label === intrinsic_summary なら見出しを消す」
   分岐は、サーバが両者に同じ TeX を入れていた時代の対症療法。サーバ側修正後は
   不要になるが、**防御として残す**（旧スナップショット・旧サーバとの組み合わせ）。
7. equation focus の KaTeX 描画（`element-card.js` 経由）は残すが、
   サーバが TeX を送らなくなるため実質使われない。**赤字エラーを出さない保険**として、
   `renderMaterialKatex` を通す前に「不完全 TeX なら空を返す」ガードを
   カード用オプション側に置く（`renderMaterialKatex` 自体は教材本文でも使うので
   `throwOnError: false` の挙動を変えない）。

### 5.3 やらないこと

- W層 `_equation_label` の 80 字切り詰めの修正（LE6 / スコープ外・§8 へ）。
- パネル内での数式レンダリング機能の追加（EC1 より不要）。
- `plain_text`（読み下し）の生成そのもの（別issue。生成されていれば
  `_equation_label` が最初に採用するため、本症状も自然に減る）。

---

## 6. ガードレール

`backend/tests/test_learner_element_context*.py`（既存ファイル）へ追加:

- **EC1/EC2**: `_project_focus` に TeX を渡すと `label` / `intrinsic_summary` に
  TeX が残らないこと。切り詰め TeX（`\frac{\rho(`）でも同様。
- **EC1**: element_id が `eq_2_7` なら label に式番号が残り、`eq_tex_b14` なら空。
- **EC3**: `導出「derivation_eq_tex_b16」のステップ「step_001」` が
  「導出の流れ」へ置換され、`relation_label` は保持されること。
- **EC5**: 役割・記号の語彙が hover 側（`equation_hover_content_design.md`）と
  同じキーで出ること。
- frontend 静的: タイトル生成が `focus.label` 空のとき種別ラベルのみになること。

---

## 7. 実装記録（2026-08-01）

| ファイル | 変更 |
|---|---|
| `backend/core/course_content_builder.py` | `_looks_like_tex_math` を公開名 `looks_like_tex_math` へ昇格（旧名はエイリアスで維持）。TeX 判定の正本を1つにし、学習者射影が import して使う |
| `backend/core/element_context.py` | `_equation_focus_label` / `_equation_explanatory_fields` / `_equation_record_in_course` 新設。`_project_focus` に `equation_record=` を追加し TeX を遮断・役割/意味/記号を付与。`_project_item` にレーン式ラベルの TeX 遮断。`_EMBEDDED_INTERNAL_ID_RE` で文中の内部 ID（`derivation_*` / `step_*` / `sys_*_step_*` / `system_derivation_*`）を遮断 |
| `frontend/public/js/app.js` | `equationExplanatoryLines()` を新設し、ホバーと文脈パネルで**同一実装**に（EC5）。`renderElementContextPanel` のタイトル生成と `evidenceChipPopoverHead` を「見出しが空なら種別ラベルのみ」に。ローディング中の見出しから内部 ID を撤去 |
| `frontend/public/js/app.js`（続き） | `looksLikeRenderableTex()` ゲートを `learnerElementCardOpts.renderMath` に挿入（TeX と判定できる文字列だけ KaTeX へ回す） |
| `frontend/public/css/styles.css` | `.element-card-body` に `white-space: pre-wrap`（本文の行構成用） |
| `backend/tests/test_learner_element_card_ui_static.py` | `test_math_renderer_is_injected` をゲート付き注入に追随 |
| `backend/tests/test_element_context_core.py` | `TestEquationPanelDisplay`（10件） |
| `backend/tests/test_learning_ui_phase3_static.py` | 共有実装・タイトル生成の静的検査を追加 |

テスト: backend 全体 **7,692 passed / 24 skipped**（回帰なし）。

### 実装上の判断

- **赤字の正体**を先に確定させた（KaTeX の `throwOnError:false` によるエラー描画）。
  「TeX エンジンを通していない」わけではないため、レンダラの追加では直らない。
- **TeX 判定を第2実装しない**: `course_content_builder` の既存ヒューリスティックを
  公開名に昇格して共有した（切り詰め TeX も `\begin{aligned}` を含むため検出できる）。
- `_project_item` の空ラベル時の挙動は**変えていない**（過剰置換を避けるため、
  equation の TeX 経路と内部 ID 経路だけを分岐）。
- 内部 ID 遮断は**過剰置換しない**ことをテストで固定（「線形化から成長方程式までの
  導出」のような人間可読ラベルはそのまま残る）。

### 残作業

- docker 実機での目視確認（タイトル・本文・レーンの3点）。
- §8 の申し送り（W層の 80 字切り詰め / `plain_text` 未生成）。

---

## 8. 申し送り（別issue候補）

- **W層 `_equation_label` の 80 字切り詰め**: 教員向け W層パネル
  （`deliberation.js`）でも同じ壊れた TeX が赤字で出ているはず。W層側で
  「切り詰めるなら TeX ではなく読み下し / 式番号にする」判断が要る。
- **`plain_text` 未生成の式が多い**こと自体（EquationSemantics の
  reconstruction が走っていない式）。ホバー・パネル双方の情報量に直結する。
