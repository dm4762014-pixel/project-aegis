# Project Aegis — working 8 GB laptop profile

Verified on Windows 11 on 5 November 2026. The existing E:\enterprise-ai-mesh project was modified in place. Pre-change gateway/benchmark copies remain in backups/pre-laptop-profile. No unrelated application was terminated by the assistant. The user offered to close unused applications to create headroom.

## Run the working demo

The running demo is **http://127.0.0.1:8000/aegis**. Its tab has been left open with a successful live proof. Click **Run real inference & cache proof** to generate a new answer, repeat it, issue twelve cached requests and check the expected rejection of an uncached request after the burst. Allow five quiet seconds between runs.

To restart from this project folder, double-click **run_aegis_laptop.bat**. It prepares Python dependencies, starts/reuses the isolated model service, installs its small model if needed, and starts Aegis. Install Python and Ollama first. Initial setup requires internet for dependencies and the 105 MB model; installed-model inference is local. Node.js is not needed for this dashboard; the preserved developer browser lab has its own optional Node/Playwright setup.

Use one copy at a time: port 8000 hosts the gateway and 11435 hosts this dedicated Ollama instance. An unknown occupied model port is refused, not stopped. A second extracted checkout is intentionally not allowed to take over the first checkout's model service. Use the already-running original project, or stop that original instance before using a different checkout.

## What was causing trouble

System RAM was fluctuating above the 90% admission/recovery threshold before inference, and the shared Ollama endpoint was not running. Clearing a small Python cache cannot solve memory held by other applications. Loading a large model would make that situation worse.

The laptop profile therefore uses **SmolLM2 135M instruct Q4_K_M**, downloaded size **105,466,867 bytes**. It runs on CPU with two threads, a 512-token context, a 32-token prefill batch and one active generation. The dedicated Ollama server is bound to 127.0.0.1:11435 and uses only this project's runtime/aegis-models folder. It is restricted to one loaded model and one parallel inference. No system-wide Ollama settings were changed.

Each generation sends **keep_alive=0** so weights unload afterward. Cached answers stay in Aegis and do not reload the model. The laptop profile rejects requests for larger/unconfigured models before contacting Ollama. The cache byte budget is 1 MiB; the queue admits at most two outstanding requests.

## Safeguards stayed enabled

The critical RAM threshold remains **90%**. Laptop admission is stricter: it waits up to **eight seconds** for RAM at or below **89%**, with at least 512 MiB available, before loading the model. If no slot opens it returns 503. Cached responses bypass this expensive path. The background monitor still runs every five seconds and the recovery cooldown remains thirty seconds.

This is an admission rule, not a hard operating-system memory cap. Overall RAM briefly exceeded 90% during successful tests, including unrelated work. The fix is a smaller bounded workload, short residency, safe waiting and explicit refusal when necessary; it is not a claim that all system pressure is eliminated. The separate model process is not falsely counted as gateway memory.

## Final real CLI benchmark

Command: `.\.venv\Scripts\python.exe benchmark.py --output results/aegis-live-laptop-final.json`

The CLI discovered the configured model from the running node. All **eight checks passed**, with real HTTP and real Ollama generation, no mocks:

| Observation | Result |
|---|---:|
| Fresh uncached request, including memory wait and model load | 2040.194 ms |
| Exact cached repeat | 5.275 ms |
| Identical burst requests served from cache | 12 / 12 |
| Total actual model calls during the benchmark | 1 |
| Successful inference endpoint responses | 14 HTTP 200 |
| Deliberately uncached request after flood | 1 expected HTTP 429 |
| Memory-slot wait before generation | 785.333 ms |
| Model-load duration reported by Ollama | 905.611 ms |
| Generated output tokens | 12, capped by the benchmark |
| Resident model list after run | Empty |

The generated benchmark answer is a short, capped model output, not a factual-quality evaluation. The first earlier passing run measured 1302.518 ms then 6.541 ms; it is retained separately. These are sample timings, not averages or universal speed promises.

