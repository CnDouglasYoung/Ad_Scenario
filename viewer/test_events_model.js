// Run with: node viewer/test_events_model.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const M = require('./events_model.js');

assert.equal(M.parse('\uFEFF{"type":"run/begin"}\r\n\n').length, 1);
assert.throws(() => M.parse('{"type":"x"}\ninvalid'), /第 2 行/);
for (const bad of ['null', '[]', '{}', '{"type":5}', '']) assert.throws(() => M.parse(bad));

const events = [
  {seq: 0, type: 'step/begin', day: 1},
  {seq: 1, type: 'agent/tool_call', callId: 'a', input: {command: 'first'}},
  {seq: 2, type: 'agent/tool_call', callId: 'b'},
  {seq: 3, type: 'agent/tool_result', callId: 'b', result: 'B'},
  {seq: 4, type: 'agent/tool_result', callId: 'a', result: 'A'},
  {seq: 5, type: 'agent/tool_result', callId: 'a', result: 'A2'},
  {seq: 6, type: 'agent/tool_call', callId: 'a', input: {command: 'second'}},
  {seq: 7, type: 'agent/tool_result', callId: 'a', result: 'second result'},
  {seq: 8, type: 'step/begin', day: 2},
  {seq: 9, type: 'agent/tool_result', callId: 'a', result: 'must stay orphaned'},
  {seq: 10, type: 'agent/tool_call', callId: 'a'},
  {seq: 11, type: 'agent/tool_result', callId: 'a', result: 'day2'},
  {seq: 12, type: 'agent/tool_call', callId: 'missing'},
  {seq: 13, type: 'agent/tool_call'},
  {seq: 14, type: 'agent/tool_result'},
];
const model = M.build(events);
assert.deepEqual(model.entries[1].item.results.map(r => r.event.result), ['A', 'A2']);
assert.equal(model.entries[2].item.results[0].event.result, 'B');
assert.equal(model.entries[6].item.results[0].event.result, 'second result');
assert.equal(model.entries[10].item.results[0].event.result, 'day2');
assert.deepEqual(model.orphaned.map(i => i.entry.index), [9, 14]);
assert.deepEqual(model.incomplete.map(i => i.entry.index), [12, 13]);
// Grouping is a lossless partition, including out-of-order and orphaned returns.
assert.deepEqual(model.items.flatMap(i => i.entries.map(e => e.index)).sort((a, b) => a - b), events.map((_, i) => i));
assert(model.entries[1].item.search.includes('a2'));
assert.equal(M.format('prefix\n{"a":1}\nnot json'), 'prefix\n{\n  "a": 1\n}\nnot json');

// callId reuse across runs must not match a previous run.
const runs = M.build([{type: 'agent/tool_call', callId: 'x'}, {type: 'run/begin'},
  {type: 'agent/tool_result', callId: 'x'}]);
assert.equal(runs.orphaned.length, 1);
assert.equal(runs.incomplete.length, 1);

const realFile = path.join(__dirname, '../work/harbor-jobs/script0/export/events.jsonl');
if (fs.existsSync(realFile)) {
  const real = M.build(M.parse(fs.readFileSync(realFile, 'utf8')));
  assert.equal(real.days.length, real.events.filter(e => e.type === 'step/begin').length);
  assert.equal(real.items.filter(i => i.kind === 'agent/tool_call').length,
    real.events.filter(e => e.type === 'agent/tool_call').length);
  assert.deepEqual(real.items.flatMap(i => i.entries.map(e => e.index)).sort((a, b) => a - b),
    real.events.map((_, i) => i));
}
console.log('Event parsing, pairing, boundaries, lossless coverage and real trace checks passed.');
