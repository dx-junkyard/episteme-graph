---
id: IK-0968
title: "疑義から作った検証提案は、本文・コーパス外の確認・到達可能性が登録直後から画面のどこにも出ず、台帳を開き直すと提案の行も状態も消える（提案を読む GET が無く、作成応答の proposal_id でその場に描くだけ）。提案直後の疑義の状態行も「未対応」のまま残る"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が主張の台帳で疑義を検証提案へ昇格させ、後日その提案の中身と進み具合を台帳から読み返す"
  layers: [doubt_d, stakes_ledger_sl, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
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
    接続軸 information: verification_proposals 行は proposal・external_check・reachability・status を保存しているが、台帳 GET（routes/doubt.py
    get_ledger_entry）の challenges[] は提案を載せず、提案を読む GET ルートも無い。doubt-atlas.js は作成応答の proposal_id だけで
    renderProposalStatusCard をその場に描き（「GET での一覧 API が無いため…再読込後は表示が引き継がれない（既知の限界・最小実装）」のコメント）、
    カードは状態の一語だけで本文を描かない。構造軸 representation: 提案は疑義の下位の記録なのに、台帳の詳細の表現に居場所が無い。
    提案直後の「未対応」は、昇格成功で疑義が led_to_verification に遷移したのに、画面が台帳を読み直さずボタンだけ無効にするため（同じ根）。
    処理軸 none（各操作は宣言どおり）。統制軸 none（SL-8 の external_check 必須は効いている）。
generalization:
  level: repo_pattern
  general_form: "記録を作る操作の応答だけで画面を組み立て、その記録を読み返す経路を作らないため、作った直後にしか中身と状態が見えず、開き直すと記録が無かったように見える"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    te-03 が原稿スタジオのノード台帳 → 支える主張の台帳 → 疑義 → 検証提案 → 着手まで歩いた（第 4 周で初めて成立）。提案直後の画面は「検証提案（コーパス外の確認あり）:
    検証に着手可能」の 1 行だけで、書いた提案・確認・到達可能性が出ず、疑義の状態行は「未対応」のまま。疑義をもう 1 件残して台帳が読み直されると、疑義 1 は
    「検証提案へ昇格済み」になったが提案のカードは消え、着手済みの状態も追えなかった。前提の地図・未検証合意リストにも提案は現れない。
    run: uxsim/runs/c-r25-doubt-r4/20261024T233017Z-7034（seq8・9・10・12）。
resolution:
  perspective: [carry_through, representation_change]
  note: >-
    台帳 GET の challenges[] に、その疑義から昇格した検証提案（proposal・external_check・reachability_label・status_label・提案者の表示名・提案者の UUID は出さない）を読み時に載せ、status_label も添えた（routes/doubt.py の _proposals_by_challenge）。doubt-atlas.js は疑義の下に提案カードを常設し（proposalCardsHtml）、昇格・状態遷移の後は台帳を読み直す（作成応答の proposal_id でその場に描く renderProposalStatusCard は撤去）。前提の地図へのコース単位の提案一覧は次段。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_stakes_ledger_ui_static.py"
    - "backend/tests/test_doubt_round25_wave4.py"
    - "docs/features/stakes_ledger_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "画面での提案カードの表示と、昇格 → 着手 → 再読込の往復はブラウザ・実 DB で未確認（第 5 周の再演で te-03 の経路を再実行する）"
      - "ハーネスの proposal_card / proposal_status の投影は台帳の proposals へ未追随（sync_keys_wave4.md 面 B）"
related: [IK-0904, IK-0853]
view_of: []
history: []
---

## 課題

症状: 検証提案を登録すると、その場では「検証に着手可能」の 1 行と状態のボタンが出るが、何を提案したか・コーパスの外で何を確かめたか・到達できる見込みは
画面に出ない。台帳を開き直すと提案は消え、疑義の状態が「検証提案へ昇格済み」になるだけで、提案の中身と進み具合は追えない。

原因: 提案の行は保存されているが、台帳の詳細の応答にも別の GET にも載らず、画面は作成応答の id でその場に描くだけ。

## 発見の観点

教員が疑義 → 提案 → 着手 → 別の疑義の記帳と歩いたとき、画面の描き直し（台帳の読み直し）の前後で提案が消えることを見た（経路の追跡・再現）。

## 解決の観点

第 4 波で是正（2026-10-25）。提案を台帳の詳細の一部として返し、画面は読み直しで描く（応答の id に依存しない）。

## 一般化

作成応答で画面を組み立てる最小実装は、読み返す経路を後回しにすると「作ったのに無い」状態を作る。確かめ方は、作成 → 画面の再読込 → 状態遷移 → 再読込で、
記録の中身と状態が毎回同じ面に出ることを見ること。

第 5 周の再演: GONE（2026-10-27）。te-04 が疑義から検証提案を作ると、提案カードが本文・コーパス外の確認・到達可能性・提案者付きで疑義の下に出て、疑義の状態は即時「検証提案へ昇格済み」。着手（PATCH）後も、台帳を開き直した後も、カードは「検証に着手済み」のまま残った。提案者がログイン名なのは H21（表示名）。取り下げた疑義が画面から消える件は別の課題 IK-0998。run: uxsim/runs/c-r25-doubt-r5/20261027T070946Z-33422（seq8・9・14）。
