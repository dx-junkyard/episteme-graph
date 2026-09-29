---
id: IK-0468
title: "英語の会話の鏡（〔鏡〕You read it as \"…\"〔/鏡〕）が抽出されず、言い直しが本文に残った。「」で引用した英語の鏡は抽出されたが、在処の事実文は日本語のまま英語の会話に入った"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "discuss の言い直し（鏡）を学習者の言語に関係なく回答の先頭の枠に出す"
  layers: [discuss]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    処理軸: extract_mirror の逐語検査は「」の引用だけを数え、"…" / “…” の英語の引用は引用ゼロ＝不合格としてマーカーを剥がすだけだった（input_handling）。接続軸:
    プロンプトの鏡の書式は日本語の「」だけを示し、英語で答えるモデルは "" を使う。抽出側と生成側で引用の約束が片側だけだった（contract）。在処の事実文 MIRROR_MOVED_NOTE は日本語の定数1つ。
generalization:
  level: general
  general_form: "生成の書式の約束が一つの言語でしか書かれておらず、別の言語の出力を検査側が形式違反として落とす"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: "req-00082 / 00084 の history で、\"\" 引用の鏡はタグだけ剥がれて本文に残り、「」引用の鏡は日本語の置換文に置き換わっていた。"
resolution:
  perspective: [explicit_contract, single_point_fix]
  note: >-
    _QUOTE_RE が「」・“”・"" の3種を同じ逐語検査にかける（全引用逐語・2文字以上は不変）。プロンプトに英語の鏡の書式を1行足す。MIRROR_MOVED_NOTE_EN
    を足し、学習者の直前の発話にかな・漢字が無いときは呼び出し側で英語の事実文に差し替える（_is_kana_kanji_free — IK-0450 と同じ規則）。
  landed_in:
    - backend/core/discuss/mirroring.py
    - backend/api/routes/learning.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（英語の discuss でモデルが \"\" 引用の鏡を書くか）"
related: [IK-0400, IK-0450]
view_of: []
history: []
---

## 課題

英語の会話の鏡（〔鏡〕You read it as "…"〔/鏡〕）が抽出されず、言い直しが本文に残った。「」で引用した英語の鏡は抽出されたが、在処の事実文は日本語のまま英語の会話に入った

## 発見の観点

req-00082 / 00084 の history で、"" 引用の鏡はタグだけ剥がれて本文に残り、「」引用の鏡は日本語の置換文に置き換わっていた。

## 解決の観点

_QUOTE_RE が「」・“”・"" の3種を同じ逐語検査にかける（全引用逐語・2文字以上は不変）。プロンプトに英語の鏡の書式を1行足す。MIRROR_MOVED_NOTE_EN を足し、学習者の直前の発話にかな・漢字が無いときは呼び出し側で英語の事実文に差し替える（_is_kana_kanji_free — IK-0450 と同じ規則）。

## 一般化

生成の書式の約束が一つの言語でしか書かれておらず、別の言語の出力を検査側が形式違反として落とす。
