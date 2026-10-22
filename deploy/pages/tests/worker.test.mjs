import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../_worker.js', import.meta.url), 'utf8');
const { default: worker } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
);

const PAGES_ORIGIN = 'https://language-conversation.pages.dev';
const BACKEND_ORIGIN = 'https://example-tunnel.trycloudflare.com';
const EDGE_SECRET = 'trusted-edge-secret';

function environment(assetResponse = new Response('asset')) {
  const assetRequests = [];
  return {
    assetRequests,
    env: {
      ASSETS: {
        async fetch(request) {
          assetRequests.push(request);
          return assetResponse;
        },
      },
      LC_BACKEND_ORIGIN: BACKEND_ORIGIN,
      LC_EDGE_SECRET: EDGE_SECRET,
    },
  };
}

async function withFetch(replacement, callback) {
  const original = globalThis.fetch;
  globalThis.fetch = replacement;
  try {
    return await callback();
  } finally {
    globalThis.fetch = original;
  }
}

test('static and non-/api/ paths use ASSETS and receive noindex', async () => {
  const { env, assetRequests } = environment(
    new Response('asset', { headers: { 'Cache-Control': 'public, max-age=3600' } }),
  );

  await withFetch(
    async () => {
      throw new Error('the backend must not receive static paths');
    },
    async () => {
      for (const path of ['/lesson', '/api', '/api-not-really']) {
        const response = await worker.fetch(new Request(`${PAGES_ORIGIN}${path}`), env);
        assert.equal(response.status, 200);
        assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow');
        assert.equal(response.headers.get('cache-control'), 'public, max-age=3600');
      }
    },
  );

  assert.deepEqual(assetRequests.map((request) => new URL(request.url).pathname), [
    '/lesson',
    '/api',
    '/api-not-really',
  ]);
});

test('API proxy fixes the upstream origin, strips spoofable forwarding headers, and preserves cookies', async () => {
  const { env, assetRequests } = environment();
  const responseHeaders = new Headers();
  responseHeaders.append('Set-Cookie', 'lc_session=one; Path=/; Secure; HttpOnly');
  responseHeaders.append('Set-Cookie', 'lc_csrf=two; Path=/; Secure');
  let forwardedRequest;

  const response = await withFetch(
    async (request) => {
      forwardedRequest = request;
      return new Response('{"ok":true}', { status: 200, headers: responseHeaders });
    },
    () => worker.fetch(
      new Request(`${PAGES_ORIGIN}/api/auth/pair?attempt=1`, {
        method: 'POST',
        headers: {
          Origin: PAGES_ORIGIN,
          'Content-Type': 'application/json',
          'X-LC-Edge-Secret': 'attacker-value',
          Forwarded: 'for=attacker.example',
          'X-Forwarded-For': '203.0.113.8',
          'X-Forwarded-Host': 'attacker.example',
          'X-Forwarded-Proto': 'http',
          'X-Real-IP': '203.0.113.8',
          'X-LC-Client-IP': '203.0.113.9',
          'CF-Connecting-IP': '198.51.100.27',
        },
        body: '{"code":"ABCDEFGH"}',
        duplex: 'half',
      }),
      env,
    ),
  );

  assert.equal(forwardedRequest.url, `${BACKEND_ORIGIN}/api/auth/pair?attempt=1`);
  assert.equal(forwardedRequest.redirect, 'manual');
  assert.equal(forwardedRequest.headers.get('origin'), PAGES_ORIGIN);
  assert.equal(forwardedRequest.headers.get('x-lc-edge-secret'), EDGE_SECRET);
  assert.equal(forwardedRequest.headers.get('x-lc-client-ip'), '198.51.100.27');
  for (const header of ['forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip', 'cf-connecting-ip']) {
    assert.equal(forwardedRequest.headers.get(header), null);
  }
  assert.equal(await forwardedRequest.text(), '{"code":"ABCDEFGH"}');
  assert.deepEqual(response.headers.getSetCookie(), [
    'lc_session=one; Path=/; Secure; HttpOnly',
    'lc_csrf=two; Path=/; Secure',
  ]);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow');
  assert.equal(assetRequests.length, 0);
});

