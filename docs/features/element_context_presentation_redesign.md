# 要素文脈の提示再設計（Element Context Presentation Redesign）

- 起票: 2026-08-02
- 対象: 数式・導出・論理要素・記号・主張・図・本文根拠の**文脈提示**4画面
  - S1 学習画面・数式ホバー（snapshot 由来・無フェッチ）
  - S2 学習画面「文脈を見る」パネル（`element_context` API）
  - S3 管理画面・原稿スタジオ「根拠リンク」ペイン（外殻 = snapshot / 展開 = 教員向け context API）
  - S4 W層「深く検討」モーダル（`deliberation.js`）
- 関連文書: `equation_hover_content_design.md`（EH1〜EH5）/
  `equation_context_panel_display_design.md`（EC1〜EC5・§8 申し送り）/
  `learner_element_context_design.md`（LE1〜LE8）/
  `element_context_lens_design.md`（W層 context lens）/
  `component_evidence_redesign.md`（component チップ = 良い先例）
- 状態: **Phase 0〜2 実装済み（2026-08-03）** — §10 実装記録を参照。Phase 3（二層説明の結線）は未着手。
  調査は Fable 5 指揮 + Opus 5 サブエージェント8体（現状調査5 + 設計3レンズ）、
  実装は同体制の3 Wave + 統合（2026-08-02〜03）。

---

## 0. 課題（オーナー指摘）

原稿スタジオ「根拠リンク」と学習画面「文脈を見る」の表示から、読み手が

1. **位置づけ** — この数式が論文の議論のどこに座るのか
2. **構成** — 何をもとに構成されているのか
3. **導出** — どこから・どの操作で導かれ、どこへつながるのか

を把握できない。観測された表示（数式 `eq_tex_b14` = δ(t,x):=(ρ−ρ̄)/ρ̄）:

- 管理画面: タイトルが生ID `eq_tex_b14` /「対応付け: 対応付けなし」という内部状態文 /
  「この論文での役割 Theory basisの根拠となる [AI候補]」/ 英語 stage 名と
  `Define eq_tex_b16` 型のID ラベルが同じ関係語で混在 /
  `導出「derivation_eq_tex_b16」のステップ「step_001」` の内部ID2連 /
  80字で途切れた生 TeX
- 学習画面: 途中で切れた英文（`…from its spatial mea`）/「導出の流れ」が2件並び区別不能 /
  「関連する数式」という情報量ゼロの一般ラベル / 記号5件の羅列（意味なし）

直近の是正（EH: ホバー / EC: パネル）は生 TeX・内部IDの**漏洩**をフィルタで止めたが、
置換先が種別ごと1語彙の一般ラベルであるため、**漏洩は止まったが情報量ゼロ**という
新しい問題が残った。本文書はその根本原因と、場面×要素種別ごとの提示再設計を定める。

---

## 1. 調査で確定した根本原因（RC1〜RC12）

すべて実コードで検証済み（file:line 付き）。

| # | 原因 | 所在 |
|---|---|---|
| RC1 | **W層 ITEM が label しか運ばない**。人間可読の区別材料・説明文が ITEM に載らないため、下流（学習者射影・フロント）でどう頑張っても復元不能 | `context_lens.py` の `_item()`（:321） |
| RC2 | `_equation_label` = `plain_text→latex→…` の `[:80]` 機械切り。**`semantics.summary`（意味の一行）を候補にすら入れない**。focus の label と intrinsic_summary に同一文字列。`needs_math_review` ゲートも通らない | `context_lens.py:470-480` / `:1448-1455` |
| RC3 | 導出項目: 入出力/前提IDを1リストに混ぜて存在判定 → **導出の向きが構造的に喪失**。`step.operation`/`reason` 不使用。label は `f"導出「{id}」のステップ「{id}」"`。同ファイル内の良い生成器（`_derivation_label`:792 / `_derivation_operation_summary`:805）を使っていない | `context_lens.py:654-708` |
| RC4 | graph node 走査に **`graph_layer` フィルタが無い** → main stage（英語）と equation_detail（traceability 用 ID ラベル）が同じ関係語で混在。`node.description`（#308 で「長い説明はここへ」と定めた欄）を context_lens は一度も読まない（grep 0件） | `context_lens.py:1396-1401` |
| RC5 | 記号: defining/used を or 合流して一律「の記号を用いる」。**意味（`definition_evidence_texts`）は `evidence_refs` に押し込まれ、学習者射影が evidence_refs をキーごと落とす**（+ `element-card.js` の editable 限定描画の二重遮断）→ 意味が構造的に消える | `context_lens.py:1418-1429` / `element_context.py:448-455` |
| RC6 | 学習者射影の内部ID遮断（EC3）が element_type→1語彙の置換 →「導出の流れ」×2 が同一文字列に潰れ**区別不能**。式番号救済 `^eq[_\-.]?[0-9]` は合成ID `eq_tex_b14` を救えず事実上死んでいる | `element_context.py:150-163` / `:137-143` |
| RC7 | 「この論文での役割」= `upper[0]` の label + relation_label の**機械連結**。equation は自身の意味（`role_in_argument`）を役割に使えない。学習者側は candidate role を落とすだけで再導出せず役割欄が空に。同一要素の「役割」に2つの別解（パネル= artifact 直読み / lens = 連結文）が併存 | `context_lens.py:387-414` |
| RC8 | **管理画面は並行実装で是正が届かない**: `lsTopicEvidenceItems` が生ID タイトル（:3069/:3167）、snapshot に載せ済みの `role_in_argument`/`semantic_kind`/`symbols` を読み捨て（:3070-3081/:7050-7057）。`build_topic_evidence_items` は admin 経路で不使用。教員向け `/context` は無加工パススルー（`routes/deliberation.py:476-478`）で EC 是正ゼロ。`onCenter`/`metaBadges`/`reviewNotes` 未配線。W層モーダルは `renderMath` 未注入で生 TeX、原稿スタジオは KaTeX 赤エラー — **同じデータが3通りに壊れる** | `admin-lecture-studio.js` / `deliberation.js` |
| RC9 | 「対応付け: 対応付けなし」= トピック↔CourseMapping の**タイトル照合来歴**（`_best_mapping`）を全 evidence_link に転写した値。要素の性質ではなく、カード単位に出すと誤読 | `course_content_builder.py:1191-1205` → `:637/:738` |
| RC10 | **言語**: A層 agent はほぼ全て英語プロンプト・出力言語指定なし（例外: discuss_opening=ja / contextual_explanation=教材言語追随）。stage 英語ラベルの日本語訳表が**3箇所に分裂**し訳語も不一致（`admin-lecture-studio.js:5787`「理論的前提」/ `discuss/opening.py:111`「理論の土台」/ `element-vocab.js` 未定義） | — |
| RC11 | **切り詰め**: 60/80/120/220字の素スライスが5箇所 + `_short_excerpt`（`...`付き）の2実装。文境界無視 → `…spatial mea` の単語途中切れ | `element_context.py:388/400` ほか |
| RC12 | equation focus には**掲載セクション項目も原文根拠項目も構造的に無い**（claim には有る）。`source_evidence_ids`/`section_id` は artifact に存在。graph node の agent ID（`theory_op_0001`）が navigable=true → 「深く検討」が **404 になる死んだ導線** | `context_lens.py:1357-1459` / `:337` |

