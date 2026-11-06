"""Aegis benchmark: explicit real-node and isolated fault-simulation modes.

Live:      python benchmark.py --model qwen2.5:0.5b
Simulated: python benchmark.py --simulate
Simulation never fills physical RAM or restarts a network/model process.
"""
import argparse
import asyncio
import json
import platform
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import httpx


async def exercise(client, model, burst):
    """Prime one exact answer, flood it, then verify cache-only miss rejection."""
    rows = []
    nonce = uuid4().hex[:8]
    payload = {'prompt': f'Reply briefly with READY. Experiment {nonce}.', 'model': model, 'max_tokens': 12, 'context_window': 512}

    async def request(label, body):
        started = time.perf_counter()
        try:
            response = await client.post('/v1/orchestrate', json=body)
            status = response.status_code
            try:
                data = response.json()
            except ValueError:
                data = {'error': 'Non-JSON response'}
            data.setdefault('routing_lane', response.headers.get('x-aegis-routing-lane'))
            data.setdefault('developer_stress_index', response.headers.get('x-developer-stress-index'))
        except httpx.HTTPError as exc:
            status, data = 0, {'error': type(exc).__name__}
        row = dict(label=label, http_status=status, round_trip_ms=round((time.perf_counter() - started) * 1000, 3), **data)
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ('label', 'http_status', 'execution_strategy', 'routing_lane', 'developer_stress_index', 'round_trip_ms')}), flush=True)
        return row

    before = (await client.get('/cluster/telemetry')).json()
    seed = await request('prime', payload)
    # A memory/upstream refusal is not a reason to keep hammering the node.
    warm = await request('repeat', payload) if seed['http_status'] == 200 else {}
    flood = []
    miss = {}
    if warm.get('http_status') == 200:
        flood = await asyncio.gather(*(request(f'cached_burst_{i}', payload) for i in range(burst)))
        miss = await request('uncached_after_flood', {**payload, 'prompt': f'Uncached unique request {nonce}'})
    after = (await client.get('/cluster/telemetry')).json()
    recovery = (await client.get('/cluster/recovery')).json()
    checks = {
        'prime_succeeded': seed['http_status'] == 200,
        'repeat_cached': warm.get('execution_strategy') == 'CACHE_HIT',
        'flood_served_from_cache': len(flood) == burst and all(r['http_status'] == 200 and r.get('execution_strategy') == 'CACHE_HIT' for r in flood),
        'cache_only_lane_seen': any(r.get('routing_lane') == 'CACHE_ONLY' for r in flood),
        'cache_only_miss_rejected': miss.get('http_status') == 429,
        'cache_byte_budget_held': after['cache_bytes_charged'] <= after['cache_byte_limit'],
        'no_active_request_leaks': after['pending_requests'] == 0 and after['active'] == 0,
    }
    result = dict(rows=rows, checks=checks, before=before, after=after, recovery=recovery)
    if before.get('runtime_profile', {}).get('name') == 'laptop':
        residency = {}
        for _ in range(20):
            response = await client.get('/aegis/residency')
            response.raise_for_status()
            residency = response.json()
            if not residency['models']:
                break
            await asyncio.sleep(.25)
        result['model_residency_after'] = residency
        checks['model_unloaded_after_run'] = not residency.get('models', [None])
    return result


