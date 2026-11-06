import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from gateway import create_app, InferenceRequest, resource_delta, Settings


def healthy(ram=20):
    return dict(sample_monotonic_ns=time.perf_counter_ns(), ram_utilization_pct=ram,
                available_ram_bytes=4*1024**3, total_ram_bytes=8*1024**3,
                gateway_rss_bytes=1000, system_context_switches=200, gateway_page_faults=None)


class AegisTests(unittest.IsolatedAsyncioTestCase):
    async def setup_client(self, handler=None, **options):
        if handler is None:
            handler=lambda r:httpx.Response(200,json={'response':'test answer','done':True})
        app=create_app(transport=httpx.MockTransport(handler),telemetry_fn=options.pop('telemetry_fn',healthy),**options)
        life=app.router.lifespan_context(app)
        await life.__aenter__();self.addAsyncCleanup(life.__aexit__,None,None,None)
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver')
        self.addAsyncCleanup(client.aclose)
        return client,app.state.engine

    async def test_two_tiers_keep_exact_model_and_context_semantics(self):
        c,e=await self.setup_client(cache_size=1,warm_size=2)
        for prompt in ('a','b'):
            self.assertEqual((await c.post('/v1/orchestrate',json={'prompt':prompt})).status_code,200)
        r=await c.post('/v1/orchestrate',json={'prompt':'a'})
        self.assertEqual(r.json()['cache_tier'],'warm')
        r=await c.post('/v1/orchestrate',json={'prompt':'a','context_window':512})
        self.assertEqual(r.json()['execution_strategy'],'LIVE_INFERENCE_ROUTING')

    async def test_panic_proxy_routes_cached_answers_and_rejects_uncached_work(self):
        c,e=await self.setup_client(duplicate_limit=2,rate_limit=4)
        for _ in range(5):
            r=await c.post('/v1/orchestrate',json={'prompt':'repeat'})
            self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['routing_lane'],'CACHE_ONLY')
        self.assertEqual(e.metrics['upstream_calls'],1)
        self.assertEqual((await c.post('/v1/orchestrate',json={'prompt':'new'})).status_code,429)

    async def test_identity_is_bounded_and_rejects_forwarded_header_spoofing(self):
        c,e=await self.setup_client()
        for i in range(1000):e.behavior(str(i),'same')
        self.assertEqual(len(e.clients),128)
        r=await c.post('/v1/orchestrate',json={'prompt':'x'},headers={'X-Forwarded-For':'1.2.3.4'})
        self.assertEqual(r.status_code,400)

    async def test_rate_penalty_expires_after_quiet_window(self):
        c,e=await self.setup_client(duplicate_limit=2,rate_window=.02)
        for _ in range(3):e.behavior('peer','a')
        self.assertEqual(e.behavior('peer','different')['routing_lane'],'CACHE_ONLY')
        await asyncio.sleep(.03)
        self.assertEqual(e.behavior('peer','different')['routing_lane'],'NORMAL')

    async def test_request_and_response_byte_limits(self):
        c,e=await self.setup_client(lambda r:httpx.Response(200,json={'response':'x'*300000,'done':True}))
        self.assertEqual((await c.post('/v1/orchestrate',content=b'x'*65537)).status_code,413)
        self.assertEqual((await c.post('/v1/orchestrate',json={'prompt':'x'})).status_code,502)
        self.assertEqual(e.pending,0)

    async def test_byte_budget_and_expiry(self):
        c,e=await self.setup_client(cache_bytes=700,cache_ttl=.01)
        await c.post('/v1/orchestrate',json={'prompt':'a'})
        await c.post('/v1/orchestrate',json={'prompt':'b'})
        self.assertLessEqual(e.cache_bytes,700)
        await asyncio.sleep(.02)
        self.assertIsNone(e.cached(e.cache_key(InferenceRequest(prompt='b'),2048)))

    async def test_background_monitor_ram_recovery_hysteresis_and_cooldown(self):
        signal={'ram':20}
        c,e=await self.setup_client(telemetry_fn=lambda:healthy(signal['ram']),monitor_interval=.01,recovery_cooldown=0)
        await c.post('/v1/orchestrate',json={'prompt':'prime'})
        signal['ram']=95
        for _ in range(50):
            if e.metrics['recoveries']:break
            await asyncio.sleep(.01)
        self.assertEqual(e.metrics['recoveries'],1)
        self.assertEqual(e.context_window,1024)
        self.assertFalse(e.hot or e.warm)
        await asyncio.sleep(.04)
        self.assertEqual(e.metrics['recoveries'],1)  # No repeat thrash at 95%.
        self.assertEqual((await c.post('/v1/orchestrate',json={'prompt':'new'})).status_code,503)
        signal['ram']=70;await asyncio.sleep(.03)
        signal['ram']=95;await asyncio.sleep(.04)
        self.assertEqual(e.context_window,512)

    async def test_recovery_drains_pool_without_closing_active_request_or_refilling_cache(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def upstream(r):
            entered.set();await release.wait()
            return httpx.Response(200,json={'response':'old generation','done':True})
        c,e=await self.setup_client(upstream)
        task=asyncio.create_task(c.post('/v1/orchestrate',json={'prompt':'slow'}))
        await asyncio.wait_for(entered.wait(),1)
        old=e.pool
        self.assertTrue(await e.evaluate_health(healthy(95)))
        self.assertTrue(old.retired)
        self.assertFalse(old.client.is_closed)
        release.set();self.assertEqual((await task).status_code,200)
        self.assertTrue(old.client.is_closed)
        self.assertFalse(e.hot or e.warm)

    async def test_slow_inflight_request_recovers_once_not_again_when_it_finishes(self):
        c,e=await self.setup_client(recovery_cooldown=0)
        e.inflight_started_ns=time.perf_counter_ns()-4_000_000_000
        self.assertTrue(await e.evaluate_health(healthy()))
        e.inflight_started_ns=None;e.latency_sequence=1
        e.last_latency_ms=4000;e.last_latency_at=time.monotonic()
        self.assertFalse(await e.evaluate_health(healthy()))

    async def test_reduced_context_is_sent_to_next_ollama_call_and_old_cache_is_not_reused(self):
        contexts=[]
        def upstream(request):
            contexts.append(json.loads(request.content)['options']['num_ctx'])
            return httpx.Response(200,json={'response':'context test','done':True})
        c,e=await self.setup_client(upstream)
        await c.post('/v1/orchestrate',json={'prompt':'same'})
        await e.evaluate_health(healthy(95))
        result=await c.post('/v1/orchestrate',json={'prompt':'same'})
        self.assertEqual(contexts,[2048,1024])
        self.assertEqual(result.json()['execution_strategy'],'LIVE_INFERENCE_ROUTING')

    async def test_recovery_cooldown_suppresses_independent_triggers(self):
        c,e=await self.setup_client(recovery_cooldown=10)
        self.assertTrue(await e.evaluate_health(healthy(95)))
        await e.evaluate_health(healthy(70))
        self.assertFalse(await e.evaluate_health(healthy(95)))
        e.last_recovery_at-=11  # Advance only this controlled test clock state.
        self.assertTrue(await e.evaluate_health(healthy(95)))
        self.assertEqual(e.context_window,512)

    async def test_telemetry_and_recovery_are_read_only_observability(self):
        c,e=await self.setup_client()
        await c.post('/v1/orchestrate',json={'prompt':'x'})
        d=(await c.get('/cluster/telemetry')).json()
        self.assertIsNone(d['gateway_page_faults'])
        self.assertIn('measurement_interval_us',d['recent_inference_deltas'][0])
        self.assertEqual((await c.post('/cluster/recovery')).status_code,405)

    async def test_cancellation_releases_queue_and_activity(self):
        entered=asyncio.Event()
        async def upstream(r):entered.set();await asyncio.sleep(10)
        c,e=await self.setup_client(upstream)
        task=asyncio.create_task(c.post('/v1/orchestrate',json={'prompt':'x'}))
        await asyncio.wait_for(entered.wait(),1);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual((e.pending,e.metrics['active']),(0,0))

    async def test_logs_do_not_contain_prompts_or_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.jsonl'
            c,e=await self.setup_client(log_path=path)
            await c.post('/v1/orchestrate',json={'prompt':'PRIVATE_PROMPT'})
            text=path.read_text()
            self.assertIn('inference_resource_delta',text)
            self.assertNotIn('PRIVATE_PROMPT',text)
            self.assertNotIn('test answer',text)
            # Close before the Windows temporary directory removes the log.
            e.handler.close();e.logger.removeHandler(e.handler);e.handler=None


class CounterTests(unittest.TestCase):
    def test_supported_and_unsupported_delta_scope(self):
        a=dict(sample_monotonic_ns=1_000_000,disk_read_count=2,disk_read_time=10,gateway_page_faults=None)
        b=dict(sample_monotonic_ns=2_000_000,disk_read_count=4,disk_read_time=14,gateway_page_faults=None)
        d=resource_delta(a,b)
        self.assertEqual(d['measurement_interval_us'],1000)
        self.assertEqual(d['disk_read_average_ms'],2)
        self.assertIsNone(d['gateway_page_faults_delta'])

    def test_invalid_recovery_and_pool_configuration_fails_fast(self):
        for kw in ({'monitor_interval':0},{'rate_limit':65},{'cache_ttl':float('nan')},{'target':'http://remote.example/'},{'min_context':256}):
            with self.assertRaises(ValueError):Settings(**kw)


if __name__=='__main__':unittest.main()
