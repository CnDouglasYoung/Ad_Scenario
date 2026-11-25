/* Shared pure data model; also executable by Node for regression checks. */
(function (root) {
  'use strict';
  const systemTypes = new Set(['agent/status', 'agent/session', 'run/begin', 'run/end']);

  function parse(text) {
    const events = [];
    text.replace(/^\uFEFF/, '').split(/\r?\n/).forEach((line, i) => {
      if (!line.trim()) return;
      let event;
      try { event = JSON.parse(line); }
      catch (_) { throw new Error(`第 ${i + 1} 行不是有效 JSON`); }
      if (!event || Array.isArray(event) || typeof event !== 'object' || typeof event.type !== 'string') {
        throw new Error(`第 ${i + 1} 行缺少事件 type 字符串`);
      }
      events.push(event);
    });
    if (!events.length) throw new Error('文件中没有事件');
    return events;
  }

  function build(events) {
    let day = null, segment = 0;
    const calls = new Map(), items = [], entries = [], days = [];
    events.forEach((event, index) => {
      if (event.type === 'run/begin') { day = null; segment++; calls.clear(); }
      if (event.type === 'step/begin') {
        day = event.day ?? '?'; segment++; calls.clear();
        days.push({key: String(segment), day, entries: [], items: []});
      }
      const entry = {event, index, day, segment: String(segment), system: systemTypes.has(event.type)};
      entries.push(entry);
      const group = days.find(d => d.key === entry.segment);
      if (group) group.entries.push(entry);
      let item;
      // Pair only with a preceding call in the same step, never across days.
      // Reused IDs attach to the most recent call; multiple results are retained.
      if (event.type === 'agent/tool_result' && event.callId != null && calls.has(event.callId)) {
        item = calls.get(event.callId);
        item.results.push(entry);
        item.entries.push(entry);
        entry.item = item;
      } else {
        item = {entry, entries: [entry], results: [], kind: event.type, system: entry.system};
        entry.item = item;
        items.push(item);
        if (group) group.items.push(item);
        if (event.type === 'agent/tool_call' && event.callId != null) calls.set(event.callId, item);
      }
    });
    items.forEach(item => { item.search = item.entries.map(e => JSON.stringify(e.event)).join('\n').toLocaleLowerCase(); });
    const incomplete = items.filter(i => i.kind === 'agent/tool_call' && !i.results.length);
    const orphaned = items.filter(i => i.kind === 'agent/tool_result');
    return {events, entries, items, days, incomplete, orphaned};
  }

  function category(type) {
    if (type === 'agent/tool_call' || type === 'agent/tool_result') return 'tool';
    if (type === 'agent/thinking') return 'thinking';
    if (type === 'agent/text' || type === 'agent/final') return 'text';
    if (type === 'step/verifier' || type === 'run/score') return 'verifier';
    if (type === 'step/begin') return 'instruction';
    return 'system';
  }

  function format(value) {
    if (typeof value !== 'string') return JSON.stringify(value, null, 2) ?? '';
    // Pretty-print complete JSON lines while retaining command prefixes and prose.
    return value.split('\n').map(line => {
      if (!/^[\s]*[\[{]/.test(line)) return line;
      try { return JSON.stringify(JSON.parse(line), null, 2); }
      catch (_) { return line; }
    }).join('\n');
  }

  const api = {parse, build, category, format};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EventModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