### 1.1 未使用の人間可読資産（表示改善の原資）

**必要な材料はすべて既存 artifact / DB に実在し、投影側が読んでいないだけ**である。
再解析・A層変更なしで届く（これが本件の効果/コスト比が例外的に良い理由）。

| 要素型 | 存在するのに未使用のフィールド |
|---|---|
| 数式（equation_semantics） | `semantics.summary`（意味の一行）/ `role_in_argument`（訳語表は `element-vocab.js` に既存）/ `equation_type` / `assumptions`（成立条件）/ `defined_symbols[].meaning`・`evidence_text` / `source_evidence_ids`（原文根拠）/ `source_location.section_id`（掲載節）/ `link_status`（`axiomatic`=「出発点として置かれる」等、**前段が無い理由**）/ `link_provenance` / `needs_math_review` |
| 導出（derivation_chain） | `chain_type`（equation_chain / claim_chain / **system_level** — スクショの区別不能2件はこれだけで区別可能）/ `operation`・`operation_family`・`operation_subtype` / `steps[].operation`・`reason` / `eliminated_symbols`・`retained_symbols`（「何を消して何を残すか」）/ `teaching_takeaway` / `conditions` / `source_section_ids` |
| グラフノード（theory_component_graphs） | **`description`**（atomic claim 本文240字。#308 の規約どおり「長い説明」の格納先）/ `visual_label`・`display_label`（≤30字表示用）/ `operation`・`theory_object` / `review_reasons` / 役割別 equation_ids / edge の `reasoning` |
| 記号（symbol_registry） | `definition_evidence_texts`（定義の逐語）/ `kind` / `scope`（この式限り/節/論文全体）/ `definition_status`（**`definition_missing`=「論文中に定義なし」は読者に有用な事実**）/ `notation_variants` / defining と used の**区別** |
| 主張 | `evidence_text`（**DB 行に SELECT 済みで未使用**）/ `claim_type` |
| narrative_annotator | `node_narratives[].narrative_role` / `edge_narratives[].transition_text` / `graph_summary` |
| 二層説明（element_explanations） | `contextual` 説明本文 — ただし equation は選抜最後尾で生成率極小 + 対象4画面のどこからも未参照 |

なお ID→可読ラベル解決器は既にリポジトリに3つある
（`theory_components.py:2257-2275` / `context_lens.py:792-802` / `:805-815`）が、
症状の出る経路はどれも使っていない。

---

## 2. 設計判断の核

### 2.1 診断の一行

> **病因は「ラベル1本に全部を詰め込む ITEM 契約」と「可読性の責務が生成側に無い」こと。**
> 関係（誰と誰）と説明（何がどう）を1つの `label` に押し込んだ結果、内部ID・80字切り
> TeX・英語 stage 名・機械連結文が同じ器に混ざり、学習者射影は器ごと潰すしかなくなった。
> 処方は器を増やし（ITEM v2）、可読ラベルの供給責任を W層に置くこと（LE6 改訂）。

### 2.2 読者の問いから逆算した4区画モデル

フラットな「上位/下位の関係リスト」をやめ、**読者の問い**に対応する区画で描く。
区画の順序と語彙は S1〜S4 で共通（場面差は「どこまで出すか」だけ）。

| 区画 | 問い | 主な材料 |
|---|---|---|
| ① これは何か | identity | 可読ラベル + 役割バッジ + 意味の一行 + 記号の意味 + 成立条件 |
| ② この論文での位置づけ | positioning | 理論段階（訳語 + description）/ 中心命題・主張 / 掲載節 |
| ③ 何をもとに構成されているか | composition | 定義する記号 / 使う記号 / もとになる式 / 前提主張 / 原文根拠。**空欄は `link_status` の事実文で語る**（「この式は定義であり、前段の式を持ちません」） |
| ④ どこへ行くか・どう導出されるか | derivation | 向き付き導出（入力→操作→出力）/ 次の式 / 支える主張・段階 |

**空は沈黙ではない**: 構造データから理由が決定論的に分かる空欄
（`link_status=axiomatic|external_reference|unresolved` / `definition_status=definition_missing` /
artifact 欠損）は事実文で述べる。推測はしないが、黙りもしない。

### 2.3 ラベルラダー（種別ごとの優先順フォールバック）

各要素種別に「必ず人間可読な一行」を作る優先順を定義し、
**内部ID・生TeX・英語 stage 生名をラダーの候補に入れない**。
ラダーが尽きたら一般ラベル + `unresolved:true` + **`sublabel`（区別材料）を必ず付ける**
—「関連する数式」が2件並んで区別不能になる事故は sublabel の有無で防ぐ。

### 2.4 言語方針（RC10）

- **統制語彙は訳す**: role_in_argument / theory stage / operation / chain_type /
  definition_status / claim_type / scope / relation。訳語の正本は
  `backend/core/element_vocab.py`（サーバ、非FastAPI）+ `frontend/public/js/element-vocab.js`
  （ミラー）の1組で、**キー集合・訳語文字列の一致をテストで固定**。3分裂した stage 訳語を
  ここへ統合する。
- **自由文（英語）は訳さず原文のまま完全表示**し「（論文の原文）」の出所注記を添える。
  切らない。表示パスに LLM 翻訳を入れない（捏造禁止・同期パス非LLM）。
  英語自由文は TTS（`_spoken_text_for_voice`）へ回さない。
- **翻訳依存を減らす切り札 = 記号 + 統制語彙からの日本語ラベル決定論合成**:
  `role=definition` + `defined_symbols=[δ(t,x)]` →「δ(t,x) を定義する式」、
  `operation=eliminate` + `eliminated_symbols=[b_s]` →「b_s を消去する（系レベルの操作）」。
  大半の式・導出のラベルが翻訳なしで日本語になる。
- **日本語一行の本命は二層説明の結線**（Phase 3）: `element_explanations` の
  `contextual`（教材言語追随・approved のみ）をラダー最上位に置く。A層非改変のまま
  日本語 headline が手に入る唯一の経路。

---

## 3. 不変条項の改訂・新設

### 3.1 改訂

**LE6 / EC4「W層非改変」→ LE6′「可読性の正本は W層、射影側は権限フィルタのみ」**

> `src/episteme_graph/agents/`（A層）は引き続き**読むだけ**（EH5/W1 と同一。artifact
> スキーマ不変）。`core/deliberation/` のうち**読み取り投影**（`context_lens.py` /
> 新設 `labels.py`）は学習者・教員・原稿スタジオの**共有正本**として改修してよい。
> **書き込み経路**（annotations / identity_links / store / standardization）は非改変のまま。
> 学習者射影（`element_context.py`）の遮断層は**二重防御として維持**する — W層改修は
> 遮断の発火頻度を下げるものであって、遮断を不要にするものではない。

改訂理由（記録）: ①EC 設計書 §8 が既に W層80字切りの申し送りを明記していた
②LE6 の下で学習者側だけ直した結果、射影が「壊れたラベルを検出して1語彙に潰す」層になり
RC6（区別不能）という新しい損失を生んだ ③教員側（S3/S4）は無加工パススルーのため
是正がゼロで、同じデータが画面ごとに別々に壊れた。正本の統合先は W層しかない。

