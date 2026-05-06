# ProjectMind

ProjectMind is a Streamlit frontend mockup for an internal BCA project workspace with agentic AI collaboration patterns.

This version intentionally keeps the backend, database, vector store, and AI model as mocked adapters so the product flow can be tested first.

## Features

- Project workspace creation with optional release/change metadata
- Member management with prototype roles: IT, UAT, BA, PO, Viewer
- Optional active agents: AI Coordinator, Agent IT, Agent UAT
- Knowledge upload for PDF, Excel/CSV, text, and image files
- Per-project mock knowledge isolation, designed to map later to ChromaDB collections
- Source labels: Gold, Silver, Bronze
- Chat interface with persisted history, source citations, and confidence levels
- Basic readiness dashboard and coordinator project summary

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The mockup stores local demo data under `.projectmind/`.

## Notes

## Integration Blueprint

- Frontend now: Streamlit chat-centric workspace
- Backend later: FastAPI + CrewAI orchestration endpoints
- Database later: PostgreSQL for project/member/chat/audit metadata
- Vector store later: ChromaDB isolated collection per project
- AI layer later: Qwen local model or BCA API Gateway
