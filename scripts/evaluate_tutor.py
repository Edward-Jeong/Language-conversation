"""Fixed-set actual provider check; AI expectations are NOT human quality signoff."""
import asyncio,json,pathlib,time,os,argparse,sys,hashlib,datetime
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from apps.api.config import Settings
from apps.api.providers import OllamaTutorProvider
async def run():
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=50);p.add_argument('--output',default='docs/evidence/tutor-quality.jsonl');a=p.parse_args()
    os.environ.setdefault('LC_AUTH_SECRET','probe-only-not-used-for-auth-000000000000')
    provider=OllamaTutorProvider(Settings.from_env())
    cases=json.loads(pathlib.Path('tests/fixtures/tutor_quality.json').read_text(encoding='utf-8-sig'))['cases'][:a.limit]
    dest=pathlib.Path(a.output)
    if dest.exists():raise SystemExit('Refusing to overwrite existing evidence. Choose another --output.')
    metadata={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'model_id':provider.model_id,'prompt_sha256':hashlib.sha256(provider.prompt.encode()).hexdigest(),'schema_sha256':hashlib.sha256(json.dumps(provider.schema,sort_keys=True).encode()).hexdigest(),'provider_source_sha256':hashlib.sha256(pathlib.Path('apps/api/providers.py').read_bytes()).hexdigest(),'expected_cases':len(cases),'human_review':'pending'}
    dest.with_suffix('.meta.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    with dest.open('w',encoding='utf-8') as f:
      for c in cases:
        ctx={'turn_id':c['id'],'transcript_version':1,'question_en':c['question'],'current_question':c['question'],'learner_transcript':c['transcript'],'fallback_next_question':'Could you tell me a little more?','scenario_id':'daily','intent_id':'meaningful-answer','attempt_kind':'initial'}
        start=time.perf_counter()
        try:
          result=await provider.evaluate(ctx);data=result.model_dump();needed=result.correction.needed
          if c['expected']=='no_required_correction':passed=result.evaluation_status=='ready' and not needed
          elif c['expected']=='one_required_correction':passed=result.evaluation_status=='ready' and needed
          else:passed=result.evaluation_status=='unassessable' or (not needed)
          row={'id':c['id'],'category':c['category'],'expected':c['expected'],'wall_ms':round((time.perf_counter()-start)*1000,1),'automatic_heuristic_pass':passed,'output':data,'human_review':'pending; verify fix adequacy and appropriateness'}
        except Exception as e:row={'id':c['id'],'wall_ms':round((time.perf_counter()-start)*1000,1),'error_type':type(e).__name__,'automatic_heuristic_pass':False}
        f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();print(c['id'],row['wall_ms'],row['automatic_heuristic_pass'],flush=True)
asyncio.run(run())
