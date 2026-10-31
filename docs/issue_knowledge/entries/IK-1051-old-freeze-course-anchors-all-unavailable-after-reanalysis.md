---
id: IK-1051
title: "再解析より前に凍結したコースの部品の ⚓ が、IK-1021 の是正（旧い凍結は凍結時の部品名が一致する行だけを採る）の後は再解析で名前の変わった部品を 1 つも開けず、学生には「対応する新しい部品が一つに決まらない」とだけ出て次の手が無く、教員には自分のコースの ⚓ が開けなくなった事実も再凍結の案内も届かない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/knowledge_objects_design.md"
  - "docs/features/component_evidence_redesign.md"
feature_context:
  realizing: "凍結したコースの教材の部品の ⚓ から、学生が部品の文脈・共通部品・説明へ渡る"
  layers: [learner_experience_b, knowledge_objects, versioning_v]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [version]
    governance: [completion]
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
    接続軸 version: コース 1bad7d9f は PINN の再解析より前に凍結され、本文は agent ID（comp_001 / comp_003）で部品を指し db_id を持たない。第 6 波（sync_keys_wave6 面 A）は旧い凍結を「凍結時の部品名が一致する行だけを採る」にしたので、再解析で全部品の名前が変わった論文では一致が 0 になり、学習者 API は available:false（文脈）/ 404（説明）を返す（uxsim/runs/c-astro-r25-share-r7/20261027T171757Z-53326 seq7-9）。st-04 の主張の ⚓ も「この要素の文脈は、このコースの教材からは特定できませんでした」（20261027T172824Z-59013 seq11）。統制軸 completion: 凍結と再解析の間で参照が切れたことを、所有者に知らせ・再凍結を促す段が無い（G層・参照の健全性のどちらも点かない — r7_prereqs.md の実測でも「旧 freeze は再凍結まで従来の解決」）。処理軸 wording: 名前の一致が 0 件の場合も「対応する新しい部品が一つに決まらないため」と言い、原因（凍結時の部品の名前が変わった）と出口が言われない。構造軸 none: 参照を UUID で持つ形（db_id）は新しい凍結から入っている。
generalization:
  level: repo_pattern
  general_form: "再生成で参照先が置き換わる仕組みに、誤った解決を止める是正だけを入れると、旧い参照は一斉に解決不能になり、利用者には袋小路、所有者には沈黙が残る"
pattern: id-not-stable-across-versions
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 第 7 周 st-05 (d) が凍結教材の ⚓ 2 つ（TOV network map / EOS network map）を開き、どちらも置き換わりの事実文で止まった。第 6 周は同じ ⚓ が別の部品を開いていた（IK-1021）。是正の規則（sync_keys_wave6 面 A）と r7_prereqs の実測（固定コースの全トピックで ⚓ と部品が一致しない）を突き合わせた。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    オーナー判断（推奨どおり）: 凍結した本文は変えず、⚓ の参照だけを出典の重なりで 1 対 1 に決まるとき後継へつなぐ — remap.read_time_superseded_successor が再解析時と同じ match_superseded_components を読み時に当てる（旧の側 = 同じ run で置き換わった部品・新の側 = その run が新しく入れた部品・書き込みなし）。決まらなければ reason（not_found / ambiguous）を付け、学生には凍結時の部品名 + 「後継が決まっていません」/「見つかりません」を言い分ける。所有者には原稿スタジオの見出しの frozen_anchor_notice（名前の列挙・［コース内容生成］で凍結し直す案内・自動では凍結し直さない・G層 To-Do なし）。
  landed_in:
    - "backend/core/knowledge_objects/remap.py"
    - "backend/core/frozen_component_refs.py"
    - "backend/core/label_vocab.py"
    - "backend/api/routes/learning.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/features/knowledge_objects_design.md"
    - "backend/tests/test_share_round25_r7_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "参照を結び直す部品を、記帳しない読み時の導出へ広げた。固有の差分: 読み手は element_id_remap に書かない（閲覧で記録を変えない）。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB の固定コース 1bad7d9f で ⚓ が読み時につながる組と残る組の分かれ方（第 8 周の砂場）"
      - "凍結した主張の ⚓（claim）の版をまたぐ解決は対象外"
