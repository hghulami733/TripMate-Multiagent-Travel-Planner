(() => {
  "use strict";

  /* ---------------------------------------------------------
     Constants / config
     --------------------------------------------------------- */
  const STORAGE_KEY = "tripmate_session_v1";

  const AGENT_META = {
    flight_agent:    { label: "Flights",   color: "var(--teal)"  },
    hotel_agent:     { label: "Hotels",    color: "var(--amber)" },
    weather_agent:   { label: "Weather",   color: "var(--sky)"   },
    budget_agent:    { label: "Budget",    color: "var(--coral)" },
    itinerary_agent: { label: "Itinerary", color: "var(--lilac)" },
  };
  const AGENT_ORDER = ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"];

  const CONSTRAINT_LABELS = {
    destination: "Destination",
    origin: "Origin",
    duration: "Duration",
    duraion: "Duration", // defensive: backend has a known typo for this key
    budget: "Budget",
    travel_style: "Style",
    special_preferences: "Preferences",
  };

  const LOADING_MESSAGES = [
    "Routing your request…",
    "Waking up the specialist agents…",
    "Checking flights and weather…",
    "Comparing places to stay…",
    "Sketching a day-by-day plan…",
  ];

  /* ---------------------------------------------------------
     DOM refs
     --------------------------------------------------------- */
  const heroEl = document.getElementById("hero");
  const formEl = document.getElementById("tripForm");
  const inputEl = document.getElementById("tripInput");
  const submitBtn = document.getElementById("submitBtn");
  const conversationEl = document.getElementById("conversation");
  const newTripBtn = document.getElementById("newTripBtn");
  const chipsEl = document.getElementById("suggestionChips");

  const tplUser = document.getElementById("tpl-turn-user");
  const tplLoading = document.getElementById("tpl-turn-loading");
  const tplBlocked = document.getElementById("tpl-turn-blocked");
  const tplError = document.getElementById("tpl-turn-error");
  const tplAssistant = document.getElementById("tpl-turn-assistant");

  /* ---------------------------------------------------------
     Session state
     --------------------------------------------------------- */
  let state = {
    threadId: null,
    hasConversation: false,
  };

  function loadSession() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.threadId) {
          state.threadId = parsed.threadId;
        }
      }
    } catch (_) { /* ignore corrupt storage */ }
  }

  function saveSession() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ threadId: state.threadId }));
    } catch (_) { /* storage unavailable — non-fatal */ }
  }

  function resetSession() {
    state.threadId = null;
    state.hasConversation = false;
    try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
    conversationEl.innerHTML = "";
    heroEl.hidden = false;
    newTripBtn.hidden = true;
    inputEl.value = "";
    inputEl.focus();
  }

  /* ---------------------------------------------------------
     Small helpers
     --------------------------------------------------------- */
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Minimal, safe markdown-lite renderer for LLM-produced text.
  // Escapes everything first, then re-introduces a small set of formatting.
  function formatContent(raw) {
    if (!raw) return "";
    const escaped = escapeHtml(String(raw));
    const lines = escaped.split(/\r?\n/);

    let html = "";
    let inList = false;

    const closeList = () => {
      if (inList) { html += "</ul>"; inList = false; }
    };

    for (let line of lines) {
      const trimmed = line.trim();

      if (!trimmed) { closeList(); continue; }

      const headingMatch = trimmed.match(/^#{1,4}\s+(.*)$/);
      if (headingMatch) {
        closeList();
        html += `<h4>${inlineFormat(headingMatch[1])}</h4>`;
        continue;
      }

      const bulletMatch = trimmed.match(/^[-*]\s+(.*)$/);
      if (bulletMatch) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inlineFormat(bulletMatch[1])}</li>`;
        continue;
      }

      const numberedMatch = trimmed.match(/^\d+\.\s+(.*)$/);
      if (numberedMatch) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inlineFormat(numberedMatch[1])}</li>`;
        continue;
      }

      closeList();
      html += `<p>${inlineFormat(trimmed)}</p>`;
    }
    closeList();
    return html || `<p>${escaped}</p>`;
  }

  function inlineFormat(text) {
    // bold **text** — safe because input was already HTML-escaped
    return text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function scrollToLatest(el) {
    requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  /* ---------------------------------------------------------
     API calls
     --------------------------------------------------------- */
  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
    if (!res.ok || !data || data.success === false) {
      const message = (data && data.error) || `Request failed (${res.status}).`;
      throw new Error(message);
    }
    return data;
  }

  /* ---------------------------------------------------------
     Rendering: user turn
     --------------------------------------------------------- */
  function renderUserTurn(text) {
    const node = tplUser.content.cloneNode(true);
    node.querySelector(".turn__text").textContent = text;
    conversationEl.appendChild(node);
  }

  /* ---------------------------------------------------------
     Rendering: loading turn (returns the element + a stop() fn)
     --------------------------------------------------------- */
  function renderLoadingTurn() {
    const node = tplLoading.content.cloneNode(true);
    const turnEl = node.querySelector(".turn");
    const msgEl = node.querySelector(".loading__msg");
    conversationEl.appendChild(node);
    scrollToLatest(turnEl);

    let i = 0;
    msgEl.textContent = LOADING_MESSAGES[0];
    const interval = setInterval(() => {
      i = (i + 1) % LOADING_MESSAGES.length;
      msgEl.textContent = LOADING_MESSAGES[i];
    }, 1600);

    return {
      el: turnEl,
      stop: () => clearInterval(interval),
    };
  }

  /* ---------------------------------------------------------
     Rendering: guardrail-blocked turn
     --------------------------------------------------------- */
  function renderBlockedTurn(reason) {
    const node = tplBlocked.content.cloneNode(true);
    node.querySelector(".blocked__text").textContent =
      reason || "That's outside what TripMate can help with — try asking about a destination, flight, hotel, weather, budget, or itinerary.";
    conversationEl.appendChild(node);
  }

  /* ---------------------------------------------------------
     Rendering: error turn
     --------------------------------------------------------- */
  function renderErrorTurn(message) {
    const node = tplError.content.cloneNode(true);
    node.querySelector(".error-card__text").textContent =
      "Something went wrong: " + message;
    conversationEl.appendChild(node);
  }

  /* ---------------------------------------------------------
     Rendering: route line (agent pipeline)
     --------------------------------------------------------- */
  function buildRoute(container, selectedAgents) {
    const agents = (selectedAgents && selectedAgents.length)
      ? AGENT_ORDER.filter((a) => selectedAgents.includes(a))
      : AGENT_ORDER;

    const track = document.createElement("div");
    track.className = "route__track";

    const fill = document.createElement("div");
    fill.className = "route__fill";
    track.appendChild(fill);

    const plane = document.createElement("div");
    plane.className = "route__plane";
    plane.innerHTML = `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 11.5L20.5 3.5L15 20.5L11.2 13.6L3 11.5Z" fill="currentColor"/>
    </svg>`;
    track.appendChild(plane);

    const stops = document.createElement("div");
    stops.className = "route__stops";

    agents.forEach((agent, idx) => {
      const meta = AGENT_META[agent] || { label: agent, color: "var(--coral)" };
      const stop = document.createElement("div");
      stop.className = "route__stop";

      const dot = document.createElement("div");
      dot.className = "route__dot";
      dot.style.setProperty("--stop-color", meta.color);
      dot.style.animationDelay = `${0.1 + idx * 0.12}s`;

      const label = document.createElement("div");
      label.className = "route__label";
      label.textContent = meta.label;

      stop.appendChild(dot);
      stop.appendChild(label);
      stops.appendChild(stop);
    });

    container.appendChild(track);
    container.appendChild(stops);
  }

  /* ---------------------------------------------------------
     Rendering: trip snapshot (constraints chips)
     --------------------------------------------------------- */
  function buildSnapshot(snapshotEl, chipsEl, constraints) {
    if (!constraints || typeof constraints !== "object") return;

    const seenLabels = new Set();
    let any = false;

    Object.entries(constraints).forEach(([key, value]) => {
      const label = CONSTRAINT_LABELS[key] || null;
      if (!label || seenLabels.has(label)) return;
      let display = "";
      if (Array.isArray(value)) {
        display = value.filter(Boolean).join(", ");
      } else if (value) {
        display = String(value);
      }
      if (!display.trim()) return;

      seenLabels.add(label);
      any = true;
      const chip = document.createElement("div");
      chip.className = "snap-chip";
      chip.innerHTML = `<b>${escapeHtml(label)}</b>${escapeHtml(display)}`;
      chipsEl.appendChild(chip);
    });

    if (any) snapshotEl.hidden = false;
  }

  /* ---------------------------------------------------------
     Rendering: tabbed result panel
     --------------------------------------------------------- */
  function buildPanel(panelEl, tabsEl, bodyEl, data) {
    const sources = [
      { key: "flight_agent", content: data.flight_results },
      { key: "hotel_agent", content: data.hotel_results },
      { key: "weather_agent", content: data.weather_results },
      { key: "budget_agent", content: data.budget_results },
      { key: "itinerary_agent", content: data.itinerary },
    ];

    const selected = data.selected_agents || [];
    const available = sources.filter(
      (s) => (selected.includes(s.key) || (s.content && String(s.content).trim())) && s.content && String(s.content).trim()
    );

    if (!available.length) return;

    available.forEach((source, idx) => {
      const meta = AGENT_META[source.key];
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tab-btn" + (idx === 0 ? " is-active" : "");
      btn.style.setProperty("--tab-color", meta.color);
      btn.setAttribute("role", "tab");
      btn.innerHTML = `<span class="tab-btn__swatch"></span>${escapeHtml(meta.label)}`;
      btn.addEventListener("click", () => {
        tabsEl.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        bodyEl.style.setProperty("--tab-color", meta.color);
        bodyEl.innerHTML = formatContent(source.content);
      });
      tabsEl.appendChild(btn);
    });

    bodyEl.style.setProperty("--tab-color", AGENT_META[available[0].key].color);
    bodyEl.innerHTML = formatContent(available[0].content);
    panelEl.hidden = false;
  }

  /* ---------------------------------------------------------
     Rendering: approval card
     --------------------------------------------------------- */
  function buildApproval(approvalEl, contentEl, draftText, onResolved) {
    contentEl.innerHTML = formatContent(draftText);
    approvalEl.hidden = false;

    const approveBtn = approvalEl.querySelector("[data-approve]");
    const requestChangesBtn = approvalEl.querySelector("[data-request-changes]");
    const feedbackBox = approvalEl.querySelector("[data-feedback-box]");
    const feedbackInput = approvalEl.querySelector("[data-feedback-input]");
    const sendFeedbackBtn = approvalEl.querySelector("[data-send-feedback]");

    function lockActions() {
      approveBtn.disabled = true;
      requestChangesBtn.disabled = true;
      sendFeedbackBtn.disabled = true;
      feedbackInput.disabled = true;
    }

    approveBtn.addEventListener("click", async () => {
      lockActions();
      try {
        const result = await postJSON("/api/travel/approve", {
          thread_id: state.threadId,
          approved: true,
          feedback: "",
        });
        approvalEl.hidden = true;
        onResolved(result);
      } catch (err) {
        lockActions();
        approveBtn.disabled = false;
        requestChangesBtn.disabled = false;
        renderErrorTurn(err.message);
      }
    });

    requestChangesBtn.addEventListener("click", () => {
      feedbackBox.hidden = false;
      requestChangesBtn.hidden = true;
      feedbackInput.focus();
    });

    feedbackInput.addEventListener("input", () => {
      sendFeedbackBtn.disabled = !feedbackInput.value.trim();
    });

    sendFeedbackBtn.addEventListener("click", async () => {
      const feedback = feedbackInput.value.trim();
      if (!feedback) return;
      approveBtn.disabled = true;
      sendFeedbackBtn.disabled = true;
      feedbackInput.disabled = true;
      try {
        const result = await postJSON("/api/travel/approve", {
          thread_id: state.threadId,
          approved: false,
          feedback,
        });
        approvalEl.hidden = true;
        onResolved(result);
      } catch (err) {
        approveBtn.disabled = false;
        sendFeedbackBtn.disabled = false;
        feedbackInput.disabled = false;
        renderErrorTurn(err.message);
      }
    });
  }

  /* ---------------------------------------------------------
     Rendering: final answer
     --------------------------------------------------------- */
  function buildFinal(finalEl, bodyEl, text) {
    bodyEl.innerHTML = formatContent(text);
    finalEl.hidden = false;

    const copyBtn = finalEl.querySelector("[data-copy]");
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text || "");
        const original = copyBtn.textContent;
        copyBtn.textContent = "Copied";
        setTimeout(() => { copyBtn.textContent = original; }, 1600);
      } catch (_) {
        copyBtn.textContent = "Couldn't copy";
      }
    });
  }

  /* ---------------------------------------------------------
     Assemble one assistant turn from an API result payload
     --------------------------------------------------------- */
  function renderAssistantResult(data) {
    const node = tplAssistant.content.cloneNode(true);
    const turnEl = node.querySelector(".turn");

    const routeEl = node.querySelector("[data-route]");
    const snapshotEl = node.querySelector("[data-snapshot]");
    const snapshotChipsEl = node.querySelector("[data-snapshot-chips]");
    const panelEl = node.querySelector("[data-panel]");
    const tabsEl = node.querySelector("[data-tabs]");
    const tabBodyEl = node.querySelector("[data-tab-body]");
    const approvalEl = node.querySelector("[data-approval]");
    const approvalContentEl = node.querySelector("[data-approval-content]");
    const finalEl = node.querySelector("[data-final]");
    const finalBodyEl = node.querySelector("[data-final-body]");

    if (data.guardrail_allowed === false) {
      routeEl.remove();
      renderBlockedTurnInline(turnEl, data.guardrail_reason);
      conversationEl.appendChild(node);
      scrollToLatest(turnEl);
      return;
    }

    buildRoute(routeEl, data.selected_agents);
    buildSnapshot(snapshotEl, snapshotChipsEl, data.trip_constraints);
    buildPanel(panelEl, tabsEl, tabBodyEl, data);

    if (data.requires_approval) {
      buildApproval(approvalEl, approvalContentEl, data.approval_request || data.itinerary || data.answer, (nextData) => {
        // Splice a follow-up assistant turn in after resolution
        renderAssistantResult(nextData);
        state.threadId = nextData.thread_id || state.threadId;
        saveSession();
      });
    } else {
      buildFinal(finalEl, finalBodyEl, data.answer);
    }

    conversationEl.appendChild(node);
    scrollToLatest(turnEl);
  }

  function renderBlockedTurnInline(turnEl, reason) {
    const wrap = document.createElement("div");
    wrap.className = "blocked";
    wrap.innerHTML = `<div class="blocked__icon">✦</div><p class="blocked__text"></p>`;
    wrap.querySelector(".blocked__text").textContent =
      reason || "That's outside what TripMate can help with — try asking about a destination, flight, hotel, weather, budget, or itinerary.";
    turnEl.appendChild(wrap);
  }

  /* ---------------------------------------------------------
     Submit flow
     --------------------------------------------------------- */
  async function handleSubmit(message) {
    if (!message.trim()) return;

    if (heroEl.hidden === false && !state.hasConversation) {
      // keep hero visible on first message too — just reveal "new trip"
    }
    state.hasConversation = true;
    newTripBtn.hidden = false;

    renderUserTurn(message);
    inputEl.value = "";
    submitBtn.disabled = true;

    const loading = renderLoadingTurn();

    try {
      const data = await postJSON("/api/travel", {
        message,
        thread_id: state.threadId,
      });
      state.threadId = data.thread_id;
      saveSession();
      loading.stop();
      loading.el.remove();
      renderAssistantResult(data);
    } catch (err) {
      loading.stop();
      loading.el.remove();
      renderErrorTurn(err.message);
    } finally {
      submitBtn.disabled = false;
      inputEl.focus();
    }
  }

  /* ---------------------------------------------------------
     Wire up events
     --------------------------------------------------------- */
  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSubmit(inputEl.value);
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      formEl.requestSubmit();
    }
  });

  chipsEl.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    inputEl.value = chip.textContent;
    inputEl.focus();
  });

  newTripBtn.addEventListener("click", resetSession);

  /* ---------------------------------------------------------
     Init
     --------------------------------------------------------- */
  loadSession();
  if (state.threadId) {
    newTripBtn.hidden = false;
    state.hasConversation = true;
  }
  inputEl.focus();
})();