# BidLens — Gemini Edition

AI procurement decision engine for comparing vendor proposals, surfacing risk, and stress-testing award decisions.

## Gemini setup

BidLens uses Google's `google-genai` SDK. Gemini API requests are parallelized for independent proposal analysis and use a thread-safe API-key pool. Upload `requirements.txt` together with the vendor proposals; the backend detects it by filename and explicitly excludes it from vendor analysis.

### API key configuration

`backend/.env` now accepts **multiple Gemini API keys** in `GEMINI_API_KEY`. Use either a JSON list (recommended) or a comma-separated list:

```env
GEMINI_API_KEY=["YOUR_GEMINI_KEY_1","YOUR_GEMINI_KEY_2","YOUR_GEMINI_KEY_3","YOUR_GEMINI_KEY_4"]
```

or:

```env
GEMINI_API_KEY=YOUR_GEMINI_KEY_1,YOUR_GEMINI_KEY_2,YOUR_GEMINI_KEY_3
```

Do not commit real keys. Rotate any key that has previously been exposed in source code, chat, logs, or a repository.

### How key rotation works

- Independent proposal extraction calls run concurrently.
- Independent vendor risk calls run concurrently.
- Concurrent Gemini requests reserve different API keys from the pool.
- The same key is never used by two in-flight worker requests at the same time.
- Retries can acquire a different key and advance to the next configured model.
- The number of simultaneous workers is capped by both `AI_MAX_WORKERS` and the number of configured keys.
- If Gemini is unavailable, deterministic fallbacks keep the API usable where possible.

Useful tuning options:

```env
AI_RETRIES=2
AI_RETRY_DELAY_SECONDS=0.5
AI_MAX_WORKERS=8
AI_KEY_WAIT_SECONDS=60
```

The final award and negotiation calls also run concurrently when at least two keys are configured. The final headline score is deterministic and is derived from the vendor scores and requirement gates, so a malformed LLM score cannot produce a misleading `0.0/10` recommendation.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Run with Docker

From the repository root:

```bash
docker compose build --no-cache
docker compose up -d
```

Check service status:

```bash
docker compose ps
```

Open:

- Frontend: `http://localhost:5173`
- Backend health: `http://localhost:8000/api/health`
- API docs: `http://localhost:8000/docs`

Follow backend logs:

```bash
docker compose logs -f backend
```

View the most recent backend logs:

```bash
docker compose logs --tail=200 backend
```

Stop the stack:

```bash
docker compose down
```

### Docker configuration notes

The compose file mounts `./backend` into `/app` for development and uses `--reload`. For a clean rebuild after dependency or code changes, run:

```bash
docker compose down
 docker compose build --no-cache
 docker compose up -d
```

The database directory is mounted at `/app/database`, so SQLite data survives container recreation.

## v6 decision-room fixes

- Counterfactual scenarios use the same 45/30/25 deterministic award weights as the main decision.
- Scenario vendors are explicitly marked `ELIGIBLE` or `INELIGIBLE`, with the exact mandatory-gate reason. A cheaper vendor with a mandatory FAIL or unresolved mandatory evidence cannot be shown as the scenario winner.
- Proposal-derived TCO is recalculated from unit price × scenario volume, setup cost, shipping and renewal escalation.
- The requirement/template prompt remains the single source of truth; `requirements.txt` is not required for uploads.
- The Vendor Battleground now uses live evidence-backed financial, technical, reliability and service/support scorecards.
- The UI uses a light procurement-oriented visual system with responsive tables, clearer gate statuses and scenario eligibility treatment.

## Troubleshooting

### 429 / quota / resource exhausted

Add more independent Gemini API keys to `GEMINI_API_KEY`. BidLens will distribute concurrent in-flight calls across the configured keys. The worker count is automatically limited by the available key count.

### Pydantic validation errors involving evidence

Gemini responses may use semantically equivalent evidence fields such as `category` and `snippet`. BidLens normalizes those variants into the stable API contract (`field`, `value`, `source`, `confidence`) before constructing the final `Comparison` model.

### Debug logging

For detailed provider failures and JSON validation traces:

```env
LOG_LEVEL=DEBUG
```

Then restart the backend container:

```bash
docker compose restart backend
```

Logs include stage, worker counts, retry/model information, key slot numbers, and failure classifications, but **never log the actual API key**.

## AI architecture

