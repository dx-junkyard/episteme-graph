/**
 * 論文レーダー（教材起点の類似論文探索と比較分析）— 教材管理タブの行メニュー
 * 教材行の 📡 アイコン「近い論文を探す」から開くモーダル。
 *
 * 正本: docs/features/paper_radar_design.md（不変条項 PR1〜PR8。PD1〜PD8 を継承）。
 *
 *   PR1 起点は教材1件・候補は読み時導出（このモジュールも候補を保存・キャッシュしない）
 *   PR2 距離は段階ラベルのみ。サーバが付けた distance_label をそのまま描き、
 *       クライアントに閾値表・数値→ラベルの変換表を持たない。ラベルの無い候補は
 *       「距離を判定できませんでした」の区画へ正直に分ける（最遠帯に化けさせない）
 *   PR3 取り込みは既存の弁のみ（/admin/discovery/ingest と /ingest-batch）
 *   PR4 比較文は AI の推定・非保存。出所ラベルとサーバ固定の注意書き（caveat）を
 *       そのまま出す（クライアントで書き換えない・独自の注意書きを発明しない）
 *   PR5 教員の明示操作のみ（検索も比較もボタン押下時だけ。ポーリング・自動検索なし）
 *   PR7 閉世界の正直さ（検索条件と closed_world_note を候補一覧の上に常時出す）
 *
 * ES5 で書く（開発ルール5。admin.js / admin-paper-discovery.js と同じ流儀）。
 * DI は init(deps)。deps = { apiFetch, escHtml, onUploadAccepted, loadDomainOptions }。
 */
