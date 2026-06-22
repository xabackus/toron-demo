/* ================================================================
   Toron Demo — Frontend Logic
   ================================================================
   Vanilla JS — no framework, no build step. Three screens:
     1. Setup:  API key, topic, side, opponent difficulty, coach strictness → POST /api/start
     2. Debate: Chat with opponent, toggle coach feedback + notes
     3. Report: Modal overlay with scores, generated via POST /api/end

   State is minimal: session ID, API key, latest notes, and UI toggles.
   All debate history lives server-side in the FastAPI session store.

   Key JS patterns used here:
     - document.getElementById("x")    → find an HTML element by its id="x"
     - el.classList.add/remove/toggle   → add/remove CSS classes on an element
     - el.insertAdjacentHTML("beforeend", html) → append HTML string as last child
     - fetch(url, options)              → make an HTTP request (like requests.post)
     - async/await                      → same as Python's async/await
   ================================================================ */


/* ----------------------------------------------------------------
   Global state
   ---------------------------------------------------------------- */
let sessionId = null;       // UUID from /api/start — identifies our server-side session
let apiKey = null;           // OpenAI key — entered by user, sent with every request
let notesOpen = false;       // Whether the notes side panel is visible
let coachVisible = false;    // Whether inline coach feedback is visible
let latestNotes = null;      // Most recent cumulative notes object (for download)
let debateTopic = "";        // Stored so we can include it in the notes download header


/* ----------------------------------------------------------------
   Setup → Start Debate
   ----------------------------------------------------------------
   Calls POST /api/start to create a session. No OpenAI call happens
   here — the student speaks first, so the backend just sets up state
   and returns a session_id. The chat opens empty.
   ---------------------------------------------------------------- */

async function startDebate() {
  // Grab values from the setup form.
  // document.getElementById returns the HTML element with that id.
  // .value gets the text content of an input/select element.
  const keyInput = document.getElementById("api-key");
  const topicInput = document.getElementById("topic");
  // querySelector finds the first element matching a CSS selector.
  // 'input[name="side"]:checked' finds the selected radio button in the "side" group.
  const sideInput = document.querySelector('input[name="side"]:checked');
  const opponentDifficultyInput =
    document.getElementById("opponent-difficulty");
  const coachDifficultyInput =
    document.getElementById("coach-difficulty");
  const errorEl = document.getElementById("setup-error");

  // Clear any previous error message
  errorEl.textContent = "";

  // .trim() removes leading/trailing whitespace
  apiKey = keyInput.value.trim();
  const topic = topicInput.value.trim();

  // Client-side validation before hitting the server
  if (!apiKey) { errorEl.textContent = "API key is required."; return; }
  if (!topic) { errorEl.textContent = "Enter a debate topic."; return; }

  try {
    // fetch() is the JS equivalent of requests.post().
    // It returns a Response object (not the body directly).
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // JSON.stringify converts a JS object to a JSON string (like json.dumps)
      body: JSON.stringify({
        api_key: apiKey,
        topic: topic,
        user_side: sideInput.value,    // "for" or "against"
        opponent_difficulty: opponentDifficultyInput.value,
        coach_difficulty: coachDifficultyInput.value,
      }),
    });

    // res.ok is true if status is 200-299
    if (!res.ok) {
      // Try to parse error details from the response body.
      // .catch(() => ({})) returns empty object if JSON parsing fails.
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }

    // Parse the response body as JSON. Returns {session_id: "uuid"}.
    const data = await res.json();
    sessionId = data.session_id;
    debateTopic = topic;

    // Switch screens by toggling the .active CSS class.
    // .active sets display:flex; without it, display:none.
    document.getElementById("setup-screen").classList.remove("active");  // hide setup
    document.getElementById("debate-screen").classList.add("active");    // show debate

    // Display the topic in the header bar
    document.getElementById("header-topic").textContent = topic;

    // Show "Turn 0" — the student hasn't spoken yet
    updateTurn(0);
  } catch (e) {
    // Display error in the setup form (e.g., "Incorrect API key provided")
    errorEl.textContent = e.message;
  }
}


/* ----------------------------------------------------------------
   Send Message (streaming)
   ----------------------------------------------------------------
   Calls POST /api/message, which returns a Server-Sent Events (SSE)
   stream instead of a single JSON response:

     1. 'token' events — opponent's text, one chunk at a time (~200ms to first)
     2. 'coach' event  — feedback + cumulative notes (after opponent finishes)
     3. 'done' event   — turn number, signals stream complete
     4. 'error' event  — if something went wrong server-side

   SSE format: "event: <type>\ndata: <json>\n\n"
   We parse these using fetch + ReadableStream (not EventSource, which
   only supports GET requests).
   ---------------------------------------------------------------- */

