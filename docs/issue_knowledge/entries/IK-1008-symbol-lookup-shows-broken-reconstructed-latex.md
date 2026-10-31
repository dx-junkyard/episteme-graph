---
id: IK-1008
title: "記号の吹き出し（symbols/lookup）が、置換文字（U+FFFD）の入った崩れた式をそのまま「AI復元」の印付きで出す — 教材・講義は annotate_reconstructed_formulas が壊れた復元を latex_withheld にして式として描かないが、吹き出しは印だけを写して伏せる規則を写さない。結果の値の列挙（S8 = 0.823 ± …）も「番号の無い式」として並ぶ"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "学生が講義・教材の記号を押して、その記号が現れる式と出所を読む"
  layers: [concept_registry, lecture_player, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: 「復元の印を付け、壊れた復元は式として描かない」規則が core/lecture.py::annotate_reconstructed_formulas に
    まとまっているが、吹き出し（backend/core/symbol_lookup.py の _reconstructed_mark_fields）は印の判定（equation_latex_is_reconstructed）
    だけを通し、is_broken_reconstructed_latex による伏せを通らない。接続軸 contract: 写し先が印と伏せの片方だけを受け取る。
    式でない値の列挙が式に入るのは A層の式候補の誤り（IK-0872 の J=3 と同型）で、吹き出しはそれを選別しない。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "表示の可否を決める規則が 1 か所にあり、新しい表示面が印だけを写して可否の判定を写さない"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    学生 st-02 が t16 で α_B の吹き出しを開くと「$s = 1 Dkin � (2 -\alpha B) � …$ AI復元」が出て、「文字化けしていて読めない」とチャットで尋ねた。
    読み取りの API で同じ吹き出しを引くと equations[] の 3 件すべてが reconstructed: true・latex に U+FFFD を含み、latex_withheld は無かった。
    run: uxsim/runs/c-astro-r25-studio-r5/20261027T072352Z-12975 seq13・15。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "印と表示の可否の組を learner_context_common.equation_display_fields の 1 関数にし（壊れた復元は latex を空にして latex_withheld）、壊れ方の判定 is_broken_reconstructed_latex も同モジュールへ移して箇条書きの点（U+2022）を加えた。記号の吹き出し（式一覧・定義式）・annotate_reconstructed_formulas（講義・読む表示）・根拠の式がこれを通り、吹き出しは伏せた式も一覧から落とさず印と事実文だけを出す。app.js は latex_withheld の式を KaTeX で描かない。"
  landed_in:
    - "backend/core/learner_context_common.py"
    - "backend/core/symbol_lookup.py"
    - "backend/core/lecture.py"
    - "backend/core/course_content_builder.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_lecture_studio_round25_r5.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "印と可否を同じ 1 関数から式の単位で受け取る形にし、吹き出し・講義・根拠の式の全経路をそれに通した。固有の差分: 吹き出しは伏せた式を一覧から落とさない（事実文だけ残す）。"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースの t16 の α_B の吹き出しで 3 式が伏せられ事実文だけになるかは実機で見ていない"
      - "値の列挙を式の候補から外す A層の選別（IK-0872 ① と同じ根）は是正していない — 箇条書きの点を含む形だけを伏せる"
related: [IK-0977, IK-0895, IK-0872, IK-0518]
view_of: []
history: []
---

## 課題

症状: 吹き出しが置換文字の入った崩れた式を「AI復元」の印付きで式として描く。数値の列挙も「番号の無い式」に並ぶ。

原因: 吹き出しの式の組み立てが、復元の印の判定だけを正本から借り、壊れた復元を伏せる規則（latex_withheld）を借りない。

## 発見の観点

学生の通し受講（reproduction）の吹き出しの観察を、読み取りの API の生値で確かめた（data_inspection）。

## 解決の観点

解決済み（2026-10-27・第 5 波）。印と表示の可否を 1 関数（equation_display_fields）から式の単位で受け取り、吹き出しも壊れた復元を伏せる。

（起票時の案）案: 吹き出しの式の組み立ても annotate_reconstructed_formulas を通す（印と伏せを 1 関数から受け取る）。伏せた式は事実文
（reconstructed_note）だけを残す。値の列挙を式から外すのは A層の式候補の選別の課題（IK-0872 ① と同じ根）で、吹き出し側では
「式として描けない」ものを伏せる規則で当面足りる。

## 一般化

印と表示の可否は同じ 1 関数から受け取る。確かめ方: U+FFFD を含む復元式を持つ記号を吹き出し・教材・講義で開き、どの面でも式として描かれないことを見る。

第 6 周の再演（2026-10-27）: **GONE**。読み取りの API で t16 の α_B の吹き出しを引くと、置換文字の入った 3 つの式は `latex: ""`・`latex_withheld: true`・印「AI復元」で返り、崩れた式は描かれない。講義の式 253 件に伏せる対象は無かった。ペルソナ st-05 の引いた P には式が無く、場面の中では観察していない。

第 7 周（2026-10-28・面 C の頭脳の NEW の裏取り）: **正常な式を伏せる誤判定は観察されない**。doubt 頭脳 st-07 の「1bad7d9f の t2 で『その関係は次の式で表されます。』の後に式本体が無い」は、transcript の教材 API が `[[FORMULA_0]]` に B_DCF の LaTeX を返していた（`latex_withheld` の真は 0 件）ので、頭脳の読み取りで `$…$` が落ちたハーネス側（`harness_fixes_studio.md` 第 7 周の参考行）。面 D の st-01 / st-07 の run で `latex_withheld: true` は t0 付録の「J=3」（raw_text だけの壊れた inline 候補 = IK-0872 ①）1 件だけで、t1 / t11 の正常な式はすべて `false` で描かれた。run: uxsim/runs/c-r25-doubt-r7/20261027T173022Z-59619 seq6、uxsim/runs/c-astro-r25-studio-r7/20261027T172130Z-55411 seq6、uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213。
