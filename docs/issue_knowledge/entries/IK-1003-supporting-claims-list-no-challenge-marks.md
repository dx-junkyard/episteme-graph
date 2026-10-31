---
id: IK-1003
title: "ノードの台帳の「支える主張」一覧に、どの主張に疑義・検証提案があるかの印が無く、ノード側は「疑義はありません」とだけ言うため、主張に残した疑義と提案へノードから辿れない（記帳した主張を覚えていないと見つからない）"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "教員がノードの台帳から、支える主張に残した疑義と検証提案の進み具合を読み返す"
  layers: [doubt_d, frontend_admin_ui, lecture_studio]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 台帳 GET の supporting_claims は {claim_id, label} だけで、各主張の疑義・提案の有無を持たない。ノード自身の challenges[] は空（疑義は主張にしか残せない — IK-0904 の設計）なので、ノードの画面は「疑義はありません」と言う。構造軸 aggregation: 主張の疑義・提案をノードの単位へ畳む集約が無い。
generalization:
  level: repo_pattern
  general_form: "下位の項目に付いた記録を上位の画面に集約しないため、上位の画面は『何も無い』と言い、下位を一つずつ開かないと記録に届かない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: >-
    te-04 が主張に疑義 → 提案 → 着手を記帳した後でノード台帳を開き直すと、支える主張 6 行に印が無く、ノードの疑義区画は「疑義はありません」だった。run: uxsim/runs/c-r25-doubt-r5/20261027T070946Z-33422（seq13）。
resolution:
  perspective: [carry_through]
  note: >-
    supporting_claims[] の各行に challenge_state（challenged / proposed / in_progress — 取り下げは数えない・着手 > 提案 > 疑義）と challenge_state_label（label_vocab）を足し、ノードの疑義区画に事実文 supporting_challenge_note を 1 行（件数なし）。判定は純関数 supporting_claim_challenge_state。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/doubt-atlas.js"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
    - "backend/tests/test_doubt_round25_wave5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "疑義を残した後にノードの台帳から印が読めることを実機で確かめていない"
related: [IK-0904, IK-0968, IK-0930]
view_of: []
history: []
---

## 課題

症状: ノードの台帳から、どの主張に疑義・提案があるかが分からない。ノードは「疑義はありません」と言う。

原因: 支える主張の一覧が疑義・提案の有無を持たず、ノードへの集約も無い。

## 発見の観点

再現（te-04 の記帳後の開き直し）。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。supporting_claims[] の各行に challenge_state（challenged / proposed / in_progress — 取り下げは数えない・着手 > 提案 > 疑義）と challenge_state_label（label_vocab）を足し、ノードの疑義区画に事実文 supporting_challenge_note を 1 行（件数なし）。判定は純関数 supporting_claim_challenge_state。

起票時の是正案: 是正案: supporting_claims の各行に `challenge_state`（疑義あり / 検証提案あり / 着手済み などの段階語）を足し、画面は行末に印を出す。ノードの疑義区画は「この対象を支える主張に疑義が N 件あります」のような事実文（件数を出さないなら「疑義のある主張があります」）。

## 一般化

記録を付けられる単位と、利用者が入口にする単位が違うときは、入口の画面に下位の記録の有無を集約する。確かめ方は、下位に記録を付けた後に上位の画面からその記録へ 1 操作で届くことを見ること。

第 6 周の再演: GONE（2026-10-27）。Cepheus のノード台帳に「この対象そのものへの疑義はありませんが、支える主張に疑義・検証提案が残っています（主張の台帳で読めます）。」と主張の行の〔検証に着手済み〕が出て、te-01 は迷わず主張の台帳へ辿った。残り（軽）: 印は最も進んだ状態だけで、同じ主張に未対応の疑義が 2 件増えても〔検証に着手済み〕のまま（設計どおり — 集約の 1 行で足りるかは運用で見る）・同文の支える主張「An equation of this paper defines $M$.」が 2 行（IK-0901）。run: uxsim/runs/c-r25-doubt-r6/20261027T122508Z-5219（seq5・13）。
