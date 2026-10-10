// Persona index explorer (/genome): personas deconstructed into searchable Elasticsearch documents.
import { api, session, BASE } from './api.js';
import { $, esc, fmtMs, origin, titleCase, toast } from './ui.js';
import { highlight } from './json.js';

const FACTORS = ['primary_objection', 'buying_stage', 'language', 'hinglish_mix', 'formality', 'warmth', 'empathy', 'sentence_length', 'voice', 'tag'];

export async function mount(root) {
  root.innerHTML = `
    <div class="page">
      <div class="page-head"><div><p class="eyebrow">Owner point 11 · our core idea</p><h1>Persona index</h1>
        <p>A persona is not a hidden prompt. Sarvam breaks it into one Elasticsearch document per factor — value, score, reason, evidence and origin — so personas can be searched across sellers, charted in Kibana and audited.</p></div>
        <div class="aside"><a class="btn" href="https://kibana.lehana.in/s/sellergenome/app/dashboards" target="_blank" rel="noopener">Kibana ↗</a></div></div>
      <div class="grid-3" style="margin-bottom:14px">
        <div class="card"><div class="card-head"><h3>1 · Evidence in</h3></div><div class="card-body muted" style="font-size:13px">Every API response is normalised into the production contract; each fact gets an id like <code>i:objection_or_blocker</code> with its origin and JSON pointer.</div></div>
        <div class="card"><div class="card-head"><h3>2 · Sarvam reads it</h3></div><div class="card-body muted" style="font-size:13px"><code>sarvam-105b-conversations</code> returns tags, the PS04 factors and <b>novel factors</b> it noticed — each citing evidence ids. Uncited claims are dropped.</div></div>
        <div class="card"><div class="card-head"><h3>3 · Typed documents out</h3></div><div class="card-body muted" style="font-size:13px">Factor names are <b>values</b>, not fields: <code>factor="funding_constraint"</code>, <code>value_keyword=…</code>. The mapping is <code>dynamic: strict</code>, so the LLM can never explode it.</div></div>
      </div>
      <div class="floor genome-layout">
        <section class="card"><div class="card-head"><h2>Search factors across sellers</h2><span class="aside dim" style="font-size:12px">${session.operator ? 'All sellers' : 'Synthetic sellers (sign in for private)'}</span></div>
          <form class="card-body g-form" id="g-form">
            <label style="display:grid;gap:4px;font-size:12px" class="dim">Factor<select name="factor"><option value="">Any factor</option>${FACTORS.map(f => `<option value="${f}" ${f === 'primary_objection' ? 'selected' : ''}>${titleCase(f)}</option>`).join('')}</select></label>
            <label style="display:grid;gap:4px;font-size:12px" class="dim">Value<input type="text" name="value" placeholder="e.g. price, time, hi-in" value=""></label>
            <label style="display:grid;gap:4px;font-size:12px" class="dim">Free text<input type="text" name="q" placeholder="e.g. cash flow"></label>
            <button class="btn btn-primary">Search</button></form>
          <div class="card-body" id="g-results"><div class="skeleton" style="height:120px"></div></div></section>
        <aside class="side" style="position:static">
          <section class="card"><div class="card-head"><h3>What the index holds</h3></div><div class="card-body" id="g-facets"><div class="skeleton" style="height:160px"></div></div></section>
          <section class="card"><div class="card-head"><h3>Novel factors (LLM-proposed)</h3></div><div class="card-body" id="g-registry"><div class="skeleton" style="height:80px"></div></div></section>
        </aside>
      </div>
      <section class="card" style="margin-top:14px"><div class="card-head"><h2>Mapping</h2><span class="aside">${origin('elastic', 'dynamic: strict')}</span></div>
        <div class="card-body"><pre class="code">${highlight({ mappings: { dynamic: 'strict', properties: { seller_id: { type: 'keyword' }, factor: { type: 'keyword' }, novel: { type: 'boolean' }, value_type: { type: 'keyword' }, value_keyword: { type: 'keyword' }, value_number: { type: 'double' }, value_bool: { type: 'boolean' }, value_text: { type: 'text' }, score: { type: 'float' }, strength: { type: 'keyword' }, origin: { type: 'keyword' }, language: { type: 'keyword' }, archetype: { type: 'keyword' }, revision: { type: 'integer' }, latest: { type: 'boolean' }, sources: { type: 'object', enabled: false }, at: { type: 'date' }, expires_at: { type: 'date' } } } })}</pre></div></section>
    </div>`;
  $('#g-form').addEventListener('submit', e => { e.preventDefault(); search(new FormData(e.target)); });
  search(new FormData($('#g-form')));
  facets();
  return {};
}

