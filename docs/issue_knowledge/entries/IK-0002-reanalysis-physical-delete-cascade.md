---
id: IK-0002
title: 再解析が知識オブジェクトを物理削除し、連鎖で学習者と教員の記録まで消える
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-13
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F2
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 12
  - docs/architecture/six_lenses_2026-09-10/03_knowledge.md
  - docs/features/knowledge_objects_design.md
feature_context:
  realizing: 論文の解析をやり直して成果を差し替えつつ、その成果に人が付けた判断を保つ
  layers: [pipeline_a, knowledge_objects, endorsement_c]
classification:
  primary: structure
  facets: [structure.representation, governance.resume, connection.version]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「知識オブジェクトが版をまたいで同じものと言える識別子を持たず、実行のたびに
    新しい行として作り直される」こと。処理は仕様どおりに動いており、権限も条件も欠けて
    いない。同一性の表現を変えないかぎり、削除の書き方を変えても「前回のあれ」と結べない
    ので再発する。確認手段は F2 行が引く削除箇所と、外部キーの連鎖経路、および外部キーを
    持たない台帳・疑義・同一性リンク・配置が孤児化する事実。
generalization:
  level: general
  general_form: 再実行が対象を作り直すとき、その対象にぶら下がる人の記録まで連鎖して失われる
pattern: delete-cascade-loses-derived-records
discovery:
  perspective: [trace_walk, invariant_audit]
  note: >-
    「情報を落とさない」という不変条項と、再解析という頻繁な操作を突き合わせた。削除の
    行から外部キーの連鎖を一段ずつ辿ると、学習者の再構成産出物と教員の説明・承認・引用が
    射程に入り、外部キーを持たない層は孤児として残ることが分かった。
resolution:
  perspective: [representation_change, state_transition]
  note: >-
    削除を丁寧にする方向ではなく、同一性の表現を先に作る方向で解いた。内容から決まる
    版非依存の鍵を与え、再実行は削除ではなく「一致すれば同じ行を更新・一致しなければ
    旧行に消された印を押す」遷移にした。読み手は生きている行だけを見る面を読む。参照が
    付け替わる箇所は記録を残して結び直し、基表を直接読める場所を限定して固定した。
  landed_in:
    - backend/core/knowledge_objects/stable_key.py
    - backend/core/knowledge_objects/sync.py
    - docs/features/knowledge_objects_design.md §12
related: [IK-0020, IK-0021]
view_of: [IK-0108, IK-0109]
history: []
---

## 課題

症状は、教材を再解析すると学習者の再構成の産出物や教員の説明・承認・引用が消えることである。
成果の行が物理削除されるため、外部キーの連鎖でその下にぶら下がる記録が一緒に落ちる。外部キーを
張っていない層（台帳・疑義・同一性リンク・配置）は消えない代わりに、指す先を失った孤児として残る。

原因は削除の書き方ではなく、知識オブジェクトに**版をまたいで同じものと言える識別子が無い**ことに
ある。実行のたびに新しい行として作られるので、「前回のあれ」と結ぶ手段が最初から存在せず、
差し替えを表す方法が全消去と再作成しかなかった。人の判断を保つための時間がこの構造には無い。

## 発見の観点

「情報を落とさない」という不変条項を、再解析という日常操作に当てて歩いた。削除の行を起点に
外部キーの連鎖を一段ずつ辿ると、消える範囲が成果だけでは終わらないことが見えた。外部キーの
有無で「消える」と「孤児になる」に分かれるだけで、どちらも記録としては失われている。

## 解決の観点

削除の範囲を狭める案は採らなかった。範囲を狭めても差し替えのたびに同一性が切れるからである。
先に内容由来の版非依存の鍵を与え、再実行を削除ではなく遷移（更新・退避）に置き換えた。消えた
ものは印の付いた行として残り、読み手は生きている面を読む。参照の付け替えは記録に残し、
どの経路が基表を直接読んでよいかを限定した。

## 一般化

「作り直し」が「消してから作る」として実装されている場所すべてで再発する。同一性の表現を
持たない対象に、後から人の判断や他層の参照がぶら下がったときに顕在化する。辞書の
`delete-cascade-loses-derived-records` に対応し、処方は「差し替えの前に同一性の鍵を決める」。