(function () {
  "use strict";

  var deps = null;

  // PD3: 供給元の日本語ラベル（キーフレーズチップの title 属性に出す）。
  // 正本は core/label_vocab.py の KEYPHRASE_SOURCE_LABELS（逐語ミラー。
  // test_paper_radar_ui_static.py が一致を固定する）。
  var SOURCE_LABELS = {
    skeleton: "分野の地図の概念から",
    cartridge: "カートリッジ語彙から",
    component: "承認済み理論部品から",
    alias: "教員が確定した別名から",
    concept: "主張の概念から",
    title: "題名から",
    thesis: "論文の中心命題・要旨から",
    manual: "手動"
  };

  // 距離の選択肢。value はサーバの語彙（RADAR_DISTANCES）、label は選択肢の説明文。
  // ここにあるのは「教員が何を選ぶか」の文言であって、候補に付く帯ラベルではない。
  // 帯ラベル（distance_label）はサーバの label_vocab が正本で、クライアントは
  // 受け取った文字列をそのまま描くだけ（PR2）。
  var DISTANCE_OPTIONS = [
    { value: "near", label: "近い（テーマも近い）" },
    { value: "mid", label: "中間" },
    { value: "far", label: "同じ分野の別テーマ" }
  ];

  // seed のカテゴリがどこから来たかを必ず1行で言う（判定不能を偽装しない — PD6）。
  var CATEGORY_SOURCE_NOTICES = {
    arxiv: "arXiv のカテゴリから取得",
    arxiv_inferred:
      "ファイル名から推定した arXiv 情報です（教材の出所としては未登録）。",
    subscription: "分野購読の条件から取得",
    manual: "カテゴリを入力してください"
  };
  var CATEGORY_PRIMARY_TITLE = "論文の主カテゴリ（クリックで外す/戻す）";
  var CATEGORY_TOGGLE_TITLE = "副カテゴリ（クリックで条件に足す/外す）";
  var NO_ARXIV_NOTICE =
    "この教材は arXiv 由来として登録されていないため、カテゴリを指定してください。";

  // arXiv 出所の後付け登録（3段階）。推定はあくまで推定として言い、
  // タイトルが一致しないときは教員に両方を見せてから確認を取る（偽装しない）。
  var PROV_INFERRED_HEAD = "ファイル名から arXiv-";
  var PROV_TITLE_MATCH_TAIL = " と推定し、タイトルが一致しました。";
  var PROV_NOT_FETCHED_TAIL =
    " と推定しましたが、arXiv から論文情報を取得できませんでした。";
  var PROV_MISMATCH_NOTICE =
    " と推定しましたが、タイトルが一致しませんでした。内容を確認してください。";
  var PROV_ARXIV_TITLE_HEAD = "arXiv の論文: ";
  var PROV_DOCUMENT_TITLE_HEAD = "この教材のタイトル: ";
  var PROV_REGISTER_LABEL = "この論文として登録する";
  var PROV_REGISTERED_NOTICE = "この教材の出所として登録しました。";
  var PROV_REGISTER_FAILED_NOTICE = "出所として登録できませんでした。";
  var PROV_CONFIRM_TAIL =
    "この教材の出所として登録しますか？（タイトルは一致していません）";

  // ── 取得する形式（TeX ソース / PDF）──────────────────────────────────
  // arXiv の論文ページ「Access Paper」の選択肢に対応する。既定は TeX ソースで、
  // 数式・節構造が原稿のまま読めるぶん解析の素材として PDF より欠落が少ない。
  // 語彙の正本はサーバ（core/paper_discovery/schema.py の SOURCE_FORMATS）で、
  // ここはその値をそのまま送る担体（クライアントで形式を判定・変換しない）。
  var FORMAT_OPTIONS = [
    { value: "tex", label: "TeX ソース" },
    { value: "pdf", label: "PDF" }
  ];
  var DEFAULT_SOURCE_FORMAT = "tex";
  // PD6 の流儀: 選んだ形式が必ず得られるとは限らない事実を、選ぶ前に言っておく
  // （TeX ソースを投稿していない論文では arXiv が PDF を返す。判定はサーバが
  // 取得したバイト列で行うので、ここで先回りして「TeX です」と名乗らせない）。
  var FORMAT_NOTICE =
    "TeX ソースが公開されていない論文や、TeX ソースとして読めない論文では、PDF を取り込みます。";
  // 生成する言語はキュー登録（6件以上）には載らない事実を、選ぶ前に言っておく。
  var LANGUAGE_NOTICE = "キュー登録（6件以上）の取り込みには適用されません。";
  var GENERATION_LANGUAGES = { ja: true, en: true };

  function selectedLanguage() {
    var node = el("pr-language-select");
    var value = node ? String(node.value || "") : "";
    return GENERATION_LANGUAGES[value] ? value : "";
  }

  // PD1: 取り込み前に必ず出す事実文（何が起きるかを省略しない）。
  // 分野購読モーダルと同一の文言・同一の境界（相互 import できないので同型に書く）。
  var INGEST_NOTICE_TAIL =
    "件の論文を取得し、解析パイプラインを実行します。解析には LLM を使用します。" +
    "解析結果は候補として保存され、公開するまで学習者には表示されません。";
  var SYNC_INGEST_MAX = 5;
  var BATCH_INGEST_MAX = 50;
  var BATCH_NOTICE_TAIL =
    "件をキューに登録します。サーバが順に取得・解析します（1件ずつ・間隔をあけて実行）。" +
    "進捗は教材一覧と分野購読モーダルの取り込みキュー欄で確認できます。解析には LLM を使用します。" +
    "解析結果は候補として保存され、公開するまで学習者には表示されません。";
  var BATCH_LIMIT_NOTICE_HEAD = "一度にキューへ登録できるのは ";
  var BATCH_LIMIT_NOTICE_TAIL = " 件までです。選択を減らしてください。";
  var BATCH_QUEUED_NOTICE_TAIL =
    "件をキューに登録しました。進捗は教材一覧で確認できます。";

  // UF1 継承: 許可ドメイン未設定は fail-closed の表示（強制はサーバ側）。
  var DOMAIN_BLOCKED_NOTICE =
    "取得先ドメインが許可されていません。システム管理者が「AIモデル」タブで設定できます。";
  var DOMAIN_UNKNOWN_NOTICE =
    "許可ドメインを確認できませんでした。取り込みはサーバ側で拒否される場合があります。";

  // ── arXiv 側のアクセス制限（HTTP 429）────────────────────────────────
  // 「混んでいる」ではなく「向こうが止めている」と読める見出しだけをフロントが持ち、
  // 理由の本文はサーバの事実文（arxiv_blocked_note / note / detail）をそのまま出す。
  // 見出しに数字・待ち時間を書かない（カウントダウン・自動再試行もしない — PR5）。
  var ARXIV_BLOCKED_HEAD = "arXiv からのアクセス制限中";
  var ARXIV_UNREACHABLE_HEAD = "arXiv に問い合わせできませんでした";
  // 検索ボタンの横に出す静的な一行（本文はバナー側のサーバ事実文が持つ）。
  var ARXIV_BLOCKED_SEARCH_HINT = "制限中は arXiv へ問い合わせません。";
  var SUBSCRIPTION_UNCHANGED_HINT = "この画面から分野購読の条件は変更されません。";

  // PD6 / PR7: 空一覧を「この論文に近い論文が無い」と読ませない。
  var EMPTY_RESULT_NOTICE =
    "この検索条件では候補が見つかりませんでした。条件を変えると別の論文が見つかることがあります。";
  var NOT_SEARCHED_NOTICE =
    "距離と条件を確認して「この条件で検索」を押してください。";
  var SOURCE_URL_NOTICE =
    "「取り込み済み」はURL経由で取り込まれた論文のみ判定できます。";
  // 取り込み済みであることは一等地（タイトル行）で言う。灰色の小さな行に混ぜると
  // 一覧を流し読みしたときに新着と区別できない。
  var INGESTED_BADGE_LABEL = "取り込み済み";
  var INGESTED_STATUS_LINE = "この論文はすでに教材として取り込まれています。";
  var INGESTED_BADGE_STYLE =
    "color:#1b6b5a;background:rgba(27,107,90,0.12);border:1px solid rgba(27,107,90,0.28)";

  // PR2: 帯の見出し。帯ラベルそのものはサーバの文字列を差し込むだけ。
  var DISTANCE_CHIP_HEAD = "距離: ";
  var OTHER_BAND_HEAD = "他の距離の候補 ";
  var UNMEASURED_HEAD = "距離を判定できませんでした";
  var BANDING_UNAVAILABLE_NOTICE =
    "距離の判定はできませんでした。候補を新着順のまま表示しています。";

  // ── 重なり・差分の表示（VA層の着地予測と関係注記）────────────────────────
  // PR2 / VA2 を継承: サーバが確定したラベル・部品名をそのまま描くだけで、
  // 近さの数値も閾値もクライアントに持たない。推定であることは凡例で一度だけ
  // 宣言し、各行では短いタグ〈推定〉で思い出させる（断定に化けさせない）。
  var RELATION_LEGEND =
    "≒ 重なり = 既存の部品・語彙と同じ内容を扱っていそう ／ " +
    "✚ 新しい面 = 起点論文が触れていない主題に近そう — " +
    "いずれもタイトル・要旨からの推定です（取り込み後の解析・教員確認で確定します）";
  var LANDING_HEAD = "取り込むと: ";
  var LANDING_TAIL = " の近くに落ちそうです";
  var OVERLAP_CHIP_HEAD = "≒ 部品「";
  var OVERLAP_CHIP_TAIL = "」と重なりそう";
  var FACET_CHIP_HEAD = "✚ 「";
  var FACET_CHIP_TAIL = "」の近く（起点論文は未言及）";
  var ESTIMATE_TAG = "〈推定〉";
  var CHIP_MORE_LABEL = "ほか";
  // 一覧の可読性のための表示上限。落とした分は「ほか」で存在だけ残す（黙って捨てない）。
  var OVERLAP_CHIP_MAX = 3;
  var FACET_CHIP_MAX = 2;
  // VA4 継承の縮退表示: 関係の下地はあるのに、この候補だけ測れなかったことを
  // 正直に言う。下地ごと無い（available でない）ときは1行も出さない
  // （全候補に同じ注記が並ぶノイズを避ける）。
  var RELATION_UNMEASURED_NOTICE =
    "着地・重なりの近さは測定できませんでした（このまま取り込みできます）";

  // 重なり=緑系 / 新しい面=紫系。styles.css の --color-text-success（#5dcaa5）は
  // 白地の 11px 文字には薄すぎるため、地色付きピル用に濃度を落とした固定色を使う
  // （どちらも中間色 + 半透明地なので明暗どちらの地でも読める）。
  var OVERLAP_CHIP_STYLE =
    "color:#1b6b5a;background:rgba(27,107,90,0.12);border:1px solid rgba(27,107,90,0.28)";
  var FACET_CHIP_STYLE =
    "color:#6d4fa3;background:rgba(109,79,163,0.12);border:1px solid rgba(109,79,163,0.28)";

  // PR4: 比較分析。見出しに「AI 推定」と明記し、caveat はサーバの文字列をそのまま出す。
  var COMPARE_MAX = 10;
  var COMPARE_HEAD = "起点論文との違い（AI 推定）";
  var COMPARE_COMMON_HEAD = "共通点: ";
  var COMPARE_OVERLAP_HEAD = "≒ 重なっていそうな要素 — 別の表現・文脈で同じ内容";
  var COMPARE_DIFF_HEAD = "✚ 異なっていそうな要素 — 関連するが別の知識";
  // 逐語引用は畳んでおく（省かない。読みたい教員だけが開く）。
  var COMPARE_QUOTE_SUMMARY = "根拠（要旨からの逐語引用）";
  var COMPARE_PART_HEAD = "部品「";
  var COMPARE_PART_TAIL = "」";
  var COMPARE_EMPTY_NOTICE = "比較できる違いは返されませんでした。";
  var COMPARE_NONE_SELECTED_NOTICE =
    "比較する論文を選択してください（チェックボックスで選びます）。";
  var COMPARE_LIMIT_NOTICE_HEAD = "一度に比較できるのは ";
  var COMPARE_LIMIT_NOTICE_TAIL = " 件までです。選択を減らしてください。";
  // 関係の種類（relation_kind）。ラベルはサーバの compare.RELATION_LABELS が正本で、
  // ここには日本語を持たない（relation_label を素通しする — 設計書 §18.4 / §19 D）。
  var RELATION_NONE_KIND = "no_notable_common_ground";
  // 直前の検索で、取得した候補のどれにも当たらなかった有効な語のチップに添える印（§20 A）。
  // 外した語の打ち消し表示とは別（外したのではなく、当たらなかった）。
  var KEYPHRASE_MISS_MARK = "当たらず";
  var KEYPHRASE_MISS_TITLE = "直前の検索で、取得した候補のどれにも当たらなかった語です。";
  // 直前の検索で網を広げる語と判定された有効な語のチップに添える印（§20 B / §23.5）。当たらずの印と
  // 同じ添え方で、外すかどうかは教員のクリック（自動では外さない — PD3）。チップの title は
  // サーバの widens_note（無ければ widening の既定文）を素通しする。
  var KEYPHRASE_WIDENS_MARK = "網を広げる";
  var KEYPHRASE_WIDENS_TITLE = "直前の検索で、取得した候補のすべてに当たった語です。";
  var RELATION_CHIP_STYLE =
    "color:var(--color-text-secondary);background:var(--color-bg-tertiary);border:1px solid var(--color-border)";

  // ── 取り込み済み・受理直後の候補から先へ進む導線（§19 C・IK-0700 / IK-0701）────
  // 教材一覧の行・グラフレビュー・位置づけ・この論文を起点に探す（連鎖）。解析が終わって
  // いないあいだは押せない理由を title に書く（ボタンを黙って隠さない）。解析の完了は
  // 教材一覧の再読込（既存のタスク追跡）で知る — この画面からポーリングしない（PR5）。
  var INGESTED_OPEN_MATERIAL_LABEL = "この教材の行へ";
  var INGESTED_GRAPH_REVIEW_LABEL = "グラフレビュー";
  var INGESTED_LANDSCAPE_LABEL = "位置づけ（分野マップ）";
  var CHAIN_SEED_LABEL = "この論文を起点に探す";
  var INGESTED_NOT_READY_REASON = "解析が終わると使えます（進捗は教材一覧で確認できます）。";
  var INGESTED_FAILED_REASON = "解析が失敗しています。教材一覧の行から再実行できます。";
  var ACCEPTED_CHAIN_NOTICE = "解析が終わったらこの論文を起点に探せます。";
  var ANALYSIS_COMPLETED = "completed";

  // ── 取り込み済み候補の配置の対比・構造照合の成立条件（§21 B / C）────────
  // 項目と出所ラベル（「AIによる推定（未確認）」/「教員確認済み」）はサーバの値を素通し。
  // 件数・weight は描かない（PR2 / LS5）。
  var CONTRAST_SHARED_HEAD = "起点と同じ面: ";
  var CONTRAST_SEED_ONLY_HEAD = "起点にだけある面: ";
  var CONTRAST_CANDIDATE_ONLY_HEAD = "この論文にだけある面: ";
  var CONTRAST_NONE = "なし";
  // 対比の粒度（§22 C）。骨格の領域とその中の概念を分けて示す。
  var CONTRAST_LAYER_REGION = "〔領域〕";
  var CONTRAST_LAYER_CONCEPT = "〔概念〕";
  var CONTRAST_VERSION_HEAD = "分野の地図 版";
  // 連鎖（§21 D）。前の起点は JS 内の呼び出し履歴だけで描く（保存しない）。
  var PREVIOUS_SEED_HEAD = "前の起点: ";
  var PREVIOUS_SEED_TAIL = "（この論文はその『近い論文』として取り込まれました）";

  var state = {
    open: false,
    documentId: "",
    title: "",
    seed: null,
    seedError: "",
    // arXiv 出所の後付け登録。推定は自動で1回だけ試し（タイトル一致時のみ）、
    // 一致しないときは教員の明示確認を経てからでないと登録しない。
    provenance: null,
    provenanceAutoAttempted: false,
    provenanceNotice: "",
    distance: "near",
    categories: [],
    categoriesSource: "",
    keyphrases: [],
    candidates: [],
    query: "",
    closedWorldNote: "",
    // キーフレーズの供給の事実文（seed.keyphrase_supply.note）と、キーフレーズ無しで
    // 検索した事実文（search の keyphrase_note）。どちらもサーバ文言の素通し（§16）。
    keyphraseSupplyNote: "",
    keyphraseNote: "",
    total: null,
    banding: null,
    // 重なり・着地の下地が使えたか（サーバの relation_context）。使えなかったときは
    // 未測定の注記も出さない（無い下地の説明を候補ごとに繰り返さない）。
    relationContext: null,
    // 近さの根拠が出ない理由の事実文（サーバの facts の素通し — §19 A）。
    facts: [],
    // 直前の検索で各キーフレーズが候補に当たったか（サーバの keyphrase_hits — 真偽だけ・
    // 件数なし = PD4）と、当たり外れの事実文（サーバ文言の素通し — §20 A / B）。
    keyphraseHits: {},
    keyphraseHitsNote: "",
    keyphraseWideningNote: "",
    // 帯と関係の種類の読み方（サーバの reading_note）。比較結果（関係の種類）が描かれた
    // ときだけ #pr-facts に出す（帯と関係ラベルが同時に出るときだけ — §20 D）。
    readingNote: "",
    // 同期取り込みを受理した候補（arxiv_id → {material_id, title}）。教材一覧の再読込で
    // 解析状態を引き直し、完了したら連鎖のボタンを押せるようにする（§19 C）。
    acceptedById: {},
    searched: false,
    searching: false,
    // 教員が開いた帯は開いたまま保つ（比較結果の描き直しで畳まない）。
    openBands: {},
    selected: {},
    // 取得する形式（tex / pdf）。既定は TeX ソース。モーダルを開くたびに既定へ戻す
    // （前回の選択をサーバにもブラウザにも保存しない — PR1 の「保存しない」に揃える）。
    sourceFormat: DEFAULT_SOURCE_FORMAT,
    ingesting: false,
    domainAllowed: null,
    // arXiv 側がこの環境からの問い合わせを制限しているか（サーバの arxiv_blocked）。
    // 解除はサーバが明示的に false を返したときだけ（減る方向の上書きをしない）。
    arxivBlocked: false,
    arxivBlockedNote: "",
    arxivBlockedKind: "",
    // 比較結果はレスポンス限り（DB に保存しない — PR4）。モーダルを閉じれば消える。
    comparing: false,
    compareById: {},
    compareNotes: []
  };

  // ── 小道具 ────────────────────────────────────────────────────────────
  function esc(text) {
    if (deps && deps.escHtml) return deps.escHtml(text == null ? "" : text);
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function api(path, options) {
    if (deps && deps.apiFetch) return deps.apiFetch(path, options);
    return Promise.reject(new Error("apiFetch is not injected"));
  }

  function el(id) {
    return document.getElementById(id);
  }

  // サーバの detail（事実文）をそのまま見せる。取れないときだけ既定文へ縮退する。
  function detailText(err, fallback) {
    if (!err) return fallback;
    var detail = err.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && detail.length && typeof detail[0] === "object" && detail[0].msg) {
      return String(detail[0].msg);
    }
    if (err.message && err.message !== "Unauthorized") return String(err.message);
    return fallback;
  }

  function rejectWithBody(res) {
    return res
      .json()
      .catch(function () {
        return {};
      })
      .then(function (body) {
        var payload = body && typeof body === "object" ? body : {};
        // arXiv へ届かなかった回（502）を 4xx と区別するために状態コードだけ添える。
        // 文言の判定には使わない（日本語の detail を照合しない）。
        payload.http_status = res.status;
        throw payload;
      });
  }

  function setNotice(message, isError) {
    var node = el("pr-notice");
    if (!node) return;
    node.textContent = message || "";
    node.style.color = isError
      ? "var(--color-text-danger, #e53935)"
      : "var(--color-text-secondary)";
  }

  function indexOfText(list, text) {
    for (var i = 0; i < list.length; i++) {
      var entry = list[i];
      var value = typeof entry === "string" ? entry : entry && entry.text;
      if (value === text) return i;
    }
    return -1;
  }

  function enabledKeyphrases() {
    var out = [];
    for (var i = 0; i < state.keyphrases.length; i++) {
      if (state.keyphrases[i] && state.keyphrases[i].enabled) {
        out.push(state.keyphrases[i].text);
      }
    }
    return out;
  }

  function bindAll(root, selector, handler) {
    var nodes = root.querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("click", function () {
        handler(this);
      });
    }
  }

  function bindEnter(inputId, handler) {
    var input = el(inputId);
    if (!input) return;
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        handler();
      }
    });
  }

  // 既存アップロード欄と同じ入力から解析オプションを引き継ぐ（第2の設定 UI を作らない）。
  // ── 解析の分野（cartridge_id）────────────────────────────────────────
  // 取り込んだ論文の解析に使う分野。意味は教材アップロードの「分野」行と同一
  // （「指定しない」= 分野固有の語彙・検証を注入しない分野中立の解析）。
  // 選択肢は admin.js の loadDomainOptions()（教材アップロードと同じ合成の1本）を
  // DI で受け取り、廃止済み（retired）の分野は出さない。値はそのまま送り、語彙外は
  // サーバが 422 で拒否する（フロントの絞り込みを信頼しない）。
  // 既定は seed 教材の採用 run の分野（教材一覧の analysis_cartridge_id）。
  var INGEST_DOMAIN_NOTICE =
    "「指定しない」は、分野固有の語彙・検証を使わずに解析します。";

  function ingestDomainRowHtml() {
    return (
      '<div id="pr-ingest-domain-row" data-ui-anchor="materials.radar-ingest-domain" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px">' +
        '<label for="pr-ingest-domain-select" style="font-size:12px;color:var(--color-text-secondary)">解析の分野</label>' +
        '<select id="pr-ingest-domain-select" style="font-size:12px">' +
          '<option value="" selected>指定しない</option>' +
        "</select>" +
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">' +
          esc(INGEST_DOMAIN_NOTICE) +
        "</span>" +
      "</div>"
    );
  }

  // 選択肢を読み込み、preferredKey が選択肢に実在すればそれを既定にする
  // （無ければ「指定しない」のまま）。取得失敗時は「指定しない」だけを残す。
  function loadIngestDomainOptions(preferredKey) {
    state.ingestDomainKeys = {};
    state.ingestDomainPreferred = preferredKey || "";
    if (!deps || !deps.loadDomainOptions) return;
    deps.loadDomainOptions()
      .then(function (opts) {
        var node = el("pr-ingest-domain-select");
        if (!node || !state.open) return;
        var keys = (opts && opts.keys) || [];
        var names = (opts && opts.names) || {};
        var lifecycles = (opts && opts.lifecycles) || {};
        for (var i = 0; i < keys.length; i++) {
          var key = keys[i];
          if (!key || lifecycles[key] === "retired") continue;
          var option = document.createElement("option");
          option.value = key;
          option.textContent = names[key] ? names[key] + " (" + key + ")" : key;
          node.appendChild(option);
          state.ingestDomainKeys[key] = true;
        }
        applyIngestDomainDefault(state.ingestDomainPreferred);
      })
      .catch(function () { /* 選択肢が読めなければ「指定しない」のまま */ });
  }

  function applyIngestDomainDefault(preferredKey) {
    state.ingestDomainPreferred = preferredKey || "";
    var node = el("pr-ingest-domain-select");
    if (!node) return;
    var keys = state.ingestDomainKeys || {};
    node.value = preferredKey && keys[preferredKey] ? preferredKey : "";
  }

  // 送る値（「指定しない」は ""）。
  function selectedIngestDomain() {
    var node = el("pr-ingest-domain-select");
    return node ? String(node.value || "") : "";
  }

  function uploadOptions() {
    var checkbox = el("upload-analyze-images");
    var analyzeImages = !!(checkbox && checkbox.checked);
    var payload = { analyze_images: analyzeImages };
    if (window.AdminLlmModels && window.AdminLlmModels.getUploadModels) {
      var models = window.AdminLlmModels.getUploadModels(analyzeImages);
      if (models) payload.models = models;
    }
    return payload;
  }

  function seedDomainKey() {
    return (state.seed && state.seed.domain_key) || "";
  }

  // ── モーダルの骨組み ──────────────────────────────────────────────────
  function close() {
    var overlay = el("paper-radar-modal");
    if (overlay) overlay.remove();
    state.open = false;
  }

  function distanceRadiosHtml() {
    var html = "";
    for (var i = 0; i < DISTANCE_OPTIONS.length; i++) {
      var option = DISTANCE_OPTIONS[i];
      html +=
        '<label style="font-size:12px;color:var(--color-text-primary);display:inline-flex;align-items:center;gap:4px">' +
        '<input type="radio" name="pr-distance-choice" class="pr-distance-choice" value="' +
        esc(option.value) +
        '"' +
        (option.value === state.distance ? " checked" : "") +
        ">" +
        esc(option.label) +
        "</label>";
    }
    return html;
  }

  function formatRadiosHtml() {
    var html = "";
    for (var i = 0; i < FORMAT_OPTIONS.length; i++) {
      var option = FORMAT_OPTIONS[i];
      html +=
        '<label style="font-size:12px;color:var(--color-text-primary);display:inline-flex;align-items:center;gap:4px">' +
        '<input type="radio" name="pr-format-choice" class="pr-format-choice" value="' +
        esc(option.value) +
        '"' +
        (option.value === state.sourceFormat ? " checked" : "") +
        ">" +
        esc(option.label) +
        "</label>";
    }
    return html;
  }

  function modalHtml() {
    return (
      // overflow-y:auto は固定区画（seed・検索条件・フッター）の合計が 88vh を超える
      // 低い画面でのフォールバック。通常は #pr-results の内部スクロールだけが効く。
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:640px;max-width:860px;width:88vw;max-height:88vh;display:flex;flex-direction:column;overflow-y:auto">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">近い論文を探す</h3>' +
          '<button type="button" id="pr-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        "</div>" +
        '<div id="pr-notice" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:8px"></div>' +

        // ① seed ヘッダ（起点の論文が何かを常に見せる）
        '<div id="pr-seed" style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:8px 10px;margin-bottom:10px"></div>' +

        // ② 距離セレクタ + ③ 検索条件
        '<div style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:10px;margin-bottom:10px">' +
          // arXiv 側のアクセス制限は条件欄の先頭に出す（灰色の事実行に紛れさせない）。
          // 中身は renderArxivBlocked が描く。制限が無いときは領域ごと出さない。
          '<div id="pr-arxiv-blocked" style="display:none;border:1px solid var(--color-text-danger, #e24b4a);' +
            'background:rgba(226,75,74,0.08);border-radius:6px;padding:8px 10px;margin-bottom:8px"></div>' +
          '<div id="pr-distance" data-ui-anchor="materials.radar-distance" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:8px">' +
            '<span style="font-size:12px;color:var(--color-text-secondary)">距離</span>' +
            distanceRadiosHtml() +
            '<span style="font-size:11.5px;color:var(--color-text-tertiary)">距離は次の検索から適用されます。</span>' +
          "</div>" +

          '<div style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">arXiv カテゴリ</div>' +
            '<div id="pr-categories" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div id="pr-category-source" style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:4px"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pr-category-input" placeholder="astro-ph.CO" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:170px">' +
              '<button type="button" id="pr-category-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          // キーフレーズは「近い」でのみ効く条件（mid / far はカテゴリだけで網を張る）。
          '<div id="pr-keyphrase-section" style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">キーフレーズ<span style="color:var(--color-text-tertiary);font-size:11px">（クリックで外す/戻す。外したものも条件として保持されます）</span></div>' +
            '<div id="pr-keyphrases" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div id="pr-keyphrase-supply-note" style="font-size:11.5px;color:var(--color-text-secondary);margin-bottom:4px"></div>' +
            // 直前の検索で当たらなかった語・全件に当たった語（サーバ文言の素通し — §20 A / B）。
            '<div id="pr-keyphrase-hits-note" style="font-size:11.5px;color:var(--color-text-secondary);margin-bottom:4px;display:none"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pr-keyphrase-input" placeholder="キーフレーズを追加" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:220px">' +
              '<button type="button" id="pr-keyphrase-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
            '<button type="button" id="pr-search-btn" data-ui-anchor="materials.radar-search" class="admin-action-btn">この条件で検索</button>' +
            '<span id="pr-search-hint" style="font-size:11.5px;color:var(--color-text-tertiary)">' +
              esc(SUBSCRIPTION_UNCHANGED_HINT) +
            "</span>" +
          "</div>" +
        "</div>" +

        // ④ 候補一覧（PR7: 検索条件と閉世界注記を常に上に出す）
        '<div id="pr-query-note" style="font-size:11.5px;color:var(--color-text-tertiary);padding-bottom:4px"></div>' +
        // 近さの根拠が出ない理由（地図・承認済み部品が無い等）。サーバの事実文の素通し（§19 A）。
        '<div id="pr-facts" style="font-size:11.5px;color:var(--color-text-secondary);padding-bottom:4px;display:none"></div>' +
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);border-bottom:1px solid var(--color-border-tertiary);padding-bottom:6px;margin-bottom:6px">' +
          esc(SOURCE_URL_NOTICE) +
        "</div>" +
        '<div id="pr-results" style="overflow-y:auto;flex:1;min-height:160px"></div>' +

        // ⑤ 分析・取り込みのフッター行（承認済みモックの foot-row。比較と取り込みは
        //    どちらも「選択した候補への操作」なので一覧の下に並べる）
        '<div style="border-top:1px solid var(--color-border-tertiary);margin-top:10px;padding-top:10px">' +
          // 取得する形式は「選択した論文をどう取り込むか」なので取り込みの区画に置く。
          '<div id="pr-format" data-ui-anchor="materials.radar-format" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:6px">' +
            '<span style="font-size:12px;color:var(--color-text-secondary)">取得する形式</span>' +
            formatRadiosHtml() +
            '<span style="font-size:11.5px;color:var(--color-text-tertiary)">' +
              esc(FORMAT_NOTICE) +
            "</span>" +
          "</div>" +
          // 生成する言語（run options.language）。既定は「指定しない」。同期の取り込み（5件以下）
          // にだけ効く — キュー登録（6件以上）は言語を持たない（migration なしの非スコープ）。
          '<div id="pr-language-row" data-ui-anchor="materials.radar-language" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px">' +
            '<label for="pr-language-select" style="font-size:12px;color:var(--color-text-secondary)">生成する言語</label>' +
            '<select id="pr-language-select" style="font-size:12px">' +
              '<option value="" selected>指定しない</option>' +
              '<option value="ja">日本語</option>' +
              '<option value="en">英語</option>' +
            '</select>' +
            '<span style="font-size:11.5px;color:var(--color-text-tertiary)">' +
              esc(LANGUAGE_NOTICE) +
            "</span>" +
          "</div>" +
          // 解析の分野（cartridge_id）。同期・キュー登録の両方に効く（キュー行が運ぶ — migration 090）。
          ingestDomainRowHtml() +
          '<div id="pr-ingest-summary" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
          '<div id="pr-ingest-result" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
          '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
            '<button type="button" id="pr-compare-btn" data-ui-anchor="materials.radar-compare" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px" disabled>違いを分析</button>' +
            '<span id="pr-compare-note" style="font-size:11.5px;color:var(--color-text-tertiary)"></span>' +
            '<span style="flex:1"></span>' +
            '<button type="button" id="pr-cancel" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">閉じる</button>' +
            '<button type="button" id="pr-ingest-btn" data-ui-anchor="materials.radar-ingest" class="admin-action-btn" disabled>選択した論文を取り込む</button>' +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function openModal(documentId, title, options) {
    if (!deps) return;
    if (!documentId) return;
    close();
    state.open = true;
    // 取り込みの「解析の分野」の既定（seed 教材の採用 run の分野。空 = 「指定しない」）。
    state.seedCartridgeId = (options && options.cartridgeId) ? String(options.cartridgeId) : "";
    state.documentId = String(documentId);
    state.title = title || "";
    // 連鎖で開き直したときの前の起点（呼び出し側が渡したときだけ・保存しない — §21 D）。
    state.previousSeedTitle = (options && options.previousSeedTitle) ? String(options.previousSeedTitle) : "";
    state.seed = null;
    state.seedError = "";
    state.provenance = null;
    state.provenanceAutoAttempted = false;
    state.provenanceNotice = "";
    state.distance = "near";
    state.categories = [];
    state.categoriesSource = "";
    state.keyphrases = [];
    state.candidates = [];
    state.query = "";
    state.closedWorldNote = "";
    state.keyphraseSupplyNote = "";
    state.keyphraseNote = "";
    state.total = null;
    state.banding = null;
    state.relationContext = null;
    state.facts = [];
    state.keyphraseHits = {};
    state.keyphraseHitsNote = "";
    state.keyphraseWideningNote = "";
    state.readingNote = "";
    state.acceptedById = {};
    state.searched = false;
    state.searching = false;
    state.openBands = {};
    state.selected = {};
    state.sourceFormat = DEFAULT_SOURCE_FORMAT;
    state.ingesting = false;
    state.domainAllowed = null;
    state.arxivBlocked = false;
    state.arxivBlockedNote = "";
    state.arxivBlockedKind = "";
    state.comparing = false;
    state.compareById = {};
    state.compareNotes = [];

    var overlay = document.createElement("div");
    overlay.id = "paper-radar-modal";
    overlay.setAttribute("data-ui-anchor", "materials.radar-modal");
    overlay.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML = modalHtml();
    document.body.appendChild(overlay);
    loadIngestDomainOptions(state.seedCartridgeId);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    el("pr-close").addEventListener("click", close);
    el("pr-cancel").addEventListener("click", close);
    el("pr-search-btn").addEventListener("click", runSearch);
    el("pr-compare-btn").addEventListener("click", runCompare);
    el("pr-ingest-btn").addEventListener("click", runIngest);
    el("pr-category-add").addEventListener("click", addCategoryFromInput);
    el("pr-keyphrase-add").addEventListener("click", addKeyphraseFromInput);
    bindEnter("pr-category-input", addCategoryFromInput);
    bindEnter("pr-keyphrase-input", addKeyphraseFromInput);
    bindDistanceChoices();
    bindFormatChoices();

    renderSeed();
    renderArxivBlocked();
    renderSearchHint();
    renderCategoryChips();
    renderKeyphraseChips();
    renderKeyphraseSection();
    renderQueryNote();
    renderCandidates();
    renderCompareControl();
    renderIngestSummary();

    // PR5 / PD8: 開いたときに走るのは seed の解決と許可ドメイン確認だけ。
    // 検索・比較は教員がボタンを押したときにしか実行しない。
    loadSeed();
    checkAllowedDomains();
  }

  // 距離の切り替えでは検索し直さない（PR5: 明示操作のみ）。
  function bindDistanceChoices() {
    var container = el("pr-distance");
    if (!container) return;
    var nodes = container.querySelectorAll(".pr-distance-choice");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("change", function () {
        if (!this.checked) return;
        state.distance = this.value;
        renderKeyphraseSection();
      });
    }
  }

  // 形式の切り替えは取り込み時にだけ効く（検索し直さない — PR5: 明示操作のみ）。
  function bindFormatChoices() {
    var container = el("pr-format");
    if (!container) return;
    var nodes = container.querySelectorAll(".pr-format-choice");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("change", function () {
        if (!this.checked) return;
        state.sourceFormat = this.value;
      });
    }
  }

  // ── arXiv 側のアクセス制限（HTTP 429）の表示 ──────────────────────────
  // サーバが「arXiv がこの環境からの問い合わせを制限している」と言ったときだけ、
  // 条件欄の先頭に枠付きのバナーを出す。見出しはフロントの静的文、理由の本文は
  // サーバの事実文をそのまま描く（言い換えない・待ち時間を計算しない）。
  //
  // 解除は **サーバが明示的に arxiv_blocked: false を返したとき** だけ。キーが無い
  // レスポンス（確かめ直していない回）で黙って通常表示へ戻さない — seed.note と
  // 同じ「減る方向の上書きをしない」規律。
  function applyArxivBlocked(flag, note, kind) {
    if (flag === true) {
      state.arxivBlocked = true;
      state.arxivBlockedKind = kind === "unreachable" ? "unreachable" : "blocked";
      if (note) state.arxivBlockedNote = String(note);
    } else if (flag === false) {
      state.arxivBlocked = false;
      state.arxivBlockedKind = "";
      state.arxivBlockedNote = "";
    }
    renderArxivBlocked();
    renderSearchHint();
  }

  // ブロックの本文はサーバの文字列だけから採る（フロントで理由を発明しない）。
  function blockedNoteFrom(data) {
    if (!data) return "";
    if (data.seed && data.seed.arxiv_blocked_note) {
      return String(data.seed.arxiv_blocked_note);
    }
    if (data.note) return String(data.note);
    if (data.notes && data.notes.length) return String(data.notes[0]);
    return "";
  }

  // 502 は「arXiv へ届かなかった」回。detail の日本語を照合せず状態コードで扱う。
  function applyRequestFailure(err) {
    if (!err || err.http_status !== 502) return;
    applyArxivBlocked(true, detailText(err, ""), "unreachable");
  }

  function renderArxivBlocked() {
    var node = el("pr-arxiv-blocked");
    if (!node) return;
    if (!state.arxivBlocked) {
      node.style.display = "none";
      node.innerHTML = "";
      return;
    }
    var head =
      state.arxivBlockedKind === "unreachable"
        ? ARXIV_UNREACHABLE_HEAD
        : ARXIV_BLOCKED_HEAD;
    node.style.display = "block";
    node.innerHTML =
      '<div id="pr-arxiv-blocked-head" style="font-size:12.5px;font-weight:600;color:var(--color-text-danger, #e24b4a)">' +
      esc(head) +
      "</div>" +
      (state.arxivBlockedNote
        ? '<div id="pr-arxiv-blocked-note" style="font-size:12px;color:var(--color-text-primary);margin-top:3px">' +
          esc(state.arxivBlockedNote) +
          "</div>"
        : "");
  }

  // 制限中もボタンは押せる（サーバは 200 で事実文を返すだけ・arXiv へは出ない）。
  function renderSearchHint() {
    var node = el("pr-search-hint");
    if (!node) return;
    node.textContent = state.arxivBlocked
      ? ARXIV_BLOCKED_SEARCH_HINT
      : SUBSCRIPTION_UNCHANGED_HINT;
  }

  // ── seed（起点論文）────────────────────────────────────────────────────
  function loadSeed() {
    var documentId = state.documentId;
    api(
      "/admin/discovery/radar/seed?document_ref=" + encodeURIComponent(documentId)
    )
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        if (state.documentId !== documentId) return; // 別教材に切り替え済みなら破棄
        applySeed((data && data.seed) || null);
      })
      .catch(function (err) {
        if (state.documentId !== documentId) return;
        state.seed = null;
        state.seedError = detailText(err, "起点の論文を読み込めませんでした。");
        renderSeed();
        setNotice(state.seedError, true);
      });
  }

  function applySeed(seed) {
    state.seed = seed;
    state.seedError = "";
    state.provenance = (seed && seed.provenance) || null;
    state.categories = categoryChipsFromSeed(seed);
    state.categoriesSource = (seed && seed.categories_source) || "";
    state.keyphrases = [];
    state.keyphraseSupplyNote =
      (seed && seed.keyphrase_supply && seed.keyphrase_supply.note) || "";
    var candidates = (seed && seed.keyphrase_candidates) || [];
    for (var i = 0; i < candidates.length; i++) {
      var entry = candidates[i] || {};
      if (!entry.text) continue;
      state.keyphrases.push({
        text: entry.text,
        source: entry.source || "manual",
        enabled: entry.enabled !== false
      });
    }
    renderSeed();
    applyArxivBlocked(
      seed ? seed.arxiv_blocked : undefined,
      (seed && seed.arxiv_blocked_note) || "",
      "blocked"
    );
    renderCategoryChips();
    renderKeyphraseChips();
    renderKeyphraseSection();
    renderQueryNote();
    renderCandidates();
  }

  function renderSeed() {
    var node = el("pr-seed");
    if (!node) return;
    var seed = state.seed;
    // 教材名がファイル名のままのときはサーバが display_title（arXiv の題名）を返す（§19 E）。
    var title = (seed && (seed.display_title || seed.title)) || state.title || "";
    var html =
      '<div style="font-size:13px;color:var(--color-text-primary)">起点の論文: ' +
      esc(title) +
      "</div>";
    if (state.previousSeedTitle) {
      html +=
        '<div id="pr-seed-previous" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
        esc(PREVIOUS_SEED_HEAD + state.previousSeedTitle + PREVIOUS_SEED_TAIL) +
        "</div>";
    }
    if (seed && seed.title_note) {
      html +=
        '<div id="pr-seed-title-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
        esc(seed.title_note) +
        " " +
        esc(PROV_DOCUMENT_TITLE_HEAD + String(seed.title || "")) +
        "</div>";
    }

    if (state.seedError) {
      html +=
        '<div id="pr-seed-error" style="font-size:11.5px;color:var(--color-text-danger, #e53935);margin-top:3px">' +
        esc(state.seedError) +
        "</div>";
      node.innerHTML = html;
      return;
    }

    if (seed && seed.arxiv_id) {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:3px">arXiv: ' +
        esc(seed.arxiv_id) +
        (seed.abs_url
          ? ' <a href="' +
            esc(seed.abs_url) +
            '" target="_blank" rel="noopener noreferrer" style="color:var(--color-accent, #2563eb)">arXiv で開く</a>'
          : "") +
        "</div>";
    } else if (seed && provenanceStatus() === "inferred") {
      // 推定は推定として下の区画で言う（未登録を「登録済み」に見せない）。
      html += "";
    } else if (seed) {
      // PD6: 判定不能を偽装しない（arXiv 由来でない教材はカテゴリを手で決める）。
      html +=
        '<div id="pr-seed-no-arxiv" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(NO_ARXIV_NOTICE) +
        "</div>";
    } else {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:3px">起点の論文を読み込んでいます...</div>';
    }

    // PR7: 「なぜ条件が空なのか」を落とさない。サーバの事実文（到達失敗 / 混雑 /
    // 該当なしの区別はサーバ側の文言が持っている）をそのまま出す。クライアントで
    // 理由を推測しない。
    if (seed && seed.note) {
      html +=
        '<div id="pr-seed-note" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(seed.note) +
        "</div>";
    }

    html += provenanceHtml();
    node.innerHTML = html;
    // 動的描画のたびにイベントを付け直す（このモジュールの既存の流儀）。
    bindProvenanceActions(node);
    maybeAutoRegisterProvenance();
  }

  // ── arXiv 出所の後付け登録 ─────────────────────────────────────────────
  function provenanceStatus() {
    return (state.provenance && state.provenance.status) || "";
  }

  function provenanceFactHtml(text) {
    return (
      '<div class="pr-provenance-fact" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
      esc(text) +
      "</div>"
    );
  }

  // 推定・並置・登録ボタンをまとめた区画。registered / none のときは何も足さない
  // （従来表示のまま）。can_register が false のときだけ登録導線を隠す
  // （キーがサーバから来ない場合は表示し、403 は事実文で degrade する）。
  function provenanceHtml() {
    var prov = state.provenance;
    var html = "";
    if (prov && prov.status === "inferred") {
      var arxivId = String(prov.arxiv_id || "");
      if (!prov.fetched) {
        html += provenanceFactHtml(
          PROV_INFERRED_HEAD + arxivId + PROV_NOT_FETCHED_TAIL
        );
      } else if (prov.title_match) {
        html += provenanceFactHtml(
          PROV_INFERRED_HEAD + arxivId + PROV_TITLE_MATCH_TAIL
        );
      } else {
        html += provenanceFactHtml(
          PROV_INFERRED_HEAD + arxivId + PROV_MISMATCH_NOTICE
        );
        html +=
          '<div class="pr-provenance-titles" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
          esc(PROV_ARXIV_TITLE_HEAD + String(prov.arxiv_title || "")) +
          "</div>" +
          '<div class="pr-provenance-titles" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:1px">' +
          esc(PROV_DOCUMENT_TITLE_HEAD + String(prov.document_title || "")) +
          "</div>";
        if (prov.can_register !== false) {
          html +=
            '<div style="margin-top:4px">' +
            '<button type="button" id="pr-provenance-register" data-ui-anchor="materials.radar-provenance" ' +
            'class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">' +
            esc(PROV_REGISTER_LABEL) +
            "</button></div>";
        }
      }
    }
    if (state.provenanceNotice) {
      html +=
        '<div id="pr-provenance-notice" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(state.provenanceNotice) +
        "</div>";
    }
    return html;
  }

  function bindProvenanceActions(root) {
    var button = root.querySelector("#pr-provenance-register");
    if (!button) return;
    button.addEventListener("click", function () {
      var prov = state.provenance || {};
      var message =
        PROV_ARXIV_TITLE_HEAD +
        String(prov.arxiv_title || "") +
        "\n" +
        PROV_DOCUMENT_TITLE_HEAD +
        String(prov.document_title || "") +
        "\n\n" +
        PROV_CONFIRM_TAIL;
      if (!window.confirm(message)) return;
      // 手動確定後は自動登録を二度と試みない。
      state.provenanceAutoAttempted = true;
      registerProvenance(true);
    });
  }

  // タイトルが一致した推定だけ、1回に限り自動登録する（PR5: 教員の作業を
  // 増やさないための例外。一致しない場合は必ず明示確認を経る）。
  function maybeAutoRegisterProvenance() {
    var prov = state.provenance;
    if (state.provenanceAutoAttempted) return;
    if (!prov || prov.status !== "inferred") return;
    if (!prov.fetched || !prov.title_match) return;
    if (prov.can_register === false) return;
    state.provenanceAutoAttempted = true;
    registerProvenance(false);
  }

  function registerProvenance(confirmed) {
    var prov = state.provenance;
    if (!prov || !prov.arxiv_id) return;
    var documentId = state.documentId;
    api("/admin/discovery/radar/provenance", {
      method: "POST",
      body: JSON.stringify({
        document_ref: documentId,
        arxiv_id: prov.arxiv_id,
        confirm: !!confirmed
      })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        if (state.documentId !== documentId) return;
        state.provenanceNotice = PROV_REGISTERED_NOTICE;
        if (data && data.seed) {
          // 登録の副作用で、POST の往復中に教員が編集した条件チップ
          // （カテゴリ・キーフレーズ）を巻き戻さない。出所の表示だけ
          // registered に更新する。
          // 記帳後の seed は arXiv を取り直していないので、手元の note
          // （取得できなかった事実）と制限の目印を消さない — applySeedMeta と
          // 同じ「減る方向の上書きをしない」規律。
          var previousNote = state.seed && state.seed.note;
          state.seed = data.seed;
          if (!data.seed.note && previousNote) state.seed.note = previousNote;
          if (data.seed.provenance) state.provenance = data.seed.provenance;
          if (data.seed.categories_source) {
            state.categoriesSource = data.seed.categories_source;
          }
          applyArxivBlocked(
            data.seed.arxiv_blocked,
            data.seed.arxiv_blocked_note || "",
            "blocked"
          );
        }
        renderSeed();
        renderCategoryChips();
      })
      .catch(function (err) {
        if (state.documentId !== documentId) return;
        // 403 / 409 / 422 はサーバの事実文をそのまま出すだけで、検索操作は妨げない。
        state.provenanceNotice = detailText(err, PROV_REGISTER_FAILED_NOTICE);
        renderSeed();
      });
  }

  // ── チップ描画 ────────────────────────────────────────────────────────
  function chipStyle(enabled) {
    var base =
      "border:1px solid var(--color-border);border-radius:12px;padding:1px 9px;font-size:11.5px;cursor:pointer;background:var(--color-bg-tertiary);color:var(--color-text-primary)";
    if (enabled) return base;
    // PD3: 外したチップは消さず打ち消し表示で残す。
    return base + ";text-decoration:line-through;opacity:0.55";
  }

  function renderCategoryChips() {
    var node = el("pr-categories");
    var source = el("pr-category-source");
    if (source) {
      source.textContent = state.categoriesSource
        ? CATEGORY_SOURCE_NOTICES[state.categoriesSource] ||
          CATEGORY_SOURCE_NOTICES.manual
        : "";
    }
    if (!node) return;
    if (!state.categories.length) {
      node.innerHTML =
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">カテゴリが未指定です。</span>';
      return;
    }
    var html = "";
    for (var i = 0; i < state.categories.length; i++) {
      var chip = state.categories[i] || {};
      // 主カテゴリだけ既定で有効・副カテゴリは打ち消し表示（クリックで足す — §19 B / PD3）。
      html +=
        '<span style="' +
        chipStyle(chip.enabled) +
        ';display:inline-flex;align-items:center;gap:5px;cursor:default">' +
        '<button type="button" class="pr-category-chip" data-value="' +
        esc(chip.text) +
        '" title="' +
        esc(chip.primary ? CATEGORY_PRIMARY_TITLE : CATEGORY_TOGGLE_TITLE) +
        '" style="background:none;border:none;color:inherit;cursor:pointer;padding:0;font-size:inherit;text-decoration:inherit">' +
        esc(chip.text) +
        "</button>" +
        '<button type="button" class="pr-category-remove" data-value="' +
        esc(chip.text) +
        '" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;padding:0;font-size:12px">&times;</button>' +
        "</span>";
    }
    node.innerHTML = html;
    bindAll(node, ".pr-category-chip", function (button) {
      var index = indexOfText(state.categories, button.getAttribute("data-value"));
      if (index >= 0) state.categories[index].enabled = !state.categories[index].enabled;
      renderCategoryChips();
    });
    bindAll(node, ".pr-category-remove", function (button) {
      var index = indexOfText(state.categories, button.getAttribute("data-value"));
      if (index >= 0) state.categories.splice(index, 1);
      renderCategoryChips();
    });
  }

  // seed の category_candidates（主カテゴリだけ enabled）からチップを作る。古いサーバ
  // （候補キーなし）では categories をすべて有効として並べる（後方互換）。
  function categoryChipsFromSeed(seed) {
    var out = [];
    var candidates = (seed && seed.category_candidates) || [];
    var i;
    if (candidates.length) {
      for (i = 0; i < candidates.length; i++) {
        var entry = candidates[i] || {};
        if (!entry.text) continue;
        out.push({ text: entry.text, enabled: entry.enabled !== false, primary: !!entry.primary });
      }
      return out;
    }
    var categories = (seed && seed.categories) || [];
    for (i = 0; i < categories.length; i++) {
      if (categories[i]) out.push({ text: String(categories[i]), enabled: true, primary: false });
    }
    return out;
  }

  function enabledCategories() {
    var out = [];
    for (var i = 0; i < state.categories.length; i++) {
      var chip = state.categories[i] || {};
      if (chip.enabled && chip.text) out.push(chip.text);
    }
    return out;
  }

  // 供給の事実文（承認済み部品が無い・何も取れなかった）を 1 行で出す（§16 / PD6）。
  // 文言はサーバの keyphrase_supply.note をそのまま描く（クライアントで作らない）。
  function renderKeyphraseSupplyNote() {
    var node = el("pr-keyphrase-supply-note");
    if (!node) return;
    node.textContent = state.keyphraseSupplyNote || "";
    node.style.display = state.keyphraseSupplyNote ? "" : "none";
  }

  // 直前の検索の当たり外れ（サーバの keyphrase_hits_note / keyphrase_widening_note の素通し）。
  function renderKeyphraseHitsNote() {
    var node = el("pr-keyphrase-hits-note");
    if (!node) return;
    var lines = [];
    if (state.keyphraseHitsNote) lines.push(state.keyphraseHitsNote);
    if (state.keyphraseWideningNote) lines.push(state.keyphraseWideningNote);
    var html = "";
    for (var i = 0; i < lines.length; i++) {
      html += '<div class="pr-keyphrase-hit-fact">' + esc(String(lines[i])) + "</div>";
    }
    node.innerHTML = html;
    node.style.display = lines.length ? "" : "none";
  }

  function renderKeyphraseChips() {
    renderKeyphraseSupplyNote();
    renderKeyphraseHitsNote();
    var node = el("pr-keyphrases");
    if (!node) return;
    if (!state.keyphrases.length) {
      node.innerHTML =
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">キーフレーズはまだありません。</span>';
      return;
    }
    var html = "";
    for (var i = 0; i < state.keyphrases.length; i++) {
      var entry = state.keyphrases[i] || {};
      var sourceLabel = SOURCE_LABELS[entry.source] || SOURCE_LABELS.manual;
      var hitInfo = state.keyphraseHits[String(entry.text || "").toLowerCase()];
      var missed = !!(entry.enabled && hitInfo && hitInfo.hit === false);
      var widens = !!(entry.enabled && hitInfo && hitInfo.widens === true);
      var chipTitle = sourceLabel;
      if (missed) chipTitle = sourceLabel + " ／ " + KEYPHRASE_MISS_TITLE;
      else if (widens) chipTitle = sourceLabel + " ／ " + (hitInfo.widensNote || KEYPHRASE_WIDENS_TITLE);
      var markText = missed ? KEYPHRASE_MISS_MARK : widens ? KEYPHRASE_WIDENS_MARK : "";
      var markClass = missed ? "pr-keyphrase-miss" : "pr-keyphrase-widens";
      html +=
        '<button type="button" class="pr-keyphrase-chip" data-value="' +
        esc(entry.text) +
        '" title="' +
        esc(chipTitle) +
        '" style="' +
        chipStyle(entry.enabled) +
        '">' +
        esc(entry.text) +
        (markText
          ? ' <span class="' +
            markClass +
            '" style="font-size:10.5px;color:var(--color-text-tertiary);border-left:1px solid var(--color-border);padding-left:5px;margin-left:2px">' +
            esc(markText) +
            "</span>"
          : "") +
        "</button>";
    }
    node.innerHTML = html;
    bindAll(node, ".pr-keyphrase-chip", function (button) {
      var index = indexOfText(state.keyphrases, button.getAttribute("data-value"));
      if (index >= 0) {
        state.keyphrases[index].enabled = !state.keyphrases[index].enabled;
      }
      renderKeyphraseChips();
    });
  }

  // 「近い」以外はカテゴリだけで網を張る（キーフレーズで絞ると「近い」に寄るため）。
  // 区画ごと隠して、効かない条件を操作させない。
  function renderKeyphraseSection() {
    var section = el("pr-keyphrase-section");
    if (!section) return;
    section.style.display = state.distance === "near" ? "" : "none";
  }

  function addCategoryFromInput() {
    var input = el("pr-category-input");
    var value = input ? input.value.trim() : "";
    if (!value) return;
    var existing = indexOfText(state.categories, value);
    if (existing >= 0) state.categories[existing].enabled = true;
    else state.categories.push({ text: value, enabled: true, primary: false });
    if (input) input.value = "";
    renderCategoryChips();
  }

  function addKeyphraseFromInput() {
    var input = el("pr-keyphrase-input");
    var value = input ? input.value.trim() : "";
    if (!value) return;
    var index = indexOfText(state.keyphrases, value);
    if (index >= 0) {
      state.keyphrases[index].enabled = true;
    } else {
      state.keyphrases.push({ text: value, source: "manual", enabled: true });
    }
    if (input) input.value = "";
    renderKeyphraseChips();
  }

  // ── 許可ドメインの確認（UF1 継承の補助表示）────────────────────────────
  function checkAllowedDomains() {
    api("/admin/url-fetch-domains")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var domains = (data && data.domains) || [];
        var allowed = false;
        for (var i = 0; i < domains.length; i++) {
          var name = String((domains[i] && domains[i].domain) || "").toLowerCase();
          // ドット境界で照合する（"myarxiv.org" を許可扱いにしない）。
          if (
            name === "arxiv.org" ||
            name.lastIndexOf(".arxiv.org") === name.length - ".arxiv.org".length
          ) {
            allowed = true;
          }
        }
        state.domainAllowed = allowed;
        renderIngestSummary();
      })
      .catch(function () {
        state.domainAllowed = null;
        renderIngestSummary();
      });
  }

  // ── 検索 ──────────────────────────────────────────────────────────────
  function runSearch() {
    if (state.searching) return;
    if (!state.documentId) return;

    state.searching = true;
    setNotice("arXiv を検索しています...");
    var button = el("pr-search-btn");
    if (button) button.disabled = true;

    var payload = {
      document_ref: state.documentId,
      distance: state.distance,
      // 有効なチップだけを送る（打ち消し表示の副カテゴリは条件に入れない — §19 B）。
      categories: enabledCategories(),
      // 「近い」以外はキーフレーズを条件に使わない（UI の非表示と挙動を一致させる）。
      keyphrases: state.distance === "near" ? enabledKeyphrases() : []
    };

    api("/admin/discovery/radar/search", {
      method: "POST",
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.searching = false;
        if (button) button.disabled = false;
        if (data && data.seed) applySeedMeta(data.seed);
        state.candidates = (data && data.candidates) || [];
        state.query = (data && data.query) || "";
        state.closedWorldNote = (data && data.closed_world_note) || "";
        state.keyphraseNote = (data && data.keyphrase_note) || "";
        state.total = data && typeof data.total === "number" ? data.total : null;
        state.banding = (data && data.banding) || null;
        // 古いレスポンス（キーなし）でも壊れない。null なら未測定注記を出さない。
        state.relationContext = (data && data.relation_context) || null;
        state.facts = (data && data.facts) || [];
        state.keyphraseHits = {};
        var hits = (data && data.keyphrase_hits) || [];
        for (var h = 0; h < hits.length; h++) {
          var hit = hits[h] || {};
          if (hit.text) {
            state.keyphraseHits[String(hit.text).toLowerCase()] = {
              hit: hit.hit === true,
              widens: hit.widens === true,
              // この語だけに一致した候補が多い語の事実文（サーバ文言の素通し・§23.5）。
              widensNote: hit.widens === true && hit.widens_note ? String(hit.widens_note) : ""
            };
          }
        }
        state.keyphraseHitsNote = (data && data.keyphrase_hits_note) || "";
        state.keyphraseWideningNote = (data && data.keyphrase_widening_note) || "";
        state.readingNote = (data && data.reading_note) || "";
        state.searched = true;
        // 制限中の 200 応答（候補ゼロ + arxiv_blocked）は「近い論文が無い」ではない。
        applyArxivBlocked(
          data ? data.arxiv_blocked : undefined,
          blockedNoteFrom(data),
          "blocked"
        );
        state.openBands = {};
        state.selected = {};
        // 前の検索の比較結果を新しい候補に持ち越さない（PR4: 結果は一時的な注釈）。
        state.compareById = {};
        state.compareNotes = [];
        setNotice("");
        renderQueryNote();
        renderKeyphraseChips();
        renderCandidates();
        renderCompareControl();
        renderIngestSummary();
      })
      .catch(function (err) {
        state.searching = false;
        if (button) button.disabled = false;
        // 404（教材不可視）/ 422（距離語彙外）/ 502（arXiv 失敗）はサーバの事実文をそのまま。
        applyRequestFailure(err);
        setNotice(detailText(err, "検索に失敗しました。"), true);
        renderCandidates();
      });
  }

  // 検索レスポンスの seed は表示メタだけ更新する（教員が編集した条件は上書きしない）。
  // provenance は検索経路だと arXiv 再取得を省くため fetched=false に劣化しうる。
  // 手元の情報が減る方向の上書きはせず、registered 化（登録済みになった）か
  // 手元が空のときだけ差し替える。
  function applySeedMeta(seed) {
    if (!seed) return;
    // 検索経路は arXiv を取り直さない（サーバは条件が明示されていれば fetch を省く）。
    // note が付いていないのは「解消した」ではなく「確かめ直していない」なので、
    // 手元の事実文を消さない（provenance と同じ「減る方向の上書きをしない」規律）。
    var previous = state.seed || {};
    var previousNote = previous.note;
    state.seed = seed;
    if (!seed.note && previousNote) state.seed.note = previousNote;
    // 見出しの arXiv 題名も「確かめ直していない」だけなので消さない（§19 E）。
    if (!seed.display_title && previous.display_title) {
      state.seed.display_title = previous.display_title;
      state.seed.title_note = previous.title_note;
    }
    var incoming = seed.provenance;
    if (incoming && (incoming.status === "registered" || !state.provenance)) {
      state.provenance = incoming;
    }
    renderSeed();
    applyArxivBlocked(seed.arxiv_blocked, seed.arxiv_blocked_note || "", "blocked");
  }

  // PR7: 何をどう検索した結果なのかを一覧の上に常時出す。
  function renderQueryNote() {
    var node = el("pr-query-note");
    if (!node) return;
    var parts = [];
    if (state.query) {
      parts.push("検索条件: " + state.query);
    } else if (state.searched) {
      parts.push(
        "検索条件: 指定されていません（カテゴリまたはキーフレーズを指定してください）。"
      );
    } else {
      parts.push("検索条件: まだ検索していません。");
    }
    if (state.total !== null) parts.push("該当件数: " + String(state.total));
    if (state.closedWorldNote) parts.push(state.closedWorldNote);
    // キーフレーズ無しの near はカテゴリの新着（§16）。「近い論文」と読ませない。
    if (state.keyphraseNote) parts.push(state.keyphraseNote);
    // PR2: 帯分けができなかったことを黙らない（サーバの note を優先して出す）。
    if (state.banding && state.banding.available === false) {
      parts.push(
        state.banding.note ? String(state.banding.note) : BANDING_UNAVAILABLE_NOTICE
      );
    }
    node.textContent = parts.join(" ／ ");
    renderFacts();
  }

  // 近さの根拠が出ない理由（サーバの facts。文言はサーバが持つ — §19 A / PR7）。
  function renderFacts() {
    var node = el("pr-facts");
    if (!node) return;
    var facts = (state.facts || []).slice();
    // 帯と関係の種類の読み方は、関係ラベル（比較結果）が描かれているときだけ（§20 D）。
    if (state.readingNote && hasRelationLabels()) facts.push(state.readingNote);
    var html = "";
    for (var i = 0; i < facts.length; i++) {
      html += '<div class="pr-fact">' + esc(String(facts[i])) + "</div>";
    }
    node.innerHTML = html;
    node.style.display = facts.length ? "" : "none";
  }

  // 比較結果のうち、いま一覧にある候補に関係ラベルが付いているか。
  function hasRelationLabels() {
    for (var i = 0; i < state.candidates.length; i++) {
      var candidate = state.candidates[i] || {};
      var item = state.compareById[candidate.arxiv_id];
      if (item && item.relation_label) return true;
    }
    return false;
  }

  // ── 重なり・差分の描画 ────────────────────────────────────────────────
  // どのキーも optional。無ければその行ごと描かない（キー欠落で例外を出さない）。
  function relationChipHtml(text, style) {
    return (
      '<span class="pr-relation-chip" style="' +
      style +
      ';display:inline-block;border-radius:10px;padding:0 7px;font-size:11px;line-height:16px">' +
      esc(text) +
      "</span>"
    );
  }

  // VA層の着地予測。サーバの node_label / region_label / nearness_label /
  // skeleton_version をそのまま連結するだけ（版を伏せない — VA8）。
  function landingLineHtml(candidate) {
    var landing = candidate && candidate.landing;
    if (!landing || !landing.node_label) return "";
    var parts = [];
    if (landing.nearness_label) parts.push(String(landing.nearness_label));
    if (landing.skeleton_version) {
      parts.push("骨格 版" + String(landing.skeleton_version));
    }
    return (
      '<div class="pr-landing" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:2px">' +
      esc(
        LANDING_HEAD +
          (landing.region_label ? String(landing.region_label) + " / " : "") +
          String(landing.node_label) +
          LANDING_TAIL +
          (parts.length ? "（" + parts.join("・") + "）" : "")
      ) +
      "</div>"
    );
  }

  // 下地はあるのにこの候補だけ測れなかったことを言う（取り込みは妨げない）。
  function relationUnmeasuredHtml(candidate) {
    var context = state.relationContext;
    if (!context || !context.available) return "";
    if (candidate && candidate.landing && candidate.landing.node_label) return "";
    return (
      '<div class="pr-relation-unmeasured" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
      esc(RELATION_UNMEASURED_NOTICE) +
      "</div>"
    );
  }

  function relationChipsHtml(candidate) {
    var overlaps = (candidate && candidate.overlap_components) || [];
    var facets = (candidate && candidate.new_facets) || [];
    if (!overlaps.length && !facets.length) return "";
    var html =
      '<div class="pr-relation-chips" style="margin-top:3px;display:flex;gap:5px;flex-wrap:wrap;align-items:center">';
    var overlapShown =
      overlaps.length > OVERLAP_CHIP_MAX ? OVERLAP_CHIP_MAX : overlaps.length;
    for (var i = 0; i < overlapShown; i++) {
      html += relationChipHtml(
        OVERLAP_CHIP_HEAD + String(overlaps[i]) + OVERLAP_CHIP_TAIL,
        OVERLAP_CHIP_STYLE
      );
    }
    if (overlaps.length > overlapShown) {
      // 上限で落とした分は存在だけ残す（件数は出さない — 数値を見せない）。
      html +=
        '<span class="pr-relation-more" style="font-size:11px;color:var(--color-text-tertiary)">' +
        esc(CHIP_MORE_LABEL) +
        "</span>";
    }
    var facetShown = facets.length > FACET_CHIP_MAX ? FACET_CHIP_MAX : facets.length;
    for (var f = 0; f < facetShown; f++) {
      html += relationChipHtml(
        FACET_CHIP_HEAD + String(facets[f]) + FACET_CHIP_TAIL,
        FACET_CHIP_STYLE
      );
    }
    html +=
      '<span class="pr-relation-estimate" style="font-size:11px;color:var(--color-text-tertiary)">' +
      esc(ESTIMATE_TAG) +
      "</span>";
    return html + "</div>";
  }

  // 凡例は結果リストの先頭で1回だけ。記号の意味と「推定である」ことをここで宣言し、
  // 各カードでは繰り返さない（PR7: 何を見ているかを一覧の上で明示する）。
  function hasRelationSignals() {
    for (var i = 0; i < state.candidates.length; i++) {
      var candidate = state.candidates[i] || {};
      if (candidate.landing && candidate.landing.node_label) return true;
      if (candidate.overlap_components && candidate.overlap_components.length) {
        return true;
      }
      if (candidate.new_facets && candidate.new_facets.length) return true;
    }
    return false;
  }

  function relationLegendHtml() {
    if (!hasRelationSignals()) return "";
    return (
      '<div id="pr-relation-legend" style="font-size:11.5px;color:var(--color-text-tertiary);padding-bottom:6px">' +
      esc(RELATION_LEGEND) +
      "</div>"
    );
  }

  // ── 取り込み済み・受理直後の候補の導線（§19 C）──────────────────────────
  // 候補に紐づく教材（見える教材だけがサーバから来る — IK-0701）。受理直後の候補は
  // サーバの判定がまだ無いので、受理応答の material_id を手元で覚えておく。
  function candidateMaterial(candidate) {
    var arxivId = (candidate && candidate.arxiv_id) || "";
    var accepted = state.acceptedById[arxivId] || null;
    var materialId = (candidate && candidate.material_id) || (accepted && accepted.material_id) || "";
    var documentId = (candidate && candidate.document_id) || "";
    var status = (candidate && candidate.analysis_status) || "";
    var title = (candidate && candidate.title) || "";
    // 教材一覧の最新の行（admin.js が保持）があればそちらの状態を優先する。
    var row = materialId && deps && deps.getMaterial ? deps.getMaterial(materialId) : null;
    if (row) {
      if (row.document_id) documentId = String(row.document_id);
      if (row.status) status = String(row.status);
      if (row.title) title = String(row.title);
    }
    if (!materialId && !documentId) return null;
    return {
      materialId: materialId,
      documentId: documentId,
      status: status,
      title: title,
      cartridgeId: (row && row.analysis_cartridge_id) || "",
      accepted: !!accepted
    };
  }

  function ingestedActionButtonHtml(cls, anchor, label, enabled, reason, material) {
    return (
      '<button type="button" class="admin-action-btn ' +
      cls +
      '" data-ui-anchor="' +
      anchor +
      '" data-material-id="' +
      esc(material.materialId) +
      '" data-document-id="' +
      esc(material.documentId) +
      '" data-title="' +
      esc(material.title) +
      '" data-cartridge-id="' +
      esc(material.cartridgeId) +
      '"' +
      (enabled ? "" : ' disabled title="' + esc(reason) + '"') +
      ' style="font-size:11px;padding:1px 7px">' +
      esc(label) +
      "</button>"
    );
  }

  // 配置の対比の 1 項目（ラベル + 出所）。
  function contrastItemText(item) {
    return String(item.label) + (item.provenance_label ? "〈" + String(item.provenance_label) + "〉" : "");
  }

  // 観点ごとに並べる（§22 C）。観点はサーバが解決した日本語（perspective_label）をそのまま使い、
  // JS に観点の表を持たない。観点の無い項目は先頭にまとめる。
  function contrastByPerspective(items) {
    var order = [];
    var groups = {};
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      if (!item.label) continue;
      var head = item.perspective_label ? String(item.perspective_label) : "";
      if (!groups.hasOwnProperty(head)) {
        groups[head] = [];
        order.push(head);
      }
      groups[head].push(contrastItemText(item));
    }
    var parts = [];
    for (var j = 0; j < order.length; j++) {
      var key = order[j];
      parts.push((key ? key + ": " : "") + groups[key].join("、"));
    }
    return parts.join(" ／ ");
  }

  // 領域と概念を分けて描く（§22 C — 粒度の違う面を同じ列に混ぜない）。
  function contrastItemsText(items) {
    if (!items || !items.length) return CONTRAST_NONE;
    var regions = [];
    var concepts = [];
    var others = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      if (!item.label) continue;
      if (item.layer === "region") regions.push(item);
      else if (item.layer === "concept") concepts.push(item);
      else others.push(item);
    }
    var parts = [];
    if (regions.length) parts.push(CONTRAST_LAYER_REGION + contrastByPerspective(regions));
    if (concepts.length) parts.push(CONTRAST_LAYER_CONCEPT + contrastByPerspective(concepts));
    if (others.length) parts.push(contrastByPerspective(others));
    return parts.length ? parts.join(" ／ ") : CONTRAST_NONE;
  }

  // 配置の対比（3 行）と構造照合の成立条件（1 行）。キーが無ければ何も描かない。
  function placementContrastHtml(candidate) {
    if (!candidate) return "";
    var style = "font-size:11.5px;color:var(--color-text-secondary);margin-top:2px";
    var html = "";
    var contrast = candidate.placement_contrast;
    if (contrast && typeof contrast === "object") {
      html += '<div class="pr-placement-contrast" style="margin-top:3px">';
      html += '<div style="' + style + '">' + esc(CONTRAST_SHARED_HEAD + contrastItemsText(contrast.shared)) + "</div>";
      html += '<div style="' + style + '">' + esc(CONTRAST_SEED_ONLY_HEAD + contrastItemsText(contrast.seed_only)) + "</div>";
      html += '<div style="' + style + '">' + esc(CONTRAST_CANDIDATE_ONLY_HEAD + contrastItemsText(contrast.candidate_only)) + "</div>";
      // 片側にだけ配置のある観点の事実文（§22.4）。サーバの文面をそのまま描く（数は無い）。
      var perspectiveFacts = Array.isArray(contrast.perspective_facts) ? contrast.perspective_facts : [];
      for (var pf = 0; pf < perspectiveFacts.length; pf++) {
        if (!perspectiveFacts[pf]) continue;
        html += '<div class="pr-placement-perspective-fact" style="' + style + '">' + esc(String(perspectiveFacts[pf])) + "</div>";
      }
      if (contrast.skeleton_version) {
        html += '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:1px">' +
          esc((String(contrast.skeleton_version).indexOf("版") >= 0 ? "分野の地図: " : CONTRAST_VERSION_HEAD) +
            String(contrast.skeleton_version)) + "</div>";
      }
      html += "</div>";
    } else if (candidate.placement_contrast_note) {
      html += '<div class="pr-placement-contrast-note" style="' + style + '">' + esc(candidate.placement_contrast_note) + "</div>";
    }
    if (candidate.module_match_note) {
      html += '<div class="pr-module-match-note" style="' + style + '">' + esc(candidate.module_match_note) + "</div>";
    }
    return html;
  }

  function ingestedActionsHtml(candidate) {
    var material = candidateMaterial(candidate);
    if (!material) return "";
    var ready = material.status === ANALYSIS_COMPLETED && !!material.documentId;
    var reason = material.status === "failed" ? INGESTED_FAILED_REASON : INGESTED_NOT_READY_REASON;
    var html = "";
    if (material.accepted && !ready) {
      html +=
        '<div class="pr-accepted-note" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(ACCEPTED_CHAIN_NOTICE) +
        "</div>";
    }
    html += '<div class="pr-ingested-actions" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">';
    if (material.materialId) {
      html += ingestedActionButtonHtml(
        "pr-ingested-open-material", "materials.radar-ingested-open-material",
        INGESTED_OPEN_MATERIAL_LABEL, true, "", material
      );
    }
    html += ingestedActionButtonHtml(
      "pr-ingested-graph-review", "materials.radar-ingested-graph-review",
      INGESTED_GRAPH_REVIEW_LABEL, ready, reason, material
    );
    html += ingestedActionButtonHtml(
      "pr-ingested-landscape", "materials.radar-ingested-landscape",
      INGESTED_LANDSCAPE_LABEL, ready, reason, material
    );
    html += ingestedActionButtonHtml(
      "pr-chain-seed", "materials.radar-chain-seed",
      CHAIN_SEED_LABEL, ready, reason, material
    );
    return html + "</div>";
  }

  // 導線の配線。グラフレビュー・位置づけはこのモーダルを閉じてから開く（全画面の
  // モーダルを重ねない）。連鎖は同じモーダルを別の起点で開き直す（openModal が状態を初期化）。
  function bindIngestedActions(root) {
    bindAll(root, ".pr-ingested-open-material", function (button) {
      var materialId = button.getAttribute("data-material-id");
      close();
      if (deps && deps.focusMaterialRow) deps.focusMaterialRow(materialId);
    });
    bindAll(root, ".pr-ingested-graph-review", function (button) {
      var documentId = button.getAttribute("data-document-id");
      var title = button.getAttribute("data-title");
      close();
      if (deps && deps.openGraphReview) deps.openGraphReview(documentId, title);
    });
    bindAll(root, ".pr-ingested-landscape", function (button) {
      var documentId = button.getAttribute("data-document-id");
      var title = button.getAttribute("data-title");
      close();
      if (deps && deps.openLandscape) deps.openLandscape(documentId, title);
    });
    bindAll(root, ".pr-chain-seed", function (button) {
      var previous = (state.seed && (state.seed.display_title || state.seed.title)) || state.title || "";
      openModal(
        button.getAttribute("data-document-id"),
        button.getAttribute("data-title"),
        { cartridgeId: button.getAttribute("data-cartridge-id") || "", previousSeedTitle: previous }
      );
    });
  }

  // 教材一覧が再読込されたとき（admin.js の renderMaterials）に呼ばれる。解析状態が
  // 変わっていれば候補カードを描き直す。ここから API は呼ばない（PR5 — ポーリングなし）。
  function onMaterialsUpdated() {
    if (!state.open || !state.candidates.length) return;
    renderCandidates();
  }

  // ── 候補一覧 ──────────────────────────────────────────────────────────
  function candidateCardHtml(candidate) {
    var arxivId = (candidate && candidate.arxiv_id) || "";
    var status = (candidate && candidate.status) || "new";
    var authors = (candidate && candidate.authors) || [];
    var categories = (candidate && candidate.categories) || [];
    var matched = (candidate && candidate.matched_keyphrases) || [];
    var meta = [];
    if (authors.length) meta.push(authors.join(", "));
    if (candidate && candidate.published) meta.push(String(candidate.published));
    if (categories.length) meta.push(categories.join(" / "));

    var html =
      '<div class="pr-candidate" data-arxiv-id="' +
      esc(arxivId) +
      '" style="border-bottom:1px solid var(--color-border-tertiary);padding:8px 0">' +
      '<div style="display:flex;gap:8px;align-items:flex-start">';

    if (status === "new") {
      html +=
        '<input type="checkbox" class="pr-select" data-arxiv-id="' +
        esc(arxivId) +
        '"' +
        (state.selected[arxivId] ? " checked" : "") +
        ' style="margin-top:3px">';
    } else {
      html += '<span style="width:13px"></span>';
    }

    html +=
      '<div style="flex:1;min-width:0">' +
      '<div style="font-size:13px;color:var(--color-text-primary)">' +
      esc((candidate && candidate.title) || arxivId);
    // 取り込み済みはタイトル行にバッジで出す（一覧を流し読みしても新着と見分けられる）。
    if (status === "ingested") {
      html +=
        '<span class="pr-ingested-badge" style="display:inline-block;margin-left:6px;vertical-align:1px;' +
        'border-radius:10px;padding:0 7px;font-size:11px;white-space:nowrap;' +
        INGESTED_BADGE_STYLE +
        '">' +
        esc(INGESTED_BADGE_LABEL) +
        "</span>";
    }
    html += "</div>";

    // PR2: サーバが確定した帯ラベルをそのまま出す（閾値判定・数値描画をしない）。
    if (candidate && candidate.distance_label) {
      html +=
        '<span class="pr-distance-label" style="display:inline-block;margin-top:3px;border:1px solid var(--color-border);border-radius:10px;padding:0 7px;font-size:11px;color:var(--color-text-secondary)">' +
        esc(DISTANCE_CHIP_HEAD + candidate.distance_label) +
        "</span>";
    }

    html +=
      '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
      esc(meta.join(" ・ ")) +
      "</div>";

    // 着地（測れたとき）か、測れなかった事実（下地があるとき）のどちらか一方。
    html += landingLineHtml(candidate);
    html += relationUnmeasuredHtml(candidate);
    html += relationChipsHtml(candidate);

    if (matched.length) {
      // なぜ候補なのかを1行で言う（ブラックボックスのおすすめにしない・数値は出さない）。
      html +=
        '<div class="pr-matched" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:2px">一致: ' +
        esc(matched.join(" ・ ")) +
        "</div>";
    }

    if (status === "ingested") {
      // 取り込み済みでも arXiv へは開ける（元論文の確認を塞がない）。バッジと
      // 同じ文言をここでも繰り返さず、すでに教材になっている事実だけを言う。
      html +=
        '<div class="pr-status-label" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(INGESTED_STATUS_LINE) +
        "</div>";
      html += placementContrastHtml(candidate);
      html += ingestedActionsHtml(candidate);
    }
    if (candidate && candidate.abs_url) {
      html +=
        '<div style="margin-top:4px">' +
        '<a href="' +
        esc(candidate.abs_url) +
        '" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:var(--color-accent, #2563eb)">arXiv で開く</a>' +
        "</div>";
    }

    html += compareBlockHtml(arxivId);

    if (candidate && candidate.summary) {
      html +=
        '<details style="margin-top:4px"><summary style="font-size:11.5px;color:var(--color-text-secondary);cursor:pointer">要旨</summary>' +
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:3px;white-space:pre-wrap">' +
        esc(candidate.summary) +
        "</div></details>";
    }

    html += "</div></div></div>";
    return html;
  }

  // 逐語引用は details に畳む（省かず、既定では statement の読みを妨げない）。
  function compareQuoteHtml(quote) {
    if (!quote) return "";
    return (
      '<details class="pr-compare-quote" style="margin-top:1px">' +
      '<summary style="font-size:11px;color:var(--color-text-tertiary);cursor:pointer">' +
      esc(COMPARE_QUOTE_SUMMARY) +
      "</summary>" +
      '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:1px">' +
      esc('"' + String(quote) + '"') +
      "</div></details>"
    );
  }

  function compareSectionHeadHtml(text) {
    return (
      '<div class="pr-compare-section-head" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:5px">' +
      esc(text) +
      "</div>"
    );
  }

  function compareItemChipHtml(text, style) {
    return (
      '<span style="' +
      style +
      ';display:inline-block;border-radius:10px;padding:0 6px;font-size:10.5px;line-height:15px;margin-right:4px">' +
      esc(text) +
      "</span>"
    );
  }

  // IK-0818 / §22.5: 取り込み済み候補では、重なり・違いが候補側の解析結果（部品・
  // 中心命題）から来たことがある。出所の文言はサーバの source_note を素通しする
  // （クライアントに語彙表を持たない・承認前を承認済みに見せない）。
  function compareSourceNoteHtml(note) {
    if (!note) return "";
    return (
      '<div class="pr-compare-source-note" style="font-size:11px;color:var(--color-text-tertiary);margin-top:1px">' +
      esc(String(note)) +
      "</div>"
    );
  }

  // PR4: 比較結果は候補カード内の一時的な注釈。出所ラベルとサーバの caveat を
  // そのまま添える（クライアントで注意書きを作らない・断定に書き換えない）。
  // 「重なり」と「違い」は主語が別なので区画を分けて見出しを付ける
  // （同じ箇条書きに混ぜると、同じ内容なのか別の知識なのかが読めない）。
  function compareBlockHtml(arxivId) {
    var item = state.compareById[arxivId];
    if (!item) return "";
    var html =
      '<div class="pr-compare-block" style="margin-top:6px;border-left:2px solid var(--color-border);padding-left:8px">' +
      '<div style="font-size:11.5px;color:var(--color-text-secondary)">' +
      esc(COMPARE_HEAD) +
      "</div>";

    // 関係の種類（§18.4 / §19 D）。目立った共通点が無いときは、その事実を1行で言う
    // （空欄にも警告色にもしない）。文言はサーバの relation_label を素通しする。
    var isNone = item.relation_kind === RELATION_NONE_KIND;
    if (item.relation_label && isNone) {
      html +=
        '<div class="pr-compare-relation-none" style="font-size:12px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(item.relation_label) +
        "</div>";
    } else if (item.relation_label) {
      html +=
        '<div class="pr-compare-relation" style="margin-top:3px">' +
        compareItemChipHtml(String(item.relation_label), RELATION_CHIP_STYLE) +
        "</div>";
    }

    var overlaps = item.overlaps || [];
    if (overlaps.length) {
      html += compareSectionHeadHtml(COMPARE_OVERLAP_HEAD);
      html += '<ul style="margin:3px 0 0 16px;padding:0">';
      for (var o = 0; o < overlaps.length; o++) {
        var overlap = overlaps[o] || {};
        html +=
          '<li style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">';
        // component_label は空文字で来ることがある（部品に紐づかない重なり）。
        if (overlap.component_label) {
          html += compareItemChipHtml(
            COMPARE_PART_HEAD + String(overlap.component_label) + COMPARE_PART_TAIL,
            OVERLAP_CHIP_STYLE
          );
        }
        html += esc(overlap.statement || "");
        html += compareQuoteHtml(overlap.evidence_quote);
        html += compareSourceNoteHtml(overlap.source_note);
        html += "</li>";
      }
      html += "</ul>";
    } else if (item.common_ground && !isNone) {
      // overlaps を返さないサーバ（旧レスポンス）では従来どおり共通点を出す。
      html +=
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(COMPARE_COMMON_HEAD + item.common_ground) +
        "</div>";
    }

    var differences = item.differences || [];
    if (differences.length) {
      html += compareSectionHeadHtml(COMPARE_DIFF_HEAD);
      html += '<ul style="margin:3px 0 0 16px;padding:0">';
      for (var i = 0; i < differences.length; i++) {
        var diff = differences[i] || {};
        html +=
          '<li style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">';
        if (diff.aspect) {
          html += compareItemChipHtml(String(diff.aspect), FACET_CHIP_STYLE);
        }
        html += esc(diff.statement || "");
        html += compareQuoteHtml(diff.evidence_quote);
        html += compareSourceNoteHtml(diff.source_note);
        html += "</li>";
      }
      html += "</ul>";
    } else if (!overlaps.length && !item.common_ground && !isNone) {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:3px">' +
        esc(COMPARE_EMPTY_NOTICE) +
        "</div>";
    }

    if (item.analysis_note) {
      html +=
        '<div class="pr-compare-analysis-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:4px">' +
        esc(String(item.analysis_note)) +
        "</div>";
    }

    if (item.caveat) {
      html +=
        '<div class="pr-compare-caveat" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:4px">' +
        esc(item.caveat) +
        "</div>";
    }

    return html + "</div>";
  }

  // PR2: 帯（distance_label）ごとに区切る。どの帯がどのラベルかはサーバの語彙が
  // 正本なので、クライアントはラベル文字列をキーにして素直にまとめるだけにする。
  // ラベルの無い候補は最遠帯へ混ぜず、専用の区画に分ける。
  function groupCandidatesByBand(candidates) {
    var groups = [];
    var index = {};
    var unmeasured = [];
    for (var i = 0; i < candidates.length; i++) {
      var candidate = candidates[i] || {};
      var label = candidate.distance_label;
      if (!label) {
        unmeasured.push(candidate);
        continue;
      }
      var key = String(label);
      if (index[key] === undefined) {
        index[key] = groups.length;
        groups.push({ label: key, items: [] });
      }
      groups[index[key]].items.push(candidate);
    }
    return { groups: groups, unmeasured: unmeasured };
  }

  // 既定で開く帯は、サーバが選択距離に対応する帯を宣言していればその帯、
  // 宣言が無ければ「候補が最も多い帯」。距離キー → 帯ラベルの対応表を
  // クライアントに持たないための決め方で（帯ラベルの正本はサーバの語彙）、
  // どちらにしても他の帯は折りたたみで必ず残す（候補を捨てない — PR7）。
  function primaryGroupIndex(groups) {
    var declared = state.banding && state.banding.primary_label;
    if (declared) {
      for (var d = 0; d < groups.length; d++) {
        if (groups[d].label === String(declared)) return d;
      }
    }
    var best = -1;
    var bestCount = -1;
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].items.length > bestCount) {
        bestCount = groups[i].items.length;
        best = i;
      }
    }
    return best;
  }

  // 既定は「選択距離に対応する（または最大の）帯だけ開く」。教員が別の帯を開いたら
  // その状態を覚えて、比較結果の描き直しで畳まないようにする。
  function bandIsOpen(label, isPrimary) {
    if (Object.prototype.hasOwnProperty.call(state.openBands, label)) {
      return !!state.openBands[label];
    }
    return !!isPrimary;
  }

  function bandSectionHtml(group, open, isPrimary) {
    var summary = isPrimary
      ? DISTANCE_CHIP_HEAD + group.label + "（" + String(group.items.length) + "件）"
      : OTHER_BAND_HEAD + group.label + "（" + String(group.items.length) + "件）";
    var html =
      '<details class="pr-band" data-band-label="' +
      esc(group.label) +
      '"' +
      (open ? " open" : "") +
      ' style="margin-bottom:6px">' +
      '<summary style="font-size:12px;color:var(--color-text-secondary);cursor:pointer">' +
      esc(summary) +
      "</summary>";
    for (var i = 0; i < group.items.length; i++) {
      html += candidateCardHtml(group.items[i]);
    }
    return html + "</details>";
  }

  function renderCandidates() {
    var node = el("pr-results");
    if (!node) return;
    if (!state.candidates.length) {
      // 制限中の空一覧に「候補が見つかりませんでした」を出さない（arXiv へ問い合わせて
      // いないのだから「近い論文が無い」ではない）。理由はサーバの事実文で言う。
      var emptyText;
      if (state.arxivBlocked) {
        emptyText = state.arxivBlockedNote || ARXIV_BLOCKED_HEAD;
      } else {
        emptyText = state.searched ? EMPTY_RESULT_NOTICE : NOT_SEARCHED_NOTICE;
      }
      node.innerHTML =
        '<div id="pr-empty-note" style="font-size:12.5px;color:var(--color-text-tertiary);padding:8px 0">' +
        esc(emptyText) +
        "</div>";
      return;
    }

    // 凡例は帯見出しより前に1回だけ（記号の意味を先に置く）。
    var html = relationLegendHtml();
    if (state.banding && state.banding.available === false) {
      // 帯分けができなかったときは帯を作らず、新着順のまま一覧にする（PR2）。
      for (var i = 0; i < state.candidates.length; i++) {
        html += candidateCardHtml(state.candidates[i]);
      }
    } else {
      var grouped = groupCandidatesByBand(state.candidates);
      var primary = primaryGroupIndex(grouped.groups);
      for (var g = 0; g < grouped.groups.length; g++) {
        html += bandSectionHtml(
          grouped.groups[g],
          bandIsOpen(grouped.groups[g].label, g === primary),
          g === primary
        );
      }
      if (grouped.unmeasured.length) {
        html +=
          '<details class="pr-band pr-band-unmeasured" data-band-label="' +
          esc(UNMEASURED_HEAD) +
          '"' +
          (bandIsOpen(UNMEASURED_HEAD, false) ? " open" : "") +
          ' style="margin-bottom:6px">' +
          '<summary style="font-size:12px;color:var(--color-text-secondary);cursor:pointer">' +
          esc(UNMEASURED_HEAD + "（" + String(grouped.unmeasured.length) + "件）") +
          "</summary>";
        for (var u = 0; u < grouped.unmeasured.length; u++) {
          html += candidateCardHtml(grouped.unmeasured[u]);
        }
        html += "</details>";
      }
    }

    node.innerHTML = html;
    bindBandToggles(node);
    bindIngestedActions(node);
    bindAll(node, ".pr-select", function (input) {
      var arxivId = input.getAttribute("data-arxiv-id");
      if (input.checked) state.selected[arxivId] = true;
      else delete state.selected[arxivId];
      renderCompareControl();
      renderIngestSummary();
    });
  }

  function bindBandToggles(root) {
    var nodes = root.querySelectorAll(".pr-band");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("toggle", function () {
        var label = this.getAttribute("data-band-label");
        if (label) state.openBands[label] = this.open;
      });
    }
  }

  function findCandidate(arxivId) {
    for (var i = 0; i < state.candidates.length; i++) {
      if (state.candidates[i] && state.candidates[i].arxiv_id === arxivId) {
        return state.candidates[i];
      }
    }
    return null;
  }

  // ── 比較分析（PR5: ボタンを押したときだけ実行する）──────────────────────
  function selectedIds() {
    var out = [];
    for (var key in state.selected) {
      if (Object.prototype.hasOwnProperty.call(state.selected, key) && state.selected[key]) {
        out.push(key);
      }
    }
    return out;
  }

  function renderCompareControl() {
    var button = el("pr-compare-btn");
    var note = el("pr-compare-note");
    var ids = selectedIds();
    var lines = [];
    if (!ids.length) {
      lines.push(COMPARE_NONE_SELECTED_NOTICE);
    } else if (ids.length > COMPARE_MAX) {
      lines.push(
        COMPARE_LIMIT_NOTICE_HEAD + String(COMPARE_MAX) + COMPARE_LIMIT_NOTICE_TAIL
      );
    }
    for (var i = 0; i < state.compareNotes.length; i++) {
      lines.push(String(state.compareNotes[i]));
    }
    if (note) note.textContent = lines.join(" ");
    if (!button) return;
    button.disabled =
      state.comparing || !ids.length || ids.length > COMPARE_MAX;
  }

  function runCompare() {
    if (state.comparing) return;
    var button = el("pr-compare-btn");
    if (button && button.disabled) return;
    var ids = selectedIds();
    if (!ids.length || ids.length > COMPARE_MAX) return;

    state.comparing = true;
    state.compareNotes = [];
    renderCompareControl();
    setNotice("違いを分析しています...");

    api("/admin/discovery/radar/compare", {
      method: "POST",
      body: JSON.stringify({ document_ref: state.documentId, arxiv_ids: ids })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.comparing = false;
        applyCompareResult(data || {});
      })
      .catch(function (err) {
        state.comparing = false;
        // 429（日次上限）/ 502（arXiv・LLM 失敗）はサーバの事実文をそのまま見せる。
        // 比較の 502 は LLM 失敗でも返るため、ここでは arXiv 側のバナーを立てない
        // （原因を取り違えた見出しを出さない）。arXiv のブロックは 200 の
        // arxiv_blocked で applyCompareResult 側が受ける。
        setNotice(detailText(err, "比較分析を実行できませんでした。"), true);
        renderCompareControl();
      });
  }

  function applyCompareResult(data) {
    applyArxivBlocked(data.arxiv_blocked, blockedNoteFrom(data), "blocked");
    var items = data.items || [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      if (item.arxiv_id) state.compareById[item.arxiv_id] = item;
    }
    var notes = [];
    var skipped = data.skipped || [];
    for (var s = 0; s < skipped.length; s++) {
      var skip = skipped[s] || {};
      // サーバの detail を独自文で上書きしない（黙って落とさない）。
      notes.push(
        (skip.arxiv_id || "") + ": " + (skip.detail || "比較できませんでした。")
      );
    }
    var serverNotes = data.notes || [];
    for (var n = 0; n < serverNotes.length; n++) {
      notes.push(String(serverNotes[n]));
    }
    state.compareNotes = notes;
    setNotice("");
    renderFacts();
    renderCandidates();
    renderCompareControl();
  }

  // ── 取り込み（PR3: 既存の弁のみ。境界も事実文も分野購読モーダルと同一）────
  function usesBatchIngest(count) {
    return count > SYNC_INGEST_MAX;
  }

  function renderIngestSummary() {
    var summary = el("pr-ingest-summary");
    var button = el("pr-ingest-btn");
    if (!summary || !button) return;
    var ids = selectedIds();
    var overLimit = ids.length > BATCH_INGEST_MAX;
    var lines = [];
    if (overLimit) {
      lines.push(
        String(ids.length) +
          "件が選択されています。" +
          BATCH_LIMIT_NOTICE_HEAD +
          String(BATCH_INGEST_MAX) +
          BATCH_LIMIT_NOTICE_TAIL
      );
    } else if (usesBatchIngest(ids.length)) {
      lines.push(String(ids.length) + BATCH_NOTICE_TAIL);
    } else if (ids.length) {
      lines.push(String(ids.length) + INGEST_NOTICE_TAIL);
    } else {
      lines.push("取り込む論文を選択してください。");
    }
    if (state.domainAllowed === false) {
      lines.push(DOMAIN_BLOCKED_NOTICE);
    } else if (state.domainAllowed === null) {
      lines.push(DOMAIN_UNKNOWN_NOTICE);
    }
    summary.textContent = lines.join(" ");
    button.disabled =
      state.ingesting || !ids.length || overLimit || state.domainAllowed === false;
  }

  function runIngest() {
    if (state.ingesting) return;
    var button = el("pr-ingest-btn");
    if (button && button.disabled) return;
    var ids = selectedIds();
    if (!ids.length) return;
    if (ids.length > BATCH_INGEST_MAX) {
      // サーバも 422 で拒否する。ここでは先回りして事実文を出すだけ。
      setNotice(
        BATCH_LIMIT_NOTICE_HEAD + String(BATCH_INGEST_MAX) + BATCH_LIMIT_NOTICE_TAIL,
        true
      );
      return;
    }
    var batch = usesBatchIngest(ids.length);

    var items = [];
    for (var i = 0; i < ids.length; i++) {
      var candidate = findCandidate(ids[i]);
      var entry = { arxiv_id: ids[i] };
      // キュー行の表示に使うタイトルだけ添える（候補は保存しない — PR1）。
      if (batch && candidate && candidate.title) entry.title = candidate.title;
      items.push(entry);
    }
    var payload = uploadOptions();
    payload.items = items;
    // 取得する形式は教員が選んだ値をそのまま送る（サーバが語彙を検証する）。
    payload.source_format = state.sourceFormat;
    // 生成する言語: 選んだときだけ送る（同期の /ingest のみ。キューは言語を持たない）。
    var ingestLanguage = selectedLanguage();
    if (!batch && ingestLanguage) payload.language = ingestLanguage;
    // 解析の分野: 常に送る（「指定しない」は ""）。domain_key は監査の帰属で、解析には効かない。
    payload.cartridge_id = selectedIngestDomain();
    // 監査の帰属は seed の分野（引けなければサーバ側の既定に落ちる）。
    if (seedDomainKey()) payload.domain_key = seedDomainKey();

    state.ingesting = true;
    if (button) button.disabled = true;
    var result = el("pr-ingest-result");
    if (result) result.textContent = batch ? "キューに登録しています..." : "取得しています...";
    setNotice("");

    api(batch ? "/admin/discovery/ingest-batch" : "/admin/discovery/ingest", {
      method: "POST",
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.ingesting = false;
        if (batch) handleBatchResult(data || {});
        else handleIngestResult(data || {});
        renderCompareControl();
        renderIngestSummary();
      })
      .catch(function (err) {
        state.ingesting = false;
        if (result) result.textContent = "";
        // 422（件数上限・許可ドメイン未設定）はサーバの事実文をそのまま見せる。
        setNotice(detailText(err, "取り込みに失敗しました。"), true);
        renderIngestSummary();
      });
  }

  function handleIngestResult(data) {
    var accepted = data.accepted || [];
    var failed = data.failed || [];
    var lines = [];

    for (var i = 0; i < accepted.length; i++) {
      var item = accepted[i] || {};
      var arxivId = item.arxiv_id || "";
      // PD2: 受理後は既存アップロードと同じ合流点へ渡す（第2のポーリングを作らない）。
      if (deps && deps.onUploadAccepted) {
        deps.onUploadAccepted(item, item.filename || item.title || arxivId);
      }
      var candidate = findCandidate(arxivId);
      // 直前に取り込んだ事実の反映（サーバ側の判定は次回検索で読み時導出される）。
      if (candidate) candidate.status = "ingested";
      // 受理応答の material_id を覚えておく（教材の行・連鎖の導線に使う — §19 C）。
      if (arxivId && item.material_id) {
        state.acceptedById[arxivId] = {
          material_id: String(item.material_id),
          title: item.title || ""
        };
      }
      delete state.selected[arxivId];
    }

    if (accepted.length) {
      lines.push(
        String(accepted.length) +
          "件の取り込みを開始しました。進捗は教材一覧で確認できます。"
      );
    }
    for (var j = 0; j < failed.length; j++) {
      var failure = failed[j] || {};
      // サーバの detail を独自文で上書きしない。
      lines.push(
        (failure.arxiv_id || "") + ": " + (failure.detail || "取り込みに失敗しました。")
      );
    }
    if (!lines.length) lines.push("取り込まれた論文はありませんでした。");

    var result = el("pr-ingest-result");
    if (result) result.textContent = lines.join(" ／ ");
    renderCandidates();
  }

  // キュー投入は「取り込み完了」ではない。候補行の status をローカルで
  // "ingested" に書き換えない（PD6: 起きていないことを起きたように見せない）。
  function handleBatchResult(data) {
    var queued = data.queued || [];
    var skipped = data.skipped || [];
    var lines = [];

    for (var i = 0; i < queued.length; i++) {
      var item = queued[i] || {};
      if (item.arxiv_id) delete state.selected[item.arxiv_id];
    }

    if (queued.length) {
      lines.push(String(queued.length) + BATCH_QUEUED_NOTICE_TAIL);
    }
    for (var j = 0; j < skipped.length; j++) {
      var skip = skipped[j] || {};
      lines.push(
        (skip.arxiv_id || "") + ": " + (skip.detail || "キューに登録できませんでした。")
      );
    }
    if (data.notice) lines.push(String(data.notice));
    if (!lines.length) lines.push("キューに登録された論文はありませんでした。");

    var result = el("pr-ingest-result");
    if (result) result.textContent = lines.join(" ／ ");
    renderCandidates();
  }

  window.PaperRadar = {
    init: function (injected) {
      deps = injected || null;
    },
    openModal: openModal,
    close: close,
    onMaterialsUpdated: onMaterialsUpdated
  };
})();
