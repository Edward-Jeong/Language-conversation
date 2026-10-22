"""Summarize frozen provider evidence without presenting heuristic checks as human accuracy."""
import argparse,json,pathlib,statistics,math,collections
p=argparse.ArgumentParser();p.add_argument('input');a=p.parse_args();source=pathlib.Path(a.input)
rows=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
categories={}
for name in ['clear_error','normal','acceptable_alternative','uncertain_or_incomplete']:
    group=[r for r in rows if r.get('category')==name]
    categories[name]={'count':len(group),'heuristic_pass':sum(r.get('automatic_heuristic_pass',False) for r in group),'required_correction':sum(r.get('output',{}).get('correction',{}).get('needed',False) for r in group),'unassessable':sum(r.get('output',{}).get('evaluation_status')=='unassessable' for r in group)}
latencies=sorted(r['wall_ms'] for r in rows)
summary={'source':str(source),'completed':len(rows),'expected':50,'complete_fixed_set':len(rows)==50,'categories':categories,'provider_wall_median_ms':statistics.median(latencies) if latencies else None,'provider_wall_p95_ms':latencies[max(0,math.ceil(len(latencies)*.95)-1)] if latencies else None,'mobile_end_to_end_ms':None,'human_validation':'pending; heuristic correction detection is not adequacy or accuracy','readiness':'not accepted as automatic mobile MVP'}
dest=source.with_suffix('.summary.json');dest.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=True,indent=2))
