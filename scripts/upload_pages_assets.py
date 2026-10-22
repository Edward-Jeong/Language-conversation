"""Upload public build assets using a short-lived Pages upload JWT (not account token).
The deployment manifest and worker are submitted separately through the connected API.
Never print the JWT. Token file must be under ignored .runtime and removed after use.
"""
import argparse, base64, hashlib, json, mimetypes, pathlib, urllib.request
p=argparse.ArgumentParser()
p.add_argument('--token-file',type=pathlib.Path,required=True)
p.add_argument('--output',type=pathlib.Path,default=pathlib.Path('.runtime/pages-manifest.json'))
a=p.parse_args()
root=pathlib.Path('apps/web/dist'); assets=[]; manifest={}
for f in sorted(root.rglob('*')):
    if not f.is_file() or f.name in {'_worker.js','_routes.json','_headers','_redirects'}: continue
    data=f.read_bytes(); key=hashlib.sha256(data+f.suffix.encode()).hexdigest()[:32]
    mime={'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.webmanifest':'application/manifest+json'}.get(f.suffix,mimetypes.guess_type(str(f))[0] or 'application/octet-stream')
    assets.append(dict(key=key,value=base64.b64encode(data).decode(),base64=True,metadata={'contentType':mime}))
    manifest['/'+f.relative_to(root).as_posix()]=key
try:
    token=a.token_file.read_text(encoding='utf-8-sig').strip()
    req=urllib.request.Request('https://api.cloudflare.com/client/v4/pages/assets/upload',data=json.dumps(assets).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=120) as r: result=json.load(r)
    if not result.get('success'): raise RuntimeError('Asset upload rejected')
    a.output.write_text(json.dumps(manifest),encoding='utf-8')
    print(json.dumps({'uploaded_files':len(assets),'manifest':str(a.output)}))
finally:
    a.token_file.unlink(missing_ok=True)
