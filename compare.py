"""Paired, alternating-order experiment. Real Ollama, same generation options."""
import asyncio
import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx


async def run():
    model = 'qwen2.5:0.5b'
    rows = []
    async with httpx.AsyncClient(timeout=190, trust_env=False) as c:
        base = 'http://127.0.0.1:8000'
        ollama = 'http://127.0.0.1:11434/api/generate'
        options = {'num_predict': 24, 'temperature': 0, 'num_ctx': 2048}
        # Warm model before timing. Gateway cache remains cold for UUID prompts.
        warm = await c.post(ollama, json={'model':model, 'prompt':'Say ready.', 'stream':False, 'options':options})
        warm.raise_for_status()
        for workload in ['repeated', 'unique']:
            for trial in range(3):
                nonce = uuid.uuid4().hex[:8]
                prompts = [f'Explain a computer network in one short sentence. Reference {nonce}-{0 if workload == "repeated" else i}.' for i in range(3)]
                # Alternate first route to reduce (not eliminate) order effects.
                for route in (['direct','gateway'] if trial % 2 == 0 else ['gateway','direct']):
                    before = (await c.get(base+'/cluster/telemetry')).json()
                    timings, statuses, strategies, answers = [], [], [], []
                    for prompt in prompts:
                        start = time.perf_counter()
                        try:
                            if route == 'direct':
                                response = await c.post(ollama, json={'model':model,'prompt':prompt,'stream':False,'options':options})
                            else:
                                response = await c.post(base+'/v1/orchestrate', json={'model':model,'prompt':prompt,'max_tokens':24})
                            data = response.json()
                            statuses.append(response.status_code)
                            strategies.append(data.get('execution_strategy','DIRECT_MODEL'))
                            answers.append(data.get('response'))
                        except httpx.HTTPError as exc:
                            statuses.append(0)
                            strategies.append(type(exc).__name__)
                            answers.append(None)
                        timings.append(round((time.perf_counter()-start)*1000,3))
                    after = (await c.get(base+'/cluster/telemetry')).json()
                    row = {'workload':workload,'trial':trial+1,'route':route,
                           'round_trip_ms':timings,'total_ms':round(sum(timings),3),
                           'http_statuses':statuses,'strategies':strategies,'answers':answers,
                           'model_calls':3 if route=='direct' else after['upstream_calls']-before['upstream_calls'],
                           'other_gateway_requests_detected':after['requests']-before['requests'] != (3 if route=='gateway' else 0)}
                    rows.append(row)
                    print(json.dumps({k:v for k,v in row.items() if k!='answers'}),flush=True)
    valid = all(all(s == 200 for s in r['http_statuses']) and all(r['answers'])
                and not r['other_gateway_requests_detected']
                and r['model_calls'] == (1 if r['route']=='gateway' and r['workload']=='repeated' else 3)
                for r in rows)
    summary = []
    for workload in ['repeated','unique']:
        for route in ['direct','gateway']:
            group = [r for r in rows if r['workload']==workload and r['route']==route]
            summary.append({'workload':workload,'route':route,'requests':9,
                            'model_calls':sum(r['model_calls'] for r in group),
                            'median_three_request_total_ms':round(statistics.median(r['total_ms'] for r in group),3)})
    report = {'timestamp_utc':datetime.now(timezone.utc).isoformat(),'model':model,'valid':valid,
              'method':'Three paired trials per workload, three sequential requests per route per trial; alternate route order; warmed model; identical options; unique UUID cache keys. Actual HTTP and Ollama; no mocks.',
              'limitations':['One laptop; tiny sample; OS load and Ollama prefix caching can affect timing.',
                             'Repeated exact prompts deliberately favor caching. Unique prompts measure the no-cache-benefit case.',
                             'Timing and call reduction do not establish answer accuracy or dollar savings.'],
              'summary':summary,'trials':rows}
    Path('results/comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if not valid:
        raise SystemExit('Experiment invalid: failed requests or concurrent gateway traffic. Inspect report.')
    print('PASS: results/comparison.json',flush=True)


if __name__ == '__main__':
    asyncio.run(run())
