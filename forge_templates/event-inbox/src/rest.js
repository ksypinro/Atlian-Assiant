// App REST API (Preview, EV section 15). The manifest declaration of this handler is UNVERIFIED (VER-06): adjust to the current Preview docs.
import { storage, startsWith } from '@forge/api';

const json = (status, body) => ({ statusCode: status, headers: { 'Content-Type': ['application/json'] }, body: JSON.stringify(body) });

export async function handle(request) {
  const url = new URL(request.path, 'https://app.invalid');
  const route = `${request.method} ${url.pathname}`;
  if (route === 'GET /events') {
    const limit = Math.min(Number(url.searchParams.get('limit') || 100), 500);
    let q = storage.query().where('key', startsWith('evt#')).limit(limit);
    const cursor = url.searchParams.get('cursor');
    if (cursor) q = q.cursor(cursor);
    const page = await q.getMany();
    return json(200, { events: page.results.map((r) => ({ ...r.value, key: r.key })), nextCursor: page.nextCursor || null });
  }
  if (route === 'POST /events/ack') {
    const { ids = [] } = JSON.parse(request.body || '{}');
    for (const key of ids) { const v = await storage.get(key); if (v) await storage.set(key, { ...v, processed: true }); }
    return json(200, { acked: ids.length });
  }
  const ack = url.pathname.match(/^\/events\/(.+)\/ack$/);
  if (request.method === 'POST' && ack) {
    const v = await storage.get(ack[1]); if (!v) return json(404, { error: 'not found' });
    await storage.set(ack[1], { ...v, processed: true }); return json(200, { acked: 1 });
  }
  if (route === 'GET /health') return json(200, { ok: true });
  if (route === 'GET /stats') {
    const page = await storage.query().where('key', startsWith('evt#')).limit(1).getMany();
    return json(200, { oldestReceivedAt: page.results[0] ? page.results[0].value.receivedAt : null });
  }
  // Dynamic Triggers (DM-08, Preview: VER-07): /subscriptions -> /forge/installation/v2/dynamic/module, as the app. Max 100 per installation.
  if (url.pathname === '/subscriptions') return json(501, { error: 'wire the Dynamic Modules API here (VER-07)' });
  return json(404, { error: 'unknown route' });
}