async function sendMessage() {
  const input = document.getElementById("user-input");
  const message = input.value.trim();

  // Don't send empty messages or if no session exists
  if (!message || !sessionId) return;

  // Clear the textarea immediately (before the API call)
  input.value = "";
  input.style.height = "auto";

  // Show the user's message in the chat right away
  appendUserMessage(message);

  // Disable send button while streaming (re-enabled in finally block)
  document.getElementById("send-btn").disabled = true;

  // Create an empty opponent message bubble. We'll append tokens to it
  // as they stream in, then attach coach feedback after.
  const msgId = "msg-" + Date.now();
  const bubbleId = "bubble-" + Date.now();
  const container = document.getElementById("chat-messages");
  container.insertAdjacentHTML("beforeend", `
    <div class="msg ai" id="${msgId}">
      <div class="msg-label">Opponent</div>
      <div class="msg-bubble" id="${bubbleId}"></div>
    </div>
  `);

  try {
    // POST request — response is an SSE stream, not JSON
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

    // Read the SSE stream using the ReadableStream API.
    // response.body.getReader() returns a reader that yields Uint8Array chunks.
    // TextDecoder converts bytes to string. { stream: true } handles chunks
    // that split a multi-byte character across two reads.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    const bubble = document.getElementById(bubbleId);

    let buffer = "";  // accumulates incomplete SSE events

    while (true) {
      // reader.read() returns { done: bool, value: Uint8Array }
      const { done, value } = await reader.read();
      if (done) break;

      // Decode bytes to string and add to buffer
      buffer += decoder.decode(value, { stream: true });

      // SSE events are delimited by double newlines (\n\n).
      // Split the buffer on \n\n to extract complete events.
      // The last element may be incomplete — keep it in buffer.
      const parts = buffer.split("\n\n");
      buffer = parts.pop();

      for (const part of parts) {
        if (!part.trim()) continue;

        // Parse SSE event: "event: <type>\ndata: <json>"
        let eventType = "";
        let data = "";
        for (const line of part.split("\n")) {
          if (line.startsWith("event: ")) eventType = line.slice(7);
          else if (line.startsWith("data: ")) data = line.slice(6);
        }

        if (eventType === "token") {
          // Append this text chunk to the opponent's bubble.
          // Using textContent += auto-escapes HTML (XSS-safe).
          const { t } = JSON.parse(data);
          bubble.textContent += t;
          // Auto-scroll as tokens arrive
          container.scrollTop = container.scrollHeight;

        } else if (eventType === "coach") {
          // Coach feedback + cumulative notes arrive as a single event
          // after the opponent finishes streaming.
          const { coach_feedback, notes } = JSON.parse(data);

          // Append coach feedback below the opponent's bubble.
          // Uses the message container ID to insert after the bubble.
          if (coach_feedback && (coach_feedback.praise || coach_feedback.criticism)) {
            const msgDiv = document.getElementById(msgId);
            let html = `<div class="coach-feedback"><strong>Coach Feedback</strong>`;
            if (coach_feedback.praise) {
              html += `<div class="feedback-praise">${escapeHtml(coach_feedback.praise)}</div>`;
            }
            if (coach_feedback.criticism) {
              html += `<div class="feedback-criticism">${escapeHtml(coach_feedback.criticism)}</div>`;
            }
            html += `</div>`;
            msgDiv.insertAdjacentHTML("beforeend", html);
          }

          // Update the notes side panel
          updateNotes(notes);

        } else if (eventType === "done") {
          const { turn } = JSON.parse(data);
          updateTurn(turn);

        } else if (eventType === "error") {
          const { detail } = JSON.parse(data);
          appendSystemMessage("Error: " + detail);
        }
      }
    }
  } catch (e) {
    appendSystemMessage("Error: " + e.message);
  } finally {
    // Re-enable the send button
    document.getElementById("send-btn").disabled = false;
  }
}


/* ----------------------------------------------------------------
   End Debate → Report Card
   ----------------------------------------------------------------
   Calls POST /api/end, which builds a transcript from the opponent's
   conversation history and sends it to a THIRD model instance (fresh
   context, lower temperature) for holistic scoring. The session is
   deleted server-side after this call. Final notes are captured for
   the download feature.
   ---------------------------------------------------------------- */