**EC3「内部IDを出さない」→ EC3′「内部IDをラベルの素材にしない（遮断は最後の砦）」**

> 内部IDは**生成時点で**ラベル素材から排除する。射影側の `_EMBEDDED_INTERNAL_ID_RE` は
> 安全網として残すが、検出時は `unresolved:true` を立て **sublabel を保持**する。
> 遮断が恒常的に発動する経路は W層側のバグとして扱う。教員向けは「▸ 識別子」
> 折りたたみ内で ID を提示する（トレーサビリティは失わない）。

**EC5「ホバーとパネルは同じことを言う」→ EC5′「パネルはホバーの上位集合 + 区画順序は場面不変」**

> S2 パネルは①区画の先頭にホバーと同一の材料（役割/意味/記号）を同一実装
> （`equationExplanatoryLines`）で必ず含む。4区画の順序と語彙は S1〜S4 で共通とし、
> 場面差は「どこまで出すか」だけとする。

### 3.2 新設（CP1〜CP8: Context Presentation）

| # | 条項 | 内容 |
|---|---|---|
| CP1 | **ラベルは見出し、説明は補足行** | `label` に説明文・理由・関係の機械連結文を入れない。説明は `sublabel` / `focus.intrinsic` へ。`focus.label` と `intrinsic_summary` に同一文字列を入れない |
| CP2 | **関係語は向きを持つ** | 導出の入力と出力を同一語彙に潰さない。向きが決定論的に判る場合は向き付き関係語、判らない場合のみ中立語（それが「判らない」ことの表明と分かる語） |
| CP3 | **層を混ぜない** | `graph_layer=main`（理論段階）と `equation_detail`（traceability）を同じ関係語・同じ区画で並べない。`debug`/`inferred` はどちらにも出さない（教員向けは notes に件数の事実文 = P4） |
| CP4 | **統制語彙はキーで配り、訳語の正本は1組** | DTO には語彙キーを載せ、訳語は `element_vocab.py` + `element-vocab.js` のみ。訳語文字列をコードに直書きしない。自由文は原文のまま完全表示 + 出所注記 |
| CP5 | **切り詰めは1実装** | `excerpt(text, limit)`（文境界→語境界→文字数、常に「…」、TeX コマンド途中で切らない）が唯一の実装。素スライスを新規に書かない |
| CP6 | **説明材料を足しても関係集合を変えない** | 本設計は「既にある関係に説明を添える」ことに限る。新しい ITEM・新しい candidate を生成しない → LE1（公開=1-hop 露出承認）の同意範囲と教員レビュー対象件数が構造的に不変 |
| CP7 | **ラベルは区別可能** | 同一区画内で2件以上が label+sublabel とも同一文字列になる生成を禁止（ガードレールで固定） |
| CP8 | **navigable は実際に開けること** | `navigable=true` はその画面から中心に据えられる（API が 200 を返す）ことを意味する。DB 解決できない agent ID には立てない（fail-closed） |
| CP9 | **対応付け来歴は要素に付けない** | `_best_mapping` の照合来歴はトピック↔CourseMapping の属性。要素カードに転写せず、出すならトピック見出し行に1回 |
| CP10 | **空は沈黙ではない** | 空欄の理由が構造データから決定論的に分かる場合は事実文で述べる。推測はしない |

維持する既存条項: EH1（数式を再掲しない — ラベル候補から latex を削除して**生成側で強化**）/
EH2・EC2（未整備は事実文）/ EH3（ホバー無フェッチ）/ EH4・W8・LE3（confidence 数値非表示、
教員にも適用）/ LE1 / LE2（candidate を学習者に出さない）/ LE5 / LE7 / LE8 / W1・EH5
（A層非改変 — **本提案は A層に一切手を入れない**）/ W2 / P4。

---

## 4. DTO 契約 v2（additive・既存キーは名も意味も不変）

### 4.1 ITEM v2

```python
ITEM = {
    # ── 既存9キー不変（旧フロントはこれだけで動き続ける）──
    "element_type", "element_id", "document_id", "label",
    "relation", "relation_label", "relation_status", "evidence_refs", "navigable",
    # ── 追加 ──
    "sublabel":   str,   # 1行の区別材料・事実文（説明・理由・意味）。内部ID不可。空可
    "qualifier":  str,   # 種別内サブ種別の統制語彙キー（chain_type / stage_key /
                         # definition_status / graph_layer / effective_mode …）。訳はフロント
    "group":      str,   # 区画キー（§4.3 の表）。未知値は「その他」区画へ（P4）
    "unresolved": bool,  # ラベル解決に失敗し一般名で代替した（element-card が淡色表示）
    "label_source": str, # 来歴（explanation|semantic_summary|paper_number|text|
                         # controlled_vocab|generic）。教員のみ・学習者射影で落とす
}
```

- **導出の向きは `relation` 語彙で運ぶ**（別キーを増やさない）。
  `RELATION_LABELS` への追加（既存キーは削除しない）:
  `feeds_derivation`「の導出に入力として使われる」/ `produced_by_derivation`「の導出で得られる」/
  `used_in_derivation`「の導出の中で使われる（向き不明時）」/
  `belongs_to_stage`「の理論段階に属する」/ `used_in_operation_step`「の操作ステップで使われる」/
  `defines_symbol`「で記号を定義する」。
  訳語修正: `uses_symbol`「の記号を用いる」→「を用いる」（ITEM が記号そのもののため）。

### 4.2 focus v2

```python
focus = {
    # 既存キー不変。契約強化: label と intrinsic_summary に同一文字列を入れない（CP1）
    "headline": str,               # 一行の identity（TeX/内部ID不可。ラダー生成）
    "intrinsic": {                 # ①これは何か（文脈非依存）。値が無いキーは欠落させる
        "kind_key": str,           # equation_type / chain_type / claim_type …
        "role_key": str,           # role_in_argument 等（訳はフロント）
        "summary": str,            # 意味の一行（自由文・原文のまま・切らない）
        "summary_is_source_language": bool,   # 「（論文の原文）」注記の出し分け
        "reading": str,            # 読み下し（TeX 判定で空）
        "symbols": [{"symbol", "meaning", "defined_here": bool, "definition_status"}],
        "conditions": [str],       # assumptions / chain.conditions
        "facts": [str],            # link_status 由来等の事実文（CP10）
    },
    "placement": {                 # ②この論文での位置づけ
        "section_label": str,
        "stage": {"key": str, "description": str} | None,  # description = node.description
        "thesis_role": str,
    },
    "contextual_role_source": "committed"|"self_described"|"structural"|"unidentified",
    "review_notes": [str],         # 教員のみ（needs_math_review / definition_missing /
                                   # review_reasons の事実文）。学習者射影で落とす
}
```

### 4.3 group → 区画の対応

| group | 区画 | relation |
|---|---|---|
| `stage` | ② | `belongs_to_stage` |
| `thesis` / `claim` / `section` | ② | `supports_thesis` / `quantifies` / `appears_in_section` |
| `symbol_defined` / `symbol_used` | ③ | `defines_symbol` / `uses_symbol` |
| `equation_up` / `derivation_in` / `evidence` | ③ | `derives_from` / `produced_by_derivation` / `rests_on_evidence` |
| `equation_down` / `derivation_out` | ④ | `leads_to` / `feeds_derivation` |
| `operation` | ④（教員は折りたたみ「式の詳細層」） | `used_in_operation_step` |
| `figure` / `component` / `related` | 関連 | 既存語彙 |

