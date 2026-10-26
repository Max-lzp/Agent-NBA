import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const code = await readFile(new URL('../../portfolio/timeline.js', import.meta.url), 'utf8');
const { validateTimeline } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const source = { kind: 'sample', id: 'teaching-v1', duration: 12 };
const event = { t: 6, label: '出手', notes: { expert: '出手说明', new: '术语解释', together: '一起观察' } };
const data = () => ({ schemaVersion: 1, source, events: [structuredClone(event)] });
assert.equal(validateTimeline(data(), source)[0].t, 6);
for (const t of [-1, 12, 100, null, '6', NaN, Infinity]) {
  const value = data(); value.events[0].t = t;
  assert.throws(() => validateTimeline(value, source));
}
assert.throws(() => validateTimeline({ ...data(), source: { ...source, id: 'wrong' } }, source));
assert.throws(() => validateTimeline({ ...data(), source: { ...source, duration: 99 } }, source));
assert.throws(() => validateTimeline({ ...data(), events: Array(1001).fill(event) }, source));
assert.throws(() => validateTimeline({ ...data(), schemaVersion: 2 }, source));
assert.throws(() => validateTimeline({ ...data(), events: [{ ...event, notes: null }] }, source));
const local = { kind: 'local', name: 'clip.mp4', size: 100, duration: 12 };
assert.throws(() => validateTimeline({ ...data(), source: { ...local, size: 101 } }, local));
const sorted = validateTimeline({ ...data(), events: [{ ...event, t: 8 }, { ...event, t: 2 }] }, source);
assert.deepEqual(sorted.map(e => e.t), [2, 8]);
assert.equal(validateTimeline({ ...data(), events: [] }, source).length, 0);
console.log('PASS: timeline contract, source matching, bounds, sorting, limits and malformed values');
