'use strict';

const $ = (id) => document.getElementById(id);

function fmt(n, currency = 'USD') {
  if (n == null || isNaN(n)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function escHtml(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

let selectedFiles = [];
let fullResults = null;
let sessionId = crypto.randomUUID();

const PIPELINE_ORDER = [
  'visionAnalyst',
  'contextParser',
  'takeoffEngine',
  'estimationAgent',
  'validation',
];

const PIPELINE_DISPLAY = {
  visionAnalyst: 'Vision Analyst',
  contextParser: 'Context Parser',
  takeoffEngine: 'Take-off Engine',
  estimationAgent: 'Estimation Agent',
  validation: 'Validation',
};

let pipelineTimerId = null;
let pipelineStartedAt = 0;
const agentStatuses = {};
const lastActivityByAgent = {};

let pipelinePlaybook = {};
let agentTraces = {};
let agentOutcomes = {};
let focusedAgent = null;
let documentTrayReady = false;

let documentWalkSteps = [];
let documentWalkIndex = -1;
let docWalkAutoFollow = true;

const DOC_RAIL_ORDER = ['document', ...PIPELINE_ORDER];

document.addEventListener('DOMContentLoaded', () => {
  initUpload();
  initTabs();
  initExports();
  initNewEstimate();
  initCopyOffer();
  loadConfig();
  loadPipelineGuide();
  initDocumentWalker();
});

function initUpload() {
  const dropzone = $('dropzone');
  const fileInput = $('fileInput');
  const runBtn = $('runBtn');
  const clearBtn = $('clearFileBtn');

  if (!dropzone || !fileInput || !runBtn) {
    console.error('Upload UI elements missing');
    return;
  }

  fileInput.addEventListener('change', () => {
    if (fileInput.files?.length) {
      addFiles(Array.from(fileInput.files));
    }
    fileInput.value = '';
  });

  dropzone.addEventListener('click', (e) => {
    if (e.target.closest('label[for="fileInput"]')) return;
    fileInput.click();
  });

  dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      fileInput.click();
    }
  });

  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('drag-over');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    if (e.dataTransfer?.files?.length) {
      addFiles(Array.from(e.dataTransfer.files));
    }
  });

  clearBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    clearFiles();
  });

  runBtn.addEventListener('click', startPipeline);
}

function fileKey(file) {
  return `${file.name}\0${file.size}\0${file.lastModified}`;
}

function addFiles(incoming) {
  const seen = new Set(selectedFiles.map(fileKey));
  const next = selectedFiles.slice();
  for (const file of incoming) {
    if (!file) continue;
    const key = fileKey(file);
    if (seen.has(key)) continue;
    seen.add(key);
    next.push(file);
  }
  setFiles(next);
}

function setFiles(files) {
  selectedFiles = files.filter(Boolean);
  const list = $('fileList');
  const clearBtn = $('clearFileBtn');
  const runBtn = $('runBtn');

  if (!selectedFiles.length) {
    if (list) list.hidden = true;
    if (clearBtn) clearBtn.hidden = true;
    if (runBtn) runBtn.disabled = true;
    return;
  }

  if (!list) {
    if (runBtn) runBtn.disabled = false;
    return;
  }

  list.innerHTML = selectedFiles.map((f, i) => `
    <li>
      <span class="file-info">
        <strong>${escHtml(f.name)}</strong>
        <span class="file-size-badge">${(f.size / 1024 / 1024).toFixed(2)} MB</span>
      </span>
      <button type="button" class="btn btn-ghost btn-sm" data-remove="${i}" aria-label="Remove file">✕</button>
    </li>`).join('');

  if (list) list.hidden = false;
  if (clearBtn) {
    clearBtn.hidden = false;
    const count = selectedFiles.length;
    clearBtn.textContent = `Clear ${count} file${count === 1 ? '' : 's'}`;
  }
  if (runBtn) runBtn.disabled = false;
  hideError();

  list.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const idx = Number(btn.getAttribute('data-remove'));
      selectedFiles.splice(idx, 1);
      setFiles(selectedFiles);
      $('fileInput').value = '';
    });
  });
}

function clearFiles() {
  selectedFiles = [];
  const fileInput = $('fileInput');
  if (fileInput) fileInput.value = '';
  $('fileList').hidden = true;
  $('clearFileBtn').hidden = true;
  $('runBtn').disabled = true;
}

async function loadConfig() {
  try {
    const resp = await fetch('/api/config');
    if (!resp.ok) return;
    const { provider, model } = await resp.json();
    const el = $('headerStatus');
    if (el && provider) {
      el.textContent = 'Take-off from source sheets';
    }
  } catch {
    /* ignore */
  }
}

async function loadPipelineGuide() {
  try {
    const resp = await fetch('/api/pipeline-guide');
    if (resp.ok) {
      const body = await resp.json();
      pipelinePlaybook = body.agents || {};
    }
  } catch {
    /* ignore */
  }
  buildDocRail();
}

function buildDocRail() {
  const rail = $('docEngineRail');
  if (!rail) return;
  rail.innerHTML = DOC_RAIL_ORDER.map((id) => {
    const pb = pipelinePlaybook[id] || {};
    const title = pb.title || (id === 'document' ? 'Files' : (PIPELINE_DISPLAY[id] || id));
    const step = id === 'document' ? '▶' : (pb.step || '—');
    return `<button type="button" class="doc-rail-btn" data-agent="${id}" disabled>
      <span class="doc-rail-step">${escHtml(String(step))}</span>
      <span class="doc-rail-name">${escHtml(title)}</span>
    </button>`;
  }).join('');

  rail.querySelectorAll('.doc-rail-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-agent');
      if (btn.disabled) return;
      showSpotlightReview(id);
    });
  });
}

function initDocumentWalker() {
  $('docWalkPrev')?.addEventListener('click', () => {
    docWalkAutoFollow = false;
    $('docWalkAutoFollow').checked = false;
    if (documentWalkIndex > 0) {
      documentWalkIndex -= 1;
      renderDocumentWalk();
    }
  });
  $('docWalkNext')?.addEventListener('click', () => {
    docWalkAutoFollow = false;
    $('docWalkAutoFollow').checked = false;
    if (documentWalkIndex < documentWalkSteps.length - 1) {
      documentWalkIndex += 1;
      renderDocumentWalk();
    }
  });
  $('docWalkAutoFollow')?.addEventListener('change', (e) => {
    docWalkAutoFollow = e.target.checked;
    if (docWalkAutoFollow && documentWalkSteps.length) {
      documentWalkIndex = documentWalkSteps.length - 1;
      renderDocumentWalk();
    }
  });
}

function resetDocumentWalk() {
  documentWalkSteps = [];
  documentWalkIndex = -1;
  docWalkAutoFollow = true;
  const follow = $('docWalkAutoFollow');
  if (follow) follow.checked = true;
  renderDocumentWalk();
}

function pushDocumentStep({ agent, message, data }) {
  const d = data || {};
  documentWalkSteps.push({
    agent: agent || 'document',
    message: message || '',
    heading: d.heading || message || 'Document',
    excerpt: d.excerpt || '',
    sourceFile: d.sourceFile,
    sourceSheet: d.sourceSheet,
    sourcePage: d.sourcePage,
    kind: d.kind || 'excerpt',
  });
  if (docWalkAutoFollow) {
    documentWalkIndex = documentWalkSteps.length - 1;
  } else if (documentWalkIndex < 0) {
    documentWalkIndex = 0;
  }
  renderDocumentWalk();
}

function formatWalkSource(step) {
  if (!step) return '—';
  const parts = [];
  if (step.sourceFile) parts.push(step.sourceFile);
  if (step.sourceSheet) parts.push(`Sheet: ${step.sourceSheet}`);
  if (step.sourcePage != null) parts.push(`Page ${step.sourcePage}`);
  return parts.length ? parts.join(' · ') : 'Project document';
}