### 4.4 契約の要点

- `_derive_contextual_role` は **自己記述優先**に改める:
  ① committed 注釈 → ② **self_described**（equation は `role_in_argument` 訳 + 参加 stage 訳
  から決定論合成。「理論の土台の段階で使われる定義式」）→ ③ 構造要約
  （素材は `group ∈ {stage, thesis, claim}` かつ `unresolved=false` の項目に限定。
  機械連結文は廃止）→ ④ unidentified（キーごと落とす）。
  学習者射影は `unidentified` のみ落とす（candidate role で役割欄が空になる問題の解消）。
- `_dedupe_items` のキーから label を外し `(element_type, element_id, relation)` に
  （ラベル規則変更で重複判定が揺れないように。`element_id=None` のみ label 併用。
  統合時は sublabel が非空の側を残す）。
- **derivations[] ストーリーブロック（Phase 2 拡張）**: equation focus の戻り値に
  `derivations: [{label, chain_type, operation_text, focus_role(input|output|intermediate|
  condition|unspecified), inputs[], outputs[], eliminated_symbols, retained_symbols, reason,
  relation_status, navigable}]` を追加し、S2/S3 が「入力 →[操作]→ 出力 + この式の役割」の
  カードで描く。v1（ITEM の向き付き関係語 + qualifier + sublabel）だけでも区別可能性は
  達成されるため、段階導入とする。

---

## 5. 要素種別ごとの提示ルール

### 5.1 数式（equation）

- **ラベルラダー**: ① 二層説明 contextual の第1文（approved のみ・Phase 3で結線）
  ② 論文の式番号（`(3.14)` / `eq_2_7` 形。合成ID `eq_tex_*` は式番号ではない）
  ③ **記号+役割の決定論合成**「δ(t,x) を定義する式」 ④ `semantics.summary` 第1文の
  `excerpt(…,44)`（原文言語のまま） ⑤ `role_in_argument` 訳 +「式」（「定義式」）
  ⑥「数式」+ `unresolved:true` + sublabel（掲載節/主記号）。
  **latex / plain_text（TeX判定に掛かるもの）/ raw_text / 内部IDはどの順位でも使わない。**
  `needs_math_review=true` の値は表示候補から除外（RC2 の根治）。
- **intrinsic**: 役割 / 種別 / 意味（原文・切らない）/ 読み（あれば）/ 記号（意味 +
  この式で定義されるか）/ 成立条件（`assumptions`）/ 事実文（`link_status`:
  axiomatic→「この式は定義であり、前段の式を持ちません」/ external_reference→
  「前段は本論文の外部にあります」/ unresolved→「前段の式は同定できていません」）。
- **新設レーン項目（RC12 解消）**: 掲載節（`source_location.section_id` →
  `appears_in_section`）と原文根拠（`source_evidence_ids` → `_evidence_quote` で逐語 →
  `rests_on_evidence`）。
- 出さない: 生TeX・切り詰めTeX（ラベル・sublabel・intrinsic すべて）/ confidence /
  `link_provenance` の生キー（学習者）/ `equation_id` の裸出し（教員は識別子折りたたみ内のみ）。

### 5.2 導出（derivation chain / step）

- **ラベルラダー**: ① `operation`（無ければ family）訳 + `chain_type` 訳
  「フーリエ変換による書き換え（式の導出）」「系レベルの消去」
  ② `teaching_takeaway`（**内部ID含有・`^(Derive|Define|Eliminate)\s+eq_` テンプレ形は
  正規表現で不採用** — 無検査採用は逆方向の事故）③ 操作列先頭2つ「線形化 → 消去（3ステップ）」
  ④「導出」+ `unresolved` + sublabel（節名）。
- **向き**: `input_*` / `output_*` / `intermediate_*` / `required_claim_ids`+`assumption_ids`
  を**別々に判定**し、`feeds_derivation` / `produced_by_derivation` / `used_in_derivation` /
  `requires` に振り分ける。両方に現れる式は intermediate。判定不能なら向きを**主張しない**。
  同一 chain 内の複数ステップ関与は break で1件に潰さない。
- sublabel: `_derivation_operation_summary()` の操作列 + `conditions`。
  qualifier: `chain_type`（**スクショの区別不能2件はこれだけで解ける**）。
  focus 時の intrinsic: 消える記号/残る記号・step の operation+reason・掲載節。
- 出さない: `derivation_id`/`step_id` を label に（`evidence_refs` = 教員の折りたたみのみ）。

### 5.3 論理要素（TheoryOperationGraph ノード）— `graph_layer` で3分岐（CP3）

- **main（理論段階）**: element_type は `stage` 扱い（navigable=false が正）。
  label = stage キーの**訳語**（英語 `Theory basis` を出さない）。
  sublabel = **`node.description` 第1文**（grep 0件だった資産の初回利用）。
  relation = `belongs_to_stage` / group = `stage`。
- **equation_detail（式単位の操作）**: label ラダー = ① `visual_label`（≤30字・生成済み）
  ② `display_label` ③ `operation` 訳 + 対象式の可読ラベル「定義: フーリエ空間の密度コントラスト」
  ④ `description` 第1文 ⑤「論理要素」+ `unresolved`。
  **`Define eq_tex_b16` 型の equation-ID ラベルは採用しない**（traceability 用。
  教員の識別子折りたたみへ）。relation = `used_in_operation_step` / qualifier = 層の明示。
  **学習者には出さない**（traceability 層）。教員は折りたたみ区画「式の詳細層」に残す（P4）。
- **debug / inferred**: レーンに出さない。教員向け notes に件数の事実文。
- navigable: agent ID（`theory_op_0001`）は DB 解決できないため **false に倒す**（CP8。
  「深く検討」404 の解消。refs 解決対象への追加は別issue — §9 Q8）。

### 5.4 記号（symbol）

- label: `canonical_symbol`。記号は「式の再掲」ではなく読解の部品なので**表示する**
  （EH1 の対象外）。KaTeX ゲート（`looksLikeRenderableTex`）を通るものはレンダリング、
  通らなければ `notation_variants` の平文形 → 一般ラベル「記号」。
- **sublabel = 意味（本修正の核・RC5）**: `definition_evidence_texts[0]` 第1文 →
  `defined_symbols[].meaning` → `kind` 訳 の順。`evidence_refs` に押し込まない。
  `definition_status=definition_missing` →「論文中に明示的な定義が見つかりません」
  （有用な事実。隠さない）。
- relation を分離: `defines_symbol`（`defining_equation_ids` に focus が含まれる）/
  `uses_symbol`（used のみ）。教員のみ: `scope` 訳・`notation_variants`。

### 5.5 主張（theory_claim）

label = `text` の `excerpt(…,60)`（文境界。`[:80]` 素スライス廃止）→ `normalized_text` →
「主張」+ `unresolved`。sublabel = `claim_type` 訳 + `evidence_text` 第1文
（**SELECT 済み未使用資産**）。出さない: agent ID / UUID / `support_status` 内部語彙。

