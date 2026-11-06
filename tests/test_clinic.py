import asyncio
import json
import unittest
import socket
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import psutil
from fastapi import FastAPI
from network_clinic import adapter_check, create_router, interpret, run_checks, portal_check


def evidence(**changes):
    checks=[{'id':key,'status':'passed'} for key in ('adapter','dns','direct_ip','https','portal')]
    for check in checks:
        if check['id'] in changes:
            check.update(changes[check['id']])
    return checks


class DiagnosisTests(unittest.TestCase):
    def test_only_usable_up_interfaces_count_and_names_stay_private(self):
        for address,up,status in [('127.0.0.1',True,'failed'),('169.254.1.2',True,'failed'),
                                   ('192.168.1.77',False,'failed'),('192.168.1.77',True,'passed')]:
            with patch('network_clinic.psutil.net_if_stats',return_value={'private-name':SimpleNamespace(isup=up)}),patch('network_clinic.psutil.net_if_addrs',return_value={'private-name':[SimpleNamespace(family=socket.AF_INET,address=address)]}):
                result=adapter_check()
            self.assertEqual(result['status'],status)
            self.assertNotIn('private-name',str(result))
            self.assertNotIn(address,str(result))

    def test_healthy_scope_does_not_claim_all_internet(self):
        result=interpret(evidence())
        self.assertEqual(result['code'],'checks_passed')
        self.assertIn('does not prove',result['explanation'])

    def test_dns_evidence_requires_ip_pass_and_web_failure(self):
        checks=evidence(dns={'status':'failed'},https={'status':'failed'})
        self.assertEqual(interpret(checks)['code'],'possible_dns')
        self.assertEqual(interpret(evidence(dns={'status':'failed'}))['code'],'partial')

    def test_portal_is_suspected_not_certain(self):
        result=interpret(evidence(portal={'status':'warning','signal':'intercepted'}))
        self.assertEqual(result['code'],'possible_portal')
        self.assertIn('one explanation',result['explanation'])

    def test_blocked_ip_is_not_an_outage_if_web_works(self):
        self.assertEqual(interpret(evidence(direct_ip={'status':'failed'}))['code'],'partial')

    def test_disconnected_interface_evidence(self):
        self.assertEqual(interpret(evidence(**{k:{'status':'failed'} for k in ('adapter','dns','direct_ip','https','portal')}))['code'],'local_link')

    def test_ambiguous_failure_remains_inconclusive(self):
        self.assertEqual(interpret(evidence(dns={'status':'failed'},direct_ip={'status':'failed'},https={'status':'failed'},portal={'status':'failed'}))['code'],'inconclusive')


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_interface_information_is_not_a_disconnection(self):
        async def denied():
            raise psutil.AccessDenied()
        async def fail():
            raise OSError()
        report=await run_checks([('adapter','adapter',denied)]+[(k,k,fail) for k in ('dns','direct_ip','https','portal')])
        self.assertEqual(report['checks'][0]['status'],'unknown')
        self.assertEqual(report['finding']['code'],'inconclusive')

    async def test_portal_redirect_is_never_followed(self):
        urls=[]
        def handle(request):
            urls.append(str(request.url))
            return httpx.Response(302,headers={'Location':'http://private-login.invalid/'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),follow_redirects=False) as client:
            self.assertEqual((await portal_check(client))['signal'],'intercepted')
        self.assertEqual(len(urls),1)

    async def test_portal_expected_and_altered_content(self):
        for content,status in [(b'Microsoft Connect Test','passed'),(b'<html>login</html>','warning')]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=content))) as c:
                self.assertEqual((await portal_check(c))['status'],status)

    async def test_probe_errors_are_evidence_without_raw_secrets(self):
        async def fail():
            raise OSError('private adapter 192.168.1.77')
        probes=[(k,k,fail) for k in ('adapter','dns','direct_ip','https','portal')]
        report=await run_checks(probes)
        self.assertEqual(report['finding']['code'],'local_link')
        self.assertNotIn('192.168.1.77',json.dumps(report))
        self.assertEqual(len(report['checks']),5)

    async def test_probe_deadline(self):
        async def slow():
            await asyncio.sleep(1)
        probes=[(k,k,slow) for k in ('adapter','dns','direct_ip','https','portal')]
        with patch('network_clinic.PROBE_TIMEOUT',.01):
            report=await run_checks(probes)
        self.assertTrue(all(c['status']=='failed' for c in report['checks']))

    async def test_concurrent_diagnostics_are_rejected(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def runner():
            entered.set()
            await release.wait()
            return {'complete':True}
        app=FastAPI();app.include_router(create_router(runner))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
            first=asyncio.create_task(c.post('/clinic/run'))
            await asyncio.wait_for(entered.wait(),1)
            self.assertEqual((await c.post('/clinic/run')).status_code,429)
            release.set()
            self.assertEqual((await first).status_code,200)
            self.assertEqual((await c.post('/clinic/run')).status_code,200)


if __name__=='__main__':unittest.main()