related: [IK-1021, IK-1012, IK-0920]
view_of: []
history: []
---

## 課題

症状: 第 6 周は固定コースの ⚓「TOV network map」が別の部品を開いた（IK-1021）。第 6 波の是正の後、同じ ⚓ は「この部品は解析のやり直しで置き換わりました。対応する新しい部品が一つに決まらないため、ここでは詳しい文脈を表示できません。」で止まり、同じトピックの ⚓「EOS network map」も同じ文、説明（標準・先生方）は 404 で同じ文、共通部品のタブも出ない。英語の学生 st-04 が開いた主張の ⚓ も「特定できませんでした」。学生には、教材の本文に名前があり出典タブに説明がある部品を開いても何も読めず、次の手（本文・出典タブ・教員への知らせ）が出ない。教員の画面には、自分の凍結コースの ⚓ が開けなくなった事実が出ない。

原因: 旧い凍結（db_id なし）は凍結時の部品名の一致だけで解決する規則にしたため、再解析で名前が全部変わった論文では 1 つも解決しない。決まらない理由が「複数」でも「0」でも同じ文になる。切れた参照を所有者に知らせ、再凍結へ導く段が無い。

## 発見の観点

再現: 前周と同じ ⚓ を同じ順で開き直した。経路の追跡: 学習者 API の応答 → 第 6 波の規則（旧い凍結の名前一致）→ 前提の実測（全トピックで不一致）。

## 解決の観点

第 7 波で是正（下の節）。当初の案:誤った解決を止める（IK-1021）のは正しいので戻さない。旧い凍結にも出典の重なりの規則を当てるか、再凍結を所有者に促すか（両方が望ましい）。学生向けの文は原因と出口を言う。再凍結の判断は所有者の操作で、自動で再凍結しない。

## 一般化

再生成で参照が切れるとき、「間違ったものを指さない」是正と「切れたことを持ち主に知らせ、つなぎ直す手を出す」是正は別の段で、前者だけでは袋小路になる。型 id-not-stable-across-versions。確かめ方は、旧い凍結のまま再解析した論文で全 ⚓ を開き、学生と所有者の両方の画面で次の手が出るかを見ること。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28・オーナー判断どおり）: 旧い凍結の ⚓ も、出典の重なりで 1 対 1 に決まれば読み時に後継へつなぐ（version_note「…出典の重なる新しい部品につないで表示しています。」）。決まらないときは凍結時の部品名と「後継が決まっていません」、重なる新しい部品が 0 件なら「見つかりません」。原稿スタジオの見出しに凍結し直しの案内（自動では凍結し直さない・G層 To-Do なし）（knowledge_objects_design.md §12.6）。

第 8 周の再演: 未評価（2026-10-28）。固定コースを ⚓ のある 6be229e1 に替えたが、st-05 の場面は t13（6be229e1 にも同題名で在る）を開き、t13 には部品の埋め込みが無いので ⚓ の後継・「置き換わりました」の見え方とも当たらなかった（6be229e1 で ⚓ を持つのは t5・t7・t9・t11・t12・t14 — 審判の読み取り）。harness H48。run: uxsim/runs/c-r25-doubt-r8/20261027T224211Z-98239（seq6・7・10）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

未評価。第 8 周の固定コース 6be229e1 の ⚓（comp_005）は再解析されていない PDF 版の部品 904b630c に直接解決した（superseded なし）— 旧い凍結が後継へつながる場面ではない。頭脳の「GONE」はコースが替わったことによる見かけ（harness_fixes_share H48）。原稿スタジオの frozen_anchor_notice も読まれていない。

