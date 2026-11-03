---
id: IK-0359
title: 図の文脈表示が、段落メンション由来の主張を「証拠を与える」関係の切り詰め断片として並べ、図が何を示すかを述べる本文の文を出していなかったため、一目で意味が通じなかった
status: resolved
recorded_at: 2026-09-25
resolved_at: 2026-09-25
sources:
  - docs/features/element_context_presentation_redesign.md §11
feature_context:
  realizing: 図を選んで内訳・文脈を確認し AI と検討する（深く検討）
  layers: [deliberation_w, pipeline_a]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning, information]
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
    接続軸: 前段は図と主張を段落単位の同時言及（図番号を含む段落の全主張）で結び付けているのに、後段はその
    リンクを「証拠を与える」「裏づける」と読み替えて描いていた（meaning。前段の結び付きの意味が後段で強く
    ずれる）。また前段の成果物には図が何を示すかを論文自身が述べる文（メンション文）が段落として残っており、
    メンションの正規表現も既にあったのに、図の文脈表示へ運ばれていなかった（information）。確認手段:
    実環境の論文で、図に付く主張行が全件段落メンション由来であること・大半が見出し長で切り詰められている
    こと・主張の出典文が全件空で種別ラベルだけが裸で残ること・親主張と細分化した子主張が同じ内容で並ぶことを
    live 行と成果物から数えた（設計書 §11.1）。構造軸: 関係語の語彙に「本文で一緒に言及される」を表す値が
    無く、同時言及を証拠関係としてしか表せなかった（representation。語彙を足さなければ再発する）。
    処理軸: カードが種別チップ → 関係語 → ラベルの順に描き、後置の動詞句である関係語が相手の前に来て文として
    読めなかった。判定できない主張の種類を「不明」と裸で出していた（wording。挙動は正しく表示だけの誤り。
    意味のずれとの境界で迷ったので medium）。統制軸: 担当・順序・予算・完了判定は関係しない（none）。
    A層の段落単位のクロスリンク自体は変えていない（読み時に同じ材料から文単位の表示を得る）。
    型は主因の「材料はあるのに表示へ配線されていない」で available-but-unwired とし、関係語と語順の問題は
    wording-mismatch にも当たる（従）。
generalization:
  level: repo_pattern
  general_form: 粗い粒度の結び付きを後段が強い関係として描き、その結び付きの根拠になった読める原文は運ばないため、利用者には強すぎる関係語と切り詰めた断片の羅列だけが見える
pattern: available-but-unwired
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    オーナーの症状報告（深く検討の支える主張の欄が断片の羅列で読めない）を受け、実環境の論文の図と主張行を
    live 行と成果物で数え、図と主張を結ぶリンクが作られる経路（段落メンションのクロスリンク）から文脈表示の
    関係語・ラベル・補足行までを 1 本ずつ辿った。
resolution:
  perspective: [carry_through, vocabulary_table]
  note: >-
    メンション文を読み時に取り出して図の掲載情報として後段まで運び、主役に据えた。段落メンション由来の主張には
    事実の強さどおりの新しい関係語（本文で参照される）を語彙表に足し、証拠関係は caption 由来だけに残した。
    主張は全文で出し、細分化した子は親にまとめ、判定できない種類は出さず、カードの語順を相手 → 関係 → 裏付けに
    改めた。A層のクロスリンクを文単位にする案は再解析が要るため採らなかった。
  landed_in:
    - backend/core/deliberation/figure_mentions.py
    - backend/core/deliberation/context_lens.py
    - frontend/public/js/element-card.js
    - docs/features/element_context_presentation_redesign.md §11
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - docker 実機の画面確認
      - 表（table）要素
      - 和文論文の「図 N」メンション
related: [IK-0204, IK-0218, IK-0358]
view_of: []
history: []
---

## 課題

**症状**: 「深く検討」で図を選ぶと、支える主張の欄が「主張 に証拠を与える（切り詰めた英文） 出典に裏付け 背景」のような
断片の羅列になり、図が何を示し、本文のどこで何のために参照されているかが一目で読めない。模式図に手法選択の主張が
「証拠を与える」相手として多数並ぶ。親主張と細分化した子主張が同じ内容で重なり、補足行は「背景」「不明」だけになる。
掲載節が空の図もある。

**原因**: 図と主張のリンクは段落単位の同時言及で作られているのに、文脈表示はそれを証拠関係として描いていた。図が何を
示すかを論文自身が述べるメンション文は成果物に残っていたが表示へ配線されておらず、代わりに同じ段落の主張が見出し長で
切られて並んだ。関係語が後置の動詞句なのにカードが相手より先に描き、意味の無い種別ラベルを裸で出していた。

## 発見の観点

実環境の論文の図と主張行を live 行と成果物で数え（`data_inspection`）、リンクの生成経路から表示の関係語・ラベルまでを
辿った（`trace_walk`）。

## 解決の観点

既にある材料（メンション文）を後段まで運んで主役にした（`carry_through`）。同時言及を表す関係語を語彙表に足し、
証拠関係と区別した（`vocabulary_table`）。前段のクロスリンクを文単位にする案は、再解析を要し既存の成果を差し替えるため
採らず、読み時に同じ結果を得る側を選んだ。

## 一般化

前段が粗い粒度で結び付けた関係を、後段がより強い関係語で描き、しかもその結び付きの根拠になった原文を運ばないと、
利用者には根拠の無い強い主張の羅列に見える。図と本文・引用と主張・配置と論文など、粗い同時出現でリンクを作る
箇所で再発しうる。主因は辞書の `available-but-unwired`、関係語と語順の食い違いは `wording-mismatch` にも当たる。