### 5.6 コンポーネント / 図 / 本文根拠 / 中心命題

- component: `component_context.py` の rich 投影が正解。ITEM 側も同じ材料
  （label = name → label、sublabel = `narrative_role` → `role_in_thesis` →
  `teaching_takeaway` の既存優先順）に寄せる。
- figure: caption 第1文 → `figure_label` → `effective_mode` 訳。sublabel = mode 訳 +
  装置候補パーツ。
- evidence: **逐語そのものを label に**（`excerpt(…,70)` + 引用符）。sublabel = 節見出し。
  逐語がラベルなら「本文の根拠箇所」への置換は不要になる（RC6 の構造的解決）。
- thesis: `central_thesis.text` / `support_structure[].text`（未使用資産）を使い、
  `support:<section>:<idx>` を出さない。

---

## 6. 場面ごとの設計（S1〜S4）

### 学習者 / 教員の差分（区画構造は共通・差は以下のみ）

| | 学習者（readonly） | 教員（editable） |
|---|---|---|
| `relation_status=candidate` | 非表示（LE2） | 表示 + AI候補バッジ（W2） |
| equation_detail 層 | 非表示 | 折りたたみ「式の詳細層」 |
| `review_notes` / notes / `label_source` / `evidence_refs` / `relation` 内部キー | 非表示 | 表示（要確認事項・根拠折りたたみ・出所バッジ） |
| 内部ID | 一切出さない | 「▸ 識別子」折りたたみ内のみ |
| 操作 | 中心移動（旅）のみ | 中心移動 + 深く検討 + この根拠リンク内で見る |
| confidence 生数値 | 出さない | **出さない**（W8 は両方に適用） |

### S1 ホバー（担当 = ①これは何か のみ。EH3 維持）

見出しをラダー生成の headline に（snapshot の `symbols`+`role_in_argument` から合成可能）。
行構成: 役割 → 意味 → 記号（≤4）→（あれば）読み → 成立条件1件 → 掲載節1行。
最終行に**「▸ 文脈を見る（どこから来て、どこへ行くか）」の明示導線**を追加 —
現状ホバーは①で完結し残り3問への出口が見えないことが「把握できなかった」の一因。

### S2 文脈パネル（3問に答える主戦場）

4区画すべて。記号は下位レーンではなく**①区画に意味付きで**出す（記号は関係ではなく
読むための材料）。②に stage 訳 + description + 掲載節。③は定義/使用記号の分離 +
「もとになる式」空欄の事実文化。④は向き付き導出（v2 でストーリーカード）。
一般ラベルへの縮退時も sublabel を保持（「関連する数式（第3.2節）」）。

### S3 根拠リンクペイン（RC8/RC9 の解消）

1. **並行実装の廃止**: `get_lecture_studio_course_structure` の各 topic に
   `evidence_items = build_topic_evidence_items(topic)` を同梱し、
   `lsTopicEvidenceItems` はサーバ値優先（無ければ現行ロジックにフォールバック =
   旧応答互換）。生ID タイトル・`plain_text||latex` の生TeX経路を撤去。
   `test_topic_material_evidence_items.py` に「admin とサーバが同一 DTO」の再発防止を置く。
2. 外殻カード = S1 と同じ headline + 役割チップ + 意味 + 記号（snapshot に載せ済みの
   `role_in_argument`/`semantic_kind`/`symbols` を**読むだけ**）。
3. 展開部 = S2 と同一カードの editable バリアント。
   **`onCenter` / `metaBadges` / `reviewNotes` を配線**（グラフ詳細ペインには既にある。
   根拠リンクだけ未達だった P3「どこに出してもグラフ近傍が辿れる」の解消）。
4. **「対応付け: 対応付けなし」をカードから撤去**し、トピック見出し行に1回だけ
   「このトピックと論文の対応付け: タイトル類似」（CP9）。
5. TeX 描画の統一: `looksLikeRenderableTex` ガードを **element-card.js 内部に移す**
   （admin=無ガード赤エラー / W層=未注入生TeX / 学習=ガード付き、の3通りの壊れ方を終わらせる）。

### S4 深く検討モーダル

S3 展開部と同一カード（editable）。左右見出しを「この要素そのもの」/「この論文の中での
位置」に分け内訳との重複を解消。`renderMath`（ゲート付き）を注入。
**対話 grounding が自動改善**する副次効果: `dialogue.py` は focus/upper/lower をそのまま
注入するため、sublabel/intrinsic が載れば LLM の入力が「Define eq_tex_b16 の根拠となる」
から「理論の土台段階の定義式（δ = 背景密度からの相対ゆらぎ）」に変わる（追加コールなし・
W6 不変）。ただし `evidence_refs`（内部ID）は grounding に渡さない。

---

## 7. Before / After（eq_tex_b14 実例）

### S2 学習画面「文脈を見る」

Before（現行）: 「導出の流れ」×2（区別不能）/「関連する数式」/ 記号5件の羅列（意味なし）/
役割欄なし。3問への回答: 位置づけ ✗ / 構成 △ / 導出 ✗。

After:

```
┌ 数式 ・ δ(t,x) を定義する式 ──────────────────────── ✕ ┐
│ 役割: 定義   意味: Definition of the matter density        │
│   contrast δ as the fractional overdensity …（論文の原文）  │
│ 記号: δ(t,x) 密度コントラスト（この式で定義）               │
│        ρ(t,x) 物質密度 ／ ρ̄(t) 背景の平均密度               │
│ 掲載: 2.1 Density contrast                                 │
├────────────────────────────────────────────────────────┤
│ ■ この論文での位置づけ                                      │
│   理論の土台 に属する                        [出典に裏付け] │
│     └ 観測量を密度ゆらぎとして定義する（ノードの説明文）      │
├────────────────────────────────────────────────────────┤
│ ■ この式を組み立てているもの                                 │
│   〈もとになる式〉この式は定義であり、前段の式を持ちません。   │
│   〈記号〉δ(t,x) で記号を定義する — 密度コントラスト          │
│           ρ(t,x)・ρ̄(t) を用いる — 物質密度／背景の平均密度   │
│   〈本文の根拠〉「We define the density contrast as …」      │
├────────────────────────────────────────────────────────┤
│ ■ この式が支えているもの／ここから先                          │
│   〈導出〉フーリエ変換による書き換え（式の導出）(入力 1/3)    │
│             — 操作: transform → linearize   [出典に裏付け]   │
│           系レベルの消去 (入力)                              │
│             — 操作: eliminate ／ 消える記号: b_s             │
│   〈次の式〉フーリエ空間の密度コントラストの定義              │
└────────────────────────────────────────────────────────┘
```

3問への回答: 位置づけ ○（段階 + 説明文 + 掲載節）/ 構成 ○（定義/使用の分離 + 意味 +
前段なしの理由）/ 導出 ○（2本が種別・向き・操作列で区別され、行き先が名前で分かる）。

### S3 根拠リンク（教員）

Before: タイトル `eq_tex_b14` / 全カードに「対応付け: 対応付けなし」/
「Theory basisの根拠となる [AI候補]」/ 内部ID2連の導出2件 / 80字切り生TeX / 中心移動なし。

