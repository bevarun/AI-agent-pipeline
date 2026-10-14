# Intake Intelligence — Automated Lead & Patient Outreach Pipeline

A resilient, full-stack pipeline that ingests heterogeneous inbound records (B2B leads via CSV and clinical patients via FHIR EMR), automatically scores and triages them, generates personalized outreach copy, enforces safety guardrails, and delivers validated results to a CMS with live status streaming.

---

## Architecture Overview

```
                      ┌───────────────────────────────────────┐
                      │    React Dashboard (Vite on :5173)    │
                      │   BankDash UI Kit · Live SSE Stream   │
                      └──────────────────┬────────────────────┘
                                         │ REST + SSE
                                         ▼
                      ┌───────────────────────────────────────┐
                      │        FastAPI Server (:8000)         │
                      │  Jobs Router · Background Queue · CMS │
                      └──────────────────┬────────────────────┘
                                         │
               ┌─────────────────────────┴─────────────────────────┐
               ▼                                                   ▼
┌───────────────────────────────┐               ┌─────────────────────────────────────┐
│       Ingestion Layer         │               │         Pipeline Agent Core         │
│  • CSV Parser & Normalizer    │               │  1. Perceive (Heuristic scoring)    │
│  • FHIR R4 REST Client        │               │  2. Triage (LLM category & channel) │
└──────────────┬────────────────┘               │  3. Guardrails (Compliance rules)   │
               │                                │  4. Compose (Targeted copy)         │
               ▼                                │  5. Critique (Verification pass)    │
┌───────────────────────────────┐               └──────────────────┬──────────────────┘
│     Unified Record Schema     │                                  │
│       (Pydantic Models)       │──────────────────────────────────┘
└───────────────────────────────┘                                  │
                                                                   ▼
                                                ┌─────────────────────────────────────┐
                                                │         Gemini LLM Engine           │
                                                │  • 15s Throttle (Max 4 RPM)         │
                                                │  • Multi-Model Failover Chain       │
                                                │  • Deterministic Rule Fallback      │
                                                └─────────────────────────────────────┘
```

---

## Key Features

- **Multi-Source Ingestion**:
  - **B2B CSV Ingestion**: Sniffs delimiters, encodings, and maps inconsistent column aliases (`Job Title`, `Role`, `Position`) into normalized schemas.
  - **Healthcare FHIR Ingestion**: Queries FHIR R4 endpoints (`Patient`, `Condition`, `Appointment`) to extract clinical risk factors and appointment gaps.
- **5-Step Agent Loop**:
  - **Perceive**: Heuristic scoring engine calculating intent, seniority, and clinical risk signals.
  - **Triage**: Categorizes priority (`hot`, `warm`, `cold`, `urgent`, `routine`, `low`) and designates contact channels.
  - **Guardrails**: Hard compliance assertions (e.g., cold leads are never called; HIPAA safety checks).
  - **Compose**: Drafts customized outreach (LinkedIn DM, SMS, Phone script, Patient Portal).
  - **Critique**: Validates length, tone consistency, and redacts sensitive data.
- **Rate-Limited LLM Engine**:
  - Strictly throttled to **max 4 requests per minute** (15-second minimum spacing) to respect free-tier quotas.
  - Multi-model failover (`gemini-2.5-flash` → `gemini-2.0-flash`) with automatic exponential backoff.
  - Graceful deterministic fallback ensures the pipeline never crashes if network or API limits are hit.
- **FastAPI Job Management & SSE**:
  - Asynchronous background worker queue with sequential execution.
  - Real-time updates streamed to the frontend via Server-Sent Events (`text/event-stream`).
  - Safe disk persistence with file-locking retries for Windows environments.
- **BankDash UI Dashboard**:
  - High-contrast, responsive UI with KPI stat cards, segmented filtering, and search.
  - Slide-out detail drawer displaying agent reasoning, generated copy, and audit metadata.
  - Interactive pipeline run wizard supporting sample datasets and custom file uploads.

---

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn, Pydantic v2, Requests, python-dotenv
- **Frontend**: React 18, Vite, Vanilla CSS (BankDash design system tokens)
- **Testing**: Pytest

---

