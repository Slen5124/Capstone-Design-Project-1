'use strict';

const el = id => document.getElementById(id);
let api;
let sites = [];
let pending;
let pendingQuestion;
const scope = () => [el('site').value, el('schedule').value, el('week').value];

function options(select, rows, value, label) {
  select.replaceChildren(...rows.map(row => new Option(row[label], row[value])));
}

async function run(action) {
  if (pending) return;
  pending = true;
  const controls = [...document.querySelectorAll('button, select, input, textarea')].filter(node => !node.disabled);
  controls.forEach(node => { node.disabled = true; });
  el('status').textContent = '처리 중입니다.';
  try {
    await action();
    el('status').textContent = '';
  } catch (error) {
    el('status').textContent = error.message || '요청을 처리하지 못했습니다.';
  } finally {
    controls.forEach(node => { node.disabled = false; });
    const isHq = sites.some(site => site.code === el('site').value && site.role === 'hq');
    el('draft').disabled = el('final').disabled = !isHq;
    pending = false;
  }
}

function showTurn(turn) {
  const article = document.createElement('article');
  article.textContent = [turn.question, turn.answer, (turn.sources || []).join(' · ')].filter(Boolean).join('\n\n');
  el('history').append(article);
}

function clearResults() {
  el('reports').replaceChildren();
  el('history').replaceChildren();
}

async function selectScope() {
  clearResults();
  const [site, schedule, week] = scope();
  el('tools').hidden = !(site && schedule && week);
  if (el('tools').hidden) return;
  const history = await api.chatHistory(site, schedule, week);
  history.turns.forEach(showTurn);
}

async function selectSite() {
  clearResults();
  const site = el('site').value;
  options(el('schedule'), site ? await api.schedules(site) : [], 'id', 'version');
  await selectScope();
}

async function refreshReports() {
  const [site, schedule, week] = scope();
  const {items} = await api.reportRows(site, schedule);
  const list = items.filter(item => item.period_start === week);
  const rows = list.map(item => {
    const li = document.createElement('li');
    li.textContent = `${item.mode === 'final' ? '최종본' : '초안'} · ${item.status} · ${item.revision}차 `;
    if (item.report_file_id) {
      const pdf = document.createElement('button');
      pdf.type = 'button';
      pdf.textContent = 'PDF 다운로드';
      pdf.onclick = () => run(async () => {
        const url = URL.createObjectURL(await api.file(site, item.report_file_id));
        const link = document.createElement('a');
        link.href = url;
        link.download = 'weekly-report.pdf';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      li.append(pdf);
    }
    return li;
  });
  if (!rows.length) {
    const empty = document.createElement('li');
    empty.textContent = '이 주차의 보고서가 없습니다.';
    rows.push(empty);
  }
  el('reports').replaceChildren(...rows);
}

el('connect').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    el('project').disabled = true;
    el('tools').hidden = true;
    pendingQuestion = undefined;
    clearResults();
    api = new ProjectToolsAPI({baseUrl: el('api-url').value, token: el('token').value.trim()});
    el('token').value = '';
    sites = await api.sites();
    options(el('site'), sites, 'code', 'name');
    el('project').disabled = false;
    await selectSite();
  });
};
el('site').onchange = () => run(selectSite);
el('schedule').onchange = el('week').onchange = () => run(selectScope);
el('refresh').onclick = () => run(refreshReports);
for (const mode of ['draft', 'final']) {
  el(mode).onclick = () => run(async () => {
    await api.requestReport(...scope(), mode, crypto.randomUUID());
    await refreshReports();
  });
}
el('ask').onsubmit = event => {
  event.preventDefault();
  const question = el('message').value.trim();
  if (!question) return;
  run(async () => {
    const request = [...scope(), question];
    const key = JSON.stringify(request);
    if (pendingQuestion?.key !== key) pendingQuestion = {key, id: crypto.randomUUID()};
    const answer = await api.ask(...request, pendingQuestion.id);
    pendingQuestion = undefined;
    showTurn(answer);
    el('message').value = '';
  });
};
