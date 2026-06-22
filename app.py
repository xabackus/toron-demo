"""
Toron Demo — AI-Coached Debate Practice
========================================
FastAPI backend with GPT-4o-mini integration.

Architecture:
  - Two separate model instances per debate: an OPPONENT and a COACH.
  - The opponent argues against the student. The coach silently observes
    and provides technique feedback + structured notes.
  - Separating them avoids role confusion: a single prompt that tries to
    simultaneously argue AND coach produces watered-down opponents and
    coaching contaminated by the adversarial persona.

Session state is in-memory (dict keyed by UUID). No database — this is a
demo. For production, swap the sessions dict for Redis.

API key is provided per-request from the browser. Never stored server-side.
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles      # serves CSS/JS/HTML from disk
from fastapi.responses import FileResponse, StreamingResponse  # file + SSE streaming
from pydantic import BaseModel                    # request validation + OpenAPI schema
import json
import uuid

# FastAPI() creates the app instance. title= shows up in auto-generated docs at /docs.
app = FastAPI(title="Toron Demo")


# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------
# Plain dict: session_id (str) → session data (dict).
# Each session holds two separate conversation histories (opponent + coach),
# cumulative notes, and metadata. Sessions are created on /api/start and
# deleted on /api/end. They don't survive server restarts.
#
# Production upgrade: swap this dict for Redis. Same key-value interface,
# adds persistence, expiration, and multi-process support.
# ---------------------------------------------------------------------------
sessions: dict = {}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
# Pydantic BaseModel subclasses. FastAPI uses these to:
#   1. Validate incoming JSON (returns 422 if fields are missing/wrong type)
#   2. Auto-generate OpenAPI docs (visible at /docs)
#
# The api_key field is on every request because we don't store it server-side.
# ---------------------------------------------------------------------------

class StartDebate(BaseModel):
    api_key: str
    topic: str
    user_side: str          # "for" or "against"
    difficulty: str = "intermediate"  # default if not provided


class SendMessage(BaseModel):
    api_key: str
    session_id: str
    message: str


class EndDebate(BaseModel):
    api_key: str
    session_id: str


# ---------------------------------------------------------------------------
# Difficulty configs
# ---------------------------------------------------------------------------
# Difficulty is split into two independent axes:
#
#   OPPONENT_DIFFICULTY — how hard the opponent argues.
#     Beginner gives room; advanced exploits every gap.
#
#   COACH_DIFFICULTY — how picky the coach is.
#     Scales UP with difficulty (advanced = more issues flagged, not fewer).
#     A harder coach catches subtler mistakes: burden-of-proof shifts,
#     implicit assumptions, scope creep, false equivalences.
#
# Instructions are behavioral ("exploit every logical gap") not vibes
# ("use sophisticated arguments") because concrete actions produce
# more differentiated model behavior across difficulty levels.
#
# These strings get injected into the system prompts via .format().
# ---------------------------------------------------------------------------

OPPONENT_DIFFICULTY = {
    "beginner": (
        "BEGINNER OPPONENT: Use simple, straightforward arguments with 1-2 main "
        "points per turn. Don't aggressively exploit logical gaps — give the "
        "student room to develop their reasoning. Avoid complex evidence chains "
        "or advanced rhetorical techniques. If the student makes a decent point, "
        "acknowledge it before countering."
    ),
    "intermediate": (
        "INTERMEDIATE OPPONENT: Use solid arguments backed by evidence and clear "
        "reasoning. Challenge weak points directly. Introduce counter-evidence "
        "when relevant. Press the student on unsupported claims, but don't "
        "overwhelm them with more than 2-3 distinct attacks per turn."
    ),
    "advanced": (
        "ADVANCED OPPONENT: Argue at the highest level. Use multi-layered "
        "arguments with detailed evidence and sophisticated rhetorical techniques. "
        "Aggressively exploit every logical gap, weak analogy, and unsupported "
        "claim. Steel-man your own position. Anticipate and preempt the student's "
        "likely responses. Make them earn every point."
    ),
}

COACH_DIFFICULTY = {
    "beginner": (
        "BEGINNER COACHING: Focus on the 1-2 most important issues per turn. Be "
        "encouraging — celebrate what works before noting what doesn't. Keep "
        "suggestions simple and actionable."
    ),
    "intermediate": (
        "INTERMEDIATE COACHING: Identify 2-3 issues per turn covering both major "
        "and moderate problems. Balance praise with substantive criticism. Suggest "
        "specific techniques for improvement."
    ),
    "advanced": (
        "ADVANCED COACHING: Be ruthlessly thorough. Flag every logical gap, weak "
        "word choice, missing evidence, structural flaw, and rhetorical missed "
        "opportunity you can find. Hold praise to a very high bar — only genuinely "
        "strong moves deserve it. Point out subtle issues a less experienced coach "
        "would miss: implicit assumptions, burden-of-proof shifts, scope creep, "
        "false equivalences, missing qualifications. The student should feel like "
        "they are being coached by someone who notices everything."
    ),
}


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
# Each model instance gets its own system prompt, optimized for one job.
#
# OPPONENT — pure debater. Returns only {"debate_response": "..."}.
#   Has a standard chat history: student messages ↔ its own responses.
#
# COACH — silent observer. Returns feedback (technique) + notes (substance).
#   Has its own history where each "user" message is a formatted observation
#   of the exchange: "STUDENT: ... / OPPONENT: ...". This lets the coach
#   see its own prior analyses and build consistent feedback over time.
#
# REPORT CARD — a third, one-shot instance used at the end. Gets the full
#   transcript and scores the student across five dimensions. Uses a fresh
#   context (no turn-by-turn history) to avoid anchoring to early impressions.
#
# All three use JSON mode (response_format: json_object) to guarantee
# parseable output without regex or string extraction hacks.
#
# Note on the doubled curly braces ({{ }}): Python's .format() uses { } for
# substitution, so literal JSON braces must be escaped as {{ }}.
# ---------------------------------------------------------------------------

OPPONENT_SYSTEM_PROMPT = """\
You are a debate opponent arguing {ai_side} the motion: "{topic}".

  • Make substantive, well-structured arguments.
  • Challenge weak reasoning and probe logical gaps.
  • Respond directly to the student's points before introducing new ones.
  • The student always speaks first. You respond to their opening argument.