After: タイトル「数式 ・ δ(t,x) を定義する式 [定義]」/ 対応付けはトピック見出しに1回 /
役割 =「理論の土台の段階で使われる定義式」/ S2 と同区画 + 出所バッジ・式の詳細層
（折りたたみ・元ラベル `Define eq_tex_b16` は識別子欄）・▸識別子（derivation_eq_tex_b16 /
step_001）・⚠要確認（atomic claim 未リンク等）・各行に [中心にする][深く検討]。

---

## 8. 実装計画

### Phase 0 — 足場（表示変更ゼロ）
`backend/core/text_excerpt.py`（切り詰め正本）/ `backend/core/element_vocab.py`（訳語サーバ正本）/
`backend/core/deliberation/labels.py`（種別別ラベルラダー・純粋関数・既存3解決器の統合先）を
新設し、単体テストと **Python⇄JS 訳語パリティテスト**を先に置く。stage 訳語のオーナー確認は
ここで通す。

### Phase 1 — W層投影 + 学習者射影 + カード（**1リリース単位**・効果の8割）
- `context_lens.py`: ラベルラダー適用（latex 候補削除）/ 導出の向き分離 / graph_layer 3分岐 +
  `node.description` / 記号 defines・uses 分離 + 意味の sublabel 化 / focus の
  intrinsic・placement / `_derive_contextual_role` 自己記述化 / 掲載節・原文根拠項目 /
  navigable fail-closed / RELATION_LABELS 追加（変更規模 ~400-600行）。
- `element_context.py`: 新キーのホワイトリスト透過（fail-closed 維持）/ equation_detail 層の
  学習者除外 / 一般ラベル置換時の sublabel 保持。
- `element-card.js`: group 見出し（0件は描かない・group ごと最大5件）/ sublabel 2行目 /
  qualifier チップ / renderMath ガード内製 / readonly でも sublabel は描く。
- `deliberation.js` に renderMath 注入。`app.js` の重複ガード撤去。
- **S2 / S3展開 / S4 は live artifact を読むため、デプロイ即時・再解析ゼロ・再freeze ゼロで
  既存全論文が改善する。** 分割リリースは「DTO に載ったが画面に出ない」中間状態を生むため
  A+B を1コミットとする（先に「関係集合 `(type,id,relation,status)` の不変スナップショット
  テスト」= CP6 の機械検証を入れてから着手し、既存 `test_deliberation_context_lens.py` の
  ラベル期待値の失敗を「意図した変更」と「回帰」に機械的に切り分ける）。

### Phase 2 — admin 並行実装の解消 + snapshot 拡張
course-structure への `evidence_items` 同梱 / `lsTopicEvidenceItems` サーバ値優先化 /
`onCenter`・`metaBadges`・`reviewNotes` 配線 / 対応付けのトピック見出し移設 /
`course_content_builder.py` に headline・掲載節・link_status を投影（**ここだけ再生成待ち**。
既存コースは旧形のまま = 劣化許容、`component_evidence_redesign` Phase 1 と同方針）/
derivations[] ストーリーブロック（v2 提示）。

### Phase 3 — 日本語一行の本命（二層説明の結線）
`element_explanations` の contextual 生成で equation の選抜を是正
（**教材本文に `![[equation:id]]` で埋め込まれた式は必ず対象**）+
`label_source="explanation"`（**approved のみ**）をラダー最上位に結線。
A層非改変のまま日本語 headline が手に入る唯一の経路。

### A層の扱い（総合判定: **今回は一切手を入れない**）
- `teaching_takeaway` の英語テンプレ+生ID → 投影側の決定論合成（chain_type + operation +
  可読式ラベル）の方が安く良い。
- `semantics.summary` の生成言語追随（contextual_explanation 前例）→ **保留**。
  equation_semantics は全文書対象で再解析コストが最大級。Phase 1 で「何の式か」は伝わる。
- `plain_text`（読み下し）生成器 → **断念を推奨**（生成器はリポジトリに存在せず EH の
  3本目の柱は現在も構造的に常に空。数式の自然言語読み下しは品質が不安定で EH2 と正面衝突。
  「役割 + 意味 + 記号の意味」の3点で読解を成立させる。EH 設計書 §3.1 の改訂が必要）。

### 後方互換

| 経路 | 改善の届き方 |
|---|---|
| S2 / S3展開 / S4 | **デプロイ即時**（live artifact 読み。再freeze 不要） |
| S1 ホバー / S3 外殻 | トピック再生成まで旧形（欠落時は EH2 固定文へ縮退・誤情報にはならない） |
| V層の版ピン学習者 | lens 改善は届く（§9 Q4） |
| 旧フロント / 旧応答 | additive DTO + フォールバックで無害 |

### 主要リスク

- **情報過多への揺り戻し（最大）**: 学習者は4区画 + group ごと最大5件・レーン合計20不変。
  教員は式の詳細層・要確認を既定折りたたみ。**受け入れ判定は「3問に答えられるか」であって
  情報量ではない**（§7 のモックが上限の目安）。
- **AI候補バッジの可視化**: metaBadges 配線で今まで根拠リンクに出ていなかった「AI候補」が
  見えるようになる。実体は増えていない（CP6）が要レビュー増に見えるため、リリース時に
  事実文で告知。
- **テスト大量失敗の切り分け**: 関係集合の不変テストを先に入れる（上記 Phase 1）。
- **ES5 制約**: element-card.js / admin-lecture-studio.js は素の for ループで実装。
- 英語自由文が TTS / LLM grounding へ流入しないことを静的検査（`assert_source_forbids`）。

### ガードレール（新設 `test_context_lens_readability.py` ほか）

- 全 ITEM の label が内部ID形でない / equation 系文字列に TeX 判定が掛からない
- derivation ITEM の relation が向き語彙のいずれか / main と detail が同じ relation を持たない（CP3）
- symbol ITEM の sublabel が意味 or definition_missing 事実文を持つ（空を許さない）
- `context_lens.py` に素スライス `[:60]`〜`[:220]` が無い（CP5、`assert_source_forbids`）
- `focus.label == intrinsic_summary` にならない（CP1）
- **CP6**: 代表 fixture で変更前後の関係集合 `(type,id,relation,status)` が同一
- 学習者 DTO の全文字列（新キー含む）に内部ID正規表現・TeX が一致しない（再帰走査）
- Python `element_vocab.py` ⇄ JS `element-vocab.js` のキー集合・訳語の完全一致
- admin: `lsEvidenceMetaLabel` が「対応付け」を含まない / `lsEvidenceContextCardOpts` に
  onCenter・metaBadges・reviewNotes / `lsTopicEvidenceItems` のサーバ値優先
- 管理UI 3点セット（マニュアル節 + ADMIN_UI_ANCHORS + data-ui-anchor）を新 UI 要素に追加

---

## 9. オーナー判断が要る論点

