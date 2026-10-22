import httpx,time,json,pathlib,datetime
runs=[]
for i in range(3):
    start=time.perf_counter()
    with open('.runtime/m0-16k.wav','rb') as f:r=httpx.post('http://127.0.0.1:8080/inference',files={'file':('sample.wav',f,'audio/wav')},data={'response_format':'json','temperature':'0.0','language':'en'},timeout=120)
    elapsed=(time.perf_counter()-start)*1000
    r.raise_for_status();runs.append({'wall_ms':round(elapsed,2),'result':r.json()});print(runs[-1],flush=True)
pathlib.Path('docs/evidence/m0-stt.json').write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'whisper_cpp':'v1.7.6','model':'base multilingual','input':'Windows Microsoft David synthetic voice, not learner audio','expected':'I would like a cup of coffee, please.','runs':runs,'iphone':None},ensure_ascii=False,indent=2),encoding='utf-8')
