"""Read-only local provider probe; never contacts a hosted inference service.
python scripts/m0_probe.py --model gemma3:4b --turns 3
"""
import argparse,json,time,urllib.request,statistics,pathlib,datetime

def request(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request('http://127.0.0.1:11434'+path,data,{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=180) as r:return json.load(r)

def main():
    p=argparse.ArgumentParser();p.add_argument('--model',default='gemma3:4b');p.add_argument('--turns',type=int,default=3);p.add_argument('--output',default='docs/evidence/m0-llm.json');a=p.parse_args()
    tags=request('/api/tags')['models'];model=next((x for x in tags if x['name']==a.model),None)
    if not model or ':cloud' in a.model:raise SystemExit('A downloaded local model is required')
    schema={'type':'object','properties':{'intent_achieved':{'type':'boolean'},'correction_needed':{'type':'boolean'},'suggested_en':{'type':'string'},'reason_ko':{'type':'string'},'next_question_en':{'type':'string'}},'required':['intent_achieved','correction_needed','suggested_en','reason_ko','next_question_en'],'additionalProperties':False}
    samples=[('What did you do yesterday?','I go to the park yesterday.'),('What would you like?','Could I get an iced latte, please?'),('What do you like doing?','I enjoy cooking because it helps me relax.')]
    runs=[]
    for i in range(a.turns):
        q,t=samples[i%len(samples)];start=time.perf_counter()
        r=request('/api/chat',{'model':a.model,'stream':False,'format':schema,'messages':[{'role':'system','content':'You are a concise English tutor. Accept all appropriate alternatives. Correct at most one important error. Explain in Korean. Return the supplied JSON schema only. No score. If no error suggested_en and reason_ko must be empty.'},{'role':'user','content':json.dumps({'question':q,'transcript':t})}],'options':{'temperature':0,'num_predict':200,'num_ctx':2048},'keep_alive':'5m'})
        elapsed=(time.perf_counter()-start)*1000
        try:parsed=json.loads(r['message']['content']);valid=set(parsed)==set(schema['required'])
        except Exception:parsed=None;valid=False
        runs.append({'sample_index':i%len(samples),'wall_ms':round(elapsed,2),'load_ms':r.get('load_duration',0)/1e6,'eval_ms':r.get('eval_duration',0)/1e6,'eval_count':r.get('eval_count'),'schema_keys_valid':valid,'output':parsed});print(f'run {i+1}: {elapsed:.0f}ms schema={valid}',flush=True)
    warm=[x['wall_ms'] for x in runs[1:]]
    out={'measured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'direct LLM probe, not final tutor contract, STT or mobile E2E','model':model,'ollama_version':request('/api/version'),'runs':runs,'warm_median_ms':statistics.median(warm) if warm else None,'warm_p95_ms':None,'mobile_e2e_ms':None,'paid_api_calls':0,'network_path':'HTTP loopback only; no hosted provider fallback'}
    pathlib.Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
