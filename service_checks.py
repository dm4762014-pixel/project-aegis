"""Specific public HTTPS checks, pinned to validated public DNS answers."""
import asyncio
import ipaddress
import re
import socket
import ssl
import time
import certifi
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TIMEOUT = 5
STAGES = ('dns','tcp','tls','http')


def normalize_target(value):
    if any(ord(c)<33 or ord(c)>126 for c in value) or '\\' in value:
        raise ValueError('Use an ASCII HTTPS URL without whitespace.')
    p = urlsplit(value)
    try:
        port = p.port
    except ValueError:
        raise ValueError('Invalid port.')
    if p.scheme!='https' or not p.hostname or p.username is not None or p.password is not None or port not in (None,443):
        raise ValueError('Use a public HTTPS website on port 443, without login credentials.')
    if p.query or p.fragment:
        raise ValueError('Remove query parameters and fragments; do not paste login tokens.')
    host = p.hostname.lower().rstrip('.')
    if len(host)>253 or '.' not in host or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',s) for s in host.split('.')):
        raise ValueError('Use a public domain name, not a local hostname or IP address.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP address targets are not supported.')
    if host.endswith(('.localhost','.local','.internal','.home','.test','.invalid')):
        raise ValueError('Local and reserved names are not supported.')
    return urlunsplit(('https',host,p.path or '/','',''))


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: str = Field(max_length=1000)
    device_id: UUID
    device_label: str = Field(min_length=1,max_length=40,pattern=r'^[^\x00-\x1f\x7f]+$')
    _target = field_validator('target')(normalize_target)


class Stage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Literal['dns','tcp','tls','http']
    status: Literal['passed','failed','skipped','warning']
    detail: str = Field(max_length=500)
    elapsed_ms: float = Field(ge=0,le=120000,allow_inf_nan=False)
    http_status: int | None = Field(default=None,ge=100,le=599)


class ServiceReport(CheckRequest):
    schema_version: Literal[2]
    kind: Literal['service']
    mode: Literal['live','simulated']
    report_id: UUID
    timestamp_utc: datetime
    stages: list[Stage] = Field(min_length=4,max_length=4)
    summary: str = Field(max_length=800)
    scope: str = Field(max_length=400)

    @model_validator(mode='after')
    def consistent(self):
        if tuple(s.id for s in self.stages)!=STAGES:
            raise ValueError('A report must contain DNS, TCP, TLS, HTTP once, in order.')
        if self.timestamp_utc.tzinfo is None:
            raise ValueError('Report timestamps must include a timezone.')
        blocked = False
        for stage in self.stages:
            if stage.id!='http' and stage.http_status is not None:
                raise ValueError('Only the HTTP stage can have an HTTP status code.')
            if stage.id=='http' and stage.status in ('passed','warning'):
                if stage.http_status is None or (200<=stage.http_status<300)!=(stage.status=='passed'):
                    raise ValueError('HTTP result and status code must agree.')
            if blocked and stage.status!='skipped':
                raise ValueError('Stages after a failed prerequisite must be skipped.')
            if stage.id!='http' and stage.status=='warning':
                raise ValueError('Only HTTP can be a warning.')
            if not blocked and stage.status=='skipped':
                raise ValueError('A stage cannot be skipped without a failed prerequisite.')
            if stage.status!='passed':
                blocked = True
        return self


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reports: list[ServiceReport] = Field(min_length=2,max_length=12)


def summary_for(stages):
    for stage in stages:
        if stage['status']=='failed':
            return {'dns':'DNS did not pass. The service address could not be safely resolved.',
                    'tcp':'DNS passed, but a TCP connection did not. A route, firewall, endpoint, or outage could be involved.',
                    'tls':'TCP passed, but certificate-verified TLS did not. Do not bypass certificate warnings.',
                    'http':'TLS passed, but no valid HTTP response was obtained.'}[stage['id']]
        if stage['status']=='warning':
            return 'The service responded over verified HTTPS, but its HTTP result needs attention. This does not confirm that login or application features work.'
    return 'The service returned a successful HTTP response over verified HTTPS. This does not test login or application features.'


async def resolve_public(host):
    answers = await asyncio.get_running_loop().getaddrinfo(host,443,type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(a[4][0] for a in answers))
    def safe(ip):
        address=ipaddress.ip_address(ip)
        return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)
    if not ips or not all(safe(ip) for ip in ips):
        raise ValueError('Target is not exclusively public')
    # Pin the connection to a validated numeric address; no second DNS lookup.
    return sorted(ips,key=lambda ip:':' in ip)[:3]


async def connect_public(ips):
    for ip in ips:
        try:
            return await asyncio.wait_for(asyncio.open_connection(ip,443,limit=16384),TIMEOUT/len(ips))
        except (OSError,TimeoutError):
            continue
    raise OSError('Connection failed')


