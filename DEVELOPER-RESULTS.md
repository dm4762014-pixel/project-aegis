# Developer Clinic — verified demonstration

Existing app extended in E:\enterprise-ai-mesh. Test date: 5 November 2026. One Windows laptop. No public deployment or production access.

## What the product does

A developer needs evidence about why the frontend loads while its API or rendering fails. This version collects real browser network/error observations from an owned failure lab and can analyze a HAR capture from the developer's own application offline. Findings separate observed failure signals from suggested next investigations. No LLM is used for diagnoses.

## Real experiment

Five intentionally broken applications plus one healthy control were loaded by real headless Chromium over HTTP. Each was then reloaded using its supplied corrected variant. No response mocks or disabled browser-security settings were used. The classifier sees observations, not scenario/fix labels. These controlled cases are not unseen real-world incidents.

| Controlled case | Observed diagnosis before | App state after supplied correction | Result |
|---|---|---|---|
| Missing CORS header | CORS_BLOCKED | ready | Verified |
| Wrong frontend API route | API_ROUTE_MISMATCH | ready | Verified |
| API returns HTTP 500 | API_SERVER_ERROR | ready | Verified |
| HTML instead of expected JSON | API_NOT_JSON | ready | Verified |
| Frontend JavaScript crash | FRONTEND_RUNTIME_ERROR | ready | Verified |
| Healthy control | NO_ISSUE_OBSERVED | ready | Verified |

All five faulty variants had an actual browser-observed error state. All five corrected variants reached a ready state and the expected JSON API returned successfully without a covered error. The healthy control was ready in both variants. This is verification of known controlled cases, not a general accuracy score. Corrections were preconfigured by the lab, not discovered and applied automatically.

## Other verification

- **50 automated tests passed**, including existing app regressions, diagnosis rules, secret stripping, malformed/oversized captures, request locking, fixture behaviors, and ambiguous evidence.
- Browser integration: **12 real page loads**, **12 actual screenshots**, lab evidence export, and original HAR download.
- A real HAR recorded from the broken API-500 fixture was uploaded through the analysis form and independently produced API_SERVER_ERROR without making requests to the supplied URLs.
- Desktop and 390px-width browser layouts passed without horizontal page overflow or JavaScript errors in the diagnostic UI. JavaScript failures inside the deliberately broken fixture are expected evidence, not UI-test failures.
- Existing connection workflow and service/report comparison browser checks still passed.
- Python dependency consistency check passed. Node install audit reported no vulnerabilities at installation time.
- The lab released its temporary port 8012 after execution; the main demo remains running on port 8000.

## Try the demo

1. Open http://127.0.0.1:8000/developer . Click **Load last measured run** to inspect saved evidence or **Run browser failure lab** to repeat the experiment.
2. Open Missing CORS header. The before capture shows browser CORS rejection even though the independent API control returned HTTP 200. The after capture shows the app ready after the supplied exact-origin CORS correction.
3. Open Wrong frontend API route. Observe a 404 on a different path while the intended API returned 200, then a successful corrected variant.
4. Inspect API 500, HTML instead of JSON, and the uncaught frontend error. Review actual request evidence and paired screenshots.
5. Download the experiment evidence. To demonstrate HAR import, download a case's original broken HAR, copy the frontend/API URLs shown in its request details, and import it in the form below. The API-500 capture can be diagnosed from HAR alone; CORS and runtime errors often need console evidence that HAR does not contain.

## Main changes

- developer_diagnostics.py: explicit observation-based rules and bounded, sanitized HAR parsing.
- dev_lab.py: deliberately broken and preconfigured corrected frontend/API variants.
- dev_worker.cjs: real Chromium capture, browser errors, independent API control, screenshots and original lab HAR files.
- developer_routes.py: run lock, bounded worker, owned-process lifecycle, saved evidence, snapshot/HAR download and offline HAR import.
- static/developer.html and developer-ui.js: paired findings, evidence, screenshots, downloads and HAR analysis.
- gateway.py: new routes; existing app retained. run_developer.bat/package lock: reproducible browser setup.
- tests/test_developer.py and tests/developer-browser.cjs: deterministic and end-to-end checks.

## What remains unverified

No real developer incident, production deployment, general root-cause accuracy, login flow, complete application behavior, user adoption, or measured debugging-time benefit has been established. The live browser runner is deliberately restricted to owned local fixtures. Real-app analysis is via HAR import. Imported captures are unverified input and may omit essential evidence. Paths can remain sensitive even after query/header/body stripping. Screenshots and original HAR downloads are only generated for the owned lab, not from user imports.

Development and testing used substantial Codex assistance. Explain your own decisions and understanding accurately in an Academy application. Use the finite experiment results rather than claiming automatic fixes or superiority over established developer tools.

## Evidence

results/developer-lab-latest.json: complete measured run. results/developer-browser.json: UI verification. results/developer-captures/<run-id>/: actual PNG/HAR files. results/automated-tests.txt: unit/regression results. The packaged run ID is 123bfb25-c446-4645-9df1-b6197bf157e0.

References: https://playwright.dev/docs/api/class-page and https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS .
