---
id: IK-1062
title: "同一性リンクを見送る・確定を取り消すと、その組を候補に戻す手も再び提案される経路も無い — 一意制約で同じ組の行は作り直せず、候補づくりは既存行で止まる。概念の見送り・ラベルの見送りには［候補に戻す］［見送りを取り消す］があるのに、リンクの否定の判断だけは誤りでも戻せない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が誤って見送った・取り消した部品と共通部品の結び付きを、後から候補に戻して判断し直す"
  layers: [concept_registry, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸 review: routes/deliberation.py の identity-links は confirm / reject / revoke だけで、rejected・revoked を candidate に戻す手が無い。
    core/deliberation/identity_links.py の作成は (instance_element_type, instance_element_id, instance_document_id, shared_part_id) の
    ON CONFLICT DO NOTHING で既存行を返すので、候補づくり（identity_candidates・記号の候補）も同じ組を立て直さない。否定の判断が
    最終状態になり、判断の誤りをやり直す手続が無い（概念・ラベルの見送りには戻す手がある — 非対称）。構造軸 none: status 遷移の
    受け皿と記録の列はある。
generalization:
  level: repo_pattern
  general_form: "否定の判断を状態で残す設計で、肯定の判断と他の否定の判断には戻す手があるのに、一つの判断だけ戻す手が無く、一意制約が作り直しも塞ぐ"
pattern: negative-decision-second-class
discovery:
  perspective: [data_inspection, boundary_walk]
  note: >-
    第 7 周の審判は「取り消した対の PDF 版リンクを再確定する」前提を提案し、第 8 周の前提も「再確定」と書いたが、DB では
    リンク 6d3fb985（PDF 版 TOV residual constraint）が revoked のまま残り、TeX 版の記号リンク 9a7d14e9（L_TOV）も前提の取り消しで
    revoked になって記号の候補が二度と立たず、te-03 の記号の確定の手が無くなった（mailbox SYMBOL_SKIP_R8）。
    API の手と作成の一意制約を突き合わせた。
resolution:
  perspective: [state_transition, guardrail_fix]
  note: >-
    同一性リンクに rejected / revoked → candidate の遷移［候補に戻す］を足した（POST /api/admin/deliberation/identity-links/{id}/restore・理由必須 422・否定の状態以外は 409・権限は確定と同じ論文の編集）。遷移の検証 → 書き込み → 監査は core.candidate_flow（否定の状態ごとに CandidateVocabulary を 1 つ）を通し、監査は identity_link.restored（前の判断の人を metadata に残す）。前の判断の列は上書きせず、093 の追補で restored_by / restored_at / restore_reason / restored_from を足した（新番号を切らず、未コミットの 093 に冪等な ADD COLUMN を追記）。行は restore_line「〔候補に戻した〕 戻した: 名前・日付（前の判断: 見送り）」と restore_reason_line を出す。戻すのは教員の操作だけ（KR2・パイプラインは戻さない）。UI はライブラリの同一性の候補・結びついた教材の部品 / 記号と W層の同一性リンク区画、アンカー deliberation.identity-link-restore + マニュアル節 #identity-link-restore。
  landed_in:
    - "backend/db/093_identity_link_revoke.sql"
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "backend/api/routes/library.py"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/deliberation.js"
    - "docs/manual/teacher/24-admin-deliberation.md"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_r8_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "人の否定の判断を機械が復活させない規律を守ったまま、人の操作だけで戻す経路を足した。固有の差分: 一意制約で作り直せないので戻す手が唯一の回復経路。"
  verification:
    methods: [guardrail]
    unverified:
      - "取り消し → 候補に戻す → 確定の通しを砂場で（第 9 周 te-05 × (a)）"
      - "migration 093 の追補が実 DB で冪等に再実行されることは scratch DB で確かめていない"
related: [IK-1050, IK-0953, IK-1024]
view_of: []
history: []
---

## 課題

症状: 確定を取り消したリンク・見送ったリンクは「記録は残ります」と言われるが、判断が誤りだったと分かっても候補に戻せず、
候補づくりを走らせ直しても同じ組は立たない。前提の運転でも、取り消した組を「再確定」できなかった。

原因: リンクの否定の判断に戻す手が無く、行の一意制約が作り直しも塞いでいる。

## 発見の観点

データの確認: 第 8 周の前に再確定したはずのリンクの状態と、取り消し後の記号の候補の有無を DB で見た。境界: 判断の取り消し × 候補の再生成。

## 解決の観点

否定の判断を候補に戻す遷移を、ラベル・概念と同じ形（理由必須・前の記録を残す）で足した。 元の見立て: 否定の判断を戻す手を、ラベル・概念と同じ形（理由必須・記録を残す）で足す。

## 一般化

否定の判断を状態で残すなら、戻す手続も揃える。一意制約で行を作り直せない設計では、戻す手が唯一の回復経路になる。型 negative-decision-second-class。確かめ方は、取り消し → 候補に戻す → 確定 の通しを同じ組で行うこと。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

GONE（戻す手）。te-05 が見送ったリンクを理由付きで［候補に戻す］→ 行に「〔候補に戻した〕 戻した: 西川 智也・2026-10-31（前の判断: 見送り）」と戻した理由、学生の画面は確定まで変わらない旨（POST /identity-links/80a778e4…/restore 200・DB restored_from=rejected）。一覧の見送りの知らせにも戻し方の一文。**確定までの通しは未評価** — 場面に戻した後の［確認］の段が無く（harness H53）、候補も同じ論文の 2 版だけだった。戻した判断の根拠は、置き換わった部品の記録を生きた相手と読み違えたもの（NEW IK-1076）。run: uxsim/runs/c-astro-r25-share-r9/20261031T003210Z-30685（seq4〜8）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

通しは成立（否定側）。te-05 が理由の無い 10/27 の見送りを理由付きで［候補に戻す］→ 戻した同じ行を詳細から理由付きで［見送り］（harness H53 の手）。確定側は確定してよい組が砂場に無く未評価。残り（小）: 戻した行は「前の判断: 見送り」だけで、前の判断の誰・いつ・理由（記録なし）は出ない — 一覧の事実文は「前の判断の記録（誰が・いつ・なぜ）は残っています」と言う。run: uxsim/runs/c-astro-r25-share-r10/20261031T053605Z-69501（seq5・6）。

## 第 10 波の是正（第 25 周・2026-10-31）

第 10 周の残り（戻した行に前の判断の誰・いつ・なぜが出ない）を是正。`identity_links.prior_decision_line` が戻した行に「前の判断の記録: 見送り: 名前・日付（見送りの理由: …／記録なし）」（取り消しからなら取り消した人・日付・元の確定と取り消しの理由）を返し、ライブラリの同一性の候補・詳細の結びついた部品 / 記号・W層の同一性リンク区画（`data-identity-prior-decision`）に出す。前の判断の列は戻しても上書きしていないので、記録をそのまま並べる。landed_in: `backend/core/deliberation/identity_links.py` / `backend/api/routes/library.py` / `frontend/public/js/admin.js` / `frontend/public/js/deliberation.js` / `backend/tests/test_share_round25_r10_fixes.py`。確かめ方は guardrail のみ（砂場の画面は第 11 周）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

残り（前の判断の記録）GONE。取り消し済みの記号を理由付きで［候補に戻す］と、行に「前の判断の記録: 取り消し: 岡村 千尋・2026-10-27（元の確定: …）（取り消しの理由: …）」が出た（uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq9）。戻した行を確定する側の通しも今周初めて成立（seq10 — ただし対象は PDF 版の行・harness H66）。
