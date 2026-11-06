import asyncio
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import httpx
from gateway import create_app


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def client_for(self, handler, **settings):
        def healthy():
            return dict(sample_monotonic_ns=time.perf_counter_ns(), ram_utilization_pct=20, available_ram_bytes=4*1024**3, total_ram_bytes=8*1024**3)
        app = create_app(transport=httpx.MockTransport(handler), telemetry_fn=healthy, min_available_mb=0, **settings)
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        self.addAsyncCleanup(lifecycle.__aexit__, None, None, None)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_model_and_output_limit_isolate_cache(self):
        calls = []
        async def handler(request):
            calls.append(request.content)
            return httpx.Response(200, json={'response': f'answer {len(calls)}', 'done': True})
        c = await self.client_for(handler)
        for model, tokens in [('a', 8), ('a', 8), ('b', 8), ('b', 9)]:
            r = await c.post('/v1/orchestrate', json={'prompt': 'same', 'model': model, 'max_tokens': tokens})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(len(calls), 3)

    async def test_duplicate_concurrency_and_queue_rejection(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'response': 'shared answer', 'done': True})
        c = await self.client_for(handler, max_pending=2)
        first = asyncio.create_task(c.post('/v1/orchestrate', json={'prompt': 'same'}))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(c.post('/v1/orchestrate', json={'prompt': 'same'}))
        for _ in range(100):
            if (await c.get('/cluster/telemetry')).json()['pending_requests'] == 2:
                break
            await asyncio.sleep(0)
        self.assertEqual((await c.post('/v1/orchestrate', json={'prompt': 'third'})).status_code, 429)
        release.set()
        responses = await asyncio.gather(first, second)
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(calls, 1)
        self.assertEqual({r.json()['execution_strategy'] for r in responses}, {'CACHE_HIT', 'LIVE_INFERENCE_ROUTING'})
        self.assertEqual((await c.get('/cluster/telemetry')).json()['pending_requests'], 0)

    async def test_input_and_origin_validation(self):
        c = await self.client_for(lambda r: httpx.Response(500))
        for body in [{'prompt': ''}, {'prompt': '   '}, {'prompt': 'x'*4001}, {'prompt': 'x', 'max_tokens': 999}]:
            self.assertEqual((await c.post('/v1/orchestrate', json=body)).status_code, 422)
        self.assertEqual((await c.post('/v1/orchestrate', json={'prompt': 'x'}, headers={'Origin': 'https://untrusted.example'})).status_code, 403)
        self.assertEqual((await c.get('/health', headers={'Host': 'untrusted.example'})).status_code, 403)

    async def test_upstream_errors_do_not_poison_cache_or_leak_slots(self):
        for mode, status in [('missing',404), ('bad',502), ('invalid',502), ('offline',503), ('timeout',504)]:
            async def handler(request):
                if mode == 'offline':
                    raise httpx.ConnectError('offline', request=request)
                if mode == 'timeout':
                    raise httpx.ReadTimeout('timeout', request=request)
                if mode == 'missing':
                    return httpx.Response(404)
                if mode == 'invalid':
                    return httpx.Response(200, json={'response': 'partial', 'done': False})
                return httpx.Response(500)
            c = await self.client_for(handler)
            self.assertEqual((await c.post('/v1/orchestrate', json={'prompt':'x'})).status_code, status)
            data = (await c.get('/cluster/telemetry')).json()
            self.assertEqual((data['pending_requests'], data['active'], data['cache_entries']), (0,0,0))

    async def test_cache_capacity_and_expiry(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={'response': 'ok', 'done': True})
        c = await self.client_for(handler, cache_size=1, warm_size=1)
        for prompt in ['a','b','c','a']:
            await c.post('/v1/orchestrate', json={'prompt':prompt})
        self.assertEqual(calls, 4)
        c = await self.client_for(handler, cache_ttl=0)
        for _ in range(2):
            await c.post('/v1/orchestrate', json={'prompt':'x'})
        self.assertEqual(calls, 6)

    async def test_low_memory_blocks_generation(self):
        c = await self.client_for(lambda r: httpx.Response(500))
        app = c._transport.app
        app.state.engine.telemetry_fn = lambda: dict(sample_monotonic_ns=time.perf_counter_ns(), ram_utilization_pct=95, available_ram_bytes=0)
        self.assertEqual((await c.post('/v1/orchestrate', json={'prompt':'x'})).status_code, 503)

    async def test_health_checks_backend_and_home_exists(self):
        c = await self.client_for(lambda r: httpx.Response(200, json={'models':[{'name':'llama3:latest'}]}))
        self.assertEqual((await c.get('/health')).json()['models'], ['llama3:latest'])
        self.assertIn('Connection Clinic', (await c.get('/')).text)
        self.assertIn('Local AI Mesh', (await c.get('/ai-lab')).text)

    async def test_evidence_endpoint_requires_a_real_saved_report(self):
        c = await self.client_for(lambda r: httpx.Response(500))
        with patch('gateway.Path.exists', return_value=False):
            response = await c.get('/experiment')
        self.assertEqual(response.status_code, 404)
        self.assertIn('compare.py', response.json()['detail'])

    async def test_deadline_releases_capacity(self):
        async def handler(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={'response':'late','done':True})
        c = await self.client_for(handler, timeout=.02)
        self.assertEqual((await c.post('/v1/orchestrate', json={'prompt':'x'})).status_code, 504)
        data = (await c.get('/cluster/telemetry')).json()
        self.assertEqual((data['pending_requests'],data['active']), (0,0))

if __name__ == '__main__':
    unittest.main()
