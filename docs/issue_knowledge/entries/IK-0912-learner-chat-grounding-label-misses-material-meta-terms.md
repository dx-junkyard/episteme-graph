---
id: IK-0912
title: "音声で「教材の L TOV ってやつの式、言葉で言うとどういう意味？」と聞くと、AI は表示中の教材を読んで「というのが教材の説明なんだ」「教材の注意書きだね」と答えるのに、出所は「出典を追えない AI の説明」（出典 0 件）になる — 教材が問いに関わるかの判定が、問いの会話の語（講義・言葉）を内容語に数え、教材側では TeX で書かれた式の名前とも照合できない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学生が講義を聴けない代わりに、音声で教材の式の意味を尋ねる"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 処理軸 logic: backend/api/routes/learning.py の _topic_material_engages_message（4356〜4375 行）は問いの内容語の過半が教材の文字列に含まれるときだけ教材を根拠に数える。_grounding_content_terms は「講義」「言葉」のような会話の語も内容語にし、「L TOV」は教材側では LaTeX（\mathcal{L}_{\rm TOV}）で書かれて一致しない。結果、教材を注入して回答させながら（6610〜6620 行）、出所は model_generated（6689〜6693 行）。接続軸 meaning（medium）: 回答本文と出所表示が別の判定に従う。どの語が外れたかは実行時の値で確かめていないので hypothesis。
generalization:
  level: repo_pattern
  general_form: "回答に材料を渡すかどうかと、その材料を根拠と表示するかどうかを別の判定で決め、表示の判定が材料の書き方（数式記法）と問いの言い回しに弱いため、本文と出所表示が食い違う"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: >-
    st-07（音声中心）の seq13。回答は教材の内容を正しく言葉で述べたが、出所は出典を追えない AI の説明。run: uxsim/runs/c-astro-r25-studio-r2/20261023T021354Z-73017（seq13）。
resolution:
  perspective: [single_point_fix]
  note: "教材が問いに関わるかの既存の判定（_topic_material_engages_message）だけを変えた: 問いの枠の語（言葉・講義・数式・言い方・言い換え・読み方・日本語）を内容語から外し、教材側は TeX の制御語と括弧・添字記号を外した文字列も照合に加える（プロンプトの本文は不変）。固定文は足していない。応答が表示中教材の出典を引用したかを判定に足す案は採らなかった（表示中の教材には出典番号が無く、生成後の判定を足すことになるため）。"
  landed_in:
    - "backend/api/routes/learning.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の実行時に外れた語は確かめていない（観察の発話を単体テストで再現して判定が変わることだけを確かめた）"
      - "回答本文と出所表示の一致を実機の音声会話で見ていない"
related: [IK-0382, IK-0583, IK-0511]
view_of: []
history: []
---

## 課題

症状: 回答本文は「教材の説明」と言い、出所は「出典を追えない AI の説明」と言う。学生はどちらを信じればよいか分からない。

原因（仮説）: 教材が関わるかの決定論判定が、会話の語と数式記法に弱い。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: 教材が問いに関わるかの既存の判定（_topic_material_engages_message）だけを変えた: 問いの枠の語（言葉・講義・数式・言い方・言い換え・読み方・日本語）を内容語から外し、教材側は TeX の制御語と括弧・添字記号を外した文字列も照合に加える（プロンプトの本文は不変）。固定文は足していない。応答が表示中教材の出典を引用したかを判定に足す案は採らなかった（表示中の教材には出典番号が無く、生成後の判定を足すことになるため）。

当初の是正案（記録）:

- 内容語から会話の語（講義・教材・言葉・意味・式 等の問いの枠の語）を外す（_GROUNDING_GENERIC_TERMS に足す）。
- 教材側は material_text_for_prompt と同じく数式を読み下した文字列（記号名の素の形）も haystack に入れる。
- 応答が表示中の教材の出典マーカーを引用したかを判定に足す（第 18 周 G2 の advice 経路と同じ規則）。IK-0382（注入しただけで教材と表示しない）は保つ。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

表示の出所は回答に渡した材料と同じ判定から導く。確かめ方は、教材に TeX で書かれた式の名前を会話調で尋ね、本文が教材に触れるとき出所が教材になることを見ること。
