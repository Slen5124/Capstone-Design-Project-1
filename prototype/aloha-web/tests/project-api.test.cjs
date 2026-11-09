const assert = require('node:assert/strict');
const ProjectAPI = require('../src/project-api.js');
(async () => {
  for (const baseUrl of ['http://example.com', 'https://user:pass@example.com', 'https://example.com/?token=x', 'https://example.com/other']) {
    assert.throws(() => new ProjectAPI({baseUrl, token: 'test'}));
  }
  const calls = [];
  const api = new ProjectAPI({baseUrl: 'http://127.0.0.1:8001', token: 'test', fetcher: async (url, options) => {
    calls.push({url, options}); return {ok: true, json: async () => ({ok: true})};
  }});
  await api.fieldHome('example', 'schedule-id', '2025-02-17');
  assert.equal(calls[0].url, 'http://127.0.0.1:8001/api/v1/sites/example/schedules/schedule-id/field-home?week_start=2025-02-17');
  await api.submit('example', 'schedule-id', 'task-id', {id: 'retry-id', progress_percent: 75});
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(calls[1].options.headers.Authorization, 'Bearer test');
  assert.equal(calls[1].options.credentials, 'omit');
  assert.equal(calls[1].options.redirect, 'error');
  assert.equal(JSON.parse(calls[1].options.body).progress_percent, 75);
  const failed = new ProjectAPI({baseUrl: 'https://example.com', token: 'test', fetcher: async () => ({ok: false, status: 409, json: async () => ({detail: '최신 제출을 확인하세요.'})})});
  await assert.rejects(() => failed.me(), e => e.status === 409 && e.message.includes('최신 제출'));
  await assert.rejects(() => api.request('//other.example/path'));
  console.log('PASS: API URL validation, authenticated scoped requests, JSON payloads and HTTP errors');
})();
