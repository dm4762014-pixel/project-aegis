import asyncio
import copy
import ssl
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from pydantic import ValidationError
from service_checks import (CheckRequest,ServiceReport,check_service,compare_reports,
                            create_service_router,normalize_target,resolve_public)


def request(target='https://example.com/'):
    return CheckRequest(target=target,device_id=uuid4(),device_label='Laptop A')


def report(failure=None,mode='live',target='https://example.com/'):
    stages=[];blocked=False
    for key in ('dns','tcp','tls','http'):
        status='skipped' if blocked else ('failed' if key==failure else 'passed')
        stages.append(dict(id=key,status=status,detail='fixture',elapsed_ms=1,http_status=200 if key=='http' and status=='passed' else None))
        if status=='failed':blocked=True
    return ServiceReport(**request(target).model_dump(),schema_version=2,kind='service',mode=mode,
                         report_id=uuid4(),timestamp_utc=datetime.now(timezone.utc),stages=stages,summary='Fixture, not a real computer',scope='Simulated test fixture')


class ValidationTests(unittest.TestCase):
    def test_url_normalization(self):
        self.assertEqual(normalize_target('https://EXAMPLE.com:443/course'),'https://example.com/course')

    def test_unsafe_targets_are_rejected(self):
        for target in ['http://example.com','https://localhost/','https://127.0.0.1/','https://[::1]/',
                       'https://example.com:8000/','https://user:password@example.com/',
                       'https://example.com/?token=secret','https://example.com/#token','https://a.local/',
                       'https://example.com/\r\nHost:x','https://example.com\\@localhost/']:
            with self.subTest(target=target),self.assertRaises(ValueError):normalize_target(target)

    def test_duplicate_and_wrong_service_comparisons_rejected(self):
        a=report()
        for reports in [[a,a],[a,report(target='https://github.com/')]]:
            with self.assertRaises(ValueError):compare_reports(reports)
        b=report();b.device_id=a.device_id
        with self.assertRaises(ValueError):compare_reports([a,b])

    def test_shared_failure_and_mixed_results_are_observations(self):
        result=compare_reports([report('dns'),report('dns'),report()])
        self.assertEqual(result['counts']['dns']['failed'],2)
        self.assertTrue(any('matching symptom' in p for p in result['patterns']))
        self.assertTrue(any('passed on some' in p for p in result['patterns']))

    def test_simulation_and_stale_times_remain_visible(self):
        a,b=report(mode='simulated'),report()
        a.timestamp_utc=datetime(2020,1,1,tzinfo=timezone.utc)
        result=compare_reports([a,b])
        self.assertTrue(result['includes_simulation'])
        self.assertGreater(result['time_spread_minutes'],60)

    def test_malformed_or_inconsistent_report_rejected(self):
        a=report('dns').model_dump(mode='json')
        a['stages'][1]['status']='passed'
        with self.assertRaises(ValidationError):ServiceReport.model_validate(a)

    def test_matching_http_codes_are_compared_without_claiming_outage(self):
        reports=[report(),report()]
        for r in reports:
            r.stages[-1].status='warning'
            r.stages[-1].http_status=503
        result=compare_reports(reports)
        self.assertEqual(result['http_status_counts'],{'503':2})
        self.assertTrue(any('HTTP 503' in p for p in result['patterns']))
        a=report().model_dump(mode='json');a['stages'][0]['elapsed_ms']=float('nan')
        with self.assertRaises(ValidationError):ServiceReport.model_validate(a)


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_and_mixed_dns_never_connect(self):
        loop=asyncio.get_running_loop()
        for ips in [['127.0.0.1'],['192.168.1.2'],['169.254.169.254'],['93.184.216.34','10.0.0.1']]:
            answers=[(2,1,6,'',(ip,443)) for ip in ips]
            with patch.object(loop,'getaddrinfo',new=AsyncMock(return_value=answers)),patch('service_checks.connect_public',new=AsyncMock()) as connect:
                result=await check_service(request())
                self.assertEqual([s['status'] for s in result['stages']],['failed','skipped','skipped','skipped'])
                connect.assert_not_called()

    async def test_success_pins_connection_and_sends_only_head(self):
        writer=MagicMock();writer.start_tls=AsyncMock();writer.drain=AsyncMock();writer.wait_closed=AsyncMock()
        reader=MagicMock();reader.readuntil=AsyncMock(return_value=b'HTTP/1.1 200 OK\r\n')
        with patch('service_checks.resolve_public',new=AsyncMock(return_value=['93.184.216.34'])),patch('service_checks.connect_public',new=AsyncMock(return_value=(reader,writer))) as connect:
            result=await check_service(request('https://example.com/course'))
        self.assertTrue(all(s['status']=='passed' for s in result['stages']))
        connect.assert_awaited_once_with(['93.184.216.34'])
        self.assertEqual(writer.start_tls.call_args.kwargs['server_hostname'],'example.com')
        self.assertTrue(writer.write.call_args.args[0].startswith(b'HEAD /course HTTP/1.1'))
        self.assertNotIn('93.184.216.34',str(result))
        writer.close.assert_called()

    async def test_certificate_failure_skips_http(self):
        writer=MagicMock();writer.start_tls=AsyncMock(side_effect=ssl.SSLCertVerificationError('private detail'));writer.wait_closed=AsyncMock()
        with patch('service_checks.resolve_public',new=AsyncMock(return_value=['93.184.216.34'])),patch('service_checks.connect_public',new=AsyncMock(return_value=(MagicMock(),writer))):
            result=await check_service(request())
        self.assertEqual([s['status'] for s in result['stages']],['passed','passed','failed','skipped'])
        self.assertNotIn('private detail',str(result))
        writer.write.assert_not_called()

    async def test_http_redirect_and_access_denied_are_not_followed(self):
        for status in (302,403,405,503):
            writer=MagicMock();writer.start_tls=AsyncMock();writer.drain=AsyncMock();writer.wait_closed=AsyncMock()
            reader=MagicMock();reader.readuntil=AsyncMock(return_value=f'HTTP/1.1 {status} Result\r\n'.encode())
            with patch('service_checks.resolve_public',new=AsyncMock(return_value=['93.184.216.34'])),patch('service_checks.connect_public',new=AsyncMock(return_value=(reader,writer))) as connect:
                result=await check_service(request())
            self.assertEqual(result['stages'][-1]['status'],'warning')
            self.assertEqual(connect.await_count,1)

    async def test_deadline_does_not_leave_false_success(self):
        async def hang(host):await asyncio.sleep(1)
        with patch('service_checks.resolve_public',new=hang),patch('service_checks.TIMEOUT',.01):
            result=await check_service(request())
        self.assertEqual(result['stages'][0]['status'],'failed')

    async def test_compare_api_limits_and_live_check_lock(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def checker(body):entered.set();await release.wait();return {'done':True}
        app=FastAPI();app.include_router(create_service_router(checker))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            self.assertEqual((await c.post('/services/compare',json={'reports':[]})).status_code,422)
            body=request().model_dump(mode='json')
            first=asyncio.create_task(c.post('/services/check',json=body));await asyncio.wait_for(entered.wait(),1)
            self.assertEqual((await c.post('/services/check',json=body)).status_code,429)
            release.set();self.assertEqual((await first).status_code,200)

    async def test_compare_api_and_examples_preserve_simulated_provenance(self):
        app=FastAPI();app.include_router(create_service_router())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            examples=(await c.get('/services/examples')).json()
            self.assertTrue(all(r['mode']=='simulated' for r in examples))
            response=await c.post('/services/compare',json={'reports':examples})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['includes_simulation'])
            self.assertEqual(response.json()['counts']['dns']['failed'],2)
            malformed=copy.deepcopy(examples);malformed[0]['stages'][0]['id']='tcp'
            self.assertEqual((await c.post('/services/compare',json={'reports':malformed})).status_code,422)


if __name__=='__main__':unittest.main()
