# 課題エントリの記入様式

[← 課題ナレッジの入口](README.md) ｜ [分類体系](taxonomy.md) ｜ [辞書](dictionary.md)

1 課題 = 1 ファイル。`docs/issue_knowledge/entries/IK-NNNN-*.md`（`NNNN` は 4 桁ゼロ埋め・`*` は slug・
連番・欠番可。slug は英小文字とハイフン）。front-matter は YAML で、機械検査
（`backend/tests/test_issue_knowledge_guardrails.py`）が語彙と必須キーを検査する。
本文は自由だが、見出しは下の 4 つを固定する。

```markdown
---
id: IK-0000
title: 一行の題名（症状ではなく課題の要約）
status: open            # open | resolved | deferred | rejected（taxonomy §7）
recorded_at: 2026-09-18 # 課題が管理対象になった日
resolved_at: null       # 解決日（resolved のとき必須）
sources:                # 起票元・根拠となる docs 内の文書（リポジトリ相対パス。§ は任意）
  - docs/architecture/xxx.md §2
feature_context:
  realizing: どの機能を実現しているときに出る課題か（動詞で一文）
  layers: [knowledge_objects, pipeline_a]  # layers.md の語彙のみ（場所の記録）
classification:
  primary: connection   # local | structure | connection | governance（taxonomy §1）
  facets: [connection.version, governance.review]   # <primary>.<sub>。主分類の接頭辞を最低1つ含む
  cause_status: confirmed   # confirmed | hypothesis（taxonomy §3）
  review: candidate         # candidate | confirmed（分類を人が確定したか。taxonomy §3.1）
  reviewed_by: null         # confirmed のとき必須（確定者の識別子）
  reviewed_at: null         # confirmed のとき必須（YYYY-MM-DD）
  basis: >-
    原因の性質のどこが主分類の定義に当たるか。hypothesis なら「仮説:」で始め、
    何を確認すれば確定するかを書く。症状の場所・修正行数を根拠にしない。
generalization:
  level: repo_pattern   # instance | repo_pattern | general（taxonomy §6）
  general_form: 機能名・層名を含まない一文で課題の型を書く
pattern: reexecution-overwrites-human-decision   # dictionary.md の型（#### 見出し）の slug
discovery:
  perspective: [adversarial_review, data_inspection]   # taxonomy §4。最大 2・先頭が主
  note: 何と何を突き合わせたときに見えたか（一〜三文）
resolution:
  perspective: [state_transition, guardrail_fix]   # taxonomy §5。最大 2・先頭が主。未解決なら [pending]
  note: どの見立てで解決策に至ったか（一〜三文）
  landed_in:            # 着地先。resolved は実在するコードのパスかコミットハッシュを最低 1 つ（文書だけは不可）
    - backend/core/knowledge_objects/sync.py
    - docs/features/knowledge_objects_design.md §12
related: [IK-0001]      # ゆるい関連（無ければ空リスト）
view_of: []             # 同じ原因の別視点にあたるエントリ（taxonomy §6.2。無ければ空リスト）
history: []             # 分類・確度・状態を変えたときの記録 [{date, field, from, to, reason}]
---

## 課題

何が起きるか。症状と、症状から遡った原因を分けて書く。

## 発見の観点

どの見方（taxonomy §4）で、何と何を突き合わせたときに見えたか。

## 解決の観点

どの見立て（taxonomy §5）で解決策を選んだか。選ばなかった案があれば理由も。
未解決なら、何が分かれば解けるかを書く。

## 一般化

この課題の型は他のどんな機能で再発しうるか。辞書の型（pattern）との対応。
```

## 書き方の規律

- **原因を主語にする**。「〜が表示されない」は症状。「〜が段階 B で落ちるため表示されない」が課題。
- **場所は `feature_context.layers`（[layers.md](layers.md) の語彙）に、根拠は `classification.basis` に**。混ぜない。
- **観点は最大 2 つ・先頭が主**。並べて受け皿にしない。
- **AI が起こした分類は `review: candidate`**。人が読んで確定するまで型の成立に数えない。
- **未確定は隠さない**。原因が仮説なら `cause_status: hypothesis` とし、本文でも「仮説」と書く。
- **数値で分類しない**。行数・件数・規模は分類の根拠にならない（taxonomy §1.2）。
- **解決したら書き換えではなく追記**。分類が変わったら `history` に残す。
- **出典は docs 内に実在する文書**（`sources` のパスは機械検査でリンク実在を確認する）。
