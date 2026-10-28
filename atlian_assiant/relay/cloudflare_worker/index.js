// Cloudflare relay (DM-42): verify the webhook, then enqueue a MINIMAL envelope. The Python consumer (CloudflareQueuePullSource) normalizes it.
// Verification mirrors the Python verifier chain: optional HMAC signature (WebSub style), optional static header token. Nothing is stored here.

const enc = new TextEncoder();

function hex(buf) { return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join(''); }

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function verify(request, body, env) {
  if (env.WEBHOOK_TOKEN) {
    const got = request.headers.get(env.WEBHOOK_HEADER || 'X-Webhook-Token') || '';
    if (!timingSafeEqual(got, env.WEBHOOK_TOKEN)) return false;
  }
  if (env.WEBHOOK_SECRET) {
    const header = request.headers.get(env.SIGNATURE_HEADER || 'X-Hub-Signature') || '';
    const digest = header.includes('=') ? header.split('=')[1] : header;
    const key = await crypto.subtle.importKey('raw', enc.encode(env.WEBHOOK_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const expected = hex(await crypto.subtle.sign('HMAC', key, enc.encode(body)));
    if (!timingSafeEqual(digest.toLowerCase(), expected)) return false;
  }
  return Boolean(env.WEBHOOK_TOKEN || env.WEBHOOK_SECRET);   // refuse to run unauthenticated
}

export function envelope(request, body) {
  const h = (n) => request.headers.get(n);
  return { relay: 'cloudflare', headers: { 'X-Atlassian-Webhook-Identifier': h('X-Atlassian-Webhook-Identifier'), 'X-Atlassian-Webhook-Retry': h('X-Atlassian-Webhook-Retry'),
                                           'X-Atlassian-Webhook-Flow': h('X-Atlassian-Webhook-Flow') }, body: JSON.parse(body) };
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return new Response('', { status: 405 });
    const body = await request.text();
    if (body.length > 1048576) return new Response('', { status: 413 });
    if (!(await verify(request, body, env))) return new Response('', { status: 401 });
    let msg;
    try { msg = envelope(request, body); } catch (e) { return new Response('', { status: 400 }); }
    await env.EVENTS_QUEUE.send(msg);          // a failure throws -> 5xx -> Atlassian retries
    return new Response('', { status: 202 });
  },
};
