'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { formatDiagnosticRaw } = require('../web/diagnostic-raw.js');
const { normalizeMonitorGroups, catalogSummary, candidatePresentation } = require('../web/diagnostic-catalog.js');
const fs = require('node:fs');
const path = require('node:path');

test('group controls describe single reads and retain the stop mode', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  assert.match(html, /id="diagnostic-read-group"[^>]*>Ler grupo<\/button>/);
  assert.match(html, /id="diagnostic-groups"[^>]*aria-label="Grupo de medição para leitura única"/);
  assert.match(html, /data-mode="idle"[^>]*>Parar<\/button>/);
});

test('VAG KW1281 connection defaults to 9600 baud and keeps manual alternatives', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  assert.match(html, /<option value="9600" selected>9600 · predefinido AFN<\/option>/);
  assert.match(html, /<option value="10400">10400<\/option>/);
  assert.match(html, /<option value="4800">4800<\/option>/);
});

test('formats only allowed group byte responses for the diagnostic panel', () => {
  assert.equal(
    formatDiagnosticRaw({ group_001_raw: '0c1020', group_003_raw: 'AABB' }),
    'Grupo 001: 0C 10 20 · Grupo 003: AA BB',
  );
});

test('hides unsupported and malformed raw values', () => {
  assert.equal(formatDiagnosticRaw({ group_004_raw: '0102', group_011_raw: 'xyz' }), 'Sem grupo lido');
});

test('shows an empty state when no physical group has been read', () => {
  assert.equal(formatDiagnosticRaw(null), 'Sem grupo lido');
});

test('monitor controls are separate, manual, and contain only the allowed groups', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  assert.match(html, /id="monitor-start"/);
  assert.match(html, /id="monitor-stop"/);
  assert.match(html, /id="monitor-groups"/);
  assert.match(html, /id="monitor-interval"/);
  assert.doesNotMatch(html, /data-monitor-autostart/);
  assert.equal(normalizeMonitorGroups([11, 1, 3]).join(','), '1,3,11');
});

test('monitor group selection rejects unknown, duplicate, and empty groups', () => {
  assert.throws(() => normalizeMonitorGroups([]), /Seleciona/);
  assert.throws(() => normalizeMonitorGroups([1, 1]), /repitas/);
  assert.throws(() => normalizeMonitorGroups([2]), /001, 003 e 011/);
});

test('catalog summary distinguishes active groups from metadata and unknown groups', () => {
  assert.equal(catalogSummary({ files_scanned: 558, metadata_rows: 30393, warning_count: 2 }),
    '558 labels · 30 393 linhas · 2 avisos');
});

test('label candidate presentation never treats metadata as a live reading', () => {
  const view = candidatePresentation({
    group_id: 2, field_id: 1, name: 'Manifold pressure', unit: 'mbar',
    range_text: '900...2200 mbar', status: 'METADATA_ONLY', decoder_id: null,
    live_readable: false, source_file: '038-906-012-AGR.lbl', source_line: 45,
    ecu_candidates: ['AGR', 'ALH'], exact_afn_label: false,
  });
  assert.equal(view.value, 'Metadados apenas');
  assert.equal(view.compatibility, 'Candidato AGR, ALH · AFN não confirmado');
  assert.match(view.source, /038-906-012-AGR.lbl:45/);
  assert.equal(view.decoder, 'Sem decoder');
});
