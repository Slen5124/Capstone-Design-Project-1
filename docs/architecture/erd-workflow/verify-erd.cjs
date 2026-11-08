'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'ALOHA_ERD_업무흐름.html'), 'utf8');
const definition = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/);
assert.ok(definition, 'Embedded schema is required');
const data = JSON.parse(definition[1]);
const tables = new Map(data.tables.map((table) => [table.id, table]));

assert.equal(tables.size, 20);
assert.equal(data.tables.length, tables.size, 'Table IDs must be unique');
assert.equal(data.flows.length, 12);

for (const table of data.tables) {
  const fields = new Set(table.fields.map((field) => field.name));
  assert.equal(fields.size, table.fields.length, `Duplicate fields in ${table.id}`);
  assert.ok(table.fields.every((field) => field.key && field.use));
}

for (const [parent, child, foreignKey] of data.edges) {
  assert.ok(tables.has(parent), `Missing parent ${parent}`);
  assert.ok(tables.has(child), `Missing child ${child}`);
  const childFields = new Set(tables.get(child).fields.map((field) => field.name));
  for (const field of foreignKey.split(/, | \/ /)) {
    assert.ok(childFields.has(field), `Missing FK ${child}.${field}`);
  }
}

for (const flow of data.flows) {
  for (const tableId of [...flow.reads, ...flow.writes]) {
    assert.ok(tables.has(tableId), `Unknown table in ${flow.name}`);
  }
  assert.ok(flow.inputs && flow.processing && flow.outputs && flow.guard);
}

const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
for (const script of scripts) {
  if (!script[0].includes('application/json')) new vm.Script(script[1]);
}

console.log(`Verified ${tables.size} tables, ${data.edges.length} relations and ${data.flows.length} workflows.`);