The final benchmark sampled actual local RAM 21 times with a target 100 ms interval: **88.8–91.3%**. Sampling cannot establish the absolute peak or attribute all usage to Aegis. Both the 90% monitor threshold and 89% admission target remained unchanged. No physical RAM was deliberately exhausted.

## Real browser proof

The visible in-app browser ran a new, real question end to end, with:

- One actual model call and 26 generated tokens; the answer was shown unedited.
- Fresh-request round trip **2292.7 ms**, cached repeat **32.7 ms**.
- All twelve burst requests returned cached answers.
- The final uncached request returned 429 as intended.
- All six browser-proof checks passed; the downloaded JSON was inspected.
- The real Ollama residency endpoint was checked afterward and its models list was empty.

This was a real visible browser check, not a Playwright response mock. The screenshot shows the actual successful state. The Aegis page was checked in the app's narrow browser pane; no physical-phone claim is made. Browser timings include browser/UI overhead and should not be treated as equivalent to the CLI's timings.

## Automated and recovery tests

**74 automated tests passed**, including previous diagnostics, Aegis safety checks and eight new laptop/launcher cases. They cover small CPU options, immediate unloading, blocking large models, retaining the RAM guard, waiting for fluctuating RAM, avoiding repeat model loads, residency failures and leaving unknown services alone. Dependency consistency also passed.

The separate recovery benchmark passed all **12 assertions** using a mocked upstream and injected RAM counters. It includes an actual 3.6-second async delay. Its accelerated monitor/cooldown are test-only. The dashboard labels these results **SIMULATED**, separately from real inference. No live RAM-allocation fault was introduced.

Reproduce:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe benchmark.py --simulate
.\.venv\Scripts\python.exe benchmark.py --output results/aegis-live-laptop.json
```

## Evidence and source changes

- results/aegis-live-laptop-final.json: final passing real CLI benchmark, RAM trace and residency proof.
- results/aegis-live-laptop-first.json: first passing CLI benchmark.
- results/aegis-browser-proof.json: downloaded real browser result.
- results/aegis-laptop-residency.json: real post-run model list.
- results/aegis-simulated.json: explicitly simulated recovery test.
- results/automated-tests.txt: 74 passing tests.
- The old failed results/aegis-live.json is preserved as historical evidence.

gateway.py adds the laptop profile, bounded memory-slot waiting, constrained CPU options, allowed-model restriction, output token/load metadata, real residency reads and evidence routes. benchmark.py discovers the model, samples real RAM, verifies unloading, stops the flood if priming fails, and explicitly isolates simulation from the runtime profile. laptop_setup.py and run_aegis_laptop.bat provide reproducible isolated startup. static/aegis.html and aegis-ui.js provide the demo. tests/test_laptop.py covers the new behavior. Runtime model weights, private instance records and logs are excluded from the source archive.

## What you can demonstrate in your Academy application

Show the real model answer and its cache reuse; explain why an 8 GB host needs a small model, bounded context and short residency. Show the expected 429 after a repeated-request burst. Point out the separate simulated recovery panel. Explain what remains uncertain and how Codex helped implement and test it, in your own words.

No public deployment, sustained workload, large-model performance, model-quality accuracy, user adoption, coordinated multi-node cluster or admissions result has been verified. The tiny model trades quality for a small footprint; it is an orchestration demonstration, not a reliable tutor or coding assistant. If the laptop is busy, waiting or a 503 is still an honest and expected safety outcome. The memory controls cannot prevent every system freeze or control unrelated applications.

Sources: [SmolLM2 135M Q4_K_M model](https://ollama.com/library/smollm2:135m-instruct-q4_K_M), [Ollama context and residency settings](https://docs.ollama.com/faq), [generation API keep_alive](https://docs.ollama.com/api/generate).
