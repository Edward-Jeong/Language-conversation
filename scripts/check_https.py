import os,ssl,httpx,json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
os.environ['LC_AUTH_SECRET']='https-probe-unused-token-0000000000000'
os.environ['LC_DATABASE_PATH']=str(pathlib.Path('.runtime/https-test.db').resolve())
from apps.api.config import Settings
from apps.api.db import Database
from apps.api.security import issue_pairing_code
s=Settings.from_env();db=Database(s.database_path);code,_=issue_pairing_code(db,s)
context=ssl.create_default_context(cafile='.cert/ca.pem')
with httpx.Client(base_url='https://127.0.0.1:8443',verify=context,trust_env=False) as c:
    unauth=c.get('/api/history');assert unauth.status_code==401
    paired=c.post('/api/auth/pair',json={'code':code},headers={'Origin':'https://127.0.0.1:8443'});paired.raise_for_status()
    cookies=paired.headers.get_list('set-cookie');assert any('Secure' in x and 'HttpOnly' in x and 'SameSite=strict' in x for x in cookies)
    csrf=paired.json()['csrf_token'];headers={'Origin':'https://127.0.0.1:8443','X-CSRF-Token':csrf}
    assert c.get('/api/history').status_code==200
    assert c.post('/api/sessions',json={'scenario_id':'cafe-order','idempotency_key':'tls-session-test'},headers={'Origin':'https://evil.invalid','X-CSRF-Token':csrf}).status_code==403
    session=c.post('/api/sessions',json={'scenario_id':'cafe-order','idempotency_key':'tls-session-test'},headers=headers);session.raise_for_status()
    assert c.post('/api/auth/pair',json={'code':code},headers={'Origin':'https://127.0.0.1:8443'}).status_code==401
    outcome={'scope':'PC loopback HTTPS verification with explicit CA trust, mock provider; not iPhone/system-trust test','tls_verified':True,'unauthenticated_denied':True,'secure_httponly_samesite_cookie':True,'allowed_origin_session_created':True,'foreign_origin_denied':True,'one_time_code_reuse_denied':True,'mode':'mock'}
    pathlib.Path('docs/evidence/https-probe.json').write_text(json.dumps(outcome,indent=2),encoding='utf-8');print(json.dumps(outcome))
