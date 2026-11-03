---
id: IK-0967
title: "論文の配置の理由文が、並べて出す原文の逐語より広いことを言う（理由「BAO と Ia 型超新星の距離測定を用いて」に対し逐語は BAO だけ）— 逐語は verbatim 検査を通るが、理由は逐語に照らして検査されない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "学習者が出典タブと地図の『論文の位置』で、論文がその場所に置かれた理由を原文の逐語で確かめる"
  layers: [knowledge_landscape, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
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
    接続軸 meaning: 画面は理由文と逐語を一組の「根拠」として並べるので、学生は理由が逐語に支えられていると読む。
    src/episteme_graph/agents/landscape_placement/validator.py は evidence_quote の verbatim と reason の有無・長さだけを
    検査し、理由が引用の範囲を越えるかは見ない（prompt は理由を自由な日本語の事実文として求める）。構造軸
    representation: 理由（生成の要約）と逐語（原文）の出所の違いが、範囲の違いとして画面に現れない。統制軸 review:
    教員の確認を経ていない AI 推定の配置が学習者に出る（〔AIによる推定（未確認）〕のラベルは付く）。処理軸 none。
generalization:
  level: repo_pattern
  general_form: "根拠の引用は原文照合を通すが、引用に添える生成の要約は照合されず、引用より広い主張を引用の権威で運ぶ"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, trace_walk]
  note: "BAO に懐疑的な学生が、宇宙膨張の配置の理由は超新星にも触れるのに逐語は BAO だけで、超新星の根拠が示されないと記した。配置の validator の検査項目を辿った。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233738Z-85320 seq 7。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    LS4（逐語の verbatim 検査）は緩めず、理由と引用の範囲の違いを印と warning で見せる。agents/landscape_placement/reason_scope.py（固有の語 = ラテン文字の略語・固有名を alias_matching の語境界一致で照合）を validator が warning placement_reason_terms_beyond_quote に使い（配置・理由は落とさない）、プロンプトに「理由は引用が言う範囲に限る」の 1 行。表示は landscape/projection が理由に reason_label「AIの要約」、引用の外の語があるときだけ reason_note「理由に挙がる「Ia」は、引用には現れません。」を付け、学習者の地図・出典タブ・教員の位置づけモーダル・リリース前の確認がサーバの文を素通しで描く。プロンプトの変更は配置層自身の agent（A層成果を読む側）への 1 行で、LS2「A層非改変」には当たらない（カテゴリギャップ §5.1 と同じ扱い）。
    第 5 波（2026-10-27）: 残り — reason_note に挙げる語を固有の語（大文字か数字を含む）に絞った（core/landscape/schema._is_proper_term）。小文字だけの普通の語（dynamical・dark・energy）は挙げない。A層の照合（reason_scope）と validator の warning は変えず表示の段で絞る。
  landed_in:
    - "src/episteme_graph/agents/landscape_placement/reason_scope.py"
    - "src/episteme_graph/agents/landscape_placement/validator.py"
    - "src/episteme_graph/agents/landscape_placement/prompt.py"
    - "backend/core/landscape/schema.py"
    - "backend/core/landscape/projection.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/landscape-layer.js"
    - "frontend/public/js/app.js"
    - "backend/tests/test_atlas_review_round25_r4.py"
    - "src/tests/agents/landscape_placement/test_validator.py"
    - "backend/tests/test_atlas_review_round25_r5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "日本語の固有語は照合しない（形態素の切り方で誤検出が増えるため v1 は対象外）"
      - "プロンプト変更後の実 LLM 出力で理由が狭まるかは測っていない"
related: [IK-0677]
view_of: []
history: []
---

## 課題

症状: 第 4 章の論文の「宇宙膨張（観測から）」の配置で、理由は「論文は BAO と Ia 型超新星の距離測定を用いて背景膨張史を制約している」、
逐語は「…constrained using DESI DR2 BAO measurements.」。超新星の根拠は画面のどこにも無い。

原因: 逐語だけが原文照合され、理由は照合されない。

## 発見の観点

通し受講（reproduction）で学生が記し、配置の検査項目を辿った（trace_walk）。

## 解決の観点

第 4 波（2026-10-25）で解決。LS4（逐語の verbatim 検査）は緩めず、理由と引用の範囲の違いを印と warning で見せる。
第 5 波（2026-10-27）で普通の英単語を挙げる残りを直した。

## 一般化

根拠に添える要約が根拠の範囲を越えていないかを確かめる。確かめ方: 配置の理由に出る固有の語が逐語に現れるかを
全配置で走査する。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 主旨は解消（理由に「〔AIの要約〕」の印、分子雲の「理由に挙がる『Cepheus』は、引用には現れません」、宇宙膨張の「『Ia』は…」）。残り（軽）: 暗黒エネルギーの配置で「理由に挙がる『dynamical』・『dark』・『energy』は、引用には現れません」と、要約が英語の句を日本語の文に混ぜただけの普通の語まで挙げ、注意の重みが薄まる（学生 st-09 が『少し煩い』と記した）。固有語の判定を、引用に無い語のうち要約の日本語文に英字で残った一般語を除く形に絞るか、句（dynamical dark energy）を 1 つの語として扱う。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071720Z-78305 seq 6 / 7。


## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE（残り）。注記に挙がる語は大文字・数字を含む語だけ（「Cepheus」「Ia」）になり、dynamical dark energy の配置の理由には注記が付かなくなった。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 6 / 7。
