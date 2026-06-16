# Toron Demo — AI-Coached Debate Practice

A lightweight demo of [Toron](https://www.toron.io): an AI debate practice platform that pairs a challenging opponent with real-time coaching feedback.

Built with **FastAPI** (Python) + vanilla HTML/CSS/JS. Uses **GPT-4o-mini** via the OpenAI API.

## Quick Start

```bash
# Clone and install
git clone https://github.com/xabackus/toron-demo.git
cd toron-demo
pip install -r requirements.txt

# Or if using pip3
git clone https://github.com/xabackus/toron-demo.git
cd toron-demo
pip install -r requirements.txt

# Run
uvicorn app:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Enter your OpenAI API key in the browser. It's sent per-request and never stored.

## How It Works

1. **Pick a topic** — policy, ethics, philosophy, anything debatable.
2. **Choose your side and difficulty** — the AI argues the opposite position.
3. **Debate** — the AI responds as a substantive opponent while simultaneously providing coaching feedback (praise + constructive criticism) after each exchange.
4. **End the debate** — an AI judge scores your performance across five dimensions and generates a detailed report card.

## The Notes System

A collapsible side panel exposes the AI's internal state in real time:

- **AI's Arguments** — every key point the AI has made (cumulative)
- **Student's Arguments** — every key point you've made (cumulative)
- **Coach Observations** — running notes on your debate patterns and tendencies

This serves as an **alignment and debugging layer**: during development and early deployment, you can inspect whether the model is accurately tracking arguments, whether its coaching feedback is calibrated, and whether its understanding of the debate matches reality. Think of it as interpretability tooling for the coaching system.

## Architecture

```
toron-demo/
├── app.py                  # FastAPI backend (sessions, OpenAI calls, prompts)
├── static/
│   ├── index.html          # Single-page frontend
│   ├── style.css
│   └── app.js
├── requirements.txt
└── README.md
```

- **Backend**: FastAPI with in-memory session storage. Three endpoints: `/api/start`, `/api/message`, `/api/end`.
- **Frontend**: Vanilla JS, no build step. Setup screen → debate chat → report card modal.
- **AI**: GPT-4o-mini with JSON mode (`response_format: json_object`). System prompt instructs the model to return structured output containing the debate response, coaching feedback, and cumulative notes — all in a single call.

## Report Card Dimensions

Aligned with Toron's coaching framework:

| Dimension | What it measures |
|---|---|
| Argument Structure | Logical organization, clear claims, supporting reasoning |
| Evidence & Reasoning | Quality and relevance of evidence, inferential strength |
| Rebuttal Quality | Engagement with opposing arguments, counter-reasoning |
| Persuasiveness | Overall convincingness, rhetorical effectiveness |
| Composure & Clarity | Writing clarity, consistency, poise under pressure |

## Notes

- **No API keys are hardcoded.** The key is entered in the browser and passed with each request.
- **Sessions are in-memory only.** They don't survive server restarts. This is a demo, not production infrastructure.
- **Cost**: GPT-4o-mini is cheap. A full debate (8-10 turns + report card) runs roughly $0.01–0.03.
