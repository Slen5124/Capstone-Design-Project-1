const assert = require('node:assert/strict');
const { catalogue, mappable } = require('../src/site-catalogue.js');

const sites = [
  { code: 'site-a', name: 'Example A', has_schedule: true, completed: false, latitude: 10, longitude: 20 },
  { code: 'site-b', name: 'Example B', has_schedule: true, completed: true, latitude: null, longitude: null },
  { code: 'site-c', name: 'Example C', has_schedule: false },
  { code: 'demo-site', name: 'Example D', has_schedule: true },
];

const result = catalogue(sites, ['demo-site']);
assert.deepEqual(result.map(site => site.id), ['site-a', 'site-b']);
assert.equal(result[0].completed, false);
assert.equal(result[1].completed, true);
assert.equal(mappable(result[0]), true);
assert.equal(mappable(result[1]), false);
assert.equal(mappable({ lat: 91, lon: 0 }), false);
assert.equal(mappable({ lat: 0, lon: 181 }), false);
console.log('PASS: scheduled sites, completion status, exclusions and map coordinates');