## Project Structure

```
├── backend/
│   ├── server.py              # Unified FastAPI server (Jobs API + Mock CMS)
│   ├── .env.example           # Environment variable template
│   ├── requirements.txt       # Python dependencies
│   ├── mock_leads.csv         # Sample dataset for demonstration
│   ├── jobs/                  # Job management and background worker
│   │   ├── manager.py         # Job worker thread, persistence & event bus
│   │   ├── router.py          # REST endpoints & SSE streaming
│   │   ├── uploads.py         # CSV file validation & header mapping
│   │   └── fhir_sources.py    # FHIR server presets & connection tester
│   ├── pipeline/              # Ingestion and agent core
│   │   ├── runner.py          # Pipeline execution orchestrator
│   │   ├── agent.py           # 5-stage agent decision loop
│   │   ├── llm.py             # Rate-limited Gemini client with failover
│   │   ├── ingest_csv.py      # Lead CSV parser & validator
│   │   ├── ingest_fhir.py     # FHIR REST client & validator
│   │   ├── models.py          # Unified data contracts
│   │   ├── guardrails.py      # Compliance and safety assertions
│   │   └── delivery.py        # CMS HTTP delivery with retries
│   ├── cms/
│   │   └── app.py             # In-memory mock CMS for ingestion validation
│   └── tests/                 # Unit and offline pipeline tests
└── frontend/
    ├── package.json           # Frontend dependencies
    ├── vite.config.js         # Vite configuration with API proxy
    ├── index.html             # Application entrypoint
    └── src/
        ├── App.jsx            # Shell container & view switcher
        ├── api.js             # REST client
        ├── index.css          # BankDash design tokens and styling
        ├── hooks/
        │   └── useJobStream.js# EventSource (SSE) progress listener
        ├── components/        # Dashboard components (Table, Drawer, StatCards)
        └── components/runs/   # Pipeline wizard, job history & live monitor
```

---

## Getting Started

### 1. Prerequisites

- Python 3.10+
- Node.js 18+ and npm
- A Google AI Studio API key (free tier available at [aistudio.google.com](https://aistudio.google.com/apikey))

### 2. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

Open `.env` and add your API key:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
LLM_MIN_INTERVAL_SECONDS=15.0
FHIR_BASE_URL=https://r4.smarthealthit.org
CMS_URL=http://127.0.0.1:8000
```

Start the backend server:
```bash
uvicorn server:app --port 8000 --reload
```
The backend API and CMS will be available at `http://127.0.0.1:8000`.

### 3. Frontend Setup

In a new terminal window:
```bash
cd frontend

# Install dependencies
npm install

# Start Vite development server
npm run dev
```
Open `http://localhost:5173` in your browser.

---

## Running the Pipeline via CLI

You can also run the ingestion and agent pipeline directly from the command line:

```bash
cd backend

# Run with sample CSV and FHIR data (limit 6 records for a quick test)
python run_pipeline.py --limit 6

# Run full pipeline with custom CSV
python run_pipeline.py --leads path/to/leads.csv

# Run with chaos mode enabled (simulates network retries and transient 503s)
python run_pipeline.py --chaos
```

---

## Testing

Run offline unit and pipeline tests (no API key or network required):

```bash
cd backend
python -m pytest tests/ -v
```

---

## API Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/pipeline/info` | Health check, active model, and FHIR server presets |
| `POST`| `/uploads` | Upload and pre-validate a custom leads CSV |
| `GET` | `/sources/fhir/test` | Connectivity probe for external FHIR server endpoints |
| `POST`| `/jobs` | Queue a new pipeline execution job |
| `GET` | `/jobs` | List all historical and active jobs |
| `GET` | `/jobs/{id}` | Retrieve status, configuration, and summary of a job |
| `GET` | `/jobs/{id}/events` | Server-Sent Events (SSE) live progress stream |
| `GET` | `/jobs/{id}/artifacts/{name}` | Download run artifacts (`results.jsonl`, `traces.jsonl`) |
| `POST`| `/jobs/{id}/cancel` | Request cancellation of an active job |
| `GET` | `/records` | Retrieve delivered and triaged CMS records |

---

## License

MIT
