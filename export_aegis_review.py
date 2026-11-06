"""Export recorded real/simulated evidence as a portable, offline HTML report.

This reads saved files; it never runs inference, contacts a server, or changes
machine settings. All values from reports are escaped before becoming HTML.
"""
import argparse
import hashlib
import html
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_report(path, expected):
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Report exceeds the 2 MiB review limit.')
    data = json.loads(raw)
    if not isinstance(data, dict) or not str(data.get('mode', '')).startswith(expected + ':'):
        raise ValueError(f'Expected an explicitly labeled {expected} report.')
    checks = data.get('checks')
    if not isinstance(checks, dict) or not checks or any(type(v) is not bool for v in checks.values()):
        raise ValueError('Report checks must be a nonempty mapping of boolean results.')
    if type(data.get('passed')) is not bool or not isinstance(data.get('rows'), list):
        raise ValueError('Missing report pass status or request rows.')
    return data, hashlib.sha256(raw).hexdigest()


def escaped(value):
    return html.escape(str(value))


def milliseconds(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
        return f'{value:,.3f} ms'
    return 'Not recorded'


def report_card(data, digest, kind):
    # Recompute the overall outcome; a claimed pass cannot hide a failed check.
    passed = data['passed'] and all(data['checks'].values())
    rows = ''.join(f'<li>{"PASS" if ok else "FAIL"} — {escaped(name.replace("_", " "))}</li>'
                   for name, ok in data['checks'].items())
    requests = ''.join('<tr>' + ''.join(f'<td>{escaped(value)}</td>' for value in (
        row.get('label', 'Request'), row.get('http_status', 'Unknown'),
        row.get('execution_strategy', 'Not recorded'), milliseconds(row.get('round_trip_ms')))) + '</tr>'
        for row in data['rows'] if isinstance(row, dict))
    memory = data.get('local_memory', {})
    ram = ''
    if isinstance(memory, dict) and 'max_percent' in memory:
        ram = f'<p>Sampled RAM maximum: {escaped(memory["max_percent"])}%. Sampling is not a hard cap or a guaranteed peak.</p>'
    return f'''<section><p class="label">{escaped(kind)}</p>
<h2>{'Checks passed' if passed else 'Checks did not all pass'}</h2>
<p>Recorded timestamp: {escaped(data.get('timestamp_utc', 'Not recorded'))}</p>
<p>Model: {escaped(data.get('model', 'Not recorded'))}</p>
<p>{escaped(data['mode'])}</p>{ram}<ul>{rows}</ul>
<details><summary>Recorded request outcomes</summary><div class="scroll"><table>
<thead><tr><th>Request</th><th>HTTP</th><th>Strategy</th><th>Round trip</th></tr></thead>
<tbody>{requests}</tbody></table></div></details>
<p class="fingerprint">Source SHA-256: {escaped(digest)}</p></section>'''


def build_review(live, simulation):
    cards = report_card(*live, 'REAL MODEL — RECORDED') + report_card(*simulation, 'SIMULATED RECOVERY — RECORDED')
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aegis — recorded evidence</title><style>
body{{font:17px/1.6 system-ui,sans-serif;color:#e6edf7;background:#0a1624;margin:0}}
main{{max-width:960px;margin:auto;padding:32px 20px}}h1{{font-size:clamp(30px,6vw,48px);line-height:1.1}}
section{{background:#14283c;border:1px solid #35516a;border-radius:16px;padding:24px;margin:24px 0}}
.label{{color:#8df0c7;font-weight:700}}.notice{{border-left:4px solid #eec982;padding-left:16px}}
.fingerprint{{overflow-wrap:anywhere;font-size:12px;color:#abc1d8}}summary{{cursor:pointer}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #35516a}}
</style><main><p class="label">PROJECT AEGIS / OFFLINE REVIEW</p>
<h1>Local AI requests, measured.</h1>
<p class="notice">This is saved evidence, not a live dashboard. Opening this file makes no network or AI calls.</p>
<p>A small local model, bounded context, exact-request caching and memory-aware admission were tested on one Windows laptop.</p>
{cards}
<section><h2>What this establishes</h2><p>The recorded real run checks generation, cache reuse, burst routing and post-run model unloading.
Recovery tests use a mocked upstream and injected counters. A rejected uncached burst request is an expected protection outcome.</p>
<p>These are individual short runs, not average performance, a model-quality evaluation, user adoption, sustained-load certification or proof of a distributed cluster.
Memory admission is not a hard Windows memory cap. Cached results reuse an answer rather than generating a new one.</p>
<h2>Contribution</h2><p>The applicant directed the product and requirements and requested safe, measurable tests.
Codex substantially implemented, debugged, tuned and ran the recorded checks. Further personal code understanding and real-user validation should be described honestly.</p>
<p>Source fingerprints identify the input bytes used for this export; they do not authenticate the claims or independently reproduce the experiment.</p></section>
</main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', type=Path, default=ROOT / 'results/aegis-live-laptop-final.json')
    parser.add_argument('--simulation', type=Path, default=ROOT / 'results/aegis-simulated.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/aegis-review.html')
    args = parser.parse_args()
    try:
        document = build_review(read_report(args.live, 'LIVE'), read_report(args.simulation, 'SIMULATED'))
    except (OSError, ValueError) as error:
        parser.error(f'Cannot export the selected evidence: {error}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding='utf-8')
    print(f'Wrote offline recorded-evidence review: {args.output}')


if __name__ == '__main__':
    main()
