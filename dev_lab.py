"""Owned, deterministic failure fixtures. Fixed variants are preconfigured corrections."""
import json
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

CASES={
    'cors':('Missing CORS header','CORS_BLOCKED','Return the exact frontend origin in Access-Control-Allow-Origin.'),
    'route':('Wrong frontend API route','API_ROUTE_MISMATCH','Point the frontend at the intended API route.'),
    'server':('API returns HTTP 500','API_SERVER_ERROR','Replace the deliberate error response with the working handler.'),
    'html':('HTML instead of expected JSON','API_NOT_JSON','Return JSON from the API instead of the frontend HTML fallback.'),
    'runtime':('Frontend JavaScript crash','FRONTEND_RUNTIME_ERROR','Remove the deliberate undefined render function call.'),
    'healthy':('Healthy control','NO_ISSUE_OBSERVED','No correction required; this is the negative control.'),
}
api=FastAPI(docs_url=None,redoc_url=None)

@api.get('/__clinic_lab__')
async def identity():return {'fixture':'developer-clinic-owned-lab-v1'}

@api.get('/api/{case}/{state}')
async def endpoint(case:str,state:str):
    if case not in CASES or state not in ('broken','fixed'):raise HTTPException(404)
    headers={} if case=='cors' and state=='broken' else {'Access-Control-Allow-Origin':'http://127.0.0.1:8000'}
    if case=='server' and state=='broken':return JSONResponse({'error':'Deliberate lab failure'},status_code=500,headers=headers)
    if case=='html' and state=='broken':return HTMLResponse('<html><body>Deliberate frontend fallback</body></html>',headers=headers)
    return JSONResponse({'message':'Demo application loaded its API successfully.'},headers=headers)

@api.get('/wrong-route')
async def wrong():return JSONResponse({'error':'Deliberate missing route'},status_code=404,headers={'Access-Control-Allow-Origin':'http://127.0.0.1:8000'})


def create_fixture_router():
    router=APIRouter(prefix='/dev/lab')
    @router.get('/{case}/{state}')
    async def frontend(case:str,state:str):
        if case not in CASES or state not in ('broken','fixed'):raise HTTPException(404)
        url=f'http://127.0.0.1:8012/api/{case}/{state}'
        if case=='route' and state=='broken':url='http://127.0.0.1:8012/wrong-route'
        script="missingRenderFunction();" if case=='runtime' and state=='broken' else f"""(async()=>{{try{{
          const response=await fetch({json.dumps(url)});if(!response.ok)throw Error('API HTTP '+response.status);
          const data=await response.json();document.getElementById('app').textContent=data.message;
          document.getElementById('app').dataset.state='ready';
        }}catch(error){{document.getElementById('app').textContent='Demo application failed to load its API.';document.getElementById('app').dataset.state='error';}}
        finally{{window.__clinicDone=true;}}}})();"""
        return HTMLResponse(f'''<!doctype html><html><title>Owned diagnostic fixture</title><style>
        body{{font:18px/1.5 system-ui;background:#f1f5f9;color:#17263a;margin:0;padding:32px}}
        h1{{font-size:28px}}p{{font-size:14px;color:#52657c}}#app{{font-size:23px;background:white;padding:24px;border:2px solid #8dabc6;border-radius:12px;margin-top:28px}}
        #app[data-state=error]{{border-color:#d66262;color:#9a2626}}#app[data-state=ready]{{border-color:#42a77b;color:#186043}}
        </style><body>
        <h1>Controlled lab application</h1><p>Scenario: {case}; variant: {state}. This is an intentionally constructed test, not a production incident.</p>
        <div id="app" data-state="loading">Loading API...</div><script>
        window.__clinicDone=false;window.addEventListener('error',()=>{{document.getElementById('app').dataset.state='error';document.getElementById('app').textContent='Frontend crashed before rendering API data.';window.__clinicDone=true;}});
        </script><script>{script}</script></body></html>''')
    return router
