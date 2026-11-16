((root) => {
  const ProjectAPI = typeof module === 'undefined' ? root.ProjectAPI : require('./project-api.js');

  class ProjectToolsAPI extends ProjectAPI {
    constructor({fetcher = (...args) => root.fetch(...args), ...config}) {
      super({...config, fetcher});
    }

    reportRows(site, schedule) {
      return this.request('/weekly' + this.schedule(site, schedule) + '/reports');
    }

    requestReport(site, schedule, week, mode, id) {
      return this.request('/weekly' + this.schedule(site, schedule) + '/reports', {
        method: 'POST', body: {id, week_start: week, mode}
      });
    }

    chatHistory(site, schedule, week) {
      return this.request(this.schedule(site, schedule) + '/copilot/history?week_start=' + encodeURIComponent(week));
    }

    ask(site, schedule, week, message, id) {
      return this.request(this.schedule(site, schedule) + '/copilot/ask', {
        method: 'POST', body: {request_id: id, week_start: week, message}
      });
    }
  }

  if (typeof module === 'undefined') root.ProjectToolsAPI = ProjectToolsAPI;
  else module.exports = ProjectToolsAPI;
})(globalThis);
