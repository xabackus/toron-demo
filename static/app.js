/* ================================================================
   Toron Demo — Frontend Logic
   ================================================================ */

let sessionId = null;
let apiKey = null;
let notesOpen = false;


/* ----------------------------------------------------------------
   Setup → Start Debate
   ---------------------------------------------------------------- */

async function startDebate() {
  const keyInput = document.getElementById("api-key");
  const topicInput = document.getElementById("topic");
  const sideInput = document.querySelector('input[name="side"]:checked');
  const diffInput = document.getElementById("difficulty");
  const errorEl = document.getElementById("setup-error");

  errorEl.textContent = "";

  apiKey = keyInput.value.trim();
  const topic = topicInput.value.trim();

  if (!apiKey) { errorEl.textContent = "API key is required."; return; }
  if (!topic) { errorEl.textContent = "Enter a debate topic."; return; }

  showLoading("Preparing your opponent...");

  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        topic: topic,
        user_side: sideInput.value,
        difficulty: diffInput.value,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }

    const data = await res.json();
    sessionId = data.session_id;

    // Switch screens
    document.getElementById("setup-screen").classList.remove("active");
    document.getElementById("debate-screen").classList.add("active");
    document.getElementById("header-topic").textContent = topic;

    // Render AI opening
    appendAIMessage(data.debate_response, data.coach_feedback);
    updateNotes(data.notes);
    updateTurn(data.turn);
  } catch (e) {
    errorEl.textContent = e.message;
  } finally {
    hideLoading();
  }
}


/* ----------------------------------------------------------------
   Send Message
   ---------------------------------------------------------------- */

async function sendMessage() {
  const input = document.getElementById("user-input");
  const message = input.value.trim();
  if (!message || !sessionId) return;

  input.value = "";
  input.style.height = "auto";
  appendUserMessage(message);
  showLoading("Crafting a rebuttal...");

  try {
    const res = await fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        session_id: sessionId,
        message: message,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }

    const data = await res.json();
    appendAIMessage(data.debate_response, data.coach_feedback);
    updateNotes(data.notes);
    updateTurn(data.turn);
  } catch (e) {
    appendSystemMessage("Error: " + e.message);
  } finally {
    hideLoading();
  }
}


/* ----------------------------------------------------------------
   End Debate → Report Card
   ---------------------------------------------------------------- */

