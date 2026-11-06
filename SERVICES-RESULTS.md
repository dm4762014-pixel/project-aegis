# Connection Clinic v2 — service checks and report comparison

Verified on 5 November 2026. Existing app extended in place; no public deployment was performed.

## User problem and workflow

A lab technician needs to investigate “the portal fails on some machines” rather than only ask whether the internet works. This version checks the actual public service URL and compares exported evidence from multiple reported devices. Shared symptoms narrow the investigation; they do not establish a shared cause.

## Current verified state

- 36 automated tests passed, including previous functionality and new service/security/schema/comparison cases.
- Real network checks from one Windows laptop: GitHub, example.com and PyPI returned HTTP 200 after DNS, TCP and verified TLS passed.
- A nonexistent example.com path returned HTTP 404. DNS, TCP and TLS passed; HTTP was a warning, correctly distinguishing a missing page from a connection failure.
- Browser tests exercised the real service check/export, successful file imports, duplicate rejection, report comparison, combined report export, explicit simulation labels, and a 390px layout without horizontal page overflow or JavaScript errors.
- Existing five-check connection workflow, before/after comparison and report export still passed browser regression checks.
- Dependency consistency check passed.

## Demonstration you can show now

1. Open http://127.0.0.1:8000/#service-lab . Check https://github.com/ with a label such as “My laptop.”
2. Explain the separate stages and the actual HTTP 200 response. Download the service report.
3. To show the comparison without claiming access to extra machines, click **Load simulated lab example**, then **Compare imported reports**. The interface visibly labels the data as simulated.
4. Show that two simulated reports fail DNS while the third passes. Explain that this suggests a direction for investigation, not a confirmed shared outage.
5. Download the comparison. For real validation later, run the app on classmates' computers and import their reports for the exact same service URL.

Use your own words and explain your actual contribution. Substantial implementation and tests were completed with Codex assistance. A public repository/demo URL is still needed if reviewers should access it without downloading and running the project. A localhost link only works on this computer.

## Evidence files

- results/services-live.json: real public-service results, one laptop.
- results/service-github.json, service-example.json, service-pypi.json: real service reports from the same laptop.
- results/service-http-negative-control.json: real HTTP 404 control.
- results/services-browser.json: browser verification and its provenance.
- results/service-comparison-mixed-example.json: one real report plus two explicitly simulated reports, exported through the browser. NOT a real three-computer experiment.
- results/automated-tests.txt: 36 passing tests.

## Changes and design decisions

service_checks.py adds URL normalization, public-address validation and pinned connections, staged probes, validated report schemas, duplicate/target checks, aggregate patterns, and explicitly simulated sample reports. gateway.py registers the endpoints and serves the added script. static/clinic.html and static/service-ui.js add the forms, report collection, table and downloads. tests/test_services.py, tests/service-live.py and tests/services-browser.cjs provide deterministic, real-network and real-browser checks. package.json adds the optional browser-test command. Existing files and the earlier AI experiment are preserved.

The first live example.com TLS attempt failed because the system certificate store could not find the issuer. Verification succeeded using the existing HTTPX-compatible certifi CA bundle; the app now uses that bundle consistently. Certificate verification and hostname matching remain enabled. An enterprise-only trust root may still differ from this bundle.

## Boundaries and remaining validation

Only public HTTPS services on port 443; no internal-only college servers, credentials, query tokens, redirects, authenticated workflows or response bodies. Up to three DNS answers are attempted. This does not check every address or match every browser/proxy setup. Certificate failure does not uniquely identify its cause. Report identity and contents are self-reported, not signed or hardware-authenticated. Reports may have different timestamps and originate on different networks.

No real college deployment, real multi-computer experiment, real repair, user adoption, measured technician time savings, or admissions outcome is claimed. Next validation: observe a technician using real reports from a few authorized lab machines to decide the next troubleshooting step, then measure whether the evidence is useful.

## Documentation references

Python streams and TLS certificate verification: https://docs.python.org/3/library/asyncio-stream.html and https://docs.python.org/3/library/ssl.html .