async function endDebate() {
  if (!sessionId) return;

  // confirm() shows a browser dialog with OK/Cancel buttons.
  // Returns true if OK, false if Cancel.
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

    // Response contains {report: {...}, notes: {...}}
    const data = await res.json();

    // Capture final notes — the session is deleted server-side,
    // so this is the last chance to grab them for download
    if (data.notes) latestNotes = data.notes;

    // Render the report card modal (scores, takeaways, etc.)
    renderReportCard(data.report);
  } catch (e) {
    // alert() is a blocking browser dialog — used here because the
    // loading overlay would hide an inline error message
    alert("Failed to generate report: " + e.message);
  } finally {
    hideLoading();
  }
}


/* ----------------------------------------------------------------
   Chat rendering
   ----------------------------------------------------------------
   Messages are appended as HTML strings via insertAdjacentHTML.
   This is simpler than createElement for complex nested structures.
   All user-provided text is escaped via escapeHtml() to prevent XSS.

   Coach feedback is always rendered in the DOM but hidden by default
   via the .coaching-hidden CSS class on the chat container. The Coach
   toggle button adds/removes this class globally — one class change
   shows/hides ALL feedback blocks at once.
   ---------------------------------------------------------------- */

/**
 * Append an opponent message + coach feedback to the chat.
 * @param {string} text - The opponent's debate response
 * @param {object} feedback - {praise: string, criticism: string} from the coach
 */
function appendAIMessage(text, feedback) {
  const container = document.getElementById("chat-messages");

  // Build the HTML string for the opponent's message bubble
  let html = `
    <div class="msg ai">
      <div class="msg-label">Opponent</div>
      <div class="msg-bubble">${escapeHtml(text)}</div>`;

  // Append coach feedback below the opponent's message.
  // This HTML is always present in the DOM; the .coaching-hidden class
  // on the parent container controls visibility via CSS (display: none).
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

  // insertAdjacentHTML("beforeend", ...) appends the HTML as the last child.
  // Unlike innerHTML +=, it doesn't re-parse existing content.
  container.insertAdjacentHTML("beforeend", html);

  // Auto-scroll to the bottom so the latest message is visible.
  // scrollTop = scrollHeight scrolls to the very end.
  container.scrollTop = container.scrollHeight;
}

/**
 * Append the student's message to the chat (right-aligned, blue bubble).
 */
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

/**
 * Display an error message in the chat (red-styled).
 * Used when an API call fails during a debate.
 */
