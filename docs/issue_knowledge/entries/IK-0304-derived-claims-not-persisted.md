---
id: IK-0304
title: 生成された主張のうち一部だけが永続化され、グラフの根拠が常に「未解決」になる
status: resolved
recorded_at: 2026-09-02
resolved_at: 2026-09-02
sources:
  - docs/features/graph_dialogue_review_design.md §14
feature_context:
  realizing: グラフのノードが何を根拠にしているかを、本文とともに読めるようにする
  layers: [theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 永続化は対象として決められた範囲を正しく保存しており、単一処理の不良は無い。

    構造: 派生した主張を保持する行が用意されていない点が表現に当たる。

    接続: 後段が識別子を持っているのに本文という情報が段の間で解決できない点が情報に当たる。

    統制: 保存対象を誰が決めるかは解決側の論点で、原因側に割り当て・順序・完了の崩れは見当たらない。
generalization:
  level: repo_pattern
  general_form: 前段が作った派生物の一部だけが保存され、その識別子を持つ後段が本文を解決できない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [symptom_report, trace_walk]
  note: >-
    「根拠の欄が『本文を取得できません』になる」という症状を、審査状態による絞り込みだと
    疑うところから始め、参照されている識別子の集合と保存されている行の集合を突き合わせて、
    そもそも行が無いことを確かめた。
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    読み時に生成ログ（工程が残す成果物）から本文を解決する二段構えにし、行を持たない
    ものは「承認の対象にならない」ことが分かる形で返した（識別子は空・出所と親を併記）。
    保存側を変える案は前段の責務に触れるため別件として残し、のちに知識オブジェクト層で
    派生主張も行にする形で恒久解が入った（IK-0019）。
  landed_in:
    - backend/api/routes/theory_components.py
    - docs/features/graph_dialogue_review_design.md §14
related: [IK-0303, IK-0019]
view_of: [IK-0019, IK-0105]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.information, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[information];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「ノードの根拠として並ぶ主張が、ほぼ全て『未解決の根拠（本文を取得できません）』に
なる」。最初は審査状態による絞り込みだと疑われたが、そうではなかった。

原因は、前段が作る主張のうち**判定済みスパンだけが永続化される**ことにある。原子化で
生まれた子の主張と、式から決定論的に合成した主張には保存先が無い。いっぽうグラフの
ノードはそれらの識別子を根拠として持っているので、参照はあるのに本文が無い状態になる。

## 発見の観点

症状から出発し（`symptom_report`）、ノードが参照する識別子の集合・保存されている行の
集合・生成ログに残る成果物の集合という三つを突き合わせた（`trace_walk`）。「絞り込みでは
なく行が無い」と分かった時点で原因が確定した。

## 解決の観点

読み時に生成ログから解決する経路を足して情報を後段まで運び（`carry_through`）、行が
無いものは識別子を空にして「承認の対象ではない」と読める形にした（`fail_closed`）。
本文を隠して「未解決」とだけ出す従来の見せ方は、根拠を確かめる作業そのものを止めるので
採らなかった。**保存側を直す**（派生主張も行にする）のが恒久解で、前段の責務とデータ
モデルに触れるため別件とし、のちに知識オブジェクト層で実施されている（IK-0019）。

## 一般化

「作ったものの一部だけを保存する」構造は、参照側が完全な識別子集合を持つ限り必ず穴として
現れる。保存の対象を決めるときは、その識別子を誰が持ち回るかを同時に決める。
辞書の型 `information-dropped-as-unrepresentable` に対応する。
