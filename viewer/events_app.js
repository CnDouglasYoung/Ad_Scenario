/* Offline DOM renderer. Log content is inserted only through text nodes. */
(() => {
  'use strict';
  const M = EventModel;
  const $ = id => document.getElementById(id);
  let model, selectedDay = 'all', mode = 'chain', focused = -1, expanded = null;
  let searchTimer, toastTimer;
  const fileItems = new Map();
  let activeFile = null, fileGeneration = 0, localFileCounter = 0;

  function renderFileList() {
    const select = $('file-select');
    select.replaceChildren();
    for (const server of [true, false]) {
      const items = [...fileItems.values()].filter(item => item.server === server);
      if (!items.length) continue;
      const group = el('optgroup'); group.label = server ? '本机事件文件' : '本次导入的文件';
      for (const item of items) {
        const option = el('option', '', item.label);
        option.value = item.id; group.append(option);
      }
      select.append(group);
    }
    select.value = activeFile;
    select.title = fileItems.get(activeFile)?.label || '';
    $('file-status').textContent = `${fileItems.size} 个文件 · 可多选导入或拖入`;
  }
  function updateFileUrl() {
    try {
      const url = new URL(location.href);
      if (fileItems.get(activeFile)?.server) url.searchParams.set('file', activeFile);
      else url.searchParams.delete('file');
      url.hash = '';
      history.replaceState(null, '', url);
    } catch (_) { /* file URL restrictions */ }
  }
  function activateFile(data, id, updateUrl = true) {
    rememberTranslations(data.translations); saveTranslations();
    load(data.events, data.name, data.path);
    activeFile = id;
    renderFileList();
    if (updateUrl) updateFileUrl();
  }
  async function selectFile(id) {
    const item = fileItems.get(id);
    if (!item) return;
    const generation = ++fileGeneration;
    $('file-status').textContent = '正在读取文件…';
    try {
      let data = item.payload;
      if (item.server) {
        const response = await fetch(`/api/file?id=${encodeURIComponent(id)}`);
        data = await response.json();
        if (!response.ok) throw new Error(data.error || '文件读取失败');
      }
      if (generation !== fileGeneration) return;
      activateFile(data, id);
      window.scrollTo({top: 0});
      toast(`已切换 · ${data.events.length} 条事件`);
    } catch (error) {
      if (generation !== fileGeneration) return;
      $('error').textContent = `切换失败：${error.message}。当前文件保持不变。`;
      $('error').classList.remove('hidden');
      renderFileList();
    }
  }
  async function refreshFiles(showMessage = false) {
    if (location.protocol === 'file:') {
      if (showMessage) toast('离线阅读时可多选导入文件，再从列表切换');
      return;
    }
    $('refresh-files').disabled = true;
    try {
      const response = await fetch('/api/files');
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.files)) throw new Error('无法获取文件列表');
      for (const [id, item] of fileItems) {
        if (item.server && id !== activeFile) fileItems.delete(id);
      }
      for (const file of data.files) fileItems.set(file.id, {...file, server: true});
      renderFileList();
      if (showMessage) toast('文件列表已刷新');
    } catch (_) {
      if (showMessage) toast('无法刷新目录；仍可通过「导入 JSONL 文件」打开文件');
    } finally { $('refresh-files').disabled = false; }
  }
  const translations = new Map(), translationErrors = new Map(), translating = new Set();
  const thoughtModes = new Map();
  let originalAll = false, batch = null, lastTranslationStatus = '', translationAvailable = null;
  const storageKey = 'event-reader:zh-translations:v1';
  function rememberTranslations(items) {
    if (!Array.isArray(items)) return;
    for (const item of items) {
      if (typeof item?.text === 'string' && typeof item?.translation === 'string' && item.translation.trim()) {
        translations.set(item.text, item.translation);
      }
    }
  }
  try { rememberTranslations(JSON.parse(localStorage.getItem(storageKey) || '[]')); } catch (_) { /* optional cache */ }
  function saveTranslations() {
    try { localStorage.setItem(storageKey, JSON.stringify([...translations].map(([text, translation]) => ({text, translation})))); }
    catch (_) { /* Server-side and embedded caches still work when storage is full. */ }
  }
  function thoughts() {
    return model.entries.filter(entry => entry.event.type === 'agent/thinking' && typeof entry.event.text === 'string' && entry.event.text.trim());
  }
  function translationControls() {
    const all = thoughts(), cached = all.filter(e => translations.has(e.event.text)).length;
    $('translate-all').textContent = batch ? `翻译中 ${batch.done}/${batch.total}` : cached === all.length && all.length ? '查看全部中文思考' : `翻译全部思考${cached ? `（${cached}/${all.length} 已缓存）` : ''}`;
    $('translate-all').disabled = !!batch || !all.length || translating.size > 0;
    $('toggle-original').classList.toggle('hidden', !cached);
    $('toggle-original').textContent = originalAll ? '全部显示中文' : '全部显示原文';
    const status = batch ? `正在翻译 ${batch.done + 1}/${batch.total} 段思考${batch.cancelled ? ' · 正在完成当前段，随后停止' : ''}；已完成的译文可立即阅读。` : lastTranslationStatus;
    $('translation-progress').classList.toggle('hidden', !status);
    $('translation-progress-text').textContent = status;
    $('stop-translation').classList.toggle('hidden', !batch);
    $('stop-translation').disabled = !!batch?.cancelled;
  }
  function renderThought(entry) {
    const text = entry.event.text;
    if (typeof text !== 'string' || !text.trim()) return content(text ?? entry.event, true);
    const wrapper = el('div', 'thinking-text');
    wrapper.dataset.entry = entry.index;
    const chinese = translations.get(text), showOriginal = thoughtModes.get(text) ?? originalAll;
    const isChinese = !!chinese && !showOriginal;
    const controls = el('div', 'translation-controls');
    controls.append(el('span', 'translation-label', isChinese ? '中文译文' : '原文'));
    const button = el('button', 'translate-one', translating.has(text) ? '翻译中…' : chinese ? (isChinese ? '显示原文' : '显示中文') : '翻译为中文');
    button.disabled = translating.has(text) || (!chinese && !!batch);
    button.addEventListener('click', async () => {
      if (translations.has(text)) { thoughtModes.set(text, isChinese); refreshTranslations(); }
      else {
        lastTranslationStatus = '';
        try { await translateText(text); } catch (_) { /* inline error */ }
      }
    });
    controls.append(button);
    if (isChinese) {
      const copyButton = el('button', '', '复制译文');
      copyButton.addEventListener('click', () => copy(chinese)); controls.append(copyButton);
    }
    wrapper.append(controls, content(isChinese ? chinese : text, true));
    if (isChinese) wrapper.append(block('对照原文', text, false, true));
    if (translationErrors.has(text)) wrapper.append(el('p', 'translation-error', translationErrors.get(text)));
    return wrapper;
  }
  function refreshTranslations() {
    // Update only thought bodies to preserve scroll position and other open cards.
    document.querySelectorAll('.thinking-text').forEach(node => {
      const entry = model.entries[Number(node.dataset.entry)];
      if (entry) node.replaceWith(renderThought(entry));
    });
    translationControls();
  }
  async function translateText(text) {
    if (translations.has(text)) { thoughtModes.set(text, false); refreshTranslations(); return; }
    if (translating.has(text)) return;
    translating.add(text); translationErrors.delete(text); refreshTranslations();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 190000);
    try {
      if (location.protocol === 'file:' || translationAvailable === false) {
        throw new Error('新增翻译需要本机翻译服务，请用 --serve 启动阅读器后打开本机网址。已有译文仍可离线阅读。');
      }
      const response = await fetch('/api/translate', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, fileId: fileItems.get(activeFile)?.server ? activeFile : null}), signal: controller.signal});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('翻译服务未连接，请用 --serve 启动阅读器后重试。'); }
      if (!response.ok) throw new Error(data.error || `翻译失败（HTTP ${response.status}）`);
      if (typeof data.translation !== 'string' || !data.translation.trim()) throw new Error('没有收到译文，请重试。');
      translations.set(text, data.translation); thoughtModes.set(text, false); saveTranslations();
    } catch (error) {
      const message = error.name === 'AbortError' ? '翻译超时，请重试。' : error.message;
      translationErrors.set(text, message); throw new Error(message);
    } finally {
      clearTimeout(timeout); translating.delete(text); refreshTranslations();
    }
  }
  async function translateAll() {
    if (batch || translating.size) return;
    originalAll = false; thoughtModes.clear();
    const texts = [...new Set(thoughts().map(e => e.event.text))].filter(text => !translations.has(text));
    if (!texts.length) { refreshTranslations(); toast('已显示全部中文译文'); return; }
    const job = {done: 0, total: texts.length, failed: 0, cancelled: false};
    batch = job; lastTranslationStatus = ''; refreshTranslations();
    for (const text of texts) {
      if (job.cancelled) break;
      try { await translateText(text); }
      catch (_) { job.failed++; job.stoppedOnError = true; }
      job.done++;
      // A disconnected service or configuration error affects the entire batch.
      if (job.stoppedOnError) break;
      if (batch === job) translationControls();
    }
    if (batch === job) {
      batch = null;
      lastTranslationStatus = `${job.cancelled ? '已停止' : job.stoppedOnError ? '翻译中断' : '翻译结束'}：新增 ${job.done - job.failed} 段中文译文${job.failed ? `，${job.failed} 段失败，可在对应卡片重试或再次点击批量翻译` : ''}。`;
      refreshTranslations();
    }
  }
  const labels = {
    'run/begin': '运行开始', 'run/end': '运行结束', 'step/begin': '任务指令',
    'agent/session': '会话', 'agent/status': '状态', 'agent/thinking': '思考',
    'agent/tool_call': '工具调用', 'agent/tool_result': '独立返回',
    'agent/text': '模型回复', 'agent/final': '最终回复',
    'step/verifier': '当天验收', 'run/score': '最终评分',
  };
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function toast(text) {
    $('toast').textContent = text;
    $('toast').classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 1800);
  }
  async function copy(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        const textarea = el('textarea');
        textarea.value = text;
        textarea.style.cssText = 'position:fixed;opacity:0';
        document.body.append(textarea); textarea.select();
        const success = document.execCommand('copy');
        textarea.remove();
        if (!success) throw new Error('clipboard unavailable');
      }
      toast('已复制完整内容');
    } catch (_) { toast('复制失败，请选中文字手动复制'); }
  }
  function content(value, prose = false) {
    const text = M.format(value);
    const wrap = el('div');
    const pre = el('pre', 'content' + (prose ? ' prose' : ''), text);
    wrap.append(pre);
    if (text.length > 1200 || text.split('\n').length > 20) {
      const button = el('button', 'long-toggle');
      const set = open => {
        pre.classList.toggle('preview', !open);
        button.textContent = open ? '收起长内容 ↑' : `展开完整内容 ↓ · ${text.length.toLocaleString()} 字符`;
        button.setAttribute('aria-expanded', String(open));
      };
      set(expanded === true);
      button.addEventListener('click', () => set(pre.classList.contains('preview')));
      wrap.append(button);
    }
    return wrap;
  }
  function block(label, value, open = false, prose = false) {
    const details = el('details', 'block');
    details.open = expanded === null ? open : expanded;
    details.append(el('summary', '', label), content(value, prose));
    return details;
  }
  function sequence(entry) { return entry.event.seq ?? `行${entry.index + 1}`; }
  function seqLink(entry) {
    const a = el('a', 'sequence', `#${sequence(entry)}`);
    a.href = `#event-${entry.index}`;
    a.title = '定位此事件；链接中的位置按文件顺序编号';
    a.addEventListener('click', event => { event.preventDefault(); jump(entry.index); });
    return a;
  }
  function isBad(event) {
    return event.error != null || event.failed === true || event.isError === true ||
      ['failed', 'error', 'cancelled', 'canceled'].includes(String(event.status).toLowerCase()) ||
      (typeof event.exitCode === 'number' && event.exitCode !== 0);
  }
  function titleFor(item) {
    const e = item.entry.event;
    if (e.type === 'agent/tool_call') return e.input?.description || e.tool || '未命名工具';
    if (e.type === 'agent/status') return [e.phase, e.turn != null ? `turn ${e.turn}` : '', e.step != null ? `step ${e.step}` : ''].filter(Boolean).join(' · ');
    if (e.type === 'agent/session') return e.sessionId || '会话信息';
    if (e.type === 'step/begin') return `第 ${e.day ?? '?'} 天 · 任务与约束`;
    if (e.type === 'run/score') return '七天运行结算';
    if (e.type === 'step/verifier') return `第 ${e.day ?? '?'} 天 · 验收结果`;
    return labels[e.type] || e.type;
  }
  function rawBlock(entry, open = false) {
    return block(`原始事件 #${sequence(entry)} · ${entry.event.type}`, entry.event, open);
  }
  function renderCard(item, raw = false) {
    const entry = item.entry, e = entry.event;
    const card = el('article', `card ${M.category(e.type)}`);
    card.id = `event-${entry.index}`;
    card.dataset.index = entry.index;
    const head = el('div', 'card-head');
    head.append(el('span', 'badge', labels[e.type] || e.type),
      el('div', 'card-title' + (raw ? ' raw-type' : ''), raw ? e.type : titleFor(item)));
    if (!raw && e.type === 'agent/tool_call') {
      const bad = item.results.some(r => isBad(r.event));
      head.append(el('span', 'status' + (bad || !item.results.length ? ' bad' : ''),
        !item.results.length ? '未找到返回' : bad ? '返回异常' : `${item.results.length} 条返回`));
    }
    head.append(seqLink(entry));
    const body = el('div', 'card-body');
    if (raw) body.append(content(e));
    else {
      switch (e.type) {
        case 'agent/tool_call': {
          body.append(el('div', 'meta', `${e.tool || 'tool'} · ${e.callId ?? '无 callId'}`));
          const input = e.input;
          if (input && typeof input === 'object' && typeof input.command === 'string') {
            body.append(block('执行命令', input.command, true));
            body.append(block('完整调用参数', input));
          } else body.append(block('调用参数', input ?? null, true));
          item.results.forEach(result => {
            const event = result.event;
            const label = `返回 #${sequence(result)} · ${event.status ?? '未标注状态'}`;
            body.append(block(label, event.result ?? event.output ?? event, true));
          });
          break;
        }
        case 'agent/thinking': body.append(renderThought(entry)); break;
        case 'agent/text': case 'agent/final':
          body.append(content(e.text ?? e, true)); break;
        case 'step/begin':
          body.append(block('完整任务指令', e.instruction ?? '文件未记录指令', false, true)); break;
        case 'step/verifier':
          body.append(el('div', 'meta', `reward: ${e.reward ?? '未记录'} · 报表 day: ${e.report?.day ?? '未记录'}（按原文件展示）`));
          for (const [key, name] of [['state', '引擎状态'], ['report', '报表'], ['score', '评分']]) {
            if (e[key] != null) body.append(block(name, e[key]));
          }
          break;
        case 'run/score':
          if (e.score) body.append(block('评分分项', e.score, true));
          if (e.summary) body.append(block('运行汇总与每日台账', e.summary, true));
          break;
        default: body.append(content(e));
      }
      // This escape hatch retains every field, including unknown future schema fields.
      item.entries.forEach(event => body.append(rawBlock(event)));
    }
    const actions = el('div', 'body-actions');
    const copyButton = el('button', 'copy', raw ? '复制事件 JSON' : '复制完整卡片 JSON');
    copyButton.addEventListener('click', () => copy(raw ? JSON.stringify(e, null, 2) :
      item.entries.map(x => JSON.stringify(x.event)).join('\n')));
    actions.append(copyButton); body.append(actions); card.append(head, body);
    return card;
  }
  function getFiltered() {
    const query = $('search').value.trim().toLocaleLowerCase();
    const seqQuery = /^#(.+)$/.exec(query);
    const kind = $('kind').value;
    const items = mode === 'raw' ? model.entries.map(entry => ({entry, entries: [entry], kind: entry.event.type, system: entry.system,
      search: JSON.stringify(entry.event).toLocaleLowerCase()})) : model.items;
    return items.filter(item => {
      if (selectedDay !== 'all' && item.entry.segment !== selectedDay) return false;
      if (!query && mode !== 'raw' && kind !== 'system' && !$('show-system').checked && item.system) return false;
      if (kind !== 'all' && M.category(item.kind) !== kind) return false;
      if (seqQuery) return item.entries.some(e => String(sequence(e)).toLocaleLowerCase() === seqQuery[1]);
      return !query || item.search.includes(query) || item.entries.some(entry =>
        entry.event.type === 'agent/thinking' && (translations.get(entry.event.text) || '').toLocaleLowerCase().includes(query));
    });
  }
  function render() {
    const items = getFiltered();
    const fragment = document.createDocumentFragment();
    let last = null;
    for (const item of items) {
      const key = item.entry.segment;
      if (last !== key) {
        const heading = el('div', 'day-heading');
        const group = model.days.find(d => d.key === key);
        heading.append(el('h2', '', group ? `第 ${group.day} 天` : '运行信息'));
        if (group) heading.append(el('span', '', `${group.entries.length} events · ${group.items.filter(i => i.kind === 'agent/tool_call').length} calls`));
        fragment.append(heading); last = key;
      }
      fragment.append(renderCard(item, mode === 'raw'));
    }
    if (!items.length) {
      const empty = el('div', 'empty');
      empty.append(el('strong', '', '没有符合条件的事件'), el('span', '', '试试其他关键词，或点击「重置筛选」。'));
      fragment.append(empty);
    }
    $('timeline').replaceChildren(fragment);
    const represented = items.reduce((sum, item) => sum + item.entries.length, 0);
    $('count').textContent = `${items.length} 张卡片 · 涵盖 ${represented} / ${model.events.length} 条原始事件`;
    $('show-system').disabled = mode === 'raw';
    $('chain').classList.toggle('active', mode === 'chain');
    $('raw').classList.toggle('active', mode === 'raw');
    $('chain').setAttribute('aria-pressed', String(mode === 'chain'));
    $('raw').setAttribute('aria-pressed', String(mode === 'raw'));
    document.querySelectorAll('#days button').forEach(button => button.classList.toggle('active', button.dataset.day === selectedDay));
    const group = model.days.find(d => d.key === selectedDay);
    $('title').textContent = group ? `第 ${group.day} 天的调用链` : '完整调用链';
    focused = -1;
    translationControls();
  }
  function reset() {
    selectedDay = 'all'; $('search').value = ''; $('kind').value = 'all'; expanded = null;
  }
  function nav() {
    $('days').replaceChildren();
    function button(label, key, detail) {
      const b = el('button');
      b.dataset.day = key;
      b.append(el('span', '', label), el('small', '', detail));
      b.addEventListener('click', () => {
        selectedDay = key; render(); window.scrollTo({top: 0});
      });
      $('days').append(b);
    }
    button('完整运行', 'all', `${model.events.length} 条`);
    model.days.forEach(d => button(`第 ${d.day} 天`, d.key, `${d.items.filter(i => i.kind === 'agent/tool_call').length} calls`));
  }
  function load(events, name, path) {
    if (batch) batch.cancelled = true;
    batch = null; lastTranslationStatus = ''; thoughtModes.clear(); originalAll = false;
    model = M.build(events);
    reset(); $('show-system').checked = false; mode = 'chain';
    const begin = events.find(e => e.type === 'run/begin') || {};
    const scoreEvent = [...events].reverse().find(e => e.type === 'run/score');
    document.title = `${begin.run || name} · 调用链阅读器`;
    $('source').textContent = path || name;
    $('source').title = path || name;
    $('description').textContent = [begin.run, begin.model, begin.agent, begin.trial].filter(Boolean).join(' / ') || name;
    $('stats').replaceChildren();
    const stats = [[model.days.length, '天'], [events.length, '事件'],
      [events.filter(e => e.type === 'agent/tool_call').length, '工具调用'],
      [events.filter(e => e.type === 'agent/thinking').length, '思考片段']];
    if (scoreEvent?.score?.main_score != null) stats.push([scoreEvent.score.main_score, '最终得分']);
    if (scoreEvent?.summary?.spend_yuan != null) stats.push([`¥${Number(scoreEvent.summary.spend_yuan).toFixed(2)}`, '总花费']);
    for (const [value, label] of stats) {
      const stat = el('span'); stat.append(el('b', '', value), document.createTextNode(label)); $('stats').append(stat);
    }
    const issues = [];
    if (model.incomplete.length) issues.push(`${model.incomplete.length} 次调用未找到返回`);
    if (model.orphaned.length) issues.push(`${model.orphaned.length} 条返回未找到前序调用（已独立保留）`);
    if (!events.some(e => e.type === 'run/end')) issues.push('未记录 run/end，文件可能仍在写入或尚未结束');
    $('warning').textContent = issues.join('；');
    $('warning').classList.toggle('hidden', !issues.length);
    $('error').classList.add('hidden');
    $('jump-score').disabled = !scoreEvent;
    nav(); render();
  }
  function focusCard(card, scroll = true) {
    document.querySelector('.card.focused')?.classList.remove('focused');
    card.classList.add('focused');
    focused = [...document.querySelectorAll('.card')].indexOf(card);
    if (scroll) card.scrollIntoView({behavior: 'instant', block: 'start'});
  }
  function jump(index) {
    const entry = model.entries[index];
    if (!entry) return;
    let target = mode === 'raw' ? entry.index : entry.item.entry.index;
    let card = $(`event-${target}`);
    if (!card) {
      reset(); selectedDay = model.days.some(d => d.key === entry.segment) ? entry.segment : 'all';
      $('show-system').checked = true; render(); card = $(`event-${target}`);
    }
    if (!card) return;
    // Opening a result's link reveals the paired return and raw source, too.
    card.querySelectorAll('details').forEach(d => { d.open = true; });
    card.querySelectorAll('.long-toggle[aria-expanded="false"]').forEach(b => b.click());
    focusCard(card);
    try { history.replaceState(null, '', `#event-${index}`); } catch (_) { /* file URL restrictions */ }
  }
  async function openFiles(selected) {
    if (!selected.length) return;
    const generation = ++fileGeneration, imported = [], errors = [];
    for (const file of selected) {
      try {
        const events = M.parse(await file.text());
        const run = events.find(event => event.type === 'run/begin')?.run;
        const id = `local:${++localFileCounter}`;
        fileItems.set(id, {id, label: run ? `${run} · ${file.name}` : file.name,
          server: false, payload: {events, name: file.name}});
        imported.push(id);
      } catch (error) { errors.push(`${file.name}：${error.message}`); }
    }
    if (generation === fileGeneration) {
      if (imported.length) {
        const id = imported[0]; activateFile(fileItems.get(id).payload, id);
        window.scrollTo({top: 0}); toast(`已导入 ${imported.length} 个文件，可从顶部列表切换`);
      } else renderFileList();
      if (errors.length) {
        $('error').textContent = `以下文件读取失败：${errors.join('；')}${imported.length ? '' : '。当前文件保持不变。'}`;
        $('error').classList.remove('hidden'); window.scrollTo({top: 0});
      }
    } else renderFileList();
    $('file').value = '';
  }
  $('open-file').addEventListener('click', () => $('file').click());
  $('file-select').addEventListener('change', event => selectFile(event.target.value));
  $('refresh-files').addEventListener('click', () => refreshFiles(true));
  $('translate-all').addEventListener('click', translateAll);
  $('toggle-original').addEventListener('click', () => { originalAll = !originalAll; thoughtModes.clear(); refreshTranslations(); });
  $('stop-translation').addEventListener('click', () => { if (batch) batch.cancelled = true; translationControls(); });
  $('file').addEventListener('change', () => openFiles(Array.from($('file').files)));
  $('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(render, 130); });
  $('kind').addEventListener('change', render);
  $('show-system').addEventListener('change', render);
  for (const value of ['chain', 'raw']) $(value).addEventListener('click', () => { mode = value; expanded = null; render(); });
  $('reset').addEventListener('click', () => { reset(); $('show-system').checked = false; render(); });
  $('expand').addEventListener('click', () => { expanded = true; render(); });
  $('collapse').addEventListener('click', () => { expanded = false; render(); });
  $('jump-score').addEventListener('click', () => {
    const entry = [...model.entries].reverse().find(e => e.event.type === 'run/score');
    if (entry) jump(entry.index);
  });
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    const editing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
    if (event.key === 'Escape') { $('search').value = ''; $('search').blur(); render(); }
    if (editing) return;
    if (event.key === '/') { event.preventDefault(); $('search').focus(); }
    if (event.key === 'j' || event.key === 'k') {
      const cards = [...document.querySelectorAll('.card')];
      if (!cards.length) return;
      event.preventDefault();
      const next = focused < 0 ? (event.key === 'j' ? 0 : cards.length - 1) :
        Math.max(0, Math.min(cards.length - 1, focused + (event.key === 'j' ? 1 : -1)));
      focusCard(cards[next]);
    }
  });
  let dragDepth = 0;
  document.addEventListener('dragenter', event => {
    if (!Array.from(event.dataTransfer.types).includes('Files')) return;
    event.preventDefault(); dragDepth++; $('drop-overlay').classList.remove('hidden');
  });
  document.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $('drop-overlay').classList.add('hidden');
  });
  document.addEventListener('dragover', event => event.preventDefault());
  document.addEventListener('drop', event => {
    event.preventDefault(); dragDepth = 0; $('drop-overlay').classList.add('hidden');
    openFiles(Array.from(event.dataTransfer.files));
  });
  window.addEventListener('hashchange', () => {
    const match = /^#event-(\d+)$/.exec(location.hash);
    if (match) jump(Number(match[1]));
  });
  try {
    const data = JSON.parse($('event-data').textContent);
    const id = data.fileId || 'local:initial';
    fileItems.set(id, {id, label: data.path || data.name, server: !!data.fileId && location.protocol !== 'file:', payload: data});
    activateFile(data, id, false);
    refreshFiles();
    const match = /^#event-(\d+)$/.exec(location.hash);
    if (match) requestAnimationFrame(() => jump(Number(match[1])));
  } catch (error) {
    $('error').textContent = `初始化失败：${error.message}`;
    $('error').classList.remove('hidden');
  }
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(entries => {
      document.documentElement.style.setProperty('--header-height', `${entries[0].target.getBoundingClientRect().height}px`);
    }).observe(document.querySelector('header'));
  }
  if (location.protocol !== 'file:') {
    fetch('/api/translation-status').then(response => response.json()).then(data => {
      translationAvailable = data.available === true;
      if (translationAvailable) $('translation-note').textContent = `${data.service || 'Google 翻译'} · 仅发送思考原文，译文缓存在本机；已有译文可离线阅读。`;
      else $('translation-note').textContent = '翻译服务未配置。可查看已有译文；新增翻译需配置本机翻译服务。';
    }).catch(() => {
      translationAvailable = false;
      $('translation-note').textContent = '已有译文可离线阅读；新增翻译请用 --serve 启动本机阅读器。';
    });
  }
})();