| # | 論点 | 推奨 |
|---|---|---|
| Q1 | **LE6′（W層読み取り投影の共有正本化）を承認するか** — 本提案の前提。否決なら射影側に第2のラベル生成器を置くことになり正本分裂が固定される | 承認 |
| Q2 | **stage 訳語の統一語**（現在3分裂）: theory_basis =「理論の土台」（discuss 採用語。「理論的前提」は D層 assumption と衝突）/ equation_system =「式の体系」ほか | 表の語で統一 |
| Q3 | **英語自由文（summary / description / 逐語根拠）を学習者に出すか** — 原文のまま + 「（論文の原文）」注記。対象は英語論文を読む大学院生であり、現状は「何も出ない」 | 出す |
| Q4 | **V層の版ピン学習者に lens 改善を届けるか** — live 読みのため自然に届く。「表示の読みやすさは運用パラメータで学習内容ではない」（M層の live モデル解決と同じ論法）。CP6 により露出承認範囲は不変 | 届ける |
| Q5 | **「対応付け」表示の扱い** — トピック見出しへ移設 or 完全撤去（教員に行動可能性がなければ noise） | 移設して観察 |
| Q6 | **読み下し（plain_text）生成の断念確定** — EH 設計書 §3.1 の3行目を落とす改訂を伴う | 断念（別issueで再検討可） |
| Q7 | **Phase 1 の1コミット規模（~800行 + テスト改訂）の許容** — 分割すると「DTO に載ったが画面に出ない」中間状態 | 1コミット |
| Q8 | **graph node の中心据え** — v1 は navigable=false（fail-closed・404解消）。体験向上（refs 解決対象への node 追加）は別issue | v1 fail-closed |
| Q9 | **教員向け `raw_latex` 隔離キー / 学習者向け `evidence_quote` 独立キー** — v1 見送り（原文確認は外殻カードの既存表示・逐語は evidence ITEM の label で足りる） | v1 見送り |

---

## 10. 実装記録（2026-08-03、Phase 0〜2）

§9 の Q1〜Q9 は全て推奨案で承認済み。テスト: **backend 8,004 passed / 25 skipped、src 1,669 passed**
（着手前 7,810 → +194 テスト）。**未コミット**。

### 10.1 新設モジュール（Wave A）

| ファイル | 内容 |
|---|---|
| `backend/core/text_excerpt.py` | 切り詰め正本 `excerpt(text, limit)`（文境界→語境界→文字数・常に「…」・TeX トークン保護）+ `first_sentence` / `normalize_whitespace` + **`looks_like_tex_math` の正本を移設**（`course_content_builder` は再エクスポートで import 面不変） |
| `backend/core/element_vocab.py` | 訳語サーバ正本。stage（**理論の土台**等の統一訳・英語表示名からの逆引き付き）/ operation（40語）/ chain_type / link_status_**fact**（事実文）/ definition_status / symbol_scope / claim_type / equation_role。未知キーは `""`（fail-closed） |
| `backend/core/deliberation/labels.py` | 種別別ラベルラダー（`Label(text, sublabel, qualifier, label_source, unresolved)`）。latex・内部ID・英語stage生名はどの段でも不採用。`needs_math_review` ゲート・teaching_takeaway の英語テンプレ除外・`Define eq_tex_b16` 型除外・`self_described_role` 合成 |
| `frontend/public/js/element-vocab.js` | Python 正本のミラー7関数 + zoneForGroup / zoneHeading / groupHeading / qualifierLabel（フロント専用写像）。**キー集合・訳語の一致は `test_element_vocab_mirror.py` が固定** |

### 10.2 W層・射影・フロント（Wave B）

- **context_lens.py**: `_equation_label` の latex[:80] 廃止 → labels 委譲 / ITEM v2
  （sublabel・qualifier・group・unresolved・label_source を additive 追加）/
  RELATION_LABELS に向き付き6語彙追加（feeds_derivation・produced_by_derivation・used_in_derivation・
  belongs_to_stage・used_in_operation_step・defines_symbol。uses_symbol 訳語は「を用いる」へ）/
  `_derivation_membership_facts` を入出力別判定に全面改訂 / equation focus に **derivations[]
  ストーリーブロック** / graph_layer 3分岐（main=stage 訳+description、equation_detail=操作合成ラベル、
  debug=notes 件数のみ）/ 記号 defines・uses 分離 + 意味を sublabel へ / 掲載節・原文根拠 ITEM 追加（RC12）/
  focus v2（headline・intrinsic・placement・contextual_role_source・review_notes）/
  `_derive_contextual_role` は committed → **self_described** → 構造要約（制限付き）→ unidentified /
  navigable fail-closed（agent ID ノード）/ `_dedupe_items` キー変更。
  ガードレール新設 `test_context_lens_readability.py`（TeX 不在・層分離・向き語彙・素スライス不在ほか）
- **element_context.py**: 新キー透過（内部ID・TeX の値ゲート付き）/ `qualifier=="equation_detail"` の
  ITEM を学習者から除外 / 一般ラベル置換時に unresolved=True + **sublabel 保持**（RC6 の根治）/
  derivations 射影（candidate 除外・ID 除去・MINI navigable 再計算）/ contextual_role_source ベースの
  役割表示（unidentified のみ落とす・旧形は status 判定へ縮退）/ 記号 label のみ TeX 遮断免除 /
  学習者 DTO 全体の再帰走査で内部ID・TeX ゼロをテスト固定
- **element-card.js**: group による4区画ゾーン描画（0件ゾーン非表示・group ごと上限5件+「▸ ほかN件」）/
  sublabel 2行目 / qualifier チップ / intrinsic・placement の「これは何か」「位置づけ」描画 /
  derivations ストーリーカード（重複描画防止）/ 式の詳細層は editable 限定の既定折りたたみ /
  **renderMath ゲート（looksLikeRenderableTex）を内製化**（3画面で同一データが別々に壊れる問題の終了）/
  readonly でも sublabel は描く（evidence_refs / reviewNotes は editable 限定を維持）
- **app.js**: 新 DTO 透過 / headline 優先タイトル / ホバーに「掲載:」「成立条件:」行 +
  「▸ 文脈を見る（どこから来て、どこへ行くか）」導線（EH3 維持） / 重複 TeX ゲート撤去（element-card へ委譲）
- **deliberation.js**: renderMath 注入（W層モーダルの生 TeX 解消）

### 10.3 admin 正本化・snapshot 拡張（Wave C）

- **topics.py**: course-structure の各 topic DTO に `evidence_items = build_topic_evidence_items(topic)` 同梱
  （並行実装の解消 = RC8。学習画面と同一の純関数）
- **admin-lecture-studio.js**: `lsTopicEvidenceItems` はサーバ evidence_items 優先 + ローカル合成フォールバック
  （生ID タイトル・`plain_text||latex` 生TeX経路は フォールバック側でも撤去。写像は
  `test_learning_material_embed_resolution.py` の node harness 契約のため**関数内ローカル関数式**に閉じる —
  トップレベル切り出し禁止）/ `lsEvidenceMetaLabel` 単一引数化 +「対応付け」撤去（CP9）→
  `lsTopicMappingNoteHtml` がペインヘッダに1回だけ3値表示 / alt_ids 解決（source 別名の畳み込み = CP7）/
  **onCenter（`lsEvidenceCenterOnItem` = ペイン内中心移動 + trail パンくず + 戻る）・
  metaBadges（contextual_role_status）・reviewNotes（focus.review_notes）を配線** /
  `LS_THEORY_STAGE_LABELS_JA` 削除 → ElementVocab 委譲（colon 形は先に分解・未知は raw 素通し）