function agentLabel(agentId) {
  if (agentId === 'document') return 'Ingest';
  return PIPELINE_DISPLAY[agentId] || agentId;
}

function renderDocumentWalk() {
  const pos = $('docWalkPosition');
  const total = documentWalkSteps.length;
  if (pos) {
    pos.textContent = total
      ? `Document step ${documentWalkIndex + 1} of ${total}`
      : 'Document step — waiting';
  }

  const prev = $('docWalkPrev');
  const next = $('docWalkNext');
  if (prev) prev.disabled = documentWalkIndex <= 0;
  if (next) next.disabled = documentWalkIndex < 0 || documentWalkIndex >= total - 1;

  const step = documentWalkSteps[documentWalkIndex];
  const agentEl = $('docWalkAgent');
  const sourceEl = $('docWalkSource');
  const headEl = $('docWalkHeading');
  const capEl = $('docWalkCaption');
  const bodyEl = $('docWalkExcerpt');

  if (!step) {
    if (agentEl) agentEl.textContent = '—';
    if (sourceEl) sourceEl.textContent = '—';
    if (headEl) headEl.textContent = 'Walk your project document step by step';
    if (capEl) capEl.textContent = 'Run Estimate to stream each file, sheet, and passage as agents read them.';
    if (bodyEl) bodyEl.textContent = '';
    renderWalkFilmstrip();
    return;
  }

  if (agentEl) agentEl.textContent = `With ${agentLabel(step.agent)}`;
  if (sourceEl) sourceEl.textContent = formatWalkSource(step);
  if (headEl) headEl.textContent = step.heading;
  if (capEl) capEl.textContent = step.message;
  if (bodyEl) bodyEl.textContent = step.excerpt || '(no excerpt)';

  renderWalkFilmstrip();
  bodyEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderWalkFilmstrip() {
  const strip = $('docWalkFilmstrip');
  if (!strip) return;
  strip.innerHTML = documentWalkSteps.map((s, i) => {
    const cls = ['doc-walk-tick'];
    if (i === documentWalkIndex) cls.push('is-active');
    if (s.kind === 'authority' || s.kind === 'matrix_row') cls.push('is-authority');
    if (s.kind === 'page') cls.push('is-page');
    if (s.kind === 'takeoff_line' || s.kind === 'takeoff_read') cls.push('is-takeoff');
    return `<button type="button" class="${cls.join(' ')}" data-idx="${i}" title="${escHtml(s.heading)}"></button>`;
  }).join('');
  strip.querySelectorAll('.doc-walk-tick').forEach((btn) => {
    btn.addEventListener('click', () => {
      docWalkAutoFollow = false;
      const cb = $('docWalkAutoFollow');
      if (cb) cb.checked = false;
      documentWalkIndex = Number(btn.getAttribute('data-idx'));
      renderDocumentWalk();
    });
  });
}

function resetDocEngine() {
  agentTraces = { document: [] };
  agentOutcomes = {};
  focusedAgent = null;
  documentTrayReady = false;
  resetDocumentWalk();
  PIPELINE_ORDER.forEach((id) => {
    agentTraces[id] = [];
  });
  renderDocTrayFromSelection();
  const now = $('spotlightNow');
  if (now) now.textContent = 'Uploading and indexing your files…';
  const trace = $('spotlightTrace');
  if (trace) trace.innerHTML = '';
  const outcome = $('spotlightOutcome');
  if (outcome) outcome.hidden = true;
  applySpotlight('document', pipelinePlaybook.document);
  updateDocRail();
}

function renderDocTrayFromSelection() {
  renderDocTray({
    fileNames: selectedFiles.map((f) => f.name),
  });
}

function renderDocTray(data) {
  const filesEl = $('docTrayFiles');
  const metaEl = $('docTrayMeta');
  if (!filesEl) return;
  const files = data.fileNames?.length
    ? data.fileNames
    : selectedFiles.map((f) => f.name);
  filesEl.innerHTML = files.map((f) => `<span class="doc-chip">${escHtml(f)}</span>`).join('');

  if (!metaEl) return;
  const parts = [];
  if (data.workbookProject) parts.push(`Project: ${data.workbookProject}`);
  if (data.matrixTotal != null) parts.push(`Window Matrix total: ${data.matrixTotal} shades`);
  if (data.workbookKinds?.length) parts.push(`Workbooks: ${data.workbookKinds.join(', ')}`);
  if (data.textChars) parts.push(`${Math.round(data.textChars / 1000)}k characters for search`);
  if (data.hasPdf) parts.push('PDF pages queued for vision');
  metaEl.textContent = parts.length
    ? parts.join(' · ')
    : 'Waiting for server to ingest files…';
  documentTrayReady = Boolean(data.textChars || data.matrixTotal != null);
  updateDocRail();
}

function applySpotlight(agentId, playbook) {
  focusedAgent = agentId;
  const pb = playbook || pipelinePlaybook[agentId] || {};
  const stepEl = $('spotlightStep');
  const titleEl = $('spotlightTitle');
  const missionEl = $('spotlightMission');
  const logicEl = $('spotlightLogic');

  if (stepEl) stepEl.textContent = agentId === 'document' ? 'START' : `Agent ${pb.step || '?'}`;
  if (titleEl) titleEl.textContent = pb.title || PIPELINE_DISPLAY[agentId] || 'Working…';
  if (missionEl) missionEl.textContent = pb.mission || '';
  if (logicEl) {
    logicEl.innerHTML = (pb.logic || []).map((line) => `<li>${escHtml(line)}</li>`).join('');
  }
  const reads = $('spotlightReads');
  const produces = $('spotlightProduces');
  if (reads) reads.textContent = pb.reads || '—';
  if (produces) produces.textContent = pb.produces || '—';

  const traces = agentTraces[agentId] || [];
  renderSpotlightTrace(traces);
  const last = traces[traces.length - 1];
  const now = $('spotlightNow');
  if (now) now.textContent = last || (agentId === 'document' ? 'Loading files…' : 'Starting…');

  if (agentOutcomes[agentId]) {
    showSpotlightOutcome(agentId, agentOutcomes[agentId]);
  } else {
    const out = $('spotlightOutcome');
    if (out) out.hidden = true;
  }

  updateDocRail();
  $('docSpotlight')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function showSpotlightReview(agentId) {
  applySpotlight(agentId, pipelinePlaybook[agentId]);
}

function appendSpotlightTrace(agentId, message) {
  if (!message) return;
  if (!agentTraces[agentId]) agentTraces[agentId] = [];
  const lines = agentTraces[agentId];
  if (lines[lines.length - 1] === message) return;
  lines.push(message);
  if (focusedAgent === agentId) {
    renderSpotlightTrace(lines);
    const now = $('spotlightNow');
    if (now) now.textContent = message;
  }
}

function renderSpotlightTrace(lines) {
  const ul = $('spotlightTrace');
  if (!ul) return;
  ul.innerHTML = lines.slice(-30).map((l) => `<li>${escHtml(l)}</li>`).join('');
  ul.scrollTop = ul.scrollHeight;
}

function buildOutcomeHtml(agentId, data) {
  if (!data) return '';
  if (data.skipped) {
    return `<p><strong>Step skipped.</strong> ${escHtml(data.reason || 'Not needed for this upload.')}</p>`;
  }
  const rows = [];
  const add = (label, val) => {
    if (val != null && val !== '') rows.push(`<p><strong>${escHtml(label)}:</strong> ${escHtml(String(val))}</p>`);
  };

  if (agentId === 'visionAnalyst') {
    add('Pages analyzed', data.pagesAnalyzed);
    add('Pre-count shades', data.estimatedTotalShades);
    add('Window openings (vision)', data.openingCount);
    add('Summary', data.catalogueSummary);
    if (data.confidenceNotes?.length) {
      rows.push(`<ul>${data.confidenceNotes.map((n) => `<li>${escHtml(n)}</li>`).join('')}</ul>`);
    }
  } else if (agentId === 'contextParser') {
    add('Document type', data.documentType);
    add('Project', data.projectName);
    add('Window schedule', data.windowScheduleLocation);
    add('Reading guide', data.readingGuide);
    add('Shade spec', data.shadeSpecification);
    if (data.keyFindings?.length) {
      rows.push(`<ul>${data.keyFindings.map((n) => `<li>${escHtml(n)}</li>`).join('')}</ul>`);
    }
  } else if (agentId === 'takeoffEngine') {
    add('Total shades', data.totalShades);
    add('Line items', data.lineCount);
    add('Summary', data.summary);
    add('Primary source', data.primarySource);
    add('Sheets reviewed', data.sourcesUsed);
    if (data.reconciliationNotes?.length) {
      rows.push(`<ul>${data.reconciliationNotes.map((n) => `<li>${escHtml(n)}</li>`).join('')}</ul>`);
    }
  } else if (agentId === 'estimationAgent') {
    add('Offer headline', data.offerHeadline);
    add('Total shades', data.totalShades);
    add('Project total', data.totalEstimate != null ? fmt(data.totalEstimate, data.currency || 'USD') : null);
    add('Executive summary', data.executiveSummary);
  } else if (agentId === 'validation') {
    add('Confidence', data.confidenceScore != null ? `${data.confidenceScore}/100 (${data.confidenceLevel || ''})` : null);
    add('Recommendation', data.recommendation);
    add('Ready to send', data.readyToSendOffer ? 'Yes' : 'Review first');
    add('Summary', data.summary);
    if (data.issues?.length) {
      rows.push(`<ul>${data.issues.map((n) => `<li>${escHtml(typeof n === 'string' ? n : JSON.stringify(n))}</li>`).join('')}</ul>`);
    }
  }
  return rows.join('') || '<p>Step completed.</p>';
}

function showSpotlightOutcome(agentId, data) {
  const wrap = $('spotlightOutcome');
  const body = $('spotlightOutcomeBody');
  if (!wrap || !body) return;
  body.innerHTML = buildOutcomeHtml(agentId, data);
  wrap.hidden = false;
}

function updateDocRail() {
  document.querySelectorAll('.doc-rail-btn').forEach((btn) => {
    const id = btn.getAttribute('data-agent');
    let st = 'idle';
    if (id === 'document') {
      st = documentTrayReady ? 'complete' : 'running';
    } else {
      st = agentStatuses[id] || 'idle';
    }
    btn.classList.toggle('is-active', id === focusedAgent);
    btn.classList.toggle('is-done', st === 'complete');
    btn.classList.toggle('is-skipped', st === 'skipped');
    const unlocked =
      id === 'document' ||
      st !== 'idle' ||
      agentOutcomes[id] ||
      (agentTraces[id] && agentTraces[id].length);
    btn.disabled = !unlocked;
  });
}

async function startPipeline() {
  if (!selectedFiles.length) {
    showError('Please choose at least one file first (Browse Files or drag & drop).');
    return;
  }

  const runBtn = $('runBtn');
  const runBtnDefaultLabel = (runBtn && runBtn.dataset.defaultLabel) || (runBtn && runBtn.textContent) || 'Estimate';
  if (runBtn && !runBtn.dataset.defaultLabel) {
    runBtn.dataset.defaultLabel = runBtnDefaultLabel;
  }

  sessionId = crypto.randomUUID();
  fullResults = null;

  hideError();
  $('resultsSection').hidden = true;
  $('pipelineSection').hidden = false;
  resetPipelineVisual();
  resetDocEngine();

  PIPELINE_ORDER.forEach((a) => {
    setAgentStatus(a, 'idle', 'Waiting…');
  });

  startPipelineVisual();
  $('pipelineSection').scrollIntoView({ behavior: 'smooth', block: 'start' });

  runBtn.disabled = true;
  runBtn.textContent = 'Estimating… (may take several minutes)';

  const sseConn = new EventSource(`/api/progress/${sessionId}`);
  sseConn.addEventListener('message', (e) => {
    try {
      handleProgress(JSON.parse(e.data));
    } catch {
      /* ignore malformed */
    }
  });
  sseConn.addEventListener('error', () => sseConn.close());

  const fd = new FormData();
  selectedFiles.forEach((f) => fd.append('files', f, f.name));
  fd.append('sessionId', sessionId);

  try {
    const resp = await fetch('/api/estimate', { method: 'POST', body: fd });
    let data = {};
    try {
      data = await resp.json();
    } catch {
      data = { detail: 'Invalid server response' };
    }

    sseConn.close();

    if (!resp.ok || !data.success) {
      const errMsg = formatApiError(data, resp.status);
      markPipelineError(errMsg);
      finishPipelineVisual(false);
      showError(errMsg);
      return;
    }

    fullResults = data;
    finishPipelineVisual(true);
    renderResults(data);
    $('resultsSection').hidden = false;
    $('resultsSection').scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    sseConn.close();
    finishPipelineVisual(false);
    const raw = err.message || String(err);
    const dropped = /failed to fetch|networkerror|empty response/i.test(raw);
    showError(
      dropped
        ? 'The connection dropped before a count came back. Run Estimate again. A drawing set stays open while the unit matrix and the unit plans are read.'
        : raw
    );
  } finally {
    stopPipelineTimer();
    runBtn.disabled = selectedFiles.length === 0;
    runBtn.textContent = runBtn.dataset.defaultLabel || 'Estimate';
  }
}

function formatElapsed(ms) {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function resetPipelineVisual() {
  PIPELINE_ORDER.forEach((id) => {
    agentStatuses[id] = 'idle';
    delete lastActivityByAgent[id];
  });
  const section = $('pipelineSection');
  if (section) section.classList.remove('is-running', 'is-done');
  const fill = $('pipelineProgressFill');
  if (fill) fill.style.width = '0%';
  const conn = $('pipelineConnector');
  if (conn) conn.style.setProperty('--connector-fill', '0%');
  const label = $('pipelineStepLabel');
  if (label) label.textContent = 'Starting pipeline…';
  const msg = $('pipelineCurrentMsg');
  if (msg) msg.textContent = 'Upload received — agents will run in sequence.';
  const feed = $('activityFeed');
  if (feed) feed.innerHTML = '';
  document.querySelectorAll('.step-dot').forEach((dot) => {
    dot.classList.remove('is-complete', 'is-running', 'is-error');
  });
}

function startPipelineVisual() {
  pipelineStartedAt = Date.now();
  const section = $('pipelineSection');
  if (section) section.classList.add('is-running');
  const elapsed = $('pipelineElapsed');
  if (elapsed) elapsed.textContent = '0:00';
  stopPipelineTimer();
  pipelineTimerId = setInterval(() => {
    const el = $('pipelineElapsed');
    if (el && pipelineStartedAt) {
      el.textContent = formatElapsed(Date.now() - pipelineStartedAt);
    }
  }, 1000);
  pushActivity('system', 'Estimate started — processing your files.', 'running');
}

function stopPipelineTimer() {
  if (pipelineTimerId) {
    clearInterval(pipelineTimerId);
    pipelineTimerId = null;
  }
}

function finishPipelineVisual(success) {
  const section = $('pipelineSection');
  if (section) {
    section.classList.remove('is-running');
    if (success) section.classList.add('is-done');
  }
  const fill = $('pipelineProgressFill');
  if (fill) fill.style.width = success ? '100%' : fill.style.width;
  const skipped = PIPELINE_ORDER.filter((id) => agentStatuses[id] === 'skipped').length;
  const label = $('pipelineStepLabel');
  if (label) {
    label.textContent = success
      ? (skipped
        ? `Complete — ${PIPELINE_ORDER.length - skipped} agents ran, ${skipped} not needed`
        : 'Complete — all 5 agents finished')
      : 'Stopped — see error below';
  }
  const msg = $('pipelineCurrentMsg');
  if (msg) {
    msg.textContent = success
      ? 'Your client offer is ready below.'
      : 'Pipeline did not finish. Check the highlighted step and error banner.';
  }
  if (success) {
    pushActivity('system', 'Pipeline complete. Results are ready.', 'complete');
  }
  updateStepDots();
}

function updatePipelineBar() {
  let completed = 0;
  let runningIdx = -1;
  let hasError = false;
  PIPELINE_ORDER.forEach((id, idx) => {
    const st = agentStatuses[id] || 'idle';
    if (st === 'complete' || st === 'skipped') completed += 1;
    if (st === 'running') runningIdx = idx;
    if (st === 'error') hasError = true;
  });

  let pct = (completed / PIPELINE_ORDER.length) * 100;
  if (runningIdx >= 0) {
    pct = ((runningIdx + 0.45) / PIPELINE_ORDER.length) * 100;
  }
  if (completed === PIPELINE_ORDER.length) pct = 100;

  const fill = $('pipelineProgressFill');
  if (fill) fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;

  const conn = $('pipelineConnector');
  if (conn) {
    const connPct = completed === PIPELINE_ORDER.length
      ? 100
      : runningIdx >= 0
        ? ((runningIdx + 0.5) / (PIPELINE_ORDER.length - 1)) * 100
        : (completed / (PIPELINE_ORDER.length - 1)) * 100;
    conn.style.setProperty('--connector-fill', `${Math.min(100, connPct)}%`);
  }

  const label = $('pipelineStepLabel');
  if (label) {
    if (hasError) {
      label.textContent = 'Error — see highlighted agent';
    } else if (completed === PIPELINE_ORDER.length) {
      label.textContent = 'Step 5 of 5 · Done';
    } else if (runningIdx >= 0) {
      label.textContent = `Step ${runningIdx + 1} of 5 · ${PIPELINE_DISPLAY[PIPELINE_ORDER[runningIdx]]}`;
    } else {
      label.textContent = `Step ${Math.min(completed + 1, 5)} of 5 · Waiting…`;
    }
  }

  updateStepDots();
}

function updateStepDots() {
  document.querySelectorAll('.step-dot').forEach((dot) => {
    const idx = Number(dot.getAttribute('data-step'));
    const agentId = PIPELINE_ORDER[idx];
    const st = agentStatuses[agentId] || 'idle';
    dot.classList.remove('is-complete', 'is-running', 'is-error', 'is-skipped');
    if (st === 'complete') dot.classList.add('is-complete');
    else if (st === 'skipped') dot.classList.add('is-skipped');
    else if (st === 'running') dot.classList.add('is-running');
    else if (st === 'error') dot.classList.add('is-error');
  });
}

function pushActivity(agentId, message, status) {
  if (agentId !== 'system' && status === 'running' && lastActivityByAgent[agentId] === message) {
    return;
  }
  if (agentId !== 'system') lastActivityByAgent[agentId] = message;

  const feed = $('activityFeed');
  if (!feed) return;

  const li = document.createElement('li');
  li.className = `activity-item activity-${status}`;

  const name =
    agentId === 'system' ? 'System' : (PIPELINE_DISPLAY[agentId] || agentId);
  const t = new Date();
  const timeStr = t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  li.innerHTML = `<time>${timeStr}</time><strong>${escHtml(name)}</strong><span>${escHtml(message)}</span>`;
  feed.appendChild(li);
  feed.scrollTop = feed.scrollHeight;
}

function formatApiError(data, status) {
  if (!data || typeof data !== 'object') return `Request failed (${status})`;
  const d = data.detail ?? data.error ?? data.message;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map((item) => item.msg || item.message || JSON.stringify(item)).join('; ');
  }
  if (d && typeof d === 'object') return JSON.stringify(d);
  return `Request failed (${status})`;
}

const AGENT_ERROR_LABELS = {
  'Vision Analyst': 'visionAnalyst',
  'Context Parser': 'contextParser',
  'Take-off Engine': 'takeoffEngine',
  'Takeoff Engine': 'takeoffEngine',
  'Estimation Agent': 'estimationAgent',
  'Validation': 'validation',
};

function markPipelineError(message) {
  const text = String(message || '');
  const prefix = text.split(':')[0]?.trim();
  const agentId = AGENT_ERROR_LABELS[prefix];
  if (agentId) {
    setAgentStatus(agentId, 'error', text);
  }
}

function handleProgress({ agent, status, message, data }) {
  if (agent === 'error') {
    markPipelineError(message);
    showError(message);
    return;
  }

  if (status === 'heartbeat' || agent === 'system') {
    const current = $('pipelineCurrentMsg');
    if (current && message) current.textContent = message;
    return;
  }

  if (status === 'docStep') {
    pushDocumentStep({ agent, message, data });
    if (agent && agent !== 'document') {
      appendSpotlightTrace(agent, message);
    }
    return;
  }

  if (agent === 'document') {
    if (data) renderDocTray(data);
    if (status === 'focus') {
      applySpotlight('document', data?.playbook || pipelinePlaybook.document);
      appendSpotlightTrace('document', message);
    } else if (message) {
      appendSpotlightTrace('document', message);
    }
    return;
  }

  if (status === 'skipped') {
    // Workbook fast path: the step is intentionally not run. Show it as resolved so
    // the progress bar and step dots do not sit at zero waiting for it.
    agentOutcomes[agent] = { skipped: true, reason: message };
    appendSpotlightTrace(agent, message || 'Skipped.');
    setAgentStatus(agent, 'skipped', message);
    return;
  }

  if (status === 'focus') {
    applySpotlight(agent, data?.playbook || pipelinePlaybook[agent]);
    appendSpotlightTrace(agent, message || 'Agent starting…');
  }

  setAgentStatus(agent, status, message);

  if (status === 'running' && message) {
    appendSpotlightTrace(agent, message);
  }

  if (status === 'complete' && data) {
    agentOutcomes[agent] = data;
    if (focusedAgent === agent) showSpotlightOutcome(agent, data);
    updateDocRail();
    const logEl = $(`log-${agent}`);
    if (!logEl) return;
    if (agent === 'visionAnalyst') logEl.textContent = `${data.pagesAnalyzed ?? 0} pages · ~${data.estimatedTotalShades ?? '?'} shades`;
    if (agent === 'contextParser') logEl.textContent = `${data.documentType ?? ''} · ${data.abbreviationCount ?? 0} abbrev.`;
    if (agent === 'takeoffEngine') logEl.textContent = `${data.totalShades ?? '?'} shades`;
    if (agent === 'estimationAgent') logEl.textContent = `Total: ${fmt(data.totalEstimate)}`;
    if (agent === 'validation') logEl.textContent = `${data.confidenceScore ?? '?'}/100 · ${data.recommendation ?? ''}`;
  }
}

function setAgentStatus(agentId, status, message) {
  const card = $(`card-${agentId}`);
  const log = $(`log-${agentId}`);
  if (card) card.dataset.status = status;
  if (log) log.textContent = message;

  if (PIPELINE_ORDER.includes(agentId)) {
    agentStatuses[agentId] = status;
    updatePipelineBar();
    updateDocRail();
    const current = $('pipelineCurrentMsg');
    if (current && status === 'running' && message) {
      current.textContent = message;
    }
    if (status === 'running' && focusedAgent !== agentId) {
      applySpotlight(agentId, pipelinePlaybook[agentId]);
    }
    if (['running', 'complete', 'error', 'skipped'].includes(status)) {
      pushActivity(agentId, message, status);
    }
  }
}

function renderResults(data) {
  const { analysisDetail, vision, context, takeoff, estimation, validation, workbook } = data;
  const detail = analysisDetail || {};
  const counts = detail.counts || {};

  const shadeCount =
    workbook?.authoritativeTotalShades ??
    counts.windowMatrixTotal ??
    counts.totalShadesAndBlinds ??
    takeoff?.totalShadeCount ??
    vision?.estimatedTotalShades;

  renderSimpleSummary(data);
  renderProjectSummary(data.projectSummary || detail.projectSummary);
  renderEstimationAudit(data.estimationAudit || detail.estimationAudit);
  renderClientOffer(data.clientOfferPackage);
  renderQuantitySchedule(data.quantitySchedule || detail.quantitySchedule);
  renderAnalysisDetail(detail, estimation, workbook);

  $('sumDocType').textContent = context?.documentType ?? (workbook ? 'Madeira workbook set' : '—');
  $('sumTrade').textContent = context?.trade ?? '—';
  $('sumShades').textContent = shadeCount ?? '—';
  const takeoffLines = (takeoff && takeoff.takeoffItems) ? takeoff.takeoffItems.length : 0;
  const itemCount = takeoffLines || (takeoff && takeoff.totalItemCount);
  $('sumItems').textContent = itemCount != null && itemCount !== '' ? itemCount : '—';
  $('sumTotal').textContent = fmt(estimation?.totalEstimate);
  $('sumConfidence').textContent =
    validation?.confidenceScore != null ? `${validation.confidenceScore}/100` : '—';

  const rec = validation?.recommendation ?? '—';
  const recEl = $('sumRecommendation');
  if (recEl) {
    recEl.textContent = validation?.readyToSendOffer ? `${rec} · OK to review` : rec;
    recEl.style.color = rec === 'Approved' ? 'var(--green)' : rec === 'Rejected' ? 'var(--red)' : 'var(--amber)';
  }

  renderVision(vision);
  renderContext(context);
  renderTakeoff(takeoff);
  renderEstimate(estimation);
  renderValidation(validation);
}

const MATCH_STATUS_COPY = {
  match: { label: 'Counts agree', cls: 'is-good' },
  matrix_only: { label: 'Matrix authority', cls: 'is-info' },
  mismatch: { label: 'Reconcile counts', cls: 'is-warn' },
  no_matrix: { label: 'No matrix total', cls: 'is-info' },
};

function psCell(label, value, hint) {
  if (value == null || value === '') return '';
  return `<div class="ps-cell">
    <span class="ps-cell-label">${escHtml(label)}</span>
    <span class="ps-cell-value">${escHtml(String(value))}</span>
    ${hint ? `<span class="ps-cell-hint">${escHtml(hint)}</span>` : ''}
  </div>`;
}

function renderProjectSummary(ps) {
  const band = $('projectSummaryBand');
  if (!band) return;
  if (!ps) {
    band.hidden = true;
    return;
  }
  band.hidden = false;

  $('psTitle').textContent = ps.projectName
    ? `${ps.projectName} — totals`
    : 'Project totals';

  const status = MATCH_STATUS_COPY[ps.matchStatus] || { label: ps.matchStatus || '—', cls: '' };
  const badge = $('psMatchBadge');
  badge.className = `ps-badge ${status.cls}`;
  badge.textContent = status.label;
  $('psMatchNote').textContent = ps.matchNote || '';

  const routeHint = ps.route === 'workbook_fast_path'
    ? 'Read straight from Excel — no AI counting'
    : 'AI vision + document analysis';

  $('psGrid').innerHTML = [
    psCell('Window Matrix TOTAL', ps.matrixTotal ?? '—', 'Sections printed on the matrix'),
    psCell('Outside the matrix', ps.additionalShadeCount, 'Added once'),
    psCell('Project shades', ps.projectShadeCount, 'Matrix plus areas the matrix does not list'),
    psCell('Take-off total', ps.takeoffTotal, ps.countAuthority),
    psCell('Schedule lines', ps.scheduleLineSum, `${ps.lineCount ?? 0} rows listed`),
    psCell('Matrix markings Σ', ps.markingSum, 'Sum of marking rows'),
    psCell('Blind QTY rows Σ', ps.blindQtySum, 'Sum of QTY columns'),
    psCell('Project total', ps.grandTotal != null ? fmt(ps.grandTotal, ps.currency) : null,
      ps.priceSource === 'bid_summary'
        ? 'Anchored to Bid Summary'
        : ps.priceSource === 'not_on_drawings'
          ? 'No price on these sheets'
          : 'Catalogue pricing'),
    psCell('Per shade (avg)', ps.pricePerUnit != null ? fmt(ps.pricePerUnit, ps.currency) : null),
    psCell('Product sales price', ps.referenceGrandTotal != null ? fmt(ps.referenceGrandTotal, ps.currency) : null, 'Quoted tabs, shades only'),
    psCell('Sheet Total Bid', ps.clientTotal != null ? fmt(ps.clientTotal, ps.currency) : null, 'Includes installation, charges, and tax'),
    ps.priceVariance && ps.priceVariance.amount
      ? psCell('Offer vs sheet', fmt(ps.priceVariance.amount, ps.currency), ps.priceVariance.formula)
      : '',
    psCell('Total sq ft', ps.totalSquareFeet),
    psCell('Route', routeHint),
  ].filter(Boolean).join('');

  $('psFiles').innerHTML = (ps.files || []).length
    ? ps.files.map((f) => `<span class="ps-file" title="${escHtml(f.role || '')}">${escHtml(f.name)}</span>`).join('')
    : '—';
}

function renderEstimationAudit(audit) {
  const box = $('calcAuditBox');
  if (!box) return;

  if (!audit) {
    $('calcAuditSummary').textContent = 'Run Estimate to see formulas and file references.';
    ['auditRefsTable', 'auditCountTable', 'auditPriceTable', 'auditLinesTable'].forEach((id) => {
      const tb = $(id)?.querySelector('tbody');
      if (tb) tb.innerHTML = '<tr><td colspan="8">Nothing yet — upload your files and run Estimate.</td></tr>';
    });
    return;
  }

  const match = audit.countCalculation?.matchesMatrix;
  const matchNote =
    match === true
      ? ' Matrix TOTAL matches take-off.'
      : match === false
        ? ' Line sum differs from Matrix TOTAL — count follows Matrix authority.'
        : '';
  $('calcAuditSummary').textContent = (audit.summary || 'Calculation audit') + matchNote;

  const refsBody = $('auditRefsTable')?.querySelector('tbody');
  if (refsBody) {
    refsBody.innerHTML = (audit.documentReferences || []).map((r) => `
      <tr>
        <td data-label="Role"><strong>${escHtml(r.role)}</strong></td>
        <td data-label="Location" class="wrap">${escHtml(r.location)}</td>
        <td data-label="Used for" class="wrap">${escHtml(r.usedFor)}</td>
      </tr>`).join('') || '<tr><td colspan="3">No references recorded.</td></tr>';
  }

  const stepRows = (steps) => (steps || []).map((s) => `
      <tr>
        <td data-label="What">${escHtml(s.label)}</td>
        <td data-label="Value"><strong>${escHtml(s.value)}</strong></td>
        <td data-label="Formula" class="wrap">${escHtml(s.formula)}</td>
        <td data-label="Reference" class="wrap">${escHtml(s.reference)}</td>
      </tr>`).join('') || '<tr><td colspan="4">No steps recorded.</td></tr>';

  const countBody = $('auditCountTable')?.querySelector('tbody');
  if (countBody) countBody.innerHTML = stepRows(audit.countCalculation?.steps);

  const priceBody = $('auditPriceTable')?.querySelector('tbody');
  if (priceBody) priceBody.innerHTML = stepRows(audit.priceCalculation?.steps);

  const linesBody = $('auditLinesTable')?.querySelector('tbody');
  if (linesBody) {
    linesBody.innerHTML = (audit.lineCalculations || []).map((l) => `
      <tr>
        <td data-label="Tag"><code>${escHtml(l.windowTag)}</code></td>
        <td data-label="Location">${escHtml(l.location)}</td>
        <td data-label="Qty"><strong>${l.quantity ?? '—'}</strong></td>
        <td data-label="How qty is counted" class="wrap">${escHtml(l.countFormula)}</td>
        <td data-label="Size math" class="wrap">${escHtml(l.dimensionFormula)}</td>
        <td data-label="Price math" class="wrap">${escHtml(l.priceFormula)}${
          l.productBasis ? `<span class="line-flag">${escHtml(l.productBasis)}</span>` : ''
        }</td>
        <td data-label="Extended">${l.extendedPrice != null ? fmt(l.extendedPrice) : '—'}</td>
        <td data-label="Source reference" class="wrap">${escHtml(l.reference)}</td>
      </tr>`).join('') || '<tr><td colspan="8">No lines were produced — check the Window Matrix upload.</td></tr>';
  }
}

function renderSimpleSummary(data) {
  const val = data.validation || {};
  const wb = data.workbook || {};
  const badge = $('simpleStatusBadge');
  const text = $('simpleStatusText');
  const list = $('simpleChecklist');
  if (!badge || !text) return;

  const summary = val.userFriendlySummary
    || val.overallAssessment
    || 'Review your shade count and price below.';
  text.textContent = summary;

  const score = val.confidenceScore;
  const ready = val.readyToSendOffer;
  badge.classList.remove('is-good', 'is-warn');
  if (ready || (score != null && score >= 80)) {
    badge.textContent = 'Ready to review';
    badge.classList.add('is-good');
  } else if (score != null && score < 60) {
    badge.textContent = 'Needs review';
    badge.classList.add('is-warn');
  } else {
    badge.textContent = 'Almost there';
  }

  if (list) {
    const items = val.reviewChecklist || [
      wb.projectShadeCount != null
        ? `Total shades: ${wb.projectShadeCount}`
          + (wb.additionalShadeCount
            ? ` (${wb.authoritativeTotalShades} on the window matrix + ${wb.additionalShadeCount} outside it)`
            : ' (from the window matrix)')
        : 'Confirm total shade count',
      'Spot-check a few widths and heights',
      'Confirm total price',
    ];
    list.innerHTML = items.map((item, i) =>
      `<li class="${ready && i === items.length - 1 ? 'done' : ''}">${escHtml(item)}</li>`
    ).join('');
  }
}

function renderQuantitySchedule(qs) {
  const tbody = $('quantityScheduleTable')?.querySelector('tbody');
  const fullTbody = $('quantityScheduleFullTable')?.querySelector('tbody');
  const meth = $('quantityMethodology');
  const totalsEl = $('quantityScheduleTotals');
  if (!tbody) return;

  if (!qs || !(qs.lines || []).length) {
    if (meth) meth.innerHTML = '<p>Run Estimate to build your shade list.</p>';
    if ($('measureNote')) $('measureNote').hidden = true;
    tbody.innerHTML = '<tr><td colspan="6">No shade lines were found. Upload the Window Matrix workbook (with its Blind QTY sheets) so each opening can be listed.</td></tr>';
    if (fullTbody) fullTbody.innerHTML = '';
    if (totalsEl) totalsEl.textContent = '';
    return;
  }

  const m = qs.methodology || {};
  if (meth) {
    const countLine = m.countRule || '';
    meth.innerHTML = `<p>${escHtml(m.projectName ? `${m.projectName} — ` : '')}${escHtml(countLine)}</p>`;
  }
  const measure = $('measureNote');
  const measureText = (m.measurementReference && m.measurementReference.note) || m.dimensionRule || '';
  if (measure) {
    const show = Boolean(m.measurementReference && m.measurementReference.note);
    measure.hidden = !show;
    if ($('measureNoteText')) $('measureNoteText').textContent = show ? measureText : '';
  }

  tbody.innerHTML = qs.lines.map((line) => {
    const loc = [line.floor || line.areaSection, line.room].filter(Boolean).join(', ');
    const size = `${line.width || '—'} × ${line.height || '—'}`;
    const warn = line.dimensionWarning
      ? `<div class="dim-warning">${escHtml(line.dimensionWarning)}</div>`
      : '';
    const src = line.sourceLocation || '—';
    return `<tr>
      <td data-label="Tag"><code>${escHtml(line.windowTag)}</code></td>
      <td data-label="Location">${escHtml(loc || '—')}</td>
      <td data-label="Size (W × H)">${escHtml(size)}${warn}</td>
      <td data-label="Qty"><strong>${line.quantity}</strong></td>
      <td data-label="Line total">${line.extendedPrice != null ? fmt(line.extendedPrice) : '—'}</td>
      <td data-label="From sheet" class="wrap">${escHtml(src.length > 48 ? src.slice(0, 45) + '…' : src)}</td>
    </tr>`;
  }).join('');

  if (fullTbody) {
    fullTbody.innerHTML = qs.lines.map((line) => `
      <tr>
        <td>${line.lineNumber}</td>
        <td><code>${escHtml(line.windowTag)}</code></td>
        <td>${escHtml(line.floor || line.areaSection)}</td>
        <td>${escHtml(line.room)}</td>
        <td>${escHtml(line.width)}${line.dimensionWarning ? `<div class="dim-warning">${escHtml(line.dimensionWarning)}</div>` : ''}</td>
        <td>${escHtml(line.height)}</td>
        <td>${line.squareFeetEach ?? '—'}</td>
        <td class="wrap">${escHtml(line.systemType)}</td>
        <td><strong>${line.quantity}</strong></td>
        <td class="wrap">${escHtml(line.countBasis)}</td>
        <td class="wrap">${escHtml(line.sourceLocation)}</td>
        <td>${line.unitPrice != null ? fmt(line.unitPrice) : '—'}</td>
        <td>${line.extendedPrice != null ? fmt(line.extendedPrice) : '—'}</td>
        <td class="wrap">${escHtml(line.pricingFormula)}</td>
      </tr>`).join('');
  }

  const t = qs.totals || {};
  if (totalsEl) {
    const parts = [
      `${t.unitQuantity ?? '—'} shades total`,
      t.grandTotal != null ? `${fmt(t.grandTotal, t.currency)} project total` : null,
      t.matchesMatrix === false ? 'Count differs from Matrix — please review' : null,
    ].filter(Boolean);
    totalsEl.textContent = parts.join(' · ');
  }
}

function renderAnalysisDetail(detail, estimation, workbook) {
  const names = detail.fileNames || (detail.fileName ? [detail.fileName] : []);
  if (!names.length && !workbook) {
    $('detailPrimarySource').textContent = 'Run the pipeline to see analysis details.';
    return;
  }

  const matrixNote = workbook?.authoritativeTotalShades != null
    ? ` Window Matrix total: ${workbook.authoritativeTotalShades} shades.`
    : '';

  $('detailPrimarySource').textContent =
    `${detail.fileName || names.join(', ')} — ${detail.primarySource || 'See steps below.'}${matrixNote}`;

  const c = detail.counts || {};
  $('detailTotalUnits').textContent =
    workbook?.authoritativeTotalShades ?? c.totalShadesAndBlinds ?? '—';
  $('detailShades').textContent = c.shades ?? '—';
  $('detailBlinds').textContent = c.blinds ?? '—';
  $('detailPrice').textContent = fmt(detail.totalEstimate ?? estimation?.totalEstimate);
  $('detailHowWeCounted').textContent =
    detail.howWeCounted || workbook?.guidance || 'Counts from structured workbook + AI take-off.';

  $('detailSteps').innerHTML = (detail.analysisSteps || []).map((s) => `
    <div class="detail-step">
      <div class="detail-step-num">${s.step ?? ''}</div>
      <div class="detail-step-body">
        <strong>${escHtml(s.stage)}</strong>
        <div class="detail-step-meta"><em>Where:</em> ${escHtml(s.where)}</div>
        <div class="detail-step-meta"><em>How:</em> ${escHtml(s.method)}</div>
        <div class="detail-step-result">${escHtml(s.result)}</div>
      </div>
    </div>`).join('');

  $('detailSourcesTable').querySelector('tbody').innerHTML =
    (detail.lineItemSources || []).map((row) => `
    <tr>
      <td><code>${escHtml(row.windowTag)}</code></td>
      <td>${escHtml(row.productKind)}</td>
      <td>${escHtml(row.item)}</td>
      <td><strong>${row.quantity ?? '—'}</strong></td>
      <td>${escHtml(row.width)} × ${escHtml(row.height)}</td>
      <td>${row.squareFeet ?? '—'}</td>
      <td>${escHtml(row.location)}</td>
      <td class="wrap">${escHtml(row.countBasis)}</td>
      <td class="wrap">${escHtml(row.sourceLocation)}</td>
    </tr>`).join('') ||
    '<tr><td colspan="9">No line items yet.</td></tr>';

  $('detailTabIntro').textContent = detail.validationNote || detail.fileName || '';
}

function renderVision(vis) {
  if (!vis || !vis.pagesAnalyzed) {
    $('visionCatalogueSummary').textContent = 'No PDF/image vision (Excel/text-only upload).';
    return;
  }
  $('visionCatalogueSummary').textContent = vis.catalogueSummary ?? '—';
  $('visionStats').innerHTML = [
    `<li>Pages analyzed: <strong>${vis.pagesAnalyzed ?? '—'}</strong></li>`,
    `<li>Shades (vision): <strong>${vis.estimatedTotalShades ?? '—'}</strong></li>`,
  ].join('');
  $('visionNotes').innerHTML = (vis.confidenceNotes ?? []).map((n) => `<li>${escHtml(n)}</li>`).join('');
  $('catalogueTable').querySelector('tbody').innerHTML = (vis.catalogueMatches ?? []).map((m) =>
    `<tr><td><code>${escHtml(m.sku)}</code></td><td>${escHtml(m.productName)}</td><td>${m.quantity ?? '—'}</td><td>${escHtml(m.reason)}</td></tr>`
  ).join('');
  $('visionWindowsTable').querySelector('tbody').innerHTML = (vis.windowOpenings ?? []).map((w) =>
    `<tr><td>${escHtml(w.tag)}</td><td>${escHtml(w.width)} × ${escHtml(w.height)}</td><td>${escHtml(w.room)}</td><td>${escHtml(w.shadeType)}</td><td>${w.quantity ?? 1}</td><td>${w.needsShade ? '✓' : '—'}</td></tr>`
  ).join('');
}

function renderContext(ctx) {
  if (!ctx) {
    $('readingGuide').textContent = '—';
    return;
  }
  $('readingGuide').textContent = ctx.readingGuide ?? '—';
  $('keyFindings').innerHTML = (ctx.keyFindings ?? []).map((f) => `<li>${escHtml(f)}</li>`).join('');
  $('structTable').querySelector('tbody').innerHTML = (ctx.documentStructure ?? []).map((s) =>
    `<tr><td><strong>${escHtml(s.section)}</strong></td><td>${escHtml(s.description)}</td></tr>`
  ).join('');
  $('abbrevTable').querySelector('tbody').innerHTML = (ctx.abbreviations ?? []).map((a) =>
    `<tr><td><code>${escHtml(a.term)}</code></td><td>${escHtml(a.definition)}</td></tr>`
  ).join('');
}

function renderTakeoff(tkf) {
  if (!tkf) {
    $('takeoffSummary').textContent = '—';
    return;
  }
  $('takeoffSummary').textContent = tkf.summary ?? '—';
  const items = tkf.takeoffItems ?? [];
  const tbody = $('takeoffTable').querySelector('tbody');
  const search = $('takeoffSearch');

  const renderRows = (rows) => {
    tbody.innerHTML = rows.map((item, i) => `
      <tr>
        <td class="num">${i + 1}</td>
        <td><code>${escHtml(item.windowTag)}</code></td>
        <td>${escHtml(item.width)} × ${escHtml(item.height)}</td>
        <td>${item.squareFeet ?? '—'}</td>
        <td>${escHtml(item.productKind || item.category)}</td>
        <td><strong>${escHtml(item.item)}</strong></td>
        <td><strong>${item.quantity ?? '—'}</strong></td>
        <td>${escHtml(item.location)}</td>
        <td class="wrap">${escHtml(item.calculationBasis || item.notes || '—')}</td>
        <td class="wrap">${escHtml(item.sourceLocation || '—')}</td>
      </tr>`).join('');
  };

  renderRows(items);
  if (search) {
    search.oninput = () => {
      const q = search.value.toLowerCase();
      renderRows(items.filter((it) =>
        [it.item, it.category, it.location].some((v) => (v ?? '').toLowerCase().includes(q))
      ));
    };
  }
}

function renderEstimate(est) {
  if (!est) return;
  const offer = est.clientOffer;
  const offerBlock = $('clientOfferBlock');
  if (offer && (offer.headline || offer.offerNarrative)) {
    offerBlock.hidden = false;
    $('clientOfferHeadline').textContent = offer.headline ?? '';
    $('clientOfferBody').textContent = offer.offerNarrative ?? '';
  } else if (offerBlock) {
    offerBlock.hidden = true;
  }

  $('execSummary').textContent = est.executiveSummary ?? '—';
  $('assumptions').innerHTML = (est.assumptions ?? []).map((a) => `<li>${escHtml(a)}</li>`).join('');

  const items = est.estimates ?? [];
  const tbody = $('estimateTable').querySelector('tbody');
  const search = $('estimateSearch');

  const renderRows = (rows) => {
    tbody.innerHTML = rows.map((item, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><code>${escHtml(item.windowTag)}</code></td>
        <td>${escHtml(item.width)} × ${escHtml(item.height)}</td>
        <td>${escHtml(item.category)}</td>
        <td><strong>${escHtml(item.item)}</strong></td>
        <td>${item.quantity ?? '—'}</td>
        <td>${escHtml(item.unit)}</td>
        <td>${fmt(item.unitCost)}</td>
        <td>${fmt(item.laborCost)}</td>
        <td>${fmt(item.materialCost)}</td>
        <td><strong>${fmt(item.totalCost)}</strong></td>
        <td class="wrap">${escHtml(item.calculationFormula || '')}</td>
      </tr>`).join('');
  };

  renderRows(items);
  if (search) {
    search.oninput = () => {
      const q = search.value.toLowerCase();
      renderRows(items.filter((it) =>
        [it.item, it.category].some((v) => (v ?? '').toLowerCase().includes(q))
      ));
    };
  }

  $('costSubtotal').textContent = fmt(est.subtotal);
  $('costOverhead').textContent = fmt(est.overhead);
  $('costProfit').textContent = fmt(est.profit);
  $('costTotal').textContent = fmt(est.totalEstimate);
}

function renderValidation(val) {
  if (!val) return;
  const score = val.confidenceScore ?? 0;
  $('confScore').textContent = `${score}/100`;
  setTimeout(() => {
    $('confFill').style.width = `${score}%`;
  }, 100);

  $('strengths').innerHTML = (val.strengths ?? []).map((s) => `<li>${escHtml(s)}</li>`).join('');
  $('limitations').innerHTML = (val.limitations ?? []).map((l) => `<li>${escHtml(l)}</li>`).join('');
  $('issuesList').innerHTML = (val.validationIssues ?? []).map((issue) => `
    <div class="issue-item ${escHtml(issue.severity)}">
      <span class="issue-sev">${escHtml(issue.severity)}</span>
      <div class="issue-body">
        <div class="issue-title">${escHtml(issue.category)}: ${escHtml(issue.issue)}</div>
        <div class="issue-rec">${escHtml(issue.recommendation)}</div>
      </div>
    </div>`).join('');
  $('xrefTable').querySelector('tbody').innerHTML = (val.crossChecks ?? []).map((xr) => `
    <tr>
      <td>${escHtml(xr.item)}</td>
      <td>${escHtml(xr.documentValue)}</td>
      <td>${escHtml(xr.estimatedValue)}</td>
      <td class="${xr.match ? 'badge-match' : 'badge-no-match'}">${xr.match ? '✓' : '✗'}</td>
      <td>${escHtml(xr.notes)}</td>
    </tr>`).join('');
  $('overallAssessment').textContent = val.overallAssessment ?? '—';
}

function initTabs() {
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = $(`tab-${btn.dataset.tab}`);
      if (panel) panel.classList.add('active');
    });
  });
}

function initCopyOffer() {
  $('copyOfferBtn')?.addEventListener('click', async () => {
    const text = fullResults?.clientOfferPackage?.emailDraft;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const btn = $('copyOfferBtn');
      const prev = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = prev; }, 2000);
    } catch {
      showError('Could not copy — select text manually from the offer section.');
    }
  });
}

function renderClientOffer(pkg) {
  const hero = $('clientOfferHero');
  if (!pkg || !hero) {
    if (hero) hero.hidden = true;
    return;
  }
  hero.hidden = false;
  $('offerHeroHeadline').textContent = pkg.headline ?? 'Client offer';
  $('offerHeroProject').textContent = pkg.projectName ? `Project: ${pkg.projectName}` : '';
  $('offerHeroShades').textContent = pkg.totalShades ?? '—';
  $('offerHeroPrice').textContent = fmt(pkg.totalPrice);
  const priceLabel = $('offerHeroPrice')?.closest('.offer-stat')?.querySelector('.offer-stat-label');
  if (priceLabel) {
    const product = Number(pkg.productPrice);
    const total = Number(pkg.totalPrice);
    priceLabel.textContent = Number.isFinite(product) && Number.isFinite(total) && Math.abs(product - total) > 1
      ? 'Total Bid'
      : 'Total price';
  }
  $('offerHeroPerUnit').textContent = pkg.pricePerShade != null ? fmt(pkg.pricePerShade) : '—';
  $('offerHeroReady').textContent = pkg.readyToSend ? 'Review & send' : 'Review first';
  $('offerHeroReady').style.color = pkg.readyToSend ? 'var(--green)' : 'var(--amber)';
  $('offerHeroNarrative').textContent = pkg.offerNarrative ?? '';
  const offerMeasure = $('offerMeasureNote');
  if (offerMeasure) {
    const note = pkg.measurementNote || '';
    offerMeasure.hidden = !note;
    if ($('offerMeasureText')) $('offerMeasureText').textContent = note;
  }
  renderOfferPriceStack(pkg);
  $('offerHeroDocs').textContent = (pkg.documentsRead ?? []).join(' · ') || 'Uploaded files';
  $('offerHeroValidity').textContent = pkg.validityNote ?? '';
}

function renderOfferPriceStack(pkg) {
  const box = $('offerPriceStack');
  if (!box) return;
  const stack = pkg.priceStack || [];
  const alternates = pkg.alternateOptions || [];
  const hasClient = stack.some((tab) => tab.clientTotal != null);
  if (!hasClient && !alternates.length) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  const cards = stack.map((tab) => {
    const rows = [];
    if (tab.product != null) rows.push(`<li>Product (Sales Price): ${fmt(tab.product)}</li>`);
    if (tab.installation != null) rows.push(`<li>Installation: ${fmt(tab.installation)}</li>`);
    for (const charge of tab.charges || []) {
      rows.push(`<li>${escHtml(charge.name)}: ${fmt(charge.amount)}</li>`);
    }
    if (tab.tax != null) rows.push(`<li>Tax: ${fmt(tab.tax)}</li>`);
    if (tab.motorizedTotal != null) {
      rows.push(`<li>Total Bid (manual): ${fmt(tab.totalBid)}</li>`);
      rows.push(`<li class="stack-total">With motors: ${fmt(tab.motorizedTotal)}</li>`);
    } else if (tab.totalBid != null) {
      rows.push(`<li class="stack-total">Total Bid: ${fmt(tab.totalBid)}</li>`);
    }
    return `<article class="offer-stack-card"><h4>${escHtml(tab.sheet || 'Quoted tab')}</h4><ul>${rows.join('')}</ul></article>`;
  });
  if (alternates.length) {
    const items = alternates.map((tab) => {
      const amount = tab.clientTotal != null ? tab.clientTotal : tab.product;
      return `<li>${escHtml(tab.sheet || 'Alternate')}: ${fmt(amount)} — same markings, not added</li>`;
    });
    cards.push(`<article class="offer-stack-card"><h4>Alternate prices</h4><ul>${items.join('')}</ul></article>`);
  }
  box.hidden = false;
  box.innerHTML = cards.join('');
}

function initExports() {
  $('exportJsonBtn')?.addEventListener('click', () => {
    if (!fullResults) return;
    triggerDownload(new Blob([JSON.stringify(fullResults, null, 2)], { type: 'application/json' }), 'estimate.json');
  });

  $('exportCsvBtn')?.addEventListener('click', () => {
    if (!fullResults) return;
    const audit = fullResults.estimationAudit;
    if (audit?.lineCalculations?.length) {
      const ps = fullResults.projectSummary || {};
      const rows = [
        ['PROJECT', ps.projectName ?? ''],
        ['Window Matrix TOTAL', ps.matrixTotal ?? ''],
        ['Take-off total', ps.takeoffTotal ?? ''],
        ['Schedule line sum', ps.scheduleLineSum ?? ''],
        ['Project total', ps.grandTotal ?? ''],
        ['Bid Summary total', ps.referenceGrandTotal ?? ''],
        ['Count match status', ps.matchStatus ?? '', ps.matchNote ?? ''],
        [],
        ['Tag', 'Location', 'Qty', 'Count formula', 'Size formula', 'Price formula', 'Extended', 'Source reference'],
        ...audit.lineCalculations.map((l) => [
          l.windowTag, l.location, l.quantity, l.countFormula, l.dimensionFormula,
          l.priceFormula, l.extendedPrice, l.reference,
        ]),
      ];
      const csv = rows.map((r) => r.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
      triggerDownload(new Blob([csv], { type: 'text/csv' }), 'estimation-audit.csv');
      return;
    }

    const qs = fullResults.quantitySchedule;
    if (qs?.lines?.length) {
      const rows = [
        [
          'Line', 'Tag', 'Floor', 'Room', 'Width', 'Height', 'SqFt', 'System', 'Qty', 'Unit',
          'Count basis', 'Source', 'Unit price', 'Extended', 'Pricing formula',
        ],
        ...qs.lines.map((l) => [
          l.lineNumber, l.windowTag, l.floor, l.room, l.width, l.height, l.squareFeetEach,
          l.systemType, l.quantity, l.unit, l.countBasis, l.sourceLocation,
          l.unitPrice, l.extendedPrice, l.pricingFormula,
        ]),
      ];
      const csv = rows.map((r) => r.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
      triggerDownload(new Blob([csv], { type: 'text/csv' }), 'quantity-schedule.csv');
      return;
    }
    if (!fullResults.estimation) return;
    const est = fullResults.estimation;
    const rows = [
      ['Tag', 'Category', 'Item', 'Qty', 'Unit', 'Unit Cost', 'Labor', 'Material', 'Total', 'Formula'],
      ...(est.estimates ?? []).map((e) => [
        e.windowTag, e.category, e.item, e.quantity, e.unit,
        e.unitCost, e.laborCost, e.materialCost, e.totalCost, e.calculationFormula,
      ]),
      [],
      ['', 'Subtotal', '', '', '', '', '', est.subtotal],
      ['', 'TOTAL', '', '', '', '', '', est.totalEstimate],
    ];
    const csv = rows.map((r) => r.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    triggerDownload(new Blob([csv], { type: 'text/csv' }), 'estimate.csv');
  });
}

function initNewEstimate() {
  $('newEstimateBtn')?.addEventListener('click', () => {
    fullResults = null;
    clearFiles();
    $('resultsSection').hidden = true;
    $('pipelineSection').hidden = true;
    $('clientOfferHero').hidden = true;
    const band = $('projectSummaryBand');
    if (band) band.hidden = true;
    hideError();
    $('uploadSection')?.scrollIntoView({ behavior: 'smooth' });
  });
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function showError(msg) {
  $('errorMsg').textContent = msg;
  $('errorBanner').hidden = false;
}

function hideError() {
  $('errorBanner').hidden = true;
}
