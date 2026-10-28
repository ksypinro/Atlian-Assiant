// inbox mode (DM-07): compact signal -> Async Queue (<= 50 events and 200 KB per push, EV section 13).
import { Queue } from '@forge/events';

const queue = new Queue({ key: 'product-events' });
const MAX_EVENTS = 50;
const MAX_BYTES = 204800;

export function compact(event) {
  const entity = event.issue || event.content || event.page || event.comment || event.project || event.version || event.sprint || {};
  return { id: event.id || `${event.eventType}#${entity.id || entity.key || Date.now()}#${Date.now()}`, source: event.eventType.split(':')[1],
           eventType: event.eventType, entity: { id: entity.id, key: entity.key }, receivedAt: Date.now() };
}

export function batches(signals) {            // never exceed the documented push limits
  const out = []; let cur = []; let size = 0;
  for (const s of signals) {
    const n = JSON.stringify(s).length;
    if (cur.length >= MAX_EVENTS || size + n > MAX_BYTES) { out.push(cur); cur = []; size = 0; }
    cur.push(s); size += n;
  }
  if (cur.length) out.push(cur);
  return out;
}

export async function onEvent(event, context) {
  for (const batch of batches([compact(event)])) await queue.push(batch);
}
