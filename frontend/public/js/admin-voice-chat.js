/**
 * 管理画面向け ハンズフリー音声対話エンジン（DOM 非依存）
 *
 * MediaRecorder + WebAudio の無音検知（VAD）で発話を区切り、
 *   文字起こし → 呼び出し側の応答生成 → 応答の読み上げ → 聞き取り再開
 * のループだけを担う。画面・API・文言の宛先はすべて呼び出し側がコールバックで
 * 注入する（このファイルは DOM 要素 ID も API パスも持たない）。
 *
 * - ES5 / IIFE。window.AdminVoiceChat を公開（開発ルール5）。
 * - VAD の定数・セグメント方式は学習画面（app.js のハンズフリー音声会話）と同一。
 *   app.js は静的テストが本文を直接検査しているため変更せず、こちらへ移植する。
 * - 数値（残回数・秒数）は UI へ出さない。エラーは日本語の事実文のみ。
 *
 * 使い方:
 *   var loop = AdminVoiceChat.createLoop({
 *     transcribe: function (blob, done) { done(err, text); },
 *     speak:      function (text, done) { done(err, played); },
 *     onUtterance: function (text, done) { done(err, replyText); },
 *     onStatus:   function (kind, label) {},   // listening|thinking|speaking|error|off
 *     onTranscript: function (text) {},        // 任意
 *     onStopped:  function () {}               // 任意
 *   });
 *   loop.start(); loop.stop(); loop.isActive();
 */
