"""Local diagnostic endpoints and bounded, owned-browser lab execution."""
import asyncio
import json
import os
import shutil
import socket
import sys
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from developer_diagnostics import analyze_har, diagnose
from dev_lab import CASES

ROOT=Path(__file__).parent


async def stop_owned(process):
    if process and process.returncode is None:
        # Terminate only this owned worker tree, including its Chromium children.
        try:
            for child in psutil.Process(process.pid).children(recursive=True):
                try:child.terminate()
                except psutil.Error:pass
        except psutil.Error:pass
        try:process.terminate()
        except ProcessLookupError:return
        try:await asyncio.wait_for(process.wait(),3)
        except TimeoutError:
            process.kill();await process.wait()


async def run_lab():
    node=shutil.which('node')
    if not node:raise HTTPException(503,'Node.js is required. Run run_developer.bat to prepare the browser lab.')
    with socket.socket() as sock:
        try:
            if os.name=='nt':sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            sock.bind(('127.0.0.1',8012))
        except OSError:raise HTTPException(409,'Lab port 8012 is already in use. No existing process was stopped.')
    flags={'creationflags':0x08000000} if os.name=='nt' else {}
    api_process=worker=None
    try:
        api_process=await asyncio.create_subprocess_exec(sys.executable,'-m','uvicorn','dev_lab:api','--host','127.0.0.1','--port','8012',
                        cwd=str(ROOT),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,**flags)
        async with httpx.AsyncClient(trust_env=False,timeout=.5) as client:
            for attempt in range(40):
                if api_process.returncode is not None:raise HTTPException(503,'The owned lab API could not start.')
                try:
                    r=await client.get('http://127.0.0.1:8012/__clinic_lab__')
                    if r.json().get('fixture')=='developer-clinic-owned-lab-v1':break
                except (httpx.HTTPError,ValueError):pass
                await asyncio.sleep(.1)
            else:raise HTTPException(503,'The owned lab API did not become ready.')
        run_id=str(uuid4())
        worker=await asyncio.create_subprocess_exec(node,str(ROOT/'dev_worker.cjs'),run_id,cwd=str(ROOT),
                       stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,**flags)
        try:stdout,stderr=await asyncio.wait_for(worker.communicate(),60)
        except TimeoutError:raise HTTPException(504,'Browser lab exceeded its 60-second limit. Its owned processes were stopped.')
        if worker.returncode!=0:
            raise HTTPException(503,'Browser lab could not complete. Run npm install and npx playwright install chromium, then retry. No passing result was recorded.')
        captures=json.loads(stdout)
        observations={(r['scenario'],r['variant']):r['observation'] for r in captures['captures']}
        if len(observations)!=12:raise ValueError('Incomplete browser capture')
        cases=[]
        for key,(title,expected,change) in CASES.items():
            before=diagnose(observations[(key,'broken')]);after=diagnose(observations[(key,'fixed')])
            before_codes=[f['code'] for f in before['findings']];after_codes=[f['code'] for f in after['findings']]
            passed=expected in before_codes and after_codes==['NO_ISSUE_OBSERVED'] and after['app_state']=='ready'
            passed=passed and before['app_state']==('ready' if key=='healthy' else 'error')
            cases.append(dict(case=key,title=title,expected_before=expected,preconfigured_correction=change,
                              before=before,after=after,verified=passed,
                              before_snapshot=f'/dev/snapshots/{run_id}/{key}/broken',after_snapshot=f'/dev/snapshots/{run_id}/{key}/fixed'))
        report=dict(schema_version=1,kind='developer-lab-experiment',timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    mode='Real Chromium and HTTP against owned local fixtures; no response mocks',
                    passed=all(c['verified'] for c in cases),cases=cases,run_id=run_id,
                    scope='Five deliberately broken applications and one healthy control, each checked in its broken and preconfigured corrected variant. One Windows laptop; no production incidents or automatic code repairs.')
        path=ROOT/'results'/'developer-lab-latest.json';path.parent.mkdir(exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(report,indent=2),encoding='utf-8');temp.replace(path)
        return report
    except (json.JSONDecodeError,KeyError,ValueError):
        raise HTTPException(502,'The browser lab returned incomplete evidence. No passing result was recorded.')
    finally:
        await stop_owned(worker)
        await stop_owned(api_process)


def create_developer_router(runner=run_lab):
    router=APIRouter(prefix='/dev')
    lock=asyncio.Lock()
    @router.post('/run-lab')
    async def execute():
        if lock.locked():raise HTTPException(429,'A browser lab run is already in progress.')
        async with lock:return await runner()
    @router.get('/latest')
    async def latest():
        path=ROOT/'results'/'developer-lab-latest.json'
        if not path.exists():raise HTTPException(404,'Run the browser lab to collect evidence first.')
        return FileResponse(path,media_type='application/json',headers={'Cache-Control':'no-store'})
    @router.post('/analyze-har')
    async def har(request:Request):
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>1024*1024:raise HTTPException(413,'Use a filtered HAR payload smaller than 1 MiB.')
            data.extend(chunk)
        try:
            payload=json.loads(data)
            return analyze_har(payload)
        except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
            raise HTTPException(422,'Invalid capture. Supply frontend_url, api_url and a standard HAR log with at most 200 HTTP(S) entries.')
    @router.get('/snapshots/{run_id}/{case}/{variant}')
    async def snapshot(run_id:UUID,case:str,variant:str):
        if case not in CASES or variant not in ('broken','fixed'):raise HTTPException(404)
        path=ROOT/'results'/'developer-captures'/str(run_id)/f'{case}-{variant}.png'
        if not path.exists():raise HTTPException(404,'No screenshot was captured for that observation.')
        return FileResponse(path,media_type='image/png')
    @router.get('/lab-har/{run_id}/{case}/{variant}')
    async def lab_har(run_id:UUID,case:str,variant:str):
        if case not in CASES or variant not in ('broken','fixed'):raise HTTPException(404)
        path=ROOT/'results'/'developer-captures'/str(run_id)/f'{case}-{variant}.har'
        if not path.exists():raise HTTPException(404,'No owned-lab HAR was captured for that observation.')
        return FileResponse(path,media_type='application/json',filename=f'owned-lab-{case}-{variant}.har')
    return router
