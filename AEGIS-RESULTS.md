> Historical pre-laptop-profile snapshot. Current working results: [AEGIS-LAPTOP-RESULTS.md](AEGIS-LAPTOP-RESULTS.md).

# Project Aegis — implementation and verification

The existing gateway and benchmark were upgraded in place. Original files are preserved in `backups/pre-aegis`. The core runs as a single Python file; existing diagnostic modules are mounted only when present.

## Run

Install `requirements-lock.txt`, start Ollama separately, then run:

```powershell
python -m uvicorn gateway:app --host 127.0.0.1 --port 8000 --no-proxy-headers
python benchmark.py --simulate
python benchmark.py --model qwen2.5:0.5b
```

Python 3.14 on Windows 11 was tested. The code uses Python 3.11+ syntax. For continuity with this 8 GB host the default model remains qwen2.5:0.5b. Select Llama 3 explicitly in the request or set `AEGIS_MODEL=llama3:latest` before starting. Llama 3 inference was not validated in this upgrade. No model is downloaded or unloaded automatically.

## Telemetry

`GET /cluster/telemetry` exposes OS context-switch totals, gateway page faults/RSS, RAM, disk I/O counts/bytes/time, event-loop lag, inference queue waits and bounded per-inference resource deltas. Samples run in worker threads; the health loop runs every five seconds by default. Inference measurement intervals use a microsecond clock. OS counters do not have microsecond sampling resolution, and system-wide deltas include unrelated applications. Disk average I/O time is an observation, not a disk queue-length measurement.

Windows page-fault counts belong to this gateway process, not Ollama or system hard faults. Unsupported OS run-queue, system hard-fault and GPU/VRAM measurements are explicitly unavailable. The first psutil CPU percentage sample may be uncalibrated.

## Adaptive admission and cache

Bounded middleware tracks real connection-peer traffic and rejects forwarded identity headers. Rapid/repeated requests enter a temporary CACHE_ONLY lane. An exact cached answer can be returned without model inference; a cache miss gets HTTP 429 and Retry-After. `developer_stress_index` is a traffic-pattern proxy, not a psychological diagnosis. Lane/index headers also appear on guarded error responses.

Hot and warm LRU tiers have TTL, entry limits, and a payload/metadata budget. Keys include model, exact prompt, output limit and effective context. No semantic or shortened answer is invented. Charged bytes are not a total process RSS cap. One heavy inference runs at a time, with four outstanding requests by default. Input bodies are capped at 64 KiB, upstream response JSON at 256 KiB, and queue-plus-inference duration at 180 seconds.

## Recovery

RAM above 90% or a new completed/in-progress upstream cycle above 3500 ms triggers cache invalidation, HTTPX pool retirement and context reduction: 2048 → 1024 → floor 512. A 30-second cooldown and RAM hysteresis prevent thrashing. Old pools drain active leases rather than cutting off requests. A generation epoch prevents an old in-flight answer refilling a cleared cache. Persistent pressure blocks uncached work. Context is not restored automatically.

Recovery changes only gateway-owned caches, HTTP clients and future inference options. It does not change adapters, OS socket tables, shared Ollama state, model files or unrelated processes. It cannot guarantee the host never freezes or reclaim memory held by other software. `GET /cluster/recovery` is read-only; there is no HTTP fault-injection endpoint.

Logs rotate at 2 MiB per file with two backups in `results/aegis.jsonl`. Rate-limit log events are coalesced per client. Prompts, answers and raw client addresses are omitted. Standard rotating logging has disk overhead; no zero-overhead throughput claim is made.

## Tests and evidence

```powershell
python -m unittest discover -s tests -v
python benchmark.py --simulate
```

- **66 workspace tests passed**, including earlier application regressions and 16 Aegis-focused checks.
- **16 core checks passed in an isolated copy without optional app modules.** Its standalone simulated benchmark passed as well.
- **All 12 simulated benchmark assertions passed.** They cover cached flood handling, uncached 429, byte budgeting, both recovery causes, draining an active pool, stale-generation cache suppression and context step-down.
- Simulation uses an HTTPX mocked upstream and injected OS counters. An actual 3.6-second async delay crosses the unchanged 3500 ms threshold. RAM=95 is a synthetic reading, not a physical allocation. Only the simulation's monitor interval/cooldown are accelerated to 0.05/0.1 seconds.
- The existing developer browser demo still passed: 12 page loads, five deliberate faults, healthy control, screenshots, export, HAR import and mobile-width layout.
- **The real-host inference benchmark did not pass.** RAM pressure refused uncached inference with HTTP 503, and admission control returned 429 during the flood. No successful real Llama 3 inference or live throughput improvement is claimed.
- A real monitor event recorded **90.1% RAM**, context reduction to **1024** and HTTP pool retirement. This is actual load shedding, not proof that system RAM was reclaimed.

Keep the failed live report. It documents the host limit rather than a passing model benchmark. Do not weaken the threshold merely to obtain a pass. Close unnecessary applications or use a host with more memory before deliberately repeating real inference.

Evidence: `results/aegis-simulated.json`, `aegis-live.json`, `aegis-host-telemetry.json`, `aegis-host-recovery.json`, and the standalone test log.

## Production boundary

This is a tested local, single-process node. Multiple Uvicorn workers would have independent limits/caches/recovery state and are not a coordinated cluster. Authentication, tenant isolation, shared scheduling, GPU instrumentation, public deployment, long-duration soak behavior and production workload outcomes were not verified. Existing diagnostics are preserved when their source files are alongside the gateway.

References: [psutil](https://psutil.io/), [HTTPX async clients](https://www.python-httpx.org/async/), [Ollama generation API](https://docs.ollama.com/api/generate).