async function endDebate() {
  if (!sessionId) return;
  if (!confirm("End the debate and generate your report card?")) return;

  showLoading("Judging your performance...");

  try {
    const res = await fetch("/api/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey, session_id: sessionId }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }

    const data = await res.json();
    renderReportCard(data.report);
  } catch (e) {
    alert("Failed to generate report: " + e.message);
  } finally {
    hideLoading();
  }
}


/* ----------------------------------------------------------------
   Chat rendering
   ---------------------------------------------------------------- */

function appendAIMessage(text, feedback) {
  const container = document.getElementById("chat-messages");

  let html = `
    <div class="msg ai">
      <div class="msg-label">Opponent</div>
      <div class="msg-bubble">${escapeHtml(text)}</div>`;

  if (feedback && (feedback.praise || feedback.criticism)) {
    html += `<div class="coach-feedback">
      <strong>Coach Feedback</strong>`;
    if (feedback.praise) {
      html += `<div class="feedback-praise">${escapeHtml(feedback.praise)}</div>`;
    }
    if (feedback.criticism) {
      html += `<div class="feedback-criticism">${escapeHtml(feedback.criticism)}</div>`;
    }
    html += `</div>`;
  }

  html += `</div>`;
  container.insertAdjacentHTML("beforeend", html);
  container.scrollTop = container.scrollHeight;
}

function appendUserMessage(text) {
  const container = document.getElementById("chat-messages");
  container.insertAdjacentHTML("beforeend", `
    <div class="msg user">
      <div class="msg-label">You</div>
      <div class="msg-bubble">${escapeHtml(text)}</div>
    </div>
  `);
  container.scrollTop = container.scrollHeight;
}

function appendSystemMessage(text) {
  const container = document.getElementById("chat-messages");
  container.insertAdjacentHTML("beforeend", `
    <div class="msg ai">
      <div class="msg-bubble" style="color:var(--danger);border-color:var(--danger);">
        ${escapeHtml(text)}
      </div>
    </div>
  `);
  container.scrollTop = container.scrollHeight;
}


/* ----------------------------------------------------------------
   Notes panel
   ---------------------------------------------------------------- */

function toggleNotes() {
  const panel = document.getElementById("notes-panel");
  const btn = document.getElementById("toggle-notes-btn");
  notesOpen = !notesOpen;
  panel.classList.toggle("collapsed", !notesOpen);
  btn.textContent = notesOpen ? "Notes ◂" : "Notes ▸";
}

function updateNotes(notes) {
  if (!notes) return;
  renderNotesList("notes-ai-points", notes.ai_points);
  renderNotesList("notes-student-points", notes.student_points);
  renderNotesList("notes-coach-obs", notes.coach_observations);
}

function renderNotesList(elId, items) {
  const ul = document.getElementById(elId);
  if (!items || items.length === 0) {
    ul.innerHTML = '<li class="empty">None yet.</li>';
    return;
  }
  ul.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function updateTurn(turn) {
  document.getElementById("turn-counter").textContent = `Turn ${turn}`;
}


/* ----------------------------------------------------------------
   Report card
   ---------------------------------------------------------------- */

function renderReportCard(report) {
  document.getElementById("report-grade").textContent = report.overall_grade || "—";
  document.getElementById("report-summary").textContent = report.summary || "";
  document.getElementById("report-strength").textContent = report.strongest_moment || "";
  document.getElementById("report-weakness").textContent = report.biggest_weakness || "";

  // Scores
  const scoresEl = document.getElementById("report-scores");
  scoresEl.innerHTML = "";

  const scoreLabels = {
    argument_structure: "Argument Structure",
    evidence_and_reasoning: "Evidence & Reasoning",
    rebuttal_quality: "Rebuttal Quality",
    persuasiveness: "Persuasiveness",
    composure_and_clarity: "Composure & Clarity",
  };

  if (report.scores) {
    for (const [key, label] of Object.entries(scoreLabels)) {
      const s = report.scores[key];
      if (!s) continue;
      const pct = (s.score / 10) * 100;
      const color = scoreColor(s.score);

      scoresEl.insertAdjacentHTML("beforeend", `
        <div class="score-row">
          <div class="score-row-top">
            <span class="score-label">${label}</span>
            <span class="score-value">${s.score}/10</span>
          </div>
          <div class="score-bar-bg">
            <div class="score-bar-fill" style="width:${pct}%;background:${color};"></div>
          </div>
          <div class="score-rationale">${escapeHtml(s.rationale || "")}</div>
        </div>
      `);
    }
  }

  // Takeaways
  const takeawaysEl = document.getElementById("report-takeaways");
  takeawaysEl.innerHTML = "";
  if (report.key_takeaways) {
    report.key_takeaways.forEach((t) => {
      takeawaysEl.insertAdjacentHTML("beforeend", `<li>${escapeHtml(t)}</li>`);
    });
  }

  document.getElementById("report-overlay").classList.add("active");
}

function scoreColor(score) {
  if (score >= 8) return "#059669";
  if (score >= 6) return "#2563eb";
  if (score >= 4) return "#d97706";
  return "#dc2626";
}


/* ----------------------------------------------------------------
   Utilities
   ---------------------------------------------------------------- */

function resetApp() {
  sessionId = null;
  document.getElementById("report-overlay").classList.remove("active");
  document.getElementById("debate-screen").classList.remove("active");
  document.getElementById("setup-screen").classList.add("active");
  document.getElementById("chat-messages").innerHTML = "";
  document.getElementById("user-input").value = "";
  document.getElementById("notes-ai-points").innerHTML = '<li class="empty">None yet.</li>';
  document.getElementById("notes-student-points").innerHTML = '<li class="empty">None yet.</li>';
  document.getElementById("notes-coach-obs").innerHTML = '<li class="empty">None yet.</li>';
}

function showLoading(text) {
  document.getElementById("loading-text").textContent = text || "Thinking...";
  document.getElementById("loading-overlay").classList.add("active");
  document.getElementById("send-btn").disabled = true;
}

function hideLoading() {
  document.getElementById("loading-overlay").classList.remove("active");
  document.getElementById("send-btn").disabled = false;
}

function handleInputKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
