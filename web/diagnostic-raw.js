'use strict';

function formatDiagnosticRaw(rawValues) {
  if (!rawValues || typeof rawValues !== 'object') return 'Sem grupo lido';

  const rows = Object.entries(rawValues).flatMap(([key, raw]) => {
    const match = /^group_(001|003|011)_raw$/.exec(key);
    if (!match || typeof raw !== 'string' || !/^(?:[0-9a-f]{2})*$/i.test(raw)) return [];
    const bytes = raw.match(/.{2}/g)?.join(' ').toUpperCase();
    return [`Grupo ${match[1]}: ${bytes || 'Sem bytes'}`];
  });

  return rows.join(' · ') || 'Sem grupo lido';
}

if (typeof module !== 'undefined' && module.exports) module.exports = { formatDiagnosticRaw };
