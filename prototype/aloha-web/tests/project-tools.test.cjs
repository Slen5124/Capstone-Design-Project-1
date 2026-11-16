const assert = require('node:assert/strict');
const ProjectToolsAPI = require('../src/project-tools-api.js');

(async () => {
  const requests = [];
  const api = new ProjectToolsAPI({
    baseUrl: 'http://localhost:8002',
    token: 'synthetic-fixture',
    fetcher: async (url, options) => {
      requests.push({url: new URL(url), ...options});
      return new Response('{}');
    }
  });
  await api.requestReport('site', 'schedule', '2025-02-17', 'draft', 'one');
  await api.reportRows('site', 'schedule');
  await api.chatHistory('site', 'schedule', '2025-02-17');
  await api.ask('site', 'schedule', '2025-02-17', '진행률', 'two');
  assert.deepEqual(requests.map(({url}) => url.pathname), [
    '/api/v1/weekly/sites/site/schedules/schedule/reports',
    '/api/v1/weekly/sites/site/schedules/schedule/reports',
    '/api/v1/sites/site/schedules/schedule/copilot/history',
    '/api/v1/sites/site/schedules/schedule/copilot/ask'
  ]);
  assert.equal(requests[2].url.searchParams.get('week_start'), '2025-02-17');
  assert.deepEqual(JSON.parse(requests[0].body), {id:'one', week_start:'2025-02-17', mode:'draft'});
  assert.deepEqual(JSON.parse(requests[3].body), {request_id:'two', week_start:'2025-02-17', message:'진행률'});
  assert.ok(requests.every(r => r.credentials === 'omit' && r.redirect === 'error'));
})().catch(error => { console.error(error); process.exitCode = 1; });
