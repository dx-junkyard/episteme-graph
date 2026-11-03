---
id: IK-0980
title: "同じ論文の TeX 版と PDF 版のあいだで、同一性の候補（意味の近さ）が名前も役割も違う部品を組にする（Training loss system ⇄ TOV network map 等）— 近傍チャンクを使う最初の未結合の部品を相手に選ぶため、2 版で本文がほぼ同じだと「同じ文の近くにある別の部品」が対になる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が同じ論文の別の版の部品の対を、同一性の候補から確かめて確定する"
  layers: [concept_registry, pipeline_a, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: 規則 ③（core/library/identity_candidates.py _chunk_proxy_twin）は起点チャンクの近傍チャンクを引き、そのチャンクを使う他 document の部品のうち「まだ結ばれていない最初の 1 件」（_first_unlinked）を相手にする。TeX 版と PDF 版は本文がほぼ同じなので近傍は対応する同じ文のチャンクになり、相手は「その文を出典に持つ部品のどれか」に落ちる — 指している対象（同じ部品）ではなく、同じ文の近くにある部品が選ばれる。処理軸 logic: 先に結ばれた部品を除外して次を取るため、1 つ目の対が正しくても後続がずれて連鎖する。構造軸・統制軸は無関係（表現・手続は正しい）。
generalization:
  level: repo_pattern
  general_form: "ほぼ同じ文書の 2 版を突き合わせるとき、文の近さを部品の同一性の代わりに使い、同じ文の近くにある別の部品を対にする"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    te-02 の候補一覧（uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq2）で、意味の近さの 5 組（Training loss system ⇄ TOV network map、Seed reproducibility protocol ⇄ EOS network map、Pulsar tension check ⇄ Maximum mass result、Softening reproducibility diagnostic ⇄ Radius estimate、Boundary constraints ⇄ Softening interpretation）が名前も役割も違うことと、_chunk_proxy_twin の相手の選び方を突き合わせた。
resolution:
  perspective: [responsibility_move, guardrail_fix]
  note: >-
    オーナー判断（推奨どおり）で、同じ論文の別の版どうしでは文の近さ（chunk-proxy）を候補の材料にしないことにした。identity_candidates._run_component_and_claim_rules が resolve_same_paper_documents の版を others_by_chunk に入れず、別の版の部品は規則 ②b（式・主張の本文の一致）だけで対にする。既に立った誤候補は消さない（KR7）。未決の chunk-proxy 候補（candidate ∧ vector_similarity）しか持たない部品は段の再実行で ②b にかけ直し、新しい対は candidate_key に接尾辞 same-paper を付けた別エントリに置く（旧エントリに相乗りさせない・人の判断がある部品は触らない）。一覧の候補カードに部品の要約、旧規則で別の版どうしを文の近さで結んだエントリには STALE_SAME_PAPER_PROXY_NOTE。規則の版は r7-same-paper-symbols。LLM 0 回。
  landed_in:
    - "backend/core/library/identity_candidates.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "docs/features/concept_registry_design.md"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_concept_registry_identity_candidates.py"
    - "backend/tests/test_concept_registry_candidates_api.py"
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "backend/tests/test_share_round25_r8_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "作り直しで人の判断（確定・却下）を上書きしない規律を、未決の機械候補だけの部品を作り直す範囲の線引きに使った。固有の差分: 旧候補は消さずに残し、新しい対は別エントリに置く。"
  verification:
    methods: [guardrail]
    unverified:
      - "実データ（PINN の TeX 版 ⇄ PDF 版）で段を再実行したときの候補の組"
      - "旧規則のエントリの見送りの実画面"
related: [IK-0919, IK-0917, IK-0981]
view_of: []
history:
  - date: '2026-10-31'
    field: resolution
    from: 第 7 波の是正
    to: 第 8 波の追補
    reason: 第 8 周で形を変えて STILL（詳細の候補の行が同じ論文の別版を「別の論文の記述と…」と言う）を是正。読み時に identity_links.reworded_same_paper_reason で言い換え（保存値は recorded_reason）。ライブラリの候補カード（旧規則エントリ）と、詳細の結びついた教材の部品（shared-part の identity-links・same_paper 判定）の両方
---

## 課題

症状: 段の再実行で PINN の TeX 版 ⇄ PDF 版の候補が 10 件出たが、「意味の近さ」の 5 組は名前も役割も違う部品の対で、教員は名前 2 つしか判断材料が無い。行動型の te-02 は「名前が同じものは同じだろう」と流し読みで上から確定した。原因: 規則 ③ は「近傍チャンク → そのチャンクを使う最初の未結合の部品」で相手を決める。2 版の本文がほぼ同じだと近傍は同じ文になり、同一性ではなく「同じ文の近く」で対が決まる。

## 発見の観点

再現（te-02 (a) seq2・seq6）とデータの点検（候補の組とチャンク近傍の選び方）。

## 解決の観点

是正案: ① 同じ論文の別の版（`resolve_same_paper_documents` が真）どうしでは規則 ③（chunk-proxy）を使わず、規則 ②b（式・主張の本文の一致）だけで対を作る — 2 版の文は一致して当然なので、文の近さは同一性の材料にならない。② 規則 ③ の相手は「近傍チャンクを使う部品」のうち、起点の部品と出典チャンクの集合の重なりが最大のものにし、未結合の先頭を取らない。③ 候補カードに両方の部品の要約を並べる（教員の判断材料）。どれも LLM 0 回。

## 一般化

代理の手がかり（文の近さ）で対象（部品の同一性）を決める型。確かめ方は、同じ論文の 2 版で段を走らせ、候補の対が同じ役割の部品どうしだけになること（名前が違っても式・主張が一致する対は残る）を見ること。

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

是正済み。オーナー判断（推奨どおり）で、同じ論文の別の版どうしでは文の近さ（chunk-proxy）を候補の材料にしないことにした。identity_candidates._run_component_and_claim_rules が resolve_same_paper_documents の版を others_by_chunk に入れず、別の版の部品は規則 ②b（式・主張の本文の一致）だけで対にする。既に立った誤候補は消さない（KR7）。未決の chunk-proxy 候補（candidate ∧ vector_similarity）しか持たない部品は段の再実行で ②b にかけ直し、新しい対は candidate_key に接尾辞 same-paper を付けた別エントリに置く（旧エントリに相乗りさせない・人の判断がある部品は触らない）。一覧の候補カードに部品の要約、旧規則で別の版どうしを文の近さで結んだエントリには STALE_SAME_PAPER_PROXY_NOTE。規則の版は r7-same-paper-symbols。LLM 0 回。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 生成は GONE（再解析後の段の再実行で 2 版間に立った新しい候補は本文一致の 1 件だけ — 10 番 TOV residual loss at collocation points、TeX/PDF の 2 本・「一致したもの: 主張「…」」付き）。旧規則の候補は STILL: 1〜9・11 番が「（意味の近さ）」「別の論文の記述と本文の意味が近いと測定されました」のまま残り、確定済みエントリ 1 番 Boundary constraints のリンク先は名前も中身も違う Softening interpretation。第 5 波の旧規則の注記（stale_rule_note）は TeX 側のリンクが再解析で隠れたため判定が働かず出ない → IK-1020。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

形を変えて STILL。エントリ詳細（Boundary constraints）の TeX 版の候補の行は「再解析前の部品に付いています」の直後に「別の論文の記述と本文の意味が近いと測定されました。」— 同じ論文の別の版。一覧の stale_rule_note（「同じ論文の別の版どうし」）と同じ画面で食い違う（uxsim/runs/c-astro-r25-share-r8/20261027T223243Z-92712 seq4）。相手が live の候補の行は reason を差し替えない（_attach_pair_confirm_questions の live 分岐）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

GONE。候補一覧のカードに「この候補は、同じ論文の別の版どうしを文の近さで結んだものです。…」、詳細の旧規則の行は「同じ論文の別の版の記述と、本文の意味が近いと測定されました（別の版どうしを文の近さで結んだ、旧い候補づくりの規則で立った候補です）。」— 「別の論文」と言う行は無かった。run: uxsim/runs/c-astro-r25-share-r9/20261031T003210Z-30685（seq2・7）。