- **course_content_builder.py**: `_equation_display_title` を labels ラダーへ委譲（`eq_2_7` は
  「式 (2.7)」表記へ統一 — W層 headline と同一文字列になり S1〜S4 の見出しが揃う。明示ラベルは
  unresolved 時のみ1段フォールバック = P4）/ 掲載節 `section_label`（document_structure の節見出しへ
  解決できた場合のみ）/ `link_status`（統制語彙キー）/ `assumptions`（先頭2件）/ `symbols[].defined_here` /
  `_short_excerpt` → text_excerpt 委譲 / **第4の生TeX供給源**（`semantics.summary` → `semantic_kind`）を
  `_explanatory_text()` で封鎖
- **equation_hover_content_design.md**: §3.1 の読み下し行に断念注記（Q6・2026-08-03 確定）

### 10.4 実装上の主な裁定

1. 式番号の表記は「式 (2.7)」に統一（既存契約 `eq_2_7` 素通しから変更。式番号情報は保持）
2. サーバ evidence_items の source 別名2件（topic_summary / summary）は signature で1枚に畳み
   `alt_ids` で両 ID 参照を維持（CP7 と P4 の両立）
3. パンくず・ヘッダ注記には `data-ui-anchor` を付けず管理UI 3点セットの対象外とした（anchor 網羅テスト 59 passed）
4. `focus.headline` は snapshot には二重化しない（`item.title` がラダー委譲済みのため）
5. TeX 遮断は学習者射影で記号以外の全型に拡大（要求ガードレール「DTO 全文字列に TeX ゼロ」の構造的充足）

### 10.4b docker 実機確認での是正（2026-08-03）

実機（DHOST 論文・eq_tex_b14 の「文脈を見る」）で2件を検出し修正。テスト 8,005 pass。

1. **KaTeX 未知マクロの赤字（EC2 違反）**: 論文独自マクロ（`\bmx` = `\bm{x}`）は
   `throwOnError:false` の KaTeX が**例外を投げず赤いエラー HTML を正常返却**するため、
   形のゲート（`looksLikeRenderableTex`）を素通りしていた。エラー経路は2つあり
   （①式全体のパース失敗 = `katex-error` クラスの全体赤字 ②未知コマンドの
   **インライン回復** = `Parser.formatUnsupportedCmd` が該当トークンだけを
   errorColor（既定 `#cc0000`）の赤テキストで式中に埋め込む。**katex-error クラスは
   付かない** — スクリーンショットの「δ(t, は正常・\bmx だけ赤」はこちら）、
   `renderMathGated` の出力側検査は両方の痕跡（`/katex-error|#cc0000/`）を検出して
   素のテキストへフォールバックする。初回修正（katex-error のみ）は経路②を
   取りこぼしており、実機確認で発覚→両経路対応に是正（2026-08-03）。
   `test_no_direct_katex_dependency` は「API 直接呼び出し禁止」の真意へ精緻化。
2. **記号の意味文が端でクリップ**: `.element-card-symbol { white-space: nowrap }` により
   長い英語の意味文が折り返せず「切れたことすら分からない」状態だった。記号名のみ nowrap、
   意味文は折り返しへ変更（切り詰め処理自体は正常＝excerpt の「…」付き）。
3. **記号表示に照合キーを流用していた（真の根本原因）**: `canonical_symbol` は
   SymbolRegistry の `normalize_symbol` が**波括弧・空白を全除去**した表記ゆれ照合用の
   キーであり（`{\bm{x}}` → `\bmx`、`\delta(t,{\bm{x}})` → `δ(t,\bmx)`）、TeX マクロ名を
   壊した文字列になる。これを表示に使っていたため、フォールバック（1・2 の修正）では
   「赤字が黒字になるだけ」で `\bmx` の表示自体は直らなかった。根治は
   **表示は原表記・照合はキー、の分離**: `labels.symbol_display_text()` が
   `notation_variants`（`add_variant(raw)` が原表記をそのまま保存）から、
   ①波括弧の対応が取れていて ②正規化すると canonical と同一記号になる変種を選ぶ
   （同一性判定は A層 `normalize_symbol` そのもの — `core/symbol_notation.py` を
   element_vocab と同型の公認 seam として新設し、core/deliberation の A層 direct
   import 禁止ガードレールを保ったまま共有。第2実装しない）。`{\bm{x}}` は KaTeX が
   `\bm` を標準サポートするため太字の x として正しく描画される。適用点は
   `labels.symbol_label`（レーン・intrinsic の共通正本）+ W層 positioning の記号説明の
   計1+1箇所で、記号を表示する全画面に一括で効く。1・2 の修正は最後の砦として維持。
4. **フロントのレンダリングゲートが正当な TeX を弾いていた**: `looksLikeRenderableTex` の
   波括弧カウント（「直前が非バックスラッシュの閉じ括弧」の `/g` 走査）は、連続する
   閉じ括弧で1個目のマッチが直前文字を消費して2個目を数え落とす。このため 3 で正しく
   届くようになった `{\bm{x}}` を「開き2・閉じ1」の不均衡と誤判定し、KaTeX に渡さず
   素通し表示していた（旧表示 `\bmx` は括弧ゼロでゲートを**通過して**赤字になり、正しい
   表記が**弾かれる**という逆転）。エスケープ済み括弧を除去してからの単純カウントへ修正
   （JavaScriptCore で実挙動を検証: `{\bm{x}}` / `\delta(t,{\bm{x}})` = 通過、
   切り詰め TeX `\frac{\rho(` = 拒否、`\bmx` 単独 = 通過→KaTeX インライン赤→
   #cc0000 検出→素テキスト、の安全網チェーンを確認）。

実機で確認された仕様どおりの挙動（バグではない）:
- 見出しが英語（`Defines the matter density contrast…`）: 記号+役割合成（ラダー③）が
  記号名の TeX（`\bmx`）により EH1 ガードで棄却され、意味の一行（ラダー④・原文）へ縮退。
  日本語一行化は Phase 3（二層説明結線）が本命
- δ に「（この式で定義）」が付かない: SymbolRegistry の `defining_equation_ids` に
  当該式が記録されていないデータ側の状態（配線はレンズ→射影→カードまで実装済み）

### 10.5 既知の残作業・申し送り

- **Phase 3（二層説明の結線）未着手**: `labels.equation_label(explanation=)` の口は開いている。
  equation の contextual 説明選抜是正 + approved のみ結線で日本語一行の headline になる
- **docker 実機 E2E 未実施**（S1〜S4 の目視。トピック再生成後に掲載節・成立条件が hover に出ること）
- node harness テスト4件はこの環境では skip（node 不在）。CI で実走確認を推奨
- 旧スナップショットのコースは S1/S3 外殻が旧形のまま（劣化許容・再生成で改善）。S2/S3展開/S4 は
  live 読みのためデプロイ即時反映
- element-vocab.js に link_status の**事実文**ミラーは未追加（S3 外殻カードで「前段が無い理由」を
  出す場合に必要。現状は展開部の intrinsic.facts / review_notes がサーバ合成で供給するため非必須）
- W層 dialogue.py の grounding は新キーを自動で受けて改善（追加コールなし）。明示的な区画化注入は将来課題
