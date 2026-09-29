---
id: IK-0376
title: "リリース前の確認の「この配置で次へ」は、編集権限の無い教材の位置づけを黙って飛ばし「確認済み 0・飛ばし N」と返すため、共有教材でコースを作った教員は何が起きたか分からない"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 共有教材から作ったコースをリリース前の確認で公開する
  layers: [release_review, knowledge_landscape]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: RR7（権限の無いソース論文は 403 にせず静かに除外し件数だけ返す）の設計どおりだが、除外の理由を言う文が
    無く、件数だけが返る（wording）。確認: 第 5 周の教員ペルソナ te-01 が「確認済み 0・2 本とも飛ばされた。自分の教材で
    ないから確認できないということなのか、はっきりしない」と記録。
generalization:
  level: repo_pattern
  general_form: 静かに除外する設計の結果を、件数だけで返して理由を言わない
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: 共有教材でコースを作る教員ペルソナ（所有ゼロ）が踏んだ。
resolution:
  perspective: [explicit_contract]
  note: >-
    飛ばした理由の事実文（編集権限なし／閲覧不可）を応答 skipped_note に足し、admin-release-review.js が描く。件数の形は不変。
  landed_in:
    - backend/api/routes/landscape.py
    - backend/core/label_vocab.py
    - frontend/public/js/admin-release-review.js
    - backend/tests/test_ik0376_release_accept_skipped_note.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - ブラウザでの表示（browser runner 未実施）
related: []
view_of: []
history: []
---

## 課題

飛ばした理由が伝わらない。

## 発見の観点

所有ゼロの教員ペルソナ。

## 解決の観点

未解決。理由の事実文を足す。

## 一般化

静かな除外の結果を件数だけで返す型。
