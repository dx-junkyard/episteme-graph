---
id: IK-1021
title: "凍結したコースの教材の ⚓（部品の agent ID 参照）が、論文の再解析で同じ agent ID を割り当てられた別の部品へ解決され、教材に書かれた名前（TOV network map）と開いた部品の見出し（TOV residual loss at collocation points）が食い違う"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/component_evidence_redesign.md"
feature_context:
  realizing: "学生が凍結済みのコースの教材の ⚓ を押し、教員がコースを作ったときに指した部品の文脈を読む"
  layers: [knowledge_objects, learner_experience_b, course_builder]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version, target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 version / target: コースの教材と出典タブは凍結時のスナップショット（⚓ の名前「TOV network map」・要約「半径・中心密度・EOS 特徴から恒星内部プロファイルを出力する構造ネットワーク」）を持ち、部品は agent ID（comp_003）で指す。学習者 API は core/component_context.py の _resolve_component_row / _resolve_live_components で agent ID を live 行の source_scope.legacy_ids に突き合わせる。agent ID は run ごとの出現順の連番で、再解析後の comp_003 は「TOV residual loss at collocation points」（実測: st-08 seq7・st-01 seq11 とも GET .../components/comp_003/context が別の部品を返した。旧名の要約に当たる部品は Structure network conditioned on EOS features）。構造軸 representation: コースが部品を指す鍵が版をまたいで同一性を持たない agent ID で、凍結版と live の対応を表せない。処理軸 none: 解決の処理は規則どおり。統制軸 none: 誰が判断するかの問題ではない。
generalization:
  level: repo_pattern
  general_form: "凍結した参照を再生成のたびに振り直される連番で持ち、読むときに最新の生成物へ解決するため、同じ番号を得た別の対象が開く"
pattern: id-not-stable-across-versions
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-08 と st-01 がそれぞれ ⚓2「TOV network map」を押し、どちらも見出し「TOV residual loss at collocation points」の部品が開いた（uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq6-7・20261027T123326Z-10141 seq11）。教材の応答の参照 comp_003 と r6_prereqs.md の再解析の実測（部品名が全件変わった）を突き合わせた。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    新しい凍結から、コースの content_blocks の部品の行と部品の evidence_links に db_id（凍結時点の live 行の UUID）を足す（core/frozen_component_refs.stamp_component_db_ids。本文の ![[component:comp_003]] と component_id は変えない — 教材の解決規則・原稿スタジオの契約を保つ）。学習者 API の部品の文脈と説明は ⚓ の ID から凍結の記録を引いて解決する（resolve_frozen_component: live = その UUID / remapped = element_id_remap の superseded_component の記録を辿った後継 + version_note / superseded = available:false と事実文、説明は 404 + 同じ文）。旧い凍結（db_id なし）は凍結時の部品名と一致する行だけを採り、複数の論文に当たれば解決しない。手がかりが無ければ従来の解決。app.js は available:false と version_note をサーバの文のまま描く。 第 7 波（2026-10-28・doubt 側）: 後継の決まらない ⚓ は部品の文脈も説明も 200 で凍結時の部品名（frozen_label）と事実文（label_vocab.frozen_component_superseded_line）を返し（_superseded_component_payload・内部 ID なし）、説明ポップアップと部品の文脈の見出しに凍結時の部品名を出す。
  landed_in:
    - "backend/core/frozen_component_refs.py"
    - "backend/core/course_content_builder.py"
    - "backend/api/routes/learning.py"
    - "backend/core/knowledge_objects/remap.py"
    - "frontend/public/js/app.js"
    - "docs/features/knowledge_objects_design.md"
    - "backend/tests/test_share_round25_r6_fixes.py"
    - "backend/tests/test_doubt_round25_wave7.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "凍結した参照を run ごとの番号ではなく行の UUID で持ち、版をまたぐのは remap の記録だけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "旧い凍結で凍結時の部品名がどの行とも一致しない ⚓ は従来の解決のまま（再凍結で db_id が付く）"
      - "砂場の凍結コースで ⚓ が凍結時の部品を開くことの再演（第 7 周 st-05）"
      - "後継の決まらない ⚓ から学生が次に読める場所の案内（再凍結の運用）はまだ無い"
