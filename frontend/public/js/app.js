/**
 * Episteme Graph – Frontend chat application
 *
 * - Sends messages to /api/chat
 * - Renders LaTeX via KaTeX auto-render
 * - Detects [[ask:...]] tags and renders clickable suggest buttons (Task 4)
 */

(function () {
  "use strict";

  // ── DOM refs ──────────────────────────────────────────────────────────
  const messagesEl = document.getElementById("messages");
  const suggestBar = document.getElementById("suggest-bar");
  const chatForm = document.getElementById("chat-form");
  const userInput = document.getElementById("user-input");
  const sendBtn = document.getElementById("send-btn");

  // ── State ─────────────────────────────────────────────────────────────
  const history = []; // {role, content}[]
  const courseData = { mastered: [] }; // client-side learning state

  // ── Auto-resize textarea ──────────────────────────────────────────────
  userInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 150) + "px";
  });

  // Submit on Enter (Shift+Enter for newline)
  userInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event("submit"));
    }
  });

  // ── Form submit ───────────────────────────────────────────────────────
  chatForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const text = userInput.value.trim();
    if (!text) return;

    userInput.value = "";
    userInput.style.height = "auto";
    sendBtn.disabled = true;
    clearSuggestions();

    appendMessage("user", text);
    history.push({ role: "user", content: text });

    // Show loading indicator
    const loadingEl = appendMessage("assistant", "考え中");
    loadingEl.classList.add("loading-dots");

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: history.slice(-20), // keep last 20 turns
          course_data: courseData,
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
      }

      const data = await resp.json();

      // Remove loading indicator
      loadingEl.remove();

      // Display answer
      const answerEl = appendMessage("assistant", data.answer);
      renderLatex(answerEl);

      history.push({ role: "assistant", content: data.answer });

      // Render suggest links (Task 4)
      if (data.suggest_links && data.suggest_links.length > 0) {
        renderSuggestions(data.suggest_links);
      }

      // Also parse any remaining [[ask:...]] from the answer text itself
      parseInlineSuggestions(data.answer);
    } catch (err) {
      loadingEl.remove();
      appendMessage("assistant", "エラーが発生しました: " + err.message);
    } finally {
      sendBtn.disabled = false;
      userInput.focus();
    }
  });

  // ── Message rendering ─────────────────────────────────────────────────

  function appendMessage(role, text) {
    const el = document.createElement("div");
    el.className = "message " + role;
    el.innerHTML = markdownToHtml(text);
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  /**
   * Minimal markdown → HTML conversion.
   * Handles: **bold**, *italic*, `code`, ```blocks```, lists, paragraphs.
   * Preserves LaTeX delimiters ($...$, $$...$$) for KaTeX.
   */
  function markdownToHtml(md) {
    // Protect LaTeX blocks from markdown processing
    const latexBlocks = [];
    let processed = md.replace(/\$\$[\s\S]*?\$\$/g, function (m) {
      latexBlocks.push(m);
      return "%%LATEX_BLOCK_" + (latexBlocks.length - 1) + "%%";
    });
    const latexInlines = [];
    processed = processed.replace(/\$[^$\n]+?\$/g, function (m) {
      latexInlines.push(m);
      return "%%LATEX_INLINE_" + (latexInlines.length - 1) + "%%";
    });

    // Code blocks
    processed = processed.replace(
      /```(\w*)\n([\s\S]*?)```/g,
      '<pre><code class="language-$1">$2</code></pre>'
    );

    // Inline code
    processed = processed.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Bold / italic
    processed = processed.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    processed = processed.replace(/\*(.+?)\*/g, "<em>$1</em>");

    // Line-based processing
    const lines = processed.split("\n");
    let html = "";
    let inList = false;

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        if (!inList) {
          html += "<ul>";
          inList = true;
        }
        html += "<li>" + trimmed.slice(2) + "</li>";
      } else {
        if (inList) {
          html += "</ul>";
          inList = false;
        }
        if (trimmed === "") {
          html += "<br/>";
        } else {
          html += "<p>" + trimmed + "</p>";
        }
      }
    }
    if (inList) html += "</ul>";

    // Restore LaTeX
    html = html.replace(/%%LATEX_BLOCK_(\d+)%%/g, function (_, i) {
      return latexBlocks[parseInt(i)];
    });
    html = html.replace(/%%LATEX_INLINE_(\d+)%%/g, function (_, i) {
      return latexInlines[parseInt(i)];
    });

    return html;
  }

  // ── LaTeX rendering via KaTeX ─────────────────────────────────────────

  function renderLatex(el) {
    if (typeof renderMathInElement === "undefined") {
      // KaTeX not yet loaded; retry after a short delay
      setTimeout(function () { renderLatex(el); }, 200);
      return;
    }
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
      ],
      throwOnError: false,
    });
  }

  // ── Suggest buttons (Task 4) ──────────────────────────────────────────

  function clearSuggestions() {
    suggestBar.innerHTML = "";
  }

  function renderSuggestions(links) {
    for (const link of links) {
      const btn = document.createElement("button");
      btn.className = "suggest-btn";
      btn.textContent = link.label;
      btn.addEventListener("click", function () {
        userInput.value = link.query;
        chatForm.dispatchEvent(new Event("submit"));
      });
      suggestBar.appendChild(btn);
    }
  }

  /**
   * Parse [[ask:...]] patterns from raw answer text and add as suggestions
   * (fallback in case the backend didn't extract them into suggest_links).
   */
  function parseInlineSuggestions(text) {
    const pattern = /\[\[ask:(.*?)\]\]/g;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const label = match[1].trim();
      // Avoid duplicates
      const existing = suggestBar.querySelectorAll(".suggest-btn");
      let dup = false;
      existing.forEach(function (btn) {
        if (btn.textContent === label) dup = true;
      });
      if (!dup) {
        renderSuggestions([{ label: label, query: label }]);
      }
    }
  }

  // ── Scroll helper ─────────────────────────────────────────────────────

  function scrollToBottom() {
    const container = document.getElementById("chat-container");
    container.scrollTop = container.scrollHeight;
  }
})();
