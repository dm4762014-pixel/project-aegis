"""Read-only, fixed-target connectivity checks. No LLM or system changes."""
import asyncio
import ipaddress
import socket
import time
from datetime import datetime, timezone

import httpx
import psutil
from fastapi import APIRouter, HTTPException

PROBE_TIMEOUT = 6


def adapter_check():
    stats = psutil.net_if_stats()
    usable = 0
    for name, addresses in psutil.net_if_addrs().items():
        if not stats.get(name) or not stats[name].isup:
            continue
        for address in addresses:
            if address.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            ip = ipaddress.ip_address(address.address.split('%')[0])
            if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
                usable += 1
                break
    return {'status':'passed' if usable else 'failed',
            'detail':f'{usable} active interface(s) have a usable IP address. Virtual/VPN adapters may count.'}


async def dns_check():
    # Use the OS resolver, not a replacement public DNS provider.
    addresses = await asyncio.get_running_loop().getaddrinfo('example.com',443,type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError('No resolver answers')
    return {'status':'passed','detail':'The system resolver returned an address for example.com.'}


async def ip_check():
    reader, writer = await asyncio.open_connection('1.1.1.1',443)
    writer.close()
    await writer.wait_closed()
    return {'status':'passed','detail':'A direct TCP connection to 1.1.1.1:443 succeeded without a DNS lookup. This tests one destination only.'}


async def web_check(client):
    async with client.stream('GET','https://example.com/') as response:
        if response.status_code != 200:
            return {'status':'warning','detail':f'HTTPS example.com responded with HTTP {response.status_code}; expected 200.'}
        return {'status':'passed','detail':'A certificate-verified HTTPS request to example.com returned HTTP 200.'}


async def portal_check(client):
    # Microsoft-documented connectivity probe. Never follow a login redirect.
    async with client.stream('GET','http://www.msftconnecttest.com/connecttest.txt') as response:
        if response.status_code in (301,302,303,307,308):
            return {'status':'warning','signal':'intercepted','detail':'The plain HTTP probe was redirected. A sign-in portal or network policy may be involved; this is not proof.'}
        if response.status_code != 200:
            return {'status':'warning','detail':f'The plain HTTP probe returned HTTP {response.status_code}. It may be blocked by network policy.'}
        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=256):
            content.extend(chunk[:256-len(content)])
            if len(content)>=256:
                break
        if bytes(content).strip() != b'Microsoft Connect Test':
            return {'status':'warning','signal':'intercepted','detail':'The plain HTTP probe returned unexpected content. A sign-in page, filtering, or endpoint change is possible.'}
        return {'status':'passed','detail':'The Microsoft connectivity probe returned the expected text without a redirect.'}


def interpret(checks):
    values = {c['id']:c for c in checks}
    passed = lambda key: values[key]['status']=='passed'
    if all(passed(key) for key in values):
        return {'code':'checks_passed','title':'These connection checks passed',
                'explanation':'Your laptop reached the tested destinations. This does not prove every app or website will work.',
                'next_steps':['If one app still fails, check its service status and exact error message.',
                              'Run this again when the issue occurs and compare the evidence.']}
    if values['adapter']['status']=='failed' and not any(passed(k) for k in ('dns','direct_ip','https')):
        return {'code':'local_link','title':'Check your local connection first',
                'explanation':'No active interface with a usable IP was found, and the external checks did not pass.',
                'next_steps':['Check Wi-Fi, the network cable, and airplane mode.',
                              'Reconnect to your usual network, then run the checks again.']}
    if not passed('dns') and passed('direct_ip') and not passed('https'):
        return {'code':'possible_dns','title':'Name resolution may be the problem',
                'explanation':'One IP destination is reachable, but resolving example.com and loading it over HTTPS did not pass. This is evidence of a possible DNS issue, not a complete diagnosis.',
                'next_steps':['Reconnect to your usual network and retry.',
                              'If it persists, share this report with your network administrator. Ask them to check the configured resolver.']}
    if values['portal'].get('signal')=='intercepted':
        return {'code':'possible_portal','title':'A sign-in page or network filter may be involved',
                'explanation':'The plain HTTP connectivity probe was redirected or changed. A portal is one explanation; filtering and endpoint changes can look similar.',
                'next_steps':['Open your browser and check for the network’s sign-in notification.',
                              'Only sign in if you recognize the network. Rerun after completing its normal login.']}
    if passed('https'):
        return {'code':'partial','title':'HTTPS works; some other checks differ',
                'explanation':'The tested HTTPS site is reachable. An individual probe may be blocked; this does not establish an internet outage.',
                'next_steps':['Inspect the individual check results below.',
                              'If your app still fails, share its error and this report with support.']}
    return {'code':'inconclusive','title':'The cause is not established yet',
            'explanation':'Some checks failed. A network outage, firewall, proxy requirement, VPN, or target-specific block could produce these results.',
            'next_steps':['Check whether another device works on the same network.',
                          'If practical, compare this laptop on a trusted alternative network.',
                          'Share this report with support; do not disable security tools based on this result.']}


async def run_checks(probes=None):
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT,trust_env=False,follow_redirects=False,
                                 headers={'User-Agent':'ConnectionClinic/1.0'}) as client:
        probes = probes or [('adapter','Local connection',lambda: asyncio.to_thread(adapter_check)),
                            ('dns','System DNS',dns_check),('direct_ip','Direct IP connection',ip_check),
                            ('https','Secure website',lambda:web_check(client)),
                            ('portal','Network sign-in check',lambda:portal_check(client))]
        async def one(key,label,probe):
            start = time.perf_counter()
            try:
                async with asyncio.timeout(PROBE_TIMEOUT):
                    result = await probe()
            except (TimeoutError,httpx.TimeoutException):
                result={'status':'failed','detail':'No successful response within 6 seconds. A timeout alone does not identify the cause.'}
            except psutil.Error:
                result={'status':'unknown','detail':'The system did not provide interface information. No local-connection conclusion can be drawn from this check.'}
            except (OSError,httpx.HTTPError):
                result={'status':'failed','detail':'This check could not complete its connection. Network policy or endpoint availability may be involved.'}
            return {'id':key,'label':label,'elapsed_ms':round((time.perf_counter()-start)*1000,1),**result}
        checks = await asyncio.gather(*(one(*p) for p in probes))
    return {'schema_version':1,'mode':'live','timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'elapsed_ms':round((time.perf_counter()-started)*1000,1),'checks':checks,'finding':interpret(checks),
            'scope':'This laptop; fixed public destinations; direct connections. Browser proxies and VPN routes may behave differently.',
            'privacy':'No adapter names, local IP addresses, SSIDs, response bodies, or portal URLs are included in this report.',
            'changes_made':'None. No settings were modified and no AI service was used.'}


def create_router(runner=run_checks):
    router = APIRouter(prefix='/clinic')
    lock = asyncio.Lock()
    @router.post('/run')
    async def run():
        if lock.locked():
            raise HTTPException(429,'A diagnostic is already running. Try again when it finishes.')
        async with lock:
            return await runner()
    return router
