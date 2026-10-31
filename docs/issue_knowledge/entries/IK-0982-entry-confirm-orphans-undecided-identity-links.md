---
id: IK-0982
title: "同一性の候補で「概念（エントリ）の確定」と「結び付き（リンク）の確認」が別の判断なのに、エントリを確定すると候補一覧から消え、詳細にはリンクを決める手が無いため、未決のリンクの置き場が画面から無くなる（確定の確認・成功の知らせも無い）"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が同一性の候補を確定し、部品どうしの結び付きを確かめて学生に届ける"
  layers: [concept_registry, deliberation_w, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸 completion: 候補一覧（routes/library.py list_identity_candidates）は review_status が candidate のエントリだけを集めるので、エントリを確定するとその候補はキューから消える — エントリの確定が「この候補の判断が済んだ」の代理になり、エントリ配下の candidate のリンク（1 リンク = 1 判断・KR2）は済んでいない。構造軸 decomposition: エントリの判断とリンクの判断を別単位に分けたのに、リンクの［確認］［見送り］は一覧のカードにしか置かれず、エントリ詳細の「結びついた教材の部品」は確定の行の［確定を取り消す］しか持たない（admin.js loadLibraryEntryBoundParts）。
generalization:
  level: repo_pattern
  general_form: "親と子に別々の判断を置いたのに、親の判断で子の判断の置き場を閉じ、未決の子が画面から消える"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-02 がエントリ「Boundary constraints」を［確定にする］で確定した後、詳細のリンク 2 本は〔候補〕のまま、一覧からは候補カードごと消えた（uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq5・seq6）。一覧のフィルタと詳細の行のボタンを突き合わせた。
resolution:
  perspective: [first_class_state, carry_through]
  note: >-
    済み = エントリとリンクの両方が決まったこと、にした。候補一覧（list_identity_candidates）は candidate のエントリ ∪ candidate のリンクを持つ確定済みエントリを集め、確定済みのカードは未決のリンクだけを並べ links_pending_note を添えてエントリの操作を出さない。エントリの確定の前に ENTRY_CONFIRM_QUESTION（確定するのは概念を置くことだけで結び付きは別）、確定後に review の応答 facts（ENTRY_CONFIRMED_FACT）。詳細の「結びついた教材の部品」の候補の行に［確認］［見送り］（既存 deliberation の confirm / reject・確定の前に confirm_question / granularity_note の確認・アンカー knowledge-library.bound-part-decide）。
  landed_in:
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_r5_fixes.py"
    - "backend/tests/test_concept_registry_candidates_api.py"
    - "backend/tests/test_share_round25_identity_consumers.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面でエントリを確定した後に未決のリンクを詳細と一覧の両方で決める通し"
related: [IK-0917, IK-0953, IK-0955, IK-0980]
view_of: []
history: []
---

## 課題

症状: 名前の違う対（Boundary constraints ⇄ Softening interpretation）のエントリを、確認ダイアログも成功の知らせも無く 1 クリックで確定でき、確定後はリンクを決める手がどこにも無い。学生に届くのは確定したリンクだけなので、教員は「確定したのに届かない」理由を画面から辿れない。原因: キューがエントリの状態だけで候補の済み・未済を決め、リンクの判断の入口がキューにしか無い。

## 発見の観点

再現（te-02 (a) seq4〜6）と経路の追跡。

## 解決の観点

是正案: ① キューの対象を「candidate のエントリ」∪「candidate のリンクを持つ確定済みエントリ」にする（済み = エントリとリンクの両方が決まった）。② エントリ詳細の「結びついた教材の部品」の候補の行に［確認］［見送り］（既存 API・リンクの確定の弁 = IK-0917 の問いと種別の文を同じく出す）。③ エントリの確定は「この概念をレジストリに置くこと」で、結び付きの確定ではないことを確定の前に 1 文で言い、確定後に「確定しました。結び付きは別に確かめます」の知らせ。

## 一般化

親の完了で子の判断を閉じる型。確かめ方は、エントリを確定した後も未決のリンクが一覧か詳細のどちらかで決められることを見ること。

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

是正済み。済み = エントリとリンクの両方が決まったこと、にした。候補一覧（list_identity_candidates）は candidate のエントリ ∪ candidate のリンクを持つ確定済みエントリを集め、確定済みのカードは未決のリンクだけを並べ links_pending_note を添えてエントリの操作を出さない。エントリの確定の前に ENTRY_CONFIRM_QUESTION（確定するのは概念を置くことだけで結び付きは別）、確定後に review の応答 facts（ENTRY_CONFIRMED_FACT）。詳細の「結びついた教材の部品」の候補の行に［確認］［見送り］（既存 deliberation の confirm / reject・確定の前に confirm_question / granularity_note の確認・アンカー knowledge-library.bound-part-decide）。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: GONE。確定済みエントリ Boundary constraints が「この概念は確定済みです。部品との結び付きのうち、まだ判断していないものがあります。」付きで一覧に残り、未決のリンクに［確認］［見送り］が出た。詳細の「結びついた教材の部品」の候補の行にも［確認］［見送り］と要約・一致の事実文が出た。エントリの確定（成功の知らせ）は今周押されず未評価。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2・4

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

形を変えて STILL（見送りの側）。te-05 が概念「Pulsar tension check」を理由付きで見送った後も、詳細「結びついた教材の部品」の TeX 版の行は〔候補〕［確認］［見送り］のまま押せる（te-05: 「概念を見送った後にこの行を確認したらどうなるのかが分からない」）。概念の判断の後に残るリンクの判断の置き場・意味が、確定の側（第 4 波の是正）と同じく見送りの側でも要る。run: uxsim/runs/c-astro-r25-share-r9/20261031T003210Z-30685（seq7）。