- Extraction Agent: Gemini structured JSON extraction
- Risk Agent: Gemini risk analysis
- Award Agent: Gemini procurement reasoning
- Negotiation Copilot: Gemini evidence-backed negotiation levers
- Decision Engine: deterministic Python scoring and mandatory-gate protection
- Scenario Engine: deterministic TCO + award simulation

The LLM does not perform final economic arithmetic. This makes the decision layer easier to test and defend.

## Tests

From the repository root:

```bash
pytest -q
```

The test suite covers model failover parsing, multi-key configuration, evidence normalization, safety/database behavior, and scenario behavior.

### Upload format

The `/api/analyze` endpoint accepts the buyer requirements either in the `requirements` field or as an uploaded file named `requirements.txt` (also recognized: `requirements.md`, `rfp.txt`, `rfp.md`, `requirements.csv`). Requirements files are never analyzed as vendors. At least two other files must be present as vendor proposals.

The frontend reset button clears the displayed decision room, scenario result, selected uploads and saved latest run so a new comparison starts cleanly.

## Decision-quality requirement gates

The Requirement Gate is deliberately evidence-first: `PASS` means the proposal explicitly supports the requirement, `FAIL` means explicit contrary evidence, and `NEEDS CONFIRMATION` means the proposal is silent, ambiguous, roadmap-only, or otherwise insufficient to award the requirement. Missing mandatory evidence does not qualify a vendor for award; BidLens will show `HOLD / NO QUALIFIED VENDOR` until the gate is resolved.

For best demo results, vendor proposals should explicitly state: API availability/authentication, SSO/SAML, SCIM inclusion, data-residency regions, SLA/P1 response, implementation duration, contract term, payment terms, renewal cap, data export, sandbox availability, and preferred capabilities. This improves evidence coverage without asking the model to guess.

During analysis, the Run Award Analysis button cycles through visible stages while the backend runs: reading proposals, extracting vendor facts, checking requirements, scoring vendors, and finalizing the recommendation. Backend logs expose the same stage boundaries for troubleshooting.


## Current UX / analysis model

The buyer ask is now defined entirely by the prompt: select an optional procurement template to populate the prompt, or start with the placeholder **Add your custom requirements** and write your own ask. Vendor proposals are the only required uploads. Any legacy `requirements.txt`/RFP-style upload is ignored rather than treated as a vendor.

### Decision-quality guardrails
- Explicit proposal text is used to ground high-value extracted facts; conflicting LLM-only values do not override source evidence.
- `SSO` is not silently promoted to `SAML`.
- Roadmap / planned residency is surfaced as **NEEDS CONFIRMATION**, not PASS.
- Missing mandatory evidence prevents a vendor from becoming award-eligible.
- Missing data is represented as a confirmation action rather than fabricated zeroes.
- Counterfactual scenarios recompute volume-based TCO and commercial score and still respect mandatory gates.

### Hackathon differentiator: AI Red-Team Challenge
After an analysis, use **Red-team the recommendation** to ask an adversarial AI agent to try to disprove the decision using only the extracted evidence and requirement gates. It can return **DEFEND**, **REOPEN**, or **HOLD** with evidence-backed challenge points.

### Docker
Compose is configured to rebuild the backend and frontend images from source on `docker compose up` using Compose's `pull_policy: build` plus `build.no_cache: true` / `build.pull: true`. The backend no longer mounts the source directory into the container, so the built image is the code that runs. Docker documents that `pull_policy: build` causes Compose to build/rebuild an image and `no_cache` disables layer cache; `docker compose up` also supports services with a `build` definition.

```bash
docker compose down
docker compose up
```

For normal local development without container rebuilds, use your usual frontend/backend dev commands separately.

### Recommended CRM demo
The included CRM PDFs intentionally contain both strong evidence and material omissions. With the Enterprise SaaS / CRM template, ApexWorks should not be presented as a definitive winner unless the missing mandatory facts (for example SAML, implementation commitment, and payment terms) are confirmed. Northstar has stronger security/residency evidence but still has missing commercial/operational commitments. CloudPeak has a renewal failure and roadmap-only India residency. This is intentional red-teamable demo behavior, not a fabricated win.

## Authentication

The web UI is protected by a login screen. The default demo credentials are:

```text
username: admin
password: productspace
```

