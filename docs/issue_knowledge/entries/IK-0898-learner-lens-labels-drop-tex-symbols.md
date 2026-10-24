---
id: IK-0898
title: "学生の部品の文脈の上位・下位の行で、数式を含む主張は見出しが「主張」だけの中身なしの項目になり、TeX の記号を含む主張は記号が抜けた切れ端（「one representing the EOS P()…」「The structure network _TOV maps…」）になる — 見出しの組み立てが TeX を含む文を使えない候補として捨てるか、記号を外して残し、数式として描く道を持たない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が部品の文脈の上位・下位のレーンで、部品とつながる主張を読む"
  layers: [deliberation_w, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 中身なしの「主張」は確認済み — backend/core/deliberation/labels.py:830 claim_label は _first_usable（:247）で text / normalized_text を選び、_usable（:208）は looks_like_tex_math の文を捨てる。「An equation of this paper defines $\mathcal{N}_\mathrm{TOV}$.」のような式由来の合成主張は全文が TeX を含むので候補が残らず、_GENERIC_CLAIM_LABEL「主張」になる。記号の欠落（P() / _TOV）は未確定 — 主張の normalized_text か学習者向けの投影（learner_context_common.project_item / safe_text）が $…$ や \mathcal{…} を取り除いた文を見出しにしている可能性が高い。確定には該当 claim の text と normalized_text を読み、どちらが見出しになったか・どこで記号が落ちたかを辿ればよい。処理軸 input_handling: 数式を含む文を壊れた入力として扱う。構造軸 representation: 見出しが「プレーンテキスト」しか表現できず、$…$ の数式区間を持てない。
generalization:
  level: repo_pattern
  general_form: "数式を含む文を表示の候補にできない見出しの組み立てが、文を捨てるか記号を剥がし、中身の無い見出しか意味の欠けた切れ端を出す"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    学生 st-06 が部品の文脈を開くと、下位に「を根拠として持つ 主張」（中身なし）と記号の抜けた主張の切れ端が並んだ。同じ部品の主張一覧（DTO の claims）には記号付きの全文があった。run: uxsim/runs/c-astro-r25-share-r2/20261022T220149Z-75546 seq11・seq16・seq19。
resolution:
  perspective: [representation_change]
  note: >-
    見出しが数式区間を持てるようにした。text_excerpt に is_prose_with_delimited_math / normalize_inline_math_delimiters（\(…\) を $…$ に）を足し、deliberation/labels.py の claim_label は式から合成した英語の定型主張（defines / depends on）を数式区間を保った和文にし、それ以外の TeX を含む散文は excerpt(keep_dollar_math=True) で区間を割らずに見出しにする。学習者の射影 project_item は数式区間付きの散文を一般ラベルへ落とさない。element-card.js は見出し・全文の $…$ 区間だけをゲート付きで数式に描く。
  landed_in:
    - "backend/core/text_excerpt.py"
    - "backend/core/deliberation/labels.py"
    - "backend/core/learner_context_common.py"
    - "frontend/public/js/element-card.js"
    - "backend/tests/test_share_round25_r2_fixes.py"
    - "backend/tests/test_element_card_ui_static.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面での下位レーンの見え方・教員側 W層での同じ見出しの見え方"
related: [IK-0871, IK-0622, IK-0862]
view_of: []
history: []
---

## 課題

症状: 部品の文脈のレーンに、中身の無い「主張」と、記号が抜けた主張の切れ端が並ぶ。原因（仮説）: 見出しの組み立てが TeX を
含む文を捨てるか記号を剥がし、数式区間を持つ見出しを表せない。

## 発見の観点

学生ペルソナの通し受講（reproduction）。レーンの見出しと DTO の主張の全文を並べた（data_inspection）。

## 解決の観点

第 2 波（2026-10-23）で是正。見出しに数式区間を持たせ、捨てない・剥がさない。

## 一般化

表示の器が数式を表せず、数式を含む情報を落とす型。確かめ方は、数式だけの主張・記号を含む文の主張・平文の主張を持つ部品を
開き、どれも中身のある見出しで、記号が数式として描かれることを見ること。

## 第 3 周の再演（第 25 周・面 A 第 3 周、2026-10-23）

残存（$…$ で区切られていない TeX）。部品の文脈の下位レーンの見出しが「Two interacting networks --- one representing the EOS P()…」
「The structure network _TOV maps radial coordinate, central…」と記号の抜けた切れ端で出た。同じ崩れが記号の吹き出しの使用例
（「The Gaussian penalty terms fix P() to 3.06 MeV/fm^3.」）にも出る。主張本文に区切りの無い TeX（\mathcal{N}_\mathrm{TOV} 等）が
残っており、keep_dollar_math は区切りのある数式区間しか守らない。区切りの無い TeX に $…$ を付ける正本
core/latex_display.delimit_bare_tex は教員向け投影（display_projection の audience == "teacher" の PROSE_MATH_KEYS）にしか
掛かっていない。是正案: 学習者向けの文の切り詰め（learner_context_common のレーンの見出し・symbol_lookup._claim_mentions）で
excerpt の前に delimit_bare_tex を通す（切り詰めの前に区切る — 上流で 1 箇所）。砂場の主張本文は 6c7f187 の TeX 取り込みの是正
より前に取り込まれたもので、再解析で直る部分もある（表示側の区切り付けは再解析を待たない是正）。
run: uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784 seq8・seq11、uxsim/runs/c-astro-r25-share-r3/20261023T033353Z-33782 seq7。

第 3 波で是正（2026-10-23）: 区切りの無い TeX を、見出しの切り詰めの前に `core/latex_display.delimit_bare_tex` で
`$…$` に区切る（上流の 1 箇所 = `core/deliberation/labels.py::claim_label` と `_claim_full_text`。学習者・教員とも
同じ見出しになる）。記号の吹き出しの使用例（`core/symbol_lookup.py::_claim_mentions`）も切り詰めの前に区切る。
テスト: `backend/tests/test_share_round25_r3_fixes.py::TestClaimLabelDelimitsBareTex`。実画面のレーンでの見え方は未確認。