第 9 周の再演（2026-10-31・固定コース 1bad7d9f＝前周と別のコース）: st-05 の t11 の ⚓「EOS network map（原文）」は部品の文脈・説明とも 200 + 「教材を作ったときの部品『EOS network map』は、再解析で置き換わり、後継が決まっていません。」（予定どおりの見え方・内部 ID なし）。学生には読みに行く先が無い（後継が無い以上は正直）。同じ ⚓ を AI の画面文脈は別の live 部品に解決する — 新規 IK-1075。第 8 周の「未評価」はトピックに ⚓ が無かったため（H48）で、比較対象は無い。run: uxsim/runs/c-r25-doubt-r9/20261031T015310Z-38509（seq7・10）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

STILL（1 対 1 に決まらない側）。固定コースを ⚓ のある TeX 版 1bad7d9f に戻した今周、t11 の ⚓ 3 個（「EOS network map」「TOV network map」・主張の ⚓）はどれも開かない — 部品の ⚓ は「教材を作ったときの部品『…』は、再解析で置き換わり、後継が決まっていません。」の 1 行、説明のボタンも同じ 1 行、主張の ⚓ は「この要素の文脈は、このコースの教材からは特定できませんでした。」。学生には次の手が無く、この部品に説明があるかどうかも分からない。一方、教員の説明カード（te-04）には「「TOV network map」への説明「AI と下書きした説明 — TOV 方程式」」が再解析前の部品に取り残されている事実文が出ている — 教員の説明が付いた部品の ⚓ が学生には 1 行で終わる。run: uxsim/runs/c-astro-r25-share-r9/20261031T015336Z-38765（seq7〜9）・uxsim/runs/c-astro-r25-share-r9/20261031T020038Z-42487（seq11）・uxsim/runs/c-astro-r25-share-r9/20261031T015134Z-37470（seq3）。

## 第 10 周の再演（第 25 周・面 B 第 10 周、2026-10-31・judge_doubt_r10 — 第 9 周と同じ t11・同じ ⚓）

STILL（1 対 1 に決まらない側・見え方は予定どおり）。部品の文脈・説明とも「…再解析で置き換わり、後継が決まっていません。」の 1 行（第 9 周と同一）。AI もいまは同じ事実で答える（IK-1075 GONE）。学生側の困りごと: わたしの地図の同じ論文の「理論の土台」には「直接検証の記録あり」+ 記録された条件があり、⚓ の部品とその土台が同じものか別物かが読めない（st-05「どっちが本当？」— AI は「対象が別々なので矛盾ではありません」と答えた）。run: uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq7・10・16・17）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

STILL（第 9 周と同じコース 1bad7d9f・同じ t11 の ⚓ で比較可）。⚓「EOS network map」「TOV network map」とも「教材を作ったときの部品『…』は、再解析で置き換わり、後継が決まっていません。」の 1 行。学習チャットが「⚓ を開いたまま同じ質問を」と案内しても、その ⚓ は何も渡さない。教員側では同じ部品に江口の説明が「再解析前の説明」として残っている（te-02 の画面）。run: uxsim/runs/c-astro-r25-share-r10/20261031T053407Z-68377（seq11）・uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202（seq7〜9・13）・uxsim/runs/c-astro-r25-share-r10/20261031T053828Z-70459（seq3）。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

第 11 周（judge_doubt_r11）: 後継の決まる側を確認。st-05 の t1 の ⚓4「磁場形態遷移診断」（comp_014）は c0e016d1 に解決され、部品の文脈（位置づけ・下位の根拠・注意・主張・出典論文・同一性の事実文）が開いた。部品の説明の一行は「この内容の検証の記録はまだありません。」（台帳行なし）。記帳ありの部品の一行（H37）は、固定コースの ⚓ に解決される部品のどれにも台帳のスコープが無い（砂場の読み取り）ため 11 周目も観察できない（harness H65）。run: 1116 seq6・7・10。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

STILL（面 A）。1bad7d9f の t11 の ⚓「TOV network map」は「…再解析で置き換わり、後継が決まっていません。」の 1 行（uxsim/runs/c-astro-r25-share-r11/20261031T101956Z-2094 seq12）。この ⚓ の部品 comp_003 は現在の解析で確定リンクを持つ「TOV residual loss at collocation points」で、ここが付け直されない限り学生の共通部品の経路（IK-0920）は固定コースに無い。
