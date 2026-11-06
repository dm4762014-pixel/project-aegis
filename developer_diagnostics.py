"""Evidence-based browser diagnostics; no AI-generated root causes."""
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


def clean_url(value):
    if not isinstance(value,str) or len(value)>4096 or any(ord(c)<32 for c in value):
        raise ValueError('Invalid URL')
    p=urlsplit(value)
    if p.scheme not in ('http','https') or not p.hostname:
        raise ValueError('Expected an HTTP(S) URL')
    port=p.port
    host=p.hostname.lower()
    if ':' in host:host=f'[{host}]'
    netloc=host+(f':{port}' if port and port!=(443 if p.scheme=='https' else 80) else '')
    return urlunsplit((p.scheme,netloc,p.path or '/','',''))


def origin(url):
    p=urlsplit(url)
    return f'{p.scheme}://{p.netloc}'


def diagnose(observation):
    """Rules consume observations only; they receive no fixture case or fix label."""
    frontend=clean_url(observation['frontend_url']);api=clean_url(observation['api_url'])
    requests=observation.get('requests',[])[:200]
    relevant=[r for r in requests if origin(r['url'])==origin(api) and
              (r.get('resource_type') in ('fetch','xhr') or r['url']==api)]
    findings=[]
    def add(code,title,confidence,evidence,next_check):
        findings.append(dict(code=code,title=title,confidence=confidence,evidence=evidence,next_check=next_check))
    server=[r for r in relevant if 500<=r.get('status',0)<=599]
    missing=[r for r in relevant if r.get('status')==404]
    probe=observation.get('direct_probe') or {}
    if server:
        add('API_SERVER_ERROR','The API returned a server error','observed',
            [f"{r['method']} {r['url']} returned HTTP {r['status']}." for r in server[:3]],
            'Inspect server logs for this request. A 5xx response does not identify the failing dependency or code.')
    refused=[r for r in relevant if r.get('status') in (401,403)]
    if refused:
        add('API_ACCESS_REFUSED','An API request was refused','observed',
            [f"The API returned HTTP {refused[0]['status']}."],
            'Check authentication, permissions and gateway policy. The status does not establish which credential or rule caused it.')
    redirected=[r for r in relevant if r['url']==api and 300<=r.get('status',0)<400]
    if redirected:
        add('API_REDIRECT','The expected API returned a redirect','observed',
            [f"The expected API returned HTTP {redirected[0]['status']}."],
            'Inspect the redirect locally for login, canonical-host or rewrite behavior. Redirect destinations are omitted from this report.')
    if observation.get('console_cors'):
        evidence=['The browser console reported a CORS policy rejection.']
        if probe.get('status')==200:evidence.append('An independent request reached the intended API and received HTTP 200.')
        add('CORS_BLOCKED','Browser access was blocked by CORS','observed',evidence,
            f'Check the API CORS policy for the exact frontend origin {origin(frontend)}. Do not disable browser security.')
    if missing:
        mismatch=[r for r in missing if r['url']!=api]
        if mismatch and probe.get('status')==200:
            add('API_ROUTE_MISMATCH','A different API route failed while the intended route responded','observed',
                [f"The frontend requested {mismatch[0]['url']} and received HTTP 404.",f'The supplied intended API {api} independently returned HTTP 200.'],
                'Compare the frontend API configuration, path and deployment environment. The evidence does not explain why they differ.')
        else:
            add('API_ROUTE_404','An API request returned HTTP 404','observed',
                [f"{missing[0]['url']} returned HTTP 404."],
                'Verify the route, API version and deployment configuration. A 404 alone does not prove a configuration bug.')
    html=[r for r in relevant if r['url']==api and r.get('status')==200 and r.get('content_type','').startswith('text/html')]
    if html:
        add('API_NOT_JSON','The expected JSON API returned HTML','observed',
            ['The supplied API URL returned HTTP 200 with Content-Type text/html.'],
            'Check for a frontend fallback, login page or rewrite. HTML is observed; its cause is not established.')
    if observation.get('page_error_count',0):
        add('FRONTEND_RUNTIME_ERROR','An uncaught JavaScript error occurred','observed',
            [f"The browser captured {observation['page_error_count']} uncaught page error(s)."],
            'Inspect the browser stack trace locally. Raw error messages are omitted from the exported report to limit secret exposure.')
    failed=[r for r in relevant if r.get('failed')]
    if failed and not observation.get('console_cors'):
        code='TLS_BROWSER_ERROR' if any(r.get('certificate_error') for r in failed) else 'REQUEST_FAILED'
        add(code,'A browser request failed before a usable response','observed',
            [f"The browser reported a failed request to {failed[0]['url']}."],
            'Inspect the browser network error. DNS, TLS, policy and routing failures can look alike; do not infer CORS from a generic failure.')
    doc=[r for r in requests if r['url']==frontend and r.get('resource_type')=='document' and r.get('status',0)>=400]
    if doc:
        add('FRONTEND_HTTP_ERROR','The frontend document returned an HTTP error','observed',
            [f"The document returned HTTP {doc[0]['status']}."],'Check hosting, document routing and deployment logs.')
    if not findings:
        successful=any(r['url']==api and 200<=r.get('status',0)<300 and 'json' in r.get('content_type','') for r in relevant)
        if successful:
            add('NO_ISSUE_OBSERVED','No covered browser/API failure was observed','limited',
                ['The expected API returned a successful JSON response and no covered error signal was captured.'],
                'This covers the captured page load only. Login, user actions and other routes were not tested.')
        else:
            add('INCONCLUSIVE','The capture does not establish the cause','insufficient',
                ['No conclusive covered failure or successful expected JSON request was captured.'],
                'Capture the failing action and supply its exact API URL. A HAR without console evidence cannot confirm a CORS rejection.')
    return dict(schema_version=1,kind='developer-diagnostic',timestamp_utc=datetime.now(timezone.utc).isoformat(),
                source=observation.get('source','imported observations'),frontend_url=frontend,api_url=api,
                findings=findings,requests=requests,app_state=observation.get('app_state','not observed'),
                direct_probe=probe or None,page_error_count=observation.get('page_error_count'),
                limits='Rule-based triage of a bounded capture; observations are not complete root causes. No code or settings were changed. URLs omit credentials, query strings and fragments; paths may still be sensitive.')


def analyze_har(payload):
    frontend=clean_url(payload['frontend_url']);api=clean_url(payload['api_url'])
    entries=payload['har']['log']['entries']
    if not isinstance(entries,list) or len(entries)>200:
        raise ValueError('Use a HAR capture with at most 200 entries.')
    requests=[]
    for entry in entries:
        request=entry.get('request',{});response=entry.get('response',{})
        url=clean_url(request.get('url',''))
        if origin(url) not in (origin(frontend),origin(api)):continue
        headers={str(h.get('name','')).lower():str(h.get('value','')) for h in response.get('headers',[]) if isinstance(h,dict)}
        status=response.get('status',0)
        if type(status) is not int or not 0<=status<=599:raise ValueError('Invalid HAR response status.')
        error=str(entry.get('_error',''))
        requests.append(dict(url=url,method=str(request.get('method','GET'))[:12],status=status,
            resource_type=str(entry.get('_resourceType','unknown'))[:20],failed=status==0 or bool(error),
            certificate_error='ERR_CERT_' in error,content_type=headers.get('content-type',response.get('content',{}).get('mimeType','')).split(';')[0].lower()[:80]))
    return diagnose(dict(frontend_url=frontend,api_url=api,requests=requests,source='Imported HAR; unverified capture; no network requests performed'))
