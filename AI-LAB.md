# Local AI Mesh

A local-first AI gateway for exploring reliable inference on student laptops. This is the existing Enterprise AI Mesh project, repaired and extended in place. It is a single-process prototype, not a distributed cluster or a production service.

## Start on Windows

1. Install Python 3.11+ and Ollama.
2. In a terminal: `ollama pull qwen2.5:0.5b` (about 398 MB download). Model installation needs internet; installed-model inference is local.
3. Double-click `run_cluster.bat`.
4. Open http://127.0.0.1:8000 . Choose the small model, enter a question, then repeat it to see a cache hit.

The launcher creates `.venv`, installs dependencies, and runs in the foreground so errors are visible. On this computer the working environment and model are already installed. If port 8000 is occupied by this session's running server, use the existing page rather than launching a second server.

## What changed

- Fixed the malformed benchmark address: requests now use `/v1/orchestrate` on port 8000.
- Added a browser interface and a real Ollama/model readiness check.
- Keyed cached answers by model, exact prompt, and output token limit. Bounded cache to 64 entries with a 10-minute lookup expiry and LRU eviction; restart clears it. Expired entries are removed on lookup or displaced by eviction.
- Serialize local generation; admit up to four outstanding requests and reject excess with HTTP 429. Identical queued requests reuse the first answer.
- Added an advisory available-RAM check (512 MiB), input/output limits, a 180-second queue-plus-generation deadline, and useful error responses.
- Disabled environment proxy routing for local HTTP. Browser requests must use the same origin; bind only to localhost.
- Replaced unsupported claims about zero compute cost, enterprise readiness, and thousands of users with measured results.

## Verify

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe benchmark.py
.\.venv\Scripts\python.exe -m pip check
```

Automated tests mock Ollama to exercise cache isolation/expiry/eviction, duplicate concurrency, queue overflow, invalid input, cross-origin requests, upstream failures, timeouts, low memory, readiness, and cleanup. The live benchmark uses actual HTTP and Ollama without mocks. It saves raw responses and timings and exits unsuccessfully when checks fail. `cold` means a new gateway cache key; it does not imply the model was unloaded.

Optional browser verification (Node.js needed):

```powershell
npm install
npx playwright install chromium
npm run test:browser
```

The browser check sends a real prompt, repeats it, checks a cache hit, and checks page errors and horizontal overflow at a 390px viewport. Screenshots go in `results/`. A phone-sized browser viewport is not a physical phone test.

## Evidence and limitations

See `RESULTS.md` and `results/` for raw evidence. Runs reflect one Windows laptop and short outputs; they are not a throughput or model-quality study. The initial Llama 3 8B attempt timed out under memory pressure; a 0.5B model completed successfully. Small models can give incorrect facts (the benchmark RAM definition is wrong) and capped outputs can be incomplete. Do not use this as a verified tutor or rely on responses without checking them.

The RAM threshold cannot predict all allocation or prevent an OS crash. Waiting requests can also time out; cancellation of the HTTP request does not guarantee Ollama immediately stops its underlying work. No multi-user authentication, durable job queue, persistent/shared cache, streaming, distributed routing, model auto-selection, or GPU monitoring is implemented. Localhost only; do not expose to a public network. Prompts and cached responses are shared within this local process. No compute cost savings in dollars are claimed.

## Project layout

- `gateway.py`: API, scheduler, cache, hardware metrics, local-browser checks.
- `static/index.html`: browser interface.
- `benchmark.py`: real HTTP benchmark and JSON report.
- `tests/test_gateway.py`: deterministic automated checks.
- `tests/browser-check.cjs`: real browser check.
- `requirements.txt`: direct dependencies; `requirements-lock.txt`: exact tested environment.
- `backups/original-20261105/`: preserved original files, kept locally and excluded from the shareable ZIP.

## Application honesty

Development, debugging, tests, and documentation in this version were completed with substantial Codex assistance. Describe only your own decisions, investigation, and work as personal contributions. Read the code, reproduce the demo, explain the limitations, and write application answers in your own words. No real student adoption or admissions outcome has been established.

## API references

The Ollama request uses its documented non-streaming generation endpoint: https://docs.ollama.com/api/generate . Deterministic tests use HTTPX mock and ASGI transports: https://www.python-httpx.org/advanced/transports/ .

## Reproducible comparison

Run `.\.venv\Scripts\python.exe compare.py` while nobody else is using the app. It compares direct Ollama with the gateway on repeated and unique workloads, alternating route order over three trials. Open the interface to inspect the resulting comparison and raw data. This is a small controlled experiment, not a novel algorithm or representative traffic study.