function appendSystemMessage(text) {
  const container = document.getElementById("chat-messages");
  // Inline style overrides the default border/text color to red
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
   Toggle controls
   ----------------------------------------------------------------
   Coach:  shows/hides ALL inline coach feedback blocks at once via
           a single CSS class on the chat container. Default: hidden.
   Notes:  slides the side panel in/out. Default: collapsed.

   Both work by toggling CSS classes. The actual show/hide logic is
   in the stylesheet, not in JS — JS just flips the class.
   ---------------------------------------------------------------- */

/**
 * Toggle inline coach feedback visibility (global, all turns at once).
 * Adds/removes .coaching-hidden on the chat container.
 * In CSS: .chat-messages.coaching-hidden .coach-feedback { display: none; }
 */
function toggleCoach() {
  coachVisible = !coachVisible;
  const container = document.getElementById("chat-messages");
  // classList.toggle(className, force):
  //   force=true → add the class
  //   force=false → remove the class
  // So when coachVisible is true, we REMOVE coaching-hidden (show feedback).
  container.classList.toggle("coaching-hidden", !coachVisible);
  // Update button text to show current state
  document.getElementById("toggle-coach-btn").textContent =
    coachVisible ? "Coach ✓" : "Coach";
}

/**
 * Toggle the notes side panel open/closed.
 * Adds/removes .collapsed on the notes panel.
 * In CSS: .notes-panel.collapsed { width: 0; padding: 0; opacity: 0; }
 * The transition property on .notes-panel animates the collapse smoothly.
 */
function toggleNotes() {
  const panel = document.getElementById("notes-panel");
  const btn = document.getElementById("toggle-notes-btn");
  notesOpen = !notesOpen;
  panel.classList.toggle("collapsed", !notesOpen);
  // Arrow direction indicates whether clicking will open or close
  btn.textContent = notesOpen ? "Notes ◂" : "Notes ▸";
}


/* ----------------------------------------------------------------
   Notes panel
   ----------------------------------------------------------------
   Notes are cumulative (accumulated server-side via list.extend()).
   Each /api/message response includes the full running list, which
   we render here. We also store the latest snapshot in latestNotes
   so the download feature always has the most recent data.
   ---------------------------------------------------------------- */

/**
 * Update all three notes lists in the side panel.
 * Called after every /api/message response.
 * @param {object} notes - {ai_points: [], student_points: [], coach_observations: []}
 */
function updateNotes(notes) {
  if (!notes) return;
  latestNotes = notes;  // store for download feature
  renderNotesList("notes-ai-points", notes.ai_points);
  renderNotesList("notes-student-points", notes.student_points);
  renderNotesList("notes-coach-obs", notes.coach_observations);
}

/**
 * Render an array of strings into a <ul> element.
 * Replaces all existing <li> children.
 * @param {string} elId - The id of the <ul> element
 * @param {string[]} items - Array of note strings to render
 */
function renderNotesList(elId, items) {
  const ul = document.getElementById(elId);
  if (!items || items.length === 0) {
    // Show placeholder text if the list is empty
    ul.innerHTML = '<li class="empty">None yet.</li>';
    return;
  }
  // .map() transforms each item into an <li> string, .join("") concatenates them.
  // escapeHtml prevents XSS if the model output contains HTML characters.
  ul.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

/**
 * Update the "Turn N" badge in the header.
 */
function updateTurn(turn) {
  // .textContent sets the visible text of an element (no HTML parsing)
  document.getElementById("turn-counter").textContent = `Turn ${turn}`;
}


/* ----------------------------------------------------------------
   Report card
   ----------------------------------------------------------------
   Renders the judge's scoring into a modal overlay.
   Five dimensions, each with a colored progress bar + rationale.
   Score colors: green (8+), blue (6-7), amber (4-5), red (<4).

   The report data comes from the third model instance (one-shot,
   fresh context, lower temperature for consistent grading).
   ---------------------------------------------------------------- */

/**
 * Render the full report card and show the modal.
 * @param {object} report - The judge's JSON output with scores, takeaways, etc.
 */
function renderReportCard(report) {
  // Fill in the simple text fields
  document.getElementById("report-grade").textContent = report.overall_grade || "—";
  document.getElementById("report-summary").textContent = report.summary || "";
  document.getElementById("report-strength").textContent = report.strongest_moment || "";
  document.getElementById("report-weakness").textContent = report.biggest_weakness || "";

  // Build score rows — one per dimension
  const scoresEl = document.getElementById("report-scores");
  scoresEl.innerHTML = "";  // clear any previous render

  // Map from JSON keys to display labels.
  // These match Toron's product page scoring dimensions.
  const scoreLabels = {
    argument_structure: "Argument Structure",
    evidence_and_reasoning: "Evidence & Reasoning",
    rebuttal_quality: "Rebuttal Quality",
    persuasiveness: "Persuasiveness",
    composure_and_clarity: "Composure & Clarity",
  };

  if (report.scores) {
    // Object.entries() returns [[key, value], ...] pairs — like dict.items() in Python
    for (const [key, label] of Object.entries(scoreLabels)) {
      const s = report.scores[key];  // e.g., {score: 7, rationale: "..."}
      if (!s) continue;  // skip if the model didn't include this dimension

      // Convert score (1-10) to a percentage for the progress bar width
      const pct = (s.score / 10) * 100;
      const color = scoreColor(s.score);

      // Each score row: label + number, colored bar, rationale text
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

  // Render actionable takeaways as a numbered list
  const takeawaysEl = document.getElementById("report-takeaways");
  takeawaysEl.innerHTML = "";
  if (report.key_takeaways) {
    // .forEach() is like a for loop — calls the function for each element
    report.key_takeaways.forEach((t) => {
      takeawaysEl.insertAdjacentHTML("beforeend", `<li>${escapeHtml(t)}</li>`);
    });
  }

  // Show the modal by adding .active (CSS: .overlay.active { display: flex })
  document.getElementById("report-overlay").classList.add("active");
}

/**
 * Map a 1-10 score to a color for the progress bar.
 * @param {number} score
 * @returns {string} CSS color hex
 */
function scoreColor(score) {
  if (score >= 8) return "#059669";  // green — strong
  if (score >= 6) return "#2563eb";  // blue — decent
  if (score >= 4) return "#d97706";  // amber — needs work
  return "#dc2626";                   // red — weak
}


/* ----------------------------------------------------------------
   Notes download
   ----------------------------------------------------------------
   Generates a markdown file from the latest cumulative notes and
   triggers a browser download. Available both mid-debate (header
   button) and post-debate (report card modal button).

   Uses the Blob API to create a downloadable file in-memory without
   any server round-trip.
   ---------------------------------------------------------------- */

function downloadNotes() {
  if (!latestNotes) return;

  // Build a markdown string with headers and numbered lists
  const lines = [];
  lines.push(`# Toron Debate Notes`);
  lines.push(`**Topic:** ${debateTopic}\n`);

  lines.push(`## AI's Arguments`);
  // (array || []) is a safety pattern: if the array is null/undefined,
  // use an empty array so .forEach doesn't crash.
  (latestNotes.ai_points || []).forEach((p, i) => lines.push(`${i + 1}. ${p}`));

  lines.push(`\n## Student's Arguments`);
  (latestNotes.student_points || []).forEach((p, i) => lines.push(`${i + 1}. ${p}`));

  lines.push(`\n## Coach Observations`);
  (latestNotes.coach_observations || []).forEach((p, i) => lines.push(`${i + 1}. ${p}`));

  // Blob represents raw data. We create one containing the markdown text.
  // The MIME type tells the browser what kind of file it is.
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });

  // URL.createObjectURL creates a temporary URL pointing to the blob.
  // This URL only exists in the browser's memory.
  const url = URL.createObjectURL(blob);

  // Create a temporary <a> element, set its href to the blob URL,
  // set the filename via download attribute, and simulate a click.
  // This triggers the browser's "Save As" dialog.
  const a = document.createElement("a");
  a.href = url;
  a.download = "toron-debate-notes.md";
  a.click();

  // Release the blob URL to free memory
  URL.revokeObjectURL(url);
}


/* ----------------------------------------------------------------
   Utilities
   ---------------------------------------------------------------- */

/**
 * Reset all state and return to the setup screen.
 * Called when the user clicks "New Debate" on the report card.
 */
function resetApp() {
  // Clear all JS state
  sessionId = null;
  latestNotes = null;
  debateTopic = "";
  coachVisible = false;

  // Hide overlays, show setup screen
  document.getElementById("report-overlay").classList.remove("active");
  document.getElementById("debate-screen").classList.remove("active");
  document.getElementById("setup-screen").classList.add("active");

  // Clear all chat messages from the DOM
  document.getElementById("chat-messages").innerHTML = "";

  // Reset coach feedback to hidden (default state)
  document.getElementById("chat-messages").classList.add("coaching-hidden");
  document.getElementById("toggle-coach-btn").textContent = "Coach";

  // Clear the input textarea
  document.getElementById("user-input").value = "";

  // Reset notes panel content to placeholder text
  document.getElementById("notes-ai-points").innerHTML = '<li class="empty">None yet.</li>';
  document.getElementById("notes-student-points").innerHTML = '<li class="empty">None yet.</li>';
  document.getElementById("notes-coach-obs").innerHTML = '<li class="empty">None yet.</li>';
}

/**
 * Show the loading overlay with a custom message.
 * Also disables the send button to prevent double-sends.
 */
function showLoading(text) {
  document.getElementById("loading-text").textContent = text || "Thinking...";
  document.getElementById("loading-overlay").classList.add("active");
  // .disabled = true grays out the button and prevents click events
  document.getElementById("send-btn").disabled = true;
}

/**
 * Hide the loading overlay and re-enable the send button.
 * Called in `finally` blocks so it runs even if the API call fails.
 */
function hideLoading() {
  document.getElementById("loading-overlay").classList.remove("active");
  document.getElementById("send-btn").disabled = false;
}

/**
 * Keyboard handler for the textarea.
 * Enter sends the message. Shift+Enter inserts a newline.
 * e.preventDefault() stops the Enter key from adding a newline before sending.
 */
function handleInputKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

/**
 * Escape HTML special characters to prevent XSS injection.
 * Creates a temporary text node (which auto-escapes) and reads back the HTML.
 * Converts: < → &lt;  > → &gt;  & → &amp;  " → &quot;
 * This is critical because we render model output as innerHTML.
 */
function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;  // .textContent auto-escapes HTML characters
  return div.innerHTML;    // .innerHTML reads back the escaped version
}
