"""Synthetic audio -> real API adapters. Not an actual mobile/audio-device test."""
import sys,pathlib,os,time,json,secrets,argparse
p=argparse.ArgumentParser();p.add_argument('--output',default='docs/evidence/real-api-audio-final.json');args=p.parse_args()
if pathlib.Path(args.output).exists():raise SystemExit('Refusing to overwrite evidence. Choose another --output.')
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
os.environ['LC_AUTH_SECRET']=secrets.token_hex(48)
os.environ['LC_DATABASE_PATH']=str(pathlib.Path('.runtime/real-integration.db').resolve())
os.environ['LC_AI_PROVIDER']='ollama'
os.environ['LC_COOKIE_SECURE']='1'
os.environ['LC_ALLOWED_ORIGINS']='https://testserver'
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.security import issue_pairing_code
app=create_app()
with TestClient(app,base_url='https://testserver') as c:
    code,_=issue_pairing_code(app.state.db,app.state.settings)
    paired=c.post('/api/auth/pair',json={'code':code},headers={'Origin':'https://testserver'});paired.raise_for_status()
    headers={'Origin':'https://testserver','X-CSRF-Token':paired.json()['csrf_token']}
    session=c.post('/api/sessions',json={'scenario_id':'cafe-order','idempotency_key':secrets.token_hex(16)},headers=headers);session.raise_for_status();sid=session.json()['id']
    start=time.perf_counter()
    with open('.runtime/m0-16k.wav','rb') as f:r=c.post(f'/api/sessions/{sid}/audio',files={'file':('sample.wav',f,'audio/wav')},data={'client_attempt_id':secrets.token_hex(16)},headers=headers)
    elapsed=(time.perf_counter()-start)*1000
    out={'scope':'API TestClient -> FFmpeg -> actual whisper.cpp -> actual Ollama -> validated server result; synthetic English, no real iPhone/TTS','provider':'real','model':app.state.settings.ollama_model,'status_code':r.status_code,'server_roundtrip_ms':round(elapsed,2),'result':r.json(),'concurrent_quality_probe':False,'mobile_e2e_ms':None}
    pathlib.Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(r.status_code,round(elapsed,2),flush=True)
    ended=c.post(f'/api/sessions/{sid}/end',json={'client_attempt_id':secrets.token_hex(16)},headers=headers);ended.raise_for_status()