async def check_service(request: CheckRequest):
    target = urlsplit(request.target)
    stages = []
    reader = writer = None
    async def stage(key,action,success):
        started=time.perf_counter()
        try:
            result=await asyncio.wait_for(action(),TIMEOUT)
            entry={'id':key,'status':'passed','detail':success,'elapsed_ms':round((time.perf_counter()-started)*1000,1)}
            if key=='http':
                if not re.fullmatch(rb'HTTP/1\.[01] [1-5][0-9]{2}(?: [^\r\n]*)?\r\n',result):
                    raise ValueError('Malformed HTTP status line')
                status=int(result.split(b' ')[1])
                entry['status']='passed' if 200<=status<300 else 'warning'
                entry['http_status']=status
                entry['detail']=f'HEAD returned HTTP {status}. '+('Redirect not followed.' if 300<=status<400 else 'No login or response body was fetched.')
            stages.append(entry)
            return result
        except (OSError,TimeoutError,ValueError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            detail={'dns':'Resolution failed or included a non-public address. Local/private targets are blocked.',
                    'tcp':'No TCP connection succeeded to the tested public addresses within the deadline.',
                    'tls':'Certificate-verified TLS failed or timed out. The certificate, network interception, or protocol could be involved.',
                    'http':'A bounded HTTP status response could not be read.'}[key]
            stages.append({'id':key,'status':'failed','detail':detail,'elapsed_ms':round((time.perf_counter()-started)*1000,1)})
            raise
    try:
        ips=await stage('dns',lambda:resolve_public(target.hostname),'System DNS returned public addresses. Resolved IPs are omitted from the report.')
        reader,writer=await stage('tcp',lambda:connect_public(ips),'Connected on TCP port 443 to a validated public address.')
        await stage('tls',lambda:writer.start_tls(ssl.create_default_context(cafile=certifi.where()),server_hostname=target.hostname,ssl_handshake_timeout=TIMEOUT),'Certificate chain and hostname verified using the bundled public CA certificates (certifi).')
        async def head():
            writer.write(f'HEAD {target.path} HTTP/1.1\r\nHost: {target.hostname}\r\nUser-Agent: ConnectionClinic/2.0\r\nConnection: close\r\n\r\n'.encode('ascii'))
            await writer.drain()
            return await reader.readuntil(b'\r\n')
        await stage('http',head,'HTTP response received.')
    except (OSError,TimeoutError,ValueError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
        pass
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(),1)
            except (OSError,TimeoutError):
                pass
    for key in STAGES[len(stages):]:
        stages.append({'id':key,'status':'skipped','detail':'A prerequisite did not pass; this stage was not tested.','elapsed_ms':0})
    report={**request.model_dump(mode='json'),'schema_version':2,'kind':'service','mode':'live',
            'report_id':str(uuid4()),'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'stages':stages,'summary':summary_for(stages),
            'scope':'One laptop, up to three public addresses, direct HTTPS on port 443. No redirects, login, credentials, or private-network targets. Device identity is self-reported.'}
    return ServiceReport.model_validate(report).model_dump(mode='json')


def compare_reports(reports):
    targets={r.target for r in reports}
    if len(targets)!=1:
        raise ValueError('Compare reports for exactly the same service URL and path.')
    ids=[r.report_id for r in reports]
    if len(set(ids))!=len(ids):
        raise ValueError('The same report was imported more than once.')
    if len({r.device_id for r in reports})!=len(reports):
        raise ValueError('Use one report per device identity. Remove older runs from the same computer.')
    counts={key:{s:0 for s in ('passed','failed','warning','skipped')} for key in STAGES}
    for report in reports:
        for stage in report.stages:
            counts[stage.id][stage.status]+=1
    patterns=[]
    for key in STAGES:
        failed=counts[key]['failed']
        if failed>=2:
            patterns.append(f'{failed} reports failed at {key.upper()}. This is a matching symptom, not proof of a shared cause.')
        if failed and counts[key]['passed']:
            patterns.append(f'{key.upper()} passed on some reported devices and failed on others. Compare their network, proxy and device configuration.')
    http_codes={}
    for report in reports:
        code=report.stages[-1].http_status
        if code is not None:
            http_codes[str(code)]=http_codes.get(str(code),0)+1
    for code,count in http_codes.items():
        if count>=2 and int(code)>=300:
            patterns.append(f'{count} reports received HTTP {code}. The service was reachable, but the HTTP response needs investigation; this does not prove a shared cause.')
    dates=[r.timestamp_utc for r in reports]
    spread=round((max(dates)-min(dates)).total_seconds()/60,1)
    return {'target':reports[0].target,'report_count':len(reports),'device_identity_count':len(reports),
            'includes_simulation':any(r.mode=='simulated' for r in reports),'time_spread_minutes':spread,
            'patterns':patterns or ['No matching failure was established. Inspect each stage and HTTP warning.'],
            'counts':counts,'http_status_counts':http_codes,'rows':[{'device_label':r.device_label,'device_id':str(r.device_id),'mode':r.mode,
                                      'timestamp_utc':r.timestamp_utc.isoformat(),'http_status':r.stages[-1].http_status,'stages':{s.id:s.status for s in r.stages}} for r in reports],
            'limits':'Imported files are unverified observations with self-reported device identities. Different timestamps and networks limit comparisons; matching failures do not prove an outage.'}


def create_service_router(checker=check_service):
    router=APIRouter(prefix='/services')
    lock=asyncio.Lock()
    @router.get('/examples')
    async def examples():
        result=[]
        for index in range(3):
            stages=[]
            for key in STAGES:
                status=('failed' if key=='dns' else 'skipped') if index<2 else 'passed'
                stages.append({'id':key,'status':status,'elapsed_ms':0,
                               'detail':'SIMULATED example; no connection was made.',
                               'http_status':200 if key=='http' and status=='passed' else None})
            result.append(ServiceReport(target='https://github.com/',device_id=uuid4(),
                device_label=f'Simulated lab PC {index+1}',schema_version=2,kind='service',mode='simulated',
                report_id=uuid4(),timestamp_utc=datetime.now(timezone.utc),stages=stages,
                summary=summary_for(stages),scope='SIMULATED demonstration. Not evidence from a real computer.').model_dump(mode='json'))
        return result
    @router.post('/check')
    async def check(body:CheckRequest):
        if lock.locked():
            raise HTTPException(429,'A service check is already running.')
        async with lock:
            return await checker(body)
    @router.post('/compare')
    async def compare(body:CompareRequest):
        try:
            return compare_reports(body.reports)
        except ValueError as exc:
            raise HTTPException(422,str(exc))
    return router
