# Aegis â€” work sample

**Purpose:** manage local AI requests on a resource-constrained laptop using
exact-request caching, memory-aware admission, bounded concurrency and telemetry.

**Current work:** an offline HTML evidence exporter and a fixed read-only gateway
review route, plus the preserved small-model laptop profile. The HTML distinguishes
recorded real inference from simulated fault tests and escapes source text.

**Verified on 6 November:** all 79 project tests passed, including five new export
and route checks. The standalone recorded report was opened in a real browser and
its request timings inspected. The new gateway route was verified in-process;
the standalone HTML works without starting the gateway. Start the updated
gateway to use the new route. The browser review used the standalone report.

**Earlier real evidence, 5 November:** 2,040.194 ms fresh request, 5.275 ms exact
cached repeat, 12 cached burst responses, expected uncached 429 and empty post-run
model residency. Recovery faults were simulated. See `AEGIS-LAPTOP-RESULTS.md`.
No new live inference was run during this portfolio preparation; sampled host RAM
was above 90%, so the existing safeguards were retained.

**Contribution:** applicant direction/requirements and insistence on safe measured
results; substantial Codex implementation, debugging, tuning and test execution.
No autonomous psychological diagnosis is claimed: the traffic score is a pattern proxy.

**Limits:** one laptop, short sample timings, limited tiny-model quality, no hard
OS memory cap, no sustained-load or distributed-cluster proof, and no verified
adoption. The source archive excludes model weights, instance records and logs.
