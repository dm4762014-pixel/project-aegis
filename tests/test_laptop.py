import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from gateway import create_app, LAPTOP_MODEL
import laptop_setup


def health(ram=20):
    return dict(sample_monotonic_ns=time.perf_counter_ns(), ram_utilization_pct=ram,
                available_ram_bytes=4*1024**3, total_ram_bytes=8*1024**3)


class LaptopTests(unittest.IsolatedAsyncioTestCase):
    async def client_for(self, handler, **settings):
        app=create_app(profile='laptop',telemetry_fn=settings.pop('telemetry_fn',health),
                       transport=httpx.MockTransport(handler),**{'memory_wait_seconds':0,**settings})
        life=app.router.lifespan_context(app);await life.__aenter__()
        self.addAsyncCleanup(life.__aexit__,None,None,None)
        c=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver')
        self.addAsyncCleanup(c.aclose)
        return c,app.state.engine

    async def test_small_profile_sends_bounded_cpu_options_and_immediate_unload(self):
        bodies=[]
        def upstream(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200,json={'response':'actual test text','done':True,'eval_count':4,'load_duration':1000000,'done_reason':'stop'})
        c,e=await self.client_for(upstream)
        r=await c.post('/v1/orchestrate',json={'prompt':'hello','context_window':8192})
        self.assertEqual(r.status_code,200)
        self.assertEqual(bodies[0]['model'],LAPTOP_MODEL)
        self.assertEqual(bodies[0]['options'],dict(num_predict=80,temperature=0,num_ctx=512,num_thread=2,num_gpu=0,num_batch=32))
        self.assertEqual(bodies[0]['keep_alive'],0)
        self.assertEqual(r.json()['generation']['output_tokens'],4)
        self.assertEqual(e.settings.ram_threshold,90)
        self.assertEqual(e.settings.target,'http://127.0.0.1:11435')

    async def test_large_model_is_refused_before_upstream_call(self):
        calls=[]
        def upstream(request):calls.append(request);return httpx.Response(500)
        c,e=await self.client_for(upstream)
        self.assertEqual((await c.post('/v1/orchestrate',json={'prompt':'x','model':'llama3:latest'})).status_code,422)
        self.assertEqual(calls,[])

    async def test_memory_guard_is_not_weakened(self):
        calls=[]
        def upstream(request):calls.append(request);return httpx.Response(500)
        c,e=await self.client_for(upstream,telemetry_fn=lambda:health(91))
        self.assertEqual((await c.post('/v1/orchestrate',json={'prompt':'x'})).status_code,503)
        self.assertEqual(calls,[])

    async def test_fluctuating_ram_waits_for_a_safe_slot_before_model_loading(self):
        samples=[];calls=[]
        def telemetry():
            samples.append(1)
            return health(94 if len(samples)<3 else 88)
        def upstream(request):calls.append(request);return httpx.Response(200,json={'response':'safe slot','done':True})
        c,e=await self.client_for(upstream,telemetry_fn=telemetry,memory_wait_seconds=.5)
        r=await c.post('/v1/orchestrate',json={'prompt':'x'})
        self.assertEqual(r.status_code,200)
        self.assertGreater(e.memory_wait_ms,100)
        self.assertEqual(len(calls),1)
        self.assertFalse(e.waiting_for_memory)

    async def test_exact_repeats_do_not_reload_model(self):
        calls=[]
        def upstream(request):calls.append(request);return httpx.Response(200,json={'response':'small answer','done':True})
        c,e=await self.client_for(upstream)
        for _ in range(2):r=await c.post('/v1/orchestrate',json={'prompt':'same'})
        self.assertEqual(r.json()['execution_strategy'],'CACHE_HIT')
        self.assertEqual(len(calls),1)

    async def test_health_and_residency_use_real_configured_upstream_shapes(self):
        def upstream(request):
            if request.url.path=='/api/ps':return httpx.Response(200,json={'models':[]})
            return httpx.Response(200,json={'models':[{'name':LAPTOP_MODEL,'size':105466867},{'name':'llama3:latest','size':4661224676}]})
        c,e=await self.client_for(upstream)
        self.assertEqual((await c.get('/health')).json()['models'],[LAPTOP_MODEL])
        self.assertEqual((await c.get('/aegis/residency')).json()['models'],[])
        self.assertEqual((await c.get('/cluster/telemetry')).json()['runtime_profile']['keep_alive_seconds'],0)
        self.assertEqual((await c.get('/aegis')).status_code,200)

    async def test_residency_failure_does_not_claim_unloaded(self):
        c,e=await self.client_for(lambda request:httpx.Response(503))
        self.assertEqual((await c.get('/aegis/residency')).status_code,503)


class LauncherTests(unittest.TestCase):
    def test_unknown_occupied_port_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temp,patch('laptop_setup.ROOT',Path(temp)),\
             patch('laptop_setup.shutil.which',return_value='ollama.exe'),\
             patch('laptop_setup.port_in_use',return_value=True),\
             patch('laptop_setup.owned_instance',return_value=False),\
             patch('laptop_setup.subprocess.Popen') as start:
            with self.assertRaisesRegex(RuntimeError,'unknown service'):
                laptop_setup.prepare()
            start.assert_not_called()


if __name__=='__main__':unittest.main()
