"""Real public-service checks on ONE laptop. No multi-machine claim."""
import json
import sys
from pathlib import Path
from uuid import uuid4
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from service_checks import ServiceReport

device=str(uuid4())
results=[]
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30,trust_env=False) as client:
    for name,target in [('github','https://github.com/'),('example','https://example.com/'),
                        ('pypi','https://pypi.org/'),('http-negative-control','https://example.com/clinic-not-found-check-20261105')]:
        response=client.post('/services/check',json={'target':target,'device_id':device,'device_label':'One real Windows laptop'})
        response.raise_for_status()
        report=ServiceReport.model_validate(response.json()).model_dump(mode='json')
        Path(f'results/service-{name}.json').write_text(json.dumps(report,indent=2))
        assert all(s['status']=='passed' for s in report['stages'][:3]),report
        expected='warning' if name=='http-negative-control' else 'passed'
        assert report['stages'][3]['status']==expected,report
        results.append({'target':target,'stages':{s['id']:s['status'] for s in report['stages']},'http_detail':report['stages'][3]['detail']})
        print(json.dumps(results[-1]),flush=True)
    blocked=client.post('/services/check',json={'target':'https://127.0.0.1/','device_id':device,'device_label':'One laptop'})
    assert blocked.status_code==422
report={'mode':'Real public network checks; one laptop; no probe mocks',
        'public_service_results':results,'private_literal_target_rejected':True,'passed':True}
Path('results/services-live.json').write_text(json.dumps(report,indent=2))
