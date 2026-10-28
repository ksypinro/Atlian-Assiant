// Storage inbox (EV section 14): keys evt#<receivedAt>#<id> sort by arrival; a prefix query returns them in order.
import { storage, startsWith } from '@forge/api';

const TTL_MS = 72 * 3600 * 1000;
const pad = (n) => String(n).padStart(15, '0');

export async function write(event) {
  const items = Array.isArray(event.payload) ? event.payload : [event.payload];
  for (const s of items) {
    await storage.set(`evt#${pad(s.receivedAt)}#${s.id}`, { source: s.source, eventType: s.eventType, entity: s.entity, receivedAt: s.receivedAt, processed: false, id: s.id });
    if (s.eventType && s.eventType.endsWith(':deleted:page')) {          // cascade note (EV section 11): the Python side invalidates descendants
      await storage.set(`del#${s.entity.id}`, { receivedAt: s.receivedAt });
    }
  }
}

export async function purge() {
  const cutoff = Date.now() - TTL_MS;
  const page = await storage.query().where('key', startsWith('evt#')).limit(100).getMany();
  for (const r of page.results) if (r.value.processed && r.value.receivedAt < cutoff) await storage.delete(r.key);
}
