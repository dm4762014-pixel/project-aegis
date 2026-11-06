import asyncio
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from developer_diagnostics import analyze_har, clean_url, diagnose
from developer_routes import create_developer_router
from dev_lab import api,create_fixture_router

FRONT='https://frontend.example/'
API='https://api.example/data'


def observation(status=200,**kwargs):
    request=dict(url=API,method='GET',status=status,resource_type='fetch',content_type='application/json',failed=False)
    result=dict(frontend_url=FRONT,api_url=API,requests=[request],app_state='ready',page_error_count=0,console_cors=False)
    result.update(kwargs)
    return result


def codes(result):return [f['code'] for f in result['findings']]


def har_payload(status=200,url=API):
    return dict(frontend_url=FRONT,api_url=API,har={'log':{'entries':[{
        'request':{'url':url,'method':'GET','headers':[{'name':'Authorization','value':'SECRET_AUTH'}]},
        'response':{'status':status,'headers':[{'name':'Content-Type','value':'application/json'},
                    {'name':'Set-Cookie','value':'SECRET_COOKIE'}],'content':{'text':'SECRET_BODY'}},
        '_resourceType':'fetch'}]}})


class AnalysisTests(unittest.TestCase):
    def test_healthy_control_and_no_capture_are_distinct(self):
        self.assertEqual(codes(diagnose(observation())),['NO_ISSUE_OBSERVED'])
        self.assertEqual(codes(diagnose(observation(requests=[]))),['INCONCLUSIVE'])

    def test_500_is_observed_not_an_invented_database_failure(self):
        report=diagnose(observation(500))
        self.assertEqual(codes(report),['API_SERVER_ERROR'])
        self.assertIn('does not identify',report['findings'][0]['next_check'])

    def test_cors_requires_explicit_browser_console_evidence(self):
        obs=observation(0,console_cors=True,direct_probe={'status':200})
        obs['requests'][0]['failed']=True
        self.assertEqual(codes(diagnose(obs)),['CORS_BLOCKED'])
        obs['console_cors']=False
        self.assertEqual(codes(diagnose(obs)),['REQUEST_FAILED'])

    def test_wrong_route_requires_intended_route_control(self):
        obs=observation(404,direct_probe={'status':200})
        obs['requests'][0]['url']='https://api.example/wrong'
        self.assertEqual(codes(diagnose(obs)),['API_ROUTE_MISMATCH'])
        obs['direct_probe']=None
        self.assertEqual(codes(diagnose(obs)),['API_ROUTE_404'])

    def test_json_expectation_and_runtime_error(self):
        obs=observation();obs['requests'][0]['content_type']='text/html'
        self.assertEqual(codes(diagnose(obs)),['API_NOT_JSON'])
        self.assertIn('FRONTEND_RUNTIME_ERROR',codes(diagnose(observation(page_error_count=1))))

    def test_refusal_redirect_and_document_error(self):
        self.assertEqual(codes(diagnose(observation(403))),['API_ACCESS_REFUSED'])
        self.assertEqual(codes(diagnose(observation(302))),['API_REDIRECT'])
        obs=observation(requests=[dict(url=FRONT,method='GET',status=502,resource_type='document')])
        self.assertEqual(codes(diagnose(obs)),['FRONTEND_HTTP_ERROR'])

    def test_har_export_omits_secrets_and_unknown_console_count(self):
        payload=har_payload(url='https://username:SECRET_PASSWORD@api.example/data?token=SECRET_QUERY#SECRET_FRAGMENT')
        report=analyze_har(payload)
        self.assertNotIn('SECRET_',json.dumps(report))
        self.assertIsNone(report['page_error_count'])
        self.assertEqual(report['requests'][0]['url'],API)

    def test_har_network_error_does_not_confirm_cors(self):
        payload=har_payload(0);payload['har']['log']['entries'][0]['_error']='net::ERR_FAILED'
        self.assertEqual(codes(analyze_har(payload)),['REQUEST_FAILED'])
        payload['har']['log']['entries'][0]['_error']='net::ERR_CERT_DATE_INVALID'
        self.assertEqual(codes(analyze_har(payload)),['TLS_BROWSER_ERROR'])

    def test_unrelated_origin_does_not_create_api_failure(self):
        self.assertEqual(codes(analyze_har(har_payload(500,'https://analytics.example/event'))),['INCONCLUSIVE'])

    def test_entry_count_and_status_are_validated(self):
        payload=har_payload();payload['har']['log']['entries']*=201
        with self.assertRaises(ValueError):analyze_har(payload)
        with self.assertRaises(ValueError):analyze_har(har_payload('200'))

    def test_no_case_label_or_path_word_shortcuts(self):
        obs=observation(scenario='server')
        obs['api_url']='https://api.example/cors/server/error'
        obs['requests'][0]['url']=obs['api_url']
        self.assertEqual(codes(diagnose(obs)),['NO_ISSUE_OBSERVED'])


class DeveloperApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_is_offline_and_does_not_save_raw_har(self):
        app=FastAPI();app.include_router(create_developer_router())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            result=await c.post('/dev/analyze-har',json=har_payload(500))
            self.assertEqual(result.status_code,200)
            self.assertEqual(codes(result.json()),['API_SERVER_ERROR'])
            self.assertNotIn('SECRET_',result.text)
            self.assertEqual((await c.post('/dev/analyze-har',content=b'bad json')).status_code,422)
            self.assertEqual((await c.post('/dev/analyze-har',content=b'x'*(1024*1024+1))).status_code,413)

    async def test_runner_lock_and_snapshot_path_validation(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def runner():entered.set();await release.wait();return {'passed':True}
        app=FastAPI();app.include_router(create_developer_router(runner))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            first=asyncio.create_task(c.post('/dev/run-lab'));await asyncio.wait_for(entered.wait(),1)
            self.assertEqual((await c.post('/dev/run-lab')).status_code,429)
            release.set();self.assertEqual((await first).status_code,200)
            self.assertEqual((await c.get(f'/dev/snapshots/{uuid4()}/unknown/broken')).status_code,404)
            self.assertEqual((await c.get('/dev/snapshots/not-a-uuid/cors/broken')).status_code,422)

    async def test_fixture_faults_and_corrections_are_real_responses(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://testserver') as c:
            before=await c.get('/api/cors/broken');after=await c.get('/api/cors/fixed')
            self.assertNotIn('access-control-allow-origin',before.headers)
            self.assertEqual(after.headers['access-control-allow-origin'],'http://127.0.0.1:8000')
            self.assertEqual((await c.get('/api/server/broken')).status_code,500)
            self.assertEqual((await c.get('/api/server/fixed')).status_code,200)
            self.assertIn('text/html',(await c.get('/api/html/broken')).headers['content-type'])


if __name__=='__main__':unittest.main()
