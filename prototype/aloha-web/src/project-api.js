/* Versioned HTTP client. Tokens live in this instance, never browser storage. */
((root) => {
  class ProjectAPI {
    constructor({baseUrl, token, fetcher = globalThis.fetch}) {
      const url = new URL(baseUrl);
      if (url.username || url.password || url.search || url.hash ||
          !['http:', 'https:'].includes(url.protocol) ||
          (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) {
        throw Error('API 주소는 HTTPS 또는 이 PC의 localhost 주소를 사용하세요.');
      }
      if (url.pathname !== '/' && url.pathname !== '/api/v1' && url.pathname !== '/api/v1/') {
        throw Error('API 서버의 기본 주소를 입력하세요.');
      }
      this.base = url.origin + '/api/v1';
      this.token = token;
      this.fetcher = fetcher;
    }
    async request(path, {method = 'GET', body, raw = false, headers = {}, blob = false} = {}) {
      if (!path.startsWith('/') || path.startsWith('//') || /[\r\n]/.test(path)) throw Error('잘못된 API 경로입니다.');
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 60000);
      try {
        const response = await this.fetcher(this.base + path, {
          method, signal: controller.signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
          headers: {Authorization: 'Bearer ' + this.token, ...(!raw && body ? {'Content-Type': 'application/json'} : {}), ...headers},
          body: body === undefined ? undefined : raw ? body : JSON.stringify(body)
        });
        if (!response.ok) {
          let data;
          try { data = await response.json(); } catch {}
          const message = Array.isArray(data?.detail) ? data.detail.map(e => e.msg).join(' / ') : data?.detail;
          const error = Error(message || 'API 요청을 처리하지 못했습니다. (' + response.status + ')');
          error.status = response.status;
          throw error;
        }
        return blob ? response.blob() : response.json();
      } catch (error) {
        if (error.name === 'AbortError') throw Error('응답 시간이 초과되었습니다. 입력은 그대로 두고 다시 시도하세요.');
        throw error;
      } finally { clearTimeout(timer); }
    }
    site(code) { return '/sites/' + encodeURIComponent(code); }
    schedule(code, id) { return this.site(code) + '/schedules/' + encodeURIComponent(id); }
    task(code, schedule, id) { return this.schedule(code, schedule) + '/tasks/' + encodeURIComponent(id); }
    me() { return this.request('/me'); }
    sites() { return this.request('/sites'); }
    schedules(code) { return this.request(this.site(code) + '/schedules'); }
    overview(code) { return this.request(this.site(code) + '/overview'); }
    participants(code) { return this.request(this.site(code) + '/participants'); }
    fieldHome(code, schedule, weekStart = '') {
      return this.request(this.schedule(code, schedule) + '/field-home' + (weekStart ? '?week_start=' + encodeURIComponent(weekStart) : ''));
    }
    taskDetail(code, schedule, id, weekStart = '') {
      return this.request(this.task(code, schedule, id) + (weekStart ? '?week_start=' + encodeURIComponent(weekStart) : ''));
    }
    tasks(code, schedule, weekStart = '') {
      return this.request(this.schedule(code, schedule) + '/tasks' + (weekStart ? '?week_start=' + encodeURIComponent(weekStart) : ''));
    }
    classify(code, schedule, id, body) { return this.request(this.task(code, schedule, id) + '/classification', {method: 'PATCH', body}); }
    history(code, schedule, id) { return this.request(this.task(code, schedule, id) + '/history'); }
    submit(code, schedule, id, body) { return this.request(this.task(code, schedule, id) + '/submissions', {method: 'POST', body}); }
    review(code, schedule, id, body) { return this.request(this.task(code, schedule, id) + '/reviews', {method: 'POST', body}); }
    changeRequests(code, schedule) { return this.request(this.schedule(code, schedule) + '/change-requests'); }
    createChange(code, schedule, id, body) { return this.request(this.task(code, schedule, id) + '/change-requests', {method: 'POST', body}); }
    requestDetail(code, schedule, id) { return this.request(this.schedule(code, schedule) + '/change-requests/' + encodeURIComponent(id)); }
    message(code, schedule, id, body) { return this.request(this.schedule(code, schedule) + '/change-requests/' + encodeURIComponent(id) + '/messages', {method: 'POST', body}); }
    decide(code, schedule, id, body) { return this.request(this.schedule(code, schedule) + '/change-requests/' + encodeURIComponent(id) + '/decision', {method: 'POST', body}); }
    reportInput(code, schedule, week) { return this.request(this.schedule(code, schedule) + '/report-input?week_start=' + encodeURIComponent(week)); }
    createReport(code, schedule, body) { return this.request(this.schedule(code, schedule) + '/reports', {method: 'POST', body}); }
    reports(code, schedule) { return this.request(this.schedule(code, schedule) + '/reports'); }
    decideReport(code, id, body) { return this.request(this.site(code) + '/reports/' + encodeURIComponent(id) + '/decision', {method: 'POST', body}); }
    async upload(code, file, {id, purpose = 'photo'} = {}) {
      if (file.size <= 0 || file.size > (purpose === 'photo' ? 20 : 10) * 1024 * 1024) {
        throw Error('사진은 20MB, 첨부파일은 10MB 이하의 빈 파일이 아닌 자료를 선택하세요.');
      }
      if (file.name.length > 160) throw Error('파일 이름을 160자 이하로 줄여 주세요.');
      id ||= crypto.randomUUID();
      const bytes = await file.arrayBuffer();
      const checksum = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
      const filename = file.name.replace(/[^\p{L}\p{N}_. ()-]/gu, '_');
      const query = new URLSearchParams({file_id: id, filename, purpose});
      return this.request(this.site(code) + '/files?' + query, {method: 'POST', raw: true, body: bytes,
        headers: {'X-Content-SHA256': checksum, 'Content-Type': file.type || 'application/octet-stream'}});
    }
    file(code, id) { return this.request(this.site(code) + '/files/' + encodeURIComponent(id), {blob: true}); }
  }
  if (typeof module !== 'undefined') module.exports = ProjectAPI;
  else root.ProjectAPI = ProjectAPI;
})(typeof window === 'undefined' ? globalThis : window);
