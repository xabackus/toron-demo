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

DIFFICULTY_GUIDANCE = {
    "beginner": (
        "Beginner mode: Use clear, accessible arguments. Be encouraging and "
        "patient. Give detailed coaching with concrete examples of improvement. "
        "Avoid overly complex evidence or advanced rhetorical techniques."
    ),
    "intermediate": (
        "Intermediate mode: Use moderately complex arguments with evidence and "
        "rhetorical technique. Balanced feedback — push the student but "
        "acknowledge good moves."
    ),
    "advanced": (
        "Advanced mode: Use sophisticated arguments, detailed evidence, and "
        "advanced rhetorical techniques. Hold the student to high standards. "
        "Be concise in coaching — they should identify issues themselves."
    ),
}

DEBATE_SYSTEM_PROMPT = """\
You are Toron, an AI debate practice system. You play two simultaneous roles:

DEBATER — You argue {ai_side} the motion: "{topic}".
  • Make substantive, well-structured arguments.
  • Challenge weak reasoning and probe logical gaps.
  • Respond directly to the student's points before introducing new ones.
  • The student always speaks first. You respond to their opening argument.

COACH — After each exchange, provide brief coaching feedback on the student's
latest argument.
  • Be balanced: acknowledge strengths AND identify weaknesses.
  • Be specific: reference exact phrases or logical moves the student made.
  • Suggest concrete improvements.

{difficulty_guidance}

You MUST respond with valid JSON matching this schema (nothing else):
{{
  "debate_response": "<your argument / rebuttal, 2-4 paragraphs>",
  "coach_feedback": {{
    "praise": "<what the student did well in their latest message>",
    "criticism": "<what could improve, with specific suggestions>"
  }},
  "notes": {{
    "ai_points":          ["<every key argument YOU have made, cumulative>"],
    "student_points":     ["<every key argument THE STUDENT has made, cumulative>"],
    "coach_observations": ["<running observations about the student's patterns>"]
  }}
}}

Notes must be CUMULATIVE — carry forward ALL points from the entire debate."""

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

    system_prompt = DEBATE_SYSTEM_PROMPT.format(
        ai_side=ai_side,
        topic=req.topic,
        difficulty=req.difficulty,
        difficulty_guidance=DIFFICULTY_GUIDANCE.get(
            req.difficulty, DIFFICULTY_GUIDANCE["intermediate"]
        ),
    )

    # No OpenAI call — the student speaks first.
    sessions[session_id] = {
        "topic": req.topic,
        "user_side": req.user_side,
        "ai_side": ai_side,
        "difficulty": req.difficulty,
        "system_prompt": system_prompt,
        "history": [{"role": "system", "content": system_prompt}],
        "notes": {"ai_points": [], "student_points": [], "coach_observations": []},
        "turn_count": 0,
    }

    return {"session_id": session_id}


@app.post("/api/message")
async def send_message(req: SendMessage):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session["history"].append({"role": "user", "content": req.message})

    try:
        result, raw = _call_openai(req.api_key, session["history"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    session["history"].append({"role": "assistant", "content": raw})
    session["notes"] = result.get("notes", session["notes"])
    session["turn_count"] += 1

    return {
        "debate_response": result.get("debate_response", ""),
        "coach_feedback": result.get("coach_feedback", {}),
        "notes": result.get("notes", {}),
        "turn": session["turn_count"],
    }


@app.post("/api/end")
async def end_debate(req: EndDebate):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build a clean transcript for the judge
    lines = []
    for msg in session["history"]:
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