{opponent_difficulty}

Respond with your argument directly in plain text (2-4 paragraphs).
Do NOT wrap your response in JSON, markdown, or any other format."""

COACH_SYSTEM_PROMPT = """\
You are an expert debate coach silently observing a practice debate.

Topic: "{topic}"
The student argues: {user_side}

Each turn you receive the student's argument and the opponent's response.
You produce two things:

1. COACH FEEDBACK — critique of the student's TECHNIQUE (how they argued):
   rhetorical moves, structure, logical soundness, persuasive effectiveness.

2. NOTES — a factual log of the SUBSTANCE (what was argued): the specific
   claims, evidence, and positions each side introduced THIS turn. This is a
   content inventory, not a quality judgment. Every exchange introduces at
   least one new point per side — capture them.

{coach_difficulty}

You MUST respond with valid JSON matching this schema (nothing else):
{{
  "coach_feedback": {{
    "praise": "<what the student did well — technique and delivery>",
    "criticism": "<what could improve, with specific suggestions>"
  }},
  "notes": {{
    "new_student_points":     ["<each distinct claim or argument the STUDENT made this turn>"],
    "new_ai_points":          ["<each distinct claim or argument the OPPONENT made this turn>"],
    "new_coach_observations": ["<new patterns or tendencies you noticed this turn>"]
  }}
}}

CRITICAL:
  • new_student_points and new_ai_points must each have at least one entry.
    If someone spoke, they made a point — log it.
  • Notes are ONLY what is new this turn. Accumulation is handled externally.
  • Do NOT leave any notes field as an empty array."""

# Report card scoring dimensions are aligned with Toron's product page:
# argument structure, evidence, rebuttal, persuasiveness, composure.
REPORT_CARD_PROMPT = """\
You are an expert debate coach producing a final report card.

Topic: "{topic}"
Student argued: {user_side}
Difficulty: {difficulty}

