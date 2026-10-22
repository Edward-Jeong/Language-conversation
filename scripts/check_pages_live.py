"""Live public Pages -> Tunnel -> PC checks. Synthetic audio; never a mobile test."""
import asyncio,json,pathlib,re,secrets,time
import httpx
BASE='https://language-conversation.pages.dev'
async def main():
    out={'scope':'public HTTPS from PC; not iPhone; synthetic audio; real Ollama and whisper.cpp','url':BASE,'checks':{}}
    code=re.search(r'Pairing code: ([A-Z0-9]+)',pathlib.Path('.runtime/external-pair-private').read_text(encoding='utf-8-sig')).group(1)
    async with httpx.AsyncClient(base_url=BASE,timeout=130,follow_redirects=False,trust_env=False) as c:
        home=await c.get('/');out['checks']['home_https']=home.status_code
        out['checks']['noindex']=home.headers.get('x-robots-tag')
        blocked=await c.get('/api/scenarios');out['checks']['unpaired_scenarios']=blocked.status_code;assert blocked.status_code==401
        cap=await c.get('/api/capabilities');cap.raise_for_status();out['capabilities']=cap.json()
        pair=await c.post('/api/auth/pair',json={'code':code},headers={'Origin':BASE});pair.raise_for_status()
        out['checks']['pair']=pair.status_code
        cookies=pair.headers.get_list('set-cookie');out['checks']['two_cookies']=len(cookies)==2
        out['checks']['secure_samesite']=all('Secure' in x and 'SameSite=strict' in x for x in cookies)
        out['checks']['session_httponly']=any(x.startswith('lc_session=') and 'HttpOnly' in x for x in cookies)
        headers={'Origin':BASE,'X-CSRF-Token':pair.json()['csrf_token']}
        bad=await c.post('/api/sessions',json={'scenario_id':'cafe-order','idempotency_key':secrets.token_hex(16)},headers={'Origin':'https://evil.example'});out['checks']['bad_origin']=bad.status_code;assert bad.status_code==403
        bad=await c.post('/api/sessions',json={'scenario_id':'cafe-order','idempotency_key':secrets.token_hex(16)},headers={'Origin':BASE});out['checks']['no_csrf']=bad.status_code;assert bad.status_code==403
        created=await c.post('/api/sessions',json={'scenario_id':'cafe-order','idempotency_key':secrets.token_hex(16)},headers=headers);created.raise_for_status();sid=created.json()['id']
        out['checks']['session_create']=created.status_code
        async def heartbeat():
            while True:
                await asyncio.sleep(15)
                r=await c.post(f'/api/sessions/{sid}/heartbeat',json={'client_attempt_id':secrets.token_hex(16)},headers=headers);r.raise_for_status()
        task=asyncio.create_task(heartbeat())
        try:
            start=time.perf_counter()
            with open('.runtime/m0-16k.wav','rb') as f:
                audio=await c.post(f'/api/sessions/{sid}/audio',files={'file':('sample.wav',f,'audio/wav')},data={'client_attempt_id':secrets.token_hex(16)},headers=headers)
            out['audio_roundtrip_ms']=round((time.perf_counter()-start)*1000,2)
            out['audio_status']=audio.status_code
            out['audio_result']=audio.json();audio.raise_for_status()
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        stored=await c.get(f'/api/sessions/{sid}');stored.raise_for_status();out['checks']['stored_session']=stored.status_code
        out['checks']['api_no_store']=stored.headers.get('cache-control')
        end=await c.post(f'/api/sessions/{sid}/end',json={'client_attempt_id':secrets.token_hex(16)},headers=headers);end.raise_for_status();out['checks']['end']=end.status_code
        logged=await c.post('/api/auth/logout',headers=headers);logged.raise_for_status();out['checks']['logout']=logged.status_code
        denied=await c.get('/api/scenarios');out['checks']['after_logout']=denied.status_code;assert denied.status_code==401
    async with httpx.AsyncClient(timeout=20,trust_env=False) as direct:
        tunnel='https://refined-film-lights-carolina.trycloudflare.com'
        r=await direct.get(tunnel+'/api/health');out['checks']['direct_tunnel_without_secret']=r.status_code;assert r.status_code==403
    pathlib.Path('docs/evidence/pages-live.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':out['checks'],'audio_status':out['audio_status'],'audio_roundtrip_ms':out['audio_roundtrip_ms']},ensure_ascii=False))
asyncio.run(main())