async function search(form) {
  const qs = new URLSearchParams({ factor: form.get('factor') || '', value: form.get('value') || '', q: form.get('q') || '' });
  const box = $('#g-results');
  try {
    const out = await api(`/api/v5/genome/search?${qs}`);
    box.innerHTML = `
      <div class="ins-strip" style="margin-bottom:8px"><span class="ins-label">${esc(out.total ?? out.hits.length)} documents · ${esc(fmtMs(out.ms))} · ${esc(out.source)}</span><span class="mono dim" style="font-size:11.5px">POST ${esc(out.index)}/_search</span></div>
      <details style="margin-bottom:10px"><summary class="dim" style="font-size:12.5px">Show the Elasticsearch query</summary><pre class="code" style="margin-top:6px">${highlight(out.query)}</pre></details>
      ${out.hits.length ? `<div class="scroll-x"><table class="table"><thead><tr><th>Seller</th><th>Factor</th><th>Value</th><th>Score</th><th>Origin</th></tr></thead><tbody>${out.hits.map(h => `
        <tr><td><b>${esc(h.city || '')}</b><br><span class="mono dim" style="font-size:11px">${esc(h.seller_id)}</span></td><td class="mono">${esc(h.factor)}${h.novel ? ' <span class="chip">novel</span>' : ''}</td>
        <td>${esc(h.value_text || h.value_keyword || h.value_number || '')}</td><td class="mono">${esc(Math.round(h.score))}</td><td>${origin(h.origin)}</td></tr>`).join('')}</tbody></table></div>`
        : `<p class="muted">No documents yet. Barge into a few calls on the <a href="${BASE}/" data-nav>seller floor</a> — each one writes its persona here.</p>`}`;
  } catch (err) { box.innerHTML = `<p class="note">${esc(err.message)}</p>`; }
}

async function facets() {
  try {
    const [f, r] = await Promise.all([api('/api/v5/genome/facets'), api('/api/v5/genome/registry')]);
    const aggs = f.aggregations?.factors?.buckets || [];
    $('#g-facets').innerHTML = aggs.length ? aggs.filter(b => FACTORS.includes(b.key)).slice(0, 8).map(b => `<div class="facet" style="margin-bottom:10px"><b class="mono" style="font-size:12px">${esc(b.key)}</b>${b.values.buckets.map(v => `<div class="facet-row"><span>${esc(v.key)}</span><span class="bar"><i style="width:${Math.round(v.doc_count / b.doc_count * 100)}%"></i></span><span class="mono dim">${v.doc_count}</span></div>`).join('')}</div>`).join('')
      + `<p class="dim" style="font-size:11.5px">Terms aggregation · ${esc(fmtMs(f.ms))}</p>` : `<p class="muted">${esc(f.source)} — barge into calls to fill the index.</p>`;
    $('#g-registry').innerHTML = r.items.length ? `<table class="table"><thead><tr><th>Factor</th><th>Seen</th><th>Example</th></tr></thead><tbody>${r.items.slice(0, 15).map(x => `<tr><td class="mono">${esc(x.factor)}</td><td class="mono">${esc(x.count)}</td><td>${esc(x.example || '')}</td></tr>`).join('')}</tbody></table>
      <p class="dim" style="font-size:11.5px;margin-top:6px">Index counts: ${Object.entries(r.counts).filter(([k]) => k !== 'status').map(([k, v]) => `${esc(k)} ${esc(v ?? '—')}`).join(' · ')}</p>` : '<p class="muted">None yet.</p>';
  } catch (err) { toast(err.message, 'error'); }
}
