---
id: IK-1006
title: "回答本文の出典の目印の整合（_reconcile_citation_markers）が、根拠の無い番号の [出典N] を本文から取り除くとき、目印が文の名詞の位置（「前の回答の「[出典3]」は…」）にあると引用符だけが残り「前の回答の「」は」と中身の欠けた文になる（前の往復の訂正の持ち越し文にも同じ欠けが入る）"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "学生が学習チャットで前の回答の誤りを尋ね、訂正を含む回答と次の往復の持ち越しを読む"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 処理軸 logic — routes/learning.py の _reconcile_citation_markers は有効な番号以外の [出典N] を空文字に置換する（句読点の前の空白だけ掃除）。目印が付記ではなく文の要素（引用符の中・主語）として使われると、文が欠ける。前の往復の本文は履歴に保存され、core/dialogue_shape.previous_correction_fact が逐語で持ち越すので欠けが次の往復の system にも入る。確定には、該当往復の LLM の生出力（置換前）に「「[出典3]」は」があることを mailbox の req-04063 応答で見ることが要る。
generalization:
  level: repo_pattern
  general_form: "本文から無効な目印を機械的に取り除くとき、目印が文の要素として使われている場合を区別せず、文の意味を壊す"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 B 4063（tutor・3 往復目）に渡ったプロンプトで、前の往復の回答本文と「前の往復での訂正」の持ち越し文の両方が「前の回答の「」は」「TeX 由来の抽出  では」と空欄だった（uxsim/runs/round25/BRAIN_NOTES_product_B_r5.md）。前の往復の回答は存在しない [出典3] を引用していた（製品頭脳 A 4062）。
resolution:
  perspective: [canonical_source, representation_change]
  note: >-
    出典の目印の除去を core/text_hygiene.remove_citation_markers の 1 箇所にした（正規表現 CITATION_MARKER_RE も移した）。目印だけを囲んだ引用符・名詞の位置（直前が「の」・文頭・読点、直後が助詞）の目印は空にせず句に置き換え（応答本文 = 確認できない出典 / 前の往復の持ち越し = 前の回答で挙げた出典、直前が「の」なら 出典）、付記の位置は前後の空白ごと取り除く。_reconcile_citation_markers と _correction_source_text（previous_correction_fact の入力）が同じ関数を通る。仮説のうち確かめたのは、空文字置換で「「[出典3]」は」が「「」は」になることと、持ち越しが同じ剥がしを通ることをコードとテストで見たところまで。req-04063 の置換前の生出力は見ていない。
  landed_in:
    - "backend/core/text_hygiene.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave5_chat_rag.py"
    - "docs/features/dialogue_response_shape_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "該当往復の LLM の置換前の生出力（mailbox req-04063）で目印が名詞の位置にあったこと"
      - "砂場での再演（前の回答の誤りを尋ねる 3 往復）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "目印の剥がしが応答本文と持ち越しの 2 経路にあり、どちらも空文字置換だったので、規則を text_hygiene の 1 関数に置いて両経路を通した。固有の差分: 句は経路ごとに引数で変える（未確認の出典 / 前の回答で挙げた出典）。"
related: [IK-0576, IK-0432]
view_of: []
history: []
---

## 課題

症状: 前の回答の誤りを説明する文が「前の回答の「」は…」のように中身を失う。持ち越しの訂正文も同じく欠ける。

原因（仮説）: 根拠の無い出典の目印を空文字に置き換える処理が、目印を名詞として使った文を壊す。

## 発見の観点

データの点検（製品頭脳に渡ったプロンプト）。

## 解決の観点

第 25 周 第 5 波で解決（frontmatter の resolution）。当初の是正案: 目印の直前が引用符・格助詞の前（「…」の中、は・が・を の直前）のときは空文字ではなく「（確認できない出典）」に置き換える。あるいは LLM への指示で、出典の目印を文の要素にしない（付記としてだけ使う）ことを求め、置換は付記の位置だけで行う。持ち越し文は置換後の本文から作るので、置換の側を直せば両方が直る。

## 一般化

機械的な除去は、除く対象が文の構成要素として使われる場合を想定する。確かめ方は、目印が付記と名詞の両方の位置にある本文で除去を走らせ、文が読めることを見ること。