For production, these are runtime environment variables, not source-code credentials:
`BIDLENS_USERNAME`, `BIDLENS_PASSWORD`, and `AUTH_SECRET`. The browser never contains the Gemini keys or the password. After login, the backend issues a signed, time-limited bearer session token.

For local development:

```bash
cd backend
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

Set your Gemini keys and keep `backend/.env` uncommitted.

## GitHub publication

From the repository root after extracting the project:

```bash
git init
git add .
git status
# Confirm backend/.env and database/*.db are NOT staged.
git commit -m "Initial BidLens release"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git
git push -u origin main
```

If the GitHub repository already exists and already has a remote, use:

```bash
git remote -v
git add .
git commit -m "Prepare BidLens for Render deployment"
git push
```

Before the first push, rotate any Gemini key that was exposed in chat, terminals, screenshots, logs, or prior commits. Never add `backend/.env` to Git.

## Render deployment

This repository includes `render.yaml` for a two-service Render Blueprint:

- `bidlens-backend`: Dockerized FastAPI web service
- `bidlens-frontend`: Render static site for the React/Vite UI

Render supports Docker services built directly from a Git repository and rebuilds them on deploys. The Blueprint also wires the frontend API URL and backend CORS origin from the services' Render URLs, so you do not need to hard-code an `onrender.com` URL in the repository.

### Deploy with the Blueprint

1. Push the repository to GitHub.
2. In Render Dashboard choose **New → Blueprint**.
3. Connect the GitHub repository containing `render.yaml`.
4. Review the two services and deploy the Blueprint.
5. During initial Blueprint creation, Render will prompt for values marked `sync: false`.
6. Set `GEMINI_API_KEY` to your JSON list or comma-separated list of keys.
7. Set `BIDLENS_PASSWORD` to `productspace` for this demo, or choose your own production password.
8. `AUTH_SECRET` is generated automatically by the Blueprint.
9. The frontend receives the backend Render URL through `VITE_API_URL` and the backend receives the frontend URL through `CORS_ORIGINS`.

### Render environment variables

Backend secrets/config:

```text
GEMINI_API_KEY=["YOUR_KEY_1","YOUR_KEY_2","YOUR_KEY_3"]
GEMINI_MODELS=gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite,gemini-1.6-pro-flash
GEMINI_MODEL=gemini-3.6-flash
MAX_OUTPUT_TOKENS=65536
TOP_P=0.95
THINKING_LEVEL=medium
AI_RETRIES=2
AI_RETRY_DELAY_SECONDS=0.5
AI_MAX_WORKERS=8
AI_KEY_WAIT_SECONDS=60
LOG_LEVEL=INFO
BIDLENS_USERNAME=admin
BIDLENS_PASSWORD=productspace
AUTH_SECRET=<generated by Render>
DB_PATH=/tmp/bidlens.db
CORS_ORIGINS=<wired from frontend service by render.yaml>
```

Frontend configuration:

```text
VITE_API_URL=<wired from backend service by render.yaml>
```

`VITE_API_URL` is intentionally not treated as a secret because it is the public API URL. Vite embeds frontend environment variables during the build, so changing it on Render triggers a rebuild of the static site. Keep Gemini keys and passwords only on the backend service's Environment page.

### Manual Render setup instead of Blueprint

Create the backend as a **Web Service → Docker** using `backend/Dockerfile`, set the health check to `/api/health`, and set the required backend environment variables. Create the frontend as a **Static Site**, root directory `frontend`, build command `npm ci && npm run build`, publish directory `dist`, and set `VITE_API_URL` to the backend service's public URL. Add `CORS_ORIGINS` on the backend to the frontend public URL.

Render automatically rebuilds linked Git services when new commits are deployed. The Docker service listens on the Render-provided `PORT` and binds to `0.0.0.0`, as required for public web services.

### SQLite note

The default Blueprint uses `/tmp/bidlens.db` because Render's ephemeral filesystem is appropriate for the hackathon demo. A restart/redeploy can reset this SQLite history. For durable production data, replace the database layer with managed Postgres or attach a persistent disk on a paid plan.

## Judge sample resources

After signing in, use the **Resources** button in the top-right corner to access the bundled demo proposal packs. Judges can download individual proposal PDFs or the full sample pack. The old per-folder `requirements.txt` files are intentionally removed: the buyer ask now lives in the procurement template/custom prompt.

Sample packs include CRM, hidden-TCO, security-gate, payroll-missing and negotiation scenarios.