Review the full transcript and respond with valid JSON only:
{{
  "overall_grade": "<A+ through F>",
  "scores": {{
    "argument_structure":    {{"score": <1-10>, "rationale": "..."}},
    "evidence_and_reasoning":{{"score": <1-10>, "rationale": "..."}},
    "rebuttal_quality":      {{"score": <1-10>, "rationale": "..."}},
    "persuasiveness":        {{"score": <1-10>, "rationale": "..."}},
    "composure_and_clarity": {{"score": <1-10>, "rationale": "..."}}
  }},
  "strongest_moment":  "<the student's single best moment>",
  "biggest_weakness":  "<most important area for improvement with example>",
  "key_takeaways":     ["<3-5 actionable takeaways>"],
  "summary":           "<2-3 sentence overall assessment>"
}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_openai(api_key: str, messages: list, temperature: float = 0.7):
    """
    Thin wrapper around OpenAI chat completions.

    Returns (parsed_dict, raw_string):
      - parsed_dict: the JSON response parsed into a Python dict (for logic)
      - raw_string: the raw response text (for storing in conversation history,
        because the model expects to see its own prior outputs verbatim)

    Temperature defaults:
      - 0.7 for debate/coaching (some variability keeps arguments fresh)
      - 0.4 for report card (consistent, calibrated scoring)

    response_format={"type": "json_object"} tells the OpenAI API to
    constrain output to valid JSON. This means json.loads() will always
    succeed unless something is very wrong.
    """
    # Import here so the module loads even if openai isn't installed yet
    # (lets us test imports without an API key)
    from openai import OpenAI

    # OpenAI() creates a client bound to the given API key.
    # Each request uses the key from the browser — we don't reuse clients.
    client = OpenAI(api_key=api_key)

    # chat.completions.create() is the main API call.
    # messages is a list of {"role": "system"|"user"|"assistant", "content": "..."}.
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        response_format={"type": "json_object"},
        temperature=temperature,
    )

    # response.choices is a list; we always have exactly one choice.
    # .message.content is the model's text output (a JSON string here).
    raw = response.choices[0].message.content

    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        # This shouldn't happen with JSON mode, but guard against it.
        raise HTTPException(status_code=502, detail="Model returned invalid JSON")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# @app.post("/api/start") registers this function as a POST handler.
# FastAPI reads the type hint (req: StartDebate) and automatically:
#   1. Parses the request body as JSON
#   2. Validates it against StartDebate's fields
#   3. Returns 422 with details if validation fails
@app.post("/api/start")
async def start_debate(req: StartDebate):
    """
    Create a new debate session. No OpenAI call here — the student speaks
    first, so we just set up the session state and return a session_id.
    The AI always argues the opposite side of the student.
    """
    # Generate a random UUID as the session key
    session_id = str(uuid.uuid4())

    # Flip the side: if student argues "for", AI argues "against"
    ai_side = "against" if req.user_side.lower() == "for" else "for"
    diff = req.difficulty

    # Build system prompts by injecting topic, side, and difficulty text.
    # .get(diff, ...) falls back to intermediate if an unknown difficulty is passed.
    opponent_prompt = OPPONENT_SYSTEM_PROMPT.format(
        ai_side=ai_side,
        topic=req.topic,
        opponent_difficulty=OPPONENT_DIFFICULTY.get(diff, OPPONENT_DIFFICULTY["intermediate"]),
    )

    coach_prompt = COACH_SYSTEM_PROMPT.format(
        user_side=req.user_side,
        topic=req.topic,
        coach_difficulty=COACH_DIFFICULTY.get(diff, COACH_DIFFICULTY["intermediate"]),
    )

    # Store everything needed to process future turns.
    # opponent_history and coach_history are separate message lists — each
    # model instance only sees its own conversation.
    sessions[session_id] = {
        "topic": req.topic,
        "user_side": req.user_side,
        "ai_side": ai_side,
        "difficulty": req.difficulty,
        # Opponent's chat history starts with just its system prompt.
        # Future turns append user/assistant message pairs.
        "opponent_history": [{"role": "system", "content": opponent_prompt}],
        # Coach's chat history also starts with its system prompt.
        # Future turns append formatted observations (not raw chat).
        "coach_history": [{"role": "system", "content": coach_prompt}],
        # Cumulative notes — accumulated server-side via list.extend().
        # The model returns only NEW items per turn (new_ai_points, etc.);
        # we append them here so accumulation is deterministic Python,
        # not a prompt instruction the model might ignore.
        "notes": {"ai_points": [], "student_points": [], "coach_observations": []},
        "turn_count": 0,
    }

    # Return just the session ID. The frontend switches to the debate screen
    # and waits for the student to type their opening argument.
    return {"session_id": session_id}