test('mutating API requests require the exact Pages Origin', async () => {
  const { env } = environment();
  let backendCalls = 0;

  await withFetch(
    async () => {
      backendCalls += 1;
      return new Response('{}');
    },
    async () => {
      for (const origin of [undefined, 'https://attacker.example', `${PAGES_ORIGIN}/`, 'not a url']) {
        const headers = origin === undefined ? {} : { Origin: origin };
        const response = await worker.fetch(
          new Request(`${PAGES_ORIGIN}/api/auth/pair`, {
            method: 'POST',
            headers,
            body: '{}',
            duplex: 'half',
          }),
          env,
        );
        assert.equal(response.status, 403);
        assert.equal((await response.json()).detail.code, 'origin_rejected');
      }
    },
  );

  assert.equal(backendCalls, 0);
});

test('invalid backend origins and missing edge secrets fail closed', async () => {
  const invalidOrigins = [
    'http://example-tunnel.trycloudflare.com',
    'https://trycloudflare.com',
    'https://example.com',
    'https://user@example-tunnel.trycloudflare.com',
    'https://example-tunnel.trycloudflare.com/path',
    'https://example-tunnel.trycloudflare.com?target=other',
    'https://example-tunnel.trycloudflare.com#fragment',
    'https://example-tunnel.trycloudflare.com:444',
  ];
  let backendCalls = 0;

  await withFetch(
    async () => {
      backendCalls += 1;
      return new Response('{}');
    },
    async () => {
      for (const backendOrigin of invalidOrigins) {
        const { env } = environment();
        env.LC_BACKEND_ORIGIN = backendOrigin;
        const response = await worker.fetch(new Request(`${PAGES_ORIGIN}/api/health`), env);
        assert.equal(response.status, 503, backendOrigin);
        assert.equal((await response.json()).detail.code, 'edge_configuration_error');
      }

      for (const secret of [undefined, '', 'bad\r\nheader']) {
        const { env } = environment();
        env.LC_EDGE_SECRET = secret;
        const response = await worker.fetch(new Request(`${PAGES_ORIGIN}/api/health`), env);
        assert.equal(response.status, 503);
      }
    },
  );

  assert.equal(backendCalls, 0);
});

test('redirects are denied without exposing their destination', async () => {
  const { env } = environment();
  const response = await withFetch(
    async () => new Response(null, {
      status: 302,
      headers: { Location: 'https://attacker.example/capture' },
    }),
    () => worker.fetch(new Request(`${PAGES_ORIGIN}/api/health`), env),
  );

  assert.equal(response.status, 502);
  assert.equal(response.headers.get('location'), null);
  assert.equal((await response.json()).detail.code, 'upstream_redirect_rejected');
});

test('timeouts and other upstream failures return bounded JSON errors', async () => {
  const { env } = environment();

  const timeout = await withFetch(
    async () => {
      throw new DOMException('upstream details', 'TimeoutError');
    },
    () => worker.fetch(new Request(`${PAGES_ORIGIN}/api/health`), env),
  );
  const unavailable = await withFetch(
    async () => {
      throw new TypeError('internal network details');
    },
    () => worker.fetch(new Request(`${PAGES_ORIGIN}/api/health`), env),
  );

  assert.equal(timeout.status, 504);
  assert.equal((await timeout.json()).detail.code, 'upstream_timeout');
  assert.equal(unavailable.status, 502);
  assert.equal((await unavailable.json()).detail.code, 'upstream_unavailable');
});

test('API fetch combines caller cancellation with the reviewed 360-second timeout', async () => {
  const { env } = environment();
  const caller = new AbortController();
  const timeout = new AbortController();
  const combined = new AbortController();
  const originalTimeout = AbortSignal.timeout;
  const originalAny = AbortSignal.any;
  let timeoutMilliseconds;
  let combinedSignals;
  let upstreamSignal;
  const browserRequest = new Request(`${PAGES_ORIGIN}/api/health`, {
    signal: caller.signal,
  });

  AbortSignal.timeout = (milliseconds) => {
    timeoutMilliseconds = milliseconds;
    return timeout.signal;
  };
  AbortSignal.any = (signals) => {
    combinedSignals = signals;
    return combined.signal;
  };
  try {
    await withFetch(
      async (request) => {
        upstreamSignal = request.signal;
        return new Response('{}');
      },
      () => worker.fetch(browserRequest, env),
    );
  } finally {
    AbortSignal.timeout = originalTimeout;
    AbortSignal.any = originalAny;
  }

  assert.equal(timeoutMilliseconds, 360_000);
  assert.equal(combinedSignals.length, 2);
  assert.equal(combinedSignals[0], browserRequest.signal);
  assert.equal(combinedSignals[1], timeout.signal);
  combined.abort();
  assert.equal(upstreamSignal.aborted, true);
});