async def simulated(args):
    # Importing the single-file core is sufficient; diagnostics are optional.
    from gateway import create_app
    control = {'ram': 20, 'delay': 0, 'counter': 0}
    def health():
        control['counter'] += 1
        n = control['counter']
        return dict(sample_monotonic_ns=time.perf_counter_ns(), ram_utilization_pct=control['ram'],
                    available_ram_bytes=6 * 1024**3, total_ram_bytes=8 * 1024**3,
                    system_context_switches=n * 20, gateway_page_faults=n * 2,
                    gateway_rss_bytes=32 * 1024**2, disk_read_count=n * 5,
                    disk_read_time=n, simulated_health=True)
    async def upstream(request):
        await asyncio.sleep(control['delay'])
        return httpx.Response(200, json={'response': 'Controlled test response', 'done': True})
    app = create_app(profile='standard', target='http://127.0.0.1:11434',
                     transport=httpx.MockTransport(upstream), telemetry_fn=health,
                     monitor_interval=.05, recovery_cooldown=.1, rate_window=2)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', timeout=10) as client:
            report = await exercise(client, args.model, args.burst)
            # Let the deliberate traffic flood expire before a fresh live lane.
            await asyncio.sleep(2.05)
            control['delay'] = 3.6
            response = await client.post('/v1/orchestrate', json={'prompt': 'Synthetic slow upstream', 'model': args.model})
            report['checks']['slow_request_survived_pool_rotation'] = response.status_code == 200
            control['delay'] = 0
            control['ram'] = 95  # A synthetic counter, not a RAM allocation.
            for _ in range(40):
                events = (await client.get('/cluster/recovery')).json()['events']
                reasons = {e.get('reason') for e in events if e['event'] == 'recovery'}
                if {'slow_upstream', 'ram_pressure'} <= reasons:
                    break
                await asyncio.sleep(.05)
            report['checks']['latency_threshold_recovery'] = 'slow_upstream' in reasons
            report['checks']['ram_threshold_recovery'] = 'ram_pressure' in reasons
            engine = app.state.engine
            report['checks']['context_reduced_to_floor'] = engine.context_window == 512
            report['checks']['cleared_cache_not_refilled_by_retired_generation'] = not engine.hot and not engine.warm
            report['final_recovery'] = (await client.get('/cluster/recovery')).json()
    report['mode'] = 'SIMULATED: HTTPX mocked Ollama + injected OS counters; actual 3.6-second async upstream delay'
    report['simulation_details'] = 'Monitor interval accelerated to 0.05s and cooldown to 0.1s. Thresholds remain 3500ms and 90%. No physical memory exhaustion or OS/network reset.'
    return report


async def main(args):
    memory_samples = deque(maxlen=2400)
    async def memory_sampler():
        import psutil
        start = time.perf_counter()
        while True:
            sample = psutil.virtual_memory()
            memory_samples.append(dict(elapsed_ms=round((time.perf_counter()-start)*1000,1),
                percent=sample.percent,available_mib=round(sample.available/1024**2,1)))
            await asyncio.sleep(.1)
    task = None
    if not args.simulate and urlsplit(args.base_url).hostname in ('localhost', '127.0.0.1'):
        task = asyncio.create_task(memory_sampler())
    try:
        if args.simulate:
            args.model = args.model or 'qwen2.5:0.5b'
            report = await simulated(args)
        else:
            async with httpx.AsyncClient(base_url=args.base_url, timeout=190, trust_env=False) as client:
                # Wait only for connection readiness after a deliberate server
                # launch. Do not retry inference or hide HTTP/validation errors.
                for attempt in range(20):
                    try:
                        response = await client.get('/cluster/telemetry', timeout=1)
                        response.raise_for_status()
                        break
                    except httpx.ConnectError:
                        if attempt == 19:
                            raise
                        await asyncio.sleep(.25)
                health = await client.get('/health')
                health.raise_for_status()
                data = health.json()
                args.model = args.model or data.get('configured_model') or next(iter(data['models']), 'qwen2.5:0.5b')
                report = await exercise(client, args.model, args.burst)
            report['mode'] = 'LIVE: real HTTP gateway and its configured Ollama; no fault injection'
            report['limits'] = 'No deliberate RAM exhaustion. Recovery is observed only if it naturally occurs; deterministic recovery coverage is in --simulate and unit tests. Host pressure, recovery cache invalidation or a preexisting traffic penalty may cause checks to fail; inspect raw evidence.'
    except (httpx.HTTPError, ValueError) as exc:
        report = dict(mode='SIMULATED' if args.simulate else 'LIVE', checks={'benchmark_completed': False},
                      error_type=type(exc).__name__, error='Inspect gateway readiness and server logs; no passing result is claimed.')
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    if memory_samples:
        report['local_memory'] = dict(source='Actual RAM samples from the local benchmark process OS; 100ms target interval, not a guaranteed peak',
            min_percent=min(s['percent'] for s in memory_samples),max_percent=max(s['percent'] for s in memory_samples),samples=list(memory_samples))
    report.update(timestamp_utc=datetime.now(timezone.utc).isoformat(), platform=platform.platform(), model=args.model)
    report['passed'] = all(report['checks'].values())
    path = Path(args.output or ('results/aegis-simulated.json' if args.simulate else 'results/aegis-live.json'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'output': str(path)}, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--model', help='Default: the running node configured model, or first installed model.')
    parser.add_argument('--burst', type=int, default=12)
    parser.add_argument('--output')
    args = parser.parse_args()
    if not 12 <= args.burst <= 32:
        parser.error('--burst must be between 12 and 32')
    asyncio.run(main(args))