related: [IK-1012, IK-0550, IK-0554]
view_of: []
history: []
---

## 課題

症状: 学生がコース 1bad7d9f の第 3 章「二重ネットワークとTOV方程式の課し方」の ⚓2「TOV network map」を押すと、見出し「論理要素 ・ TOV residual loss at collocation points」の部品が開く。教材本文と出典タブはまだ「TOV network map — 半径・中心密度・EOS 特徴から恒星内部プロファイルを出力する構造ネットワークを定義する」と言い、開いた部品はその構造ネットワークを前提として使う別の部品（損失）。

原因: コースは凍結時に部品を agent ID（comp_003）で埋め込み、学習者 API は live の行の legacy_ids で解決する。agent ID は解析 run ごとの連番で、PINN の TeX 版を再解析したら comp_003 が別の部品に割り当たった。

## 発見の観点

再現（reproduction）: 2 人の学生が別々の run で同じ ⚓ を押し、同じずれが出た。経路の追跡（trace_walk）: 教材の応答の参照（comp_001 / comp_003）→ component_context の agent ID 解決 → 再解析の記録。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: 凍結時に ⚓ の参照を agent ID ではなく部品の UUID（または stable_key）で書き、解決時に superseded なら「この部品は解析のやり直しで置き換わりました」と事実文で出す。版をまたぐ付け替えは IK-1012 と同じ受け皿（出典の重なりで 1 対 1 に決まるときだけ）。agent ID を跨 run の鍵に使う経路の棚卸しも要る。

## 一般化

生成物の内部番号（出現順の連番）を、凍結した文書・他層の参照の鍵に使うと、再生成で番号が別の対象に移ったときに黙って別物を指す。型 id-not-stable-across-versions。

面 B 第 6 周の別の観察（審判 doubt・2026-10-27）: 同じ根が学生の画面にも出た。固定コース 1bad7d9f の t13 の ⚓「Radius estimate」（本文 `![[component:comp_011]]`・第 5 周は TeX 版 ca93151e「Radius estimate」に解決）が、PINN の TeX 版の再解析の後は同じ論文の af80e2b1「Inferred EOS meets nuclear, causality, pQCD」に解決され、学生 st-05 の部品の文脈・説明ポップアップ・検証状態の一行がすべて別の部品のものになった（押した札と開いた中身が違う — 学生は「戸惑う」と記した）。runner の番号付け（本文の 2 つ目の埋め込み）は正しく、ハーネスの偽所見ではない。是正案の追加: 旧コースは読み時に凍結時の label と解決先の label が食い違えば着地させず事実文に縮退する。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq6・9・10）/ uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq9）。

第 7 周の再演: 形を変えて STILL（2026-10-28）。st-08 の t11 の ⚓「EOS network map」は、別の部品に着地せず「この部品は解析のやり直しで置き換わりました。対応する新しい部品が一つに決まらないため、ここでは詳しい文脈を表示できません。」で止まった（推測で結ばない — 判断の推奨どおり）。ただ固定コース 1bad7d9f は再凍結していないので全トピックで ⚓ と部品が一致せず（r7_prereqs）、学生は教材の ⚓ から何も読めず次の手も示されない。説明ポップアップも同じ文で、見出しは「説明バージョン」だけ（どの部品のものか名前が無い）。記帳ありの部品の一行（H37）は 7 周目も部品チップでは未到達（わたしの地図の側では読めた）。run: uxsim/runs/c-r25-doubt-r7/20261027T172411Z-57109（seq9・10・16）。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 別の部品を開くことは GONE — ⚓「TOV network map」（comp_003）は「この部品は解析のやり直しで置き換わりました。対応する新しい部品が一つに決まらないため…」で止まり別物を開かない（uxsim/runs/c-astro-r25-share-r7/20261027T171757Z-53326 seq7）。ただし旧い凍結（db_id なし）のコースでは名前一致が 0 で ⚓ が 1 つも開けず、学生に次の手・教員に事実が無い → NEW IK-1051。
