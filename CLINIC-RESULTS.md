# Connection Clinic — evidence for review

Built and tested on 5 November 2026. This is the existing project extended in E:\enterprise-ai-mesh; the earlier AI experiment is retained at /ai-lab.

## Problem and reason to use it

A connected laptop can still fail to load a website. A nontechnical user needs to collect evidence and see whether their attempted fix changed anything. Connection Clinic runs real checks and exports the before/after evidence. It does not require asking an AI what commands to run or interpreting raw command output. The value proposition is a hypothesis; no college users or adoption are claimed.

## What works now

- Five concurrent, fixed-target, read-only checks: usable interface, system DNS, TCP to a public IP, certificate-verified HTTPS, and a Microsoft network sign-in probe.
- Rule-based interpretation with uncertainty: a failed probe is not automatically called an outage.
- A rerun shows which check outcomes changed.
- A local JSON download contains both runs and their comparison.
- No AI, Ollama, account, administrator rights, network resets, or automatic setting changes are needed.
- Reports omit local IP addresses, interface names, Wi-Fi names and portal content. Network checks do make ordinary connections to the listed public endpoints.

## Verified evidence

- **22 automated tests passed**: 13 diagnostic tests plus 9 existing AI-lab regression tests.
- Dependency consistency check passed in the isolated environment.
- Real Chromium: two real diagnostic runs, all five checks rendered, before/after comparison rendered, JSON download verified, zero page errors, and no horizontal overflow at 390px width.
- In the final browser-exported run all five checks passed. Reported scan duration: **210.4 ms**. This is a sample on one laptop, not a speed guarantee.
- The comparison correctly reported no changes between the two healthy runs.
- Failure cases including possible DNS failure, possible portal interception, blocked IP probe, no usable interface, unknown adapter state, timeout, and concurrent request rejection were tested through simulations. They were not created on the user's real network.

## What we have not established

No real broken connection was repaired, no college-network deployment was performed, and no user adoption or time savings were measured. Checks cover a few public destinations, not the whole internet. Captive portals, DNS problems, firewalls, proxies, and blocked endpoints can overlap. Advice remains a hypothesis. A mobile-width browser test is not a phone test. This is not a novel networking algorithm or guaranteed admissions advantage.

## 60-second demonstration outline

- 0–10 seconds: Explain the connection problem and why evidence is useful for support.
- 10–25 seconds: Click Check my connection and show the five actual observations.
- 25–40 seconds: Explain one inference boundary: a blocked public IP does not prove an outage when HTTPS works.
- 40–50 seconds: Rerun and show the actual comparison, even if nothing changed. Download the report.
- 50–60 seconds: Explain what you personally understand, how Codex helped, and the next test with real users.

Use your own words. Substantial implementation and tests were completed with Codex assistance. Do not claim unaided authorship, proven root causes, real fixes, or customers.

## Useful next validation

Watch two classmates troubleshoot a real issue without coaching. Record where they become confused and whether support can use the exported report. Do not disconnect shared college networks or change other people's settings to create a demonstration.

## Reference

Microsoft's connectivity-probe behavior: https://learn.microsoft.com/en-us/windows-server/networking/ncsi/ncsi-frequently-asked-questions

## Files

README.md: startup and full limitations. results/clinic-live.json: real API scan. results/clinic-browser.json: real browser evidence. results/connection-clinic-report.json: downloaded before/after report. results/automated-tests.txt: test results. Original files remain in the local backups folder; that folder is excluded from the ZIP.
