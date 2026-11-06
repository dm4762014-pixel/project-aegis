# Local AI Mesh — verified results

Test date: 5 November 2026 (India). One Windows laptop, 7.69 GiB physical RAM. Model: qwen2.5:0.5b (Q4_K_M), local Ollama. No paid AI API used for inference.

## Final measured run

- 9 automated tests passed in the isolated Python environment. Dependency check: no broken requirements.
- All 6 live HTTP requests returned HTTP 200; all 6 benchmark assertions passed.
- New gateway cache key: **1448.47 ms** round trip.
- Exact cached repeat: **7.761 ms** round trip.
- Four simultaneous identical new prompts: one actual model call, three cache reuses; about 1.18 seconds per request including waiting.
- Real headless Chromium test: successful answer, repeated answer served from cache, no JavaScript page errors, no horizontal overflow at 390px viewport.

These are samples, not averages or promises. The model was already loaded during the final new-key test. The first earlier run measured 4209.209 ms then 10.605 ms; its raw report is retained separately. Gateway-only timings exclude browser/network time and must not be compared with end-to-end model timings.

## Failure and correction

The original benchmark address was incomplete, and its output did not check HTTP failures. The original cache ignored model identity. The first real run with installed Llama 3 8B encountered approximately 0.02 GiB available system RAM and a gateway timeout; the benchmark client also timed out. This is an observed failure, not a completed model-quality comparison. The 8B model was unloaded and a roughly 398 MB 0.5B model was installed. No existing model was deleted. The default now selects the small model and the interface lists installed models smallest first.

## Important limits

The small model incorrectly called RAM non-volatile, and 24-token benchmark outputs are truncated. Infrastructure checks passed; factual accuracy did not. The project is not a dependable educational tutor yet. Available RAM remained low during testing. The 512 MiB preflight check may refuse new work; that is intentional. Close unnecessary apps if that happens. No guarantee of preventing memory exhaustion is made.

No physical phone, other laptop, real student adoption, sustained load, production deployment, distributed cluster, or financial savings was verified. This version runs only on this computer. A localhost URL is not an application-reviewer-accessible demo.

## Show it in your application

1. Open http://127.0.0.1:8000 and choose qwen2.5:0.5b.
2. Ask a short question. Point out the actual response and its limitations.
3. Repeat it to show CACHE_HIT and the cache-hit counter.
4. Explain that four duplicate concurrent requests share one model call, supported by the saved report.
5. Explain the real 8B memory failure and why you chose a smaller model.
6. Share the source ZIP or upload the source to your public repository after reviewing it. Record a short screen demonstration; localhost itself is not shareable.

Substantial implementation and testing were done with Codex assistance. Do not claim unaided authorship or user adoption. Use these facts to write your own explanation of what you understand and contributed.

## Reproduce

See README.md. Raw live report: results/live-benchmark.json. Automated test output: results/automated-tests.txt. Browser report: results/browser-check.json. Original files are preserved locally under backups/original-20261105.


## Controlled comparison added

36 successful real HTTP requests, three paired trials per workload, three sequential requests per route per trial; route order alternated. Same model and generation settings. Model warmed before timing; UUID prompts prevent accidental gateway cache reuse between trials. No concurrent external gateway requests were detected by request counters.

| Workload | Route | Model calls / 9 requests | Median time per 3-request trial |
|---|---|---:|---:|
| repeated | direct | 9 | 4.483 s |
| repeated | gateway | 3 | 1.304 s |
| unique | direct | 9 | 4.267 s |
| unique | gateway | 9 | 3.906 s |

Interpretation: exact-answer reuse avoided 6 of 9 model calls on the repeated workload. Unique requests still needed 9 calls on both routes. Differences in unique-prompt timing are noisy and are not proof of a speed benefit. This is an intentionally cache-friendly repeated workload, not representative user traffic. Ollama has its own lower-level caches. No claim of novel caching research is made.

The interface now displays this comparison and links to raw trial timings and generated answers. Run `.\.venv\Scripts\python.exe compare.py` while nobody else is using the gateway to reproduce. CPU work is performed locally. Automated suite now has **9 passing tests**; real browser check also verifies that the experiment table loads, then checks the demo and mobile-width layout.
