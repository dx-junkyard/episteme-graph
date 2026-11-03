---
id: IK-0596
title: "課題ナレッジの辞書・解決原理・再発計器は書かれたが、設計・実装の前にエージェントの文脈へ載る経路が無く、読まれないまま同型の再発が続いた"
status: resolved
recorded_at: 2026-10-02
resolved_at: 2026-10-02
sources:
  - docs/architecture/issue_knowledge_effectiveness_review_2026-10-02.md §2
  - docs/issue_knowledge/README.md §3.3b
feature_context:
  realizing: "過去の課題から取り出した型・原理を、次の設計・実装で先に当てて同型の再発を防ぐ"
  layers: [cycle_design, cycle_recording]
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
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続=information: 型・原理・再発の分布（索引 §14.1 / §15）は生成されていたが、設計・実装の段で読む側（毎セッション
    文脈に載る CLAUDE.md・開発スキル・チェックリスト）へ届く経路が無く、規約文「README §3.3b を読む」だけだった。
    索引 1,773 行・辞書 696 行は開きに行かないと見えず、`use: consulted` は 0 件、pre-mortem 節を持つ設計書は
    09-18 以降の 30 本中 2 本、原理を書いた翌日に同じ型が出た（IK-0584 / IK-0592）。統制=review: 新しい設計書が
    pre-mortem 節を持つかを検査する手続が無く、節の欠落が静かに通った。処理=none: 索引・辞書の中身の誤りではない。
    構造=none: 正本は 1 つで分裂していない（表の二重化は本是正の前には無かった）。
generalization:
  level: general
  general_form: "蓄えた知識の索引が精密に育つ一方で、それを使う段の文脈へ自動で載る経路が無く、読む判断を利用者（エージェント）の自発に任せたため使われない"
pattern: available-but-unwired
discovery:
  perspective: [inventory, doc_code_diff]
  note: "エントリ 351 件の front-matter（`use` の分布・pattern ごとの再発）・09-18 以降の設計書 30 本の pre-mortem 節の有無・索引 §15 の自己申告（前向きの適用なし）を並べ、蓄積の計器と活用の計器を分けて集計した。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    生成器に想起シート `recall.md`（変更の語 → 族 / 繰り返し出ている型の見分け方・確かめ方・原理 / 設計書に書く形。
    行数上限）を足し、CLAUDE.md 開発ルール 8 の先頭・開発スキル 2 本・開発チェックリスト §6・改善サイクル §2.3 から
    参照させた。新しい `*_design.md` に pre-mortem 節（型 slug か「該当なし」）を必須にするガードレールと、設計書が
    予告した型 × その設計書を出典に持つエントリの型を突き合わせる索引 §14.5 を足した。引き金の表は生成器の定数だけが
    持ち README の表は撤去した（正本の二重化を作らない）。4 軸・確信度・提案の格下げは別の判断として残した。
  landed_in:
    - backend/scripts/issue_knowledge_index.py
    - docs/issue_knowledge/recall.md
    - backend/tests/test_issue_knowledge_guardrails.py
    - CLAUDE.md
    - .claude/skills/episteme-graph-dev/SKILL.md
    - docs/development_checklist.md
  verification:
    methods: [guardrail]
    unverified:
      - "次の波で `consulted` と索引 §14.5 の的中が実際に増えるか（経路を置いただけで、読まれた実績はまだ無い）"
related: [IK-0325, IK-0372, IK-0584, IK-0592]
view_of: []
history: []
---

## 課題

課題ナレッジは 2 週間で 351 件に育ち、辞書 46 型・解決原理 3 本・機械索引 15 節を持つに至ったが、設計・実装の前に
それを読んだ記録が無い（`use: consulted` 0 件・pre-mortem 節を持つ設計書 2/30）。索引 §14.1 は同じ型が月をまたいで
出続けることを表示していたが、4 つの根の統合の起点はオーナー指示だった。原理を書いた翌日に、その原理の「破られる条件」
そのものの形で同じ型が再発した。

## 発見の観点

蓄積の計器（件数・分類の精密さ）と活用の計器（設計前に読まれた記録・予告と実際の一致・再発）を分けて数えた。
効いた知識（CLAUDE.md の不変条項・.personalize）と効かなかった知識（索引・辞書）の差は「毎セッション文脈に載るか」だった。

## 解決の観点

知識の分量を増やさず、読む段の文脈に必ず載る短い 1 枚を機械生成で置き、設計書側に型を書くことを検査で求め、
予告と実際の一致を計器にした。

## 一般化

蓄えた知識の索引が精密に育つ一方で、それを使う段の文脈へ自動で載る経路が無く、読む判断を利用者の自発に任せたため使われない。
