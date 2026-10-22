const API_TIMEOUT_MS = 360_000;
const ROBOTS_POLICY = 'noindex, nofollow';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function apiJson(status, code, message, retryable = false) {
  return new Response(
    JSON.stringify({ detail: { code, message, retryable } }),
    {
      status,
      headers: {
        'Cache-Control': 'no-store',
        'Content-Type': 'application/json; charset=utf-8',
        'X-Robots-Tag': ROBOTS_POLICY,
      },
    },
  );
}

function configuredBackendOrigin(value) {
  if (typeof value !== 'string') return null;

  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }

  const isExactRoot = value === parsed.origin || value === `${parsed.origin}/`;
  const isTryCloudflareHost =
    parsed.hostname.endsWith('.trycloudflare.com') &&
    parsed.hostname.length > '.trycloudflare.com'.length;
  if (
    parsed.protocol !== 'https:' ||
    !isTryCloudflareHost ||
    parsed.port !== '' ||
    parsed.username !== '' ||
    parsed.password !== '' ||
    parsed.pathname !== '/' ||
    parsed.search !== '' ||
    parsed.hash !== '' ||
    !isExactRoot
  ) {
    return null;
  }
  return parsed.origin;
}

function configuredEdgeSecret(value) {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value !== value.trim() ||
    /[\r\n]/u.test(value)
  ) {
    return null;
  }
  return value;
}

function stripUntrustedProxyHeaders(headers) {
  for (const name of [...headers.keys()]) {
    const lowerName = name.toLowerCase();
    if (
      lowerName === 'forwarded' ||
      lowerName.startsWith('x-forwarded-') ||
      lowerName === 'x-real-ip' ||
      lowerName === 'x-lc-edge-secret' ||
      lowerName === 'x-lc-client-ip' ||
      lowerName === 'cf-connecting-ip'
    ) {
      headers.delete(name);
    }
  }
}

function withResponseHeaders(response, { api }) {
  const headers = new Headers(response.headers);
  headers.set('X-Robots-Tag', ROBOTS_POLICY);
  if (api) headers.set('Cache-Control', 'no-store');
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function proxyApi(request, env, requestUrl) {
  const backendOrigin = configuredBackendOrigin(env.LC_BACKEND_ORIGIN);
  const edgeSecret = configuredEdgeSecret(env.LC_EDGE_SECRET);
  if (!backendOrigin || !edgeSecret) {
    return apiJson(
      503,
      'edge_configuration_error',
      'The API proxy is not configured.',
    );
  }

  if (
    !SAFE_METHODS.has(request.method.toUpperCase()) &&
    request.headers.get('Origin') !== requestUrl.origin
  ) {
    return apiJson(
      403,
      'origin_rejected',
      'The request origin is not allowed.',
    );
  }

  const upstreamUrl = new URL(backendOrigin);
  upstreamUrl.pathname = requestUrl.pathname;
  upstreamUrl.search = requestUrl.search;

  const headers = new Headers(request.headers);
  const clientIp = request.headers.get('CF-Connecting-IP')?.trim() || 'unknown';
  stripUntrustedProxyHeaders(headers);
  headers.set('X-LC-Edge-Secret', edgeSecret);
  headers.set('X-LC-Client-IP', clientIp);

  let upstream;
  try {
    const requestWithTarget = new Request(upstreamUrl, request);
    const upstreamRequest = new Request(requestWithTarget, {
      headers,
      redirect: 'manual',
      signal: AbortSignal.any([
        request.signal,
        AbortSignal.timeout(API_TIMEOUT_MS),
      ]),
    });
    upstream = await fetch(upstreamRequest);
  } catch (error) {
    const timedOut = error?.name === 'TimeoutError' || error?.name === 'AbortError';
    return apiJson(
      timedOut ? 504 : 502,
      timedOut ? 'upstream_timeout' : 'upstream_unavailable',
      timedOut
        ? 'The private API did not respond in time.'
        : 'The private API is temporarily unavailable.',
      true,
    );
  }

  if (upstream.status >= 300 && upstream.status < 400) {
    if (upstream.body) {
      try {
        await upstream.body.cancel();
      } catch {
        // The response is already being discarded; cancellation is best effort.
      }
    }
    return apiJson(
      502,
      'upstream_redirect_rejected',
      'The private API returned an unexpected redirect.',
    );
  }

  return withResponseHeaders(upstream, { api: true });
}

export default {
  async fetch(request, env) {
    const requestUrl = new URL(request.url);
    if (requestUrl.pathname.startsWith('/api/')) {
      return proxyApi(request, env, requestUrl);
    }

    const assetResponse = await env.ASSETS.fetch(request);
    return withResponseHeaders(assetResponse, { api: false });
  },
};
