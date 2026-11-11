'use strict';

const ALLOWED_GROUPS = Object.freeze([1, 3, 11]);

function normalizeMonitorGroups(groups) {
  if (!Array.isArray(groups) || groups.length === 0) {
    throw new Error('Seleciona pelo menos um grupo para monitorização.');
  }
  if (groups.some(group => !Number.isInteger(group) || !ALLOWED_GROUPS.includes(group))) {
    throw new Error('Monitorização limitada aos grupos 001, 003 e 011.');
  }
  if (new Set(groups).size !== groups.length) {
    throw new Error('Não repitas grupos na monitorização.');
  }
  return ALLOWED_GROUPS.filter(group => groups.includes(group));
}

function catalogSummary(summary = {}) {
  const count = value => Number.isFinite(value)
    ? value.toLocaleString('pt-PT').replace(/[\u00a0\u202f]/g, ' ')
    : '0';
  return `${count(summary.files_scanned)} labels · ${count(summary.metadata_rows)} linhas · ${count(summary.warning_count)} avisos`;
}

function candidatePresentation(candidate = {}) {
  const ecu = Array.isArray(candidate.ecu_candidates) ? candidate.ecu_candidates.filter(Boolean) : [];
  const compatibility = candidate.exact_afn_label
    ? 'Label AFN exato no ficheiro local'
    : `${ecu.length ? `Candidato ${ecu.join(', ')}` : 'Compatibilidade não determinada'} · AFN não confirmado`;
  return {
    value: candidate.status === 'METADATA_ONLY' || !candidate.decoder_id || !candidate.live_readable
      ? 'Metadados apenas'
      : 'Valor live possível · não validado na ECU',
    compatibility,
    source: `${candidate.source_file || 'Origem desconhecida'}${candidate.source_line ? `:${candidate.source_line}` : ''}`,
    decoder: candidate.decoder_id || 'Sem decoder',
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { normalizeMonitorGroups, catalogSummary, candidatePresentation };
}
if (typeof window !== 'undefined') {
  window.FISDiagnosticCatalog = { normalizeMonitorGroups, catalogSummary, candidatePresentation };
}
