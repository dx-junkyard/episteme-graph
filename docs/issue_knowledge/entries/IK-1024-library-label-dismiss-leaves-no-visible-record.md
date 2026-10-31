---
id: IK-1024
title: "ナレッジライブラリの別名・隠しラベルを理由付きで見送ると行ごと詳細から消え、見送った記録・書いた理由・戻す手が画面に無い — 一覧の読み込みが include_dismissed を渡さず、見送り済みの行を淡色で描く分岐は届かず、ラベルを候補に戻す API も無い（足し直すと「見送り済みです。見送りは取り消しません」で止まる）"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が概念のエントリの表記ゆれを整理し、見送ったラベルの理由を後から確かめ、必要なら戻す"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: frontend/public/js/admin.js loadLibraryEntryLabels は GET /admin/library/entries/{id}/labels を include_dismissed なしで呼び（routes/library.py:832 の既定 false）、見送り済みの行を opacity 0.6 で描く分岐（admin.js の dismissed 分岐）に値が届かない。dismiss の応答には status: dismissed と review_note が返っている（uxsim/runs/c-astro-r25-share-r6/20261027T122544Z-5649 seq7）が画面に知らせも無い。統制軸 review: 否定の判断（見送り）の理由と帰属が記録されても読み手が無く、戻す遷移（restore）も API に無い（labels の POST は追加・dismiss だけ）。構造軸 none: status 遷移の受け皿はある。処理軸 none。
generalization:
  level: repo_pattern
  general_form: "否定の判断を状態遷移で残しているのに、一覧が既定でそれを読まず、理由も戻す手も画面から消える"
pattern: negative-decision-second-class
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-04 が隠しラベル「TOV eq.」を理由付きで見送ったら行ごと消え、足し直すと「この隠しラベルは見送り済みです。追加し直しても見送りは取り消しません。」で止まった（uxsim/runs/c-astro-r25-share-r6/20261027T122544Z-5649 seq7-8）。admin.js の読み込みと描画の分岐を辿った。
resolution:
  perspective: [state_transition]
  note: >-
    ラベルの見送りは行を消さず、詳細のラベル区画の折りたたみ「見送り済みのラベル」（一覧は include_dismissed=true）に「見送り: 理由（見送った人・日付）」の decision_line と［見送りを取り消す］を出す。取り消しは registry.restore_label（dismissed からのみ・理由必須・行は 1 本のまま・UPDATE だけ）+ POST /entries/{id}/labels/{label_id}/restore（retired は 409・監査 label_restore）。見送りの応答は facts で、見送った行がどこに残るかを言う。新アンカー knowledge-library.labels-dismissed / knowledge-library.label-restore とマニュアル節。
  landed_in:
    - "backend/core/library/registry.py"
    - "backend/core/library/schema.py"
    - "backend/api/routes/library.py"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_r6_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面での見送り → 取り消しの通し（第 7 周 te-02）"
related: [IK-0897, IK-0867]
view_of: []
history: []
---

## 課題

症状: 隠しラベルの［見送り］に理由を書いて押すと、その行は区画から消え、成功の知らせも見送り済みの行も理由も出ない。同じラベルを足し直すと「この隠しラベルは見送り済みです。追加し直しても見送りは取り消しません。」— 見送ったことは分かるが、どこで見られるか・どう戻すかは言わない。

原因: ラベルの一覧の読み込みが見送り済みを含めない。見送り済みを淡色で描く分岐はあるが値が来ない。ラベルを候補に戻す遷移は API に無い。

## 発見の観点

再現（reproduction）: 見送り → 開き直し → 足し直しの順で画面の応答を読んだ。経路の追跡（trace_walk）: dismiss の応答（status・review_note あり）→ 一覧 GET の既定 → 描画の分岐。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: 詳細のラベル区画に「見送り済みも表示」を置き（関係・地図との対応・gap と同じ作法）、見送り済みの行に理由と見送った人を出す。ラベルの［候補に戻す］（restore・理由必須・監査）を足すか、足し直しの文に戻し方を言わせるかを決める。

## 一般化

否定の判断を状態で残しても、一覧の既定がそれを除くと利用者には消去と同じに見える。足し直しの文が「取り消さない」とだけ言うと出口が無い。型 negative-decision-second-class。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: GONE。見送りの応答「「TOV」を見送りました。見送り済みの行は、この区画の「見送り済みのラベル」に理由とともに残り、［見送りを取り消す］で戻せます。」と折りたたみが出た（uxsim/runs/c-astro-r25-share-r7/20261027T172320Z-56507 seq7。見送られたのが「TOV eq.」ではなく「TOV」なのはハーネスの選び方 — H42）。残り（軽）: 見送り済みの別名を足し直したときの文「この別名は見送り済みです。追加し直しても見送りは取り消しません。」が戻す手（［見送りを取り消す］）を指さない（seq8・見送った直後の文は指す）。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28・残り）: 見送り済みの別名・隠しラベルを足し直したときの文に、戻す手（「見送り済みのラベル」の［見送りを取り消す］）を足した。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

GONE（足し直しの文）。見送り済みの隠しラベルの足し直しに「この隠しラベルは見送り済みです。追加し直しても見送りは取り消しません。戻すときは…［見送りを取り消す］から理由を書いて戻します。」。新規・既存・見送り済みの 3 文が別々に出た（uxsim/runs/c-astro-r25-share-r8/20261027T223603Z-94618 seq5・6・8）。
