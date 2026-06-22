"""
Toron Demo — AI-Coached Debate Practice
FastAPI backend with GPT-4o-mini integration.
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import json
import uuid

app = FastAPI(title="Toron Demo")


# ---------------------------------------------------------------------------
# In-memory session store (demo only — no persistence)
# ---------------------------------------------------------------------------
sessions: dict = {}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class StartDebate(BaseModel):
    api_key: str
    topic: str
    user_side: str          # "for" or "against"
    difficulty: str = "intermediate"


class SendMessage(BaseModel):
    api_key: str
    session_id: str
    message: str


class EndDebate(BaseModel):
    api_key: str
    session_id: str


# ---------------------------------------------------------------------------
# Prompts
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

OPPONENT_SYSTEM_PROMPT = """\
You are a debate opponent arguing {ai_side} the motion: "{topic}".

  • Make substantive, well-structured arguments.
  • Challenge weak reasoning and probe logical gaps.
  • Respond directly to the student's points before introducing new ones.
  • The student always speaks first. You respond to their opening argument.

{opponent_difficulty}

You MUST respond with valid JSON matching this schema (nothing else):
{{
  "debate_response": "<your argument / rebuttal, 2-4 paragraphs>"
}}"""

COACH_SYSTEM_PROMPT = """\
You are an expert debate coach silently observing a practice debate.

Topic: "{topic}"
The student argues: {user_side}

After each exchange you will receive the student's argument and the opponent's
response. Analyze the student's performance:
  • Be balanced: acknowledge strengths AND identify weaknesses.
  • Be specific: reference exact phrases or logical moves the student made.
  • Suggest concrete improvements.

{coach_difficulty}

You MUST respond with valid JSON matching this schema (nothing else):
{{
  "coach_feedback": {{
    "praise": "<what the student did well in their latest message>",
    "criticism": "<what could improve, with specific suggestions>"
  }},
  "notes": {{
    "new_ai_points":          ["<NEW arguments the opponent made THIS turn only>"],
    "new_student_points":     ["<NEW arguments the student made THIS turn only>"],
    "new_coach_observations": ["<NEW observations from THIS exchange only>"]
  }}
}}

Notes must contain ONLY what is new in this exchange. Do NOT repeat points
from earlier turns — accumulation is handled externally."""

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
    """Thin wrapper around the OpenAI chat completion API."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    raw = response.choices[0].message.content
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Model returned invalid JSON")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/start")
async def start_debate(req: StartDebate):
    session_id = str(uuid.uuid4())
    ai_side = "against" if req.user_side.lower() == "for" else "for"
    diff = req.difficulty
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

    # No OpenAI call — the student speaks first.
    sessions[session_id] = {
        "topic": req.topic,
        "user_side": req.user_side,
        "ai_side": ai_side,
        "difficulty": req.difficulty,
        "opponent_history": [{"role": "system", "content": opponent_prompt}],
        "coach_history": [{"role": "system", "content": coach_prompt}],
        "notes": {"ai_points": [], "student_points": [], "coach_observations": []},
        "turn_count": 0,
    }

    return {"session_id": session_id}


@app.post("/api/message")
async def send_message(req: SendMessage):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # ---- 1. Opponent: debate response ----
    session["opponent_history"].append({"role": "user", "content": req.message})

    try:
        opp_result, opp_raw = _call_openai(req.api_key, session["opponent_history"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Opponent error: {e}")

    debate_response = opp_result.get("debate_response", "")
    session["opponent_history"].append({"role": "assistant", "content": opp_raw})

    # ---- 2. Coach: feedback + notes ----
    coach_observation = (
        f"STUDENT: {req.message}\n\nOPPONENT: {debate_response}"
    )
    session["coach_history"].append({"role": "user", "content": coach_observation})

    try:
        coach_result, coach_raw = _call_openai(req.api_key, session["coach_history"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Coach error: {e}")

    session["coach_history"].append({"role": "assistant", "content": coach_raw})

    # Accumulate notes server-side
    new_notes = coach_result.get("notes", {})
    session["notes"]["ai_points"].extend(new_notes.get("new_ai_points", []))
    session["notes"]["student_points"].extend(new_notes.get("new_student_points", []))
    session["notes"]["coach_observations"].extend(new_notes.get("new_coach_observations", []))

    session["turn_count"] += 1

    return {
        "debate_response": debate_response,
        "coach_feedback": coach_result.get("coach_feedback", {}),
        "notes": session["notes"],
        "turn": session["turn_count"],
    }


@app.post("/api/end")
async def end_debate(req: EndDebate):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build a clean transcript from the opponent history
    lines = []
    for msg in session["opponent_history"]:
        if msg["role"] == "user":
            lines.append(f"STUDENT: {msg['content']}")
        elif msg["role"] == "assistant":
            try:
                parsed = json.loads(msg["content"])
                lines.append(f"OPPONENT: {parsed.get('debate_response', '')}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"OPPONENT: {msg['content']}")

    transcript = "\n\n".join(lines)

    report_system = REPORT_CARD_PROMPT.format(
        topic=session["topic"],
        user_side=session["user_side"],
        difficulty=session["difficulty"],
    )

    try:
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

    # Grab final notes before cleanup
    final_notes = session["notes"]
    del sessions[req.session_id]

    return {"report": report, "notes": final_notes}


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")
