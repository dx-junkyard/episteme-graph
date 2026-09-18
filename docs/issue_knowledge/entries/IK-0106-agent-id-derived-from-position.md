---
id: IK-0106
title: 知識オブジェクトの識別子が出現順由来で、再解析のたびに同じ ID が別物を指す
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §2 D2
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-4 / S-5
  - docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md C-1 / C-7
  - docs/features/knowledge_objects_design.md §5.1
feature_context:
  realizing: 再解析で成果を作り直しても、同じ主張・部品を同じものとして指し続ける
  layers: [knowledge_objects, pipeline_a]
classification:
  primary: structure
  facets: [structure.representation, connection.version]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    識別子が内容ではなく出現順（連番・分割順）から作られるため、入力が少し変わるだけで同じ ID が
    別の対象に付く。凍結済みコースが参照する部品 ID が現在の集合に存在しないことを実測で確認した。
    内容由来の同一性という表現を持たない限り、参照する側をいくら直しても版をまたいだ追跡は
    できない点が構造の定義に当たる。内容ハッシュは生成側に既にありながら保存されていなかった。
generalization:
  level: general
  general_form: >-
    識別子を内容ではなく出現順から作るため、作り直しのたびに同じ識別子が別の対象を指し、
    それを参照する判断が宙に浮く
pattern: id-not-stable-across-versions
discovery:
  perspective: [data_inspection, adversarial_review]
  note: >-
    凍結コース・説明・台帳が持つ ID を現在の集合と突き合わせ、解決できない参照を数えた。
    生成側に内容ハッシュが既に存在するのに保存先が無い、という供給と保存の非対称も併せて見えた。
resolution:
  perspective: [representation_change, state_transition]
  note: >-
    内容（正規化本文・出典ブロック集合・文書）から導く安定キーを発行し、出現順 ID は別列に
    残した（情報を落とさない）。同一性は安定キーで取り、対応が変わった事実は再係留の表に記録する。
  landed_in:
    - backend/core/knowledge_objects/stable_key.py
    - backend/core/knowledge_objects/remap.py
related: [IK-0105, IK-0107, IK-0108, IK-0118]
view_of: []
history: []
---

## 課題

**症状**: 教員が承認した部品がどれだったかを再解析後に特定できない。説明・台帳・痕跡が
実体のない ID を指したまま残り、誰にも見えない。

**原因**: 識別子が `comp_001` / `claim_span_001_9_sub02` のように出現順・分割順から作られていた。
本文が同じでも前後の抽出結果が変われば番号がずれ、同じ ID が別物を指す。内容から導けるキーは
生成側の成果物に存在していたが、保存先が無かった。

## 発見の観点

参照する側（凍結コース・説明・台帳）の ID を現在の集合と突き合わせ、解決できない参照を数えた
（`data_inspection`）。「再解析すると何が起きるか」を壊すつもりで辿ったことで、参照先が静かに
入れ替わる経路が見えた（`adversarial_review`）。

## 解決の観点

参照する側を個別に直しても追いつかないので、同一性そのものを内容由来へ移した
（`representation_change`）。旧 ID は捨てず別列に残し、対応の変化は削除ではなく記録として
残す（`state_transition`）。

## 一般化

作り直しのある生成物（解析結果・レイアウト・インポート）に出現順の識別子を与えると、
その識別子を参照した人間の判断が版をまたいだ瞬間に意味を失う。識別子の作り方は、
参照する側の寿命に合わせて決める必要がある。
