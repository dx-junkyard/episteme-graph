---
id: IK-0859
title: "コースビルダーの 2 往復目以降、AI は画面に出ている現在のコース構成案を見られない — 応答から COURSE_DRAFT_JSON 以降を剥がした本文だけを履歴に積み、構成案の疑似ターンは履歴が 1 件以下のときしか注入しないため、教員が「各トピックがどの学ぶ単位から作られているか」「これで登録する」と続けても、AI は前の案のトピック一覧を持たずに答える"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/learning_units_design.md"
feature_context:
  realizing: "教員がコースビルダーで構成案を出させ、続けてその案の根拠を確かめてから登録する"
  layers: [course_builder, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    接続軸 information: `frontend/public/js/admin.js` のコースビルダー送信（5335-）は `extractCourseDraftFromAnswer` で JSON を
    剥がした `assistantAnswer` を `state.chatHistory` に積み（5386-5388）、`state.courseDraft` の疑似ターン（【現在のコース構成（JSON）】）は
    `chatHistory.length <= 1` のとき（取り込み直後の初回）だけ先頭に足す（5343-5353）。2 往復目以降の要求には現在の構成案が
    どこにも載らない。AI 自身が「前回お示しした構成案のトピック一覧（区切り文字以降の JSON）は、いま私が見られる会話の中に
    残っていません」と答え、登録直前の確認でも「登録されるのは画面に表示されているコース構成案です」と断った。製品頭脳の
    所見 3980 / 3988 / 3993 / 3995 が同根。処理軸 none。構造軸 none（構成案は state.courseDraft に正本としてある）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "会話の状態（いまの成果物）を表示用の本文から剥がして持ち、AI に渡す履歴へは最初の 1 回しか戻さないため、続く往復で AI が画面と違うものを前提に答える"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "種まき（t-v5-register-own-course）で教員 te-03 が 2 往復目に学ぶ単位の対応を求めると、AI が前の案の一覧を見られないと答えた。製品頭脳の材料（3980 / 3982 / 3988）にも構成案が無かった。送信本文の組み立てを admin.js で辿った。 run: uxsim/runs/c-r25-doubt-r1/20261022T123418Z-31805（seq6・7）・uxsim/runs/c-r25-doubt-r1/20261022T124129Z-35838（seq6・7）。頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_product_P0.md 3980・3988・BRAIN_NOTES_product_P1.md 3993・3995。"
resolution:
  perspective: [carry_through]
  note: >-
    admin.js の sendCourseChat は state.courseDraft があれば毎回、最新の構成案の疑似ターン 1 組を履歴の先頭に入れる（chatHistory.length <= 1 の条件を外した。state.chatHistory には積まない・サーバの window_history head_keep=2 が守る）。U ハンドルを本文に書かない指示は IK-0883 側で入っている。
  landed_in:
    - "frontend/public/js/admin.js"
    - "backend/tests/test_doubt_course_scope_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "コースビルダーの 2 往復目以降に AI が構成案を参照できることは実機で未確認"
      - "構成案が大きいと履歴の文字数予算（4000）で後続の会話が早く畳まれる可能性"
related: [IK-0438, IK-0882]
view_of: [IK-0882]
history: []
---

## 課題

症状: コースビルダーの 2 往復目以降、AI が「前の構成案は見えていない」と答え、登録前の確認が画面の案と食い違いうる。
原因: 構成案の JSON を剥がした本文だけを履歴に積み、構成案の疑似ターンは初回しか注入しない。

## 発見の観点

種まきの通し（reproduction）と送信本文の組み立ての追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。構成案の正本（state.courseDraft）を毎回の要求に載せる。

## 一般化

表示のために本文から状態を剥がす会話 UI は、剥がした状態を別経路で毎回戻さないと AI と画面が食い違う。確かめ方は、
3 往復以上続けて「直前の案のトピックを列挙して」と聞き、画面の案と一致することを見ること。
同じ根は、会話履歴をブラウザ内に持つ他の対話（教材図スタジオの対話生成・原稿スタジオの書き換え）で、画面上の現在の成果物を
毎回の要求に戻していない経路にも出うる（studio 側の IK-0882 はこの課題の参照エントリ）。
