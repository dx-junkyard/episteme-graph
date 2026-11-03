---
id: IK-1076
title: "ナレッジライブラリのエントリ詳細「結びついた教材の部品」が、再解析で置き換わった部品に付いたままの同一性リンク（候補一覧では隠す行）を〔候補〕［確認］［見送り］の判断できる行として出し、確認の問いは「部品「（名前を読めない要素）」と…」になる — 一覧は「組の片方だけが残っている」と言うのに詳細は相手が生きているように見え、教員は見送った判断をその行を根拠に候補へ戻した"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
  - "docs/features/knowledge_objects_design.md"
feature_context:
  realizing: "教員が同一性の候補を、一覧とエントリ詳細のどちらで見ても同じ組として読み、判断できる行だけを判断する"
  layers: [concept_registry, knowledge_objects, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 version: routes/library.py の候補一覧は _link_resolves_to_live_component で superseded の部品に付いたリンクを落とす（IK-1020）が、
    詳細が読む routes/deliberation.py list_identity_links_for_shared_part は document の閲覧可否だけで絞り、置き換わった部品のリンクも返す。
    応答はその行に instance_stranded_note（IK-1012）を付けるが status は candidate のままなので、admin.js _libraryBoundLinksHtml は
    isCandidate の行に［確認］［見送り］を描き、_attach_pair_confirm_questions は instance_label が空の相手を「（名前を読めない要素）」と書く
    （identity_links.py:1066-1090）。統制軸 assignment: 「記録を残す（KO3）」と「判断の対象に出す」を同じ status で表し、
    判断できるかを画面ごとに別の規則で決めている。DB: shared_part f8952baf の 2 本のうち 3d5979a1 は superseded の部品 4f7ef481 に付いた candidate。
generalization:
  level: repo_pattern
  general_form: "再生成で消えた対象に付いた記録を残すとき、一方の画面は隠し他方の画面は判断できる行として出し、同じ組が画面ごとに違って見える"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 9 周 te-05 (a) — 候補一覧（seq2）は「組の片方だけが残っています」、エントリ詳細（seq4）は TeX 版の行を〔候補〕［確認］［見送り］で出し、
    te-05 は「詳細を開いたら相手の TeX 版の部品が見え、根拠の主張の本文が一致していると書かれていた」を理由に見送りを候補に戻した（seq5・POST restore 200）。
    戻した行は「相手が残っていないため確かめられません」と言い続け、直上の行と食い違って読めた。生応答の confirm_question と DB の superseded を突き合わせた
    （uxsim/runs/c-astro-r25-share-r9/20261031T003210Z-30685）。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    判断可否の述語を 1 本にした: core/deliberation/identity_links.instance_is_live（一覧の _link_resolves_to_live_component は委譲）と
    link_undecidable_note（置き換わった部品の記録 = instance_stranded_note / 見送った概念の行）。詳細の _identity_link_response は
    判断できない行に decidable:false + undecidable_note を付け confirm_question / confirm_note を落とす（判断できる行の DTO の形は不変）。
    _attach_pair_confirm_questions は判断できない行に問いを作らない（「（名前を読めない要素）」を名指ししない）。理論モジュールも
    置き換わりを事実文で言う。確定・候補に戻すは判断できない行で 409（日本語の事実文）。admin.js（一覧・詳細・候補に戻す）と
    deliberation.js は decidable !== false のときだけボタンを出し、文を出す。行は消さない（KO3・P4）。概念を見送った後のリンク
    （IK-0982 の見送り側）も同じ述語で判断の対象から外す。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/deliberation.js"
    - "backend/tests/test_share_round25_r9_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "一覧と詳細（と W層モーダル）が判断の可否を同じ述語で読む。固有の差分: 記録は残し、判断の対象から外すだけ。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で te-05 × (a) を再演し、置き換わった部品の行が詳細で判断の対象に見えないこと（第 10 周）"
related: [IK-1020, IK-1012, IK-1062, IK-0982]
view_of: []
history: []
---

## 課題

症状: エントリ詳細の「結びついた教材の部品」で、再解析で置き換わった TeX 版の部品に付いた記録が〔候補〕［確認］［見送り］で並ぶ。候補一覧はこの行を隠して
「組の片方だけが残っています」と言うので、同じ組が一覧と詳細で違って見える。確認を押すと「部品「（名前を読めない要素）」と共通部品「…」は、同じものですか」と問われる。

原因: 一覧と詳細が別の経路で、判断できる行の条件（live な部品に付いているか）を一覧だけが見ている。記録を残す印（instance_stranded_note）は付くが status は candidate のまま。

## 発見の観点

再現: te-05 が一覧 → 詳細 → 候補に戻すの順に歩いた。経路の追跡: 一覧（library.py）と詳細（deliberation.py）のリンクの絞り込みを突き合わせ、DB で部品の superseded を見た。

## 解決の観点

未着手。見立ては判断可否の述語を一覧と詳細で 1 つにし、置き換わった部品の記録は事実文だけで出すこと。

## 一般化

記録を残すことと、判断の対象として出すことは別の判定で、画面ごとに決めると同じ組が別のものに見える。型 rule-enforced-per-surface。確かめ方は、片側が置き換わった組を一覧と詳細の両方で開き、判断のボタンと文が一致するかを見ること。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

GONE（第 9 周と同じ te-05・同じエントリ「Physical consistency result」で比較可）。詳細の TeX 版の行は「この行は判断の対象にしていません（付いている部品が解析のやり直しで置き換わったため、確認・見送り・候補に戻すはできません）。記録は消さずに残しています。」でボタンなし・「（名前を読めない要素）」の問いも無い。PDF 版の行だけ［確認］［見送り］。run: uxsim/runs/c-astro-r25-share-r10/20261031T053605Z-69501（seq4・6）。
