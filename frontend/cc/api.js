// Network helpers. Every call goes through the deployment base path; errors carry the server's message.
export const BASE = document.body.dataset.base || '';
export const VERSION = document.body.dataset.version || '';

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = 'GET', body, signal, form } = {}) {
  const init = { method, signal, credentials: 'same-origin', headers: {} };
  if (form) init.body = form;
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch(BASE + path, init); }
  catch (err) { if (err.name === 'AbortError') throw err; throw new ApiError(0, 'Network error — check your connection and try again.'); }
  const type = res.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await res.json().catch(() => ({})) : await res.text();
  if (!res.ok) {
    const detail = typeof data === 'object' ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail) : '';
    if (res.status === 401) window.dispatchEvent(new CustomEvent('sg:auth-required', { detail }));
    throw new ApiError(res.status, detail || `Request failed (${res.status}).`);
  }
  return data;
}

// Server-sent events for a run; reconnects with Last-Event-ID semantics via ?after.
export function streamRun(runId, onEvent, onEnd) {
  let last = 0, closed = false, source = null, retries = 0;
  const open = () => {
    if (closed) return;
    source = new EventSource(`${BASE}/api/v5/runs/${encodeURIComponent(runId)}/stream?after=${last}`);
    source.addEventListener('step', e => { retries = 0; const ev = JSON.parse(e.data); last = Math.max(last, ev.seq); onEvent(ev); });
    source.addEventListener('end', e => { closed = true; source.close(); onEnd && onEnd(JSON.parse(e.data || '{}')); });
    source.onerror = () => { source.close(); if (!closed && retries++ < 8) setTimeout(open, 600 * retries); };
  };
  open();
  return () => { closed = true; source && source.close(); };
}

export const session = { operator: false, status: null };

export async function refreshStatus() {
  try { session.status = await api('/api/v5/status'); session.operator = !!session.status.operator; }
  catch { session.status = null; }
  window.dispatchEvent(new CustomEvent('sg:status', { detail: session.status }));
  return session.status;
}

export async function login(password) {
  await api('/api/auth/login', { method: 'POST', body: { password } });
  await refreshStatus();
}

export async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* already signed out */ }
  await refreshStatus();
}