(function () {
  "use strict";

  var VOICE_SILENCE_MS = 1400;      // 発話後この長さの沈黙で区切って送信
  var VOICE_MIN_SPEECH_MS = 400;    // これ未満の発話は物音とみなして破棄
  var VOICE_RMS_THRESHOLD = 0.015;  // 発話とみなす音量（RMS）
  var VOICE_IDLE_RESET_MS = 60000;  // 無発話でセグメントを作り直す（メモリ抑制）
  var VOICE_ERROR_FEEDBACK_COOLDOWN_MS = 15000; // 定型文の読み上げはこの間隔をあける
  var VOICE_MIN_BLOB_BYTES = 1000;  // これ未満は無音とみなして送らない

  var MSG_ERROR = "エラーが発生しました。もう一度どうぞ。";
  var MSG_UNSUPPORTED = "このブラウザは録音に対応していません。Chrome または Edge をお試しください。";
  var MSG_NO_MIC = "マイクの使用が許可されていません。ブラウザのアドレスバーからマイクの許可を確認してください。";
  var MSG_SPEAK_FAILED = "音声を再生できませんでした。応答はチャット欄に表示されています。";

  var LABEL_PREPARING = "マイクを準備しています…";
  var LABEL_LISTENING = "聞いています… 話し終えると自動で送信されます";
  var LABEL_HEARING = "聞き取り中…";
  var LABEL_TRANSCRIBING = "文字起こし中…";
  var LABEL_THINKING = "AI が応答を作成中…";
  var LABEL_SPEAKING = "AI が話しています…";

  function noop() {}

  function pickMimeType() {
    var candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    for (var i = 0; i < candidates.length; i++) {
      if (window.MediaRecorder && window.MediaRecorder.isTypeSupported &&
          window.MediaRecorder.isTypeSupported(candidates[i])) {
        return candidates[i];
      }
    }
    return "";
  }

  function isSupported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder &&
      (window.AudioContext || window.webkitAudioContext));
  }

  function stopTracks(stream) {
    if (!stream || !stream.getTracks) return;
    stream.getTracks().forEach(function (track) {
      try { track.stop(); } catch (e) { /* noop */ }
    });
  }

  function createLoop(options) {
    var opts = options || {};
    var transcribe = typeof opts.transcribe === "function" ? opts.transcribe : null;
    var speak = typeof opts.speak === "function" ? opts.speak : null;
    var onUtterance = typeof opts.onUtterance === "function" ? opts.onUtterance : null;
    var onStatus = typeof opts.onStatus === "function" ? opts.onStatus : noop;
    var onTranscript = typeof opts.onTranscript === "function" ? opts.onTranscript : noop;
    var onStopped = typeof opts.onStopped === "function" ? opts.onStopped : noop;

    var st = {
      active: false,
      starting: false,
      stream: null,
      recorder: null,
      chunks: [],
      mimeType: "",
      audioCtx: null,
      analyser: null,
      vadTimer: null,
      speechDetected: false,
      speechStart: 0,
      silenceStart: 0,
      segmentStart: 0,
      busy: false,               // 文字起こし〜応答再生中（この間は区切り検知を止める）
      lastErrorFeedbackAt: 0,
    };

    function status(kind, label) {
      try { onStatus(kind, label); } catch (e) { /* 表示側の失敗でループを壊さない */ }
    }

    // -----------------------------------------------------------------
    // 開始・終了
    // -----------------------------------------------------------------

    function start() {
      if (st.active || st.starting) return;
      if (!isSupported()) {
        status("error", MSG_UNSUPPORTED);
        return;
      }
      st.starting = true;
      status("thinking", LABEL_PREPARING);
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        st.starting = false;
        if (st.active) { stopTracks(stream); return; }
        var AudioCtx = window.AudioContext || window.webkitAudioContext;
        var ctx;
        try {
          ctx = new AudioCtx();
          var source = ctx.createMediaStreamSource(stream);
          st.analyser = ctx.createAnalyser();
          st.analyser.fftSize = 2048;
          source.connect(st.analyser);
        } catch (e) {
          stopTracks(stream);
          st.analyser = null;
          status("error", MSG_UNSUPPORTED);
          return;
        }
        st.audioCtx = ctx;
        st.stream = stream;
        st.mimeType = pickMimeType();
        st.active = true;
        st.busy = false;
        st.lastErrorFeedbackAt = 0;
        status("listening", LABEL_LISTENING);
        startSegment();
        st.vadTimer = window.setInterval(vadTick, 100);
      }, function () {
        st.starting = false;
        status("error", MSG_NO_MIC);
      });
    }

    function stop() {
      var wasActive = st.active || st.starting;
      st.active = false;
      st.starting = false;
      st.busy = false;
      if (st.vadTimer) { window.clearInterval(st.vadTimer); st.vadTimer = null; }
      if (st.recorder && st.recorder.state !== "inactive") {
        st.recorder.onstop = null; // 終了時の残りセグメントは送信しない
        try { st.recorder.stop(); } catch (e) { /* noop */ }
      }
      st.recorder = null;
      st.chunks = [];
      stopTracks(st.stream);
      st.stream = null;
      if (st.audioCtx) {
        try { st.audioCtx.close(); } catch (e) { /* noop */ }
        st.audioCtx = null;
      }
      st.analyser = null;
      if (!wasActive) return;
      status("off", "");
      try { onStopped(); } catch (e) { /* noop */ }
    }

    // -----------------------------------------------------------------
    // 録音セグメントと無音検知
    // -----------------------------------------------------------------

    function startSegment() {
      if (!st.active || !st.stream) return;
      st.chunks = [];
      st.speechDetected = false;
      st.speechStart = 0;
      st.silenceStart = 0;
      st.segmentStart = Date.now();
      var recorder;
      try {
        recorder = st.mimeType
          ? new window.MediaRecorder(st.stream, { mimeType: st.mimeType })
          : new window.MediaRecorder(st.stream);
      } catch (e) {
        status("error", MSG_UNSUPPORTED);
        stop();
        return;
      }
      recorder.ondataavailable = function (e) {
        if (e.data && e.data.size > 0) st.chunks.push(e.data);
      };
      recorder.onstop = function () { handleSegment(); };
      try { recorder.start(); } catch (e) {
        status("error", MSG_UNSUPPORTED);
        stop();
        return;
      }
      st.recorder = recorder;
    }

    function restartSegment() {
      if (!st.recorder) return;
      st.recorder.onstop = null; // 破棄（handleSegment を呼ばない）
      try { st.recorder.stop(); } catch (e) { /* noop */ }
      startSegment();
    }

    function currentRms() {
      var analyser = st.analyser;
      if (!analyser) return 0;
      var buf = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(buf);
      var sum = 0;
      for (var i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      return Math.sqrt(sum / buf.length);
    }

    function vadTick() {
      if (!st.active || st.busy || !st.recorder) return;
      var now = Date.now();
      var rms = currentRms();
      if (rms >= VOICE_RMS_THRESHOLD) {
        if (!st.speechDetected) {
          st.speechDetected = true;
          st.speechStart = now;
          status("listening", LABEL_HEARING);
        }
        st.silenceStart = 0;
        return;
      }
      if (st.speechDetected) {
        if (!st.silenceStart) {
          st.silenceStart = now;
        } else if (now - st.silenceStart >= VOICE_SILENCE_MS) {
          // 会話の区切れ目: 発話が短すぎればセグメントを捨てて作り直す
          if (st.silenceStart - st.speechStart < VOICE_MIN_SPEECH_MS) {
            restartSegment();
          } else if (st.recorder.state !== "inactive") {
            st.busy = true; // onstop → handleSegment で送信
            try { st.recorder.stop(); } catch (e) { st.busy = false; }
          }
        }
      } else if (now - st.segmentStart >= VOICE_IDLE_RESET_MS) {
        restartSegment(); // 長時間無発話: 録りっぱなしを避ける
      }
    }

    // -----------------------------------------------------------------
    // 1発話ぶんの処理（文字起こし → 応答 → 読み上げ → 再開）
    // -----------------------------------------------------------------

    function handleSegment() {
      if (!st.active) return;
      var blob = new Blob(st.chunks, { type: st.mimeType || "audio/webm" });
      st.chunks = [];
      if (blob.size < VOICE_MIN_BLOB_BYTES) { resumeListening(); return; }
      if (!transcribe || !onUtterance) { errorFeedback(); return; }

      status("thinking", LABEL_TRANSCRIBING);
      transcribe(blob, function (err, text) {
        if (!st.active) return;
        if (err) { errorFeedback(); return; }
        var spoken = String(text == null ? "" : text).trim();
        // 無音・聞き取れずはエラーではない（黙って聞き取りに戻る）。
        if (!spoken) { resumeListening(); return; }
        try { onTranscript(spoken); } catch (e) { /* noop */ }
        status("thinking", LABEL_THINKING);
        onUtterance(spoken, function (replyErr, reply) {
          if (!st.active) return;
          if (replyErr) { errorFeedback(); return; }
          var replyText = String(reply == null ? "" : reply).trim();
          if (!replyText) { resumeListening(); return; }
          speakReply(replyText);
        });
      });
    }

    function speakReply(text) {
      if (!speak) { resumeListening(); return; }
      status("speaking", LABEL_SPEAKING);
      speak(text, function (err, played) {
        if (!st.active) return;
        var failed = !!(err || !played);
        if (failed) status("error", MSG_SPEAK_FAILED);
        // 失敗の事実文は消さずに残す（次の発話検知で「聞き取り中…」に変わる）。
        resumeListening(failed);
      });
    }

    // 失敗を無言で聞き取りに戻さない。可能なら短い定型文を読み上げ、読み上げ自体が
    // 失敗している場合は表示のみに縮退する（再帰・自動再試行はしない）。
    function errorFeedback() {
      if (!st.active) return;
      status("error", MSG_ERROR);
      var now = Date.now();
      if (!speak || (st.lastErrorFeedbackAt && now - st.lastErrorFeedbackAt < VOICE_ERROR_FEEDBACK_COOLDOWN_MS)) {
        resumeListening(true); // 表示のみに縮退（事実文は残す）
        return;
      }
      st.lastErrorFeedbackAt = now;
      speak(MSG_ERROR, function () {
        if (!st.active) return;
        resumeListening(true);
      });
    }

    // keepStatus=true のときは直前に出した事実文を消さずに聞き取りへ戻る。
    function resumeListening(keepStatus) {
      if (!st.active) return;
      st.busy = false;
      if (!keepStatus) status("listening", LABEL_LISTENING);
      startSegment();
    }

    return {
      start: start,
      stop: stop,
      isActive: function () { return !!st.active; },
    };
  }

  window.AdminVoiceChat = {
    createLoop: createLoop,
    isSupported: isSupported,
  };
})();