@app.post("/api/message")
async def send_message(req: SendMessage):
    """
    Process one debate turn via Server-Sent Events (SSE).

    Returns a stream of events instead of a single JSON response:
      1. 'token' events — opponent's response, one chunk at a time
      2. 'coach' event  — coaching feedback + cumulative notes
      3. 'done' event   — turn number, signals stream complete

    The opponent streams in plain text (no JSON mode — can't incrementally
    parse JSON tokens). The coach still uses JSON mode (non-streaming)
    since it's a short structured response and the user is reading the
    opponent's text while it runs.

    This cuts perceived latency dramatically: the first token appears
    in ~200ms instead of waiting 3-5 seconds for the full response.
    """
    # Validate session before entering the generator.
    # HTTPException must be raised HERE, not inside the generator
    # (StreamingResponse has already started by then).
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Append the student's message to the opponent's chat history now,
    # before the generator starts.
    session["opponent_history"].append({"role": "user", "content": req.message})

    # Capture the user's message for the coach observation (closures
    # over req won't work reliably once the endpoint returns).
    user_message = req.message
    api_key = req.api_key

    def generate():
        """
        Synchronous SSE generator. FastAPI runs sync generators in a
        thread pool, so this won't block the event loop.

        Yields SSE-formatted strings: "event: <type>\ndata: <json>\n\n"
        """
        from openai import OpenAI

        # ---- 1. Stream opponent response ----
        try:
            client = OpenAI(api_key=api_key)
            stream = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=session["opponent_history"],
                temperature=0.7,
                stream=True,
                # No response_format — opponent outputs plain text for streaming
            )

            # Accumulate the full response while streaming tokens to the client
            full_response = ""
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    full_response += delta.content
                    # Each token is an SSE event the frontend appends to the bubble
                    yield f"event: token\ndata: {json.dumps({'t': delta.content})}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'detail': f'Opponent error: {e}'})}\n\n"
            return

        # Store the full response as plain text in the opponent's history.
        # (No JSON wrapping — the opponent now speaks plain text.)
        session["opponent_history"].append(
            {"role": "assistant", "content": full_response}
        )

        # ---- 2. Coach: feedback + notes (non-streaming, JSON mode) ----
        # The coach needs the full exchange to analyze, so it runs after
        # the opponent finishes. The user is reading the opponent's text
        # during this ~1-2 second window.
        coach_observation = f"STUDENT: {user_message}\n\nOPPONENT: {full_response}"
        session["coach_history"].append({"role": "user", "content": coach_observation})

        try:
            coach_result, coach_raw = _call_openai(api_key, session["coach_history"])
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'detail': f'Coach error: {e}'})}\n\n"
            return

        session["coach_history"].append({"role": "assistant", "content": coach_raw})

        # ---- 3. Accumulate notes server-side ----
        new_notes = coach_result.get("notes", {})
        session["notes"]["ai_points"].extend(new_notes.get("new_ai_points", []))
        session["notes"]["student_points"].extend(new_notes.get("new_student_points", []))
        session["notes"]["coach_observations"].extend(
            new_notes.get("new_coach_observations", [])
        )

        session["turn_count"] += 1

        # ---- 4. Send coach feedback + notes as a single event ----
        yield f"event: coach\ndata: {json.dumps({'coach_feedback': coach_result.get('coach_feedback', {}), 'notes': session['notes']})}\n\n"

        # ---- 5. Signal completion ----
        yield f"event: done\ndata: {json.dumps({'turn': session['turn_count']})}\n\n"

    # Return the SSE stream.
    # media_type tells the browser this is an event stream, not JSON.
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/end")
async def end_debate(req: EndDebate):
    """
    End the debate and generate a report card.

    Uses a THIRD model instance (fresh context, no turn-by-turn history)
    to score the student holistically. This avoids anchoring bias from
    the coach's accumulated observations.

    Temperature is set lower (0.4) for more consistent, calibrated grading.

    Returns the report card AND the final cumulative notes (for download).
    Session is deleted after this call.
    """
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build a clean transcript from the opponent history.
    # Opponent responses are plain text (streaming mode), no JSON to parse.
    lines = []
    for msg in session["opponent_history"]:
        if msg["role"] == "user":
            lines.append(f"STUDENT: {msg['content']}")
        elif msg["role"] == "assistant":
            lines.append(f"OPPONENT: {msg['content']}")

    # Join with double newlines for readability in the judge's context
    transcript = "\n\n".join(lines)

    # Format the report card system prompt with debate metadata
    report_system = REPORT_CARD_PROMPT.format(
        topic=session["topic"],
        user_side=session["user_side"],
        difficulty=session["difficulty"],
    )

    try:
        # One-shot call: system prompt + transcript. No prior history.
        # temperature=0.4 for consistent scoring (less randomness).
        report, _ = _call_openai(
            req.api_key,
            [
                {"role": "system", "content": report_system},
                {"role": "user", "content": f"Transcript:\n\n{transcript}"},
            ],
            temperature=0.4,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Grab final notes before deleting the session
    final_notes = session["notes"]
    del sessions[req.session_id]

    # Return both report and notes. The frontend uses notes for the
    # download feature (session is now gone, so this is the last chance).
    return {"report": report, "notes": final_notes}


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------
# app.mount() attaches a sub-application that serves files from the static/
# directory. Any request to /static/foo.js returns static/foo.js from disk.
#
# The root route (/) returns index.html directly via FileResponse, so
# visiting http://localhost:8000 loads the app without needing /static/ prefix.
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")
